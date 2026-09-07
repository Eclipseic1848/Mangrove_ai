"""真实 G1 入口使用隔离账号库，外部 Runtime 全部替身。"""
import asyncio
import hashlib

import pytest

from src.account_execution import current_authorization, ExecutionDenied
from src.agentic_runtime.models import CandidateArtifact, SourceInput, PiRuntimeRequest, PiRuntimeResult, RuntimeStatus, VerificationReport, VerificationStatus
from src.api.store import WebUIStore
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database
from tests.test_g1_independent_runner import _load_run_g1


@pytest.mark.parametrize("revoke", [None, "runtime", "assertion", "unknown"])
def test_real_driver_freezes_owner_through_runtime_and_retry(tmp_path, revoke):
    driver = _load_run_g1()
    driver.EVALS_ROOT = tmp_path
    driver.RUNS_DIR = tmp_path / "runs"
    driver.FORMAL_DELIVERY_DB = tmp_path / "evaluation.db"
    driver.FORMAL_DELIVERY_ROOT = tmp_path / "deliveries"
    migrated_webui_database(driver.FORMAL_DELIVERY_DB)
    seed_execution_owner(driver.FORMAL_DELIVERY_DB)
    store = WebUIStore(driver.FORMAL_DELIVERY_DB)
    candidate = tmp_path / "result.csv"
    candidate.write_text("team,total\nA,12\n", encoding="utf-8")
    request = PiRuntimeRequest(user_id="owner-a", task_id="g1-isolated", revision=1,
        objective_text="汇总", requested_output_formats=("csv",), sources=(SourceInput(upload_id="source", original_name=candidate.name, host_path=candidate, sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(), media_type="text/csv"),),
        model="fake", base_url="http://127.0.0.1:1/v1", api_key="fake")
    calls = []

    async def runtime(*args):
        auth = current_authorization()
        calls.append(auth.generation)
        store.require_account_execution(auth, "workspace", request.task_id)
        if revoke == "unknown":
            raise RuntimeError("外部结果未知")
        if revoke == "runtime":
            store.update_user("owner-a", disabled=True)
            store.update_user("owner-a", disabled=False)
        return PiRuntimeResult(status=RuntimeStatus.CANDIDATE_READY, run_id="g1-run",
            workspace_root=tmp_path, candidates=(CandidateArtifact(artifact_id="csv", filename=candidate.name,
                format="csv", host_path=candidate, sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
                size_bytes=candidate.stat().st_size, openable=True, qa_checks=("reopened",)),),
            verification=VerificationReport(status=VerificationStatus.PASSED, summary="通过", checks=(), evidence_count=1))

    def assertion(*args):
        if revoke == "assertion":
            store.update_user("owner-a", disabled=True)
            store.update_user("owner-a", disabled=False)

    invocation = driver.run_case({"id": "isolated", "objective": "汇总", "output_format": "csv"},
        1, 2, {"kind": "local"}, request_factory=lambda *args: request,
        runtime_runner=runtime, candidate_assertion=assertion)
    if revoke == "unknown":
        with pytest.raises(RuntimeError, match="外部结果未知"):
            asyncio.run(invocation)
        assert calls == [0]
        assert store.account_execution_binding("owner-a", "workspace", request.task_id)["state"] == "cleanup_failed"
        return
    if revoke:
        with pytest.raises(ExecutionDenied):
            asyncio.run(invocation)
        assert calls == [0]
        return
    result = asyncio.run(invocation)
    assert calls == [0]
    assert result["passed"] is (not revoke)
    if not revoke:
        assert result["attempts"][0]["formal_output_ids"]


def test_local_runtime_cancel_uses_evaluation_broker_only(tmp_path, monkeypatch):
    from src.agentic_runtime.pi_runtime import PiRuntime
    from src.agentic_runtime.repository import AgenticRuntimeRepository
    from src.agentic_runtime.document_tools import DocumentToolBroker
    driver = _load_run_g1()
    driver.FORMAL_DELIVERY_DB = migrated_webui_database(tmp_path / "evaluation.db")
    seed_execution_owner(driver.FORMAL_DELIVERY_DB)
    documents = DocumentToolBroker(retriever=object(), state_store=AgenticRuntimeRepository(driver.FORMAL_DELIVERY_DB))
    monkeypatch.setattr(driver, "ensure_document_relay", lambda *args: (documents, "http://127.0.0.1:1/internal/document-tools"))
    calls = []

    def forbidden():
        calls.append("default")
        raise AssertionError("禁止评测读取产品默认 Broker")

    monkeypatch.setattr("src.agentic_runtime.pi_runtime.get_default_broker", forbidden)

    async def start(runtime, request, **kwargs):
        assert runtime._state_store.db_path == driver.FORMAL_DELIVERY_DB
        assert runtime._connection_broker._repository.db_path == str(driver.FORMAL_DELIVERY_DB)
        await runtime.cancel(request.user_id, request.task_id, request.revision)
        return "cancelled"

    monkeypatch.setattr(PiRuntime, "start", start)
    from types import SimpleNamespace
    request = SimpleNamespace(user_id="owner-a", task_id="task", revision=1, model_connection_id=None)
    assert asyncio.run(driver._run_runtime(request, tmp_path / "runtime", 1, None)) == "cancelled"
    assert calls == []


