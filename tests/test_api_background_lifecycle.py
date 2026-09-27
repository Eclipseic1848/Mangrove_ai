"""验证真实 lifespan 的退出和部分启动失败收尾，不访问产品库或网络。"""
import asyncio

import pytest


@pytest.mark.parametrize("failure", [None, "start:workspace", "start:platform", "stop:account"])
def test_lifespan_closes_every_started_service(monkeypatch, failure):
    from src.api import (auth, main, services, account_execution_runtime,
                         cookie_health_scanner, library_dedup_scanner,
                         semantic_workspace_runtime, capability_governance_runtime)
    from src.config import runtime_config
    from src.observability import workspace_telemetry

    started, stopped = [], []

    class Worker:
        def __init__(self, name):
            self.name = name

        def start(self):
            started.append(self.name)
            if failure == "start:" + self.name:
                raise RuntimeError(failure)

        async def stop(self):
            stopped.append(self.name)
            if failure == "stop:" + self.name:
                raise RuntimeError(failure)

    workers = {name: Worker(name) for name in ("scheduler", "cookie", "library", "workspace", "capability", "platform", "account")}
    monkeypatch.setattr(auth, "get_store", lambda: object())
    monkeypatch.setattr(runtime_config, "apply_global_overrides", lambda _: None)
    monkeypatch.setattr(main.settings, "workspace_telemetry_enabled", False)
    monkeypatch.setattr(workspace_telemetry, "shutdown_workspace_telemetry", lambda: stopped.append("telemetry"))
    monkeypatch.setattr(services, "_service", workers["scheduler"])
    monkeypatch.setattr(cookie_health_scanner, "_scanner", workers["cookie"])
    monkeypatch.setattr(library_dedup_scanner, "_scanner", workers["library"])
    monkeypatch.setattr(main, "start_scheduler", workers["scheduler"].start)
    monkeypatch.setattr(main, "start_cookie_health_scanner", workers["cookie"].start)
    monkeypatch.setattr(main, "start_library_dedup_scanner", workers["library"].start)
    monkeypatch.setattr(semantic_workspace_runtime, "get_semantic_workspace_manager", lambda: workers["workspace"])
    monkeypatch.setattr(capability_governance_runtime, "get_capability_validation_manager", lambda: workers["capability"])
    monkeypatch.setattr(capability_governance_runtime, "get_platform_validation_manager", lambda: workers["platform"])
    monkeypatch.setattr(account_execution_runtime, "AccountExecutionManager", lambda *_: workers["account"])

    async def run():
        async with main.lifespan(main.app):
            assert started == list(workers)

    if failure:
        with pytest.raises(RuntimeError, match=failure):
            asyncio.run(run())
    else:
        asyncio.run(run())
    assert stopped == list(reversed(started)) + ["telemetry"]


@pytest.mark.parametrize("kind", ["cookie", "library"])
def test_scanner_singleton_can_restart_after_stop(monkeypatch, kind):
    from src.api import cookie_health_scanner, library_dedup_scanner
    module, start_name, setting = (
        (cookie_health_scanner, "start_cookie_health_scanner", "cookie_health_scan_enabled")
        if kind == "cookie" else
        (library_dedup_scanner, "start_library_dedup_scanner", "library_dedup_scan_enabled")
    )
    monkeypatch.setattr(module, "_scanner", None)
    monkeypatch.setattr(module.settings, setting, False)

    async def run():
        for _ in range(2):
            getattr(module, start_name)()
            worker = module._scanner
            try:
                await asyncio.sleep(0)
                assert worker._task is not None and not worker._task.done()
            finally:
                await worker.stop()

    asyncio.run(run())
