# -*- coding: utf-8 -*-
"""覆盖契约、覆盖账本与独立完成门。"""
from __future__ import annotations

from enum import Enum
from typing import Any, Iterable
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResultCardinality(str, Enum):
    FIRST = "first"
    ORDINAL = "ordinal"
    COUNT = "count"
    ALL = "all"


class Completeness(str, Enum):
    STRICT = "strict"
    BEST_EFFORT = "best_effort"


class Confidence(str, Enum):
    HIGH = "high"
    LOW = "low"


class AuthorizedScope(BaseModel):
    """Pi 只能在用户本次任务已经授权的来源和内容单元内收窄范围。"""

    model_config = ConfigDict(extra="forbid")

    source_ids: tuple[str, ...] = Field(min_length=1)
    unit_ids: tuple[str, ...] = ()


class CoverageContractDraft(BaseModel):
    """Pi 提交的业务理解；校验器只检查一致性，不重写语义。"""

    model_config = ConfigDict(extra="forbid")

    authorized_scope: AuthorizedScope
    result_cardinality: ResultCardinality
    result_count: int | None = Field(default=None, ge=1)
    result_ordinal: int | None = Field(default=None, ge=1)
    completeness: Completeness
    ordering: str = Field(min_length=1, max_length=500)
    required_fields: tuple[str, ...] = ()
    object_boundary: str = Field(min_length=1, max_length=1000)
    stop_semantics: str = Field(min_length=1, max_length=1000)
    interpretation: str = Field(min_length=1, max_length=1000)
    confidence: Confidence

    @model_validator(mode="after")
    def validate_cardinality(self) -> "CoverageContractDraft":
        if self.result_cardinality is ResultCardinality.COUNT:
            if self.result_count is None:
                raise ValueError("count 基数必须提供 result_count")
        elif self.result_count is not None:
            raise ValueError("只有 count 基数可以提供 result_count")
        if self.result_cardinality is ResultCardinality.ORDINAL:
            if self.result_ordinal is None:
                raise ValueError("ordinal 基数必须提供 result_ordinal")
        elif self.result_ordinal is not None:
            raise ValueError("只有 ordinal 基数可以提供 result_ordinal")
        return self


class CoverageContract(CoverageContractDraft):
    """经确定性校验后冻结的不可变契约。"""

    contract_id: str = Field(min_length=1)


class EvidenceBinding(BaseModel):
    """权威证据与其来源内容单元的不可伪造绑定。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_ref: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)


class ProposedResult(BaseModel):
    """一个业务结果对象可以跨越多个页面，但每页都必须有对应证据。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    result_id: str = Field(min_length=1, max_length=200)
    unit_ids: tuple[str, ...] = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    boundary_evidence_refs: tuple[str, ...] = ()
    required_field_evidence: dict[str, tuple[str, ...]] = Field(
        default_factory=dict
    )


