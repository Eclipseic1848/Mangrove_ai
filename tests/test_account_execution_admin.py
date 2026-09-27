"""管理状态投影使用真实认证路由与虚构账号。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from src.api import auth
from src.api.routes import admin_routes
from tests.test_platform_login_sessions import PASSWORD, login, sessions


def test_disable_returns_persistent_processing_and_reenable_keeps_same_hold(sessions):
    device, store, owner = sessions
    store.create_user("synthetic-admin", auth.hash_password(PASSWORD), role="super_admin")
    client = device()
    login(client, "synthetic-admin")
    url = f"/api/admin/users/{owner['user_id']}"
    response = client.patch(url, json={"disabled": True})
    assert response.status_code == 200
    assert response.json().get("user"), "账号修改回执需要区分后台停止状态"
    hold = response.json()["user"]["execution_hold"]
    assert hold["status"] == "processing"
    assert hold["affected_count"] == hold["pending_count"] == 0
    assert set(hold) == {"operation_id", "generation", "status", "affected_count", "pending_count", "error_code", "retryable", "updated_at"}
    enabled = client.patch(url, json={"disabled": False}).json()["user"]
    assert enabled["execution_hold"]["operation_id"] == hold["operation_id"]
    restored = next(row for row in client.get("/api/admin/users").json()["users"] if row["user_id"] == owner["user_id"])
    assert restored["execution_hold"] == enabled["execution_hold"]
    assert "password_hash" not in restored and "execution_generation" not in restored


def test_retry_cannot_manage_equal_rank_or_another_owners_operation(sessions):
    device, store, owner = sessions
    actor = store.create_user("synthetic-admin", auth.hash_password(PASSWORD), role="admin")
    peer = store.create_user("synthetic-peer", "synthetic-hash", role="admin")
    client = device()
    login(client, "synthetic-admin")
    assert client.post(f"/api/admin/users/{peer['user_id']}/execution-hold/retry", json={"operation_id": "synthetic-operation"}).status_code == 403
    assert client.post(f"/api/admin/users/{owner['user_id']}/execution-hold/retry", json={"operation_id": "synthetic-operation"}).status_code == 404


@pytest.mark.parametrize("change", ["target_promoted", "actor_demoted", "actor_disabled", "actor_pending"])
def test_password_update_rechecks_roles_at_commit(sessions, monkeypatch, change):
    device, store, owner = sessions
    actor = store.create_user("synthetic-admin", auth.hash_password(PASSWORD), role="admin")
    store.create_user("synthetic-super", auth.hash_password(PASSWORD), role="super_admin")
    client, supervisor = device(), device()
    login(client, "synthetic-admin")
    login(supervisor, "synthetic-super")
    entered, release = Event(), Event()
    original_hash = admin_routes.hash_password

    def paused_hash(password):
        entered.set()
        assert release.wait(10), "并发角色变更未完成"
        return original_hash(password)

    monkeypatch.setattr(admin_routes, "hash_password", paused_hash)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.patch, f"/api/admin/users/{owner['user_id']}", json={"password": "synthetic-changed-password"})
        try:
            assert entered.wait(10), "请求未进入哈希窗口"
            target = owner if change == "target_promoted" else actor
            changes = {"role": "admin"} if change == "target_promoted" else {
                "actor_demoted": {"role": "user"}, "actor_disabled": {"disabled": True},
                "actor_pending": {"pending": True},
            }[change]
            assert supervisor.patch(f"/api/admin/users/{target['user_id']}", json=changes).status_code == 200
        finally:
            release.set()
        assert future.result(timeout=10).status_code == 403
    assert auth.verify_password(PASSWORD, store.get_user(owner["user_id"])["password_hash"])


@pytest.mark.parametrize("operation", ["delete", "retry"])
def test_account_commands_recheck_target_role_at_commit(sessions, monkeypatch, operation):
    device, store, owner = sessions
    store.create_user("synthetic-admin", auth.hash_password(PASSWORD), role="admin")
    client = device()
    login(client, "synthetic-admin")
    store.update_user(owner["user_id"], disabled=True)
    hold = store.admin_user(owner["user_id"])["execution_hold"]
    method = "delete_user" if operation == "delete" else "retry_account_execution_hold"
    original = getattr(store, method)

    def changed_role(*args, **kwargs):
        # 请求已过路由预检，另一个合法事务在写入前提升目标角色。
        store.update_user(owner["user_id"], role="admin")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, method, changed_role)
    url = f"/api/admin/users/{owner['user_id']}"
    response = client.delete(url) if operation == "delete" else client.post(
        url + "/execution-hold/retry", json={"operation_id": hold["operation_id"]},
    )
    assert response.status_code == 403
    assert store.get_user(owner["user_id"])["role"] == "admin"


def test_create_account_rechecks_assignable_role(sessions, monkeypatch):
    device, store, _ = sessions
    actor = store.create_user("synthetic-super", auth.hash_password(PASSWORD), role="super_admin")
    client = device()
    login(client, "synthetic-super")
    original_hash = admin_routes.hash_password

    def changed_role(password):
        store.update_user(actor["user_id"], role="admin")
        return original_hash(password)

    monkeypatch.setattr(admin_routes, "hash_password", changed_role)
    response = client.post("/api/admin/users", json={"username": "synthetic-new-admin", "password": PASSWORD, "role": "admin"})
    assert response.status_code == 403
    assert store.get_user_by_name("synthetic-new-admin") is None
