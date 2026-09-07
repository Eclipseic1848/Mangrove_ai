"""真实平台会话约束业务流，控制额度覆盖旧入口。"""
import asyncio
import importlib
import json
import sqlite3

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.api import auth
from src.api.routes import auth_routes, chat, semantic_workspace
from src.api.schemas import ChatIn
from src.api.store import WebUIStore
from tests.database_migration_helpers import migrated_webui_database


CONTROL_ROUTES = {
    "chat": {"chat_stream"},
    "tasks": {"create_task", "create_manual_task", "run_task_now_endpoint"},
    "semantic_harness": {"create_run", "resume_run"},
    "semantic_executions": {"execute_plan"},
    "semantic_documents": {"execute_document"},
    "data_tasks": {"create_document_draft", "create_document_scope_revision", "execute_document_extraction", "create_task", "rerun_task"},
    "semantic_workspace": {"create_task", "answer_task", "decide_steering_revision", "request_candidate_reverification", "create_revision", "refresh_task_source"},
    "source_acquisition": {"acquire_source"},
    "capability_governance": {"request_capability_validation"},
}


@pytest.mark.parametrize("module_name,names", CONTROL_ROUTES.items())
def test_task_control_routes_explicitly_marked(module_name, names):
    module = importlib.import_module("src.api.routes." + module_name)
    routes = {route.endpoint.__name__: route for route in module.router.routes}
    assert names <= routes.keys()
    for name in names:
        assert (routes[name].openapi_extra or {}).get("x-mangrove-task-control") is True, name
    for route in routes.values():
        if "GET" in route.methods or route.path.endswith("/cancel"):
            assert not (route.openapi_extra or {}).get("x-mangrove-task-control"), route.path


@pytest.fixture
def stream_session(tmp_path, monkeypatch):
    path = migrated_webui_database(tmp_path / "streams.db")
    store = WebUIStore(str(path))
    monkeypatch.setattr(auth, "_store", store)
    owner = store.create_user("synthetic-stream", auth.hash_password("synthetic-password"), pending=False)
    app = FastAPI()
    app.include_router(auth_routes.router)
    with TestClient(app, base_url="https://testserver", headers={"Origin": "https://testserver", "X-Mangrove-CSRF": "1"}) as client:
        assert client.post("/api/auth/login", json={"username": "synthetic-stream", "password": "synthetic-password"}).status_code == 200
        request = Request({"type": "http", "method": "GET", "scheme": "https", "server": ("testserver", 443), "path": "/api/test", "query_string": b"", "headers": [(b"host", b"testserver"), (b"cookie", f"{auth.ACCESS_COOKIE}={client.cookies[auth.ACCESS_COOKIE]}".encode())], "client": ("127.0.0.1", 1)})
        user = auth.get_current_user(request)
        yield store, path, request, user


def invalidate(store, request, reason, monkeypatch):
    if reason == "revoke":
        store.platform_logout_all(request.state.platform_user["user_id"], now=auth.time.time())
    elif reason == "absolute":
        # 独立命中绝对期限，不能让先到的 access 期限掩盖持久会话复核。
        with sqlite3.connect(store.db_path) as conn:
            conn.execute("UPDATE platform_login_sessions SET absolute_expires_at=? WHERE session_id=?", (auth.time.time(), request.state.platform_claims["sid"]))
    else:
        cutoff = request.state.platform_claims["exp"]
        monkeypatch.setattr(auth.time, "time", lambda: cutoff)


@pytest.mark.parametrize("reason", ["revoke", "access", "absolute"])
def test_workspace_stream_stops_disclosure_without_stopping_run(stream_session, monkeypatch, reason):
    store, path, request, user = stream_session
    store.create_semantic_workspace_task(user["user_id"], task_id="synthetic-task", title="虚构任务", objective_text="虚构正文", upload_ids=[], output_formats=[], provider="local", model=None, external_api_confirmed=False)

    async def scenario():
        response = semantic_workspace.stream_task("synthetic-task", request=request, user=user)
        events = response.body_iterator
        first = await anext(events)
        assert first["event"] == "status"
        waiting = asyncio.create_task(anext(events))
        await asyncio.sleep(0.05)
        invalidate(store, request, reason, monkeypatch)
        expired = await asyncio.wait_for(waiting, 2)
        assert expired["event"] == "auth-expired"
        assert json.loads(expired["data"]) == {"message": "登录已失效，请重新登录"}
        with pytest.raises(StopAsyncIteration):
            await anext(events)
        assert store.get_semantic_workspace_task(user["user_id"], "synthetic-task")["status"] == "queued"

    asyncio.run(scenario())
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM platform_rate_events WHERE bucket='request.api'").fetchone()[0] == 1


