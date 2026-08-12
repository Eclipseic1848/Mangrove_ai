# -*- coding: utf-8 -*-
"""Qwen 文档视觉客户端的安全默认值测试。"""
from __future__ import annotations

import json

import httpx

from src.services.qwen_document import QwenDocumentClient


def test_qwen_client_disables_thinking_and_marks_missing_bbox_for_review() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"text":"合同编号 HT-001","confidence":0.91}'}}],
                "model": "Qwen3.6-35B-A3B",
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    client = QwenDocumentClient(base_url="http://qwen.test/v1", http_client=http_client)
    result = client.extract_page(b"png", "提取合同编号")

    assert captured["chat_template_kwargs"]["enable_thinking"] is False
    assert captured["temperature"] == 0
    assert result.text == "合同编号 HT-001"
    assert result.bbox is None
    assert result.review_required is True
