# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.acceptance.report_phase4b_8b1 import ReportError, build_report


def _write_result(
    path: Path,
    *,
    run_id: str = "g5-report",
    checks: list[dict[str, object]],
) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": "phase4b-8b1-check/v1",
                "run_id": run_id,
                "checks": checks,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _check(check_id: str, status: str) -> dict[str, object]:
    return {
        "check_id": check_id,
        "status": status,
        "summary": f"{check_id} 结论",
        "evidence": [f"{check_id.lower()}.json"],
        "remediation": None if status == "passed" else "按说明处理",
    }


def test_report_passes_required_checks_and_keeps_8b2_separate(tmp_path: Path) -> None:
    first = _write_result(
        tmp_path / "core.json",
        checks=[_check("CORE-001", "passed"), _check("FLOW-001", "passed")],
    )
    second = _write_result(
        tmp_path / "server.json",
        checks=[_check("SERVER-GPU-001", "pending_8b2")],
    )

    summary = build_report(
        [first, second],
        output_markdown=tmp_path / "acceptance.md",
        output_html=tmp_path / "acceptance.html",
    )

    assert summary.status == "passed"
    assert summary.pending_8b2 == 1
    markdown = (tmp_path / "acceptance.md").read_text(encoding="utf-8")
    html = (tmp_path / "acceptance.html").read_text(encoding="utf-8")
    for value in ("CORE-001", "FLOW-001", "SERVER-GPU-001", "pending_8b2"):
        assert value in markdown
        assert value in html
    assert "G5 本机前置验收报告" in markdown
    assert "不代表完整 8B-1 或目标服务器 8B-2 通过" in markdown


@pytest.mark.parametrize("status", ["failed", "not_run"])
def test_report_fails_when_required_check_did_not_pass(
    tmp_path: Path,
    status: str,
) -> None:
    result = _write_result(
        tmp_path / "result.json",
        checks=[_check("FLOW-001", status)],
    )

    summary = build_report(
        [result],
        output_markdown=tmp_path / "acceptance.md",
        output_html=tmp_path / "acceptance.html",
    )

    assert summary.status == "failed"


def test_report_marks_listed_missing_result_as_failed(tmp_path: Path) -> None:
    summary = build_report(
        [tmp_path / "missing.json"],
        output_markdown=tmp_path / "acceptance.md",
        output_html=tmp_path / "acceptance.html",
    )

    assert summary.status == "failed"
    assert summary.checks[0]["status"] == "failed"
    assert summary.checks[0]["summary"] == "结构化结果文件缺失"


@pytest.mark.parametrize("problem", ["duplicate", "run_id"])
def test_report_rejects_ambiguous_input(tmp_path: Path, problem: str) -> None:
    first = _write_result(
        tmp_path / "first.json",
        checks=[_check("CORE-001", "passed")],
    )
    second = _write_result(
        tmp_path / "second.json",
        run_id="another-run" if problem == "run_id" else "g5-report",
        checks=[_check("CORE-001" if problem == "duplicate" else "FLOW-001", "passed")],
    )

    with pytest.raises(ReportError):
        build_report(
            [first, second],
            output_markdown=tmp_path / "acceptance.md",
            output_html=tmp_path / "acceptance.html",
        )


def test_report_rejects_absolute_or_parent_evidence_paths(tmp_path: Path) -> None:
    result = _write_result(
        tmp_path / "unsafe.json",
        checks=[{
            **_check("FLOW-001", "passed"),
            "evidence": ["../trace.zip"],
        }],
    )

    with pytest.raises(ReportError, match="evidence"):
        build_report(
            [result],
            output_markdown=tmp_path / "acceptance.md",
            output_html=tmp_path / "acceptance.html",
        )
