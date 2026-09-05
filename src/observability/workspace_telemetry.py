# -*- coding: utf-8 -*-
"""统一工作台的低敏 OpenTelemetry 轨迹。"""
from __future__ import annotations

from collections import deque
from contextlib import nullcontext
import hashlib
import logging
import threading
import time
from typing import Any, ContextManager, Sequence

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.utils import suppress_instrumentation
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SpanExportResult


QUEUE_CAPACITY = 256
BATCH_SIZE = 32
SCHEDULE_DELAY_SECONDS = 0.5
_lock = threading.Lock()
_provider: TracerProvider | None = None
_tracer: Any = None
_processor: _WorkspaceSpanProcessor | None = None
_configuring = False
_close_requested = False
_export_context = threading.local()
_exporter_logger = logging.getLogger(OTLPSpanExporter.__module__)


class _ExporterLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # 官方 HTTP exporter 会打印响应正文；仅屏蔽本模块导出线程的原始日志。
        return not getattr(_export_context, "active", False)


class _WorkspaceSpanProcessor(SpanProcessor):
    """单线程批处理；等待有界不代表在途 HTTP 已终止。"""

    def __init__(self, exporter: Any):
        self._exporter = exporter
        self._condition = threading.Condition()
        self._queue: deque[tuple[int, Any]] = deque()
        self._accepted = self._completed = self._flush_target = 0
        self._first_failed = 0
        self._in_flight = 0
        self._closing = False
        self._close_ok = False
        self._dropped_full = self._dropped_shutdown = self._rejected = 0
        self._failures = 0
        self._worker = threading.Thread(
            target=self._run, name="workspace-telemetry", daemon=True,
        )
        self._worker.start()

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        pass

    def on_end(self, span: Any) -> None:
        if not (span.context and span.context.trace_flags.sampled):
            return
        with self._condition:
            if self._closing:
                self._rejected += 1
            elif len(self._queue) + self._in_flight >= QUEUE_CAPACITY:
                self._dropped_full += 1
            else:
                self._accepted += 1
                self._queue.append((self._accepted, span))
                self._condition.notify_all()

    def _run(self) -> None:
        _export_context.active = True
        log_filter = _ExporterLogFilter()
        _exporter_logger.addFilter(log_filter)
        try:
            while True:
                with self._condition:
                    deadline = time.monotonic() + SCHEDULE_DELAY_SECONDS
                    while not self._closing and (
                        not self._queue or (
                            len(self._queue) < BATCH_SIZE
                            and self._flush_target <= self._completed
                            and time.monotonic() < deadline
                        )
                    ):
                        self._condition.wait(max(0, deadline - time.monotonic()) if self._queue else None)
                    if self._closing and not self._queue:
                        break
                    batch = [self._queue.popleft() for _ in range(min(BATCH_SIZE, len(self._queue)))]
                    self._in_flight = len(batch)
                try:
                    with suppress_instrumentation():
                        ok = self._exporter.export([span for _, span in batch]) == SpanExportResult.SUCCESS
                except Exception:  # noqa: BLE001
                    ok = False
                with self._condition:
                    if not ok:
                        self._failures += 1
                        self._first_failed = min(self._first_failed or batch[0][0], batch[0][0])
                    self._completed = batch[-1][0]
                    self._in_flight = 0
                    self._condition.notify_all()
            try:
                with suppress_instrumentation():
                    self._exporter.shutdown()
                self._close_ok = True
            except Exception:  # noqa: BLE001
                # 保留关闭失败实例；不能换一个新 exporter 掩盖未确认的资源。
                pass
        finally:
            _exporter_logger.removeFilter(log_filter)
            _export_context.active = False
            with self._condition:
                self._condition.notify_all()

    def force_flush(self, timeout_millis: int = 5000) -> bool:
        deadline = time.monotonic() + max(0, timeout_millis) / 1000
        with self._condition:
            target = self._accepted
            self._flush_target = max(self._flush_target, target)
            self._condition.notify_all()
            while self._completed < target:
                if self._first_failed and self._first_failed <= target:
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return not self._first_failed or self._first_failed > target

    def shutdown(self, timeout_millis: int = 1000) -> bool:
        deadline = time.monotonic() + max(0, timeout_millis) / 1000
        with self._condition:
            self._closing = True
            self._condition.notify_all()
        self._worker.join(max(0, deadline - time.monotonic()))
        with self._condition:
            if self._queue:
                if not self._first_failed:
                    self._first_failed = self._queue[0][0]
                self._dropped_shutdown += len(self._queue)
                self._queue.clear()
                self._condition.notify_all()
        return self._close_ok and not self._worker.is_alive()

    def status(self) -> dict[str, bool | int]:
        with self._condition:
            return {
                "active": not self._closing,
                "queued": len(self._queue),
                "in_flight": self._in_flight,
                "dropped_queue_full": self._dropped_full,
                "dropped_shutdown": self._dropped_shutdown,
                "rejected_closed": self._rejected,
                "export_failures": self._failures,
                "shutdown_complete": self._close_ok and not self._worker.is_alive(),
            }


