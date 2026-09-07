"""真实 SQLite 验证绑定与停用竞态；外部步骤全部替身。"""
from contextlib import closing
import asyncio
import sqlite3
import threading

import pytest

from src import account_execution as execution
from src.capability_governance.models import CapabilityGovernanceTarget, CapabilityValidationRun, PlatformValidationRun, ValidationTaskRef, ValidationRunStatus
from src.capability_governance.sqlite_repository import SqliteCapabilityGovernanceRepository
from src.capability_governance.models import ValidationStep, ValidationEvidence, PlatformValidationStep, PlatformValidationEvidence
from src.capability_governance.service import CapabilityGovernance
from src.capability_governance.platform_validation import PlatformValidationManager
from src.capability_governance.validation_runtime import CapabilityValidationManager
from src.capability_catalog import CatalogActor
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture
def validation(tmp_path):
    path = migrated_webui_database(tmp_path / 'validation.db')
    auth = seed_execution_owner(path)
    return path, SqliteCapabilityGovernanceRepository(str(path)), auth


def run_for(platform):
    target = CapabilityGovernanceTarget(owner_id=None if platform else 'owner-a', scope='platform' if platform else 'personal', pack_id='synthetic-pack', version='1', digest='sha256:'+'a'*64)
    common = dict(run_id='same-id', target=target, actor_id='owner-a', actor_role='admin', idempotency_key='synthetic')
    if platform:
        return PlatformValidationRun(**common)
    return CapabilityValidationRun(**common, owner_id='owner-a', task_ref=ValidationTaskRef(task_id='task', revision=1, source_snapshot_sha256='b'*64, input_sha256='c'*64, output_sha256='d'*64, capability_digest=target.digest, authorization_id='selection'))


def disable_reenable(path):
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('BEGIN IMMEDIATE')
        execution.update_account_status(conn, 'owner-a', disabled=True, actor_user_id='admin', now=1)
        execution.update_account_status(conn, 'owner-a', disabled=False, actor_user_id='admin', now=2)
        conn.commit()


@pytest.mark.parametrize('platform', [False, True])
def test_late_validation_registration_cannot_use_new_generation(validation, platform):
    path, repo, auth = validation
    create = repo.create_platform_validation_run if platform else repo.create_validation_run
    with execution.execution_context(auth):
        disable_reenable(path)
        with pytest.raises(execution.ExecutionDenied):
            create(run_for(platform))
    assert repo.list_platform_validation_runs() == ()
    assert repo.list_validation_runs() == ()


@pytest.mark.parametrize('platform', [False, True])
def test_late_validation_success_never_commits(validation, platform):
    path, repo, auth = validation
    create = repo.create_platform_validation_run if platform else repo.create_validation_run
    save = repo.save_platform_validation_run if platform else repo.save_validation_run
    get = repo.get_platform_validation_run if platform else repo.get_validation_run
    with execution.execution_context(auth):
        run = create(run_for(platform))
        disable_reenable(path)
        with pytest.raises(execution.ExecutionDenied):
            save(run.model_copy(update={'status': ValidationRunStatus.SUCCEEDED}))
    assert get(run.run_id).status is ValidationRunStatus.QUEUED


def test_personal_and_platform_same_run_id_have_distinct_bindings(validation):
    _path, repo, auth = validation
    with execution.execution_context(auth):
        repo.create_validation_run(run_for(False))
        repo.create_platform_validation_run(run_for(True))
    with closing(sqlite3.connect(_path)) as conn:
        assert {b['resource_id'] for b in execution.list_execution_bindings(conn, 'owner-a')} == {'same-id', 'platform:same-id'}


@pytest.mark.parametrize('disable', [False, True])
@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_personal_worker_stops_next_step_and_confirms_actual_cleanup(validation, disable, cleanup_fails):
    path, repo, auth = validation
    with execution.execution_context(auth):
        run = repo.create_validation_run(run_for(False))
    steps = []

    class Executor:
        def execute(self, current, step):
            steps.append(step)
            if disable and len(steps) == 1:
                disable_reenable(path)
            return ValidationEvidence(step=step, status='failed' if step is ValidationStep.CLEANUP and cleanup_fails else 'passed', evidence_ref='evidence://synthetic/check', evidence_sha256='a'*64, summary='虚构验证')

    governance = CapabilityGovernance(None, repo)
    if disable or cleanup_fails:
        with pytest.raises((execution.ExecutionDenied, RuntimeError)):
            governance.execute_validation(CatalogActor(owner_id='owner-a', role='admin'), run.run_id, worker_id='worker', executor=Executor())
    else:
        result = governance.execute_validation(CatalogActor(owner_id='owner-a', role='admin'), run.run_id, worker_id='worker', executor=Executor())
        assert result.status is ValidationRunStatus.SUCCEEDED
    if disable:
        assert steps == [ValidationStep.SYNTHETIC_SMOKE, ValidationStep.CLEANUP]
        current = repo.get_validation_run(run.run_id)
        assert not current.evidence
        with closing(sqlite3.connect(path)) as conn:
            binding = execution.list_execution_bindings(conn, 'owner-a')[0]
        assert binding['state'] == ('cleanup_failed' if cleanup_fails else 'paused')
        assert current.status is (ValidationRunStatus.RUNNING if cleanup_fails else ValidationRunStatus.CANCELLED)


