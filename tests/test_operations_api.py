"""运营公开接口：隔离数据库、角色范围与脱敏，不使用真实账号。"""
from datetime import datetime, timezone, timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.api.auth import get_current_user
from src.api.store import WebUIStore
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture
def platform(tmp_path, monkeypatch):
    from src.api.routes import operations
    store = WebUIStore(str(migrated_webui_database(tmp_path / "webui.db")))
    users = {name: store.create_user(name, "unused", role=role) for name, role in (
        ("member", "user"), ("manager", "admin"), ("peer", "admin"), ("root", "super_admin"))}
    actor = {"user": users["member"]}

    def identity(request: Request):
        request.state.platform_user = actor["user"]
        request.state.platform_claims = {"sid": "synthetic-device-" + actor["user"]["user_id"]}
        return actor["user"]

    monkeypatch.setattr(operations, "get_store", lambda: store)
    app = FastAPI()
    app.include_router(operations.router)
    app.dependency_overrides[get_current_user] = identity
    return TestClient(app), actor, users, store


def period(**values):
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    return {"start": today, "end": today, **values}


def test_scope_is_shared_by_list_stats_details_and_export(platform):
    client, actor, users, _ = platform
    ids = {}
    for name, user in users.items():
        actor["user"] = user
        response = client.post("/api/operations/visits", json={
            "event_id": f"00000000-0000-4000-8000-{len(ids):012d}", "page": "/data-prep", "source": "direct",
        })
        assert response.status_code == 200
        ids[name] = response.json()["event_id"]
    actor["user"] = users["member"]
    assert client.post("/api/operations/events/query", json=period()).status_code == 403
    actor["user"] = users["manager"]
    query = period(kind="visit")
    response = client.post("/api/operations/events/query", json=query)
    assert response.status_code == 200
    assert {row["actor_id"] for row in response.json()["items"]} == {users[x]["user_id"] for x in ("member", "manager")}
    stats = client.post("/api/operations/summary", json=query).json()
    assert stats["pv"] == 2 and stats["uv"] == 2
    assert client.get(f"/api/operations/events/{ids['root']}").status_code == 404
    assert client.post("/api/operations/events/query", json=period(actor_id=users["root"]["user_id"])).json()["total"] == 0
    exported = client.post("/api/operations/export", json={"filters": query, "format": "csv"})
    assert exported.status_code == 200
    assert "member" in exported.text and "peer" not in exported.text and "root" not in exported.text
    actor["user"] = users["root"]
    assert client.post("/api/operations/summary", json=query).json()["pv"] == 4
    assert client.get(f"/api/operations/events/{ids['root']}").status_code == 200


def test_renamed_module_keeps_history_views_and_pagination(platform):
    import json
    from src import operations as ops
    client, actor, users, store = platform
    actor["user"] = users["root"]
    with store._conn() as conn:
        for index in range(12):
            event = ops.begin(conn, kind="visit", module="运营与审计" if index < 6 else "运营审计",
                action="访问页面", actor=users["root"])
            ops.finish(conn, event, actor=users["root"])
        conn.execute("INSERT INTO operations_views VALUES (?,?,?,?)", (users["root"]["user_id"], "legacy-view", "旧筛选",
            json.dumps({"tab": "visit", "filters": period(module="运营与审计")})))
    for name in ("运营与审计", "运营审计"):
        response = client.post("/api/operations/events/query", json=period(module=name, kind="visit"))
        assert response.status_code == 200
        assert response.json()["total"] == 12
        assert len(response.json()["items"]) == 10
        assert {row["module"] for row in response.json()["items"]} == {"运营审计"}
        response = client.post("/api/operations/events/query", json=period(module=name, kind="visit", page_size=100))
        assert len(response.json()["items"]) == 12
    summary = client.post("/api/operations/summary", json=period(module="运营审计", kind="visit")).json()
    assert summary["modules"] == [{"module": "运营审计", "pv": 12, "uv": 1, "actions": 0}]
    assert client.get("/api/operations/views").json()["items"][0]["filters"]["module"] == "运营审计"
    assert client.get(f"/api/operations/events/{event}").json()["module"] == "运营审计"


