"""会话历史删除保护在途任务，并保留现有 Owner 隔离。"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import auth
from src.api.routes import chat, conversations
from src.api.store import WebUIStore
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture
def deletion_api(tmp_path, monkeypatch):
    database = migrated_webui_database(tmp_path / "conversations.db")
    for owner_id in ("owner-a", "owner-b"):
        seed_execution_owner(database, owner_id)
    store = WebUIStore(str(database))
    monkeypatch.setattr(auth, "_store", store)
    monkeypatch.setattr(chat, "_RUNNING", {})
    app = FastAPI()
    app.include_router(conversations.router)
    app.dependency_overrides[auth.get_current_user] = lambda: {"user_id": "owner-a"}
    with TestClient(app) as client:
        yield client, store


def test_running_conversation_cannot_be_deleted_or_cancelled(deletion_api, monkeypatch):
    client, store = deletion_api
    conv_id = store.create_conversation("owner-a")["conv_id"]
    store.add_message(conv_id, "user", "保留原任务")
    cancelled = []
    task = SimpleNamespace(done=lambda: False, cancel=lambda: cancelled.append(True))
    monkeypatch.setitem(chat._RUNNING, f"owner-a:{conv_id}", task)

    response = client.delete(f"/api/conversations/{conv_id}")

    assert response.status_code == 409
    assert "先停止" in response.json()["detail"]
    assert store.get_conversation(conv_id) is not None
    assert [message["content"] for message in store.list_messages(conv_id)] == ["保留原任务"]
    assert chat._RUNNING[f"owner-a:{conv_id}"] is task
    assert cancelled == []


@pytest.mark.parametrize("finished_task", [False, True])
def test_finished_conversation_history_can_be_deleted(deletion_api, monkeypatch, finished_task):
    client, store = deletion_api
    conv_id = store.create_conversation("owner-a")["conv_id"]
    store.add_message(conv_id, "assistant", "已结束的结果")
    if finished_task:
        monkeypatch.setitem(chat._RUNNING, f"owner-a:{conv_id}", SimpleNamespace(done=lambda: True))

    response = client.delete(f"/api/conversations/{conv_id}")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert store.get_conversation(conv_id) is None
    assert store.list_messages(conv_id) == []


def test_foreign_conversation_is_hidden_before_runtime_lookup(deletion_api, monkeypatch):
    client, store = deletion_api
    conv_id = store.create_conversation("owner-b")["conv_id"]
    store.add_message(conv_id, "assistant", "另一账号的记录")

    def forbidden_lookup():
        pytest.fail("无权限的会话不应检查运行态")

    monkeypatch.setitem(chat._RUNNING, f"owner-a:{conv_id}", SimpleNamespace(done=forbidden_lookup))
    monkeypatch.setitem(chat._RUNNING, f"owner-b:{conv_id}", SimpleNamespace(done=forbidden_lookup))

    response = client.delete(f"/api/conversations/{conv_id}")

    assert response.status_code == 404
    assert store.get_conversation(conv_id)["user_id"] == "owner-b"
    assert [message["content"] for message in store.list_messages(conv_id)] == ["另一账号的记录"]
