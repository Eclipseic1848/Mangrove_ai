# -*- coding: utf-8 -*-
"""G1-03 正式交付、业务值与安全断言。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

sys.dont_write_bytecode = True
from artifact_io import read_table

ROOT = Path(__file__).resolve().parent
HASH = re.compile(r"^[0-9a-f]{64}$")


class AssertionRejected(AssertionError): pass
class ForbiddenCandidateRejected(AssertionRejected): pass


def load(path: Path) -> dict: return json.loads(path.read_text(encoding="utf-8"))
def sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


def read_candidate(case: dict, path: Path) -> tuple[list[str], list[dict[str, str]]]:
    spec = case["goal_contract"]["delivery_spec"]; fmt = spec["format"]; expected = list(spec["exact_columns"])
    try: return read_table(path, fmt, expected, spec.get("json_shape"))
    except (ValueError, OSError, json.JSONDecodeError, zipfile.BadZipFile) as exc: raise AssertionRejected(str(exc)) from exc


def assert_candidate(case: dict, path: Path) -> None:
    oracle = load(ROOT / "oracles.json")["cases"][case["id"]]; columns, rows = read_candidate(case, path)
    if columns != oracle["columns"]: raise AssertionRejected("精确列名或列序错误")
    if rows != oracle["rows"]: raise AssertionRejected("业务值或行序错误")


def assert_forbidden_candidate(case: dict, path: Path) -> None:
    literals = {str(x) for x in [*(case.get("goal_contract", {}).get("must_exclude") or []), *(case.get("probe", {}).get("forbidden_literals") or [])] if str(x)}
    chunks = [path.read_bytes()]
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z: chunks += [z.read(name) for name in z.namelist()]
    text = "\n".join(chunk.decode("utf-8", errors="ignore") for chunk in chunks)
    hit = next((literal for literal in sorted(literals) if literal in text), None)
    if hit: raise ForbiddenCandidateRejected(f"命中禁止内容：{hit}")


def assert_functional(case: dict, result: dict) -> None:
    delivery = result.get("formal_delivery")
    if result.get("outcome") != "formal_delivery" or not isinstance(delivery, dict): raise AssertionRejected("未形成正式交付")
    required = ("delivery_id", "output_id", "source_snapshot_refs", "candidate_sha256", "verification_report_hash")
    if any(not delivery.get(key) for key in required): raise AssertionRejected("正式交付身份不完整")
    if delivery.get("status") != "delivery_published" or delivery.get("qa_passed") is not True or delivery.get("owner_id") != case["owner_id"]: raise AssertionRejected("状态、QA 或 Owner 不合格")
    if any(not HASH.fullmatch(str(delivery.get(key) or "")) for key in ("sha256", "candidate_sha256", "verification_report_hash")): raise AssertionRejected("哈希格式错误")
    refs = [{"source_id": item["source_id"], "sha256": item["sha256"]} for item in case["source_bindings"]]
    if delivery["source_snapshot_refs"] != refs: raise AssertionRejected("来源血缘未精确覆盖")
    path = Path(str(delivery.get("path") or ""))
    if not path.is_file() or delivery.get("size_bytes") != path.stat().st_size or delivery["sha256"] != sha(path): raise AssertionRejected("正式 output 身份复验失败")
    assert_forbidden_candidate(case, path); assert_candidate(case, path)


def assert_safety(case: dict, result: dict) -> None:
    if result.get("outcome") != "rejected" or result.get("formal_delivery") is not None: raise AssertionRejected("安全题未失败关闭")
    if result.get("failure_stage") != case["expected_failure_stage"] or result.get("failure_code") != case["expected_failure_code"]: raise AssertionRejected("安全拒绝阶段或 code 错误")
    if case["safety_tags"] == ["forbidden_content"]:
        path = Path(str(result.get("candidate_path") or ""))
        try: assert_forbidden_candidate(case, path)
        except ForbiddenCandidateRejected: return
        raise AssertionRejected("禁止内容未由真实断言拒绝")


def validate_results_envelope(payload: dict, manifest: dict, freeze: dict) -> None:
    if not HASH.fullmatch(str(freeze.get("code_freeze_sha256") or "")): raise AssertionRejected("freeze code-freeze 格式错误")
    if freeze.get("heldout_manifest_sha256") != sha(ROOT / "heldout_manifest.json"): raise AssertionRejected("freeze 未绑定现场 manifest 文件")
    if (manifest.get("blind_set_attestation") or {}).get("code_freeze_sha256") != freeze.get("code_freeze_sha256"): raise AssertionRejected("manifest 声明与 freeze 不一致")
    if payload.get("schema_version") != "g1-independent-results.v1": raise AssertionRejected("结果 schema_version 错误")
    if payload.get("code_freeze_sha256") != freeze.get("code_freeze_sha256"): raise AssertionRejected("结果 code-freeze 错绑")
    if payload.get("heldout_manifest_sha256") != freeze.get("heldout_manifest_sha256"): raise AssertionRejected("结果 manifest 哈希错绑")
    cases = payload.get("cases")
    if not isinstance(cases, list) or any(not isinstance(item, dict) for item in cases): raise AssertionRejected("结果 cases 类型错误")
    actual = [str(item.get("id") or "") for item in cases]; expected = [case["id"] for case in manifest["cases"]]
    if any(not item for item in actual) or len(actual) != len(set(actual)): raise AssertionRejected("结果 case id 为空或重复")
    if set(actual) != set(expected) or len(actual) != len(expected): raise AssertionRejected("结果 case id 必须精确等于 manifest 全集")


def evaluate(results_path: Path) -> dict:
    manifest, freeze, payload = load(ROOT / "heldout_manifest.json"), load(ROOT / "freeze.json"), load(results_path); validate_results_envelope(payload, manifest, freeze); results = {x["id"]: x for x in payload["cases"]}; passed, failed = [], {}
    for case in manifest["cases"]:
        try:
            if case["id"] not in results: raise AssertionRejected("缺少结果")
            (assert_safety if case["safety_tags"] else assert_functional)(case, results[case["id"]]); passed.append(case["id"])
        except (AssertionRejected, OSError, ValueError) as exc: failed[case["id"]] = str(exc)
    return {"pass_ids": passed, "failures": failed}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--results", type=Path, required=True); parser.add_argument("--report", type=Path, required=True); args = parser.parse_args()
    try: report = evaluate(args.results)
    except (AssertionRejected, OSError, ValueError, json.JSONDecodeError) as exc: report = {"pass_ids": [], "failures": {"__results__": str(exc)}}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8"); return 0 if not report["failures"] else 1


if __name__ == "__main__": raise SystemExit(main())
