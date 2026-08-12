# -*- coding: utf-8 -*-
"""固定文档黄金集的数量与页数门禁。"""
from __future__ import annotations

import json
from pathlib import Path

from pypdf import PdfReader


def test_document_golden_manifest_and_files_are_complete() -> None:
    root = Path(__file__).parent / "fixtures" / "document_golden"
    manifest = json.loads((root / "expected.json").read_text(encoding="utf-8"))
    assert manifest["document_count"] == 24
    assert manifest["page_count"] == 120
    assert manifest["distribution"] == {"contract": 12, "bid": 7, "invoice": 5}
    assert len(manifest["documents"]) == 24
    assert sum(len(PdfReader(root / item["file"]).pages) for item in manifest["documents"]) == 120
    assert all(item["license"] == "CC0-1.0 synthetic" for item in manifest["documents"])
