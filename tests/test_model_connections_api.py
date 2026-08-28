from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import gzip
import json
import sqlite3
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import get_current_user
import src.api.auth as auth_mod
from src.api.routes import model_connections
from src.model_connections import ConnectionBroker, ConnectionError, GrantError
from src.model_connections.storage import ModelConnectionRepository
from src.model_connections.vault import FernetCredentialVault
from src.config.settings import settings
from tests.database_migration_helpers import migrated_webui_database


def _connection_version(
    broker: ConnectionBroker,
    connection: dict[str, object],
    *,
    owner_user_id: str = "user-a",
) -> str:
    return broker.freeze_connection(
        owner_user_id,
        str(connection["connection_id"]),
    ).connection_version


def _assert_pinned_provider_request(
    seen: dict[str, object],
    expected_url: str,
) -> None:
    expected = urlsplit(expected_url)
    actual = urlsplit(str(seen["url"]))
    assert actual.scheme == expected.scheme
    assert actual.hostname == "8.8.8.8"
    assert actual.path == expected.path
    assert actual.query == expected.query
    assert seen["host"] == expected.hostname
    assert seen["sni_hostname"] == expected.hostname


def _client(
    user_id: str = "user-a",
    role: str = "user",
    broker: ConnectionBroker | None = None,
) -> tuple[FastAPI, TestClient]:
    test_app = FastAPI()
    test_app.include_router(model_connections.router)
    test_app.dependency_overrides[get_current_user] = lambda: {
        "user_id": user_id,
        "role": role,
    }
    if broker is not None:
        test_app.dependency_overrides[
            model_connections.get_connection_broker
        ] = lambda: broker
    return test_app, TestClient(test_app)


def test_authenticated_user_sees_seven_small_provider_presets_without_internal_endpoint():
    _, client = _client()
    response = client.get("/api/model-connections/presets")

    assert response.status_code == 200
    payload = response.json()
    assert [item["preset_id"] for item in payload["items"]] == [
        "deepseek",
        "qwen",
        "openai",
        "anthropic",
        "gemini",
        "kimi",
        "zhipu",
    ]
    assert all(2 <= len(item["models"]) <= 4 for item in payload["items"])
    assert all(item["recommended_model"] in item["models"] for item in payload["items"])
    assert all(
        len(item["model_catalog"]) == len(item["models"])
        for item in payload["items"]
    )
    deepseek = next(
        item for item in payload["items"] if item["preset_id"] == "deepseek"
    )
    flash = next(
        item
        for item in deepseek["model_catalog"]
        if item["model_id"] == "deepseek-v4-flash"
    )
    assert flash["display_name"] == "DeepSeek V4 Flash（0731 正式版）"
    assert "base_url" not in response.text
    assert "api_format" not in response.text
    assert "api_key" not in response.text


def test_user_sets_isolated_default_connection_and_model(tmp_path):
    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"choices": [{"message": {"content": "OK"}}]},
            )
        ),
        resolver=lambda _host: ["8.8.8.8"],
    )
    connection = asyncio.run(
        broker.create_personal(
            owner_user_id="user-a",
            display_name="我的 DeepSeek",
            preset_id="deepseek",
            api_key="personal-secret-1234",
        )
    )
    _, owner = _client(user_id="user-a", broker=broker)
    _, other = _client(user_id="user-b", broker=broker)
    saved = owner.put(
        "/api/model-connections/preferences/default",
        json={
            "connection_id": connection["connection_id"],
            "model_id": "deepseek-v4-pro",
        },
    )

    assert saved.status_code == 200
    assert saved.json()["available"] is True
    assert owner.get("/api/model-connections/preferences/default").json()[
        "preference"
    ]["model_id"] == "deepseek-v4-pro"
    assert other.get("/api/model-connections/preferences/default").json() == {
        "preference": None
    }


def test_user_configures_and_lists_personal_preset_connection(tmp_path):
    seen: dict[str, object] = {}

    def provider(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["host"] = request.headers.get("host")
        seen["sni_hostname"] = request.extensions.get("sni_hostname")
        seen["authorization"] = request.headers.get("authorization")
        seen["payload"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "OK"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )
    _, client = _client(broker=broker)

    saved = client.put(
        "/api/model-connections/presets/deepseek",
        json={"api_key": "sk-personal-secret-1234", "model": "deepseek-v4-pro"},
    )
    listed = client.get("/api/model-connections")

    assert saved.status_code == 200
    assert saved.json()["owner_scope"] == "user_personal"
    assert saved.json()["preset_id"] == "deepseek"
    assert saved.json()["preset_version"] == "2026-08-02.1"
    assert saved.json()["display_name"] == "DeepSeek"
    assert saved.json()["model"] == "deepseek-v4-pro"
    assert saved.json()["default_model"] == "deepseek-v4-pro"
    assert saved.json()["available_model_count"] == 1
    assert [item["model_id"] for item in saved.json()["models"]] == [
        "deepseek-v4-pro"
    ]
    assert saved.json()["api_format"] == "openai_chat_completions"
    assert saved.json()["locality"] == "public_external"
    assert saved.json()["status"] == "verified"
    assert saved.json()["key_hint"] == "1234"
    assert listed.status_code == 200
    assert listed.json()["items"] == [saved.json()]
    assert seen["url"] == "https://8.8.8.8/chat/completions"
    assert seen["host"] == "api.deepseek.com"
    assert seen["sni_hostname"] == "api.deepseek.com"
    assert seen["authorization"] == "Bearer sk-personal-secret-1234"
    assert "sk-personal-secret-1234" not in saved.text
    assert "sk-personal-secret-1234" not in listed.text


def test_user_creates_two_named_personal_connections_for_same_provider(tmp_path):
    verified_keys: list[str] = []

    def provider(request: httpx.Request) -> httpx.Response:
        verified_keys.append(request.headers["authorization"])
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "OK"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )
    _, owner_client = _client(broker=broker)

    primary = owner_client.post(
        "/api/model-connections/presets/deepseek",
        json={
            "display_name": "DeepSeek 日常",
            "api_key": "sk-personal-primary-1111",
            "model": "deepseek-v4-pro",
        },
    )
    backup = owner_client.post(
        "/api/model-connections/presets/deepseek",
        json={
            "display_name": "DeepSeek 备用",
            "api_key": "sk-personal-backup-2222",
            "model": "deepseek-v4-pro",
        },
    )
    listed = owner_client.get("/api/model-connections")

    assert primary.status_code == 201
    assert backup.status_code == 201
    assert primary.json()["connection_id"] != backup.json()["connection_id"]
    assert {
        (item["display_name"], item["key_hint"])
        for item in listed.json()["items"]
    } == {
        ("DeepSeek 日常", "1111"),
        ("DeepSeek 备用", "2222"),
    }
    assert verified_keys == [
        "Bearer sk-personal-primary-1111",
        "Bearer sk-personal-primary-1111",
        "Bearer sk-personal-backup-2222",
        "Bearer sk-personal-backup-2222",
    ]

    legacy = owner_client.put(
        "/api/model-connections/presets/deepseek",
        json={
            "api_key": "sk-personal-legacy-3333",
            "model": "deepseek-v4-pro",
        },
    )
    after_legacy = owner_client.get("/api/model-connections").json()["items"]
    assert legacy.status_code == 200
    assert {item["display_name"] for item in after_legacy} == {
        "DeepSeek 日常",
        "DeepSeek 备用",
        "DeepSeek",
    }

    _, other_client = _client(user_id="user-b", broker=broker)
    assert other_client.get("/api/model-connections").json()["items"] == []


