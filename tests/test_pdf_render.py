# -*- coding: utf-8 -*-
"""PDF 页渲染使用宽松许可的 pypdfium2。"""
from __future__ import annotations

from src.parsers.pdf_render import render_pdf_page_png


def test_pdf_render_rejects_oversized_page_before_bitmap(monkeypatch):
    import io
    from pypdf import PdfWriter
    import pytest
    import pypdfium2 as pdfium

    writer = PdfWriter()
    writer.add_blank_page(width=10000, height=10000)
    buffer = io.BytesIO()
    writer.write(buffer)
    # 红测也不能实际分配恶意尺寸位图。
    monkeypatch.setattr(pdfium.PdfPage, "render", lambda *args, **kwargs: pytest.fail("超限页进入位图分配"))
    with pytest.raises(ValueError, match="像素"):
        render_pdf_page_png(buffer.getvalue(), page_number=1)

from tests.test_pdf_office_parsers import _make_pdf_bytes


def test_render_pdf_page_to_png() -> None:
    image = render_pdf_page_png(_make_pdf_bytes(["Render me"]), page_number=1, dpi=144)
    assert image.startswith(b"\x89PNG\r\n\x1a\n")


def test_pdf_source_rejects_encryption():
    import io
    import pytest
    from pypdf import PdfWriter
    from src.parsers.pdf_render import validate_pdf_source

    encrypted = PdfWriter()
    encrypted.add_blank_page(width=100, height=100)
    encrypted.encrypt("synthetic-password")
    buffer = io.BytesIO()
    encrypted.write(buffer)
    with pytest.raises(ValueError, match="加密"):
        validate_pdf_source(buffer.getvalue())


def test_pdf_source_rejects_owner_only_encryption():
    import io
    import pytest
    from pypdf import PdfWriter
    from src.parsers.pdf_render import validate_pdf_source

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt(user_password="", owner_password="synthetic-owner-password")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(ValueError, match="加密"):
        validate_pdf_source(buffer.getvalue())


def test_pdf_source_rejects_page_limit():
    import io
    import pytest
    from pypdf import PdfWriter
    from src.parsers.pdf_render import validate_pdf_source

    oversized = PdfWriter()
    for _ in range(1001):
        oversized.add_blank_page(width=100, height=100)
    buffer = io.BytesIO()
    oversized.write(buffer)
    with pytest.raises(ValueError, match="1000"):
        validate_pdf_source(buffer.getvalue())
