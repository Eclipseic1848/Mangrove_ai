"""账号执行阻断穿过工作台队列、安全点与恢复；仅临时库和虚构执行器。"""
import asyncio
from types import SimpleNamespace
from datetime import datetime, timezone
import hashlib

import pytest

from src.api import auth
from src.api import semantic_workspace_runtime as runtime_mod
from src.api.semantic_workspace_runtime import SemanticWorkspaceManager
from src.api.store import WebUIStore
from src.account_execution import ExecutionDenied, capture_authorization, execution_context
from src.agentic_runtime import coremind_runtime
from src.config.settings import settings
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    path = migrated_webui_database(tmp_path / "account-runtime.db")
    store = WebUIStore(str(path))
    monkeypatch.setattr(settings, "webui_db_path", str(path))
    monkeypatch.setattr(settings, "semantic_execution_root", str(tmp_path / "executions"))
    monkeypatch.setattr(settings, "data_prep_upload_root", str(tmp_path / "uploads"))
    monkeypatch.setattr(auth, "_store", store)
    monkeypatch.setattr(runtime_mod, "get_store", lambda: store)
    owner = store.create_user("synthetic-runtime-owner", auth.hash_password("synthetic-password"), pending=False)
    with store._conn() as conn:
        authorization = capture_authorization(conn, owner["user_id"])
    with execution_context(authorization):
        store.create_semantic_workspace_task(owner["user_id"], task_id="synthetic-task", title="虚构任务", objective_text="虚构目标", upload_ids=[], output_formats=[], provider="local", model=None, external_api_confirmed=False)
        yield store, owner, SemanticWorkspaceManager()


@pytest.mark.parametrize("change", [{"disabled": True}, {"pending": True}])
def test_blocked_owner_cannot_launch_queued_workspace(workspace, monkeypatch, change):
    store, owner, manager = workspace
    launched = []

    async def run(*identity):
        launched.append(identity)

    monkeypatch.setattr(manager, "_run_task", run)
    store.update_user(owner["user_id"], **change)
    asyncio.run(manager._launch_job(owner["user_id"], "synthetic-task"))
    assert launched == []
    assert store.get_semantic_workspace_task(owner["user_id"], "synthetic-task")["status"] == "paused"


def test_active_owner_can_launch_workspace(workspace, monkeypatch):
    _, owner, manager = workspace
    launched = []

    async def run(*identity):
        launched.append(identity)

    monkeypatch.setattr(manager, "_run_task", run)
    asyncio.run(manager._launch_job(owner["user_id"], "synthetic-task"))
    assert launched == [(owner["user_id"], "synthetic-task")]


def test_account_pause_preserves_original_question(workspace, monkeypatch):
    store, owner, manager = workspace
    question = {"kind": "harness", "question_id": "synthetic-question", "resume_token": "synthetic-checkpoint", "prompt": "保留原问题"}
    store.update_semantic_workspace_task(owner["user_id"], "synthetic-task", status="needs_input", question=question)
    store.update_user(owner["user_id"], disabled=True)

    async def run(*_):
        pass

    monkeypatch.setattr(manager, "_run_task", run)
    asyncio.run(manager._launch_job(owner["user_id"], "synthetic-task"))
    task = store.get_semantic_workspace_task(owner["user_id"], "synthetic-task")
    assert task["status"] == "paused"
    assert task["question"] == question


def test_cleanup_failure_never_confirms_account_pause(workspace, monkeypatch):
    store, owner, manager = workspace

    async def cleanup(*args, **kwargs):
        return False

    async def run(*_):
        pass

    monkeypatch.setattr(manager, "_confirm_runtime_stopped", cleanup)
    monkeypatch.setattr(manager, "_run_task", run)
    store.update_user(owner["user_id"], disabled=True)
    asyncio.run(manager._launch_job(owner["user_id"], "synthetic-task"))
    assert store.get_semantic_workspace_task(owner["user_id"], "synthetic-task")["status"] == "pausing"


