"""停止必须覆盖来源刷新从注册到修订提交的完整异步窗口。"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import httpx
import pytest

from src.api.auth import get_store
from src.api import semantic_workspace_runtime as runtime_mod
from src.api.routes import semantic_workspace as routes
from src.config.settings import settings
from src.connectors.http_security import HttpSecurityGuard
from src.source_acquisition import AnonymousWebFetcher, SourceAcquisitionRepository, SourceAcquisitionService
from tests.test_web_source_delivery_api import CoverageAwareWebPiRuntime, _client, _seed_snapshot, _wait_for_delivery


@pytest.mark.parametrize("pause_at", ["before_claim", "stream", "binding"])
def test_task_stop_prevents_late_source_refresh_and_allows_explicit_new_revision(tmp_path, monkeypatch, pause_at):
    runtime = CoverageAwareWebPiRuntime()
    client = _client(tmp_path, monkeypatch, role="admin", pi_runtime=runtime)
    snapshot_id, _ = _seed_snapshot(Path(settings.webui_db_path))
    repository = SourceAcquisitionRepository(settings.webui_db_path)
    reached = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    visited = []

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            reached.set()
            await asyncio.Event().wait()
            yield b"unreachable"

        async def aclose(self):
            closed.set()

    def respond(request):
        visited.append(str(request.url))
        if pause_at == "stream":
            return httpx.Response(200, headers={"content-type": "text/html"}, stream=SlowStream())
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>new facts</html>")

    class Service(SourceAcquisitionService):
        async def acquire(self, **kwargs):
            if pause_at == "before_claim":
                reached.set()
                assert await asyncio.to_thread(release.wait, 10)
            return await super().acquire(**kwargs)

    service = Service(repository, AnonymousWebFetcher(
        security_guard=HttpSecurityGuard(resolver=lambda _host: ["93.184.216.34"]),
        transport=httpx.MockTransport(respond),
    ))
    monkeypatch.setattr(routes, "_source_acquisition_service", lambda: service)
    with client:
        created = client.post("/api/semantic-workspace/tasks", json={
            "objective_text": "生成隔离网页摘要", "upload_ids": [], "source_snapshot_id": snapshot_id,
            "must_include": [], "explicit_exclusions": [], "quantity_requirement": "页面有证据的内容",
            "completeness_requirement": "仅对精确页面负责", "output_formats": ["json"],
            "runtime_version": "pi", "provider": "local",
        })
        assert created.status_code == 202, created.text
        task_id = created.json()["task_id"]
        _wait_for_delivery(client, task_id)
        manager = runtime_mod.get_semantic_workspace_manager()
        prepare = manager.prepare_runtime_binding

        async def prepare_after_barrier(**kwargs):
            reached.set()
            assert await asyncio.to_thread(release.wait, 10)
            return await prepare(**kwargs)

        if pause_at == "binding":
            monkeypatch.setattr(manager, "prepare_runtime_binding", prepare_after_barrier)
        with ThreadPoolExecutor(max_workers=1) as pool:
            refresh = pool.submit(client.post, f"/api/semantic-workspace/tasks/{task_id}/source-refresh",
                                  headers={"Idempotency-Key": "stop-refresh"}, json={"expected_active_revision": 1})
            try:
                assert reached.wait(5)
                stopped = client.post(f"/api/semantic-workspace/tasks/{task_id}/cancel")
                assert stopped.status_code == 200, stopped.text
                count_at_stop = repository.count_snapshots("user-a")
            finally:
                release.set()
            result = refresh.result(timeout=10)
        assert result.status_code == 409, result.text
        # 再次停止同时验证幂等重试能完成来源静默确认。
        client.post(f"/api/semantic-workspace/tasks/{task_id}/cancel")
        task = get_store().get_semantic_workspace_task("user-a", task_id)
        assert task["active_revision"] == 1
        assert task["cancel_generation"] >= 1
        assert runtime.start_calls == 1
        assert repository.count_snapshots("user-a") == count_at_stop
        if pause_at == "stream":
            assert closed.is_set()
            assert task["status"] == "cancelled"
        if pause_at == "before_claim":
            assert visited == []
        replay = client.post(f"/api/semantic-workspace/tasks/{task_id}/source-refresh",
                             headers={"Idempotency-Key": "stop-refresh"}, json={"expected_active_revision": 1})
        assert replay.status_code == 409
        assert runtime.start_calls == 1
        # 停止只否决旧操作；用户后来明确提交的新修订仍然有效。
        monkeypatch.setattr(manager, "prepare_runtime_binding", prepare)
        revision = client.post(f"/api/semantic-workspace/tasks/{task_id}/revisions", json={
            "instruction": "确认重新处理原冻结来源", "expected_active_revision": 1,
        })
        assert revision.status_code == 202, revision.text
        assert revision.json()["revision"] == 2
        _wait_for_delivery(client, task_id)
        assert runtime.start_calls == 2


def test_task_stop_closes_source_refresh_from_previous_revision(tmp_path, monkeypatch):
    runtime = CoverageAwareWebPiRuntime()
    client = _client(tmp_path, monkeypatch, role="admin", pi_runtime=runtime)
    snapshot_id, _ = _seed_snapshot(Path(settings.webui_db_path))
    repository = SourceAcquisitionRepository(settings.webui_db_path)
    started = threading.Event()
    closed = threading.Event()
    visited = []

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            await asyncio.Event().wait()
            yield b"unreachable"

        async def aclose(self):
            closed.set()

    def respond(request):
        visited.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "text/html"}, stream=SlowStream())

    service = SourceAcquisitionService(repository, AnonymousWebFetcher(
        security_guard=HttpSecurityGuard(resolver=lambda _host: ["93.184.216.34"]),
        transport=httpx.MockTransport(respond), timeout_seconds=5,
    ))
    monkeypatch.setattr(routes, "_source_acquisition_service", lambda: service)
    with client:
        created = client.post("/api/semantic-workspace/tasks", json={
            "objective_text": "验证跨版本来源停止", "upload_ids": [], "source_snapshot_id": snapshot_id,
            "must_include": [], "explicit_exclusions": [], "quantity_requirement": "页面有证据的内容",
            "completeness_requirement": "仅对精确页面负责", "output_formats": ["json"],
            "runtime_version": "pi", "provider": "local",
        })
        assert created.status_code == 202, created.text
        task_id = created.json()["task_id"]
        _wait_for_delivery(client, task_id)
        with ThreadPoolExecutor(max_workers=1) as pool:
            refresh = pool.submit(client.post, f"/api/semantic-workspace/tasks/{task_id}/source-refresh",
                                  headers={"Idempotency-Key": "previous-revision-refresh"},
                                  json={"expected_active_revision": 1})
            assert started.wait(5)
            # 用户显式建立 V2 不等于旧 V1 的读取已经退出。
            revision = client.post(f"/api/semantic-workspace/tasks/{task_id}/revisions", json={
                "instruction": "明确使用原冻结来源建立 V2", "expected_active_revision": 1,
            })
            assert revision.status_code == 202, revision.text
            assert revision.json()["revision"] == 2
            _wait_for_delivery(client, task_id)
            assert not closed.is_set()
            assert runtime.start_calls == 2
            snapshots_before_stop = repository.count_snapshots("user-a")
            stopped = client.post(f"/api/semantic-workspace/tasks/{task_id}/cancel")
            assert stopped.status_code == 200, stopped.text
            assert stopped.json()["status"] in {"cancelling", "cancelled"}
            if stopped.json()["status"] == "cancelled":
                assert closed.is_set()
            assert closed.wait(2), "停止 V2 也必须关闭该任务仍在读取的 V1 来源"
            result = refresh.result(timeout=5)
            assert result.status_code == 409, result.text
        confirmed = client.post(f"/api/semantic-workspace/tasks/{task_id}/cancel")
        assert confirmed.json()["status"] == "cancelled"
        task = get_store().get_semantic_workspace_task("user-a", task_id)
        assert task["active_revision"] == 2
        assert repository.count_snapshots("user-a") == snapshots_before_stop
        assert runtime.start_calls == 2
        assert len(visited) == 1
