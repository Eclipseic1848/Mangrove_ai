"""隔离门在真实消费原语前拒绝旧身份，原未知和删除意图保持。"""
from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
import json
import shutil

import pytest
import sqlite3

from src import account_execution as execution
from src.config.settings import settings
from src.deployment_quarantine import ACCEPTED_RISK, SETTING_KEY, QuarantinedExecutionError
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture(scope="module")
def enforcement_template(tmp_path_factory):
    return migrated_webui_database(tmp_path_factory.mktemp("quarantine-enforcement") / "webui.db")


@pytest.fixture
def database(tmp_path, enforcement_template, monkeypatch):
    path = tmp_path / "webui.db"
    shutil.copyfile(enforcement_template, path)
    monkeypatch.setattr(settings, "webui_db_path", str(path))
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO users(user_id,username,password_hash,created_at) VALUES ('owner-a','synthetic-owner','synthetic','2026-01-01')")
        conn.commit()
    return path


def _pin(conn, *identities, records=()):
    manifest = {"version": 1, "approval_reference": "issue-244-synthetic-approved-isolation", "actor_id": "synthetic-super-admin",
                "accepted_risk": ACCEPTED_RISK, "targets": [dict(owner_id=owner, kind=kind, resource_id=resource, generation=None) for owner, kind, resource in identities], "records": list(records)}
    assert conn.execute("UPDATE app_settings SET value=? WHERE key=?", (json.dumps(manifest), SETTING_KEY)).rowcount == 1
    conn.commit()


@pytest.mark.parametrize("operation,state", [("require", "active"), ("bind", "active"), ("resume", "paused"), ("confirm", "active"), ("cleanup_failed", "active")])
def test_binding_consumers_reject_isolated_identity_before_state_change(database, operation, state):
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("INSERT INTO account_execution_bindings VALUES (?,?,?,?,?,?)", ("owner-a", "chat", "old-chat", 0, state, 1))
        _pin(conn, ("owner-a", "chat", "old-chat"))
        original = conn.execute("SELECT * FROM account_execution_bindings").fetchall()
        auth = execution.ExecutionAuthorization("owner-a", 0)
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(QuarantinedExecutionError):
            if operation == "require":
                execution.require_binding(conn, auth, "chat", "old-chat")
            elif operation == "bind":
                execution.bind_execution(conn, auth, "chat", "old-chat", now=2)
            elif operation == "resume":
                execution.resume_execution(conn, auth, "chat", "old-chat", expected_generation=0, now=2)
            else:
                execution.confirm_execution_stopped(conn, "owner-a", "chat", "old-chat", expected_generation=0, cleanup_failed=operation == "cleanup_failed", now=2)
        assert conn.execute("SELECT * FROM account_execution_bindings").fetchall() == original


def test_new_binding_is_not_inserted_under_a_quarantined_resource_id(database):
    with closing(sqlite3.connect(database)) as conn:
        _pin(conn, ("owner-a", "chat", "old-chat"))
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(QuarantinedExecutionError):
            execution.bind_execution(conn, execution.ExecutionAuthorization("owner-a", 0), "chat", "old-chat", now=2)
        assert conn.execute("SELECT count(*) FROM account_execution_bindings").fetchone()[0] == 0


def test_task_target_alone_blocks_reusing_its_id_as_an_execution_binding(database):
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("INSERT INTO account_execution_bindings VALUES ('owner-a','workspace','old-task',0,'active',1)")
        _pin(conn, ("owner-a", "task", "old-task"))
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(QuarantinedExecutionError):
            execution.require_binding(conn, execution.ExecutionAuthorization("owner-a", 0), "workspace", "old-task")


