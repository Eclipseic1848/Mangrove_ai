# -*- coding: utf-8 -*-
"""平台设备会话通过真实认证路由和临时迁移库验证。"""
from http.cookies import SimpleCookie
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
import secrets
import sqlite3
import time

import jwt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import auth
from src.api.routes import admin_routes, auth_routes
from src.api.store import WebUIStore
from src.config.settings import settings
from tests.database_migration_helpers import migrated_webui_database


PASSWORD = "synthetic-device-password"


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "sessions.db")))
    monkeypatch.setattr(auth, "_store", store)
    owner = store.create_user("synthetic-owner", auth.hash_password(PASSWORD), pending=False)
    app = FastAPI()
    app.include_router(auth_routes.router)
    app.include_router(admin_routes.router)
    clients = []

    def device():
        client = TestClient(app, base_url="https://testserver", headers={
            "X-Mangrove-CSRF": "1", "Origin": "https://testserver",
        })
        clients.append(client)
        return client

    yield device, store, owner
    for client in clients:
        client.close()


def login(client, username="synthetic-owner", password=PASSWORD):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response


def test_login_issues_only_host_only_http_only_cookies(sessions):
    device, _, owner = sessions
    client = device()
    response = login(client)
    cookies = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookies.load(header)
    assert len(cookies) == 2, "登录必须通过两个 HttpOnly Cookie 发放设备凭证"
    assert {cookie["path"] for cookie in cookies.values()} == {"/api", "/api/auth"}
    for cookie in cookies.values():
        assert cookie["httponly"] and cookie["secure"]
        assert cookie["samesite"].lower() == "strict"
        assert not cookie["domain"]
    assert "access_token" not in response.json()
    assert "refresh_token" not in response.json()
    assert response.json()["user_id"] == owner["user_id"]
    assert client.get("/api/auth/me").status_code == 200


def test_logout_revokes_copied_current_device_credentials(sessions):
    device, _, _ = sessions
    client = device()
    login(client)
    copied = device()
    copied.cookies.update(client.cookies)
    response = client.post("/api/auth/logout")
    assert response.status_code in (200, 204)
    assert copied.get("/api/auth/me").status_code == 401
    assert copied.post("/api/auth/refresh").status_code == 401
    assert client.post("/api/auth/logout").status_code in (200, 204)


def test_logout_only_revokes_one_device_and_logout_all_revokes_both(sessions):
    device, _, _ = sessions
    first, second = device(), device()
    login(first)
    login(second)
    assert first.post("/api/auth/logout").status_code in (200, 204)
    assert second.get("/api/auth/me").status_code == 200
    login(first)
    assert second.post("/api/auth/logout-all").status_code in (200, 204)
    for client in (first, second):
        assert client.get("/api/auth/me").status_code == 401
        assert client.post("/api/auth/refresh").status_code == 401


def test_me_never_rotates_or_extends_credentials(sessions):
    device, _, _ = sessions
    client = device()
    initial = login(client)
    cookies = dict(client.cookies)
    for _ in range(2):
        response = client.get("/api/auth/me")
        assert response.status_code == 200
        assert not response.headers.get_list("set-cookie")
        assert response.json() == initial.json()
        assert dict(client.cookies) == cookies


def test_access_claims_are_short_lived_and_refresh_secret_is_not_stored(sessions):
    device, store, owner = sessions
    client = device()
    response = login(client)
    access = client.cookies["mangrove_access"]
    refresh = client.cookies["mangrove_refresh"]
    claims = jwt.decode(access, settings.jwt_secret, algorithms=["HS256"])
    assert {"typ", "sub", "sid", "iat", "exp"} <= claims.keys()
    assert claims["sub"] == owner["user_id"]
    assert 0 < claims["exp"] - claims["iat"] <= 1800
    assert claims["sid"] in refresh
    assert len(refresh) >= len(claims["sid"]) + 32
    assert {"access_expires_at", "session_expires_at"} <= response.json().keys()
    with sqlite3.connect(store.db_path) as conn:
        persisted = "\n".join(conn.iterdump())
    assert refresh not in persisted
    assert access not in persisted


