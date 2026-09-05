"""来源取消须等待真实读取静默，且不能产生迟到快照。"""
from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import sys
from contextlib import suppress

from fastapi import FastAPI, Request
import httpx
import pytest

from src.api.auth import get_current_user
from src.api.routes import source_acquisition as source_routes
from src.connectors.http_security import HttpSecurityGuard
from src.source_acquisition import AnonymousWebFetcher, SourceAcquisitionRepository, SourceAcquisitionRequest, SourceAcquisitionService
from tests.database_migration_helpers import migrated_webui_database


@pytest.mark.asyncio
@pytest.mark.parametrize("resume_unknown", [False, True])
async def test_stale_replay_cannot_take_over_live_reader_or_cleanup(tmp_path, resume_unknown):
    database = migrated_webui_database(tmp_path / "live.db")
    repository = SourceAcquisitionRepository(database)
    request = SourceAcquisitionRequest(url="https://example.com/page", purpose="隔离存活读取验证")
    attempt, _ = repository.claim_attempt(owner_id="owner-a", idempotency_key="live", request=request)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE source_acquisition_attempts SET started_at='2000-01-01T00:00:00+00:00'")

    class NeverRead:
        async def fetch(self, url):
            pytest.fail("同键重放不能抢走活执行者")

    with repository.execution_lock("owner-a", attempt["attempt_id"]):
        result = await SourceAcquisitionService(repository, NeverRead()).acquire(
            owner_id="owner-a", idempotency_key="live", request=request, resume_unknown=resume_unknown,
        )
        assert result["status"] == "acquiring"
        assert result["finished_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["current_page", "same_site", "redirect"])
async def test_cancel_api_stops_open_stream_before_confirming(tmp_path, monkeypatch, scope):
    database = migrated_webui_database(tmp_path / "cancel.db")
    repository = SourceAcquisitionRepository(database)
    started = asyncio.Event()
    closed = asyncio.Event()
    visited = []

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"<html><body>example<a href='/next'>next</a>"
            started.set()
            await asyncio.Event().wait()

        async def aclose(self):
            closed.set()

    def handler(request):
        visited.append(request.url.path)
        if scope == "redirect" and request.url.path == "/page":
            return httpx.Response(302, headers={"location": "/article"})
        return httpx.Response(200, headers={"content-type": "text/html"}, stream=SlowStream())

    def service():
        # 两个请求获得不同 Service，取消必须跨实例生效。
        return SourceAcquisitionService(SourceAcquisitionRepository(database), AnonymousWebFetcher(
            security_guard=HttpSecurityGuard(resolver=lambda _host: ["93.184.216.34"]),
            transport=httpx.MockTransport(handler), timeout_seconds=5,
        ))

    def owner(request: Request):
        return {"user_id": request.headers.get("X-Test-Owner", "owner-a")}

    monkeypatch.setattr(source_routes, "get_source_acquisition_service", service)
    app = FastAPI()
    app.include_router(source_routes.router)
    app.dependency_overrides[get_current_user] = owner
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        reading = asyncio.create_task(client.post(
            "/api/semantic-workspace/source-acquisitions",
            headers={"Idempotency-Key": "cancel-stream"},
            json={"url": "https://example.com/page", "purpose": "隔离取消验证",
                  "allowed_scope": "same_site" if scope == "same_site" else "current_page",
                  "page_limit": 3},
        ))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            attempt = repository.get_by_idempotency_key("owner-a", "cancel-stream")
            url = f"/api/semantic-workspace/source-acquisitions/{attempt['attempt_id']}"
            refused = await client.post(url + "/cancel", headers={"X-Test-Owner": "owner-b"})
            assert refused.status_code == 404
            assert not closed.is_set()
            requested = await client.post(url + "/cancel")
            assert requested.status_code == 200
            assert requested.json()["status"] in {"cancelling", "canceled"}
            if requested.json()["status"] == "canceled":
                assert closed.is_set(), "只有真正停止读取才可确认已取消"
            result = await asyncio.wait_for(reading, timeout=1)
            assert closed.is_set()
            assert result.json()["status"] == "canceled"
            confirmed = (await client.get(url)).json()
            assert confirmed["status"] == "canceled"
            assert confirmed["snapshot_id"] is None
            assert confirmed["finished_at"] is not None
            assert repository.count_snapshots("owner-a") == 0
            assert visited == (["/page", "/article"] if scope == "redirect" else ["/page"])
            replay = await client.post(url + "/cancel")
            assert replay.json()["status"] == "canceled"
        finally:
            reading.cancel()
            with suppress(asyncio.CancelledError):
                await reading


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_resource", ["stream", "transport"])
@pytest.mark.parametrize("cancel_again", [False, True])
async def test_close_failure_keeps_cancelling_until_retry_finishes(tmp_path, failed_resource, cancel_again):
    database = migrated_webui_database(tmp_path / "close-failure.db")
    repository = SourceAcquisitionRepository(database)
    observer = SourceAcquisitionRepository(database)
    started = asyncio.Event()
    retry_started = asyncio.Event()
    allow_close = asyncio.Event()
    stream_closed = asyncio.Event()
    transport_closed = asyncio.Event()
    calls = {"stream": 0, "transport": 0}

    async def close_resource(name, closed):
        calls[name] += 1
        if name == failed_resource:
            if calls[name] == 1:
                raise httpx.ReadError("隔离关闭故障")
            retry_started.set()
            await allow_close.wait()
        closed.set()

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            await asyncio.Event().wait()
            yield b"unreachable"

        async def aclose(self):
            await close_resource("stream", stream_closed)

    class ClosingTransport(httpx.MockTransport):
        async def aclose(self):
            await close_resource("transport", transport_closed)

    transport = ClosingTransport(lambda request: httpx.Response(
        200, headers={"content-type": "text/html"}, stream=SlowStream(),
    ))
    service = SourceAcquisitionService(repository, AnonymousWebFetcher(
        security_guard=HttpSecurityGuard(resolver=lambda _host: ["93.184.216.34"]),
        transport=transport, timeout_seconds=5,
    ))
    reading = asyncio.create_task(service.acquire(
        owner_id="owner-a", idempotency_key="close-failure",
        request=SourceAcquisitionRequest(url="https://example.com/page", purpose="隔离关闭验证"),
    ))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        attempt = observer.get_by_idempotency_key("owner-a", "close-failure")
        attempt_id = attempt["attempt_id"]
        assert observer.cancel_attempt("owner-a", attempt_id)["status"] == "cancelling"
        await asyncio.wait_for(retry_started.wait(), timeout=2)
        if cancel_again:
            reading.cancel()
            # 回调屏障让二次取消先传播，不依赖固定睡眠猜测调度时序。
            checkpoint = asyncio.get_running_loop().create_future()
            asyncio.get_running_loop().call_soon(checkpoint.set_result, None)
            await checkpoint
        assert not reading.done()
        assert observer.get_attempt("owner-a", attempt_id)["status"] == "cancelling"
        assert observer.count_snapshots("owner-a") == 0
        assert not (stream_closed.is_set() and transport_closed.is_set())
        allow_close.set()
        result = await asyncio.wait_for(asyncio.shield(reading), timeout=2)
        assert result["status"] == "canceled"
        assert stream_closed.is_set() and transport_closed.is_set()
        assert observer.get_attempt("owner-a", attempt_id)["status"] == "canceled"
        assert observer.count_snapshots("owner-a") == 0
    finally:
        allow_close.set()
        if not reading.done():
            reading.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(asyncio.shield(reading), timeout=3)


@pytest.mark.asyncio
async def test_process_lock_exit_recovers_cancellation_without_repeating_read(tmp_path):
    database = migrated_webui_database(tmp_path / "process-cancel.db")
    repository = SourceAcquisitionRepository(database)
    request = SourceAcquisitionRequest(url="https://example.com/page", purpose="隔离进程恢复验证")
    attempt, created = repository.claim_attempt(
        owner_id="owner-a", idempotency_key="process-exit", request=request,
    )
    assert created
    attempt_id = attempt["attempt_id"]
    lock = repository.execution_lock("owner-a", attempt_id)
    # 子进程只持有临时数据库对应的真实文件锁，握手后模拟进程崩溃。
    script = (
        "import sys\nfrom filelock import FileLock\n"
        "with FileLock(sys.argv[1]):\n"
        " print('locked', flush=True)\n"
        " sys.stdin.readline()\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(lock.lock_file)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    try:
        assert await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), timeout=10) == "locked\n"
        observer = SourceAcquisitionRepository(database)
        assert observer.cancel_attempt("owner-a", attempt_id)["status"] == "cancelling"
        assert observer.get_attempt("owner-a", attempt_id)["status"] == "cancelling"
        assert observer.count_snapshots("owner-a") == 0
        child.kill()
        assert await asyncio.to_thread(child.wait, timeout=5) != 0
        restored = SourceAcquisitionRepository(database)
        assert restored.get_attempt("owner-a", attempt_id)["status"] == "canceled"

        class NeverRead:
            async def fetch(self, url):
                pytest.fail("恢复取消事实不得重新读取来源")

        for _ in range(2):
            result = await SourceAcquisitionService(restored, NeverRead()).acquire(
                owner_id="owner-a", idempotency_key="process-exit", request=request, resume_unknown=True,
            )
            assert result["status"] == "canceled"
        assert restored.count_snapshots("owner-a") == 0
    finally:
        if child.poll() is None:
            child.kill()
            await asyncio.to_thread(child.wait, timeout=5)
        for pipe in (child.stdin, child.stdout, child.stderr):
            pipe.close()
