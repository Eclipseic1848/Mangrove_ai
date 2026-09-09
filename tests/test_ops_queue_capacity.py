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
        store.create_semantic_workspace_task(owner.owner_user_id, task_id="overflow", title="虚构", objective_text="保留", upload_ids=[], output_formats=[], provider="local", model=None, external_api_confirmed=False)
    monkeypatch.setattr(module, "get_store", lambda: store)
    monkeypatch.setattr(module, "REVERIFICATION_RECOVERY_POLL_SECONDS", .01)

    async def scenario():
        manager = module.SemanticWorkspaceManager(agent_kernels={"fixture": object()}, primary_adapter_id="fixture")
        for index in range(256):
            manager.enqueue(owner.owner_user_id, f"queued-{index}")
        manager.enqueue(owner.owner_user_id, "overflow")
        assert manager._queue.qsize() == 256
        assert "overflow" not in manager._queued
        assert store.get_semantic_workspace_task(owner.owner_user_id, "overflow")["status"] == "queued"
        _, released = manager._queue.get_nowait()
        manager._queue.task_done()
        manager._queued.discard(released)

        def unavailable():
            raise RuntimeError("不涉及候选重验")

        monkeypatch.setattr(manager, "_candidate_verification_module", unavailable)
        maintenance = asyncio.create_task(manager._maintenance_loop())
        try:
            async with asyncio.timeout(2):
                while "overflow" not in manager._queued:
                    await asyncio.sleep(.01)
            manager.enqueue(owner.owner_user_id, "overflow")
            assert manager._queue.qsize() == 256
            assert len(manager._queued) == 256
            assert store.get_semantic_workspace_task(owner.owner_user_id, "overflow")["objective_text"] == "保留"
        finally:
            maintenance.cancel()
            await asyncio.gather(maintenance, return_exceptions=True)

    asyncio.run(scenario())
