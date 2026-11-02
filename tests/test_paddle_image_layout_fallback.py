"""布局为空的图片只补一次全图识别，原件、坐标和调用边界保持。"""
import base64
import asyncio
from copy import deepcopy
from contextlib import nullcontext
import io
import json
import hashlib
import time
import threading

import httpx
from PIL import Image
import pytest

from src.data_prep.artifact_store import ArtifactStore
from src.parsers.image import ImageParser
from src.services.paddleocr_vl_document import PaddleOCRVLDocumentClient
from src.services.document_parser_contracts import DocumentParserServiceError
from src.services import document_parser_resilience as resilience


def response_payload(*, text="", layout=True):
    return {"logId": "actual-layout" if layout else "actual-full-image", "errorCode": 0,
        "result": {"dataInfo": {"pages": [{"width": 50, "height": 40}]}, "layoutParsingResults": [
            {"prunedResult": {"model_settings": {"use_doc_preprocessor": False}, "parsing_res_list": [
                {"block_content": text, "block_bbox": [0, 0, 50, 40], "score": 0.9}
            ] if text else []}}]}}


def image_bytes():
    output = io.BytesIO()
    Image.new("RGB", (50, 40), "white").save(output, format="PNG")
    return output.getvalue()


def test_empty_image_layout_has_one_full_image_request_and_unverified_replay(tmp_path):
    payload = image_bytes()
    calls = []
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"paths": {"/layout-parsing": {"post": {}}}})
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json=response_payload(text="中国光大银行 Debit Card" if not body["useLayoutDetection"] else "",
                                                       layout=body["useLayoutDetection"]))
    client = PaddleOCRVLDocumentClient(base_url="http://paddle.test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = client.parse_image(payload, filename="card.png")
    assert [item["useLayoutDetection"] for item in calls] == [True, False]
    assert all(base64.b64decode(item["file"]) == payload and item["fileType"] == 1 for item in calls)
    assert result.blocks[0].text == "中国光大银行 Debit Card"
    assert not result.source_coordinates_verified
    assert not client.parse_response(deepcopy(result.raw_response)).source_coordinates_verified
    store = ArtifactStore(root=str(tmp_path))
    artifact = store.write_raw("task", "card", payload, uri="card.png", media_type="image/png", ext="png")
    records, rejects = ImageParser(document_clients=[client], artifact_store=store).parse(artifact, payload)
    assert not rejects
    element = records[0].data["elements"][0]
    assert element["bbox"] is None and element["review_required"] is True
    assert element["metadata"]["coordinate_mapping"] == "unverified"
    post_count = len(calls)
    cached_records, cached_rejects = ImageParser(document_clients=[client], artifact_store=store).parse(artifact, payload)
    assert not cached_rejects and cached_records[0].data == records[0].data and len(calls) == post_count
    assert store.resolve_path(artifact.storage_path).read_bytes() == payload


@pytest.mark.parametrize("kind", ["image_with_text", "pdf_empty", "image_still_empty"])
def test_fallback_does_not_repeat_successful_images_or_change_pdf(kind):
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json=response_payload(text="正常文字" if kind == "image_with_text" else ""))
    client = PaddleOCRVLDocumentClient(base_url="http://paddle.test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = client.parse_pdf(b"pdf", filename="source.pdf") if kind == "pdf_empty" else client.parse_image(image_bytes(), filename="source.png")
    assert [item["useLayoutDetection"] for item in calls] == ([True, False] if kind == "image_still_empty" else [True])
    if kind == "image_with_text":
        assert result.source_coordinates_verified
    else:
        assert not result.blocks
    if kind == "pdf_empty":
        assert calls[0]["fileType"] == 0 and "_mangrove_image_ocr_mode" not in result.raw_response


@pytest.mark.parametrize("failure", ["http", "timeout", "application", "invalid_shape", "nonfinite_box"])
def test_unknown_or_failed_initial_image_request_is_never_repeated(failure):
    calls = []
    def handler(request):
        calls.append(request)
        if failure == "http": return httpx.Response(503, json={"error": "unavailable"})
        if failure == "timeout": raise httpx.ReadTimeout("unknown result", request=request)
        if failure == "application": return httpx.Response(200, json={"errorCode": 500, "errorMsg": "layout failure unknown"})
        if failure == "invalid_shape": return httpx.Response(200, json={"errorCode": 0, "result": {}})
        payload = response_payload(text="原文")
        payload["result"]["layoutParsingResults"][0]["prunedResult"]["parsing_res_list"][0]["block_bbox"][2] = "NaN"
        return httpx.Response(200, json=payload)
    client = PaddleOCRVLDocumentClient(base_url="http://paddle.test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(DocumentParserServiceError):
        client.parse_image(image_bytes(), filename="card.png")
    assert len(calls) == 1


@pytest.mark.parametrize("unknown", ["missing_pruned", "invalid_blocks", "missing_bbox", "empty_pages"])
def test_skipped_malformed_blocks_are_not_confirmed_empty_layout(unknown):
    calls = []
    payload = response_payload()
    page = payload["result"]["layoutParsingResults"][0]
    if unknown == "empty_pages": payload["result"]["layoutParsingResults"] = []
    elif unknown == "missing_pruned": page.clear()
    elif unknown == "invalid_blocks": page["prunedResult"]["parsing_res_list"] = {}
    else: page["prunedResult"]["parsing_res_list"] = [{"block_content": "有文字但缺坐标"}]
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload)
    client = PaddleOCRVLDocumentClient(base_url="http://paddle.test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert not client.parse_image(image_bytes(), filename="card.png").blocks
    assert len(calls) == 1


@pytest.mark.parametrize("stop", ["deadline", "authorization"])
def test_stopped_execution_does_not_start_full_image_request(monkeypatch, stop):
    from src.api import execution
    calls = []
    def handler(request):
        calls.append(request)
        if stop == "deadline":
            resilience._DEADLINE.set(time.monotonic() - 1)
        return httpx.Response(200, json=response_payload())
    def checkpoint():
        if stop == "authorization" and calls:
            raise PermissionError("本任务授权已撤销")
    monkeypatch.setattr(execution, "execution_checkpoint", checkpoint)
    client = PaddleOCRVLDocumentClient(base_url="http://paddle.test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with resilience.document_parser_deadline(time.monotonic() + 10):
        with pytest.raises(TimeoutError if stop == "deadline" else PermissionError):
            client.parse_image(image_bytes(), filename="card.png")
    assert len(calls) == 1


def test_old_empty_image_cache_cannot_hide_new_fallback_policy(tmp_path):
    raw = image_bytes()
    calls = []
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"paths": {"/layout-parsing": {"post": {}}}})
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json=response_payload(text="中国光大银行" if not body["useLayoutDetection"] else ""))
    client = PaddleOCRVLDocumentClient(base_url="http://paddle.test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    store = ArtifactStore(root=str(tmp_path))
    artifact = store.write_raw("task", "card", raw, uri="card.png", media_type="image/png", ext="png")
    old_identity = "|".join((artifact.sha256, client.provider, client.base_url, client.backend, client.model_version, "image-ocr-v1"))
    old_key = hashlib.sha256(old_identity.encode("utf-8")).hexdigest()[:24]
    old_relative = f"third_party/{client.provider}/{artifact.artifact_id}-{old_key}.json"
    old_ref = store.write_json_if_absent("task", old_relative, response_payload())
    old_bytes = store.resolve_path(old_ref).read_bytes()
    records, rejects = ImageParser(document_clients=[client], artifact_store=store).parse(artifact, raw)
    assert not rejects and records[0].data["text"] == "中国光大银行"
    assert [item["useLayoutDetection"] for item in calls] == [True, False]
    assert store.resolve_path(old_ref).read_bytes() == old_bytes


def test_single_attempt_collection_does_not_repeat_empty_layout_or_use_secondary(tmp_path):
    raw = image_bytes()
    calls = []
    def handler(request):
        calls.append((request.url.host, request.method))
        if request.method == "GET":
            return httpx.Response(200, json={"paths": {"/layout-parsing": {"post": {}}}})
        return httpx.Response(200, json=response_payload())
    http = httpx.Client(transport=httpx.MockTransport(handler))
    clients = [PaddleOCRVLDocumentClient(base_url=f"http://{host}.test", http_client=http)
               for host in ("primary", "secondary")]
    store = ArtifactStore(root=str(tmp_path))
    artifact = store.write_raw("task", "card", raw, uri="card.png", media_type="image/png")
    with resilience.document_parser_deadline(time.monotonic() + 10, single_attempt=True):
        records, rejects = ImageParser(document_clients=clients, artifact_store=store).parse(artifact, raw)
    assert not records and rejects[0]["reason"] == "image_ocr_required"
    assert calls == [("primary.test", "GET"), ("primary.test", "POST")]
    assert not resilience.document_parser_single_attempt()


@pytest.mark.parametrize("blocked_at", ["health", "queue"])
def test_cancelled_collection_image_thread_never_starts_ocr(monkeypatch, tmp_path, blocked_at):
    from src.conductor import source_images
    from src.parsers import image as image_module
    raw = image_bytes()
    started, release = threading.Event(), threading.Event()
    calls = []
    def handler(request):
        calls.append(request.method)
        if request.method == "GET":
            started.set()
            if blocked_at == "health":
                assert release.wait(3), "合成健康检查未解除"
            return httpx.Response(200, json={"paths": {"/layout-parsing": {"post": {}}}})
        return httpx.Response(200, json=response_payload(text="原文"))
    client = PaddleOCRVLDocumentClient(base_url="http://paddle.test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(image_module, "configured_document_parser_clients", lambda: [client])
    monkeypatch.setattr(source_images.settings, "data_prep_artifact_root", str(tmp_path))
    monkeypatch.setattr(source_images.settings, "document_parser_max_concurrency", 1)
    monkeypatch.setattr(source_images.HttpSecurityGuard, "validate", lambda self, url: None)
    monkeypatch.setattr(source_images, "PinnedAsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(
        lambda request: httpx.Response(200, content=raw, headers={"content-type": "image/png"})))
    note = {"metadata": {"note_id": "cancel", "image_urls": ["https://image.test/one.png"]}}

    async def run():
        with resilience.document_parser_request_slot() if blocked_at == "queue" else nullcontext():
            with resilience.document_parser_deadline(time.monotonic() + 3, single_attempt=True):
                task = asyncio.create_task(source_images.read_images(note, "cancel", False))
                async with asyncio.timeout(2):
                    while not started.is_set():
                        await asyncio.sleep(.001)
                task.cancel()
                await asyncio.sleep(.01)
                if blocked_at == "health":
                    assert not task.done(), "取消不能在原线程收口前返回"
                release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert calls == ["GET"]
    assert note["images"][0]["status"] == "downloaded"
