# -*- coding: utf-8 -*-
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import get_current_user
from src.api.routes import models
from src.config.user_ctx import get_user_override


class _Store:
    def config_all(self, user_id: str):
        assert user_id == "user-a"
        return {
            "document_extraction_model": "qwen::qwen-plus",
            "qwen_api_key": "user-test-key",
        }


def test_models_route_returns_user_document_default_and_restores_context(monkeypatch):
    monkeypatch.setattr(models, "get_store", lambda: _Store())
    monkeypatch.setattr(
        models,
        "list_models",
        lambda: {
            "local": ["Qwen3.6-35B-A3B"],
            "qwen": ["qwen-plus"],
        },
    )
    monkeypatch.setattr(models, "available_providers", lambda: ["local", "qwen"])
    app = FastAPI()
    app.include_router(models.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "user-a"}

    response = TestClient(app).get("/api/models")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["document_default"] == {
        "provider": "qwen",
        "model": "qwen-plus",
        "label": "qwen · qwen-plus",
    }
    assert body["document_default_source"] == "user"
    assert body["pi_runtime_enabled"] is True
    assert get_user_override("qwen_api_key") is None