def test_personal_connection_keeps_independent_model_results_and_available_default(
    tmp_path,
):
    db_path = tmp_path / "webui.db"

    def provider(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        if model == "deepseek-v4-pro":
            return httpx.Response(
                403,
                json={"error": {"message": "SENSITIVE_NO_MODEL_PERMISSION"}},
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "SENSITIVE_PROVIDER_RESPONSE",
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 1,
                    "total_tokens": 4,
                },
            },
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(db_path))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )
    _, client = _client(broker=broker)

    response = client.post(
        "/api/model-connections/presets/deepseek",
        json={
            "display_name": "DeepSeek 主连接",
            "api_key": "sk-personal-multi-model-1234",
            "model": "deepseek-v4-flash",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["model"] == "deepseek-v4-flash"
    assert payload["default_model"] == "deepseek-v4-flash"
    assert payload["available_model_count"] == 1
    assert payload["models"] == [
        {
            "model_id": "deepseek-v4-flash",
            "display_name": "DeepSeek V4 Flash（0731 正式版）",
            "catalog_role": "balanced",
            "catalog_version": "2026-08-02.1",
            "status": "available",
            "enabled": True,
            "is_default": True,
            "verified_at": payload["models"][0]["verified_at"],
            "error_code": None,
            "usage_status": "reported",
        },
        {
            "model_id": "deepseek-v4-pro",
            "display_name": "DeepSeek V4 Pro",
            "catalog_role": "quality",
            "catalog_version": "2026-08-02.1",
            "status": "model_access_denied",
            "enabled": False,
            "is_default": False,
            "verified_at": payload["models"][1]["verified_at"],
            "error_code": "model_access_denied",
            "usage_status": "unknown",
        },
    ]
    raw_db = db_path.read_bytes()
    assert b"SENSITIVE_PROVIDER_RESPONSE" not in raw_db
    assert b"SENSITIVE_NO_MODEL_PERMISSION" not in raw_db


def test_all_recommended_models_failing_does_not_create_connection_or_secret(
    tmp_path,
):
    db_path = tmp_path / "webui.db"

    def provider(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": "SENSITIVE_INVALID_CREDENTIAL"}},
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(db_path))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )
    _, client = _client(broker=broker)

    response = client.post(
        "/api/model-connections/presets/deepseek",
        json={
            "display_name": "不会保存的连接",
            "api_key": "sk-invalid-all-models-1234",
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "所有推荐模型验证失败，连接未保存",
        "model_results": [
            {
                "model_id": "deepseek-v4-flash",
                "display_name": "DeepSeek V4 Flash（0731 正式版）",
                "catalog_role": "balanced",
                "catalog_version": "2026-08-02.1",
                "status": "credentials_invalid",
                "enabled": False,
                "verified_at": response.json()["model_results"][0]["verified_at"],
                "error_code": "credentials_invalid",
                "usage_status": "unknown",
            },
            {
                "model_id": "deepseek-v4-pro",
                "display_name": "DeepSeek V4 Pro",
                "catalog_role": "quality",
                "catalog_version": "2026-08-02.1",
                "status": "credentials_invalid",
                "enabled": False,
                "verified_at": response.json()["model_results"][1]["verified_at"],
                "error_code": "credentials_invalid",
                "usage_status": "unknown",
            },
        ],
    }
    assert client.get("/api/model-connections").json()["items"] == []
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM model_connections"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM model_connection_secrets"
        ).fetchone()[0] == 0
    assert b"SENSITIVE_INVALID_CREDENTIAL" not in db_path.read_bytes()


@pytest.mark.parametrize(
    ("failure_kind", "expected_status"),
    [
        ("forbidden", "model_access_denied"),
        ("rate_limit", "rate_limited"),
        ("server_error", "network_unreachable"),
        ("network_error", "network_unreachable"),
        ("wrong_protocol", "protocol_incompatible"),
    ],
)
def test_each_model_validation_failure_has_stable_product_status(
    tmp_path,
    failure_kind,
    expected_status,
):
    def provider(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        if model == "deepseek-v4-flash":
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "OK"}}]},
            )
        if failure_kind == "forbidden":
            return httpx.Response(403, json={"error": {"message": "private"}})
        if failure_kind == "rate_limit":
            return httpx.Response(429, json={"error": {"message": "private"}})
        if failure_kind == "server_error":
            return httpx.Response(503, json={"error": {"message": "private"}})
        if failure_kind == "network_error":
            raise httpx.ConnectError("private", request=request)
        return httpx.Response(200, json={"unexpected": "private"})

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )
    _, client = _client(broker=broker)

    response = client.post(
        "/api/model-connections/presets/deepseek",
        json={
            "display_name": f"DeepSeek {failure_kind}",
            "api_key": "sk-classification-secret-1234",
        },
    )

    assert response.status_code == 201
    failed = next(
        item
        for item in response.json()["models"]
        if item["model_id"] == "deepseek-v4-pro"
    )
    assert failed["status"] == expected_status
    assert failed["error_code"] == expected_status
    assert failed["enabled"] is False


def test_user_retries_failed_model_changes_default_and_controls_model_state(tmp_path):
    requests: list[str] = []
    pro_available = False

    def provider(request: httpx.Request) -> httpx.Response:
        nonlocal pro_available
        model = json.loads(request.content)["model"]
        requests.append(model)
        if model == "deepseek-v4-pro" and not pro_available:
            return httpx.Response(403, json={"error": {"message": "no access"}})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "OK"}}],
            },
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )
    _, client = _client(broker=broker)
    created = client.post(
        "/api/model-connections/presets/deepseek",
        json={
            "display_name": "DeepSeek 多模型",
            "api_key": "sk-retry-model-secret-1234",
            "model": "deepseek-v4-flash",
        },
    ).json()
    connection_id = created["connection_id"]
    frozen_before_default_change = broker.freeze_connection(
        "user-a",
        connection_id,
    )

    pro_available = True
    retried = client.post(
        f"/api/model-connections/{connection_id}/models/retry",
        json={"model_ids": ["deepseek-v4-pro"]},
    )
    changed_default = client.put(
        f"/api/model-connections/{connection_id}/default-model",
        json={"model": "deepseek-v4-pro"},
    )
    with pytest.raises(GrantError, match="版本已变化"):
        broker.issue_grant(
            owner_user_id="user-a",
            connection_id=connection_id,
            connection_version=frozen_before_default_change.connection_version,
            task_id="task-old-default",
            revision=1,
            run_id="run-old-default",
            purpose="agent_inference",
        )
    disabled_default = client.patch(
        f"/api/model-connections/{connection_id}/models/deepseek-v4-pro",
        json={"enabled": False},
    )
    restored_default = client.put(
        f"/api/model-connections/{connection_id}/default-model",
        json={"model": "deepseek-v4-flash"},
    )
    reenabled = client.patch(
        f"/api/model-connections/{connection_id}/models/deepseek-v4-pro",
        json={"enabled": True},
    )

    assert requests == [
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        "deepseek-v4-pro",
    ]
    assert retried.status_code == 200
    assert retried.json()["available_model_count"] == 2
    assert changed_default.status_code == 200
    assert changed_default.json()["default_model"] == "deepseek-v4-pro"
    assert disabled_default.status_code == 200
    assert disabled_default.json()["status"] == "needs_default_model"
    assert disabled_default.json()["default_model"] is None
    assert restored_default.status_code == 200
    assert restored_default.json()["status"] == "verified"
    assert restored_default.json()["default_model"] == "deepseek-v4-flash"
    pro = next(
        item
        for item in restored_default.json()["models"]
        if item["model_id"] == "deepseek-v4-pro"
    )
    assert pro["status"] == "disabled"
    assert pro["enabled"] is False
    assert reenabled.status_code == 200
    assert reenabled.json()["available_model_count"] == 2
    reenabled_pro = next(
        item
        for item in reenabled.json()["models"]
        if item["model_id"] == "deepseek-v4-pro"
    )
    assert reenabled_pro["status"] == "available"
    assert reenabled_pro["enabled"] is True


