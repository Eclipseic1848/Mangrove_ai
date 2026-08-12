# -*- coding: utf-8 -*-
"""Phase 4B 批次 1 的语义编译输入、草案和结果契约。"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional, Tuple

from pydantic import Field, field_validator, model_validator

from .models import (
    Ambiguity,
    CombineSpec,
    ContentPolicy,
    ContractModel,
    DeliveryFormat,
    DeliverySpec,
    EvidencePolicy,
    OperationSpec,
    PostconditionSpec,
    ProjectionField,
    SelectionPredicate,
    SemanticTaskPlan,
    TaskFamily,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CompileStatus(str, Enum):
    READY = "ready"
    NEEDS_USER = "needs_user"
    FAILED = "failed"


class DiagnosticSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class ClarificationResolution(ContractModel):
    """一次结构化澄清；不得只把回答拼接回自然语言后丢弃上一版语义。"""

    ambiguity_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)


class CompileRequest(ContractModel):
    """服务端可信范围与用户目标；来源 ID 不允许由模型生成。"""

    task_id: str = Field(min_length=1)
    objective_text: str = Field(min_length=1)
    artifact_ids: Tuple[str, ...] = ()
    source_ids: Tuple[str, ...] = ()
    pages: Dict[str, Tuple[int, ...]] = Field(default_factory=dict)
    table_scope: Optional[str] = Field(default=None, min_length=1)
    section_patterns: Tuple[str, ...] = ()
    time_ranges: Tuple[str, ...] = ()
    accepted_formats: Tuple[str, ...] = ()
    accepted_media_types: Tuple[str, ...] = ()
    requested_output_formats: Tuple[DeliveryFormat, ...] = ()
    provider: str = Field(default="local", min_length=1)
    model: Optional[str] = Field(default=None, min_length=1)
    external_api_confirmed: bool = False
    max_repair_attempts: int = Field(default=2, ge=0, le=2)
    prior_plan: Optional[SemanticTaskPlan] = None
    clarification: Optional[ClarificationResolution] = None

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        return value.strip().lower()

    @model_validator(mode="after")
    def validate_scope(self) -> "CompileRequest":
        if any(not item for item in (*self.artifact_ids, *self.source_ids)):
            raise ValueError("来源 ID 不得为空")
        if len(self.artifact_ids) != len(set(self.artifact_ids)):
            raise ValueError("artifact_ids 不得重复")
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("source_ids 不得重复")
        if (self.prior_plan is None) != (self.clarification is None):
            raise ValueError("prior_plan 与 clarification 必须同时提供")
        if self.prior_plan is not None:
            if self.prior_plan.task_id != self.task_id:
                raise ValueError("上一版计划必须属于同一任务")
            ambiguity_ids = {
                item.ambiguity_id for item in self.prior_plan.ambiguities
            }
            assert self.clarification is not None
            if self.clarification.ambiguity_id not in ambiguity_ids:
                raise ValueError("澄清回答必须对应上一版计划中的歧义")
        return self


class PlanSemanticsDraft(ContractModel):
    """模型只生成用户语义，不生成身份、来源 ID、权限或修复预算。"""

    task_family: TaskFamily
    normalized_objective: str = Field(min_length=1)
    table_scope: Optional[str] = Field(default=None, min_length=1)
    section_patterns: Tuple[str, ...] = ()
    time_ranges: Tuple[str, ...] = ()
    whole_document: bool = False
    accepted_formats: Tuple[str, ...] = ()
    accepted_media_types: Tuple[str, ...] = ()
    selection: Tuple[SelectionPredicate, ...] = ()
    projection: Tuple[ProjectionField, ...] = ()
    record_grain: Optional[str] = Field(
        default=None,
        min_length=1,
        description=(
            "每条结果代表什么。无分组/聚合的表格筛选使用 source_detail_row；"
            "有分组/聚合时必须明确业务粒度。"
        ),
    )
    operations: Tuple[OperationSpec, ...] = ()
    combine: CombineSpec = Field(default_factory=CombineSpec)
    content_policy: ContentPolicy = ContentPolicy.VERBATIM
    evidence_policy: EvidencePolicy = Field(default_factory=EvidencePolicy)
    delivery: DeliverySpec
    postconditions: PostconditionSpec = Field(default_factory=PostconditionSpec)
    ambiguities: Tuple[Ambiguity, ...] = ()


class CompileDiagnostic(ContractModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR
    path: Optional[str] = Field(default=None, min_length=1)
    repairable: bool = True
    attempt: int = Field(ge=0, le=2)


class ClarificationRequest(ContractModel):
    ambiguity_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    candidates: Tuple[str, ...] = ()


class PlanProvenance(ContractModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_version: str = Field(default="1", pattern=r"^1$")
    repair_attempts: int = Field(ge=0, le=2)
    generated_at: datetime = Field(default_factory=_utc_now)


class CompileResult(ContractModel):
    plan_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    status: CompileStatus
    plan: Optional[SemanticTaskPlan] = None
    summary: str = ""
    diagnostics: Tuple[CompileDiagnostic, ...] = ()
    clarification: Optional[ClarificationRequest] = None
    provenance: PlanProvenance

    @model_validator(mode="after")
    def validate_result(self) -> "CompileResult":
        if self.status == CompileStatus.READY:
            if self.plan is None or not self.plan.is_executable:
                raise ValueError("ready 必须包含可执行的逻辑计划")
            if self.clarification is not None:
                raise ValueError("ready 不得携带澄清问题")
        elif self.status == CompileStatus.NEEDS_USER:
            if self.clarification is None:
                raise ValueError("needs_user 必须包含一个澄清问题")
        elif self.plan is not None:
            raise ValueError("failed 不得携带逻辑计划")
        if self.plan is not None:
            if (
                self.plan.plan_id != self.plan_id
                or self.plan.task_id != self.task_id
                or self.plan.revision != self.revision
            ):
                raise ValueError("结果身份必须与逻辑计划一致")
        return self
