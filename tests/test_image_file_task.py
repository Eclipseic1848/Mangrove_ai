# -*- coding: utf-8 -*-
"""图片公共 HTTP 文件闭环；只替换 OCR 服务和语义生成，保留解析/绑定/QA。"""
from __future__ import annotations

import hashlib
import io

import pytest
from PIL import Image

from src.api.auth import get_store
from src.data_prep.artifact_store import ArtifactStore
from src.parsers.image import ImageParser
from src.parsers.registry import ParserRegistry
import src.parsers.registry as registry_module
from src.semantic_harness.compiler_models import PlanSemanticsDraft
from src.semantic_harness.models import ContentPolicy, DeliveryFormat, DeliverySpec, TaskFamily
from src.services.document_parser_contracts import DocumentPageBlock, DocumentParseResult, DocumentParserHealth
from tests.test_semantic_workspace_api import _client, _wait


class ImagePlanGenerator:
    provider = "local"
    model = "image-contract-fixture"
    prompt_version = "image-contract-test"
    prompt_sha256 = "1" * 64

    async def generate(self, request, *, diagnostics, attempt):
        return PlanSemanticsDraft(
            task_family=TaskFamily.EXTRACT,
            normalized_objective="逐字提取图片全部正文",
            whole_document=True,
            accepted_formats=("png", "jpg", "jpeg", "webp"),
            content_policy=ContentPolicy.VERBATIM,
            delivery=DeliverySpec(formats=(DeliveryFormat.TXT,)),
        )


class ContractImageOcr:
    """明确替身：不发 HTTP，不代表真实识别精度。"""
    provider = "test-image-ocr"
    base_url = "http://unused.invalid"
    backend = "contract"

    def __init__(self, *, confidence=0.99, known=True):
        self.confidence = confidence
        self.known = known
        self.inputs = []

    def health(self):
        return DocumentParserHealth(status="healthy", version="test-1")

    def parse_image(self, raw_bytes, *, filename):
        self.inputs.append(raw_bytes)
        return DocumentParseResult(
            task_id="test", backend=self.backend, provider=self.provider, version="test-1",
            source_coordinates_verified=True,
            blocks=(DocumentPageBlock(
                page=1, text="合同测试发票 金额100元", bbox=(10, 10, 250, 80),
                coordinate_space="image_pixels", confidence=self.confidence,
                confidence_known=self.known,
            ),), raw_response={"test_contract_only": True},
        )


def _image_bytes(suffix, orientation=1):
    buffer = io.BytesIO()
    exif = Image.Exif()
    exif[274] = orientation
    Image.new("RGB", (320, 200), "white").save(
        buffer, format={"png": "PNG", "jpeg": "JPEG", "webp": "WEBP"}[suffix], exif=exif,
    )
    return buffer.getvalue()


def _setup(tmp_path, monkeypatch, ocr):
    registry = ParserRegistry()
    registry.register(ImageParser(document_clients=(ocr,), artifact_store=ArtifactStore(root=tmp_path / "artifacts")))
    monkeypatch.setattr(registry_module, "_default_registry", registry)
    monkeypatch.setattr(registry_module, "_phase2_registered", True)
    return _client(tmp_path, monkeypatch, generator=ImagePlanGenerator())[0]


def _upload(client, raw, suffix):
    response = client.post("/api/data-sources/uploads", files={
        "file": (f"invoice.{suffix}", raw, f"image/{suffix}"),
    })
    assert response.status_code == 200, response.text
    return response.json()["upload_id"]


def _create(client, upload_ids):
    return client.post("/api/semantic-workspace/tasks", json={
        "objective_text": "逐字提取图片全部正文", "upload_ids": upload_ids,
        "output_formats": ["txt"], "provider": "local", "model": "image-contract-fixture",
    })


@pytest.mark.parametrize("suffix", ["png", "jpeg", "webp"])
def test_image_http_task_publishes_qa_delivery_and_preserves_original(tmp_path, monkeypatch, suffix):
    ocr = ContractImageOcr()
    client = _setup(tmp_path, monkeypatch, ocr)
    raw = _image_bytes(suffix)
    with client:
        upload_id = _upload(client, raw, suffix)
        created = _create(client, [upload_id])
        assert created.status_code == 202, created.text
        task = _wait(client, created.json()["task_id"], {"completed", "failed", "needs_input"})
        assert task["status"] == "completed", task
        outputs = task["delivery"]["outputs"]
        assert outputs
        for output in outputs:
            assert output["qa"]["openable"]
            stored = get_store().get_semantic_delivery_output("user-a", output["output_id"])
            assert stored is not None
            downloaded = client.get(output["download_url"])
            assert downloaded.status_code == 200
            assert "合同测试发票 金额100元" in downloaded.content.decode("utf-8-sig")
            assert hashlib.sha256(downloaded.content).hexdigest() == output["sha256"] == stored["sha256"]
        preview = client.get(f"/api/semantic-workspace/tasks/{task['task_id']}/sources/{upload_id}/preview", params={"revision": task["active_revision"]})
        assert preview.status_code == 200, preview.text
        assert preview.json()["kind"] == "image"
        original = client.get(preview.json()["content_url"])
        assert original.status_code == 200
        assert original.content == raw
        assert hashlib.sha256(original.content).hexdigest() == hashlib.sha256(raw).hexdigest()
        assert len(ocr.inputs) >= 2
        assert all(value == raw for value in ocr.inputs)


@pytest.mark.parametrize("confidence,known,orientation", [(0, False, 1), (0.3, True, 1), (0.99, True, 6)])
def test_image_http_task_does_not_publish_untrusted_evidence(tmp_path, monkeypatch, confidence, known, orientation):
    ocr = ContractImageOcr(confidence=confidence, known=known)
    client = _setup(tmp_path, monkeypatch, ocr)
    with client:
        upload_id = _upload(client, _image_bytes("jpeg", orientation), "jpeg")
        created = _create(client, [upload_id])
        assert created.status_code == 202, created.text
        task = _wait(client, created.json()["task_id"], {"completed", "failed", "needs_input"})
        assert task["status"] in {"failed", "needs_input"}, task
        assert task["delivery"] is None
        assert get_store().latest_semantic_delivery("user-a", task["task_id"]) is None
        assert ocr.inputs


def test_image_csv_mix_is_rejected_by_public_task_api(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch, ContractImageOcr())
    # 创建请求必须同步拒绝；不启动后台执行器，避免错误接收后继续运行任务。
    try:
        image_id = _upload(client, _image_bytes("png"), "png")
        uploaded = client.post("/api/data-sources/uploads", files={"file": ("table.csv", b"name,amount\na,100\n", "text/csv")})
        assert uploaded.status_code == 200, uploaded.text
        response = _create(client, [image_id, uploaded.json()["upload_id"]])
        assert response.status_code == 422, response.text
        assert "混合处理文档和表格" in response.json()["detail"]
    finally:
        client.close()
