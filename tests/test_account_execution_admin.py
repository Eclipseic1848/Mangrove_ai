"""管理状态投影使用真实认证路由与虚构账号。"""
import asyncio

from src.api import auth
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
