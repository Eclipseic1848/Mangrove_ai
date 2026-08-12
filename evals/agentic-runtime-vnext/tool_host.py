# -*- coding: utf-8 -*-
"""三个候选共用的最小领域工具宿主与独立验证器。"""
from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path
import sys
import time
from typing import Any


MAX_CANDIDATE_CHARS = 200_000


def load_case(case_file: Path, case_id: str) -> dict[str, Any]:
    payload = json.loads(case_file.read_text(encoding="utf-8"))
    for item in payload["cases"]:
        if item["case_id"] == case_id:
            return item
    raise ValueError(f"未知用例：{case_id}")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _observe_sources(case: dict[str, Any]) -> dict[str, Any]:
    sources = []
    for source in case["sources"]:
        # SourceManifest 在工具宿主再次按 GoalContract 过滤，不能只相信模型会守范围。
        if source["source_id"] not in case["goal"]["source_scope"]:
            continue
        sources.append(
            {
                "source_id": source["source_id"],
                "name": source["name"],
                "kind": source["kind"],
                "sha256": source["sha256"],
                "available_locators": [
                    {
                        "locator": item["locator"],
                        "title": item["title"],
                        "kind": item["kind"],
                    }
                    for item in source["locators"]
                ],
            }
        )
    return {"sources": sources}


def _read_source(
    case: dict[str, Any],
    *,
    source_id: str,
    locator: str,
) -> dict[str, Any]:
    if source_id not in case["goal"]["source_scope"]:
        raise PermissionError("来源不在 GoalContract 的允许范围内")
    for source in case["sources"]:
        if source["source_id"] != source_id:
            continue
        for item in source["locators"]:
            if item["locator"] == locator:
                delay_seconds = float(item.get("delay_seconds", 0))
                if delay_seconds > 0:
                    # 仅用于取消传播赛马；Supervisor 必须能结束这个休眠中的工具子进程。
                    time.sleep(delay_seconds)
                failure = item.get("failure")
                if failure:
                    error_type = failure.get("type")
                    message = failure.get("message", "来源读取失败")
                    if error_type == "timeout":
                        raise TimeoutError(message)
                    raise RuntimeError(message)
                return {
                    "source_id": source_id,
                    "locator": locator,
                    "title": item["title"],
                    "kind": item["kind"],
                    "content": item["content"],
                    "evidence_ref": f"{source_id}#{locator}",
                }
        raise ValueError("来源中不存在该 locator；请先调用 observe_sources")
    raise PermissionError("来源不存在或当前用户无权访问")


def _submit_candidate(
    case: dict[str, Any],
    run_dir: Path,
    *,
    output_format: str,
    filename: str,
    content: str,
) -> dict[str, Any]:
    expected_format = case["goal"]["output_format"]
    if output_format.lower() != expected_format:
        raise ValueError(f"GoalContract 要求 {expected_format}，不得改成 {output_format}")
    if Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise ValueError("filename 只能是文件名，不能包含路径")
    if not filename.lower().endswith(f".{expected_format}"):
        raise ValueError("文件扩展名与 GoalContract 不一致")
    if len(content) > MAX_CANDIDATE_CHARS:
        raise ValueError("候选内容超过原型预算")
    candidate = {
        "output_format": output_format.lower(),
        "filename": filename,
        "content": content,
    }
    target = run_dir / "candidate.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(candidate, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return {"candidate_ref": str(target), "filename": filename}


def _request_clarification(
    run_dir: Path,
    *,
    question: str,
    options: list[str],
) -> dict[str, Any]:
    if not question.strip():
        raise ValueError("待确认问题不能为空")
    normalized = [item.strip() for item in options if item.strip()]
    if len(normalized) < 2 or len(normalized) > 4:
        raise ValueError("待确认问题必须提供 2 至 4 个真实操作")
    clarification = {"question": question.strip(), "options": normalized}
    target = run_dir / "clarification.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(clarification, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return {"clarification_ref": str(target), **clarification}


