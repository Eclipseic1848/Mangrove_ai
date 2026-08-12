# -*- coding: utf-8 -*-
"""PDF 页渲染使用宽松许可的 pypdfium2。"""
from __future__ import annotations

from src.parsers.pdf_render import render_pdf_page_png

from tests.test_pdf_office_parsers import _make_pdf_bytes


def test_render_pdf_page_to_png() -> None:
    image = render_pdf_page_png(_make_pdf_bytes(["Render me"]), page_number=1, dpi=144)
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
