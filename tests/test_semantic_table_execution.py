# -*- coding: utf-8 -*-
"""Phase 4B 批次 3：确定性表格执行、血缘和安全失败闸门。"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.semantic_harness.binder import bind_semantic_plan
from src.semantic_harness.capabilities import get_capability_registry
from src.semantic_harness.inspectors.tabular import inspect_tabular_path
from src.semantic_harness.models import (
    CombineMode,
    CombineSpec,
    DeliveryFormat,
    DeliverySpec,
    InputContract,
    ObjectiveSpec,
    PostconditionSpec,
    PredicateOperator,
    PredicatePostcondition,
    ProjectionField,
    SelectionPredicate,
    SemanticTaskPlan,
    SourceScope,
    TaskFamily,
)
from src.semantic_harness.physical_models import (
    AggregateExpression,
    AggregateFunction,
    AggregateStep,
    DeduplicateStep,
    JoinCardinality,
    JoinKey,
    JoinStep,
    PhysicalPlan,
    PhysicalPlanStatus,
    PhysicalSource,
    ProjectColumn,
    ProjectStep,
    RenameStep,
    RuntimeProfileName,
    SortDirection,
    SortKey,
    SortStep,
    SourceColumn,
)
from src.semantic_harness.physical_planner import (
    CAPABILITY_VERSION,
    compile_physical_plan,
    runtime_policy,
)
from src.semantic_harness.table_executor import execute_physical_plan
from src.semantic_harness.table_executor import _validate_select_sql
from src.semantic_harness.table_verifier import verify_table_execution


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _gate_plan(
    artifact_ids: tuple[str, ...],
    *,
    person: str = "谢超群",
) -> SemanticTaskPlan:
    return SemanticTaskPlan(
        plan_id="plan_batch3_gate",
        task_id="task_batch3_gate",
        task_family=TaskFamily.TABULAR_TRANSFORM,
        objective=ObjectiveSpec(
            original_text="只保留谢超群的核销工作量天数和工作量费用",
            normalized_text="筛选姓名并只投影两个业务列",
        ),
        source_scope=SourceScope(
            artifact_ids=artifact_ids,
            table_scope="all_detected_tables",
        ),
        input_contract=InputContract(
            accepted_formats=("csv", "tsv", "xlsx", "parquet", "json", "jsonl")
        ),
        selection=(
            SelectionPredicate(
                field="姓名",
                operator=PredicateOperator.EQ,
                value=person,
            ),
        ),
        projection=(
            ProjectionField(name="核销工作量天数"),
            ProjectionField(name="工作量费用"),
        ),
        record_grain="source_detail_row",
        combine=CombineSpec(mode=CombineMode.ONE_TABLE),
        delivery=DeliverySpec(formats=(DeliveryFormat.PARQUET,)),
        postconditions=PostconditionSpec(
            table_count=1,
            exact_visible_columns=("核销工作量天数", "工作量费用"),
            expected_row_count=11,
            predicates=(
                PredicatePostcondition(
                    field="姓名",
                    operator=PredicateOperator.EQ,
                    value=person,
                ),
            ),
            minimum_evidence_coverage=1.0,
        ),
    )


def _write_gate_csv(path: Path) -> None:
    lines = ["姓名,核销工作量天数,工作量费用,项目"]
    lines.extend(
        f"谢超群,{index / 2:.1f},{index * 100:.2f},项目A"
        for index in range(1, 12)
    )
    lines.extend(
        f"其他人员,{index / 2:.1f},{index * 100:.2f},项目B"
        for index in range(12, 17)
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_gate_exact_11_rows_two_columns_and_full_lineage(tmp_path: Path) -> None:
    manifest = get_capability_registry().manifest("table.duckdb")
    assert manifest.deterministic is True
    assert manifest.network.value == "none"
    assert get_capability_registry().is_healthy("table.duckdb") is True
    source_path = tmp_path / "workload.csv"
    _write_gate_csv(source_path)
    artifact_id = "upload_gate"
    plan = _gate_plan((artifact_id,))
    report = inspect_tabular_path(
        artifact_id=artifact_id,
        artifact_sha256=_sha(source_path),
        path=source_path,
        original_name=source_path.name,
        declared_media_type="text/csv",
    )
    bind_result = bind_semantic_plan(plan, (report,))
    assert bind_result.bound_plan is not None
    assert bind_result.bound_plan.is_executable is True

    physical = compile_physical_plan(
        plan, bind_result.bound_plan, (report,)
    )
    bundle = execute_physical_plan(
        physical,
        artifact_paths={artifact_id: source_path},
        output_dir=tmp_path / "output",
    )
    verification = verify_table_execution(plan, bundle)

    assert physical.status == PhysicalPlanStatus.READY
    assert bundle.tool_result.status.value == "succeeded"
    assert bundle.output_table is not None
    assert bundle.output_table.num_rows == 11
    assert tuple(
        name
        for name in bundle.output_table.column_names
        if not name.startswith("__mg_")
    ) == ("核销工作量天数", "工作量费用")
    assert len({row["output_record_id"] for row in bundle.evidence_rows}) == 11
    assert verification.status.value == "pass"
    assert verification.authoritative_output_allowed is True


@pytest.mark.parametrize(
    "suffix,media_type",
    [
        ("csv", "text/csv"),
        ("tsv", "text/tab-separated-values"),
        ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("parquet", "application/vnd.apache.parquet"),
        ("json", "application/json"),
        ("jsonl", "application/x-ndjson"),
    ],
)
def test_six_common_formats_share_the_same_duckdb_semantics(
    suffix: str,
    media_type: str,
    tmp_path: Path,
) -> None:
    source_path = (
        Path("tests/fixtures/semantic_harness/public/batch0/workload_filter")
        / f"source.{suffix}"
    )
    artifact_id = f"fixture_{suffix}"
    plan = _gate_plan((artifact_id,), person="示例人员甲")
    report = inspect_tabular_path(
        artifact_id=artifact_id,
        artifact_sha256=_sha(source_path),
        path=source_path,
        original_name=source_path.name,
        declared_media_type=media_type,
    )
    bound = bind_semantic_plan(plan, (report,)).bound_plan
    assert bound is not None and bound.is_executable
    physical = compile_physical_plan(plan, bound, (report,))

    bundle = execute_physical_plan(
        physical,
        artifact_paths={artifact_id: source_path},
        output_dir=tmp_path / suffix,
    )
    verification = verify_table_execution(plan, bundle)

    assert bundle.tool_result.status.value == "succeeded"
    assert bundle.tool_result.tool_config_summary["business_engine"] == "duckdb"
    assert bundle.output_table is not None
    assert bundle.output_table.num_rows == 11
    assert verification.status.value == "pass"


def test_two_sources_union_by_confirmed_schema_and_keep_lineage(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    header = "姓名,核销工作量天数,工作量费用\n"
    first.write_text(
        header
        + "".join(
            f"谢超群,{index / 2:.1f},{index * 100:.2f}\n"
            for index in range(1, 7)
        ),
        encoding="utf-8",
    )
    second.write_text(
        header
        + "".join(
            f"谢超群,{index / 2:.1f},{index * 100:.2f}\n"
            for index in range(7, 12)
        ),
        encoding="utf-8",
    )
    artifact_ids = ("union_a", "union_b")
    plan = _gate_plan(artifact_ids)
    reports = tuple(
        inspect_tabular_path(
            artifact_id=artifact_id,
            artifact_sha256=_sha(path),
            path=path,
            original_name=path.name,
            declared_media_type="text/csv",
        )
        for artifact_id, path in zip(artifact_ids, (first, second))
    )
    bound = bind_semantic_plan(plan, reports).bound_plan
    assert bound is not None and bound.is_executable
    physical = compile_physical_plan(plan, bound, reports)

    bundle = execute_physical_plan(
        physical,
        artifact_paths={"union_a": first, "union_b": second},
        output_dir=tmp_path / "union-output",
    )

    assert any(step.kind == "union" for step in physical.steps)
    assert bundle.tool_result.status.value == "succeeded"
    assert bundle.output_table is not None
    assert bundle.output_table.num_rows == 11
    assert len(bundle.evidence_rows) == 11


def _physical_source(
    *,
    source_id: str,
    artifact_id: str,
    path: Path,
    names: tuple[str, ...],
) -> PhysicalSource:
    return PhysicalSource(
        source_id=source_id,
        artifact_id=artifact_id,
        artifact_sha256=_sha(path),
        table_ref=f"artifact://{artifact_id}/table/0",
        table_index=0,
        table_name=path.stem,
        header_row=1,
        detected_format="parquet",
        columns=tuple(
            SourceColumn(
                semantic_name=name,
                physical_ref=f"artifact://{artifact_id}/table/0/column/{index}",
                column_index=index,
                output_name=name,
                inferred_type="string",
            )
            for index, name in enumerate(names)
        ),
    )


def test_non_unique_many_to_one_join_stops_without_fallback(
    tmp_path: Path,
) -> None:
    left_path = tmp_path / "facts.parquet"
    right_path = tmp_path / "departments.parquet"
    pq.write_table(
        pa.table({"部门": ["研发", "交付"], "费用": [100, 200]}),
        left_path,
    )
    pq.write_table(
        pa.table({"部门": ["研发", "研发"], "区域": ["东", "西"]}),
        right_path,
    )
    left = _physical_source(
        source_id="source_left",
        artifact_id="facts",
        path=left_path,
        names=("部门", "费用"),
    )
    right = _physical_source(
        source_id="source_right",
        artifact_id="departments",
        path=right_path,
        names=("部门", "区域"),
    )
    physical = PhysicalPlan(
        physical_plan_id="physical_join_guard",
        logical_plan_id="logical_join_guard",
        logical_plan_revision=1,
        logical_plan_hash="a" * 64,
        bound_plan_id="bound_join_guard",
        bound_plan_hash="b" * 64,
        binding_revision=1,
        status=PhysicalPlanStatus.READY,
        capability_version=CAPABILITY_VERSION,
        sources=(left, right),
        steps=(
            JoinStep(
                step_id="step_join",
                input_ids=("source_left", "source_right"),
                keys=(JoinKey(left="部门", right="部门"),),
                cardinality=JoinCardinality.MANY_TO_ONE,
            ),
            ProjectStep(
                step_id="step_project",
                input_ids=("step_join",),
                columns=(
                    ProjectColumn(source="部门", output="部门"),
                    ProjectColumn(source="区域", output="区域"),
                ),
            ),
        ),
        final_step_id="step_project",
        visible_columns=("部门", "区域"),
        runtime_policy=runtime_policy(RuntimeProfileName.WINDOWS_LOCAL),
    )

    bundle = execute_physical_plan(
        physical,
        artifact_paths={"facts": left_path, "departments": right_path},
        output_dir=tmp_path / "output",
    )

    assert bundle.tool_result.status.value == "needs_input"
    assert "join_cardinality_violation" in bundle.tool_result.error_message
    assert bundle.tool_result.tool_config_summary["fallback_used"] is False


@pytest.mark.parametrize(
    "query",
    [
        "CREATE TABLE stolen AS SELECT 1",
        "SELECT * FROM read_parquet('C:/secret.parquet')",
    ],
)
def test_sql_ast_guard_rejects_ddl_and_direct_file_access(query: str) -> None:
    with pytest.raises(ValueError):
        _validate_select_sql(query)


def test_rename_deduplicate_aggregate_and_sort_are_deterministic(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "facts.parquet"
    pq.write_table(
        pa.table(
            {
                "记录号": ["R1", "R1", "R2", "R3"],
                "部门": ["研发", "研发", "研发", "交付"],
                "费用": [100, 100, 200, 300],
            }
        ),
        source_path,
    )
    source = _physical_source(
        source_id="source_facts",
        artifact_id="facts",
        path=source_path,
        names=("记录号", "部门", "费用"),
    )
    physical = PhysicalPlan(
        physical_plan_id="physical_operations",
        logical_plan_id="logical_operations",
        logical_plan_revision=1,
        logical_plan_hash="c" * 64,
        bound_plan_id="bound_operations",
        bound_plan_hash="d" * 64,
        binding_revision=1,
        status=PhysicalPlanStatus.READY,
        capability_version=CAPABILITY_VERSION,
        sources=(source,),
        steps=(
            RenameStep(
                step_id="step_rename",
                input_ids=("source_facts",),
                mapping={"费用": "金额"},
            ),
            DeduplicateStep(
                step_id="step_deduplicate",
                input_ids=("step_rename",),
                keys=("记录号",),
            ),
            AggregateStep(
                step_id="step_aggregate",
                input_ids=("step_deduplicate",),
                group_by=("部门",),
                aggregates=(
                    AggregateExpression(
                        function=AggregateFunction.SUM,
                        column="金额",
                        output="费用合计",
                    ),
                ),
            ),
            SortStep(
                step_id="step_sort",
                input_ids=("step_aggregate",),
                keys=(
                    SortKey(
                        column="费用合计",
                        direction=SortDirection.DESC,
                    ),
                    SortKey(column="部门", direction=SortDirection.ASC),
                ),
            ),
            ProjectStep(
                step_id="step_project",
                input_ids=("step_sort",),
                columns=(
                    ProjectColumn(source="部门", output="部门"),
                    ProjectColumn(source="费用合计", output="费用合计"),
                ),
            ),
        ),
        final_step_id="step_project",
        visible_columns=("部门", "费用合计"),
        runtime_policy=runtime_policy(RuntimeProfileName.WINDOWS_LOCAL),
    )

    bundle = execute_physical_plan(
        physical,
        artifact_paths={"facts": source_path},
        output_dir=tmp_path / "output",
    )

    assert bundle.tool_result.status.value == "succeeded"
    assert bundle.output_table is not None
    assert bundle.output_table.select(["部门", "费用合计"]).to_pylist() == [
        {"部门": "交付", "费用合计": 300},
        {"部门": "研发", "费用合计": 300},
    ]
    assert len(bundle.evidence_rows) == 4
