"""真实任务列表的绑定查询须有界，执行状态与 Owner 投影保持。"""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import auth
from src.api.routes import tasks
from tests.test_account_execution_scheduler import add as add_schedule
from tests.test_account_execution_scheduler import fixture


@pytest.fixture
def schedule_api(fixture, monkeypatch):
    web, store, owner, authorization = fixture
    running = set()
    binding_reads = []
    original_conn = web._conn

    def record(statement):
        if (statement.lstrip().upper().startswith(("SELECT", "WITH"))
                and "account_execution_bindings" in statement.lower()):
            binding_reads.append(statement)

    @contextmanager
    def traced_connection():
        with original_conn() as connection:
            connection.set_trace_callback(record)
            try:
                yield connection
            finally:
                connection.set_trace_callback(None)

    # 观察真实 SQLite 查询，不约束批量接口名称或调用形状。
    monkeypatch.setattr(web, "_conn", traced_connection)
    monkeypatch.setattr(tasks, "get_schedule_store", lambda: store)
    monkeypatch.setattr(tasks, "get_scheduler_service", lambda: SimpleNamespace(
        is_running=lambda task_id: task_id in running,
    ))
    user = {"user_id": owner}
    app = FastAPI()
    app.include_router(tasks.router)
    app.dependency_overrides[auth.get_current_user] = lambda: user
    with TestClient(app, base_url="https://testserver") as client:
        yield SimpleNamespace(client=client, web=web, store=store, owner=owner,
                              authorization=authorization, user=user, running=running,
                              binding_reads=binding_reads)


def _add(api, *, authorization=None, binding_state="idle", status="active", time_zone="Asia/Shanghai"):
    authorization = authorization or api.authorization
    task_id = add_schedule(api.store, authorization, "cron")
    # 仅在隔离库构造历史/停止状态；列表读取不得修补这些已保存事实。
    with api.store._conn() as connection:
        connection.execute("UPDATE scheduled_tasks SET status=?, time_zone=? WHERE task_id=?",
                           (status, time_zone, task_id))
    with api.web._conn() as connection:
        if binding_state is None:
            connection.execute("DELETE FROM account_execution_bindings WHERE owner_user_id=? "
                               "AND resource_kind='schedule' AND resource_id=?",
                               (authorization.owner_user_id, task_id))
        else:
            connection.execute("UPDATE account_execution_bindings SET state=? WHERE owner_user_id=? "
                               "AND resource_kind='schedule' AND resource_id=?",
                               (binding_state, authorization.owner_user_id, task_id))
    return task_id


@pytest.mark.parametrize("count,binding_state", [
    pytest.param(0, "idle", id="empty"),
    pytest.param(1, "idle", id="one"),
    pytest.param(8, "idle", id="many"),
    pytest.param(8, None, id="many-missing"),
])
def test_list_binding_reads_are_bounded(schedule_api, count, binding_state):
    api = schedule_api
    task_ids = {_add(api, binding_state=binding_state) for _ in range(count)}
    api.binding_reads.clear()
    response = api.client.get("/api/tasks")
    assert response.status_code == 200, response.text
    assert {row["task_id"] for row in response.json()} == task_ids
    assert len(api.binding_reads) <= 1, api.binding_reads
    for row in response.json():
        assert row["execution_state"] == ("blocked" if binding_state is None else "idle")
        assert row["can_recreate"] is (binding_state is None)


IDLE = ("idle", "", False)
PAUSED = ("paused", "", False)
RUNNING = ("running", "", False)
STOP_UNCONFIRMED = ("blocked", "停止状态待确认，请联系管理员核查；不会自动重试", False)
LEGACY_TIME_ZONE = ("blocked", "历史时区未记录，请核对模型及执行时间后重新创建；不会自动补跑", True)
MISSING_BINDING = ("blocked", "缺少执行授权，请重新创建计划", True)
PAUSED_AUTHORIZATION = ("blocked", "执行授权已暂停，请明确恢复计划", False)


