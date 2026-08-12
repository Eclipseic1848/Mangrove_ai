#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""在隔离解释器中运行 Docling 批次 0 兼容性探针。"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
import time
from typing import Any


def _contains_all(text: str, expected: list[str]) -> bool:
    compact = "".join(text.split())
    return all("".join(item.split()) in compact for item in expected)


def run(root: Path) -> dict[str, Any]:
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    contract_tokens = ["付款条款", "交付条款", "违约责任"]
    cases = [
        ("pdf", "documents/contract.pdf", contract_tokens),
        ("docx", "documents/contract.docx", contract_tokens),
        ("pptx", "documents/contract.pptx", contract_tokens),
        ("html", "documents/contract.html", contract_tokens),
        ("markdown", "documents/contract.md", contract_tokens),
        (
            "xlsx",
            "workload_filter/source.xlsx",
            ["示例人员甲", "核销工作量天数", "工作量费用"],
        ),
    ]
    results = []
    for file_format, relative_path, expected in cases:
        started = time.perf_counter()
        item: dict[str, Any] = {"format": file_format, "path": relative_path}
        try:
            document = converter.convert(root / relative_path).document
            text = document.export_to_markdown()
            item.update(
                {
                    "status": "pass" if _contains_all(text, expected) else "fail",
                    "expected_tokens_found": [
                        token for token in expected if _contains_all(text, [token])
                    ],
                    "text_chars": len(text),
                }
            )
        except Exception as exc:  # noqa: BLE001 - 探针必须保留单项错误
            item.update(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
        item["duration_ms"] = int((time.perf_counter() - started) * 1000)
        results.append(item)
    return {
        "tool": "docling",
        "version": importlib.metadata.version("docling"),
        "execution_mode": "isolated_python_sidecar_probe",
        "passed": sum(item["status"] == "pass" for item in results),
        "total": len(results),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.fixture_root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "passed": report["passed"],
                "total": report["total"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