def test_recording_is_idempotent_and_new_privileged_role_hides_history(platform):
    client, actor, users, store = platform
    payload = {"event_id": "00000000-0000-4000-8000-000000000001", "page": "/data-prep"}
    first = client.post("/api/operations/visits", json=payload).json()
    assert client.post("/api/operations/visits", json=payload).json() == first
    assert client.post("/api/operations/visits", json={**payload, "page": "/settings"}).status_code == 409
    assert client.post("/api/operations/visits", json={**payload, "page": "/data-prep?secret=private"}).status_code == 422
    assert client.post("/api/operations/visits", json={**payload, "body": "private"}).status_code == 422
    actor["user"] = users["manager"]
    assert client.post("/api/operations/summary", json=period(kind="visit")).json()["pv"] == 1
    store.update_user(users["member"]["user_id"], role="admin", actor_user_id=users["root"]["user_id"])
    assert client.post("/api/operations/summary", json=period(kind="visit")).json()["pv"] == 0
    store.update_user(users["manager"]["user_id"], role="user", actor_user_id=users["root"]["user_id"])
    assert client.post("/api/operations/summary", json=period()).status_code == 403


def test_request_audit_records_success_failure_and_unknown_without_body(platform, monkeypatch):
    from src.api.operations_middleware import OperationsMiddleware
    client, actor, users, store = platform
    app = client.app
    app.add_middleware(OperationsMiddleware, store_provider=lambda: store)

    @app.post("/api/templates/synthetic")
    def success(request: Request):
        request.state.platform_user = users["member"]
        return {"private": "不应写入日志的业务正文"}

    @app.delete("/api/templates/synthetic")
    def failure(request: Request):
        from fastapi import HTTPException
        request.state.platform_user = users["member"]
        raise HTTPException(409, "private error body")

    @app.post("/api/templates/interrupted")
    def interrupted(request: Request):
        request.state.platform_user = users["member"]
        raise RuntimeError("synthetic interrupted request")

    assert client.post("/api/templates/synthetic", json={"api_key": "never-log-this"}).status_code == 200
    assert client.delete("/api/templates/synthetic").status_code == 409
    with pytest.raises(RuntimeError, match="synthetic interrupted"):
        client.post("/api/templates/interrupted")
    actor["user"] = users["manager"]
    response = client.post("/api/operations/events/query", json=period(kind="action"))
    assert {row["result"] for row in response.json()["items"]} == {"success", "failure", "unknown"}
    assert "never-log-this" not in response.text and "private" not in response.text
    assert "不应写入" not in response.text


def test_statistics_drilldown_and_user_options_share_scope(platform):
    client, actor, users, _ = platform
    for index, name in enumerate(("member", "member", "peer")):
        actor["user"] = users[name]
        assert client.post("/api/operations/visits", json={
            "event_id": f"00000000-0000-4000-8000-{index:012d}",
            "page": "/data-prep" if index != 1 else "/settings", "source": "direct",
        }).status_code == 200
    actor["user"] = users["manager"]
    stats = client.post("/api/operations/summary", json=period()).json()
    assert stats["pv_per_user"] == 2
    assert sum(point["pv"] for point in stats["trend"]) == 2
    assert {item["module"]: item["pv"] for item in stats["modules"]} == {"任务工作台": 1, "设置": 1}
    assert stats["active_users"] == {"day": 1, "week": 1, "month": 1}
    assert stats["activity_distribution"] == {"high": 0, "active": 1, "unseen": 1, "threshold": 10}
    assert stats["comparison"]["previous"]["available"] is False
    assert stats["comparison"]["year"]["available"] is False
    assert stats["coverage"]["started_at"] and stats["coverage"]["timezone"] == "Asia/Shanghai"
    options = client.get("/api/operations/options").json()
    assert {item["user_id"] for item in options["users"]} == {users[x]["user_id"] for x in ("member", "manager")}
    detail = client.post("/api/operations/events/query", json=period(kind="visit", module="设置")).json()
    assert detail["total"] == 1


