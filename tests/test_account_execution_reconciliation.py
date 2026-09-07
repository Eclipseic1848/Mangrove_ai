"""停止证明由实际持久身份和资源收口共同决定。"""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from src import account_execution as execution
from src.api import auth
from src.api.account_execution_runtime import AccountExecutionManager
from src.api.execution import execution_to_thread, running_execution, execution_validation, execution_checkpoint
from src.api.store import WebUIStore
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture
def resource(tmp_path, monkeypatch):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "reconcile.db")))
    owner = store.create_user("synthetic-resource", "synthetic-hash")["user_id"]
    monkeypatch.setattr(auth, "_store", store)
    async def scheduler(*_):
        return True
    manager = AccountExecutionManager(store, None, SimpleNamespace(reconcile_account_execution=scheduler), None, None)
    return store, owner, manager


def test_empty_binding_scan_still_requires_scheduler_confirmation(resource):
    store, owner, manager = resource
    store.update_user(owner, disabled=True)
    async def unavailable(*_):
        raise OSError("虚构调度库不可读")
    manager.scheduler.reconcile_account_execution = unavailable
    asyncio.run(manager.run_once())
    hold = store.admin_user(owner)["execution_hold"]
    assert hold["status"] == "failed" and hold["error_code"] == "scheduler_unavailable"
    store.retry_account_execution_hold(owner, hold["operation_id"])
    assert store.admin_user(owner)["execution_hold"]["status"] == "processing"
    async def ready(*_):
        return True
    manager.scheduler.reconcile_account_execution = ready
    asyncio.run(manager.run_once())
    assert store.admin_user(owner)["execution_hold"]["status"] == "completed"


def test_free_local_lock_does_not_prove_unknown_chat_result(resource):
    store, owner, manager = resource
    old = store.capture_account_execution(owner)
    store.bind_account_execution(old, "chat", "synthetic-orphan")
    store.update_user(owner, disabled=True)
    store.update_user(owner, disabled=False)
    asyncio.run(manager.run_once())
    hold = store.admin_user(owner)["execution_hold"]
    assert hold["status"] == "failed" and hold["error_code"] == "holder_unknown"
    assert store.account_execution_binding(owner, "chat", "synthetic-orphan")["state"] == "active"


def test_atomic_thread_remains_pending_until_real_completion(resource):
    store, owner, manager = resource
    old = store.capture_account_execution(owner)
    store.bind_account_execution(old, "chat", "synthetic-running")
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    def atomic():
        entered.set()
        assert release.wait(5)
        finished.set()
    async def scenario():
        async def run():
            with execution.execution_context(old):
                async with running_execution(store, "chat", "synthetic-running"):
                    await execution_to_thread(atomic)
        worker = asyncio.create_task(run())
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(.01)
            assert entered.is_set()
            store.update_user(owner, disabled=True)
            await manager.run_once()
            assert store.admin_user(owner)["execution_hold"]["status"] == "processing"
            assert not finished.is_set() and not worker.done()
        finally:
            release.set()
            with pytest.raises(execution.ExecutionDenied):
                await worker
        assert finished.is_set()
        await manager.run_once()
        assert store.admin_user(owner)["execution_hold"]["status"] == "completed"
    asyncio.run(scenario())


def test_repeated_cancel_cannot_abandon_atomic_thread():
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    def atomic():
        entered.set()
        assert release.wait(5)
        finished.set()
    async def scenario():
        worker = asyncio.create_task(execution_to_thread(atomic))
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(.01)
            assert entered.is_set()
            worker.cancel()
            await asyncio.sleep(0)
            worker.cancel()
            await asyncio.sleep(.01)
            assert not worker.done() and not finished.is_set()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await worker
        assert finished.is_set()
    asyncio.run(scenario())


def test_explicit_database_authorizer_follows_real_thread_context(resource, tmp_path, monkeypatch):
    store, owner, _ = resource
    frozen = store.capture_account_execution(owner)
    other_database = WebUIStore(str(migrated_webui_database(tmp_path / "other-database.db")))
    monkeypatch.setattr(auth, "_store", other_database)

    def atomic():
        execution_checkpoint(required=True)
        store.update_user(owner, disabled=True)
        return "迟到结果"

    async def run():
        with execution.execution_context(frozen), execution_validation(store.require_account_authorization):
            with pytest.raises(execution.ExecutionDenied):
                await execution_to_thread(atomic)
    asyncio.run(run())
    assert store.get_user(owner)["disabled"]


def test_queued_thread_rechecks_before_atomic_action_starts(resource):
    from concurrent.futures import ThreadPoolExecutor

    store, owner, _ = resource
    frozen = store.capture_account_execution(owner)
    occupied, release = threading.Event(), threading.Event()
    calls = []

    def occupy_worker():
        occupied.set()
        assert release.wait(5)

    async def run():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        blocker = asyncio.create_task(asyncio.to_thread(occupy_worker))
        try:
            while not occupied.is_set():
                await asyncio.sleep(.01)
            with execution.execution_context(frozen):
                queued = asyncio.create_task(execution_to_thread(lambda: calls.append("started")))
            await asyncio.sleep(.02)
            assert not queued.done()
            store.update_user(owner, disabled=True)
        finally:
            release.set()
            await blocker
        with pytest.raises(execution.ExecutionDenied):
            await queued
        assert calls == []

    asyncio.run(run())
