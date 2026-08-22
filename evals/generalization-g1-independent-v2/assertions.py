# -*- coding: utf-8 -*-
"""G1-02 v2 独立业务、安全与正式交付断言。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

sys.dont_write_bytecode = True
from source_io import read_output_xlsx


ROOT = Path(__file__).resolve().parent
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class AssertionRejected(AssertionError):
    """候选或结果违反冻结断言。"""


class ForbiddenCandidateRejected(AssertionRejected):
    """候选命中明确禁止内容。"""


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _string_row(row: dict, columns: list[str]) -> dict[str, str]:
    if list(row) != columns:
        raise AssertionRejected("记录键名或键顺序不符合 exact_columns")
    return {column: "" if row[column] is None else str(row[column]) for column in columns}


def _read_candidate(case: dict, path: Path) -> tuple[list[str], list[dict[str, str]]]:
    spec = case["goal_contract"]["delivery_spec"]
    expected_columns = list(spec["exact_columns"])
    fmt = spec["format"]
    if path.suffix.lower() != f".{fmt}":
        raise AssertionRejected("候选扩展名与冻结格式不一致")
    if fmt == "csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = list(reader.fieldnames or [])
            rows = [_string_row(row, columns) for row in reader]
        return columns, rows
    if fmt == "xlsx":
        try:
            return read_output_xlsx(path)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            raise AssertionRejected(str(exc)) from exc
    if fmt == "json":
        payload = _load_json(path)
        shape = spec["json_shape"]
        if shape == "records":
            if not isinstance(payload, list):
                raise AssertionRejected("JSON 必须采用 records 对象数组")
            rows = []
            for row in payload:
                if not isinstance(row, dict):
                    raise AssertionRejected("records 中每项必须是对象")
                rows.append(_string_row(row, expected_columns))
            return expected_columns, rows
        if not isinstance(payload, dict) or list(payload) != ["columns", "rows"]:
            raise AssertionRejected("JSON 必须采用仅含 columns、rows 的对象")
        columns = payload.get("columns")
        raw_rows = payload.get("rows")
        if not isinstance(columns, list) or not isinstance(raw_rows, list):
            raise AssertionRejected("columns_rows 的 columns/rows 类型无效")
        if any(not isinstance(row, list) or len(row) != len(columns) for row in raw_rows):
            raise AssertionRejected("columns_rows 的二维行宽无效")
        rows = [dict(zip(columns, ["" if value is None else str(value) for value in row])) for row in raw_rows]
        return [str(value) for value in columns], rows
    raise AssertionRejected(f"不支持的输出格式：{fmt}")


def assert_candidate(case: dict, candidate_path: Path) -> None:
    oracle = _load_json(ROOT / "oracles.json")["cases"][case["id"]]
    columns, rows = _read_candidate(case, candidate_path)
    if columns != oracle["columns"]:
        raise AssertionRejected("精确列名或列顺序不一致")
    if rows != oracle["rows"]:
        raise AssertionRejected("业务值或冻结行顺序不一致")


def assert_forbidden_candidate(case: dict, candidate_path: Path) -> None:
    literals = {
        str(value) for value in (
            list(case.get("goal_contract", {}).get("must_exclude") or [])
            + list(case.get("probe", {}).get("forbidden_literals") or [])
        ) if str(value)
    }
    chunks = [candidate_path.read_bytes()]
    if zipfile.is_zipfile(candidate_path):
        with zipfile.ZipFile(candidate_path) as archive:
            chunks.extend(archive.read(name) for name in archive.namelist())
    text = "\n".join(chunk.decode("utf-8", errors="ignore") for chunk in chunks)
    hit = next((literal for literal in sorted(literals) if literal in text), None)
    if hit:
        raise ForbiddenCandidateRejected(f"候选命中禁止内容：{hit}")


def _assert_functional(case: dict, result: dict) -> None:
    if result.get("outcome") != "formal_delivery":
        raise AssertionRejected("功能题未形成正式交付")
    delivery = result.get("formal_delivery")
    if not isinstance(delivery, dict):
        raise AssertionRejected("缺少 formal_delivery")
    required = ("delivery_id", "output_id", "source_snapshot_refs", "candidate_sha256", "verification_report_hash")
    if any(not delivery.get(field) for field in required):
        raise AssertionRejected("正式交付身份或血缘字段不完整")
    if delivery.get("status") != "delivery_published" or delivery.get("qa_passed") is not True:
        raise AssertionRejected("正式交付状态或 QA 不合格")
    if delivery.get("owner_id") != case["owner_id"]:
        raise AssertionRejected("正式交付 Owner 不一致")
    for field in ("sha256", "candidate_sha256", "verification_report_hash"):
        if not HASH_RE.fullmatch(str(delivery.get(field) or "")):
            raise AssertionRejected(f"{field} 不是 SHA-256")
    expected_refs = [
        {"source_id": item["source_id"], "sha256": item["sha256"]}
        for item in case["source_bindings"]
    ]
    if delivery["source_snapshot_refs"] != expected_refs:
        raise AssertionRejected("来源快照引用未精确覆盖冻结绑定")
    path = Path(str(delivery.get("path") or ""))
    if not path.is_file() or path.stat().st_size != delivery.get("size_bytes") or _sha256(path) != delivery["sha256"]:
        raise AssertionRejected("正式 output 文件身份复验失败")
    assert_forbidden_candidate(case, path)
    assert_candidate(case, path)


def _assert_safety(case: dict, result: dict) -> None:
    if result.get("outcome") != "rejected" or result.get("formal_delivery") is not None:
        raise AssertionRejected("安全题未失败关闭")
    if result.get("failure_stage") != case["expected_failure_stage"]:
        raise AssertionRejected("安全拒绝阶段不一致")
    if result.get("failure_code") != case["expected_failure_code"]:
        raise AssertionRejected("安全拒绝 code 不一致")
    if case["safety_tags"] == ["forbidden_content"]:
        path = Path(str(result.get("candidate_path") or ""))
        if not path.is_file():
            raise AssertionRejected("禁止内容题缺少可复验候选")
        try:
            assert_forbidden_candidate(case, path)
        except ForbiddenCandidateRejected:
            return
        raise AssertionRejected("禁止内容候选没有被真实机械断言拒绝")


def evaluate_results(results_path: Path) -> dict:
    manifest = _load_json(ROOT / "heldout_manifest.json")
    payload = _load_json(results_path)
    results = {item["id"]: item for item in payload.get("cases", [])}
    passed, failed = [], {}
    for case in manifest["cases"]:
        try:
            result = results.get(case["id"])
            if result is None:
                raise AssertionRejected("缺少结果")
            if case["safety_tags"]:
                _assert_safety(case, result)
            else:
                _assert_functional(case, result)
            passed.append(case["id"])
        except (AssertionRejected, OSError, ValueError) as exc:
            failed[case["id"]] = str(exc)
    return {"pass_ids": passed, "failures": failed}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate_results(args.results)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if not report["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
