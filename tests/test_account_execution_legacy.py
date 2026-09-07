"""Legacy 编译写入与复跑清理的真实 SQLite 授权屏障。"""
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from filelock import Timeout

from src import account_execution as execution
from src.api import auth
from src.api.execution import execution_lock
from src.api.routes import data_tasks, semantic_plans
from src.api.store import WebUIStore
from src.config.settings import settings
from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.models import DataPrepTaskSpec
from src.semantic_harness.compiler_models import CompileRequest, CompileResult, CompileStatus, PlanProvenance
from src.semantic_harness.inspection_models import BindResult, BindStatus, BindProvenance
from src.semantic_harness.physical_models import PhysicalPlan, RuntimePolicy
from tests.database_migration_helpers import migrated_webui_database
from tests.test_semantic_plan_api import ApiFakeGenerator


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "legacy.db")))
    monkeypatch.setattr(auth, "_store", store)
    monkeypatch.setattr(settings, "webui_db_path", store.db_path)
    monkeypatch.setattr(settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr("src.data_prep.artifact_store._DEFAULT_ROOT", None)
    owner = store.create_user("legacy-fixture", "synthetic-unused")["user_id"]
    authorization = store.capture_account_execution(owner)
    app = FastAPI()
    app.include_router(data_tasks.router)
    app.include_router(semantic_plans.router)
    app.dependency_overrides[auth.get_current_user] = lambda: store.get_user(owner)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield store, owner, authorization, client


def save(store, owner, kind):
    if kind == "plan":
        request = CompileRequest(task_id="synthetic-task", objective_text="虚构编译目标")
        result = CompileResult(plan_id="synthetic-plan", task_id=request.task_id, revision=1, status=CompileStatus.FAILED,
                               provenance=PlanProvenance(provider="local", model="synthetic", prompt_version="1", prompt_sha256="1" * 64, repair_attempts=0))
        return store.save_semantic_plan_revision(owner, request=request, result=result)
    if kind == "binding":
        result = BindResult(status=BindStatus.BLOCKED, logical_plan_id="synthetic-plan", logical_plan_revision=1, binding_revision=1,
                            provenance=BindProvenance(binder_version="1", threshold_version="1", auto_bind_threshold=0.9, margin_threshold=0.1, semantic_backend="synthetic"))
        return store.save_semantic_binding_revision(owner, reports=(), result=result)
    plan = PhysicalPlan(physical_plan_id="synthetic-physical", logical_plan_id="synthetic-plan", logical_plan_revision=1,
                        logical_plan_hash="1" * 64, bound_plan_id="synthetic-bound", bound_plan_hash="2" * 64, binding_revision=1,
                        status="needs_user", capability_id="synthetic.table", capability_version="1", diagnostics=("虚构待确认",),
                        runtime_policy=RuntimePolicy(profile="windows_local", threads=1, memory_limit="64MB", timeout_seconds=1,
                                                     arrow_batch_rows=1024, max_temp_bytes=1024, max_input_bytes=1024, max_input_rows=10))
    return store.save_physical_plan(owner, plan)


@pytest.mark.parametrize("kind", ["plan", "binding", "physical"])
def test_late_compile_insert_cannot_adopt_reenabled_generation(workspace, kind):
    store, owner, authorization, _ = workspace
    store.update_user(owner, disabled=True)
    store.update_user(owner, disabled=False)
    with execution.execution_context(authorization), pytest.raises(execution.ExecutionDenied):
        save(store, owner, kind)
    table = {"plan": "semantic_plan_revisions", "binding": "semantic_binding_revisions", "physical": "physical_plan_revisions"}[kind]
    with store._conn() as conn:
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("transition", [False, True])
def test_data_task_write_checks_real_owner_even_if_shadow_binding_exists(workspace, transition):
    store, owner, authorization, _ = workspace
    with execution.execution_context(authorization):
        store.create_data_prep_task(owner, "same-id", {"intent": "虚构任务"}, status="READY")
    other = store.create_user("shadow-owner", "synthetic-unused")["user_id"]
    other_auth = store.capture_account_execution(other)
    with store.account_execution_transaction(other_auth) as conn:
        execution.bind_execution(conn, other_auth, "data", "same-id", state="idle", now=time.time())
    with execution.execution_context(other_auth), pytest.raises(execution.ExecutionDenied):
        if transition:
            store.transition_data_prep_task("same-id", from_statuses={"READY"}, to_status="RUNNING")
        else:
            store.update_data_prep_task("same-id", status="FAILED", error="跨账号改写")
    assert store.get_data_prep_task("same-id")["status"] == "READY"


def ready_task(store, owner, authorization):
    with execution.execution_context(authorization):
        store.create_data_prep_task(owner, "rerun-task", DataPrepTaskSpec(intent="虚构任务").model_dump(mode="json"), status="SUCCEEDED")
    directory = ArtifactStore().task_dir("rerun-task")
    directory.mkdir(parents=True)
    artifact = directory / "old.txt"
    artifact.write_text("必须保留的旧产物", encoding="utf-8")
    return artifact


def test_denied_rerun_does_not_delete_existing_artifacts(workspace):
    store, owner, authorization, client = workspace
    artifact = ready_task(store, owner, authorization)
    store.update_user(owner, disabled=True)
    store.update_user(owner, disabled=False)
    response = client.post("/api/data-tasks/rerun-task/rerun")
    assert response.status_code == 409
    assert artifact.read_text(encoding="utf-8") == "必须保留的旧产物"


def test_busy_rerun_does_not_delete_before_acquiring_execution_lock(workspace):
    store, owner, authorization, client = workspace
    artifact = ready_task(store, owner, authorization)
    with execution_lock(store, owner, "data", "rerun-task"):
        response = client.post("/api/data-tasks/rerun-task/rerun")
    assert response.status_code == 409
    assert artifact.exists()
    assert store.get_data_prep_task("rerun-task")["status"] == "SUCCEEDED"


def test_authorized_rerun_cleans_inside_one_real_execution_lock(workspace, monkeypatch):
    store, owner, authorization, client = workspace
    artifact = ready_task(store, owner, authorization)
    calls = []
    async def run(spec, task_id, **kwargs):
        assert not artifact.exists()
        with pytest.raises(Timeout):
            execution_lock(store, owner, "data", task_id).acquire(timeout=0)
        calls.append(task_id)
        return {"status": "SUCCEEDED"}
    monkeypatch.setattr(data_tasks, "run_data_prep", run)
    response = client.post("/api/data-tasks/rerun-task/rerun")
    assert response.status_code == 200, response.text
    assert calls == ["rerun-task"]


def test_http_compile_has_persistent_binding_during_real_generator_call(workspace, monkeypatch):
    store, owner, _, client = workspace
    observed = []
    class Generator(ApiFakeGenerator):
        async def generate(self, *args, **kwargs):
            with store._conn() as conn:
                rows = conn.execute("SELECT resource_id,state FROM account_execution_bindings WHERE owner_user_id=? AND resource_kind='data'", (owner,)).fetchall()
            observed.extend(tuple(row) for row in rows)
            return await super().generate(*args, **kwargs)
    monkeypatch.setattr(semantic_plans, "_build_generator", lambda **_: Generator())
    response = client.post("/api/semantic-plans/compile", json={"task_id": "compiler-task", "objective_text": "只保留谢超群明细", "artifact_ids": ["synthetic-upload"], "provider": "local"})
    assert response.status_code == 200, response.text
    assert len(observed) == 1 and observed[0][0].startswith("compile_") and observed[0][1] == "active"
    with store._conn() as conn:
        before = conn.execute("SELECT count(*) FROM account_execution_bindings").fetchone()[0]
    assert client.get(f"/api/semantic-plans/{response.json()['plan_id']}/revisions").status_code == 200
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM account_execution_bindings").fetchone()[0] == before
        assert conn.execute("SELECT state FROM account_execution_bindings").fetchone()[0] == "idle"



def test_late_checkpoint_cannot_write_after_account_epoch_changes(workspace):
    store, owner, authorization, _ = workspace
    ready_task(store, owner, authorization)
    store.update_user(owner, disabled=True)
    store.update_user(owner, disabled=False)
    with execution.execution_context(authorization), pytest.raises(execution.ExecutionDenied):
        store.set_task_checkpoint("rerun-task", {"last_row": 7})
    assert store.get_task_checkpoint("rerun-task") is None


@pytest.mark.parametrize("kind", ["plan", "binding", "physical"])
def test_platform_compile_store_requires_context(workspace, kind):
    store, owner, _, _ = workspace
    with pytest.raises(execution.ExecutionDenied):
        save(store, owner, kind)


def test_http_compile_discards_output_if_account_changes_inside_generator(workspace, monkeypatch):
    store, owner, _, client = workspace
    class Generator(ApiFakeGenerator):
        async def generate(self, *args, **kwargs):
            store.update_user(owner, disabled=True)
            store.update_user(owner, disabled=False)
            return await super().generate(*args, **kwargs)
    monkeypatch.setattr(semantic_plans, "_build_generator", lambda **_: Generator())
    response = client.post("/api/semantic-plans/compile", json={"task_id": "late-compiler", "objective_text": "只保留谢超群明细", "artifact_ids": ["synthetic-upload"], "provider": "local"})
    assert response.status_code == 409, response.text
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM semantic_plan_revisions").fetchone()[0] == 0
        assert conn.execute("SELECT generation,state FROM account_execution_bindings WHERE resource_kind='data'").fetchone()["state"] == "paused"


@pytest.mark.asyncio
async def test_pure_compiler_without_platform_context_remains_usable():
    from src.semantic_harness.compiler_graph import compile_semantic_plan
    result = await compile_semantic_plan(CompileRequest(task_id="standalone", objective_text="只保留谢超群明细", artifact_ids=("synthetic-upload",)), generator=ApiFakeGenerator())
    assert result.plan_id and result.revision == 1
