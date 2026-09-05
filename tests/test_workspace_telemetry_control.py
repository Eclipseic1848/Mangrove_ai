# -*- coding: utf-8 -*-
"""虚构 Exporter 的线程屏障回归，不访问遥测服务或业务库。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest
from opentelemetry.sdk.trace.export import SpanExportResult

from src.observability import workspace_telemetry as telemetry


class Exporter:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.closed = threading.Event()
        self.shutdown_started = threading.Event()
        self.shutdown_release = threading.Event()
        self.shutdown_release.set()
        self.batches = []
        self.threads = set()
        self.outcome = SpanExportResult.SUCCESS

    def export(self, spans):
        self.threads.add(threading.current_thread())
        self.batches.append(tuple(spans))
        self.entered.set()
        self.release.wait(15)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def shutdown(self):
        self.threads.add(threading.current_thread())
        self.shutdown_started.set()
        self.shutdown_release.wait(15)
        self.closed.set()


@pytest.mark.parametrize("enabled", [False, True])
def test_slow_export_keeps_event_loop_control_responsive(monkeypatch, enabled):
    exporter = Exporter()
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", lambda **_kwargs: exporter)
    monkeypatch.setattr(telemetry, "BATCH_SIZE", 1, raising=False)
    telemetry.shutdown_workspace_telemetry()
    if enabled:
        assert telemetry.configure_workspace_telemetry(endpoint="http://synthetic.invalid/v1/traces")
    # 即使旧同步实现卡住当前 loop，也由独立 daemon 释放；失败不能挂死 pytest。
    fallback = threading.Timer(0.8, exporter.release.set)
    fallback.daemon = True
    fallback.start()

    async def scenario():
        facts = {"status": "running"}
        cancelled = asyncio.Event()

        async def task():
            try:
                await asyncio.Event().wait()
            finally:
                facts["status"] = "cancelled"
                cancelled.set()

        work = asyncio.create_task(task())
        await asyncio.sleep(0)
        started = time.monotonic()

        async def control():
            if enabled:
                assert await asyncio.to_thread(exporter.entered.wait, 1)
            await asyncio.sleep(0.02)
            heartbeat = time.monotonic() - started
            before_cancel = facts["status"]
            work.cancel()
            await cancelled.wait()
            await asyncio.gather(work, return_exceptions=True)
            return heartbeat, before_cancel, facts["status"]

        controls = asyncio.create_task(control())
        with telemetry.workspace_stage_span("control-probe"):
            await asyncio.sleep(0)
        return await controls

    try:
        latency, before, after = asyncio.run(scenario())
        assert (before, after) == ("running", "cancelled")
        assert latency < 0.3, f"遥测阻塞事件循环控制 {latency:.3f}s"
    finally:
        exporter.release.set()
        fallback.cancel()
        telemetry.shutdown_workspace_telemetry()


def wait_until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "屏障未在预算内到达"
        threading.Event().wait(0.005)


def span(name="fixture"):
    with telemetry.workspace_stage_span(name):
        pass


@pytest.fixture
def configured(monkeypatch):
    exporter = Exporter()
    created = []

    def factory(**_kwargs):
        created.append(exporter)
        return exporter

    telemetry.shutdown_workspace_telemetry()
    monkeypatch.setattr(telemetry, "QUEUE_CAPACITY", 3)
    monkeypatch.setattr(telemetry, "BATCH_SIZE", 1)
    monkeypatch.setattr(telemetry, "SCHEDULE_DELAY_SECONDS", 0.01)
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", factory)
    assert telemetry.configure_workspace_telemetry(endpoint="http://synthetic.invalid/v1/traces")
    try:
        yield exporter, created
    finally:
        exporter.release.set()
        exporter.shutdown_release.set()
        assert telemetry.shutdown_workspace_telemetry(timeout_millis=2000)


def test_capacity_includes_inflight_and_shutdown_counts_only_unstarted(configured):
    exporter, created = configured
    span("inflight")
    assert exporter.entered.wait(2)
    for index in range(5):
        span(f"queued-{index}")
    status = telemetry.workspace_telemetry_status()
    assert status["in_flight"] == 1
    assert status["queued"] == 2
    assert status["dropped_queue_full"] == 3
    assert status["dropped_shutdown"] == 0
    assert all(len(batch) <= 1 for batch in exporter.batches)

    # 已创建但尚未结束的 span 在关闭后不得进入队列。
    late = telemetry.workspace_stage_span("late")
    late.__enter__()
    try:
        started = time.monotonic()
        assert not telemetry.shutdown_workspace_telemetry(timeout_millis=20)
        assert time.monotonic() - started < 0.3
    finally:
        late.__exit__(None, None, None)
    status = telemetry.workspace_telemetry_status()
    assert status["queued"] == 0
    assert status["in_flight"] == 1
    assert status["dropped_shutdown"] == 2
    assert status["dropped_queue_full"] == 3
    assert status["rejected_closed"] == 1
    assert status["shutdown_complete"] is False
    assert status["active"] is False
    before = {thread.ident for thread in threading.enumerate()}
    for _ in range(5):
        assert not telemetry.shutdown_workspace_telemetry(timeout_millis=5)
        assert not telemetry.configure_workspace_telemetry(endpoint="http://other-synthetic.invalid/v1/traces")
    assert len(created) == 1
    assert {thread.ident for thread in threading.enumerate()} <= before
    assert len(exporter.threads) == 1
    assert all(thread.daemon for thread in exporter.threads)

    exporter.release.set()
    assert telemetry.shutdown_workspace_telemetry(timeout_millis=2000)
    status = telemetry.workspace_telemetry_status()
    assert status["shutdown_complete"] is True
    assert status["in_flight"] == 0
    assert status["dropped_shutdown"] == 2
    assert len(exporter.batches) == 1


@pytest.mark.parametrize("outcome", [SpanExportResult.FAILURE, RuntimeError("synthetic-sensitive-response")])
def test_export_failure_is_observable_but_does_not_change_task_facts(configured, outcome, caplog):
    exporter, _ = configured
    exporter.outcome = outcome
    facts = {}
    with telemetry.workspace_stage_span("failed-telemetry", status="succeeded"):
        facts["status"] = "succeeded"
    assert exporter.entered.wait(2)
    exporter.release.set()
    assert not telemetry.force_flush_workspace_telemetry(timeout_millis=1000)
    status = telemetry.workspace_telemetry_status()
    assert facts == {"status": "succeeded"}
    assert status["export_failures"] == 1
    assert status["in_flight"] == status["queued"] == 0
    assert all(type(value) in (int, bool) for value in status.values())
    assert "synthetic-sensitive-response" not in json.dumps(status) + caplog.text
    assert "synthetic.invalid" not in json.dumps(status) + caplog.text


def test_short_flush_budget_does_not_cancel_or_duplicate_inflight(configured):
    exporter, _ = configured
    span()
    assert exporter.entered.wait(2)
    started = time.monotonic()
    for _ in range(5):
        assert not telemetry.force_flush_workspace_telemetry(timeout_millis=10)
    assert time.monotonic() - started < 0.4
    assert len(exporter.batches) == 1
    assert telemetry.workspace_telemetry_status()["in_flight"] == 1
    exporter.release.set()
    assert telemetry.force_flush_workspace_telemetry(timeout_millis=1000)
    assert len(exporter.batches) == 1


def test_exporter_shutdown_uses_same_worker_and_remains_unconfirmed_until_released(configured):
    exporter, created = configured
    exporter.release.set()
    span()
    assert telemetry.force_flush_workspace_telemetry(timeout_millis=1000)
    exporter.shutdown_release.clear()
    try:
        started = time.monotonic()
        assert not telemetry.shutdown_workspace_telemetry(timeout_millis=20)
        assert time.monotonic() - started < 0.3
        assert exporter.shutdown_started.wait(1)
        assert telemetry.workspace_telemetry_status()["shutdown_complete"] is False
        before = {thread.ident for thread in threading.enumerate()}
        for _ in range(4):
            assert not telemetry.shutdown_workspace_telemetry(timeout_millis=5)
            assert not telemetry.configure_workspace_telemetry(endpoint="http://synthetic.invalid/reconfigure")
        assert len(created) == 1
        assert len(exporter.threads) == 1
        assert {thread.ident for thread in threading.enumerate()} <= before
    finally:
        exporter.shutdown_release.set()
    assert telemetry.shutdown_workspace_telemetry(timeout_millis=1000)
    assert telemetry.workspace_telemetry_status()["shutdown_complete"] is True


def test_flush_waits_for_entry_watermark_not_later_spans(monkeypatch):
    first_entered, second_entered = threading.Event(), threading.Event()
    first_release, second_release = threading.Event(), threading.Event()
    exported = []

    class SequencedExporter:
        def export(self, spans):
            exported.extend(item.name for item in spans)
            entered, release = (first_entered, first_release) if len(exported) == 1 else (second_entered, second_release)
            entered.set()
            release.wait(5)
            return SpanExportResult.SUCCESS

        def shutdown(self):
            pass

    telemetry.shutdown_workspace_telemetry()
    monkeypatch.setattr(telemetry, "QUEUE_CAPACITY", 64)
    monkeypatch.setattr(telemetry, "BATCH_SIZE", 1)
    monkeypatch.setattr(telemetry, "SCHEDULE_DELAY_SECONDS", 0.01)
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", lambda **_kwargs: SequencedExporter())
    assert telemetry.configure_workspace_telemetry(endpoint="http://synthetic.invalid/watermark")
    flush_started, flush_done = threading.Event(), threading.Event()
    result = []

    def flush():
        flush_started.set()
        result.append(telemetry.force_flush_workspace_telemetry(timeout_millis=1000))
        flush_done.set()

    thread = threading.Thread(target=flush, daemon=True)
    try:
        span("first")
        assert first_entered.wait(1)
        thread.start()
        assert flush_started.wait(1)
        # 等待 flush 进入 Condition 等待；栈仅验证公开函数已调用，不读取产品私有状态。
        def flush_is_waiting():
            frame = sys._current_frames().get(thread.ident)
            names = []
            while frame:
                names.append(frame.f_code.co_name)
                frame = frame.f_back
            return "force_flush_workspace_telemetry" in names and "wait" in names

        wait_until(flush_is_waiting)
        for index in range(10):
            span(f"later-{index}")
        first_release.set()
        assert second_entered.wait(1)
        assert flush_done.wait(0.4), "flush 不应等待调用后进入的 span"
        assert result == [True]
        assert telemetry.workspace_telemetry_status()["in_flight"] == 1
    finally:
        first_release.set()
        second_release.set()
        thread.join(timeout=2)
        assert telemetry.shutdown_workspace_telemetry(timeout_millis=2000)


@pytest.mark.parametrize("shutdown_fails", [False, True])
def test_unconfirmed_shutdown_does_not_block_process_exit_or_allow_reconfigure(tmp_path, shutdown_fails):
    # 单独进程验证永久未确认实例；不借私有变量重置模块来掩盖资源残留。
    code = '''
import json, threading
from src.observability import workspace_telemetry as t
from opentelemetry.sdk.trace.export import SpanExportResult
started = threading.Event()
class Exporter:
    def export(self, spans):
        started.set()
        if not FAIL: threading.Event().wait(30)
        return SpanExportResult.SUCCESS
    def shutdown(self):
        if FAIL: raise RuntimeError("synthetic-private-shutdown")
t.OTLPSpanExporter = lambda **kwargs: Exporter()
t.SCHEDULE_DELAY_SECONDS = 0.01
assert t.configure_workspace_telemetry(endpoint="http://synthetic.invalid/exit")
with t.workspace_stage_span("exit"): pass
assert started.wait(2)
if FAIL: assert t.force_flush_workspace_telemetry(timeout_millis=1000)
assert not t.shutdown_workspace_telemetry(timeout_millis=20)
for _ in range(3):
    assert not t.configure_workspace_telemetry(endpoint="http://synthetic.invalid/rejected")
assert not t.workspace_telemetry_status()["shutdown_complete"]
print(json.dumps(t.workspace_telemetry_status()))
'''
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]), "PYTHONUTF8": "1"}
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", f"FAIL = {shutdown_fails!r}\n" + code],
                            cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=6)
    assert result.returncode == 0, result.stderr
    status = json.loads(result.stdout.strip())
    assert status["shutdown_complete"] is False
    assert "synthetic-private-shutdown" not in result.stdout + result.stderr


def test_official_http_exporter_hides_response_body_only_on_its_worker(monkeypatch, caplog):
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    canary = "synthetic-sensitive-otlp-response-123"
    calls = []

    class Response:
        ok = False
        status_code = 400
        reason = canary
        text = canary

    class Session:
        headers = {}

        def post(self, **kwargs):
            calls.append(kwargs)
            entered.set()
            release.wait(5)
            return Response()

        def close(self):
            closed.set()

    telemetry.shutdown_workspace_telemetry()
    session = Session()
    monkeypatch.setattr(telemetry, "BATCH_SIZE", 1)
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", lambda **kwargs: OTLPSpanExporter(session=session, **kwargs))
    logger = logging.getLogger(OTLPSpanExporter.__module__)
    caplog.set_level(logging.WARNING, logger=logger.name)
    assert telemetry.configure_workspace_telemetry(endpoint="http://synthetic.invalid/no-network")
    try:
        span("official-exporter")
        assert entered.wait(2)
        # 与遥测 worker 同用官方 logger 的其他线程不应被全局静音。
        thread = threading.Thread(target=lambda: logger.warning("ordinary-thread-log-123"), daemon=True)
        thread.start()
        thread.join(timeout=1)
        assert not thread.is_alive()
        assert "ordinary-thread-log-123" in caplog.text
        release.set()
        assert not telemetry.force_flush_workspace_telemetry(timeout_millis=1000)
        assert telemetry.workspace_telemetry_status()["export_failures"] == 1
        assert canary not in caplog.text
        assert len(calls) == 1
        assert isinstance(calls[0]["data"], bytes) and calls[0]["data"]
        assert 0 < calls[0]["timeout"] <= 5
    finally:
        release.set()
        assert telemetry.shutdown_workspace_telemetry(timeout_millis=2000)
    assert closed.is_set()
    logger.warning("ordinary-after-close-123")
    assert "ordinary-after-close-123" in caplog.text
    assert canary not in caplog.text


@pytest.mark.parametrize("kind", ["root", "stage"])
def test_business_exception_propagates_without_exporting_body_or_stack(configured, kind):
    exporter, _ = configured
    exporter.release.set()
    canary = "synthetic-business-body-123"
    error = RuntimeError(canary)
    code = "WORKSPACE_SYNTHETIC_FAILED"
    context = telemetry.workspace_task_span(
        task_id="synthetic-private-task-id", revision=1, source_types=["upload"],
        source_count=1, output_formats=["txt"], provider="local", model="synthetic-model",
    ) if kind == "root" else telemetry.workspace_stage_span("compile", status="failed", error_code=code)
    with pytest.raises(RuntimeError) as observed:
        with context:
            if kind == "root":
                with telemetry.workspace_stage_span("compile", status="failed", error_code=code):
                    pass
            raise error
    assert observed.value is error, "遥测不能吞掉或替换业务原异常"
    assert telemetry.force_flush_workspace_telemetry(timeout_millis=1000)
    spans = [item for batch in exporter.batches for item in batch]
    assert any(item.attributes.get("workspace.error_code") == code for item in spans)
    exported = [{
        "attributes": dict(item.attributes),
        "events": [{"name": event.name, "attributes": dict(event.attributes)} for event in item.events],
        "description": item.status.description,
    } for item in spans]
    payload = json.dumps(exported, ensure_ascii=False)
    assert canary not in payload
    assert "synthetic-private-task-id" not in payload
    assert "exception.stacktrace" not in payload


def test_lifespan_initialization_failure_closes_real_telemetry(monkeypatch):
    from src.api import auth, main, capability_governance_runtime, semantic_workspace_runtime
    from src.config import runtime_config

    exporter = Exporter()
    exporter.release.set()
    failure = RuntimeError("synthetic-manager-start-failure")

    class Manager:
        def start(self):
            raise failure

        async def stop(self):
            pass

    manager = Manager()
    telemetry.shutdown_workspace_telemetry()
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", lambda **_kwargs: exporter)
    monkeypatch.setattr(main.settings, "workspace_telemetry_enabled", True)
    monkeypatch.setattr(main.settings, "workspace_otlp_endpoint", "http://synthetic.invalid/startup")
    monkeypatch.setattr(auth, "get_store", lambda: object())
    monkeypatch.setattr(runtime_config, "apply_global_overrides", lambda _: None)
    for name in ("start_scheduler", "start_cookie_health_scanner", "start_library_dedup_scanner"):
        monkeypatch.setattr(main, name, lambda: None)
    monkeypatch.setattr(semantic_workspace_runtime, "get_semantic_workspace_manager", lambda: manager)
    monkeypatch.setattr(capability_governance_runtime, "get_capability_validation_manager", lambda: manager)
    monkeypatch.setattr(capability_governance_runtime, "get_platform_validation_manager", lambda: manager)

    async def startup():
        async with main.lifespan(main.app):
            pytest.fail("初始化失败不能进入产品请求阶段")

    try:
        with pytest.raises(RuntimeError) as observed:
            asyncio.run(startup())
        assert observed.value is failure
        assert exporter.closed.wait(0.5), "初始化异常绕过遥测关闭，Exporter 仍然存活"
        status = telemetry.workspace_telemetry_status()
        assert status["active"] is False
        assert status["shutdown_complete"] is True
    finally:
        assert telemetry.shutdown_workspace_telemetry(timeout_millis=1000)
