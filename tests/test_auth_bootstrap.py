"""首次管理员由维护者显式建立，公开注册不具备引导权限。"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import asyncio
import sqlite3
import warnings
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api import auth
from src.api.routes import auth_routes
from src.api.store import WebUIStore
from src.config.settings import settings
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "webui.db")))
    monkeypatch.setattr(auth, "_store", store)
    monkeypatch.setattr(settings, "jwt_secret", "isolated-auth-test-key-" + "x" * 32)
    monkeypatch.setattr(settings, "webui_allow_register", False)
    app = FastAPI()
    app.include_router(auth_routes.router)
    return TestClient(app), store


@pytest.mark.parametrize("setting", [None, "0"])
def test_empty_database_cannot_bypass_closed_registration(auth_client, setting):
    client, store = auth_client
    if setting is not None:
        store.set_setting("allow_register", setting)

    response = client.post("/api/auth/register", json={"username": "alice", "password": "example-password"})

    assert response.status_code == 403
    assert store.count_users() == 0


def test_open_registration_only_creates_pending_ordinary_user(auth_client):
    client, store = auth_client
    store.set_setting("allow_register", "1")

    response = client.post("/api/auth/register", json={"username": "alice", "password": "example-password"})

    assert response.status_code == 200
    assert response.json()["pending"] is True
    assert "access_token" not in response.json()
    user = store.get_user_by_name("alice")
    assert user["role"] == "user"
    assert user["pending"] == 1
    assert client.post("/api/auth/login", json={"username": "alice", "password": "example-password"}).status_code == 403


def test_concurrent_maintainers_create_exactly_one_super_admin(auth_client):
    _, store = auth_client
    barrier = Barrier(2)
    password_hash = auth.hash_password("example-admin-password")

    def initialize(username):
        independent_store = WebUIStore(store.db_path)
        barrier.wait(timeout=10)
        try:
            independent_store.bootstrap_super_admin(username, password_hash)
            return "created"
        except ValueError:
            return "already_initialized"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(initialize, ["maintainer-a", "maintainer-b"]))

    assert sorted(outcomes) == ["already_initialized", "created"]
    users, total = store.list_users(role="super_admin")
    assert total == store.count_users() == 1
    assert users[0]["pending"] == 0


def test_bootstrap_preserves_existing_users_and_never_replaces_super_admin(auth_client):
    _, store = auth_client
    ordinary = store.create_user("alice", auth.hash_password("example-password"), pending=True)
    before = store.get_user(ordinary["user_id"])

    admin = store.bootstrap_super_admin("maintainer", auth.hash_password("example-admin-password"))
    store.update_user(admin["user_id"], disabled=True)
    admin_before = store.get_user(admin["user_id"])
    with pytest.raises(ValueError, match="已存在"):
        store.bootstrap_super_admin("replacement", auth.hash_password("another-example-password"))

    assert store.get_user(ordinary["user_id"]) == before
    assert store.get_user(admin["user_id"]) == admin_before
    assert store.count_users() == 2


def test_maintainer_cli_initializes_with_registration_closed(auth_client, monkeypatch, capsys):
    from src.api import bootstrap_admin

    client, store = auth_client
    monkeypatch.setattr(bootstrap_admin.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(bootstrap_admin.getpass, "getpass", lambda prompt: "example-admin-password")

    assert bootstrap_admin.main(["--database", store.db_path, "--username", "maintainer"]) == 0

    assert "example-admin-password" not in capsys.readouterr().out
    response = client.post("/api/auth/login", json={"username": "maintainer", "password": "example-admin-password"})
    assert response.status_code == 200
    assert response.json()["role"] == "super_admin"
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer " + response.json()["access_token"]}).status_code == 200


@pytest.mark.parametrize("secret", ["", " " * 32, "short", "mangrove-dev-secret-change-me-in-production-please", "change-me-to-a-long-random-secret-string"])
def test_token_signing_and_verification_reject_unsafe_configuration(secret, monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "isolated-auth-test-key-" + "x" * 32)
    token = auth.create_token("owner-test")
    monkeypatch.setattr(settings, "jwt_secret", secret)

    with pytest.raises(ValueError, match="JWT_SECRET"):
        auth.create_token("owner-test")
    with pytest.raises(ValueError, match="JWT_SECRET"):
        auth.get_current_user(auth.HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))


def test_gateway_rejects_missing_secret_before_starting_services(monkeypatch):
    from src.api import main

    monkeypatch.setattr(settings, "jwt_secret", "")

    def unexpected_store_access():
        pytest.fail("配置失败必须先于数据库和后台服务启动")

    monkeypatch.setattr(auth, "get_store", unexpected_store_access)

    async def start():
        async with main.lifespan(main.app):
            pytest.fail("缺少签名配置仍然启动成功")

    with pytest.raises(ValueError, match="JWT_SECRET"):
        asyncio.run(start())


def test_bootstrap_never_promotes_existing_username(auth_client):
    _, store = auth_client
    user = store.create_user("alice", auth.hash_password("example-password"), pending=True)
    before = store.get_user(user["user_id"])

    with pytest.raises(sqlite3.IntegrityError):
        store.bootstrap_super_admin("alice", auth.hash_password("replacement-password"))

    assert store.get_user(user["user_id"]) == before
    assert store.count_users() == 1


@pytest.mark.parametrize("passwords", [("short",), ("example-password-a", "example-password-b")])
def test_bootstrap_cli_rejects_invalid_password_without_creating_user(auth_client, monkeypatch, passwords):
    from src.api import bootstrap_admin

    _, store = auth_client
    answers = iter(passwords)
    monkeypatch.setattr(bootstrap_admin.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(bootstrap_admin.getpass, "getpass", lambda prompt: next(answers))

    with pytest.raises(SystemExit) as exc:
        bootstrap_admin.main(["--database", store.db_path, "--username", "maintainer"])

    assert exc.value.code == 2
    assert store.count_users() == 0


def test_bootstrap_cli_refuses_noninteractive_password_input(auth_client, monkeypatch):
    from src.api import bootstrap_admin

    _, store = auth_client
    monkeypatch.setattr(bootstrap_admin.sys.stdin, "isatty", lambda: False)

    with pytest.raises(SystemExit) as exc:
        bootstrap_admin.main(["--database", store.db_path, "--username", "maintainer"])

    assert exc.value.code == 2
    assert store.count_users() == 0


def test_database_credentials_require_safe_fallback_but_keep_independent_key(monkeypatch):
    from src.services.db_connections import decrypt_password, encrypt_password

    monkeypatch.setattr(settings, "jwt_secret", "")
    monkeypatch.setattr(settings, "data_prep_db_secret_key", "")
    with pytest.raises(ValueError, match="JWT_SECRET"):
        encrypt_password("fixture-password")

    monkeypatch.setattr(settings, "data_prep_db_secret_key", "independent-fixture-key")
    encrypted = encrypt_password("fixture-password")
    assert decrypt_password(encrypted) == "fixture-password"


@pytest.mark.parametrize("warning_at", [1, 2])
def test_bootstrap_cli_stops_if_password_cannot_be_hidden(auth_client, monkeypatch, warning_at):
    from src.api import bootstrap_admin

    _, store = auth_client
    calls = 0

    def password_prompt(prompt):
        nonlocal calls
        calls += 1
        if calls == warning_at:
            warnings.warn("无法关闭终端回显", bootstrap_admin.getpass.GetPassWarning)
        return "example-admin-password"

    monkeypatch.setattr(bootstrap_admin.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(bootstrap_admin.getpass, "getpass", password_prompt)
    with pytest.raises(SystemExit) as exc:
        bootstrap_admin.main(["--database", store.db_path, "--username", "maintainer"])

    assert exc.value.code == 1
    assert calls == warning_at
    assert store.count_users() == 0
