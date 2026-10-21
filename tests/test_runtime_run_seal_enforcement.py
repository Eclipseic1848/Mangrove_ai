"""封存历史 Run 后，执行与 Grant 均不得重发或改写原事实。"""
from __future__ import annotations

from contextlib import contextmanager
import asyncio
import sqlite3
import hashlib
from types import SimpleNamespace

import httpx
import pytest

from src.account_execution import execution_context
from src.agentic_runtime.document_tools import DocumentToolBroker
from src.agentic_runtime.kernel import AgentKernel
from src.agentic_runtime.models import PiRuntimeCheckpoint, RuntimeStatus
from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.api.store import WebUIStore
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database
from tests.test_agent_kernel import _ContractAdapter, _registered_repository, _request
from tests.test_document_tool_relay import InspectAdapter
from tests.test_account_execution_grants import broker_and_grant, relay
from tests.test_account_execution_grants import grants
from tests.test_runtime_run_seals import unknown_run, run_seal_database_template
from tests.test_account_execution_resume import workspace as account_workspace


@contextmanager
def sealed(database, owner="user-a", task="task-a", revision=1):
    # 合成记录只覆盖拒绝门；真实停止证明由 writer 的独立测试验证。
    with sqlite3.connect(database) as connection:
        connection.execute("""INSERT INTO runtime_run_seals
            (owner_id,task_id,revision,run_id,runtime_identity_sha256,snapshot_json,stop_proof_json,
             actor_id,reason,approval_reference,idempotency_key,request_sha256,sealed_at)
            VALUES (?,?,?,'synthetic-run',?,'{}','{}','synthetic-operator','合成封存',
                    'synthetic-approval','synthetic-key',?,'2026-10-20T00:00:00+00:00')""",
            (owner, task, revision, "a" * 64, "b" * 64))
    yield


