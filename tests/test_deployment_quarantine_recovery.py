"""首次维护隔离拒绝旧快照恢复与验证续跑；仅临时库和合成执行器。"""
import asyncio
from contextlib import closing
import hashlib
import json
import shutil
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src import account_execution as execution
from src.api import auth
from src.api.routes import chat
from src.api.schemas import ChatIn
from src.api.store import WebUIStore
from src.capability_governance.models import PlatformValidationEvidence, ValidationRunStatus
from src.capability_governance.platform_validation import PlatformValidationManager
from src.capability_governance.sqlite_repository import SqliteCapabilityGovernanceRepository
from src.config.settings import settings
from src.conductor.collection_recovery import load_recovery, progress_location
from src.deployment_quarantine import QuarantinedExecutionError
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database
from tests.test_account_execution_validation import run_for
from tests.test_evidence_collection_flow import spec_for


@pytest.fixture(scope="module")
def recovery_template(tmp_path_factory):
    return migrated_webui_database(tmp_path_factory.mktemp("quarantine-recovery") / "webui.db")


@pytest.fixture
def database(tmp_path, recovery_template):
    path = tmp_path / "webui.db"
    shutil.copyfile(recovery_template, path)
    seed_execution_owner(path, "owner-a")
    seed_execution_owner(path, "owner-b")
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("INSERT INTO account_execution_bindings VALUES ('owner-a','workspace','legacy-task',0,'idle',1)")
    return path


def quarantine(database, kind, resource_id):
    # 只验证消费接缝；注册器另行验证真实原件摘要及关联覆盖。
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM account_execution_bindings WHERE resource_id='legacy-task'").fetchone())
        digest = hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":")).encode("utf-8")).hexdigest()
        manifest = {"version": 1, "approval_reference": "synthetic-cutover-approval", "actor_id": "synthetic-admin",
                    "accepted_risk": "first_cutover_unknown_interruption",
                    "targets": [{"owner_id": "owner-a", "kind": kind, "resource_id": resource_id, "generation": 0}],
                    "records": [{"profile": "webui", "table": "account_execution_bindings",
                                 "key": {"owner_user_id": "owner-a", "resource_kind": "workspace", "resource_id": "legacy-task"},
                                 "sha256": digest}]}
        assert conn.execute("UPDATE app_settings SET value=? WHERE key=?",
                            (json.dumps(manifest, ensure_ascii=False),
                             "deployment.first_cutover_quarantine")).rowcount == 1


def rows(database, table):
    with closing(sqlite3.connect(database)) as conn:
        return conn.execute('SELECT * FROM "' + table + '" ORDER BY rowid').fetchall()


