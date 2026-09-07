"""工作台显式恢复入口：临时真实 SQLite，执行器不启动。"""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.account_execution import execution_context
from src.api import auth, semantic_workspace_runtime
from src.api.routes import semantic_workspace as route
from src.api.semantic_workspace_runtime import SemanticWorkspaceManager
from src.api.store import WebUIStore
from src.config.settings import settings
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    path = migrated_webui_database(tmp_path / "resume.db")
    store = WebUIStore(str(path))
    monkeypatch.setattr(settings, "webui_db_path", str(path))
    monkeypatch.setattr(settings, "semantic_execution_root", str(tmp_path / "execution"))
    monkeypatch.setattr(settings, "data_prep_upload_root", str(tmp_path / "uploads"))
    monkeypatch.setattr(auth, "_store", store)
    owner = store.create_user("resume-fixture", "synthetic-unused")
    current = {"user": owner}
    manager = SemanticWorkspaceManager()
    queued = []
    monkeypatch.setattr(manager, "enqueue", lambda *args: queued.append(args))
    monkeypatch.setattr(route, "get_semantic_workspace_manager", lambda: manager)
    monkeypatch.setattr(semantic_workspace_runtime, "get_store", lambda: store)
    with execution_context(store.capture_account_execution(owner["user_id"])):
        store.create_semantic_workspace_task(owner["user_id"], task_id="resume-task", title="虚构暂停任务", objective_text="虚构目标", upload_ids=[], output_formats=[], provider="local", model=None, external_api_confirmed=False)
    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[auth.get_current_user] = lambda: store.get_user(current["user"]["user_id"])
    with TestClient(app) as client:
        yield store, owner["user_id"], manager, client, queued, current


def pause(store, owner, manager, *, waiting=False):
    question = {"kind": "harness", "question_id": "frozen-question", "prompt": "保留此问题", "resume_token": "frozen-checkpoint", "options": [], "allow_free_text": True}
    with execution_context(store.capture_account_execution(owner)):
        if waiting:
            store.update_semantic_workspace_task(owner, "resume-task", status="needs_input", question=question)
    store.update_user(owner, disabled=True)
    asyncio.run(manager.pause_account_execution(owner, "resume-task"))
    store.update_user(owner, disabled=False)
    return question


@pytest.mark.parametrize("waiting", [False, True])
def test_explicit_resume_preserves_revision_and_waiting_question(workspace, waiting):
    store, owner, manager, client, queued, _ = workspace
    question = pause(store, owner, manager, waiting=waiting)
    response = client.post("/api/semantic-workspace/tasks/resume-task/account-resume", json={"expected_generation": 0, "expected_active_revision": 1})
    assert response.status_code == 200, response.text
    task = store.get_semantic_workspace_task(owner, "resume-task")
    assert task["active_revision"] == 1
    assert task["status"] == ("needs_input" if waiting else "queued")
    assert task["question"] == (question if waiting else None)
    assert queued == ([] if waiting else [(owner, "resume-task")])
    assert store.account_execution_binding(owner, "workspace", "resume-task")["generation"] == 1
    repeated = client.post("/api/semantic-workspace/tasks/resume-task/account-resume", json={"expected_generation": 0, "expected_active_revision": 1})
    assert repeated.status_code == 409
    assert queued == ([] if waiting else [(owner, "resume-task")])


@pytest.mark.parametrize("kind", ["revision", "generation", "owner", "not_paused"])
def test_resume_rejects_stale_wrong_owner_and_unconfirmed_stop(workspace, kind):
    store, owner, manager, client, queued, current = workspace
    if kind != "not_paused":
        pause(store, owner, manager)
    if kind == "owner":
        current["user"] = store.create_user("other-resume", "synthetic-unused")
    response = client.post("/api/semantic-workspace/tasks/resume-task/account-resume", json={"expected_generation": 9 if kind == "generation" else 0, "expected_active_revision": 2 if kind == "revision" else 1})
    assert response.status_code == (404 if kind == "owner" else 409), response.text
    assert queued == []


@pytest.mark.parametrize("waiting", [False, True])
def test_busy_stop_lock_returns_conflict_without_rebinding(workspace, waiting):
    from src.api.execution import execution_lock
    store, owner, manager, client, queued, _ = workspace
    pause(store, owner, manager, waiting=waiting)
    with execution_lock(store, owner, "workspace", "resume-task"):
        response = client.post("/api/semantic-workspace/tasks/resume-task/account-resume", json={"expected_generation": 0, "expected_active_revision": 1})
    assert response.status_code == 409 and "仍在停止" in response.json()["detail"]
    assert store.account_execution_binding(owner, "workspace", "resume-task")["generation"] == 0
    assert queued == []