def test_disable_during_compile_stops_next_stage(workspace, monkeypatch):
    store, owner, manager = workspace
    bound = []

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def compile_plan(*_):
            entered.set()
            await release.wait()
            return {"status": "ready", "plan_id": "synthetic-plan", "revision": 1, "summary": "虚构编译完成"}

        async def bind(*args, **kwargs):
            bound.append(True)
            return {"status": "failed"}

        monkeypatch.setattr(manager, "_compile", compile_plan)
        monkeypatch.setattr(manager, "_bind", bind)
        running = asyncio.create_task(manager._run_task_inner(owner["user_id"], "synthetic-task"))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            store.update_user(owner["user_id"], disabled=True)
        finally:
            release.set()
            await asyncio.wait_for(running, 3)

    asyncio.run(scenario())
    assert bound == []
    assert store.get_semantic_workspace_task(owner["user_id"], "synthetic-task")["status"] == "paused"


def test_reenable_and_restart_do_not_revive_old_workspace(workspace, monkeypatch):
    store, owner, manager = workspace
    launched = []

    async def run(*identity):
        launched.append(identity)

    def unavailable():
        raise RuntimeError("虚构候选模块不参与本测试")

    monkeypatch.setattr(manager, "_run_task", run)
    monkeypatch.setattr(manager, "_candidate_verification_module", unavailable)
    store.update_user(owner["user_id"], disabled=True)
    store.update_user(owner["user_id"], disabled=False)

    async def scenario():
        manager.start()
        try:
            await asyncio.wait_for(manager._queue.join(), 3)
        finally:
            await manager.stop()

    asyncio.run(scenario())
    assert launched == []
    assert store.get_semantic_workspace_task(owner["user_id"], "synthetic-task")["status"] == "paused"


def test_coremind_host_tool_checks_owner_before_real_call(workspace, tmp_path, monkeypatch):
    store, owner, _ = workspace
    adapter = coremind_runtime.CoreMindAgentKernelAdapter.__new__(coremind_runtime.CoreMindAgentKernelAdapter)
    called = []
    monkeypatch.setattr(adapter, "_read_source", lambda *_: called.append(True))
    tool = next(item for item in coremind_runtime._tool_definitions() if item["name"] == "mangrove_read_source")
    call = {**tool, "runId": "synthetic-run", "callId": "synthetic-call", "args": {}}
    store.update_user(owner["user_id"], disabled=True)
    store.update_user(owner["user_id"], disabled=False)
    with pytest.raises(ExecutionDenied):
        asyncio.run(adapter._answer_tool_call(
            object(), request=SimpleNamespace(user_id=owner["user_id"], task_id="synthetic-task"),
            binding=SimpleNamespace(external_run_id="synthetic-run"), run_root=tmp_path, call=call,
        ))
    assert called == []


def test_runtime_cleanup_failure_waits_for_atomic_execution(workspace, monkeypatch):
    store, owner, manager = workspace

    async def scenario():
        release, cleanup_seen = asyncio.Event(), asyncio.Event()

        async def execute():
            await release.wait()
            return "不得提交的旧结果"

        async def cleanup(*args, **kwargs):
            cleanup_seen.set()
            return False

        async def no_capabilities(*_):
            return False

        monkeypatch.setattr(manager, "_confirm_runtime_stopped", cleanup)
        monkeypatch.setattr(manager, "_selection_has_capabilities", no_capabilities)
        execution = asyncio.create_task(execute())
        watched = asyncio.create_task(manager._await_with_gate_supervision(owner["user_id"], "synthetic-task", 1, execution))
        store.update_user(owner["user_id"], disabled=True)
        try:
            await asyncio.wait_for(cleanup_seen.wait(), 2)
            assert not watched.done()
            assert not execution.done()
            assert store.account_execution_binding(owner["user_id"], "workspace", "synthetic-task")["state"] == "cleanup_failed"
        finally:
            release.set()
            with pytest.raises(ExecutionDenied):
                await asyncio.wait_for(watched, 2)
            await execution

    asyncio.run(scenario())


def test_disabled_owner_cannot_create_candidate_attempt(workspace):
    from src.candidate_verification import SqliteCandidateVerificationRepository
    from tests.test_candidate_verification_repository import _requested_attempt

    store, owner, _ = workspace
    repository = SqliteCandidateVerificationRepository(settings.webui_db_path)
    attempt = _requested_attempt().model_copy(update={"owner_id": owner["user_id"], "actor_id": owner["user_id"], "task_id": "synthetic-task"})
    store.update_user(owner["user_id"], disabled=True)
    with pytest.raises(ExecutionDenied):
        repository.create(attempt)
    assert repository.get(owner["user_id"], attempt.attempt_id) is None


