"""默认输出遵循可信模型容量，显式思考与操作额度不污染其他调用或绕过冻结连接。"""
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
from src.model_connections.catalog import PRESETS_BY_ID
from src.model_connections.storage import ModelConnectionRepository
from src.model_connections.vault import FernetCredentialVault
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture
def bound_call(tmp_path, monkeypatch):
    database = migrated_webui_database(tmp_path / "deepseek-bound.db")
    owner = seed_execution_owner(database, "deepseek-owner")
    store = WebUIStore(str(database))
    repository = ModelConnectionRepository(str(database))
    calls = []
    connection = None
    monkeypatch.setattr(settings, "qwen_enable_thinking", None)

    async def invoke(*, preset="deepseek", model="deepseek-flash", protocol="openai_chat_completions",
                     thinking=False, sequence=None, max_tokens=None, revoked=False, base_url=None, truncated=False):
        nonlocal connection

        async def send(request):
            calls.append(json.loads(request.content))
            if revoked:
                repository.set_platform_enabled(connection["connection_id"], enabled=False)
            if protocol == "openai_responses":
                payload = {"status": "completed", "output_text": "合成结果",
                           "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}}
            elif protocol == "anthropic_messages":
                payload = {"content": [{"type": "text", "text": "合成结果"}], "stop_reason": "end_turn",
                           "usage": {"input_tokens": 2, "output_tokens": 1}}
            elif protocol == "gemini_generate_content":
                payload = {"candidates": [{"content": {"parts": [{"text": "合成结果"}]}, "finishReason": "STOP"}],
                           "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1, "totalTokenCount": 3}}
            else:
                payload = {"choices": [{"message": {"content": "合成结果"}, "finish_reason": "length" if truncated else "stop"}],
                           "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}}
            return httpx.Response(200, json=payload)

        broker = ConnectionBroker(repository=repository, vault=FernetCredentialVault.generate(),
            transport=httpx.MockTransport(send), resolver=lambda _: ["8.8.8.8"])
        connection = repository.create_managed(created_by=owner.owner_user_id, display_name="合成项目抽取连接",
            base_url=base_url or (PRESETS_BY_ID[preset].base_url if preset in PRESETS_BY_ID else "https://provider.example/v1"), model=model, api_format=protocol,
            locality="public_external", ciphertext=None, key_hint="", verified_at="2026-01-01",
            preset_id=preset)
        monkeypatch.setattr(conductor, "get_default_broker", lambda: broker)
        with execution_context(owner), execution_validation(store.require_account_authorization):
            binding = broker.freeze_connection(owner.owner_user_id, connection["connection_id"])
            with conductor.conductor_connection(owner_id=owner.owner_user_id, connection_id=binding.connection_id,
                connection_version=binding.connection_version, model=binding.model,
                task_id="deepseek-bound-task", run_id="deepseek-bound-run"):
                replies = []
                for value in sequence if sequence is not None else [thinking]:
                    options = {"thinking": value} if value is not None else {}
                    if max_tokens is not None:
                        options["max_tokens"] = max_tokens
                    replies.append(await achat([{"role": "system", "content": "只返回有原文依据的项目字段"},
                        {"role": "user", "content": "合成项目材料"}], **options))
                return replies

    return invoke, calls, database


@pytest.mark.parametrize("model", ["deepseek-flash", "deepseek-v4-pro"])
def test_default_bound_call_uses_model_output_capacity_without_disabling_thinking(bound_call, model):
    invoke, calls, _ = bound_call
    assert asyncio.run(invoke(model=model, thinking=None)) == ["合成结果"]
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == 393216
    assert "thinking" not in calls[0] and "reasoning" not in calls[0]


@pytest.mark.parametrize("model,protocol,field", [
    ("deepseek-flash", "openai_chat_completions", "max_tokens"),
    ("unknown-anthropic", "anthropic_messages", "max_tokens"),
    ("unknown-responses", "openai_responses", "max_output_tokens"),
    ("unknown-gemini", "gemini_generate_content", "maxOutputTokens"),
])
def test_unknown_capacity_keeps_original_output_compatibility(bound_call, model, protocol, field):
    invoke, calls, _ = bound_call
    assert asyncio.run(invoke(preset=None, model=model, protocol=protocol, thinking=None)) == ["合成结果"]
    body = calls[0]["generationConfig"] if protocol == "gemini_generate_content" else calls[0]
    assert body[field] == 8192


@pytest.mark.parametrize("protocol,field", [("openai_chat_completions", "max_tokens"), ("openai_responses", "max_output_tokens")])
def test_custom_endpoint_with_official_preset_and_model_keeps_compatibility_budget(bound_call, protocol, field):
    invoke, calls, _ = bound_call
    assert asyncio.run(invoke(base_url="https://api.deepseek.com.other.example", protocol=protocol, thinking=None)) == ["合成结果"]
    assert calls[0][field] == 8192


def test_real_output_truncation_still_stops_one_call_and_revokes_grant(bound_call):
    from src.model_connections.text_protocol import ModelOutputTruncatedError
    invoke, calls, database = bound_call
    with pytest.raises(ModelOutputTruncatedError):
        asyncio.run(invoke(thinking=None, truncated=True))
    assert len(calls) == 1
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT count(*) FROM model_connection_grants WHERE revoked_at IS NULL").fetchone()[0] == 0


@pytest.mark.parametrize("model", ["deepseek-flash", "deepseek-v4-pro"])
@pytest.mark.parametrize("protocol", ["openai_chat_completions", "openai_responses"])
@pytest.mark.parametrize("max_tokens", [None, 4096])
def test_explicit_non_thinking_preserves_operation_budget_and_default_protocol(bound_call, model, protocol, max_tokens):
    invoke, calls, database = bound_call
    assert asyncio.run(invoke(model=model, protocol=protocol, max_tokens=max_tokens)) == ["合成结果"]
    assert len(calls) == 1
    if protocol == "openai_chat_completions":
        assert calls[0]["thinking"] == {"type": "disabled"}
        assert "reasoning" not in calls[0]
        assert calls[0]["max_tokens"] == (max_tokens or 393216)
    else:
        assert calls[0]["reasoning"] == {"effort": "none"}
        assert "thinking" not in calls[0]
        if max_tokens is None:
            assert "max_output_tokens" not in calls[0]
        else:
            assert calls[0]["max_output_tokens"] == max_tokens
    assert "chat_template_kwargs" not in calls[0]
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT status,total_tokens FROM model_provider_usage").fetchall() == [("recorded", 3)]
        assert conn.execute("SELECT revoke_reason FROM model_connection_grants").fetchall() == [("conductor_call_finished",)]


@pytest.mark.parametrize("preset,model,protocol,thinking", [
    ("deepseek", "deepseek-flash", "openai_chat_completions", None),
    ("deepseek", "deepseek-flash", "openai_responses", None),
    (None, "deepseek-flash", "openai_chat_completions", False),
    ("openai", "deepseek-flash", "openai_responses", False),
    ("deepseek", "deepseek-chat", "openai_chat_completions", False),
    ("deepseek", "gpt-5.6-sol", "openai_responses", False),
    ("qwen", "qwen3.8-max", "openai_responses", False),
    ("deepseek", "deepseek-flash", "anthropic_messages", False),
])
def test_default_custom_other_provider_model_and_protocol_keep_original_body(bound_call, preset, model, protocol, thinking):
    invoke, calls, _ = bound_call
    assert asyncio.run(invoke(preset=preset, model=model, protocol=protocol, thinking=thinking)) == ["合成结果"]
    assert len(calls) == 1
    assert "thinking" not in calls[0] and "reasoning" not in calls[0]
    assert "enable_thinking" not in calls[0] and "chat_template_kwargs" not in calls[0]


@pytest.mark.parametrize("protocol", ["openai_chat_completions", "openai_responses"])
def test_non_thinking_does_not_leak_to_next_call_of_same_bound_task(bound_call, protocol):
    invoke, calls, database = bound_call
    assert asyncio.run(invoke(protocol=protocol, sequence=[False, None])) == ["合成结果", "合成结果"]
    key = "thinking" if protocol == "openai_chat_completions" else "reasoning"
    assert key in calls[0] and key not in calls[1]
    assert len(calls) == 2
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT count(*) FROM model_connection_grants WHERE revoked_at IS NULL").fetchone()[0] == 0


def test_non_thinking_never_bypasses_revoked_connection(bound_call):
    invoke, calls, database = bound_call
    with pytest.raises(ValueError, match="未切换模型或自动重发"):
        asyncio.run(invoke(revoked=True))
    assert len(calls) == 1
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT status FROM model_provider_usage").fetchall() == [("unknown",)]
        assert conn.execute("SELECT count(*) FROM model_connection_grants WHERE revoked_at IS NULL").fetchone()[0] == 0
