"""账号停用与调度计划的授权屏障；仅使用临时显式迁移库。"""
import asyncio
from datetime import datetime

import pytest

from src.scheduler.store import ScheduleStore
from src.scheduler.service import SchedulerService
from src.account_execution import ExecutionDenied, execution_context
from src.api.store import WebUIStore
from tests.database_migration_helpers import migrated_profile_database, migrated_webui_database


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    web = WebUIStore(str(migrated_webui_database(tmp_path / "webui.db")))
    owner = web.create_user("schedule-fixture", "synthetic-unused")["user_id"]
    monkeypatch.setattr("src.api.auth.get_store", lambda: web)
    store = ScheduleStore(str(migrated_profile_database(tmp_path / "scheduler.db", profile="scheduler")))
    auth = web.capture_account_execution(owner)
    return web, store, owner, auth


def add(store, auth, trigger="once"):
    planned = datetime(2026, 9, 6)
    with execution_context(auth):
        return store.add(user_input="虚构任务", provider=None, model=None,
                         trigger_type=trigger, cron_expr="0 8 * * *" if trigger == "cron" else None,
                         run_at=planned, next_run_at=planned, owner_user_id=auth.owner_user_id)


@pytest.mark.parametrize("next_run", [None, datetime(2026, 9, 7)])
def test_late_mark_run_does_not_reactivate_or_finish_paused_schedule(fixture, next_run):
    web, store, owner, auth = fixture
    task_id = add(store, auth, "cron" if next_run else "once")
    with execution_context(auth):
        store.set_status(task_id, "paused")
        store.mark_run(task_id, success=True, result="迟到结果", next_run_at=next_run)
    task = store.get(task_id)
    assert task["status"] == "paused"
    assert task["next_run_at"] == datetime(2026, 9, 6).isoformat()


@pytest.mark.asyncio
async def test_late_runner_result_after_hold_is_discarded(fixture):
    web, store, owner, auth = fixture
    task_id = add(store, auth)
    entered, release = asyncio.Event(), asyncio.Event()

    async def runner(*args, **kwargs):
        entered.set()
        await release.wait()
        return {"reply": "不得回写的迟到正文"}

    service = SchedulerService(store, runner=runner)
    work = asyncio.create_task(service.tick(datetime(2026, 9, 6, 1)))
    await asyncio.wait_for(entered.wait(), 3)
    web.update_user(owner, disabled=True)
    release.set()
    await work
    assert store.list_runs(task_id) == []
    assert store.get(task_id)["last_result"] in (None, "")
    assert web.account_execution_binding(owner, "schedule", task_id)["state"] == "paused"


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["once", "cron"])
async def test_restart_cannot_run_old_binding_after_reenable(fixture, trigger):
    web, store, owner, auth = fixture
    task_id = add(store, auth, trigger)
    web.update_user(owner, disabled=True)
    web.update_user(owner, disabled=False)
    calls = []

    async def runner(*args, **kwargs):
        calls.append(args)
        return {"reply": "不应执行"}

    await SchedulerService(store, runner=runner).tick(datetime(2026, 9, 6, 1))
    assert calls == []
    assert store.list_runs(task_id) == []


def test_stale_creation_and_marks_fail_closed(fixture):
    web, store, owner, auth = fixture
    task_id = add(store, auth)
    web.update_user(owner, disabled=True)
    web.update_user(owner, disabled=False)
    with pytest.raises(ExecutionDenied):
        add(store, auth)
    with execution_context(auth), pytest.raises(ExecutionDenied):
        store.mark_run(task_id, success=True, result="旧代正文")


@pytest.mark.asyncio
async def test_unknown_active_binding_is_not_claimed_or_confirmed(fixture):
    web, store, owner, auth = fixture
    task_id = add(store, auth)
    store.claim_execution(task_id, expected_task=store.due_tasks(datetime(2026, 9, 6, 1))[0])
    calls = []

    async def runner(*args, **kwargs):
        calls.append(args)
        return {"reply": "不应执行"}

    service = SchedulerService(store, runner=runner)
    await service.tick(datetime(2026, 9, 6, 1))
    assert calls == []
    web.update_user(owner, disabled=True)
    assert await service.reconcile_account_execution(owner, auth.generation + 1) is False
    assert web.account_execution_binding(owner, "schedule", task_id)["state"] == "active"
    assert store.get(task_id)["status"] == "paused"