def test_candidate_cannot_commit_late_report_after_reenable(workspace):
    from src.candidate_verification import AttemptStatus, SqliteCandidateVerificationRepository
    from tests.test_candidate_verification_repository import _requested_attempt

    store, owner, _ = workspace
    repository = SqliteCandidateVerificationRepository(settings.webui_db_path)
    attempt = _requested_attempt().model_copy(update={"owner_id": owner["user_id"], "actor_id": owner["user_id"], "task_id": "synthetic-task"})
    repository.create_and_start_if_p0_allowed(attempt, started_at=datetime.now(timezone.utc))
    store.update_user(owner["user_id"], disabled=True)
    store.update_user(owner["user_id"], disabled=False)
    report = '{"status":"passed"}'
    with pytest.raises(ExecutionDenied):
        repository.finish(owner["user_id"], attempt.attempt_id, status=AttemptStatus.PASSED,
                          report_json=report, report_hash=hashlib.sha256(report.encode("utf-8")).hexdigest(),
                          finished_at=datetime.now(timezone.utc))
    assert repository.get(owner["user_id"], attempt.attempt_id).status is AttemptStatus.RUNNING


@pytest.mark.parametrize("runtime_status,strategy", [("needs_input", "waiting"), ("cancelled", "new_revision")])
def test_pause_resume_strategy_preserves_runtime_checkpoint(workspace, monkeypatch, runtime_status, strategy):
    from src.agentic_runtime.models import RuntimeTaskConfig, RuntimeStatus, RuntimeVersion
    from src.agentic_runtime.repository import AgenticRuntimeRepository

    store, owner, manager = workspace
    repository = AgenticRuntimeRepository(settings.webui_db_path)
    repository.register(RuntimeTaskConfig(user_id=owner["user_id"], task_id="synthetic-task", revision=1, runtime_version=RuntimeVersion.PI))
    repository.update(owner["user_id"], "synthetic-task", 1, status=RuntimeStatus(runtime_status), run_id="synthetic-run", workspace_root="synthetic-checkpoint", session_file="synthetic-session")
    question = {"kind": "plan", "question_id": "synthetic-question", "prompt": "原问题"}
    store.update_semantic_workspace_task(owner["user_id"], "synthetic-task", status="needs_input", question=question)
    cancelled = []

    async def cancel(*identity):
        cancelled.append(identity)

    monkeypatch.setattr(manager, "_kernel_for_run", lambda *_: SimpleNamespace(cancel=cancel))
    store.update_user(owner["user_id"], disabled=True)
    assert asyncio.run(manager.pause_account_execution(owner["user_id"], "synthetic-task"))
    assert manager.account_resume_strategy(owner["user_id"], "synthetic-task") == strategy
    paused = repository.get(owner["user_id"], "synthetic-task", 1)
    assert paused["status"] is RuntimeStatus(runtime_status)
    assert paused["session_file"] == "synthetic-session"
    assert store.get_semantic_workspace_task(owner["user_id"], "synthetic-task")["question"] == question
    if runtime_status == "needs_input":
        assert cancelled == []


def test_other_workspace_holder_prevents_false_pause_confirmation(workspace):
    from src.api.execution import execution_lock

    store, owner, manager = workspace
    lease = execution_lock(store, owner["user_id"], "workspace", "synthetic-task")
    with lease:
        store.update_user(owner["user_id"], disabled=True)
        assert not asyncio.run(manager.pause_account_execution(owner["user_id"], "synthetic-task"))
        assert store.account_execution_binding(owner["user_id"], "workspace", "synthetic-task")["state"] == "active"
        assert store.get_semantic_workspace_task(owner["user_id"], "synthetic-task")["status"] == "pausing"
    assert asyncio.run(manager.pause_account_execution(owner["user_id"], "synthetic-task"))
    assert store.account_execution_binding(owner["user_id"], "workspace", "synthetic-task")["state"] == "paused"


