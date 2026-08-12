# -*- coding: utf-8 -*-
"""Phase 4B 批次 4 文档执行、证据、差异与中间 AST 契约。"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Dict, Optional, Tuple

from pydantic import Field, model_validator

from src.data_prep.document_models import EvidenceRef

from .models import ContentPolicy, ContractModel
from .physical_models import RuntimePolicy


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DocumentPlanStatus(str, Enum):
    READY = "ready"
    NEEDS_USER = "needs_user"


class DocumentAction(str, Enum):
    VERBATIM = "verbatim"
    COMPARE = "compare"
    AUDIT = "audit"
    SUMMARIZE = "summarize"
    REWRITE = "rewrite"
    TRANSLATE = "translate"
    COMPOSE = "compose"


class AuditOperator(str, Enum):
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"
    CONTAINS = "contains"
    REGEX = "regex"
    NUMERIC_EQ = "numeric_eq"
    NUMERIC_LTE = "numeric_lte"
    NUMERIC_GTE = "numeric_gte"
    DATE_LTE = "date_lte"
    DATE_GTE = "date_gte"
    SEMANTIC = "semantic"


class FindingStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_FOUND = "not_found"
    CANNOT_DETERMINE = "cannot_determine"


class DocumentNodeType(str, Enum):
    DOCUMENT = "document"
    SECTION = "section"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    QUOTE = "quote"
    COMPARISON = "comparison"
    FINDING = "finding"
    DERIVED_CONTENT = "derived_content"


class DocumentSource(ContractModel):
    source_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    detected_format: str = Field(min_length=1)
    original_name: str = Field(min_length=1)
    element_ids: Tuple[str, ...] = ()


class DocumentSelection(ContractModel):
    semantic_ref: str = Field(min_length=1)
    label: str = Field(min_length=1)
    artifact_element_ids: Dict[str, Tuple[str, ...]] = Field(default_factory=dict)


class AuditRule(ContractModel):
    rule_id: str = Field(pattern=r"^rule_[a-z0-9_]+$")
    label: str = Field(min_length=1)
    query: str = Field(min_length=1)
    operator: AuditOperator
    value: Any = None
    unit: Optional[str] = Field(default=None, min_length=1)
    pattern: Optional[str] = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_operand(self) -> "AuditRule":
        if self.operator == AuditOperator.REGEX and not self.pattern:
            raise ValueError("regex 审查规则必须提供 pattern")
        if self.operator in {
            AuditOperator.NUMERIC_EQ,
            AuditOperator.NUMERIC_LTE,
            AuditOperator.NUMERIC_GTE,
            AuditOperator.DATE_LTE,
            AuditOperator.DATE_GTE,
            AuditOperator.CONTAINS,
        } and self.value is None:
            raise ValueError(f"{self.operator.value} 审查规则必须提供 value")
        return self


class DocumentPhysicalPlan(ContractModel):
    spec_version: str = Field(default="1", pattern=r"^1$")
    physical_plan_id: str = Field(min_length=1)
    logical_plan_id: str = Field(min_length=1)
    logical_plan_revision: int = Field(ge=1)
    logical_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    bound_plan_id: str = Field(min_length=1)
    bound_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    binding_revision: int = Field(ge=1)
    status: DocumentPlanStatus
    capability_id: str = Field(default="document.evidence", pattern=r"^[a-z0-9._-]+$")
    capability_version: str = Field(default="1.0.0", min_length=1)
    action: DocumentAction
    content_policy: ContentPolicy
    sources: Tuple[DocumentSource, ...] = ()
    selections: Tuple[DocumentSelection, ...] = ()
    whole_document: bool = False
    audit_rules: Tuple[AuditRule, ...] = ()
    instruction: str = Field(min_length=1)
    target_language: Optional[str] = Field(default=None, min_length=1)
    runtime_policy: RuntimePolicy
    diagnostics: Tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_ready(self) -> "DocumentPhysicalPlan":
        if self.status == DocumentPlanStatus.READY:
            if not self.sources:
                raise ValueError("ready 文档计划必须包含来源")
            if self.action == DocumentAction.COMPARE and len(self.sources) < 2:
                raise ValueError("文档比较至少需要两个来源")
            if self.action == DocumentAction.AUDIT and not self.audit_rules:
                raise ValueError("文档审查必须包含已确认规则")
            if (
                self.action == DocumentAction.VERBATIM
                and not self.whole_document
                and not self.selections
            ):
                raise ValueError(
                    "原文提取必须包含限定选择；"
                    "只有明确全文任务才能设置 whole_document"
                )
            required_policy = {
                DocumentAction.VERBATIM: ContentPolicy.VERBATIM,
                DocumentAction.SUMMARIZE: ContentPolicy.SUMMARIZED,
                DocumentAction.REWRITE: ContentPolicy.REWRITTEN,
                DocumentAction.TRANSLATE: ContentPolicy.TRANSLATED,
            }.get(self.action)
            if required_policy is not None and self.content_policy != required_policy:
                raise ValueError(
                    f"{self.action.value} 必须使用 {required_policy.value} 内容政策"
                )
        elif not self.diagnostics:
            raise ValueError("needs_user 文档计划必须记录原因")
        return self

    def canonical_hash(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json", exclude={"created_at"}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class Passage(ContractModel):
    passage_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    text: str = Field(min_length=1)
    evidence_refs: Tuple[EvidenceRef, ...] = Field(min_length=1)


class DocumentDiff(ContractModel):
    diff_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    change_type: str = Field(pattern=r"^(added|removed|modified|table_cell)$")
    before: Optional[str] = None
    after: Optional[str] = None
    before_evidence: Tuple[EvidenceRef, ...] = ()
    after_evidence: Tuple[EvidenceRef, ...] = ()
    impact: Optional[str] = None
    impact_evidence_ids: Tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_side_evidence(self) -> "DocumentDiff":
        if self.change_type in {"removed", "modified", "table_cell"} and not self.before_evidence:
            raise ValueError("删除或修改必须包含修改前证据")
        if self.change_type in {"added", "modified", "table_cell"} and not self.after_evidence:
            raise ValueError("新增或修改必须包含修改后证据")
        return self


class AuditFinding(ContractModel):
    finding_id: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    status: FindingStatus
    message: str = Field(min_length=1)
    observed_value: Any = None
    evidence_refs: Tuple[EvidenceRef, ...] = ()

    @model_validator(mode="after")
    def require_evidence_for_decision(self) -> "AuditFinding":
        if self.status in {FindingStatus.PASS, FindingStatus.FAIL} and not self.evidence_refs:
            raise ValueError("通过或失败的审查结论必须包含证据")
        return self


class DerivedContent(ContractModel):
    content_id: str = Field(min_length=1)
    action: DocumentAction
    content: str = Field(min_length=1)
    evidence_refs: Tuple[EvidenceRef, ...] = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)


class DocumentASTNode(ContractModel):
    node_id: str = Field(min_length=1)
    node_type: DocumentNodeType
    text: Optional[str] = None
    children: Tuple["DocumentASTNode", ...] = ()
    evidence_refs: Tuple[EvidenceRef, ...] = ()
    derived_from: Tuple[str, ...] = ()
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_traceability(self) -> "DocumentASTNode":
        if self.text and self.node_type != DocumentNodeType.DOCUMENT and not self.evidence_refs:
            raise ValueError("带正文的 AST 节点必须包含来源证据")
        return self


class DocumentAST(ContractModel):
    schema_version: str = Field(default="1", pattern=r"^1$")
    ast_id: str = Field(min_length=1)
    source_artifact_ids: Tuple[str, ...] = Field(min_length=1)
    root: DocumentASTNode


class DocumentExecutionResult(ContractModel):
    result_id: str = Field(min_length=1)
    action: DocumentAction
    passages: Tuple[Passage, ...] = ()
    differences: Tuple[DocumentDiff, ...] = ()
    findings: Tuple[AuditFinding, ...] = ()
    derived_content: Tuple[DerivedContent, ...] = ()
    ast: DocumentAST
    warnings: Tuple[str, ...] = ()