class CandidateRejection(BaseModel):
    """候选经权威读取后可判为非结果，但必须留下证据。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    unit_id: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = Field(min_length=1)


class CoverageLedger(BaseModel):
    """只记录可验证事实，不替 Pi 决定下一步。"""

    model_config = ConfigDict(extra="forbid")

    coverage_contract_id: str
    authorized_unit_ids: tuple[str, ...]
    observed_unit_ids: tuple[str, ...] = ()
    discovered_candidate_unit_ids: tuple[str, ...] = ()
    authoritatively_read_unit_ids: tuple[str, ...] = ()
    low_quality_units: tuple[str, ...] = ()
    unknown_units: tuple[str, ...] = ()
    parser_versions: tuple[str, ...] = ()
    evidence_bindings: tuple[EvidenceBinding, ...] = ()
    ordering_proof: tuple[str, ...] = ()
    proposed_results: tuple[ProposedResult, ...] = ()
    proposed_candidate_rejections: tuple[CandidateRejection, ...] = ()
    result_empty_confirmed: bool = False
    cache_hits: int = Field(default=0, ge=0)
    agent_stop_proposal: str | None = None
    verifier_decision: str | None = None
    verifier_gaps: tuple[str, ...] = ()

    def public_progress(self) -> dict[str, Any]:
        return {
            "authorized": len(self.authorized_unit_ids),
            "observed": len(self.observed_unit_ids),
            "candidates": len(self.discovered_candidate_unit_ids),
            "authoritatively_read": len(
                self.authoritatively_read_unit_ids
            ),
            "low_quality": len(self.low_quality_units),
            "unknown": len(self.unknown_units),
            "evidence": len(self.evidence_bindings),
            "cache_hits": self.cache_hits,
        }


class CoverageDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    decision: str
    gaps: tuple[str, ...] = ()


def freeze_contract(
    draft: CoverageContractDraft,
    *,
    bound_source_ids: set[str],
    inspected_unit_ids: Iterable[str],
) -> tuple[CoverageContract, CoverageLedger]:
    """只验证授权与结构，不从用户文字推导或修改业务含义。"""

    requested_sources = set(draft.authorized_scope.source_ids)
    if not requested_sources <= bound_source_ids:
        raise ValueError("覆盖契约包含未授权来源")
    inspected_order = tuple(dict.fromkeys(inspected_unit_ids))
    inspected_set = set(inspected_order)
    requested_units = set(draft.authorized_scope.unit_ids)
    if requested_units and not requested_units <= inspected_set:
        raise ValueError("覆盖契约包含不存在或尚未检查的内容单元")
    authorized_units = (
        tuple(
            unit_id
            for unit_id in inspected_order
            if unit_id in requested_units
        )
        if requested_units
        else inspected_order
    )
    if not authorized_units:
        raise ValueError("覆盖契约没有可执行的内容单元")
    contract = CoverageContract(
        **draft.model_dump(),
        contract_id=f"coverage_{uuid.uuid4().hex}",
    )
    ledger = CoverageLedger(
        coverage_contract_id=contract.contract_id,
        authorized_unit_ids=tuple(authorized_units),
        unknown_units=tuple(authorized_units),
    )
    return contract, ledger


def verify_coverage(
    contract: CoverageContract,
    ledger: CoverageLedger,
) -> CoverageDecision:
    """独立完成门：Pi 可以提议结束，但不能签发通过结论。"""

    gaps: list[str] = []
    authorized = set(ledger.authorized_unit_ids)
    observed = set(ledger.observed_unit_ids)
    read = set(ledger.authoritatively_read_unit_ids)
    low_quality = set(ledger.low_quality_units)
    unknown = set(ledger.unknown_units)

    if not ledger.agent_stop_proposal:
        gaps.append("Pi 尚未提交停止提议")
    results = ledger.proposed_results
    rejections = ledger.proposed_candidate_rejections
    result_units = {
        unit_id for result in results for unit_id in result.unit_ids
    }
    evidence_by_ref = {
        binding.evidence_ref: binding.unit_id
        for binding in ledger.evidence_bindings
    }
    if not results and not ledger.result_empty_confirmed:
        gaps.append("停止提议没有声明结果对象")
    if results and ledger.result_empty_confirmed:
        gaps.append("非空结果与空结果确认不能同时提交")
    if not result_units <= authorized:
        gaps.append("有结果内容单元超出冻结的获准范围")
    if not result_units <= read:
        gaps.append("有结果内容单元尚未经过权威读取")
    if result_units & low_quality:
        gaps.append("有结果内容单元尚未达到可信读取质量")
    rejected_units = {rejection.unit_id for rejection in rejections}
    if result_units & rejected_units:
        gaps.append("同一候选不能同时声明为结果和非结果")
    if not rejected_units <= read:
        gaps.append("有排除候选尚未经过权威读取")
    for rejection in rejections:
        mapped_units = {
            evidence_by_ref.get(evidence_ref)
            for evidence_ref in rejection.evidence_refs
        }
        if None in mapped_units or mapped_units != {rejection.unit_id}:
            gaps.append(f"排除候选 {rejection.unit_id} 的证据无效")
    for result in results:
        item_units = set(result.unit_ids)
        mapped_units = {
            evidence_by_ref.get(evidence_ref)
            for evidence_ref in result.evidence_refs
        }
        if None in mapped_units:
            gaps.append(f"结果 {result.result_id} 引用了未知权威证据")
        if not item_units <= mapped_units:
            gaps.append(f"结果 {result.result_id} 并非每个内容单元都有权威证据")
        boundary_units = {
            evidence_by_ref.get(evidence_ref)
            for evidence_ref in result.boundary_evidence_refs
        }
        if None in boundary_units or not boundary_units <= item_units:
            gaps.append(f"结果 {result.result_id} 的对象边界证据无效")
        if not result.boundary_evidence_refs:
            gaps.append(f"结果 {result.result_id} 缺少对象边界证明")
        for field_name, field_refs in result.required_field_evidence.items():
            field_units = {
                evidence_by_ref.get(evidence_ref)
                for evidence_ref in field_refs
            }
            if not field_refs or None in field_units or not field_units <= item_units:
                gaps.append(
                    f"结果 {result.result_id} 的字段 {field_name} 证据无效"
                )
        missing_result_fields = set(contract.required_fields) - set(
            result.required_field_evidence
        )
        if missing_result_fields:
            gaps.append(
                f"结果 {result.result_id} 缺少必需字段证明："
                + "、".join(sorted(missing_result_fields))
            )
    if contract.result_cardinality is ResultCardinality.ALL:
        if contract.completeness is Completeness.STRICT:
            unresolved_candidates = (
                set(ledger.discovered_candidate_unit_ids)
                - result_units
                - rejected_units
            )
            if unresolved_candidates:
                gaps.append(
                    f"仍有 {len(unresolved_candidates)} 个发现候选未形成结果或证据化排除"
                )
            missing = authorized - observed
            if missing:
                gaps.append(f"仍有 {len(missing)} 个获准内容单元未参与可信发现")
            if unknown:
                gaps.append(f"仍有 {len(unknown)} 个未知内容单元")
            if low_quality:
                gaps.append(f"仍有 {len(low_quality)} 个低质量内容单元未解决")
    if contract.result_cardinality is ResultCardinality.FIRST:
        if len(results) != 1:
            gaps.append("首个结果必须且只能声明一个结果对象")
        elif result_units <= authorized:
            first_unit = min(
                results[0].unit_ids,
                key=ledger.authorized_unit_ids.index,
            )
            ordered = list(ledger.authorized_unit_ids)
            preceding = set(ordered[:ordered.index(first_unit)])
            missing_preceding = preceding - observed
            if missing_preceding:
                gaps.append(
                    f"首个结果之前仍有 {len(missing_preceding)} 个内容单元未发现"
                )
        else:
            gaps.append("首个结果不在冻结的获准范围内")
        if not ledger.ordering_proof:
            gaps.append("首个结果缺少稳定顺序证明")
    if contract.result_cardinality is ResultCardinality.ORDINAL:
        assert contract.result_ordinal is not None
        if len(results) != 1:
            gaps.append("序数目标必须且只能声明一个结果对象")
        elif result_units <= authorized:
            first_unit = min(
                results[0].unit_ids,
                key=ledger.authorized_unit_ids.index,
            )
            ordered = list(ledger.authorized_unit_ids)
            preceding = set(ordered[:ordered.index(first_unit)])
            unresolved_preceding = preceding - observed
            unresolved_preceding |= preceding & (unknown | low_quality)
            if unresolved_preceding:
                gaps.append(
                    f"序数目标之前仍有 {len(unresolved_preceding)} 个内容单元未可信发现"
                )
        else:
            gaps.append("序数目标不在冻结的获准范围内")
        if len(ledger.ordering_proof) < contract.result_ordinal:
            gaps.append(
                f"第 {contract.result_ordinal} 个结果缺少足够的稳定顺序证明"
            )
    if contract.result_cardinality is ResultCardinality.COUNT:
        assert contract.result_count is not None
        if len(results) != contract.result_count:
            gaps.append(
                f"指定数量应为 {contract.result_count}，实际声明 {len(results)}"
            )
        elif result_units <= authorized:
            last_result = max(
                ledger.authorized_unit_ids.index(unit_id)
                for unit_id in result_units
            )
            preceding = set(ledger.authorized_unit_ids[:last_result])
            missing_preceding = preceding - observed
            if missing_preceding:
                gaps.append(
                    f"指定数量结果之前仍有 {len(missing_preceding)} 个内容单元未发现"
                )
        else:
            gaps.append("指定数量结果包含冻结范围之外的内容单元")
    if not read and not ledger.result_empty_confirmed:
        gaps.append("候选内容尚未经过权威读取")
    return CoverageDecision(
        passed=not gaps,
        decision="passed" if not gaps else "replan_required",
        gaps=tuple(gaps),
    )
