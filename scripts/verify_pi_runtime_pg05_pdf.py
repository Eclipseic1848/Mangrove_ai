# -*- coding: utf-8 -*-
"""用真实上传 PDF 验证 PG-05 的“附件表格 → 单一 CSV”P0 场景。"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile

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
    return "".join(str(value or "").replace(",", "").split())


def _assert_expected_csv(path: Path) -> None:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    flattened = "\n".join(
        "\t".join(_normalized(cell) for cell in row)
        for row in rows
    )
    expected = (
        ("潘英豪", "L3", "8028"),
        ("王嘉飞", "L4", "700"),
        ("王嘉飞", "L2", "6504"),
    )
    for values in expected:
        if not all(value in flattened for value in values):
            raise AssertionError(f"CSV 缺少真实附件明细：{values}")
    if "15232" not in flattened:
        raise AssertionError("CSV 缺少附件表格的 15,232 合计")
    forbidden = ("违约责任", "保密协议", "甲方的权利")
    if any(value in flattened for value in forbidden):
        raise AssertionError("CSV 混入了附件 2 之外的合同正文")


async def _run_once(
    *,
    source: Path,
    metadata: dict,
    run_number: int,
) -> None:
    root = PROJECT_ROOT / ".pytest-tmp" / "pi-runtime-pg05-pdf"
    root.mkdir(parents=True, exist_ok=True)
    execution_root = Path(
        tempfile.mkdtemp(prefix=f"run-{run_number}-", dir=root)
    )
    request = PiRuntimeRequest(
        user_id=str(metadata["user_id"]),
        task_id=f"pg05_pdf_attachment_2_{run_number}",
        revision=1,
        objective_text=(
            "将附件2中的【服务费用标准及明细】抽取出来，输出一张单独的表，"
            "以CSV格式输出，其余的内容我都不要。"
        ),
        requested_output_formats=("csv",),
        sources=(
            SourceInput(
                upload_id=str(metadata["upload_id"]),
                original_name=str(metadata["original_name"]),
                host_path=source,
                sha256=str(metadata["sha256"]),
                media_type=str(metadata.get("media_type") or "application/pdf"),
            ),
        ),
        permission_profile=PermissionProfile.STANDARD,
        model=settings.llm_model_name,
        base_url=settings.llm_base_url,
        api_key="local-runtime",
    )

    async def event_sink(event: RuntimeEvent) -> None:
        print(
            f"[{run_number}] {event.event_type}: {event.summary}",
            flush=True,
        )

    result = await PiRuntime(
        execution_root=execution_root,
        timeout_seconds=600,
    ).start(request, on_event=event_sink)
    if len(result.candidates) != 1:
        raise AssertionError(
            f"必须只生成一张 CSV，实际 {len(result.candidates)} 个候选"
        )
    candidate = result.candidates[0]
    if candidate.format != "csv":
        raise AssertionError(f"候选格式不是 CSV：{candidate.format}")
    _assert_expected_csv(candidate.host_path)
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
        f"[{run_number}] PASS: {candidate.host_path}",
        flush=True,
    )


async def _main(upload_id: str, repeat: int) -> None:
    source, metadata = _resolve_upload(upload_id)
    for run_number in range(1, repeat + 1):
        await _run_once(
            source=source,
            metadata=metadata,
            run_number=run_number,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--upload-id", required=True)
    parser.add_argument("--repeat", type=int, default=1, choices=range(1, 4))
    args = parser.parse_args()
    asyncio.run(_main(args.upload_id, args.repeat))
