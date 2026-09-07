"""图节点保留独立库用法，平台执行不能越过停用安全点。"""
import asyncio

import pytest

from src import account_execution as execution
from src.api import auth
from src.api.store import WebUIStore
from src.conductor.graph import _traced
from tests.database_migration_helpers import migrated_webui_database


def test_platform_node_rejects_result_after_account_generation_changes(tmp_path, monkeypatch):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "graph.db")))
    owner = store.create_user("synthetic-graph", "synthetic-hash")
    monkeypatch.setattr(auth, "_store", store)
    frozen = store.capture_account_execution(owner["user_id"])
    called = []

    async def node(state):
        called.append("first")
        store.update_user(owner["user_id"], disabled=True)
        store.update_user(owner["user_id"], disabled=False)
        return {"reply": "迟到结果"}

    with execution.execution_context(frozen), pytest.raises(execution.ExecutionDenied):
        asyncio.run(_traced("synthetic", node)({}))
    assert called == ["first"]


def test_independent_node_keeps_existing_library_behavior():
    async def node(state):
        return {"reply": "独立虚构结果"}
    assert asyncio.run(_traced("synthetic", node)({}))["reply"] == "独立虚构结果"
