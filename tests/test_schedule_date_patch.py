"""日期 PATCH 经过公开接口和临时库，保留模型及执行身份。"""
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.account_execution import execution_context
from src.api import auth
from src.api.routes import tasks
from tests.test_account_execution_scheduler import fixture


@pytest.fixture
def date_api(fixture, monkeypatch):
    web, store, owner, authorization = fixture
    # 固定北京时间，直接验证真实调度计算，不替换 compute_next_run。
    monkeypatch.setattr("src.timezone.now", lambda: datetime(2032, 2, 28, 10))
    with execution_context(authorization):
        task_id = store.add(
            name="合成日期计划", user_input="合成内容", provider="deepseek", model="synthetic-model",
            model_connection_id="synthetic-date-connection", model_connection_version="1" * 64,
            trigger_type="cron", cron_expr="0 9 * * *", run_at=None,
            start_date="2032-03-02", end_date="2032-03-04",
            next_run_at=datetime(2032, 3, 2, 9), owner_user_id=owner,
        )
    monkeypatch.setattr(tasks, "get_schedule_store", lambda: store)
    monkeypatch.setattr(tasks, "get_scheduler_service", lambda: SimpleNamespace(is_running=lambda _: False))
    user = {"user_id": owner, "role": "user", "execution_generation": authorization.generation}
    app = FastAPI()
    app.include_router(tasks.router)
    app.dependency_overrides[auth.get_current_user] = lambda: user
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, store, web, owner, task_id, user


def read_task(client, task_id):
    response = client.get("/api/tasks")
    assert response.status_code == 200, response.text
    return next(item for item in response.json() if item["task_id"] == task_id)


@pytest.mark.parametrize("patch,now,start,end,next_run", [
    ({"start_date": None}, datetime(2032, 2, 28, 10), None, "2032-03-04", "2032-02-29T09:00"),
    ({"end_date": None}, datetime(2032, 3, 5, 10), "2032-03-02", None, "2032-03-06T09:00"),
    ({"start_date": None, "end_date": None}, datetime(2032, 3, 5, 10), None, None, "2032-03-06T09:00"),
])
def test_explicit_null_clears_dates_and_recomputes_next_run(date_api, monkeypatch, patch, now, start, end, next_run):
    client, store, web, owner, task_id, user = date_api
    before = read_task(client, task_id)
    binding = web.account_execution_binding(owner, "schedule", task_id)
    monkeypatch.setattr("src.timezone.now", lambda: now)
    response = client.patch("/api/tasks/" + task_id, json=patch)
    assert response.status_code == 200, response.text
    saved = read_task(client, task_id)
    assert (saved["start_date"], saved["end_date"]) == (start, end)
    assert response.json()["next_run_at"] == next_run
    assert saved["next_run_at"] == next_run + ":00"
    for key in ("owner_user_id", "provider", "model", "model_connection_id", "model_connection_version", "time_zone"):
        assert saved[key] == before[key]
    assert web.account_execution_binding(owner, "schedule", task_id) == binding


def test_omitted_dates_preserve_bounds_when_only_name_changes(date_api):
    client, store, web, owner, task_id, user = date_api
    response = client.patch("/api/tasks/" + task_id, json={"name": "只改名称"})
    assert response.status_code == 200, response.text
    saved = read_task(client, task_id)
    assert saved["name"] == "只改名称"
    assert (saved["start_date"], saved["end_date"]) == ("2032-03-02", "2032-03-04")
    assert saved["next_run_at"] == "2032-03-02T09:00:00"


