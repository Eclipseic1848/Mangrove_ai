# -*- coding: utf-8 -*-
from __future__ import annotations

from src.evaluation.g1_manifest import qualification_gaps


def test_diagnostic_manifest_is_not_qualifying() -> None:
    gaps = qualification_gaps({
        "evaluation_status": "diagnostic_only",
        "independent_heldout": False,
        "cases": [],
    })

    assert "清单不是独立 held-out 状态" in gaps
    assert "任务数不足 30" in gaps
    assert "缺少安全夹具：cross_owner" in gaps


def test_complete_independent_manifest_satisfies_machine_contract() -> None:
    categories = ["pdf", "docx", "xlsx", "csv", "compound", "fuzzy"]
    formats = ["csv", "json", "txt"]
    safety_tags = [
        "permission_denied",
        "cross_owner",
        "user_isolation",
        "forbidden_content",
        "failure_not_success",
    ]
    cases = []
    for index in range(30):
        traps = []
        if index < 11:
            traps.append("paraphrase")
        if 11 <= index < 22:
            traps.append("similar")
        cases.append({
            "id": f"H{index + 1}",
            "category": categories[index % len(categories)],
            "output_format": formats[index % len(formats)],
            "traps": traps,
            "safety_tags": [safety_tags[index]] if index < len(safety_tags) else [],
        })
    safety_config = {
        "permission_denied": {
            "expected_failure_stage": "formal_delivery",
            "expected_failure_code": "permission_denied",
            "owner_id": "owner-a",
            "publish_actor_id": "owner-b",
        },
        "cross_owner": {
            "expected_failure_stage": "formal_delivery",
            "expected_failure_code": "formal_delivery_missing",
            "owner_id": "owner-a",
            "qualification_owner_id": "owner-b",
        },
        "user_isolation": {
            "expected_failure_stage": "formal_delivery",
            "expected_failure_code": "formal_delivery_missing",
            "owner_id": "owner-c",
            "qualification_owner_id": "owner-d",
        },
        "forbidden_content": {
            "expected_failure_stage": "assertion",
            "expected_failure_code": "assertion_rejected",
        },
        "failure_not_success": {
            "expected_failure_stage": "verification",
            "expected_failure_code": "verification_failed",
        },
    }
    for index, (tag, config) in enumerate(safety_config.items()):
        cases[index].update({
            "safety_tags": [tag],
            "expected_outcome": "rejected",
            **config,
        })
    manifest = {
        "evaluation_status": "heldout",
        "independent_heldout": True,
        "blind_set_attestation": {
            "provider": "independent-evaluator",
            "provided_at": "2026-08-20T12:00:00Z",
            "code_freeze_sha256": "a" * 64,
        },
        "cases": cases,
    }

    assert qualification_gaps(
        manifest,
        expected_code_freeze_sha256="a" * 64,
    ) == ()

    cases[10]["expected_outcome"] = "rejected"
    assert "功能夹具 H11 必须期望 formal_delivery" in qualification_gaps(
        manifest,
        expected_code_freeze_sha256="a" * 64,
    )
    cases[10].pop("expected_outcome")
    cases[0]["safety_tags"].append("cross_owner")
    assert "安全夹具 H1 必须一项一标签且期望 rejected" in qualification_gaps(
        manifest,
        expected_code_freeze_sha256="a" * 64,
    )


def test_ratios_and_attestation_are_bound_to_actual_freeze() -> None:
    cases = [
        {
            "id": f"H{index}",
            "category": ("pdf", "docx", "xlsx", "csv", "compound", "fuzzy")[index % 6],
            "output_format": ("csv", "json", "txt")[index % 3],
            "traps": ["paraphrase", "similar"] if index < 11 else [],
            "safety_tags": [],
        }
        for index in range(60)
    ]
    for index, tag in enumerate((
        "permission_denied",
        "cross_owner",
        "user_isolation",
        "forbidden_content",
        "failure_not_success",
    )):
        cases[index].update({
            "safety_tags": [tag],
            "expected_outcome": "rejected",
            "expected_failure_stage": (
                "assertion" if tag == "forbidden_content" else "verification"
            ),
            "expected_failure_code": (
                "assertion_rejected" if tag == "forbidden_content" else "verification_failed"
            ),
        })
    manifest = {
        "evaluation_status": "heldout",
        "independent_heldout": True,
        "blind_set_attestation": {
            "provider": "independent-evaluator",
            "provided_at": "garbage",
            "code_freeze_sha256": "a" * 64,
        },
        "cases": cases,
    }

    gaps = qualification_gaps(
        manifest,
        expected_code_freeze_sha256="b" * 64,
    )

    assert "独立盲集提供时间无效" in gaps
    assert "盲集声明未绑定当前代码冻结身份" in gaps
    assert "同义/口语/省略/顺序变化任务不足 20" in gaps
    assert "相似表/章节或冲突来源任务不足 20" in gaps
