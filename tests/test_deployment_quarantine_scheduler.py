"""隔离旧调度保持原事实，自动轮询及新停用治理继续处理独立计划。"""
import asyncio
import hashlib
import json
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import auth as api_auth
from src.api.routes import tasks as task_routes
from src.account_execution import execution_context
from src.deployment_quarantine import QuarantinedExecutionError, SETTING_KEY
from src.scheduler.service import SchedulerService
from tests.test_account_execution_scheduler import add, fixture


def quarantine(web, owner, task_id, kind="schedule"):
    # 消费接缝使用真实绑定摘要；登记覆盖由注册器回归独立验证。
    with web._conn() as connection:
        row = dict(connection.execute("SELECT * FROM account_execution_bindings WHERE owner_user_id=? "
                                      "AND resource_kind='schedule' AND resource_id=?", (owner, task_id)).fetchone())
        digest = hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":")).encode("utf-8")).hexdigest()
        manifest = {"version": 1, "approval_reference": "synthetic-approved-first-cutover", "actor_id": "synthetic-admin",
                    "accepted_risk": "first_cutover_unknown_interruption",
                    "targets": [{"owner_id": owner, "kind": kind, "resource_id": task_id, "generation": 0}],
                    "records": [{"profile": "webui", "table": "account_execution_bindings",
                                 "key": {"owner_user_id": owner, "resource_kind": "schedule", "resource_id": task_id},
                                 "sha256": digest}]}
        assert connection.execute("UPDATE app_settings SET value=? WHERE key=?",
                                  (json.dumps(manifest, ensure_ascii=False), SETTING_KEY)).rowcount == 1


@pytest.mark.parametrize("kind", ["schedule", "task"])
def test_quarantined_due_schedule_does_not_starve_new_schedule(fixture, kind):
    web, store, owner, auth = fixture
    old = add(store, auth)
    new = add(store, auth)
    with execution_context(auth):
        store.set_status(new, "active", next_run_at=datetime(2026, 9, 6, 0, 1))
    quarantine(web, owner, old, kind)
    original_task = store.get(old)
    original_binding = web.account_execution_binding(owner, "schedule", old)
    called = []

    async def runner(*args, **kwargs):
        called.append(kwargs["session_id"])
        return {"reply": "独立新计划完成"}

    assert asyncio.run(SchedulerService(store, runner=runner).tick(datetime(2026, 9, 6, 1))) == 1
    assert called == ["scheduler:" + new]
    assert store.get(old) == original_task
    assert web.account_execution_binding(owner, "schedule", old) == original_binding
    assert store.list_runs(old) == []
    assert store.get(new)["status"] == "done"


@pytest.mark.parametrize("background", [False, True])
def test_manual_execution_of_quarantined_schedule_remains_rejected(fixture, background):
    web, store, owner, auth = fixture
    old = add(store, auth)
    quarantine(web, owner, old)
    original_task = store.get(old)
    original_binding = web.account_execution_binding(owner, "schedule", old)
    called = []

    async def runner(*args, **kwargs):
        called.append(args)
        return {"reply": "不得执行"}

    with execution_context(auth), pytest.raises(QuarantinedExecutionError):
        asyncio.run(SchedulerService(store, runner=runner).run_task_now(old, background=background))
    assert called == []
    assert store.get(old) == original_task
    assert web.account_execution_binding(owner, "schedule", old) == original_binding
    assert store.list_runs(old) == []


