# -*- coding: utf-8 -*-
"""使用 pypdfium2 将 PDF 页确定性渲染为 PNG。"""
from __future__ import annotations

import io
import math

from src.services.upload_store import MAX_IMAGE_PIXELS


def validate_pdf_source(raw_bytes: bytes) -> int:
    """在文字提取/OCR 前检查页面资源，不分配位图或改写原件。"""
    import pypdfium2 as pdfium
    from src.config.settings import settings

    if len(raw_bytes) > settings.data_prep_max_upload_bytes:
        raise ValueError("PDF 超过上传字节上限")
    try:
        with pdfium.PdfDocument(raw_bytes) as document:
            # 空用户密码也可能打开加密文档，不能用“打开成功”代替加密检查。
            if pdfium.raw.FPDF_GetSecurityHandlerRevision(document) != -1:
                raise ValueError("PDF 已加密，无法安全读取")
            count = len(document)
            if not 1 <= count <= 1000:
                raise ValueError("PDF 页数须为 1 至 1000 页")
            for number in range(count):
                width, height = document.get_page_size(number)
                _validate_render_size(width, height, 200)
            return count
    except pdfium.PdfiumError as exc:
        raise ValueError("PDF 损坏或已加密，无法安全读取") from exc


def _validate_render_size(width: float, height: float, dpi: int) -> None:
    if not all(math.isfinite(value) and value > 0 for value in (width, height, dpi)):
        raise ValueError("PDF 页面尺寸或 DPI 无效")
    if math.ceil(width * dpi / 72) * math.ceil(height * dpi / 72) > MAX_IMAGE_PIXELS:
        raise ValueError("PDF 页面超过 1600 万解码像素上限")


def render_pdf_page_png(raw_bytes: bytes, *, page_number: int, dpi: int = 200) -> bytes:
    """渲染 1-based 页码；供 PaddleOCR/Qwen 共用同一页图。"""
    if page_number < 1:
        raise ValueError("page_number 必须从 1 开始")
    if dpi < 72:
        raise ValueError("dpi 不得低于 72")
    validate_pdf_source(raw_bytes)

    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(raw_bytes)
    try:
        if page_number > len(document):
            raise IndexError(f"PDF 仅有 {len(document)} 页，无法渲染第 {page_number} 页")
        page = document[page_number - 1]
        try:
            _validate_render_size(*page.get_size(), dpi)
            bitmap = page.render(scale=dpi / 72.0)
            try:
                with bitmap.to_pil() as image:
                    output = io.BytesIO()
                    image.save(output, format="PNG", optimize=True)
                    return output.getvalue()
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        document.close()
