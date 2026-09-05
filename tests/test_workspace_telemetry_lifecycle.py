# -*- coding: utf-8 -*-
"""遥测生命周期使用虚构资源验证，不启动产品服务。"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from src.observability import workspace_telemetry as telemetry


@pytest.mark.parametrize("enabled", [True, False])
def test_real_lifespan_yields_during_telemetry_shutdown(monkeypatch, enabled):
    from src.api import auth, main, capability_governance_runtime, semantic_workspace_runtime
    from src.config import runtime_config

    calls = []

    class Manager:
        def start(self):
            calls.append("start")

        async def stop(self):
            calls.append("stop")

    manager = Manager()
    monkeypatch.setattr(auth, "get_store", lambda: object())
    monkeypatch.setattr(runtime_config, "apply_global_overrides", lambda _: None)
    monkeypatch.setattr(main.settings, "workspace_telemetry_enabled", enabled)
    for name in ("start_scheduler", "start_cookie_health_scanner", "start_library_dedup_scanner"):
        monkeypatch.setattr(main, name, lambda: None)
    monkeypatch.setattr(semantic_workspace_runtime, "get_semantic_workspace_manager", lambda: manager)
    monkeypatch.setattr(capability_governance_runtime, "get_capability_validation_manager", lambda: manager)
    monkeypatch.setattr(capability_governance_runtime, "get_platform_validation_manager", lambda: manager)
    monkeypatch.setattr(telemetry, "configure_workspace_telemetry", lambda **_: calls.append("configure"))
    entered, heartbeat = threading.Event(), threading.Event()
    observed = []

    def shutdown(**_):
        entered.set()
        # 只有事件循环获得运行机会才能释放屏障；超时防止旧实现卡死测试。
        observed.append(heartbeat.wait(1))
        return True

    monkeypatch.setattr(telemetry, "shutdown_workspace_telemetry", shutdown)

    async def run():
        async def beat():
            while not entered.is_set():
                await asyncio.sleep(0)
            heartbeat.set()

        task = asyncio.create_task(beat())
        try:
            async with main.lifespan(main.app):
                pass
        finally:
            heartbeat.set()
            await asyncio.wait_for(task, 2)

    asyncio.run(run())
    assert calls.count("configure") == int(enabled)
    assert calls.count("start") == calls.count("stop") == 3
    assert observed == [True], "真实 lifespan 同步等待遥测，事件循环无法释放关闭屏障"


def test_blocked_exporter_does_not_hold_process_exit(tmp_path):
    source = '''
import threading
from src.observability import workspace_telemetry as telemetry

entered = threading.Event()
blocked = threading.Event()
class Exporter:
    def export(self, spans):
        entered.set()
        blocked.wait()
    def shutdown(self):
        blocked.wait()

telemetry.OTLPSpanExporter = lambda **kwargs: Exporter()
assert telemetry.configure_workspace_telemetry(endpoint="http://synthetic.invalid/traces")
def emit():
    with telemetry.workspace_stage_span("cancel"):
        pass
threading.Thread(target=emit, daemon=True).start()
assert entered.wait(3), "导出未进入屏障"
print("EXPORT_BLOCKED_MAIN_EXIT", flush=True)
'''
    script = tmp_path / "exit_probe.py"
    script.write_text(source, encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    process = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(script)], cwd=tmp_path, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
    )
    try:
        stdout, stderr = process.communicate(timeout=6)
    except subprocess.TimeoutExpired:
        # 只终止本测试创建并持有句柄的子进程。
        process.kill()
        stdout, stderr = process.communicate(timeout=3)
        pytest.fail(f"子进程退出被遥测拖住；屏障证据：{stdout!r}；stderr={stderr!r}")
    assert "EXPORT_BLOCKED_MAIN_EXIT" in stdout
    assert process.returncode == 0, stderr


def test_unreachable_http_export_keeps_explicit_timeout(monkeypatch):
    from requests import ConnectionError

    posts = []
    closed = threading.Event()

    class Session:
        headers = {}

        def post(self, **kwargs):
            posts.append(kwargs["timeout"])
            raise ConnectionError("synthetic unavailable")

        def close(self):
            closed.set()

    exporter_type = telemetry.OTLPSpanExporter
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", lambda **kwargs: exporter_type(session=Session(), **kwargs))
    try:
        assert telemetry.configure_workspace_telemetry(endpoint="http://synthetic.invalid/traces")
        with telemetry.workspace_stage_span("cancel"):
            pass
        telemetry.force_flush_workspace_telemetry(timeout_millis=1000)
    finally:
        telemetry.shutdown_workspace_telemetry()
    assert posts
    assert all(0 < timeout <= 5 for timeout in posts)
    assert closed.wait(1)


def test_shutdown_during_configuration_prevents_late_activation(monkeypatch):
    from opentelemetry.sdk.trace.export import SpanExportResult

    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    constructed = []
    configured = []

    class Exporter:
        def export(self, spans):
            return SpanExportResult.SUCCESS

        def shutdown(self):
            closed.set()

    def construct(**_):
        constructed.append(Exporter())
        entered.set()
        assert release.wait(3), "测试必须释放配置构造屏障"
        return constructed[-1]

    telemetry.shutdown_workspace_telemetry()
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", construct)
    worker = threading.Thread(target=lambda: configured.append(
        telemetry.configure_workspace_telemetry(endpoint="http://synthetic.invalid/traces")
    ), daemon=True)
    worker.start()
    try:
        assert entered.wait(2)
        assert not telemetry.shutdown_workspace_telemetry(timeout_millis=20)
        # 配置尚未退出时重复请求不能积累实例，也不能撤回关闭意图。
        assert not telemetry.configure_workspace_telemetry(endpoint="http://synthetic.invalid/traces")
        release.set()
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert len(constructed) == 1
        assert configured == [False], "关闭期间的旧配置不得在稍后宣布启用成功"
        assert not telemetry.workspace_telemetry_status()["active"]
        assert closed.wait(1)
    finally:
        release.set()
        worker.join(timeout=3)
        telemetry.shutdown_workspace_telemetry()


def test_real_lifespan_cancelled_configuration_cannot_activate_later(monkeypatch):
    from src.api import auth, main
    from src.config import runtime_config

    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    shutdown_budgets = []

    class Exporter:
        def export(self, spans):
            pytest.fail("已取消的配置不得接收业务 span")

        def shutdown(self):
            closed.set()

    def construct(**_):
        entered.set()
        assert release.wait(3), "测试必须释放配置构造屏障"
        return Exporter()

    def unexpected_start():
        pytest.fail("配置等待取消后不得启动产品后台服务")

    telemetry.shutdown_workspace_telemetry()
    original_shutdown = telemetry.shutdown_workspace_telemetry

    def shutdown(**kwargs):
        shutdown_budgets.append(kwargs.get("timeout_millis"))
        return original_shutdown(**kwargs)

    monkeypatch.setattr(auth, "get_store", lambda: object())
    monkeypatch.setattr(runtime_config, "apply_global_overrides", lambda _: None)
    monkeypatch.setattr(main.settings, "workspace_telemetry_enabled", True)
    monkeypatch.setattr(main.settings, "workspace_otlp_endpoint", "http://synthetic.invalid/traces")
    monkeypatch.setattr(main, "start_scheduler", unexpected_start)
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", construct)
    monkeypatch.setattr(telemetry, "shutdown_workspace_telemetry", shutdown)

    async def run():
        async def start():
            async with main.lifespan(main.app):
                pytest.fail("被取消的启动不得进入应用服务阶段")

        task = asyncio.create_task(start())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            assert shutdown_budgets == [0]
            assert not telemetry.workspace_telemetry_status()["shutdown_complete"]
            release.set()
            assert await asyncio.to_thread(closed.wait, 2)

            async def wait_closed():
                while not telemetry.workspace_telemetry_status()["shutdown_complete"]:
                    await asyncio.sleep(0)

            await asyncio.wait_for(wait_closed(), 1)
            assert not telemetry.workspace_telemetry_status()["active"]
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(run())
    finally:
        original_shutdown()
