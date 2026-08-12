# -*- coding: utf-8 -*-
"""Phase 4B 批次 0：Golden、评测协议和赛马 Graph 门禁。"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from src.evaluation.semantic_harness.fixtures import load_batch0_manifest
from src.evaluation.semantic_harness.graph import run_table_benchmark
from src.evaluation.semantic_harness.scoring import score_table_result
from src.evaluation.semantic_harness.table_ops import run_table_operation_suite


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_ROOT = (
    PROJECT_ROOT / "tests" / "fixtures" / "semantic_harness" / "public" / "batch0"
)


def test_batch0_manifest_covers_core_formats_and_deidentified_gate() -> None:
    manifest = load_batch0_manifest(FIXTURE_ROOT / "manifest.json")
    gate = manifest.case("workload_filter")

    assert gate.expected.row_count == 11
    assert gate.expected.visible_columns == ("核销工作量天数", "工作量费用")
    assert gate.expected.table_count == 1
    assert gate.expected.evidence_coverage == 1.0
    assert gate.selection == {"姓名": "示例人员甲"}
    assert "谢超群" not in json.dumps(
        manifest.model_dump(mode="json"), ensure_ascii=False
    )
    assert {
        "csv",
        "tsv",
        "xlsx",
        "json",
        "jsonl",
        "parquet",
        "pdf",
        "docx",
        "pptx",
        "html",
        "markdown",
    }.issubset(set(manifest.core_formats))


def test_table_scorer_rejects_extra_column_and_missing_evidence() -> None:
    manifest = load_batch0_manifest(FIXTURE_ROOT / "manifest.json")
    case = manifest.case("workload_filter")
    expected_records = json.loads(
        (FIXTURE_ROOT / case.expected.records_path).read_text(encoding="utf-8")
    )
    passing = {
        "table_count": 1,
        "visible_columns": ["核销工作量天数", "工作量费用"],
        "records": expected_records,
        "_expected_records": expected_records,
        "evidence": [
            {
                "record_index": index,
                "source_ref": f"source.parquet#row={index + 1}",
                "selection": {"姓名": "示例人员甲"},
            }
            for index in range(len(expected_records))
        ],
    }

    report = score_table_result(case, passing, logical_plan_hash="0" * 64)
    assert report.authoritative_output_allowed is True

    passing["visible_columns"] = ["姓名", "核销工作量天数", "工作量费用"]
    passing["evidence"] = []
    report = score_table_result(case, passing, logical_plan_hash="0" * 64)

    assert report.authoritative_output_allowed is False
    failed_codes = {check.code for check in report.checks if not check.passed}
    assert "exact_visible_columns" in failed_codes
    assert "evidence_coverage" in failed_codes


def test_table_benchmark_graph_writes_refs_and_passes_pandas_baseline(
    tmp_path: Path,
) -> None:
    report = run_table_benchmark(
        manifest_path=FIXTURE_ROOT / "manifest.json",
        case_id="workload_filter",
        candidate_ids=("table.pandas",),
        output_dir=tmp_path,
    )

    assert report["status"] == "pass"
    assert report["winner"] == "table.pandas"
    candidate = report["candidates"][0]
    assert Path(candidate["tool_result_path"]).is_file()
    assert Path(candidate["verification_path"]).is_file()
    assert "records" not in candidate
    assert candidate["verification_status"] == "pass"


def test_table_operation_suite_covers_merge_aggregate_and_negative_cases() -> None:
    pytest.importorskip("duckdb")
    pytest.importorskip("polars")
    operation_root = FIXTURE_ROOT / "table_operations"
    for capability_id in ("table.duckdb", "table.polars", "table.pandas"):
        result = run_table_operation_suite(capability_id, operation_root)
        assert result["status"] == "pass"
        assert result["quality_score"] == 1.0
        assert all(result["checks"].values())
