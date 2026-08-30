"""可抛弃原型：Mangrove AgentKernel 合同的纯内存状态机。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import FrozenSet


REQUIRED_CAPABILITIES: FrozenSet[str] = frozenset(
    {
        "start",
        "resume",
        "steer",
        "cancel",
        "events",
        "query",
        "usage",
        "checkpoint",
        "tool_effect",
    }
)


class Lifecycle(StrEnum):
    READY = "ready"
    INCOMPATIBLE = "incompatible"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"


class ActionType(StrEnum):
    START = "start"
    TICK = "tick"
    TOOL_SUCCEEDED = "tool_succeeded"
    TOOL_FAILED = "tool_failed"
    FAILURE_RECOVERED = "failure_recovered"
    PAUSE = "pause"
    RESUME = "resume"
    STEER = "steer"
    USAGE_KNOWN = "usage_known"
    USAGE_UNKNOWN = "usage_unknown"
    UNKNOWN_EVENT = "unknown_event"
    FINISH = "finish"
    CANCEL = "cancel"


@dataclass(frozen=True)
class KernelManifest:
    family: str
    version: str
    protocol: str
    evidence_level: str
    capabilities: FrozenSet[str]

    @property
    def missing_required(self) -> tuple[str, ...]:
        return tuple(sorted(REQUIRED_CAPABILITIES - self.capabilities))


@dataclass(frozen=True)
class EventRecord:
    sequence: int
    event_type: str
    summary: str


@dataclass(frozen=True)
class PrototypeState:
    manifest: KernelManifest
    lifecycle: Lifecycle = Lifecycle.READY
    run_id: str | None = None
    sequence: int = 0
    active_ticks: int = 0
    waiting_ticks: int = 0
    tool_calls: int = 0
    unresolved_failures: int = 0
    recovered_failures: int = 0
    model_calls: int = 0
    known_tokens: int = 0
    unknown_usage_calls: int = 0
    candidate_ready: bool = False
    delivery_created: bool = False
    events: tuple[EventRecord, ...] = ()
    last_error: str | None = None

    @property
    def usage_summary(self) -> str:
        if self.unknown_usage_calls:
            return (
                f"至少 {self.known_tokens:,} Tokens · {self.model_calls} 次模型调用 · "
                f"{self.unknown_usage_calls} 次未知"
            )
        return f"{self.known_tokens:,} Tokens · {self.model_calls} 次模型调用"

    @property
    def verdict(self) -> str:
        missing = self.manifest.missing_required
        if missing:
            return f"不兼容：缺少必需能力 {', '.join(missing)}"
        if self.lifecycle is Lifecycle.SUCCEEDED:
            return "状态合同可表达；仍需实际 Runtime 协议与进程探针"
        if self.lifecycle is Lifecycle.CANCELLED:
            return "取消已形成终态；后续事件不得改写结果"
        return "能力形状满足；请继续手动推动边界场景"


@dataclass(frozen=True)
class Action:
    action_type: ActionType
    value: int = 0


def initial_state(manifest: KernelManifest) -> PrototypeState:
    return PrototypeState(manifest=manifest)


def reduce_state(state: PrototypeState, action: Action) -> PrototypeState:
    """应用一个离散动作并返回新状态，不执行任何 I/O。"""

    action_type = action.action_type
    if action_type is ActionType.START:
        if state.lifecycle is not Lifecycle.READY:
            return _reject(state, "只有 ready 状态可以启动")
        if state.manifest.missing_required:
            return _append_event(
                replace(
                    state,
                    lifecycle=Lifecycle.INCOMPATIBLE,
                    last_error="缺少必需 Runtime 能力，未创建 Run",
                ),
                "runtime_incompatible",
                "启动前失败关闭，未创建 Run",
            )
        return _append_event(
            replace(
                state,
                lifecycle=Lifecycle.RUNNING,
                run_id="prototype-run-001",
                last_error=None,
            ),
            "run_started",
            "已冻结 RuntimeBinding 并启动同一 Run",
        )

    if action_type is ActionType.TICK:
        if state.lifecycle is Lifecycle.RUNNING:
            return replace(state, active_ticks=state.active_ticks + 1, last_error=None)
        if state.lifecycle is Lifecycle.PAUSED:
            return replace(state, waiting_ticks=state.waiting_ticks + 1, last_error=None)
        return _reject(state, "只有 running/paused 状态可以推进时间")

    if action_type is ActionType.PAUSE:
        if state.lifecycle is not Lifecycle.RUNNING:
            return _reject(state, "只有 running 状态可以暂停")
        return _append_event(
            replace(state, lifecycle=Lifecycle.PAUSED, last_error=None),
            "run_paused",
            "同一 Run 已暂停",
        )

    if action_type is ActionType.RESUME:
        if state.lifecycle is not Lifecycle.PAUSED:
            return _reject(state, "只有 paused 状态可以恢复")
        return _append_event(
            replace(state, lifecycle=Lifecycle.RUNNING, last_error=None),
            "run_resumed",
            "沿用原 RuntimeBinding 和 Run ID",
        )

    if action_type is ActionType.STEER:
        if state.lifecycle is not Lifecycle.RUNNING:
            return _reject(state, "只有 running 状态可以接受运行中引导")
        return _append_event(state, "steering_applied", "运行中引导已应用到同一 Run")

    if action_type is ActionType.TOOL_SUCCEEDED:
        if state.lifecycle is not Lifecycle.RUNNING:
            return _reject(state, "只有 running 状态可以完成工具调用")
        return _append_event(
            replace(state, tool_calls=state.tool_calls + 1, last_error=None),
            "tool_succeeded",
            "工具行动成功",
        )

    if action_type is ActionType.TOOL_FAILED:
        if state.lifecycle is not Lifecycle.RUNNING:
            return _reject(state, "只有 running 状态可以记录工具失败")
        return _append_event(
            replace(
                state,
                tool_calls=state.tool_calls + 1,
                unresolved_failures=state.unresolved_failures + 1,
                last_error=None,
            ),
            "tool_failed",
            "工具行动失败，等待恢复或终止",
        )

    if action_type is ActionType.FAILURE_RECOVERED:
        if state.lifecycle is not Lifecycle.RUNNING or state.unresolved_failures < 1:
            return _reject(state, "当前没有可恢复的工具失败")
        return _append_event(
            replace(
                state,
                unresolved_failures=state.unresolved_failures - 1,
                recovered_failures=state.recovered_failures + 1,
                last_error=None,
            ),
            "failure_recovered",
            "失败事实已保留，原目标已安全恢复",
        )

    if action_type is ActionType.USAGE_KNOWN:
        if state.lifecycle is not Lifecycle.RUNNING:
            return _reject(state, "只有 running 状态可以记录模型调用")
        tokens = action.value if action.value > 0 else 1_200
        return _append_event(
            replace(
                state,
                model_calls=state.model_calls + 1,
                known_tokens=state.known_tokens + tokens,
                last_error=None,
            ),
            "provider_usage_known",
            f"Provider 返回 {tokens} Tokens",
        )

    if action_type is ActionType.USAGE_UNKNOWN:
        if state.lifecycle is not Lifecycle.RUNNING:
            return _reject(state, "只有 running 状态可以记录模型调用")
        return _append_event(
            replace(
                state,
                model_calls=state.model_calls + 1,
                unknown_usage_calls=state.unknown_usage_calls + 1,
                last_error=None,
            ),
            "provider_usage_unknown",
            "Provider 未返回 Usage，不按零或估算处理",
        )

    if action_type is ActionType.UNKNOWN_EVENT:
        if state.lifecycle not in {Lifecycle.RUNNING, Lifecycle.PAUSED}:
            return _reject(state, "终态后未知事件不能进入工作记录")
        return _append_event(
            replace(state, last_error="发现未映射 Runtime 事件，不能作为成功证据"),
            "runtime_event_unsupported",
            "仅记录兼容缺口，不展示原始载荷",
        )

    if action_type is ActionType.FINISH:
        if state.lifecycle is not Lifecycle.RUNNING:
            return _reject(state, "只有 running 状态可以完成")
        if state.unresolved_failures:
            return _reject(state, "仍有未恢复失败，不能形成成功 Candidate")
        return _append_event(
            replace(
                state,
                lifecycle=Lifecycle.SUCCEEDED,
                candidate_ready=True,
                delivery_created=False,
                last_error=None,
            ),
            "candidate_ready",
            "Runtime 只形成 Candidate，正式 Delivery 仍为 false",
        )

    if action_type is ActionType.CANCEL:
        if state.lifecycle not in {Lifecycle.RUNNING, Lifecycle.PAUSED}:
            return _reject(state, "只有 running/paused 状态可以取消")
        return _append_event(
            replace(
                state,
                lifecycle=Lifecycle.CANCELLED,
                candidate_ready=False,
                delivery_created=False,
                last_error=None,
            ),
            "run_cancelled",
            "取消终态已形成，零 Candidate、零 Delivery",
        )

    return _reject(state, f"未知动作：{action_type}")


def _append_event(state: PrototypeState, event_type: str, summary: str) -> PrototypeState:
    sequence = state.sequence + 1
    event = EventRecord(sequence=sequence, event_type=event_type, summary=summary)
    return replace(state, sequence=sequence, events=(*state.events, event))


def _reject(state: PrototypeState, message: str) -> PrototypeState:
    return replace(state, last_error=message)
