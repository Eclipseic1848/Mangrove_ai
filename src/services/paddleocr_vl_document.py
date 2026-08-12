# -*- coding: utf-8 -*-
"""PaddleOCR-VL 完整文档解析服务适配器。

使用 PaddleX 官方服务协议 `POST /layout-parsing`。这里只做协议适配和统一
坐标块转换，不在 Mangrove 内实现 OCR、版面分析或表格识别算法。单独的
OpenAI/vLLM 兼容端点只负责 VLM 识别阶段，不能冒充完整解析服务。
"""
from __future__ import annotations

import base64
from typing import Any, Dict, Mapping, Optional

import httpx

from src.config.settings import settings
from src.services.document_parser_contracts import (
    DocumentPageBlock,
    DocumentParseResult,
    DocumentParserHealth,
    DocumentParserServiceError,
)
from src.services.document_parser_resilience import document_parser_request_slot


def _bbox(value: Any) -> Optional[tuple[float, float, float, float]]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if min(x0, y0) < 0 or x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _confidence(value: Any) -> float:
    if value is None:
        return 1.0
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(score, 1.0))


def _page_dimensions(
    data_info: Any,
    page_index: int,
) -> Optional[tuple[float, float]]:
    """读取 Paddle dataInfo 中的原始页宽高。"""
    if not isinstance(data_info, Mapping):
        return None
    pages = data_info.get("pages")
    if not isinstance(pages, list) or page_index >= len(pages):
        return None
    page = pages[page_index]
    if not isinstance(page, Mapping):
        return None
    try:
        width = float(page.get("width"))
        height = float(page.get("height"))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _normalize_bbox(
    box: tuple[float, float, float, float],
    dimensions: Optional[tuple[float, float]],
) -> tuple[tuple[float, float, float, float], str]:
    """有页尺寸时归一到 0..1000，供前端跨分辨率稳定叠框。"""
    if dimensions is None:
        return box, "image_pixels"
    width, height = dimensions
    x0, y0, x1, y1 = box
    if x0 >= width or y0 >= height:
        return box, "image_pixels"
    return (
        (
            min(1000.0, x0 / width * 1000.0),
            min(1000.0, y0 / height * 1000.0),
            min(1000.0, x1 / width * 1000.0),
            min(1000.0, y1 / height * 1000.0),
        ),
        "normalized_1000",
    )


