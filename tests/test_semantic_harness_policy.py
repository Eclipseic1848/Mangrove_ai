# -*- coding: utf-8 -*-
"""Phase 4B 批次 5 运行契约和有界修复策略。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.semantic_harness.harness_models import (
    HarnessLoopPolicy,
    RepairAction,
    RepairDecision,
    RepairProposal,
)
from src.semantic_harness.harness_policy import decide_repair
from src.semantic_harness.models import FailureKind


def _decision(
    *,
    kind: FailureKind,
    repairs: int = 0,
    retries: int = 0,
    same: int = 1,
):
    return decide_repair(
        run_id="run-1",
        failure_kind=kind,
        failure_fingerprint="a" * 64,
        reason="脱敏失败说明",
        policy=HarnessLoopPolicy(),
        repair_rounds=repairs,
        transient_retries=retries,
        same_failure_count=same,
    )


def test_transient_retry_and_global_bounds_are_hard_limits() -> None:
    assert _decision(kind=FailureKind.TRANSIENT).proposal.action == (
        RepairAction.RETRY_SAME_TOOL
    )
    assert _decision(
        kind=FailureKind.TRANSIENT,
        retries=2,
    ).proposal.action == RepairAction.STOP
    assert _decision(
        kind=FailureKind.INVALID_PLAN,
        same=2,
    ).proposal.action == RepairAction.STOP
    assert _decision(
        kind=FailureKind.INVALID_PLAN,
        repairs=5,
    ).proposal.action == RepairAction.STOP


def test_semantic_or_permission_change_never_auto_approves() -> None:
    decision = _decision(kind=FailureKind.POLICY_DENIED)
    assert decision.proposal.action == RepairAction.REQUEST_USER
    assert decision.requires_user is True
    assert decision.approved is False

    proposal = RepairProposal(
        proposal_id="proposal-1",
        run_id="run-1",
        failure_kind=FailureKind.INVALID_PLAN,
        failure_fingerprint="b" * 64,
        action=RepairAction.SEMANTIC_REPLAN,
        reason="需要改变字段含义",
        changes_user_semantics=True,
    )
    with pytest.raises(ValidationError):
        RepairDecision(
            decision_id="decision-1",
            proposal=proposal,
            approved=True,
            policy_reason="不应通过",
        )


def test_policy_rejects_unknown_fields_and_over_budget_values() -> None:
    with pytest.raises(ValidationError):
        HarnessLoopPolicy.model_validate(
            {"max_total_repair_rounds": 5, "arbitrary_patch": {}}
        )
    with pytest.raises(ValidationError):
        HarnessLoopPolicy(max_semantic_replans=3)
