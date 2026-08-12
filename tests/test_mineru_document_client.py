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
