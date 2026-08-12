# -*- coding: utf-8 -*-
"""外部文档解析服务的统一轻量契约。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, Tuple, runtime_checkable


class DocumentParserServiceError(RuntimeError):
    """外部文档解析服务不可达、拒绝请求或返回无效响应。"""


@dataclass(frozen=True)
class DocumentParserHealth:
    status: str
    version: str
    protocol_version: Optional[int] = None


@dataclass(frozen=True)
class DocumentPageBlock:
    page: int
    text: str
    bbox: Tuple[float, float, float, float]
    coordinate_space: str
    confidence: float
    element_type: str = "text"


@dataclass(frozen=True)
class DocumentParseResult:
    task_id: str
    backend: str
    version: str
    blocks: Tuple[DocumentPageBlock, ...]
    raw_response: Dict[str, Any]
    provider: str = ""


@runtime_checkable
class DocumentParserClient(Protocol):
    """PdfParser 所需的最小服务接口；第三方 SDK 对象不得越过此边界。"""

    provider: str
    base_url: str
    backend: str

    def health(self) -> DocumentParserHealth:
        ...

    def parse_pdf(self, raw_bytes: bytes, *, filename: str) -> DocumentParseResult:
        ...

    def parse_response(self, raw_response: Dict[str, Any]) -> DocumentParseResult:
        ...