def test_rotation_replay_revokes_only_its_device(sessions):
    device, _, _ = sessions
    current, other, replay = device(), device(), device()
    initial = login(current).json()
    login(other)
    replay.cookies.update(current.cookies)
    old_refresh = current.cookies["mangrove_refresh"]
    rotated = current.post("/api/auth/refresh")
    assert rotated.status_code == 200
    assert current.cookies["mangrove_refresh"] != old_refresh
    assert rotated.json()["session_expires_at"] == initial["session_expires_at"]
    assert replay.post("/api/auth/refresh").status_code == 401
    assert current.get("/api/auth/me").status_code == 401
    assert current.post("/api/auth/refresh").status_code == 401
    assert other.get("/api/auth/me").status_code == 200


def test_random_refresh_secret_cannot_revoke_known_device(sessions):
    device, _, _ = sessions
    current, attacker = device(), device()
    login(current)
    refresh = current.cookies["mangrove_refresh"]
    claims = jwt.decode(current.cookies["mangrove_access"], settings.jwt_secret, algorithms=["HS256"])
    sid = claims["sid"]
    assert refresh.startswith(sid)
    # 保留真实设备编号与分隔符，仅替换秘密，不能被当成确定历史重放。
    forged = refresh[:len(sid) + 1] + secrets.token_urlsafe(48)
    attacker.cookies.set("mangrove_refresh", forged, path="/api/auth")
    assert attacker.post("/api/auth/refresh").status_code == 401
    assert current.get("/api/auth/me").status_code == 200
    assert current.post("/api/auth/refresh").status_code == 200


def test_concurrent_refresh_has_one_winner_and_replay_revokes_session(sessions):
    device, _, _ = sessions
    source, first, second = device(), device(), device()
    login(source)
    for client in (first, second):
        client.cookies.update(source.cookies)
    barrier = Barrier(2)

    def refresh(client):
        barrier.wait(timeout=3)
        return client.post("/api/auth/refresh").status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(refresh, (first, second)))
    assert sorted(outcomes) == [200, 401]
    assert source.get("/api/auth/me").status_code == 401
    assert first.get("/api/auth/me").status_code == 401
    assert second.get("/api/auth/me").status_code == 401


def test_password_change_revokes_all_devices_and_requires_current_password(sessions):
    device, _, _ = sessions
    first, second = device(), device()
    login(first)
    login(second)
    assert first.post("/api/auth/password", json={
        "current_password": "wrong-password", "new_password": "synthetic-new-password",
    }).status_code in (400, 401, 403)
    assert second.get("/api/auth/me").status_code == 200
    assert first.post("/api/auth/password", json={
        "current_password": PASSWORD, "new_password": "synthetic-new-password",
    }).status_code in (200, 204)
    for client in (first, second):
        assert client.get("/api/auth/me").status_code == 401
        assert client.post("/api/auth/refresh").status_code == 401
    assert first.post("/api/auth/login", json={"username": "synthetic-owner", "password": PASSWORD}).status_code == 401
    login(first, password="synthetic-new-password")


@pytest.mark.parametrize("change", [{"password": "synthetic-reset-password"}, {"disabled": True}, {"pending": True}])
def test_admin_security_change_revokes_all_owner_devices(sessions, change):
    device, store, owner = sessions
    store.create_user("synthetic-admin", auth.hash_password(PASSWORD), role="super_admin", pending=False)
    admin, first, second = device(), device(), device()
    login(admin, "synthetic-admin")
    login(first)
    login(second)
    assert admin.patch(f"/api/admin/users/{owner['user_id']}", json=change).status_code == 200
    for client in (first, second):
        assert client.get("/api/auth/me").status_code == 401
        assert client.post("/api/auth/refresh").status_code == 401
    if "disabled" in change or "pending" in change:
        assert admin.patch(f"/api/admin/users/{owner['user_id']}", json={key: False for key in change}).status_code == 200
        assert first.get("/api/auth/me").status_code == 401
        login(first)


