"""完整 ASGI 生命周期计数，维护放行不替代业务鉴权。"""
from __future__ import annotations

import asyncio
import json

import pytest
from starlette.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from src.api import deployment_middleware as module
from src.services.deployment_drain import DeploymentGate


def marker(gate, phase):
    gate.request_path.write_text(json.dumps({"version": 1, "request_id": "b" * 32, "phase": phase}), encoding="utf-8")


def scope(path, method="POST"):
    return {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
        "path": path, "raw_path": path.encode(), "method": method,
        "headers": [(b"x-mangrove-task-control", b"true")], "query_string": b"",
        "scheme": "http", "server": ("testserver", 80), "client": ("127.0.0.1", 1),
    }


async def receive():
    return {"type": "http.request", "body": b"", "more_body": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("path,allowed", [
    ("/api/tasks/manual", False),
    ("/api/semantic-workspace/tasks", False),
    ("/api/semantic-workspace/tasks/task/account-resume", False),
    ("/api/semantic-workspace/tasks/task/answer", False),
    ("/api/semantic-workspace/tasks/task/source-refresh", False),
    ("/api/semantic-workspace/tasks/task/cancel", True),
    ("/api/semantic-workspace/source-acquisitions/attempt/cancel", True),
    ("/api/admin/users/user/execution-hold/retry", True),
    ("/api/semantic-workspace/deletion-operations/op/resume", True),
    ("/api/auth/login", True),
    ("/internal/model-relay/v1/chat/completions", True),
    ("/internal/document-tools/search", True),
    ("/internal/document-tools/search/extra", False),
])
async def test_drain_only_admits_existing_cleanup_and_relay(tmp_path, monkeypatch, path, allowed):
    gate = DeploymentGate(tmp_path)
    monkeypatch.setattr(module, "get_deployment_gate", lambda: gate)
    marker(gate, "draining")
    called = []
    messages = []

    async def original(request, receive, send):
        called.append(request)
        assert gate.snapshot()["active"] == {"http": 1}
        # 原处理器照常拒绝无授权请求；排空门不能把 Relay 变成匿名入口。
        await JSONResponse({"detail": "synthetic original authentication"}, status_code=401)(request, receive, send)

    async def send(message):
        messages.append(message)

    await module.DeploymentAdmissionMiddleware(original)(scope(path), receive, send)
    assert bool(called) is allowed
    assert messages[0]["status"] == (401 if allowed else 503)
    assert gate.snapshot()["total_active"] == 0


@pytest.mark.asyncio
async def test_headers_body_and_background_all_remain_active(tmp_path, monkeypatch):
    gate = DeploymentGate(tmp_path)
    monkeypatch.setattr(module, "get_deployment_gate", lambda: gate)
    headers_sent, release_body, background_started, release_background = [asyncio.Event() for _ in range(4)]

    async def body():
        await release_body.wait()
        yield b"done"

    async def background():
        background_started.set()
        await release_background.wait()

    async def original(request, receive, send):
        await StreamingResponse(body(), background=BackgroundTask(background))(request, receive, send)

    async def send(message):
        if message["type"] == "http.response.start":
            headers_sent.set()

    task = asyncio.create_task(module.DeploymentAdmissionMiddleware(original)(scope("/internal/model-relay/v1"), receive, send))
    await asyncio.wait_for(headers_sent.wait(), 2)
    marker(gate, "draining")
    assert gate.snapshot()["total_active"] == 1
    release_body.set()
    await asyncio.wait_for(background_started.wait(), 2)
    assert gate.snapshot()["total_active"] == 1
    release_background.set()
    await task
    assert gate.snapshot()["total_active"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,expected", [("draining", 200), ("sealed", 503)])
async def test_history_is_counted_while_draining_and_paused_when_sealed(tmp_path, monkeypatch, phase, expected):
    gate = DeploymentGate(tmp_path)
    monkeypatch.setattr(module, "get_deployment_gate", lambda: gate)
    marker(gate, phase)
    messages = []

    async def original(request, receive, send):
        assert gate.snapshot()["active"] == {"http_read": 1}
        await JSONResponse({"ok": True})(request, receive, send)

    async def send(message):
        if message["type"] == "http.response.start":
            messages.append(message["status"])

    middleware = module.DeploymentAdmissionMiddleware(original)
    await middleware(scope("/api/semantic-workspace/tasks/task", "GET"), receive, send)
    assert messages == [expected]
    assert gate.snapshot()["total_active"] == 0


@pytest.mark.asyncio
async def test_sealed_preserves_cheap_health_and_static_resources(tmp_path, monkeypatch):
    gate = DeploymentGate(tmp_path)
    monkeypatch.setattr(module, "get_deployment_gate", lambda: gate)
    marker(gate, "sealed")
    messages = []

    async def original(request, receive, send):
        assert gate.snapshot()["total_active"] == 0
        await JSONResponse({"ok": True})(request, receive, send)

    async def send(message):
        if message["type"] == "http.response.start":
            messages.append(message["status"])

    middleware = module.DeploymentAdmissionMiddleware(original)
    for path in ("/api/health", "/api/readiness", "/assets/previous.mjs"):
        await middleware(scope(path, "GET"), receive, send)
    assert messages == [200, 200, 200]


@pytest.mark.asyncio
async def test_handler_failure_releases_counter(tmp_path, monkeypatch):
    gate = DeploymentGate(tmp_path)
    monkeypatch.setattr(module, "get_deployment_gate", lambda: gate)

    async def original(*args):
        raise RuntimeError("synthetic")

    with pytest.raises(RuntimeError):
        await module.DeploymentAdmissionMiddleware(original)(scope("/api/tasks"), receive, None)
    assert gate.snapshot()["total_active"] == 0