def test_admin_publishes_provider_preset_with_required_key(tmp_path):
    def provider(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://8.8.8.8/chat/completions"
        assert request.headers["host"] == "api.deepseek.com"
        assert request.extensions["sni_hostname"] == "api.deepseek.com"
        assert request.headers["authorization"] == "Bearer platform-secret-2468"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]},
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )
    _, user_client = _client(role="user", broker=broker)
    denied = user_client.post(
        "/api/model-connections/managed/presets/deepseek",
        json={
            "display_name": "平台 DeepSeek",
            "model": "deepseek-v4-pro",
            "api_key": "platform-secret-2468",
        },
    )
    _, admin_client = _client(
        user_id="admin-a",
        role="admin",
        broker=broker,
    )
    missing_key = admin_client.post(
        "/api/model-connections/managed/presets/deepseek",
        json={
            "display_name": "平台 DeepSeek",
            "model": "deepseek-v4-pro",
            "api_key": "",
        },
    )
    saved = admin_client.post(
        "/api/model-connections/managed/presets/deepseek",
        json={
            "display_name": "平台 DeepSeek",
            "model": "deepseek-v4-pro",
            "api_key": "platform-secret-2468",
        },
    )
    _, member_client = _client(
        user_id="member-a",
        role="user",
        broker=broker,
    )
    listed = member_client.get("/api/model-connections")

    assert denied.status_code == 403
    assert missing_key.status_code == 400
    assert saved.status_code == 201
    assert saved.json()["owner_scope"] == "platform_shared"
    assert saved.json()["preset_id"] == "deepseek"
    assert saved.json()["preset_version"] == "2026-08-02.1"
    assert saved.json()["display_name"] == "平台 DeepSeek"
    assert saved.json()["model"] == "deepseek-v4-pro"
    assert saved.json()["key_hint"] == "2468"
    assert listed.json()["items"][0]["key_hint"] == ""
    assert "platform-secret-2468" not in saved.text
    assert "platform-secret-2468" not in listed.text


@pytest.mark.parametrize("manager_role", ["admin", "super_admin"])
def test_manager_publishes_multiple_platform_connections_with_partial_models(
    tmp_path,
    manager_role,
):
    def provider(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["model"] == "deepseek-v4-pro":
            return httpx.Response(403, json={"error": {"message": "not entitled"}})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]},
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )
    _, manager = _client(
        user_id=f"{manager_role}-a",
        role=manager_role,
        broker=broker,
    )
    saved = [
        manager.post(
            "/api/model-connections/managed/presets/deepseek",
            json={
                "display_name": name,
                "model": "deepseek-v4-pro",
                "api_key": key,
            },
        )
        for name, key in [
            ("平台 DeepSeek 主用", "platform-primary-1111"),
            ("平台 DeepSeek 备用", "platform-backup-2222"),
        ]
    ]
    _, member = _client(user_id="member-a", role="user", broker=broker)
    member_items = member.get("/api/model-connections").json()["items"]

    assert [response.status_code for response in saved] == [201, 201]
    assert saved[0].json()["connection_id"] != saved[1].json()["connection_id"]
    assert {item["display_name"] for item in member_items} == {
        "平台 DeepSeek 主用",
        "平台 DeepSeek 备用",
    }
    for item in member_items:
        assert item["owner_scope"] == "platform_shared"
        assert item["key_hint"] == ""
        assert item["default_model"] == "deepseek-v4-flash"
        assert item["available_model_count"] == 1
        assert [
            (model["model_id"], model["status"], model["enabled"])
            for model in item["models"]
        ] == [
            ("deepseek-v4-flash", "available", True),
            ("deepseek-v4-pro", "model_access_denied", False),
        ]
    assert "platform-primary-1111" not in member.get("/api/model-connections").text
    assert "platform-backup-2222" not in member.get("/api/model-connections").text


@pytest.mark.asyncio
async def test_only_manager_controls_platform_models_and_disabling_revokes_grant(
    tmp_path,
):
    def provider(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]},
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )
    _, admin = _client(user_id="admin-a", role="admin", broker=broker)
    _, member = _client(user_id="member-a", role="user", broker=broker)
    saved = admin.post(
        "/api/model-connections/managed/presets/deepseek",
        json={
            "display_name": "平台 DeepSeek",
            "model": "deepseek-v4-flash",
            "api_key": "platform-secret-2468",
        },
    ).json()
    connection_id = saved["connection_id"]

    denied_retry = member.post(
        f"/api/model-connections/{connection_id}/models/retry",
        json={"model_ids": ["deepseek-v4-pro"]},
    )
    denied_default = member.put(
        f"/api/model-connections/{connection_id}/default-model",
        json={"model": "deepseek-v4-pro"},
    )
    denied_model_state = member.patch(
        f"/api/model-connections/{connection_id}/models/deepseek-v4-pro",
        json={"enabled": False},
    )
    denied_connection_state = member.patch(
        f"/api/model-connections/{connection_id}",
        json={"enabled": False},
    )
    binding = broker.freeze_connection("member-a", connection_id)
    grant = broker.issue_grant(
        owner_user_id="member-a",
        connection_id=connection_id,
        connection_version=binding.connection_version,
        task_id="task-platform",
        revision=1,
        run_id="run-platform",
        purpose="agent_inference",
    )

    disabled = admin.patch(
        f"/api/model-connections/{connection_id}",
        json={"enabled": False},
    )
    member_after_disable = member.get("/api/model-connections").json()["items"]
    manager_after_disable = admin.get("/api/model-connections").json()["items"]

    assert denied_retry.status_code == 404
    assert denied_default.status_code == 404
    assert denied_model_state.status_code == 404
    assert denied_connection_state.status_code == 403
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert member_after_disable == []
    assert manager_after_disable[0]["connection_id"] == connection_id
    assert manager_after_disable[0]["status"] == "disabled"
    with pytest.raises(GrantError, match="已撤销"):
        await broker.relay(
            grant_token=grant.token,
            protocol_path="/chat/completions",
            method="POST",
            headers={"content-type": "application/json"},
            body=json.dumps({"model": grant.model, "messages": []}).encode(),
        )

    reenabled = admin.patch(
        f"/api/model-connections/{connection_id}",
        json={"enabled": True},
    )
    assert reenabled.status_code == 200
    assert reenabled.json()["status"] == "verified"
    assert member.get("/api/model-connections").json()["items"][0]["connection_id"] == connection_id
    next_binding = broker.freeze_connection("member-a", connection_id)
    next_grant = broker.issue_grant(
        owner_user_id="member-a",
        connection_id=connection_id,
        connection_version=next_binding.connection_version,
        model_id=next_binding.model,
        task_id="task-platform-delete",
        revision=1,
        run_id="run-platform-delete",
        purpose="agent_inference",
    )
    assert admin.delete(f"/api/model-connections/{connection_id}").status_code == 200
    with pytest.raises(GrantError, match="无效"):
        await broker.relay(
            grant_token=next_grant.token,
            protocol_path="/chat/completions",
            method="POST",
            headers={"content-type": "application/json"},
            body=json.dumps({"model": next_grant.model, "messages": []}).encode(),
        )


