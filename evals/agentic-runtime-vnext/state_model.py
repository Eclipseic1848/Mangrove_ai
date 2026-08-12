# -*- coding: utf-8 -*-
"""阶段 1 原型可保留的纯状态模型。"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Mapping


class RunStatus(StrEnum):
    READY = "ready"
    RUNNING = "running"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class GoalContract:
    original_request: str
    source_scope: tuple[str, ...]
    output_format: str
    output_file_count: int
    must_include: tuple[str, ...] = ()
    must_not_include: tuple[str, ...] = ()


@dataclass(frozen=True)
class KernelEvent:
    event_type: str
    summary: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunState:
    candidate: str
    case_id: str
    goal: GoalContract
    status: RunStatus = RunStatus.READY
    active_tools: tuple[str, ...] = ()
    tool_calls: int = 0
    candidate_created: bool = False
    clarification_required: bool = False
    verification_passed: bool | None = None
    adapter_failed: bool = False
    last_summary: str = "等待执行"
    events: tuple[KernelEvent, ...] = ()


def reduce_event(state: RunState, event: KernelEvent) -> RunState:
    """应用统一事件，并确保框架失败不会被后续验证结果覆盖。"""

    # 所有候选都先归一到同一事件模型，业务层不能依赖某个框架特有的状态字段。
    updated = replace(
        state,
        last_summary=event.summary,
        events=state.events + (event,),
    )
    if event.event_type in {"run.started", "run.resumed"}:
        return replace(updated, status=RunStatus.RUNNING)
    if event.event_type == "tool.started":
        tool_name = str(event.payload.get("tool_name") or "unknown")
        return replace(
            updated,
            active_tools=state.active_tools + (tool_name,),
            tool_calls=state.tool_calls + 1,
        )
    if event.event_type in {"tool.completed", "tool.failed"}:
        tool_name = str(event.payload.get("tool_name") or "unknown")
        active_tools = list(state.active_tools)
        if tool_name in active_tools:
            active_tools.remove(tool_name)
        return replace(updated, active_tools=tuple(active_tools))
    if event.event_type == "candidate.created":
        return replace(updated, candidate_created=True)
    if event.event_type == "approval.required":
        return replace(updated, clarification_required=True)
    if event.event_type == "verification.started":
        # 取消和 Adapter 失败是更强的终态；进入验证不能把它们重新标记成“执行中”。
        return replace(
            updated,
            status=(
                RunStatus.CANCELLED
                if state.status == RunStatus.CANCELLED
                else RunStatus.FAILED
                if state.adapter_failed
                else RunStatus.VERIFYING
            ),
        )
    if event.event_type == "verification.completed":
        passed = bool(event.payload.get("passed"))
        return replace(updated, verification_passed=passed)
    if event.event_type == "run.completed":
        passed = state.verification_passed
        # 只有 Adapter 正常结束且独立验证通过，候选结果才允许成为 completed。
        if state.status == RunStatus.CANCELLED:
            return replace(updated, status=RunStatus.CANCELLED, active_tools=())
        return replace(
            updated,
            status=(
                RunStatus.COMPLETED
                if passed and not state.adapter_failed
                else RunStatus.FAILED
            ),
            active_tools=(),
        )
    if event.event_type == "run.cancelled":
        return replace(updated, status=RunStatus.CANCELLED, active_tools=())
    if event.event_type == "run.failed":
        return replace(
            updated,
            status=RunStatus.FAILED,
            active_tools=(),
            adapter_failed=True,
        )
    return updated
