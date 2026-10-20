# -*- coding: utf-8 -*-
"""文档解析外部请求的进程内并发保护。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import threading
import time
from typing import Iterator

from src.config.settings import settings


_LOCK = threading.Lock()
_GATES: dict[int, threading.BoundedSemaphore] = {}
_DEADLINE: ContextVar[float | None] = ContextVar("document_parser_deadline", default=None)


@contextmanager
def document_parser_deadline(deadline: float) -> Iterator[None]:
    """图片任务的预算跟随线程；普通文档解析保留原超时配置。"""
    token = _DEADLINE.set(deadline)
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def document_parser_remaining_timeout(configured: float) -> float | None:
    deadline = _DEADLINE.get()
    if deadline is None:
        return None
    from src.api.execution import execution_checkpoint
    execution_checkpoint()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("图片解析预算已用尽")
    return min(configured, remaining)


def _gate(limit: int) -> threading.BoundedSemaphore:
    with _LOCK:
        return _GATES.setdefault(limit, threading.BoundedSemaphore(limit))


@contextmanager
def document_parser_request_slot() -> Iterator[None]:
    """限制跨任务同时占用 MinerU/Paddle 的请求数量。"""
    gate = _gate(settings.document_parser_max_concurrency)
    remaining = document_parser_remaining_timeout(float("inf"))
    acquired = gate.acquire() if remaining is None else gate.acquire(timeout=remaining)
    if not acquired:
        raise TimeoutError("图片解析排队预算已用尽")
    try:
        # 排队成功后仍须核对预算，不能在阶段结束后发出新的识别请求。
        document_parser_remaining_timeout(float("inf"))
        yield
    finally:
        gate.release()
