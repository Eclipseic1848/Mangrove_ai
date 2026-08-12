# -*- coding: utf-8 -*-
"""使用 pypdfium2 将 PDF 页确定性渲染为 PNG。"""
from __future__ import annotations

import io


def render_pdf_page_png(raw_bytes: bytes, *, page_number: int, dpi: int = 200) -> bytes:
    """渲染 1-based 页码；供 PaddleOCR/Qwen 共用同一页图。"""
    if page_number < 1:
        raise ValueError("page_number 必须从 1 开始")
    if dpi < 72:
        raise ValueError("dpi 不得低于 72")

    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(raw_bytes)
    try:
        if page_number > len(document):
            raise IndexError(f"PDF 仅有 {len(document)} 页，无法渲染第 {page_number} 页")
        page = document[page_number - 1]
        try:
            bitmap = page.render(scale=dpi / 72.0)
            image = bitmap.to_pil()
            output = io.BytesIO()
            image.save(output, format="PNG", optimize=True)
            return output.getvalue()
        finally:
            page.close()
    finally:
        document.close()
