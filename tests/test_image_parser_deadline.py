"""图片阶段的剩余预算必须约束同步 OCR 的排队和请求。"""
import time

import httpx
import pytest

from src.config.settings import settings
from src.services import document_parser_resilience as resilience
from src.services.mineru_document import MinerUDocumentClient
from src.services.paddleocr_vl_document import PaddleOCRVLDocumentClient


@pytest.mark.parametrize('client_type', [MinerUDocumentClient, PaddleOCRVLDocumentClient])
def test_expired_image_budget_does_not_send_health_request(client_type):
    calls = []
    transport = httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200, json={}))
    with httpx.Client(transport=transport) as http:
        client = client_type(base_url='http://ocr.test', http_client=http, timeout_seconds=600)
        with resilience.document_parser_deadline(time.monotonic() - 1):
            with pytest.raises(TimeoutError):
                client.health()
    assert not calls


def test_queue_wait_uses_remaining_image_budget(monkeypatch):
    monkeypatch.setattr(settings, 'document_parser_max_concurrency', 1)
    gate = resilience._gate(1)
    assert gate.acquire(blocking=False)
    start = time.monotonic()
    try:
        with resilience.document_parser_deadline(start + .03):
            with pytest.raises(TimeoutError):
                with resilience.document_parser_request_slot():
                    pytest.fail('预算内未取得槽位，不得开始解析')
        assert time.monotonic() - start < .3
    finally:
        gate.release()
    with resilience.document_parser_request_slot():
        pass


def test_revoked_execution_does_not_send_ocr_request(monkeypatch):
    from src.api import execution
    def denied():
        raise PermissionError('任务已停止')
    monkeypatch.setattr(execution, 'execution_checkpoint', denied)
    calls = []
    with httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200))) as http:
        client = PaddleOCRVLDocumentClient(base_url='http://ocr.test', http_client=http)
        with resilience.document_parser_deadline(time.monotonic() + 10):
            with pytest.raises(PermissionError):
                client._request('POST', '/layout-parsing')
    assert not calls


@pytest.mark.parametrize('client_type', [MinerUDocumentClient, PaddleOCRVLDocumentClient])
def test_request_timeout_is_bounded_only_inside_image_budget(client_type):
    timeouts = []
    def handle(request):
        timeouts.append(request.extensions['timeout']['read'])
        return httpx.Response(200, json={})
    with httpx.Client(transport=httpx.MockTransport(handle), timeout=25) as http:
        client = client_type(base_url='http://ocr.test', http_client=http, timeout_seconds=600)
        with resilience.document_parser_deadline(time.monotonic() + .1):
            client._request('GET', '/probe')
        client._request('GET', '/probe')
    assert 0 < timeouts[0] <= .1
    assert timeouts[1] == 25
