# -*- coding: utf-8 -*-
"""文档解析外部请求的进程内并发保护。"""
from __future__ import annotations

from contextlib import contextmanager
import threading
from typing import Iterator

from src.config.settings import settings


_LOCK = threading.Lock()
_GATES: dict[int, threading.BoundedSemaphore] = {}


def _gate(limit: int) -> threading.BoundedSemaphore:
    with _LOCK:
        return _GATES.setdefault(limit, threading.BoundedSemaphore(limit))


@contextmanager
def document_parser_request_slot() -> Iterator[None]:
    """限制跨任务同时占用 MinerU/Paddle 的请求数量。"""
    gate = _gate(settings.document_parser_max_concurrency)
    gate.acquire()
    try:
        yield
    finally:
        gate.release()
