# -*- coding: utf-8 -*-
"""MinerU 文档解析服务适配器。

第三方响应在本模块边界内转换为轻量 DTO；调用方只接收文本、坐标和置信度，
不把 MinerU 私有对象放进 LangGraph state。
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

import httpx

from src.config.settings import settings
from src.services.document_parser_contracts import (
    DocumentPageBlock,
    DocumentParseResult,
    DocumentParserHealth,
    DocumentParserServiceError,
)
from src.services.document_parser_resilience import document_parser_request_slot


class MinerUServiceError(DocumentParserServiceError):
    """MinerU 不可达、拒绝请求或返回无效响应。"""


# 兼容既有导入名；实际契约已提升为服务无关 DTO。
MinerUHealth = DocumentParserHealth
MinerUPageBlock = DocumentPageBlock
MinerUParseResult = DocumentParseResult


def _json_value(value: Any, *, default: Any) -> Any:
    """兼容 MinerU 3.x 将嵌套结果序列化为 JSON 字符串的响应格式。"""
    if value is None or value == "":
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _bbox(value: Any) -> Optional[Tuple[float, float, float, float]]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if min(x0, y0) < 0 or x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _normalize_bbox(
    box: Tuple[float, float, float, float],
    *,
    width: Any,
    height: Any,
) -> Optional[Tuple[float, float, float, float]]:
    try:
        page_width = float(width)
        page_height = float(height)
    except (TypeError, ValueError):
        return None
    if page_width <= 0 or page_height <= 0:
        return None
    x0, y0, x1, y1 = box
    return (
        x0 * 1000.0 / page_width,
        y0 * 1000.0 / page_height,
        x1 * 1000.0 / page_width,
        y1 * 1000.0 / page_height,
    )


def _confidence(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(score, 1.0))


def _result_items(payload: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    results = payload.get("results")
    if not isinstance(results, Mapping):
        return ()
    return (item for item in results.values() if isinstance(item, Mapping))


def _blocks_from_model_output(item: Mapping[str, Any]) -> list[MinerUPageBlock]:
    blocks: list[MinerUPageBlock] = []
    model_output = _json_value(item.get("model_output"), default=[])
    if not isinstance(model_output, list):
        return blocks
    for page_result in model_output:
        if not isinstance(page_result, Mapping):
            continue
        page_info = page_result.get("page_info")
        page_index = page_info.get("page_no", 0) if isinstance(page_info, Mapping) else 0
        try:
            page = int(page_index) + 1
        except (TypeError, ValueError):
            page = 1
        detections = page_result.get("layout_dets")
        if not isinstance(detections, list):
            continue
        for detection in detections:
            if not isinstance(detection, Mapping):
                continue
            label = str(detection.get("label") or "").lower()
            if label not in {"ocr_text", "text"}:
                continue
            text = str(
                detection.get("text")
                or detection.get("content")
                or detection.get("latex")
                or ""
            ).strip()
            box = _bbox(detection.get("bbox"))
            if not text or box is None:
                continue
            normalized_box = (
                _normalize_bbox(
                    box,
                    width=page_info.get("width"),
                    height=page_info.get("height"),
                )
                if isinstance(page_info, Mapping)
                else None
            )
            blocks.append(MinerUPageBlock(
                page=page,
                text=text,
                bbox=normalized_box or box,
                coordinate_space=(
                    "normalized_1000" if normalized_box else "image_pixels"
                ),
                confidence=_confidence(detection.get("score", 1.0)),
                element_type="text",
            ))
    return blocks


def _blocks_from_content_list(item: Mapping[str, Any]) -> list[MinerUPageBlock]:
    """补充表格/图片等结构块；普通文本以 model_output 的像素坐标为准，避免重复。"""
    blocks: list[MinerUPageBlock] = []
    content_list = _json_value(item.get("content_list"), default=[])
    if not isinstance(content_list, list):
        return blocks
    for content in content_list:
        if not isinstance(content, Mapping):
            continue
        content_type = str(content.get("type") or "").lower()
        if content_type not in {"table", "image"}:
            continue
        text = str(
            content.get("table_body")
            or content.get("image_caption")
            or content.get("text")
            or ""
        ).strip()
        box = _bbox(content.get("bbox"))
        if not text or box is None:
            continue
        try:
            page = int(content.get("page_idx", 0)) + 1
        except (TypeError, ValueError):
            page = 1
        blocks.append(MinerUPageBlock(
            page=page,
            text=text,
            bbox=box,
            coordinate_space="normalized_1000",
            confidence=_confidence(content.get("score", 1.0)),
            element_type=content_type,
        ))
    return blocks


def _blocks_from_middle_json(item: Mapping[str, Any]) -> list[MinerUPageBlock]:
    blocks: list[MinerUPageBlock] = []
    middle = _json_value(item.get("middle_json"), default={})
    if not isinstance(middle, Mapping):
        return blocks
    pdf_info = middle.get("pdf_info")
    if not isinstance(pdf_info, list):
        return blocks
    for page_result in pdf_info:
        if not isinstance(page_result, Mapping):
            continue
        try:
            page = int(page_result.get("page_idx", 0)) + 1
        except (TypeError, ValueError):
            page = 1
        para_blocks = page_result.get("para_blocks")
        if not isinstance(para_blocks, list):
            continue
        for para in para_blocks:
            if not isinstance(para, Mapping):
                continue
            element_type = str(para.get("type") or "text").lower()
            for line in para.get("lines") or []:
                if not isinstance(line, Mapping):
                    continue
                for span in line.get("spans") or []:
                    if not isinstance(span, Mapping):
                        continue
                    text = str(span.get("content") or span.get("text") or "").strip()
                    box = _bbox(span.get("bbox"))
                    if not text or box is None:
                        continue
                    blocks.append(MinerUPageBlock(
                        page=page,
                        text=text,
                        bbox=box,
                        coordinate_space="pdf_points",
                        confidence=_confidence(span.get("score", 1.0)),
                        element_type=element_type,
                    ))
    return blocks


class MinerUDocumentClient:
    """MinerU 3.x `/file_parse` 同步调用客户端。"""

    provider = "mineru"

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        backend: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self.base_url = (base_url or settings.mineru_base_url).rstrip("/")
        self.backend = backend or settings.mineru_backend
        self.timeout_seconds = float(timeout_seconds or settings.mineru_timeout_seconds)
        self._client = http_client

    def _request_client(self) -> tuple[httpx.Client, bool]:
        if self._client is not None:
            return self._client, False
        timeout = httpx.Timeout(self.timeout_seconds, connect=min(5.0, self.timeout_seconds))
        return httpx.Client(timeout=timeout, trust_env=False), True

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        client, should_close = self._request_client()
        try:
            response = client.request(method, f"{self.base_url}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise MinerUServiceError(f"MinerU 请求失败: {exc}") from exc
        finally:
            if should_close:
                client.close()
        if response.is_error:
            try:
                body = response.json()
            except ValueError:
                body = response.text
            if isinstance(body, Mapping):
                detail = body.get("error") or body.get("detail") or body.get("message") or body
            else:
                detail = body
            raise MinerUServiceError(f"MinerU HTTP {response.status_code}: {detail}")
        return response

    def health(self) -> MinerUHealth:
        try:
            payload = self._request("GET", "/health").json()
        except ValueError as exc:
            raise MinerUServiceError("MinerU /health 未返回合法 JSON") from exc
        if not isinstance(payload, Mapping):
            raise MinerUServiceError("MinerU /health 响应必须是 JSON 对象")
        protocol_version = payload.get("protocol_version")
        return MinerUHealth(
            status=str(payload.get("status") or "unknown"),
            version=str(payload.get("version") or "unknown"),
            protocol_version=int(protocol_version) if protocol_version is not None else None,
        )

    def parse_pdf(self, raw_bytes: bytes, *, filename: str) -> MinerUParseResult:
        with document_parser_request_slot():
            response = self._request(
                "POST",
                "/file_parse",
                files={"files": (filename, raw_bytes, "application/pdf")},
                data={
                    "backend": self.backend,
                    "parse_method": "ocr",
                    "lang_list": "ch",
                    "formula_enable": "true",
                    "table_enable": "true",
                    "return_md": "true",
                    "return_middle_json": "true",
                    "return_model_output": "true",
                    "return_content_list": "true",
                    "return_images": "false",
                    "response_format_zip": "false",
                },
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise MinerUServiceError("MinerU /file_parse 未返回合法 JSON") from exc
        if not isinstance(payload, dict):
            raise MinerUServiceError("MinerU /file_parse 响应必须是 JSON 对象")
        return self.parse_response(payload)

    def parse_response(self, raw_response: Dict[str, Any]) -> MinerUParseResult:
        if str(raw_response.get("status") or "").lower() == "failed":
            detail = raw_response.get("error") or raw_response.get("message") or "未知故障"
            raise MinerUServiceError(f"MinerU 解析失败: {detail}")

        blocks: list[MinerUPageBlock] = []
        items = tuple(_result_items(raw_response))
        for item in items:
            item_blocks = _blocks_from_model_output(item)
            if not item_blocks:
                item_blocks = _blocks_from_middle_json(item)
            item_blocks.extend(_blocks_from_content_list(item))
            blocks.extend(item_blocks)

        return MinerUParseResult(
            task_id=str(raw_response.get("task_id") or ""),
            backend=str(raw_response.get("backend") or self.backend),
            version=str(raw_response.get("version") or "unknown"),
            blocks=tuple(blocks),
            raw_response=dict(raw_response),
            provider=self.provider,
        )
