# -*- coding: utf-8 -*-
"""确定性生成 G1-02 v2 独立盲集及冻结。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from definitions import (
    PROVIDED_AT,
    PROVIDER,
    functional_definitions,
    safety_definitions,
)
from oracle_engine import derive_rows
from source_io import write_source


ROOT = Path(__file__).resolve().parent
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
}
IMMUTABLE_NAMES = (
    "assertions.py",
    "build_independent_set.py",
    "definitions.py",
    "derivation_proof.json",
    "heldout_manifest.json",
    "oracle_engine.py",
    "oracles.json",
    "README.md",
    "results-schema.json",
    "self_check.py",
    "source_catalog.json",
    "source_io.py",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _binding(source: dict) -> dict:
    path = ROOT / "sources" / source["filename"]
    return {
        "source_id": source["source_id"],
        "path": f"sources/{source['filename']}",
        "sha256": sha256_file(path),
        "media_type": MEDIA_TYPES[source["format"]],
    }


def build_payloads(code_freeze_sha256: str, *, write_sources: bool) -> tuple[dict, dict, dict, dict]:
    if not HASH_RE.fullmatch(code_freeze_sha256):
        raise ValueError("expected code-freeze 必须是 64 位小写十六进制")
    functional = functional_definitions()
    safety = safety_definitions()
    if write_sources:
        (ROOT / "sources").mkdir(parents=True, exist_ok=True)
        for case in functional:
            for source in case["sources"]:
                write_source(ROOT / "sources" / source["filename"], source["format"], source["tables"])
        for case in safety:
            source = case["source"]
            write_source(ROOT / "sources" / source["filename"], source["format"], source["tables"])
    manifest_cases = []
    oracle_cases = {}
    proofs = []
    catalog = []
    for case in functional:
        bindings = [_binding(source) for source in case["sources"]]
        rows = derive_rows(case, ROOT)
        delivery_spec = {
            "format": case["output_format"],
            "exact_columns": case["columns"],
            "row_order": case["row_order"],
            "value_formats": case["value_formats"],
        }
        if case["output_format"] == "json":
            delivery_spec["json_shape"] = case["json_shape"]
        manifest_cases.append({
            "id": case["id"],
            "category": case["category"],
            "traps": case["traps"],
            "safety_tags": [],
            "owner_id": case["owner_id"],
            "objective": case["objective"],
            "output_format": case["output_format"],
            "expected_outcome": "formal_delivery",
            "external_api_confirmed": True,
            "source_bindings": bindings,
            "goal_contract": {
                "operation": "按明确规则筛选、计算、映射并排序",
                "must_include": case["columns"],
                "must_exclude": ["nearby 表的数据", "未授权来源", "额外列"],
                "result_semantics": "source_view",
                "delivery_spec": delivery_spec,
            },
        })
        oracle_cases[case["id"]] = {
            "columns": case["columns"],
            "rows": rows,
            "ordered_rows": True,
            "json_shape": case["json_shape"],
        }
        proofs.append({
            "case_id": case["id"],
            "method": "source_io.read_source -> oracle_engine.derive_rows",
            "source_refs": [{"source_id": item["source_id"], "sha256": item["sha256"]} for item in bindings],
            "oracle_sha256": canonical_sha256(oracle_cases[case["id"]]),
        })
        catalog.extend({**item, "case_id": case["id"]} for item in bindings)
    for case in safety:
        binding = _binding(case["source"])
        probe_rows = [{"probe": case.get("forbidden", "owner-isolation-v2")}]
        manifest_case = {
            "id": case["id"],
            "category": case["category"],
            "traps": [],
            "safety_tags": [case["tag"]],
            "owner_id": case["owner_id"],
            "objective": "验证安全失败关闭；不得形成攻击者可见的正式 Delivery。",
            "output_format": case["format"],
            "expected_outcome": "rejected",
            "expected_failure_stage": case["stage"],
            "expected_failure_code": case["code"],
            "external_api_confirmed": True,
            "source_bindings": [binding],
            "goal_contract": {
                "operation": "安全拒绝探针",
                "must_include": [],
                "must_exclude": [case.get("forbidden", "跨 Owner 内容")],
                "result_semantics": "rejected",
            },
            "probe": {"candidate_rows": probe_rows, "forbidden_literals": [case["forbidden"]] if case.get("forbidden") else []},
        }
        if case.get("actor"):
            manifest_case["publish_actor_id"] = case["actor"]
            manifest_case["qualification_owner_id"] = case["owner_id"]
        if case.get("attacker"):
            manifest_case["qualification_owner_id"] = case["attacker"]
        manifest_cases.append(manifest_case)
        catalog.append({**binding, "case_id": case["id"]})
    manifest = {
        "schema_version": "g1-independent-heldout.v2",
        "evaluation_status": "heldout",
        "independent_heldout": True,
        "blind_set_attestation": {
            "provider": PROVIDER,
            "provided_at": PROVIDED_AT,
            "code_freeze_sha256": code_freeze_sha256,
            "source": "由独立生成器运行时接收当前 G1 代码冻结身份；题源及业务 oracle 不依赖生产实现。",
        },
        "cases": manifest_cases,
    }
    oracles = {"schema_version": "g1-independent-oracles.v2", "cases": oracle_cases}
    derivation = {"schema_version": "g1-independent-derivation.v2", "proofs": proofs}
    source_catalog = {"schema_version": "g1-independent-sources.v2", "sources": catalog}
    return manifest, oracles, derivation, source_catalog


def _bundle_hash(file_hashes: dict[str, str]) -> str:
    return canonical_sha256(file_hashes)


def build(code_freeze_sha256: str) -> dict:
    manifest, oracles, derivation, catalog = build_payloads(code_freeze_sha256, write_sources=True)
    _write_json(ROOT / "heldout_manifest.json", manifest)
    _write_json(ROOT / "oracles.json", oracles)
    _write_json(ROOT / "derivation_proof.json", derivation)
    _write_json(ROOT / "source_catalog.json", catalog)
    file_hashes = {name: sha256_file(ROOT / name) for name in IMMUTABLE_NAMES}
    source_hashes = {
        path.relative_to(ROOT).as_posix(): sha256_file(path)
        for path in sorted((ROOT / "sources").iterdir()) if path.is_file()
    }
    evaluation_files = {**file_hashes, **source_hashes}
    freeze = {
        "schema_version": "g1-independent-freeze.v2",
        "code_freeze_sha256": code_freeze_sha256,
        "heldout_manifest_sha256": file_hashes["heldout_manifest.json"],
        "source_bundle_sha256": canonical_sha256(source_hashes),
        "evaluation_bundle_sha256": _bundle_hash(evaluation_files),
        "files": evaluation_files,
    }
    _write_json(ROOT / "freeze.json", freeze)
    return freeze


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-code-freeze-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.expected_code_freeze_sha256), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
