# -*- coding: utf-8 -*-
"""Phase 4B 语义计划、工具调用与验证结果契约。

本模块只定义控制面契约，不执行数据变换，也不把大表、长文或模型原始响应放进图状态。
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_CAPABILITY_ID_PATTERN = r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ContractModel(BaseModel):
    """禁止额外字段并冻结顶层赋值，避免契约被静默扩展或原地改写。"""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class TaskFamily(str, Enum):
    EXTRACT = "extract"
    TABULAR_TRANSFORM = "tabular_transform"
    COMPARE = "compare"
    AUDIT = "audit"
    COMPOSE = "compose"
    SUMMARIZE = "summarize"
    TRANSLATE = "translate"
    CONVERT = "convert"
    TRANSCRIBE = "transcribe"
    DISCOVER = "discover"


class ContentPolicy(str, Enum):
    VERBATIM = "verbatim"
    NORMALIZED = "normalized"
    SUMMARIZED = "summarized"
    REWRITTEN = "rewritten"
    TRANSLATED = "translated"
    ANALYZED = "analyzed"


class PredicateOperator(str, Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    CONTAINS = "contains"
    IN = "in"
    NOT_IN = "not_in"
    IS_NULL = "is_null"
    NOT_NULL = "not_null"
    REGEX = "regex"


class OperationType(str, Enum):
    SORT = "sort"
    UNION = "union"
    JOIN = "join"
    GROUP = "group"
    AGGREGATE = "aggregate"
    DEDUPLICATE = "deduplicate"
    NORMALIZE = "normalize"
    COMPARE = "compare"
    AUDIT = "audit"


class CombineMode(str, Enum):
    PRESERVE = "preserve"
    ONE_TABLE = "one_table"


class DeliveryFormat(str, Enum):
    JSON = "json"
    JSONL = "jsonl"
    CSV = "csv"
    TSV = "tsv"
    XLSX = "xlsx"
    PARQUET = "parquet"
    DOCX = "docx"
    PDF = "pdf"
    HTML = "html"
    MARKDOWN = "markdown"
    TXT = "txt"
    PPTX = "pptx"


class ExecutionBoundary(str, Enum):
    LOCAL_OR_LAN = "local_or_lan"
    EXTERNAL_API = "external_api"


class BindingStatus(str, Enum):
    BOUND = "bound"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"


class SideEffect(str, Enum):
    NONE = "none"
    READ_ONLY = "read_only"


class NetworkAccess(str, Enum):
    NONE = "none"
    LAN = "lan"
    EXTERNAL = "external"


class ResourceClass(str, Enum):
    CPU_SMALL = "cpu_small"
    CPU_MEDIUM = "cpu_medium"
    CPU_LARGE = "cpu_large"
    CONVERTER = "converter"
    GPU = "gpu"


class ToolStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    NEEDS_INPUT = "needs_input"


class FailureKind(str, Enum):
    TRANSIENT = "transient"
    TOOL_INCOMPATIBLE = "tool_incompatible"
    INVALID_PLAN = "invalid_plan"
    INSUFFICIENT_DATA = "insufficient_data"
    NEEDS_USER = "needs_user"
    POLICY_DENIED = "policy_denied"
    RESOURCE_EXHAUSTED = "resource_exhausted"


class VerificationStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NEEDS_USER = "needs_user"


class ObjectiveSpec(ContractModel):
    original_text: str = Field(min_length=1)
    normalized_text: str = Field(min_length=1)


class SourceScope(ContractModel):
    artifact_ids: Tuple[str, ...] = ()
    source_ids: Tuple[str, ...] = ()
    table_scope: Optional[str] = None
    pages: Dict[str, Tuple[int, ...]] = Field(default_factory=dict)
    section_patterns: Tuple[str, ...] = ()
    time_ranges: Tuple[str, ...] = ()
    whole_document: bool = False


class InputContract(ContractModel):
    accepted_formats: Tuple[str, ...] = ()
    accepted_media_types: Tuple[str, ...] = ()
    allow_internal_conversion: bool = True
    encrypted_policy: str = Field(default="reject", pattern=r"^(reject|needs_user)$")
    corrupt_policy: str = Field(default="reject", pattern=r"^(reject|isolate)$")


class SelectionPredicate(ContractModel):
    field: str = Field(min_length=1)
    operator: PredicateOperator
    value: Any = None
    values: Tuple[Any, ...] = ()
    case_sensitive: bool = False

    @model_validator(mode="after")
    def validate_operand(self) -> "SelectionPredicate":
        if self.operator in {PredicateOperator.IS_NULL, PredicateOperator.NOT_NULL}:
            if self.value is not None or self.values:
                raise ValueError("空值判断不得携带 value/values")
        elif self.operator in {PredicateOperator.IN, PredicateOperator.NOT_IN}:
            if not self.values:
                raise ValueError("in/not_in 至少需要一个 values 值")
        elif self.value is None:
            raise ValueError(f"{self.operator.value} 需要 value")
        return self


class ProjectionField(ContractModel):
    name: str = Field(min_length=1)
    alias: Optional[str] = Field(default=None, min_length=1)


class OperationSpec(ContractModel):
    operation: OperationType
    params: Dict[str, Any] = Field(default_factory=dict)


class CombineSpec(ContractModel):
    mode: CombineMode = CombineMode.PRESERVE


class EvidencePolicy(ContractModel):
    require_evidence: bool = True
    minimum_coverage: float = Field(default=1.0, ge=0, le=1)
    require_source_position: bool = True


class DeliverySpec(ContractModel):
    formats: Tuple[DeliveryFormat, ...] = Field(min_length=1)
    output_name: Optional[str] = Field(default=None, min_length=1)
    requested_file_count: Optional[int] = Field(default=None, ge=1)


class PredicatePostcondition(ContractModel):
    field: str = Field(min_length=1)
    operator: PredicateOperator
    value: Any = None
    values: Tuple[Any, ...] = ()
    required_ratio: float = Field(default=1.0, ge=0, le=1)

    @model_validator(mode="after")
    def validate_operand(self) -> "PredicatePostcondition":
        SelectionPredicate(
            field=self.field,
            operator=self.operator,
            value=self.value,
            values=self.values,
        )
        return self


class PostconditionSpec(ContractModel):
    table_count: Optional[int] = Field(default=None, ge=0)
    exact_visible_columns: Tuple[str, ...] = ()
    expected_row_count: Optional[int] = Field(default=None, ge=0)
    predicates: Tuple[PredicatePostcondition, ...] = ()
    minimum_evidence_coverage: float = Field(default=1.0, ge=0, le=1)
    require_lineage: bool = True
    require_openable_output: bool = True


class RiskPolicy(ContractModel):
    execution_boundary: ExecutionBoundary = ExecutionBoundary.LOCAL_OR_LAN
    external_api_confirmed: bool = False
    allow_side_effects: bool = False

    @model_validator(mode="after")
    def forbid_side_effects(self) -> "RiskPolicy":
        if self.allow_side_effects:
            raise ValueError("Phase 4B 工具不得产生业务写入副作用")
        return self


class BudgetSpec(ContractModel):
    max_bytes: Optional[int] = Field(default=None, ge=1)
    max_rows: Optional[int] = Field(default=None, ge=1)
    max_pages: Optional[int] = Field(default=None, ge=1)
    max_seconds: Optional[int] = Field(default=None, ge=1)
    max_tool_calls: int = Field(default=20, ge=1)
    max_repair_attempts: int = Field(default=2, ge=0, le=2)


class Ambiguity(ContractModel):
    ambiguity_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    candidates: Tuple[str, ...] = Field(min_length=2)
    material: bool = True
    resolved: bool = False
    resolution: Optional[str] = None

    @model_validator(mode="after")
    def validate_resolution(self) -> "Ambiguity":
        if self.resolved and not self.resolution:
            raise ValueError("已解决歧义必须记录 resolution")
        if not self.resolved and self.resolution:
            raise ValueError("未解决歧义不得提前写入 resolution")
        return self


class SemanticTaskPlan(ContractModel):
    """来源无关的逻辑计划；后续绑定和物理选型不得改写这里的用户语义。"""

    spec_version: str = Field(default="1", pattern=r"^1$")
    plan_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    revision: int = Field(default=1, ge=1)
    task_family: TaskFamily
    objective: ObjectiveSpec
    source_scope: SourceScope
    input_contract: InputContract = Field(default_factory=InputContract)
    selection: Tuple[SelectionPredicate, ...] = ()
    projection: Tuple[ProjectionField, ...] = ()
    record_grain: Optional[str] = Field(default=None, min_length=1)
    operations: Tuple[OperationSpec, ...] = ()
    combine: CombineSpec = Field(default_factory=CombineSpec)
    content_policy: ContentPolicy = ContentPolicy.VERBATIM
    evidence_policy: EvidencePolicy = Field(default_factory=EvidencePolicy)
    delivery: DeliverySpec
    postconditions: PostconditionSpec = Field(default_factory=PostconditionSpec)
    risk_policy: RiskPolicy = Field(default_factory=RiskPolicy)
    budgets: BudgetSpec = Field(default_factory=BudgetSpec)
    ambiguities: Tuple[Ambiguity, ...] = ()

    @model_validator(mode="after")
    def validate_semantics(self) -> "SemanticTaskPlan":
        if (
            self.task_family != TaskFamily.DISCOVER
            and not self.source_scope.artifact_ids
            and not self.source_scope.source_ids
        ):
            raise ValueError("非 discover 计划必须限定 artifact_ids 或 source_ids")
        if self.task_family == TaskFamily.TABULAR_TRANSFORM and not self.record_grain:
            raise ValueError("表格变换必须显式声明 record_grain")
        if (
            self.task_family == TaskFamily.EXTRACT
            and not self.source_scope.whole_document
            and not self.source_scope.section_patterns
            and not self.source_scope.pages
            and not self.selection
        ):
            raise ValueError(
                "文档限定提取必须声明章节/概念、页码或选择范围；"
                "只有用户明确要求全文转换时才能使用 whole_document"
            )
        if (
            self.source_scope.whole_document
            and (
                self.source_scope.section_patterns
                or self.source_scope.pages
                or self.selection
            )
        ):
            raise ValueError("whole_document 不得与章节、页码或选择条件同时使用")
        names = [field.name for field in self.projection]
        if len(names) != len(set(names)):
            raise ValueError("projection 不得包含重复字段")
        visible_columns = tuple(field.alias or field.name for field in self.projection)
        if self.projection and not self.postconditions.exact_visible_columns:
            raise ValueError("projection 必须配套 exact_visible_columns 后置条件")
        if (
            self.postconditions.exact_visible_columns
            and self.postconditions.exact_visible_columns != visible_columns
        ):
            raise ValueError("exact_visible_columns 必须与 projection 的可见列完全一致")
        for predicate in self.selection:
            has_postcondition = any(
                check.field == predicate.field
                and check.operator == predicate.operator
                and check.value == predicate.value
                and check.values == predicate.values
                and check.required_ratio == 1.0
                for check in self.postconditions.predicates
            )
            if not has_postcondition:
                raise ValueError("每个 selection 都必须有 required_ratio=1.0 的谓词后置条件")
        formats = list(self.delivery.formats)
        if len(formats) != len(set(formats)):
            raise ValueError("delivery.formats 不得重复")
        if (
            self.combine.mode == CombineMode.ONE_TABLE
            and self.task_family != TaskFamily.TABULAR_TRANSFORM
        ):
            raise ValueError("one_table 只能用于表格变换")
        if self.combine.mode == CombineMode.ONE_TABLE and self.postconditions.table_count != 1:
            raise ValueError("one_table 必须配套 table_count=1 后置条件")
        if self.task_family == TaskFamily.SUMMARIZE and self.content_policy != ContentPolicy.SUMMARIZED:
            raise ValueError("summarize 任务必须使用 summarized 内容政策")
        if self.task_family == TaskFamily.TRANSLATE and self.content_policy != ContentPolicy.TRANSLATED:
            raise ValueError("translate 任务必须使用 translated 内容政策")
        if (
            self.task_family == TaskFamily.COMPARE
            and not any(
                item.operation == OperationType.COMPARE
                for item in self.operations
            )
        ):
            raise ValueError("compare 任务必须包含 compare 操作")
        if (
            self.task_family == TaskFamily.AUDIT
            and not any(
                item.operation == OperationType.AUDIT
                for item in self.operations
            )
        ):
            raise ValueError("audit 任务必须包含 audit 操作及已确认规则")
        if (
            self.evidence_policy.require_evidence
            and self.postconditions.minimum_evidence_coverage
            < self.evidence_policy.minimum_coverage
        ):
            raise ValueError("证据后置条件不得低于 evidence_policy")
        return self

    @property
    def is_executable(self) -> bool:
        ambiguities_resolved = not any(
            item.material and not item.resolved for item in self.ambiguities
        )
        external_confirmed = not (
            self.risk_policy.execution_boundary == ExecutionBoundary.EXTERNAL_API
            and not self.risk_policy.external_api_confirmed
        )
        return ambiguities_resolved and external_confirmed

    def canonical_hash(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class BindingEvidence(ContractModel):
    source_ref: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    samples: Tuple[str, ...] = ()


class BindingTarget(ContractModel):
    """一个真实来源目标；同一语义可跨文件、表或章节绑定多个目标。"""

    physical_ref: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    target_kind: str = Field(
        pattern=r"^(table_column|document_section|document_element|document_table_cell)$"
    )
    confidence: float = Field(ge=0, le=1)
    evidence: Tuple[BindingEvidence, ...] = Field(min_length=1)


class Binding(ContractModel):
    semantic_ref: str = Field(min_length=1)
    status: BindingStatus
    confidence: float = Field(ge=0, le=1)
    targets: Tuple[BindingTarget, ...] = ()

    @model_validator(mode="after")
    def validate_binding(self) -> "Binding":
        if self.status == BindingStatus.BOUND:
            if not self.targets:
                raise ValueError("bound 绑定必须包含至少一个 target")
            if self.confidence != min(item.confidence for item in self.targets):
                raise ValueError("binding confidence 必须等于全部目标中的最低置信度")
        elif self.targets:
            raise ValueError("ambiguous/missing 绑定不得提前写入 targets")
        return self


class BoundPlan(ContractModel):
    """逻辑计划与真实来源结构绑定后的不可变快照。"""

    spec_version: str = Field(default="2", pattern=r"^2$")
    bound_plan_id: str = Field(min_length=1)
    logical_plan_id: str = Field(min_length=1)
    logical_plan_revision: int = Field(ge=1)
    logical_plan_hash: str = Field(pattern=_SHA256_PATTERN)
    binding_revision: int = Field(default=1, ge=1)
    input_artifact_hashes: Dict[str, str] = Field(min_length=1)
    inspection_report_hashes: Dict[str, str] = Field(min_length=1)
    binder_version: str = Field(min_length=1)
    threshold_version: str = Field(min_length=1)
    bindings: Tuple[Binding, ...] = ()
    unresolved_ambiguities: Tuple[Ambiguity, ...] = ()
    bound_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_hashes(self) -> "BoundPlan":
        if any(not artifact_id for artifact_id in self.input_artifact_hashes):
            raise ValueError("input_artifact_hashes 的 artifact_id 不得为空")
        if any(
            re.fullmatch(_SHA256_PATTERN, value) is None
            for value in self.input_artifact_hashes.values()
        ):
            raise ValueError("input_artifact_hashes 必须是小写 sha256")
        if set(self.input_artifact_hashes) != set(self.inspection_report_hashes):
            raise ValueError("每个输入制品必须有对应 inspection report hash")
        if any(
            re.fullmatch(_SHA256_PATTERN, value) is None
            for value in self.inspection_report_hashes.values()
        ):
            raise ValueError("inspection_report_hashes 必须是小写 sha256")
        semantic_refs = [binding.semantic_ref for binding in self.bindings]
        if len(semantic_refs) != len(set(semantic_refs)):
            raise ValueError("同一 semantic_ref 不得重复绑定")
        return self

    def canonical_hash(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json", exclude={"bound_at"}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @property
    def is_executable(self) -> bool:
        bindings_ready = all(item.status == BindingStatus.BOUND for item in self.bindings)
        ambiguities_ready = not any(
            item.material and not item.resolved for item in self.unresolved_ambiguities
        )
        return bindings_ready and ambiguities_ready


class CapabilityLimits(ContractModel):
    max_bytes: Optional[int] = Field(default=None, ge=1)
    max_rows: Optional[int] = Field(default=None, ge=1)
    max_pages: Optional[int] = Field(default=None, ge=1)
    timeout_seconds: int = Field(ge=1)
    max_concurrency: int = Field(default=1, ge=1)


class CapabilityManifest(ContractModel):
    capability_id: str = Field(pattern=_CAPABILITY_ID_PATTERN)
    version: str = Field(min_length=1)
    accepts: Tuple[str, ...] = Field(min_length=1)
    produces: Tuple[str, ...] = Field(min_length=1)
    operations: Tuple[str, ...] = Field(min_length=1)
    deterministic: bool
    evidence_preserving: bool
    side_effect: SideEffect = SideEffect.NONE
    network: NetworkAccess = NetworkAccess.NONE
    resource_class: ResourceClass
    limits: CapabilityLimits
    healthcheck: str = Field(min_length=1)
    parameters_schema: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_unique_declarations(self) -> "CapabilityManifest":
        for name, values in (
            ("accepts", self.accepts),
            ("produces", self.produces),
            ("operations", self.operations),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} 不得包含重复声明")
        return self


class ArtifactRef(ContractModel):
    artifact_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)
    size_bytes: int = Field(ge=0)


class ExecutionLedger(ContractModel):
    input_records: Optional[int] = Field(default=None, ge=0)
    output_records: Optional[int] = Field(default=None, ge=0)
    filtered_out_records: int = Field(default=0, ge=0)
    rejected_records: int = Field(default=0, ge=0)
    input_bytes: Optional[int] = Field(default=None, ge=0)
    output_bytes: Optional[int] = Field(default=None, ge=0)


class LineageEvent(ContractModel):
    event: str = Field(min_length=1)
    input_artifact_ids: Tuple[str, ...] = ()
    output_artifact_ids: Tuple[str, ...] = ()
    details: Dict[str, Any] = Field(default_factory=dict)


class ResourceUsage(ContractModel):
    duration_ms: int = Field(ge=0)
    peak_memory_bytes: Optional[int] = Field(default=None, ge=0)
    peak_gpu_memory_bytes: Optional[int] = Field(default=None, ge=0)
    cpu_seconds: Optional[float] = Field(default=None, ge=0)


class ToolResult(ContractModel):
    call_id: str = Field(min_length=1)
    capability_id: str = Field(pattern=_CAPABILITY_ID_PATTERN)
    capability_version: str = Field(min_length=1)
    status: ToolStatus
    input_artifacts: Tuple[ArtifactRef, ...] = ()
    output_artifacts: Tuple[ArtifactRef, ...] = ()
    ledger: ExecutionLedger = Field(default_factory=ExecutionLedger)
    lineage: Tuple[LineageEvent, ...] = ()
    facts: Dict[str, Any] = Field(default_factory=dict)
    warnings: Tuple[str, ...] = ()
    rejects: Tuple[str, ...] = ()
    failure_kind: Optional[FailureKind] = None
    error_message: Optional[str] = None
    retryable: bool = False
    tool_config_summary: Dict[str, Any] = Field(default_factory=dict)
    resource_usage: ResourceUsage

    @model_validator(mode="after")
    def validate_result(self) -> "ToolResult":
        if self.status == ToolStatus.SUCCEEDED:
            if self.failure_kind or self.error_message or self.retryable:
                raise ValueError("成功结果不得携带失败或重试标记")
            if not self.output_artifacts and not self.facts:
                raise ValueError("成功结果必须返回制品或可验证 facts")
            if self.output_artifacts and not self.lineage:
                raise ValueError("产生输出制品的成功结果必须记录 lineage")
        elif self.status in {ToolStatus.FAILED, ToolStatus.PARTIAL}:
            if not self.failure_kind or not self.error_message:
                raise ValueError("失败/部分成功必须包含 failure_kind 和 error_message")
        elif self.status == ToolStatus.NEEDS_INPUT:
            if self.failure_kind != FailureKind.NEEDS_USER or not self.error_message:
                raise ValueError("needs_input 必须明确标记 needs_user 和问题")
        if self.retryable and self.failure_kind not in {
            FailureKind.TRANSIENT,
            FailureKind.RESOURCE_EXHAUSTED,
        }:
            raise ValueError("只有暂时性或资源耗尽错误可自动重试")
        return self


class VerificationCheck(ContractModel):
    code: str = Field(min_length=1)
    passed: bool
    expected: Any = None
    actual: Any = None
    message: str = Field(min_length=1)
    repairable: bool = False
    evidence_refs: Tuple[str, ...] = ()


class VerificationReport(ContractModel):
    report_id: str = Field(min_length=1)
    logical_plan_id: str = Field(min_length=1)
    logical_plan_revision: int = Field(ge=1)
    logical_plan_hash: str = Field(pattern=_SHA256_PATTERN)
    status: VerificationStatus
    checks: Tuple[VerificationCheck, ...] = Field(min_length=1)
    repair_attempt: int = Field(default=0, ge=0, le=2)
    failure_fingerprint: Optional[str] = Field(default=None, pattern=_SHA256_PATTERN)
    generated_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_status(self) -> "VerificationReport":
        all_passed = all(check.passed for check in self.checks)
        if self.status == VerificationStatus.PASS and not all_passed:
            raise ValueError("pass 报告的全部检查必须通过")
        if self.status == VerificationStatus.FAIL and all_passed:
            raise ValueError("fail 报告至少需要一项失败检查")
        if self.status == VerificationStatus.NEEDS_USER:
            if all_passed or not any(not check.passed and not check.repairable for check in self.checks):
                raise ValueError("needs_user 必须包含无法自动修复的失败检查")
        if self.status != VerificationStatus.PASS and not self.failure_fingerprint:
            raise ValueError("失败或需用户处理时必须记录 failure_fingerprint")
        if self.status == VerificationStatus.PASS and self.failure_fingerprint:
            raise ValueError("通过报告不得携带 failure_fingerprint")
        return self

    @property
    def authoritative_output_allowed(self) -> bool:
        return self.status == VerificationStatus.PASS
