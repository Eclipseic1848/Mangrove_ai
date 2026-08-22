# -*- coding: utf-8 -*-
"""G1 独立盲集执行入口。"""
from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

if str(PROJECT_ROOT := Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agentic_runtime.models import (
    CandidateArtifact,
    PermissionProfile,
    PiRuntimeRequest,
    PiRuntimeResult,
    RuntimeStatus,
    SourceInput,
    TableOutputContract,
    VerificationCheck,
    VerificationReport,
    VerificationStatus,
)
from src.delivery_publishing.repository import DeliveryPublishingRepository
from src.evaluation.formal_delivery import (
    publish_runtime_result_as_formal_delivery,
    qualify_formal_delivery,
)


EVAL_ROOT = Path(__file__).resolve().parent
INDEPENDENT_ROOT = PROJECT_ROOT / "evals" / "generalization-g1-independent-v3"
DIAGNOSTIC_MANIFEST = EVAL_ROOT / "fixtures.json"
HELDOUT_MANIFEST = INDEPENDENT_ROOT / "heldout_manifest.json"
FREEZE = INDEPENDENT_ROOT / "freeze.json"
SELF_CHECK = INDEPENDENT_ROOT / "self_check.py"
INDEPENDENT_ASSERTIONS = INDEPENDENT_ROOT / "assertions.py"
RUNS_DIR = EVAL_ROOT / "runs" / "independent-v3"
FORMAL_DELIVERY_DB = RUNS_DIR / "formal-delivery.db"
FORMAL_DELIVERY_ROOT = RUNS_DIR / "formal-deliveries"
POST_COMMIT_FREEZE_METADATA = {
    "evals/generalization-g1/fixtures.json",
    "evals/generalization-g1-independent-v3/README.md",
    "evals/generalization-g1-independent-v3/freeze.json",
    "evals/generalization-g1-independent-v3/heldout_manifest.json",
    "evals/generalization-g1-independent-v3/self-check-report.json",
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def adapt_run_result(case: dict, run_result: dict, repository: Any) -> dict:
    """把真实 Publisher 持久化事实转换为独立断言输入。"""

    if case.get("safety_tags"):
        attempts = [
            attempt
            for attempt in run_result.get("attempts", [])
            if attempt.get("safety_passed") is True
        ]
        if len(attempts) != 1:
            raise ValueError(f"{case['id']}: 必须且只能有一次安全拒绝通过")
        attempt = attempts[0]
        stage = str(attempt.get("failure_stage") or "")
        code = str(attempt.get("failure_code") or "")
        if (
            stage != case.get("expected_failure_stage")
            or code != case.get("expected_failure_code")
        ):
            raise ValueError(f"{case['id']}: 安全拒绝阶段或 code 与冻结目标不一致")
        run_id = str(attempt.get("run_id") or "")
        if not run_id:
            raise ValueError(f"{case['id']}: 安全拒绝缺少 run_id")
        qualification_owner = str(
            case.get("qualification_owner_id") or case["owner_id"]
        )
        if repository.latest_delivery(qualification_owner, run_id):
            raise ValueError(f"{case['id']}: 攻击者视图仍出现正式 Delivery")
        adapted = {
            "id": case["id"],
            "outcome": "rejected",
            "failure_stage": stage,
            "failure_code": code,
            "formal_delivery": None,
        }
        if run_result.get("candidate_path"):
            adapted["candidate_path"] = str(run_result["candidate_path"])
        return adapted

    attempts = [
        attempt
        for attempt in run_result.get("attempts", [])
        if attempt.get("formal_delivery_passed") is True
    ]
    if len(attempts) != 1:
        raise ValueError(f"{case['id']}: 必须且只能有一次正式交付通过")
    attempt = attempts[0]
    output_ids = list(attempt.get("formal_output_ids") or ())
    if len(output_ids) != 1 or attempt.get("formal_delivery_qa_passed") is not True:
        raise ValueError(f"{case['id']}: 正式 output 或独立 QA 不满足单文件交付契约")
    delivery_id = str(attempt.get("formal_delivery_id") or "")
    raw_source_refs = list(attempt.get("source_snapshot_refs") or ())
    source_refs: list[dict[str, str]] = []
    for reference in raw_source_refs:
        source_id, separator, source_sha256 = str(reference).rpartition(":")
        if not separator or not source_id or len(source_sha256) != 64:
            raise ValueError(f"{case['id']}: 来源快照身份无效")
        source_refs.append({"source_id": source_id, "sha256": source_sha256})
    candidate_sha256 = str(
        (attempt.get("candidate_artifact") or {}).get("sha256") or ""
    )
    verification_hash = str(attempt.get("verification_report_hash") or "")
    if (
        not delivery_id
        or not source_refs
        or len(candidate_sha256) != 64
        or len(verification_hash) != 64
    ):
        raise ValueError(f"{case['id']}: 正式交付血缘身份不完整")
    owner_id = str(case["owner_id"])
    persisted = repository.get_output(owner_id, output_ids[0])
    if not persisted:
        raise ValueError(f"{case['id']}: 正式 output_id 未持久化")
    path = Path(str(persisted["file_path"]))
    if (
        not path.is_file()
        or path.stat().st_size != persisted.get("size_bytes")
        or _sha256(path) != persisted.get("sha256")
    ):
        raise ValueError(f"{case['id']}: 正式 output 文件身份复验失败")
    return {
        "id": case["id"],
        "outcome": "formal_delivery",
        "failure_stage": None,
        "failure_code": None,
        "formal_delivery": {
            "status": "delivery_published",
            "qa_passed": True,
            "owner_id": owner_id,
            "path": str(path),
            "sha256": str(persisted["sha256"]),
            "size_bytes": int(persisted["size_bytes"]),
            "delivery_id": delivery_id,
            "output_id": output_ids[0],
            "source_snapshot_refs": source_refs,
            "candidate_sha256": candidate_sha256,
            "verification_report_hash": verification_hash,
        },
    }


def make_request(
    case: dict,
    run_number: int,
    model_route: dict[str, str],
) -> PiRuntimeRequest:
    sources: list[SourceInput] = []
    for binding in case["source_bindings"]:
        path = (INDEPENDENT_ROOT / str(binding["path"])).resolve()
        if not path.is_relative_to(INDEPENDENT_ROOT.resolve()):
            raise ValueError(f"{case['id']}: 来源路径越出独立盲集目录")
        if not path.is_file() or _sha256(path) != binding["sha256"]:
            raise ValueError(f"{case['id']}: 来源文件缺失或哈希不一致")
        sources.append(
            SourceInput(
                upload_id=str(binding["source_id"]),
                original_name=path.name,
                host_path=path,
                sha256=str(binding["sha256"]),
                media_type=str(binding["media_type"]),
            )
        )
    table_output_contracts: tuple[TableOutputContract, ...] = ()
    if case.get("expected_outcome") == "formal_delivery":
        delivery_spec = case.get("goal_contract", {}).get("delivery_spec")
        if not isinstance(delivery_spec, dict):
            raise ValueError(f"{case['id']}: 正式交付缺少冻结 delivery_spec")
        if delivery_spec.get("format") != case.get("output_format"):
            raise ValueError(f"{case['id']}: delivery_spec 格式与输出格式不一致")
        table_output_contracts = (TableOutputContract(
            format=str(delivery_spec["format"]),
            exact_columns=tuple(delivery_spec.get("exact_columns") or ()),
            json_shape=delivery_spec.get("json_shape"),
        ),)
    common = {
        "user_id": str(case["owner_id"]),
        "task_id": f"g1_independent_{case['id']}_{run_number}",
        "revision": 1,
        "objective_text": str(case["objective"]),
        "requested_output_formats": (str(case["output_format"]),),
        "table_output_contracts": table_output_contracts,
        "sources": tuple(sources),
        "permission_profile": PermissionProfile.STANDARD,
        "external_api_confirmed": bool(case.get("external_api_confirmed", False)),
    }
    if model_route["kind"] == "connection":
        return PiRuntimeRequest(
            **common,
            model_connection_id=model_route["connection_id"],
            model_connection_version=model_route["connection_version"],
            model_connection_model=model_route["model"],
        )
    return PiRuntimeRequest(
        **common,
        model=model_route["model"],
        base_url=model_route["base_url"],
        api_key="local-runtime",
    )


def _defer_independent_assertion(_case: dict, _candidate_path: Path) -> None:
    """业务值只在正式 Delivery 形成后由独立断言读取。"""


def _load_independent_assertions():
    path = INDEPENDENT_ROOT / "assertions.py"
    spec = importlib.util.spec_from_file_location("g1_independent_assertions", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载独立断言模块")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(INDEPENDENT_ROOT))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(INDEPENDENT_ROOT))
    return module


async def run_functional_cases(
    cases: list[dict],
    model_route: dict[str, str],
    *,
    retries: int,
    timeout_seconds: int,
    repository: Any,
    case_runner=None,
) -> list[dict]:
    if case_runner is None:
        if str(EVAL_ROOT) not in sys.path:
            sys.path.insert(0, str(EVAL_ROOT))
        from run_g1 import run_case as case_runner

    adapted: list[dict] = []
    for case in cases:
        raw = await case_runner(
            case,
            1,
            retries,
            model_route,
            timeout_seconds=timeout_seconds,
            request_factory=make_request,
            candidate_assertion=_defer_independent_assertion,
        )
        try:
            adapted.append(adapt_run_result(case, raw, repository))
        except ValueError as exc:
            adapted.append(
                {
                    "id": case["id"],
                    "outcome": "rejected",
                    "failure_stage": "execution",
                    "failure_code": "formal_delivery_missing",
                    "formal_delivery": None,
                    "adapter_error": str(exc),
                    "raw_result": raw,
                }
            )
    return adapted


def run_safety_probe(
    case: dict,
    *,
    repository: DeliveryPublishingRepository,
    output_root: Path,
) -> dict:
    """执行不向模型暴露 probe 的机械安全检查。"""

    tags = list(case.get("safety_tags") or ())
    if len(tags) != 1:
        raise ValueError(f"{case['id']}: 安全探针必须且只能有一个标签")
    tag = tags[0]
    request = make_request(
        case,
        1,
        {
            "kind": "local",
            "model": "g1-safety-probe",
            "base_url": "http://127.0.0.1:1/v1",
        },
    )
    candidate_dir = output_root.parent / "probe-candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    candidate = candidate_dir / f"{case['id']}.{case['output_format']}"
    rows = list((case.get("probe") or {}).get("candidate_rows") or ())
    if case["output_format"] == "csv":
        if not rows:
            source = request.sources[0].host_path
            shutil.copy2(source, candidate)
        else:
            columns = list(rows[0])
            with candidate.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows)
    elif case["output_format"] == "json":
        if not rows:
            rows = [{"probe": "owner-isolation"}]
        candidate.write_text(
            json.dumps(
                {"columns": list(rows[0]), "rows": rows},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    elif case["output_format"] == "xlsx":
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "probe"
        sheet.append(["probe"])
        sheet.append(["owner-isolation"])
        workbook.save(candidate)
    else:
        raise ValueError(f"{case['id']}: 安全探针不支持该输出格式")
    run_id = f"g1-independent-{case['id']}"
    verification_status = (
        VerificationStatus.FAILED
        if tag == "failure_not_success"
        else VerificationStatus.PASSED
    )
    outcome = PiRuntimeResult(
        status=RuntimeStatus.CANDIDATE_READY,
        run_id=run_id,
        workspace_root=candidate_dir,
        candidates=(
            CandidateArtifact(
                artifact_id=f"candidate-{case['id']}",
                filename=candidate.name,
                format=str(case["output_format"]),
                host_path=candidate,
                sha256=_sha256(candidate),
                size_bytes=candidate.stat().st_size,
                openable=True,
                qa_checks=("reopened",),
            ),
        ),
        verification=VerificationReport(
            status=verification_status,
            summary="隔离探针候选通过机械验证",
            checks=(
                VerificationCheck(code="probe", passed=True, summary="通过"),
            ),
            evidence_count=1,
            formal_delivery_eligible=verification_status is VerificationStatus.PASSED,
        ),
    )
    if tag == "forbidden_content":
        assertions = _load_independent_assertions()
        try:
            assertions.assert_forbidden_candidate(case, candidate)
        except assertions.ForbiddenCandidateRejected:
            return _safety_result(
                case,
                run_id,
                "assertion",
                "assertion_rejected",
                candidate_path=candidate,
            )
        raise ValueError(f"{case['id']}: 独立断言没有拒绝禁止内容候选")
    if tag == "failure_not_success":
        if outcome.verification is None or outcome.verification.status is not VerificationStatus.FAILED:
            raise ValueError(f"{case['id']}: 验证失败探针没有形成 failed 报告")
        try:
            publish_runtime_result_as_formal_delivery(
                repository=repository,
                output_root=output_root,
                request=request,
                result=outcome,
                output_name=f"G1 {case['id']} 验证失败探针",
                actor_id=str(case["owner_id"]),
                qualification_owner_id=str(case["owner_id"]),
            )
        except ValueError:
            if repository.latest_delivery(str(case["owner_id"]), run_id) is None:
                return _safety_result(
                    case,
                    run_id,
                    "verification",
                    "verification_failed",
                )
        raise ValueError(f"{case['id']}: 验证失败后仍形成正式交付")
    if tag == "permission_denied":
        try:
            publish_runtime_result_as_formal_delivery(
                repository=repository,
                output_root=output_root,
                request=request,
                result=outcome,
                output_name=f"G1 {case['id']} 权限探针",
                actor_id=str(case["publish_actor_id"]),
                qualification_owner_id=str(case["qualification_owner_id"]),
            )
        except PermissionError:
            return _safety_result(case, run_id, "formal_delivery", "permission_denied")
        raise ValueError(f"{case['id']}: 未授权发布者意外形成正式交付")
    if tag not in {"cross_owner", "user_isolation"}:
        raise ValueError(f"{case['id']}: 未知安全标签 {tag}")
    owner_qualification = publish_runtime_result_as_formal_delivery(
        repository=repository,
        output_root=output_root,
        request=request,
        result=outcome,
        output_name=f"G1 {case['id']} 隔离基线",
        actor_id=str(case["owner_id"]),
        qualification_owner_id=str(case["owner_id"]),
    )
    if not owner_qualification.passed:
        raise ValueError(f"{case['id']}: 合法 Owner 隔离基线发布失败")
    attacker = qualify_formal_delivery(
        repository=repository,
        owner_id=str(case["qualification_owner_id"]),
        run_id=run_id,
        expected_formats=(str(case["output_format"]),),
        output_root=output_root,
    )
    passed = attacker.reason_code == "formal_delivery_missing"
    if tag == "user_isolation":
        output_id = owner_qualification.output_ids[0]
        passed = passed and repository.get_output(
            str(case["qualification_owner_id"]), output_id
        ) is None
    return _safety_result(
        case,
        run_id,
        "formal_delivery",
        attacker.reason_code,
        passed=passed,
    )


def _safety_result(
    case: dict,
    run_id: str,
    stage: str,
    code: str,
    *,
    passed: bool = True,
    candidate_path: Path | None = None,
) -> dict:
    result = {
        "id": case["id"],
        "passed": passed,
        "attempts": [
            {
                "safety_passed": passed,
                "run_id": run_id,
                "failure_stage": stage,
                "failure_code": code,
            }
        ],
    }
    if candidate_path is not None:
        result["candidate_path"] = str(candidate_path)
    return result


def dry_run() -> None:
    diagnostic = _load_json(DIAGNOSTIC_MANIFEST)
    driver = _load_frozen_driver()
    driver.check_frozen(diagnostic)
    expected_code_freeze = str(
        diagnostic["frozen_inputs"]["code_freeze_sha256"]
    )
    freeze = _load_json(FREEZE)
    if freeze.get("code_freeze_sha256") != expected_code_freeze:
        raise SystemExit("当前代码冻结身份与独立盲集绑定不一致")
    if _sha256(HELDOUT_MANIFEST) != freeze.get("heldout_manifest_sha256"):
        raise SystemExit("独立盲集 Manifest 哈希不一致")
    readme = INDEPENDENT_ROOT / "README.md"
    if _sha256(readme) != freeze.get("files", {}).get("README.md"):
        raise SystemExit("独立盲集 README 哈希不一致")
    completed = subprocess.run(
        [
            sys.executable,
            str(SELF_CHECK),
            "--expected-code-freeze-sha256",
            expected_code_freeze,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise SystemExit(completed.stderr or completed.stdout)
    report = json.loads(completed.stdout)
    if (
        report.get("status") != "PASS"
        or report.get("qualification_gaps")
        or report.get("manifest_sha256") != freeze.get("heldout_manifest_sha256")
        or report.get("evaluation_bundle_sha256")
        != freeze.get("evaluation_bundle_sha256")
    ):
        raise SystemExit("独立盲集离线资格检查未通过")

    print("独立盲集资格：PASS")
    print(
        f"{report['case_count']} 题（{report['functional_count']} 功能 + "
        f"{report['safety_count']} 安全）"
    )
    print(f"code-freeze：{expected_code_freeze[:8]}")


def _load_frozen_driver():
    if str(EVAL_ROOT) not in sys.path:
        sys.path.insert(0, str(EVAL_ROOT))
    import run_g1

    return run_g1


def _configure_driver_paths(driver) -> None:
    driver.RUNS_DIR = RUNS_DIR
    driver.FORMAL_DELIVERY_DB = FORMAL_DELIVERY_DB
    driver.FORMAL_DELIVERY_ROOT = FORMAL_DELIVERY_ROOT


async def run_all(
    *,
    retries: int,
    timeout_seconds: int,
    connection_id: str = "",
    model_id: str = "",
) -> dict:
    dry_run()
    driver = _load_frozen_driver()
    dirty = driver._dirty_worktree_paths(allowed_paths=POST_COMMIT_FREEZE_METADATA)
    if dirty:
        raise SystemExit("G1 独立正式运行要求干净工作树")
    model_route = driver._resolve_model_route(connection_id, model_id)
    model_route_sha256 = driver.require_frozen_model_route(model_route)
    manifest = _load_json(HELDOUT_MANIFEST)
    if model_route["kind"] == "connection" and any(
        not case.get("external_api_confirmed") for case in manifest["cases"]
    ):
        raise SystemExit("外部模型路线缺少逐任务冻结的外发确认")

    _configure_driver_paths(driver)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    repository = DeliveryPublishingRepository(FORMAL_DELIVERY_DB)
    functional_cases = [case for case in manifest["cases"] if not case["safety_tags"]]
    safety_cases = [case for case in manifest["cases"] if case["safety_tags"]]
    results = await run_functional_cases(
        functional_cases,
        model_route,
        retries=retries,
        timeout_seconds=timeout_seconds,
        repository=repository,
        case_runner=driver.run_case,
    )
    for case in safety_cases:
        raw = run_safety_probe(
            case,
            repository=repository,
            output_root=FORMAL_DELIVERY_ROOT,
        )
        results.append(adapt_run_result(case, raw, repository))

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results_path = RUNS_DIR / f"g1-independent-{stamp}-results.json"
    assertion_report_path = RUNS_DIR / f"g1-independent-{stamp}-assertions.json"
    payload = {
        "schema_version": "g1-independent-results.v1",
        "code_freeze_sha256": _load_json(FREEZE)["code_freeze_sha256"],
        "heldout_manifest_sha256": _sha256(HELDOUT_MANIFEST),
        "runner_sha256": _sha256(Path(__file__)),
        "model_route": model_route,
        "model_route_sha256": model_route_sha256,
        "execution_budget": {
            "retries_per_case": retries,
            "timeout_seconds_per_attempt": timeout_seconds,
        },
        "cases": results,
    }
    results_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(INDEPENDENT_ASSERTIONS),
            "--results",
            str(results_path),
            "--report",
            str(assertion_report_path),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assertion_report = _load_json(assertion_report_path)
    passed_ids = set(assertion_report["pass_ids"])
    functional_passed = sum(case["id"] in passed_ids for case in functional_cases)
    safety_passed = sum(case["id"] in passed_ids for case in safety_cases)
    summary = {
        "functional": {
            "passed": functional_passed,
            "total": len(functional_cases),
            "rate": round(functional_passed / len(functional_cases) * 100, 1),
        },
        "safety": {
            "passed": safety_passed,
            "total": len(safety_cases),
            "rate": round(safety_passed / len(safety_cases) * 100, 1),
        },
    }
    summary["qualified"] = bool(
        summary["functional"]["rate"] >= 90
        and summary["safety"]["rate"] == 100
    )
    payload["assertion_exit_code"] = completed.returncode
    payload["assertion_report"] = str(assertion_report_path)
    payload["summary"] = summary
    results_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"正式结果：{results_path}")
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="G1 独立盲集执行入口",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--freeze-model-route", action="store_true")
    parser.add_argument("--connection-id", default="")
    parser.add_argument("--model-id", default="")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    args = parser.parse_args()
    if args.timeout_seconds <= 0 or args.retries <= 0:
        parser.error("超时和重试次数必须大于 0")
    driver = _load_frozen_driver()
    if args.freeze_model_route:
        driver.freeze_model_route(args.connection_id.strip(), args.model_id.strip())
        return 0
    if args.dry_run:
        dry_run()
        return 0
    summary = asyncio.run(
        run_all(
            retries=args.retries,
            timeout_seconds=args.timeout_seconds,
            connection_id=args.connection_id.strip(),
            model_id=args.model_id.strip(),
        )
    )
    return 0 if summary["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
