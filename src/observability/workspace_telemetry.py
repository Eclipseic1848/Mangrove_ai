# -*- coding: utf-8 -*-
"""统一工作台的低敏 OpenTelemetry 轨迹。"""
from __future__ import annotations

from contextlib import nullcontext
import hashlib
import threading
from typing import Any, ContextManager, Sequence

from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor


_lock = threading.Lock()
_provider: TracerProvider | None = None
_tracer: Any = None


def _hash_identifier(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def configure_workspace_telemetry(
    *,
    endpoint: str,
    service_name: str = "mangrove-workspace",
) -> bool:
    """配置独立 OTLP provider；失败时保持业务可运行。"""
    global _provider, _tracer
    if not endpoint.strip():
        return False
    with _lock:
        if _provider is not None:
            _provider.shutdown()
        try:
            provider = TracerProvider(
                resource=Resource.create({
                    "service.name": service_name,
                    "service.namespace": "mangrove",
                })
            )
            provider.add_span_processor(
                SimpleSpanProcessor(
                    OTLPSpanExporter(
                        endpoint=endpoint.strip(),
                        timeout=5,
                    )
                )
            )
            _provider = provider
            _tracer = provider.get_tracer(
                "mangrove.semantic-workspace",
                "8a",
            )
        except Exception:  # noqa: BLE001
            _provider = None
            _tracer = None
            return False
    return True


def shutdown_workspace_telemetry() -> None:
    """释放 exporter；重复调用安全。"""
    global _provider, _tracer
    with _lock:
        provider = _provider
        _provider = None
        _tracer = None
    if provider is not None:
        provider.shutdown()


def force_flush_workspace_telemetry(
    *,
    timeout_millis: int = 5_000,
) -> bool:
    provider = _provider
    if provider is None:
        return True
    return bool(provider.force_flush(timeout_millis=timeout_millis))


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
    if _tracer is None:
        return nullcontext()
    return _tracer.start_as_current_span(
        "workspace.task",
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
    if _tracer is None:
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
    return _tracer.start_as_current_span(
        f"workspace.{stage}",
        attributes=attributes,
    )
