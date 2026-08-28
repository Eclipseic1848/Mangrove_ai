# -*- coding: utf-8 -*-
"""把一个冻结 Run 的安全事件与 ProviderUsage 投影为可检查工作记录。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.conversation_steering import StructuredProgressEvent


class UsageSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int = Field(ge=0)
    call_count: int = Field(ge=0)
    unknown_call_count: int = Field(ge=0)


class WorkTraceEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    sequence: int = Field(ge=1)
    created_at: datetime
    event_type: str
    summary: str
    purpose: str | None = None
    input_summary: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    result_summary: str | None = None
    evidence_refs: tuple[str, ...] = ()
    recovery_status: str | None = None
    tool_name: str | None = None
    action_id: str | None = None


class ProviderUsageView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_user_id: str
    task_id: str
    revision: int = Field(ge=1)
    run_id: str
    connection_id: str
    model: str
    purpose: str
    status: str
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    request_count: int = Field(ge=1)
    created_at: datetime


class WorkSessionView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    revision: int = Field(ge=1)
    run_id: str
    status: str
    started_at: datetime | None
    ended_at: datetime | None
    work_duration_ms: int = Field(ge=0)
    waiting_duration_ms: int = Field(ge=0)
    action_count: int = Field(ge=0)
    tool_call_count: int = Field(ge=0)
    handled_retry_count: int = Field(ge=0)
    usage: UsageSummary
    provider_usage: tuple[ProviderUsageView, ...]
    entries: tuple[WorkTraceEntry, ...]


_WAIT_START = {
    "question.requested",
    "question_required",
    "owner_action.requested",
    "revision.waiting_safe_point",
    "stage_waiting",
}
_WAIT_END = {
    "resumed",
    "runtime.resuming",
    "question.answered",
    "question_answered",
    "revision.safe_point_applied",
}
_START_EVENTS = {"runtime.preparing", "runtime.resuming", "run.started"}
_END_EVENTS = {
    "candidate.ready",
    "run.completed",
    "run.failed",
    "task_completed",
    "task_cancelled",
    "candidate_verification_failed",
}


def _trace_type(event: StructuredProgressEvent) -> str:
    return event.runtime_event_type or event.event_type


def _milliseconds(start: datetime, end: datetime) -> int:
    # 历史 SQLite 事件保存的是无时区的本机时间；新事件可能带 UTC。
    # 只在只读投影边界补齐本机时区，避免改写冻结事实或产生 7/8 小时偏差。
    if start.tzinfo is None:
        start = start.astimezone()
    if end.tzinfo is None:
        end = end.astimezone()
    return max(0, int((end - start).total_seconds() * 1000))


class WorkTraceProjection:
    """只接收已归一化事件；原始 Adapter payload 不进入此边界。"""

    def project(
        self,
        *,
        task_id: str,
        revision: int,
        run_id: str,
        status: str,
        events: tuple[StructuredProgressEvent, ...],
        provider_usage: list[dict[str, Any]],
        observed_at: datetime | None = None,
    ) -> WorkSessionView:
        selected = sorted(
            (event for event in events if event.run_id == run_id),
            key=lambda event: (event.sequence, event.event_id),
        )
        started_at = next(
            (
                event.created_at
                for event in selected
                if _trace_type(event) in _START_EVENTS
            ),
            None,
        )
        ended_at = next(
            (
                event.created_at
                for event in reversed(selected)
                if _trace_type(event) in _END_EVENTS
            ),
            None,
        )
        waiting_ms = 0
        waiting_since: datetime | None = None
        for event in selected:
            event_type = _trace_type(event)
            if event_type in _WAIT_START and waiting_since is None:
                waiting_since = event.created_at
            elif event_type in _WAIT_END and waiting_since is not None:
                waiting_ms += _milliseconds(waiting_since, event.created_at)
                waiting_since = None
        calculation_end = ended_at or observed_at or datetime.now(timezone.utc)
        if waiting_since is not None:
            waiting_ms += _milliseconds(waiting_since, calculation_end)
        span_ms = _milliseconds(started_at, calculation_end) if started_at else 0

        selected_usage = [item for item in provider_usage if item.get("run_id") == run_id]
        if selected_usage:
            usage_rows = selected_usage
        else:
            # 本地 Pi 不一定经过 Broker；此时只使用 Adapter 已归一化的数值，
            # 不读取原始消息，也绝不把缺失用量估算为 0。
            usage_rows = [
                {
                    "input_tokens": event.input_tokens,
                    "output_tokens": event.output_tokens,
                    "cache_tokens": event.cache_tokens,
                    "total_tokens": event.total_tokens,
                    "request_count": 1,
                }
                for event in selected
                if _trace_type(event) == "provider.usage"
            ]
            if not usage_rows:
                historical_call_count = sum(
                    1
                    for event in selected
                    if _trace_type(event) in {"agent.started", "agent.retrying"}
                )
                if historical_call_count == 0 and any(
                    _trace_type(event) in {"agent.settled", "candidate.ready"}
                    for event in selected
                ):
                    historical_call_count = 1
                usage_rows = [
                    {
                        "input_tokens": None,
                        "output_tokens": None,
                        "cache_tokens": None,
                        "total_tokens": None,
                        "request_count": 1,
                    }
                    for _ in range(historical_call_count)
                ]
        known = [item for item in usage_rows if item.get("total_tokens") is not None]
        calls = sum(int(item.get("request_count") or 0) for item in usage_rows)
        unknown_calls = sum(
            int(item.get("request_count") or 0)
            for item in usage_rows
            if item.get("total_tokens") is None
        )
        entries = tuple(
            WorkTraceEntry(
                event_id=event.event_id,
                sequence=event.sequence,
                created_at=event.created_at,
                event_type=_trace_type(event),
                summary=event.summary,
                purpose=event.purpose,
                input_summary=event.input_summary,
                duration_ms=event.duration_ms,
                result_summary=event.result_summary,
                evidence_refs=event.evidence_refs,
                recovery_status=event.recovery_status,
                tool_name=(
                    str(event.action.get("tool"))
                    if event.action and event.action.get("tool")
                    else None
                ),
                action_id=(
                    str(event.action.get("action_id"))
                    if event.action and event.action.get("action_id")
                    else None
                ),
            )
            for event in selected
        )
        return WorkSessionView(
            task_id=task_id,
            revision=revision,
            run_id=run_id,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            work_duration_ms=max(0, span_ms - waiting_ms),
            waiting_duration_ms=waiting_ms,
            action_count=len(entries),
            tool_call_count=sum(
                1 for event in selected if _trace_type(event) == "tool.started"
            ),
            handled_retry_count=sum(
                1
                for event in selected
                if event.recovery_status == "handled"
                or _trace_type(event) == "agent.retrying"
            ),
            usage=UsageSummary(
                input_tokens=sum(int(item.get("input_tokens") or 0) for item in known),
                output_tokens=sum(int(item.get("output_tokens") or 0) for item in known),
                cache_tokens=(
                    sum(int(item["cache_tokens"]) for item in known)
                    if known and all(item.get("cache_tokens") is not None for item in known)
                    else None
                ),
                total_tokens=sum(int(item["total_tokens"]) for item in known),
                call_count=calls,
                unknown_call_count=unknown_calls,
            ),
            provider_usage=tuple(
                ProviderUsageView.model_validate(item)
                for item in selected_usage
                if all(item.get(key) is not None for key in (
                    "run_id", "connection_id", "model", "purpose", "status", "created_at"
                ))
            ),
            entries=entries,
        )