@pytest.mark.parametrize("kind", ["workspace", "candidate"])
def test_old_hold_snapshot_cannot_stop_explicitly_resumed_generation(workspace, monkeypatch, kind):
    from src.account_execution import resume_execution
    from src.candidate_verification import AttemptStatus, SqliteCandidateVerificationRepository
    from tests.test_candidate_verification_repository import _requested_attempt

    store, owner, manager = workspace
    owner_id = owner["user_id"]
    resource_id = "synthetic-task"
    repository = SqliteCandidateVerificationRepository(settings.webui_db_path)
    if kind == "candidate":
        attempt = _requested_attempt().model_copy(update={"owner_id": owner_id, "actor_id": owner_id, "task_id": "synthetic-task"})
        repository.create(attempt)
        resource_id = attempt.attempt_id
    store.update_user(owner_id, disabled=True)
    assert store.confirm_account_execution_stopped(owner_id, kind, resource_id, 0)
    store.update_user(owner_id, disabled=False)
    with store._conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        resumed = capture_authorization(conn, owner_id)
        resume_execution(conn, resumed, kind, resource_id, expected_generation=0, now=1)
    with execution_context(resumed):
        store.set_account_execution_state(resumed, kind, resource_id, "active")
    # 第二次停用也只能由新代操作处理，第一代迟到快照不能借此命中新 Run。
    store.update_user(owner_id, disabled=True)
    cleanup = []

    async def stop(*args, **kwargs):
        cleanup.append(True)
        return True

    def close_unstarted(*, owner_id, attempt_id):
        cleanup.append(True)
        return repository.cancel_requested(owner_id, attempt_id, finished_at=datetime.now(timezone.utc))

    monkeypatch.setattr(manager, "_confirm_runtime_stopped", stop)
    monkeypatch.setattr(manager, "_candidate_verification_module", lambda: SimpleNamespace(close_unstarted_reverification=close_unstarted))
    pause = manager.pause_account_execution if kind == "workspace" else manager.pause_account_candidate
    assert not asyncio.run(pause(owner_id, resource_id, expected_generation=0))
    assert cleanup == []
    assert store.account_execution_binding(owner_id, kind, resource_id)["state"] == "active"
    if kind == "candidate":
        # 即使跨过接口复核，真实取消事务也必须按旧授权代数回滚。
        with pytest.raises(ExecutionDenied):
            repository.cancel_requested(owner_id, resource_id, finished_at=datetime.now(timezone.utc))
        assert repository.get(owner_id, resource_id).status is AttemptStatus.REQUESTED
    else:
        assert not store.update_account_workspace_state(owner_id, resource_id, 0, "pausing")
        assert store.get_semantic_workspace_task(owner_id, resource_id)["status"] == "queued"


