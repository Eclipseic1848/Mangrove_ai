# -*- coding: utf-8 -*-
"""外部文档解析请求的并发保护测试。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

from src.config.settings import settings
from src.services.document_parser_resilience import document_parser_request_slot


def test_document_parser_request_slot_limits_cross_task_concurrency(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "document_parser_max_concurrency", 1)
    lock = threading.Lock()
    active = 0
    maximum = 0

    def worker() -> None:
        nonlocal active, maximum
        with document_parser_request_slot():
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.03)
            with lock:
                active -= 1

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: worker(), range(4)))

    assert maximum == 1


def test_unlimited_image_keeps_single_attempt_and_cancel_signal():
    import pytest
    from src.services.document_parser_resilience import (
        document_parser_deadline, document_parser_remaining_timeout,
        document_parser_single_attempt, document_parser_stop_event,
    )
    with document_parser_deadline(None, single_attempt=True):
        assert document_parser_single_attempt()
        assert document_parser_remaining_timeout(60) is None
        document_parser_stop_event().set()
        with pytest.raises(TimeoutError, match="已停止"):
            document_parser_remaining_timeout(60)
    assert not document_parser_single_attempt()
    assert document_parser_stop_event() is None