@pytest.mark.parametrize('before_start', [False, True])
def test_platform_worker_does_not_continue_old_steps_after_hold(validation, tmp_path, before_start):
    path, repo, auth = validation
    with execution.execution_context(auth):
        run = repo.create_platform_validation_run(run_for(True))
    steps = []

    class Executor:
        def execute(self, current, step):
            steps.append(step)
            disable_reenable(path)
            return PlatformValidationEvidence(step=step, status='passed', evidence_ref='evidence://synthetic/check', evidence_sha256='a'*64, summary='虚构平台验证')

    if before_start:
        disable_reenable(path)
    manager = PlatformValidationManager(repo, executor=Executor(), signing=None, layout_path=tmp_path, private_key_path=tmp_path/'fake-private', public_key_path=tmp_path/'fake-public')
    manager.run_once()
    assert steps == ([] if before_start else [PlatformValidationStep.SYNTHETIC_SMOKE])
    current = repo.get_platform_validation_run(run.run_id)
    assert current.status is ValidationRunStatus.CANCELLED
    assert not current.evidence
    manager.run_once()
    assert steps == ([] if before_start else [PlatformValidationStep.SYNTHETIC_SMOKE])


@pytest.mark.asyncio
async def test_cancelled_cleanup_await_keeps_binding_until_real_thread_finishes(validation):
    path, repo, auth = validation
    with execution.execution_context(auth):
        run = repo.create_validation_run(run_for(False))
    disable_reenable(path)
    entered, release = threading.Event(), threading.Event()

    class Executor:
        def execute(self, current, step):
            assert step is ValidationStep.CLEANUP
            entered.set()
            assert release.wait(3)
            return ValidationEvidence(step=step, status='passed', evidence_ref='evidence://synthetic/cleanup', evidence_sha256='a'*64, summary='已清理虚构资源')

    manager = CapabilityValidationManager(CapabilityGovernance(None, repo), lambda _: Executor())
    task = asyncio.create_task(manager.reconcile_account_execution('owner-a', run.run_id, 0))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        assert repo.get_validation_binding(run)['state'] == 'active'
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert repo.get_validation_binding(run)['state'] == 'paused'
        assert repo.get_validation_run(run.run_id).status is ValidationRunStatus.CANCELLED
    finally:
        release.set()
        if not task.done():
            with pytest.raises(asyncio.CancelledError):
                await task


def test_platform_reconcile_keeps_unknown_legacy_external_execution_blocked(validation, tmp_path):
    path, repo, auth = validation
    with execution.execution_context(auth):
        run = repo.create_platform_validation_run(run_for(True))
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('UPDATE account_execution_bindings SET updated_at=0')
        conn.commit()
    disable_reenable(path)
    manager = PlatformValidationManager(repo, executor=None, signing=None, layout_path=tmp_path, private_key_path=tmp_path/'fake-private', public_key_path=tmp_path/'fake-public')
    assert not manager.reconcile_account_execution('owner-a', 'platform:' + run.run_id, 0)
    assert repo.get_validation_binding(run)['state'] == 'active'


def test_platform_internal_authorization_abort_does_not_invent_cleanup_proof(validation, tmp_path):
    path, repo, auth = validation
    with execution.execution_context(auth):
        run = repo.create_platform_validation_run(run_for(True))

    class Executor:
        def execute(self, current, step):
            disable_reenable(path)
            raise execution.ExecutionDenied('原子外部步骤缺少关闭证明')

    manager = PlatformValidationManager(repo, executor=Executor(), signing=None, layout_path=tmp_path, private_key_path=tmp_path/'fake-private', public_key_path=tmp_path/'fake-public')
    manager.run_once()
    assert repo.get_validation_binding(run)['state'] == 'cleanup_failed'
    manager.run_once()
    assert repo.get_validation_binding(run)['state'] == 'cleanup_failed'
    assert repo.get_platform_validation_run(run.run_id).status is ValidationRunStatus.RUNNING


