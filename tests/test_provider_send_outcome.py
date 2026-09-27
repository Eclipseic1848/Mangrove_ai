"""真实回环故障证明发送边界；不调用外部模型、不改生产连接。"""
import asyncio
import json
import socket
import sqlite3

import httpx
import pytest

from src.account_execution import execution_context
from src.model_connections import ConnectionBroker
from src.model_connections import ProviderNotSentError, ProviderOutcomeUnknownError
from src.model_connections.storage import ModelConnectionRepository
from src.model_connections.vault import FernetCredentialVault
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database


PROTOCOLS = {
    "openai_chat_completions": "chat/completions",
    "openai_responses": "responses",
    "anthropic_messages": "messages",
    "gemini_generate_content": "models/fixture-model:generateContent",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("first_reachable", [False, True])
async def test_pinned_connection_uses_next_validated_address_before_sending(first_reachable):
    from src.connectors.http_security import ValidatedTarget
    from src.model_connections.pinned_transport import PinnedAsyncHTTPTransport

    requests = []
    finished = asyncio.Event()
    async def provider(reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            length = next(int(line.split(b":", 1)[1]) for line in head.split(b"\r\n")
                          if line.lower().startswith(b"content-length:"))
            requests.append((head, await reader.readexactly(length)))
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            finished.set()

    server = await asyncio.start_server(provider, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    refused = socket.socket()
    # 首地址只占端口不监听，次地址为真实服务；任何模型正文最多发送一次。
    refused.bind(("127.0.0.2", port))
    target = ValidatedTarget(url=f"http://provider.test:{port}/v1", scheme="http", host="provider.test",
                             port=port, ips=(("127.0.0.1", "127.0.0.2") if first_reachable else ("127.0.0.2", "127.0.0.1")))
    try:
        # Windows 本机拒绝连接也可能等待约 2 秒；留足切换预算，预算耗尽另有确定性回归。
        async with httpx.AsyncClient(transport=PinnedAsyncHTTPTransport(target=target), timeout=5) as client:
            response = await client.post(target.url, content=b"synthetic-body")
            assert response.status_code == 200 and response.text == "OK"
        await asyncio.wait_for(finished.wait(), 2)
        assert len(requests) == 1 and requests[0][1] == b"synthetic-body"
        assert f"Host: provider.test:{port}".encode() in requests[0][0]
    finally:
        refused.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,fault,attempts", [
    ("connect", "connect", 2), ("none", "connect", 1), ("send", "connect", 1),
    ("connect", "protocol", 1), ("connect", "cancel", 1), ("connect", "http500", 1),
])
async def test_pinned_fallback_requires_unsent_connection_evidence(stage, fault, attempts):
    from src.connectors.http_security import ValidatedTarget
    from src.model_connections.pinned_transport import PinnedAsyncHTTPTransport

    addresses, events = [], []
    errors = {"connect": httpx.ConnectError, "protocol": httpx.RemoteProtocolError, "cancel": asyncio.CancelledError}
    async def provider(request):
        addresses.append(request.url.host)
        assert request.headers["host"] == "provider.test" and request.extensions["sni_hostname"] == "provider.test"
        if len(addresses) == 2:
            return httpx.Response(200, content=b"OK")
        if stage != "none":
            await request.extensions["trace"]("connection.connect_tcp.started", {})
        if stage == "send":
            await request.extensions["trace"]("http11.send_request_headers.started", {})
        if fault == "http500":
            return httpx.Response(500)
        raise errors[fault]("synthetic-failure")
    async def original_trace(event, info):
        events.append(event)
    target = ValidatedTarget("http://provider.test/v1", "http", "provider.test", 80, ("192.0.2.1", "192.0.2.2"))
    async with httpx.AsyncClient(transport=PinnedAsyncHTTPTransport(target=target, transport=httpx.MockTransport(provider))) as client:
        if attempts == 2 or fault == "http500":
            response = await client.post(target.url, extensions={"trace": original_trace})
            assert response.status_code == (500 if fault == "http500" else 200)
        else:
            with pytest.raises(errors[fault]):
                await client.post(target.url, extensions={"trace": original_trace})
    assert len(addresses) == attempts
    assert bool(events) is (stage != "none")


@pytest.mark.asyncio
@pytest.mark.parametrize("elapsed,attempts", [(1.25, 2), (5.0, 1)])
async def test_pinned_addresses_share_connect_budget(monkeypatch, elapsed, attempts):
    from types import SimpleNamespace
    from src.connectors.http_security import ValidatedTarget
    from src.model_connections import pinned_transport

    now, budgets = [0.0], []
    monkeypatch.setattr(pinned_transport, "time", SimpleNamespace(monotonic=lambda: now[0]))
    async def provider(request):
        budgets.append(request.extensions["timeout"]["connect"])
        await request.extensions["trace"]("connection.connect_tcp.started", {})
        now[0] += elapsed
        raise httpx.ConnectError("synthetic-refused")
    target = ValidatedTarget("http://provider.test/v1", "http", "provider.test", 80, ("192.0.2.1", "192.0.2.2"))
    transport = pinned_transport.PinnedAsyncHTTPTransport(target=target, transport=httpx.MockTransport(provider))
    async with httpx.AsyncClient(transport=transport, timeout=5.0) as client:
        with pytest.raises(httpx.ConnectError):
            await client.post(target.url)
    assert budgets == ([5.0, 3.75] if attempts == 2 else [5.0])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["tls_timeout", "slow_response"])
