# -*- coding: utf-8 -*-
"""把同一事实事件投影为普通用户或管理员可见进度。"""
from __future__ import annotations

from .models import (
    ProgressAudience,
    ProgressStage,
    ProgressStageView,
    StageStatus,
    StructuredProgressEvent,
    TaskProgressView,
)


_STAGE_ORDER = tuple(ProgressStage)
_USER_ACTION_FIELDS = {"action_id", "disabled", "kind", "label"}
_USER_CAPABILITY_FIELDS = {"name", "kind", "version", "purpose"}
_USER_CAPABILITY_KINDS = {
    "tool",
    "mcp_local",
    "mcp_remote",
    "skill",
    "dependency_bundle",
    "capability_pack",
}


def _project_user_action(action: dict | None) -> dict | None:
    if action is None:
        return None
    # 普通用户投影是权限边界：未知字段默认拒绝，而不是依赖敏感词黑名单。
    # 只允许渲染操作所需的简单标量，技术参数始终保留在管理员视图。
    return {
        key: value
        for key, value in action.items()
        if key in _USER_ACTION_FIELDS
        and (value is None or isinstance(value, (str, bool, int, float)))
    }


def _project_user_refs(refs: dict) -> dict:
    """只开放产品化能力身份，其他引用仍按默认拒绝处理。"""

    capabilities = refs.get("capabilities")
    if not isinstance(capabilities, list):
        return {}
    safe_items = []
    for item in capabilities:
        if not isinstance(item, dict):
            continue
        projected = {
            key: value
            for key, value in item.items()
            if key in _USER_CAPABILITY_FIELDS and isinstance(value, str)
        }
        if (
            set(projected) != _USER_CAPABILITY_FIELDS
            or projected["kind"] not in _USER_CAPABILITY_KINDS
        ):
            continue
        safe_items.append(projected)
    return {"capabilities": safe_items} if safe_items else {}


def _completed(event_type: str) -> bool:
    value = event_type.lower()
    return value.endswith((".completed", "_completed", ".passed", ".published"))


def _failed(event_type: str) -> bool:
    value = event_type.lower()
    return value.endswith((".failed", "_failed"))


def _started(event_type: str) -> bool:
    value = event_type.lower()
    return value.endswith((".started", "_started"))


class ProgressProjection:
    def project(
        self,
        events: tuple[StructuredProgressEvent, ...],
        *,
        audience: ProgressAudience,
        task_status: str,
    ) -> TaskProgressView:
        unique: dict[str, StructuredProgressEvent] = {}
        for event in events:
            unique[event.event_id] = event
        ordered = sorted(unique.values(), key=lambda item: (item.sequence, item.event_id))
        visible: list[StructuredProgressEvent] = []
        for event in ordered:
            if audience is ProgressAudience.USER and event.audience not in {
                ProgressAudience.USER,
                ProgressAudience.ALL,
            }:
                continue
            refs = event.refs
            action = event.action
            if audience is ProgressAudience.USER:
                # refs 默认拒绝；唯一例外是已按固定字段投影的能力身份。
                refs = _project_user_refs(refs)
                action = _project_user_action(action)
            visible.append(
                event.model_copy(update={"refs": refs, "action": action})
            )

        by_stage = {
            stage: [event for event in visible if event.stage is stage]
            for stage in _STAGE_ORDER
        }
        active_stage = None
        if task_status in {"queued", "running", "cancelling"}:
            for event in reversed(visible):
                stage_events = by_stage[event.stage]
                latest_completion = max(
                    (
                        candidate.sequence
                        for candidate in stage_events
                        if _completed(candidate.event_type)
                        or _failed(candidate.event_type)
                    ),
                    default=0,
                )
                if _started(event.event_type) and event.sequence > latest_completion:
                    active_stage = event.stage
                    break
            # 旧工作台事件没有统一的 started 事件。迁移期以最新一个尚未结束的
            # 业务事件作为活动阶段，避免用户只看到“任务运行中”却不知所在位置。
            if active_stage is None and visible:
                latest = visible[-1]
                if not _completed(latest.event_type) and not _failed(latest.event_type):
                    active_stage = latest.stage

        # 能力准备是按需阶段：只有真正选择、挂载或获取能力时才展示。
        # 固定保留一个无事件的空阶段，会让已完成任务错误显示为 5/6。
        visible_stages = tuple(
            stage
            for stage in _STAGE_ORDER
            if stage is not ProgressStage.PREPARE_CAPABILITIES
            or by_stage[stage]
        )
        stages: list[ProgressStageView] = []
        for stage in visible_stages:
            stage_events = by_stage[stage]
            latest = stage_events[-1] if stage_events else None
            failure = next(
                (event for event in reversed(stage_events) if _failed(event.event_type)),
                None,
            )
            completion = next(
                (event for event in reversed(stage_events) if _completed(event.event_type)),
                None,
            )
            latest_terminal = max(
                (event for event in (failure, completion) if event is not None),
                key=lambda event: event.sequence,
                default=None,
            )
            if stage is active_stage:
                status = StageStatus.ACTIVE
                summary = latest.summary if latest else "正在处理"
            elif failure is not None and latest_terminal is failure:
                status = StageStatus.FAILED
                summary = failure.summary
            elif completion is not None and latest_terminal is completion:
                status = StageStatus.COMPLETED
                summary = completion.summary
            elif task_status == "needs_input" and latest is not None:
                status = StageStatus.WAITING
                summary = latest.summary
            else:
                status = StageStatus.PENDING
                summary = "尚未开始"
            stages.append(
                ProgressStageView(stage=stage, status=status, summary=summary)
            )

        return TaskProgressView(
            active_stage=active_stage,
            stages=tuple(stages),
            events=tuple(visible),
        )
