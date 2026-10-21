"""历史隔离只解除精确旧项阻断，新增未知继续阻止部署。"""
from contextlib import closing
import sqlite3

import pytest

from src.deployment_quarantine import register_quarantine, snapshot_record
from src.services.deployment_preflight import collect_persisted_blockers
from tests.database_migration_helpers import migrated_profile_database
from tests.test_runtime_run_seals import unknown_run, run_seal_database_template


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_preflight_excludes_only_the_exact_quarantined_hold_across_all_state_branches(tmp_path, status):
    database = migrated_profile_database(tmp_path / "webui.db", profile="webui")
    scheduler = migrated_profile_database(tmp_path / "scheduler.db", profile="scheduler")
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("INSERT INTO users(user_id,username,password_hash,role,created_at) "
                           "VALUES ('operator','synthetic-operator','synthetic','super_admin','before')")
        connection.execute("INSERT INTO account_execution_holds(operation_id,owner_user_id,generation,actor_user_id,"
                           "reason,status,reconciliation_complete,error_code,created_at,updated_at) "
                           "VALUES ('old-hold','operator',1,'operator','disabled',?,0,'cleanup_failed','before','before')", (status,))
        register_quarantine(connection, {
            "version": 1, "approval_reference": "synthetic-approved-cutover", "actor_id": "operator",
            "accepted_risk": "first_cutover_unknown_interruption", "targets": [],
            "records": [snapshot_record(connection, "webui", "account_execution_holds", {"operation_id": "old-hold"})],
        })
    assert collect_persisted_blockers(database, scheduler) == ()
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("INSERT INTO account_execution_holds(operation_id,owner_user_id,generation,actor_user_id,"
                           "reason,status,reconciliation_complete,created_at,updated_at) "
                           "VALUES ('new-hold','operator',2,'operator','disabled','pending',0,'now','now')")
    blockers = collect_persisted_blockers(database, scheduler)
    assert [(item.check_id, item.count) for item in blockers] == [("persisted.account_hold", 1)]


def test_preflight_classifies_exact_quarantine_without_forging_run_seal(unknown_run, tmp_path):
    database = unknown_run["db"]
    scheduler = migrated_profile_database(tmp_path / "scheduler.db", profile="scheduler")
    assert {item.check_id for item in collect_persisted_blockers(database, scheduler)} >= {
        "persisted.runtime_unknown", "persisted.workspace_unknown",
    }
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        records = [snapshot_record(connection, "webui", table, key) for table, key in (
            ("agentic_runtime_runs", {"user_id": "owner", "task_id": "task", "revision": 1}),
            ("semantic_workspace_tasks", {"task_id": "task"}),
            ("account_execution_bindings", {"owner_user_id": "owner", "resource_kind": "workspace", "resource_id": "task"}),
        )]
        register_quarantine(connection, {
            "version": 1, "approval_reference": "synthetic-approved-cutover", "actor_id": "operator",
            "accepted_risk": "first_cutover_unknown_interruption", "records": records,
            "targets": [
                {"owner_id": "owner", "kind": "task", "resource_id": "task", "generation": 2},
                {"owner_id": "owner", "kind": "workspace", "resource_id": "task", "generation": 2},
            ],
        })
    assert collect_persisted_blockers(database, scheduler) == ()
    with closing(sqlite3.connect(database)) as connection, connection:
        assert connection.execute("SELECT count(*) FROM runtime_run_seals").fetchone()[0] == 0
        connection.execute("INSERT INTO source_read_uses VALUES ('new-use','owner',NULL,NULL,'preview','[]','active','now',NULL)")
    assert "persisted.source_reads" in {item.check_id for item in collect_persisted_blockers(database, scheduler)}


@pytest.mark.parametrize("statement", [
    "UPDATE agentic_runtime_runs SET failure_json=NULL WHERE task_id='task'",
    "DELETE FROM agentic_runtime_events WHERE event_id='unknown-event'",
    "INSERT OR REPLACE INTO agentic_runtime_events(event_id,user_id,task_id,revision,event_type,summary,details_json,created_at) VALUES ('unknown-event','other-owner','new-task',1,'late','synthetic','{}','now')",
    "UPDATE account_execution_bindings SET state='paused' WHERE resource_id='task'",
    "UPDATE app_settings SET value='{}' WHERE key='deployment.first_cutover_quarantine'",
    "DELETE FROM app_settings WHERE key='deployment.first_cutover_quarantine'",
    "INSERT OR REPLACE INTO app_settings VALUES ('deployment.first_cutover_quarantine','{}')",
])
def test_late_sql_and_replace_cannot_rewrite_original_or_erase_quarantine(unknown_run, statement):
    import json

    database = unknown_run["db"]
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        records = [snapshot_record(connection, "webui", table, key) for table, key in (
            ("agentic_runtime_runs", {"user_id": "owner", "task_id": "task", "revision": 1}),
            ("account_execution_bindings", {"owner_user_id": "owner", "resource_kind": "workspace", "resource_id": "task"}),
        )]
        register_quarantine(connection, {
            "version": 1, "approval_reference": "synthetic-approved-cutover", "actor_id": "operator",
            "accepted_risk": "first_cutover_unknown_interruption", "records": records,
            "targets": [{"owner_id": "owner", "kind": "task", "resource_id": "task", "generation": 2},
                        {"owner_id": "owner", "kind": "workspace", "resource_id": "task", "generation": 2}],
        })
    with closing(sqlite3.connect(database)) as connection:
        before = {table: connection.execute(f'SELECT * FROM "{table}"').fetchall()
                  for table in ("agentic_runtime_runs", "agentic_runtime_events", "account_execution_bindings", "app_settings")}
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(statement)
        for table, original in before.items():
            assert connection.execute(f'SELECT * FROM "{table}"').fetchall() == original
        value = connection.execute("SELECT value FROM app_settings WHERE key='deployment.first_cutover_quarantine'").fetchone()[0]
        assert json.loads(value)["accepted_risk"] == "first_cutover_unknown_interruption"
