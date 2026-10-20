"""URL 图片文件名和任务内 OCR 缓存只用合成原件验证。"""
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
from threading import Event

import pytest
from PIL import Image

from src.data_prep.artifact_store import ArtifactStore
from src.parsers.image import ImageParser
from src.services.document_parser_contracts import DocumentPageBlock, DocumentParseResult, DocumentParserHealth


class ImageClient:
    provider = "mineru"
    base_url = "http://ocr.test"
    backend = "test-ocr"
    version = "1.0"

    def __init__(self):
        self.inputs = []

    def health(self):
        return DocumentParserHealth(status="healthy", version=self.version)

    def parse_image(self, raw_bytes, *, filename):
        assert Path(filename).suffix in {".png", ".jpg", ".jpeg", ".webp"}
        assert "?" not in filename and "#" not in filename
        self.inputs.append((raw_bytes, filename))
        return self.parse_response({"text": f"识别结果 {len(self.inputs)}", "version": self.version})

    def parse_response(self, raw_response):
        return DocumentParseResult(task_id="synthetic", backend=self.backend, version=raw_response["version"],
            provider=self.provider, source_coordinates_verified=True, raw_response=raw_response,
            blocks=(DocumentPageBlock(page=1, text=raw_response["text"], bbox=(1, 2, 30, 40),
                coordinate_space="image_pixels", confidence=.99),))


def fixture(tmp_path, uri="source.png", task="task"):
    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buffer, format="PNG")
    payload = buffer.getvalue()
    store = ArtifactStore(root=str(tmp_path))
    artifact = store.write_raw(task, "source", payload, uri=uri, media_type="image/png")
    return store, artifact, payload


@pytest.mark.parametrize("uri", ["https://example.com/hash", "https://example.com/hash?format=png",
    "https://example.com/photo.png?token=private#view", "https://example.com/photo.webp!format"])
def test_url_image_uses_verified_media_extension_and_preserves_original(tmp_path, uri):
    store, artifact, payload = fixture(tmp_path, uri)
    client = ImageClient()
    records, rejects = ImageParser(document_clients=[client], artifact_store=store).parse(artifact, payload)
    assert records and not rejects
    assert client.inputs == [(payload, artifact.artifact_id + ".png")]
    assert artifact.uri == uri and store.resolve_path(artifact.storage_path).read_bytes() == payload


def test_repeated_image_uses_same_response_as_frozen_evidence(tmp_path):
    store, artifact, payload = fixture(tmp_path)
    client = ImageClient()
    parser = ImageParser(document_clients=[client], artifact_store=store)
    first, _ = parser.parse(artifact, payload)
    second, rejects = parser.parse(artifact, payload)
    assert not rejects and len(client.inputs) == 1
    assert first[0].data == second[0].data
    element = second[0].data["elements"][0]
    response = json.loads(store.resolve_path(element["raw_result_ref"]).read_text(encoding="utf-8"))
    assert response["text"] == element["text"]


def test_generic_legacy_media_type_uses_decoded_image_format(tmp_path):
    store, artifact, payload = fixture(tmp_path)
    artifact = artifact.model_copy(update={"media_type": "application/octet-stream"})
    client = ImageClient()
    records, rejects = ImageParser(document_clients=[client], artifact_store=store).parse(artifact, payload)
    assert records and not rejects
    assert client.inputs[0] == (payload, artifact.artifact_id + ".png")


@pytest.mark.parametrize("changed", ["version", "base_url", "backend"])
def test_image_cache_does_not_reuse_changed_service_binding(tmp_path, changed):
    store, artifact, payload = fixture(tmp_path)
    client = ImageClient()
    parser = ImageParser(document_clients=[client], artifact_store=store)
    first, _ = parser.parse(artifact, payload)
    setattr(client, changed, getattr(client, changed) + "-new")
    second, rejects = parser.parse(artifact, payload)
    assert not rejects and len(client.inputs) == 2
    assert first[0].data["elements"][0]["raw_result_ref"] != second[0].data["elements"][0]["raw_result_ref"]


def test_image_cache_is_task_scoped_and_rejects_changed_original(tmp_path):
    store, artifact, payload = fixture(tmp_path)
    _, other, _ = fixture(tmp_path, task="other-owner-task")
    client = ImageClient()
    parser = ImageParser(document_clients=[client], artifact_store=store)
    parser.parse(artifact, payload)
    records, rejects = parser.parse(other, payload)
    assert records and not rejects and len(client.inputs) == 2
    records, rejects = parser.parse(artifact, payload + b"changed")
    assert not records and rejects[0]["reason"] == "invalid_image" and len(client.inputs) == 2


def test_busy_same_image_does_not_launch_duplicate_ocr_or_mix_response(tmp_path):
    store, artifact, payload = fixture(tmp_path, uri="source.png")
    started, release, begun = Event(), Event(), []
    class SlowClient(ImageClient):
        def parse_image(self, raw_bytes, *, filename):
            begun.append(filename)
            started.set()
            assert release.wait(2)
            return super().parse_image(raw_bytes, filename=filename)
    client = SlowClient()
    parser = ImageParser(document_clients=[client], artifact_store=store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(parser.parse, artifact, payload)
        assert started.wait(1)
        try:
            records, rejects = parser.parse(artifact, payload)
            assert not records and rejects
            assert len(begun) == 1
        finally:
            release.set()
        assert first.result()[0] and len(client.inputs) == 1
