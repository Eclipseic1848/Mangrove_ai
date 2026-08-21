# -*- coding: utf-8 -*-
"""独立盲集的静态资格、来源哈希与期望值自检。"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import importlib.util
import json
import tempfile
from collections import Counter
from copy import deepcopy
from math import ceil
from pathlib import Path

from assertions import (
    AssertionRejected,
    ForbiddenCandidateRejected,
    _assert_functional,
    assert_forbidden_candidate,
    evaluate,
)
from build_independent_set import build_freeze, build_manifest, json_document_bytes
from definitions import FUNCTIONAL_CASES, SAFETY_CASES
from oracle_engine import derive
from source_io import read_source, write_xlsx_source


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[1]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _load_public_contract():
    contract_path = REPO_ROOT / "src" / "evaluation" / "g1_manifest.py"
    spec = importlib.util.spec_from_file_location("g1_public_manifest_contract", contract_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 G1 公共清单合约")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bundle_digest(paths: list[Path]) -> str:
    records = [
        {"path": path.relative_to(ROOT).as_posix(), "sha256": _sha256_file(path)}
        for path in sorted(paths, key=lambda item: item.relative_to(ROOT).as_posix())
    ]
    return _sha256_bytes(_canonical_bytes(records))


def _verify_rebuilt_metadata(manifest: dict, freeze: dict, expected_code_freeze_sha256: str) -> None:
    rebuilt_manifest = build_manifest(expected_code_freeze_sha256)
    if manifest != rebuilt_manifest:
        raise AssertionError("磁盘 manifest 与不可变生成器重建结果不等价")
    rebuilt_freeze = build_freeze(rebuilt_manifest, expected_code_freeze_sha256)
    if freeze != rebuilt_freeze:
        raise AssertionError("磁盘 freeze 与独立重算结果不等价")


def _assert_coordinated_tamper_rejected(manifest: dict, freeze: dict, expected_code_freeze_sha256: str) -> None:
    tampered_manifest = deepcopy(manifest)
    tampered_code_freeze = "0" * 64 if expected_code_freeze_sha256 != "0" * 64 else "1" * 64
    tampered_manifest["blind_set_attestation"]["code_freeze_sha256"] = tampered_code_freeze
    tampered_manifest_sha256 = _sha256_bytes(json_document_bytes(tampered_manifest))
    tampered_freeze = deepcopy(freeze)
    tampered_freeze["code_freeze_sha256"] = tampered_code_freeze
    tampered_freeze["heldout_manifest_sha256"] = tampered_manifest_sha256
    tampered_freeze["files"]["heldout_manifest.json"] = tampered_manifest_sha256
    tampered_records = [
        {"path": path, "sha256": digest}
        for path, digest in sorted(tampered_freeze["files"].items())
    ]
    tampered_freeze["evaluation_bundle_sha256"] = _sha256_bytes(_canonical_bytes(tampered_records))
    try:
        _verify_rebuilt_metadata(tampered_manifest, tampered_freeze, expected_code_freeze_sha256)
    except AssertionError:
        return
    raise AssertionError("协调篡改 manifest+freeze 未被不可变重建拒绝")


def _write_csv(path: Path, table: dict) -> None:
    import csv

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=table["columns"])
        writer.writeheader()
        writer.writerows(table["rows"])


def _lineage_metadata(case: dict) -> dict:
    return {
        "delivery_id": f"delivery-{case['id']}",
        "output_id": f"output-{case['id']}",
        "source_snapshot_refs": [
            {"source_id": binding["source_id"], "sha256": binding["sha256"]}
            for binding in case["source_bindings"]
        ],
        "candidate_sha256": _sha256_bytes(f"candidate:{case['id']}".encode("utf-8")),
        "verification_report_hash": _sha256_bytes(f"verification:{case['id']}".encode("utf-8")),
    }


def _assert_wrong_values_rejected(manifest: dict, oracles: dict) -> list[str]:
    checked: list[str] = []
    representatives = {
        "csv": next(case for case in manifest["cases"] if case["expected_outcome"] == "formal_delivery" and case["output_format"] == "csv"),
        "json": next(case for case in manifest["cases"] if case["expected_outcome"] == "formal_delivery" and case["output_format"] == "json"),
        "xlsx": next(case for case in manifest["cases"] if case["expected_outcome"] == "formal_delivery" and case["output_format"] == "xlsx"),
    }
    with tempfile.TemporaryDirectory(prefix="g1-independent-selfcheck-") as temporary:
        temporary_root = Path(temporary)
        for output_format, case in representatives.items():
            oracle = oracles["cases"][case["id"]]
            wrong = {"columns": list(oracle["columns"]), "rows": [dict(row) for row in oracle["rows"]]}
            first_column = wrong["columns"][0]
            wrong["rows"][0][first_column] = "__WRONG_BUSINESS_VALUE__"
            output_path = temporary_root / f"{case['id']}.{output_format}"
            if output_format == "csv":
                _write_csv(output_path, wrong)
            elif output_format == "json":
                output_path.write_text(json.dumps(wrong, ensure_ascii=False), encoding="utf-8")
            else:
                write_xlsx_source(output_path, {"Delivery": wrong})
            delivery = {
                "status": "delivery_published", "qa_passed": True, "owner_id": case["owner_id"],
                "path": str(output_path), "sha256": _sha256_file(output_path), "size_bytes": output_path.stat().st_size,
                **_lineage_metadata(case),
            }
            try:
                _assert_functional(case, {"outcome": "formal_delivery", "formal_delivery": delivery}, oracle, temporary_root)
            except AssertionRejected:
                checked.append(output_format)
            else:
                raise AssertionError(f"{output_format} 的错误业务值未被强断言拒绝")
    return checked


def _assert_complete_synthetic_result_passes(manifest: dict, oracles: dict) -> int:
    with tempfile.TemporaryDirectory(prefix="g1-independent-positive-") as temporary:
        temporary_root = Path(temporary)
        results: list[dict] = []
        for case in manifest["cases"]:
            oracle = oracles["cases"][case["id"]]
            if oracle["mode"] == "exact_rejection":
                rejected_result = {
                    "id": case["id"], "outcome": "rejected",
                    "failure_stage": oracle["failure_stage"], "failure_code": oracle["failure_code"],
                    "formal_delivery": None,
                }
                if case["safety_tags"] == ["forbidden_content"]:
                    candidate_path = temporary_root / f"{case['id']}-candidate.json"
                    candidate_path.write_text(
                        json.dumps({"rows": case["probe"]["candidate_rows"]}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    rejected_result["candidate_path"] = str(candidate_path)
                results.append(rejected_result)
                continue
            table = {"columns": oracle["columns"], "rows": oracle["rows"]}
            output_path = temporary_root / f"{case['id']}.{case['output_format']}"
            if case["output_format"] == "csv":
                _write_csv(output_path, table)
            elif case["output_format"] == "json":
                output_path.write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
            else:
                write_xlsx_source(output_path, {"Delivery": table})
            results.append({
                "id": case["id"], "outcome": "formal_delivery",
                "formal_delivery": {
                    "status": "delivery_published", "qa_passed": True, "owner_id": case["owner_id"],
                    "path": str(output_path), "sha256": _sha256_file(output_path), "size_bytes": output_path.stat().st_size,
                    **_lineage_metadata(case),
                },
            })
        report = evaluate(manifest, oracles, {"cases": results}, temporary_root)
        if not report["passed"] or report["passed_count"] != len(manifest["cases"]):
            raise AssertionError(f"独立断言完整正例自检失败：{report!r}")
        return report["passed_count"]


def _assert_forbidden_probe_is_mechanical(manifest: dict) -> str:
    case = next(case for case in manifest["cases"] if case["safety_tags"] == ["forbidden_content"])
    with tempfile.TemporaryDirectory(prefix="g1-independent-forbidden-") as temporary:
        candidate_path = Path(temporary) / "adversarial-candidate.json"
        candidate_path.write_text(
            json.dumps({"rows": case["probe"]["candidate_rows"]}, ensure_ascii=False),
            encoding="utf-8",
        )
        try:
            assert_forbidden_candidate(case, candidate_path)
        except ForbiddenCandidateRejected:
            pass
        else:
            raise AssertionError("真实禁止内容函数未拒绝 adversarial candidate")
        safe_path = Path(temporary) / "safe-candidate.json"
        safe_path.write_text(json.dumps({"rows": []}, ensure_ascii=False), encoding="utf-8")
        assert_forbidden_candidate(case, safe_path)
        try:
            assert_forbidden_candidate(case, Path(temporary) / "missing-candidate.json")
        except ForbiddenCandidateRejected as error:
            raise AssertionError("缺失候选被误判为真实禁项命中") from error
        except AssertionRejected:
            pass
        else:
            raise AssertionError("缺失候选未失败关闭")
    return case["id"]


def _assert_lineage_fail_closed(manifest: dict, oracles: dict) -> list[str]:
    case = next(
        case for case in manifest["cases"]
        if case["expected_outcome"] == "formal_delivery" and case["category"] == "compound"
    )
    oracle = oracles["cases"][case["id"]]
    table = {"columns": oracle["columns"], "rows": oracle["rows"]}
    with tempfile.TemporaryDirectory(prefix="g1-independent-lineage-") as temporary:
        temporary_root = Path(temporary)
        output_path = temporary_root / f"{case['id']}.{case['output_format']}"
        if case["output_format"] == "csv":
            _write_csv(output_path, table)
        elif case["output_format"] == "json":
            output_path.write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
        else:
            write_xlsx_source(output_path, {"Delivery": table})
        base = {
            "status": "delivery_published", "qa_passed": True, "owner_id": case["owner_id"],
            "path": str(output_path), "sha256": _sha256_file(output_path), "size_bytes": output_path.stat().st_size,
            **_lineage_metadata(case),
        }
        mutations = {
            "empty_delivery_id": {"delivery_id": ""},
            "empty_output_id": {"output_id": ""},
            "invalid_candidate_sha256": {"candidate_sha256": "bad"},
            "invalid_verification_report_hash": {"verification_report_hash": "bad"},
            "missing_source_snapshot_refs": {"source_snapshot_refs": []},
            "extra_source_snapshot_ref": {"source_snapshot_refs": base["source_snapshot_refs"] + [{"source_id": "unexpected", "sha256": "0" * 64}]},
        }
        rejected: list[str] = []
        for name, mutation in mutations.items():
            delivery = dict(base)
            delivery.update(mutation)
            try:
                _assert_functional(
                    case,
                    {"outcome": "formal_delivery", "formal_delivery": delivery},
                    oracle,
                    temporary_root,
                )
            except AssertionRejected:
                rejected.append(name)
            else:
                raise AssertionError(f"血缘缺口未失败关闭：{name}")
    return rejected


def main() -> None:
    parser = argparse.ArgumentParser(description="G1 独立盲集静态与冻结闭环自检")
    parser.add_argument("--expected-code-freeze-sha256", required=True)
    args = parser.parse_args()
    manifest = _load_json(ROOT / "heldout_manifest.json")
    freeze = _load_json(ROOT / "freeze.json")
    expected_code_freeze_sha256 = args.expected_code_freeze_sha256
    _verify_rebuilt_metadata(manifest, freeze, expected_code_freeze_sha256)
    _assert_coordinated_tamper_rejected(manifest, freeze, expected_code_freeze_sha256)
    oracles = _load_json(ROOT / "oracles.json")
    catalog_document = _load_json(ROOT / "source_catalog.json")
    catalog = catalog_document["sources"]
    derivation_proof = _load_json(ROOT / "derivation_proof.json")
    all_definitions = FUNCTIONAL_CASES + SAFETY_CASES

    contract = _load_public_contract()
    gaps = contract.qualification_gaps(
        manifest,
        expected_code_freeze_sha256=expected_code_freeze_sha256,
    )
    if gaps:
        raise AssertionError("公共资格合约未通过：\n" + "\n".join(gaps))

    if len(manifest["cases"]) != len(all_definitions):
        raise AssertionError("manifest 与定义题量不一致")
    if any(case.get("external_api_confirmed") is not True for case in manifest["cases"]):
        raise AssertionError("并非全部 case 都冻结了 external_api_confirmed=true")
    if set(oracles["cases"]) != {case["id"] for case in all_definitions}:
        raise AssertionError("oracle ID 集合与定义不一致")

    case_by_id = {case["id"]: case for case in all_definitions}
    for manifest_case in manifest["cases"]:
        definition = case_by_id[manifest_case["id"]]
        expected_goal_hash = _sha256_bytes(_canonical_bytes(manifest_case["goal_contract"]))
        if manifest_case["goal_contract_sha256"] != expected_goal_hash:
            raise AssertionError(f"GoalContract 哈希错误：{manifest_case['id']}")
        if definition["objective"] != manifest_case["objective"]:
            raise AssertionError(f"目标文本漂移：{manifest_case['id']}")

    parsed_catalog: dict[str, dict] = {}
    for relative_path, catalog_source in catalog.items():
        path = (ROOT / relative_path).resolve()
        if ROOT.resolve() not in path.parents:
            raise AssertionError(f"来源越出独立目录：{relative_path}")
        if _sha256_file(path) != catalog_source["sha256"]:
            raise AssertionError(f"来源哈希不匹配：{relative_path}")
        tables = read_source(
            path,
            catalog_source["format"],
            csv_table_name=catalog_source.get("csv_table_name"),
        )
        if tables != catalog_source["tables"]:
            raise AssertionError(f"实际源内容与冻结逻辑表不一致：{relative_path}")
        parsed_catalog[relative_path] = {"tables": tables}

    for case in FUNCTIONAL_CASES:
        derived = derive(parsed_catalog, case["derivation"])
        if derived != case["expected"]:
            raise AssertionError(f"源推导期望不一致：{case['id']}\nderived={derived!r}\nexpected={case['expected']!r}")
        oracle = oracles["cases"][case["id"]]
        frozen_expected = {"columns": oracle["columns"], "rows": oracle["rows"]}
        if derived != frozen_expected:
            raise AssertionError(f"oracle 与源推导不一致：{case['id']}")
        proof = derivation_proof["cases"][case["id"]]
        if proof["expected_sha256"] != _sha256_bytes(_canonical_bytes(derived)):
            raise AssertionError(f"推导证明哈希错误：{case['id']}")

    manifest_sha256 = _sha256_file(ROOT / "heldout_manifest.json")
    if freeze["heldout_manifest_sha256"] != manifest_sha256:
        raise AssertionError("冻结记录中的 manifest 哈希错误")
    for relative_path, expected_hash in freeze["files"].items():
        if _sha256_file(ROOT / relative_path) != expected_hash:
            raise AssertionError(f"冻结文件哈希错误：{relative_path}")
    source_paths = [ROOT / path for path in catalog]
    if freeze["source_bundle_sha256"] != _bundle_digest(source_paths):
        raise AssertionError("来源 bundle 哈希错误")

    n = len(manifest["cases"])
    quota = max(11, ceil(n / 3))
    transformation = sum(bool(set(case["traps"]) & {"paraphrase", "colloquial", "ellipsis", "reordered"}) for case in manifest["cases"])
    ambiguity = sum(bool(set(case["traps"]) & {"similar", "conflict"}) for case in manifest["cases"])
    if transformation < quota or ambiguity < quota:
        raise AssertionError("构成配额不足")
    safety_matrix = {
        case["safety_tags"][0]: {
            "id": case["id"], "stage": case["expected_failure_stage"], "code": case["expected_failure_code"]
        }
        for case in manifest["cases"] if case["safety_tags"]
    }
    if len(safety_matrix) != 5:
        raise AssertionError("安全矩阵不是五类一类一题")

    synthetic_pass_count = _assert_complete_synthetic_result_passes(manifest, oracles)
    wrong_value_formats = _assert_wrong_values_rejected(manifest, oracles)
    forbidden_probe_case = _assert_forbidden_probe_is_mechanical(manifest)
    lineage_rejections = _assert_lineage_fail_closed(manifest, oracles)
    report = {
        "status": "PASS",
        "expected_code_freeze_sha256": expected_code_freeze_sha256,
        "manifest_sha256": manifest_sha256,
        "evaluation_bundle_sha256": freeze["evaluation_bundle_sha256"],
        "manifest_rebuild_equivalent": True,
        "freeze_rebuild_equivalent": True,
        "coordinated_manifest_freeze_tamper_rejected": True,
        "case_count": n,
        "functional_count": len(FUNCTIONAL_CASES),
        "safety_count": len(SAFETY_CASES),
        "external_api_confirmed_count": sum(case.get("external_api_confirmed") is True for case in manifest["cases"]),
        "source_file_count": len(catalog),
        "category_counts": dict(sorted(Counter(case["category"] for case in manifest["cases"]).items())),
        "output_format_counts": dict(sorted(Counter(case["output_format"] for case in manifest["cases"]).items())),
        "ratio_quota": quota,
        "transformation_count": transformation,
        "ambiguity_count": ambiguity,
        "source_hashes_verified": len(catalog),
        "source_derived_oracles_verified": len(FUNCTIONAL_CASES),
        "synthetic_assertion_contract_pass_count": synthetic_pass_count,
        "wrong_business_value_rejection_verified_formats": wrong_value_formats,
        "forbidden_candidate_mechanical_rejection_verified_case": forbidden_probe_case,
        "lineage_fail_closed_checks": lineage_rejections,
        "safety_matrix": safety_matrix,
        "qualification_gaps": [],
    }
    (ROOT / "self-check-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
