# -*- coding: utf-8 -*-
"""MinerU 文档服务客户端契约测试。"""
from __future__ import annotations

import json

import httpx
import pytest

from src.services.mineru_document import MinerUDocumentClient, MinerUServiceError


def _completed_payload() -> dict:
    return {
        "task_id": "task-mineru-1",
        "status": "completed",
        "backend": "pipeline",
        "version": "3.4.4",
        "results": {
            "scan": {
                "md_content": "invoice_no: INV-2026-003",
                "model_output": json.dumps(
                    [
                        {
                            "layout_dets": [
                                {
                                    "label": "ocr_text",
                                    "bbox": [130, 364, 622, 428],
                                    "score": 0.979,
                                    "text": "invoice_no:INV-2026-003",
                                }
                            ],
                            "page_info": {"page_no": 1, "width": 1654, "height": 2339},
                        }
                    ],
                    ensure_ascii=False,
                ),
                "content_list": json.dumps(
                    [
                        {
                            "type": "table",
                            "table_body": "<table><tr><td>合计</td></tr></table>",
                            "bbox": [100, 500, 900, 800],
                            "page_idx": 1,
                        }
                    ],
                    ensure_ascii=False,
                ),
                "middle_json": "{}",
            }
        },
    }


@pytest.mark.parametrize("case", ["verified", "version", "missing_version", "backend", "content", "middle", "missing_size", "invalid_size", "page", "empty"])
def test_mineru_coordinate_confirmation_only_covers_verified_model_output(case):
    payload = _completed_payload()
    item = payload["results"]["scan"]
    model = json.loads(item["model_output"])
    model[0]["page_info"]["page_no"] = 0
    item["model_output"] = model
    if case != "content":
        item["content_list"] = []
    if case == "version":
        payload["version"] = "3.4.5"
    elif case == "missing_version":
        payload.pop("version")
    elif case == "backend":
        payload["backend"] = "vlm"
    elif case in {"middle", "empty"}:
        item["model_output"] = []
        if case == "middle":
            item["middle_json"] = {"pdf_info": [{"page_idx": 0, "para_blocks": [{"lines": [{"spans": [{"content": "回退正文", "bbox": [1, 2, 3, 4], "score": 0.99}]}]}]}]}
    elif case == "missing_size":
        model[0]["page_info"].pop("width")
    elif case == "invalid_size":
        model[0]["page_info"]["width"] = True
    elif case == "page":
        model[0]["page_info"]["page_no"] = 1
    result = MinerUDocumentClient(base_url="http://mineru.test").parse_response(payload)
    assert result.source_coordinates_verified is (case == "verified")
    if case in {"content", "middle"}:
        assert result.blocks


@pytest.mark.parametrize("case", ["verified", "missing_page", "invalid_page", "negative_page", "outside", "image", "middle", "version", "backend", "model"])
def test_mineru_verified_table_region_keeps_unknown_score(case):
    payload = _completed_payload()
    item = payload["results"]["scan"]
    item["model_output"] = []
    table = {"type": "table", "page_idx": 0, "bbox": [30, 72, 966, 855], "table_body": "<table><tr><td>金额</td><td>100</td></tr></table>"}
    item["content_list"] = [table]
    if case == "missing_page":
        table.pop("page_idx")
    elif case == "invalid_page":
        table["page_idx"] = False
    elif case == "negative_page":
        table["page_idx"] = -1
    elif case == "outside":
        table["bbox"][2] = 1001
    elif case == "image":
        item["content_list"].append({"type": "image", "page_idx": 0, "bbox": [1, 2, 3, 4], "image_caption": "未核图片"})
    elif case == "middle":
        item["middle_json"] = {"pdf_info": [{"page_idx": 0, "para_blocks": [{"lines": [{"spans": [{"content": "未核回退", "bbox": [1, 2, 3, 4], "score": 0.99}]}]}]}]}
    elif case == "version":
        payload["version"] = "unknown"
    elif case == "backend":
        payload["backend"] = "vlm"
    elif case == "model":
        model = json.loads(_completed_payload()["results"]["scan"]["model_output"])
        model[0]["page_info"]["page_no"] = 0
        item["model_output"] = model
    result = MinerUDocumentClient(base_url="http://mineru.test").parse_response(payload)
    assert result.source_coordinates_verified is (case in {"verified", "model"})
    block = next(block for block in result.blocks if block.element_type == "table")
    assert block.confidence_known is False
    assert block.confidence == 0
    if case == "verified":
        assert block.bbox == (30, 72, 966, 855)


