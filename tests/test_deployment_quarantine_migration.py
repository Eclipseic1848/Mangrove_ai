"""首次切换隔离只增加专用权威，保留既有业务事实。"""
import json
import sqlite3
from contextlib import closing

import pytest

from tests.database_migration_helpers import migrated_webui_database


def test_explicit_migration_initializes_fail_closed_quarantine_authority(tmp_path):
    database = migrated_webui_database(tmp_path / "webui.db")
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute(
            "SELECT value FROM app_settings WHERE key='deployment.first_cutover_quarantine'"
        ).fetchone()
    assert row is not None, "缺少隔离权威不能被普通启动默认为无历史隔离"
    assert json.loads(row[0]) == {
        "version": 1, "targets": [], "records": [], "approval_reference": None,
        "actor_id": None, "accepted_risk": None,
    }


def test_registration_rejects_a_changed_original_before_persisting_exception(tmp_path):
    from src.deployment_quarantine import register_quarantine

    database = migrated_webui_database(tmp_path / "webui.db")
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("INSERT INTO users(user_id,username,password_hash,role,created_at) "
                           "VALUES ('operator','synthetic-operator','synthetic','super_admin','before')")
        connection.execute("INSERT INTO account_execution_bindings VALUES ('owner','chat','old-chat',0,'cleanup_failed',1)")
        manifest = {
            "version": 1, "approval_reference": "synthetic-approved-cutover", "actor_id": "operator",
            "accepted_risk": "first_cutover_unknown_interruption",
            "targets": [{"owner_id": "owner", "kind": "chat", "resource_id": "old-chat", "generation": 0}],
            "records": [{"profile": "webui", "table": "account_execution_bindings",
                         "key": {"owner_user_id": "owner", "resource_kind": "chat", "resource_id": "old-chat"},
                         "sha256": "0" * 64}],
        }
        with pytest.raises(ValueError, match="原件"):
            register_quarantine(connection, manifest)


@pytest.mark.parametrize("other_key", ["synthetic-other-setting", None])
def test_update_or_replace_from_another_setting_cannot_clear_fixed_quarantine(tmp_path, other_key):
    from src.deployment_quarantine import register_quarantine, snapshot_record

    database = migrated_webui_database(tmp_path / "webui.db")
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("INSERT INTO users(user_id,username,password_hash,role,created_at) "
                           "VALUES ('operator','synthetic-operator','synthetic','super_admin','before')")
        connection.execute("INSERT INTO account_execution_bindings VALUES ('owner','chat','old-chat',0,'cleanup_failed',1)")
        empty = connection.execute("SELECT value FROM app_settings WHERE key='deployment.first_cutover_quarantine'").fetchone()[0]
        register_quarantine(connection, {
            "version": 1, "approval_reference": "synthetic-approved-cutover", "actor_id": "operator",
            "accepted_risk": "first_cutover_unknown_interruption",
            "targets": [{"owner_id": "owner", "kind": "chat", "resource_id": "old-chat", "generation": 0}],
            "records": [snapshot_record(connection, "webui", "account_execution_bindings",
                                        {"owner_user_id": "owner", "resource_kind": "chat", "resource_id": "old-chat"})],
        })
        connection.execute("INSERT INTO app_settings VALUES (?,?)", (other_key, empty))
        before = connection.execute("SELECT * FROM app_settings ORDER BY key").fetchall()
        assert connection.execute("PRAGMA recursive_triggers").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE OR REPLACE app_settings SET key='deployment.first_cutover_quarantine' "
                               "WHERE key IS ?", (other_key,))
        assert connection.execute("SELECT * FROM app_settings ORDER BY key").fetchall() == before