def test_hard_stopped_runtime_uses_existing_revision_hook_and_retains_old_state(workspace, monkeypatch):
    from src.agentic_runtime.models import RuntimeTaskConfig, RuntimeStatus, RuntimeVersion
    from src.agentic_runtime.repository import AgenticRuntimeRepository
    store, owner, manager, client, queued, _ = workspace
    pause(store, owner, manager)
    repository = AgenticRuntimeRepository(settings.webui_db_path)
    repository.register(RuntimeTaskConfig(user_id=owner, task_id="resume-task", revision=1, runtime_version=RuntimeVersion.LEGACY))
    repository.update(owner, "resume-task", 1, status=RuntimeStatus.CANCELLED, run_id="old-stopped-run", session_file="old-checkpoint")
    original_hook = route._prepare_runtime_binding
    hooks = []

    def prepare(*args):
        selected, hook = original_hook(*args)
        def bind(conn):
            assert conn.in_transaction
            binding = conn.execute("SELECT generation,state FROM account_execution_bindings WHERE resource_kind='workspace' AND resource_id='resume-task'").fetchone()
            assert tuple(binding) == (1, "idle")
            hooks.append(True)
            hook(conn)
        return selected, bind

    monkeypatch.setattr(route, "_prepare_runtime_binding", prepare)
    response = client.post("/api/semantic-workspace/tasks/resume-task/account-resume", json={"expected_generation": 0, "expected_active_revision": 1})
    assert response.status_code == 200, response.text
    assert response.json()["strategy"] == "new_revision"
    assert hooks == [True] and queued == [(owner, "resume-task")]
    assert store.get_semantic_workspace_task(owner, "resume-task")["active_revision"] == 2
    old = repository.get(owner, "resume-task", 1)
    assert old["status"] is RuntimeStatus.CANCELLED and old["session_file"] == "old-checkpoint"
    assert repository.get(owner, "resume-task", 2) is not None


def test_revision_hook_failure_rolls_back_rebinding(workspace, monkeypatch):
    from src.agentic_runtime.models import RuntimeTaskConfig, RuntimeStatus, RuntimeVersion
    from src.agentic_runtime.repository import AgenticRuntimeRepository
    store, owner, manager, client, queued, _ = workspace
    pause(store, owner, manager)
    repository = AgenticRuntimeRepository(settings.webui_db_path)
    repository.register(RuntimeTaskConfig(user_id=owner, task_id="resume-task", revision=1, runtime_version=RuntimeVersion.LEGACY))
    repository.update(owner, "resume-task", 1, status=RuntimeStatus.CANCELLED, run_id="old-stopped-run")
    def fail(conn):
        raise RuntimeError("合成合同冲突")
    monkeypatch.setattr(route, "_prepare_runtime_binding", lambda plan, config: (RuntimeVersion.LEGACY, fail))
    response = client.post("/api/semantic-workspace/tasks/resume-task/account-resume", json={"expected_generation": 0, "expected_active_revision": 1})
    assert response.status_code == 409
    binding = store.account_execution_binding(owner, "workspace", "resume-task")
    assert binding["generation"] == 0 and binding["state"] == "paused"
    assert store.get_semantic_workspace_task(owner, "resume-task")["active_revision"] == 1
    assert queued == []



def test_preparation_never_rebinds_and_stale_request_cannot_adopt_new_epoch(workspace, monkeypatch):
    from src.agentic_runtime.models import RuntimeTaskConfig, RuntimeStatus, RuntimeVersion
    from src.agentic_runtime.repository import AgenticRuntimeRepository
    store, owner, manager, client, queued, _ = workspace
    pause(store, owner, manager)
    repository = AgenticRuntimeRepository(settings.webui_db_path)
    repository.register(RuntimeTaskConfig(user_id=owner, task_id="resume-task", revision=1, runtime_version=RuntimeVersion.LEGACY))
    repository.update(owner, "resume-task", 1, status=RuntimeStatus.CANCELLED, run_id="old-stopped-run")
    original = route._inherit_task_context_hook
    def changed(*args, **kwargs):
        binding = store.account_execution_binding(owner, "workspace", "resume-task")
        assert binding["generation"] == 0 and binding["state"] == "paused"
        store.update_user(owner, disabled=True)
        store.update_user(owner, disabled=False)
        return original(*args, **kwargs)
    monkeypatch.setattr(route, "_inherit_task_context_hook", changed)
    response = client.post("/api/semantic-workspace/tasks/resume-task/account-resume", json={"expected_generation": 0, "expected_active_revision": 1})
    assert response.status_code == 409
    assert store.account_execution_binding(owner, "workspace", "resume-task")["generation"] == 0
    assert store.get_semantic_workspace_task(owner, "resume-task")["active_revision"] == 1
    assert queued == []


def test_current_detail_offers_low_sensitivity_explicit_resume(workspace):
    store, owner, manager, client, queued, _ = workspace
    pause(store, owner, manager, waiting=True)
    response = client.get("/api/semantic-workspace/tasks/resume-task")
    assert response.status_code == 200, response.text
    assert response.json()["account_resume"] == {"generation": 0, "strategy": "waiting"}
    assert queued == []