def test_waiting_harness_keeps_original_resume_strategy(workspace):
    from src.semantic_harness.harness_models import HarnessRun, HarnessStatus

    store, owner, manager = workspace
    run = HarnessRun(run_id="synthetic-harness", user_id=owner["user_id"], thread_id="synthetic-harness", logical_plan_id="synthetic-plan", logical_plan_revision=1, logical_plan_hash="1" * 64, binding_revision=1, binding_hash="2" * 64, capability_id="synthetic-capability", capability_version="1", runtime_profile="synthetic", status=HarnessStatus.NEEDS_USER)
    store.create_semantic_harness_run(run)
    question = {"kind": "harness", "question_id": "original-question", "resume_token": "original-checkpoint", "prompt": "保留原问题"}
    store.update_semantic_workspace_task(owner["user_id"], "synthetic-task", status="needs_input", run_id=run.run_id, question=question)
    store.update_user(owner["user_id"], disabled=True)
    assert asyncio.run(manager.pause_account_execution(owner["user_id"], "synthetic-task", expected_generation=0))
    assert manager.account_resume_strategy(owner["user_id"], "synthetic-task") == "waiting"
    assert store.get_semantic_workspace_task(owner["user_id"], "synthetic-task")["question"] == question


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["worker", "grant", "none"])
async def test_unknown_worker_cleanup_never_becomes_idle(workspace, monkeypatch, tmp_path, failure_mode):
    import os
    import threading
    from src.agentic_runtime.coremind_runtime import CoreMindAgentKernelAdapter
    from src.agentic_runtime.kernel import AgentKernel
    from src.agentic_runtime.models import RuntimeTaskConfig, RuntimeVersion, PermissionProfile
    from src.agentic_runtime.repository import AgenticRuntimeRepository
    from tests.test_coremind_agent_kernel_adapter import _FakeCoreMindClient, _request

    store, owner, manager = workspace
    identity = (owner['user_id'], 'synthetic-task', 1)
    repository = AgenticRuntimeRepository(store.db_path)
    repository.register(RuntimeTaskConfig(user_id=identity[0], task_id=identity[1], revision=1,
        runtime_version=RuntimeVersion.PI, permission_profile=PermissionProfile.STANDARD))
    assert repository.get(*identity)['run_id'] is None
    release = threading.Event()
    worker = threading.Thread(target=release.wait)
    worker.start()

    class Process:
        pid = os.getpid()
        def poll(self):
            return None if worker.is_alive() else 0
        def terminate(self):
            raise OSError('虚构资源暂不能停止')

    class Client(_FakeCoreMindClient):
        _process = Process()
        def query(self, run_id):
            raise TimeoutError('虚构Provider结果未知')
        def close(self):
            if failure_mode == "worker":
                raise OSError('虚构关闭失败')
            release.set()
            worker.join(3)
            super().close()

    client = Client()
    def factory(**_):
        # 真Kernel必须先持久化run_id与绑定，再启动Client；不能因旧空列误判静默。
        assert repository.get(*identity)['run_id']
        assert kernel.frozen_binding(*identity) is not None
        return client

    grant_released = False
    class Broker:
        def revoke_run_grants(self, *args, **kwargs):
            if failure_mode == "grant" and not grant_released:
                raise OSError('虚构Grant撤销失败')

    adapter = CoreMindAgentKernelAdapter(execution_root=tmp_path / 'runtime', client_factory=factory, connection_broker=Broker())
    monkeypatch.setattr(adapter, '_pid_exists', lambda _: worker.is_alive())
    kernel = AgentKernel(adapter=adapter, repository=repository)
    request = _request(tmp_path).model_copy(update={'user_id': identity[0], 'task_id': identity[1]})

    async def failed_run(*_):
        await kernel.start(request, on_event=lambda _: asyncio.sleep(0))

    monkeypatch.setattr(manager, '_run_pi_task', failed_run)
    monkeypatch.setattr(manager, '_kernel_for_run', lambda *_: kernel)
    try:
        await manager._run_task_with_authorization(identity[0], identity[1])
        binding = store.account_execution_binding(identity[0], 'workspace', identity[1])
        task = store.get_semantic_workspace_task(identity[0], identity[1])
        if failure_mode == "none":
            assert binding['state'] == 'idle'
            assert task['status'] == 'needs_input'
            assert client.closed and not worker.is_alive()
            assert not client.cancel_calls
            return
        assert worker.is_alive() is (failure_mode == "worker")
        assert binding['state'] == 'active'
        assert task['status'] == 'cancelling'
        store.update_user(identity[0], disabled=True)
        store.update_user(identity[0], disabled=False)
        assert not await manager.pause_account_execution(identity[0], identity[1], expected_generation=0)
        assert store.account_execution_binding(identity[0], 'workspace', identity[1])['state'] == 'cleanup_failed'
        def hold_status():
            from src.account_execution import refresh_hold_operation
            with store._conn() as connection:
                connection.execute('BEGIN IMMEDIATE')
                operation_id = connection.execute('SELECT operation_id FROM account_execution_holds WHERE owner_user_id=?', (identity[0],)).fetchone()[0]
                return refresh_hold_operation(connection, operation_id, reconciliation_complete=True, now=1)['status']
        assert hold_status() == 'failed'
        grant_released = True
        release.set()
        worker.join(3)
        assert await manager.pause_account_execution(identity[0], identity[1], expected_generation=0)
        assert store.account_execution_binding(identity[0], 'workspace', identity[1])['state'] == 'paused'
        assert hold_status() == 'completed'
    finally:
        release.set()
        worker.join(3)