async def test_native_tcp_and_tls_share_deadline_but_response_does_not(mode):
    import httpcore
    from src.connectors.http_security import ValidatedTarget
    from src.model_connections.pinned_transport import PinnedAsyncHTTPTransport

    sent, stages, closed = [], [], []
    class Stream(httpcore.AsyncMockStream):
        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            stages.append("tls")
            await asyncio.wait_for(asyncio.sleep(.55 if mode == "tls_timeout" else 0), timeout)
            return self
        async def write(self, buffer, timeout=None):
            sent.append(buffer)
            await super().write(buffer, timeout=timeout)
        async def read(self, max_bytes, timeout=None):
            if mode == "slow_response":
                await asyncio.sleep(1.1)
            return await super().read(max_bytes, timeout=timeout)
        async def aclose(self):
            closed.append(True)
            await super().aclose()
    class Backend(httpcore.AsyncMockBackend):
        async def connect_tcp(self, host, port, timeout=None, **kwargs):
            if host == "192.0.2.1":
                await asyncio.sleep(.2 if mode == "tls_timeout" else 0)
                raise httpcore.ConnectError("synthetic-refused")
            await asyncio.wait_for(asyncio.sleep(.55 if mode == "tls_timeout" else 0), timeout)
            return Stream([b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK"])

    # 仅替换原生网络后端；HTTP阶段与请求发送仍由实际httpcore执行。
    native = httpx.AsyncHTTPTransport()
    native._pool._network_backend = Backend([])
    target = ValidatedTarget("https://provider.test/v1", "https", "provider.test", 443, ("192.0.2.1", "192.0.2.2"))
    async with httpx.AsyncClient(transport=PinnedAsyncHTTPTransport(target=target, transport=native),
                                 timeout=httpx.Timeout(3, connect=1)) as client:
        if mode == "tls_timeout":
            with pytest.raises(httpx.ConnectTimeout):
                await client.get(target.url)
            assert not sent and closed
        else:
            assert (await client.get(target.url)).text == "OK"
    assert stages == ["tls"]


def prepare(tmp_path, endpoint, protocol):
    database = migrated_webui_database(tmp_path / "relay.db")
    owner = seed_execution_owner(database, "synthetic-owner")
    repository = ModelConnectionRepository(str(database))
    vault = FernetCredentialVault.generate()
    connection = repository.create_managed(created_by=owner.owner_user_id, display_name="合成故障服务",
        base_url=endpoint, model="fixture-model", api_format=protocol, locality="managed_private",
        ciphertext=vault.encrypt("synthetic-secret"), key_hint="fake", verified_at="2026-01-01")
    broker = ConnectionBroker(repository=repository, vault=vault, provider_timeout_seconds=0.5)
    with execution_context(owner):
        binding = broker.freeze_connection(owner.owner_user_id, connection["connection_id"])
        grant = broker.issue_grant(owner_user_id=owner.owner_user_id, connection_id=binding.connection_id,
            connection_version=binding.connection_version, task_id="synthetic-task", revision=1,
            run_id="synthetic-run", purpose="context_rewrite")
    return database, broker, grant


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["revoke", "disable"])
async def test_native_alternate_connect_cannot_send_after_grant_changes(tmp_path, action):
    import httpcore
    from src.model_connections import GrantError

    database, broker, grant = prepare(tmp_path, "http://localhost:1/v1", "openai_chat_completions")
    broker._resolver = lambda host: ["127.0.0.2", "127.0.0.1"]
    sent = []
    class Stream(httpcore.AsyncMockStream):
        async def write(self, buffer, timeout=None):
            sent.append(buffer)
            await super().write(buffer, timeout=timeout)
    class Backend(httpcore.AsyncMockBackend):
        async def connect_tcp(self, host, port, timeout=None, **kwargs):
            if host == "127.0.0.2":
                raise httpcore.ConnectError("synthetic-refused")
            if action == "revoke":
                broker.revoke_grant(grant.grant_id, "synthetic-revocation")
            else:
                broker._repository.set_platform_enabled(grant.connection_id, enabled=False)
            return Stream([b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}"])
    broker._transport = httpx.AsyncHTTPTransport()
    broker._transport._pool._network_backend = Backend([])
    with pytest.raises(GrantError):
        await broker.relay(grant_token=grant.token, protocol_path="chat/completions", method="POST",
                           headers={}, body=b'{"model":"fixture-model"}')
    assert sent == []
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT request_count FROM model_provider_usage").fetchall() == [(1,)]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["allowed", "revoke", "disable"])
async def test_pinned_fallback_rechecks_grant_and_records_one_usage(tmp_path, action):
    from src.model_connections import GrantError

    database, broker, grant = prepare(tmp_path, "http://localhost:1/v1", "openai_chat_completions")
    broker._resolver = lambda host: ["127.0.0.2", "127.0.0.1"]
    attempted, sent = [], []
    async def provider(request):
        attempted.append(request.url.host)
        await request.extensions["trace"]("connection.connect_tcp.started", {})
        if len(attempted) == 1:
            if action == "revoke":
                broker.revoke_grant(grant.grant_id, "synthetic-revocation")
            elif action == "disable":
                broker._repository.set_platform_enabled(grant.connection_id, enabled=False)
            raise httpx.ConnectError("synthetic-refused")
        sent.append(request.content)
        return httpx.Response(200, json={"usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}})
    broker._transport = httpx.MockTransport(provider)
    body = b'{"model":"fixture-model"}'
    async def invoke():
        response = await broker.relay(grant_token=grant.token, protocol_path="chat/completions", method="POST",
                                      headers={}, body=body)
        try:
            return b"".join([chunk async for chunk in response.iter_bytes()])
        finally:
            await response.aclose()
    if action == "allowed":
        assert json.loads(await invoke())["usage"]["total_tokens"] == 3
    else:
        with pytest.raises(GrantError):
            await invoke()
    assert attempted == ["127.0.0.2", "127.0.0.1"] and sent == ([body] if action == "allowed" else [])
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT request_count, total_tokens FROM model_provider_usage").fetchall()
    assert rows == [(1, 3 if action == "allowed" else None)]


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("fault", ["refused", "disconnected", "timeout", "cancel", "body_timeout", "body_cancel"])
async def test_real_transport_keeps_unsent_distinct_from_unknown(tmp_path, protocol, fault):
    received, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    requests = []

    async def provider(reader, writer):
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(int(line.split(b":", 1)[1]) for line in headers.split(b"\r\n")
                          if line.lower().startswith(b"content-length:"))
            requests.append(await reader.readexactly(length))
            if fault.startswith("body_"):
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{")
                await writer.drain()
            received.set()
            if fault != "disconnected":
                await release.wait()
        finally:
            writer.close()
            await writer.wait_closed()
            finished.set()

    # 保持端口占有但不监听，避免关闭后被别的进程抢占造成假阳性。
    bound = socket.socket()
    bound.bind(("127.0.0.1", 0))
    port = bound.getsockname()[1]
    server = None if fault == "refused" else await asyncio.start_server(provider, sock=bound)
    database, broker, grant = prepare(tmp_path, f"http://127.0.0.1:{port}/v1", protocol)

    async def invoke():
        response = await broker.relay(grant_token=grant.token, protocol_path=PROTOCOLS[protocol], method="POST",
            headers={}, body=json.dumps({"model": "fixture-model", "messages": [{"role": "user", "content": "private-fixture"}]}).encode("utf-8"))
        try:
            return b"".join([chunk async for chunk in response.iter_bytes()])
        finally:
            await response.aclose()

    pending = asyncio.create_task(invoke())
    try:
        if "cancel" in fault:
            await asyncio.wait_for(received.wait(), 5)
            pending.cancel()
        with pytest.raises(BaseException) as error:
            await asyncio.wait_for(pending, 5)
    finally:
        release.set()
        if server is not None:
            server.close()
            await server.wait_closed()
            await asyncio.wait_for(finished.wait(), 5)
        else:
            bound.close()

    assert len(requests) == (0 if fault == "refused" else 1), "未知不能自动重发"
    if fault == "refused":
        assert type(error.value).__name__ == "ProviderNotSentError"
        assert isinstance(error.value.__cause__, (httpx.ConnectError, httpx.ConnectTimeout))
    elif "cancel" in fault:
        assert isinstance(error.value, asyncio.CancelledError)
    elif fault.startswith("body_"):
        assert isinstance(error.value, httpx.ReadTimeout)
    else:
        assert type(error.value).__name__ == "ProviderOutcomeUnknownError"
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT grant_id, run_id, status, total_tokens, request_count, native_json FROM model_provider_usage").fetchall()
    assert len(rows) == 1
    assert rows[0][:5] == (grant.grant_id, "synthetic-run", "unknown", None, 1)
    assert "private-fixture" not in rows[0][5] and "synthetic-secret" not in rows[0][5]
    if fault == "refused":
        assert json.loads(rows[0][5])["relay_outcome"] == "not_sent"
    # 数据库重开后仍保留同一次失败尝试；不把未知 Token 变成零或遗失历史。
    reopened = ConnectionBroker(repository=ModelConnectionRepository(str(database)), vault=broker._vault)
    assert reopened.get_usage_for_grant(grant.owner_user_id, task_id=grant.task_id, revision=1,
                                   run_id=grant.run_id, grant_id=grant.grant_id)["request_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type,outcome", [(ProviderNotSentError, "not_sent"), (ProviderOutcomeUnknownError, "unknown")])
async def test_relay_http_error_exposes_only_safe_outcome(error_type, outcome):
    from fastapi import FastAPI
    from src.api.routes.model_relay import router
    from src.api.routes.model_connections import get_connection_broker

    class Broker:
        async def relay(self, **kwargs):
            raise error_type("private-fixture synthetic-secret")

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_connection_broker] = Broker
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/internal/model-relay/chat/completions",
                                     headers={"Authorization": "Bearer synthetic-grant"}, json={})
    assert response.status_code == 502
    assert response.headers["X-Mangrove-Provider-Outcome"] == outcome
    assert "private-fixture" not in response.text and "synthetic-secret" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [ProviderNotSentError, ProviderOutcomeUnknownError])
async def test_recovery_does_not_resend_either_failure(tmp_path, error_type):
    from types import SimpleNamespace
    from src.agentic_runtime.output_requirements import freeze_output_requirements

    calls = []
    async def infer():
        calls.append(1)
        raise error_type("private-fixture")

    request = SimpleNamespace(user_id="owner", task_id="task", revision=1,
                              objective_text="输出result.json", requested_output_formats=("json",))
    frozen = await freeze_output_requirements(tmp_path, request, "run", infer)
    assert frozen["status"] == "unverified"
    assert frozen["error_type"] == error_type.__name__
    assert await freeze_output_requirements(tmp_path, request, "run", infer) == frozen
    assert calls == [1] and "private-fixture" not in json.dumps(frozen)


@pytest.mark.asyncio
@pytest.mark.parametrize("has_usage", [True, False])
async def test_complete_response_overrides_earlier_write_failure(tmp_path, has_usage):
    database, broker, grant = prepare(tmp_path, "http://127.0.0.1:1/v1", "openai_chat_completions")

    async def provider(request):
        # httpcore 允许服务端在请求正文写完前返回完整响应，不应被旧 trace 错误污染。
        await request.extensions["trace"]("http11.send_request_body.failed", {"exception": httpx.WriteError("private-fixture")})
        payload = {"choices": [{"message": {"content": "OK"}}]}
        if has_usage:
            payload["usage"] = {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}
        return httpx.Response(200, json=payload)

    broker._transport = httpx.MockTransport(provider)
    response = await broker.relay(grant_token=grant.token, protocol_path="chat/completions", method="POST",
                                 headers={}, body=b'{"model":"fixture-model"}')
    assert json.loads(b"".join([chunk async for chunk in response.iter_bytes()]))["choices"]
    with sqlite3.connect(database) as connection:
        status, total, raw = connection.execute("SELECT status,total_tokens,native_json FROM model_provider_usage").fetchone()
    assert (status, total) == (("recorded", 3) if has_usage else ("unknown", None))
    assert "relay_outcome" not in json.loads(raw), "完整响应不等于未知响应；未知用量也不等于响应未知"