@pytest.mark.asyncio
async def test_explicit_resume_and_ordinary_pause_run_now(fixture):
    web, store, owner, auth = fixture
    task_id = add(store, auth, "cron")
    calls = []

    async def runner(*args, **kwargs):
        calls.append(args)
        return {"reply": "测试回复"}

    service = SchedulerService(store, runner=runner)
    with execution_context(auth):
        store.set_status(task_id, "paused")
        assert await service.run_task_now(task_id) == "started"
    web.update_user(owner, disabled=True)
    web.update_user(owner, disabled=False)
    current = web.capture_account_execution(owner)
    with execution_context(current):
        with pytest.raises(ExecutionDenied):
            await service.run_task_now(task_id)
        store.set_status(task_id, "active", next_run_at=datetime(2026, 9, 7))
        with execution_context(auth), pytest.raises(ExecutionDenied):
            await service.run_task_now(task_id)
        assert await service.run_task_now(task_id) == "started"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_other_owner_keeps_running_and_idle_hold_is_confirmable(fixture):
    web, store, owner, auth = fixture
    task_id = add(store, auth)
    other = web.create_user("other-fixture", "synthetic-unused")["user_id"]
    other_auth = web.capture_account_execution(other)
    other_id = add(store, other_auth)
    web.update_user(owner, disabled=True)
    calls = []

    async def runner(*args, **kwargs):
        calls.append(kwargs["session_id"])
        return {"reply": "独立账号完成"}

    service = SchedulerService(store, runner=runner)
    assert await service.reconcile_account_execution(owner, auth.generation + 1) is True
    await service.tick(datetime(2026, 9, 6, 1))
    assert calls == [f"scheduler:{other_id}"]
    assert store.get(task_id)["status"] == "paused"
    assert store.get(other_id)["status"] == "done"
    assert len(store.list_runs(other_id)) == 1


@pytest.mark.asyncio
async def test_timeout_does_not_prove_worker_stopped_or_retry(fixture, monkeypatch):
    web, store, owner, auth = fixture
    task_id = add(store, auth, "cron")
    calls = []

    async def runner(*args, **kwargs):
        calls.append(args)
        await asyncio.Event().wait()

    from src.config.settings import settings
    monkeypatch.setattr(settings, "scheduler_task_timeout_seconds", 0.01)
    service = SchedulerService(store, runner=runner)
    await service.tick(datetime(2026, 9, 6, 1))
    await service.tick(datetime(2026, 9, 7, 1))
    assert len(calls) == 1
    assert web.account_execution_binding(owner, "schedule", task_id)["state"] == "cleanup_failed"
    web.update_user(owner, disabled=True)
    assert await service.reconcile_account_execution(owner, auth.generation + 1) is False
    web.update_user(owner, disabled=False)
    with execution_context(web.capture_account_execution(owner)), pytest.raises(ExecutionDenied):
        store.set_status(task_id, "active", next_run_at=datetime(2026, 9, 8))


@pytest.mark.asyncio
async def test_reconcile_waits_for_actual_return_before_completion(fixture):
    web, store, owner, auth = fixture
    task_id = add(store, auth)
    entered, release = asyncio.Event(), asyncio.Event()

    async def runner(*args, **kwargs):
        entered.set()
        await release.wait()
        return {"reply": "迟到正文"}

    service = SchedulerService(store, runner=runner)
    work = asyncio.create_task(service.tick(datetime(2026, 9, 6, 1)))
    await asyncio.wait_for(entered.wait(), 3)
    web.update_user(owner, disabled=True)
    assert await service.reconcile_account_execution(owner, auth.generation + 1) is False
    assert store.get(task_id)["status"] == "paused"
    release.set()
    await work
    assert await service.reconcile_account_execution(owner, auth.generation + 1) is True
    assert store.list_runs(task_id) == []


