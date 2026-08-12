# -*- coding: utf-8 -*-
"""数字页、扫描页和混合页的确定性优先级路由。"""
from __future__ import annotations

from dataclasses import dataclass

from src.data_prep.document_models import PageContentKind


@dataclass(frozen=True)
class PageSignals:
    text_chars: int
    image_coverage: float = 0.0


@dataclass(frozen=True)
class PageRouteDecision:
    page_kind: PageContentKind
    primary_backend: str
    fallback_backends: tuple[str, ...]
    review_backends: tuple[str, ...]
    reason: str


def route_page(signals: PageSignals) -> PageRouteDecision:
    """准确率优先：结构解析/OCR 定位在前，Qwen 负责复核与语义候选。"""
    text_chars = max(0, signals.text_chars)
    image_coverage = min(1.0, max(0.0, signals.image_coverage))
    if text_chars == 0:
        return PageRouteDecision(
            page_kind=PageContentKind.SCANNED,
            primary_backend="paddleocr",
            fallback_backends=("qwen_vl",),
            review_backends=("qwen_vl",),
            reason="页面无可用数字文本，优先坐标型 OCR",
        )
    if image_coverage >= 0.65:
        return PageRouteDecision(
            page_kind=PageContentKind.MIXED,
            primary_backend="docling+paddleocr",
            fallback_backends=("pdfium-text", "qwen_vl"),
            review_backends=("qwen_vl",),
            reason="页面同时含数字文本和大面积图像，分别解析后合并",
        )
    return PageRouteDecision(
        page_kind=PageContentKind.DIGITAL,
        primary_backend="docling",
        fallback_backends=("pdfium-text", "paddleocr"),
        review_backends=(),
        reason="页面存在可用数字文本，优先结构化解析",
    )