def original_facts(database):
    with sqlite3.connect(database) as connection:
        return (
            connection.execute("SELECT * FROM agentic_runtime_runs ORDER BY user_id,task_id,revision").fetchall(),
            connection.execute("SELECT * FROM agentic_runtime_events ORDER BY sequence").fetchall(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["start", "resume", "steer", "cancel"])
async def test_sealed_kernel_rejects_before_manifest_or_adapter(tmp_path, operation):
    repository = _registered_repository(tmp_path)
    repository.update("user-a", "task-a", 1, status=RuntimeStatus.NEEDS_INPUT,
                      run_id="historical-run", failure={"error_code": "MODEL_OUTCOME_UNKNOWN", "cause_summary": "保留原事实"})
    repository.append_event("user-a", "task-a", 1, event_type="unknown", summary="保留原事件")
    request = _request(tmp_path)
    adapter = _ContractAdapter()
    manifest_calls = []
    async def resolve_manifest():
        manifest_calls.append(True)
        raise AssertionError("封存后不能解析外部 manifest")
    adapter.prepare_manifest = resolve_manifest
    kernel = AgentKernel(adapter=adapter, repository=repository)
    async def sink(event):
        raise AssertionError("封存后不能追加业务事件")
    with sealed(repository.db_path):
        before = original_facts(repository.db_path)
        with pytest.raises(PermissionError):
            if operation == "start":
                await kernel.start(request, on_event=sink)
            elif operation == "resume":
                await kernel.resume(request, checkpoint=PiRuntimeCheckpoint(run_id="historical-run", workspace_root=tmp_path), on_event=sink)
            elif operation == "steer":
                await kernel.steer("user-a", "task-a", 1, "继续")
            else:
                await kernel.cancel("user-a", "task-a", 1)
        assert original_facts(repository.db_path) == before
    assert manifest_calls == []
    assert (adapter.start_calls, adapter.resume_calls, adapter.cancel_calls) == (0, 0, 0)


@pytest.mark.parametrize("change", [{"clear_failure": True}, {"status": RuntimeStatus.RUNNING}, {"run_id": "replacement"}])
def test_repository_seal_preserves_original_bytes(tmp_path, change):
    repository = _registered_repository(tmp_path)
    repository.update("user-a", "task-a", 1, status=RuntimeStatus.NEEDS_INPUT,
                      failure={"error_code": "MODEL_OUTCOME_UNKNOWN", "cause_summary": "保留"})
    with sealed(repository.db_path):
        before = original_facts(repository.db_path)
        with pytest.raises(PermissionError):
            repository.update("user-a", "task-a", 1, **change)
        with pytest.raises(PermissionError):
            repository.append_event("user-a", "task-a", 1, event_type="late", summary="迟到结果")
        assert original_facts(repository.db_path) == before


@pytest.mark.asyncio
async def test_model_old_token_and_new_grant_cannot_send_after_seal(grants):
    _, repository, _, _ = grants
    sent = []
    broker, grant = broker_and_grant(grants, lambda request: sent.append(request) or httpx.Response(200))
    with sealed(repository.db_path, "synthetic-owner", "synthetic-task"):
        with pytest.raises(PermissionError):
            await relay(broker, grant)
        with pytest.raises(PermissionError):
            broker_and_grant(grants, lambda request: httpx.Response(200))
    assert sent == []


@pytest.mark.asyncio
async def test_document_old_token_and_new_grant_cannot_read_after_seal(tmp_path):
    database = migrated_webui_database(tmp_path / "document.db")
    authorization = seed_execution_owner(database, "document-owner")
    repository = AgenticRuntimeRepository(database)
    request = _request(tmp_path)
    retriever = InspectAdapter()
    broker = DocumentToolBroker(retriever=retriever, state_store=repository)
    def issue(revision=1):
        return broker.issue_grant(owner_user_id="document-owner", task_id="task-a", revision=revision,
                                  run_id=f"run-{revision}", sources=request.sources)
    with execution_context(authorization):
        grant = issue()
        with sealed(database, "document-owner"):
            with pytest.raises(PermissionError):
                issue()
            with pytest.raises(PermissionError):
                await broker.call(grant_token=grant.token, operation="inspect_source", payload={"source_id": "upload-a"})
            fresh = issue(2)
            assert (await broker.call(grant_token=fresh.token, operation="inspect_source", payload={"source_id": "upload-a"}))["source_id"] == "upload-a"


def test_seal_is_precise_to_owner_task_revision(tmp_path):
    repository = _registered_repository(tmp_path)
    with sealed(repository.db_path, "other-owner"):
        repository.update("user-a", "task-a", 1, status=RuntimeStatus.RUNNING)
        assert repository.get("user-a", "task-a", 1)["status"] is RuntimeStatus.RUNNING


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from src.api import semantic_workspace_runtime as runtime
    from src.api.routes import semantic_workspace as routes
    from src.config.settings import settings
    repository = _registered_repository(tmp_path)
    authorization = seed_execution_owner(repository.db_path, "user-a")
    store = WebUIStore(str(repository.db_path))
    with execution_context(authorization):
        store.create_semantic_workspace_task("user-a", task_id="task-a", title="合成历史任务",
            objective_text="保留历史", upload_ids=[], output_formats=["txt"], provider="local",
            model=None, external_api_confirmed=False)
    repository.update("user-a", "task-a", 1, status=RuntimeStatus.NEEDS_INPUT,
                      run_id="historical-run", failure={"error_code": "MODEL_OUTCOME_UNKNOWN"})
    monkeypatch.setattr(runtime, "get_store", lambda: store)
    monkeypatch.setattr(routes, "get_store", lambda: store)
    monkeypatch.setattr(settings, "webui_db_path", str(repository.db_path))
    return repository, store, runtime.SemanticWorkspaceManager.__new__(runtime.SemanticWorkspaceManager)


def test_invalid_seal_cannot_turn_account_resume_into_old_run_resume(workspace):
    repository, _, manager = workspace
    with sqlite3.connect(repository.db_path) as connection:
        connection.execute("UPDATE semantic_workspace_tasks SET status='paused' WHERE user_id='user-a' AND task_id='task-a'")
        connection.execute("UPDATE account_execution_bindings SET state='paused' WHERE owner_user_id='user-a' AND resource_kind='workspace' AND resource_id='task-a'")
    with sealed(repository.db_path):
        with pytest.raises(PermissionError):
            manager.account_resume_strategy("user-a", "task-a")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["enqueue", "execute", "answer", "cancel", "retry"])
