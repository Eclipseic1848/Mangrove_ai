# -*- coding: utf-8 -*-
"""批次 3 表格结果的独立后置验证和权威输出闸门。"""
from __future__ import annotations

import hashlib
import json
from typing import Any
import uuid

import pyarrow.parquet as pq

from .models import (
    PredicateOperator,
    SemanticTaskPlan,
    ToolStatus,
    VerificationCheck,
    VerificationReport,
    VerificationStatus,
)
from .table_executor import ExecutionBundle


def _matches(value: Any, check) -> bool:
    operator = check.operator
    if operator == PredicateOperator.IS_NULL:
        return value is None
    if operator == PredicateOperator.NOT_NULL:
        return value is not None
    if operator == PredicateOperator.IN:
        return value in check.values
    if operator == PredicateOperator.NOT_IN:
        return value not in check.values
    if operator == PredicateOperator.EQ:
        return value == check.value
    if operator == PredicateOperator.NE:
        return value != check.value
    if operator == PredicateOperator.GT:
        return value > check.value
    if operator == PredicateOperator.GTE:
        return value >= check.value
    if operator == PredicateOperator.LT:
        return value < check.value
    if operator == PredicateOperator.LTE:
        return value <= check.value
    text = "" if value is None else str(value)
    if operator == PredicateOperator.CONTAINS:
        return str(check.value) in text
    if operator == PredicateOperator.REGEX:
        import re

        return re.search(str(check.value), text) is not None
    return False


def verify_table_execution(
    plan: SemanticTaskPlan,
    bundle: ExecutionBundle,
) -> VerificationReport:
    checks: list[VerificationCheck] = []
    if bundle.tool_result.status != ToolStatus.SUCCEEDED:
        checks.append(
            VerificationCheck(
                code="tool_succeeded",
                passed=False,
                expected="succeeded",
                actual=bundle.tool_result.status.value,
                message=bundle.tool_result.error_message or "工具执行失败",
                repairable=False,
            )
        )
    else:
        assert bundle.output_table is not None
        visible = tuple(
            name
            for name in bundle.output_table.column_names
            if not name.startswith("__mg_")
        )
        expected_visible = plan.postconditions.exact_visible_columns
        checks.append(
            VerificationCheck(
                code="exact_visible_columns",
                passed=visible == expected_visible,
                expected=list(expected_visible),
                actual=list(visible),
                message="可见列及顺序必须与确认计划完全一致",
            )
        )
        actual_rows = bundle.output_table.num_rows
        expected_rows = plan.postconditions.expected_row_count
        checks.append(
            VerificationCheck(
                code="expected_row_count",
                passed=expected_rows is None or actual_rows == expected_rows,
                expected=expected_rows,
                actual=actual_rows,
                message="结果行数满足计划后置条件",
            )
        )
        non_empty = actual_rows > 0 or expected_rows == 0
        checks.append(
            VerificationCheck(
                code="non_empty_unless_expected",
                passed=non_empty,
                expected=">0 或计划明确期望 0",
                actual=actual_rows,
                message="未知期望下的零行结果不得冒充成功",
            )
        )
        checks.append(
            VerificationCheck(
                code="table_count",
                passed=plan.postconditions.table_count in (None, 1),
                expected=plan.postconditions.table_count,
                actual=1,
                message="确定性执行只产出一个结果表",
            )
        )
        schema_valid = False
        try:
            import pandera.pandas as pandera

            frame = bundle.output_table.select(list(visible)).to_pandas()
            schema = pandera.DataFrameSchema(
                {
                    name: pandera.Column(nullable=True, required=True)
                    for name in visible
                },
                strict=True,
                ordered=True,
            )
            schema.validate(frame, lazy=True)
            schema_valid = True
        except Exception:
            schema_valid = False
        checks.append(
            VerificationCheck(
                code="strict_ordered_schema",
                passed=schema_valid,
                expected=True,
                actual=schema_valid,
                message="Pandera 严格校验列集合和顺序",
            )
        )
        reconciliation = bundle.tool_result.facts.get("reconciliation", {})
        input_rows = sum(
            reconciliation.get("input_rows_by_source", {}).values()
        )
        grain_changed = bool(reconciliation.get("grain_changed"))
        ledger_valid = (
            grain_changed
            or input_rows
            == actual_rows + bundle.tool_result.ledger.filtered_out_records
        )
        checks.append(
            VerificationCheck(
                code="row_ledger_reconciliation",
                passed=ledger_valid,
                expected=input_rows,
                actual=(
                    actual_rows
                    + bundle.tool_result.ledger.filtered_out_records
                ),
                message=(
                    "粒度不变时输入行必须等于输出行加过滤/去重行；"
                    "粒度变化时由血缘和数值账本复核"
                ),
            )
        )

        evidence_by_source: dict[str, dict[str, Any]] = {}
        output_ids = set()
        for row in bundle.evidence_rows:
            output_ids.add(row["output_record_id"])
            evidence_by_source[row["source_row_id"]] = json.loads(
                row["evidence_json"]
            )
        coverage = (
            len(output_ids) / actual_rows if actual_rows else 1.0
        )
        minimum = plan.postconditions.minimum_evidence_coverage
        checks.append(
            VerificationCheck(
                code="evidence_coverage",
                passed=coverage >= minimum,
                expected=minimum,
                actual=coverage,
                message="每条输出都必须能回溯到来源行",
                evidence_refs=tuple(sorted(evidence_by_source)),
            )
        )
        for index, predicate in enumerate(plan.postconditions.predicates):
            values = [
                item.get(predicate.field)
                for item in evidence_by_source.values()
            ]
            ratio = (
                sum(_matches(value, predicate) for value in values)
                / len(values)
                if values
                else 0.0
            )
            checks.append(
                VerificationCheck(
                    code=f"predicate_{index}",
                    passed=ratio >= predicate.required_ratio,
                    expected=predicate.required_ratio,
                    actual=ratio,
                    message=f"来源证据必须满足谓词：{predicate.field}",
                    evidence_refs=tuple(sorted(evidence_by_source)),
                )
            )

        openable = False
        if bundle.result_path and bundle.lineage_path:
            try:
                result = pq.read_table(bundle.result_path)
                lineage = pq.read_table(bundle.lineage_path)
                openable = (
                    result.num_rows == actual_rows
                    and set(
                        [
                            "output_record_id",
                            "source_row_id",
                            "artifact_id",
                            "table_ref",
                            "row_number",
                            "evidence_json",
                        ]
                    ).issubset(lineage.column_names)
                )
            except Exception:
                openable = False
        checks.append(
            VerificationCheck(
                code="openable_output",
                passed=openable,
                expected=True,
                actual=openable,
                message="结果和血缘 Parquet 必须可重新打开",
            )
        )

    passed = all(item.passed for item in checks)
    fingerprint = None
    if not passed:
        raw = json.dumps(
            [
                item.model_dump(mode="json")
                for item in checks
                if not item.passed
            ],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        fingerprint = hashlib.sha256(raw).hexdigest()
    return VerificationReport(
        report_id=f"verify_{uuid.uuid4().hex[:16]}",
        logical_plan_id=plan.plan_id,
        logical_plan_revision=plan.revision,
        logical_plan_hash=plan.canonical_hash(),
        status=(
            VerificationStatus.PASS
            if passed
            else VerificationStatus.NEEDS_USER
        ),
        checks=tuple(checks),
        failure_fingerprint=fingerprint,
    )