@pytest.mark.parametrize("kind", ["schedule", "task"])
@pytest.mark.parametrize("old_state", ["idle", "cleanup_failed"])
def test_new_hold_skips_quarantined_schedule_without_pausing_or_confirming_it(fixture, kind, old_state):
    web, store, owner, auth = fixture
    old = add(store, auth)
    new = add(store, auth)
    with web._conn() as connection:
        connection.execute("UPDATE account_execution_bindings SET state=? WHERE resource_kind='schedule' AND resource_id=?", (old_state, old))
    quarantine(web, owner, old, kind)
    original_task = store.get(old)
    original_binding = web.account_execution_binding(owner, "schedule", old)
    web.update_user(owner, disabled=True)
    assert store.reconcile_account_execution(owner, auth.generation + 1) is True
    assert store.get(old) == original_task
    assert web.account_execution_binding(owner, "schedule", old) == original_binding
    assert store.list_runs(old) == []
    assert store.get(new)["status"] == "paused"
    assert web.account_execution_binding(owner, "schedule", new)["state"] == "paused"


@pytest.mark.parametrize("kind", ["schedule", "task"])
def test_cancel_preserves_quarantined_schedule_and_allows_new_work_after_revocation(fixture, kind):
    web, store, owner, auth = fixture
    old = add(store, auth)
    new = add(store, auth)
    quarantine(web, owner, old, kind)
    original_task = store.get(old)
    original_binding = web.account_execution_binding(owner, "schedule", old)
    web.update_user(owner, disabled=True)
    with execution_context(auth):
        with pytest.raises(QuarantinedExecutionError):
            store.cancel(old)
        assert store.cancel(new) is True
    assert store.get(old) == original_task
    assert web.account_execution_binding(owner, "schedule", old) == original_binding
    assert store.get(new)["status"] == "cancelled"


@pytest.mark.parametrize("kind", ["schedule", "task"])
@pytest.mark.parametrize("returned", [False, True])
def test_close_stopped_run_preserves_quarantine_and_allows_new_work_after_revocation(fixture, kind, returned):
    web, store, owner, auth = fixture
    old = add(store, auth)
    new = add(store, auth)
    run_ids = {}
    for task_id in (old, new):
        task = next(item for item in store.due_tasks(datetime(2026, 9, 6, 1)) if item["task_id"] == task_id)
        captured, task = store.claim_execution(task_id, expected_task=task)
        with execution_context(captured):
            run_ids[task_id] = store.begin_run(task)
    quarantine(web, owner, old, kind)
    original_run = store.get_run(old, run_ids[old])
    original_binding = web.account_execution_binding(owner, "schedule", old)
    web.update_user(owner, disabled=True)
    with execution_context(auth):
        with pytest.raises(QuarantinedExecutionError):
            store.close_stopped_run(old, run_ids[old], owner, returned=returned)
        store.close_stopped_run(new, run_ids[new], owner, returned=returned)
    assert store.get_run(old, run_ids[old]) == original_run
    assert web.account_execution_binding(owner, "schedule", old) == original_binding
    current = store.get_run(new, run_ids[new])
    assert current["state"] == ("cancelled" if returned else "unknown")
    assert current["ended_at"] is not None
    assert not current["report_path"] and not current["json_path"]


def test_non_owner_cannot_distinguish_quarantined_schedule_or_close_its_run(fixture, monkeypatch):
    web, store, owner, auth = fixture
    old = add(store, auth)
    task = store.due_tasks(datetime(2026, 9, 6, 1))[0]
    captured, task = store.claim_execution(old, expected_task=task)
    with execution_context(captured):
        run_id = store.begin_run(task)
    quarantine(web, owner, old)
    original_run = store.get_run(old, run_id)
    store.close_stopped_run(old, run_id, "other-owner", returned=True)
    assert store.get_run(old, run_id) == original_run
    app = FastAPI()
    app.include_router(task_routes.router)
    monkeypatch.setattr(task_routes, "get_schedule_store", lambda: store)
    app.dependency_overrides[api_auth.get_current_user] = lambda: {"user_id": "other-owner", "role": "user"}
    with TestClient(app) as client:
        existing = client.delete("/api/tasks/" + old)
        missing = client.delete("/api/tasks/missing-schedule")
    assert existing.status_code == missing.status_code == 404
    assert existing.json() == missing.json()
