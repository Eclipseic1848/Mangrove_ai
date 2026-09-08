# -*- coding: utf-8 -*-
from __future__ import annotations

import io
from dataclasses import replace
from PIL import Image
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
            source_coordinates_verified=True,
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
    buffer = io.BytesIO()
    Image.new("RGB", (50, 40), "white").save(buffer, format="PNG")
    payload = buffer.getvalue()
    store = ArtifactStore(root=str(tmp_path))
    artifact = store.write_raw(
        "task-image",
        "upload:image",
        payload,
        uri="invoice.png",
        media_type="image/png",
        ext="png",
    )
    parser = ImageParser(
        document_clients=[_FakeImageClient()],
        artifact_store=store,
    )

    records, rejects = parser.parse(artifact, payload)

    assert rejects == []
    assert records[0].data["text"] == "发票号码：001"
    element = records[0].data["elements"][0]
    assert element["bbox"]["coordinate_space"] == "normalized_1000"
    assert element["extractor"] == "paddleocr_vl"


def test_image_parser_preserves_unknown_confidence_as_review_required(tmp_path):
    class UnknownClient(_FakeImageClient):
        def parse_image(self, raw_bytes, *, filename):
            result = super().parse_image(raw_bytes, filename=filename)
            return replace(result, blocks=(replace(result.blocks[0], confidence=0.0, confidence_known=False),))

    buffer = io.BytesIO()
    Image.new("RGB", (50, 40), "white").save(buffer, format="PNG")
    payload = buffer.getvalue()
    store = ArtifactStore(root=str(tmp_path))
    artifact = store.write_raw("task", "upload:image", payload, uri="image.png", media_type="image/png", ext="png")
    records, rejects = ImageParser(document_clients=[UnknownClient()], artifact_store=store).parse(artifact, payload)
    assert not rejects
    element = records[0].data["elements"][0]
    assert element["review_required"] is True
    assert element["metadata"]["confidence_known"] is False


def test_image_parser_rejects_corrupt_original_before_external_call(tmp_path):
    class NoCallClient(_FakeImageClient):
        def health(self):
            raise AssertionError("损坏原件不得外发")

    store = ArtifactStore(root=str(tmp_path))
    payload = b"not an image"
    artifact = store.write_raw("task", "upload:image", payload, uri="image.png", media_type="image/png", ext="png")
    records, rejects = ImageParser(document_clients=[NoCallClient()], artifact_store=store).parse(artifact, payload)
    assert records == []
    assert rejects[0]["reason"] == "invalid_image"


def test_rotated_image_does_not_claim_unmapped_ocr_box(tmp_path):
    buffer = io.BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (50, 40), "white").save(buffer, format="JPEG", exif=exif)
    payload = buffer.getvalue()
    store = ArtifactStore(root=str(tmp_path))
    artifact = store.write_raw("task", "upload:image", payload, uri="image.jpg", media_type="image/jpeg", ext="jpg")
    records, _ = ImageParser(document_clients=[_FakeImageClient()], artifact_store=store).parse(artifact, payload)
    element = records[0].data["elements"][0]
    assert element["bbox"] is None
    assert element["review_required"] is True
    assert element["page"] == 1 and element["text"] == "发票号码：001"


def test_service_unknown_transform_does_not_claim_source_coordinates(tmp_path):
    class UnmappedClient(_FakeImageClient):
        def parse_image(self, raw_bytes, *, filename):
            return replace(super().parse_image(raw_bytes, filename=filename), source_coordinates_verified=False)

    buffer = io.BytesIO()
    Image.new("RGB", (50, 40), "white").save(buffer, format="PNG")
    payload = buffer.getvalue()
    store = ArtifactStore(root=str(tmp_path))
    artifact = store.write_raw("task", "upload:image", payload, uri="image.png", media_type="image/png", ext="png")
    records, _ = ImageParser(document_clients=[UnmappedClient()], artifact_store=store).parse(artifact, payload)
    element = records[0].data["elements"][0]
    assert element["bbox"] is None and element["review_required"] is True
