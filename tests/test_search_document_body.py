"""严格来源读取须解析文档字节，不能把二进制解码成网页正文。"""
import io
import socket
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from src.collectors import search_collector as module
from src.collectors.base import CollectedItem


@pytest.fixture(autouse=True)
def no_external_requests(monkeypatch):
    attempts = []
    original_connect = socket.socket.connect
    original_resolve = socket.getaddrinfo

    def connect(sock, address):
        if address[0] not in {"127.0.0.1", "::1", "localhost"}:
            attempts.append(address)
            raise AssertionError("文档正文测试禁止外部请求")
        return original_connect(sock, address)

    def resolve(host, *args, **kwargs):
        if host not in {"127.0.0.1", "::1", "localhost"}:
            attempts.append(host)
            raise AssertionError("文档正文测试禁止外部请求")
        return original_resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(module, "get_rate_limiter", lambda: AsyncMock())
    yield
    assert not attempts, "测试曾尝试访问外部来源，已拦截"


def pdf_bytes(*pages):
    from reportlab.pdfgen.canvas import Canvas

    output = io.BytesIO()
    document = Canvas(output)
    for text in pages:
        document.drawString(72, 720, text)
        document.showPage()
    document.save()
    return output.getvalue()


def respond_with(monkeypatch, body, media_type):
    requests = []

    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=body, headers={"Content-Type": media_type})

    monkeypatch.setattr(module, "smart_client", lambda *args, **kwargs: httpx.AsyncClient(
        transport=httpx.MockTransport(respond)))
    return requests


@pytest.mark.asyncio
@pytest.mark.parametrize("media_type", ["application/pdf", "application/octet-stream", "text/html"])
async def test_candidate_pdf_is_extracted_from_bytes_instead_of_html(monkeypatch, media_type):
    raw = pdf_bytes("Synthetic medical equipment notice", "FINAL PAGE EVIDENCE")
    requests = respond_with(monkeypatch, raw, media_type)
    collector = module.SearchDiscoveryCollector()
    item = CollectedItem(url="https://documents.example/notice.pdf?download=1", content="检索摘要")
    failures = {}
    result = await collector._read_project_pages([item], "", module.time.monotonic() + 30, failures)
    assert result[0].metadata["body_status"] == "complete"
    assert "Synthetic medical equipment notice" in result[0].content
    assert "FINAL PAGE EVIDENCE" in result[0].content
    assert "%PDF" not in result[0].content and len(result[0].content) < 1000
    assert len(requests) == 1 and failures == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("media_type", ["application/zip", "application/octet-stream", "text/html"])
async def test_other_binary_never_becomes_complete_body_or_repeats(monkeypatch, media_type):
    requests = respond_with(monkeypatch, b"PK\x03\x04" + b"\x00\xff" * 500, media_type)
    collector = module.SearchDiscoveryCollector()
    url = "https://documents.example/download"
    failures = {}
    result = await collector._read_project_pages([CollectedItem(url=url)], "", module.time.monotonic() + 30, failures)
    assert result[0].metadata["body_status"] == "unknown"
    assert result[0].metadata["body_error_code"] == "SOURCE_BINARY_UNSUPPORTED"
    assert result[0].content == "" and failures[url] == "SOURCE_BINARY_UNSUPPORTED"
    await collector._read_project_pages([CollectedItem(url=url)], "", module.time.monotonic() + 30, failures)
    assert requests == [url], "未知文档不得由下一后端再次读取"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["damaged", "blank", "mixed"])
async def test_unreadable_pdf_keeps_diagnostic_instead_of_summary(monkeypatch, kind):
    raw = b"%PDF-1.7\ninvalid" if kind == "damaged" else pdf_bytes(*([""] if kind == "blank" else ["Known page", ""]))
    requests = respond_with(monkeypatch, raw, "application/pdf")
    url = "https://documents.example/notice.pdf"
    failures = {}
    result = await module.SearchDiscoveryCollector()._read_project_pages(
        [CollectedItem(url=url, content="原摘要不能冒充已解析正文")], "", module.time.monotonic() + 30, failures)
    expected = "SOURCE_PDF_PARSE_FAILED" if kind == "damaged" else "SOURCE_PDF_TEXT_INCOMPLETE"
    assert result[0].metadata["body_status"] == "unknown" and result[0].content == ""
    assert result[0].metadata["body_error_code"] == failures[url] == expected
    assert requests == [url]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["pdf", "binary"])
