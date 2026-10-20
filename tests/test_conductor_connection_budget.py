"""通过真实 Broker 和隔离 SQLite 验证长调用预算，不请求外部模型。"""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import httpx
import pytest

from src.account_execution import execution_context
from src.api.execution import execution_validation
from src.api.store import WebUIStore
from src.config.settings import settings
from src.llm.provider import _bound_chat
from src.model_connections import ConnectionBroker, conductor
from src.model_connections.storage import ModelConnectionRepository
from src.model_connections.vault import FernetCredentialVault
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture
def call_model(tmp_path, monkeypatch):
    database = migrated_webui_database(tmp_path / "conductor.db")
    owner = seed_execution_owner(database, "budget-owner")
    store = WebUIStore(str(database))
    repository = ModelConnectionRepository(str(database))
    now = [datetime.now(timezone.utc)]
    calls = []

    async def invoke(provider, protocol="openai_chat_completions"):
        async def send(request):
            calls.append(json.loads(request.content))
            return await provider(request)

        broker = ConnectionBroker(repository=repository, vault=FernetCredentialVault.generate(),
            transport=httpx.MockTransport(send), resolver=lambda _: ["8.8.8.8"], clock=lambda: now[0])
        connection = repository.create_managed(created_by=owner.owner_user_id, display_name="合成长调用模型",
            base_url="https://provider.example/v1", model="fixture-model", api_format=protocol,
            locality="public_external", ciphertext=None, key_hint="", verified_at="2026-01-01")
        monkeypatch.setattr(conductor, "get_default_broker", lambda: broker)
        with execution_context(owner), execution_validation(store.require_account_authorization):
            binding = broker.freeze_connection(owner.owner_user_id, connection["connection_id"])
            with conductor.conductor_connection(owner_id=owner.owner_user_id, connection_id=binding.connection_id,
                connection_version=binding.connection_version, model=binding.model, task_id="budget-task", run_id="budget-run"):
                generate = _bound_chat.get()
                result = await generate([{"role": "user", "content": "合成请求"}])
                return result

    return invoke, now, calls, repository, database


@pytest.mark.parametrize("protocol", ["openai_chat_completions", "openai_responses"])
@pytest.mark.parametrize("budget,elapsed", [(600, 576), (1800, 1000)])
def test_conductor_accepts_one_response_within_execution_budget(call_model, monkeypatch, protocol, budget, elapsed):
    invoke, now, calls, _, database = call_model
    monkeypatch.setattr(settings, "pi_runtime_timeout_seconds", budget)

    async def provider(request):
        now[0] += timedelta(seconds=elapsed)
        if protocol == "openai_responses":
            payload = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "合成成功"}]}],
                "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}}
        else:
            payload = {"choices": [{"message": {"content": "合成成功"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}}
        return httpx.Response(200, json=payload)

    assert asyncio.run(invoke(provider, protocol)) == "合成成功"
    assert len(calls) == 1
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT status,total_tokens FROM model_provider_usage").fetchall() == [("recorded", 3)]
        assert db.execute("SELECT revoke_reason FROM model_connection_grants").fetchall() == [("conductor_call_finished",)]


@pytest.mark.parametrize("block", ["expired", "disabled", "revoked"])
def test_conductor_long_call_still_rejects_invalid_authorization(call_model, monkeypatch, block):
    invoke, now, calls, repository, database = call_model
    monkeypatch.setattr(settings, "pi_runtime_timeout_seconds", 600)

    async def provider(request):
        now[0] += timedelta(seconds=576 if block != "expired" else 661)
        with sqlite3.connect(database) as db:
            grant = db.execute("SELECT grant_id,connection_id FROM model_connection_grants").fetchone()
        if block == "disabled":
            repository.set_platform_enabled(grant[1], enabled=False)
        elif block == "revoked":
            repository.revoke_grant(grant[0], "synthetic-revocation")
        return httpx.Response(200, json={"choices": [{"message": {"content": "不可用结果"}}]})

    with pytest.raises(ValueError, match="未切换模型或自动重发"):
        asyncio.run(invoke(provider))
    assert len(calls) == 1
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT status FROM model_provider_usage").fetchall() == [("unknown",)]


@pytest.mark.parametrize("stage", ["headers", "body"])
def test_conductor_stops_at_total_call_budget_without_resending(call_model, monkeypatch, stage):
    invoke, _, calls, _, database = call_model
    monkeypatch.setattr(settings, "pi_runtime_timeout_seconds", 1)
    closed = []

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(2)
            yield b'{"choices":[{"message":{"content":"late"}}]}'

        async def aclose(self):
            closed.append(True)

    async def provider(request):
        if stage == "body":
            return httpx.Response(200, stream=SlowBody())
        await asyncio.sleep(2)
        return httpx.Response(200, json={"choices": [{"message": {"content": "超时结果"}}]})

    with pytest.raises(ValueError, match="未切换模型或自动重发"):
        asyncio.run(invoke(provider))
    assert len(calls) == 1
    if stage == "body":
        assert closed
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT status FROM model_provider_usage").fetchall() == [("unknown",)]
        assert db.execute("SELECT revoke_reason FROM model_connection_grants").fetchall() == [("conductor_call_finished",)]