@pytest.mark.parametrize("headers", [{"X-Mangrove-CSRF": ""}, {"Origin": "null"}, {"Origin": "https://untrusted.invalid"}])
def test_browser_write_rejects_missing_csrf_or_untrusted_origin(sessions, headers):
    device, _, _ = sessions
    client = device()
    assert client.post("/api/auth/login", headers=headers, json={"username": "synthetic-owner", "password": PASSWORD}).status_code == 403
    login(client)
    assert client.post("/api/auth/logout", headers=headers).status_code == 403
    assert client.get("/api/auth/me").status_code == 200


def test_legacy_jwt_is_rejected_and_expired_access_is_distinguished(sessions):
    device, _, owner = sessions
    client = device()
    legacy = jwt.encode({"sub": owner["user_id"], "iat": int(time.time()), "exp": int(time.time()) + 600}, settings.jwt_secret, algorithm="HS256")
    rejected = client.get("/api/auth/me", headers={"Authorization": f"Bearer {legacy}"})
    assert rejected.status_code == 401
    assert rejected.headers["X-Mangrove-Auth"] == "session-invalid"
    login(client)
    claims = jwt.decode(client.cookies["mangrove_access"], settings.jwt_secret, algorithms=["HS256"])
    claims.update(iat=int(time.time()) - 1801, exp=int(time.time()) - 1)
    expired = jwt.encode(claims, settings.jwt_secret, algorithm="HS256")
    client.cookies.clear()
    client.cookies.set("mangrove_access", expired, path="/api")
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.headers["X-Mangrove-Auth"] == "access-expired"


def test_logout_with_expired_access_uses_refresh_proof(sessions):
    device, _, _ = sessions
    client, copied = device(), device()
    login(client)
    copied.cookies.update(client.cookies)
    refresh = client.cookies["mangrove_refresh"]
    claims = jwt.decode(client.cookies["mangrove_access"], settings.jwt_secret, algorithms=["HS256"])
    claims.update(iat=int(time.time()) - 1801, exp=int(time.time()) - 1)
    client.cookies.clear()
    client.cookies.set("mangrove_access", jwt.encode(claims, settings.jwt_secret, algorithm="HS256"), path="/api")
    client.cookies.set("mangrove_refresh", refresh, path="/api/auth")
    assert client.post("/api/auth/logout").status_code in (200, 204)
    assert copied.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("change", [{"password": "synthetic-reset-password"}, {"disabled": True}, {"pending": True}])
def test_password_verified_before_admin_reset_cannot_create_late_session(sessions, monkeypatch, change):
    device, store, owner = sessions
    store.create_user("synthetic-admin", auth.hash_password(PASSWORD), role="super_admin", pending=False)
    admin, late = device(), device()
    login(admin, "synthetic-admin")
    verified, release = Event(), Event()
    original = auth_routes.verify_password

    def verify(password, encoded):
        result = original(password, encoded)
        if result:
            verified.set()
            assert release.wait(5), "测试必须释放已完成密码验证的登录"
        return result

    monkeypatch.setattr(auth_routes, "verify_password", verify)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(late.post, "/api/auth/login", json={"username": "synthetic-owner", "password": PASSWORD})
        try:
            assert verified.wait(3)
            assert admin.patch(f"/api/admin/users/{owner['user_id']}", json=change).status_code == 200
        finally:
            release.set()
        response = future.result(timeout=3)
    assert response.status_code == 401
    assert not response.headers.get_list("set-cookie")
    assert late.get("/api/auth/me").status_code == 401