def test_external_runtime_requires_explicit_same_database(tmp_path):
    from types import SimpleNamespace
    driver = _load_run_g1()
    driver.FORMAL_DELIVERY_DB = tmp_path / "evaluation.db"
    with pytest.raises(ValueError, match="缺少显式"):
        driver._resolve_model_route("connection", "model")
    with pytest.raises(ValueError, match="正式评测库"):
        driver.configure_evaluation_model_broker(SimpleNamespace(_repository=SimpleNamespace(db_path=tmp_path / "other.db")), "http://127.0.0.1:1")


@pytest.mark.parametrize("missing", ["owner", "binding"])
def test_evaluation_entry_does_not_create_owner_or_repair_missing_binding(tmp_path, missing):
    import sqlite3
    from types import SimpleNamespace
    from src.account_execution import ExecutionDenied
    from src.evaluation.account_execution import evaluation_execution
    database = migrated_webui_database(tmp_path / "evaluation.db")
    request = SimpleNamespace(user_id="owner-a", task_id="task", revision=1, sources=(),
        table_output_contracts=(), objective_text="测试", requested_output_formats=("csv",),
        model="fake", model_connection_model=None, external_api_confirmed=False)
    async def enter():
        async with evaluation_execution(database, request):
            pass

    if missing == "binding":
        seed_execution_owner(database)
        asyncio.run(enter())
        with sqlite3.connect(database) as conn:
            conn.execute("DELETE FROM account_execution_bindings")
    with pytest.raises(ExecutionDenied):
        asyncio.run(enter())


def test_live_evaluation_cannot_be_confirmed_paused(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src.evaluation.account_execution import evaluation_execution
    from src.api import semantic_workspace_runtime as workspace
    database = migrated_webui_database(tmp_path / "evaluation.db")
    seed_execution_owner(database)
    store = WebUIStore(database)
    monkeypatch.setattr(workspace, "get_store", lambda: store)
    monkeypatch.setattr(workspace.settings, "webui_db_path", str(database))
    monkeypatch.setattr(workspace.settings, "semantic_execution_root", str(tmp_path / "outputs"))
    manager = workspace.SemanticWorkspaceManager()
    request = SimpleNamespace(user_id="owner-a", task_id="task", revision=1, sources=(),
        table_output_contracts=(), objective_text="测试", requested_output_formats=("csv",),
        model="fake", model_connection_model=None, external_api_confirmed=False)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def fake_runtime():
            async with evaluation_execution(database, request):
                entered.set()
                await release.wait()

        running = asyncio.create_task(fake_runtime())
        await entered.wait()
        store.update_user("owner-a", disabled=True)
        try:
            assert await manager.pause_account_execution("owner-a", "task", expected_generation=0) is False
            assert store.account_execution_binding("owner-a", "workspace", "task")["state"] == "active"
        finally:
            release.set()
            outcomes = await asyncio.gather(running, return_exceptions=True)
            assert isinstance(outcomes[0], ExecutionDenied)
        assert store.account_execution_binding("owner-a", "workspace", "task")["state"] == "paused"

    asyncio.run(scenario())


@pytest.mark.parametrize("wrong_owner", [False, True])
def test_existing_old_authorization_cannot_be_recaptured_for_first_task(tmp_path, wrong_owner):
    from types import SimpleNamespace
    from src.account_execution import execution_context
    from src.evaluation.account_execution import evaluation_execution
    database = migrated_webui_database(tmp_path / "evaluation.db")
    old = seed_execution_owner(database)
    store = WebUIStore(database)
    if wrong_owner:
        seed_execution_owner(database, "owner-b")
    else:
        store.update_user("owner-a", disabled=True)
        store.update_user("owner-a", disabled=False)
    request = SimpleNamespace(user_id="owner-b" if wrong_owner else "owner-a", task_id="late-first", revision=1, sources=(),
        table_output_contracts=(), objective_text="测试", requested_output_formats=("csv",),
        model="fake", model_connection_model=None, external_api_confirmed=False)

    async def scenario():
        with execution_context(old):
            with pytest.raises(ExecutionDenied):
                async with evaluation_execution(database, request):
                    pass

    asyncio.run(scenario())
    assert store.get_semantic_workspace_task(request.user_id, "late-first") is None
