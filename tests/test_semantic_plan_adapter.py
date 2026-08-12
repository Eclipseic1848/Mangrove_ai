# -*- coding: utf-8 -*-
"""Phase 4A ExtractionSpec 到 Phase 4B STP 的诚实适配门禁。"""
from __future__ import annotations

from src.data_prep.document_models import (
    DiscoverySpec,
    ExtractionFieldSpec,
    ExtractionSpec,
    ResultCardinality,
    ResultContract,
    ResultShape,
    TaskGoal,
)
from src.semantic_harness.adapter import extraction_spec_to_stp
from src.semantic_harness.models import CombineMode, DeliveryFormat, TaskFamily


def test_legacy_table_spec_keeps_projection_but_marks_missing_semantics():
    spec = ExtractionSpec(
        goal=TaskGoal(
            objective="提取谢超群的核销工作量并整理成一个表",
            success_criteria=["只保留两列"],
        ),
        discovery=DiscoverySpec(artifact_ids=["artifact_workload"]),
        fields=[
            ExtractionFieldSpec(name="核销工作量天数"),
            ExtractionFieldSpec(name="工作量费用"),
        ],
        result_contract=ResultContract(
            shape=ResultShape.TABLES,
            cardinality=ResultCardinality.ALL,
            renderer="tables",
            output_formats=["xlsx"],
            merge_tables=True,
        ),
    )

    plan = extraction_spec_to_stp(
        spec,
        task_id="legacy_task",
        plan_id="legacy_plan",
    )

    assert plan.task_family == TaskFamily.TABULAR_TRANSFORM
    assert [field.name for field in plan.projection] == [
        "核销工作量天数",
        "工作量费用",
    ]
    assert plan.combine.mode == CombineMode.ONE_TABLE
    assert plan.delivery.formats == (DeliveryFormat.XLSX,)
    assert plan.is_executable is False
    assert any(item.ambiguity_id == "legacy.selection" for item in plan.ambiguities)
    assert any(item.ambiguity_id == "legacy.record_grain" for item in plan.ambiguities)


def test_legacy_document_spec_maps_to_verbatim_extract_without_fake_filter():
    spec = ExtractionSpec(
        goal=TaskGoal(objective="摘录付款和交付条款"),
        discovery=DiscoverySpec(
            artifact_ids=["artifact_contract"],
            section_patterns=["付款", "交付"],
        ),
        fields=[],
        result_contract=ResultContract(
            shape=ResultShape.DOCUMENT,
            cardinality=ResultCardinality.ALL,
            renderer="document",
            output_formats=["docx", "pdf"],
        ),
    )

    plan = extraction_spec_to_stp(
        spec,
        task_id="legacy_document_task",
        plan_id="legacy_document_plan",
    )

    assert plan.task_family == TaskFamily.EXTRACT
    assert plan.selection == ()
    assert plan.source_scope.section_patterns == ("付款", "交付")
    assert plan.delivery.formats == (
        DeliveryFormat.DOCX,
        DeliveryFormat.PDF,
    )
    assert plan.is_executable is True