def test_access_and_absolute_expiry_boundaries(sessions, monkeypatch):
    device, _, _ = sessions
    client = device()
    issued = login(client)
    initial = issued.json()
    claims = jwt.decode(client.cookies[auth.ACCESS_COOKIE], settings.jwt_secret, algorithms=["HS256"])
    assert initial["session_expires_at"] - claims["iat"] == auth.SESSION_SECONDS
    cookies = SimpleCookie()
    for header in issued.headers.get_list("set-cookie"):
        cookies.load(header)
    assert int(cookies[auth.ACCESS_COOKIE]["max-age"]) == auth.SESSION_SECONDS
    monkeypatch.setattr(auth.time, "time", lambda: initial["access_expires_at"])
    expired = client.get("/api/auth/me")
    assert expired.status_code == 401
    assert expired.headers["X-Mangrove-Auth"] == "access-expired"
    monkeypatch.setattr(auth.time, "time", lambda: initial["session_expires_at"] - 1)
    rotated = client.post("/api/auth/refresh")
    assert rotated.status_code == 200
    assert rotated.json()["access_expires_at"] == initial["session_expires_at"]
    assert rotated.json()["session_expires_at"] == initial["session_expires_at"]
    copied = dict(client.cookies)
    monkeypatch.setattr(auth.time, "time", lambda: initial["session_expires_at"])
    # 模拟复制的凭证继续发送，不能仅靠浏览器到期删除 Cookie 通过此门。
    client.cookies.clear()
    client.cookies.set(auth.ACCESS_COOKIE, copied[auth.ACCESS_COOKIE], path="/api")
    client.cookies.set(auth.REFRESH_COOKIE, copied[auth.REFRESH_COOKIE], path="/api/auth")
    expired = client.get("/api/auth/me")
    assert expired.status_code == 401
    assert expired.headers["X-Mangrove-Auth"] == "session-invalid"
    assert client.post("/api/auth/refresh").status_code == 401


def test_overlong_access_is_invalid_even_with_valid_session(sessions):
    device, _, _ = sessions
    client = device()
    login(client)
    claims = jwt.decode(client.cookies[auth.ACCESS_COOKIE], settings.jwt_secret, algorithms=["HS256"])
    claims["exp"] = claims["iat"] + 1801
    client.cookies.clear()
    client.cookies.set(auth.ACCESS_COOKIE, jwt.encode(claims, settings.jwt_secret, algorithm="HS256"), path="/api")
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.headers["X-Mangrove-Auth"] == "session-invalid"


@pytest.mark.parametrize("path", ["me", "refresh", "logout"])
def test_owner_header_cannot_act_on_another_cookie_owner(sessions, path):
    device, store, owner = sessions
    client = device()
    login(client)
    before = dict(client.cookies)
    response = client.request("GET" if path == "me" else "POST", f"/api/auth/{path}", headers={"X-Mangrove-Owner": "synthetic-other-owner"})
    assert response.status_code == 401
    assert not response.headers.get_list("set-cookie")
    assert dict(client.cookies) == before
    assert client.get("/api/auth/me", headers={"X-Mangrove-Owner": owner["user_id"]}).status_code == 200
    assert client.post("/api/auth/refresh").status_code == 200


@pytest.mark.parametrize("path", ["refresh", "logout"])
def test_auth_limit_commits_audit_without_rotating_revoking_or_clearing(sessions, path):
    device, store, owner = sessions
    client = device()
    login(client)
    claims = jwt.decode(client.cookies[auth.ACCESS_COOKIE], settings.jwt_secret, algorithms=["HS256"])
    before = store.get_platform_session(claims["sid"])
    cookies = dict(client.cookies)
    for _ in range(120):
        assert store.platform_request_limit(owner_user_id=owner["user_id"], control=False, now=time.time()) == 0
    response = client.post(f"/api/auth/{path}")
    assert response.status_code == 429
    assert 0 < int(response.headers["Retry-After"]) <= 60
    assert not response.headers.get_list("set-cookie")
    assert dict(client.cookies) == cookies
    assert store.get_platform_session(claims["sid"]) == before
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM platform_spent_refresh").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM platform_security_audit WHERE action='rate_limit_threshold' AND actor_user_id=?", (owner["user_id"],)).fetchone()[0] == 1


@pytest.mark.parametrize("path", ["refresh", "logout"])
def test_valid_refresh_proof_consumes_one_ordinary_request(sessions, path):
    device, store, owner = sessions
    client, other = device(), device()
    login(client)
    login(other)
    refresh = client.cookies[auth.REFRESH_COOKIE]
    client.cookies.clear()
    client.cookies.set(auth.REFRESH_COOKIE, refresh, path="/api/auth")
    for _ in range(119):
        assert store.platform_request_limit(owner_user_id=owner["user_id"], control=False, now=time.time()) == 0
    assert client.post(f"/api/auth/{path}").status_code == 200
    assert other.get("/api/auth/me").status_code == 429


