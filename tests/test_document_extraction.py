# -*- coding: utf-8 -*-
"""证据约束字段抽取测试。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.data_prep.document_models import (
    BoundingBox,
    DiscoverySpec,
    DocumentElement,
    ElementType,
    ExtractionFieldSpec,
    ExtractionSpec,
    ExtractionStatus,
    ResultCardinality,
    ResultContract,
    ResultShape,
    TaskGoal,
)
from src.services.document_extraction import (
    EvidenceBoundExtractor,
    FieldCandidate,
    InstructorSemanticMatchProvider,
    SemanticMatchBatch,
    SemanticMatchDecision,
    _document_structured_extra_body,
)
from src.llm.provider import ResolvedModelConnection


class StubProvider:
    def __init__(self, candidates):
        self.candidates = candidates

    def extract(self, spec, elements):
        return self.candidates


def _connection(provider: str, model: str, extra_body=None) -> ResolvedModelConnection:
    return ResolvedModelConnection(
        provider=provider,
        requested_model=model,
        model=model,
        base_url="http://model.test/v1",
        api_key="test-key",
        timeout=30,
        trust_env=provider != "local",
        extra_body=extra_body,
    )


def test_document_structured_output_forces_thinking_off():
    assert _document_structured_extra_body(
        _connection("deepseek", "deepseek-v4-pro")
    ) == {"thinking": {"type": "disabled"}}
    assert _document_structured_extra_body(
        _connection("qwen", "qwen3.7-plus", {"enable_thinking": True})
    ) == {"enable_thinking": False}
    assert _document_structured_extra_body(
        _connection(
            "local",
            "Qwen3.6-35B-A3B",
            {"chat_template_kwargs": {"enable_thinking": True}},
        )
    ) == {"chat_template_kwargs": {"enable_thinking": False}}


def _semantic_provider_with(decisions):
    provider = object.__new__(InstructorSemanticMatchProvider)
    provider.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: SemanticMatchBatch(
                    decisions=decisions(kwargs)
                )
            )
        )
    )
    provider.model = "fixture-model"
    provider.extra_body = None
    provider.max_retries = 0
    provider.max_chars = 100
    provider.max_items = 2
    return provider


def test_semantic_match_provider_preserves_input_order_across_chunks():
    def decisions(kwargs):
        payload = kwargs["messages"][1]["content"]
        import json

        items = json.loads(payload)["evidence"]
        return [
            SemanticMatchDecision(
                evidence_id=item["evidence_id"],
                evidence_role="contractual_clause",
                category=(
                    "matches_query"
                    if "违约" in item["text"]
                    else "does_not_match"
                ),
                reason="fixture",
            )
            for item in reversed(items)
        ]

    provider = _semantic_provider_with(decisions)
    result = provider.classify(
        "查找违约条款",
        (
            ("a", "普通功能"),
            ("b", "违约责任"),
            ("c", "其他内容"),
        ),
    )

    assert [item.evidence_id for item in result] == ["a", "b", "c"]
    assert [item.category for item in result] == [
        "does_not_match",
        "matches_query",
        "does_not_match",
    ]


def test_semantic_match_provider_rejects_missing_evidence_id():
    provider = _semantic_provider_with(
        lambda kwargs: [
            SemanticMatchDecision(
                evidence_id="a",
                evidence_role="contractual_clause",
                category="matches_query",
                reason="fixture",
            )
        ]
    )

    with pytest.raises(ValueError, match="缺失、重复或越界"):
        provider.classify(
            "查找条款",
            (("a", "条款A"), ("b", "条款B")),
        )


def test_broad_contract_query_uses_structural_rules_consistently():
    decisions = (
        SemanticMatchDecision(
            evidence_id="contract",
            evidence_role="contractual_clause",
            category="does_not_match",
            reason="fixture",
        ),
        SemanticMatchDecision(
            evidence_id="feature",
            evidence_role="product_feature_requirement",
            category="matches_query",
            reason="fixture",
        ),
        SemanticMatchDecision(
            evidence_id="technical",
            evidence_role="contractual_clause",
            category="matches_query",
            reason="fixture",
        ),
    )

    result = InstructorSemanticMatchProvider._apply_broad_contract_rules(
        "提取所有商务条款",
        decisions,
        (
            (
                "contract",
                "section_context: 十、验收条件和标准\n"
                "structural_hints: (none)\n"
                "current_text: 项目交付物完整。",
            ),
            (
                "feature",
                "section_context: 功能清单\n"
                "structural_hints: product_feature_requirement_row\n"
                "current_text: 功能需求：支持费用结算。",
            ),
            (
                "technical",
                "section_context: 五、业务需求\n"
                "structural_hints: (none)\n"
                "current_text: 投标方必须实现数据权限闭环。",
            ),
        ),
    )
    specific = InstructorSemanticMatchProvider._apply_broad_contract_rules(
        "只提取违约条款",
        decisions,
    )

    assert [item.category for item in result] == [
        "matches_query",
        "does_not_match",
        "does_not_match",
    ]
    assert [item.category for item in specific] == [
        "does_not_match",
        "matches_query",
        "matches_query",
    ]


def test_broad_contract_query_uses_structural_path_without_model_call():
    provider = _semantic_provider_with(
        lambda kwargs: (_ for _ in ()).throw(
            AssertionError("广义商务条款不应调用模型")
        )
    )

    result = provider.classify(
        "提取所有商务条款",
        (
            (
                "acceptance",
                "section_context: 十、验收条件和标准\n"
                "structural_hints: (none)\n"
                "current_text: 项目交付物必须完整。",
            ),
            (
                "feature",
                "section_context: 五、业务需求\n"
                "structural_hints: product_feature_requirement_row\n"
                "current_text: 功能需求：支持费用结算。",
            ),
        ),
    )

    assert [item.category for item in result] == [
        "matches_query",
        "does_not_match",
    ]


def _element(
    element_id: str,
    text: str,
    *,
    artifact_id: str = "artifact-a",
    page: int = 2,
    bbox: bool = True,
) -> DocumentElement:
    return DocumentElement(
        element_id=element_id,
        artifact_id=artifact_id,
        page=page,
        element_type=ElementType.PARAGRAPH,
        text=text,
        bbox=BoundingBox(
            x0=10, y0=20, x1=200, y1=40, coordinate_space="pdf_points"
        ) if bbox else None,
        extractor="mineru",
        extractor_version="3.0.9",
        confidence=0.99,
    )


def _spec(*names: str) -> ExtractionSpec:
    return ExtractionSpec(
        goal=TaskGoal(objective="抽取合同付款和交付条款"),
        discovery=DiscoverySpec(artifact_ids=["artifact-a"]),
        fields=[
            ExtractionFieldSpec(
                name=name,
                description=name,
                min_confidence=0.90,
            )
            for name in names
        ],
    )


def test_found_field_binds_real_element_evidence():
    element = _element("el-payment", "首付款比例为合同金额的30%。")
    provider = StubProvider([FieldCandidate(
        field_name="付款比例",
        value="30%",
        quote="首付款比例为合同金额的30%",
        element_ids=["el-payment"],
        confidence=0.98,
    )])

    result = EvidenceBoundExtractor(provider).extract(_spec("付款比例"), [element])

    field = result.fields[0]
    assert field.status == ExtractionStatus.FOUND
    assert field.value == "30%"
    assert field.evidence_refs[0].element_id == "el-payment"
    assert field.evidence_refs[0].bbox is not None
    assert not result.review_tasks


def test_multi_element_candidate_uses_element_that_contains_quote():
    elements = [
        _element("el-label", "delivery_days"),
        _element("el-value", "21 days"),
    ]
    provider = StubProvider([FieldCandidate(
        field_name="交付期限",
        value="21 days",
        quote="21 days",
        element_ids=["el-label", "el-value"],
        confidence=0.98,
    )])

    result = EvidenceBoundExtractor(provider).extract(_spec("交付期限"), elements)

    assert result.fields[0].status == ExtractionStatus.FOUND
    assert [ref.element_id for ref in result.fields[0].evidence_refs] == ["el-value"]


def test_fabricated_element_id_never_becomes_found():
    provider = StubProvider([FieldCandidate(
        field_name="收款账户",
        value="62220000",
        quote="收款账户：62220000",
        element_ids=["el-fabricated"],
        confidence=0.99,
    )])

    result = EvidenceBoundExtractor(provider).extract(
        _spec("收款账户"),
        [_element("el-real", "本页没有收款账户")],
    )

    assert result.fields[0].status == ExtractionStatus.LOW_CONFIDENCE
    assert result.fields[0].value is None
    assert result.fields[0].evidence_refs == []
    assert len(result.review_tasks) == 1


def test_conflicting_values_create_review_task():
    elements = [
        _element("el-a", "交付地点：南京"),
        _element("el-b", "交付地点：上海", page=3),
    ]
    provider = StubProvider([
        FieldCandidate(
            field_name="交付地点", value="南京", quote="交付地点：南京",
            element_ids=["el-a"], confidence=0.98,
        ),
        FieldCandidate(
            field_name="交付地点", value="上海", quote="交付地点：上海",
            element_ids=["el-b"], confidence=0.97,
        ),
    ])

    result = EvidenceBoundExtractor(provider).extract(_spec("交付地点"), elements)

    assert result.fields[0].status == ExtractionStatus.CONFLICT
    assert result.fields[0].value is None
    assert len(result.fields[0].evidence_refs) == 2
    assert result.review_tasks[0].field_name == "交付地点"


def test_missing_candidate_is_not_found_without_review():
    result = EvidenceBoundExtractor(StubProvider([])).extract(
        _spec("违约责任"),
        [_element("el-a", "普通合同正文")],
    )

    assert result.fields[0].status == ExtractionStatus.NOT_FOUND
    assert result.fields[0].value is None
    assert result.review_tasks == []


def test_missing_bbox_forces_review():
    element = _element("el-a", "交付时间：合同生效后30日", bbox=False)
    provider = StubProvider([FieldCandidate(
        field_name="交付时间",
        value="合同生效后30日",
        quote="交付时间：合同生效后30日",
        element_ids=["el-a"],
        confidence=0.99,
    )])

    result = EvidenceBoundExtractor(provider).extract(_spec("交付时间"), [element])

    assert result.fields[0].status == ExtractionStatus.LOW_CONFIDENCE
    assert result.review_tasks[0].reasons == ["证据缺少 bbox 或结构化位置"]


def test_structural_location_is_valid_evidence_without_bbox():
    element = _element("el-docx", "付款条件：验收后30日内付款", bbox=False)
    element.metadata["location"] = {
        "kind": "docx_paragraph",
        "paragraph": 3,
    }
    provider = StubProvider([FieldCandidate(
        field_name="付款条件",
        value="验收后30日内付款",
        quote="付款条件：验收后30日内付款",
        element_ids=["el-docx"],
        confidence=0.99,
    )])

    result = EvidenceBoundExtractor(provider).extract(_spec("付款条件"), [element])

    assert result.fields[0].status == ExtractionStatus.FOUND
    assert result.fields[0].evidence_refs[0].bbox is None
    assert result.fields[0].evidence_refs[0].location == {
        "kind": "docx_paragraph",
        "paragraph": 3,
    }
    assert result.review_tasks == []


def test_discovery_scope_rejects_cross_document_candidate():
    foreign = _element(
        "el-foreign",
        "合同金额：100万元",
        artifact_id="artifact-b",
    )
    provider = StubProvider([FieldCandidate(
        field_name="合同金额",
        value="100万元",
        quote="合同金额：100万元",
        element_ids=["el-foreign"],
        confidence=0.99,
    )])

    result = EvidenceBoundExtractor(provider).extract(_spec("合同金额"), [foreign])

    assert result.fields[0].status == ExtractionStatus.LOW_CONFIDENCE
    assert "越界" in (result.fields[0].review_reason or "")


def test_record_result_keeps_all_logical_rows_instead_of_conflicting():
    elements = [
        _element("el-work-1", "张三负责需求分析"),
        _element("el-work-2", "张三负责测试验收", page=3),
    ]
    provider = StubProvider([
        FieldCandidate(
            field_name="工作内容",
            value="需求分析",
            quote="张三负责需求分析",
            element_ids=["el-work-1"],
            confidence=0.99,
            record_id="work-1",
        ),
        FieldCandidate(
            field_name="工作内容",
            value="测试验收",
            quote="张三负责测试验收",
            element_ids=["el-work-2"],
            confidence=0.99,
            record_id="work-2",
        ),
    ])
    spec = _spec("工作内容").model_copy(update={
        "result_contract": ResultContract(
            shape=ResultShape.RECORDS,
            cardinality=ResultCardinality.ALL,
            record_grain="一项工作",
            renderer="data_grid",
            exhaustive=True,
        ),
    })

    result = EvidenceBoundExtractor(provider).extract(spec, elements)

    assert result.fields == []
    assert [record.values["工作内容"] for record in result.records] == [
        "需求分析",
        "测试验收",
    ]
    assert result.review_tasks == []
    assert result.coverage["records_extracted"] == 2
    assert result.coverage["elements_processed"] == 2


def test_document_result_is_deterministic_continuous_content_with_evidence():
    elements = [
        _element("page-2", "第二页正文", page=2),
        _element("page-1", "第一页正文", page=1),
    ]
    spec = ExtractionSpec(
        goal=TaskGoal(objective="输出连续文档"),
        discovery=DiscoverySpec(artifact_ids=["artifact-a"]),
        fields=[],
        result_contract=ResultContract(
            shape=ResultShape.DOCUMENT,
            renderer="document_view",
        ),
    )

    result = EvidenceBoundExtractor(StubProvider([])).extract(spec, elements)

    assert result.fields == []
    assert len(result.documents) == 1
    assert result.documents[0].content == "第一页正文\n\n第二页正文"
    assert [
        ref.element_id for ref in result.documents[0].evidence_refs
    ] == ["page-1", "page-2"]
    assert result.coverage["document_chars"] == len("第一页正文\n\n第二页正文")


def test_aggregate_result_has_dedicated_evidence_bound_object():
    element = _element("el-total", "合同总额：100万元")
    provider = StubProvider([FieldCandidate(
        field_name="合同总额",
        value="100万元",
        quote="合同总额：100万元",
        element_ids=["el-total"],
        confidence=0.99,
    )])
    spec = _spec("合同总额").model_copy(update={
        "result_contract": ResultContract(
            shape=ResultShape.AGGREGATE,
            renderer="aggregate_cards",
        ),
    })

    result = EvidenceBoundExtractor(provider).extract(spec, [element])

    assert len(result.aggregates) == 1
    assert result.aggregates[0].values == {"合同总额": "100万元"}
    assert result.aggregates[0].status == ExtractionStatus.FOUND
    assert result.aggregates[0].source_artifact_ids == ["artifact-a"]
    assert result.aggregates[0].fields[0].evidence_refs[0].element_id == "el-total"


def test_table_result_preserves_every_parsed_row_without_llm_selection():
    elements = [
        DocumentElement(
            element_id=f"table-1-row-{index}",
            artifact_id="artifact-a",
            page=1,
            element_type=ElementType.TABLE,
            text=f"{name} | {amount}",
            extractor="python-docx",
            extractor_version="1.2.0",
            metadata={
                "location": {"kind": "docx_table_row", "table": 1, "row": index},
                "table_columns": ["姓名", "金额"],
                "table_row": {"姓名": name, "金额": amount},
            },
        )
        for index, (name, amount) in enumerate(
            [("张三", 100), ("李四", 200), ("王五", 300)],
            start=1,
        )
    ]
    spec = _spec("表格内容").model_copy(update={
        "result_contract": ResultContract(
            shape=ResultShape.TABLES,
            cardinality=ResultCardinality.ALL,
            record_grain="原表一行",
            renderer="table_tabs",
            exhaustive=True,
        ),
    })

    result = EvidenceBoundExtractor(StubProvider([])).extract(spec, elements)

    assert len(result.tables) == 1
    assert result.tables[0].columns == ["姓名", "金额"]
    assert [row["姓名"] for row in result.tables[0].rows] == ["张三", "李四", "王五"]
    assert result.coverage["table_rows"] == 3


def test_table_result_merges_only_when_contract_requests_one_table():
    elements = [
        DocumentElement(
            element_id=f"{artifact_id}-row",
            artifact_id=artifact_id,
            page=page,
            element_type=ElementType.TABLE,
            text=f"{name} | {amount}",
            extractor="pdfplumber",
            extractor_version="0.11.7",
            metadata={
                "location": {
                    "kind": "pdf_table_row",
                    "table": 1,
                    "row": 1,
                },
                "table_name": table_name,
                "table_columns": ["姓名", "金额"],
                "table_row": {"姓名": name, "金额": amount},
            },
        )
        for artifact_id, page, table_name, name, amount in (
            ("artifact-a", 1, "订单A", "张三", 100),
            ("artifact-b", 2, "订单B", "李四", 200),
        )
    ]
    spec = _spec("合并表格").model_copy(update={
        "goal": TaskGoal(objective="把两张表合并成一张表"),
        "discovery": DiscoverySpec(
            artifact_ids=["artifact-a", "artifact-b"],
        ),
        "result_contract": ResultContract(
            shape=ResultShape.TABLES,
            cardinality=ResultCardinality.ALL,
            renderer="table_tabs",
            exhaustive=True,
            merge_tables=True,
        ),
    })

    result = EvidenceBoundExtractor(StubProvider([])).extract(spec, elements)

    assert len(result.tables) == 1
    assert result.tables[0].name == "合并表格（2 张原表）"
    assert result.tables[0].columns == ["来源表", "来源页", "列1", "列2"]
    assert result.tables[0].rows == [
        {"来源表": "订单A", "来源页": 1, "列1": "张三", "列2": 100},
        {"来源表": "订单B", "来源页": 2, "列1": "李四", "列2": 200},
    ]
    assert result.coverage["tables_extracted"] == 1
    assert result.coverage["table_rows"] == 2
