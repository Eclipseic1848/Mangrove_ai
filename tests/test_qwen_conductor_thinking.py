"""用真实 Broker 与隔离 SQLite 验证冻结 Qwen 连接的思考配置，不调用云端。"""
import asyncio
import json
import sqlite3

import httpx
import pytest

from src.account_execution import execution_context
from src.api.execution import execution_validation
from src.api.store import WebUIStore
from src.config.settings import settings
from src.llm import achat
from src.model_connections import ConnectionBroker, conductor
from src.model_connections.storage import ModelConnectionRepository
from src.model_connections.vault import FernetCredentialVault
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture
def bound_call(tmp_path, monkeypatch):
    database = migrated_webui_database(tmp_path / "qwen-bound.db")
    owner = seed_execution_owner(database, "qwen-owner")
    store = WebUIStore(str(database))
    repository = ModelConnectionRepository(str(database))
    calls = []
    connection = None

    async def invoke(*, preset="qwen", model="qwen3.8-max", protocol="openai_responses",
                     thinking=False, max_tokens=4096, disabled=False):
        nonlocal connection
        monkeypatch.setattr(settings, "qwen_enable_thinking", thinking)

        async def send(request):
            calls.append(json.loads(request.content))
            if disabled:
                repository.set_platform_enabled(connection["connection_id"], enabled=False)
            if protocol == "openai_responses":
                payload = {"status": "completed", "output_text": "合成结果",
                           "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}}
            else:
                payload = {"choices": [{"message": {"content": "合成结果"}, "finish_reason": "stop"}],
                           "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}}
            return httpx.Response(200, json=payload)

        broker = ConnectionBroker(repository=repository, vault=FernetCredentialVault.generate(),
            transport=httpx.MockTransport(send), resolver=lambda _: ["8.8.8.8"])
        connection = repository.create_managed(created_by=owner.owner_user_id, display_name="合成思考配置连接",
            base_url="https://provider.example/v1", model=model, api_format=protocol,
            locality="public_external", ciphertext=None, key_hint="", verified_at="2026-01-01",
            preset_id=preset)
        monkeypatch.setattr(conductor, "get_default_broker", lambda: broker)
        with execution_context(owner), execution_validation(store.require_account_authorization):
            binding = broker.freeze_connection(owner.owner_user_id, connection["connection_id"])
            with conductor.conductor_connection(owner_id=owner.owner_user_id, connection_id=binding.connection_id,
                connection_version=binding.connection_version, model=binding.model,
                task_id="qwen-bound-task", run_id="qwen-bound-run"):
                return await achat([{"role": "system", "content": "只返回结构化结论"},
                    {"role": "user", "content": "合成范围判断"}], max_tokens=max_tokens)

    return invoke, calls, database


@pytest.mark.parametrize("model", ["qwen3.8-max", "qwen3.8-flash"])
@pytest.mark.parametrize("max_tokens", [4096, 65536])
def test_bound_qwen_responses_honors_existing_thinking_disabled(bound_call, model, max_tokens):
    invoke, calls, database = bound_call
    assert asyncio.run(invoke(model=model, max_tokens=max_tokens)) == "合成结果"
    assert len(calls) == 1
    assert calls[0]["reasoning"] == {"effort": "none"}
    assert calls[0]["model"] == model
    assert calls[0]["max_output_tokens"] == max_tokens
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT status,total_tokens FROM model_provider_usage").fetchall() == [("recorded", 3)]
        assert db.execute("SELECT revoke_reason FROM model_connection_grants").fetchall() == [("conductor_call_finished",)]


@pytest.mark.parametrize("preset,model,protocol,thinking", [
    ("qwen", "qwen3.8-max", "openai_responses", True),
    ("qwen", "qwen3.8-max", "openai_responses", None),
    ("openai", "qwen3.8-max", "openai_responses", False),
    (None, "qwen3.8-max", "openai_responses", False),
    ("qwen", "qwen3-max", "openai_responses", False),
    ("qwen", "qwen3.8-max", "openai_chat_completions", False),
    ("openai", "gpt-5.6-sol", "openai_responses", False),
    ("deepseek", "deepseek-flash", "openai_chat_completions", False),
])
def test_other_models_protocols_and_explicit_settings_keep_original_request(bound_call, preset, model, protocol, thinking):
    invoke, calls, _ = bound_call
    assert asyncio.run(invoke(preset=preset, model=model, protocol=protocol, thinking=thinking)) == "合成结果"
    assert len(calls) == 1
    assert calls[0]["model"] == model
    assert "reasoning" not in calls[0]
    assert "enable_thinking" not in calls[0]
    assert "chat_template_kwargs" not in calls[0]


def test_qwen_thinking_option_never_bypasses_revoked_model(bound_call):
    invoke, calls, database = bound_call
    with pytest.raises(ValueError, match="未切换模型或自动重发"):
        asyncio.run(invoke(disabled=True))
    assert len(calls) == 1
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT status FROM model_provider_usage").fetchall() == [("unknown",)]
        assert db.execute("SELECT count(*) FROM model_connection_grants WHERE revoked_at IS NULL").fetchone()[0] == 0
