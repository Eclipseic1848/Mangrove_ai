# -*- coding: utf-8 -*-
"""有界修复策略；只批准不改变用户语义的白名单动作。"""
from __future__ import annotations

from typing import Any, Mapping
import uuid

from deepdiff import DeepDiff
from pydantic import ValidationError

from .harness_models import (
    HarnessLoopPolicy,
    RepairAction,
    RepairDecision,
    RepairProposal,
)
from .models import FailureKind


def classify_exception(exc: Exception) -> FailureKind:
    """把异常收敛为稳定分类，避免对所有 ValueError 盲目重试。"""

    if isinstance(exc, (TimeoutError, ConnectionError)):
        return FailureKind.TRANSIENT
    if isinstance(exc, MemoryError):
        return FailureKind.RESOURCE_EXHAUSTED
    if isinstance(exc, (ValidationError, ValueError, TypeError)):
        return FailureKind.INVALID_PLAN
    if isinstance(exc, PermissionError):
        return FailureKind.POLICY_DENIED
    return FailureKind.TOOL_INCOMPATIBLE


def safe_error_message(exc: Exception) -> str:
    """只保留异常类型和短消息，不把来源正文或完整路径写入审计。"""

    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text[:300]}"


def plan_diff(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """使用 DeepDiff 生成可序列化差异，忽略运行身份和时间字段。"""

    if before is None or after is None:
        return {}
    diff = DeepDiff(
        before,
        after,
        ignore_order=False,
        exclude_regex_paths=(
            r"root\['physical_plan_id'\]",
            r"root\['created_at'\]",
        ),
    )
    return diff.to_dict()


def decide_repair(
    *,
    run_id: str,
    failure_kind: FailureKind,
    failure_fingerprint: str,
    reason: str,
    policy: HarnessLoopPolicy,
    repair_rounds: int,
    transient_retries: int,
    same_failure_count: int,
    before_hash: str | None = None,
    after_hash: str | None = None,
    diff: Mapping[str, Any] | None = None,
) -> RepairDecision:
    """按冻结预算决定唯一动作；无安全自动动作时停止或询问用户。"""

    action = RepairAction.STOP
    approved = False
    requires_user = False
    policy_reason = "没有满足策略的安全自动修复动作"

    if same_failure_count >= policy.max_same_failure:
        policy_reason = "同一失败指纹已连续达到上限"
    elif repair_rounds >= policy.max_total_repair_rounds:
        policy_reason = "任务总修复轮数已达到上限"
    elif failure_kind == FailureKind.TRANSIENT:
        if transient_retries < policy.max_transient_retries:
            action = RepairAction.RETRY_SAME_TOOL
            approved = True
            policy_reason = "暂时性故障且仍有同工具重试预算"
        else:
            policy_reason = "暂时性重试预算已耗尽"
    elif failure_kind == FailureKind.RESOURCE_EXHAUSTED:
        if transient_retries < policy.max_transient_retries:
            action = RepairAction.RETRY_SAME_TOOL
            approved = True
            policy_reason = "资源故障仍有短退避重试预算"
        else:
            policy_reason = "资源重试预算已耗尽"
    elif failure_kind == FailureKind.INVALID_PLAN:
        action = RepairAction.RECOMPILE_PHYSICAL_PLAN
        approved = True
        policy_reason = "只重新编译物理计划，不改变逻辑计划"
    elif failure_kind in {
        FailureKind.INSUFFICIENT_DATA,
        FailureKind.NEEDS_USER,
        FailureKind.POLICY_DENIED,
    }:
        action = RepairAction.REQUEST_USER
        requires_user = True
        policy_reason = "继续执行可能改变范围、含义或权限，必须询问用户"

    proposal = RepairProposal(
        proposal_id=f"repair_{uuid.uuid4().hex[:16]}",
        run_id=run_id,
        failure_kind=failure_kind,
        failure_fingerprint=failure_fingerprint,
        action=action,
        reason=reason,
        changes_user_semantics=action
        in {RepairAction.SEMANTIC_REPLAN, RepairAction.REBIND_SOURCE},
        before_hash=before_hash,
        after_hash=after_hash,
        diff=dict(diff or {}),
    )
    return RepairDecision(
        decision_id=f"decision_{uuid.uuid4().hex[:16]}",
        proposal=proposal,
        approved=approved,
        policy_reason=policy_reason,
        requires_user=requires_user,
    )
