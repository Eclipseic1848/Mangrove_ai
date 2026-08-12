#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""在同一固定黄金集上比较 MinerU 与 Paddle 完整文档解析服务。

比较对象：
- MinerU 3.4.4 pipeline
- MinerU 3.4.4 hybrid-engine / medium
- MinerU 3.4.4 hybrid-engine / high
- PaddleOCR-VL 1.6 完整 /layout-parsing Pipeline

本脚本只评估解析层，不调用字段候选 LLM，避免把语义模型差异混入 OCR/版面结果。
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import unicodedata
from collections import defaultdict
from dataclasses import asdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

import httpx
from jiwer import cer, wer
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.generate_document_golden import _case, _page_lines  # noqa: E402
from src.services.document_parser_contracts import DocumentPageBlock  # noqa: E402
from src.services.mineru_document import MinerUDocumentClient  # noqa: E402
from src.services.paddleocr_vl_document import PaddleOCRVLDocumentClient  # noqa: E402

DEFAULT_GOLDEN = PROJECT_ROOT / "tests" / "fixtures" / "document_golden"
PILOT_IDS = {
    "contract_05_scanned",
    "bid_04_scanned",
    "invoice_05_mixed",
}


def _compact_text(value: str) -> str:
    """归一 Unicode、大小写、空白和常见 Markdown 标记，用于内容覆盖比较。"""
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    ignored = set(" \t\r\n#*`|_:：")
    return "".join(char for char in normalized if char not in ignored)


def _plain_block_text(value: str) -> str:
    """把 Paddle 表格 HTML 转成可与页面原文比较的纯文本。"""
    if "<" not in value or ">" not in value:
        return value
    return BeautifulSoup(value, "html.parser").get_text(" ", strip=True)


def _expected_pages(document: dict[str, Any]) -> dict[int, list[str]]:
    case = _case(document["domain"], int(document["id"].split("_")[1]))
    return {
        page: _page_lines(case, page)
        for page in range(1, int(document["pages"]) + 1)
    }


def _group_blocks(
    blocks: Iterable[DocumentPageBlock],
) -> dict[int, list[DocumentPageBlock]]:
    result: dict[int, list[DocumentPageBlock]] = defaultdict(list)
    for block in blocks:
        result[int(block.page)].append(block)
    return dict(result)


def _document_metrics(
    document: dict[str, Any],
    blocks: Iterable[DocumentPageBlock],
    *,
    elapsed_seconds: float,
) -> dict[str, Any]:
    block_list = list(blocks)
    by_page = _group_blocks(block_list)
    expected_pages = _expected_pages(document)
    expected_lines = [
        line
        for page in expected_pages.values()
        for line in page
    ]
    actual_text_by_page = {
        page: "\n".join(
            _plain_block_text(block.text)
            for block in items
            if block.text.strip()
        )
        for page, items in by_page.items()
    }
    actual_all = "\n".join(
        actual_text_by_page.get(page, "")
        for page in sorted(expected_pages)
    )
    expected_all = "\n".join(
        line
        for page in sorted(expected_pages)
        for line in expected_pages[page]
    )
    compact_actual = _compact_text(actual_all)
    compact_expected = _compact_text(expected_all)
    matched_lines = sum(
        _compact_text(line) in compact_actual
        for line in expected_lines
    )
    expected_values = [
        item["value"]
        for item in document["expected_fields"].values()
    ]
    matched_values = [
        value for value in expected_values
        if _compact_text(value) in compact_actual
    ]
    table_lines = [
        line
        for page in (4, 5)
        for line in expected_pages[page]
        if line.startswith("Row ")
    ]
    matched_table_lines = sum(
        _compact_text(line) in compact_actual
        for line in table_lines
    )
    bbox_blocks = [block for block in block_list if block.bbox is not None]
    coordinate_spaces = sorted({
        block.coordinate_space for block in bbox_blocks
    })
    return {
        "expected_pages": len(expected_pages),
        "parsed_pages": sorted(by_page),
        "page_coverage": len(set(by_page) & set(expected_pages)) / len(expected_pages),
        "blocks": len(block_list),
        "bbox_blocks": len(bbox_blocks),
        "bbox_rate": len(bbox_blocks) / len(block_list) if block_list else 0.0,
        "coordinate_spaces": coordinate_spaces,
        "expected_lines": len(expected_lines),
        "matched_lines": matched_lines,
        "line_recall": matched_lines / len(expected_lines) if expected_lines else 1.0,
        "expected_values": expected_values,
        "matched_values": matched_values,
        "field_value_recall": len(matched_values) / len(expected_values),
        "expected_table_rows": len(table_lines),
        "matched_table_rows": matched_table_lines,
        "table_row_recall": (
            matched_table_lines / len(table_lines) if table_lines else 1.0
        ),
        "normalized_cer": cer(compact_expected, compact_actual),
        "normalized_wer": wer(
        " ".join(_compact_text(line) for line in expected_lines),
            " ".join(
                _compact_text(_plain_block_text(block.text))
                for block in block_list
            ),
        ),
        "sequence_similarity": SequenceMatcher(
            None, compact_expected, compact_actual, autojunk=False
        ).ratio(),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "sample_blocks": [asdict(block) for block in block_list[:3]],
    }


