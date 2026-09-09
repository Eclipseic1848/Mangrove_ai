"""列表只投影当前 Owner/Revision；省去重复 Repository 构造而不缓存权限或结构。"""
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src import account_execution as execution
from src.agentic_runtime.models import RuntimeTaskConfig
from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.api import auth
from src.api.routes import semantic_workspace as route
from src.api.store import WebUIStore
from src.config.settings import settings
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database


def test_list_projection_keeps_owner_revision_filters_and_drift_refusal(tmp_path, monkeypatch):
    database = migrated_webui_database(tmp_path / "projection.db")
    owner = seed_execution_owner(database)
    other = seed_execution_owner(database, "owner-b")
    store = WebUIStore(str(database))
    monkeypatch.setattr(auth, "_store", store)
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    for identity, task_id in ((owner, "own"), (owner, "legacy"), (other, "other")):
        with execution.execution_context(identity):
            store.create_semantic_workspace_task(identity.owner_user_id, task_id=task_id, title="合成", objective_text="隔离", upload_ids=[], output_formats=[], provider="local", model=None, external_api_confirmed=False)
    repository = AgenticRuntimeRepository(database)
    repository.register(RuntimeTaskConfig(user_id=owner.owner_user_id, task_id="own", revision=1, runtime_version="pi"))
    repository.register(RuntimeTaskConfig(user_id=owner.owner_user_id, task_id="own", revision=2, runtime_version="pi", permission_profile="extended"))
    repository.register(RuntimeTaskConfig(user_id=other.owner_user_id, task_id="other", revision=1, runtime_version="pi", permission_profile="host_dev"))
    app = FastAPI()
    app.include_router(route.router)
    current = [owner.owner_user_id]
    app.dependency_overrides[auth.get_current_user] = lambda: {"user_id": current[0], "role": "user"}
    with TestClient(app) as client:
        response = client.get("/api/semantic-workspace/tasks")
        assert response.status_code == 200
        items = {item["task_id"]: item for item in response.json()}
        assert set(items) == {"own", "legacy"}
        assert items["own"]["runtime_version"] == "pi"
        assert items["own"]["permission_profile"] == "standard"
        assert items["own"]["agentic_runtime_status"] == "queued"
        assert items["legacy"]["runtime_version"] == "legacy"
        assert items["legacy"]["agentic_runtime_status"] is None
        assert len(client.get("/api/semantic-workspace/tasks?limit=1").json()) == 1
        assert client.get("/api/semantic-workspace/tasks?status=completed").json() == []
        assert client.get("/api/semantic-workspace/tasks?deleted=true").json() == []
        current[0] = other.owner_user_id
        assert [item["task_id"] for item in client.get("/api/semantic-workspace/tasks").json()] == ["other"]
    assert "runtime_version" not in store.list_semantic_workspace_tasks(owner.owner_user_id)[0]
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE agentic_runtime_runs RENAME COLUMN runtime_version TO broken_version")
    with pytest.raises(sqlite3.OperationalError):
        store.list_semantic_workspace_tasks(owner.owner_user_id, include_runtime=True)
    with pytest.raises(RuntimeError):
        WebUIStore(str(database))
