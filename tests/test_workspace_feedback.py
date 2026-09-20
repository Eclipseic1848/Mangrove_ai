"""反馈公开接口、Owner/版本绑定以及管理员审计读取。"""
import json
import sqlite3
import shutil
from contextlib import closing

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import get_current_user
from src.api.routes import semantic_workspace as routes
from src.api.store import WebUIStore
from tests.database_migration_helpers import migrated_webui_database


@pytest.mark.parametrize("keep_feedback", [False, True])
def test_upgrade_keeps_existing_feedback_audit_and_id_highwater(tmp_path, keep_feedback):
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL
    from src import database_migrations as migrations
    database = tmp_path / "old.db"
    engine = create_engine(URL.create("sqlite", database=str(database)))
    with engine.begin() as conn:
        config = migrations._alembic_config(conn)
        config.attributes["backup_sha256"] = "a" * 64
        migrations.command.upgrade(config, "webui_0020")
    engine.dispose()
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("INSERT INTO message_feedback(id,message_id,conv_id,user_id,rating,created_at) VALUES (100,1,'conv','owner','up','now')")
        conn.execute("DELETE FROM message_feedback")
        if keep_feedback:
            conn.execute("INSERT INTO message_feedback(id,message_id,conv_id,user_id,rating,created_at,comment,status,admin_note) VALUES (1,1,'conv','owner','down','before','原反馈','resolved','原备注')")
        conn.execute("INSERT INTO feedback_content_access(event_id,actor_id,actor_role,idempotency_key,reason,action,feedback_id,message_id,conv_id,owner_id,request_digest,response_digest,content_bytes,truncated,result,created_at) VALUES ('event','admin','admin','key','审查反馈内容','feedback_content_read',100,1,'conv','owner','a','b',0,0,'success','now')")
        conn.commit()
        audit = conn.execute("SELECT * FROM feedback_content_access").fetchall()
        feedback = conn.execute("SELECT * FROM message_feedback").fetchall()
    receipt = migrations.apply_migrations(migrations.DatabaseTarget("webui", database), tmp_path / "backup.db")
    with closing(sqlite3.connect(database)) as conn:
        assert [row[:17] for row in conn.execute("SELECT * FROM feedback_content_access")] == audit
        assert [row[:10] for row in conn.execute("SELECT * FROM message_feedback")] == feedback
        conn.execute("INSERT INTO message_feedback(message_id,conv_id,user_id,rating,created_at) VALUES (2,'conv','owner','up','now')")
        assert conn.execute("SELECT MAX(id) FROM message_feedback").fetchone()[0] == 101
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM feedback_content_access")
        conn.rollback()
    assert migrations.apply_migrations(migrations.DatabaseTarget("webui", database), tmp_path / "replay.db").applied_revisions == ()
    restored = tmp_path / "restored.db"
    shutil.copyfile(receipt.backup_path, restored)
    assert migrations.verify_restored_copy(receipt.receipt_path, restored).integrity_check == "ok"


@pytest.fixture
def feedback_api(tmp_path, monkeypatch):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "webui.db")))
    owner = store.create_user("owner", "unused")
    other = store.create_user("other", "unused")
    admin = store.create_user("admin", "unused", role="admin")
    with store._conn() as conn:
        conn.execute("INSERT INTO semantic_workspace_tasks(task_id,user_id,title,objective_text,status,created_at,updated_at) VALUES ('task',?,'title','QUESTION','completed','now','now')", (owner["user_id"],))
        conn.execute("INSERT INTO semantic_workspace_revisions(task_id,revision,user_id,objective_text,status,summary,created_at,updated_at) VALUES ('task',1,?,'QUESTION','completed','ANSWER','now','now')", (owner["user_id"],))
        conn.execute("INSERT INTO conversation_raw_turns(turn_id,owner_id,task_id,revision,text,created_at) VALUES ('turn',?,'task',1,'FOLLOWUP','now')", (owner["user_id"],))
        conn.execute("INSERT INTO conversation_steering_results(result_id,owner_id,task_id,turn_id,payload_json,created_at) VALUES ('reply',?,'task','turn',?,'now')", (owner["user_id"], json.dumps({"revision": 1, "answer": "REPLY"})))
    monkeypatch.setattr(routes, "get_store", lambda: store)
    app = FastAPI(); app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: owner
    return TestClient(app), app, store, owner, other, admin