@pytest.fixture
def recovery(database, tmp_path, monkeypatch):
    store = WebUIStore(str(database))
    monkeypatch.setattr(auth, "_store", store)
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    monkeypatch.setattr(settings, "semantic_execution_root", str(tmp_path / "execution"))
    monkeypatch.setattr(settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr("src.data_prep.artifact_store._DEFAULT_ROOT", None)
    user = {"user_id": "owner-a", "role": "user", "execution_generation": 0}

    def snapshot(task_id, *, conversation_owner="owner-a"):
        conv_id = store.create_conversation(conversation_owner, "合成恢复任务")["conv_id"]
        spec = spec_for("设备说明", raw_only=True, initial_candidates=1, candidate_limit=1)
        saved = {"binding": "a" * 64,
                 "binding_fields": {"owner": "owner-a", "task": task_id, "spec": spec.model_dump(mode="json"),
                                    "connection": ["owner-a", "synthetic-connection", "version-1", "synthetic-model", conv_id, "original-run", 1]},
                 "account_identity": {"account_ref": "b" * 64}, "groups": [{"done": False}],
                 "request": {"session_id": conv_id, "user_input": spec.intent, "provider": "bound", "model": "synthetic-model"}}
        artifacts, key = progress_location("owner-a", task_id)
        artifacts.write_json(key, "progress.json", saved)
        return conv_id, artifacts.task_dir(key) / "progress.json"

    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[auth.get_current_user] = lambda: user
    app.dependency_overrides[auth.get_execution_user] = lambda: user
    return store, user, snapshot, app


@pytest.mark.parametrize("blocked_identity", ["collection-task", "frozen-task"])
def test_chat_resume_rejects_old_identity_before_new_binding(database, recovery, monkeypatch, blocked_identity):
    store, _user, snapshot, app = recovery
    conv_id, progress_file = snapshot("collection-old")
    new_conv, _ = snapshot("collection-new")
    quarantine(database, "task", "collection-old" if blocked_identity == "collection-task" else conv_id)
    before = {name: rows(database, name) for name in ("account_execution_bindings", "messages", "conversations")}
    original = progress_file.read_bytes()
    invoked = []

    async def pipeline(body, request, user, recovered=None, **kwargs):
        invoked.append(recovered["task_id"])
        return {"task_id": recovered["task_id"]}

    monkeypatch.setattr(chat, "_chat_stream", pipeline)
    with TestClient(app) as client:
        denied = client.post("/api/chat/collections/collection-old/resume", json={"request_id": "a" * 32})
        assert denied.status_code == 409, denied.text
        assert invoked == []
        assert progress_file.read_bytes() == original
        assert {name: rows(database, name) for name in before} == before
        allowed = client.post("/api/chat/collections/collection-new/resume", json={"request_id": "b" * 32})
        assert allowed.status_code == 200, allowed.text
    assert invoked == ["collection-new"]
    assert new_conv != conv_id
    assert store.list_messages(conv_id) == []


@pytest.mark.parametrize("blocked_identity", ["collection-task", "frozen-task"])
def test_direct_chat_recovery_rejects_before_connection_freeze(database, recovery, monkeypatch, blocked_identity):
    import src.model_connections as connections

    _store, user, snapshot, _app = recovery
    conv_id, progress_file = snapshot("collection-old")
    saved = load_recovery("owner-a", "collection-old")
    quarantine(database, "task", "collection-old" if blocked_identity == "collection-task" else conv_id)
    before = {name: rows(database, name) for name in ("account_execution_bindings", "messages", "conversations")}
    original = progress_file.read_bytes()
    freezes = []

    def broker():
        freezes.append("freeze")
        raise AssertionError("隔离恢复不得触及模型连接")

    monkeypatch.setattr(connections, "get_default_broker", broker)
    body = ChatIn(conv_id=conv_id, content="恢复原任务", model="synthetic-model", mode="legacy_analysis",
                  model_connection_id="synthetic-connection", model_connection_version="version-1", external_api_confirmed=True)
    with pytest.raises(HTTPException) as error:
        asyncio.run(chat._chat_stream(body, SimpleNamespace(), user, saved))
    assert error.value.status_code == 409
    assert freezes == []
    assert progress_file.read_bytes() == original
    assert {name: rows(database, name) for name in before} == before


def test_chat_recovery_preserves_owner_check_before_quarantine(database, recovery):
    _store, _user, snapshot, app = recovery
    snapshot("collection-old", conversation_owner="owner-b")
    quarantine(database, "task", "collection-old")
    with TestClient(app) as client:
        response = client.post("/api/chat/collections/collection-old/resume", json={"request_id": "a" * 32})
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("platform", [False, True])
def test_validation_execution_rejects_before_yield_and_preserves_facts(database, platform):
    repository = SqliteCapabilityGovernanceRepository(str(database))
    auth_context = execution.ExecutionAuthorization("owner-a", 0)
    create = repository.create_platform_validation_run if platform else repository.create_validation_run
    with execution.execution_context(auth_context):
        run = create(run_for(platform))
    resource_id = "platform:" + run.run_id if platform else run.run_id
    quarantine(database, "validation", resource_id)
    before = {name: rows(database, name) for name in ("account_execution_bindings", "capability_validation_runs", "capability_platform_validation_runs")}
    entered = []
    with pytest.raises(QuarantinedExecutionError):
        with repository.validation_execution(run):
            entered.append(run.run_id)
    assert entered == []
    assert {name: rows(database, name) for name in before} == before


def test_new_personal_validation_cannot_adopt_quarantined_task_snapshot(database):
    repository = SqliteCapabilityGovernanceRepository(str(database))
    run = run_for(False)
    quarantine(database, "task", run.task_ref.task_id)
    before = {name: rows(database, name) for name in ("account_execution_bindings", "capability_validation_runs", "capability_validation_idempotency")}
    with execution.execution_context(execution.ExecutionAuthorization("owner-a", 0)):
        with pytest.raises(QuarantinedExecutionError):
            repository.create_validation_run(run)
    assert {name: rows(database, name) for name in before} == before


def test_platform_worker_skips_quarantine_without_confirming_stop_or_starving_new_run(database, tmp_path, monkeypatch):
    repository = SqliteCapabilityGovernanceRepository(str(database))
    with execution.execution_context(execution.ExecutionAuthorization("owner-a", 0)):
        old = repository.create_platform_validation_run(run_for(True))
        new = repository.create_platform_validation_run(run_for(True).model_copy(update={
            "run_id": "new-run", "idempotency_key": "synthetic-new",
            "target": run_for(True).target.model_copy(update={"pack_id": "synthetic-new", "digest": "sha256:" + "c" * 64}),
        }))
    quarantine(database, "validation", "platform:" + old.run_id)
    old_payload = repository.get_platform_validation_run(old.run_id)
    old_binding = repository.get_validation_binding(old)
    invoked = []

    class Executor:
        def execute(self, run, step):
            invoked.append(run.run_id)
            return PlatformValidationEvidence(step=step, status="failed", evidence_ref="evidence://synthetic/check",
                                              evidence_sha256="a" * 64, summary="合成独立新验证")

    def forbidden_stop(*args, **kwargs):
        raise AssertionError("隔离拒绝不能确认为已停止")

    monkeypatch.setattr(repository, "confirm_validation_stopped", forbidden_stop)
    manager = PlatformValidationManager(repository, executor=Executor(), signing=None, layout_path=tmp_path,
                                        private_key_path=tmp_path / "synthetic-private", public_key_path=tmp_path / "synthetic-public")
    manager.run_once()
    assert invoked == [new.run_id]
    assert repository.get_platform_validation_run(old.run_id) == old_payload
    assert repository.get_validation_binding(old) == old_binding
    assert repository.get_platform_validation_run(new.run_id).status is ValidationRunStatus.FAILED
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("SELECT count(*) FROM capability_platform_validation_leases WHERE run_id=?", (old.run_id,)).fetchone() == (0,)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["startup", "maintenance"])
@pytest.mark.parametrize("old_status", ["running", "cancelling"])
async def test_workspace_recovery_skips_quarantine_without_starving_new_task(database, tmp_path, monkeypatch, operation, old_status):
    from src.api import semantic_workspace_runtime as runtime
    from src.services.deployment_drain import DeploymentGate

    store = WebUIStore(str(database))
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    monkeypatch.setattr(runtime, "get_store", lambda: store)
    gate = DeploymentGate(tmp_path / ".deployment-control")
    monkeypatch.setattr(runtime, "get_deployment_gate", lambda: gate)
    with execution.execution_context(store.capture_account_execution("owner-a")):
        for task_id, status in (("legacy-task", old_status), ("independent-task", "running")):
            store.create_semantic_workspace_task("owner-a", task_id=task_id, title="合成恢复任务",
                objective_text="仅本地队列恢复", upload_ids=[], output_formats=[], provider="local",
                model=None, external_api_confirmed=False)
            store.update_semantic_workspace_task("owner-a", task_id, status=status)
    quarantine(database, "task", "legacy-task")
    before = store.get_semantic_workspace_task("owner-a", "legacy-task")
    original_binding = store.account_execution_binding("owner-a", "workspace", "legacy-task")
    manager = runtime.SemanticWorkspaceManager.__new__(runtime.SemanticWorkspaceManager)
    manager._workers, manager._queue = [], asyncio.Queue()
    manager._active, manager._queued, manager._delivery_retry_after = {}, set(), {}
    monkeypatch.setattr(manager, "_candidate_verification_module", lambda: SimpleNamespace(
        list_running_reverifications=lambda: [], list_requested_reverifications=lambda: []))

    async def forbidden_cancel(*args, **kwargs):
        raise AssertionError("隔离旧任务不能被维护循环取消或确认停止")

    monkeypatch.setattr(manager, "cancel", forbidden_cancel)
    if operation == "startup":
        async def park(*args):
            await asyncio.Event().wait()

        for name in ("_worker", "_maintenance_loop", "_learning_loop"):
            monkeypatch.setattr(manager, name, park)
        try:
            manager.start()
        finally:
            tasks = [*manager._workers, manager._maintenance, manager._learning_worker]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    else:
        sleeps = []

        async def one_round(_seconds):
            if sleeps:
                raise asyncio.CancelledError
            sleeps.append(1)

        monkeypatch.setattr(runtime, "asyncio", SimpleNamespace(sleep=one_round))
        with pytest.raises(asyncio.CancelledError):
            await manager._maintenance_loop()
    assert manager._queue.qsize() == 1
    assert manager._queue.get_nowait() == ("owner-a", "independent-task")
    assert store.get_semantic_workspace_task("owner-a", "legacy-task") == before
    assert store.account_execution_binding("owner-a", "workspace", "legacy-task") == original_binding
