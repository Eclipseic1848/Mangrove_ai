# -*- coding: utf-8 -*-
"""G1-02 v2 盲集离线资格、反例与防篡改自检。"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import assertions
from build_independent_set import IMMUTABLE_NAMES, build_payloads, canonical_sha256, sha256_file
from definitions import functional_definitions, safety_definitions
from oracle_engine import derive_rows
from source_io import read_source, write_output_xlsx
from src.evaluation.g1_manifest import qualification_gaps


HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _pretty_hash(value: object) -> str:
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _write_candidate(path: Path, case: dict, columns: list[str], rows: list[dict[str, str]], *, wrong_shape: bool = False) -> None:
    fmt = case["output_format"]
    if fmt == "csv":
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        return
    if fmt == "xlsx":
        write_output_xlsx(path, columns, rows)
        return
    shape = case["goal_contract"]["delivery_spec"]["json_shape"]
    if wrong_shape:
        shape = "columns_rows" if shape == "records" else "records"
    if shape == "records":
        payload = [{column: row[column] for column in columns} for row in rows]
    else:
        payload = {"columns": columns, "rows": [[row[column] for column in columns] for row in rows]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _expect_rejected(callback, label: str) -> None:
    try:
        callback()
    except assertions.AssertionRejected:
        return
    raise AssertionError(f"反例未被拒绝：{label}")


def _verify_freeze(manifest: dict, freeze: dict, expected_code_freeze: str) -> None:
    rebuilt_manifest, rebuilt_oracles, rebuilt_derivation, rebuilt_catalog = build_payloads(expected_code_freeze, write_sources=False)
    if manifest != rebuilt_manifest:
        raise AssertionError("manifest 无法由不可变定义按 expected code-freeze 等价重建")
    expected_json = {
        "oracles.json": rebuilt_oracles,
        "derivation_proof.json": rebuilt_derivation,
        "source_catalog.json": rebuilt_catalog,
    }
    for name, expected in expected_json.items():
        if _load(ROOT / name) != expected:
            raise AssertionError(f"{name} 无法由来源与定义等价重建")
    if freeze.get("code_freeze_sha256") != expected_code_freeze:
        raise AssertionError("freeze 未绑定 expected code-freeze")
    file_hashes = {name: sha256_file(ROOT / name) for name in IMMUTABLE_NAMES}
    source_hashes = {
        path.relative_to(ROOT).as_posix(): sha256_file(path)
        for path in sorted((ROOT / "sources").iterdir()) if path.is_file()
    }
    all_hashes = {**file_hashes, **source_hashes}
    if freeze.get("files") != all_hashes:
        raise AssertionError("冻结文件逐项哈希不一致")
    if freeze.get("heldout_manifest_sha256") != file_hashes["heldout_manifest.json"]:
        raise AssertionError("manifest 冻结哈希不一致")
    if freeze.get("source_bundle_sha256") != canonical_sha256(source_hashes):
        raise AssertionError("来源 bundle 哈希不一致")
    if freeze.get("evaluation_bundle_sha256") != canonical_sha256(all_hashes):
        raise AssertionError("评测 bundle 哈希不一致")


def run(expected_code_freeze: str) -> dict:
    manifest = _load(ROOT / "heldout_manifest.json")
    freeze = _load(ROOT / "freeze.json")
    _verify_freeze(manifest, freeze, expected_code_freeze)
    gaps = list(qualification_gaps(manifest, expected_code_freeze_sha256=expected_code_freeze))
    functional_cases = [case for case in manifest["cases"] if not case["safety_tags"]]
    safety_cases = [case for case in manifest["cases"] if case["safety_tags"]]
    if len(functional_cases) != 31 or len(safety_cases) != 5:
        gaps.append("题量不是 31 功能 + 5 安全")
    categories = Counter(case["category"] for case in manifest["cases"])
    formats = Counter(case["output_format"] for case in manifest["cases"])
    json_shapes = Counter(
        case["goal_contract"]["delivery_spec"].get("json_shape")
        for case in functional_cases if case["output_format"] == "json"
    )
    if formats != Counter({"csv": 12, "json": 12, "xlsx": 12}):
        gaps.append("输出格式未达到 12/12/12")
    if json_shapes != Counter({"records": 5, "columns_rows": 5}):
        gaps.append("功能 JSON 形态未达到 5/5")
    for case in functional_cases:
        spec = case["goal_contract"].get("delivery_spec") or {}
        if spec.get("format") != case["output_format"] or not spec.get("exact_columns"):
            gaps.append(f"{case['id']}: delivery_spec 格式或精确列缺失")
        if case["output_format"] == "json" and spec.get("json_shape") not in {"records", "columns_rows"}:
            gaps.append(f"{case['id']}: JSON 形态缺失")
    definitions = {case["id"]: case for case in functional_definitions()}
    oracles = _load(ROOT / "oracles.json")["cases"]
    source_reopen_count = 0
    for case_id, definition in definitions.items():
        for source in definition["sources"]:
            reopened = read_source(ROOT / "sources" / source["filename"], source["format"])
            if reopened != source["tables"]:
                raise AssertionError(f"{case_id}: 来源重开逐值不一致")
            source_reopen_count += 1
        if derive_rows(definition, ROOT) != oracles[case_id]["rows"]:
            raise AssertionError(f"{case_id}: oracle 不能从重开来源逐值重算")
    for definition in safety_definitions():
        source = definition["source"]
        reopened = read_source(ROOT / "sources" / source["filename"], source["format"])
        if reopened != source["tables"]:
            raise AssertionError(f"{definition['id']}: 安全来源重开逐值不一致")
        source_reopen_count += 1
    with tempfile.TemporaryDirectory(prefix="g102-v2-selfcheck-") as temp:
        temp_root = Path(temp)
        for case in functional_cases:
            oracle = oracles[case["id"]]
            suffix = f".{case['output_format']}"
            valid = temp_root / f"{case['id']}-valid{suffix}"
            _write_candidate(valid, case, list(oracle["columns"]), deepcopy(oracle["rows"]))
            assertions.assert_candidate(case, valid)
            wrong_value_rows = deepcopy(oracle["rows"])
            wrong_value_rows[0][oracle["columns"][-1]] = "999999.99"
            wrong_value = temp_root / f"{case['id']}-wrong-value{suffix}"
            _write_candidate(wrong_value, case, list(oracle["columns"]), wrong_value_rows)
            _expect_rejected(lambda c=case, p=wrong_value: assertions.assert_candidate(c, p), f"{case['id']} 错误值")
            wrong_columns = list(oracle["columns"])
            wrong_columns[0], wrong_columns[1] = wrong_columns[1], wrong_columns[0]
            wrong_column_path = temp_root / f"{case['id']}-wrong-columns{suffix}"
            _write_candidate(wrong_column_path, case, wrong_columns, deepcopy(oracle["rows"]))
            _expect_rejected(lambda c=case, p=wrong_column_path: assertions.assert_candidate(c, p), f"{case['id']} 错列序")
            if case["output_format"] == "json":
                wrong_shape = temp_root / f"{case['id']}-wrong-shape.json"
                _write_candidate(wrong_shape, case, list(oracle["columns"]), deepcopy(oracle["rows"]), wrong_shape=True)
                _expect_rejected(lambda c=case, p=wrong_shape: assertions.assert_candidate(c, p), f"{case['id']} 错 JSON 形态")
        forbidden_case = next(case for case in safety_cases if case["safety_tags"] == ["forbidden_content"])
        forbidden_path = temp_root / "forbidden.json"
        forbidden_path.write_text(json.dumps({"probe": forbidden_case["probe"]["forbidden_literals"][0]}, ensure_ascii=False), encoding="utf-8")
        _expect_rejected(lambda: assertions.assert_forbidden_candidate(forbidden_case, forbidden_path), "禁止内容")
        lineage_case = functional_cases[0]
        lineage_oracle = oracles[lineage_case["id"]]
        lineage_path = temp_root / f"{lineage_case['id']}-lineage.{lineage_case['output_format']}"
        _write_candidate(lineage_path, lineage_case, list(lineage_oracle["columns"]), deepcopy(lineage_oracle["rows"]))
        lineage_result = {
            "id": lineage_case["id"],
            "outcome": "formal_delivery",
            "formal_delivery": {
                "status": "delivery_published",
                "qa_passed": True,
                "owner_id": lineage_case["owner_id"],
                "path": str(lineage_path),
                "sha256": sha256_file(lineage_path),
                "size_bytes": lineage_path.stat().st_size,
                "delivery_id": "delivery-v2-selfcheck",
                "output_id": "output-v2-selfcheck",
                "source_snapshot_refs": [
                    {"source_id": item["source_id"], "sha256": item["sha256"]}
                    for item in lineage_case["source_bindings"]
                ],
                "candidate_sha256": sha256_file(lineage_path),
                "verification_report_hash": "a" * 64,
            },
        }
        assertions._assert_functional(lineage_case, lineage_result)
        for field, bad_value in (
            ("delivery_id", ""),
            ("owner_id", "wrong-owner"),
            ("source_snapshot_refs", []),
            ("verification_report_hash", "not-a-hash"),
        ):
            bad = deepcopy(lineage_result)
            bad["formal_delivery"][field] = bad_value
            _expect_rejected(lambda b=bad: assertions._assert_functional(lineage_case, b), f"正式交付 {field}")
    tampered_manifest = deepcopy(manifest)
    tampered_freeze = deepcopy(freeze)
    fake = "0" * 64 if expected_code_freeze != "0" * 64 else "1" * 64
    tampered_manifest["blind_set_attestation"]["code_freeze_sha256"] = fake
    tampered_freeze["code_freeze_sha256"] = fake
    tampered_hash = _pretty_hash(tampered_manifest)
    tampered_freeze["heldout_manifest_sha256"] = tampered_hash
    tampered_freeze["files"]["heldout_manifest.json"] = tampered_hash
    tampered_freeze["evaluation_bundle_sha256"] = canonical_sha256(tampered_freeze["files"])
    tamper_rejected = False
    try:
        _verify_freeze(tampered_manifest, tampered_freeze, expected_code_freeze)
    except AssertionError:
        tamper_rejected = True
    if not tamper_rejected:
        gaps.append("协调篡改 manifest+freeze 未被拒绝")
    safety_tags = Counter(case["safety_tags"][0] for case in safety_cases)
    expected_tags = {"permission_denied", "cross_owner", "user_isolation", "forbidden_content", "failure_not_success"}
    if set(safety_tags) != expected_tags or any(count != 1 for count in safety_tags.values()):
        gaps.append("安全矩阵不完整或重复")
    report = {
        "status": "PASS" if not gaps else "FAIL",
        "qualification_gaps": gaps,
        "case_count": len(manifest["cases"]),
        "functional_count": len(functional_cases),
        "safety_count": len(safety_cases),
        "category_distribution": dict(sorted(categories.items())),
        "format_distribution": dict(sorted(formats.items())),
        "json_shape_distribution": dict(sorted(json_shapes.items())),
        "transformation_trap_count": sum(bool(set(case["traps"]) & {"paraphrase", "colloquial", "ellipsis", "reordered"}) for case in manifest["cases"]),
        "ambiguity_trap_count": sum(bool(set(case["traps"]) & {"similar", "conflict"}) for case in manifest["cases"]),
        "source_reopen_count": source_reopen_count,
        "functional_counterexamples": {"wrong_value": 31, "wrong_column_order": 31, "wrong_json_shape": 10},
        "formal_delivery_counterexamples": 4,
        "coordinated_tamper_rejected": tamper_rejected,
        "manifest_sha256": sha256_file(ROOT / "heldout_manifest.json"),
        "source_bundle_sha256": freeze["source_bundle_sha256"],
        "evaluation_bundle_sha256": freeze["evaluation_bundle_sha256"],
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-code-freeze-sha256")
    args = parser.parse_args()
    expected = args.expected_code_freeze_sha256 or _load(ROOT / "freeze.json")["code_freeze_sha256"]
    if not HASH_RE.fullmatch(expected):
        raise SystemExit("expected code-freeze 格式无效")
    report = run(expected)
    (ROOT / "self-check-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
