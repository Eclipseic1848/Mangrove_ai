# -*- coding: utf-8 -*-
"""任务对话、能力引用和渐进式进度的公共契约。"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProcedureScope(StrEnum):
    PERSONAL = "personal"
    PLATFORM = "platform"


class CapabilityMaturity(StrEnum):
    DRAFT = "draft"
    VERIFIED = "verified"
    DEPRECATED = "deprecated"


class CapabilityPack(BaseModel):
    """不可变能力包版本；可执行内容由 OCI digest 寻址。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pack_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    scope: ProcedureScope
    maturity: CapabilityMaturity
    owner_id: str | None = Field(default=None, min_length=1, max_length=120)
    task_refs: tuple[str, ...] = ()
    component_refs: tuple[str, ...] = ()
    validation_refs: tuple[str, ...] = ()
    manifest: tuple[tuple[str, str], ...] = ()
    source_provenance: tuple[str, ...] = ()
    permission_requirements: tuple[str, ...] = ()
    resource_requirements: tuple[tuple[str, str], ...] = ()
    entrypoint: str | None = Field(default=None, max_length=500)
    healthcheck: str | None = Field(default=None, max_length=500)
    created_by: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_scope(self) -> "CapabilityPack":
        if self.scope is ProcedureScope.PERSONAL and not self.owner_id:
            raise ValueError("个人能力包必须绑定 Owner")
        if self.scope is ProcedureScope.PLATFORM:
            if self.owner_id is not None:
                raise ValueError("平台能力包不得绑定个人 Owner")
            if self.task_refs:
                raise ValueError("平台能力包不得携带个人任务引用")
            if self.maturity is CapabilityMaturity.DRAFT:
                raise ValueError("平台能力包必须先通过验证")
        return self


class AutomationProcedure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    procedure_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    scope: ProcedureScope
    maturity: CapabilityMaturity
    owner_id: str | None = Field(default=None, min_length=1, max_length=120)
    capability_refs: tuple[str, ...] = ()
    task_refs: tuple[str, ...] = ()
    applicability: tuple[str, ...] = ()
    preferred_sequence: tuple[str, ...] = ()
    allowed_adaptations: tuple[str, ...] = ()
    permission_requirements: tuple[str, ...] = ()
    completion_gates: tuple[str, ...] = ()
    failure_handling: tuple[str, ...] = ()
    fixture_refs: tuple[str, ...] = ()
    validation_summary: str | None = Field(default=None, max_length=1000)
    artifact_reference: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_scope(self) -> "AutomationProcedure":
        if self.scope is ProcedureScope.PERSONAL and not self.owner_id:
            raise ValueError("个人自动化方案必须绑定 Owner")
        if self.scope is ProcedureScope.PLATFORM and (
            self.owner_id is not None or self.task_refs
        ):
            raise ValueError("平台自动化方案不得包含个人身份或任务引用")
        return self


class AcquisitionBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_duration_seconds: int = Field(gt=0)
    max_download_bytes: int = Field(gt=0)
    max_unpacked_bytes: int = Field(gt=0)
    max_candidates: int = Field(gt=0)
    max_retries_per_source: int = Field(ge=0)
    max_concurrency: int = Field(gt=0)


class AcquisitionStatus(StrEnum):
    DISCOVERING = "discovering"
    AWAITING_PERMISSION = "awaiting_permission"
    ACQUIRING = "acquiring"
    BUILDING = "building"
    VALIDATING = "validating"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AcquisitionRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    acquisition_id: str
    owner_id: str
    need_summary: str
    budget: AcquisitionBudget
    status: AcquisitionStatus = AcquisitionStatus.DISCOVERING
    pack_ref: str | None = None


class RawUserTurn(BaseModel):
    """用户原话是权威输入，转写不得覆盖。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    turn_id: str = Field(min_length=1, max_length=120)
    owner_id: str = Field(min_length=1, max_length=120)
    task_id: str = Field(min_length=1, max_length=120)
    revision: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=20_000)
    idempotency_key: str | None = Field(default=None, max_length=200)
    created_at: datetime = Field(default_factory=_now)


class DeltaConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TurnIntent(StrEnum):
    STATUS_QUESTION = "status_question"
    RATIONALE_QUESTION = "rationale_question"
    NORMALIZATION = "normalization"
    TASK_REFINEMENT = "task_refinement"
    NEW_TASK = "new_task"
    PERMISSION_REQUEST = "permission_request"


class ContextDelta(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    delta_id: str
    owner_id: str
    task_id: str
    inherited_revision: int = Field(ge=1)
    source_turn_ids: tuple[str, ...] = Field(min_length=1)
    intent: TurnIntent
    goal_delta: str | None = None
    source_scope_delta: tuple[str, ...] = ()
    selection_delta: dict[str, Any] = Field(default_factory=dict)
    coverage_delta: dict[str, Any] = Field(default_factory=dict)
    field_semantics_delta: dict[str, Any] = Field(default_factory=dict)
    output_delta: tuple[str, ...] = ()
    permission_delta: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    confidence: DeltaConfidence
    normalized_text: str
    direct_answer: str | None = None
    created_at: datetime = Field(default_factory=_now)


class SteeringAction(StrEnum):
    ANSWER_ONLY = "answer_only"
    NORMALIZED_NO_MATERIAL_CHANGE = "normalized_no_material_change"
    REVISION_PROPOSAL = "revision_proposal"
    NEW_TASK_PROPOSAL = "new_task_proposal"
    PERMISSION_REQUEST = "permission_request"


class SteeringRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_id: str
    task_id: str
    revision: int = Field(ge=1)
    run_id: str | None = None
    text: str = Field(min_length=1, max_length=20_000)
    idempotency_key: str | None = Field(default=None, max_length=200)
    current_status: str
    status_summary: str = ""
    current_goal: str = ""
    selection_reason: str = ""
    event_summaries: tuple[str, ...] = ()
    provider: str = "local"
    model: str | None = None
    external_api_confirmed: bool = False


class SteeringResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    result_id: str
    owner_id: str
    task_id: str
    turn_id: str
    delta_id: str
    action: SteeringAction
    acknowledgement: str
    answer: str | None = None
    proposal_id: str | None = None
    run_id: str | None = None
    revision: int = Field(ge=1)
    created_at: datetime = Field(default_factory=_now)


class ReferencedContextSummary(BaseModel):
    """进入有界上下文的摘要及其不可混淆来源引用。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_ref: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=4_000)


class ContextCompileRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_id: str
    task_id: str
    revision: int = Field(ge=1)
    system_boundaries: tuple[str, ...] = Field(min_length=1)
    goal_contract: str = Field(min_length=1)
    confirmed_semantics: tuple[str, ...] = ()
    run_summary: str = ""
    procedure_summaries: tuple[str, ...] = ()
    task_template_summaries: tuple[ReferencedContextSummary, ...] = ()
    owner_memory_summaries: tuple[ReferencedContextSummary, ...] = ()
    relevant_turns: tuple[RawUserTurn, ...] = ()
    evidence_snippets: tuple[str, ...] = ()
    max_chars: int = Field(ge=256, le=200_000)

    @model_validator(mode="after")
    def validate_turn_scope(self) -> "ContextCompileRequest":
        if any(
            turn.owner_id != self.owner_id
            or turn.task_id != self.task_id
            or turn.revision > self.revision
            for turn in self.relevant_turns
        ):
            raise ValueError("相关回合必须属于当前 Owner、任务和 revision 历史")
        return self


class ContextCompositionItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    category: str
    source_ref: str
    char_count: int = Field(ge=0)
    protected: bool


class CompiledContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    context_id: str
    owner_id: str
    task_id: str
    revision: int
    content: str
    composition: tuple[ContextCompositionItem, ...]
    char_count: int
    estimated_tokens: int
    summary_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    omitted_categories: tuple[str, ...] = ()


class ProgressAudience(StrEnum):
    USER = "user"
    ADMIN = "admin"
    ALL = "all"


class ProgressStage(StrEnum):
    UNDERSTAND = "understand"
    INSPECT_SOURCES = "inspect_sources"
    PREPARE_CAPABILITIES = "prepare_capabilities"
    EXECUTE = "execute"
    VERIFY = "verify"
    DELIVER = "deliver"


class ProgressValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    current: int = Field(ge=0)
    total: int | None = Field(default=None, gt=0)
    unit: str = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_known_total(self) -> "ProgressValue":
        if self.total is not None and self.current > self.total:
            raise ValueError("进度 current 不得超过 total")
        return self


class StructuredProgressEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    sequence: int = Field(ge=1)
    task_id: str
    revision: int = Field(ge=1)
    run_id: str | None = None
    stage: ProgressStage
    event_type: str
    summary: str = Field(min_length=1, max_length=500)
    progress: ProgressValue | None = None
    refs: dict[str, Any] = Field(default_factory=dict)
    action: dict[str, Any] | None = None
    audience: ProgressAudience = ProgressAudience.ALL
    created_at: datetime = Field(default_factory=_now)


class StageStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING = "waiting"


class ProgressStageView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: ProgressStage
    status: StageStatus
    summary: str


class TaskProgressView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    active_stage: ProgressStage | None
    stages: tuple[ProgressStageView, ...]
    events: tuple[StructuredProgressEvent, ...]


class RevisionProposalStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"


class RevisionProposal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: str
    owner_id: str
    task_id: str
    base_revision: int = Field(ge=1)
    delta_id: str
    summary: str
    material_changes: tuple[str, ...] = Field(min_length=1)
    status: RevisionProposalStatus = RevisionProposalStatus.PENDING
    created_at: datetime = Field(default_factory=_now)


class RevisionSwitchMode(StrEnum):
    CANCEL_NOW = "cancel_now"
    AFTER_SAFE_POINT = "after_safe_point"
    NEW_TASK = "new_task"


class RevisionDecisionStatus(StrEnum):
    WAITING_SAFE_POINT = "waiting_safe_point"
    READY_TO_APPLY = "ready_to_apply"
    NEW_TASK_REQUIRED = "new_task_required"
    APPLIED = "applied"


class RevisionDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str
    proposal_id: str
    owner_id: str
    task_id: str
    base_revision: int = Field(ge=1)
    mode: RevisionSwitchMode
    status: RevisionDecisionStatus
    safe_point: str | None = None
    external_api_confirmed: bool = False
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
