# -*- coding: utf-8 -*-
"""Phase 4A 文档结构、证据与复核契约。"""
from __future__ import annotations

from enum import Enum
import hashlib
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


class PageContentKind(str, Enum):
    """页面内容形态；混合文档在页级分别路由。"""

    DIGITAL = "digital"
    SCANNED = "scanned"
    MIXED = "mixed"


class ElementType(str, Enum):
    DOCUMENT = "document"
    PAGE = "page"
    SECTION = "section"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    QUOTE = "quote"
    TABLE = "table"
    CELL = "cell"
    IMAGE = "image"


class ExtractionStatus(str, Enum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    LOW_CONFIDENCE = "low_confidence"


class ResultShape(str, Enum):
    """与业务领域无关的结果形态；新场景通过组合而非新增专用任务类型。"""

    FIELDS = "fields"
    RECORDS = "records"
    TABLES = "tables"
    DOCUMENT = "document"
    AGGREGATE = "aggregate"


class ResultCardinality(str, Enum):
    ONE = "one"
    MANY = "many"
    ALL = "all"


class ResultContract(BaseModel):
    """意图规划后、执行前由用户确认的结果契约。"""

    shape: ResultShape = ResultShape.FIELDS
    cardinality: ResultCardinality = ResultCardinality.ONE
    record_grain: Optional[str] = None
    renderer: str = "field_cards"
    output_formats: List[str] = Field(default_factory=lambda: ["jsonl", "xlsx"])
    exhaustive: bool = False
    merge_tables: bool = Field(
        default=False,
        description="仅在用户明确要求时，将多张原表按列位置无损合并为一张表",
    )


class TaskGoal(BaseModel):
    """用户确认后的文档任务目标。"""

    objective: str
    document_types: List[str] = Field(default_factory=list)
    success_criteria: List[str] = Field(default_factory=list)


class DiscoverySpec(BaseModel):
    """模型和检索器允许访问的制品与范围。"""

    artifact_ids: List[str] = Field(min_length=1)
    pages: Dict[str, List[int]] = Field(
        default_factory=dict,
        description="artifact_id 到允许页码的映射；空列表表示该制品全部页",
    )
    section_patterns: List[str] = Field(default_factory=list)


class ExtractionFieldSpec(BaseModel):
    name: str
    dtype: str = "string"
    required: bool = False
    description: Optional[str] = None
    require_evidence: bool = True
    min_confidence: float = Field(default=0.90, ge=0, le=1)


class ExtractionSpec(BaseModel):
    """经用户确认后才用于字段抽取的规格。"""

    spec_version: str = "3"
    goal: TaskGoal
    discovery: DiscoverySpec
    fields: List[ExtractionFieldSpec] = Field(default_factory=list, max_length=30)
    result_contract: ResultContract = Field(default_factory=ResultContract)
    conflict_policy: str = Field(default="review", pattern=r"^(review|keep_all|reject)$")

    @model_validator(mode="after")
    def require_fields_for_structured_results(self) -> "ExtractionSpec":
        if self.result_contract.shape != ResultShape.DOCUMENT and not self.fields:
            raise ValueError("除 document 外的结果形态至少需要一个字段")
        return self


class BoundingBox(BaseModel):
    """矩形坐标，坐标空间必须显式记录。"""

    x0: float = Field(ge=0)
    y0: float = Field(ge=0)
    x1: float = Field(gt=0)
    y1: float = Field(gt=0)
    coordinate_space: str = Field(
        description="pdf_points、image_pixels 或 normalized_1000"
    )

    @model_validator(mode="after")
    def validate_order(self) -> "BoundingBox":
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("bbox 必须满足 x1>x0 且 y1>y0")
        return self


class DocumentElement(BaseModel):
    """第三方解析结果进入 Mangrove 后的统一文档元素。"""

    element_id: str
    artifact_id: str
    page: int = Field(ge=1)
    element_type: ElementType
    text: Optional[str] = None
    bbox: Optional[BoundingBox] = None
    reading_order: Optional[int] = Field(default=None, ge=0)
    parent_element_id: Optional[str] = None
    extractor: str
    extractor_version: str
    confidence: float = Field(default=1.0, ge=0, le=1)
    review_required: bool = False
    raw_result_ref: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def enforce_visual_candidate_review(self) -> "DocumentElement":
        if self.extractor == "qwen_vl" and self.bbox is None and not self.review_required:
            raise ValueError("Qwen 候选缺少确定性 bbox 时必须进入复核")
        return self


class EvidenceRef(BaseModel):
    """字段值指向不可变制品和文档元素的证据引用。"""

    artifact_id: str
    element_id: str
    page: int = Field(ge=1)
    bbox: Optional[BoundingBox] = None
    quote: Optional[str] = None
    quote_sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    extractor: str
    extractor_version: str
    confidence: float = Field(ge=0, le=1)
    raw_result_ref: Optional[str] = None
    location: Dict[str, Any] = Field(
        default_factory=dict,
        description="无视觉 bbox 时用于复核的确定性结构位置，如 DOCX 段落或表格行",
    )

    @model_validator(mode="after")
    def require_verifiable_quote(self) -> "EvidenceRef":
        if not (self.quote and self.quote.strip()) and not self.quote_sha256:
            raise ValueError("EvidenceRef 必须包含 quote 或 quote_sha256")
        if self.quote and self.quote_sha256:
            actual = hashlib.sha256(self.quote.encode("utf-8")).hexdigest()
            if actual != self.quote_sha256:
                raise ValueError("quote_sha256 与 quote 不一致")
        return self


class ExtractedField(BaseModel):
    """证据约束字段；found 状态禁止无证据填值。"""

    name: str
    value: Any = None
    status: ExtractionStatus
    evidence_refs: List[EvidenceRef] = Field(default_factory=list)
    candidates: List[Any] = Field(default_factory=list)
    review_reason: Optional[str] = None

    @model_validator(mode="after")
    def enforce_evidence_contract(self) -> "ExtractedField":
        if self.status == ExtractionStatus.FOUND:
            if self.value is None or not self.evidence_refs:
                raise ValueError("found 字段必须有非空值和至少一条证据")
        elif self.status == ExtractionStatus.NOT_FOUND and self.value is not None:
            raise ValueError("not_found 字段不得携带推测值")
        return self


class ExtractedRecord(BaseModel):
    """多记录任务的一行；每个字段继续复用证据约束契约。"""

    record_id: str
    fields: List[ExtractedField] = Field(default_factory=list)
    status: ExtractionStatus = ExtractionStatus.FOUND
    source_artifact_ids: List[str] = Field(default_factory=list)
    review_required: bool = False

    @property
    def values(self) -> Dict[str, Any]:
        return {field.name: field.value for field in self.fields}


class ExtractedTable(BaseModel):
    """确定性解析器产出的原始表格。"""

    table_id: str
    name: str
    artifact_id: str
    page: int = Field(ge=1)
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_element_ids: List[str] = Field(default_factory=list)


class ExtractedDocument(BaseModel):
    """确定性拼接的连续文档；正文片段必须可回到原始元素。"""

    document_id: str
    title: str
    content: str = Field(min_length=1)
    source_artifact_ids: List[str] = Field(min_length=1)
    evidence_refs: List[EvidenceRef] = Field(min_length=1)


class ExtractedAggregate(BaseModel):
    """跨当前任务范围汇总的一行结果；指标继续复用字段证据契约。"""

    aggregate_id: str
    fields: List[ExtractedField] = Field(min_length=1)
    status: ExtractionStatus
    source_artifact_ids: List[str] = Field(default_factory=list)
    review_required: bool = False

    @property
    def values(self) -> Dict[str, Any]:
        return {field.name: field.value for field in self.fields}


class ReviewPolicy(BaseModel):
    min_confidence: float = Field(default=0.90, ge=0, le=1)
    require_bbox: bool = True
    review_conflicts: bool = True


class ReviewTask(BaseModel):
    task_id: str
    artifact_id: str
    page: int = Field(ge=1)
    field_name: str
    record_id: Optional[str] = None
    reasons: List[str]
    candidates: List[Any] = Field(default_factory=list)
    status: str = "pending"
    resolution: Optional[Dict[str, Any]] = None
