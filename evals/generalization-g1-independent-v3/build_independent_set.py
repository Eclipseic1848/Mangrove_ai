# -*- coding: utf-8 -*-
"""生成并冻结 G1-03 独立盲集。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from artifact_io import write_source
from definitions import PROVIDED_AT, PROVIDER, functional_cases, safety_cases
from oracle_engine import derive


ROOT = Path(__file__).resolve().parent
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
MEDIA = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "csv": "text/csv"}
IMMUTABLE = (".gitignore", "artifact_io.py", "assertions.py", "build_independent_set.py", "definitions.py", "derivation_proof.json", "heldout_manifest.json", "oracle_engine.py", "oracles.json", "README.md", "results-schema.json", "self_check.py", "source_catalog.json")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""): digest.update(chunk)
    return digest.hexdigest()


def canonical(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def generate_sources(target: Path) -> None:
    for case in functional_cases():
        for source in case["sources"]:
            write_source(target / "sources" / source["filename"], source["format"], source["sections"])
    for case in safety_cases():
        source = case["source"]
        write_source(target / "sources" / source["filename"], source["format"], source["sections"])


def source_hashes(target: Path) -> dict[str, str]:
    return {path.relative_to(target).as_posix(): sha(path) for path in sorted((target / "sources").iterdir()) if path.is_file()}


def payloads(target: Path, code_freeze: str, *, write_sources: bool) -> tuple[dict, dict, dict, dict]:
    if not HASH_RE.fullmatch(code_freeze): raise ValueError("code-freeze 必须为 64 位小写十六进制")
    if write_sources: generate_sources(target)
    manifest_cases, oracle_cases, proofs, catalog = [], {}, [], []
    for case in functional_cases():
        bindings = []
        for source in case["sources"]:
            path = target / "sources" / source["filename"]
            binding = {"source_id": source["source_id"], "path": f"sources/{source['filename']}", "sha256": sha(path), "media_type": MEDIA[source["format"]]}
            bindings.append(binding); catalog.append({"case_id": case["id"], **binding})
        spec = {"format": case["output_format"], "exact_columns": case["columns"], "row_order": case["row_order"], "value_formats": {case["columns"][2]: "decimal_2"}}
        if case["output_format"] == "json": spec["json_shape"] = case["json_shape"]
        manifest_cases.append({
            "id": case["id"], "category": case["category"], "traps": case["traps"], "safety_tags": [],
            "owner_id": case["owner_id"], "objective": case["objective"], "output_format": case["output_format"],
            "expected_outcome": "formal_delivery", "external_api_confirmed": True, "source_bindings": bindings,
            "goal_contract": {"operation": "按冻结规则筛选、修订、计算与排序", "must_include": case["columns"], "must_exclude": ["lookalike 区段", "未授权数据", "额外列"], "result_semantics": "source_view", "delivery_spec": spec},
        })
        oracle_cases[case["id"]] = {"columns": case["columns"], "rows": derive(case, target), "ordered_rows": True, "json_shape": case["json_shape"]}
        proofs.append({"case_id": case["id"], "method": "artifact_io.read_source -> oracle_engine.derive", "source_refs": [{"source_id": b["source_id"], "sha256": b["sha256"]} for b in bindings], "oracle_sha256": canonical(oracle_cases[case["id"]])})
    for case in safety_cases():
        source = case["source"]; path = target / "sources" / source["filename"]
        binding = {"source_id": source["source_id"], "path": f"sources/{source['filename']}", "sha256": sha(path), "media_type": MEDIA[source["format"]]}
        item = {
            "id": case["id"], "category": case["category"], "traps": [], "safety_tags": [case["tag"]],
            "owner_id": case["owner_id"], "objective": "执行冻结安全探针，必须失败关闭且攻击者视角不得出现正式交付。",
            "output_format": case["format"], "expected_outcome": "rejected", "expected_failure_stage": case["stage"], "expected_failure_code": case["code"],
            "external_api_confirmed": True, "source_bindings": [binding],
            "goal_contract": {"operation": "安全失败关闭探针", "must_include": [], "must_exclude": [case["forbidden"] or "跨 Owner 正文"], "result_semantics": "rejected"},
            "probe": {"candidate_rows": [{"probe": case["forbidden"] or "g103-owner-boundary"}], "forbidden_literals": [case["forbidden"]] if case["forbidden"] else []},
        }
        if case["tag"] == "permission_denied": item.update({"publish_actor_id": case["attacker_id"], "qualification_owner_id": case["owner_id"]})
        if case["tag"] in {"cross_owner", "user_isolation"}: item["qualification_owner_id"] = case["attacker_id"]
        manifest_cases.append(item); catalog.append({"case_id": case["id"], **binding})
    manifest = {"schema_version": "g1-independent-heldout.v3", "evaluation_status": "heldout", "independent_heldout": True, "blind_set_attestation": {"provider": PROVIDER, "provided_at": PROVIDED_AT, "code_freeze_sha256": code_freeze, "source": "独立生成器仅在运行时接收公开代码冻结身份；业务题源和答案不依赖生产实现。"}, "cases": manifest_cases}
    return manifest, {"schema_version": "g1-independent-oracles.v3", "cases": oracle_cases}, {"schema_version": "g1-independent-derivation.v3", "proofs": proofs}, {"schema_version": "g1-independent-sources.v3", "sources": catalog}


def build(code_freeze: str) -> dict:
    manifest, oracles, proof, catalog = payloads(ROOT, code_freeze, write_sources=True)
    for name, value in (("heldout_manifest.json", manifest), ("oracles.json", oracles), ("derivation_proof.json", proof), ("source_catalog.json", catalog)): write_json(ROOT / name, value)
    files = {name: sha(ROOT / name) for name in IMMUTABLE}; sources = source_hashes(ROOT); all_files = {**files, **sources}
    freeze = {"schema_version": "g1-independent-freeze.v3", "code_freeze_sha256": code_freeze, "heldout_manifest_sha256": files["heldout_manifest.json"], "source_bundle_sha256": canonical(sources), "evaluation_bundle_sha256": canonical(all_files), "files": all_files}
    write_json(ROOT / "freeze.json", freeze); return freeze


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--expected-code-freeze-sha256", required=True); args = parser.parse_args()
    print(json.dumps(build(args.expected_code_freeze_sha256), ensure_ascii=True, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