@pytest.mark.parametrize("operation", ["INSERT", "UPDATE"])
@pytest.mark.parametrize("table,key,kind,values,changed", [
    ("source_acquisition_attempts", "attempt_id", "source",
     {"owner_id": "owner", "idempotency_key": "old-key", "status": "acquiring"}, {"idempotency_key": "new-key"}),
    ("account_execution_holds", "operation_id", None,
     {"owner_user_id": "owner", "generation": 1, "status": "pending", "reason": "disabled"}, {"generation": 2}),
    ("delivery_publish_intents", "publication_key", "task",
     {"owner_id": "owner", "task_id": "old-task", "request_idempotency_hash": "old-key", "status": "staging"},
     {"request_idempotency_hash": "new-key"}),
    ("candidate_verification_attempts", "attempt_id", "candidate",
     {"owner_id": "owner", "task_id": "old-task", "revision": 1, "run_id": "old-run", "candidate_set_hash": "old-hash",
      "idempotency_key": "old-key", "status": "requested", "reason_code": "initial", "ruleset_identity_status": "versioned"},
     {"candidate_set_hash": "new-hash"}),
])
def test_replace_through_secondary_unique_keys_cannot_delete_quarantined_original(tmp_path, operation, table, key, kind, values, changed):
    from src.deployment_quarantine import RECORD_KEYS, register_quarantine, snapshot_record
    from tests.test_deployment_preflight import _insert

    database = migrated_webui_database(tmp_path / "webui.db")
    _insert(database, "users", user_id="operator", username="synthetic-operator", role="super_admin")
    _insert(database, table, **{key: "old-id", **values})
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.row_factory = sqlite3.Row
        record_key = {name: values.get(name, "old-id") for name in RECORD_KEYS[("webui", table)]}
        records = [snapshot_record(connection, "webui", table, record_key)]
        targets = []
        if kind == "task":
            targets = [{"owner_id": "owner", "kind": "task", "resource_id": "old-task", "generation": None}]
        elif kind:
            connection.execute("INSERT INTO account_execution_bindings VALUES ('owner',?,'old-id',0,'idle',1)", (kind,))
            records.append(snapshot_record(connection, "webui", "account_execution_bindings",
                                           {"owner_user_id": "owner", "resource_kind": kind, "resource_id": "old-id"}))
            targets = [{"owner_id": "owner", "kind": kind, "resource_id": "old-id", "generation": 0}]
        register_quarantine(connection, {
            "version": 1, "approval_reference": "synthetic-approved-cutover", "actor_id": "operator",
            "accepted_risk": "first_cutover_unknown_interruption", "records": records, "targets": targets,
        })
        original = dict(connection.execute(f'SELECT * FROM "{table}" WHERE "{key}"=?', ("old-id",)).fetchone())
        replacement = {**original, key: "new-id"}
        if kind == "task":
            replacement["task_id"] = "new-task"
        if kind == "candidate":
            replacement["idempotency_key"] = "new-key"
        columns = ",".join(f'"{name}"' for name in replacement)
        insert = f'INSERT INTO "{table}" ({columns}) VALUES ({",".join("?" for _ in replacement)})'
        if operation == "UPDATE":
            connection.execute(insert, tuple({**replacement, **changed}.values()))
            statement = f'UPDATE OR REPLACE "{table}" SET ' + ",".join(f'"{name}"=?' for name in changed) + f' WHERE "{key}"=?'
            arguments = (*[replacement[name] for name in changed], "new-id")
        else:
            statement = insert.replace("INSERT INTO", "INSERT OR REPLACE INTO", 1)
            arguments = tuple(replacement.values())
        before = [tuple(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY "{key}"')]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(statement, arguments)
        assert [tuple(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY "{key}"')] == before


def test_partial_unique_guard_does_not_block_a_nonconflicting_new_candidate(tmp_path):
    from tests.test_deployment_preflight import _insert

    database = migrated_webui_database(tmp_path / "webui.db")
    values = {"owner_id": "owner", "task_id": "task", "revision": 1, "run_id": "run", "candidate_set_hash": "same",
              "reason_code": "initial", "ruleset_identity_status": "versioned"}
    _insert(database, "candidate_verification_attempts", attempt_id="old", idempotency_key="old", status="requested", **values)
    with closing(sqlite3.connect(database)) as connection, connection:
        # 只验证SQL冲突谓词，不把直接设置夹具冒充生产登记。
        manifest = {"version": 1, "targets": [], "approval_reference": "synthetic", "actor_id": "operator",
                    "accepted_risk": "first_cutover_unknown_interruption",
                    "records": [{"profile": "webui", "table": "candidate_verification_attempts", "key": {"attempt_id": "old"}, "sha256": "0" * 64}]}
        connection.execute("UPDATE app_settings SET value=? WHERE key='deployment.first_cutover_quarantine'", (json.dumps(manifest),))
    _insert(database, "candidate_verification_attempts", attempt_id="new", idempotency_key="new", status="failed", **values)