@pytest.mark.parametrize('late_candidate', [False, True])
def test_platform_signing_unknown_keeps_live_resource_blocked_and_never_retries(validation, tmp_path, late_candidate):
    from tests.test_capability_platform_publish import _candidate_event
    path, repo, auth = validation
    with execution.execution_context(auth):
        run = repo.create_platform_validation_run(run_for(True))
        if not late_candidate:
            repo.save_platform_event(_candidate_event(run.target))
    live, release = threading.Event(), threading.Event()
    def external_resource():
        live.set()
        release.wait(5)
    resource = threading.Thread(target=external_resource)
    calls = []
    states_at_sign = []
    class Executor:
        def execute(self, current, step):
            return PlatformValidationEvidence(step=step, status='passed', evidence_ref='evidence://synthetic/check', evidence_sha256='a'*64, summary='虚构平台验证')
    class Signer:
        def execute(self, request):
            calls.append(request.transaction_id)
            states_at_sign.append(repo.get_validation_binding(run)['state'])
            resource.start()
            assert live.wait(2)
            raise RuntimeError('虚构签名返回未知，外部资源仍活跃')
    manager = PlatformValidationManager(repo, executor=Executor(), signing=Signer(), layout_path=tmp_path, private_key_path=tmp_path/'fake-private', public_key_path=tmp_path/'fake-public')
    if late_candidate:
        manager.run_once()
        assert repo.get_validation_binding(run)['state'] == 'idle'
        with execution.execution_context(auth):
            repo.save_platform_event(_candidate_event(run.target))
    try:
        manager.run_once()
        assert resource.is_alive()
        assert states_at_sign == ['active']
        assert repo.get_validation_binding(run)['state'] == 'cleanup_failed'
        assert repo.get_platform_validation_run(run.run_id).signing_signature_digest is None
        manager.run_once()
        assert len(calls) == 1
        disable_reenable(path)
        manager.run_once()
        assert len(calls) == 1
        assert repo.get_validation_binding(run)['state'] == 'cleanup_failed'
        with closing(sqlite3.connect(path)) as conn:
            conn.execute('BEGIN IMMEDIATE')
            hold = execution.list_hold_operations(conn, auth.owner_user_id)[0]
            summary = execution.refresh_hold_operation(conn, hold['operation_id'], reconciliation_complete=True, now=3)
            assert summary['status'] == 'failed'
            conn.commit()
    finally:
        release.set()
        if resource.ident is not None:
            resource.join(3)


def test_platform_restart_does_not_repeat_unconfirmed_signing(validation, tmp_path):
    from tests.test_capability_platform_publish import _candidate_event
    path, repo, auth = validation
    with execution.execution_context(auth):
        run = repo.create_platform_validation_run(run_for(True))
        run = repo.save_platform_validation_run(run.model_copy(update={'status': ValidationRunStatus.SUCCEEDED}))
        repo.save_platform_event(_candidate_event(run.target))
    calls = []
    class Signer:
        def execute(self, request):
            calls.append(request)
            raise RuntimeError('不应再次签名')
    # 模拟持久 active 之后进程退出；新管理器没有本轮步骤完成证明。
    manager = PlatformValidationManager(repo, executor=None, signing=Signer(), layout_path=tmp_path, private_key_path=tmp_path/'fake-private', public_key_path=tmp_path/'fake-public')
    manager.run_once()
    assert calls == []
    assert repo.get_validation_binding(run)['state'] == 'cleanup_failed'


@pytest.mark.parametrize('late_candidate', [False, True])
def test_platform_known_signing_success_remains_idempotent(validation, tmp_path, late_candidate):
    from types import SimpleNamespace
    from tests.test_capability_platform_publish import _candidate_event
    path, repo, auth = validation
    with execution.execution_context(auth):
        run = repo.create_platform_validation_run(run_for(True))
        if not late_candidate:
            repo.save_platform_event(_candidate_event(run.target))
    calls = []
    class Executor:
        def execute(self, current, step):
            return PlatformValidationEvidence(step=step, status='passed', evidence_ref='evidence://synthetic/check', evidence_sha256='a'*64, summary='虚构平台验证')
    class Signer:
        def execute(self, request):
            assert repo.get_validation_binding(run)['state'] == 'active'
            calls.append(request.transaction_id)
            return SimpleNamespace(subject_digest=run.target.digest, signature_digest='sha256:'+'b'*64, public_key_sha256='c'*64)
    manager = PlatformValidationManager(repo, executor=Executor(), signing=Signer(), layout_path=tmp_path, private_key_path=tmp_path/'fake-private', public_key_path=tmp_path/'fake-public')
    if late_candidate:
        manager.run_once()
        assert repo.get_validation_binding(run)['state'] == 'idle'
        with execution.execution_context(auth):
            repo.save_platform_event(_candidate_event(run.target))
    manager.run_once()
    manager.run_once()
    assert len(calls) == 1
    assert repo.get_validation_binding(run)['state'] == 'idle'
    assert repo.get_platform_validation_run(run.run_id).signing_signature_digest == 'sha256:'+'b'*64