def _parse_mineru(
    raw: bytes,
    *,
    filename: str,
    base_url: str,
    backend: str,
    effort: str | None,
    timeout: float,
) -> tuple[list[DocumentPageBlock], dict[str, Any]]:
    data = {
        "backend": backend,
        "parse_method": "ocr",
        "lang_list": "ch",
        "formula_enable": "true",
        "table_enable": "true",
        "image_analysis": "true" if effort == "high" else "false",
        "return_md": "true",
        "return_middle_json": "true",
        "return_model_output": "true",
        "return_content_list": "true",
        "return_images": "false",
        "response_format_zip": "false",
    }
    if effort:
        data["effort"] = effort
    with httpx.Client(
        timeout=httpx.Timeout(timeout, connect=10.0),
        trust_env=False,
    ) as client:
        response = client.post(
            f"{base_url.rstrip('/')}/file_parse",
            files={"files": (filename, raw, "application/pdf")},
            data=data,
        )
        if response.is_error:
            raise RuntimeError(
                f"MinerU HTTP {response.status_code}: {response.text[:2000]}"
            )
        payload = response.json()
    parsed = MinerUDocumentClient(
        base_url=base_url,
        backend=backend,
    ).parse_response(payload)
    return list(parsed.blocks), {
        "provider": parsed.provider,
        "backend": parsed.backend,
        "version": parsed.version,
        "task_id": parsed.task_id,
    }


def _parse_paddle(
    raw: bytes,
    *,
    base_url: str,
    timeout: float,
) -> tuple[list[DocumentPageBlock], dict[str, Any]]:
    payload = {
        "file": base64.b64encode(raw).decode("ascii"),
        "fileType": 0,
        "useLayoutDetection": True,
        "useSealRecognition": True,
        "useOcrForImageBlock": True,
        "formatBlockContent": True,
        "temperature": 0,
        "prettifyMarkdown": True,
        "returnMarkdownImages": False,
        "restructurePages": False,
        "visualize": False,
    }
    with httpx.Client(
        timeout=httpx.Timeout(timeout, connect=10.0),
        trust_env=False,
    ) as client:
        response = client.post(
            f"{base_url.rstrip('/')}/layout-parsing",
            json=payload,
        )
        if response.is_error:
            raise RuntimeError(
                f"Paddle HTTP {response.status_code}: {response.text[:2000]}"
            )
        raw_response = response.json()
    parsed = PaddleOCRVLDocumentClient(
        base_url=base_url,
        model_version="PaddleOCR-VL-1.6-0.9B",
    ).parse_response(raw_response)
    return list(parsed.blocks), {
        "provider": parsed.provider,
        "backend": parsed.backend,
        "version": parsed.version,
        "task_id": parsed.task_id,
    }


def _backends(args: argparse.Namespace):
    return {
        "mineru_pipeline": lambda raw, filename: _parse_mineru(
            raw,
            filename=filename,
            base_url=args.mineru_url,
            backend="pipeline",
            effort=None,
            timeout=args.timeout,
        ),
        "mineru_hybrid_medium": lambda raw, filename: _parse_mineru(
            raw,
            filename=filename,
            base_url=args.mineru_url,
            backend="hybrid-engine",
            effort="medium",
            timeout=args.timeout,
        ),
        "mineru_hybrid_high": lambda raw, filename: _parse_mineru(
            raw,
            filename=filename,
            base_url=args.mineru_url,
            backend="hybrid-engine",
            effort="high",
            timeout=args.timeout,
        ),
        "paddle_layout_parsing": lambda raw, filename: _parse_paddle(
            raw,
            base_url=args.paddle_url,
            timeout=args.timeout,
        ),
    }


