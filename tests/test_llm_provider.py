# -*- coding: utf-8 -*-
import asyncio
from types import SimpleNamespace

import pytest

from src.config.settings import settings
from src.config.user_ctx import effective, user_overrides_context
from src.llm.provider import MultiModelProvider
from tests.database_migration_helpers import migrated_webui_database


def test_resolved_model_uses_request_user_key_without_leaking_context(monkeypatch, tmp_path):
    from src.model_connections import ConnectionBroker
    from src.model_connections.storage import ModelConnectionRepository
    from src.model_connections.vault import FernetCredentialVault
    import src.model_connections as connections
    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "models.db"))),
        vault=FernetCredentialVault.generate(),
    )
    monkeypatch.setattr(connections, "get_default_broker", lambda: broker)
    monkeypatch.setattr(settings, "deepseek_api_key", "global-test-key")
    provider = object.__new__(MultiModelProvider)
    provider._init_once()

    assert provider.resolve_model("deepseek").api_key == "global-test-key"
    with user_overrides_context({"deepseek_api_key": "user-test-key"}):
        assert "deepseek" in provider.available_providers()
        assert provider.resolve_model("deepseek").api_key == "user-test-key"
    assert provider.resolve_model("deepseek").api_key == "global-test-key"


def test_user_document_model_override_is_scoped():
    global_value = effective("document_extraction_model")
    with user_overrides_context({
        "document_extraction_model": "qwen::qwen-plus",
    }):
        assert effective("document_extraction_model") == "qwen::qwen-plus"
    assert effective("document_extraction_model") == global_value


@pytest.mark.parametrize("name,model,endpoint,expected", [
    ("deepseek", "deepseek-flash", "https://api.deepseek.com/v1", True),
    ("deepseek", "deepseek-v4-pro", "https://api.deepseek.com", True),
    ("local", "deepseek-flash", "http://127.0.0.1:9000/v1", False),
    ("deepseek", "deepseek-flash", "https://custom.example/v1", False),
    ("deepseek", "custom-model", "https://api.deepseek.com", False),
    ("qwen", "qwen3.8-max", "https://dashscope.aliyuncs.com", False),
])
def test_operation_thinking_does_not_mutate_cached_client_or_other_providers(monkeypatch, name, model, endpoint, expected):
    provider = object.__new__(MultiModelProvider)
    calls = []

    async def invoke(messages, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(content="合成结果")

    client = SimpleNamespace(ainvoke=invoke)
    connection = SimpleNamespace(provider=name, model=model, base_url=endpoint, extra_body={"existing": 1})
    monkeypatch.setattr(provider, "get_chat_model", lambda *args, **kwargs: client)
    monkeypatch.setattr(provider, "resolve_model", lambda *args, **kwargs: connection)
    messages = [{"role": "user", "content": "合成项目"}]
    assert asyncio.run(provider.achat(messages, name, model=model, thinking=False)) == "合成结果"
    assert asyncio.run(provider.achat(messages, name, model=model)) == "合成结果"
    assert calls == ([{"extra_body": {"existing": 1, "thinking": {"type": "disabled"}}}, {}] if expected else [{}, {}])
    assert connection.extra_body == {"existing": 1}
