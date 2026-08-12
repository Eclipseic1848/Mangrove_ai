# -*- coding: utf-8 -*-
"""Phase 4B 批次 5 有界 Harness 的运行、修复与暂停契约。"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Dict, Optional, Tuple

from pydantic import Field, model_validator

from .models import ContractModel, FailureKind, VerificationReport


_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class HarnessStatus(str, Enum):
    RUNNING = "running"
    NEEDS_USER = "needs_user"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class HarnessNode(str, Enum):
    INTERPRET = "interpret"
    INSPECT = "inspect"
    BIND = "bind"
    PLAN = "plan"
    EXECUTE = "execute"
    VERIFY = "verify"
    REPAIR = "repair"
    NEEDS_USER = "needs_user"
    DELIVER = "deliver"


class RepairAction(str, Enum):
    RETRY_SAME_TOOL = "retry_same_tool"
    SWITCH_COMPATIBLE_TOOL = "switch_compatible_tool"
    REBIND_SOURCE = "rebind_source"
    RECOMPILE_PHYSICAL_PLAN = "recompile_physical_plan"
    SEMANTIC_REPLAN = "semantic_replan"
    REQUEST_USER = "request_user"
    STOP = "stop"


class HarnessLoopPolicy(ContractModel):
    max_transient_retries: int = Field(default=2, ge=0, le=10)
    max_semantic_replans: int = Field(default=2, ge=0, le=2)
    max_total_repair_rounds: int = Field(default=5, ge=0, le=20)
    max_same_failure: int = Field(default=2, ge=1, le=10)
    allow_external_api: bool = False


class HarnessRun(ContractModel):
    run_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    logical_plan_id: str = Field(min_length=1)
    logical_plan_revision: int = Field(ge=1)
    logical_plan_hash: str = Field(pattern=_SHA256_PATTERN)
    binding_revision: int = Field(ge=1)
    binding_hash: str = Field(pattern=_SHA256_PATTERN)
    capability_id: str = Field(min_length=1)
    capability_version: str = Field(min_length=1)
    runtime_profile: str = Field(min_length=1)
    policy: HarnessLoopPolicy = Field(default_factory=HarnessLoopPolicy)
    status: HarnessStatus = HarnessStatus.RUNNING
    current_node: HarnessNode = HarnessNode.INTERPRET
    repair_rounds: int = Field(default=0, ge=0)
    semantic_replans: int = Field(default=0, ge=0)
    transient_retries: int = Field(default=0, ge=0)
    same_failure_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_thread_and_budget(self) -> "HarnessRun":
        if self.thread_id != self.run_id:
            raise ValueError("thread_id 必须稳定等于 run_id")
        if self.repair_rounds > self.policy.max_total_repair_rounds:
            raise ValueError("修复轮数超过冻结策略")
        if self.semantic_replans > self.policy.max_semantic_replans:
            raise ValueError("语义重规划次数超过冻结策略")
        if self.transient_retries > self.policy.max_transient_retries:
            raise ValueError("暂时性重试次数超过冻结策略")
        return self


class HarnessQuestionOption(ContractModel):
    value: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)


class HarnessQuestion(ContractModel):
    question_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    checkpoint_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    affected_scope: str = Field(min_length=1)
    options: Tuple[HarnessQuestionOption, ...] = Field(min_length=2, max_length=3)
    allow_free_text: bool = True
    answer_schema: Dict[str, Any] = Field(default_factory=dict)
    resume_token: str = Field(min_length=16)
    external_service: Optional[str] = None
    outbound_data: Tuple[str, ...] = ()
    purpose: Optional[str] = None
    risk: Optional[str] = None


class HarnessResume(ContractModel):
    question_id: str = Field(min_length=1)
    resume_token: str = Field(min_length=16)
    answer: str = Field(min_length=1)


class RepairProposal(ContractModel):
    proposal_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    failure_kind: FailureKind
    failure_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    action: RepairAction
    reason: str = Field(min_length=1)
    changes_user_semantics: bool = False
    before_hash: Optional[str] = Field(default=None, pattern=_SHA256_PATTERN)
    after_hash: Optional[str] = Field(default=None, pattern=_SHA256_PATTERN)
    diff: Dict[str, Any] = Field(default_factory=dict)


class RepairDecision(ContractModel):
    decision_id: str = Field(min_length=1)
    proposal: RepairProposal
    approved: bool
    policy_reason: str = Field(min_length=1)
    requires_user: bool = False

    @model_validator(mode="after")
    def validate_decision(self) -> "RepairDecision":
        if self.proposal.changes_user_semantics and self.approved:
            raise ValueError("改变用户语义的修复不得自动批准")
        if self.requires_user and self.approved:
            raise ValueError("需要用户确认的修复不得提前批准")
        return self


def failure_fingerprint(
    failure_kind: FailureKind,
    message: str,
    *,
    checks: Tuple[Dict[str, Any], ...] = (),
) -> str:
    """对脱敏错误分类和失败检查生成稳定指纹，不记录来源正文。"""

    payload = json.dumps(
        {
            "failure_kind": failure_kind.value,
            "message": message.strip()[:400],
            "checks": checks,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def report_fingerprint(report: VerificationReport) -> Optional[str]:
    if report.failure_fingerprint:
        return report.failure_fingerprint
    return None
