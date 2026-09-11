"""队列满时保留持久任务，维护循环有空间后继续接管。"""
import asyncio

from src import account_execution as execution
from src.api.store import WebUIStore
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database


def runtime_module():
    from src.api import semantic_workspace_runtime
    return semantic_workspace_runtime


def test_saturated_queue_preserves_task_for_maintenance_recovery(tmp_path, monkeypatch):
    module = runtime_module()
    database = migrated_webui_database(tmp_path / "queue.db")
    owner = seed_execution_owner(database)
    store = WebUIStore(str(database))
    with execution.execution_context(owner):
        for task_id in ("overflow-000", "overflow-001"):
            store.create_semantic_workspace_task(owner.owner_user_id, task_id=task_id, title="虚构", objective_text="保留", upload_ids=[], output_formats=[], provider="local", model=None, external_api_confirmed=False)
    monkeypatch.setattr(module, "get_store", lambda: store)
    monkeypatch.setattr(module, "REVERIFICATION_RECOVERY_POLL_SECONDS", .01)

    async def scenario():
        manager = module.SemanticWorkspaceManager(agent_kernels={"fixture": object()}, primary_adapter_id="fixture")
        for index in range(256):
            manager.enqueue(owner.owner_user_id, f"queued-{index}")
        manager.enqueue(owner.owner_user_id, "overflow-000")
        assert manager._queue.qsize() == 256
        assert "overflow-000" not in manager._queued
        _, released = manager._queue.get_nowait()
        manager._queue.task_done()
        manager._queued.discard(released)

        def unavailable():
            raise RuntimeError("不涉及候选重验")

        monkeypatch.setattr(manager, "_candidate_verification_module", unavailable)
        maintenance = asyncio.create_task(manager._maintenance_loop())
        try:
            async with asyncio.timeout(2):
                while "overflow-000" not in manager._queued:
                    await asyncio.sleep(.01)
            _, released = manager._queue.get_nowait()
            manager._queue.task_done()
            manager._queued.discard(released)
            async with asyncio.timeout(2):
                while "overflow-001" not in manager._queued:
                    await asyncio.sleep(.01)
            assert manager._queue.qsize() == 256
            assert len(manager._queued) == 256
            assert all(store.get_semantic_workspace_task(owner.owner_user_id, task_id)["objective_text"] == "保留" for task_id in ("overflow-000", "overflow-001"))
        finally:
            maintenance.cancel()
            await asyncio.gather(maintenance, return_exceptions=True)

    asyncio.run(scenario())


def test_pending_scan_is_bounded_and_has_stable_following_page(tmp_path):
    database = migrated_webui_database(tmp_path / "pending-pages.db")
    owner = seed_execution_owner(database)
    store = WebUIStore(str(database))
    with execution.execution_context(owner):
        for index in range(258):
            store.create_semantic_workspace_task(owner.owner_user_id, task_id=f"pending-{index:03d}", title="虚构", objective_text="保留", upload_ids=[], output_formats=[], provider="local", model=None, external_api_confirmed=False)
        store.update_semantic_workspace_task(owner.owner_user_id, "pending-257", status="cancelling", cancel_requested=True)

    cancelling = store.list_pending_semantic_workspace_tasks(statuses=("cancelling",), limit=256)
    first = store.list_pending_semantic_workspace_tasks(statuses=("queued", "running"), limit=256)
    following = store.list_pending_semantic_workspace_tasks(
        statuses=("queued", "running"),
        limit=256,
        exclude_task_ids={item["task_id"] for item in first},
    )

    assert [item["task_id"] for item in cancelling] == ["pending-257"]
    assert len(first) == 256
    assert [item["task_id"] for item in following] == ["pending-256"]


def test_cancelling_recovery_rotates_past_failed_first_page(tmp_path, monkeypatch):
    module = runtime_module()
    database = migrated_webui_database(tmp_path / "cancel-pages.db")
    owner = seed_execution_owner(database)
    store = WebUIStore(str(database))
    tasks = [
        {
            "user_id": owner.owner_user_id,
            "task_id": f"cancel-{index:03d}",
            "status": "cancelling",
            "created_at": f"2026-09-11T00:00:{index:03d}",
        }
        for index in range(257)
    ]
    attempted = []
    first_failed = False

    def pending(*, statuses, limit, exclude_task_ids=(), after=None):
        assert limit == 256
        if statuses != ("cancelling",):
            return []
        start = 0 if after is None else next(
            index + 1
            for index, task in enumerate(tasks)
            if (task["created_at"], task["task_id"]) == after
        )
        return tasks[start:start + limit]

    async def cancel(_user_id, task_id):
        nonlocal first_failed
        attempted.append(task_id)
        if not first_failed:
            first_failed = True
            raise RuntimeError("合成清理失败")
        return {}

    monkeypatch.setattr(store, "list_pending_semantic_workspace_tasks", pending)
    monkeypatch.setattr(module, "get_store", lambda: store)
    monkeypatch.setattr(module, "REVERIFICATION_RECOVERY_POLL_SECONDS", .01)

    async def scenario():
        manager = module.SemanticWorkspaceManager(agent_kernels={"fixture": object()}, primary_adapter_id="fixture")
        monkeypatch.setattr(manager, "_candidate_verification_module", lambda: (_ for _ in ()).throw(RuntimeError("不涉及候选重验")))
        monkeypatch.setattr(manager, "_workspace_authorization", lambda _user_id, _task_id: owner)
        monkeypatch.setattr(manager, "cancel", cancel)
        maintenance = asyncio.create_task(manager._maintenance_loop())
        try:
            async with asyncio.timeout(2):
                while "cancel-256" not in attempted:
                    await asyncio.sleep(.01)
        finally:
            maintenance.cancel()
            await asyncio.gather(maintenance, return_exceptions=True)

    asyncio.run(scenario())
    assert attempted.count("cancel-000") == 1
    assert "cancel-256" in attempted
