# -*- coding: utf-8 -*-
"""自然语言到 STP 的有界 LangGraph 编译流程。"""
from __future__ import annotations

import uuid
from typing import Optional, Tuple, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from .compiler import PlanDraftGenerator
from .compiler_models import (
    ClarificationRequest,
    CompileDiagnostic,
    CompileRequest,
    CompileResult,
    CompileStatus,
    DiagnosticSeverity,
    PlanProvenance,
    PlanSemanticsDraft,
)
from .models import (
    BudgetSpec,
    ExecutionBoundary,
    InputContract,
    OperationType,
    ObjectiveSpec,
    PredicateOperator,
    PredicatePostcondition,
    RiskPolicy,
    SelectionPredicate,
    SemanticTaskPlan,
    SourceScope,
    TaskFamily,
)
from .summary import render_plan_summary


class _CompilerState(TypedDict, total=False):
    request: CompileRequest
    plan_id: str
    revision: int
    attempt: int
    draft: Optional[PlanSemanticsDraft]
    plan: Optional[SemanticTaskPlan]
    diagnostics: Tuple[CompileDiagnostic, ...]
    result: CompileResult


def _new_plan_id() -> str:
    return f"stp_{uuid.uuid4().hex[:16]}"


def _diagnostic(
    *,
    code: str,
    message: str,
    attempt: int,
    repairable: bool = True,
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR,
) -> CompileDiagnostic:
    return CompileDiagnostic(
        code=code,
        message=message,
        severity=severity,
        repairable=repairable,
        attempt=attempt,
    )


def _safe_exception_message(exc: Exception, *, limit: int = 4000) -> str:
    text = f"{type(exc).__name__}: {exc}"
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…（已截断）"