def test_account_hold_preserves_isolated_idle_binding_and_stops_independent_idle_binding(database):
    with closing(sqlite3.connect(database)) as conn:
        conn.executemany("INSERT INTO account_execution_bindings VALUES ('owner-a','chat',?,0,'idle',1)", [("old-chat",), ("independent-chat",)])
        _pin(conn, ("owner-a", "chat", "old-chat"))
        original = conn.execute("SELECT * FROM account_execution_bindings WHERE resource_id='old-chat'").fetchone()
        conn.execute("BEGIN IMMEDIATE")
        execution.update_account_status(conn, "owner-a", disabled=True, actor_user_id="synthetic-admin", now=2)
        assert conn.execute("SELECT * FROM account_execution_bindings WHERE resource_id='old-chat'").fetchone() == original
        assert conn.execute("SELECT state FROM account_execution_bindings WHERE resource_id='independent-chat'").fetchone()[0] == "paused"
        assert conn.execute("SELECT disabled FROM users WHERE user_id='owner-a'").fetchone()[0] == 1


def test_new_independent_binding_still_completes_its_existing_lifecycle(database):
    with closing(sqlite3.connect(database)) as conn:
        _pin(conn, ("owner-a", "chat", "old-chat"))
        conn.execute("BEGIN IMMEDIATE")
        auth = execution.ExecutionAuthorization("owner-a", 0)
        execution.bind_execution(conn, auth, "chat", "new-chat", now=1)
        assert execution.set_execution_state(conn, auth, "chat", "new-chat", state="idle", now=2)["state"] == "idle"


def test_account_hold_still_revokes_grant_even_when_its_old_binding_is_isolated(database):
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("INSERT INTO account_execution_bindings VALUES ('owner-a','chat','old-chat',0,'idle',1)")
        conn.execute("INSERT INTO model_connection_grants(grant_id,token_hash,owner_user_id,task_id,revision,run_id,connection_id,purpose,base_url,model,api_format,locality,expires_at,created_at) VALUES ('grant','synthetic-hash','owner-a','old-task',1,'old-chat','connection','agent_inference','https://synthetic.invalid','synthetic','openai_responses','external','2099-01-01T00:00:00+00:00','2026-01-01')")
        _pin(conn, ("owner-a", "chat", "old-chat"))
        conn.execute("BEGIN IMMEDIATE")
        execution.update_account_status(conn, "owner-a", disabled=True, actor_user_id="synthetic-admin", now=2)
        assert conn.execute("SELECT revoked_at IS NOT NULL FROM model_connection_grants").fetchone()[0] == 1
        assert conn.execute("SELECT state FROM account_execution_bindings").fetchone()[0] == "idle"


def test_new_hold_classifies_unquarantined_bindings_without_confirming_old_unknown(database):
    with closing(sqlite3.connect(database)) as conn:
        conn.executemany("INSERT INTO account_execution_bindings VALUES ('owner-a','chat',?,0,?,1)", [("old-chat", "cleanup_failed"), ("new-chat", "idle")])
        conn.execute("INSERT INTO account_execution_holds(operation_id,owner_user_id,generation,actor_user_id,reason,status,reconciliation_complete,created_at,updated_at) VALUES ('new-hold','owner-a',1,'synthetic-admin','disabled','pending',0,1,1)")
        _pin(conn, ("owner-a", "chat", "old-chat"))
        original = conn.execute("SELECT * FROM account_execution_bindings WHERE resource_id='old-chat'").fetchone()
        conn.execute("BEGIN IMMEDIATE")
        result = execution.refresh_hold_operation(conn, "new-hold", reconciliation_complete=True, now=2)
        assert result["status"] == "completed"
        assert conn.execute("SELECT * FROM account_execution_bindings WHERE resource_id='old-chat'").fetchone() == original


