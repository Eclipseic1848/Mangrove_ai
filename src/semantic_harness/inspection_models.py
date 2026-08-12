# -*- coding: utf-8 -*-
"""Phase 4B 批次 2 的来源检查、候选和绑定结果契约。"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Dict, Optional, Tuple

from pydantic import Field, model_validator

from .compiler_models import ClarificationRequest
from .models import BoundPlan, ContractModel


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InspectionStatus(str, Enum):
    READY = "ready"
    UNSUPPORTED = "unsupported"
    CORRUPT = "corrupt"
    ENCRYPTED = "encrypted"
    OVER_LIMIT = "over_limit"
    NEEDS_USER = "needs_user"


class SourceKind(str, Enum):
    TABULAR = "tabular"
    DOCUMENT = "document"


class TargetKind(str, Enum):
    TABLE_COLUMN = "table_column"
    DOCUMENT_SECTION = "document_section"
    DOCUMENT_ELEMENT = "document_element"
    DOCUMENT_TABLE_CELL = "document_table_cell"


class InspectionDiagnostic(ContractModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    path: Optional[str] = Field(default=None, min_length=1)


class ColumnProfile(ContractModel):
    physical_ref: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    table_ref: str = Field(min_length=1)
    column_index: int = Field(ge=0)
    raw_name: str
    normalized_name: str
    inferred_type: str = Field(min_length=1)
    null_ratio: float = Field(ge=0, le=1)
    unique_ratio: float = Field(ge=0, le=1)
    sample_values: Tuple[str, ...] = ()
    duplicate_group: Optional[str] = None


class TableProfile(ContractModel):
    table_ref: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    table_index: int = Field(ge=0)
    header_row: int = Field(ge=1)
    sampled_rows: int = Field(ge=0)
    estimated_rows: Optional[int] = Field(default=None, ge=0)
    columns: Tuple[ColumnProfile, ...] = ()


class DocumentTarget(ContractModel):
    physical_ref: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    target_kind: TargetKind
    label: str = Field(min_length=1)
    text_excerpt: str = Field(min_length=1)
    page: int = Field(ge=1)
    element_ids: Tuple[str, ...] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    evidence_ready: bool


class SourceInspectionReport(ContractModel):
    spec_version: str = Field(default="1", pattern=r"^1$")
    inspection_id: str = Field(min_length=1)
    inspector_version: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    original_name: str = Field(min_length=1)
    declared_media_type: str = Field(min_length=1)
    detected_format: str = Field(min_length=1)
    source_kind: SourceKind
    status: InspectionStatus
    tables: Tuple[TableProfile, ...] = ()
    document_targets: Tuple[DocumentTarget, ...] = ()
    diagnostics: Tuple[InspectionDiagnostic, ...] = ()
    generated_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_ready_inventory(self) -> "SourceInspectionReport":
        if self.status == InspectionStatus.READY:
            if self.source_kind == SourceKind.TABULAR and not self.tables:
                raise ValueError("ready 表格报告必须包含 tables")
            if self.source_kind == SourceKind.DOCUMENT and not self.document_targets:
                raise ValueError("ready 文档报告必须包含 document_targets")
        return self

    def canonical_hash(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json", exclude={"generated_at"}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class BindingCandidate(ContractModel):
    semantic_ref: str = Field(min_length=1)
    semantic_label: str = Field(min_length=1)
    physical_ref: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    target_kind: TargetKind
    name_score: float = Field(ge=0, le=1)
    semantic_score: float = Field(default=0, ge=0, le=1)
    literal_support: float = Field(default=0, ge=0, le=1)
    type_support: float = Field(default=0.5, ge=0, le=1)
    evidence_quality: float = Field(default=1, ge=0, le=1)
    contradiction_penalty: float = Field(default=0, ge=0, le=1)
    total_score: float = Field(ge=0, le=1)
    evidence_reasons: Tuple[str, ...] = Field(min_length=1)
    evidence_samples: Tuple[str, ...] = ()


class BindStatus(str, Enum):
    READY = "ready"
    NEEDS_USER = "needs_user"
    BLOCKED = "blocked"


class BindProvenance(ContractModel):
    binder_version: str = Field(min_length=1)
    threshold_version: str = Field(min_length=1)
    auto_bind_threshold: float = Field(ge=0, le=1)
    margin_threshold: float = Field(ge=0, le=1)
    semantic_backend: str = Field(min_length=1)
    generated_at: datetime = Field(default_factory=_utc_now)


class BindResult(ContractModel):
    status: BindStatus
    logical_plan_id: str = Field(min_length=1)
    logical_plan_revision: int = Field(ge=1)
    binding_revision: int = Field(ge=1)
    bound_plan: Optional[BoundPlan] = None
    candidates: Tuple[BindingCandidate, ...] = ()
    clarification: Optional[ClarificationRequest] = None
    provenance: BindProvenance

    @model_validator(mode="after")
    def validate_status(self) -> "BindResult":
        if self.status == BindStatus.READY:
            if self.bound_plan is None or not self.bound_plan.is_executable:
                raise ValueError("ready 必须包含可执行 BoundPlan")
            if self.clarification is not None:
                raise ValueError("ready 不得包含澄清问题")
        elif self.status == BindStatus.NEEDS_USER:
            if self.bound_plan is None or self.clarification is None:
                raise ValueError("needs_user 必须包含 BoundPlan 和一个澄清问题")
        elif self.bound_plan is not None:
            raise ValueError("blocked 不得包含 BoundPlan")
        return self
