# -*- coding: utf-8 -*-
"""外部文档解析服务的统一轻量契约。"""
from __future__ import annotations

from dataclasses import dataclass
import math
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
    confidence_known: bool = True

    def __post_init__(self) -> None:
        try:
            score = float(self.confidence)
            known = self.confidence_known and not isinstance(self.confidence, bool) and math.isfinite(score) and 0 <= score <= 1
        except (TypeError, ValueError):
            score, known = 0.0, False
        # 未知分数的零值仅供既有质量门失败关闭，不表示模型测得零分。
        object.__setattr__(self, "confidence", score if known else 0.0)
        object.__setattr__(self, "confidence_known", known)


@dataclass(frozen=True)
class DocumentParseResult:
    task_id: str
    backend: str
    version: str
    blocks: Tuple[DocumentPageBlock, ...]
    raw_response: Dict[str, Any]
    provider: str = ""
    source_coordinates_verified: bool = False


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
