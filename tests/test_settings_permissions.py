from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import get_current_user
from src.api.routes import config_routes, settings_routes
from src.api.store import WebUIStore


def _client(
    *,
    role: str | None,
    user_id: str = "user-a",
    store: WebUIStore | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(config_routes.router)
    app.include_router(settings_routes.router)
    if role is not None:
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": user_id,
            "role": role,
        }
    if store is not None:
        app.dependency_overrides[settings_routes.get_settings_store] = lambda: store
    return TestClient(app)


def test_model_catalog_requires_authentication():
    response = _client(role=None).get("/api/config/models")

    assert response.status_code == 401


def test_regular_user_model_catalog_hides_internal_local_urls(monkeypatch):
    monkeypatch.setattr(
        "src.llm.provider.LOCAL_MODELS",
        {"Office-Qwen": "http://192.168.1.20:6012/v1"},
    )

    response = _client(role="user").get("/api/config/models")

    assert response.status_code == 200
    assert "local_urls" not in response.json()
    assert "192.168.1.20" not in response.text


@pytest.mark.parametrize("role", ["admin", "super_admin"])
def test_manager_model_catalog_includes_internal_local_urls(monkeypatch, role):
    monkeypatch.setattr(
        "src.llm.provider.LOCAL_MODELS",
        {"Office-Qwen": "http://192.168.1.20:6012/v1"},
    )

    response = _client(role=role).get("/api/config/models")

    assert response.status_code == 200
    assert response.json()["local_urls"] == {
        "Office-Qwen": "http://192.168.1.20:6012/v1"
    }


def test_regular_user_cannot_run_platform_selfcheck():
    response = _client(role="user").post(
        "/api/settings/selfcheck",
        json={"target": "slack"},
    )

    assert response.status_code == 403


@pytest.mark.parametrize("role", ["admin", "super_admin"])
def test_manager_can_reach_platform_selfcheck(role):
    response = _client(role=role).post(
        "/api/settings/selfcheck",
        json={"target": "unsupported-test-target"},
    )

    assert response.status_code == 400


def test_model_connection_guide_state_is_persisted_per_user(tmp_path):
    store = WebUIStore(str(tmp_path / "settings.db"))
    user_a = _client(role="user", user_id="user-a", store=store)
    user_b = _client(role="user", user_id="user-b", store=store)

    initial = user_a.get("/api/settings/onboarding/model-connections")
    completed = user_a.put(
        "/api/settings/onboarding/model-connections",
        json={"state": "completed"},
    )
    reloaded = user_a.get("/api/settings/onboarding/model-connections")
    isolated = user_b.get("/api/settings/onboarding/model-connections")

    assert initial.json() == {"state": "not_started"}
    assert completed.json() == {"state": "completed"}
    assert reloaded.json() == {"state": "completed"}
    assert isolated.json() == {"state": "not_started"}
