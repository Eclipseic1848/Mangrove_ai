# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from collections.abc import Sequence

from markdown_it import MarkdownIt


SCHEMA_VERSION = "phase4b-8b1-check/v1"
_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_CHECK_ID_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9_-]{0,95}\Z")
_STATUSES = {"passed", "failed", "not_run", "pending_8b2"}


class ReportError(ValueError):
    """结构化验收结果不可信。"""


@dataclass(frozen=True)
class AcceptanceSummary:
    run_id: str
    status: str
    checks: tuple[dict[str, object], ...]
    pending_8b2: int


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_evidence(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReportError("evidence 必须是非空相对路径")
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if (
        normalized.startswith("/")
        or PureWindowsPath(value).is_absolute()
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ReportError("evidence 只能使用报告目录内的相对路径")
    return normalized


def _normalize_check(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ReportError("check 必须是对象")
    check_id = raw.get("check_id")
    status = raw.get("status")
    summary = raw.get("summary")
    evidence = raw.get("evidence")
    remediation = raw.get("remediation")
    if not isinstance(check_id, str) or _CHECK_ID_PATTERN.fullmatch(check_id) is None:
        raise ReportError("check_id 非法")
    if status not in _STATUSES:
        raise ReportError("check status 非法")
    if not isinstance(summary, str) or not summary.strip():
        raise ReportError("check summary 不能为空")
    if not isinstance(evidence, list):
        raise ReportError("evidence 必须是数组")
    if remediation is not None and not isinstance(remediation, str):
        raise ReportError("remediation 必须是文本或 null")
    return {
        "check_id": check_id,
        "status": status,
        "summary": summary.strip(),
        "evidence": [_safe_evidence(item) for item in evidence],
        "remediation": remediation.strip() if isinstance(remediation, str) else None,
    }


def _markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _render_markdown(summary: AcceptanceSummary) -> str:
    lines = [
        "# G5 本机前置验收报告",
        "",
        f"- Run ID：`{summary.run_id}`",
        f"- 本机前置包状态：`{summary.status}`",
        f"- 待 8B-2 项：`{summary.pending_8b2}`",
        "- 边界：这里只证明目标服务器验收前的本机前置条件；不代表完整 8B-1 或目标服务器 8B-2 通过。",
        "",
        "| 检查 | 状态 | 结论 | 证据 | 处理建议 |",
        "|---|---|---|---|---|",
    ]
    for check in summary.checks:
        evidence = ", ".join(f"`{item}`" for item in check["evidence"]) or "—"
        remediation = check["remediation"] or "—"
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(item)
                for item in (
                    check["check_id"],
                    check["status"],
                    check["summary"],
                    evidence,
                    remediation,
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 8B-2 边界",
            "",
            "目标 Linux 服务器、GPU、生产并发、长期运行、RAID 和灾难恢复必须在目标服务器单独验收。",
            "",
        ]
    )
    return "\n".join(lines)


def _render_html(markdown: str) -> str:
    body = MarkdownIt("commonmark", {"html": False}).enable("table").render(markdown)
    return (
        "<!doctype html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>G5 本机前置验收报告</title><style>"
        "body{font-family:system-ui,sans-serif;max-width:1120px;margin:32px auto;padding:0 20px;line-height:1.6}"
        "table{border-collapse:collapse;width:100%}th,td{border:1px solid #d0d7de;padding:8px;text-align:left}"
        "code{background:#f6f8fa;padding:2px 4px;border-radius:4px}"
        "</style></head><body>"
        f"{body}</body></html>\n"
    )


def build_report(
    result_files: Sequence[Path],
    *,
    output_markdown: Path,
    output_html: Path,
) -> AcceptanceSummary:
    checks: list[dict[str, object]] = []
    run_id: str | None = None
    seen: set[str] = set()
    for index, result_path in enumerate(result_files, start=1):
        path = Path(result_path)
        if not path.is_file():
            missing = {
                "check_id": f"RESULT-FILE-{index:03d}",
                "status": "failed",
                "summary": "结构化结果文件缺失",
                "evidence": [],
                "remediation": "重新执行对应验收步骤",
            }
            checks.append(missing)
            seen.add(str(missing["check_id"]))
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ReportError("结构化结果无法读取") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            raise ReportError("结构化结果 schema_version 非法")
        candidate_run_id = payload.get("run_id")
        if (
            not isinstance(candidate_run_id, str)
            or _RUN_ID_PATTERN.fullmatch(candidate_run_id) is None
        ):
            raise ReportError("run_id 非法")
        if run_id is None:
            run_id = candidate_run_id
        elif run_id != candidate_run_id:
            raise ReportError("多个结果的 run_id 不一致")
        raw_checks = payload.get("checks")
        if not isinstance(raw_checks, list) or not raw_checks:
            raise ReportError("结构化结果缺少 checks")
        for raw in raw_checks:
            check = _normalize_check(raw)
            check_id = str(check["check_id"])
            if check_id in seen:
                raise ReportError("check_id 重复")
            seen.add(check_id)
            checks.append(check)
    if not checks:
        raise ReportError("没有可聚合的验收结果")
    effective_run_id = run_id or "unknown-run"
    status = (
        "failed"
        if any(check["status"] in {"failed", "not_run"} for check in checks)
        else "passed"
    )
    summary = AcceptanceSummary(
        run_id=effective_run_id,
        status=status,
        checks=tuple(checks),
        pending_8b2=sum(check["status"] == "pending_8b2" for check in checks),
    )
    markdown = _render_markdown(summary)
    _write_text(output_markdown, markdown)
    _write_text(output_html, _render_html(markdown))
    return summary
