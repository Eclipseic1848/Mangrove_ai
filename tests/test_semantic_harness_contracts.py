# -*- coding: utf-8 -*-
"""Phase 4B 批次 -1：语义 Harness 契约与 JSON Schema 门禁。"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from src.semantic_harness.models import (
    Ambiguity,
    ArtifactRef,
    Binding,
    BindingEvidence,
    BindingTarget,
    BindingStatus,
    BoundPlan,
    CapabilityLimits,
    CapabilityManifest,
    CombineMode,
    CombineSpec,
    ContentPolicy,
    DeliveryFormat,
    DeliverySpec,
    ExecutionBoundary,
    FailureKind,
    InputContract,
    LineageEvent,
    NetworkAccess,
    ObjectiveSpec,
    PostconditionSpec,
    PredicateOperator,
    PredicatePostcondition,
    ProjectionField,
    ResourceClass,
    ResourceUsage,
    RiskPolicy,
    SemanticTaskPlan,
    SideEffect,
    SourceScope,
    TaskFamily,
    ToolResult,
    ToolStatus,
    VerificationCheck,
    VerificationReport,
    VerificationStatus,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = PROJECT_ROOT / "docs" / "schemas"


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _golden_plan(**overrides) -> SemanticTaskPlan:
    values = {
        "plan_id": "plan_xiechaoqun_v1",
        "task_id": "task_xiechaoqun",
        "revision": 1,
        "task_family": TaskFamily.TABULAR_TRANSFORM,
        "objective": ObjectiveSpec(
            original_text="只保留谢超群的数据，输出核销工作量天数和工作量费用到一个表",
            normalized_text="筛选姓名为谢超群的明细行，投影两列并合并成一张表",
        ),
        "source_scope": SourceScope(
            artifact_ids=("artifact_fixture",),
            table_scope="all_detected_tables",
        ),
        "input_contract": InputContract(accepted_formats=("xlsx", "pdf_table")),
        "selection": (
            {
                "field": "姓名",
                "operator": PredicateOperator.EQ,
                "value": "谢超群",
            },
        ),
        "projection": (
            ProjectionField(name="核销工作量天数"),
            ProjectionField(name="工作量费用"),
        ),
        "record_grain": "source_detail_row",
        "combine": CombineSpec(mode=CombineMode.ONE_TABLE),
        "content_policy": ContentPolicy.VERBATIM,
        "delivery": DeliverySpec(formats=(DeliveryFormat.XLSX,)),
        "postconditions": PostconditionSpec(
            table_count=1,
            exact_visible_columns=("核销工作量天数", "工作量费用"),
            expected_row_count=11,
            predicates=(
                PredicatePostcondition(
                    field="姓名",
                    operator=PredicateOperator.EQ,
                    value="谢超群",
                    required_ratio=1.0,
                ),
            ),
            minimum_evidence_coverage=1.0,
        ),
    }
    values.update(overrides)
    return SemanticTaskPlan.model_validate(values)


def test_semantic_plan_round_trip_hash_and_default_policy():
    plan = _golden_plan()
    restored = SemanticTaskPlan.model_validate_json(plan.model_dump_json())

    assert restored == plan
    assert plan.content_policy == ContentPolicy.VERBATIM
    assert plan.is_executable is True
    assert plan.canonical_hash() == restored.canonical_hash()
    assert len(plan.canonical_hash()) == 64


def test_tabular_plan_requires_record_grain_and_exact_one_table_condition():
    with pytest.raises(ValidationError, match="record_grain"):
        _golden_plan(record_grain=None)

    with pytest.raises(ValidationError, match="table_count=1"):
        _golden_plan(
            postconditions=PostconditionSpec(
                table_count=2,
                exact_visible_columns=("核销工作量天数", "工作量费用"),
                predicates=(
                    PredicatePostcondition(
                        field="姓名",
                        operator=PredicateOperator.EQ,
                        value="谢超群",
                    ),
                ),
            )
        )

    with pytest.raises(ValidationError, match="one_table 只能用于表格变换"):
        _golden_plan(
            task_family=TaskFamily.EXTRACT,
            record_grain=None,
        )


def test_projection_always_requires_exact_visible_columns():
    with pytest.raises(ValidationError, match="projection 必须配套"):
        _golden_plan(
            postconditions=PostconditionSpec(
                table_count=1,
                predicates=(
                    PredicatePostcondition(
                        field="姓名",
                        operator=PredicateOperator.EQ,
                        value="谢超群",
                    ),
                ),
            )
        )


def test_selection_and_projection_require_matching_postconditions():
    with pytest.raises(ValidationError, match="exact_visible_columns"):
        _golden_plan(
            postconditions=PostconditionSpec(
                table_count=1,
                exact_visible_columns=("姓名",),
                predicates=(
                    PredicatePostcondition(
                        field="姓名",
                        operator=PredicateOperator.EQ,
                        value="谢超群",
                    ),
                ),
            )
        )

    with pytest.raises(ValidationError, match="每个 selection"):
        _golden_plan(
            postconditions=PostconditionSpec(
                table_count=1,
                exact_visible_columns=("核销工作量天数", "工作量费用"),
            )
        )


def test_material_ambiguity_blocks_execution():
    plan = _golden_plan(
        ambiguities=(
            Ambiguity(
                ambiguity_id="grain",
                question="每一行代表源明细还是按人员汇总？",
                candidates=("source_detail_row", "person_summary"),
            ),
        )
    )

    assert plan.is_executable is False


def test_external_api_plan_can_wait_for_confirmation_but_cannot_execute():
    pending = _golden_plan(
        risk_policy=RiskPolicy(execution_boundary=ExecutionBoundary.EXTERNAL_API)
    )
    assert pending.is_executable is False

    confirmed = _golden_plan(
        risk_policy=RiskPolicy(
            execution_boundary=ExecutionBoundary.EXTERNAL_API,
            external_api_confirmed=True,
        )
    )
    assert confirmed.is_executable is True

    with pytest.raises(ValidationError, match="不得产生业务写入"):
        RiskPolicy(allow_side_effects=True)


def test_bound_plan_requires_evidence_and_is_frozen():
    plan = _golden_plan()
    binding = Binding(
        semantic_ref="projection.核销工作量天数",
        status=BindingStatus.BOUND,
        confidence=1.0,
        targets=(
            BindingTarget(
                physical_ref="table:workload/column:核销工作量天数",
                artifact_id="artifact_fixture",
                target_kind="table_column",
                confidence=1.0,
                evidence=(
                    BindingEvidence(
                        source_ref="artifact_fixture",
                        reason="列名精确匹配",
                        samples=("0.5", "1.0"),
                    ),
                ),
            ),
        ),
    )
    bound = BoundPlan(
        bound_plan_id="bound_xiechaoqun_v1",
        logical_plan_id=plan.plan_id,
        logical_plan_revision=plan.revision,
        logical_plan_hash=plan.canonical_hash(),
        input_artifact_hashes={"artifact_fixture": _sha("fixture")},
        inspection_report_hashes={"artifact_fixture": _sha("inspection")},
        binder_version="source-binder/1",
        threshold_version="batch2/v1",
        bindings=(binding,),
    )

    assert bound.is_executable is True
    with pytest.raises(ValidationError, match="frozen"):
        bound.binding_revision = 2

    with pytest.raises(ValidationError, match="不得提前写入 targets"):
        Binding(
            semantic_ref="projection.工作量费用",
            status=BindingStatus.AMBIGUOUS,
            confidence=0.5,
            targets=(
                BindingTarget(
                    physical_ref="table:workload/column:未知",
                    artifact_id="artifact_fixture",
                    target_kind="table_column",
                    confidence=0.5,
                    evidence=(
                        BindingEvidence(
                            source_ref="artifact_fixture",
                            reason="仅候选",
                        ),
                    ),
                ),
            ),
        )


def test_capability_manifest_forbids_unknown_fields():
    manifest = CapabilityManifest(
        capability_id="table.query.duckdb",
        version="1",
        accepts=("arrow_table", "parquet"),
        produces=("arrow_table",),
        operations=("filter", "project"),
        deterministic=True,
        evidence_preserving=True,
        side_effect=SideEffect.NONE,
        network=NetworkAccess.NONE,
        resource_class=ResourceClass.CPU_MEDIUM,
        limits=CapabilityLimits(
            max_rows=1_000_000,
            timeout_seconds=60,
            max_concurrency=2,
        ),
        healthcheck="semantic_harness.health.duckdb",
        parameters_schema={"type": "object", "additionalProperties": False},
    )

    assert manifest.deterministic is True
    with pytest.raises(ValidationError, match="Extra inputs"):
        CapabilityManifest.model_validate(
            {**manifest.model_dump(), "shell_command": "任意命令"}
        )


def test_tool_result_success_and_failure_invariants():
    output = ArtifactRef(
        artifact_id="result_table",
        kind="clean",
        media_type="application/vnd.apache.arrow.file",
        sha256=_sha("result"),
        size_bytes=128,
    )
    result = ToolResult(
        call_id="call_1",
        capability_id="table.query.duckdb",
        capability_version="1",
        status=ToolStatus.SUCCEEDED,
        output_artifacts=(output,),
        lineage=(
            LineageEvent(
                event="filter_project",
                input_artifact_ids=("source_table",),
                output_artifact_ids=("result_table",),
            ),
        ),
        facts={"rows": 11, "columns": 2},
        resource_usage=ResourceUsage(duration_ms=25),
    )
    assert result.status == ToolStatus.SUCCEEDED

    with pytest.raises(ValidationError, match="failure_kind"):
        ToolResult(
            call_id="call_2",
            capability_id="table.query.duckdb",
            capability_version="1",
            status=ToolStatus.FAILED,
            resource_usage=ResourceUsage(duration_ms=25),
        )

    with pytest.raises(ValidationError, match="可自动重试"):
        ToolResult(
            call_id="call_3",
            capability_id="table.query.duckdb",
            capability_version="1",
            status=ToolStatus.FAILED,
            failure_kind=FailureKind.INVALID_PLAN,
            error_message="计划缺少字段绑定",
            retryable=True,
            resource_usage=ResourceUsage(duration_ms=25),
        )


def test_verification_report_alone_controls_authoritative_output():
    plan = _golden_plan()
    report = VerificationReport(
        report_id="verify_1",
        logical_plan_id=plan.plan_id,
        logical_plan_revision=plan.revision,
        logical_plan_hash=plan.canonical_hash(),
        status=VerificationStatus.PASS,
        checks=(
            VerificationCheck(
                code="exact_row_count",
                passed=True,
                expected=11,
                actual=11,
                message="结果行数为 11",
            ),
            VerificationCheck(
                code="exact_visible_columns",
                passed=True,
                expected=["核销工作量天数", "工作量费用"],
                actual=["核销工作量天数", "工作量费用"],
                message="可见列完全一致",
            ),
        ),
    )

    assert report.authoritative_output_allowed is True

    with pytest.raises(ValidationError, match="全部检查必须通过"):
        VerificationReport(
            report_id="verify_bad",
            logical_plan_id=plan.plan_id,
            logical_plan_revision=plan.revision,
            logical_plan_hash=plan.canonical_hash(),
            status=VerificationStatus.PASS,
            checks=(
                VerificationCheck(
                    code="predicate_ratio",
                    passed=False,
                    expected=1.0,
                    actual=0.07,
                    message="过滤条件未满足",
                ),
            ),
        )


def test_phase4b_json_schemas_are_valid_and_accept_round_trip_instance():
    expected_models = {
        "SemanticTaskPlan",
        "BoundPlan",
        "CapabilityManifest",
        "ToolResult",
        "VerificationReport",
        "CompileRequest",
        "PlanSemanticsDraft",
        "CompileResult",
        "PhysicalPlan",
        "AuditRule",
        "DocumentPhysicalPlan",
        "DocumentAST",
        "DocumentExecutionResult",
        "HarnessLoopPolicy",
        "HarnessRun",
        "HarnessQuestion",
        "HarnessResume",
        "RepairProposal",
        "RepairDecision",
    }
    index = json.loads((SCHEMA_DIR / "index.json").read_text(encoding="utf-8"))
    assert expected_models.issubset(set(index["models"]))

    for model_name in expected_models:
        schema = json.loads(
            (SCHEMA_DIR / f"{model_name}.json").read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator.check_schema(schema)

    plan = _golden_plan()
    schema = json.loads(
        (SCHEMA_DIR / "SemanticTaskPlan.json").read_text(encoding="utf-8")
    )
    jsonschema.validate(plan.model_dump(mode="json"), schema)