@pytest.mark.parametrize("manager_role", ["admin", "super_admin"])
def test_only_admin_roles_can_publish_an_exact_lan_model_connection(
    tmp_path,
    manager_role,
):
    def provider(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == (
            "http://192.168.1.20:6012/v1/chat/completions"
        )
        assert request.headers["authorization"] == "Bearer lan-secret-5678"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]},
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
    )
    payload = {
        "display_name": "办公室 Qwen",
        "base_url": "http://192.168.1.20:6012/v1",
        "api_format": "openai_chat_completions",
        "model": "Qwen3.6-35B-A3B",
        "api_key": "lan-secret-5678",  # gitleaks:allow -- 测试假值
    }

    _, user_client = _client(role="user", broker=broker)
    denied = user_client.post("/api/model-connections/managed", json=payload)

    _, admin_client = _client(
        user_id="admin-a",
        role=manager_role,
        broker=broker,
    )
    saved = admin_client.post("/api/model-connections/managed", json=payload)

    _, member_client = _client(user_id="user-b", role="user", broker=broker)
    listed = member_client.get("/api/model-connections")
    manager_listed = admin_client.get("/api/model-connections")
    member_delete = member_client.delete(
        f"/api/model-connections/{saved.json()['connection_id']}"
    )
    admin_delete = admin_client.delete(
        f"/api/model-connections/{saved.json()['connection_id']}"
    )
    after_delete = member_client.get("/api/model-connections")

    assert denied.status_code == 403
    assert saved.status_code == 200
    assert saved.json()["owner_scope"] == "platform_shared"
    assert saved.json()["locality"] == "managed_private"
    assert saved.json()["display_name"] == "办公室 Qwen"
    assert saved.json()["key_hint"] == "5678"
    member_item = listed.json()["items"][0]
    assert member_item["connection_id"] == saved.json()["connection_id"]
    assert member_item["display_name"] == "办公室 Qwen"
    assert member_item["key_hint"] == ""
    assert manager_listed.json()["items"][0]["key_hint"] == "5678"
    assert member_delete.status_code == 404
    assert admin_delete.status_code == 200
    assert after_delete.json()["items"] == []
    assert "192.168.1.20" not in saved.text
    assert "lan-secret-5678" not in saved.text
    assert "lan-secret-5678" not in listed.text


def test_public_custom_connection_requires_api_key(tmp_path):
    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"choices": [{"message": {"content": "OK"}}]},
            )
        ),
        resolver=lambda _host: ["8.8.8.8"],
    )
    _, admin_client = _client(role="admin", broker=broker)

    response = admin_client.post(
        "/api/model-connections/managed",
        json={
            "display_name": "公网兼容接口",
            "base_url": "https://gateway.example/v1",
            "api_format": "openai_chat_completions",
            "model": "example-model",
            "api_key": "",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "公网自定义连接必须填写 API Key"


def test_exact_lan_connection_may_use_service_without_api_key(tmp_path):
    def provider(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") is None
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "OK"}}]},
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
    )
    _, admin_client = _client(role="admin", broker=broker)

    response = admin_client.post(
        "/api/model-connections/managed",
        json={
            "display_name": "无鉴权本地模型",
            "base_url": "http://192.168.1.20:6012/v1",
            "api_format": "openai_chat_completions",
            "model": "local-model",
            "api_key": "",
        },
    )

    assert response.status_code == 200
    assert response.json()["locality"] == "managed_private"
    assert response.json()["key_hint"] == ""


def test_personal_connection_cannot_be_deleted_or_seen_by_another_user(tmp_path):
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]},
        )
    )
    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=transport,
        resolver=lambda _host: ["8.8.8.8"],
    )
    _, owner_client = _client(user_id="owner-a", broker=broker)
    created = owner_client.put(
        "/api/model-connections/presets/deepseek",
        json={"api_key": "owner-a-secret-9999"},
    ).json()

    _, other_client = _client(user_id="owner-b", broker=broker)
    hidden = other_client.get("/api/model-connections")
    denied = other_client.delete(
        f"/api/model-connections/{created['connection_id']}"
    )
    owner_still_has_it = owner_client.get("/api/model-connections")
    deleted = owner_client.delete(
        f"/api/model-connections/{created['connection_id']}"
    )
    owner_after_delete = owner_client.get("/api/model-connections")

    assert hidden.json()["items"] == []
    assert denied.status_code == 404
    assert owner_still_has_it.json()["items"] == [created]
    assert deleted.status_code == 200
    assert owner_after_delete.json()["items"] == []


def test_failed_provider_verification_does_not_persist_connection(tmp_path):
    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                401,
                json={"error": {"message": "invalid api key"}},
            )
        ),
        resolver=lambda _host: ["8.8.8.8"],
    )
    _, client = _client(broker=broker)

    failed = client.put(
        "/api/model-connections/presets/deepseek",
        json={"api_key": "invalid-secret-1234"},  # gitleaks:allow -- 测试假值
    )
    listed = client.get("/api/model-connections")

    assert failed.status_code == 400
    assert "HTTP 401" in failed.json()["detail"]
    assert listed.json()["items"] == []


def test_managed_connection_rejects_cloud_metadata_before_transport(tmp_path):
    transport_called = False

    def provider(_request: httpx.Request) -> httpx.Response:
        nonlocal transport_called
        transport_called = True
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "OK"}}]},
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
    )
    _, client = _client(user_id="admin-a", role="admin", broker=broker)

    response = client.post(
        "/api/model-connections/managed",
        json={
            "display_name": "伪装的内网模型",
            "base_url": "http://169.254.169.254/latest/meta-data",
            "api_format": "openai_chat_completions",
            "model": "fake-model",
            "api_key": "metadata-secret",
        },
    )

    assert response.status_code == 400
    assert "cloud_metadata" in response.json()["detail"]
    assert transport_called is False


def test_managed_public_endpoint_requires_https_before_transport(tmp_path):
    transport_called = False

    def provider(_request: httpx.Request) -> httpx.Response:
        nonlocal transport_called
        transport_called = True
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "OK"}}]},
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )
    _, client = _client(user_id="admin-a", role="admin", broker=broker)

    response = client.post(
        "/api/model-connections/managed",
        json={
            "display_name": "不安全的公网模型",
            "base_url": "http://models.example.test/v1",
            "api_format": "openai_chat_completions",
            "model": "fake-model",
            "api_key": "public-secret",
        },
    )

    assert response.status_code == 400
    assert "只允许 HTTPS" in response.json()["detail"]
    assert transport_called is False


def test_personal_secret_is_encrypted_and_replacement_removes_old_secret(tmp_path):
    db_path = tmp_path / "webui.db"
    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(db_path))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "OK"}}
                    ]
                },
            )
        ),
        resolver=lambda _host: ["8.8.8.8"],
    )
    _, client = _client(broker=broker)

    first = client.put(
        "/api/model-connections/presets/deepseek",
        json={"api_key": "first-plaintext-secret-1111"},
    )
    replaced = client.put(
        "/api/model-connections/presets/deepseek",
        json={"api_key": "second-plaintext-secret-2222"},
    )

    with sqlite3.connect(db_path) as conn:
        secret_count = conn.execute(
            "SELECT COUNT(*) FROM model_connection_secrets"
        ).fetchone()[0]
        ciphertext = conn.execute(
            "SELECT ciphertext FROM model_connection_secrets"
        ).fetchone()[0]
    raw_db = db_path.read_bytes()

    assert first.status_code == 200
    assert replaced.status_code == 200
    assert first.json()["connection_id"] == replaced.json()["connection_id"]
    assert replaced.json()["key_hint"] == "2222"
    assert secret_count == 1
    assert ciphertext.startswith("gAAAA")
    assert b"first-plaintext-secret-1111" not in raw_db
    assert b"second-plaintext-secret-2222" not in raw_db


