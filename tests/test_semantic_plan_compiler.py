# -*- coding: utf-8 -*-
"""Phase 4B 批次 1：STP 编译、歧义与有界修复门禁。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Sequence

from src.semantic_harness import compiler as compiler_module
from src.semantic_harness.compiler import InstructorPlanDraftGenerator
from src.semantic_harness.compiler import PlanDraftGenerator
from src.semantic_harness.compiler_graph import compile_semantic_plan
from src.semantic_harness.compiler_models import (
    ClarificationResolution,
    CompileRequest,
    CompileStatus,
    PlanSemanticsDraft,
)
from src.semantic_harness.models import (
    Ambiguity,
    CombineMode,
    CombineSpec,
    ContentPolicy,
    DeliveryFormat,
    DeliverySpec,
    OperationSpec,
    OperationType,
    ObjectiveSpec,
    PostconditionSpec,
    PredicateOperator,
    PredicatePostcondition,
    ProjectionField,
    SemanticTaskPlan,
    SourceScope,
    TaskFamily,
)


def _table_draft(*, with_predicate_check: bool = True) -> PlanSemanticsDraft:
    predicates = ()
    if with_predicate_check:
        predicates = (
            PredicatePostcondition(
                field="姓名",
                operator=PredicateOperator.EQ,
                value="谢超群",
            ),
        )
    return PlanSemanticsDraft(
        task_family=TaskFamily.TABULAR_TRANSFORM,
        normalized_objective="筛选姓名为谢超群的明细，仅保留两列并合并成一张表",
        table_scope="all_detected_tables",
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
        content_policy=ContentPolicy.VERBATIM,
        delivery=DeliverySpec(
            formats=(DeliveryFormat.XLSX,),
            requested_file_count=1,
        ),
        postconditions=PostconditionSpec(
            table_count=1,
            exact_visible_columns=("核销工作量天数", "工作量费用"),
            predicates=predicates,
        ),
    )


class FakeGenerator(PlanDraftGenerator):
    provider = "local"
    model = "fixture-model"
    prompt_version = "stp-v1-test"
    prompt_sha256 = "1" * 64

    def __init__(self, drafts: Sequence[PlanSemanticsDraft | Exception]) -> None:
        self._drafts = list(drafts)
        self.calls = 0

    async def generate(
        self,
        request: CompileRequest,
        *,
        diagnostics,
        attempt: int,
    ) -> PlanSemanticsDraft:
        del request, diagnostics, attempt
        item = self._drafts[min(self.calls, len(self._drafts) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item


def _request(**overrides) -> CompileRequest:
    values = {
        "task_id": "task_xiechaoqun",
        "objective_text": "只提取谢超群的数据，只保留核销工作量天数和工作量费用，合并成一张表",
        "artifact_ids": ("artifact_workload",),
        "accepted_formats": ("pdf", "xlsx"),
        "provider": "local",
        "model": "fixture-model",
    }
    values.update(overrides)
    return CompileRequest.model_validate(values)


def test_instructor_compiler_disables_thinking_and_bounds_generation(
    monkeypatch,
):
    captured: dict = {}

    class _Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return _table_draft()

    class _RawClient:
        def __init__(self, **kwargs):
            captured["client_timeout"] = kwargs["timeout"]

        async def close(self):
            return None

    connection = SimpleNamespace(
        provider="local",
        requested_model="Qwen3.6-35B-A3B",
        model="Qwen3.6-35B-A3B",
        base_url="http://127.0.0.1:6012/v1",
        api_key="local",
        timeout=600,
        trust_env=False,
        extra_body={
            "chat_template_kwargs": {"enable_thinking": True}
        },
    )
    monkeypatch.setattr(
        compiler_module,
        "get_provider",
        lambda: SimpleNamespace(
            resolve_model=lambda provider, model: connection
        ),
    )
    monkeypatch.setattr(compiler_module, "AsyncOpenAI", _RawClient)
    monkeypatch.setattr(
        compiler_module.instructor,
        "from_openai",
        lambda raw, mode: SimpleNamespace(
            chat=SimpleNamespace(completions=_Completions())
        ),
    )
    generator = InstructorPlanDraftGenerator(
        provider="local",
        model=connection.requested_model,
    )

    asyncio.run(
        generator.generate(_request(), diagnostics=(), attempt=0)
    )

    assert captured["client_timeout"] == 120
    assert captured["max_tokens"] == 8192
    assert captured["max_retries"] == 0
    assert captured["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False}
    }


def test_table_intent_compiles_to_ready_plan_and_deterministic_summary():
    generator = FakeGenerator([_table_draft()])

    result = asyncio.run(
        compile_semantic_plan(
            _request(),
            generator=generator,
            plan_id="plan_xiechaoqun",
        )
    )

    assert result.status == CompileStatus.READY
    assert result.plan is not None
    assert result.plan.is_executable is True
    assert [field.name for field in result.plan.projection] == [
        "核销工作量天数",
        "工作量费用",
    ]
    assert result.plan.postconditions.table_count == 1
    assert "筛选：姓名 等于 谢超群" in result.summary
    assert "可见字段：核销工作量天数、工作量费用" in result.summary
    assert result.provenance.repair_attempts == 0
    assert generator.calls == 1


def test_scoped_extract_without_scope_fails_closed_after_bounded_repairs():
    draft = PlanSemanticsDraft(
        task_family=TaskFamily.EXTRACT,
        normalized_objective="提取合同条款",
        content_policy=ContentPolicy.VERBATIM,
        delivery=DeliverySpec(formats=(DeliveryFormat.TXT,)),
    )
    generator = FakeGenerator([draft])

    result = asyncio.run(
        compile_semantic_plan(
            _request(
                objective_text="提取合同中的付款条款",
                accepted_formats=("docx",),
            ),
            generator=generator,
            plan_id="plan_missing_document_scope",
        )
    )

    assert result.status == CompileStatus.FAILED
    assert result.plan is None
    assert generator.calls == 3
    assert any(
        "文档限定提取必须声明章节/概念、页码或选择范围" in item.message
        for item in result.diagnostics
    )


def test_explicit_whole_document_extract_remains_supported():
    draft = PlanSemanticsDraft(
        task_family=TaskFamily.EXTRACT,
        normalized_objective="把整份文档逐字转换为文本",
        whole_document=True,
        content_policy=ContentPolicy.VERBATIM,
        delivery=DeliverySpec(formats=(DeliveryFormat.TXT,)),
    )

    result = asyncio.run(
        compile_semantic_plan(
            _request(
                objective_text="把整个 Word 原样转成 TXT",
                accepted_formats=("docx",),
            ),
            generator=FakeGenerator([draft]),
            plan_id="plan_explicit_whole_document",
        )
    )

    assert result.status == CompileStatus.READY
    assert result.plan is not None
    assert result.plan.source_scope.whole_document is True


def test_trusted_scoped_request_overrides_model_whole_document_flag():
    draft = PlanSemanticsDraft(
        task_family=TaskFamily.EXTRACT,
        normalized_objective="从全文检索并汇总商务条款",
        whole_document=True,
        content_policy=ContentPolicy.VERBATIM,
        delivery=DeliverySpec(formats=(DeliveryFormat.TXT,)),
    )

    result = asyncio.run(
        compile_semantic_plan(
            _request(
                objective_text=(
                    "从整份 Word 中识别并汇总商务条款，"
                    "只输出相关条款，不要输出全文，交付 TXT"
                ),
                section_patterns=("商务条款",),
                accepted_formats=("txt",),
                max_repair_attempts=0,
            ),
            generator=FakeGenerator([draft]),
            plan_id="plan_trusted_scoped_request",
        )
    )

    assert result.status == CompileStatus.READY
    assert result.plan is not None
    assert result.plan.source_scope.whole_document is False
    assert result.plan.source_scope.section_patterns == ("商务条款",)
    assert any(
        item.code == "normalized_trusted_scope"
        for item in result.diagnostics
    )


def test_requested_output_formats_override_model_delivery_draft():
    draft = PlanSemanticsDraft(
        task_family=TaskFamily.EXTRACT,
        normalized_objective="提取商务条款",
        section_patterns=("商务条款",),
        content_policy=ContentPolicy.VERBATIM,
        delivery=DeliverySpec(formats=(DeliveryFormat.DOCX,)),
    )

    result = asyncio.run(
        compile_semantic_plan(
            _request(
                objective_text="提取商务条款并输出 TXT",
                accepted_formats=("docx",),
                requested_output_formats=(DeliveryFormat.TXT,),
                max_repair_attempts=0,
            ),
            generator=FakeGenerator([draft]),
            plan_id="plan_requested_output_txt",
        )
    )

    assert result.status == CompileStatus.READY
    assert result.plan is not None
    assert result.plan.input_contract.accepted_formats == ("docx",)
    assert result.plan.delivery.formats == (DeliveryFormat.TXT,)
    assert result.plan.delivery.requested_file_count == 1


def test_clarification_preserves_prior_scope_and_records_resolution():
    ambiguity = Ambiguity(
        ambiguity_id="extract.mode",
        question="需要逐字原文还是摘要？",
        candidates=("逐字原文", "摘要"),
    )
    prior = SemanticTaskPlan(
        plan_id="plan_scoped_clarification",
        task_id="task_xiechaoqun",
        revision=1,
        task_family=TaskFamily.EXTRACT,
        objective=ObjectiveSpec(
            original_text="提取付款条款",
            normalized_text="提取付款条款",
        ),
        source_scope=SourceScope(
            artifact_ids=("artifact_workload",),
            section_patterns=("付款条款",),
        ),
        record_grain="条款",
        content_policy=ContentPolicy.VERBATIM,
        delivery=DeliverySpec(formats=(DeliveryFormat.TXT,)),
        ambiguities=(ambiguity,),
    )
    clarified_draft = PlanSemanticsDraft(
        task_family=TaskFamily.EXTRACT,
        normalized_objective="逐字提取条款原文",
        content_policy=ContentPolicy.VERBATIM,
        delivery=DeliverySpec(formats=(DeliveryFormat.DOCX,)),
        ambiguities=(ambiguity,),
    )

    result = asyncio.run(
        compile_semantic_plan(
            _request(
                objective_text="提取付款条款\n用户补充：逐字原文",
                accepted_formats=("docx",),
                prior_plan=prior,
                clarification=ClarificationResolution(
                    ambiguity_id="extract.mode",
                    question="需要逐字原文还是摘要？",
                    answer="逐字原文",
                ),
            ),
            generator=FakeGenerator([clarified_draft]),
            plan_id=prior.plan_id,
            revision=2,
        )
    )

    assert result.status == CompileStatus.READY
    assert result.plan is not None
    assert result.plan.source_scope.section_patterns == ("付款条款",)
    assert result.plan.record_grain == "条款"
    assert result.plan.delivery.formats == (DeliveryFormat.TXT,)
    resolved = next(
        item
        for item in result.plan.ambiguities
        if item.ambiguity_id == "extract.mode"
    )
    assert resolved.resolved is True
    assert resolved.resolution == "逐字原文"
    assert any(
        item.code == "preserved_clarification_constraints"
        for item in result.diagnostics
    )


def test_explicit_whole_document_answer_overrides_prior_section_scope():
    ambiguity = Ambiguity(
        ambiguity_id="extract.scope",
        question="只提取付款条款还是转换全文？",
        candidates=("付款条款", "转换全文"),
    )
    prior = SemanticTaskPlan(
        plan_id="plan_scope_change",
        task_id="task_xiechaoqun",
        revision=1,
        task_family=TaskFamily.EXTRACT,
        objective=ObjectiveSpec(
            original_text="转换合同",
            normalized_text="确认转换范围",
        ),
        source_scope=SourceScope(
            artifact_ids=("artifact_workload",),
            section_patterns=("付款条款",),
        ),
        content_policy=ContentPolicy.VERBATIM,
        delivery=DeliverySpec(formats=(DeliveryFormat.TXT,)),
        ambiguities=(ambiguity,),
    )
    whole_draft = PlanSemanticsDraft(
        task_family=TaskFamily.EXTRACT,
        normalized_objective="逐字转换整份合同",
        whole_document=True,
        content_policy=ContentPolicy.VERBATIM,
        delivery=DeliverySpec(formats=(DeliveryFormat.TXT,)),
    )

    result = asyncio.run(
        compile_semantic_plan(
            _request(
                objective_text="转换合同\n用户补充：转换全文",
                accepted_formats=("docx",),
                prior_plan=prior,
                clarification=ClarificationResolution(
                    ambiguity_id="extract.scope",
                    question="只提取付款条款还是转换全文？",
                    answer="转换全文",
                ),
            ),
            generator=FakeGenerator([whole_draft]),
            plan_id=prior.plan_id,
            revision=2,
        )
    )

    assert result.status == CompileStatus.READY
    assert result.plan is not None
    assert result.plan.source_scope.whole_document is True
    assert result.plan.source_scope.section_patterns == ()


def test_entire_document_search_answer_does_not_enable_full_text_output():
    ambiguity = Ambiguity(
        ambiguity_id="extract.location",
        question="商务条款所在的章节标题或页码范围是什么？",
        candidates=("商务条款章节", "整份文档"),
    )
    prior = SemanticTaskPlan(
        plan_id="plan_search_scope",
        task_id="task_xiechaoqun",
        revision=1,
        task_family=TaskFamily.EXTRACT,
        objective=ObjectiveSpec(
            original_text="这个 Word 里的商务条款有哪些？",
            normalized_text="提取所有商务相关合同条款",
        ),
        source_scope=SourceScope(
            artifact_ids=("artifact_workload",),
            section_patterns=("商务条款",),
        ),
        record_grain="条款",
        content_policy=ContentPolicy.VERBATIM,
        delivery=DeliverySpec(formats=(DeliveryFormat.TXT,)),
        ambiguities=(ambiguity,),
    )
    incorrect_whole_draft = PlanSemanticsDraft(
        task_family=TaskFamily.EXTRACT,
        normalized_objective="从整份文档查找商务条款",
        whole_document=True,
        record_grain="条款",
        content_policy=ContentPolicy.VERBATIM,
        delivery=DeliverySpec(formats=(DeliveryFormat.TXT,)),
    )

    result = asyncio.run(
        compile_semantic_plan(
            _request(
                objective_text="这个 Word 里的商务条款有哪些？\n用户补充：整份文档",
                accepted_formats=("docx",),
                prior_plan=prior,
                clarification=ClarificationResolution(
                    ambiguity_id="extract.location",
                    question="商务条款所在的章节标题或页码范围是什么？",
                    answer="整份文档",
                ),
            ),
            generator=FakeGenerator([incorrect_whole_draft]),
            plan_id=prior.plan_id,
            revision=2,
        )
    )

    assert result.status == CompileStatus.READY
    assert result.plan is not None
    assert result.plan.source_scope.whole_document is False
    assert result.plan.source_scope.section_patterns == ()
    assert result.plan.selection[0].field == "content"
    assert result.plan.selection[0].value == "提取所有商务相关合同条款"
    assert result.plan.postconditions.predicates[0].field == "content"
    assert any(
        item.code == "normalized_full_source_search"
        for item in result.diagnostics
    )


def test_page_scoped_extract_is_not_misclassified_as_unscoped():
    draft = PlanSemanticsDraft(
        task_family=TaskFamily.EXTRACT,
        normalized_objective="逐字提取第一页",
        content_policy=ContentPolicy.VERBATIM,
        delivery=DeliverySpec(formats=(DeliveryFormat.TXT,)),
    )

    result = asyncio.run(
        compile_semantic_plan(
            _request(
                objective_text="只提取第一页",
                pages={"artifact_workload": (1,)},
                accepted_formats=("docx",),
            ),
            generator=FakeGenerator([draft]),
            plan_id="plan_page_scope",
        )
    )

    assert result.status == CompileStatus.READY
    assert result.plan is not None
    assert result.plan.source_scope.pages == {"artifact_workload": (1,)}
    assert result.plan.source_scope.whole_document is False


def test_schema_error_is_repaired_once_by_graph_without_hidden_retries():
    generator = FakeGenerator(
        [
            _table_draft(with_predicate_check=False),
            _table_draft(),
        ]
    )

    result = asyncio.run(
        compile_semantic_plan(
            _request(),
            generator=generator,
            plan_id="plan_repaired",
        )
    )

    assert result.status == CompileStatus.READY
    assert result.provenance.repair_attempts == 1
    assert generator.calls == 2
    assert any(item.code == "invalid_plan" for item in result.diagnostics)


def test_detail_row_grain_is_safely_inferred_without_prompt_special_case():
    draft = _table_draft().model_copy(update={"record_grain": None})
    generator = FakeGenerator([draft])

    result = asyncio.run(
        compile_semantic_plan(
            _request(),
            generator=generator,
            plan_id="plan_inferred_grain",
        )
    )

    assert result.status == CompileStatus.READY
    assert result.plan is not None
    assert result.plan.record_grain == "source_detail_row"
    assert generator.calls == 1
    assert any(
        item.code == "inferred_source_detail_grain"
        for item in result.diagnostics
    )


def test_graph_stops_after_two_repairs():
    generator = FakeGenerator([_table_draft(with_predicate_check=False)])

    result = asyncio.run(
        compile_semantic_plan(
            _request(max_repair_attempts=2),
            generator=generator,
            plan_id="plan_failed",
        )
    )

    assert result.status == CompileStatus.FAILED
    assert result.plan is None
    assert result.provenance.repair_attempts == 2
    assert generator.calls == 3


def test_material_ambiguity_returns_one_question_and_never_claims_ready():
    draft = _table_draft().model_copy(
        update={
            "ambiguities": (
                Ambiguity(
                    ambiguity_id="grain",
                    question="每一行代表原始明细，还是按人员汇总？",
                    candidates=("原始明细", "按人员汇总"),
                ),
                Ambiguity(
                    ambiguity_id="aggregate",
                    question="工作量费用需要原值还是求和？",
                    candidates=("保留原值", "按人员求和"),
                ),
            )
        }
    )
    generator = FakeGenerator([draft])

    result = asyncio.run(
        compile_semantic_plan(
            _request(),
            generator=generator,
            plan_id="plan_ambiguous",
        )
    )

    assert result.status == CompileStatus.NEEDS_USER
    assert result.plan is not None
    assert result.plan.is_executable is False
    assert result.clarification is not None
    assert result.clarification.ambiguity_id == "grain"
    assert generator.calls == 1


def test_binder_and_renderer_questions_do_not_block_logical_plan():
    draft = _table_draft().model_copy(
        update={
            "ambiguities": (
                Ambiguity(
                    ambiguity_id="physical_field",
                    question="谢超群对应的实际字段名是姓名还是员工姓名？",
                    candidates=("姓名", "员工姓名"),
                ),
                Ambiguity(
                    ambiguity_id="merge_key",
                    question="合并多张表时使用哪个关联键？",
                    candidates=("姓名", "工号"),
                ),
                Ambiguity(
                    ambiguity_id="output_files",
                    question="DOCX 和 PDF 是两个文件还是同一内容的两种格式？",
                    candidates=("两个文件", "两种格式"),
                ),
            ),
            "delivery": DeliverySpec(
                formats=(DeliveryFormat.DOCX, DeliveryFormat.PDF),
            ),
        }
    )
    generator = FakeGenerator([draft])

    result = asyncio.run(
        compile_semantic_plan(
            _request(),
            generator=generator,
            plan_id="plan_deferred_questions",
        )
    )

    assert result.status == CompileStatus.READY
    assert result.plan is not None
    assert all(not item.material for item in result.plan.ambiguities)
    assert any(
        item.code == "deferred_non_logical_ambiguity"
        for item in result.diagnostics
    )


def test_compare_operation_normalizes_task_family_and_safe_strategy_question():
    draft = PlanSemanticsDraft(
        task_family=TaskFamily.TABULAR_TRANSFORM,
        normalized_objective="比较合同条款差异并保留原文证据",
        record_grain="单条款跨合同对比记录",
        operations=(
            OperationSpec(
                operation=OperationType.COMPARE,
                params={"comparison_type": "text_difference"},
            ),
        ),
        content_policy=ContentPolicy.VERBATIM,
        delivery=DeliverySpec(
            formats=(DeliveryFormat.DOCX, DeliveryFormat.PDF),
        ),
        ambiguities=(
            Ambiguity(
                ambiguity_id="comparison_strategy",
                question="差异采用逐字比较还是语义实质比较？",
                candidates=("逐字比较", "语义实质比较"),
            ),
        ),
    )
    generator = FakeGenerator([draft])

    result = asyncio.run(
        compile_semantic_plan(
            _request(),
            generator=generator,
            plan_id="plan_compare",
        )
    )

    assert result.status == CompileStatus.READY
    assert result.plan is not None
    assert result.plan.task_family == TaskFamily.COMPARE
    assert result.plan.ambiguities[0].material is False


def test_audit_operation_normalizes_family_and_keeps_typed_rules():
    draft = PlanSemanticsDraft(
        task_family=TaskFamily.EXTRACT,
        normalized_objective="核查交付周期是否不超过30天",
        operations=(
            OperationSpec(
                operation=OperationType.AUDIT,
                params={
                    "rules": [
                        {
                            "rule_id": "rule_delivery_days",
                            "label": "交付周期不超过30天",
                            "query": "交付周期",
                            "operator": "lte",
                            "value": 30,
                            "unit": "天",
                        }
                    ]
                },
            ),
        ),
        content_policy=ContentPolicy.VERBATIM,
        delivery=DeliverySpec(formats=(DeliveryFormat.DOCX,)),
    )

    result = asyncio.run(
        compile_semantic_plan(
            _request(objective_text="核查交付周期是否不超过30天"),
            generator=FakeGenerator([draft]),
            plan_id="plan_audit",
        )
    )

    assert result.status == CompileStatus.READY
    assert result.plan is not None
    assert result.plan.task_family == TaskFamily.AUDIT
    assert result.plan.operations[0].params["rules"][0]["value"] == 30


def test_unconfirmed_external_provider_never_calls_generator():
    generator = FakeGenerator([_table_draft()])

    result = asyncio.run(
        compile_semantic_plan(
            _request(provider="deepseek", external_api_confirmed=False),
            generator=generator,
            plan_id="plan_external_blocked",
        )
    )

    assert result.status == CompileStatus.NEEDS_USER
    assert result.plan is None
    assert result.clarification is not None
    assert result.clarification.ambiguity_id == "risk.external_api"
    assert generator.calls == 0


def test_four_generic_task_families_are_expressible():
    contract = PlanSemanticsDraft(
        task_family=TaskFamily.EXTRACT,
        normalized_objective="逐字摘录商务条款并整理成 Word",
        section_patterns=("付款", "交付", "违约"),
        content_policy=ContentPolicy.VERBATIM,
        delivery=DeliverySpec(formats=(DeliveryFormat.DOCX,)),
    )
    comparison = PlanSemanticsDraft(
        task_family=TaskFamily.COMPARE,
        normalized_objective="比较多份合同商务条款差异",
        operations=(OperationSpec(operation=OperationType.COMPARE),),
        content_policy=ContentPolicy.VERBATIM,
        delivery=DeliverySpec(formats=(DeliveryFormat.DOCX, DeliveryFormat.PDF)),
    )
    formatting = _table_draft().model_copy(
        update={
            "operations": (
                OperationSpec(operation=OperationType.SORT, params={"field": "日期"}),
                OperationSpec(operation=OperationType.DEDUPLICATE),
            )
        }
    )

    assert contract.content_policy == ContentPolicy.VERBATIM
    assert comparison.operations[0].operation == OperationType.COMPARE
    assert [item.operation for item in formatting.operations] == [
        OperationType.SORT,
        OperationType.DEDUPLICATE,
    ]