def test_exact_historical_hold_record_cannot_be_refreshed(database):
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("INSERT INTO account_execution_holds(operation_id,owner_user_id,generation,actor_user_id,reason,status,reconciliation_complete,created_at,updated_at) VALUES ('old-hold','owner-a',1,'synthetic-admin','disabled','pending',0,1,1)")
        _pin(conn, ("owner-a", "chat", "old-chat"), records=[{"profile": "webui", "table": "account_execution_holds", "key": {"operation_id": "old-hold"}, "sha256": "a" * 64}])
        original = conn.execute("SELECT * FROM account_execution_holds").fetchall()
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(QuarantinedExecutionError):
            execution.refresh_hold_operation(conn, "old-hold", reconciliation_complete=True, now=2)
        assert conn.execute("SELECT * FROM account_execution_holds").fetchall() == original


@pytest.mark.parametrize("operation", ["start", "recheck", "finish"])
def test_source_use_with_no_task_is_blocked_by_exact_use_id_and_releases_lock(database, operation):
    from src.source_acquisition.reuse import SourceReadUse

    with closing(sqlite3.connect(database)) as conn:
        conn.execute("INSERT INTO source_read_uses VALUES ('old-use','owner-a',NULL,NULL,'preview','[]','unknown','2026-01-01',NULL)")
        _pin(conn, ("owner-a", "source_read", "old-use"))
        original = conn.execute("SELECT * FROM source_read_uses").fetchall()
    use = SourceReadUse("owner-a", [], operation="preview")
    use.use_id = "old-use"
    released = []
    if operation == "finish":
        class HeldLock:
            def __exit__(self, *args):
                released.append(True)
        use._locks = HeldLock()
    with pytest.raises(QuarantinedExecutionError):
        if operation == "start":
            use.start()
        elif operation == "recheck":
            use.recheck()
        else:
            use.finish(known=True)
    if operation == "finish":
        assert released == [True]
        assert use._locks is None
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("SELECT * FROM source_read_uses").fetchall() == original


def test_new_source_use_cannot_reenter_an_isolated_task(database):
    from src.source_acquisition.reuse import SourceReadUse

    with closing(sqlite3.connect(database)) as conn:
        _pin(conn, ("owner-a", "task", "old-task"))
    with pytest.raises(QuarantinedExecutionError):
        SourceReadUse("owner-a", [], operation="preview", task_id="old-task", revision=1).start()
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("SELECT count(*) FROM source_read_uses").fetchone()[0] == 0


def test_recreated_source_use_cannot_hide_its_recorded_task_with_null_projection(database):
    from src.source_acquisition.reuse import SourceReadUse

    with closing(sqlite3.connect(database)) as conn:
        conn.execute("INSERT INTO source_read_uses VALUES ('old-use','owner-a','old-task',1,'preview','[]','unknown','2026-01-01',NULL)")
        _pin(conn, ("owner-a", "task", "old-task"))
    use = SourceReadUse("owner-a", [], operation="preview", task_id=None)
    use.use_id = "old-use"
    with pytest.raises(QuarantinedExecutionError):
        use.recheck()


def test_independent_source_use_still_registers_and_finishes(database):
    from src.source_acquisition.reuse import SourceReadUse

    with closing(sqlite3.connect(database)) as conn:
        _pin(conn, ("owner-a", "task", "old-task"))
    use = SourceReadUse("owner-a", [], operation="preview", task_id="new-task", revision=1).start()
    use.recheck()
    use.finish(known=True)
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("SELECT state,finished_at IS NOT NULL FROM source_read_uses WHERE use_id=?", (use.use_id,)).fetchone() == ("completed", 1)