async def test_strict_http_fallback_does_not_decode_binary_as_html(monkeypatch, kind):
    monkeypatch.setitem(sys.modules, "crawl4ai", None)
    raw = pdf_bytes("FINAL PDF BODY") if kind == "pdf" else b"PK\x03\x04\x00\xff" * 100
    requests = respond_with(monkeypatch, raw, "application/octet-stream")
    failures = {}
    result = await module.SearchDiscoveryCollector()._fetch_all(
        ["https://documents.example/download"], module.time.monotonic() + 30, strict=True, body_failures=failures)
    if kind == "pdf":
        assert result[0].metadata["body_status"] == "complete" and "FINAL PDF BODY" in result[0].content
    else:
        assert result[0].metadata["body_status"] == "unknown" and result[0].content == ""
        assert result[0].metadata["body_error_code"] == "SOURCE_BINARY_UNSUPPORTED"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_known_pdf_uses_existing_local_parser_before_browser(monkeypatch):
    class Browser:
        def __init__(self, **kwargs):
            raise AssertionError("PDF 应直接解析原始字节，不启动网页渲染")

    monkeypatch.setitem(sys.modules, "crawl4ai", SimpleNamespace(AsyncWebCrawler=Browser))
    requests = respond_with(monkeypatch, pdf_bytes("LOCAL PDF EVIDENCE"), "application/pdf")
    result = await module.SearchDiscoveryCollector()._fetch_all(
        ["https://documents.example/notice.pdf?download=1"], module.time.monotonic() + 30,
        strict=True, body_failures={})
    assert result[0].metadata["body_status"] == "complete" and "LOCAL PDF EVIDENCE" in result[0].content
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_browser_pdf_response_cannot_be_published_as_markdown(monkeypatch):
    class Browser:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def arun(self, **kwargs):
            return SimpleNamespace(success=True, markdown="%PDF-1.7 decoded binary", html="%PDF-1.7 decoded binary",
                response_headers={"content-type": "application/pdf"}, metadata={})

    monkeypatch.setitem(sys.modules, "crawl4ai", SimpleNamespace(AsyncWebCrawler=Browser))
    monkeypatch.setattr(module, "crawler_browser_config", lambda: None)
    failures = {}
    result = await module.SearchDiscoveryCollector()._fetch_all(
        ["https://documents.example/download"], module.time.monotonic() + 30, strict=True, body_failures=failures)
    assert result[0].metadata["body_status"] == "unknown" and result[0].content == ""
    assert failures["https://documents.example/download"] == "SOURCE_PDF_PARSE_FAILED"


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_regular_html_body_keeps_existing_extraction(monkeypatch, fallback):
    monkeypatch.setitem(sys.modules, "crawl4ai", None)
    html = "<article><h1>合成正式公告</h1><p>" + "医疗设备采购正文。" * 60 + "完整正文末尾</p></article>"
    requests = respond_with(monkeypatch, html.encode("utf-8"), "text/html; charset=utf-8")
    collector = module.SearchDiscoveryCollector()
    url = "https://documents.example/notice.html"
    deadline = module.time.monotonic() + 30
    if fallback:
        result = await collector._fetch_all([url], deadline, strict=True, body_failures={})
    else:
        result = await collector._read_project_pages([CollectedItem(url=url)], "", deadline, {})
    assert result[0].metadata["body_status"] == "complete"
    assert "医疗设备采购正文" in result[0].content and "完整正文末尾" in result[0].content
    assert requests == [url]


@pytest.mark.asyncio
async def test_pdf_parser_does_not_swallow_execution_denial(monkeypatch):
    from src.account_execution import ExecutionAuthorization, ExecutionDenied, execution_context
    from src.api.execution import execution_validation

    respond_with(monkeypatch, pdf_bytes("Frozen owner evidence"), "application/pdf")
    def deny(auth):
        raise ExecutionDenied("合成撤权")

    with execution_context(ExecutionAuthorization("synthetic-owner", 0)), execution_validation(deny):
        with pytest.raises(ExecutionDenied):
            await module.SearchDiscoveryCollector()._read_project_pages(
                [CollectedItem(url="https://documents.example/notice.pdf")], "", module.time.monotonic() + 30, {})