def _validation_error_message(exc: ValidationError) -> str:
    errors = [
        {
            "path": ".".join(str(item) for item in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors(include_url=False, include_input=False)
    ]
    import json

    return json.dumps(errors, ensure_ascii=False, separators=(",", ":"))


def _apply_safe_defaults(
    request: CompileRequest,
    draft: PlanSemanticsDraft,
    *,
    attempt: int,
) -> tuple[PlanSemanticsDraft, Tuple[CompileDiagnostic, ...]]:
    """只补不会改变用户语义且可机械证明的默认值。"""

    diagnostics = []
    if draft.whole_document and (
        request.section_patterns or request.pages
    ):
        draft = draft.model_copy(update={"whole_document": False})
        diagnostics.append(
            _diagnostic(
                code="normalized_trusted_scope",
                message=(
                    "请求已明确章节或页码范围，"
                    "已禁止模型把检索范围扩大为全文输出"
                ),
                attempt=attempt,
                repairable=False,
                severity=DiagnosticSeverity.WARNING,
            )
        )
    compare_operations = [
        item
        for item in draft.operations
        if item.operation == OperationType.COMPARE
    ]
    audit_operations = [
        item
        for item in draft.operations
        if item.operation == OperationType.AUDIT
    ]
    if compare_operations and draft.task_family != TaskFamily.COMPARE:
        draft = draft.model_copy(update={"task_family": TaskFamily.COMPARE})
        diagnostics.append(
            _diagnostic(
                code="normalized_compare_family",
                message="计划已包含 compare 操作，任务类型规范化为 compare",
                attempt=attempt,
                repairable=False,
                severity=DiagnosticSeverity.WARNING,
            )
        )
    if audit_operations and draft.task_family != TaskFamily.AUDIT:
        draft = draft.model_copy(update={"task_family": TaskFamily.AUDIT})
        diagnostics.append(
            _diagnostic(
                code="normalized_audit_family",
                message="计划已包含 audit 操作，任务类型规范化为 audit",
                attempt=attempt,
                repairable=False,
                severity=DiagnosticSeverity.WARNING,
            )
        )

    normalized_ambiguities = []
    deferred = 0
    has_join = any(
        item.operation == OperationType.JOIN for item in draft.operations
    )
    compare_has_strategy = any(
        item.params.get("comparison_type") for item in compare_operations
    )
    for item in draft.ambiguities:
        question = item.question.lower()
        physical_binding = any(
            marker in question
            for marker in (
                "实际字段",
                "字段名",
                "列名",
                "对应的业务字段",
                "真实字段",
            )
        )
        unnecessary_merge_key = (
            draft.combine.mode.value == "one_table"
            and not has_join
            and any(
                marker in question
                for marker in ("关联键", "连接键", "合并键", "join key")
            )
        )
        renderer_detail = (
            len(draft.delivery.formats) > 1
            and any(
                marker in question
                for marker in (
                    "两个独立文件",
                    "两个文件",
                    "两种格式",
                    "同一份文档",
                )
            )
        )
        resolved_compare_strategy = (
            compare_has_strategy
            and "差异" in question
            and any(marker in question for marker in ("逐字", "语义"))
        )
        if item.material and (
            physical_binding
            or unnecessary_merge_key
            or renderer_detail
            or resolved_compare_strategy
        ):
            item = item.model_copy(update={"material": False})
            deferred += 1
        normalized_ambiguities.append(item)
    if deferred:
        draft = draft.model_copy(
            update={"ambiguities": tuple(normalized_ambiguities)}
        )
        diagnostics.append(
            _diagnostic(
                code="deferred_non_logical_ambiguity",
                message=f"{deferred} 个字段绑定或渲染问题已延后到对应阶段",
                attempt=attempt,
                repairable=False,
                severity=DiagnosticSeverity.WARNING,
            )
        )

    if (
        draft.task_family == TaskFamily.TABULAR_TRANSFORM
        and not draft.record_grain
    ):
        has_group_or_aggregate = any(
            item.operation in {OperationType.GROUP, OperationType.AGGREGATE}
            for item in draft.operations
        )
        grain_ambiguity = any(
            item.material
            and not item.resolved
            and (
                "grain" in item.ambiguity_id.lower()
                or "粒度" in item.question
                or "每一行" in item.question
            )
            for item in draft.ambiguities
        )
        if grain_ambiguity:
            return draft.model_copy(
                update={"record_grain": "unresolved_record_grain"}
            ), tuple(diagnostics)
        if not has_group_or_aggregate:
            diagnostic = _diagnostic(
                code="inferred_source_detail_grain",
                message="无分组或聚合操作，结果粒度安全推导为源明细行",
                attempt=attempt,
                repairable=False,
                severity=DiagnosticSeverity.WARNING,
            )
            return draft.model_copy(
                update={"record_grain": "source_detail_row"}
            ), (*diagnostics, diagnostic)
    return draft, tuple(diagnostics)


def _preserve_clarification_context(
    request: CompileRequest,
    draft: PlanSemanticsDraft,
    *,
    attempt: int,
) -> tuple[PlanSemanticsDraft, Tuple[CompileDiagnostic, ...]]:
    """澄清只能补充当前歧义，不得静默删除上一版已确定的其他约束。"""

    prior = request.prior_plan
    clarification = request.clarification
    if prior is None or clarification is None:
        return draft, ()

    updates: dict[str, object] = {}
    clarification_question = clarification.question.strip().lower()
    clarification_answer = clarification.answer.strip().lower()
    asks_source_location = any(
        marker in clarification_question
        for marker in (
            "所在",
            "章节标题",
            "页码范围",
            "来源范围",
            "查找范围",
            "检索范围",
        )
    )
    answers_entire_source = any(
        marker in clarification_answer
        for marker in ("整份文档", "整篇文档", "全文", "全篇")
    )
    asks_full_output = any(
        marker in f"{clarification_question} {clarification_answer}"
        for marker in (
            "转换全文",
            "输出全文",
            "全文转写",
            "全部原文",
            "完整原文",
            "原样转换",
        )
    )
    if (
        draft.task_family == TaskFamily.EXTRACT
        and draft.whole_document
        and asks_source_location
        and answers_entire_source
        and not asks_full_output
    ):
        semantic_query = prior.objective.normalized_text.strip()
        predicate = SelectionPredicate(
            field="content",
            operator=PredicateOperator.CONTAINS,
            value=semantic_query,
        )
        existing_checks = tuple(draft.postconditions.predicates)
        predicate_check = PredicatePostcondition(
            field=predicate.field,
            operator=predicate.operator,
            value=predicate.value,
        )
        updates["whole_document"] = False
        updates["section_patterns"] = ()
        updates["selection"] = draft.selection or (predicate,)
        if not draft.selection and predicate_check not in existing_checks:
            updates["postconditions"] = draft.postconditions.model_copy(
                update={
                    "predicates": (*existing_checks, predicate_check),
                }
            )
    if draft.table_scope is None and prior.source_scope.table_scope:
        updates["table_scope"] = prior.source_scope.table_scope
    if (
        not draft.whole_document
        and not draft.section_patterns
        and prior.source_scope.section_patterns
    ):
        updates["section_patterns"] = prior.source_scope.section_patterns
    if not draft.time_ranges and prior.source_scope.time_ranges:
        updates["time_ranges"] = prior.source_scope.time_ranges
    if (
        not draft.whole_document
        and prior.source_scope.whole_document
        and not draft.section_patterns
        and not draft.selection
    ):
        updates["whole_document"] = True
    if not draft.whole_document and not draft.selection and prior.selection:
        updates["selection"] = prior.selection
    if not draft.projection and prior.projection:
        updates["projection"] = prior.projection
    if draft.record_grain is None and prior.record_grain:
        updates["record_grain"] = prior.record_grain
    if not draft.operations and prior.operations:
        updates["operations"] = prior.operations
    if (
        draft.combine == draft.combine.__class__()
        and prior.combine != prior.combine.__class__()
    ):
        updates["combine"] = prior.combine
    if (
        draft.postconditions == draft.postconditions.__class__()
        and prior.postconditions != prior.postconditions.__class__()
    ):
        updates["postconditions"] = prior.postconditions
    clarification_text = (
        f"{clarification.ambiguity_id} {clarification.question}"
    ).lower()
    changes_delivery = any(
        token in clarification_text
        for token in (
            "delivery",
            "format",
            "output",
            "交付",
            "格式",
            "输出",
            "文件类型",
        )
    )
    if not changes_delivery and draft.delivery != prior.delivery:
        updates["delivery"] = prior.delivery
    if (
        draft.evidence_policy == draft.evidence_policy.__class__()
        and prior.evidence_policy != prior.evidence_policy.__class__()
    ):
        updates["evidence_policy"] = prior.evidence_policy

    current_ambiguities = [
        item
        for item in draft.ambiguities
        if item.ambiguity_id != clarification.ambiguity_id
    ]
    current_ids = {item.ambiguity_id for item in current_ambiguities}
    current_questions = {item.question.strip() for item in current_ambiguities}
    preserved_ambiguities = []
    for item in prior.ambiguities:
        if item.ambiguity_id == clarification.ambiguity_id:
            preserved_ambiguities.append(
                item.model_copy(
                    update={
                        "resolved": True,
                        "resolution": clarification.answer,
                    }
                )
            )
            continue
        if (
            item.ambiguity_id not in current_ids
            and item.question.strip() not in current_questions
        ):
            preserved_ambiguities.append(item)
    if preserved_ambiguities:
        updates["ambiguities"] = tuple(
            (*preserved_ambiguities, *current_ambiguities)
        )

    if not updates:
        return draft, ()
    normalized_search_scope = (
        "whole_document" in updates
        and updates["whole_document"] is False
        and asks_source_location
        and answers_entire_source
    )
    return (
        draft.model_copy(update=updates),
        (
            _diagnostic(
                code=(
                    "normalized_full_source_search"
                    if normalized_search_scope
                    else "preserved_clarification_constraints"
                ),
                message=(
                    "已把“整份文档”解释为全篇检索范围，保留语义目标且禁止全文输出"
                    if normalized_search_scope
                    else (
                        "已保留上一版中与当前回答无冲突的范围、选择、粒度、"
                        "操作、交付要求和后置条件"
                    )
                ),
                attempt=attempt,
                repairable=False,
                severity=DiagnosticSeverity.WARNING,
            ),
        ),
    )


def _assemble_plan(
    request: CompileRequest,
    draft: PlanSemanticsDraft,
    *,
    plan_id: str,
    revision: int,
) -> SemanticTaskPlan:
    """模型语义与服务端可信身份/范围合并，来源 ID 永远以请求为准。"""

    accepted_formats = request.accepted_formats or draft.accepted_formats
    accepted_media_types = (
        request.accepted_media_types or draft.accepted_media_types
    )
    delivery = draft.delivery
    if request.requested_output_formats:
        delivery = delivery.model_copy(
            update={
                "formats": request.requested_output_formats,
                "requested_file_count": len(
                    request.requested_output_formats
                ),
            }
        )
    return SemanticTaskPlan(
        plan_id=plan_id,
        task_id=request.task_id,
        revision=revision,
        task_family=draft.task_family,
        objective=ObjectiveSpec(
            original_text=request.objective_text,
            normalized_text=draft.normalized_objective,
        ),
        source_scope=SourceScope(
            artifact_ids=request.artifact_ids,
            source_ids=request.source_ids,
            table_scope=request.table_scope or draft.table_scope,
            pages=request.pages,
            section_patterns=(
                request.section_patterns or draft.section_patterns
            ),
            time_ranges=request.time_ranges or draft.time_ranges,
            whole_document=draft.whole_document,
        ),
        input_contract=InputContract(
            accepted_formats=accepted_formats,
            accepted_media_types=accepted_media_types,
        ),
        selection=draft.selection,
        projection=draft.projection,
        record_grain=draft.record_grain,
        operations=draft.operations,
        combine=draft.combine,
        content_policy=draft.content_policy,
        evidence_policy=draft.evidence_policy,
        delivery=delivery,
        postconditions=draft.postconditions,
        risk_policy=RiskPolicy(
            execution_boundary=(
                ExecutionBoundary.LOCAL_OR_LAN
                if request.provider == "local"
                else ExecutionBoundary.EXTERNAL_API
            ),
            external_api_confirmed=request.external_api_confirmed,
        ),
        budgets=BudgetSpec(max_repair_attempts=request.max_repair_attempts),
        ambiguities=draft.ambiguities,
    )


def _provenance(
    generator: PlanDraftGenerator,
    *,
    request: CompileRequest,
    repairs: int,
) -> PlanProvenance:
    return PlanProvenance(
        provider=getattr(generator, "provider", request.provider),
        model=getattr(generator, "model", request.model or "default"),
        prompt_version=generator.prompt_version,
        prompt_sha256=generator.prompt_sha256,
        repair_attempts=repairs,
    )


def _external_confirmation_result(
    request: CompileRequest,
    generator: PlanDraftGenerator,
    *,
    plan_id: str,
    revision: int,
) -> CompileResult:
    diagnostic = _diagnostic(
        code="external_confirmation_required",
        message="外部模型调用需要用户明确确认数据外发风险",
        attempt=0,
        repairable=False,
    )
    return CompileResult(
        plan_id=plan_id,
        task_id=request.task_id,
        revision=revision,
        status=CompileStatus.NEEDS_USER,
        diagnostics=(diagnostic,),
        clarification=ClarificationRequest(
            ambiguity_id="risk.external_api",
            question="是否允许将本次用户目标发送到所选外部模型？",
            candidates=("允许本次发送", "改用本地模型"),
        ),
        provenance=_provenance(generator, request=request, repairs=0),
    )


def _build_graph(generator: PlanDraftGenerator):
    async def generate(state: _CompilerState) -> dict:
        request = state["request"]
        attempt = state["attempt"]
        try:
            draft = await generator.generate(
                request,
                diagnostics=state.get("diagnostics", ()),
                attempt=attempt,
            )
            return {"draft": draft, "plan": None}
        except Exception as exc:  # 模型/协议错误必须进入同一有界预算
            diagnostic = _diagnostic(
                code="model_generation_failed",
                message=_safe_exception_message(exc),
                attempt=attempt,
            )
            return {
                "draft": None,
                "plan": None,
                "diagnostics": (*state.get("diagnostics", ()), diagnostic),
            }

    def validate(state: _CompilerState) -> dict:
        draft = state.get("draft")
        if draft is None:
            return {"plan": None}
        draft, default_diagnostics = _apply_safe_defaults(
            state["request"],
            draft,
            attempt=state["attempt"],
        )
        draft, clarification_diagnostics = _preserve_clarification_context(
            state["request"],
            draft,
            attempt=state["attempt"],
        )
        diagnostics = (
            *state.get("diagnostics", ()),
            *default_diagnostics,
            *clarification_diagnostics,
        )
        try:
            plan = _assemble_plan(
                state["request"],
                draft,
                plan_id=state["plan_id"],
                revision=state["revision"],
            )
            return {"plan": plan, "diagnostics": diagnostics}
        except ValidationError as exc:
            diagnostic = _diagnostic(
                code="invalid_plan",
                message=_validation_error_message(exc),
                attempt=state["attempt"],
            )
            return {
                "plan": None,
                "diagnostics": (*diagnostics, diagnostic),
            }

    def route_after_validate(state: _CompilerState) -> str:
        if state.get("plan") is not None:
            return "finalize"
        if state["attempt"] < state["request"].max_repair_attempts:
            return "prepare_retry"
        return "fail"

    def prepare_retry(state: _CompilerState) -> dict:
        return {"attempt": state["attempt"] + 1, "draft": None, "plan": None}

    def finalize(state: _CompilerState) -> dict:
        plan = state["plan"]
        assert plan is not None
        unresolved = [
            item
            for item in plan.ambiguities
            if item.material and not item.resolved
        ]
        clarification = None
        status = CompileStatus.READY
        if unresolved:
            item = unresolved[0]
            status = CompileStatus.NEEDS_USER
            clarification = ClarificationRequest(
                ambiguity_id=item.ambiguity_id,
                question=item.question,
                candidates=item.candidates,
            )
        result = CompileResult(
            plan_id=state["plan_id"],
            task_id=state["request"].task_id,
            revision=state["revision"],
            status=status,
            plan=plan,
            summary=render_plan_summary(plan),
            diagnostics=state.get("diagnostics", ()),
            clarification=clarification,
            provenance=_provenance(
                generator,
                request=state["request"],
                repairs=state["attempt"],
            ),
        )
        return {"result": result}

    def fail(state: _CompilerState) -> dict:
        result = CompileResult(
            plan_id=state["plan_id"],
            task_id=state["request"].task_id,
            revision=state["revision"],
            status=CompileStatus.FAILED,
            diagnostics=state.get("diagnostics", ()),
            provenance=_provenance(
                generator,
                request=state["request"],
                repairs=state["attempt"],
            ),
        )
        return {"result": result}

    builder = StateGraph(_CompilerState)
    builder.add_node("generate", generate)
    builder.add_node("validate", validate)
    builder.add_node("prepare_retry", prepare_retry)
    builder.add_node("finalize", finalize)
    builder.add_node("fail", fail)
    builder.add_edge(START, "generate")
    builder.add_edge("generate", "validate")
    builder.add_conditional_edges(
        "validate",
        route_after_validate,
        {
            "prepare_retry": "prepare_retry",
            "finalize": "finalize",
            "fail": "fail",
        },
    )
    builder.add_edge("prepare_retry", "generate")
    builder.add_edge("finalize", END)
    builder.add_edge("fail", END)
    return builder.compile()


async def compile_semantic_plan(
    request: CompileRequest,
    *,
    generator: PlanDraftGenerator,
    plan_id: str | None = None,
    revision: int = 1,
) -> CompileResult:
    """编译一个不可变 revision；外部模型未确认时在调用前阻断。"""

    resolved_plan_id = plan_id or _new_plan_id()
    if request.provider != "local" and not request.external_api_confirmed:
        return _external_confirmation_result(
            request,
            generator,
            plan_id=resolved_plan_id,
            revision=revision,
        )
    graph = _build_graph(generator)
    state = await graph.ainvoke(
        {
            "request": request,
            "plan_id": resolved_plan_id,
            "revision": revision,
            "attempt": 0,
            "draft": None,
            "plan": None,
            "diagnostics": (),
        },
        config={"recursion_limit": 16},
    )
    return state["result"]
