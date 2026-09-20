"""概览只读聚合：真实数据库、Owner 隔离、全量统计与分页。"""
from datetime import datetime, timedelta
from src.timezone import now as beijing_now
import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import get_current_user
from src.api.routes import overview
from src.api.store import WebUIStore
from tests.database_migration_helpers import migrated_webui_database


def test_activity_counts_all_owned_tasks_and_filters_before_pagination(tmp_path, monkeypatch):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "webui.db")))
    owner = store.create_user("owner", "unused", role="super_admin")
    other = store.create_user("other", "unused")
    # 合成历史记录覆盖默认 100/500 条上限，不触发任务执行。
    now = beijing_now().isoformat(timespec="seconds")
    with store._conn() as conn:
        for index in range(505):
            conn.execute(
                "INSERT INTO semantic_workspace_tasks "
                "(task_id,user_id,title,objective_text,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (f"own-{index}", owner["user_id"], f"任务 {index}", "不可出现在概览的正文", "running", now, now),
            )
        conn.execute("UPDATE semantic_workspace_tasks SET status='paused' WHERE task_id='own-0'")
        conn.execute("UPDATE semantic_workspace_tasks SET status='completed' WHERE task_id='own-1'")
        conn.execute("INSERT INTO semantic_workspace_events (event_id,task_id,user_id,sequence,stage,event_type,summary,created_at) VALUES ('finished','own-1',?,1,'deliver','task_completed','合成交付',?)", (owner["user_id"], now))
        conn.execute("UPDATE semantic_workspace_tasks SET deleted_at=? WHERE task_id='own-2'", (now,))
        conn.execute("UPDATE semantic_workspace_tasks SET user_id=? WHERE task_id='own-3'", (other["user_id"],))
    monkeypatch.setattr(overview, "get_store", lambda: store)
    app = FastAPI()
    app.include_router(overview.router)
    app.dependency_overrides[get_current_user] = lambda: owner
    client = TestClient(app)
    response = client.get("/api/overview/activity?filter=attention&limit=2")
    assert response.status_code == 200
    body = response.json()
    assert body["stats"] == {"active": 501, "attention": 1, "completed": 1}
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == ["own-0"]
    assert "objective_text" not in response.text
    assert "不可出现在概览" not in response.text
    assert client.get("/api/overview/activity?filter=active&offset=500&limit=2").json()["total"] == 501
    assert len(client.get("/api/overview/activity?filter=active&offset=500&limit=2").json()["items"]) == 1
    assert client.get("/api/overview/activity?filter=unknown").status_code == 422
    hundred = client.get("/api/overview/activity?filter=active&limit=100")
    assert hundred.status_code == 200
    assert len(hundred.json()["items"]) == 100
    assert client.get("/api/overview/activity?limit=101").status_code == 422
    assert client.get("/api/overview/activity?limit=1000").status_code == 422


def test_services_only_exposes_existing_evidence_without_logs_or_secrets(tmp_path, monkeypatch):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "webui.db")))
    store.cookie_health_set("mc_cookie_xhs", "valid", "secret raw log", "private-user")
    monkeypatch.setattr(overview, "get_store", lambda: store)
    monkeypatch.setattr(overview.settings, "slack_webhook_url", "")
    monkeypatch.setattr(overview.settings, "slack_bot_token", "secret-token")
    monkeypatch.setattr(overview.settings, "slack_channel_id", "synthetic-channel")
    app = FastAPI()
    app.include_router(overview.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "owner", "role": "user"}
    response = TestClient(app).get("/api/overview/services")
    assert response.status_code == 200
    assert "secret" not in response.text and "private-user" not in response.text
    cookies = {item["platform"]: item for item in response.json()["cookies"]}
    assert cookies["xiaohongshu"]["status"] == "valid"
    assert cookies["weibo"]["status"] == "unknown"
    slack = next(item for item in response.json()["services"] if item["key"] == "slack")
    assert slack["configured"] is True
    assert "verified" not in slack


@pytest.mark.parametrize("provider,expected", [("auto", True), ("ddgs", True), ("tavily", False), ("searxng", False)])
def test_search_summary_respects_selected_backend(tmp_path, monkeypatch, provider, expected):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "webui.db")))
    monkeypatch.setattr(overview, "get_store", lambda: store)
    for key in ("tavily_api_key", "searxng_base_url", "anysearch_api_key"):
        monkeypatch.setattr(overview.settings, key, "")
    monkeypatch.setattr(overview.settings, "search_provider", provider)
    result = overview.service_summary(user={"user_id": "owner"})
    assert result["services"][0]["configured"] is expected


def test_schedule_summary_keeps_owner_and_timezone_without_body(tmp_path, monkeypatch):
    from src.scheduler.store import ScheduleStore
    from tests.database_migration_helpers import migrated_profile_database
    schedule_store = ScheduleStore(str(migrated_profile_database(tmp_path / "schedules.db", profile="scheduler")))
    now = datetime.now().replace(microsecond=0)
    with schedule_store._conn() as conn:
        for owner in ("mine", "other"):
            conn.execute("INSERT INTO scheduled_tasks (task_id,owner_user_id,name,user_input,trigger_type,status,run_count,created_at,next_run_at) VALUES (?,?,?,'private body','interval','active',0,?,?)", (owner, owner, "计划", now.isoformat(), now.isoformat()))
    monkeypatch.setattr(overview, "get_schedule_store", lambda: schedule_store)
    app = FastAPI(); app.include_router(overview.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "mine"}
    response = TestClient(app).get("/api/overview/schedules")
    assert response.status_code == 200
    assert len(response.json()) == 1
    item = response.json()[0]
    assert item["task_id"] == "mine"
    assert item["next_run_at"] == now.isoformat()
    assert datetime.fromisoformat(item["next_run_at"]).tzinfo is None
    assert "private body" not in response.text


def test_recent_completion_uses_event_or_reply_time_not_rename(tmp_path, monkeypatch):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "webui.db")))
    owner = store.create_user("owner", "unused")
    recent = store.create_conversation(owner["user_id"], "最近完成")
    old = store.create_conversation(owner["user_id"], "旧会话刚改名")
    for conversation in (recent, old):
        store.add_message(conversation["conv_id"], "assistant", "合成结果")
    with store._conn() as conn:
        conn.execute("UPDATE messages SET created_at=? WHERE conv_id=?", ((datetime.now() - timedelta(days=30)).isoformat(), old["conv_id"]))
    store.rename_conversation(old["conv_id"], "今天重命名")
    monkeypatch.setattr(overview, "get_store", lambda: store)
    result = overview.activity(user=owner, filter="completed", offset=0, limit=6)
    assert result["stats"]["completed"] == 1
    assert [item["id"] for item in result["items"]] == [recent["conv_id"]]
    assert all("completed_at" not in item for item in store.list_chat_history(owner["user_id"]))
