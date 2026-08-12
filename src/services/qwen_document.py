# -*- coding: utf-8 -*-
"""本地 Qwen3.6 文档视觉候选提取客户端。"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any, Optional

import httpx


@dataclass(frozen=True)
class QwenPageCandidate:
    text: str
    confidence: float
    bbox: None = None
    review_required: bool = True
    raw_response: Optional[dict[str, Any]] = None


class QwenDocumentClient:
    """Qwen 只返回语义候选；没有确定性 bbox 时必须复核。"""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self.base_url = (base_url or os.getenv(
            "QWEN_VL_BASE_URL", os.getenv("LLM_BASE_URL", "http://192.168.1.20:6012/v1")
        )).rstrip("/")
        self.model = model or os.getenv(
            "QWEN_VL_MODEL", os.getenv("LLM_MODEL_NAME", "Qwen3.6-35B-A3B")
        )
        self.api_key = api_key or os.getenv("QWEN_VL_API_KEY", "not-needed")
        self._owns_client = http_client is None
        self.http_client = http_client or httpx.Client(trust_env=False, timeout=600.0)

    def close(self) -> None:
        if self._owns_client:
            self.http_client.close()

    def extract_page(self, image_bytes: bytes, instruction: str) -> QwenPageCandidate:
        """从单页图片提取候选；调用方仍需用 OCR/解析器坐标校验证据。"""
        encoded = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            instruction
                            + "\n只返回 JSON：{\"text\":字符串,\"confidence\":0到1}。"
                            + "不得猜测图片中不存在的内容。"
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}},
                ],
            }],
            "temperature": 0,
            "max_tokens": 4096,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        response = self.http_client.post(
            self.base_url + "/chat/completions",
            headers={"Authorization": "Bearer " + self.api_key},
            json=payload,
        )
        response.raise_for_status()
        raw = response.json()
        content = raw["choices"][0]["message"].get("content") or ""
        parsed = _parse_json_content(content)
        return QwenPageCandidate(
            text=str(parsed.get("text") or "").strip(),
            confidence=float(parsed.get("confidence") or 0.0),
            raw_response=raw,
        )


def _parse_json_content(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
        if text.startswith("json"):
            text = text[4:].lstrip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Qwen 文档候选不是合法 JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Qwen 文档候选必须是 JSON 对象")
    return parsed