@pytest.mark.parametrize("result_id,answer", [("delivery", "ANSWER"), ("reply", "REPLY")])
def test_feedback_persists_cancels_and_enters_audited_management(feedback_api, result_id, answer):
    client, app, store, owner, other, admin = feedback_api
    path = "/api/semantic-workspace/tasks/task/feedback"
    body = {"revision": 1, "result_id": result_id, "rating": "up"}
    assert client.post(path, json=body).status_code == 200
    assert client.post(path, json=body).status_code == 200
    assert store.feedback_list()["total"] == 1
    body.update(rating="down", reasons=["格式错误"], comment="COMMENT")
    assert client.post(path, json=body).status_code == 200
    assert client.get(path, params={"revision": 1, "result_id": result_id}).json()["feedback"]["current"]["rating"] == "down"
    listing = store.feedback_list()
    assert listing["items"][0]["content_available"] is True
    assert "ANSWER" not in json.dumps(listing) and "COMMENT" not in json.dumps(listing)
    item_id = listing["items"][0]["id"]
    audited = store.audit_feedback_content(item_id, actor_id=admin["user_id"], reason="排查用户反馈问题", idempotency_key="audit")
    assert audited["content"]["answer"] == answer
    assert audited["content"]["question"] == ("FOLLOWUP" if result_id == "reply" else "QUESTION")
    assert store.audit_feedback_content(item_id, actor_id=admin["user_id"], reason="排查用户反馈问题", idempotency_key="audit") == audited
    app.dependency_overrides[get_current_user] = lambda: other
    assert client.get(path, params={"revision": 1}).status_code == 404
    assert client.post(path, json=body).status_code == 404
    app.dependency_overrides[get_current_user] = lambda: owner
    assert client.post(path, json={**body, "revision": 2}).status_code == 404
    assert client.post(path, json={**body, "result_id": "missing"}).status_code == 404
    assert client.post(path, json={**body, "rating": "bad"}).status_code == 422
    assert client.post(path, json={**body, "rating": None}).status_code == 200
    assert client.get(path, params={"revision": 1, "result_id": result_id}).json()["feedback"]["current"] is None
    with store._conn() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM feedback_content_access")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO message_feedback(task_id,result_id,user_id,rating,created_at) VALUES ('task','delivery',?,'up','now')", (owner["user_id"],))


def test_workspace_audit_bounds_large_utf8_content(feedback_api):
    from src.api.workspace_feedback import target
    from src.api.feedback_audit import CONTENT_LIMIT
    client, _, store, owner, _, admin = feedback_api
    with store._conn() as conn:
        conn.execute("UPDATE semantic_workspace_revisions SET summary=? WHERE task_id='task'", ("测试正文" * CONTENT_LIMIT,))
        assert len(target(conn, owner["user_id"], "task", 1, "delivery")["answer"]) == CONTENT_LIMIT + 1
    assert client.post("/api/semantic-workspace/tasks/task/feedback", json={"revision": 1, "rating": "up"}).status_code == 200
    item = store.feedback_list()["items"][0]
    result = store.audit_feedback_content(item["id"], actor_id=admin["user_id"], reason="检查大正文边界", idempotency_key="large")
    assert result["truncated"] is True
    assert result["content_bytes"] < CONTENT_LIMIT


def test_workspace_audit_keeps_hidden_internal_content_hidden(feedback_api):
    client, _, store, _, _, admin = feedback_api
    with store._conn() as conn:
        conn.execute("UPDATE semantic_workspace_revisions SET summary='<analysis>internal only</analysis>' WHERE task_id='task'")
    assert client.post("/api/semantic-workspace/tasks/task/feedback", json={"revision": 1, "rating": "down"}).status_code == 200
    item = store.feedback_list()["items"][0]
    result = store.audit_feedback_content(item["id"], actor_id=admin["user_id"], reason="核查隐藏回答内容", idempotency_key="hidden")
    assert "internal only" not in result["content"]["answer"]