def test_old_single_connection_database_upgrades_idempotently(tmp_path):
    db_path = tmp_path / "legacy-webui.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE model_connections (
                connection_id TEXT PRIMARY KEY,
                owner_scope TEXT NOT NULL,
                owner_user_id TEXT,
                preset_id TEXT,
                preset_version TEXT,
                display_name TEXT NOT NULL,
                base_url TEXT NOT NULL,
                model TEXT NOT NULL,
                api_format TEXT NOT NULL,
                locality TEXT NOT NULL,
                secret_id TEXT,
                status TEXT NOT NULL,
                key_hint TEXT NOT NULL DEFAULT '',
                verified_at TEXT,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX idx_model_connections_personal_preset
            ON model_connections(owner_user_id, preset_id)
            WHERE owner_scope='user_personal';
            INSERT INTO model_connections VALUES (
                'legacy-connection', 'user_personal', 'user-a', 'deepseek',
                '2026-07-30.1', 'DeepSeek', 'https://api.deepseek.com',
                'deepseek-v4-pro', 'openai_chat_completions', 'public_external',
                NULL, 'verified', '1111', '2026-07-30T10:00:00', 'user-a',
                '2026-07-30T10:00:00', '2026-07-30T10:00:00'
            );
            """
        )

    repository = ModelConnectionRepository(str(migrated_webui_database(db_path)))
    ModelConnectionRepository(str(migrated_webui_database(db_path)))
    created = repository.create_personal(
        owner_user_id="user-a",
        preset_id="deepseek",
        preset_version="2026-07-30.1",
        display_name="DeepSeek 备用",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-pro",
        api_format="openai_chat_completions",
        ciphertext="encrypted-new-secret",
        key_hint="2222",
        verified_at="2026-07-30T11:00:00",
    )

    assert created["connection_id"] != "legacy-connection"
    assert {
        item["display_name"]
        for item in repository.list_available("user-a")
    } == {"DeepSeek", "DeepSeek 备用"}
    legacy_public = next(
        item
        for item in repository.list_available("user-a")
        if item["connection_id"] == "legacy-connection"
    )
    assert legacy_public["preset_version"] == "2026-07-30.1"
    assert [item["model_id"] for item in legacy_public["models"]] == [
        "deepseek-v4-pro"
    ]
    with sqlite3.connect(db_path) as conn:
        compatibility_slot = conn.execute(
            "SELECT compatibility_slot FROM model_connections "
            "WHERE connection_id='legacy-connection'"
        ).fetchone()[0]
        legacy_models = conn.execute(
            "SELECT model_id, status, enabled FROM model_connection_models "
            "WHERE connection_id='legacy-connection'"
        ).fetchall()
    assert compatibility_slot == "personal_preset_v1"
    assert legacy_models == [("deepseek-v4-pro", "available", 1)]


def test_legacy_user_key_import_is_idempotent_and_never_calls_provider(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "webui_db_path", str(tmp_path / "webui.db"))
    auth_mod._store = None
    migrated_webui_database(settings.webui_db_path)
    store = auth_mod.get_store()
    store.config_set(
        "user-a", "deepseek_api_key", "legacy-personal-secret-7788", "user-a"
    )
    calls = 0

    def provider(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )
    _, client = _client(user_id="user-a", broker=broker)
    first = client.post("/api/model-connections/imports/legacy")
    second = client.post("/api/model-connections/imports/legacy")

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls == 0
    assert first.json()["items"][0]["connection_id"] == second.json()["items"][0][
        "connection_id"
    ]
    assert first.json()["items"][0]["status"] == "pending_validation"
    assert "legacy-personal-secret-7788" not in first.text
    assert store.config_all("user-a")["deepseek_api_key"] == (
        "legacy-personal-secret-7788"
    )
    # 旧表仍保留原值是明确迁移边界；新连接密文表和产品响应不得复制明文。
    with sqlite3.connect(tmp_path / "webui.db") as conn:
        ciphertext = conn.execute(
            "SELECT ciphertext FROM model_connection_secrets"
        ).fetchone()[0]
    assert "legacy-personal-secret-7788" not in ciphertext


def test_imported_lan_connection_without_key_can_be_verified(tmp_path):
    def provider(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") is None
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "OK"}}]},
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["192.168.50.10"],
    )
    imported = broker.import_legacy_connection(
        source_scope="global",
        source_key="local_model:0",
        owner_user_id=None,
        created_by="admin-a",
        display_name="导入的本地模型",
        base_url="http://192.168.50.10:6012/v1",
        api_format="openai_chat_completions",
        model="Qwen3.6-35B-A3B",
        api_key="",
        locality="managed_private",
    )

    verified = asyncio.run(
        broker.retry_models(
            owner_user_id="admin-a",
            connection_id=str(imported["connection_id"]),
            model_ids=["Qwen3.6-35B-A3B"],
            can_manage=True,
        )
    )

    assert verified["status"] == "verified"
    assert verified["models"][0]["status"] == "available"
    with sqlite3.connect(tmp_path / "webui.db") as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM model_connection_secrets"
        ).fetchone()[0] == 0


def test_imported_official_preset_can_retry_through_clash_fake_ip(tmp_path):
    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"choices": [{"message": {"content": "OK"}}]},
            )
        ),
        resolver=lambda _host: ["198.18.0.159"],
    )
    imported = broker.import_legacy_connection(
        source_scope="global",
        source_key="deepseek_api_key",
        owner_user_id=None,
        created_by="admin-a",
        display_name="导入的平台 DeepSeek",
        base_url="https://api.deepseek.com",
        api_format="openai_chat_completions",
        model="deepseek-v4-flash",
        api_key="legacy-secret-1234",  # gitleaks:allow -- 测试假值
        preset_id="deepseek",
    )
    _, admin = _client(user_id="admin-a", role="admin", broker=broker)

    response = admin.post(
        f"/api/model-connections/{imported['connection_id']}/models/retry",
        json={"model_ids": ["deepseek-v4-flash", "deepseek-v4-pro"]},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "verified"
    assert {
        item["status"] for item in response.json()["models"]
    } == {"available"}


def test_manager_discovers_four_protocols_but_user_cannot_probe_custom_endpoint(
    tmp_path,
):
    def provider(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "model-a"}]})
        if path.endswith("/v1/messages"):
            return httpx.Response(200, json={"type": "message"})
        if path.endswith("/chat/completions"):
            return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})
        if path.endswith("/responses"):
            return httpx.Response(200, json={"object": "response"})
        if path.endswith(":generateContent"):
            return httpx.Response(200, json={"candidates": [{"content": {}}]})
        return httpx.Response(404)

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )
    _, user = _client(role="user", broker=broker)
    _, admin = _client(role="admin", broker=broker)
    denied = user.post(
        "/api/model-connections/managed/discover",
        json={"base_url": "https://gateway.example/v1", "api_key": "secret-1234"},
    )
    discovered = admin.post(
        "/api/model-connections/managed/discover",
        json={"base_url": "https://gateway.example/v1", "api_key": "secret-1234"},
    )

    assert denied.status_code == 403
    assert discovered.status_code == 200
    assert discovered.json()["models"] == ["model-a"]
    assert set(discovered.json()["detected_api_formats"]) == {
        "anthropic_messages",
        "openai_chat_completions",
        "openai_responses",
        "gemini_generate_content",
    }


@pytest.mark.parametrize(
    ("api_format", "expected_suffix", "provider_payload"),
    [
        (
            "anthropic_messages",
            "/v1/messages",
            {"type": "message"},
        ),
        (
            "openai_chat_completions",
            "/chat/completions",
            {"choices": [{"message": {"content": "OK"}}]},
        ),
        (
            "openai_responses",
            "/responses",
            {"object": "response"},
        ),
        (
            "gemini_generate_content",
            "/models/model-a:generateContent",
            {"candidates": [{"content": {}}]},
        ),
    ],
)
def test_manager_saves_each_custom_protocol_after_real_minimal_probe(
    tmp_path,
    api_format,
    expected_suffix,
    provider_payload,
):
    def provider(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(expected_suffix)
        return httpx.Response(200, json=provider_payload)

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / f"{api_format}.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )
    _, admin = _client(role="admin", broker=broker)
    saved = admin.post(
        "/api/model-connections/managed",
        json={
            "display_name": f"自定义 {api_format}",
            "base_url": "https://gateway.example/v1",
            "api_format": api_format,
            "model": "model-a",
            "models": ["model-a"],
            "api_key": "custom-secret-1234",
        },
    )

    assert saved.status_code == 200
    assert saved.json()["api_format"] == api_format
    assert saved.json()["models"][0]["status"] == "available"
    assert "custom-secret-1234" not in saved.text


def test_openai_preset_is_verified_through_responses_api(tmp_path):
    seen: dict[str, object] = {}

    def provider(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["host"] = request.headers.get("host")
        seen["sni_hostname"] = request.extensions.get("sni_hostname")
        seen["authorization"] = request.headers.get("authorization")
        seen["json"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "output": [{"type": "message", "content": []}],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )
    _, client = _client(broker=broker)

    response = client.put(
        "/api/model-connections/presets/openai",
        json={"api_key": "openai-secret-0001", "model": "gpt-5.6-terra"},
    )

    assert response.status_code == 200
    _assert_pinned_provider_request(
        seen,
        "https://api.openai.com/v1/responses",
    )
    assert seen["authorization"] == "Bearer openai-secret-0001"
    request_json = str(seen["json"])
    assert '"input":"Reply with OK."' in request_json
    assert '"store":false' in request_json
    assert response.json()["api_format"] == "openai_responses"


def test_qwen_preset_uses_shared_china_responses_route_and_frozen_model(tmp_path):
    seen: dict[str, object] = {}

    def provider(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["host"] = request.headers.get("host")
        seen["sni_hostname"] = request.extensions.get("sni_hostname")
        seen["json"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={"id": "resp_qwen", "object": "response", "output": []},
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )
    _, client = _client(broker=broker)

    response = client.put(
        "/api/model-connections/presets/qwen",
        json={"api_key": "qwen-secret-0002"},
    )

    assert response.status_code == 200
    _assert_pinned_provider_request(
        seen,
        "https://dashscope.aliyuncs.com/compatible-mode/v1/responses",
    )
    assert '"model":"qwen3.7-plus-2026-05-26"' in str(seen["json"])
    assert response.json()["model"] == "qwen3.7-plus-2026-05-26"
    assert response.json()["api_format"] == "openai_responses"


def test_anthropic_preset_is_verified_through_native_messages_api(tmp_path):
    seen: dict[str, object] = {}

    def provider(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["host"] = request.headers.get("host")
        seen["sni_hostname"] = request.extensions.get("sni_hostname")
        seen["x_api_key"] = request.headers.get("x-api-key")
        seen["version"] = request.headers.get("anthropic-version")
        seen["json"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "OK"}],
            },
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )
    _, client = _client(broker=broker)

    response = client.put(
        "/api/model-connections/presets/anthropic",
        json={"api_key": "anthropic-secret-0003"},  # gitleaks:allow -- 测试假值
    )

    assert response.status_code == 200
    _assert_pinned_provider_request(
        seen,
        "https://api.anthropic.com/v1/messages",
    )
    assert seen["x_api_key"] == "anthropic-secret-0003"
    assert seen["version"] == "2023-06-01"
    assert '"model":"claude-sonnet-5"' in str(seen["json"])
    assert response.json()["api_format"] == "anthropic_messages"


def test_gemini_preset_is_verified_through_native_generate_content(tmp_path):
    seen: dict[str, object] = {}

    def provider(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["host"] = request.headers.get("host")
        seen["sni_hostname"] = request.extensions.get("sni_hostname")
        seen["api_key"] = request.headers.get("x-goog-api-key")
        seen["json"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": "OK"}],
                        }
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 1,
                    "candidatesTokenCount": 1,
                    "totalTokenCount": 2,
                },
            },
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )
    _, client = _client(broker=broker)

    response = client.put(
        "/api/model-connections/presets/gemini",
        json={"api_key": "gemini-secret-0004"},
    )

    assert response.status_code == 200
    _assert_pinned_provider_request(
        seen,
        "https://generativelanguage.googleapis.com/v1beta/"
        "models/gemini-3.6-flash:generateContent",
    )
    assert seen["api_key"] == "gemini-secret-0004"
    assert '"contents":' in str(seen["json"])
    assert response.json()["api_format"] == "gemini_generate_content"


def test_product_app_exposes_model_connection_interface(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    database = migrated_webui_database(tmp_path / "product-webui.db")
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    from src.api.main import app as product_app

    paths = product_app.openapi()["paths"]

    assert "/api/model-connections/presets" in paths
    assert "/api/model-connections" in paths
    assert "/api/model-connections/managed" in paths


def test_broker_relay_uses_scoped_grant_and_records_native_stream_usage(
    tmp_path,
):
    provider_secret = "personal-provider-secret-7788"
    seen: dict[str, object] = {}
    stream = (
        b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":7,'
        b'"completion_tokens":2,"total_tokens":9}}\n\n'
        b"data: [DONE]\n\n"
    )

    def provider(request: httpx.Request) -> httpx.Response:
        body = request.read()
        if b'"stream":false' in body:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "OK",
                            }
                        }
                    ]
                },
            )
        seen["url"] = str(request.url)
        seen["host"] = request.headers.get("host")
        seen["sni_hostname"] = request.extensions.get("sni_hostname")
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = body
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=stream,
        )

    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
        clock=lambda: now,
    )

    async def scenario():
        connection = await broker.configure_personal(
            owner_user_id="user-a",
            preset_id="deepseek",
            api_key=provider_secret,
            model="deepseek-v4-pro",
        )
        grant = broker.issue_grant(
            owner_user_id="user-a",
            connection_id=str(connection["connection_id"]),
            connection_version=_connection_version(broker, connection),
            task_id="workspace_grant_relay",
            revision=1,
            run_id="pi_run_grant_relay",
            purpose="agent_inference",
            ttl_seconds=1800,
        )
        relayed = await broker.relay(
            grant_token=grant.token,
            protocol_path="chat/completions",
            method="POST",
            headers={
                "authorization": f"Bearer {grant.token}",
                "content-type": "application/json",
            },
            body=(
                b'{"model":"deepseek-v4-pro","stream":true,'
                b'"messages":[{"role":"user","content":"hello"}]}'
            ),
        )
        body = b"".join([chunk async for chunk in relayed.iter_bytes()])
        usage = broker.list_usage(
            "user-a",
            task_id="workspace_grant_relay",
            revision=1,
        )
        trace_usage = broker.list_usage(
            "user-a",
            task_id="workspace_grant_relay",
            revision=1,
            include_identity=True,
        )
        return grant, relayed, body, usage, trace_usage

    grant, relayed, body, usage, trace_usage = asyncio.run(scenario())

    assert seen["url"] == "https://8.8.8.8/chat/completions"
    assert seen["host"] == "api.deepseek.com"
    assert seen["sni_hostname"] == "api.deepseek.com"
    assert seen["authorization"] == f"Bearer {provider_secret}"
    assert grant.token not in str(seen["authorization"])
    assert provider_secret not in grant.model_dump_json()
    assert grant.expires_at == now + timedelta(seconds=1800)
    assert relayed.status_code == 200
    assert relayed.content_type == "text/event-stream"
    assert body == stream
    assert usage == [
        {
            "purpose": "agent_inference",
            "status": "recorded",
            "input_tokens": 7,
            "output_tokens": 2,
            "total_tokens": 9,
            "request_count": 1,
        }
    ]
    assert len(trace_usage) == 1
    assert trace_usage[0] == {
        "owner_user_id": "user-a",
        "task_id": "workspace_grant_relay",
        "revision": 1,
        "run_id": "pi_run_grant_relay",
        "connection_id": str(grant.connection_id),
        "model": "deepseek-v4-pro",
        "purpose": "agent_inference",
        "status": "recorded",
        "input_tokens": 7,
        "output_tokens": 2,
        "total_tokens": 9,
        "request_count": 1,
        "created_at": trace_usage[0]["created_at"],
        "cache_tokens": None,
    }
    assert isinstance(trace_usage[0]["created_at"], str)
    assert "native_json" not in trace_usage[0]


@pytest.mark.parametrize(
    (
        "preset_id",
        "protocol_path",
        "relay_body",
        "verification_response",
        "relay_response",
        "expected_url",
        "auth_header",
        "expected_usage",
    ),
    [
        (
            "qwen",
            "responses",
            {
                "model": "qwen3.7-plus-2026-05-26",
                "input": "hello",
                "stream": False,
            },
            {"id": "resp_verify", "object": "response", "output": []},
            {
                "id": "resp_relay",
                "object": "response",
                "output": [],
                "usage": {
                    "input_tokens": 3,
                    "output_tokens": 2,
                    "total_tokens": 5,
                },
            },
            "https://dashscope.aliyuncs.com/compatible-mode/v1/responses",
            "authorization",
            (3, 2, 5),
        ),
        (
            "anthropic",
            "v1/messages",
            {
                "model": "claude-sonnet-5",
                "max_tokens": 32,
                "messages": [{"role": "user", "content": "hello"}],
            },
            {
                "id": "msg_verify",
                "type": "message",
                "content": [{"type": "text", "text": "OK"}],
            },
            {
                "id": "msg_relay",
                "type": "message",
                "content": [{"type": "text", "text": "OK"}],
                "usage": {"input_tokens": 4, "output_tokens": 3},
            },
            "https://api.anthropic.com/v1/messages",
            "x-api-key",
            (4, 3, None),
        ),
        (
            "gemini",
            "models/gemini-3.6-flash:generateContent",
            {
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": "hello"}],
                    }
                ]
            },
            {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": "OK"}],
                        }
                    }
                ]
            },
            {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": "OK"}],
                        }
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 5,
                    "candidatesTokenCount": 4,
                    "totalTokenCount": 9,
                },
            },
            (
                "https://generativelanguage.googleapis.com/v1beta/"
                "models/gemini-3.6-flash:generateContent"
            ),
            "x-goog-api-key",
            (5, 4, 9),
        ),
    ],
)
def test_broker_relay_preserves_each_native_protocol_and_usage(
    tmp_path,
    preset_id,
    protocol_path,
    relay_body,
    verification_response,
    relay_response,
    expected_url,
    auth_header,
    expected_usage,
):
    provider_secret = f"{preset_id}-provider-secret-1234"
    call_count = 0
    seen: dict[str, object] = {}

    def provider(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, json=verification_response)
        seen["url"] = str(request.url)
        seen["host"] = request.headers.get("host")
        seen["sni_hostname"] = request.extensions.get("sni_hostname")
        seen["auth"] = request.headers.get(auth_header)
        return httpx.Response(200, json=relay_response)

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )

    async def scenario():
        connection = await broker.configure_personal(
            owner_user_id="user-a",
            preset_id=preset_id,
            api_key=provider_secret,
        )
        grant = broker.issue_grant(
            owner_user_id="user-a",
            connection_id=str(connection["connection_id"]),
            connection_version=_connection_version(broker, connection),
            task_id=f"workspace_{preset_id}_relay",
            revision=1,
            run_id=f"pi_run_{preset_id}_relay",
            purpose="agent_inference",
        )
        response = await broker.relay(
            grant_token=grant.token,
            protocol_path=protocol_path,
            method="POST",
            headers={auth_header: grant.token},
            body=json.dumps(relay_body).encode("utf-8"),
        )
        _ = b"".join(
            [chunk async for chunk in response.iter_bytes()]
        )
        return broker.list_usage(
            "user-a",
            task_id=f"workspace_{preset_id}_relay",
            revision=1,
        )

    usage = asyncio.run(scenario())

    expected_parts = urlsplit(expected_url)
    pinned_parts = urlsplit(str(seen["url"]))
    assert pinned_parts.scheme == expected_parts.scheme
    assert pinned_parts.hostname == "8.8.8.8"
    assert pinned_parts.path == expected_parts.path
    assert pinned_parts.query == expected_parts.query
    assert seen["host"] == expected_parts.hostname
    assert seen["sni_hostname"] == expected_parts.hostname
    assert (
        seen["auth"] == provider_secret
        if auth_header != "authorization"
        else seen["auth"] == f"Bearer {provider_secret}"
    )
    assert (
        usage[0]["input_tokens"],
        usage[0]["output_tokens"],
        usage[0]["total_tokens"],
    ) == expected_usage


def test_broker_fails_closed_for_owner_expiry_revocation_and_rotation(
    tmp_path,
):
    current_time = [datetime(2026, 7, 30, tzinfo=timezone.utc)]

    def provider(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "OK"}}
                ]
            },
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
        clock=lambda: current_time[0],
    )

    async def scenario() -> None:
        connection = await broker.configure_personal(
            owner_user_id="user-a",
            preset_id="deepseek",
            api_key="provider-secret-before-rotation",
            model="deepseek-v4-pro",
        )
        version = _connection_version(broker, connection)
        with pytest.raises(GrantError, match="无权访问"):
            broker.issue_grant(
                owner_user_id="user-b",
                connection_id=str(connection["connection_id"]),
                connection_version=version,
                task_id="workspace-owner",
                revision=1,
                run_id="pi_run_owner",
                purpose="agent_inference",
            )

        expired = broker.issue_grant(
            owner_user_id="user-a",
            connection_id=str(connection["connection_id"]),
            connection_version=version,
            task_id="workspace-expired",
            revision=1,
            run_id="pi_run_expired",
            purpose="agent_inference",
            ttl_seconds=1,
        )
        current_time[0] += timedelta(seconds=2)
        with pytest.raises(GrantError, match="已过期"):
            await broker.relay(
                grant_token=expired.token,
                protocol_path="chat/completions",
                method="POST",
                headers={},
                body=b'{"model":"deepseek-v4-pro","messages":[]}',
            )

        revoked = broker.issue_grant(
            owner_user_id="user-a",
            connection_id=str(connection["connection_id"]),
            connection_version=version,
            task_id="workspace-revoked",
            revision=1,
            run_id="pi_run_revoked",
            purpose="agent_inference",
        )
        assert broker.revoke_grant(revoked.grant_id, "test") is True
        with pytest.raises(GrantError, match="已撤销"):
            await broker.relay(
                grant_token=revoked.token,
                protocol_path="chat/completions",
                method="POST",
                headers={},
                body=b'{"model":"deepseek-v4-pro","messages":[]}',
            )

        before_rotation = broker.issue_grant(
            owner_user_id="user-a",
            connection_id=str(connection["connection_id"]),
            connection_version=version,
            task_id="workspace-rotated",
            revision=1,
            run_id="pi_run_rotated",
            purpose="agent_inference",
        )
        await broker.configure_personal(
            owner_user_id="user-a",
            preset_id="deepseek",
            api_key="provider-secret-after-rotation",
            model="deepseek-v4-pro",
        )
        with pytest.raises(GrantError, match="版本已轮换"):
            await broker.relay(
                grant_token=before_rotation.token,
                protocol_path="chat/completions",
                method="POST",
                headers={},
                body=b'{"model":"deepseek-v4-pro","messages":[]}',
            )
        with pytest.raises(GrantError, match="版本已变化"):
            broker.issue_grant(
                owner_user_id="user-a",
                connection_id=str(connection["connection_id"]),
                connection_version=version,
                task_id="workspace-stale-binding",
                revision=1,
                run_id="pi_run_stale_binding",
                purpose="agent_inference",
            )

    asyncio.run(scenario())


def test_broker_records_unknown_usage_when_provider_send_fails(tmp_path):
    calls = 0

    def provider(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "OK"}}
                    ]
                },
            )
        raise httpx.ConnectError("provider unavailable", request=request)

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )

    async def scenario():
        connection = await broker.configure_personal(
            owner_user_id="user-a",
            preset_id="deepseek",
            api_key="provider-secret-unknown-usage",
            model="deepseek-v4-pro",
        )
        grant = broker.issue_grant(
            owner_user_id="user-a",
            connection_id=str(connection["connection_id"]),
            connection_version=_connection_version(broker, connection),
            task_id="workspace-unknown-usage",
            revision=1,
            run_id="pi_run_unknown_usage",
            purpose="agent_inference",
        )
        with pytest.raises(ConnectionError, match="连接失败"):
            await broker.relay(
                grant_token=grant.token,
                protocol_path="chat/completions",
                method="POST",
                headers={},
                body=b'{"model":"deepseek-v4-pro","messages":[]}',
            )
        return broker.list_usage(
            "user-a",
            task_id="workspace-unknown-usage",
            revision=1,
        )

    assert asyncio.run(scenario()) == [
        {
            "purpose": "agent_inference",
            "status": "unknown",
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "request_count": 1,
        }
    ]


def test_broker_restores_gemini_sse_query_and_json_content_type(tmp_path):
    calls = 0
    seen: dict[str, str] = {}

    def provider(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "role": "model",
                                "parts": [{"text": "OK"}],
                            }
                        }
                    ]
                },
            )
        seen["url"] = str(request.url)
        seen["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"candidates":[],"usageMetadata":'
                b'{"promptTokenCount":2,"candidatesTokenCount":1,'
                b'"totalTokenCount":3}}\n\n'
            ),
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )

    async def scenario():
        connection = await broker.configure_personal(
            owner_user_id="user-a",
            preset_id="gemini",
            api_key="gemini-provider-secret",
        )
        grant = broker.issue_grant(
            owner_user_id="user-a",
            connection_id=str(connection["connection_id"]),
            connection_version=_connection_version(broker, connection),
            task_id="workspace-gemini-stream",
            revision=1,
            run_id="pi_run_gemini_stream",
            purpose="agent_inference",
        )
        response = await broker.relay(
            grant_token=grant.token,
            protocol_path=(
                "models/gemini-3.6-flash:streamGenerateContent"
            ),
            method="POST",
            headers={"x-goog-api-key": grant.token},
            body=b'{"contents":[]}',
        )
        return b"".join(
            [chunk async for chunk in response.iter_bytes()]
        )

    body = asyncio.run(scenario())
    assert seen["url"].endswith(
        "/models/gemini-3.6-flash:streamGenerateContent?alt=sse"
    )
    assert seen["content_type"] == "application/json"
    assert body.startswith(b"data: ")


def test_broker_decodes_compressed_provider_response_before_forwarding(
    tmp_path,
):
    calls = 0
    payload = json.dumps(
        {
            "choices": [{"message": {"content": "OK"}}],
            "usage": {
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "total_tokens": 3,
            },
        }
    ).encode("utf-8")

    def provider(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "OK"}}
                    ]
                },
            )
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "content-encoding": "gzip",
            },
            content=gzip.compress(payload),
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )

    async def scenario():
        connection = await broker.configure_personal(
            owner_user_id="user-a",
            preset_id="deepseek",
            api_key="provider-secret-compressed",
            model="deepseek-v4-pro",
        )
        grant = broker.issue_grant(
            owner_user_id="user-a",
            connection_id=str(connection["connection_id"]),
            connection_version=_connection_version(broker, connection),
            task_id="workspace-compressed",
            revision=1,
            run_id="pi_run_compressed",
            purpose="agent_inference",
        )
        response = await broker.relay(
            grant_token=grant.token,
            protocol_path="chat/completions",
            method="POST",
            headers={},
            body=b'{"model":"deepseek-v4-pro","messages":[]}',
        )
        body = b"".join(
            [chunk async for chunk in response.iter_bytes()]
        )
        return response, body

    response, body = asyncio.run(scenario())
    assert body == payload
    assert "content-encoding" not in response.headers


def test_internal_relay_http_adapter_forwards_grant_and_is_not_documented(
    tmp_path,
    monkeypatch,
):
    from src.api.routes import model_relay

    provider_secret = "relay-http-provider-secret-8899"

    def provider(request: httpx.Request) -> httpx.Response:
        body = request.read()
        if b'"stream":false' in body:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "OK"}}
                    ]
                },
            )
        assert request.headers["authorization"] == (
            f"Bearer {provider_secret}"
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'data: {"choices":[]}\n\ndata: [DONE]\n\n',
        )

    broker = ConnectionBroker(
        repository=ModelConnectionRepository(str(migrated_webui_database(tmp_path / "webui.db"))),
        vault=FernetCredentialVault.generate(),
        transport=httpx.MockTransport(provider),
        resolver=lambda _host: ["8.8.8.8"],
    )

    async def setup():
        connection = await broker.configure_personal(
            owner_user_id="user-a",
            preset_id="deepseek",
            api_key=provider_secret,
            model="deepseek-v4-pro",
        )
        return broker.issue_grant(
            owner_user_id="user-a",
            connection_id=str(connection["connection_id"]),
            connection_version=_connection_version(broker, connection),
            task_id="workspace_relay_http",
            revision=1,
            run_id="pi_run_relay_http",
            purpose="agent_inference",
        )

    grant = asyncio.run(setup())
    app = FastAPI()
    app.include_router(model_relay.router)
    app.dependency_overrides[
        model_relay.get_connection_broker
    ] = lambda: broker

    with TestClient(app) as client:
        response = client.post(
            "/internal/model-relay/chat/completions",
            headers={
                "Authorization": f"Bearer {grant.token}",
                "Content-Type": "application/json",
            },
            content=(
                b'{"model":"deepseek-v4-pro","stream":true,'
                b'"messages":[]}'
            ),
        )
        query_token = client.post(
            (
                "/internal/model-relay/chat/completions"
                f"?key={grant.token}"
            ),
            headers={"Content-Type": "application/json"},
            content=b'{"model":"deepseek-v4-pro","messages":[]}',
        )
        monkeypatch.setattr(model_relay, "_MAX_RELAY_REQUEST_BYTES", 32)
        oversized = client.post(
            "/internal/model-relay/chat/completions",
            headers={
                "Authorization": f"Bearer {grant.token}",
                "Content-Type": "application/json",
            },
            content=b"x" * 33,
        )

    assert response.status_code == 200
    assert response.content == (
        b'data: {"choices":[]}\n\ndata: [DONE]\n\n'
    )
    assert query_token.status_code == 401
    assert oversized.status_code == 413
    assert "/internal/model-relay/{protocol_path}" not in app.openapi()[
        "paths"
    ]