@pytest.mark.parametrize("patch,start,end,next_run", [
    ({"start_date": "2032-02-29", "end_date": "2032-02-29"}, "2032-02-29", "2032-02-29", "2032-02-29T09:00:00"),
    ({"start_date": "2032-03-04"}, "2032-03-04", "2032-03-04", "2032-03-04T09:00:00"),
    ({"end_date": "2032-03-02"}, "2032-03-02", "2032-03-02", "2032-03-02T09:00:00"),
    ({"start_date": "2032-03-02T12:00:00"}, "2032-03-02T12:00:00", "2032-03-04", "2032-03-03T09:00:00"),
    ({"start_date": "20320303"}, "20320303", "2032-03-04", "2032-03-03T09:00:00"),
    ({"start_date": "", "end_date": ""}, "", "", "2032-02-29T09:00:00"),
])
def test_valid_dates_and_existing_iso_compatibility(date_api, patch, start, end, next_run):
    client, store, web, owner, task_id, user = date_api
    response = client.patch("/api/tasks/" + task_id, json=patch)
    assert response.status_code == 200, response.text
    saved = read_task(client, task_id)
    assert (saved["start_date"], saved["end_date"], saved["next_run_at"]) == (start, end, next_run)


@pytest.mark.parametrize("field", ["start_date", "end_date"])
@pytest.mark.parametrize("value", ["2032-02-30", "2031-02-29", "not-a-date"])
def test_invalid_date_is_rejected_without_changing_plan(date_api, field, value):
    client, store, web, owner, task_id, user = date_api
    before = read_task(client, task_id)
    response = client.patch("/api/tasks/" + task_id, json={field: value})
    assert response.status_code == 422, response.text
    assert read_task(client, task_id) == before


@pytest.mark.parametrize("patch", [{"start_date": "2032-03-05"}, {"end_date": "2032-03-01"}])
def test_reversed_date_window_keeps_existing_rejection(date_api, patch):
    client, store, web, owner, task_id, user = date_api
    before = read_task(client, task_id)
    response = client.patch("/api/tasks/" + task_id, json=patch)
    assert response.status_code == 422, response.text
    assert read_task(client, task_id) == before


def test_resume_uses_cleared_bounds_after_original_window_ends(date_api, monkeypatch):
    client, store, web, owner, task_id, user = date_api
    assert client.patch("/api/tasks/" + task_id, json={"start_date": None, "end_date": None}).status_code == 200
    assert client.patch("/api/tasks/" + task_id, json={"status": "paused"}).status_code == 200
    monkeypatch.setattr("src.timezone.now", lambda: datetime(2032, 3, 5, 10))
    response = client.patch("/api/tasks/" + task_id, json={"status": "active"})
    assert response.status_code == 200, response.text
    saved = read_task(client, task_id)
    assert (saved["start_date"], saved["end_date"]) == (None, None)
    assert saved["next_run_at"] == "2032-03-06T09:00:00"


def test_other_owner_cannot_clear_schedule_dates(date_api):
    client, store, web, owner, task_id, user = date_api
    before = read_task(client, task_id)
    original_user = dict(user)
    user["user_id"] = web.create_user("other-date-owner", "synthetic-unused")["user_id"]
    try:
        existing = client.patch("/api/tasks/" + task_id, json={"start_date": None, "end_date": None})
        missing = client.patch("/api/tasks/missing-schedule", json={"start_date": None})
        assert existing.status_code == missing.status_code == 404
        assert existing.json() == missing.json()
        assert client.get("/api/tasks").json() == []
    finally:
        user.update(original_user)
    assert read_task(client, task_id) == before


def test_quarantined_schedule_cannot_clear_dates(date_api):
    from src.deployment_quarantine import QuarantinedExecutionError
    from tests.test_deployment_quarantine_scheduler import quarantine

    client, store, web, owner, task_id, user = date_api
    quarantine(web, owner, task_id)
    before = store.get(task_id)
    binding = web.account_execution_binding(owner, "schedule", task_id)
    # 最小路由应用没有全站异常处理器，保留真实隔离异常而不模拟放行。
    with TestClient(client.app) as rejecting, pytest.raises(QuarantinedExecutionError):
        rejecting.patch("/api/tasks/" + task_id, json={"start_date": None, "end_date": None})
    assert store.get(task_id) == before
    assert web.account_execution_binding(owner, "schedule", task_id) == binding