@pytest.mark.parametrize("score", [None, "invalid", float("nan"), float("inf"), -0.1, 1.1])
def test_mineru_unknown_confidence_is_not_full_confidence(score):
    payload = _completed_payload()
    detections = json.loads(payload["results"]["scan"]["model_output"])
    detections[0]["layout_dets"][0]["score"] = score
    payload["results"]["scan"]["model_output"] = detections
    result = MinerUDocumentClient(base_url="http://mineru.test").parse_response(payload)
    assert result.blocks[0].confidence == 0.0
    assert result.blocks[0].confidence_known is False
    assert result.blocks[1].confidence_known is False


def test_mineru_image_reuses_file_parse_with_original_bytes():
    import io
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (30, 20), "white").save(buffer, format="PNG")
    payload = buffer.getvalue()
    captured = []
    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=_completed_payload())
    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as transport:
        result = MinerUDocumentClient(base_url="http://mineru.test", http_client=transport).parse_image(payload, filename="scan.png")
    assert result.blocks
    assert len(captured) == 1 and captured[0].url.path == "/file_parse"
    assert payload in captured[0].content
    assert b"image/png" in captured[0].content and b'filename="scan.png"' in captured[0].content


def test_mineru_client_uses_pipeline_and_parses_bbox_blocks() -> None:
    captured: dict[str, bytes | str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(
                200,
                json={"status": "healthy", "version": "3.4.4", "protocol_version": 2},
            )
        captured["path"] = request.url.path
        captured["body"] = request.content
        return httpx.Response(200, json=_completed_payload())

    http_client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    client = MinerUDocumentClient(
        base_url="http://mineru.test:8000",
        backend="pipeline",
        http_client=http_client,
    )

    health = client.health()
    result = client.parse_pdf(b"%PDF-test", filename="scan.pdf")

    assert health.version == "3.4.4"
    assert health.protocol_version == 2
    assert captured["path"] == "/file_parse"
    body = captured["body"]
    assert isinstance(body, bytes)
    assert b'name="backend"' in body and b"pipeline" in body
    assert b'name="parse_method"' in body and b"ocr" in body
    assert b'name="return_model_output"' in body and b"true" in body
    assert result.version == "3.4.4"
    assert result.backend == "pipeline"
    assert result.task_id == "task-mineru-1"
    assert result.blocks[0].page == 2
    assert result.blocks[0].text == "invoice_no:INV-2026-003"
    assert result.blocks[0].bbox == pytest.approx((
        130 / 1654 * 1000,
        364 / 2339 * 1000,
        622 / 1654 * 1000,
        428 / 2339 * 1000,
    ))
    assert result.blocks[0].coordinate_space == "normalized_1000"
    assert result.blocks[0].confidence == pytest.approx(0.979)
    assert result.blocks[1].element_type == "table"
    assert result.blocks[1].coordinate_space == "normalized_1000"


def test_mineru_client_falls_back_to_middle_json_spans() -> None:
    payload = _completed_payload()
    item = payload["results"]["scan"]
    item["model_output"] = "[]"
    item["content_list"] = "[]"
    item["middle_json"] = json.dumps(
        {
            "pdf_info": [
                {
                    "page_idx": 0,
                    "page_size": [595, 841],
                    "para_blocks": [
                        {
                            "type": "text",
                            "lines": [
                                {
                                    "spans": [
                                        {
                                            "type": "text",
                                            "content": "合同编号：HT-001",
                                            "bbox": [42, 100, 220, 125],
                                            "score": 0.91,
                                        }
                                    ]
                                }
                            ],
                        }
                    ],
                }
            ]
        },
        ensure_ascii=False,
    )

    client = MinerUDocumentClient(
        base_url="http://mineru.test:8000",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
            trust_env=False,
        ),
    )
    result = client.parse_pdf(b"%PDF-test", filename="scan.pdf")

    assert len(result.blocks) == 1
    assert result.blocks[0].text == "合同编号：HT-001"
    assert result.blocks[0].bbox == (42.0, 100.0, 220.0, 125.0)
    assert result.blocks[0].coordinate_space == "pdf_points"


def test_mineru_client_exposes_server_failure_without_hiding_root_cause() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "status": "failed",
                "error": "Engine core initialization failed",
                "message": "Task execution failed",
            },
        )

    client = MinerUDocumentClient(
        base_url="http://mineru.test:8000",
        http_client=httpx.Client(transport=httpx.MockTransport(handler), trust_env=False),
    )

    with pytest.raises(MinerUServiceError, match="Engine core initialization failed"):
        client.parse_pdf(b"%PDF-test", filename="scan.pdf")
