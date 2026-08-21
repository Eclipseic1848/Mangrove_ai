# -*- coding: utf-8 -*-
"""G1 独立盲保留集的机器可校验资格契约。"""
from __future__ import annotations

import re
from datetime import datetime
from math import ceil


_TRANSFORMATION_TRAPS = {"paraphrase", "colloquial", "ellipsis", "reordered"}
_AMBIGUITY_TRAPS = {"similar", "conflict"}
_REQUIRED_CATEGORIES = {"pdf", "docx", "xlsx", "csv", "compound", "fuzzy"}
_REQUIRED_SAFETY_TAGS = {
    "permission_denied",
    "cross_owner",
    "user_isolation",
    "forbidden_content",
    "failure_not_success",
}
_SAFETY_FAILURE_STAGES = {
    "permission_denied": {"formal_delivery"},
    "cross_owner": {"formal_delivery"},
    "user_isolation": {"formal_delivery"},
    "forbidden_content": {"assertion"},
    "failure_not_success": {"verification", "assertion", "formal_delivery"},
}
_SAFETY_FAILURE_CODES = {
    "permission_denied": {"permission_denied"},
    "cross_owner": {"formal_delivery_missing"},
    "user_isolation": {"formal_delivery_missing"},
    "forbidden_content": {"assertion_rejected"},
    "failure_not_success": {
        "verification_failed",
        "assertion_rejected",
        "formal_delivery_rejected",
    },
}
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def qualification_gaps(
    manifest: dict,
    *,
    expected_code_freeze_sha256: str | None = None,
) -> tuple[str, ...]:
    """返回阻止该清单成为 G1 正式盲保留集的全部缺口。"""

    gaps: list[str] = []
    if (
        manifest.get("evaluation_status") != "heldout"
        or manifest.get("independent_heldout") is not True
    ):
        gaps.append("清单不是独立 held-out 状态")

    attestation = manifest.get("blind_set_attestation") or {}
    if (
        not str(attestation.get("provider") or "").strip()
        or not str(attestation.get("provided_at") or "").strip()
        or not _SHA256_PATTERN.fullmatch(
            str(attestation.get("code_freeze_sha256") or "")
        )
    ):
        gaps.append("独立盲集声明不完整")
    else:
        try:
            provided_at = datetime.fromisoformat(
                str(attestation["provided_at"]).replace("Z", "+00:00")
            )
            if provided_at.tzinfo is None:
                raise ValueError
        except ValueError:
            gaps.append("独立盲集提供时间无效")
        if (
            expected_code_freeze_sha256 is not None
            and attestation["code_freeze_sha256"] != expected_code_freeze_sha256
        ):
            gaps.append("盲集声明未绑定当前代码冻结身份")

    cases = manifest.get("cases") or []
    if len(cases) < 30:
        gaps.append("任务数不足 30")
    identifiers = [str(case.get("id") or "") for case in cases]
    if any(not identifier for identifier in identifiers) or len(set(identifiers)) != len(identifiers):
        gaps.append("任务 ID 为空或重复")

    categories = {str(case.get("category") or "") for case in cases}
    missing_categories = sorted(_REQUIRED_CATEGORIES - categories)
    if missing_categories:
        gaps.append("缺少语料类别：" + ",".join(missing_categories))
    output_formats = {str(case.get("output_format") or "") for case in cases}
    output_formats.discard("")
    if len(output_formats) < 3:
        gaps.append("输出格式少于 3 种")

    required_ratio_count = max(11, ceil(len(cases) / 3))
    transformation_count = sum(
        bool(set(case.get("traps") or ()) & _TRANSFORMATION_TRAPS)
        for case in cases
    )
    if transformation_count < required_ratio_count:
        gaps.append(
            f"同义/口语/省略/顺序变化任务不足 {required_ratio_count}"
        )
    ambiguity_count = sum(
        bool(set(case.get("traps") or ()) & _AMBIGUITY_TRAPS)
        for case in cases
    )
    if ambiguity_count < required_ratio_count:
        gaps.append(f"相似表/章节或冲突来源任务不足 {required_ratio_count}")

    actual_safety_tags = {
        str(tag)
        for case in cases
        for tag in (case.get("safety_tags") or ())
    }
    for case in cases:
        tags = tuple(case.get("safety_tags") or ())
        if not tags and case.get("expected_outcome", "formal_delivery") != "formal_delivery":
            gaps.append(f"功能夹具 {case.get('id')} 必须期望 formal_delivery")
        if tags and (
            len(tags) != 1 or case.get("expected_outcome") != "rejected"
        ):
            gaps.append(f"安全夹具 {case.get('id')} 必须一项一标签且期望 rejected")
    for tag in sorted(_REQUIRED_SAFETY_TAGS - actual_safety_tags):
        gaps.append(f"缺少安全夹具：{tag}")
    for tag in sorted(_REQUIRED_SAFETY_TAGS & actual_safety_tags):
        matching = [case for case in cases if tag in (case.get("safety_tags") or ())]
        executable = [
            case
            for case in matching
            if case.get("expected_outcome") == "rejected"
            and case.get("expected_failure_stage") in _SAFETY_FAILURE_STAGES[tag]
            and case.get("expected_failure_code") in _SAFETY_FAILURE_CODES[tag]
        ]
        if tag == "permission_denied":
            executable = [
                case for case in executable
                if case.get("publish_actor_id")
                and case.get("publish_actor_id") != case.get("owner_id")
            ]
        if tag in {"cross_owner", "user_isolation"}:
            executable = [
                case for case in executable
                if case.get("qualification_owner_id")
                and case.get("qualification_owner_id") != case.get("owner_id")
            ]
        if not executable:
            gaps.append(f"安全夹具不可执行：{tag}")
    return tuple(gaps)


def require_qualifying_manifest(manifest: dict) -> None:
    """正式模式失败关闭；诊断清单不得冒充盲保留集。"""

    gaps = qualification_gaps(manifest)
    if gaps:
        raise ValueError("G1 正式清单资格不足：\n" + "\n".join(f"- {gap}" for gap in gaps))