@pytest.mark.parametrize("reason", ["revoke", "access", "absolute"])
def test_chat_stream_expires_while_background_finishes(stream_session, monkeypatch, reason):
    store, _, request, user = stream_session
    monkeypatch.setattr(chat, "_resolve_model", lambda *_: ("local", "synthetic"))

    async def history(messages, **_):
        return messages

    monkeypatch.setattr(chat, "compress_history", history)
    monkeypatch.setattr(chat, "_build_result", lambda *args: {"reply": "后台虚构结果", "kind": "output"})

    async def scenario():
        release = asyncio.Event()

        async def runner(**_):
            await release.wait()
            yield "final", {"reply": "后台虚构结果"}

        monkeypatch.setattr(chat, "astream_conductor", runner)
        response = await chat.chat_stream(ChatIn(content="虚构输入", mode="legacy_analysis"), request=request, user=user)
        events = response.body_iterator
        meta = await anext(events)
        conv_id = json.loads(meta["data"])["conv_id"]
        background = chat._RUNNING[f"{user['user_id']}:{conv_id}"]
        try:
            waiting = asyncio.create_task(anext(events))
            await asyncio.sleep(0.05)
            invalidate(store, request, reason, monkeypatch)
            expired = await asyncio.wait_for(waiting, 2)
            assert expired["event"] == "auth-expired"
            assert not background.done()
            with pytest.raises(StopAsyncIteration):
                await anext(events)
            release.set()
            await asyncio.wait_for(background, 2)
            assert store.list_messages(conv_id)[-1]["content"] == "后台虚构结果"
            assert f"{user['user_id']}:{conv_id}" not in chat._RUNNING
        finally:
            release.set()
            await asyncio.wait_for(background, 2)
            await events.aclose()

    asyncio.run(scenario())


def test_scheduler_resume_counts_once_but_pause_and_edit_do_not(stream_session, tmp_path, monkeypatch):
    from datetime import datetime
    from src.api.routes import tasks
    from src.scheduler.store import ScheduleStore
    from tests.database_migration_helpers import migrated_profile_database

    _, path, request, user = stream_session
    schedules = ScheduleStore(str(migrated_profile_database(tmp_path / "scheduler.db", profile="scheduler")))
    monkeypatch.setattr(tasks, "get_schedule_store", lambda: schedules)
    task_id = schedules.add(user_input="虚构任务", provider=None, model=None, trigger_type="cron", cron_expr="0 * * * *", run_at=None, next_run_at=datetime.now(), owner_user_id=user["user_id"])
    app = FastAPI()
    app.include_router(tasks.router)
    with TestClient(app, base_url="https://testserver", headers={"Origin": "https://testserver", "X-Mangrove-CSRF": "1", "Cookie": request.headers["cookie"]}) as client:
        for _ in range(10):
            response = client.patch(f"/api/tasks/{task_id}", json={"status": "active"})
            assert response.status_code == 200, response.text
        limited = client.patch(f"/api/tasks/{task_id}", json={"status": "active"})
        assert limited.status_code == 429 and int(limited.headers["Retry-After"]) > 0
        assert client.patch(f"/api/tasks/{task_id}", json={"status": "paused"}).status_code == 200
        assert client.patch(f"/api/tasks/{task_id}", json={"name": "虚构编辑"}).status_code == 200
    with sqlite3.connect(path) as conn:
        counts = dict(conn.execute("SELECT bucket, count(*) FROM platform_rate_events GROUP BY bucket"))
    assert counts["request.control"] == 10
    assert counts["request.api"] == 13


