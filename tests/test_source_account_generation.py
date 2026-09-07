"""账号协调的取消副作用须与绑定代数在同事务核对。"""
from contextlib import closing
import sqlite3

import pytest

from src import account_execution as execution
from src.source_acquisition import SourceAcquisitionRepository, SourceAcquisitionRequest
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture
def source(tmp_path):
    path = migrated_webui_database(tmp_path / 'source-generation.db')
    auth = seed_execution_owner(path)
    repo = SourceAcquisitionRepository(path)
    with execution.execution_context(auth):
        attempt, _ = repo.claim_attempt(owner_id=auth.owner_user_id, idempotency_key='synthetic', request=SourceAcquisitionRequest('https://example.invalid/', '虚构来源'))
    return path, repo, auth, attempt['attempt_id']


def state(path, attempt_id):
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute('SELECT status,cancel_requested_at FROM source_acquisition_attempts WHERE attempt_id=?', (attempt_id,)).fetchone()


def test_old_cancel_snapshot_cannot_touch_explicitly_restored_source(source):
    path, repo, old, attempt_id = source
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('BEGIN IMMEDIATE')
        execution.update_account_status(conn, old.owner_user_id, disabled=True, actor_user_id='synthetic-admin', now=1)
        execution.confirm_execution_stopped(conn, old.owner_user_id, 'source', attempt_id, expected_generation=0, now=2)
        execution.update_account_status(conn, old.owner_user_id, disabled=False, actor_user_id='synthetic-admin', now=3)
        current = execution.capture_authorization(conn, old.owner_user_id)
        execution.resume_execution(conn, current, 'source', attempt_id, expected_generation=0, now=4)
        conn.commit()
    assert repo.cancel_for_account(old.owner_user_id, attempt_id, 0) is False
    assert state(path, attempt_id) == ('acquiring', None)
    with execution.execution_context(old):
        assert repo.confirm_account_stop(old.owner_user_id, attempt_id) is False
    assert state(path, attempt_id) == ('acquiring', None)


def test_current_old_binding_cancel_only_requests_until_real_stop(source):
    path, repo, old, attempt_id = source
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('BEGIN IMMEDIATE')
        execution.update_account_status(conn, old.owner_user_id, pending=True, actor_user_id='synthetic-admin', now=1)
        conn.commit()
    assert repo.cancel_for_account('other-owner', attempt_id, 0) is False
    assert repo.cancel_for_account(old.owner_user_id, 'missing', 0) is False
    assert repo.cancel_for_account(old.owner_user_id, attempt_id, 0) is True
    first = state(path, attempt_id)
    assert first[0] == 'acquiring' and first[1] is not None
    assert repo.cancel_for_account(old.owner_user_id, attempt_id, 0) is True
    assert state(path, attempt_id) == first
    with repo.execution_lock(old.owner_user_id, attempt_id), execution.execution_context(old):
        assert repo.confirm_account_stop(old.owner_user_id, attempt_id) is True
    assert state(path, attempt_id)[0] == 'canceled'