async def test_workspace_rejects_before_clearing_original_failure(workspace, operation):
    repository, store, manager = workspace
    with sealed(repository.db_path):
        before = original_facts(repository.db_path)
        task_before = store.get_semantic_workspace_task("user-a", "task-a")
        with pytest.raises(PermissionError):
            if operation == "enqueue":
                manager.enqueue("user-a", "task-a")
            elif operation == "execute":
                await manager._run_task_authorized("user-a", "task-a")
            elif operation == "answer":
                await manager.answer("user-a", "task-a", "继续", accepted_turn_id="synthetic-turn")
            elif operation == "cancel":
                await manager.cancel("user-a", "task-a")
            else:
                await manager.retry_candidate_verification("user-a", "task-a")
        assert original_facts(repository.db_path) == before
        assert store.get_semantic_workspace_task("user-a", "task-a") == task_before


def test_source_cleanup_cannot_erase_sealed_artifacts_or_events(workspace, tmp_path, monkeypatch):
    from src.source_acquisition import deletion
    from src.config.settings import settings
    repository, _, _ = workspace
    root = tmp_path / "executions"
    monkeypatch.setattr(settings, "semantic_execution_root", root)
    run_root = root / "agentic-vnext" / hashlib.sha256(b"user-a").hexdigest()[:16] / "task-a" / "r1" / "historical-run"
    run_root.mkdir(parents=True)
    artifact = run_root / "preserved.txt"
    artifact.write_text("保留原产物", encoding="utf-8")
    repository.update("user-a", "task-a", 1, workspace_root=run_root)
    with sealed(repository.db_path):
        before = original_facts(repository.db_path)
        with pytest.raises(PermissionError):
            deletion._cleanup_runtime_copies("user-a", {"task-a": {1}})
        assert original_facts(repository.db_path) == before
        assert artifact.read_text(encoding="utf-8") == "保留原产物"


def test_production_pi_document_factory_uses_injected_persistent_seal_gate(tmp_path):
    from src.agentic_runtime.pi_runtime import PiRuntime
    repository = _registered_repository(tmp_path)
    runtime = PiRuntime(state_store=repository, execution_root=tmp_path / "runtime",
                        configure_as_default_document_broker=False)
    with sealed(repository.db_path):
        with pytest.raises(PermissionError):
            runtime._document_tool_broker.issue_grant(owner_user_id="user-a", task_id="task-a", revision=1,
                run_id="historical-run", sources=_request(tmp_path).sources)


def test_valid_stopped_seal_requires_new_revision_instead_of_old_run_resume(unknown_run, monkeypatch):
    from src import runtime_run_seals
    from src.api import semantic_workspace_runtime as runtime
    from src.config.settings import settings
    database = unknown_run["db"]
    runtime_run_seals.seal_unknown_run(database, **unknown_run["kwargs"])
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE semantic_workspace_tasks SET status='paused' WHERE user_id='owner' AND task_id='task'")
        connection.execute("UPDATE account_execution_bindings SET state='paused' WHERE owner_user_id='owner' AND resource_kind='workspace' AND resource_id='task'")
    store = WebUIStore(str(database))
    monkeypatch.setattr(runtime, "get_store", lambda: store)
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    before = original_facts(database)
    manager = runtime.SemanticWorkspaceManager.__new__(runtime.SemanticWorkspaceManager)
    assert manager.account_resume_strategy("owner", "task") == "new_revision"
    assert original_facts(database) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["initial", "retry", "request"])