def call_tool(
    case: dict[str, Any],
    run_dir: Path,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    # 成功调用和失败尝试分开记录，既保留重规划证据，又不把异常伪装成 Observation。
    attempt = {"tool_name": tool_name, "arguments": arguments}
    try:
        if tool_name == "observe_sources":
            result = _observe_sources(case)
        elif tool_name == "read_source":
            result = _read_source(case, **arguments)
        elif tool_name == "request_clarification":
            result = _request_clarification(run_dir, **arguments)
        elif tool_name == "submit_candidate":
            result = _submit_candidate(case, run_dir, **arguments)
        else:
            raise ValueError(f"工具不在本次 Tool Catalog：{tool_name}")
    except Exception as exc:
        _append_jsonl(
            run_dir / "tool_attempts.jsonl",
            {
                **attempt,
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise
    _append_jsonl(
        run_dir / "tool_attempts.jsonl",
        {**attempt, "status": "completed"},
    )
    _append_jsonl(
        run_dir / "tool_calls.jsonl",
        {"tool_name": tool_name, "arguments": arguments, "result": result},
    )
    if tool_name == "request_clarification":
        # 先固化工具调用证据，再通知统一 Supervisor 执行硬暂停。
        (run_dir / "control.json").write_text(
            json.dumps(
                {"action": "pause_for_clarification"},
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    return result


def _normalized_csv(content: str) -> tuple[list[str], list[list[str]]]:
    rows = list(csv.reader(io.StringIO(content)))
    if not rows:
        return [], []
    return [cell.strip() for cell in rows[0]], [
        [cell.strip() for cell in row] for row in rows[1:] if any(cell.strip() for cell in row)
    ]


def verify_candidate(case: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    expected = case["expected"]
    candidate_path = run_dir / "candidate.json"
    errors: list[str] = []
    tool_log = run_dir / "tool_calls.jsonl"
    tool_calls = (
        [
            json.loads(line)
            for line in tool_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if tool_log.is_file()
        else []
    )
    tool_names = [item.get("tool_name") for item in tool_calls]
    attempt_log = run_dir / "tool_attempts.jsonl"
    tool_attempts = (
        [
            json.loads(line)
            for line in attempt_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if attempt_log.is_file()
        else []
    )
    if expected.get("outcome") == "clarification":
        clarification_path = run_dir / "clarification.json"
        if candidate_path.is_file():
            errors.append("目标未澄清前生成了候选产物")
        if not clarification_path.is_file():
            errors.append("没有生成可操作的待确认问题")
            clarification = {"question": "", "options": []}
        else:
            clarification = json.loads(clarification_path.read_text(encoding="utf-8"))
        option_text = "\n".join(clarification.get("options", []))
        for snippet in expected.get("required_option_snippets", []):
            if snippet not in option_text:
                errors.append(f"待确认操作缺少：{snippet}")
        if not tool_names or tool_names[0] != "observe_sources":
            errors.append("提出问题前没有先观察来源")
        if tool_names.count("request_clarification") != 1:
            errors.append("request_clarification 必须且只能调用一次")
        if "submit_candidate" in tool_names:
            errors.append("待确认状态不得提交候选产物")
        result = {
            "passed": not errors,
            "errors": errors,
            "candidate_ref": None,
            "clarification_ref": (
                str(clarification_path) if clarification_path.is_file() else None
            ),
        }
        (run_dir / "verification.json").write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        return result
    if not candidate_path.is_file():
        return {"passed": False, "errors": ["没有候选产物"], "candidate_ref": None}
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    if not tool_names or tool_names[0] != "observe_sources":
        errors.append("首个工具调用不是 observe_sources")
    if tool_names.count("observe_sources") != 1:
        errors.append("observe_sources 必须且只能调用一次")
    if tool_names.count("submit_candidate") != 1:
        errors.append("submit_candidate 必须且只能调用一次")
    elif tool_names[-1] != "submit_candidate":
        errors.append("submit_candidate 必须是最后一个工具调用")
    # 不能只验最终文件：先探测 Schema、避开非目标来源同样属于业务正确性。
    read_locators = [
        item["arguments"].get("locator")
        for item in tool_calls
        if item.get("tool_name") == "read_source"
    ]
    required_locators = expected.get("required_read_locators", [])
    cursor = 0
    for locator in required_locators:
        try:
            cursor = read_locators.index(locator, cursor) + 1
        except ValueError:
            errors.append(f"缺少或乱序的必要读取：{locator}")
    for locator in expected.get("forbidden_read_locators", []):
        if locator in read_locators:
            errors.append(f"读取了明确禁止的非目标位置：{locator}")
    failed_locators = [
        item["arguments"].get("locator")
        for item in tool_attempts
        if item.get("tool_name") == "read_source"
        and item.get("status") == "failed"
    ]
    for locator in expected.get("required_failed_locators", []):
        count = failed_locators.count(locator)
        if count == 0:
            errors.append(f"没有覆盖预期工具故障：{locator}")
        elif count > 1:
            errors.append(f"相同失败指纹重复空转：{locator}，共 {count} 次")
    if candidate["output_format"] != case["goal"]["output_format"]:
        errors.append("输出格式不符")
    content = candidate["content"]
    for forbidden in case["goal"].get("must_not_include", []):
        if forbidden in content:
            errors.append(f"包含明确禁止内容：{forbidden}")
    if candidate["output_format"] == "csv":
        columns, rows = _normalized_csv(content)
        if columns != expected["columns"]:
            errors.append(f"列不符：{columns}")
        if rows != expected["rows"]:
            errors.append(f"数据行不符：{rows}")
        if "total" in expected and rows:
            try:
                total_index = columns.index("费用合计")
                actual_total = str(sum(int(row[total_index]) for row in rows))
                if actual_total != expected["total"]:
                    errors.append(f"费用合计不符：{actual_total}")
            except (ValueError, IndexError):
                errors.append("无法复核费用合计")
    else:
        for snippet in expected["required_snippets"]:
            if snippet not in content:
                errors.append(f"缺少必要内容：{snippet}")
        for snippet in expected["forbidden_snippets"]:
            if snippet in content:
                errors.append(f"照搬非目标正文：{snippet}")
    result = {
        "passed": not errors,
        "errors": errors,
        "candidate_ref": str(candidate_path),
    }
    (run_dir / "verification.json").write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-file", required=True, type=Path)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("command", choices=("call", "verify"))
    parser.add_argument("tool_name", nargs="?")
    args = parser.parse_args()
    case = load_case(args.case_file, args.case_id)
    try:
        if args.command == "verify":
            result = verify_candidate(case, args.run_dir)
        else:
            if not args.tool_name:
                raise ValueError("call 命令缺少 tool_name")
            raw = sys.stdin.read()
            arguments = json.loads(raw) if raw.strip() else {}
            result = call_tool(case, args.run_dir, args.tool_name, arguments)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"error_type": type(exc).__name__, "error": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
