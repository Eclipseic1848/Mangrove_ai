"""社媒图片首次全图识别；网络与服务响应均为合成替身。"""
import asyncio
import base64
import hashlib
import io
import json
import time

import httpx
from PIL import Image
import pytest

from src.conductor import source_images
from src.data_prep.artifact_store import ArtifactStore
from src.parsers import image as image_module
from src.services.document_parser_contracts import DocumentPageBlock, DocumentParseResult, DocumentParserHealth
from src.services.document_parser_resilience import document_parser_deadline
from src.services.paddleocr_vl_document import PaddleOCRVLDocumentClient


def image_flow(monkeypatch, tmp_path, *, response_kind="text"):
    monkeypatch.setattr(source_images.settings, "data_prep_artifact_root", str(tmp_path))
    monkeypatch.setattr(source_images.settings, "paddleocr_vl_enabled", True)
    monkeypatch.setattr(source_images.settings, "paddleocr_vl_base_url", "http://paddle.test")
    monkeypatch.setattr(source_images.settings, "paddleocr_vl_api_key", "")
    output = io.BytesIO()
    Image.new("RGB", (50, 40), "white").save(output, format="PNG")
    raw = output.getvalue()
    calls = {"download": [], "ocr": [], "legacy": []}

    class LegacyClient:
        provider, base_url, backend = "mineru", "http://legacy.test", "pipeline"

        def health(self):
            return DocumentParserHealth(status="healthy", version="fixture")

        def parse_image(self, data, *, filename):
            calls["legacy"].append(filename)
            return DocumentParseResult(task_id="fixture", backend=self.backend, version="fixture",
                blocks=(DocumentPageBlock(page=1, text="底部文字", bbox=(1, 2, 30, 40),
                    coordinate_space="image_pixels", confidence=.9),), raw_response={"fixture": True})

    monkeypatch.setattr(image_module, "configured_document_parser_clients", lambda: [LegacyClient()])
    client_type = httpx.Client

    def handle(request):
        assert request.url.host == "paddle.test"
        if request.method == "GET":
            return httpx.Response(200, json={"paths": {"/layout-parsing": {"post": {}}}})
        payload = json.loads(request.content)
        calls["ocr"].append(payload)
        assert base64.b64decode(payload["file"]) == raw
        if response_kind == "refused":
            return httpx.Response(503, json={"error": "private internal failure"})
        if response_kind == "timeout":
            raise httpx.ReadTimeout("private result unknown", request=request)
        text = "" if response_kind == "empty" else "底部文字" if payload["useLayoutDetection"] else "光大私行品牌节"
        return httpx.Response(200, json={"logId": "fixture", "errorCode": 0, "result": {
            "layoutParsingResults": [{"prunedResult": {"model_settings": {"use_doc_preprocessor": False},
                "parsing_res_list": [{"block_content": text, "block_bbox": [0, 0, 50, 40], "score": .9}] if text else []}}],
            "dataInfo": {"pages": [{"width": 50, "height": 40}]}}})

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs))
    monkeypatch.setattr(source_images.HttpSecurityGuard, "validate", lambda self, url: None)

    async def download(request):
        calls["download"].append(str(request.url))
        if response_kind == "download_refused":
            return httpx.Response(461)
        return httpx.Response(200, content=raw, headers={"content-type": "image/png"})

    monkeypatch.setattr(source_images, "PinnedAsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(download))
    note = {"metadata": {"note_id": "poster", "image_urls": ["https://example.com/poster.png"]}}
    return note, raw, calls


def test_collection_uses_full_image_first_and_preserves_unverified_coordinates(monkeypatch, tmp_path):
    note, raw, calls = image_flow(monkeypatch, tmp_path)
    with document_parser_deadline(time.monotonic() + 10, single_attempt=True):
        rows = asyncio.run(source_images.read_images(note, "owner-task", False))
    assert rows[0]["status"] == "recognized" and rows[0]["text"] == "光大私行品牌节"
    assert calls["legacy"] == []
    assert [request["useLayoutDetection"] for request in calls["ocr"]] == [False]
    assert calls["ocr"][0]["fileType"] == 1
    assert rows[0]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert (tmp_path / rows[0]["artifact_path"]).read_bytes() == raw
    assert rows[0]["review_required"] is True
    assert all(element["bbox"] is None and element["metadata"]["coordinate_mapping"] == "unverified"
               for element in rows[0]["elements"])


def test_image_modes_keep_separate_immutable_responses_and_reuse_same_mode(monkeypatch, tmp_path):
    note, raw, calls = image_flow(monkeypatch, tmp_path)
    store = ArtifactStore(root=str(tmp_path))
    artifact = store.write_raw("owner-task", "poster", raw, uri=note["metadata"]["image_urls"][0], media_type="image/png")
    ordinary = image_module.ImageParser(document_clients=[PaddleOCRVLDocumentClient()], artifact_store=store)
    first, _ = ordinary.parse(artifact, raw)
    original_ref = first[0].data["elements"][0]["raw_result_ref"]
    original_response = store.resolve_path(original_ref).read_bytes()
    assert first[0].data["text"] == "底部文字"
    with document_parser_deadline(time.monotonic() + 10, single_attempt=True):
        full = asyncio.run(source_images.read_images(note, "owner-task", False))
        cached = asyncio.run(source_images.read_images(note, "owner-task", False))
    assert full[0]["text"] == cached[0]["text"] == "光大私行品牌节"
    assert [request["useLayoutDetection"] for request in calls["ocr"]] == [True, False]
    again, _ = ordinary.parse(artifact, raw)
    assert again[0].data["text"] == "底部文字"
    assert store.resolve_path(original_ref).read_bytes() == original_response
    assert len(calls["ocr"]) == 2
    with document_parser_deadline(time.monotonic() + 10, single_attempt=True):
        other = asyncio.run(source_images.read_images(note, "other-owner-task", False))
    assert other[0]["artifact_path"].startswith("other-owner-task/") and len(calls["ocr"]) == 3


@pytest.mark.parametrize("kind, reason, requests", [
    ("empty", "image_ocr_no_text", 1),
    ("refused", "image_ocr_service_failed", 1),
    ("timeout", "image_ocr_service_failed", 1),
    ("download_refused", "image_download_failed", 0),
])
def test_collection_failure_has_safe_stage_and_no_repeat(monkeypatch, tmp_path, kind, reason, requests):
    note, _, calls = image_flow(monkeypatch, tmp_path, response_kind=kind)
    with document_parser_deadline(time.monotonic() + 10, single_attempt=True):
        rows = asyncio.run(source_images.read_images(note, "owner-task", False))
    assert rows[0]["status"] == "failed" and rows[0]["reason"] == reason
    assert len(calls["ocr"]) == requests and calls["legacy"] == []
    assert "private" not in json.dumps(rows)
    if requests:
        assert (tmp_path / rows[0]["artifact_path"]).is_file()


@pytest.mark.parametrize("raw_only, enabled", [(True, True), (False, False)])
def test_raw_collection_and_disabled_paddle_preserve_existing_behavior(monkeypatch, tmp_path, raw_only, enabled):
    note, _, calls = image_flow(monkeypatch, tmp_path)
    monkeypatch.setattr(source_images.settings, "paddleocr_vl_enabled", enabled)
    rows = asyncio.run(source_images.read_images(note, "owner-task", raw_only))
    assert calls["ocr"] == []
    if raw_only:
        assert rows[0]["status"] == "downloaded" and calls["legacy"] == []
    else:
        assert rows[0]["text"] == "底部文字" and len(calls["legacy"]) == 1


@pytest.mark.parametrize("operation", ["ordinary_image", "pdf", "full_image_empty"])
def test_explicit_full_image_mode_preserves_other_calls_and_never_retries(monkeypatch, tmp_path, operation):
    _, raw, calls = image_flow(monkeypatch, tmp_path, response_kind="empty" if operation == "full_image_empty" else "text")
    client = PaddleOCRVLDocumentClient(image_layout_detection=operation == "ordinary_image")
    if operation == "pdf":
        client.parse_pdf(raw, filename="protocol-fixture.pdf")
    else:
        result = client.parse_image(raw, filename="poster.png")
        if operation == "full_image_empty":
            assert not result.blocks and not result.source_coordinates_verified
    assert len(calls["ocr"]) == 1
    assert calls["ocr"][0]["fileType"] == (0 if operation == "pdf" else 1)
    assert calls["ocr"][0]["useLayoutDetection"] is (operation != "full_image_empty")


def test_customer_message_uses_safe_image_stage_without_changing_stop_state():
    from src.conductor.collection_results import collection_stop_explanation
    group = {"stop_reason": "image_read_or_parse_failed", "image_failure_reason": "image_ocr_no_text"}
    assert collection_stop_explanation(group) == "图片已取得，但识别服务未返回可用文字，已停止后续处理"
    assert group["stop_reason"] == "image_read_or_parse_failed"
    group["image_failure_reason"] = "http://private.test/?token=private"
    assert collection_stop_explanation(group) == "图片读取或识别失败，已保留取得的资料并停止后续处理"
