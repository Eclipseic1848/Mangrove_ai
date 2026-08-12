# -*- coding: utf-8 -*-
"""把已确认语义与来源绑定编译为文档 PhysicalPlan。"""
from __future__ import annotations

from collections import defaultdict
import re
import uuid
from typing import Any, Mapping, Sequence

from .document_models import (
    AuditOperator,
    AuditRule,
    DocumentAction,
    DocumentPhysicalPlan,
    DocumentPlanStatus,
    DocumentSelection,
    DocumentSource,
)
from .inspection_models import SourceInspectionReport, SourceKind
from .inspectors.tabular import normalize_label
from .models import (
    BoundPlan,
    ContentPolicy,
    OperationType,
    PredicateOperator,
    SemanticTaskPlan,
    TaskFamily,
)
from .physical_models import RuntimeProfileName
from .physical_planner import runtime_policy


CAPABILITY_VERSION = "1.0.0"


def _action(plan: SemanticTaskPlan) -> DocumentAction:
    if plan.task_family == TaskFamily.COMPARE:
        return DocumentAction.COMPARE
    if plan.task_family == TaskFamily.AUDIT:
        return DocumentAction.AUDIT
    if plan.task_family == TaskFamily.SUMMARIZE:
        return DocumentAction.SUMMARIZE
    if plan.task_family == TaskFamily.TRANSLATE:
        return DocumentAction.TRANSLATE
    if plan.content_policy == ContentPolicy.REWRITTEN:
        return DocumentAction.REWRITE
    if plan.task_family == TaskFamily.COMPOSE:
        return DocumentAction.COMPOSE
    return DocumentAction.VERBATIM


def _needs_user(
    plan: SemanticTaskPlan,
    bound_plan: BoundPlan,
    *,
    profile: RuntimeProfileName,
    diagnostics: Sequence[str],
) -> DocumentPhysicalPlan:
    return DocumentPhysicalPlan(
        physical_plan_id=f"docphysical_{uuid.uuid4().hex[:16]}",
        logical_plan_id=plan.plan_id,
        logical_plan_revision=plan.revision,
        logical_plan_hash=plan.canonical_hash(),
        bound_plan_id=bound_plan.bound_plan_id,
        bound_plan_hash=bound_plan.canonical_hash(),
        binding_revision=bound_plan.binding_revision,
        status=DocumentPlanStatus.NEEDS_USER,
        action=_action(plan),
        content_policy=plan.content_policy,
        instruction=plan.objective.original_text,
        runtime_policy=runtime_policy(profile),
        diagnostics=tuple(diagnostics),
    )


def _element_id(physical_ref: str) -> str | None:
    match = re.search(r"/element/([^/]+)$", physical_ref)
    return match.group(1) if match else None


def _audit_operator(value: Any) -> AuditOperator:
    aliases = {
        "eq": AuditOperator.NUMERIC_EQ,
        "lte": AuditOperator.NUMERIC_LTE,
        "gte": AuditOperator.NUMERIC_GTE,
        "contains": AuditOperator.CONTAINS,
        "regex": AuditOperator.REGEX,
        "exists": AuditOperator.EXISTS,
        "not_exists": AuditOperator.NOT_EXISTS,
        "semantic": AuditOperator.SEMANTIC,
        "date_lte": AuditOperator.DATE_LTE,
        "date_gte": AuditOperator.DATE_GTE,
    }
    try:
        return aliases[str(value)]
    except KeyError as exc:
        raise ValueError(f"不支持的文档审查操作符：{value}") from exc


def _explicit_audit_rules(plan: SemanticTaskPlan) -> tuple[AuditRule, ...]:
    rules = []
    for operation in plan.operations:
        if operation.operation != OperationType.AUDIT:
            continue
        raw_rules = operation.params.get("rules")
        if not isinstance(raw_rules, list):
            raise ValueError("audit.rules 必须是数组")
        for index, raw in enumerate(raw_rules):
            if not isinstance(raw, Mapping):
                raise ValueError("每条 audit rule 必须是对象")
            payload = dict(raw)
            payload.setdefault("rule_id", f"rule_{index + 1}")
            payload.setdefault("label", str(payload.get("query") or f"规则{index + 1}"))
            payload["operator"] = _audit_operator(payload.get("operator"))
            rules.append(AuditRule.model_validate(payload))
    return tuple(rules)


def _selection_audit_rules(plan: SemanticTaskPlan) -> tuple[AuditRule, ...]:
    operator_map = {
        PredicateOperator.CONTAINS: AuditOperator.CONTAINS,
        PredicateOperator.EQ: AuditOperator.NUMERIC_EQ,
        PredicateOperator.LTE: AuditOperator.NUMERIC_LTE,
        PredicateOperator.GTE: AuditOperator.NUMERIC_GTE,
    }
    rules = []
    for index, predicate in enumerate(plan.selection):
        operator = operator_map.get(predicate.operator)
        if operator is None:
            continue
        if (
            predicate.operator == PredicateOperator.EQ
            and not isinstance(predicate.value, (int, float))
        ):
            operator = AuditOperator.CONTAINS
        rules.append(
            AuditRule(
                rule_id=f"rule_selection_{index + 1}",
                label=predicate.field,
                query=predicate.field,
                operator=operator,
                value=predicate.value,
            )
        )
    return tuple(rules)