def _deletion(conn, operation="old-operation", task="old-task", state="needs_confirmation"):
    plan = json.dumps({"task_id": task, "objects": [], "affected_tasks": [], "shared_policy": "keep_shared"})
    request_hash = hashlib.sha256(json.dumps([task, "token", "keep_shared"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    conn.execute("INSERT INTO source_deletion_operations VALUES (?,?,?,?,?,?,?,?,?,?)", (operation, "owner-a", task, "request-key-" + operation, request_hash, plan, state, None, "2026-01-01", "2026-01-01"))


@pytest.mark.parametrize("operation", ["begin_replay", "set_state", "continue", "reconfirm"])
def test_deletion_mutation_and_resume_reject_exact_operation_without_task_target(database, monkeypatch, operation):
    from src.source_acquisition import deletion
    from src.api.execution import execution_validation

    with closing(sqlite3.connect(database)) as conn:
        _deletion(conn)
        _pin(conn, ("owner-a", "deletion", "old-operation"))
        original = conn.execute("SELECT * FROM source_deletion_operations").fetchall()
    def forbidden_plan(*args):
        pytest.fail("隔离操作不能进入重新计算或删除计划")
    monkeypatch.setattr(deletion, "deletion_plan", forbidden_plan)
    with execution.execution_context(execution.ExecutionAuthorization("owner-a", 0)), execution_validation(lambda auth: None):
        with pytest.raises(QuarantinedExecutionError):
            if operation == "begin_replay":
                deletion.begin_operation("owner-a", "old-task", "token", "keep_shared", "request-key-old-operation")
            elif operation == "set_state":
                deletion._set_state("owner-a", "old-operation", "completed")
            elif operation == "reconfirm":
                deletion._reconfirm_operation("owner-a", "old-operation", "token", "keep_shared")
            else:
                asyncio.run(deletion.continue_operation("owner-a", "old-operation", object()))
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("SELECT * FROM source_deletion_operations").fetchall() == original


def test_actual_source_deletion_checks_operation_before_filesystem_access(database, monkeypatch):
    from src.source_acquisition import deletion

    with closing(sqlite3.connect(database)) as conn:
        _deletion(conn, state="cleaning")
        conn.execute("INSERT INTO source_deletions VALUES ('owner-a','upload:old-source','old-operation',?,NULL,'deleting',NULL)", ("a" * 64,))
        _pin(conn, ("owner-a", "deletion", "old-operation"))
        original = conn.execute("SELECT * FROM source_deletions").fetchall()
    def forbidden_uploads():
        pytest.fail("隔离删除不能访问原件存储")
    monkeypatch.setattr(deletion.reuse, "uploads", forbidden_uploads)
    with pytest.raises(QuarantinedExecutionError):
        deletion._remove_object("owner-a", {"kind": "upload", "source_key": "upload:old-source", "upload_id": "old-source", "sha256": "a" * 64})
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("SELECT * FROM source_deletions").fetchall() == original


def test_deletion_uses_original_operation_task_when_only_the_task_is_isolated(database):
    from src.source_acquisition import deletion

    with closing(sqlite3.connect(database)) as conn:
        _deletion(conn, state="incomplete")
        _pin(conn, ("owner-a", "task", "old-task"))
        original = conn.execute("SELECT * FROM source_deletion_operations").fetchall()
    with pytest.raises(QuarantinedExecutionError):
        deletion._set_state("owner-a", "old-operation", "completed")
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("SELECT * FROM source_deletion_operations").fetchall() == original


def test_quarantine_does_not_make_deleting_source_readable_and_keeps_independent_operation_available(database):
    from src.source_acquisition import deletion

    with closing(sqlite3.connect(database)) as conn:
        _deletion(conn)
        _deletion(conn, operation="new-operation", task="new-task", state="incomplete")
        conn.execute("INSERT INTO source_deletions VALUES ('owner-a','upload:old-source','old-operation',?,NULL,'deleting',NULL)", ("a" * 64,))
        _pin(conn, ("owner-a", "deletion", "old-operation"))
    with pytest.raises(ValueError, match="source_deleting"):
        deletion.assert_sources_readable("owner-a", [{"upload_id": "old-source"}])
    deletion._set_state("owner-a", "new-operation", "needs_confirmation")
    assert deletion.get_operation("owner-a", "old-operation")["state"] == "needs_confirmation"
    assert deletion.get_operation("owner-a", "new-operation")["state"] == "needs_confirmation"
