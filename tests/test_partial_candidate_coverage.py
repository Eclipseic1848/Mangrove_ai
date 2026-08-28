# -*- coding: utf-8 -*-
"""部分候选覆盖结论的领域回归。"""

from src.agentic_runtime.coverage import (
    AuthorizedScope,
    Completeness,
    Confidence,
    CoverageConclusionKind,
    CoverageContractDraft,
    CoverageLedger,
    EvidenceBinding,
    ProposedResult,
    ResultItem,
    ResultCardinality,
    assess_partial_candidate,
    assess_web_candidate,
    freeze_contract,
)


def _contract(*, count: int = 10, completeness: Completeness = Completeness.STRICT):
    return freeze_contract(
        CoverageContractDraft(
            authorized_scope=AuthorizedScope(
                source_ids=("snapshot-a",),
                unit_ids=tuple(f"unit-{index}" for index in range(1, 11)),
            ),
            result_cardinality=ResultCardinality.COUNT,
            result_count=count,
            completeness=completeness,
            ordering="按来源顺序",
            object_boundary="每家公司是一项结果",
            stop_semantics="达到目标数量或完成获准范围后停止",
            interpretation="列出 10 家公司",
            confidence=Confidence.HIGH,
        ),
        bound_source_ids={"snapshot-a"},
        inspected_unit_ids=tuple(f"unit-{index}" for index in range(1, 11)),
    )[0]


def _result(index: int) -> ProposedResult:
    return ProposedResult(
        result_id=f"company-{index}",
        unit_ids=(f"unit-{index}",),
        evidence_refs=(f"evidence-{index}",),
        boundary_evidence_refs=(f"evidence-{index}",),
    )


def _ledger(*, unknown: tuple[str, ...] = (), discovered_extra: bool = False):
    results = tuple(_result(index) for index in range(1, 10))
    discovered = tuple(f"unit-{index}" for index in range(1, 10))
    if discovered_extra:
        discovered += ("unit-10",)
    observed = tuple(
        unit_id
        for unit_id in (f"unit-{index}" for index in range(1, 11))
        if unit_id not in unknown
    )
    return CoverageLedger(
        coverage_contract_id="coverage-a",
        authorized_unit_ids=tuple(f"unit-{index}" for index in range(1, 11)),
        observed_unit_ids=observed,
        discovered_candidate_unit_ids=discovered,
        authoritatively_read_unit_ids=observed,
        unknown_units=unknown,
        evidence_bindings=tuple(
            EvidenceBinding(
                evidence_ref=f"evidence-{index}",
                unit_id=f"unit-{index}",
            )
            for index in range(1, 10)
        ),
        proposed_results=results,
        proposed_candidate_rejections=(
            () if discovered_extra else ()
        ),
        agent_stop_proposal="已完成获准范围",
    )


def test_strict_ten_to_nine_is_confirmed_scope_insufficient_when_scope_is_complete():
    assessment = assess_partial_candidate(_contract(), _ledger())

    assert assessment.is_partial is True
    assert assessment.actual_result_count == 9
    assert assessment.target_result_count == 10
    assert assessment.conclusion.kind is CoverageConclusionKind.CONFIRMED_SCOPE_INSUFFICIENT
    assert assessment.formal_delivery_eligible is False
    assert len(assessment.result_items) == 9
    assert assessment.result_items[0].evidence_refs == ("evidence-1",)


def test_unread_unit_makes_the_coverage_conclusion_unknown():
    assessment = assess_partial_candidate(
        _contract(),
        _ledger(unknown=("unit-10",)),
    )

    assert assessment.conclusion.kind is CoverageConclusionKind.UNKNOWN
    assert "不能判断" in assessment.conclusion.reason
    assert assessment.formal_delivery_eligible is False


def test_discovered_but_omitted_qualified_unit_is_confirmed_omission():
    assessment = assess_partial_candidate(
        _contract(),
        _ledger(discovered_extra=True),
    )

    assert assessment.conclusion.kind is CoverageConclusionKind.CONFIRMED_OMISSION
    assert assessment.same_run_repair_allowed is True
    assert assessment.repair_unit_ids == ("unit-10",)


def test_exploratory_target_can_publish_evidenced_results_with_disclosure():
    assessment = assess_partial_candidate(
        _contract(completeness=Completeness.BEST_EFFORT),
        _ledger(unknown=("unit-10",)),
    )

    assert assessment.is_partial is False
    assert assessment.formal_delivery_eligible is True
    assert assessment.disclosure.failed_unit_count == 0
    assert assessment.disclosure.unknown_unit_count == 1
    assert assessment.disclosure.actual_result_count == 9


def test_web_confirmed_omission_requires_same_run_repair():
    results = tuple(
        ResultItem(
            result_id=f"company-{index}",
            evidence_refs=(f"evidence-{index}",),
        )
        for index in range(1, 10)
    )
    assessment = assess_web_candidate(
        result_items=results,
        qualified_omissions=(
            ResultItem(
                result_id="company-10",
                evidence_refs=("evidence-10",),
            ),
        ),
        target_result_count=10,
        strict=True,
        scope_complete=True,
        failed_page_count=0,
        coverage_unknown=False,
        result_search_complete=True,
        observed_page_count=1,
    )

    assert assessment.conclusion.kind is CoverageConclusionKind.CONFIRMED_OMISSION
    assert assessment.same_run_repair_allowed is True
    assert assessment.repair_unit_ids == ("company-10",)


def test_web_scope_complete_without_result_search_proof_is_unknown():
    assessment = assess_web_candidate(
        result_items=(
            ResultItem(result_id="company-1", evidence_refs=("evidence-1",)),
        ),
        target_result_count=2,
        strict=True,
        scope_complete=True,
        failed_page_count=0,
        coverage_unknown=False,
        result_search_complete=False,
        observed_page_count=1,
    )

    assert assessment.conclusion.kind is CoverageConclusionKind.UNKNOWN
    assert assessment.same_run_repair_allowed is False
    assert assessment.disclosure.authorized_unit_count == 1
    assert assessment.disclosure.observed_unit_count == 1
