# -*- coding: utf-8 -*-
from __future__ import annotations

from src.data_prep.artifact_store import ArtifactStore
from src.services.document_parser_contracts import (
    DocumentPageBlock,
    DocumentParseResult,
    DocumentParserHealth,
)
from src.parsers.image import ImageParser


class _FakeImageClient:
    provider = "paddleocr_vl"
    base_url = "http://paddle.test:18081"
    backend = "PaddleOCR-VL-1.6"

    def health(self):
        return DocumentParserHealth(status="healthy", version="1.6")

    def parse_image(self, raw_bytes: bytes, *, filename: str):
        return DocumentParseResult(
            task_id="image-1",
            backend=self.backend,
            version="1.6",
            provider=self.provider,
            blocks=(
                DocumentPageBlock(
                    page=1,
                    text="发票号码：001",
                    bbox=(10, 20, 500, 80),
                    coordinate_space="normalized_1000",
                    confidence=0.99,
                ),
            ),
            raw_response={"result": "ok"},
        )


def test_image_parser_outputs_evidence_ready_elements(tmp_path):
    store = ArtifactStore(root=str(tmp_path))
    artifact = store.write_raw(
        "task-image",
        "upload:image",
        b"fake-image",
        uri="invoice.png",
        media_type="image/png",
        ext="png",
    )
    parser = ImageParser(
        document_clients=[_FakeImageClient()],
        artifact_store=store,
    )

    records, rejects = parser.parse(artifact, b"fake-image")

    assert rejects == []
    assert records[0].data["text"] == "发票号码：001"
    element = records[0].data["elements"][0]
    assert element["bbox"]["coordinate_space"] == "normalized_1000"
    assert element["extractor"] == "paddleocr_vl"
