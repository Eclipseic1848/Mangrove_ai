# -*- coding: utf-8 -*-
"""Phase 4B 批次 3 的确定性表格物理计划契约。"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Annotated, Any, Dict, Literal, Optional, Tuple, Union

from pydantic import Field, model_validator

from .models import ContractModel, PredicateOperator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RuntimeProfileName(str, Enum):
    WINDOWS_LOCAL = "windows_local"
    SERVER = "server"


class PhysicalPlanStatus(str, Enum):
    READY = "ready"
    NEEDS_USER = "needs_user"


class ExecutionRunStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_USER = "needs_user"


class SortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


class JoinKind(str, Enum):
    INNER = "inner"
    LEFT = "left"


class JoinCardinality(str, Enum):
    ONE_TO_ONE = "one_to_one"
    MANY_TO_ONE = "many_to_one"
    ONE_TO_MANY = "one_to_many"


class AggregateFunction(str, Enum):
    COUNT = "count"
    SUM = "sum"
    MIN = "min"
    MAX = "max"
    AVG = "avg"


class SourceColumn(ContractModel):
    semantic_name: str = Field(min_length=1)
    physical_ref: str = Field(min_length=1)
    column_index: int = Field(ge=0)
    output_name: str = Field(min_length=1)
    inferred_type: str = Field(min_length=1)


class PhysicalSource(ContractModel):
    source_id: str = Field(pattern=r"^source_[a-z0-9_]+$")
    artifact_id: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    table_ref: str = Field(min_length=1)
    table_index: int = Field(ge=0)
    table_name: str = Field(min_length=1)
    header_row: int = Field(ge=1)
    detected_format: str = Field(
        pattern=r"^(csv|tsv|xlsx|parquet|json|jsonl)$"
    )
    columns: Tuple[SourceColumn, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_columns(self) -> "PhysicalSource":
        semantic_names = [item.semantic_name for item in self.columns]
        output_names = [item.output_name for item in self.columns]
        indexes = [item.column_index for item in self.columns]
        if len(semantic_names) != len(set(semantic_names)):
            raise ValueError("同一来源的 semantic_name 不得重复")
        if len(output_names) != len(set(output_names)):
            raise ValueError("同一来源的 output_name 不得重复")
        if len(indexes) != len(set(indexes)):
            raise ValueError("同一来源的 column_index 不得重复")
        return self


class FilterCondition(ContractModel):
    column: str = Field(min_length=1)
    operator: PredicateOperator
    value: Any = None
    values: Tuple[Any, ...] = ()
    case_sensitive: bool = False


class FilterStep(ContractModel):
    kind: Literal["filter"] = "filter"
    step_id: str = Field(pattern=r"^step_[a-z0-9_]+$")
    input_ids: Tuple[str, ...] = Field(min_length=1, max_length=1)
    conditions: Tuple[FilterCondition, ...] = Field(min_length=1)


class ProjectColumn(ContractModel):
    source: str = Field(min_length=1)
    output: str = Field(min_length=1)


class ProjectStep(ContractModel):
    kind: Literal["project"] = "project"
    step_id: str = Field(pattern=r"^step_[a-z0-9_]+$")
    input_ids: Tuple[str, ...] = Field(min_length=1, max_length=1)
    columns: Tuple[ProjectColumn, ...] = Field(min_length=1)


class RenameStep(ContractModel):
    kind: Literal["rename"] = "rename"
    step_id: str = Field(pattern=r"^step_[a-z0-9_]+$")
    input_ids: Tuple[str, ...] = Field(min_length=1, max_length=1)
    mapping: Dict[str, str] = Field(min_length=1)


class SortKey(ContractModel):
    column: str = Field(min_length=1)
    direction: SortDirection = SortDirection.ASC
    nulls_last: bool = True


class SortStep(ContractModel):
    kind: Literal["sort"] = "sort"
    step_id: str = Field(pattern=r"^step_[a-z0-9_]+$")
    input_ids: Tuple[str, ...] = Field(min_length=1, max_length=1)
    keys: Tuple[SortKey, ...] = Field(min_length=1)


class UnionStep(ContractModel):
    kind: Literal["union"] = "union"
    step_id: str = Field(pattern=r"^step_[a-z0-9_]+$")
    input_ids: Tuple[str, ...] = Field(min_length=2)


class JoinKey(ContractModel):
    left: str = Field(min_length=1)
    right: str = Field(min_length=1)


class JoinStep(ContractModel):
    kind: Literal["join"] = "join"
    step_id: str = Field(pattern=r"^step_[a-z0-9_]+$")
    input_ids: Tuple[str, ...] = Field(min_length=2, max_length=2)
    keys: Tuple[JoinKey, ...] = Field(min_length=1)
    join_kind: JoinKind = JoinKind.INNER
    cardinality: JoinCardinality


class DeduplicateStep(ContractModel):
    kind: Literal["deduplicate"] = "deduplicate"
    step_id: str = Field(pattern=r"^step_[a-z0-9_]+$")
    input_ids: Tuple[str, ...] = Field(min_length=1, max_length=1)
    keys: Tuple[str, ...] = Field(min_length=1)
    order_by: Tuple[SortKey, ...] = ()


class AggregateExpression(ContractModel):
    function: AggregateFunction
    column: Optional[str] = Field(default=None, min_length=1)
    output: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_column(self) -> "AggregateExpression":
        if self.function != AggregateFunction.COUNT and not self.column:
            raise ValueError(f"{self.function.value} 必须指定 column")
        return self


class AggregateStep(ContractModel):
    kind: Literal["aggregate"] = "aggregate"
    step_id: str = Field(pattern=r"^step_[a-z0-9_]+$")
    input_ids: Tuple[str, ...] = Field(min_length=1, max_length=1)
    group_by: Tuple[str, ...] = ()
    aggregates: Tuple[AggregateExpression, ...] = Field(min_length=1)


PhysicalStep = Annotated[
    Union[
        FilterStep,
        ProjectStep,
        RenameStep,
        SortStep,
        UnionStep,
        JoinStep,
        DeduplicateStep,
        AggregateStep,
    ],
    Field(discriminator="kind"),
]


class RuntimePolicy(ContractModel):
    profile: RuntimeProfileName
    threads: int = Field(ge=1)
    memory_limit: str = Field(pattern=r"^\d+(?:MB|GB)$")
    timeout_seconds: int = Field(ge=1)
    arrow_batch_rows: int = Field(ge=1024)
    max_temp_bytes: int = Field(ge=1)
    max_input_bytes: int = Field(ge=1)
    max_input_rows: int = Field(ge=1)


class PhysicalPlan(ContractModel):
    """只能包含已登记算子和结构化参数，禁止承载自由 SQL 或客户端路径。"""

    spec_version: str = Field(default="1", pattern=r"^1$")
    physical_plan_id: str = Field(min_length=1)
    logical_plan_id: str = Field(min_length=1)
    logical_plan_revision: int = Field(ge=1)
    logical_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    bound_plan_id: str = Field(min_length=1)
    bound_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    binding_revision: int = Field(ge=1)
    status: PhysicalPlanStatus
    capability_id: str = Field(default="table.duckdb", pattern=r"^[a-z0-9._-]+$")
    capability_version: str = Field(min_length=1)
    sources: Tuple[PhysicalSource, ...] = ()
    steps: Tuple[PhysicalStep, ...] = ()
    final_step_id: Optional[str] = Field(default=None, min_length=1)
    visible_columns: Tuple[str, ...] = ()
    expected_row_count: Optional[int] = Field(default=None, ge=0)
    runtime_policy: RuntimePolicy
    diagnostics: Tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=_utc_now)

    @model_validator(mode="after")
    def validate_graph(self) -> "PhysicalPlan":
        if self.status == PhysicalPlanStatus.READY:
            if not self.sources or not self.steps or not self.final_step_id:
                raise ValueError("ready 物理计划必须包含来源、步骤和最终步骤")
            known = {source.source_id for source in self.sources}
            for step in self.steps:
                if step.step_id in known:
                    raise ValueError("步骤 ID 不得与来源 ID 重复")
                unknown = set(step.input_ids) - known
                if unknown:
                    raise ValueError(f"步骤引用未知输入：{sorted(unknown)}")
                known.add(step.step_id)
            if self.final_step_id not in known:
                raise ValueError("final_step_id 不存在")
            if not self.visible_columns:
                raise ValueError("ready 物理计划必须声明可见列")
        elif not self.diagnostics:
            raise ValueError("needs_user 物理计划必须记录原因")
        return self

    def canonical_hash(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json", exclude={"created_at"}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
