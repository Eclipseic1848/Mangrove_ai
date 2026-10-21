"""部署排空只阻止新领取，已在途的合成工作自然收尾。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from datetime import datetime
import threading
from types import SimpleNamespace

import pytest

from src.config.settings import settings
from src.scheduler.service import SchedulerService
from tests.test_account_execution_scheduler import add, fixture


@pytest.fixture
def drain_gate(tmp_path, monkeypatch):
    from importlib import import_module
    from src.services.deployment_drain import DeploymentGate

    gate = DeploymentGate(tmp_path / ".deployment-control")
    for name in (
        "src.scheduler.service", "src.api.semantic_workspace_runtime",
        "src.api.cookie_health_scanner", "src.api.library_dedup_scanner",
        "src.capability_governance.validation_runtime", "src.capability_governance.platform_validation",
    ):
        monkeypatch.setattr(import_module(name), "get_deployment_gate", lambda: gate)
    return gate


def request_drain(gate, phase="draining"):
    gate.request_path.parent.mkdir(parents=True, exist_ok=True)
    gate.request_path.write_text(json.dumps({"version": 1, "request_id": "a" * 32, "phase": phase}), encoding="utf-8")


@pytest.mark.asyncio
async def test_scheduler_drain_marker_leaves_due_schedule_unclaimed(fixture, tmp_path, monkeypatch, drain_gate):
    web, store, owner, auth = fixture
    monkeypatch.setattr(settings, "webui_db_path", web.db_path)
    marker = tmp_path / ".deployment-control" / "drain.json"
    marker.parent.mkdir(exist_ok=True)
    marker.write_text(json.dumps({"version": 1, "request_id": "a" * 32, "phase": "draining"}), encoding="utf-8")
    task_id = add(store, auth)
    calls = []

    async def runner(*args, **kwargs):
        calls.append(task_id)
        return {"reply": "合成任务完成"}

    service = SchedulerService(store, runner=runner)
    assert await service.tick(datetime(2026, 9, 6, 1)) == 0
    assert calls == []
    assert store.list_runs(task_id) == []
    assert store.get(task_id)["status"] == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("background", [False, True])
async def test_scheduler_manual_drain_is_rejected_before_claim(fixture, drain_gate, background):
    from src.account_execution import execution_context

    _web, store, _owner, auth = fixture
    task_id = add(store, auth)
    request_drain(drain_gate)
    with execution_context(auth):
        assert await SchedulerService(store).run_task_now(task_id, background=background) == "draining"
    assert store.list_runs(task_id) == []
    assert drain_gate.snapshot()["total_active"] == 0


@pytest.mark.asyncio
async def test_scheduler_background_lease_outlives_http_and_finishes_naturally(fixture, drain_gate):
    from src.account_execution import execution_context

    _web, store, _owner, auth = fixture
    task_id = add(store, auth)
    entered, release = asyncio.Event(), asyncio.Event()

    async def runner(*args, **kwargs):
        entered.set()
        await release.wait()
        return {"reply": "在途任务自然完成"}

    service = SchedulerService(store, runner=runner)
    with execution_context(auth):
        outcome = await service.run_task_now(task_id, background=True)
    workers = tuple(service._workers)
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert outcome["state"] == "running"
        assert drain_gate.snapshot()["active"] == {"scheduler": 1}
        request_drain(drain_gate)
        assert not workers[0].done()
        release.set()
        await asyncio.wait_for(asyncio.gather(*workers), 3)
        await asyncio.sleep(0)
        assert not workers[0].cancelled()
        assert store.list_runs(task_id)[0]["state"] == "succeeded"
        assert drain_gate.snapshot()["total_active"] == 0
    finally:
        release.set()
        await asyncio.gather(*workers, return_exceptions=True)


@pytest.mark.asyncio
async def test_workspace_rechecks_drain_after_waiting_for_heavy_slot(monkeypatch, drain_gate, tmp_path):
    from src.api import semantic_workspace_runtime as runtime
    from tests.database_migration_helpers import migrated_webui_database

    monkeypatch.setattr(settings, "webui_db_path", str(migrated_webui_database(tmp_path / "workspace.db")))

    manager = runtime.SemanticWorkspaceManager.__new__(runtime.SemanticWorkspaceManager)
    manager._queue, manager._queued, manager._active = asyncio.Queue(), set(), {}
    manager._heavy = asyncio.Semaphore(0)
    waiting = asyncio.Event()
    calls = []
    task = {"task_id": "queued-task", "active_revision": 1, "deleted_at": None, "status": "queued", "cancel_requested": False}
    monkeypatch.setattr(runtime, "get_store", lambda: SimpleNamespace(get_semantic_workspace_task=lambda *args: task))

    def heavy(_task):
        waiting.set()
        return True

    async def run(*args):
        calls.append(args)

    monkeypatch.setattr(runtime, "_is_heavy", heavy)
    monkeypatch.setattr(manager, "_launch_admitted_job", run)
    manager.enqueue("owner", "queued-task")
    worker = asyncio.create_task(manager._worker(0))
    try:
        await asyncio.wait_for(waiting.wait(), 3)
        request_drain(drain_gate)
        manager._heavy.release()
        await asyncio.wait_for(manager._queue.join(), 3)
        assert calls == [] and task["status"] == "queued"
        assert drain_gate.snapshot()["total_active"] == 0
        drain_gate.request_path.unlink()
        manager.enqueue("owner", "queued-task")
        await asyncio.wait_for(manager._queue.join(), 3)
        assert calls == [("owner", "queued-task")]
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_workspace_existing_job_keeps_lease_until_natural_completion(monkeypatch, drain_gate):
    from src.api import semantic_workspace_runtime as runtime

    manager = runtime.SemanticWorkspaceManager.__new__(runtime.SemanticWorkspaceManager)
    entered, release = asyncio.Event(), asyncio.Event()

    async def run(*args):
        entered.set()
        await release.wait()

    monkeypatch.setattr(manager, "_launch_admitted_job", run)
    work = asyncio.create_task(manager._launch_job("owner", "task"))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        request_drain(drain_gate)
        assert drain_gate.snapshot()["active"] == {"workspace": 1}
        assert not work.done()
        release.set()
        await asyncio.wait_for(work, 3)
        assert not work.cancelled() and drain_gate.snapshot()["total_active"] == 0
    finally:
        release.set()
        await asyncio.gather(work, return_exceptions=True)


@pytest.mark.asyncio
async def test_requested_reverification_waits_and_existing_one_finishes(monkeypatch, drain_gate):
    from src.api import semantic_workspace_runtime as runtime

    manager = runtime.SemanticWorkspaceManager.__new__(runtime.SemanticWorkspaceManager)
    manager._reverification_tasks, manager._reverification_task_context = set(), {}
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def execute(**kwargs):
        calls.append(kwargs["attempt_id"])
        entered.set()
        await release.wait()

    monkeypatch.setattr(manager, "_execute_candidate_reverification", execute)
    manager._schedule_candidate_reverification(owner_id="owner", attempt_id="existing", verifier_factory=None)
    workers = tuple(manager._reverification_tasks)
    try:
        await asyncio.wait_for(entered.wait(), 3)
        request_drain(drain_gate)
        manager._schedule_candidate_reverification(owner_id="owner", attempt_id="requested", verifier_factory=None)
        assert calls == ["existing"]
        assert drain_gate.snapshot()["active"] == {"candidate-reverification": 1}
        release.set()
        await asyncio.wait_for(asyncio.gather(*workers), 3)
        await asyncio.sleep(0)
        assert manager._reverification_tasks == set()
        assert drain_gate.snapshot()["total_active"] == 0
    finally:
        release.set()
        await asyncio.gather(*workers, return_exceptions=True)


@pytest.mark.asyncio
async def test_learning_finishes_current_receipt_and_leaves_next_pending(monkeypatch, drain_gate):
    from src.account_execution import ExecutionAuthorization
    from src.api import semantic_workspace_runtime as runtime
    from src.memory import workspace_learning

    manager = runtime.SemanticWorkspaceManager.__new__(runtime.SemanticWorkspaceManager)
    rows = [{"owner_id": "owner", "task_id": task, "revision": 1, "run_id": "run", "delivery_id": "delivery"}
            for task in ("first", "second")]
    monkeypatch.setattr(workspace_learning, "pending_template_uses", lambda _path: rows)
    monkeypatch.setattr(workspace_learning, "pending_lesson_failures", lambda _path: [])
    monkeypatch.setattr(runtime, "get_store", lambda: SimpleNamespace(
        get_semantic_workspace_task=lambda *args: {"deleted_at": None},
        get_semantic_workspace_revision=lambda *args: {"status": "completed"},
    ))
    monkeypatch.setattr(manager, "_workspace_authorization", lambda *args: ExecutionAuthorization("owner", 0))
    monkeypatch.setattr(manager, "_require_workspace_execution", lambda *args: None)
    calls, sleeps = [], []

    async def record(owner, task, *args, **kwargs):
        calls.append(task)
        request_drain(drain_gate)
        assert drain_gate.snapshot()["active"] == {"workspace-learning": 1}

    async def sleep(_seconds):
        if sleeps:
            raise asyncio.CancelledError
        sleeps.append(1)

    monkeypatch.setattr(manager, "_record_delivery_learning", record)
    monkeypatch.setattr(runtime, "asyncio", SimpleNamespace(sleep=sleep, to_thread=asyncio.to_thread))
    with pytest.raises(asyncio.CancelledError):
        await manager._learning_loop()
    assert calls == ["first"] and rows[1]["task_id"] == "second"
    assert drain_gate.snapshot()["total_active"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,expected", [("draining", ["templates", "lessons"]), ("sealed", [])], ids=["draining", "sealed"])
async def test_learning_metadata_writes_are_counted_and_obey_seal(monkeypatch, drain_gate, phase, expected):
    from src.api import semantic_workspace_runtime as runtime
    from src.memory import workspace_learning

    request_drain(drain_gate, phase)
    manager = runtime.SemanticWorkspaceManager.__new__(runtime.SemanticWorkspaceManager)
    calls, observed, sleeps = [], [], []

    def pending(kind):
        calls.append(kind)
        observed.append(drain_gate.snapshot()["active"])
        return []

    async def sleep(_seconds):
        if sleeps:
            raise asyncio.CancelledError
        sleeps.append(1)

    monkeypatch.setattr(workspace_learning, "pending_template_uses", lambda _path: pending("templates"))
    monkeypatch.setattr(workspace_learning, "pending_lesson_failures", lambda _path: pending("lessons"))
    monkeypatch.setattr(runtime, "asyncio", SimpleNamespace(sleep=sleep, to_thread=asyncio.to_thread))
    with pytest.raises(asyncio.CancelledError):
        await manager._learning_loop()
    assert calls == expected
    assert all(active == {"workspace-maintenance": 1} for active in observed)
    assert drain_gate.snapshot()["total_active"] == 0


@pytest.mark.asyncio
async def test_cancelled_learning_metadata_keeps_lease_until_thread_finishes(monkeypatch, drain_gate):
    from src.api import semantic_workspace_runtime as runtime
    from src.memory import workspace_learning

    manager = runtime.SemanticWorkspaceManager.__new__(runtime.SemanticWorkspaceManager)
    entered, release = threading.Event(), threading.Event()

    def pending(_path):
        entered.set()
        assert release.wait(3)
        return []

    monkeypatch.setattr(workspace_learning, "pending_template_uses", pending)
    monkeypatch.setattr(runtime, "REVERIFICATION_RECOVERY_POLL_SECONDS", 0)
    work = asyncio.create_task(manager._learning_loop())
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        work.cancel()
        await asyncio.sleep(0)
        assert not work.done()
        assert drain_gate.snapshot()["active"] == {"workspace-maintenance": 1}
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(work, 3)
        assert drain_gate.snapshot()["total_active"] == 0
    finally:
        release.set()
        await asyncio.gather(work, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,expected", [("draining", ["task"]), ("sealed", [])])
async def test_workspace_cleanup_is_counted_and_seal_blocks_new_cleanup(monkeypatch, drain_gate, phase, expected, tmp_path):
    from src.account_execution import ExecutionAuthorization
    from src.api import semantic_workspace_runtime as runtime
    from tests.database_migration_helpers import migrated_webui_database

    monkeypatch.setattr(settings, "webui_db_path", str(migrated_webui_database(tmp_path / "workspace.db")))

    request_drain(drain_gate, phase)
    manager = runtime.SemanticWorkspaceManager.__new__(runtime.SemanticWorkspaceManager)
    monkeypatch.setattr(manager, "_candidate_verification_module", lambda: None)
    monkeypatch.setattr(manager, "_workspace_authorization", lambda *args: ExecutionAuthorization("owner", 0))
    monkeypatch.setattr(runtime, "get_store", lambda: SimpleNamespace(
        list_pending_semantic_workspace_tasks=lambda: [{"status": "cancelling", "user_id": "owner", "task_id": "task", "active_revision": 1}],
    ))
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: 0))
    calls, sleeps = [], []

    async def cancel(owner, task, **kwargs):
        calls.append(task)
        assert kwargs == {"for_revision": True}
        assert drain_gate.snapshot()["active"] == {"workspace-cleanup": 1}
        await asyncio.sleep(0)
        assert drain_gate.snapshot()["active"] == {"workspace-cleanup": 1}

    async def sleep(_seconds):
        if sleeps:
            raise asyncio.CancelledError
        sleeps.append(1)

    monkeypatch.setattr(manager, "cancel", cancel)
    monkeypatch.setattr(runtime, "asyncio", SimpleNamespace(sleep=sleep))
    with pytest.raises(asyncio.CancelledError):
        await manager._maintenance_loop()
    assert calls == expected and drain_gate.snapshot()["total_active"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["draining", "sealed"])
async def test_workspace_restart_keeps_sealed_metadata_unchanged(monkeypatch, drain_gate, phase, tmp_path):
    from src.account_execution import ExecutionAuthorization
    from src.api import semantic_workspace_runtime as runtime
    from tests.database_migration_helpers import migrated_webui_database

    monkeypatch.setattr(settings, "webui_db_path", str(migrated_webui_database(tmp_path / "workspace.db")))

    request_drain(drain_gate, phase)
    manager = runtime.SemanticWorkspaceManager.__new__(runtime.SemanticWorkspaceManager)
    manager._workers = []
    calls = []

    def record(value):
        assert drain_gate.snapshot()["active"] == {"workspace-maintenance": 1}
        calls.append(value)

    store = SimpleNamespace(
        purge_expired_semantic_workspace_tasks=lambda: record("purge"),
        list_pending_semantic_workspace_tasks=lambda: [{"user_id": "owner", "task_id": "task", "status": "queued", "active_revision": 1}],
        require_account_execution=lambda *args: None,
        update_semantic_workspace_task=lambda *args, **kwargs: record(kwargs["status"]),
    )
    monkeypatch.setattr(runtime, "get_store", lambda: store)
    monkeypatch.setattr(manager, "_candidate_verification_module", lambda: object())
    monkeypatch.setattr(manager, "_recover_interrupted_candidate_reverifications", lambda _module: record("recover"))
    monkeypatch.setattr(manager, "_resume_requested_candidate_reverifications", lambda _module: record("resume"))
    monkeypatch.setattr(manager, "_workspace_authorization", lambda *args: ExecutionAuthorization("owner", 0))
    waiting = asyncio.Event()

    async def park(*args):
        await waiting.wait()

    for name in ("_worker", "_maintenance_loop", "_learning_loop"):
        monkeypatch.setattr(manager, name, park)
    manager.start()
    try:
        assert calls == (["purge", "recover", "resume", "queued"] if phase == "draining" else [])
        assert len(manager._workers) == 2
        assert drain_gate.snapshot()["total_active"] == 0
    finally:
        tasks = [*manager._workers, manager._maintenance, manager._learning_worker]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["draining", "sealed"])
async def test_workspace_periodic_metadata_obeys_seal(monkeypatch, drain_gate, phase):
    from src.api import semantic_workspace_runtime as runtime

    request_drain(drain_gate, phase)
    manager = runtime.SemanticWorkspaceManager.__new__(runtime.SemanticWorkspaceManager)
    clock, calls, sleeps = [0], [], []

    def record(value):
        assert drain_gate.snapshot()["active"] == {"workspace-maintenance": 1}
        calls.append(value)

    monkeypatch.setattr(manager, "_candidate_verification_module", lambda: object())
    monkeypatch.setattr(manager, "_recover_interrupted_candidate_reverifications", lambda _module: record("recover"))
    monkeypatch.setattr(manager, "_resume_requested_candidate_reverifications", lambda _module: record("resume"))
    monkeypatch.setattr(runtime, "get_store", lambda: SimpleNamespace(
        list_pending_semantic_workspace_tasks=lambda: [],
        purge_expired_semantic_workspace_tasks=lambda: record("purge"),
    ))
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    async def sleep(_seconds):
        if sleeps:
            raise asyncio.CancelledError
        sleeps.append(1)
        clock[0] = runtime.WORKSPACE_PURGE_INTERVAL_SECONDS + 1

    monkeypatch.setattr(runtime, "asyncio", SimpleNamespace(sleep=sleep))
    with pytest.raises(asyncio.CancelledError):
        await manager._maintenance_loop()
    assert calls == (["recover", "resume", "purge"] if phase == "draining" else [])
    assert drain_gate.snapshot()["total_active"] == 0


@pytest.mark.asyncio
async def test_cookie_drain_preserves_next_item_without_resending(monkeypatch, drain_gate):
    from src.api import cookie_health_scanner as runtime
    from src.api.routes import config_routes

    scanner = runtime.CookieHealthScanner()
    monkeypatch.setattr(settings, "cookie_health_scan_enabled", True)
    monkeypatch.setattr(settings, "cookie_health_maintenance_file", "")
    monkeypatch.setattr(config_routes, "_COOKIE_HEALTH_KEYS", ["first", "second"])
    calls = []

    async def verify(key, source):
        assert drain_gate.snapshot()["active"] == {"cookie-health": 1}
        calls.append(key)
        if key == "first":
            request_drain(drain_gate)

    async def sleep(seconds):
        assert drain_gate.snapshot()["total_active"] == 0
        if seconds == runtime._IDLE_CHECK_SECONDS:
            assert calls == ["first"]
            drain_gate.request_path.unlink()
        await asyncio.sleep(0)

    monkeypatch.setattr(config_routes, "_verify_global_cookie", verify)
    monkeypatch.setattr(scanner, "_sleep", sleep)
    await asyncio.wait_for(scanner._run_one_scan(), 3)
    assert calls == ["first", "second"]
    assert drain_gate.snapshot()["total_active"] == 0


@pytest.mark.asyncio
async def test_library_drain_blocks_new_scan_and_keeps_active_round(monkeypatch, drain_gate):
    from src.api import library_dedup_scanner as runtime

    scanner = runtime.LibraryDedupScanner()
    monkeypatch.setattr(settings, "library_dedup_scan_enabled", True)
    request_drain(drain_gate)
    calls = []

    async def scan():
        calls.append("scan")
        request_drain(drain_gate)
        assert drain_gate.snapshot()["active"] == {"library-dedup": 1}
        await asyncio.sleep(0)
        assert drain_gate.snapshot()["active"] == {"library-dedup": 1}

    async def sleep(_seconds):
        assert drain_gate.snapshot()["total_active"] == 0
        if not calls:
            assert scanner._last_scan_at == 0
            drain_gate.request_path.unlink()
        else:
            scanner._stop.set()
        await asyncio.sleep(0)

    monkeypatch.setattr(scanner, "_run_one_scan", scan)
    monkeypatch.setattr(scanner, "_sleep", sleep)
    await asyncio.wait_for(scanner._loop(), 3)
    assert calls == ["scan"] and scanner._last_scan_at > 0
    assert drain_gate.snapshot()["total_active"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,cancel_requested,expected", [
    ("draining", True, ["cleanup"]), ("sealed", True, []), ("draining", False, []),
])
async def test_capability_drain_keeps_cleanup_but_does_not_claim_queued(drain_gate, phase, cancel_requested, expected):
    from src.capability_governance.models import ValidationRunStatus
    from src.capability_governance.validation_runtime import CapabilityValidationManager

    request_drain(drain_gate, phase)
    runs = [SimpleNamespace(run_id=name, owner_id="owner", actor_role="user", status=status, cancel_requested=cancel_requested)
            for name, status in [("queued", ValidationRunStatus.QUEUED), ("cleanup", ValidationRunStatus.CANCELLING)]]
    calls = []

    def execute(_actor, run_id, **kwargs):
        calls.append(run_id)
        assert drain_gate.snapshot()["active"] == {"capability-validation": 1}
        return SimpleNamespace(status=ValidationRunStatus.CANCELLED)

    governance = SimpleNamespace(list_all_validations_for_worker=lambda: runs, execute_validation=execute)
    manager = CapabilityValidationManager(governance, lambda _run: None)
    await manager.run_once()
    assert calls == expected
    assert drain_gate.snapshot()["total_active"] == 0


@pytest.mark.asyncio
async def test_capability_drain_rechecks_between_runs(drain_gate):
    from src.capability_governance.models import ValidationRunStatus
    from src.capability_governance.validation_runtime import CapabilityValidationManager

    runs = [SimpleNamespace(run_id=name, owner_id="owner", actor_role="user", status=ValidationRunStatus.QUEUED)
            for name in ("first", "second")]
    calls = []

    def execute(_actor, run_id, **kwargs):
        calls.append(run_id)
        assert drain_gate.snapshot()["active"] == {"capability-validation": 1}
        request_drain(drain_gate)
        return SimpleNamespace(status=ValidationRunStatus.FAILED)

    manager = CapabilityValidationManager(
        SimpleNamespace(list_all_validations_for_worker=lambda: runs, execute_validation=execute), lambda _run: None,
    )
    await manager.run_once()
    assert calls == ["first"] and drain_gate.snapshot()["total_active"] == 0


def test_platform_thread_keeps_current_run_and_blocks_next(monkeypatch, drain_gate):
    from src.capability_governance.platform_validation import PlatformValidationManager

    manager = PlatformValidationManager.__new__(PlatformValidationManager)
    runs = [SimpleNamespace(run_id=name) for name in ("first", "second")]
    manager._repository = SimpleNamespace(list_platform_validation_runs=lambda: runs)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def run(item):
        calls.append(item.run_id)
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr(manager, "_run_one", run)
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(manager.run_once)
        try:
            assert entered.wait(3)
            request_drain(drain_gate)
            assert drain_gate.snapshot()["active"] == {"platform-validation": 1}
            assert not worker.done()
            release.set()
            worker.result(timeout=3)
        finally:
            release.set()
    assert calls == ["first"] and drain_gate.snapshot()["total_active"] == 0