def test_workspace_http_sse_revocation_blocks_already_buffered_body(stream_session):
    store, path, authenticated, user = stream_session
    store.create_semantic_workspace_task(user["user_id"], task_id="synthetic-wire", title="虚构任务", objective_text="虚构目标", upload_ids=[], output_formats=[], provider="local", model=None, external_api_confirmed=False)
    for summary in ("撤销前虚构事件", "撤销后不得发送正文"):
        store.append_semantic_workspace_event(user["user_id"], "synthetic-wire", stage="execute", event_type="tool.started", summary=summary)
    app = FastAPI()
    app.include_router(semantic_workspace.router)

    async def scenario():
        bodies = []
        revoked = False

        async def send(message):
            nonlocal revoked
            if message["type"] == "http.response.start":
                assert message["status"] == 200
                assert b"text/event-stream" in dict(message["headers"])[b"content-type"]
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                bodies.append(chunk)
                if b"event: progress" in chunk and not revoked:
                    revoked = True
                    store.platform_logout_all(user["user_id"], now=auth.time.time())

        async def receive():
            await asyncio.Event().wait()

        scope = {**authenticated.scope, "path": "/api/semantic-workspace/tasks/synthetic-wire/stream", "raw_path": b"/api/semantic-workspace/tasks/synthetic-wire/stream", "http_version": "1.1", "root_path": "", "state": {}}
        await asyncio.wait_for(app(scope, receive, send), 3)
        body = b"".join(bodies).decode("utf-8")
        assert "撤销前虚构事件" in body
        assert "event: auth-expired" in body
        assert "登录已失效，请重新登录" in body
        assert "撤销后不得发送正文" not in body
        assert store.get_semantic_workspace_task(user["user_id"], "synthetic-wire")["status"] == "queued"

    asyncio.run(scenario())
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM platform_rate_events WHERE bucket='request.api'").fetchone()[0] == 2


@pytest.mark.parametrize("action", ["accept_gap", "reject_gap", "supplement_source", "refresh_source"])
def test_gap_action_only_acceptance_consumes_control(stream_session, action):
    _, path, request, _ = stream_session
    app = FastAPI()
    app.include_router(semantic_workspace.router)
    with TestClient(app, base_url="https://testserver", headers={"Origin": "https://testserver", "X-Mangrove-CSRF": "1", "Cookie": request.headers["cookie"]}) as client:
        # 缺少冻结权威字段，验证动作分类后即停在输入边界，不操作业务任务。
        response = client.post("/api/semantic-workspace/tasks/synthetic-missing/candidate-gap-actions", json={"action": action})
        assert response.status_code == 422
    with sqlite3.connect(path) as conn:
        counts = dict(conn.execute("SELECT bucket, count(*) FROM platform_rate_events GROUP BY bucket"))
    assert counts["request.api"] == 2
    assert counts.get("request.control", 0) == int(action == "accept_gap")


@pytest.mark.parametrize("module_name,names", CONTROL_ROUTES.items())
def test_actual_control_route_auth_consumes_both_buckets(stream_session, module_name, names):
    _, path, request, _ = stream_session
    module = importlib.import_module("src.api.routes." + module_name)
    app = FastAPI()
    app.include_router(module.router)
    with TestClient(app, base_url="https://testserver", headers={"Origin": "https://testserver", "X-Mangrove-CSRF": "1", "Cookie": request.headers["cookie"]}) as client:
        # 先耗尽控制额；实际业务入口必须在读取资源、启动执行或校验正文之前拒绝。
        for _ in range(10):
            assert auth.get_store().platform_request_limit(owner_user_id=request.state.platform_user["user_id"], control=True, now=auth.time.time()) == 0
        for route in module.router.routes:
            if route.endpoint.__name__ in names:
                route_path = route.path
                for parameter in route.param_convertors:
                    route_path = route_path.replace("{" + parameter + "}", "synthetic-missing")
                response = client.post(route_path, json={})
                assert response.status_code == 429, (route_path, response.text)
    with sqlite3.connect(path) as conn:
        counts = dict(conn.execute("SELECT bucket, count(*) FROM platform_rate_events GROUP BY bucket"))
    assert counts["request.api"] == 11
    assert counts["request.control"] == 10
