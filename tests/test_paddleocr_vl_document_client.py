# -*- coding: utf-8 -*-
"""PaddleOCR-VL 完整文档服务适配器契约测试。"""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from src.services.document_parser_contracts import DocumentParserServiceError
from src.services.document_parser_factory import configured_document_parser_clients
from src.services.paddleocr_vl_document import PaddleOCRVLDocumentClient


def _success_payload() -> dict:
    return {
        "logId": "paddle-log-1",
        "errorCode": 0,
        "errorMsg": "Success",
        "result": {
            "layoutParsingResults": [
                {
                    "prunedResult": {
                        "parsing_res_list": [
                            {
                                "block_bbox": [10, 20, 210, 60],
                                "block_label": "text",
                                "block_content": "合同编号：HT-001",
                                "block_id": 0,
                                "block_order": 0,
                                "score": 0.98,
                            },
                            {
                                "block_bbox": [10, 100, 500, 400],
                                "block_label": "table",
                                "block_content": "|项目|金额|\n|---|---|",
                                "block_id": 1,
                                "block_order": 1,
                            },
                        ]
                    },
                    "markdown": {"text": "合同编号：HT-001", "images": None},
                    "outputImages": None,
                    "inputImage": None,
                }
            ],
            "dataInfo": {
                "pages": [{"width": 1000, "height": 500}],
            },
        },
    }


def test_paddleocr_vl_client_uses_official_layout_parsing_protocol() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/openapi.json":
            return httpx.Response(
                200,
                json={"paths": {"/layout-parsing": {"post": {}}}},
            )
        captured["path"] = request.url.path
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json=_success_payload())

    client = PaddleOCRVLDocumentClient(
        base_url="http://paddle.test:8080",
        model_version="PaddleOCR-VL-1.6",
        http_client=httpx.Client(
            transport=httpx.MockTransport(handler),
            trust_env=False,
        ),
    )

    health = client.health()
    result = client.parse_pdf(b"%PDF-test", filename="contract.pdf")

    assert health.status == "healthy"
    assert health.version == "PaddleOCR-VL-1.6"
    assert captured["path"] == "/layout-parsing"
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["fileType"] == 0
    assert base64.b64decode(payload["file"]) == b"%PDF-test"
    assert payload["visualize"] is False
    assert payload["returnMarkdownImages"] is False
    assert payload["temperature"] == 0
    assert result.provider == "paddleocr_vl"
    assert result.version == "PaddleOCR-VL-1.6"
    assert len(result.blocks) == 2
    assert result.blocks[0].page == 1
    assert result.blocks[0].text == "合同编号：HT-001"
    assert result.blocks[0].bbox == (10.0, 40.0, 210.0, 120.0)
    assert result.blocks[0].coordinate_space == "normalized_1000"
    assert result.blocks[0].confidence == pytest.approx(0.98)
    assert result.blocks[1].element_type == "table"


def test_paddleocr_vl_client_sends_image_file_type() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_success_payload())

    client = PaddleOCRVLDocumentClient(
        base_url="http://paddle.test:18081",
        http_client=httpx.Client(
            transport=httpx.MockTransport(handler),
            trust_env=False,
        ),
    )

    result = client.parse_image(b"fake-png", filename="scan.png")

    assert captured["fileType"] == 1
    assert len(result.blocks) == 2


def test_paddleocr_vl_health_rejects_vlm_only_service() -> None:
    client = PaddleOCRVLDocumentClient(
        base_url="http://paddle-vlm.test:18080",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"paths": {"/v1/chat/completions": {"post": {}}}},
                )
            ),
            trust_env=False,
        ),
    )

    with pytest.raises(
        DocumentParserServiceError,
        match="VLM 推理子服务",
    ):
        client.health()


def test_paddleocr_vl_client_rejects_application_error() -> None:
    client = PaddleOCRVLDocumentClient(
        base_url="http://paddle.test:8080",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "logId": "failed",
                        "errorCode": 500,
                        "errorMsg": "pipeline unavailable",
                    },
                )
            ),
            trust_env=False,
        ),
    )

    with pytest.raises(DocumentParserServiceError, match="pipeline unavailable"):
        client.parse_pdf(b"%PDF-test", filename="contract.pdf")


def test_paddleocr_vl_client_does_not_fabricate_bbox_from_markdown() -> None:
    payload = _success_payload()
    page = payload["result"]["layoutParsingResults"][0]
    page["prunedResult"]["parsing_res_list"] = []
    page["markdown"]["text"] = "只有 Markdown，没有坐标"
    client = PaddleOCRVLDocumentClient(
        base_url="http://paddle.test:8080",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=payload)
            ),
            trust_env=False,
        ),
    )

    result = client.parse_pdf(b"%PDF-test", filename="contract.pdf")

    assert result.blocks == ()


def test_document_parser_factory_skips_service_until_url_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.config.settings import settings

    monkeypatch.setattr(settings, "document_parser_primary", "paddleocr_vl")
    monkeypatch.setattr(settings, "document_parser_secondary", "mineru")
    monkeypatch.setattr(settings, "document_parser_fallback_enabled", True)
    monkeypatch.setattr(settings, "paddleocr_vl_enabled", True)
    monkeypatch.setattr(settings, "paddleocr_vl_base_url", "")
    monkeypatch.setattr(settings, "mineru_enabled", True)
    monkeypatch.setattr(settings, "mineru_base_url", "http://mineru.test:8000")

    clients = configured_document_parser_clients()

    assert [client.provider for client in clients] == ["mineru"]


def test_document_parser_factory_builds_primary_and_secondary_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.config.settings import settings

    monkeypatch.setattr(settings, "document_parser_primary", "paddleocr_vl")
    monkeypatch.setattr(settings, "document_parser_secondary", "mineru")
    monkeypatch.setattr(settings, "document_parser_fallback_enabled", True)
    monkeypatch.setattr(settings, "paddleocr_vl_enabled", True)
    monkeypatch.setattr(
        settings,
        "paddleocr_vl_base_url",
        "http://paddle.test:8080",
    )
    monkeypatch.setattr(settings, "mineru_enabled", True)
    monkeypatch.setattr(settings, "mineru_base_url", "http://mineru.test:8000")

    clients = configured_document_parser_clients()

    assert [client.provider for client in clients] == [
        "paddleocr_vl",
        "mineru",
    ]