def test_saved_views_are_private_and_retention_requires_super_admin(platform):
    client, actor, users, _ = platform
    actor["user"] = users["manager"]
    response = client.post("/api/operations/views", json={"name": "失败登录", "filters": period(kind="login", result="failure")})
    assert response.status_code == 200
    view_id = response.json()["view_id"]
    assert len(client.get("/api/operations/views").json()["items"]) == 1
    policy = client.get("/api/operations/options").json()["policy"]
    assert client.put("/api/operations/policy", json={"retention_days": 90, "version": policy["version"], "confirmed": True}).status_code == 403
    actor["user"] = users["root"]
    assert client.get("/api/operations/views").json()["items"] == []
    assert client.delete(f"/api/operations/views/{view_id}").status_code == 404
    assert client.put("/api/operations/policy", json={"retention_days": 90, "version": policy["version"], "confirmed": False}).status_code == 422
    assert client.put("/api/operations/policy", json={"retention_days": 90, "version": policy["version"], "confirmed": True}).status_code == 200
    assert client.put("/api/operations/policy", json={"retention_days": 180, "version": policy["version"], "confirmed": True}).status_code == 409
    logs = client.post("/api/operations/events/query", json=period(kind="access")).json()["items"]
    assert any(row["action"] == "修改保留策略" and row["changes"] for row in logs)


def test_foreground_heartbeat_ignores_client_duration_and_multiple_tabs(platform, monkeypatch):
    from src import operations
    client, actor, users, store = platform
    clock = [operations.time.time()]
    monkeypatch.setattr(operations.time, "time", lambda: clock[0])
    assert client.post("/api/operations/heartbeat", json={"duration": 99999}).status_code == 422
    initial = clock[0]
    for elapsed in (0, 30, 30, 60):
        clock[0] = initial + elapsed
        assert client.post("/api/operations/heartbeat", json={}).status_code == 200
    actor["user"] = users["manager"]
    stats = client.post("/api/operations/summary", json=period()).json()["sessions"]
    assert stats["online_users"] == 1
    assert stats["count"] == 1 and stats["average_seconds"] == 60
    assert stats["peak_users"] == 1
    clock[0] += 120
    assert client.post("/api/operations/summary", json=period()).json()["sessions"]["online_users"] == 0
    actor["user"] = users["peer"]
    assert client.post("/api/operations/heartbeat", json={}).status_code == 200
    store.update_user(users["peer"]["user_id"], role="user", actor_user_id=users["root"]["user_id"])
    actor["user"] = users["manager"]
    assert client.post("/api/operations/summary", json=period()).json()["sessions"]["count"] == 1


def test_user_permission_changes_have_safe_before_after_diff(platform, monkeypatch):
    from src.api.auth import require_admin
    from src.api.routes import admin_routes
    from src.api.operations_middleware import OperationsMiddleware
    client, actor, users, store = platform
    monkeypatch.setattr(admin_routes, "get_store", lambda: store)
    client.app.include_router(admin_routes.router)
    client.app.add_middleware(OperationsMiddleware, store_provider=lambda: store)
    client.app.dependency_overrides[require_admin] = client.app.dependency_overrides[get_current_user]
    actor["user"] = users["root"]
    response = client.patch(f"/api/admin/users/{users['member']['user_id']}", json={"role": "admin"})
    assert response.status_code == 200
    event = client.post("/api/operations/events/query", json=period(kind="action")).json()["items"][0]
    assert {"field": "角色", "before": "user", "after": "admin"} in event["changes"]
    actor["user"] = users["manager"]
    assert client.get(f"/api/operations/events/{event['event_id']}").status_code == 404


def test_configuration_diff_keeps_switches_but_never_credentials(platform, monkeypatch):
    from src.api.auth import require_admin
    from src.api.routes import config_routes
    from src.api.operations_middleware import OperationsMiddleware
    from src.config import runtime_config as rc
    client, actor, users, store = platform
    monkeypatch.setattr(config_routes, "get_store", lambda: store)
    monkeypatch.setattr(rc, "_BASELINE", {})
    monkeypatch.setattr(rc.settings, "smtp_enabled", False)
    monkeypatch.setattr(rc.settings, "smtp_password", "synthetic-old-secret")
    client.app.include_router(config_routes.router)
    client.app.add_middleware(OperationsMiddleware, store_provider=lambda: store)
    client.app.dependency_overrides[require_admin] = client.app.dependency_overrides[get_current_user]
    actor["user"] = users["root"]
    assert client.put("/api/config/batch", json={"values": {"smtp_enabled": "true", "smtp_password": "synthetic-new-secret"}}).status_code == 200
    response = client.post("/api/operations/events/query", json=period(kind="action"))
    assert "synthetic-old-secret" not in response.text and "synthetic-new-secret" not in response.text
    changes = response.json()["items"][0]["changes"]
    assert any(change["before"] is False and change["after"] is True for change in changes)
    assert any(change["after"] == "已修改" for change in changes)


