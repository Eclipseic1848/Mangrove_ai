# -*- coding: utf-8 -*-
"""把已确认 BoundPlan 编译为零 LLM、可审计的表格 PhysicalPlan。"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import uuid
from typing import Any, Mapping, Sequence

from .inspection_models import SourceInspectionReport, SourceKind
from .models import (
    BoundPlan,
    OperationType,
    SemanticTaskPlan,
    TaskFamily,
)
from .physical_models import (
    AggregateExpression,
    AggregateFunction,
    AggregateStep,
    DeduplicateStep,
    FilterCondition,
    FilterStep,
    JoinCardinality,
    JoinKey,
    JoinKind,
    JoinStep,
    PhysicalPlan,
    PhysicalPlanStatus,
    PhysicalSource,
    ProjectColumn,
    ProjectStep,
    RuntimePolicy,
    RuntimeProfileName,
    SortDirection,
    SortKey,
    SortStep,
    SourceColumn,
    UnionStep,
)


CAPABILITY_VERSION = "1.0.0"


def runtime_policy(profile: RuntimeProfileName) -> RuntimePolicy:
    """返回经批次 0 本机基准约束的保守初始配置。"""

    if profile == RuntimeProfileName.SERVER:
        return RuntimePolicy(
            profile=profile,
            threads=8,
            memory_limit="32GB",
            timeout_seconds=600,
            arrow_batch_rows=262_144,
            max_temp_bytes=100 * 1024**3,
            max_input_bytes=10 * 1024**3,
            max_input_rows=100_000_000,
        )
    return RuntimePolicy(
        profile=profile,
        threads=4,
        memory_limit="4GB",
        timeout_seconds=120,
        arrow_batch_rows=65_536,
        max_temp_bytes=10 * 1024**3,
        max_input_bytes=10 * 1024**3,
        max_input_rows=100_000_000,
    )


def _needs_user_plan(
    plan: SemanticTaskPlan,
    bound_plan: BoundPlan,
    profile: RuntimeProfileName,
    *diagnostics: str,
) -> PhysicalPlan:
    policy = runtime_policy(profile)
    policy = policy.model_copy(
        update={
            "max_input_bytes": plan.budgets.max_bytes or policy.max_input_bytes,
            "max_input_rows": plan.budgets.max_rows or policy.max_input_rows,
            "timeout_seconds": plan.budgets.max_seconds or policy.timeout_seconds,
        }
    )
    return PhysicalPlan(
        physical_plan_id=f"physical_{uuid.uuid4().hex[:16]}",
        logical_plan_id=plan.plan_id,
        logical_plan_revision=plan.revision,
        logical_plan_hash=plan.canonical_hash(),
        bound_plan_id=bound_plan.bound_plan_id,
        bound_plan_hash=bound_plan.canonical_hash(),
        binding_revision=bound_plan.binding_revision,
        status=PhysicalPlanStatus.NEEDS_USER,
        capability_version=CAPABILITY_VERSION,
        runtime_policy=policy,
        diagnostics=tuple(diagnostics),
    )


def _as_string_tuple(value: Any, *, name: str) -> tuple[str, ...]:
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, (list, tuple)) and all(
        isinstance(item, str) and item for item in value
    ):
        values = tuple(value)
    else:
        raise ValueError(f"{name} 必须是非空字符串数组")
    if not values:
        raise ValueError(f"{name} 不得为空")
    return values


def _sort_keys(params: Mapping[str, Any]) -> tuple[SortKey, ...]:
    raw = params.get("keys")
    if raw is None:
        columns = _as_string_tuple(
            params.get("columns", params.get("column")),
            name="sort.columns",
        )
        direction = SortDirection(str(params.get("direction", "asc")).lower())
        return tuple(SortKey(column=item, direction=direction) for item in columns)
    if not isinstance(raw, list) or not raw:
        raise ValueError("sort.keys 必须是非空数组")
    return tuple(SortKey.model_validate(item) for item in raw)


def _aggregate_expressions(
    params: Mapping[str, Any],
) -> tuple[AggregateExpression, ...]:
    raw = params.get("aggregates")
    if not isinstance(raw, list) or not raw:
        raise ValueError("aggregate.aggregates 必须是非空数组")
    return tuple(AggregateExpression.model_validate(item) for item in raw)


def _source_inventory(
    reports: Sequence[SourceInspectionReport],
) -> tuple[dict[str, Any], dict[str, SourceInspectionReport]]:
    tables: dict[str, Any] = {}
    by_artifact: dict[str, SourceInspectionReport] = {}
    for report in reports:
        by_artifact[report.artifact_id] = report
        for table in report.tables:
            tables[table.table_ref] = table
    return tables, by_artifact


def compile_physical_plan(
    plan: SemanticTaskPlan,
    bound_plan: BoundPlan,
    reports: Sequence[SourceInspectionReport],
    *,
    profile: RuntimeProfileName = RuntimeProfileName.WINDOWS_LOCAL,
) -> PhysicalPlan:
    """确定性编译；任何结构冲突都停在 needs_user，不做猜测或自动转换。"""

    if plan.task_family != TaskFamily.TABULAR_TRANSFORM:
        return _needs_user_plan(
            plan, bound_plan, profile, "批次 3 只执行 tabular_transform"
        )
    if not plan.is_executable or not bound_plan.is_executable:
        return _needs_user_plan(
            plan, bound_plan, profile, "逻辑计划或来源绑定尚未确认"
        )
    if bound_plan.logical_plan_id != plan.plan_id:
        return _needs_user_plan(
            plan, bound_plan, profile, "BoundPlan 与逻辑计划 ID 不一致"
        )
    if bound_plan.logical_plan_revision != plan.revision:
        return _needs_user_plan(
            plan, bound_plan, profile, "BoundPlan 与逻辑计划 revision 不一致"
        )
    if bound_plan.logical_plan_hash != plan.canonical_hash():
        return _needs_user_plan(
            plan, bound_plan, profile, "BoundPlan 与逻辑计划哈希不一致"
        )
    if any(report.source_kind != SourceKind.TABULAR for report in reports):
        return _needs_user_plan(
            plan, bound_plan, profile, "批次 3 不执行文档来源"
        )

    tables, reports_by_artifact = _source_inventory(reports)
    targets_by_table: dict[str, dict[str, Any]] = defaultdict(dict)
    for binding in bound_plan.bindings:
        if not binding.semantic_ref.startswith("field:"):
            continue
        semantic_name = binding.semantic_ref.split(":", 1)[1]
        for target in binding.targets:
            table_ref = target.physical_ref.rsplit("/column/", 1)[0]
            targets_by_table[table_ref][semantic_name] = target

    derived_fields = {
        str(aggregate.get("output"))
        for operation in plan.operations
        if operation.operation == OperationType.AGGREGATE
        for aggregate in operation.params.get("aggregates", [])
        if isinstance(aggregate, Mapping) and aggregate.get("output")
    }
    operation_fields: set[str] = set()
    for operation in plan.operations:
        params = operation.params
        for key in ("field", "column", "by", "group_by", "fields", "columns", "keys"):
            value = params.get(key)
            if isinstance(value, str):
                operation_fields.add(value)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, str):
                        operation_fields.add(item)
                    elif isinstance(item, Mapping) and item.get("column"):
                        operation_fields.add(str(item["column"]))
        if operation.operation == OperationType.AGGREGATE:
            for aggregate in params.get("aggregates", []):
                if isinstance(aggregate, Mapping) and aggregate.get("column"):
                    operation_fields.add(str(aggregate["column"]))
    required = (
        {item.field for item in plan.selection}
        | {
            item.name
            for item in plan.projection
            if item.name not in derived_fields
        }
        | operation_fields
    )
    sources: list[PhysicalSource] = []
    table_to_source: dict[str, str] = {}
    diagnostics: list[str] = []
    for source_index, table_ref in enumerate(sorted(targets_by_table)):
        table = tables.get(table_ref)
        if table is None:
            diagnostics.append(f"绑定引用了不存在的表：{table_ref}")
            continue
        missing = required - set(targets_by_table[table_ref])
        if missing:
            diagnostics.append(
                f"{table_ref} 缺少列：{', '.join(sorted(missing))}"
            )
            continue
        report = reports_by_artifact[table.artifact_id]
        suffix = Path(report.original_name).suffix.lower().lstrip(".")
        detected_format = suffix if suffix in {
            "csv", "tsv", "xlsx", "parquet", "json", "jsonl"
        } else report.detected_format
        columns = []
        for semantic_name, target in sorted(
            targets_by_table[table_ref].items()
        ):
            column_profile = next(
                (
                    item
                    for item in table.columns
                    if item.physical_ref == target.physical_ref
                ),
                None,
            )
            if column_profile is None:
                diagnostics.append(
                    f"绑定列不在检查报告中：{target.physical_ref}"
                )
                continue
            columns.append(
                SourceColumn(
                    semantic_name=semantic_name,
                    physical_ref=column_profile.physical_ref,
                    column_index=column_profile.column_index,
                    output_name=semantic_name,
                    inferred_type=column_profile.inferred_type,
                )
            )
        source_id = f"source_{source_index}"
        table_to_source[table_ref] = source_id
        sources.append(
            PhysicalSource(
                source_id=source_id,
                artifact_id=table.artifact_id,
                artifact_sha256=report.artifact_sha256,
                table_ref=table_ref,
                table_index=table.table_index,
                table_name=table.name,
                header_row=table.header_row,
                detected_format=detected_format,
                columns=tuple(columns),
            )
        )
    if diagnostics or not sources:
        return _needs_user_plan(plan, bound_plan, profile, *(
            diagnostics or ["没有可执行表格来源"]
        ))

    steps = []
    current_id: str
    if len(sources) > 1:
        steps.append(
            UnionStep(
                step_id="step_union_sources",
                input_ids=tuple(item.source_id for item in sources),
            )
        )
        current_id = "step_union_sources"
    else:
        current_id = sources[0].source_id

    if plan.selection:
        steps.append(
            FilterStep(
                step_id="step_filter",
                input_ids=(current_id,),
                conditions=tuple(
                    FilterCondition(
                        column=item.field,
                        operator=item.operator,
                        value=item.value,
                        values=item.values,
                        case_sensitive=item.case_sensitive,
                    )
                    for item in plan.selection
                ),
            )
        )
        current_id = "step_filter"

    group_by: tuple[str, ...] = ()
    try:
        for index, operation in enumerate(plan.operations):
            step_id = f"step_operation_{index}"
            params = operation.params
            if operation.operation == OperationType.UNION:
                continue
            if operation.operation == OperationType.SORT:
                step = SortStep(
                    step_id=step_id,
                    input_ids=(current_id,),
                    keys=_sort_keys(params),
                )
            elif operation.operation == OperationType.DEDUPLICATE:
                step = DeduplicateStep(
                    step_id=step_id,
                    input_ids=(current_id,),
                    keys=_as_string_tuple(params.get("keys"), name="deduplicate.keys"),
                    order_by=tuple(
                        SortKey.model_validate(item)
                        for item in params.get("order_by", [])
                    ),
                )
            elif operation.operation == OperationType.GROUP:
                group_by = _as_string_tuple(
                    params.get("columns", params.get("group_by")),
                    name="group.columns",
                )
                continue
            elif operation.operation == OperationType.AGGREGATE:
                active_group = tuple(params.get("group_by") or group_by)
                step = AggregateStep(
                    step_id=step_id,
                    input_ids=(current_id,),
                    group_by=active_group,
                    aggregates=_aggregate_expressions(params),
                )
            elif operation.operation == OperationType.JOIN:
                left_ref = str(params.get("left_table_ref", ""))
                right_ref = str(params.get("right_table_ref", ""))
                raw_keys = params.get("keys")
                if (
                    left_ref not in table_to_source
                    or right_ref not in table_to_source
                    or not isinstance(raw_keys, list)
                ):
                    raise ValueError(
                        "join 必须指定已绑定的 left_table_ref、right_table_ref 和 keys"
                    )
                step = JoinStep(
                    step_id=step_id,
                    input_ids=(
                        table_to_source[left_ref],
                        table_to_source[right_ref],
                    ),
                    keys=tuple(JoinKey.model_validate(item) for item in raw_keys),
                    join_kind=JoinKind(params.get("join_kind", "inner")),
                    cardinality=JoinCardinality(params["cardinality"]),
                )
            else:
                raise ValueError(
                    f"批次 3 不支持操作：{operation.operation.value}"
                )
            steps.append(step)
            current_id = step_id
        if group_by and not any(
            isinstance(item, AggregateStep) for item in steps
        ):
            raise ValueError("group 必须与 aggregate 配套")
    except (KeyError, TypeError, ValueError) as exc:
        return _needs_user_plan(
            plan, bound_plan, profile, f"操作参数需要用户确认：{exc}"
        )

    if plan.projection:
        steps.append(
            ProjectStep(
                step_id="step_project",
                input_ids=(current_id,),
                columns=tuple(
                    ProjectColumn(
                        source=item.name,
                        output=item.alias or item.name,
                    )
                    for item in plan.projection
                ),
            )
        )
        current_id = "step_project"
    if not steps:
        return _needs_user_plan(
            plan, bound_plan, profile, "计划没有任何确定性操作"
        )

    visible_columns = (
        plan.postconditions.exact_visible_columns
        or tuple(item.output_name for item in sources[0].columns)
    )
    return PhysicalPlan(
        physical_plan_id=f"physical_{uuid.uuid4().hex[:16]}",
        logical_plan_id=plan.plan_id,
        logical_plan_revision=plan.revision,
        logical_plan_hash=plan.canonical_hash(),
        bound_plan_id=bound_plan.bound_plan_id,
        bound_plan_hash=bound_plan.canonical_hash(),
        binding_revision=bound_plan.binding_revision,
        status=PhysicalPlanStatus.READY,
        capability_version=CAPABILITY_VERSION,
        sources=tuple(sources),
        steps=tuple(steps),
        final_step_id=current_id,
        visible_columns=visible_columns,
        expected_row_count=plan.postconditions.expected_row_count,
        runtime_policy=runtime_policy(profile),
    )
