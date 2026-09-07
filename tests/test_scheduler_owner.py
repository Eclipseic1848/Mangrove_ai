# -*- coding: utf-8 -*-
"""调度执行的持久 Owner、配置和记忆隔离；不调用模型。"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

import src.api.auth as auth
from src.api.store import WebUIStore
from src.config import user_ctx
from src.config.settings import settings
from src.llm.provider import MultiModelProvider
from src.memory.loader import personal_context
from src.scheduler.service import SchedulerService
from src.scheduler.store import ScheduleStore
from src.account_execution import ExecutionDenied, execution_context
from tests.database_migration_helpers import migrated_profile_database, migrated_webui_database


@pytest.fixture
def owner_stores(tmp_path, monkeypatch):
    web = WebUIStore(str(migrated_webui_database(tmp_path / "webui.db")))
    schedules = ScheduleStore(str(migrated_profile_database(tmp_path / "scheduler.db", profile="scheduler")))
    monkeypatch.setattr(auth, "get_store", lambda: web)
    monkeypatch.setattr(MultiModelProvider, "_instance", None)
    for provider in ("deepseek", "qwen"):
        monkeypatch.setattr(settings, f"{provider}_base_url", f"https://{provider}.invalid/v1")
        monkeypatch.setattr(settings, f"{provider}_model", f"{provider}-default")
        monkeypatch.setattr(settings, f"{provider}_api_key", "synthetic-global-key")
    monkeypatch.setattr(settings, "mc_cookie_xhs", "synthetic-global-cookie")
    owners = []
    for name in ("alice", "bob"):
        owner = web.create_user(name, "synthetic-password-hash")["user_id"]
        for key in ("deepseek_api_key", "qwen_api_key", "mc_cookie_xhs"):
            web.config_set(owner, key, f"synthetic-{name}-{key}")
        web.memory_add(owner, f"{name}的个人记忆")
        owners.append(owner)
    return web, schedules, owners


def _add(schedules, owner, provider="deepseek", model="saved-model"):
    with execution_context(auth.get_store().capture_account_execution(owner)):
        return schedules.add(user_input="虚构调度任务", provider=provider, model=model,
                             trigger_type="once", cron_expr=None, run_at=datetime(2026, 1, 1),
                             next_run_at=datetime(2026, 1, 1), owner_user_id=owner)


def test_tick_uses_persisted_owner_keys_models_and_memories(owner_stores):
    _, schedules, owners = owner_stores
    observed = []

    async def runner(user_input, **kwargs):
        resolved = MultiModelProvider().resolve_model(kwargs["provider"], model=kwargs["model"])
        observed.append({"provider": resolved.provider, "model": resolved.requested_model,
                         "key": resolved.api_key, "cookie": user_ctx.effective("mc_cookie_xhs"),
                         "memory": personal_context(), "kwargs": kwargs})
        return {"outputs": {"report_md": "synthetic-report.md"}}

    ids = [_add(schedules, owner, provider, f"saved-{provider}")
           for owner, provider in zip(owners, ("deepseek", "qwen"))]
    service = SchedulerService(schedules, runner=runner)
    assert asyncio.run(service.tick(datetime(2026, 1, 2))) == 2
    assert len(observed) == 2
    for result, name, provider in zip(observed, ("alice", "bob"), ("deepseek", "qwen")):
        assert result["provider"] == provider and result["model"] == f"saved-{provider}"
        assert result["key"] == f"synthetic-{name}-{provider}_api_key"
        assert result["cookie"] == f"synthetic-{name}-mc_cookie_xhs"
        assert f"{name}的个人记忆" in result["memory"]
        assert result["kwargs"]["approved_db_write"] is False
        assert result["kwargs"]["ignore_schedule"] is True
    assert all(schedules.list_runs(task_id)[0]["success"] == 1 for task_id in ids)


@pytest.mark.parametrize("identity", ["missing", "unknown", "pending", "disabled", "wrong_field"])
def test_invalid_owner_never_calls_runner(owner_stores, monkeypatch, identity):
    web, schedules, owners = owner_stores
    owner = owners[0]
    task_id = _add(schedules, owner)
    if identity in ("missing", "unknown", "wrong_field"):
        invalid_owner = "missing-owner" if identity == "unknown" else None
        with schedules._conn() as conn:
            conn.execute("UPDATE scheduled_tasks SET owner_user_id=? WHERE task_id=?", (invalid_owner, task_id))
    elif identity == "pending":
        web.update_user(owner, pending=True)
    else:
        web.update_user(owner, disabled=True)
    if identity == "wrong_field":
        get = schedules.get
        monkeypatch.setattr(schedules, "get", lambda task_id: {**get(task_id), "user_id": owners[0]})
    called = []

    async def runner(*args, **kwargs):
        called.append(kwargs)
        return {"reply": "不应执行"}

    before = schedules.get(task_id)
    with pytest.raises(ExecutionDenied):
        asyncio.run(SchedulerService(schedules, runner=runner).run_task_now(task_id))
    assert called == []
    assert schedules.list_runs(task_id) == []
    after = schedules.get(task_id)
    assert (after["status"], after["next_run_at"]) == (before["status"], before["next_run_at"])


@pytest.mark.parametrize("method", ["config_all", "memory_list"])
def test_owner_context_read_failure_is_closed_and_redacted(owner_stores, monkeypatch, caplog, method):
    web, schedules, owners = owner_stores
    task_id = _add(schedules, owners[0])
    marker = "synthetic-sensitive-context-marker"

    def fail(*args):
        raise RuntimeError(marker)

    monkeypatch.setattr(web, method, fail)
    called = []

    async def runner(*args, **kwargs):
        called.append(kwargs)
        return {"reply": "不应执行"}

    asyncio.run(SchedulerService(schedules, runner=runner).tick(datetime(2026, 1, 2)))
    assert called == []
    history = schedules.list_runs(task_id)
    assert history[0]["success"] == 0
    assert marker not in str(history) + str(schedules.get(task_id)) + caplog.text


def test_concurrent_run_now_keeps_each_owner_across_await(owner_stores):
    _, schedules, owners = owner_stores
    ids = [_add(schedules, owner, provider, f"saved-{provider}")
           for owner, provider in zip(owners, ("deepseek", "qwen"))]

    async def run():
        entered = [asyncio.Event(), asyncio.Event()]
        release = asyncio.Event()
        observations = {}

        async def runner(*args, **kwargs):
            index = ids.index(kwargs["session_id"].removeprefix("scheduler:"))
            def snapshot():
                resolved = MultiModelProvider().resolve_model(kwargs["provider"], model=kwargs["model"])
                return (resolved.api_key, user_ctx.effective("mc_cookie_xhs"), personal_context())
            observations[index] = [snapshot()]
            entered[index].set()
            await release.wait()
            observations[index].append(snapshot())
            return {"reply": "完成"}

        service = SchedulerService(schedules, runner=runner)
        async def run_owned(task_id, owner):
            with execution_context(auth.get_store().capture_account_execution(owner)):
                return await service.run_task_now(task_id)

        tasks = [asyncio.create_task(run_owned(task_id, owner)) for task_id, owner in zip(ids, owners)]
        try:
            await asyncio.gather(*(event.wait() for event in entered))
            assert await service.run_task_now(ids[0]) == "running"
            release.set()
            assert await asyncio.gather(*tasks) == ["started", "started"]
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        for index, name in enumerate(("alice", "bob")):
            assert observations[index][0] == observations[index][1]
            assert observations[index][0][0].startswith(f"synthetic-{name}-")
            assert observations[index][0][1] == f"synthetic-{name}-mc_cookie_xhs"
            assert f"{name}的个人记忆" in observations[index][0][2]
        assert all(len(schedules.list_runs(task_id)) == 1 for task_id in ids)

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["success", "exception", "cancel", "timeout", "empty_owner"])
def test_invocation_restores_exact_caller_context(owner_stores, outcome):
    web, schedules, owners = owner_stores
    owner = web.create_user("empty-owner", "synthetic-hash")["user_id"] if outcome == "empty_owner" else owners[0]
    task = schedules.get(_add(schedules, owner))

    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def runner(*args, **kwargs):
            entered.set()
            if outcome == "empty_owner":
                assert user_ctx.effective("deepseek_api_key") == "synthetic-global-key"
                assert personal_context() == ""
            else:
                assert user_ctx.effective("deepseek_api_key") == "synthetic-alice-deepseek_api_key"
                assert "alice的个人记忆" in personal_context()
            await release.wait()
            if outcome == "exception":
                raise RuntimeError("synthetic-runner-error")
            return {"reply": "完成"}

        service = SchedulerService(schedules, runner=runner)

        async def caller():
            overrides = {"deepseek_api_key": "synthetic-caller", "extra": "exact"}
            memories = ["调用者记忆", "第二条"]
            ot = user_ctx.set_user_overrides(overrides)
            mt = user_ctx.set_user_memories(memories)
            try:
                try:
                    if outcome == "timeout":
                        async with asyncio.timeout(0.03):
                            await service._invoke_runner(task)
                    else:
                        await service._invoke_runner(task)
                except (RuntimeError, asyncio.CancelledError, TimeoutError):
                    if outcome in ("success", "empty_owner"):
                        raise
                assert user_ctx._OVERRIDES.get() == overrides
                assert user_ctx.get_user_memories() == memories
            finally:
                user_ctx._OVERRIDES.reset(ot)
                user_ctx._MEMORIES.reset(mt)

        running = asyncio.create_task(caller())
        try:
            await entered.wait()
            if outcome == "cancel":
                running.cancel()
            elif outcome != "timeout":
                release.set()
            await running
        finally:
            release.set()
            if not running.done():
                running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    with execution_context(web.capture_account_execution(owner)):
        asyncio.run(run())


def test_public_timeout_keeps_schedule_and_records_failure(owner_stores, monkeypatch):
    _, schedules, owners = owner_stores
    task_id = _add(schedules, owners[0])
    before = schedules.get(task_id)
    monkeypatch.setattr(settings, "scheduler_task_timeout_seconds", 0.03)

    async def run():
        entered = asyncio.Event()
        closed = asyncio.Event()

        async def runner(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        assert await SchedulerService(schedules, runner=runner).run_task_now(task_id) == "started"
        assert entered.is_set() and closed.is_set()

    with execution_context(auth.get_store().capture_account_execution(owners[0])):
        asyncio.run(run())
    history = schedules.list_runs(task_id)
    assert history[0]["success"] == 0 and "超时" in history[0]["summary"]
    after = schedules.get(task_id)
    assert (after["status"], after["next_run_at"]) == (before["status"], before["next_run_at"])