def test_first_schedule_requires_frozen_owner_and_never_backfills(fixture):
    web, store, owner, auth = fixture
    with pytest.raises(ExecutionDenied):
        store.add(user_input="虚构", provider=None, model=None, trigger_type="once",
                  cron_expr=None, run_at=None, next_run_at=None, owner_user_id=owner)
    task_id = add(store, auth)
    with web._lock, web._conn() as conn:
        conn.execute("DELETE FROM account_execution_bindings WHERE resource_id=?", (task_id,))
    with pytest.raises(ExecutionDenied):
        store.claim_execution(task_id, expected_task=store.due_tasks(datetime(2026, 9, 6, 1))[0])
    assert web.account_execution_binding(owner, "schedule", task_id) is None


@pytest.mark.asyncio
async def test_denied_safety_boundary_does_not_starve_other_owner(fixture):
    web, store, owner, auth = fixture
    task_id = add(store, auth)
    other = web.create_user("other-denial-fixture", "synthetic-unused")["user_id"]
    other_id = add(store, web.capture_account_execution(other))

    async def runner(*args, **kwargs):
        if kwargs["session_id"] == f"scheduler:{task_id}":
            raise ExecutionDenied("冻结授权已失效")
        return {"reply": "其它账号仍可执行"}

    await SchedulerService(store, runner=runner).tick(datetime(2026, 9, 6, 1))
    assert len(store.list_runs(other_id)) == 1
    assert store.list_runs(task_id) == []
    assert web.account_execution_binding(owner, "schedule", task_id)["state"] == "cleanup_failed"


@pytest.mark.asyncio
async def test_missing_binding_inventory_never_counts_as_quiescent(fixture):
    web, store, owner, auth = fixture
    task_id = add(store, auth)
    with web._conn() as conn:
        conn.execute("DELETE FROM account_execution_bindings WHERE resource_id=?", (task_id,))
    web.update_user(owner, disabled=True)
    service = SchedulerService(store, runner=None)
    assert await service.reconcile_account_execution(owner, auth.generation + 1) is False
    assert web.account_execution_binding(owner, "schedule", task_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change_epoch,future_run", [(True, True), (True, False), (False, True)])
async def test_old_due_snapshot_cannot_run_rescheduled_future_task(fixture, change_epoch, future_run):
    web, store, _, first_auth = fixture
    first = add(store, first_auth)
    second_owner = web.create_user("queued-second", "synthetic-unused")["user_id"]
    second_auth = web.capture_account_execution(second_owner)
    second = add(store, second_auth, "cron")
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def runner(*args, **kwargs):
        task_id = kwargs["session_id"].removeprefix("scheduler:")
        calls.append(task_id)
        if task_id == first:
            entered.set()
            await release.wait()
        return {"reply": "虚构完成"}

    service = SchedulerService(store, runner=runner)
    work = asyncio.create_task(service.tick(datetime(2026, 9, 6, 1)))
    await asyncio.wait_for(entered.wait(), 3)
    if change_epoch:
        web.update_user(second_owner, disabled=True)
        assert await service.reconcile_account_execution(second_owner, 1)
        web.update_user(second_owner, disabled=False)
    current = web.capture_account_execution(second_owner)
    future = datetime(2026, 9, 9) if future_run else datetime(2026, 9, 6)
    with execution_context(current):
        store.set_status(second, "active", next_run_at=future)
    release.set()
    await work
    assert calls == [first]
    assert store.get(second)["next_run_at"] == future.isoformat()
    assert store.list_runs(second) == []
    assert web.account_execution_binding(second_owner, "schedule", second)["generation"] == current.generation
    # 新一轮读取到期事实后，合法恢复的计划仍能正常执行。
    await service.tick(future)
    assert calls == [first, second]
    assert len(store.list_runs(second)) == 1