async def test_candidate_verification_service_cannot_call_local_verifier_for_sealed_run(tmp_path, operation):
    from src.candidate_verification import CandidateVerificationService, SqliteCandidateVerificationRepository
    repository = _registered_repository(tmp_path)
    service = CandidateVerificationService.__new__(CandidateVerificationService)
    service._repository = SqliteCandidateVerificationRepository(repository.db_path)
    with sealed(repository.db_path):
        before = original_facts(repository.db_path)
        with pytest.raises(PermissionError):
            if operation == "initial":
                await service.verify_initial(request=_request(tmp_path), run_id="historical-run", candidates=(),
                    manifest_path=tmp_path / "manifest.json", verifier=None, actor_id="user-a",
                    idempotency_key="synthetic-key", goal_contract_hash="a" * 64, delivery_spec_hash="b" * 64)
            elif operation == "retry":
                await service.retry_current_semantic(owner_id="user-a", task_id="task-a", revision=1, verifier_factory=None)
            else:
                await service.request_reverification(owner_id="user-a", task_id="task-a", revision=1,
                    expected_previous_attempt_id="previous", external_api_confirmed=False,
                    idempotency_key="synthetic-key", verifier_factory=None)
        assert original_facts(repository.db_path) == before


@pytest.mark.asyncio
async def test_startup_skips_sealed_pending_run_without_rewriting_original_projection(workspace, monkeypatch):
    repository, store, manager = workspace
    with sqlite3.connect(repository.db_path) as connection:
        connection.execute("UPDATE semantic_workspace_tasks SET status='running' WHERE user_id='user-a' AND task_id='task-a'")
    async def wait_forever(*args):
        await asyncio.Event().wait()
    manager._workers = []
    monkeypatch.setattr(manager, "_worker", wait_forever)
    monkeypatch.setattr(manager, "_maintenance_loop", wait_forever)
    monkeypatch.setattr(manager, "_learning_loop", wait_forever)
    monkeypatch.setattr(manager, "_candidate_verification_module", lambda: SimpleNamespace(
        list_running_reverifications=lambda: [], list_requested_reverifications=lambda: []))
    with sealed(repository.db_path):
        before = store.get_semantic_workspace_task("user-a", "task-a")
        try:
            manager.start()
            assert store.get_semantic_workspace_task("user-a", "task-a") == before
        finally:
            tasks = [*manager._workers, manager._maintenance, manager._learning_worker]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def test_invalid_seal_blocks_explicit_new_revision_even_when_old_task_failed(account_workspace):
    from src.agentic_runtime.models import RuntimeTaskConfig, RuntimeVersion
    from src.api.routes import semantic_workspace as routes
    store, owner, _, _, queued, _ = account_workspace
    repository = AgenticRuntimeRepository(store.db_path)
    repository.register(RuntimeTaskConfig(user_id=owner, task_id="resume-task", revision=1,
                                        runtime_version=RuntimeVersion.LEGACY))
    repository.update(owner, "resume-task", 1, status=RuntimeStatus.FAILED, run_id="historical-run",
                      failure={"error_code": "MODEL_OUTCOME_UNKNOWN"})
    with execution_context(store.capture_account_execution(owner)):
        store.update_semantic_workspace_task(owner, "resume-task", status="failed")
        with sealed(store.db_path, owner, "resume-task"):
            before = original_facts(store.db_path)
            with pytest.raises(PermissionError):
                asyncio.run(routes._create_revision("resume-task",
                    routes.WorkspaceRevisionIn(instruction="显式新版本", expected_active_revision=1),
                    store.get_user(owner)))
            assert original_facts(store.db_path) == before
            assert store.get_semantic_workspace_task(owner, "resume-task")["active_revision"] == 1
            assert queued == []
