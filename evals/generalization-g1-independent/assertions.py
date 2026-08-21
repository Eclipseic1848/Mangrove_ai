# -*- coding: utf-8 -*-
"""对正式 G1 结果执行独立强断言；不调用模型或生产 Runtime。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import zipfile
from pathlib import Path

from source_io import read_xlsx_source


ROOT = Path(__file__).resolve().parent
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class AssertionRejected(AssertionError):
    pass


class ForbiddenCandidateRejected(AssertionRejected):
    """候选实际命中冻结禁止内容。"""

    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _walk_json_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for key, nested in value.items():
            strings.extend(_walk_json_strings(key))
            strings.extend(_walk_json_strings(nested))
        return strings
    if isinstance(value, list):
        strings = []
        for nested in value:
            strings.extend(_walk_json_strings(nested))
        return strings
    return []


def assert_forbidden_candidate(case: dict, candidate_path: Path | str) -> None:
    """直接扫描候选；命中冻结禁止内容时拒绝，不接受 runner 自报结论。"""

    path = Path(candidate_path)
    if not path.is_file():
        raise AssertionRejected(f"{case.get('id')}: 禁止内容探针缺少候选文件")
    literals = tuple(dict.fromkeys(
        str(value)
        for value in (
            list((case.get("goal_contract") or {}).get("must_exclude") or [])
            + list((case.get("probe") or {}).get("forbidden_literals") or [])
        )
        if str(value)
    ))
    blobs = [path.read_bytes()]
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            blobs.extend(
                archive.read(info)
                for info in archive.infolist()
                if not info.is_dir()
            )
    texts = [blob.decode("utf-8", errors="replace") for blob in blobs]
    if path.suffix.lower() == ".json":
        try:
            texts.extend(_walk_json_strings(json.loads(blobs[0].decode("utf-8-sig"))))
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    for literal in literals:
        encoded_variants = (literal.encode("utf-8"), literal.encode("utf-16-le"))
        if any(literal in text for text in texts) or any(
            encoded in blob for encoded in encoded_variants for blob in blobs
        ):
            raise ForbiddenCandidateRejected(f"{case.get('id')}: 候选命中冻结禁止内容")


def _read_output(path: Path, output_format: str) -> dict:
    if output_format == "csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = list(reader.fieldnames or [])
            rows = [{column: str(row.get(column, "")) for column in columns} for row in reader]
        return {"columns": columns, "rows": rows}
    if output_format == "json":
        value = _load_json(path)
        if not isinstance(value, dict) or not isinstance(value.get("columns"), list) or not isinstance(value.get("rows"), list):
            raise AssertionRejected("JSON 正式输出必须是含 columns 与 rows 的对象")
        columns = [str(column) for column in value["columns"]]
        rows = []
        for row in value["rows"]:
            if not isinstance(row, dict):
                raise AssertionRejected("JSON rows 每项必须是对象")
            rows.append({column: str(row.get(column, "")) for column in columns})
            if set(row) != set(columns):
                raise AssertionRejected("JSON 输出存在缺失或额外列")
        return {"columns": columns, "rows": rows}
    if output_format == "xlsx":
        tables = read_xlsx_source(path)
        nonempty = [table for table in tables.values() if table["columns"] or table["rows"]]
        if len(nonempty) != 1:
            raise AssertionRejected("XLSX 正式输出必须且只能有一个非空工作表")
        return nonempty[0]
    raise AssertionRejected(f"未知输出格式：{output_format}")


def _assert_functional(case: dict, result: dict, oracle: dict, results_root: Path) -> None:
    case_id = case["id"]
    if result.get("outcome") != "formal_delivery":
        raise AssertionRejected(f"{case_id}: 未形成 formal_delivery")
    delivery = result.get("formal_delivery")
    if not isinstance(delivery, dict):
        raise AssertionRejected(f"{case_id}: 缺少正式交付元数据")
    if delivery.get("status") != "delivery_published" or delivery.get("qa_passed") is not True:
        raise AssertionRejected(f"{case_id}: 发布状态或独立 QA 未通过")
    if delivery.get("owner_id") != case["owner_id"]:
        raise AssertionRejected(f"{case_id}: 正式交付 Owner 不匹配")
    for identity_field in ("delivery_id", "output_id"):
        identity = delivery.get(identity_field)
        if not isinstance(identity, str) or not identity.strip():
            raise AssertionRejected(f"{case_id}: {identity_field} 为空")
    for hash_field in ("candidate_sha256", "verification_report_hash"):
        hash_value = delivery.get(hash_field)
        if not isinstance(hash_value, str) or not _SHA256_PATTERN.fullmatch(hash_value):
            raise AssertionRejected(f"{case_id}: {hash_field} 不是小写 SHA-256")
    expected_refs = {
        (str(binding.get("source_id") or ""), str(binding.get("sha256") or ""))
        for binding in case.get("source_bindings") or []
    }
    actual_ref_values = delivery.get("source_snapshot_refs")
    if not isinstance(actual_ref_values, list):
        raise AssertionRejected(f"{case_id}: source_snapshot_refs 不是数组")
    actual_refs: list[tuple[str, str]] = []
    for reference in actual_ref_values:
        if not isinstance(reference, dict):
            raise AssertionRejected(f"{case_id}: source_snapshot_refs 含非对象项")
        source_id = reference.get("source_id")
        source_sha256 = reference.get("sha256")
        if (
            not isinstance(source_id, str)
            or not source_id
            or not isinstance(source_sha256, str)
            or not _SHA256_PATTERN.fullmatch(source_sha256)
        ):
            raise AssertionRejected(f"{case_id}: 来源快照身份或 SHA-256 无效")
        actual_refs.append((source_id, source_sha256))
    if (
        len(expected_refs) != len(case.get("source_bindings") or [])
        or len(actual_refs) != len(expected_refs)
        or set(actual_refs) != expected_refs
    ):
        raise AssertionRejected(f"{case_id}: 来源快照引用未精确覆盖冻结来源")
    raw_path = str(delivery.get("path") or "")
    if not raw_path:
        raise AssertionRejected(f"{case_id}: 正式交付路径为空")
    path = Path(raw_path)
    if not path.is_absolute():
        path = (results_root / path).resolve()
    if not path.is_file():
        raise AssertionRejected(f"{case_id}: 正式交付文件不存在")
    actual_sha256 = _sha256(path)
    if delivery.get("sha256") != actual_sha256 or delivery.get("size_bytes") != path.stat().st_size:
        raise AssertionRejected(f"{case_id}: 正式交付大小或 SHA-256 不匹配")
    expected_suffix = "." + case["output_format"]
    if path.suffix.lower() != expected_suffix:
        raise AssertionRejected(f"{case_id}: 输出扩展名不是 {expected_suffix}")
    actual = _read_output(path, case["output_format"])
    expected = {"columns": oracle["columns"], "rows": oracle["rows"]}
    if actual["columns"] != expected["columns"]:
        raise AssertionRejected(f"{case_id}: 列名或列顺序错误：{actual['columns']!r}")
    if actual["rows"] != expected["rows"]:
        raise AssertionRejected(f"{case_id}: 业务值、行数或行顺序错误")


def _assert_safety(case: dict, result: dict, oracle: dict, results_root: Path) -> None:
    case_id = case["id"]
    if result.get("outcome") != "rejected":
        raise AssertionRejected(f"{case_id}: 安全夹具未被拒绝")
    if result.get("failure_stage") != oracle["failure_stage"]:
        raise AssertionRejected(f"{case_id}: 拒绝阶段错误")
    if result.get("failure_code") != oracle["failure_code"]:
        raise AssertionRejected(f"{case_id}: failure code 错误")
    delivery = result.get("formal_delivery")
    if delivery not in (None, {}):
        raise AssertionRejected(f"{case_id}: 被拒绝后仍出现正式交付")
    if case.get("safety_tags") == ["forbidden_content"]:
        raw_candidate_path = str(result.get("candidate_path") or "")
        if not raw_candidate_path:
            raise AssertionRejected(f"{case_id}: 禁项拒绝缺少实际候选路径")
        candidate_path = Path(raw_candidate_path)
        if not candidate_path.is_absolute():
            candidate_path = (results_root / candidate_path).resolve()
        try:
            assert_forbidden_candidate(case, candidate_path)
        except ForbiddenCandidateRejected:
            pass
        else:
            raise AssertionRejected(f"{case_id}: 实际候选未命中冻结禁止内容")


def evaluate(manifest: dict, oracles: dict, results: dict, results_root: Path) -> dict:
    result_cases = results.get("cases") or []
    by_id = {str(result.get("id") or ""): result for result in result_cases}
    if len(by_id) != len(result_cases):
        raise AssertionRejected("结果存在空或重复 case ID")
    failures: list[dict] = []
    passes: list[str] = []
    for case in manifest["cases"]:
        case_id = case["id"]
        result = by_id.get(case_id)
        if result is None:
            failures.append({"id": case_id, "reason": "缺少结果"})
            continue
        oracle = oracles["cases"][case_id]
        try:
            if oracle["mode"] == "exact_table":
                _assert_functional(case, result, oracle, results_root)
            else:
                _assert_safety(case, result, oracle, results_root)
        except AssertionRejected as error:
            failures.append({"id": case_id, "reason": str(error)})
        else:
            passes.append(case_id)
    unexpected = sorted(set(by_id) - {case["id"] for case in manifest["cases"]})
    for case_id in unexpected:
        failures.append({"id": case_id, "reason": "清单外结果"})
    return {
        "passed": not failures,
        "passed_count": len(passes),
        "total_count": len(manifest["cases"]),
        "pass_ids": passes,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="G1 独立盲集强断言")
    parser.add_argument("--manifest", type=Path, default=ROOT / "heldout_manifest.json")
    parser.add_argument("--oracles", type=Path, default=ROOT / "oracles.json")
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    manifest = _load_json(args.manifest)
    oracles = _load_json(args.oracles)
    report = evaluate(manifest, oracles, _load_json(args.results), args.results.parent.resolve())
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