def test_denied_reads_and_exports_are_audited_with_real_result(platform):
    client, actor, users, _ = platform
    denied = client.post("/api/operations/export", json={"filters": period(), "format": "csv"})
    assert denied.status_code == 403
    actor["user"] = users["root"]
    assert client.get("/api/operations/events/00000000000040008000000000000009").status_code == 404
    rows = client.post("/api/operations/events/query", json=period(kind="access")).json()["items"]
    assert any(row["action"] == "导出日志" and row["result"] == "failure" and row["status_code"] == 403 for row in rows)
    assert any(row["action"] == "查看详情" and row["result"] == "failure" and row["status_code"] == 404 for row in rows)
    response = client.post("/api/operations/events/query", json=period(kind="visit"))
    assert response.headers["cache-control"] == "no-store"
    rows = client.post("/api/operations/events/query", json=period(kind="access")).json()["items"]
    assert any(row.get("context", {}).get("kind") == "visit" and row["context"]["count"] == 0 and row["device"] for row in rows)
    assert client.post("/api/operations/events/query", json={"start": "invalid"}).status_code == 422
    rows = client.post("/api/operations/events/query", json=period(kind="access")).json()["items"]
    assert any(row["status_code"] == 422 and row["result"] == "failure" for row in rows)


def test_task_actions_and_downloads_share_safe_object_identity(platform):
    from src.api.operations_middleware import OperationsMiddleware
    client, actor, users, store = platform
    client.app.add_middleware(OperationsMiddleware, store_provider=lambda: store)
    task_id = "00000000000040008000000000000001"

    @client.app.post("/api/semantic-workspace/tasks", status_code=202)
    def create(request: Request):
        from src.operations import bind_object
        request.state.platform_user = users["member"]
        bind_object(request, "任务", task_id)
        return {"task_id": task_id}

    @client.app.post("/api/semantic-workspace/tasks/{task_id}/cancel")
    @client.app.post("/api/semantic-workspace/tasks/{task_id}/notify")
    @client.app.get("/api/downloads/{task_id}/{file_path:path}")
    def action(request: Request):
        request.state.platform_user = users["member"]
        return {"ok": True}

    client.post("/api/semantic-workspace/tasks")
    client.post(f"/api/semantic-workspace/tasks/{task_id}/cancel")
    client.post(f"/api/semantic-workspace/tasks/{task_id}/notify")
    client.get(f"/api/downloads/{task_id}/private-file-name.csv")
    actor["user"] = users["manager"]
    response = client.post("/api/operations/events/query", json=period(kind="action"))
    rows = response.json()["items"]
    assert {row["action"] for row in rows} == {"创建任务", "停止任务", "发送结果", "下载文件"}
    assert len({row["object_ref"] for row in rows}) == 1
    assert "private-file-name" not in response.text


def test_exports_escape_formulas_and_event_records_reject_updates(platform):
    import io
    import sqlite3
    from openpyxl import load_workbook
    client, actor, users, store = platform
    with store._conn() as conn:
        conn.execute("UPDATE users SET display_name='=1+2' WHERE user_id=?", (users["member"]["user_id"],))
    event = client.post("/api/operations/visits", json={"event_id": "00000000000040008000000000000007", "page": "/"}).json()["event_id"]
    actor["user"] = users["root"]
    for format in ("csv", "xlsx"):
        response = client.post("/api/operations/export", json={"filters": period(kind="visit"), "format": format})
        assert response.status_code == 200
        if format == "csv":
            assert "'=1+2" in response.text
        else:
            book = load_workbook(io.BytesIO(response.content), read_only=True)
            assert list(book.active.values)[1][2] == "'=1+2"
            book.close()
    with store._conn() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE operations_events SET action='篡改' WHERE event_id=?", (event,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE operations_outcomes SET result='failure' WHERE event_id=?", (event,))