class PaddleOCRVLDocumentClient:
    """PaddleOCR-VL 1.6 完整 API 产线客户端。"""

    provider = "paddleocr_vl"

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        model_version: Optional[str] = None,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self.base_url = (base_url or settings.paddleocr_vl_base_url).rstrip("/")
        self.model_version = (
            model_version or settings.paddleocr_vl_model_version
        )
        self.backend = self.model_version
        configured_endpoint = endpoint or settings.paddleocr_vl_endpoint
        self.endpoint = "/" + configured_endpoint.strip("/")
        self.api_key = (
            api_key if api_key is not None else settings.paddleocr_vl_api_key
        )
        self.timeout_seconds = float(
            timeout_seconds or settings.paddleocr_vl_timeout_seconds
        )
        self._client = http_client

    def _request_client(self) -> tuple[httpx.Client, bool]:
        if self._client is not None:
            return self._client, False
        timeout = httpx.Timeout(
            self.timeout_seconds,
            connect=min(5.0, self.timeout_seconds),
        )
        return httpx.Client(timeout=timeout, trust_env=False), True

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        client, should_close = self._request_client()
        headers = dict(kwargs.pop("headers", {}) or {})
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = client.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise DocumentParserServiceError(
                f"PaddleOCR-VL 请求失败: {exc}"
            ) from exc
        finally:
            if should_close:
                client.close()
        if response.is_error:
            try:
                body = response.json()
            except ValueError:
                body = response.text
            raise DocumentParserServiceError(
                f"PaddleOCR-VL HTTP {response.status_code}: {body}"
            )
        return response

    def health(self) -> DocumentParserHealth:
        """通过 OpenAPI 确认目标确实提供完整 `/layout-parsing` 能力。"""
        response = self._request("GET", "/openapi.json")
        try:
            payload = response.json()
        except ValueError as exc:
            raise DocumentParserServiceError(
                "PaddleOCR-VL /openapi.json 未返回合法 JSON"
            ) from exc
        paths = payload.get("paths") if isinstance(payload, Mapping) else None
        if not isinstance(paths, Mapping) or self.endpoint not in paths:
            if isinstance(paths, Mapping) and "/v1/chat/completions" in paths:
                raise DocumentParserServiceError(
                    "当前地址仅是 PaddleOCR-VL 的 VLM 推理子服务，不是完整 "
                    "Pipeline；请配置提供 /layout-parsing 的服务地址"
                )
            raise DocumentParserServiceError(
                f"PaddleOCR-VL 服务未提供 {self.endpoint}"
            )
        return DocumentParserHealth(
            status="healthy",
            version=self.model_version,
        )

    def parse_pdf(self, raw_bytes: bytes, *, filename: str) -> DocumentParseResult:
        return self._parse_file(raw_bytes, file_type=0)

    def parse_image(self, raw_bytes: bytes, *, filename: str) -> DocumentParseResult:
        """解析 PNG/JPEG/WEBP 图片；PaddleX 协议中 fileType=1。"""
        return self._parse_file(raw_bytes, file_type=1)

    def _parse_file(
        self,
        raw_bytes: bytes,
        *,
        file_type: int,
    ) -> DocumentParseResult:
        payload = {
            "file": base64.b64encode(raw_bytes).decode("ascii"),
            "fileType": file_type,
            "useLayoutDetection": True,
            "useSealRecognition": True,
            "useOcrForImageBlock": True,
            "formatBlockContent": True,
            "temperature": 0,
            "prettifyMarkdown": True,
            "returnMarkdownImages": False,
            "restructurePages": False,
            "visualize": False,
        }
        with document_parser_request_slot():
            response = self._request("POST", self.endpoint, json=payload)
        try:
            raw_response = response.json()
        except ValueError as exc:
            raise DocumentParserServiceError(
                "PaddleOCR-VL 未返回合法 JSON"
            ) from exc
        if not isinstance(raw_response, dict):
            raise DocumentParserServiceError(
                "PaddleOCR-VL 响应必须是 JSON 对象"
            )
        return self.parse_response(raw_response)

    def parse_response(self, raw_response: Dict[str, Any]) -> DocumentParseResult:
        error_code = raw_response.get("errorCode", 0)
        if error_code not in (0, "0", None):
            detail = raw_response.get("errorMsg") or f"errorCode={error_code}"
            raise DocumentParserServiceError(
                f"PaddleOCR-VL 解析失败: {detail}"
            )
        result = raw_response.get("result")
        if not isinstance(result, Mapping):
            raise DocumentParserServiceError(
                "PaddleOCR-VL 响应缺少 result 对象"
            )
        pages = result.get("layoutParsingResults")
        if not isinstance(pages, list):
            raise DocumentParserServiceError(
                "PaddleOCR-VL 响应缺少 layoutParsingResults"
            )

        data_info = result.get("dataInfo")
        blocks: list[DocumentPageBlock] = []
        for page_index, page_result in enumerate(pages):
            if not isinstance(page_result, Mapping):
                continue
            pruned = page_result.get("prunedResult")
            if not isinstance(pruned, Mapping):
                continue
            parsing_blocks = pruned.get("parsing_res_list")
            if not isinstance(parsing_blocks, list):
                continue
            for block in parsing_blocks:
                if not isinstance(block, Mapping):
                    continue
                text = str(block.get("block_content") or "").strip()
                box = _bbox(block.get("block_bbox"))
                # Markdown 没有确定性坐标时不能进入证据块。
                if not text or box is None:
                    continue
                normalized_box, coordinate_space = _normalize_bbox(
                    box,
                    _page_dimensions(data_info, page_index),
                )
                label = str(block.get("block_label") or "text").lower()
                blocks.append(DocumentPageBlock(
                    page=page_index + 1,
                    text=text,
                    bbox=normalized_box,
                    coordinate_space=coordinate_space,
                    confidence=_confidence(
                        block.get("score", block.get("confidence"))
                    ),
                    element_type=label,
                ))

        return DocumentParseResult(
            task_id=str(raw_response.get("logId") or ""),
            backend=self.backend,
            version=self.model_version,
            blocks=tuple(blocks),
            raw_response=dict(raw_response),
            provider=self.provider,
        )
