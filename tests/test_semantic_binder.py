# -*- coding: utf-8 -*-
"""Phase 4B 批次 2：全局 Binder、歧义闸门与一对多绑定。"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from src.data_prep.document_models import DocumentElement, ElementType
from src.semantic_harness.binder import bind_semantic_plan
from src.semantic_harness.document_models import DocumentPlanStatus
from src.semantic_harness.document_planner import compile_document_plan
from src.semantic_harness.inspection_models import BindStatus
from src.semantic_harness.inspectors.document import inspect_document_elements
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
    SemanticTaskPlan,
    SourceScope,
    TaskFamily,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_page_scope_becomes_explicit_document_selection() -> None:
    elements = tuple(
        DocumentElement(
            element_id=f"page-{page}",
            artifact_id="doc_pages",
            page=page,
            element_type=ElementType.PARAGRAPH,
            text=f"第{page}页正文",
            reading_order=page,
            extractor="python-docx",
            extractor_version="1",
            metadata={"paragraph_index": page},
        )
        for page in (1, 2)
    )
    report = inspect_document_elements(
        artifact_id="doc_pages",
        artifact_sha256="c" * 64,
        original_name="分页合同.docx",
        declared_media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        size_bytes=100,
        elements=elements,
    )
    plan = SemanticTaskPlan(
        plan_id="plan_pages",
        task_id="task_pages",
        revision=1,
        task_family=TaskFamily.EXTRACT,
        objective=ObjectiveSpec(
            original_text="只提取第二页",
            normalized_text="逐字提取第二页",
        ),
        source_scope=SourceScope(
            artifact_ids=("doc_pages",),
            pages={"doc_pages": (2,)},
        ),
        input_contract=InputContract(accepted_formats=("docx",)),
        delivery=DeliverySpec(formats=(DeliveryFormat.TXT,)),
    )

    bound = bind_semantic_plan(plan, (report,))
    assert bound.status == BindStatus.READY
    assert bound.bound_plan is not None
    physical = compile_document_plan(plan, bound.bound_plan, (report,))

    assert physical.status == DocumentPlanStatus.READY
    assert physical.whole_document is False
    assert len(physical.selections) == 1
    assert physical.selections[0].label == "第 2 页"
    assert physical.selections[0].artifact_element_ids == {
        "doc_pages": ("page-2",)
    }


def _table_plan(artifact_ids: tuple[str, ...]) -> SemanticTaskPlan:
    return SemanticTaskPlan(
        plan_id="plan_binder",
        task_id="task_binder",
        revision=1,
        task_family=TaskFamily.TABULAR_TRANSFORM,
        objective=ObjectiveSpec(
            original_text="只保留谢超群，输出核销工作量天数和工作量费用",
            normalized_text="筛选姓名后投影两列",
        ),
        source_scope=SourceScope(
            artifact_ids=artifact_ids,
            table_scope="all_detected_tables",
        ),
        input_contract=InputContract(accepted_formats=("csv",)),
        selection=(
            {
                "field": "姓名",
                "operator": PredicateOperator.EQ,
                "value": "谢超群",
            },
        ),
        projection=(
            ProjectionField(name="核销工作量天数"),
            ProjectionField(name="工作量费用"),
        ),
        record_grain="source_detail_row",
        combine=CombineSpec(mode=CombineMode.ONE_TABLE),
        delivery=DeliverySpec(formats=(DeliveryFormat.XLSX,)),
        postconditions=PostconditionSpec(
            table_count=1,
            exact_visible_columns=("核销工作量天数", "工作量费用"),
            predicates=(
                PredicatePostcondition(
                    field="姓名",
                    operator=PredicateOperator.EQ,
                    value="谢超群",
                ),
            ),
        ),
    )


def _report(tmp_path, artifact_id: str, headers: str):
    path = tmp_path / f"{artifact_id}.csv"
    column_count = len(headers.split(","))
    values = ["谢超群", *(["0.5"] * max(column_count - 2, 0)), "1200"]
    path.write_text(f"{headers}\n{','.join(values)}\n", encoding="utf-8")
    data = path.read_bytes()
    return inspect_tabular_path(
        artifact_id=artifact_id,
        artifact_sha256=_sha(data),
        path=path,
        original_name=path.name,
        declared_media_type="text/csv",
    )


def test_exact_table_fields_auto_bind_across_two_artifacts(tmp_path):
    reports = (
        _report(tmp_path, "a1", "姓名,核销工作量天数,工作量费用"),
        _report(tmp_path, "a2", "姓名,核销工作量天数,工作量费用"),
    )

    result = bind_semantic_plan(_table_plan(("a1", "a2")), reports)

    assert result.status == BindStatus.READY
    assert result.bound_plan is not None
    assert all(len(binding.targets) == 2 for binding in result.bound_plan.bindings)
    assert result.bound_plan.is_executable is True


def test_missing_field_in_one_artifact_blocks_cross_source_binding(tmp_path):
    reports = (
        _report(tmp_path, "a1", "姓名,核销工作量天数,工作量费用"),
        _report(tmp_path, "a2", "姓名,核销工作量天数,其他费用"),
    )

    result = bind_semantic_plan(_table_plan(("a1", "a2")), reports)

    assert result.status == BindStatus.NEEDS_USER
    assert result.clarification is not None
    assert "artifact://a2/table/0" in result.clarification.ambiguity_id
    fee_binding = next(
        item
        for item in result.bound_plan.bindings
        if item.semantic_ref == "field:工作量费用"
    )
    assert fee_binding.status.value != "bound"


def test_duplicate_header_stops_and_asks_only_one_question(tmp_path):
    report = _report(
        tmp_path,
        "a1",
        "姓名,核销工作量天数,核销工作量天数,工作量费用",
    )

    result = bind_semantic_plan(_table_plan(("a1",)), (report,))

    assert result.status == BindStatus.NEEDS_USER
    assert result.clarification is not None
    assert result.clarification.ambiguity_id.startswith(
        "field:核销工作量天数|artifact://a1/table/0"
    )
    unresolved = [
        item
        for item in result.bound_plan.bindings
        if item.status.value != "bound"
    ]
    assert len(unresolved) == 1


def test_user_can_only_resolve_to_a_generated_candidate(tmp_path):
    report = _report(
        tmp_path,
        "a1",
        "姓名,核销工作量天数,核销工作量天数,工作量费用",
    )
    first = bind_semantic_plan(_table_plan(("a1",)), (report,))
    candidate = next(
        item
        for item in first.candidates
        if item.semantic_ref == "field:核销工作量天数"
    )

    resolved = bind_semantic_plan(
        _table_plan(("a1",)),
        (report,),
        binding_revision=2,
        resolutions={
            first.clarification.ambiguity_id: candidate.physical_ref
        },
    )
    invalid = bind_semantic_plan(
        _table_plan(("a1",)),
        (report,),
        binding_revision=2,
        resolutions={
            first.clarification.ambiguity_id: "artifact://a1/table/0/column/999"
        },
    )

    assert resolved.status == BindStatus.READY
    assert resolved.bound_plan.binding_revision == 2
    assert invalid.status == BindStatus.NEEDS_USER


def test_document_concept_binds_all_high_confidence_sections():
    elements = tuple(
        DocumentElement(
            element_id=f"section_{index}",
            artifact_id="doc_a",
            page=index,
            element_type=ElementType.SECTION,
            text=f"商务条款：付款条件第{index}部分",
            reading_order=index,
            extractor="python-docx",
            extractor_version="1",
            metadata={"paragraph_index": index},
        )
        for index in (1, 2)
    )
    report = inspect_document_elements(
        artifact_id="doc_a",
        artifact_sha256="b" * 64,
        original_name="合同.docx",
        declared_media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=100,
        elements=elements,
    )
    plan = SemanticTaskPlan(
        plan_id="plan_document",
        task_id="task_document",
        revision=1,
        task_family=TaskFamily.EXTRACT,
        objective=ObjectiveSpec(
            original_text="提取商务条款",
            normalized_text="提取商务条款并保留原文",
        ),
        source_scope=SourceScope(
            artifact_ids=("doc_a",),
            section_patterns=("商务条款",),
        ),
        input_contract=InputContract(accepted_formats=("docx",)),
        delivery=DeliverySpec(formats=(DeliveryFormat.DOCX,)),
        postconditions=PostconditionSpec(minimum_evidence_coverage=1.0),
    )

    result = bind_semantic_plan(plan, (report,))

    assert result.status == BindStatus.READY
    assert len(result.bound_plan.bindings) == 1
    assert len(result.bound_plan.bindings[0].targets) == 2


class _SemanticClassifier:
    name = "fixture_classifier"
    classifier_name = "fixture_classifier"

    def __init__(self, *, uncertain: bool = False) -> None:
        self.uncertain = uncertain
        self.evidence = ()

    def score(self, query, documents):
        del query, documents
        return None

    def classify(self, query, evidence):
        assert "商务相关" in query
        self.evidence = tuple(evidence)
        return [
            SimpleNamespace(
                evidence_id=evidence_id,
                category=(
                    "matches_query"
                    if "违约金额" in text
                    else "uncertain"
                    if self.uncertain
                    else "does_not_match"
                ),
                reason="fixture",
            )
            for evidence_id, text in evidence
        ]


def _semantic_content_plan() -> SemanticTaskPlan:
    predicate = {
        "field": "content",
        "operator": PredicateOperator.CONTAINS,
        "value": "所有商务相关合同条款",
    }
    return SemanticTaskPlan(
        plan_id="plan_semantic_content",
        task_id="task_semantic_content",
        revision=1,
        task_family=TaskFamily.EXTRACT,
        objective=ObjectiveSpec(
            original_text="从整份文档查找商务相关条款",
            normalized_text="从整份文档查找所有商务相关合同条款",
        ),
        source_scope=SourceScope(artifact_ids=("doc_semantic",)),
        input_contract=InputContract(accepted_formats=("docx",)),
        selection=(predicate,),
        record_grain="条款",
        delivery=DeliverySpec(formats=(DeliveryFormat.TXT,)),
        postconditions=PostconditionSpec(
            predicates=(
                PredicatePostcondition(
                    field="content",
                    operator=PredicateOperator.CONTAINS,
                    value="所有商务相关合同条款",
                ),
            ),
        ),
    )


def _semantic_content_report():
    elements = (
        DocumentElement(
            element_id="feature",
            artifact_id="doc_semantic",
            page=1,
            element_type=ElementType.PARAGRAPH,
            text="功能需求：支持订单费用自动计算和报表展示",
            reading_order=1,
            extractor="python-docx",
            extractor_version="1",
            metadata={"paragraph_index": 1},
        ),
        DocumentElement(
            element_id="penalty",
            artifact_id="doc_semantic",
            page=1,
            element_type=ElementType.PARAGRAPH,
            text="投标方逾期交付时，违约金额为合同总额的20%",
            reading_order=2,
            extractor="python-docx",
            extractor_version="1",
            metadata={"paragraph_index": 2},
        ),
    )
    return inspect_document_elements(
        artifact_id="doc_semantic",
        artifact_sha256="d" * 64,
        original_name="技术要求.docx",
        declared_media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        size_bytes=100,
        elements=elements,
    )


def test_content_query_scans_all_targets_without_fuzzy_prefilter():
    plan = _semantic_content_plan()
    report = _semantic_content_report()
    provider = _SemanticClassifier()

    result = bind_semantic_plan(
        plan,
        (report,),
        semantic_provider=provider,
    )

    assert result.status == BindStatus.READY
    assert result.provenance.semantic_backend == "fixture_classifier"
    assert result.bound_plan is not None
    binding = result.bound_plan.bindings[0]
    assert binding.semantic_ref.startswith("content_query:")
    assert [target.physical_ref for target in binding.targets] == [
        "artifact://doc_semantic/page/1/element/penalty"
    ]
    physical = compile_document_plan(plan, result.bound_plan, (report,))
    assert physical.status == DocumentPlanStatus.READY
    assert physical.whole_document is False
    assert physical.selections[0].label == "所有商务相关合同条款"
    assert physical.selections[0].artifact_element_ids == {
        "doc_semantic": ("penalty",)
    }
    assert "structural_hints:" in provider.evidence[0][1]
    assert "current_text: 功能需求" in provider.evidence[0][1]


def test_uncertain_content_query_fails_closed_instead_of_emitting_full_document():
    result = bind_semantic_plan(
        _semantic_content_plan(),
        (_semantic_content_report(),),
        semantic_provider=_SemanticClassifier(uncertain=True),
    )

    assert result.status == BindStatus.NEEDS_USER
    assert result.bound_plan is not None
    assert result.bound_plan.is_executable is False


def test_duplicate_classifier_ids_fail_closed():
    class DuplicateClassifier(_SemanticClassifier):
        def classify(self, query, evidence):
            decisions = super().classify(query, evidence)
            return [*decisions, decisions[0]]

    with pytest.raises(
        ValueError,
        match="逐条且仅逐条一次覆盖",
    ):
        bind_semantic_plan(
            _semantic_content_plan(),
            (_semantic_content_report(),),
            semantic_provider=DuplicateClassifier(),
        )


def test_unknown_classifier_category_fails_closed():
    class UnknownCategoryClassifier(_SemanticClassifier):
        def classify(self, query, evidence):
            decisions = super().classify(query, evidence)
            decisions[0].category = "probably"
            return decisions

    with pytest.raises(ValueError, match="未知类别"):
        bind_semantic_plan(
            _semantic_content_plan(),
            (_semantic_content_report(),),
            semantic_provider=UnknownCategoryClassifier(),
        )
