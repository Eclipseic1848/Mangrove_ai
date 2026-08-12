# -*- coding: utf-8 -*-
"""有证据的语义字段/章节绑定；只生成 BoundPlan，不执行数据操作。"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import re
from typing import Dict, Mapping, Optional, Protocol, Sequence
from urllib.parse import urlparse
import uuid

from rapidfuzz import fuzz
from scipy.optimize import linear_sum_assignment

from src.config.settings import settings
from src.memory.embeddings import rerank_scores

from .compiler_models import ClarificationRequest
from .inspection_models import (
    BindProvenance,
    BindResult,
    BindStatus,
    BindingCandidate,
    InspectionStatus,
    SourceInspectionReport,
    SourceKind,
    TargetKind,
)
from .inspectors.tabular import normalize_label
from .models import (
    Ambiguity,
    Binding,
    BindingEvidence,
    BindingStatus,
    BindingTarget,
    BoundPlan,
    OperationType,
    PredicateOperator,
    SemanticTaskPlan,
    TaskFamily,
)


BINDER_VERSION = "batch2-binder-v2"
THRESHOLD_VERSION = "batch2-heldout-v1"
AUTO_BIND_THRESHOLD = 0.90
MARGIN_THRESHOLD = 0.08
RECALL_CUTOFF = 0.45
_RERANK_INSTRUCT = "判断真实列名或文档片段是否表达用户要求绑定的同一业务概念"


@dataclass(frozen=True)
class SemanticReference:
    semantic_ref: str
    label: str
    kind: str
    literals: tuple[str, ...] = ()


class SemanticScoreProvider(Protocol):
    name: str

    def score(self, query: str, documents: Sequence[str]) -> Optional[list[float]]:
        """返回与 documents 同序的分数；不可用时返回 None。"""

    def classify(
        self,
        query: str,
        evidence: Sequence[tuple[str, str]],
    ) -> Sequence[object]:
        """逐条分类真实文档证据；不可用或协议错误时应抛错。"""


def _is_local_or_lan_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host in {"localhost", "0.0.0.0"}:
        return True
    try:
        return ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class LocalRerankScoreProvider:
    """只允许本地/LAN 语义能力；配置为公网时直接禁用。"""

    name = "local_rerank"
    classifier_name = "local_structural_instructor_v1"

    def score(self, query: str, documents: Sequence[str]) -> Optional[list[float]]:
        base = settings.rerank_base_url or ""
        if not base or not _is_local_or_lan_url(base):
            return None
        return rerank_scores(
            query,
            list(documents),
            instruct=_RERANK_INSTRUCT,
        )

    def classify(
        self,
        query: str,
        evidence: Sequence[tuple[str, str]],
    ) -> Sequence[object]:
        from src.services.document_extraction import (
            InstructorSemanticMatchProvider,
        )

        return InstructorSemanticMatchProvider(provider="local").classify(
            query,
            evidence,
        )


def semantic_references(plan: SemanticTaskPlan) -> tuple[SemanticReference, ...]:
    """把 selection/projection/章节中重复出现的同一概念合并。"""

    collected: Dict[str, SemanticReference] = {}
    literals: Dict[str, list[str]] = {}
    derived_fields = {
        normalize_label(str(aggregate.get("output", "")))
        for operation in plan.operations
        if operation.operation == OperationType.AGGREGATE
        for aggregate in operation.params.get("aggregates", [])
        if isinstance(aggregate, Mapping) and aggregate.get("output")
    }

    def add(label: str, *, kind: str, literal: object = None) -> None:
        clean = str(label).strip()
        if not clean:
            return
        key = f"{kind}:{normalize_label(clean)}"
        if key not in collected:
            collected[key] = SemanticReference(
                semantic_ref=key,
                label=clean,
                kind=kind,
            )
        if literal is not None and str(literal).strip():
            literals.setdefault(key, []).append(str(literal).strip())

    for predicate in plan.selection:
        if plan.task_family != TaskFamily.TABULAR_TRANSFORM:
            if (
                normalize_label(predicate.field) in {"content", "text", "正文"}
                and predicate.operator
                in {PredicateOperator.CONTAINS, PredicateOperator.EQ}
            ):
                add(str(predicate.value), kind="content_query")
            else:
                add(predicate.field, kind="concept", literal=predicate.value)
        else:
            add(predicate.field, kind="field", literal=predicate.value)
    for field in plan.projection:
        if not (
            plan.task_family != TaskFamily.TABULAR_TRANSFORM
            and normalize_label(field.name) in {"content", "text", "正文"}
        ) and normalize_label(field.name) not in derived_fields:
            add(field.name, kind="field")
    for pattern in plan.source_scope.section_patterns:
        add(pattern, kind="concept")
    for operation in plan.operations:
        for key in (
            "field",
            "column",
            "by",
            "group_by",
            "fields",
            "columns",
            "keys",
        ):
            value = operation.params.get(key)
            if isinstance(value, str):
                add(value, kind="field")
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, str):
                        add(item, kind="field")
                    elif isinstance(item, Mapping) and item.get("column"):
                        add(str(item["column"]), kind="field")
        if operation.operation == OperationType.AGGREGATE:
            for aggregate in operation.params.get("aggregates", []):
                if isinstance(aggregate, Mapping) and aggregate.get("column"):
                    add(str(aggregate["column"]), kind="field")

    return tuple(
        reference.__class__(
            semantic_ref=reference.semantic_ref,
            label=reference.label,
            kind=reference.kind,
            literals=tuple(dict.fromkeys(literals.get(key, ()))),
        )
        for key, reference in collected.items()
    )


def _literal_support(literals: Sequence[str], samples: Sequence[str]) -> float:
    if not literals:
        return 0.0
    normalized_samples = {normalize_label(value) for value in samples}
    matched = sum(
        1 for literal in literals if normalize_label(literal) in normalized_samples
    )
    return matched / len(literals)


def _candidate_total(
    *,
    name_score: float,
    semantic_score: float,
    literal_support: float,
    type_support: float,
    evidence_quality: float,
    contradiction_penalty: float,
) -> float:
    deterministic = (
        0.82 * name_score
        + 0.10 * literal_support
        + 0.03 * type_support
        + 0.05 * evidence_quality
    )
    semantic = (
        0.62 * name_score
        + 0.18 * semantic_score
        + 0.10 * literal_support
        + 0.05 * type_support
        + 0.05 * evidence_quality
    )
    return max(0.0, min(1.0, max(deterministic, semantic) - contradiction_penalty))


def _table_candidates(
    reference: SemanticReference,
    reports: Sequence[SourceInspectionReport],
) -> list[BindingCandidate]:
    candidates = []
    for report in reports:
        if report.source_kind != SourceKind.TABULAR:
            continue
        for table in report.tables:
            for column in table.columns:
                name_score = (
                    fuzz.WRatio(reference.label, column.raw_name) / 100.0
                    if column.raw_name
                    else 0.0
                )
                if normalize_label(reference.label) == column.normalized_name:
                    name_score = 1.0
                literal = _literal_support(reference.literals, column.sample_values)
                penalty = 0.0
                reasons = [f"列名相似度 {name_score:.3f}"]
                if literal:
                    reasons.append(f"筛选字面量样本支持 {literal:.3f}")
                if column.duplicate_group:
                    penalty += 0.10
                    reasons.append("规范化后存在重复表头")
                if column.inferred_type == "empty":
                    penalty += 0.35
                    reasons.append("候选列样本全空")
                total = _candidate_total(
                    name_score=name_score,
                    semantic_score=0,
                    literal_support=literal,
                    type_support=1.0,
                    evidence_quality=1.0,
                    contradiction_penalty=penalty,
                )
                if total < RECALL_CUTOFF:
                    continue
                candidates.append(
                    BindingCandidate(
                        semantic_ref=reference.semantic_ref,
                        semantic_label=reference.label,
                        physical_ref=column.physical_ref,
                        artifact_id=column.artifact_id,
                        target_kind=TargetKind.TABLE_COLUMN,
                        name_score=name_score,
                        literal_support=literal,
                        type_support=1.0,
                        evidence_quality=1.0,
                        contradiction_penalty=penalty,
                        total_score=total,
                        evidence_reasons=tuple(reasons),
                        evidence_samples=column.sample_values[:3],
                    )
                )
    return candidates


def _document_candidates(
    reference: SemanticReference,
    reports: Sequence[SourceInspectionReport],
) -> list[BindingCandidate]:
    candidates = []
    normalized_query = normalize_label(reference.label)
    for report in reports:
        if report.source_kind != SourceKind.DOCUMENT:
            continue
        for target in report.document_targets:
            name_score = fuzz.WRatio(reference.label, target.label) / 100.0
            normalized_text = normalize_label(target.text_excerpt)
            if normalized_query and normalized_query in normalized_text:
                name_score = 1.0
            quality = target.confidence if target.evidence_ready else 0.25
            penalty = 0.0 if target.evidence_ready else 0.35
            reasons = [f"文档文本相似度 {name_score:.3f}"]
            if not target.evidence_ready:
                reasons.append("缺少可复核位置或需要人工复核")
            total = _candidate_total(
                name_score=name_score,
                semantic_score=0,
                literal_support=0,
                type_support=1.0,
                evidence_quality=quality,
                contradiction_penalty=penalty,
            )
            if total < RECALL_CUTOFF:
                continue
            candidates.append(
                BindingCandidate(
                    semantic_ref=reference.semantic_ref,
                    semantic_label=reference.label,
                    physical_ref=target.physical_ref,
                    artifact_id=target.artifact_id,
                    target_kind=target.target_kind,
                    name_score=name_score,
                    type_support=1.0,
                    evidence_quality=quality,
                    contradiction_penalty=penalty,
                    total_score=total,
                    evidence_reasons=tuple(reasons),
                    evidence_samples=(target.text_excerpt[:160],),
                )
            )
    return candidates


def _semantic_document_candidates(
    reference: SemanticReference,
    reports: Sequence[SourceInspectionReport],
    *,
    query: str,
    semantic_provider: SemanticScoreProvider,
) -> list[BindingCandidate]:
    """让本地结构化分类器完整扫描文档目标，不经过模糊召回截断。"""

    targets = []
    evidence_contexts: list[str] = []
    for report in reports:
        if report.source_kind != SourceKind.DOCUMENT:
            continue
        section_context = ""
        for target in report.document_targets:
            text = target.text_excerpt.strip()
            is_heading = (
                target.target_kind == TargetKind.DOCUMENT_SECTION
                or (
                    len(text) <= 60
                    and bool(
                        re.match(
                            r"^(?:[一二三四五六七八九十百零〇]+|\d+)"
                            r"(?:[、.．]|\s)",
                            text,
                        )
                    )
                )
                or (
                    len(text) <= 30
                    and not text.startswith(("", "•", "*", "-", "–", "—"))
                    and not any(
                        punctuation in text
                        for punctuation in ("，", "。", "；", "：", ",", ";", ":")
                    )
                    and not any(
                        marker in text
                        for marker in (
                            "必须",
                            "不得",
                            "有权",
                            "承担",
                            "提供",
                        )
                    )
                    and not text.startswith(
                        ("要求", "需", "应", "必须", "不得")
                    )
                )
            )
            if is_heading:
                section_context = text
            structural_hints = []
            if is_heading:
                structural_hints.append("section_heading")
            if all(
                marker in text
                for marker in ("功能模块", "功能描述", "功能需求")
            ):
                structural_hints.append("product_feature_requirement_row")
            if all(
                marker in text
                for marker in ("技术分类", "技术要求条款")
            ):
                structural_hints.append("technical_requirement_row")
            evidence_contexts.append(
                "\n".join(
                    (
                        f"section_context: {section_context or '(none)'}",
                        "structural_hints: "
                        + (
                            ", ".join(structural_hints)
                            if structural_hints
                            else "(none)"
                        ),
                        f"current_text: {text}",
                    )
                )
            )
            targets.append(target)
    short_ids = {
        f"e{index:04d}": target
        for index, target in enumerate(targets, start=1)
    }
    evidence = [
        (short_id, evidence_contexts[index])
        for index, short_id in enumerate(short_ids)
    ]
    decisions = semantic_provider.classify(query, evidence)
    actual_ids = [
        str(getattr(item, "evidence_id", ""))
        for item in decisions
    ]
    decision_by_id = {
        str(getattr(item, "evidence_id", "")): item
        for item in decisions
    }
    if (
        len(actual_ids) != len(short_ids)
        or len(set(actual_ids)) != len(actual_ids)
        or set(decision_by_id) != set(short_ids)
    ):
        raise ValueError("语义分类必须逐条且仅逐条一次覆盖全部文档目标")
    invalid_categories = sorted({
        str(getattr(item, "category", ""))
        for item in decisions
        if str(getattr(item, "category", ""))
        not in {"matches_query", "does_not_match", "uncertain"}
    })
    if invalid_categories:
        raise ValueError(
            f"语义分类返回未知类别：{invalid_categories[:3]}"
        )
    has_uncertain = any(
        str(getattr(item, "category", "")) == "uncertain"
        for item in decisions
    )
    candidates = []
    for short_id, target in short_ids.items():
        decision = decision_by_id[short_id]
        category = str(getattr(decision, "category", ""))
        if category not in {"matches_query", "uncertain"}:
            continue
        reason = str(getattr(decision, "reason", "")).strip()
        confidence = 0.70 if has_uncertain else 0.95
        candidates.append(
            BindingCandidate(
                semantic_ref=reference.semantic_ref,
                semantic_label=reference.label,
                physical_ref=target.physical_ref,
                artifact_id=target.artifact_id,
                target_kind=target.target_kind,
                name_score=0.0,
                semantic_score=(1.0 if category == "matches_query" else 0.5),
                type_support=1.0,
                evidence_quality=(
                    target.confidence if target.evidence_ready else 0.25
                ),
                contradiction_penalty=(
                    0.0 if target.evidence_ready else 0.35
                ),
                total_score=confidence,
                evidence_reasons=(
                    f"本地证据级语义分类：{category}",
                    reason or "模型未提供分类理由",
                ),
                evidence_samples=(target.text_excerpt[:160],),
            )
        )
    return candidates


def _semantic_query(
    plan: SemanticTaskPlan,
    reference: SemanticReference,
) -> str:
    """区分语义目标澄清与搜索位置/示例补充，避免把“等”误作封闭清单。"""

    original = plan.objective.original_text.strip()
    parts = re.split(r"\n用户补充[：:]", original, maxsplit=1)
    primary = parts[0].strip()
    supplement = parts[1].strip() if len(parts) > 1 else ""
    restrictive = any(
        marker in supplement
        for marker in (
            "仅",
            "只要",
            "只提取",
            "排除",
            "不要",
            "不包括",
            "限定",
            "改为",
        )
    )
    expansive = any(
        marker in supplement
        for marker in (
            "所有",
            "全部",
            "包括",
            "等",
            "无论",
            "整份",
            "全文",
            "全篇",
        )
    )
    use_supplement = bool(supplement and (restrictive or not expansive))
    if use_supplement:
        query = plan.objective.normalized_text.strip()
    else:
        query = primary or plan.objective.normalized_text.strip()
    if (
        not (supplement and expansive and not restrictive)
        and normalize_label(reference.label) not in normalize_label(query)
    ):
        query = f"{query}\n补充目标：{reference.label}"
    return query


def generate_candidates(
    plan: SemanticTaskPlan,
    reports: Sequence[SourceInspectionReport],
    *,
    semantic_provider: SemanticScoreProvider | None = None,
) -> tuple[tuple[SemanticReference, ...], tuple[BindingCandidate, ...], str]:
    references = semantic_references(plan)
    all_candidates = []
    backend_name = "deterministic"
    for reference in references:
        if reference.kind == "field":
            candidates = _table_candidates(reference, reports)
        elif reference.kind == "content_query":
            classifier = getattr(semantic_provider, "classify", None)
            if semantic_provider is None or not callable(classifier):
                candidates = []
            else:
                candidates = _semantic_document_candidates(
                    reference,
                    reports,
                    query=_semantic_query(plan, reference),
                    semantic_provider=semantic_provider,
                )
                backend_name = getattr(
                    semantic_provider,
                    "classifier_name",
                    semantic_provider.name,
                )
        else:
            candidates = _document_candidates(reference, reports)
        candidates.sort(key=lambda item: item.total_score, reverse=True)
        if (
            semantic_provider
            and candidates
            and reference.kind != "content_query"
        ):
            top = candidates[:20]
            scores = semantic_provider.score(
                reference.label,
                [
                    f"{item.semantic_label} -> {item.evidence_samples} "
                    f"{item.evidence_reasons}"
                    for item in top
                ],
            )
            if scores and len(scores) == len(top):
                backend_name = semantic_provider.name
                updated = []
                for item, score in zip(top, scores):
                    value = max(0.0, min(1.0, float(score)))
                    updated.append(
                        item.model_copy(
                            update={
                                "semantic_score": value,
                                "total_score": _candidate_total(
                                    name_score=item.name_score,
                                    semantic_score=value,
                                    literal_support=item.literal_support,
                                    type_support=item.type_support,
                                    evidence_quality=item.evidence_quality,
                                    contradiction_penalty=item.contradiction_penalty,
                                ),
                            }
                        )
                    )
                candidates = updated + candidates[20:]
                candidates.sort(key=lambda item: item.total_score, reverse=True)
        all_candidates.extend(candidates)
    return references, tuple(all_candidates), backend_name


def _table_group(physical_ref: str) -> str:
    return physical_ref.rsplit("/column/", 1)[0]


def _target_from_candidate(candidate: BindingCandidate, *, user_selected: bool = False) -> BindingTarget:
    reasons = list(candidate.evidence_reasons)
    if user_selected:
        reasons.append("用户在候选集合中明确选择")
    return BindingTarget(
        physical_ref=candidate.physical_ref,
        artifact_id=candidate.artifact_id,
        target_kind=candidate.target_kind.value,
        confidence=candidate.total_score,
        evidence=(
            BindingEvidence(
                source_ref=candidate.physical_ref,
                reason="；".join(reasons),
                samples=candidate.evidence_samples,
            ),
        ),
    )


def _resolve_table_candidates(
    references: Sequence[SemanticReference],
    candidates: Sequence[BindingCandidate],
    reports: Sequence[SourceInspectionReport],
) -> tuple[dict[str, list[BindingTarget]], dict[str, set[str]]]:
    targets: dict[str, list[BindingTarget]] = {
        reference.semantic_ref: [] for reference in references
    }
    unresolved_groups = {
        reference.semantic_ref: set() for reference in references
    }
    groups: Dict[str, list[BindingCandidate]] = {
        table.table_ref: []
        for report in reports
        if report.source_kind == SourceKind.TABULAR
        for table in report.tables
    }
    for candidate in candidates:
        if candidate.target_kind == TargetKind.TABLE_COLUMN:
            groups.setdefault(_table_group(candidate.physical_ref), []).append(candidate)

    for table_group, group_candidates in groups.items():
        physical_refs = list(
            dict.fromkeys(item.physical_ref for item in group_candidates)
        )
        matrix = [
            [
                next(
                    (
                        item.total_score
                        for item in group_candidates
                        if item.semantic_ref == reference.semantic_ref
                        and item.physical_ref == physical_ref
                    ),
                    0.0,
                )
                for physical_ref in physical_refs
            ]
            for reference in references
        ]
        if not matrix or not physical_refs:
            for reference in references:
                unresolved_groups[reference.semantic_ref].add(table_group)
            continue
        rows, columns = linear_sum_assignment(matrix, maximize=True)
        assigned_rows = set()
        for row, column in zip(rows, columns):
            reference = references[row]
            assigned_rows.add(row)
            score = matrix[row][column]
            if score < RECALL_CUTOFF:
                unresolved_groups[reference.semantic_ref].add(table_group)
                continue
            selected = next(
                item
                for item in group_candidates
                if item.semantic_ref == reference.semantic_ref
                and item.physical_ref == physical_refs[column]
            )
            alternatives = sorted(
                (
                    item.total_score
                    for item in group_candidates
                    if item.semantic_ref == reference.semantic_ref
                    and item.physical_ref != selected.physical_ref
                ),
                reverse=True,
            )
            margin = score - (alternatives[0] if alternatives else 0.0)
            if score >= AUTO_BIND_THRESHOLD and margin >= MARGIN_THRESHOLD:
                targets[reference.semantic_ref].append(
                    _target_from_candidate(selected)
                )
            else:
                unresolved_groups[reference.semantic_ref].add(table_group)
        for row, reference in enumerate(references):
            if row not in assigned_rows:
                unresolved_groups[reference.semantic_ref].add(table_group)
    return targets, unresolved_groups


def _resolve_document_candidates(
    references: Sequence[SemanticReference],
    candidates: Sequence[BindingCandidate],
) -> tuple[dict[str, list[BindingTarget]], set[str]]:
    targets = {reference.semantic_ref: [] for reference in references}
    ambiguous: set[str] = set()
    for reference in references:
        ranked = sorted(
            (
                item
                for item in candidates
                if item.semantic_ref == reference.semantic_ref
                and item.target_kind != TargetKind.TABLE_COLUMN
            ),
            key=lambda item: item.total_score,
            reverse=True,
        )
        accepted = [
            item for item in ranked if item.total_score >= AUTO_BIND_THRESHOLD
        ]
        if accepted:
            targets[reference.semantic_ref].extend(
                _target_from_candidate(item) for item in accepted
            )
        elif ranked:
            ambiguous.add(reference.semantic_ref)
    return targets, ambiguous


def _question_for(
    reference: SemanticReference,
    candidates: Sequence[BindingCandidate],
    *,
    target_group: str | None = None,
) -> ClarificationRequest:
    ranked = sorted(
        (
            item
            for item in candidates
            if item.semantic_ref == reference.semantic_ref
            and (
                target_group is None
                or _table_group(item.physical_ref) == target_group
            )
        ),
        key=lambda item: item.total_score,
        reverse=True,
    )
    if ranked:
        labels = [
            f"{item.evidence_samples[0] if item.evidence_samples else item.physical_ref} "
            f"（匹配度 {item.total_score:.2f}）"
            for item in ranked[:3]
        ]
        if len(labels) == 1:
            labels.append("以上候选都不是")
        return ClarificationRequest(
            ambiguity_id=(
                f"{reference.semantic_ref}|{target_group}"
                if target_group
                else reference.semantic_ref
            ),
            question=f"“{reference.label}”存在多个或低置信候选，请确认应绑定哪一个？",
            candidates=tuple(labels),
        )
    return ClarificationRequest(
        ambiguity_id=(
            f"{reference.semantic_ref}|{target_group}"
            if target_group
            else reference.semantic_ref
        ),
        question=f"没有找到“{reference.label}”对应的真实字段或章节，应停止还是调整目标？",
        candidates=("停止本次任务", "调整目标"),
    )


def bind_semantic_plan(
    plan: SemanticTaskPlan,
    reports: Sequence[SourceInspectionReport],
    *,
    binding_revision: int = 1,
    semantic_provider: SemanticScoreProvider | None = None,
    resolutions: Mapping[str, str] | None = None,
) -> BindResult:
    """生成一个不可变 BoundPlan revision；任何来源不可用时直接阻断。"""

    blocked = [report for report in reports if report.status != InspectionStatus.READY]
    provenance = BindProvenance(
        binder_version=BINDER_VERSION,
        threshold_version=THRESHOLD_VERSION,
        auto_bind_threshold=AUTO_BIND_THRESHOLD,
        margin_threshold=MARGIN_THRESHOLD,
        semantic_backend="deterministic",
    )
    if blocked:
        return BindResult(
            status=BindStatus.BLOCKED,
            logical_plan_id=plan.plan_id,
            logical_plan_revision=plan.revision,
            binding_revision=binding_revision,
            provenance=provenance,
        )

    references, candidates, backend = generate_candidates(
        plan,
        reports,
        semantic_provider=semantic_provider,
    )
    if (
        plan.task_family == TaskFamily.EXTRACT
        and not plan.source_scope.whole_document
        and not plan.source_scope.pages
        and not references
    ):
        raise ValueError(
            "限定提取没有可绑定的章节或概念引用，禁止按全文执行"
        )
    provenance = provenance.model_copy(update={"semantic_backend": backend})
    field_refs = [item for item in references if item.kind == "field"]
    concept_refs = [
        item
        for item in references
        if item.kind in {"concept", "content_query"}
    ]
    table_targets, table_ambiguous = _resolve_table_candidates(
        field_refs,
        candidates,
        reports,
    )
    document_targets, document_ambiguous = _resolve_document_candidates(
        concept_refs,
        candidates,
    )
    target_map = {**table_targets, **document_targets}
    unresolved_groups = {
        **table_ambiguous,
        **{
            semantic_ref: {"document"}
            for semantic_ref in document_ambiguous
        },
    }
    resolutions = dict(resolutions or {})

    bindings = []
    unresolved = []
    first_question = None
    for reference in references:
        selected_targets = list(target_map.get(reference.semantic_ref, ()))
        pending_groups = set(unresolved_groups.get(reference.semantic_ref, ()))
        matching_resolutions = [
            (key, value)
            for key, value in resolutions.items()
            if key == reference.semantic_ref
            or key.startswith(f"{reference.semantic_ref}|")
        ]
        for resolution_key, resolution in matching_resolutions:
            chosen = next(
                (
                    item
                    for item in candidates
                    if item.semantic_ref == reference.semantic_ref
                    and item.physical_ref == resolution
                ),
                None,
            )
            if chosen is not None:
                if all(
                    item.physical_ref != chosen.physical_ref
                    for item in selected_targets
                ):
                    selected_targets.append(
                        _target_from_candidate(chosen, user_selected=True)
                    )
                group = (
                    _table_group(chosen.physical_ref)
                    if chosen.target_kind == TargetKind.TABLE_COLUMN
                    else "document"
                )
                pending_groups.discard(group)
        if selected_targets and not pending_groups:
            confidence = min(item.confidence for item in selected_targets)
            bindings.append(
                Binding(
                    semantic_ref=reference.semantic_ref,
                    status=BindingStatus.BOUND,
                    confidence=confidence,
                    targets=tuple(selected_targets),
                )
            )
            continue

        target_group = (
            sorted(pending_groups)[0] if pending_groups else None
        )
        has_candidates = any(
            item.semantic_ref == reference.semantic_ref
            and (
                target_group in {None, "document"}
                or _table_group(item.physical_ref) == target_group
            )
            for item in candidates
        )
        status = (
            BindingStatus.AMBIGUOUS if has_candidates else BindingStatus.MISSING
        )
        bindings.append(
            Binding(
                semantic_ref=reference.semantic_ref,
                status=status,
                confidence=0.0,
            )
        )
        question = _question_for(
            reference,
            candidates,
            target_group=(
                target_group
                if target_group not in {None, "document"}
                else None
            ),
        )
        unresolved.append(
            Ambiguity(
                ambiguity_id=question.ambiguity_id,
                question=question.question,
                candidates=question.candidates,
            )
        )
        if first_question is None:
            first_question = question

    bound_plan = BoundPlan(
        bound_plan_id=f"bound_{uuid.uuid4().hex[:16]}",
        logical_plan_id=plan.plan_id,
        logical_plan_revision=plan.revision,
        logical_plan_hash=plan.canonical_hash(),
        binding_revision=binding_revision,
        input_artifact_hashes={
            report.artifact_id: report.artifact_sha256 for report in reports
        },
        inspection_report_hashes={
            report.artifact_id: report.canonical_hash() for report in reports
        },
        binder_version=BINDER_VERSION,
        threshold_version=THRESHOLD_VERSION,
        bindings=tuple(bindings),
        unresolved_ambiguities=tuple(unresolved),
    )
    return BindResult(
        status=(
            BindStatus.READY if bound_plan.is_executable else BindStatus.NEEDS_USER
        ),
        logical_plan_id=plan.plan_id,
        logical_plan_revision=plan.revision,
        binding_revision=binding_revision,
        bound_plan=bound_plan,
        candidates=tuple(candidates),
        clarification=first_question,
        provenance=provenance,
    )