def compile_document_plan(
    plan: SemanticTaskPlan,
    bound_plan: BoundPlan,
    reports: Sequence[SourceInspectionReport],
    *,
    profile: RuntimeProfileName = RuntimeProfileName.WINDOWS_LOCAL,
) -> DocumentPhysicalPlan:
    """确定性冻结文档计划；不在执行时重新解释用户语义。"""

    if plan.task_family == TaskFamily.TABULAR_TRANSFORM:
        return _needs_user(
            plan,
            bound_plan,
            profile=profile,
            diagnostics=("表格任务必须使用 table.duckdb 能力",),
        )
    if not plan.is_executable or not bound_plan.is_executable:
        return _needs_user(
            plan,
            bound_plan,
            profile=profile,
            diagnostics=("逻辑计划或来源绑定尚未确认",),
        )
    if (
        bound_plan.logical_plan_id != plan.plan_id
        or bound_plan.logical_plan_revision != plan.revision
        or bound_plan.logical_plan_hash != plan.canonical_hash()
    ):
        return _needs_user(
            plan,
            bound_plan,
            profile=profile,
            diagnostics=("BoundPlan 与逻辑计划身份或哈希不一致",),
        )
    if any(report.source_kind != SourceKind.DOCUMENT for report in reports):
        return _needs_user(
            plan,
            bound_plan,
            profile=profile,
            diagnostics=("文档计划只能消费文档来源",),
        )

    report_by_artifact = {report.artifact_id: report for report in reports}
    if set(report_by_artifact) != set(bound_plan.input_artifact_hashes):
        return _needs_user(
            plan,
            bound_plan,
            profile=profile,
            diagnostics=("来源检查报告与 BoundPlan 制品集合不一致",),
        )
    sources = []
    for index, artifact_id in enumerate(plan.source_scope.artifact_ids):
        report = report_by_artifact.get(artifact_id)
        if report is None:
            continue
        sources.append(
            DocumentSource(
                source_id=f"document_{index}",
                artifact_id=artifact_id,
                artifact_sha256=report.artifact_sha256,
                detected_format=report.detected_format,
                original_name=report.original_name,
                element_ids=tuple(
                    element_id
                    for target in report.document_targets
                    for element_id in target.element_ids
                ),
            )
        )

    selected: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    selection_labels: dict[str, str] = {
        f"concept:{normalize_label(pattern)}": pattern
        for pattern in plan.source_scope.section_patterns
    }
    for predicate in plan.selection:
        if (
            normalize_label(predicate.field) in {"content", "text", "正文"}
            and predicate.operator
            in {PredicateOperator.CONTAINS, PredicateOperator.EQ}
        ):
            label = str(predicate.value).strip()
            if label:
                selection_labels[
                    f"content_query:{normalize_label(label)}"
                ] = label
        elif plan.task_family != TaskFamily.TABULAR_TRANSFORM:
            selection_labels[
                f"concept:{normalize_label(predicate.field)}"
            ] = predicate.field
    for binding in bound_plan.bindings:
        for target in binding.targets:
            element_id = _element_id(target.physical_ref)
            if element_id:
                selected[binding.semantic_ref][target.artifact_id].append(element_id)
    for artifact_id, pages in plan.source_scope.pages.items():
        report = report_by_artifact.get(artifact_id)
        if report is None:
            continue
        page_set = set(pages)
        semantic_ref = (
            f"pages:{artifact_id}:{','.join(str(page) for page in pages)}"
        )
        selection_labels[semantic_ref] = (
            "第 " + "、".join(str(page) for page in pages) + " 页"
        )
        selected[semantic_ref][artifact_id].extend(
            element_id
            for target in report.document_targets
            if target.page in page_set
            for element_id in target.element_ids
        )
    selections = tuple(
        DocumentSelection(
            semantic_ref=semantic_ref,
            label=selection_labels.get(
                semantic_ref,
                semantic_ref.split(":", 1)[-1],
            ),
            artifact_element_ids={
                artifact_id: tuple(dict.fromkeys(element_ids))
                for artifact_id, element_ids in by_artifact.items()
            },
        )
        for semantic_ref, by_artifact in sorted(selected.items())
    )

    action = _action(plan)
    try:
        audit_rules = (
            _explicit_audit_rules(plan) or _selection_audit_rules(plan)
            if action == DocumentAction.AUDIT
            else ()
        )
    except (TypeError, ValueError) as exc:
        return _needs_user(
            plan,
            bound_plan,
            profile=profile,
            diagnostics=(str(exc),),
        )
    diagnostics = []
    if action == DocumentAction.COMPARE and len(sources) < 2:
        diagnostics.append("文档比较至少需要两个来源")
    if action == DocumentAction.AUDIT and not audit_rules:
        diagnostics.append("审查要求尚未编译成已确认 AuditRulePlan")
    if (
        action == DocumentAction.VERBATIM
        and not plan.source_scope.whole_document
        and not selections
    ):
        diagnostics.append(
            "限定原文提取没有形成章节选择，禁止退化为全文转换"
        )
    if diagnostics:
        return _needs_user(
            plan,
            bound_plan,
            profile=profile,
            diagnostics=tuple(diagnostics),
        )

    target_language = None
    if action == DocumentAction.TRANSLATE:
        for operation in plan.operations:
            value = operation.params.get("target_language")
            if isinstance(value, str) and value.strip():
                target_language = value.strip()
                break

    return DocumentPhysicalPlan(
        physical_plan_id=f"docphysical_{uuid.uuid4().hex[:16]}",
        logical_plan_id=plan.plan_id,
        logical_plan_revision=plan.revision,
        logical_plan_hash=plan.canonical_hash(),
        bound_plan_id=bound_plan.bound_plan_id,
        bound_plan_hash=bound_plan.canonical_hash(),
        binding_revision=bound_plan.binding_revision,
        status=DocumentPlanStatus.READY,
        action=action,
        content_policy=plan.content_policy,
        sources=tuple(sources),
        selections=selections,
        whole_document=plan.source_scope.whole_document,
        audit_rules=audit_rules,
        instruction=plan.objective.original_text,
        target_language=target_language,
        runtime_policy=runtime_policy(profile),
    )
