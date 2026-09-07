"""正式发布的最后数据库提交必须再次核对账号执行代数。"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3
import threading
from pathlib import Path

import pytest

from src import account_execution as execution
from src.api.store import WebUIStore
from src.delivery_publishing.repository import DeliveryPublishingRepository
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database
from tests.test_vnext_delivery_publisher import _command, _publisher


@pytest.fixture
def publication(tmp_path):
    path = migrated_webui_database(tmp_path / 'publishing.db')
    auth = seed_execution_owner(path)
    store = WebUIStore(str(path))
    with execution.execution_context(auth):
        store.create_semantic_workspace_task(auth.owner_user_id, task_id='task-a', title='虚构', objective_text='虚构', upload_ids=[], output_formats=['json'], provider='local', model=None, external_api_confirmed=False)
    candidate = tmp_path / 'candidate.json'
    candidate.write_text('{"synthetic":true}', encoding='utf-8')
    repo = DeliveryPublishingRepository(path)
    return store, auth, repo, _publisher(tmp_path, repo, candidate), _command(candidate)


def hold(store, owner):
    store.update_user(owner, disabled=True)
    store.update_user(owner, disabled=False)


def test_account_hold_after_rename_before_insert_aborts_unpublished_intent(publication, monkeypatch):
    store, old, repo, publisher, command = publication
    reached, release = threading.Event(), threading.Event()
    commit = repo.commit_delivery

    def blocked_commit(*args):
        reached.set()
        assert release.wait(5)
        return commit(*args)

    monkeypatch.setattr(repo, 'commit_delivery', blocked_commit)

    def publish():
        with execution.execution_context(old):
            return publisher.publish(command, actor_id=old.owner_user_id)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(publish)
        try:
            assert reached.wait(5)
            intent = repo.get_intent(command.publication_key)
            assert intent['status'] == 'committing'
            from pathlib import Path
            final = Path(intent['final_dir'])
            files = {p.name: p.read_bytes() for p in final.iterdir()}
            hold(store, old.owner_user_id)
        finally:
            release.set()
        with pytest.raises(execution.ExecutionDenied):
            future.result(timeout=5)
    assert repo.latest_delivery(old.owner_user_id, command.run_id) is None
    assert repo.get_intent(command.publication_key)['status'] == 'aborted'
    assert {p.name: p.read_bytes() for p in final.iterdir()} == files


def test_committing_recovery_cannot_borrow_new_generation_after_workspace_resume(publication, monkeypatch):
    store, old, repo, publisher, command = publication
    commit = repo.commit_delivery
    monkeypatch.setattr(repo, 'commit_delivery', lambda *_: (_ for _ in ()).throw(RuntimeError('虚构rename后进程中断')))
    with execution.execution_context(old), pytest.raises(RuntimeError):
        publisher.publish(command, actor_id=old.owner_user_id)
    monkeypatch.setattr(repo, 'commit_delivery', commit)
    hold(store, old.owner_user_id)
    store.confirm_account_execution_stopped(old.owner_user_id, 'workspace', command.task_id, 0)
    new = store.capture_account_execution(old.owner_user_id)
    with store.account_execution_transaction(new) as conn:
        execution.resume_execution(conn, new, 'workspace', command.task_id, expected_generation=0, now=2)
    with execution.execution_context(new), pytest.raises(execution.ExecutionDenied):
        publisher.publish(command, actor_id=new.owner_user_id)
    assert repo.latest_delivery(new.owner_user_id, command.run_id) is None
    assert repo.get_intent(command.publication_key)['status'] == 'aborted'
    assert repo.get_intent(command.publication_key)['execution_generation'] == 0
    with execution.execution_context(new):
        revision = store.create_semantic_workspace_revision(new.owner_user_id, command.task_id, objective_text='显式新执行', output_formats=['json'], change_summary='账号恢复后新修订')
    assert revision['revision'] == 2


def test_completed_delivery_is_preserved_and_readable_after_account_hold(publication):
    store, old, repo, publisher, command = publication
    with execution.execution_context(old):
        manifest = publisher.publish(command, actor_id=old.owner_user_id)
        hold(store, old.owner_user_id)
        repeated = publisher.publish(command, actor_id=old.owner_user_id)
    assert repeated == manifest
    assert repo.get_delivery(old.owner_user_id, manifest.delivery_id) is not None
    assert repo.get_intent(command.publication_key)['status'] == 'published'


def test_late_first_intent_is_not_registered(publication):
    store, old, repo, publisher, command = publication
    hold(store, old.owner_user_id)
    with execution.execution_context(old), pytest.raises(execution.ExecutionDenied):
        publisher.publish(command, actor_id=old.owner_user_id)
    assert repo.get_intent(command.publication_key) is None


@pytest.mark.parametrize('recover_after_rename', [False, True])
def test_user_cancel_after_begin_commit_keeps_original_linearization(publication, monkeypatch, recover_after_rename):
    store, auth, repo, publisher, command = publication
    begin = repo.begin_commit
    commit = repo.commit_delivery

    def begin_then_cancel(*args, **kwargs):
        begin(*args, **kwargs)
        store.update_semantic_workspace_task(auth.owner_user_id, command.task_id, cancel_requested=True)

    monkeypatch.setattr(repo, 'begin_commit', begin_then_cancel)
    with execution.execution_context(auth):
        if recover_after_rename:
            monkeypatch.setattr(repo, 'commit_delivery', lambda *_: (_ for _ in ()).throw(RuntimeError('虚构提交中断')))
            with pytest.raises(RuntimeError):
                publisher.publish(command, actor_id=auth.owner_user_id)
            assert repo.get_intent(command.publication_key)['status'] == 'committing'
            monkeypatch.setattr(repo, 'commit_delivery', commit)
        manifest = publisher.publish(command, actor_id=auth.owner_user_id)
    assert manifest.status.value == 'succeeded'
    assert repo.get_intent(command.publication_key)['status'] == 'published'


def test_missing_caller_context_does_not_abort_current_valid_committing_intent(publication, monkeypatch):
    _store, auth, repo, publisher, command = publication
    commit = repo.commit_delivery
    monkeypatch.setattr(repo, 'commit_delivery', lambda *_: (_ for _ in ()).throw(RuntimeError('虚构提交中断')))
    with execution.execution_context(auth), pytest.raises(RuntimeError):
        publisher.publish(command, actor_id=auth.owner_user_id)
    monkeypatch.setattr(repo, 'commit_delivery', commit)
    with pytest.raises(execution.ExecutionDenied):
        publisher.publish(command, actor_id=auth.owner_user_id)
    assert repo.get_intent(command.publication_key)['status'] == 'committing'
    with execution.execution_context(auth):
        assert publisher.publish(command, actor_id=auth.owner_user_id).status.value == 'succeeded'


@pytest.mark.asyncio
@pytest.mark.parametrize("busy", [False, True])
async def test_crashed_old_intent_is_reconciled_before_explicit_new_revision(publication, monkeypatch, busy):
    from filelock import FileLock
    from src.api import auth as auth_module
    from src.api.semantic_workspace_runtime import SemanticWorkspaceManager
    from src.config.settings import settings

    store, old, repo, publisher, command = publication
    monkeypatch.setattr(auth_module, '_store', store)
    monkeypatch.setattr(settings, 'webui_db_path', store.db_path)
    monkeypatch.setattr(settings, 'semantic_execution_root', str(publisher._output_root))
    monkeypatch.setattr(repo, 'commit_delivery', lambda *_: (_ for _ in ()).throw(RuntimeError('虚构断电遗留提交意图')))
    with execution.execution_context(old), pytest.raises(RuntimeError):
        publisher.publish(command, actor_id=old.owner_user_id)
    intent = repo.get_intent(command.publication_key)
    assert intent['status'] == 'committing'
    final = Path(intent['final_dir'])
    preserved = {p.name: p.read_bytes() for p in final.iterdir()}
    hold(store, old.owner_user_id)
    manager = SemanticWorkspaceManager()
    lock = FileLock(publisher._output_root / '.publication-locks' / f'{command.publication_key}.lock', timeout=0)
    if busy:
        with lock:
            assert not await manager.pause_account_execution(old.owner_user_id, command.task_id, expected_generation=0)
            assert repo.get_intent(command.publication_key)['status'] == 'committing'
    assert await manager.pause_account_execution(old.owner_user_id, command.task_id, expected_generation=0)
    assert repo.get_intent(command.publication_key)['status'] == 'aborted'
    assert repo.latest_delivery(old.owner_user_id, command.run_id) is None
    assert {p.name: p.read_bytes() for p in final.iterdir()} == preserved
    assert repo.get_intent(command.publication_key)['manifest_json'] == intent['manifest_json']
    new = store.capture_account_execution(old.owner_user_id)
    with execution.execution_context(new):
        revision = store.create_semantic_workspace_revision(new.owner_user_id, command.task_id,
            objective_text='明确重新执行', output_formats=['json'], change_summary='账号恢复后新修订',
            expected_revision=2, account_resume_generation=0)
    assert revision['revision'] == 2


@pytest.mark.parametrize("published", [False, True])
def test_account_reconciliation_preserves_current_epoch_and_formal_delivery(publication, monkeypatch, published):
    from src.delivery_publishing.service import reconcile_account_publications
    store, auth, repo, publisher, command = publication
    if not published:
        monkeypatch.setattr(repo, 'commit_delivery', lambda *_: (_ for _ in ()).throw(RuntimeError('虚构提交中断')))
    with execution.execution_context(auth):
        if published:
            publisher.publish(command, actor_id=auth.owner_user_id)
        else:
            with pytest.raises(RuntimeError):
                publisher.publish(command, actor_id=auth.owner_user_id)
    before = repo.get_intent(command.publication_key)
    assert reconcile_account_publications(repo, publisher._output_root, 'different-owner', command.task_id, 0)
    assert reconcile_account_publications(repo, publisher._output_root, auth.owner_user_id, command.task_id, 0) is published
    assert repo.get_intent(command.publication_key) == before
