# -*- coding: utf-8 -*-
from src.config.settings import settings
from src.config.user_ctx import effective, user_overrides_context
from src.llm.provider import MultiModelProvider


def test_resolved_model_uses_request_user_key_without_leaking_context(monkeypatch):
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