def _summarize(documents: list[dict[str, Any]]) -> dict[str, Any]:
    successes = [item for item in documents if "metrics" in item]
    totals = {
        "documents": len(documents),
        "successes": len(successes),
        "errors": len(documents) - len(successes),
        "expected_pages": sum(
            item["metrics"]["expected_pages"] for item in successes
        ),
        "parsed_pages": sum(
            len(item["metrics"]["parsed_pages"]) for item in successes
        ),
        "expected_values": sum(
            len(item["metrics"]["expected_values"]) for item in successes
        ),
        "matched_values": sum(
            len(item["metrics"]["matched_values"]) for item in successes
        ),
        "expected_lines": sum(
            item["metrics"]["expected_lines"] for item in successes
        ),
        "matched_lines": sum(
            item["metrics"]["matched_lines"] for item in successes
        ),
        "expected_table_rows": sum(
            item["metrics"]["expected_table_rows"] for item in successes
        ),
        "matched_table_rows": sum(
            item["metrics"]["matched_table_rows"] for item in successes
        ),
        "blocks": sum(item["metrics"]["blocks"] for item in successes),
        "bbox_blocks": sum(item["metrics"]["bbox_blocks"] for item in successes),
        "elapsed_seconds": round(sum(
            item["metrics"]["elapsed_seconds"] for item in successes
        ), 3),
    }
    totals.update({
        "page_coverage": (
            totals["parsed_pages"] / totals["expected_pages"]
            if totals["expected_pages"] else 0.0
        ),
        "field_value_recall": (
            totals["matched_values"] / totals["expected_values"]
            if totals["expected_values"] else 0.0
        ),
        "line_recall": (
            totals["matched_lines"] / totals["expected_lines"]
            if totals["expected_lines"] else 0.0
        ),
        "table_row_recall": (
            totals["matched_table_rows"] / totals["expected_table_rows"]
            if totals["expected_table_rows"] else 0.0
        ),
        "bbox_rate": (
            totals["bbox_blocks"] / totals["blocks"]
            if totals["blocks"] else 0.0
        ),
        "mean_normalized_cer": (
            sum(item["metrics"]["normalized_cer"] for item in successes)
            / len(successes) if successes else None
        ),
        "mean_sequence_similarity": (
            sum(item["metrics"]["sequence_similarity"] for item in successes)
            / len(successes) if successes else None
        ),
    })
    totals["weighted_score"] = round(
        0.30 * totals["field_value_recall"]
        + 0.25 * totals["line_recall"]
        + 0.15 * totals["table_row_recall"]
        + 0.15 * totals["page_coverage"]
        + 0.10 * totals["bbox_rate"]
        + 0.05 * (totals["mean_sequence_similarity"] or 0.0),
        6,
    )
    return totals


def run(args: argparse.Namespace) -> dict[str, Any]:
    manifest = json.loads(
        (args.golden_dir / "expected.json").read_text(encoding="utf-8")
    )
    selected = [
        item for item in manifest["documents"]
        if item["mode"] in {"scanned", "mixed"} or item["domain"] == "invoice"
    ]
    if args.suite == "pilot":
        selected = [item for item in selected if item["id"] in PILOT_IDS]
    if args.limit:
        selected = selected[:args.limit]

    results: dict[str, list[dict[str, Any]]] = {}
    started = time.perf_counter()
    available_backends = _backends(args)
    requested_backends = {
        item.strip()
        for item in args.backends.split(",")
        if item.strip()
    }
    unknown_backends = requested_backends - set(available_backends)
    if unknown_backends:
        raise ValueError(f"未知解析后端: {sorted(unknown_backends)}")
    selected_backends = (
        {
            name: parse
            for name, parse in available_backends.items()
            if name in requested_backends
        }
        if requested_backends
        else available_backends
    )
    for backend_name, parse in selected_backends.items():
        backend_results = []
        print(
            f"[START] {backend_name}: {len(selected)} documents",
            flush=True,
        )
        for index, document in enumerate(selected, start=1):
            raw = (args.golden_dir / document["file"]).read_bytes()
            item: dict[str, Any] = {
                "id": document["id"],
                "file": document["file"],
                "domain": document["domain"],
                "mode": document["mode"],
            }
            call_started = time.perf_counter()
            try:
                blocks, service = parse(raw, document["file"])
                item["service"] = service
                item["metrics"] = _document_metrics(
                    document,
                    blocks,
                    elapsed_seconds=time.perf_counter() - call_started,
                )
                print(
                    f"[PASS] {backend_name} {index}/{len(selected)} "
                    f"{document['id']} score_fields="
                    f"{item['metrics']['field_value_recall']:.3f} "
                    f"lines={item['metrics']['line_recall']:.3f} "
                    f"seconds={item['metrics']['elapsed_seconds']:.3f}",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001 - A/B 必须保留单项错误继续
                item["error"] = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "elapsed_seconds": round(
                        time.perf_counter() - call_started, 3
                    ),
                }
                print(
                    f"[FAIL] {backend_name} {index}/{len(selected)} "
                    f"{document['id']}: {type(exc).__name__}: {exc}",
                    flush=True,
                )
            backend_results.append(item)
        results[backend_name] = backend_results
        print(
            f"[SUMMARY] {backend_name}: "
            f"{json.dumps(_summarize(backend_results), ensure_ascii=False)}",
            flush=True,
        )

    summaries = {
        name: _summarize(items)
        for name, items in results.items()
    }
    ranking = sorted(
        summaries,
        key=lambda name: (
            summaries[name]["errors"] == 0,
            summaries[name]["weighted_score"],
            -summaries[name]["elapsed_seconds"],
        ),
        reverse=True,
    )
    return {
        "suite": args.suite,
        "golden_dir": str(args.golden_dir),
        "documents": [item["id"] for item in selected],
        "services": {
            "mineru": args.mineru_url,
            "paddle": args.paddle_url,
        },
        "summaries": summaries,
        "ranking": ranking,
        "details": results,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--suite", choices=("pilot", "full"), default="pilot")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--mineru-url", default="http://192.168.1.21:8000")
    parser.add_argument("--paddle-url", default="http://192.168.1.21:18081")
    parser.add_argument(
        "--backends",
        default="",
        help="逗号分隔的后端名；留空比较全部后端",
    )
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    args.golden_dir = args.golden_dir.resolve()

    report = run(args)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0 if all(
        summary["errors"] == 0
        for summary in report["summaries"].values()
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