def test_admin_revocation_audit_records_actor_not_target(sessions):
    device, store, owner = sessions
    actor = store.create_user("synthetic-admin", auth.hash_password(PASSWORD), role="super_admin", pending=False)
    admin, client = device(), device()
    login(admin, "synthetic-admin")
    login(client)
    assert admin.patch(f"/api/admin/users/{owner['user_id']}", json={"disabled": True}).status_code == 200
    with sqlite3.connect(store.db_path) as conn:
        actors = conn.execute("SELECT actor_user_id FROM platform_security_audit WHERE action='session_revoked' AND reason='account_changed'").fetchall()
    assert actors == [(actor["user_id"],)]


@pytest.mark.parametrize("change", [{"password_hash": "synthetic-unusable-hash"}, {"disabled": True}, {"pending": True}])
def test_account_change_and_revocation_roll_back_together(sessions, change):
    device, store, owner = sessions
    client = device()
    login(client)
    previous = store.get_user(owner["user_id"])
    # 在会话撤销处强制失败，证明用户变更不能绕过同一事务提交。
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("CREATE TRIGGER reject_session_revoke BEFORE UPDATE ON platform_login_sessions BEGIN SELECT RAISE(ABORT, 'synthetic rollback'); END")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic rollback"):
        store.update_user(owner["user_id"], **change)
    assert store.get_user(owner["user_id"]) == previous
    assert client.get("/api/auth/me").status_code == 200


def test_password_verified_before_logout_cannot_change_password(sessions, monkeypatch):
    device, store, owner = sessions
    changing, exiting = device(), device()
    login(changing)
    exiting.cookies.update(changing.cookies)
    before = store.get_user(owner["user_id"])["password_hash"]
    verified, release = Event(), Event()
    original = auth_routes.verify_password

    def verify(password, encoded):
        result = original(password, encoded)
        verified.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(auth_routes, "verify_password", verify)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(changing.post, "/api/auth/password", json={"current_password": PASSWORD, "new_password": "synthetic-new-password"})
        try:
            assert verified.wait(3)
            assert exiting.post("/api/auth/logout").status_code == 200
        finally:
            release.set()
        assert future.result(timeout=3).status_code == 401
    assert store.get_user(owner["user_id"])["password_hash"] == before


@pytest.mark.parametrize("action", ["logout", "logout-all", "disabled", "password"])
def test_refresh_interleaved_with_revocation_never_restores_session(sessions, action):
    device, store, owner = sessions
    client = device()
    login(client)
    claims = jwt.decode(client.cookies[auth.ACCESS_COOKIE], settings.jwt_secret, algorithms=["HS256"])
    sid, digest = auth.refresh_identity(client.cookies[auth.REFRESH_COOKIE])
    barrier = Barrier(2)

    def refresh():
        barrier.wait(timeout=3)
        return store.platform_rotate_refresh(session_id=sid, digest=digest, next_digest="synthetic-next-digest", now=time.time(), access_expires_at=time.time() + 1800)

    def revoke():
        barrier.wait(timeout=3)
        if action == "logout":
            store.platform_logout(session_id=sid, owner_user_id=owner["user_id"], now=time.time())
        elif action == "logout-all":
            store.platform_logout_all(owner["user_id"], now=time.time())
        elif action == "disabled":
            store.update_user(owner["user_id"], disabled=True)
        else:
            store.update_user(owner["user_id"], password_hash="synthetic-new-hash")

    with ThreadPoolExecutor(max_workers=2) as executor:
        rotated, revoked = executor.submit(refresh), executor.submit(revoke)
        rotated.result(timeout=5)
        revoked.result(timeout=5)
    assert store.get_platform_session(claims["sid"])["revoked_at"] is not None
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/refresh").status_code == 401