def _hash_identifier(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def configure_workspace_telemetry(
    *, endpoint: str, service_name: str = "mangrove-workspace",
) -> bool:
    """旧实例确认关闭后才配置；遥测失败不改变任务事实。"""
    global _provider, _tracer, _processor, _configuring, _close_requested
    if not endpoint.strip():
        return False
    with _lock:
        if _configuring:
            return False
        _configuring = True
        _close_requested = False
        old = _processor
        _provider = _tracer = None
    processor = None
    ready = False
    try:
        if old is not None:
            state = old.status()
            if not state["active"]:
                if not state["shutdown_complete"]:
                    return False
            elif not old.shutdown():
                return False
        provider = TracerProvider(
            resource=Resource.create({
                "service.name": service_name, "service.namespace": "mangrove",
            }),
            shutdown_on_exit=False,
        )
        tracer = provider.get_tracer("mangrove.semantic-workspace", "8a")
        processor = _WorkspaceSpanProcessor(OTLPSpanExporter(endpoint=endpoint.strip(), timeout=5))
        with _lock:
            _processor = processor
        provider.add_span_processor(processor)
        ready = True
    except Exception:  # noqa: BLE001
        return False
    finally:
        with _lock:
            # to_thread 的等待被取消也不会终止构造；发布必须服从期间锁存的关闭意图。
            ready = ready and not _close_requested
            if ready:
                _provider, _tracer = provider, tracer
            elif processor is not None:
                # 零预算只封住接收和唤醒原 worker，不在发布锁内等待网络收尾。
                processor.shutdown(timeout_millis=0)
            _configuring = False
    return ready


def shutdown_workspace_telemetry(*, timeout_millis: int = 1000) -> bool:
    """等待有上限；未关闭实例保留身份，重复调用不增加线程。"""
    global _provider, _tracer, _close_requested
    deadline = time.monotonic() + max(0, timeout_millis) / 1000
    with _lock:
        _close_requested = True
        if _configuring:
            return False
        processor = _processor
        _provider = _tracer = None
    if processor is None:
        return True
    return processor.shutdown(timeout_millis=max(0, (deadline - time.monotonic()) * 1000))


def force_flush_workspace_telemetry(*, timeout_millis: int = 5000) -> bool:
    processor = _processor
    return True if processor is None else processor.force_flush(timeout_millis)


def workspace_telemetry_status() -> dict[str, bool | int]:
    with _lock:
        processor = _processor
        configuring = _configuring
    if processor is not None:
        status = processor.status()
        if configuring:
            status["active"] = status["shutdown_complete"] = False
        return status
    return {
        "active": False, "queued": 0, "in_flight": 0,
        "dropped_queue_full": 0, "dropped_shutdown": 0, "rejected_closed": 0,
        "export_failures": 0, "shutdown_complete": not configuring,
    }


def workspace_task_span(
    *,
    task_id: str,
    revision: int,
    source_types: Sequence[str],
    source_count: int,
    output_formats: Sequence[str],
    provider: str,
    model: str | None,
) -> ContextManager[Any]:
    """创建任务根 span，只记录哈希标识和低敏枚举。"""
    tracer = _tracer
    if tracer is None:
        return nullcontext()
    return tracer.start_as_current_span(
        "workspace.task",
        # 业务异常仍向外传播，但正文和堆栈不进入低敏轨迹。
        record_exception=False,
        set_status_on_exception=False,
        attributes={
            "workspace.task_id_hash": _hash_identifier(task_id),
            "workspace.revision": revision,
            "workspace.source_types": tuple(source_types),
            "workspace.source_count": source_count,
            "workspace.output_formats": tuple(output_formats),
            "workspace.provider": provider,
            "workspace.model": model or "",
        },
    )


def workspace_stage_span(
    stage: str,
    *,
    status: str | None = None,
    error_code: str | None = None,
    retry_count: int | None = None,
) -> ContextManager[Any]:
    """创建业务阶段 span；不接收 Prompt、正文、路径或文件名。"""
    tracer = _tracer
    if tracer is None:
        return nullcontext()
    attributes: dict[str, str | int] = {
        "workspace.stage": stage,
    }
    if status is not None:
        attributes["workspace.status"] = status
    if error_code is not None:
        attributes["workspace.error_code"] = error_code
    if retry_count is not None:
        attributes["workspace.retry_count"] = retry_count
    return tracer.start_as_current_span(
        f"workspace.{stage}",
        record_exception=False,
        set_status_on_exception=False,
        attributes=attributes,
    )
