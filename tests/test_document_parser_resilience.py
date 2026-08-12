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
