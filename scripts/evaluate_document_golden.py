#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""评测 Phase 4A 固定黄金集的页路由、数字文本和可选 Qwen 视觉覆盖。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data_prep.models import RawArtifact  # noqa: E402
from src.parsers.pdf import PdfParser  # noqa: E402
from src.parsers.pdf_render import render_pdf_page_png  # noqa: E402
from src.services.qwen_document import QwenDocumentClient  # noqa: E402

DEFAULT_GOLDEN = PROJECT_ROOT / "tests" / "fixtures" / "document_golden"


def _artifact(document: dict, raw: bytes) -> RawArtifact:
    digest = hashlib.sha256(raw).hexdigest()
    return RawArtifact(
        artifact_id="golden-" + digest[:16], source_id="golden", task_id="phase4a-poc",
        uri=document["file"], media_type="application/pdf", size_bytes=len(raw),
        sha256=digest, storage_path=document["file"],
    )


def evaluate(golden_dir: Path, qwen_limit: int) -> dict:
    manifest = json.loads((golden_dir / "expected.json").read_text(encoding="utf-8"))
    parser = PdfParser()
    route_total = route_correct = 0
    digital_field_total = digital_field_correct = 0
    qwen_docs = qwen_field_total = qwen_field_correct = 0
    qwen_client = QwenDocumentClient() if qwen_limit else None
    started = time.perf_counter()
    try:
        for document in manifest["documents"]:
            raw = (golden_dir / document["file"]).read_bytes()
            records, rejects = parser.parse(_artifact(document, raw), raw)
            observed = {
                item.meta["position"]["page"]: item.meta["page_kind"] for item in records
            }
            observed.update({item["position"]["page"]: item["page_kind"] for item in rejects})
            for page, expected_kind in enumerate(document["page_modes"], start=1):
                route_total += 1
                route_correct += observed.get(page) == expected_kind

            page_two = next((item for item in records if item.meta["position"]["page"] == 2), None)
            if document["page_modes"][1] == "digital":
                text = page_two.data["text"] if page_two else ""
                for expected in document["expected_fields"].values():
                    digital_field_total += 1
                    digital_field_correct += expected["value"] in text
            elif qwen_client and qwen_docs < qwen_limit:
                image = render_pdf_page_png(raw, page_number=2, dpi=200)
                candidate = qwen_client.extract_page(
                    image, "逐字提取页面中的全部字段名和字段值，保留数字与标点",
                )
                qwen_docs += 1
                for expected in document["expected_fields"].values():
                    qwen_field_total += 1
                    qwen_field_correct += expected["value"] in candidate.text
    finally:
        if qwen_client:
            qwen_client.close()

    elapsed = time.perf_counter() - started
    return {
        "golden_set": {"documents": manifest["document_count"], "pages": manifest["page_count"]},
        "page_routing": {
            "correct": route_correct, "total": route_total,
            "accuracy": route_correct / route_total if route_total else 0,
        },
        "digital_exact_field_coverage": {
            "correct": digital_field_correct, "total": digital_field_total,
            "accuracy": digital_field_correct / digital_field_total if digital_field_total else 0,
        },
        "qwen_scanned_field_coverage": {
            "documents": qwen_docs, "correct": qwen_field_correct, "total": qwen_field_total,
            "accuracy": qwen_field_correct / qwen_field_total if qwen_field_total else None,
            "evidence_note": "Qwen 无确定性 bbox，仅作为候选；found 状态仍需 PaddleOCR/结构解析坐标校验",
        },
        "elapsed_seconds": round(elapsed, 3),
    }


def main() -> int:
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN)
    arg_parser.add_argument("--qwen-limit", type=int, default=0, help="调用本地 Qwen 复核的扫描文档数")
    arg_parser.add_argument("--output", type=Path)
    args = arg_parser.parse_args()
    report = evaluate(args.golden_dir.resolve(), max(0, args.qwen_limit))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
