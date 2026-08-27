# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import importlib.util
import subprocess
import sys

import pytest

from src.agentic_runtime.models import (
    CandidateArtifact,
    PermissionProfile,
    PiRuntimeRequest,
    PiRuntimeResult,
    RuntimeStatus,
    SourceInput,
    VerificationCheck,
    VerificationReport,
    VerificationStatus,
)
from tests.database_migration_helpers import migrated_webui_database


@pytest.mark.parametrize(
    "root_name",
    ["generalization-g1-independent-v2", "generalization-g1-independent-v3"],
)
def test_independent_binary_sources_disable_git_text_conversion(
    root_name: str,
) -> None:
    project_root = Path(__file__).resolve().parents[1]
    sources = project_root / "evals" / root_name / "sources"
    paths = sorted(
        path
        for suffix in ("*.pdf", "*.docx", "*.xlsx")
        for path in sources.glob(suffix)
    )
    assert paths

    completed = subprocess.run(
        ["git", "check-attr", "text", "--", *map(str, paths)],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    assert completed.stdout.count("text: unset") == len(paths)


def test_independent_g1_dry_run_verifies_frozen_blind_set() -> None:
    project_root = Path(__file__).resolve().parents[1]
    runner = project_root / "evals/generalization-g1/run_independent_g1.py"

    completed = subprocess.run(
        [sys.executable, str(runner), "--dry-run"],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    assert "独立盲集资格：PASS" in completed.stdout
    assert "36 题（31 功能 + 5 安全）" in completed.stdout
    fixtures = json.loads(
        (project_root / "evals/generalization-g1/fixtures.json").read_text(
            encoding="utf-8"
        )
    )
    expected_prefix = fixtures["frozen_inputs"]["code_freeze_sha256"][:8]
    assert f"code-freeze：{expected_prefix}" in completed.stdout


def _load_runner():
    project_root = Path(__file__).resolve().parents[1]
    runner_path = project_root / "evals/generalization-g1/run_independent_g1.py"
    spec = importlib.util.spec_from_file_location("g1_independent_runner", runner_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_run_g1():
    project_root = Path(__file__).resolve().parents[1]
    driver_path = project_root / "evals/generalization-g1/run_g1.py"
    spec = importlib.util.spec_from_file_location("g1_frozen_driver", driver_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(driver_path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(driver_path.parent))
    return module


def test_functional_result_only_adapts_persisted_formal_output(tmp_path: Path) -> None:
    runner = _load_runner()
    output = tmp_path / "answer.csv"
    output.write_text("team,total\nA,12\n", encoding="utf-8")

    class Repository:
        def get_output(self, owner_id: str, output_id: str) -> dict:
            assert (owner_id, output_id) == ("owner-a", "output-1")
            return {
                "status": "delivery_published",
                "qa": {"openable": True},
                "file_path": str(output),
                "sha256": runner._sha256(output),
                "size_bytes": output.stat().st_size,
            }

    adapted = runner.adapt_run_result(
        {"id": "IH-CSV-01", "owner_id": "owner-a", "safety_tags": []},
        {
            "attempts": [
                {
                    "formal_delivery_passed": True,
                    "formal_delivery_id": "delivery-1",
                    "formal_output_ids": ["output-1"],
                    "formal_delivery_qa_passed": True,
                    "source_snapshot_refs": ["source-a:" + "1" * 64],
                    "candidate_artifact": {"sha256": "2" * 64},
                    "verification_report_hash": "3" * 64,
                }
            ]
        },
        Repository(),
    )

    assert adapted == {
        "id": "IH-CSV-01",
        "outcome": "formal_delivery",
        "failure_stage": None,
        "failure_code": None,
        "formal_delivery": {
            "status": "delivery_published",
            "qa_passed": True,
            "owner_id": "owner-a",
            "path": str(output),
            "sha256": runner._sha256(output),
            "size_bytes": output.stat().st_size,
            "delivery_id": "delivery-1",
            "output_id": "output-1",
            "source_snapshot_refs": [
                {"source_id": "source-a", "sha256": "1" * 64}
            ],
            "candidate_sha256": "2" * 64,
            "verification_report_hash": "3" * 64,
        },
    }


def test_safety_result_requires_exact_rejection_and_no_delivery() -> None:
    runner = _load_runner()

    class Repository:
        def latest_delivery(self, owner_id: str, run_id: str):
            assert owner_id in {"owner-a", "owner-b"}
            assert run_id == "run-safety"
            return None

    adapted = runner.adapt_run_result(
        {
            "id": "IH-SAFE-33",
            "owner_id": "owner-a",
            "qualification_owner_id": "owner-b",
            "safety_tags": ["cross_owner"],
            "expected_failure_stage": "formal_delivery",
            "expected_failure_code": "formal_delivery_missing",
        },
        {
            "attempts": [
                {
                    "safety_passed": True,
                    "run_id": "run-safety",
                    "failure_stage": "formal_delivery",
                    "failure_code": "formal_delivery_missing",
                }
            ]
        },
        Repository(),
    )

    assert adapted == {
        "id": "IH-SAFE-33",
        "outcome": "rejected",
        "failure_stage": "formal_delivery",
        "failure_code": "formal_delivery_missing",
        "formal_delivery": None,
    }


def test_run_case_keeps_real_publisher_after_injected_model_boundary(
    tmp_path: Path,
) -> None:
    driver = _load_run_g1()
    source = tmp_path / "source.csv"
    source.write_text("team,value\nA,12\n", encoding="utf-8")
    candidate = tmp_path / "candidate.csv"
    candidate.write_text("team,total\nA,12\n", encoding="utf-8")
    request = PiRuntimeRequest(
        user_id="owner-a",
        task_id="heldout-task",
        revision=1,
        objective_text="按团队汇总",
        requested_output_formats=("csv",),
        sources=(
            SourceInput(
                upload_id="source-a",
                original_name=source.name,
                host_path=source,
                sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                media_type="text/csv",
            ),
        ),
        permission_profile=PermissionProfile.STANDARD,
        model="test-model",
        base_url="http://127.0.0.1:1/v1",
        api_key="test-only",
    )
    outcome = PiRuntimeResult(
        status=RuntimeStatus.CANDIDATE_READY,
        run_id="run-heldout",
        workspace_root=tmp_path,
        candidates=(
            CandidateArtifact(
                artifact_id="candidate-csv",
                filename=candidate.name,
                format="csv",
                host_path=candidate,
                sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
                size_bytes=candidate.stat().st_size,
                openable=True,
                qa_checks=("reopened",),
            ),
        ),
        verification=VerificationReport(
            status=VerificationStatus.PASSED,
            summary="候选通过",
            checks=(VerificationCheck(code="content", passed=True, summary="通过"),),
            evidence_count=1,
            formal_delivery_eligible=True,
        ),
    )
    asserted: list[Path] = []

    async def runtime_runner(*args, **kwargs):
        return outcome

    driver.EVALS_ROOT = tmp_path
    driver.RUNS_DIR = tmp_path / "runs"
    driver.FORMAL_DELIVERY_DB = driver.RUNS_DIR / "delivery.db"
    driver.FORMAL_DELIVERY_ROOT = driver.RUNS_DIR / "deliveries"
    migrated_webui_database(driver.FORMAL_DELIVERY_DB)
    result = asyncio.run(
        driver.run_case(
            {
                "id": "IH-CSV-01",
                "objective": "按团队汇总",
                "output_format": "csv",
                "owner_id": "owner-a",
                "safety_tags": [],
                "expected_outcome": "formal_delivery",
            },
            1,
            1,
            {"kind": "local", "model": "test-model", "base_url": "local"},
            request_factory=lambda *args: request,
            runtime_runner=runtime_runner,
            candidate_assertion=lambda _case, path: asserted.append(path),
        )
    )

    assert result["passed"] is True
    assert result["attempts"][0]["run_id"] == "run-heldout"
    assert result["attempts"][0]["formal_delivery_passed"] is True
    assert len(result["attempts"][0]["formal_output_ids"]) == 1
    assert asserted


def test_independent_request_uses_frozen_local_sources() -> None:
    runner = _load_runner()
    manifest = runner._load_json(runner.HELDOUT_MANIFEST)
    case = manifest["cases"][0]

    request = runner.make_request(
        case,
        1,
        {
            "kind": "local",
            "model": "test-model",
            "base_url": "http://127.0.0.1:1/v1",
        },
    )

    assert request.user_id == case["owner_id"]
    assert request.objective_text == case["objective"]
    assert request.requested_output_formats == (case["output_format"],)
    assert request.table_output_contracts[0].exact_columns == tuple(
        case["goal_contract"]["delivery_spec"]["exact_columns"]
    )
    assert len(request.sources) == len(case["source_bindings"])
    for source, binding in zip(request.sources, case["source_bindings"], strict=True):
        assert source.host_path.is_file()
        assert source.sha256 == binding["sha256"]
        assert runner._sha256(source.host_path) == binding["sha256"]


def test_independent_json_request_rejects_undeclared_representation() -> None:
    runner = _load_runner()
    manifest = runner._load_json(runner.HELDOUT_MANIFEST)
    case = next(
        item
        for item in manifest["cases"]
        if item.get("expected_outcome") == "formal_delivery"
        and item["output_format"] == "json"
    )
    case = json.loads(json.dumps(case, ensure_ascii=False))
    case["goal_contract"]["delivery_spec"].pop("json_shape")

    with pytest.raises(ValueError, match="JSON 表格输出必须冻结表示形态"):
        runner.make_request(
            case,
            1,
            {
                "kind": "local",
                "model": "test-model",
                "base_url": "http://127.0.0.1:1/v1",
            },
        )


def test_functional_batch_runs_formal_case_then_adapts_output(tmp_path: Path) -> None:
    runner = _load_runner()
    manifest = runner._load_json(runner.HELDOUT_MANIFEST)
    case = next(item for item in manifest["cases"] if not item["safety_tags"])
    output = tmp_path / f"answer.{case['output_format']}"
    output.write_text("placeholder", encoding="utf-8")

    class Repository:
        def get_output(self, owner_id: str, output_id: str) -> dict:
            return {
                "file_path": str(output),
                "sha256": runner._sha256(output),
                "size_bytes": output.stat().st_size,
            }

    async def case_runner(received_case, *args, **kwargs):
        assert received_case is case
        assert kwargs["request_factory"] is runner.make_request
        return {
            "attempts": [
                    {
                        "formal_delivery_passed": True,
                        "formal_delivery_id": "delivery-1",
                        "formal_output_ids": ["output-1"],
                        "formal_delivery_qa_passed": True,
                        "source_snapshot_refs": [
                            f"{binding['source_id']}:{binding['sha256']}"
                            for binding in case["source_bindings"]
                        ],
                        "candidate_artifact": {"sha256": "2" * 64},
                        "verification_report_hash": "3" * 64,
                    }
            ]
        }

    results = asyncio.run(
        runner.run_functional_cases(
            [case],
            {"kind": "local", "model": "test-model", "base_url": "local"},
            retries=1,
            timeout_seconds=1800,
            repository=Repository(),
            case_runner=case_runner,
        )
    )

    assert len(results) == 1
    assert results[0]["id"] == case["id"]
    assert results[0]["outcome"] == "formal_delivery"


def test_cross_owner_probe_keeps_owner_delivery_hidden_from_attacker(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    manifest = runner._load_json(runner.HELDOUT_MANIFEST)
    case = next(
        item for item in manifest["cases"] if item["safety_tags"] == ["cross_owner"]
    )
    repository = runner.DeliveryPublishingRepository(
        migrated_webui_database(tmp_path / "delivery.db")
    )

    result = runner.run_safety_probe(
        case,
        repository=repository,
        output_root=tmp_path / "deliveries",
    )

    attempt = result["attempts"][0]
    assert result["passed"] is True
    assert attempt["failure_stage"] == "formal_delivery"
    assert attempt["failure_code"] == "formal_delivery_missing"
    assert repository.latest_delivery(case["owner_id"], attempt["run_id"]) is not None
    assert (
        repository.latest_delivery(case["qualification_owner_id"], attempt["run_id"])
        is None
    )
    adapted = runner.adapt_run_result(case, result, repository)
    assert adapted["formal_delivery"] is None
    assert "formal_output_ids" not in adapted


@pytest.mark.parametrize(
    "safety_tag",
    [
        "permission_denied",
        "user_isolation",
        "forbidden_content",
        "failure_not_success",
    ],
)
def test_remaining_safety_probes_reject_without_attacker_delivery(
    tmp_path: Path,
    safety_tag: str,
) -> None:
    runner = _load_runner()
    manifest = runner._load_json(runner.HELDOUT_MANIFEST)
    case = next(
        item for item in manifest["cases"] if item["safety_tags"] == [safety_tag]
    )
    repository = runner.DeliveryPublishingRepository(
        migrated_webui_database(tmp_path / f"{safety_tag}.db")
    )

    result = runner.run_safety_probe(
        case,
        repository=repository,
        output_root=tmp_path / safety_tag / "deliveries",
    )
    adapted = runner.adapt_run_result(case, result, repository)

    assert result["passed"] is True
    assert adapted["outcome"] == "rejected"
    assert adapted["failure_stage"] == case["expected_failure_stage"]
    assert adapted["failure_code"] == case["expected_failure_code"]
    assert adapted["formal_delivery"] is None
