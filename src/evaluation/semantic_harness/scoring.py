# -*- coding: utf-8 -*-
"""批次 0 的确定性评分器；模型或工具自报成功不参与判定。"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from src.semantic_harness.models import (
    VerificationCheck,
    VerificationReport,
    VerificationStatus,
)

from .fixtures import Batch0Case


def _canonical_records(records: Any) -> list[dict[str, Any]]:
    return [
        {str(key): value for key, value in dict(record).items()}
        for record in list(records or [])
    ]


def score_table_result(
    case: Batch0Case,
    result: Mapping[str, Any],
    *,
    logical_plan_hash: str,
) -> VerificationReport:
    """按行、列、谓词和证据同时验收表格结果。"""

    records = _canonical_records(result.get("records"))
    visible_columns = tuple(str(item) for item in result.get("visible_columns") or ())
    evidence = list(result.get("evidence") or [])
    expected_records_value = result.get("_expected_records")
    if expected_records_value is None:
        raise ValueError("评分输入缺少内部 _expected_records")
    expected_records = _canonical_records(expected_records_value)

    evidence_by_record = {
        int(item["record_index"]): item
        for item in evidence
        if isinstance(item, Mapping) and "record_index" in item
    }
    evidence_coverage = (
        len(evidence_by_record) / len(records)
        if records
        else (1.0 if not expected_records else 0.0)
    )
    predicate_matches = 0
    for index in range(len(records)):
        item = evidence_by_record.get(index, {})
        if dict(item.get("selection") or {}) == case.selection:
            predicate_matches += 1
    predicate_ratio = predicate_matches / len(records) if records else 0.0

    checks = (
        VerificationCheck(
            code="exact_table_count",
            passed=int(result.get("table_count") or 0) == case.expected.table_count,
            expected=case.expected.table_count,
            actual=int(result.get("table_count") or 0),
            message="结果表数量必须与计划一致",
        ),
        VerificationCheck(
            code="exact_visible_columns",
            passed=visible_columns == case.expected.visible_columns,
            expected=list(case.expected.visible_columns),
            actual=list(visible_columns),
            message="可见业务列必须精确匹配且顺序一致",
        ),
        VerificationCheck(
            code="exact_row_count",
            passed=len(records) == case.expected.row_count,
            expected=case.expected.row_count,
            actual=len(records),
            message="结果行数必须与 Golden 一致",
        ),
        VerificationCheck(
            code="exact_records",
            passed=records == expected_records,
            expected=expected_records,
            actual=records,
            message="结果记录和值必须与 Golden 完全一致",
        ),
        VerificationCheck(
            code="selection_predicate_ratio",
            passed=predicate_ratio == 1.0,
            expected=1.0,
            actual=predicate_ratio,
            message="每个输出行的来源记录都必须满足筛选条件",
        ),
        VerificationCheck(
            code="evidence_coverage",
            passed=evidence_coverage >= case.expected.evidence_coverage,
            expected=case.expected.evidence_coverage,
            actual=evidence_coverage,
            message="每个输出行都必须具有来源证据",
        ),
        VerificationCheck(
            code="openable_output",
            passed=isinstance(result, Mapping),
            expected=True,
            actual=isinstance(result, Mapping),
            message="候选输出必须能够重新解析",
        ),
    )
    all_passed = all(check.passed for check in checks)
    failed_codes = [check.code for check in checks if not check.passed]
    fingerprint = None
    if not all_passed:
        fingerprint = hashlib.sha256(
            json.dumps(failed_codes, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
    return VerificationReport(
        report_id=f"verify_{case.case_id}",
        logical_plan_id=case.plan_id,
        logical_plan_revision=1,
        logical_plan_hash=logical_plan_hash,
        status=VerificationStatus.PASS if all_passed else VerificationStatus.FAIL,
        checks=checks,
        failure_fingerprint=fingerprint,
    )
