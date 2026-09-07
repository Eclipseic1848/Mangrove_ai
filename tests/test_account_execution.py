"""账户执行权限只在临时库和显式业务事务中验证。"""
import asyncio
import sqlite3
from contextlib import closing, contextmanager

import pytest

from src import account_execution as execution
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture
def database(tmp_path):
    path = migrated_webui_database(tmp_path / "execution.db")
    with closing(sqlite3.connect(path)) as conn:
        conn.executemany("INSERT INTO users(user_id,username,password_hash,display_name,created_at,role,pending,disabled) VALUES (?,?, 'synthetic-hash', '', '2026-01-01','user',0,0)", [("owner-a", "synthetic-a"), ("owner-b", "synthetic-b")])
        conn.commit()
    return path


@contextmanager
def transaction(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def freeze(path, owner="owner-a"):
    with transaction(path) as conn:
        return execution.capture_authorization(conn, owner)


@pytest.mark.parametrize("flag", ["disabled", "pending"])
def test_late_first_binding_stays_denied_after_reenable(database, flag):
    old = freeze(database)
    with transaction(database) as conn:
        operation = execution.update_account_status(conn, "owner-a", **{flag: True}, actor_user_id="admin", now=1)
        duplicate = execution.update_account_status(conn, "owner-a", **{flag: True}, actor_user_id="admin", now=2)
        assert duplicate["operation_id"] == operation["operation_id"]
        execution.update_account_status(conn, "owner-a", **{flag: False}, actor_user_id="admin", now=3)
    with pytest.raises(execution.ExecutionDenied), transaction(database) as conn:
        conn.execute("INSERT INTO conversations(conv_id,user_id,title,created_at,updated_at) VALUES ('late','owner-a','虚构','now','now')")
        execution.bind_execution(conn, old, "chat", "late", now=4)
    with transaction(database) as conn:
        assert conn.execute("SELECT count(*) FROM conversations WHERE conv_id='late'").fetchone()[0] == 0
        assert conn.execute("SELECT execution_generation FROM users WHERE user_id='owner-a'").fetchone()[0] == 1
        for owner in ("owner-a", "owner-b"):
            current = execution.capture_authorization(conn, owner)
            execution.bind_execution(conn, current, "workspace", owner + "-task", now=4)
            execution.require_binding(conn, current, "workspace", owner + "-task")


@pytest.mark.parametrize("kind", ["workspace", "data", "harness", "source", "validation", "chat", "candidate", "schedule"])
def test_missing_and_paused_binding_require_explicit_restore(database, kind):
    old = freeze(database)
    with transaction(database) as conn:
        with pytest.raises(execution.ExecutionDenied):
            execution.require_binding(conn, old, kind, "run")
        execution.bind_execution(conn, old, kind, "run", now=1)
        execution.update_account_status(conn, "owner-a", disabled=True, actor_user_id="admin", now=2)
        assert execution.confirm_execution_stopped(conn, "owner-a", kind, "run", expected_generation=0, now=3)
        execution.update_account_status(conn, "owner-a", disabled=False, actor_user_id="admin", now=4)
        current = execution.capture_authorization(conn, "owner-a")
        with pytest.raises(execution.ExecutionDenied):
            execution.bind_execution(conn, current, kind, "run", now=5)
        execution.resume_execution(conn, current, kind, "run", expected_generation=0, now=5)
        assert not execution.confirm_execution_stopped(conn, "owner-a", kind, "run", expected_generation=0, cleanup_failed=True, now=6)
        assert execution.require_binding(conn, current, kind, "run")["state"] == "idle"


def test_operation_needs_real_stop_and_complete_reconciliation(database):
    auth = freeze(database)
    with transaction(database) as conn:
        execution.bind_execution(conn, auth, "source", "in-flight", now=1)
        execution.bind_execution(conn, auth, "workspace", "waiting", state="idle", now=1)
        op = execution.update_account_status(conn, "owner-a", disabled=True, actor_user_id="admin", now=2)
        assert execution.refresh_hold_operation(conn, op["operation_id"], reconciliation_complete=True, now=3)["status"] == "pending"
        execution.confirm_execution_stopped(conn, "owner-a", "source", "in-flight", expected_generation=0, cleanup_failed=True, now=4)
        assert execution.refresh_hold_operation(conn, op["operation_id"], now=4)["status"] == "failed"
        execution.confirm_execution_stopped(conn, "owner-a", "source", "in-flight", expected_generation=0, now=5)
        assert execution.refresh_hold_operation(conn, op["operation_id"], now=5)["status"] == "completed"
        empty = execution.update_account_status(conn, "owner-b", pending=True, actor_user_id="admin", now=6)
        assert execution.refresh_hold_operation(conn, empty["operation_id"], now=7)["status"] == "pending"
        assert execution.refresh_hold_operation(conn, empty["operation_id"], reconciliation_complete=True, now=8)["status"] == "completed"


def test_second_hold_still_counts_unsettled_older_generations(database):
    auth = freeze(database)
    with transaction(database) as conn:
        execution.bind_execution(conn, auth, "data", "old", now=1)
        first = execution.update_account_status(conn, "owner-a", disabled=True, actor_user_id="admin", now=2)
        execution.update_account_status(conn, "owner-a", disabled=False, actor_user_id="admin", now=3)
        second = execution.update_account_status(conn, "owner-a", pending=True, actor_user_id="admin", now=4)
        assert first["operation_id"] != second["operation_id"]
        assert execution.refresh_hold_operation(conn, second["operation_id"], reconciliation_complete=True, now=5)["status"] == "pending"
        assert len(execution.list_hold_operations(conn, "owner-a")) == 2


def test_mutations_need_transaction_and_missing_owner_never_authorizes(database):
    auth = freeze(database)
    with closing(sqlite3.connect(database)) as conn:
        with pytest.raises(ValueError, match="事务"):
            execution.bind_execution(conn, auth, "chat", "run", now=1)
    with transaction(database) as conn:
        with pytest.raises(ValueError):
            execution.bind_execution(conn, auth, "unknown", "run", now=1)
        with pytest.raises(execution.ExecutionDenied):
            execution.capture_authorization(conn, "missing")
        conn.execute("DELETE FROM users WHERE user_id='owner-a'")
        with pytest.raises(execution.ExecutionDenied):
            execution.require_authorized(conn, auth)


def test_context_is_immutable_isolated_and_restored():
    first = execution.ExecutionAuthorization("owner-a", 0)
    second = execution.ExecutionAuthorization("owner-b", 3)

    async def run(auth):
        with execution.execution_context(auth):
            await asyncio.sleep(0)
            assert execution.current_authorization() == auth
            with pytest.raises(AttributeError):
                auth.generation = 9
        with pytest.raises(execution.ExecutionDenied):
            execution.current_authorization()

    async def both():
        await asyncio.gather(run(first), run(second))

    asyncio.run(both())


def test_failed_operation_survives_refresh_until_explicit_successful_retry(database):
    with transaction(database) as conn:
        operation = execution.update_account_status(conn, 'owner-a', pending=True, actor_user_id='admin', now=1)
        opid = operation['operation_id']
        assert execution.refresh_hold_operation(conn, opid, error_code='scheduler_unavailable', now=2)['status'] == 'failed'
        assert execution.refresh_hold_operation(conn, opid, reconciliation_complete=True, now=3)['status'] == 'failed'
        assert execution.refresh_hold_operation(conn, opid, error_code=None, now=4)['status'] == 'completed'