@pytest.mark.parametrize("running", [False, True], ids=["not-running", "running"])
@pytest.mark.parametrize("binding_state,status,time_zone,quiet_display,running_display", [
    pytest.param("idle", "active", "Asia/Shanghai", IDLE, IDLE, id="idle-active-current"),
    pytest.param("idle", "paused", "Asia/Shanghai", PAUSED, PAUSED, id="idle-paused-current"),
    pytest.param("idle", "active", None, LEGACY_TIME_ZONE, LEGACY_TIME_ZONE, id="idle-active-legacy"),
    pytest.param("idle", "paused", None, LEGACY_TIME_ZONE, LEGACY_TIME_ZONE, id="idle-paused-legacy"),
    pytest.param("paused", "active", "Asia/Shanghai", PAUSED_AUTHORIZATION, PAUSED_AUTHORIZATION, id="paused-active-current"),
    pytest.param("paused", "paused", "Asia/Shanghai", PAUSED, PAUSED, id="paused-paused-current"),
    pytest.param("paused", "active", None, LEGACY_TIME_ZONE, LEGACY_TIME_ZONE, id="paused-active-legacy"),
    pytest.param("paused", "paused", None, LEGACY_TIME_ZONE, LEGACY_TIME_ZONE, id="paused-paused-legacy"),
    pytest.param("active", "active", "Asia/Shanghai", STOP_UNCONFIRMED, RUNNING, id="active-active-current"),
    pytest.param("active", "paused", "Asia/Shanghai", STOP_UNCONFIRMED, RUNNING, id="active-paused-current"),
    pytest.param("active", "active", None, STOP_UNCONFIRMED, RUNNING, id="active-active-legacy"),
    pytest.param("active", "paused", None, STOP_UNCONFIRMED, RUNNING, id="active-paused-legacy"),
    pytest.param("cleanup_failed", "active", "Asia/Shanghai", STOP_UNCONFIRMED, STOP_UNCONFIRMED, id="cleanup-active-current"),
    pytest.param("cleanup_failed", "paused", "Asia/Shanghai", STOP_UNCONFIRMED, STOP_UNCONFIRMED, id="cleanup-paused-current"),
    pytest.param("cleanup_failed", "active", None, STOP_UNCONFIRMED, STOP_UNCONFIRMED, id="cleanup-active-legacy"),
    pytest.param("cleanup_failed", "paused", None, STOP_UNCONFIRMED, STOP_UNCONFIRMED, id="cleanup-paused-legacy"),
    pytest.param(None, "active", "Asia/Shanghai", MISSING_BINDING, MISSING_BINDING, id="missing-active-current"),
    pytest.param(None, "paused", "Asia/Shanghai", MISSING_BINDING, MISSING_BINDING, id="missing-paused-current"),
    pytest.param(None, "active", None, LEGACY_TIME_ZONE, LEGACY_TIME_ZONE, id="missing-active-legacy"),
    pytest.param(None, "paused", None, LEGACY_TIME_ZONE, LEGACY_TIME_ZONE, id="missing-paused-legacy"),
])
def test_list_preserves_saved_binding_projection(
    schedule_api, binding_state, status, time_zone, quiet_display, running_display, running,
):
    api = schedule_api
    task_id = _add(api, binding_state=binding_state, status=status, time_zone=time_zone)
    if running:
        api.running.add(task_id)
    saved_task = api.store.get(task_id)
    saved_binding = api.web.account_execution_binding(api.owner, "schedule", task_id)
    response = api.client.get("/api/tasks")
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1
    item = response.json()[0]
    assert (item["execution_state"], item["blocked_reason"], item["can_recreate"]) == (
        running_display if running else quiet_display
    )
    assert {key: item[key] for key in saved_task} == saved_task
    assert api.store.get(task_id) == saved_task
    assert api.web.account_execution_binding(api.owner, "schedule", task_id) == saved_binding


def test_list_binding_snapshot_is_owner_and_kind_scoped_and_fresh(schedule_api):
    api = schedule_api
    task_id = _add(api, binding_state=None)
    other = api.web.create_user("projection-other-owner", "synthetic-unused")["user_id"]
    other_authorization = api.web.capture_account_execution(other)
    other_task_id = _add(api, authorization=other_authorization)
    # 相同 resource_id 的其它 Owner/资源类型不能冒充本人的计划授权。
    api.web.bind_account_execution(other_authorization, "schedule", task_id, state="idle")
    assert api.web.confirm_account_execution_stopped(
        other, "schedule", task_id, expected_generation=other_authorization.generation, cleanup_failed=True,
    )
    foreign = api.web.account_execution_binding(other, "schedule", task_id)
    chat = api.web.bind_account_execution(api.authorization, "chat", task_id, state="active")
    response = api.client.get("/api/tasks")
    assert response.status_code == 200, response.text
    assert [row["task_id"] for row in response.json()] == [task_id]
    item = response.json()[0]
    assert item["owner_user_id"] == api.owner
    assert (item["execution_state"], item["blocked_reason"], item["can_recreate"]) == MISSING_BINDING
    assert api.web.account_execution_binding(api.owner, "schedule", task_id) is None
    assert api.web.account_execution_binding(other, "schedule", task_id) == foreign
    assert api.web.account_execution_binding(api.owner, "chat", task_id) == chat
    api.user["user_id"] = other
    response = api.client.get("/api/tasks")
    assert response.status_code == 200, response.text
    assert [row["task_id"] for row in response.json()] == [other_task_id]
    assert response.json()[0]["owner_user_id"] == other
    assert response.json()[0]["execution_state"] == "idle"
    # 下一请求必须读取当前快照，不能继续使用先前缺绑定的结果。
    api.web.bind_account_execution(api.authorization, "schedule", task_id, state="idle")
    assert api.web.confirm_account_execution_stopped(
        api.owner, "schedule", task_id, expected_generation=api.authorization.generation,
    )
    api.user["user_id"] = api.owner
    response = api.client.get("/api/tasks")
    assert response.status_code == 200, response.text
    assert [row["task_id"] for row in response.json()] == [task_id]
    item = response.json()[0]
    assert (item["execution_state"], item["blocked_reason"], item["can_recreate"]) == PAUSED_AUTHORIZATION
