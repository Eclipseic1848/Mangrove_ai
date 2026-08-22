# -*- coding: utf-8 -*-
"""按运行时代码冻结身份确定性重建独立 G1 清单与冻结元数据。"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import re
from pathlib import Path

from definitions import BLIND_SET_PROVIDED_AT, FUNCTIONAL_CASES, SAFETY_CASES


ROOT = Path(__file__).resolve().parent
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def json_document_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_json(path: Path, value: object) -> None:
    path.write_bytes(json_document_bytes(value))


def require_code_freeze_sha256(value: str) -> str:
    if not _SHA256_PATTERN.fullmatch(value):
        raise ValueError("expected code-freeze 必须是 64 位小写 SHA-256")
    return value


def source_definitions() -> dict[str, dict]:
    sources: dict[str, dict] = {}
    for case in FUNCTIONAL_CASES + SAFETY_CASES:
        for source in case["sources"]:
            relative_path = source["file"]
            if relative_path in sources:
                raise ValueError(f"来源路径重复：{relative_path}")
            path = ROOT / relative_path
            if not path.is_file():
                raise ValueError(f"冻结来源不存在：{relative_path}")
            sources[relative_path] = source
    return sources


def _source_binding(source: dict) -> dict:
    path = ROOT / source["file"]
    media_types = {
        "csv": "text/csv",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pdf": "application/pdf",
    }
    return {
        "source_id": "src-" + sha256_bytes(source["file"].encode("utf-8"))[:16],
        "path": source["file"],
        "format": source["format"],
        "media_type": media_types[source["format"]],
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "table_names": list(source["tables"]),
    }


def _goal_contract(case: dict, bindings: list[dict]) -> dict:
    expected = case.get("expected")
    columns = list(expected["columns"]) if expected else []
    return {
        "objective": case["objective"],
        "source_scope": [binding["source_id"] for binding in bindings],
        "operation": "reject_probe" if case.get("safety_tags") else "source_derived_transform",
        "must_include": columns,
        "must_exclude": list(case.get("probe", {}).get("forbidden_literals", [])),
        "result_semantics": "source_view",
        "evidence_policy": {"source_sha256_required": True, "exact_business_value_required": True},
        "delivery_spec": {
            "format": case["output_format"],
            "exact_columns": columns,
            "no_extra_rows": True,
            "no_extra_columns": True,
        },
    }


def _manifest_case(case: dict) -> dict:
    bindings = [_source_binding(source) for source in case["sources"]]
    owner_id = case.get("owner_id", f"heldout-owner-{case['id'].lower()}")
    goal_contract = _goal_contract(case, bindings)
    entry = {
        "id": case["id"],
        "category": case["category"],
        "objective": case["objective"],
        "traps": case["traps"],
        "output_format": case["output_format"],
        "source_bindings": bindings,
        "goal_contract": goal_contract,
        "goal_contract_sha256": sha256_bytes(canonical_json_bytes(goal_contract)),
        "owner_id": owner_id,
        "publish_actor_id": case.get("publish_actor_id", owner_id),
        "qualification_owner_id": case.get("qualification_owner_id", owner_id),
        "external_api_confirmed": True,
        "safety_tags": case.get("safety_tags", []),
        "expected_outcome": "rejected" if case.get("safety_tags") else "formal_delivery",
        "oracle_key": case["id"],
    }
    if case.get("safety_tags"):
        entry.update({
            "expected_failure_stage": case["expected_failure_stage"],
            "expected_failure_code": case["expected_failure_code"],
            "probe": case["probe"],
        })
    return entry


def build_manifest(expected_code_freeze_sha256: str) -> dict:
    """只依赖不可变定义、冻结源文件和显式 code-freeze 重建完整清单。"""

    expected_code_freeze_sha256 = require_code_freeze_sha256(expected_code_freeze_sha256)
    source_definitions()
    return {
        "schema_version": "g1-independent-heldout-manifest.v1",
        "evaluation_status": "heldout",
        "independent_heldout": True,
        "blind_set_attestation": {
            "provider": "Codex G1 独立评测方",
            "provided_at": BLIND_SET_PROVIDED_AT,
            "code_freeze_sha256": expected_code_freeze_sha256,
            "code_freeze_source": "由正式 runner 通过 --expected-code-freeze-sha256 显式提供",
            "prohibited_inputs_observed": False,
            "statement": "未读取既有 fixtures/assertions/runs 或 Runtime/Publisher/评测实现；题目和答案均在代码冻结后独立创建。",
        },
        "oracle_policy": {
            "path": "oracles.json",
            "available_to_execution_model": False,
            "evaluation_only": True,
        },
        "cases": [_manifest_case(case) for case in FUNCTIONAL_CASES + SAFETY_CASES],
    }


def _frozen_files(source_by_path: dict[str, dict]) -> tuple[list[Path], list[Path]]:
    metadata_and_immutable = [
        ROOT / "heldout_manifest.json", ROOT / "oracles.json", ROOT / "derivation_proof.json",
        ROOT / "source_catalog.json", ROOT / "assertions.py", ROOT / "self_check.py",
        ROOT / "oracle_engine.py", ROOT / "source_io.py", ROOT / "definitions.py",
        ROOT / "build_independent_set.py", ROOT / "results-schema.json", ROOT / "README.md",
    ]
    source_files = [ROOT / path for path in source_by_path]
    return metadata_and_immutable, source_files


def _hash_records(paths: list[Path], overrides: dict[str, bytes] | None = None) -> list[dict[str, str]]:
    overrides = overrides or {}
    records: list[dict[str, str]] = []
    for path in sorted(paths, key=lambda item: item.relative_to(ROOT).as_posix()):
        relative_path = path.relative_to(ROOT).as_posix()
        content = overrides.get(relative_path)
        digest = sha256_bytes(content) if content is not None else sha256_file(path)
        records.append({"path": relative_path, "sha256": digest})
    return records


def _bundle_digest(paths: list[Path], overrides: dict[str, bytes] | None = None) -> str:
    return sha256_bytes(canonical_json_bytes(_hash_records(paths, overrides)))


def build_freeze(manifest: dict, expected_code_freeze_sha256: str) -> dict:
    """在内存中重算完整 freeze；不信任磁盘上的 manifest 或 freeze。"""

    expected_code_freeze_sha256 = require_code_freeze_sha256(expected_code_freeze_sha256)
    if (manifest.get("blind_set_attestation") or {}).get("code_freeze_sha256") != expected_code_freeze_sha256:
        raise ValueError("manifest 未绑定 expected code-freeze")
    source_by_path = source_definitions()
    frozen_files, source_files = _frozen_files(source_by_path)
    manifest_bytes = json_document_bytes(manifest)
    overrides = {"heldout_manifest.json": manifest_bytes}
    file_records = _hash_records(frozen_files + source_files, overrides)
    return {
        "schema_version": "g1-independent-freeze.v1",
        "code_freeze_sha256": expected_code_freeze_sha256,
        "heldout_manifest_sha256": sha256_bytes(manifest_bytes),
        "oracles_sha256": sha256_file(ROOT / "oracles.json"),
        "assertions_sha256": sha256_file(ROOT / "assertions.py"),
        "source_bundle_sha256": _bundle_digest(source_files),
        "evaluation_bundle_sha256": sha256_bytes(canonical_json_bytes(file_records)),
        "files": {record["path"]: record["sha256"] for record in file_records},
    }


def rebuild_metadata(expected_code_freeze_sha256: str) -> tuple[dict, dict]:
    """只写 runner 允许更新的 manifest/freeze 元数据。"""

    manifest = build_manifest(expected_code_freeze_sha256)
    freeze = build_freeze(manifest, expected_code_freeze_sha256)
    write_json(ROOT / "heldout_manifest.json", manifest)
    write_json(ROOT / "freeze.json", freeze)
    return manifest, freeze


def main() -> None:
    parser = argparse.ArgumentParser(description="重建 G1 独立盲集 manifest/freeze 元数据")
    parser.add_argument("--expected-code-freeze-sha256", required=True)
    args = parser.parse_args()
    manifest, freeze = rebuild_metadata(args.expected_code_freeze_sha256)
    print(json.dumps({
        "cases": len(manifest["cases"]),
        "code_freeze_sha256": freeze["code_freeze_sha256"],
        "manifest_sha256": freeze["heldout_manifest_sha256"],
        "evaluation_bundle_sha256": freeze["evaluation_bundle_sha256"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
