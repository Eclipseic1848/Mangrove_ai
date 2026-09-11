"""维护协程用原任务冻结身份收口，不能借用请求身份或假报停止。"""
import asyncio
from contextlib import suppress
import time
import pytest

from src.account_execution import execution_context
from src.api import auth, semantic_workspace_runtime as runtime_module
from tests.test_pi_runtime_workspace_api import _client


def test_maintenance_resumes_cancellation_without_request_identity(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch, role="admin")
    store = auth.get_store()
    manager = runtime_module.get_semantic_workspace_manager()
    with execution_context(store.capture_account_execution("user-a")):
        store.create_semantic_workspace_task("user-a", task_id="maintenance-cancel", title="合成恢复", objective_text="取消恢复", upload_ids=[], output_formats=[], provider="local", model=None, external_api_confirmed=False)
        store.request_semantic_workspace_cancellation("user-a", "maintenance-cancel")
        store.update_semantic_workspace_task("user-a", "maintenance-cancel", status="cancelling", cancel_requested=True)
    monkeypatch.setattr(runtime_module, "REVERIFICATION_RECOVERY_POLL_SECONDS", .01)
    def no_verification_module():
        raise RuntimeError("本例只测取消维护分支")
    monkeypatch.setattr(manager, "_candidate_verification_module", no_verification_module)

    async def scenario():
        maintenance = asyncio.create_task(manager._maintenance_loop())
        try:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                row = store.get_semantic_workspace_task("user-a", "maintenance-cancel")
                if row["status"] == "cancelled":
                    break
                await asyncio.sleep(.02)
            assert row["status"] == "cancelled"
            events = store.list_semantic_workspace_events("user-a", "maintenance-cancel")
            assert any(event["event_type"] == "task_cancelled" for event in events)
        finally:
            maintenance.cancel()
            with suppress(asyncio.CancelledError):
                await maintenance
    asyncio.run(scenario())


@pytest.mark.parametrize("stopped", [True, False])
def test_cancel_before_job_body_starts_confirms_real_stop(tmp_path, monkeypatch, stopped):
    _client(tmp_path, monkeypatch, role="admin")
    store = auth.get_store()
    manager = runtime_module.get_semantic_workspace_manager()
    if not stopped:
        async def cleanup_unknown(*_args, **_kwargs):
            return False
        monkeypatch.setattr(manager, "_confirm_runtime_stopped", cleanup_unknown)
    frozen = store.capture_account_execution("user-a")
    with execution_context(frozen):
        store.create_semantic_workspace_task("user-a", task_id="before-body", title="合成取消", objective_text="取消", upload_ids=[], output_formats=[], provider="local", model=None, external_api_confirmed=False)

    async def scenario():
        job = asyncio.create_task(asyncio.Event().wait())
        manager._active["before-body"] = job
        try:
            with execution_context(frozen):
                result = await manager.cancel("user-a", "before-body")
            assert job.done()
            assert result["status"] == ("cancelled" if stopped else "cancelling")
        finally:
            manager._active.pop("before-body", None)
            job.cancel()
            with suppress(asyncio.CancelledError):
                await job
    asyncio.run(scenario())


def test_cancel_does_not_finish_new_revision_after_stop_await(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch, role="admin")
    store = auth.get_store()
    manager = runtime_module.get_semantic_workspace_manager()
    frozen = store.capture_account_execution("user-a")
    with execution_context(frozen):
        store.create_semantic_workspace_task("user-a", task_id="revision-race", title="合成竞态", objective_text="取消", upload_ids=[], output_formats=[], provider="local", model=None, external_api_confirmed=False)

    async def stopped_old_revision(*_args, **_kwargs):
        store.update_semantic_workspace_task("user-a", "revision-race", active_revision=2, status="running", cancel_requested=False)
        await asyncio.sleep(0)
        return True
    monkeypatch.setattr(manager, "_confirm_runtime_stopped", stopped_old_revision)

    async def scenario():
        job = asyncio.create_task(asyncio.Event().wait())
        manager._active["revision-race"] = job
        try:
            with execution_context(frozen):
                result = await manager.cancel("user-a", "revision-race")
            assert result["active_revision"] == 2
            assert result["status"] == "running"
            assert not any(event["event_type"] == "task_cancelled" for event in store.list_semantic_workspace_events("user-a", "revision-race"))
        finally:
            manager._active.pop("revision-race", None)
            job.cancel()
            with suppress(asyncio.CancelledError):
                await job
    asyncio.run(scenario())
