# -*- coding: utf-8 -*-
"""用真实 Word/Excel 上传对象验证 PG-05 的跨格式泛化能力。"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import unicodedata

from docx import Document
from openpyxl import load_workbook

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agentic_runtime.models import (
    PermissionProfile,
    PiRuntimeRequest,
    RuntimeEvent,
    SourceInput,
    VerificationStatus,
)
from src.agentic_runtime.pi_runtime import PiRuntime
from src.config.settings import settings


def _resolve_upload(upload_id: str) -> tuple[Path, dict]:
    matches = list(
        (PROJECT_ROOT / settings.data_prep_upload_root).glob(
            f"*/objects/{upload_id}.meta"
        )
    )
    if len(matches) != 1:
        raise ValueError(
            f"upload_id 应唯一命中一个本地上传对象，实际 {len(matches)} 个"
        )
    meta_path = matches[0]
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    source = meta_path.with_suffix("")
    if not source.is_file():
        raise FileNotFoundError(source)
    actual = hashlib.sha256(source.read_bytes()).hexdigest()
    if actual != metadata["sha256"]:
        raise ValueError("真实上传对象与元数据哈希不一致")
    return source, metadata


def _normalized(value: object) -> str:
    return re.sub(
        r"\s+",
        "",
        unicodedata.normalize("NFKC", str(value or "")),
    )


def _word_source_text(source: Path) -> str:
    document = Document(source)
    parts = [
        paragraph.text.strip()
        for paragraph in document.paragraphs
        if paragraph.text.strip()
    ]
    for table in document.tables:
        for row in table.rows:
            parts.extend(
                cell.text.strip()
                for cell in row.cells
                if cell.text.strip()
            )
    return "\n".join(parts)


def _assert_word_summary(candidate: Path, source: Path) -> None:
    content = candidate.read_text(encoding="utf-8-sig")
    source_text = _word_source_text(source)
    required_categories = (
        "费用",
        "服务",
        "验收",
        "保密",
        "知识产权",
        "违约",
    )
    missing = [
        category
        for category in required_categories
        if category not in content
    ]
    if missing:
        raise AssertionError(f"TXT 缺少商务条款类别：{missing}")
    if len(content.strip()) < 800:
        raise AssertionError("TXT 过短，未形成可用的商务条款汇总")
    # 这是防止“把整份 Word 原封不动转成 TXT”的独立验收门，不影响摘要写法。
    if len(content) >= len(source_text) * 0.75:
        raise AssertionError("TXT 接近全文长度，疑似整份文档照搬")


def _expected_excel_rows(source: Path) -> Counter[tuple[str, str]]:
    with source.open("rb") as handle:
        workbook = load_workbook(handle, read_only=True, data_only=True)
        try:
            worksheet = workbook["工作表2"]
            rows = Counter()
            for row in worksheet.iter_rows(min_row=2, values_only=True):
                if _normalized(row[5] if len(row) > 5 else "") != "1月":
                    continue
                scene = _normalized(row[4] if len(row) > 4 else "")
                maturity = _normalized(row[6] if len(row) > 6 else "")
                if scene:
                    rows[(scene, maturity)] += 1
            return rows
        finally:
            workbook.close()


def _candidate_excel_rows(candidate: Path) -> Counter[tuple[str, str]]:
    with candidate.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        raise AssertionError("CSV 是空文件")
    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if any(
                "应用场景" in _normalized(cell)
                and "三级" in _normalized(cell)
                for cell in row
            )
            and any("目标成熟度" in _normalized(cell) for cell in row)
        ),
        None,
    )
    if header_index is None:
        raise AssertionError("CSV 缺少约定的两列表头")
    header = [_normalized(cell) for cell in rows[header_index]]
    scene_index = next(
        index
        for index, cell in enumerate(header)
        if "应用场景" in cell and "三级" in cell
    )
    maturity_index = next(
        index
        for index, cell in enumerate(header)
        if "目标成熟度" in cell
    )
    actual: Counter[tuple[str, str]] = Counter()
    for row in rows[header_index + 1 :]:
        scene = _normalized(
            row[scene_index] if scene_index < len(row) else ""
        )
        maturity = _normalized(
            row[maturity_index] if maturity_index < len(row) else ""
        )
        if scene:
            actual[(scene, maturity)] += 1
    return actual


def _assert_excel_filter(candidate: Path, source: Path) -> None:
    expected = _expected_excel_rows(source)
    actual = _candidate_excel_rows(candidate)
    if actual != expected:
        missing = list((expected - actual).elements())[:5]
        extra = list((actual - expected).elements())[:5]
        raise AssertionError(
            "CSV 与真实工作表筛选结果不一致："
            f"期望 {sum(expected.values())} 行，实际 {sum(actual.values())} 行；"
            f"缺少 {missing}；多出 {extra}"
        )


def _case_contract(case: str) -> tuple[str, str]:
    if case == "word":
        return (
            "按费用、服务、验收、保密、知识产权、违约六类，提取并汇总"
            "这个 Word 里的商务条款，输出一个 TXT；不要复制整份文档。",
            "txt",
        )
    return (
        "只读取 Excel 的【工作表2】，筛选【计划交付月份】为【1月】的记录，"
        "只输出【应用场景（三级）】和【目标成熟度】两列，生成一个 CSV；"
        "其他工作表、其他月份和说明文字都不要。",
        "csv",
    )


async def _run_once(
    *,
    case: str,
    source: Path,
    metadata: dict,
    run_number: int,
) -> None:
    objective, output_format = _case_contract(case)
    root = PROJECT_ROOT / ".pytest-tmp" / f"pi-runtime-pg05-{case}"
    root.mkdir(parents=True, exist_ok=True)
    execution_root = Path(
        tempfile.mkdtemp(prefix=f"run-{run_number}-", dir=root)
    )
    request = PiRuntimeRequest(
        user_id=str(metadata["user_id"]),
        task_id=f"pg05_{case}_{run_number}",
        revision=1,
        objective_text=objective,
        requested_output_formats=(output_format,),
        sources=(
            SourceInput(
                upload_id=str(metadata["upload_id"]),
                original_name=str(metadata["original_name"]),
                host_path=source,
                sha256=str(metadata["sha256"]),
                media_type=str(
                    metadata.get("media_type")
                    or "application/octet-stream"
                ),
            ),
        ),
        permission_profile=PermissionProfile.STANDARD,
        model=settings.llm_model_name,
        base_url=settings.llm_base_url,
        api_key="local-runtime",
    )

    async def event_sink(event: RuntimeEvent) -> None:
        print(
            f"[{case}:{run_number}] "
            f"{event.event_type}: {event.summary}",
            flush=True,
        )

    result = await PiRuntime(
        execution_root=execution_root,
        timeout_seconds=600,
    ).start(request, on_event=event_sink)
    if len(result.candidates) != 1:
        raise AssertionError(
            f"必须只生成一个 {output_format.upper()}，"
            f"实际 {len(result.candidates)} 个候选"
        )
    candidate = result.candidates[0]
    if candidate.format != output_format:
        raise AssertionError(
            f"候选格式不是 {output_format.upper()}：{candidate.format}"
        )
    if case == "word":
        _assert_word_summary(candidate.host_path, source)
    else:
        _assert_excel_filter(candidate.host_path, source)
    if (
        result.verification is None
        or result.verification.status is not VerificationStatus.PASSED
    ):
        raise AssertionError(
            "独立验证未通过："
            + (
                result.verification.model_dump_json()
                if result.verification
                else "无验证报告"
            )
        )
    print(
        f"[{case}:{run_number}] PASS: {candidate.host_path}",
        flush=True,
    )


async def _main(case: str, upload_id: str, repeat: int) -> None:
    source, metadata = _resolve_upload(upload_id)
    for run_number in range(1, repeat + 1):
        await _run_once(
            case=case,
            source=source,
            metadata=metadata,
            run_number=run_number,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True, choices=("word", "excel"))
    parser.add_argument("--upload-id", required=True)
    parser.add_argument("--repeat", type=int, default=1, choices=range(1, 4))
    args = parser.parse_args()
    asyncio.run(_main(args.case, args.upload_id, args.repeat))
