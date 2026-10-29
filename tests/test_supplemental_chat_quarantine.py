"""补充隔离仅覆盖两条已批准旧 Chat，原未知与首次隔离保持。"""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
import shutil
import sqlite3

import pytest

from src import account_execution as execution
import src.database_migrations as migrations
from src.database_migrations import DatabaseTarget, apply_migrations, inspect_database
from src.deployment_quarantine import (
    CHAT_ACCEPTED_RISK, CHAT_SETTING_KEY, EMPTY_MANIFEST, SETTING_KEY,
    QuarantinedExecutionError, assert_not_quarantined, is_quarantined,
    load_chat_quarantine, load_quarantine, quarantine_summary,
    register_chat_quarantine, register_quarantine, snapshot_record,
    verify_chat_quarantine_records,
)
from src.services.deployment_preflight import collect_persisted_blockers
from tests.database_migration_helpers import migrated_profile_database


@pytest.fixture(scope="module")
def templates(tmp_path_factory):
    directory = tmp_path_factory.mktemp("supplemental-chat-schema")
    return tuple(migrated_profile_database(directory / (profile + ".db"), profile=profile)
                 for profile in ("webui", "scheduler"))


@pytest.fixture
def databases(tmp_path, templates):
    webui, scheduler = (tmp_path / name for name in ("webui.db", "scheduler.db"))
    for source, destination in zip(templates, (webui, scheduler)):
        shutil.copyfile(source, destination)
    with closing(sqlite3.connect(webui)) as connection, connection:
        connection.executemany(
            "INSERT INTO users(user_id,username,password_hash,role,created_at) VALUES (?,?,'synthetic',?,'before')",
            [("operator", "synthetic-operator", "super_admin"),
             ("owner", "synthetic-owner", "user"), ("other", "synthetic-other", "user")],
        )
        connection.executemany(
            "INSERT INTO account_execution_bindings VALUES ('owner','chat',?,0,'cleanup_failed',1)",
            [("chat-a",), ("chat-b",)],
        )
    return webui, scheduler


def manifest(connection):
    targets = [{"owner_id": "owner", "kind": "chat", "resource_id": resource, "generation": 0}
               for resource in ("chat-a", "chat-b")]
    return {"version": 1, "approval_reference": "synthetic-two-chat-approval", "actor_id": "operator",
            "accepted_risk": CHAT_ACCEPTED_RISK, "targets": targets,
            "records": [snapshot_record(connection, "webui", "account_execution_bindings",
                                        {"owner_user_id": "owner", "resource_kind": "chat", "resource_id": target["resource_id"]})
                        for target in targets]}


def register(connection):
    connection.execute("BEGIN IMMEDIATE")
    value = manifest(connection)
    register_chat_quarantine(connection, value)
    connection.commit()
    return value


def binding_rows(connection):
    return connection.execute("SELECT * FROM account_execution_bindings ORDER BY owner_user_id,resource_kind,resource_id").fetchall()


def test_migration_initializes_independent_empty_authority_and_preserves_first_pin(databases):
    webui, _ = databases
    with closing(sqlite3.connect(webui)) as connection:
        original = connection.execute("SELECT value FROM app_settings WHERE key=?", (SETTING_KEY,)).fetchone()[0]
        rows = binding_rows(connection)
        assert load_chat_quarantine(connection) == EMPTY_MANIFEST
        assert load_quarantine(connection) == json.loads(original)
    status = inspect_database(DatabaseTarget("webui", webui))
    assert status.current_revision == "webui_0026" and status.state == "current"
    replay = apply_migrations(DatabaseTarget("webui", webui), webui.with_name("replay-backup.db"))
    assert replay.applied_revisions == ()
    with closing(sqlite3.connect(webui)) as connection:
        assert binding_rows(connection) == rows
        assert connection.execute("SELECT value FROM app_settings WHERE key=?", (SETTING_KEY,)).fetchone()[0] == original


def test_forward_migration_preserves_old_bindings_first_pin_and_restorable_backup(tmp_path):
    database = tmp_path / "old-webui.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("VACUUM")
    central_backup = tmp_path / "empty-central-backup.db"
    shutil.copyfile(database, central_backup)
    engine = migrations.create_engine(migrations.URL.create("sqlite", database=str(database)))
    try:
        with engine.begin() as connection:
            config = migrations._alembic_config(connection)
            config.attributes["backup_sha256"] = hashlib.sha256(central_backup.read_bytes()).hexdigest()
            migrations.command.upgrade(config, "webui_0025")
    finally:
        engine.dispose()
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("INSERT INTO users(user_id,username,password_hash,role,created_at) VALUES ('operator','synthetic-operator','synthetic','super_admin','before')")
        connection.execute("INSERT INTO account_execution_bindings VALUES ('owner','chat','chat-a',0,'cleanup_failed',1)")
        register_quarantine(connection, {
            "version": 1, "approval_reference": "synthetic-first-approval", "actor_id": "operator",
            "accepted_risk": "first_cutover_unknown_interruption",
            "targets": [{"owner_id": "owner", "kind": "chat", "resource_id": "chat-a", "generation": 0}],
            "records": [snapshot_record(connection, "webui", "account_execution_bindings",
                                        {"owner_user_id": "owner", "resource_kind": "chat", "resource_id": "chat-a"})],
        })
        original = binding_rows(connection)
        first = connection.execute("SELECT value FROM app_settings WHERE key=?", (SETTING_KEY,)).fetchone()[0]
        original_dump = "\n".join(connection.iterdump())
    original_bytes = database.read_bytes()
    receipt = apply_migrations(DatabaseTarget("webui", database), tmp_path / "before-chat-migration.db")
    assert receipt.source_revision == "webui_0025"
    assert receipt.target_revision == "webui_0026" and receipt.applied_revisions == ("webui_0026",)
    assert receipt.source_database_sha256 == hashlib.sha256(original_bytes).hexdigest()
    backup_bytes = receipt.backup_path.read_bytes()
    assert receipt.backup_sha256 == hashlib.sha256(backup_bytes).hexdigest()
    with closing(sqlite3.connect(receipt.backup_path)) as connection:
        # SQLite backup 会重建文件头；核全库逻辑原件，独立恢复再核备份字节。
        assert "\n".join(connection.iterdump()) == original_dump
    with closing(sqlite3.connect(database)) as connection:
        assert binding_rows(connection) == original
        assert connection.execute("SELECT value FROM app_settings WHERE key=?", (SETTING_KEY,)).fetchone()[0] == first
        assert load_chat_quarantine(connection) == EMPTY_MANIFEST
    restored = tmp_path / "independent-restored.db"
    shutil.copyfile(receipt.backup_path, restored)
    verification = migrations.verify_restored_copy(receipt.receipt_path, restored)
    assert verification.schema_state == "legacy" and restored.read_bytes() == backup_bytes


def test_registration_preserves_original_nonempty_quarantine_and_unknown_rows(databases):
    webui, _ = databases
    with closing(sqlite3.connect(webui)) as connection:
        connection.execute("INSERT INTO account_execution_bindings VALUES ('owner','chat','first-old-chat',0,'cleanup_failed',1)")
        first = {"version": 1, "approval_reference": "synthetic-first-approval", "actor_id": "operator",
                 "accepted_risk": "first_cutover_unknown_interruption",
                 "targets": [{"owner_id": "owner", "kind": "chat", "resource_id": "first-old-chat", "generation": 0}],
                 "records": [snapshot_record(connection, "webui", "account_execution_bindings",
                                             {"owner_user_id": "owner", "resource_kind": "chat", "resource_id": "first-old-chat"})]}
        register_quarantine(connection, first)
        connection.commit()
        before = binding_rows(connection)
        raw_first = connection.execute("SELECT value FROM app_settings WHERE key=?", (SETTING_KEY,)).fetchone()[0]
        value = register(connection)
        assert load_chat_quarantine(connection) == value
        assert binding_rows(connection) == before
        assert connection.execute("SELECT value FROM app_settings WHERE key=?", (SETTING_KEY,)).fetchone()[0] == raw_first
        assert load_quarantine(connection) == first
        connection.execute("BEGIN IMMEDIATE")
        changes = connection.total_changes
        assert register_chat_quarantine(connection, deepcopy(value)) == value
        assert connection.total_changes == changes
        connection.commit()
        summary = quarantine_summary(connection)
        assert (summary["targets"], summary["records"]) == (1, 1)
        assert summary["historical_stop_proven"] is False
        supplement = summary["supplemental_chat_quarantine"]
        assert (supplement["targets"], supplement["records"]) == (2, 2)
        assert supplement["accepted_risk"] == CHAT_ACCEPTED_RISK
        assert supplement["approval_reference"] == value["approval_reference"]
        assert supplement["historical_stop_proven"] is False


def test_registration_requires_caller_transaction(databases):
    with closing(sqlite3.connect(databases[0])) as connection:
        value = manifest(connection)
        with pytest.raises(ValueError):
            register_chat_quarantine(connection, value)


@pytest.mark.parametrize("mutate", [
    lambda value: value["targets"].pop(),
    lambda value: value["records"].pop(),
    lambda value: value["targets"].append(dict(value["targets"][0], resource_id="chat-c")),
    lambda value: value["targets"][0].update(owner_id="other"),
    lambda value: value["targets"][0].update(kind="workspace"),
    lambda value: value["targets"][0].update(generation=1),
    lambda value: value["targets"][0].update(generation=True),
    lambda value: value["targets"][0].update(generation=None),
    lambda value: value["records"][0].update(table="source_read_uses", key={"use_id": "synthetic"}),
    lambda value: value["records"][0].update(sha256="0" * 64),
    lambda value: value.update(accepted_risk="first_cutover_unknown_interruption"),
    lambda value: value.update(actor_id="owner"),
    lambda value: value.update(approval_reference=""),
])
def test_registration_rejects_scope_or_approval_or_original_mismatch(databases, mutate):
    with closing(sqlite3.connect(databases[0])) as connection:
        value = manifest(connection)
        mutate(value)
        before = binding_rows(connection)
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises((ValueError, PermissionError)):
            register_chat_quarantine(connection, value)
        assert load_chat_quarantine(connection) == EMPTY_MANIFEST
        assert binding_rows(connection) == before


@pytest.mark.parametrize("state", ["idle", "active", "paused"])
def test_even_current_snapshot_cannot_isolate_a_nonfailed_chat(databases, state):
    with closing(sqlite3.connect(databases[0])) as connection:
        connection.execute("UPDATE account_execution_bindings SET state=? WHERE resource_id='chat-a'", (state,))
        value = manifest(connection)
        with pytest.raises(ValueError):
            register_chat_quarantine(connection, value)


@pytest.mark.parametrize("operation", ["require", "bind", "resume", "confirm"])
def test_old_chat_cannot_execute_resume_rebind_or_receive_late_stop(databases, operation):
    with closing(sqlite3.connect(databases[0])) as connection:
        register(connection)
        before = binding_rows(connection)
        connection.execute("BEGIN IMMEDIATE")
        auth = execution.ExecutionAuthorization("owner", 0)
        with pytest.raises(QuarantinedExecutionError):
            if operation == "require":
                execution.require_binding(connection, auth, "chat", "chat-a")
            elif operation == "bind":
                execution.bind_execution(connection, auth, "chat", "chat-a", now=2)
            elif operation == "resume":
                execution.resume_execution(connection, auth, "chat", "chat-a", expected_generation=0, now=2)
            else:
                execution.confirm_execution_stopped(connection, "owner", "chat", "chat-a", expected_generation=0, now=2)
        assert binding_rows(connection) == before


def test_independent_chat_and_other_owner_or_kind_remain_available(databases):
    with closing(sqlite3.connect(databases[0])) as connection:
        register(connection)
        connection.execute("BEGIN IMMEDIATE")
        for owner, kind, resource in (("owner", "chat", "new-chat"), ("other", "chat", "chat-a"), ("owner", "data", "chat-a")):
            assert not is_quarantined(connection, owner, kind, resource)
            auth = execution.ExecutionAuthorization(owner, 0)
            assert execution.bind_execution(connection, auth, kind, resource, state="idle", now=2)["state"] == "idle"


@pytest.mark.parametrize("statement", [
    "UPDATE account_execution_bindings SET state='paused' WHERE owner_user_id='owner' AND resource_id='chat-a'",
    "UPDATE account_execution_bindings SET owner_user_id='other' WHERE owner_user_id='owner' AND resource_id='chat-a'",
    "DELETE FROM account_execution_bindings WHERE owner_user_id='owner' AND resource_id='chat-a'",
    "INSERT OR REPLACE INTO account_execution_bindings VALUES ('owner','chat','chat-a',7,'active',2)",
    "UPDATE OR REPLACE account_execution_bindings SET resource_id='chat-a' WHERE owner_user_id='owner' AND resource_id='new-chat'",
    "UPDATE OR REPLACE account_execution_bindings SET owner_user_id='owner' WHERE owner_user_id='other' AND resource_id='chat-a'",
])
def test_late_sql_cannot_rewrite_or_replace_isolated_chat_original(databases, statement):
    with closing(sqlite3.connect(databases[0])) as connection:
        register(connection)
        connection.execute("INSERT INTO account_execution_bindings VALUES ('owner','chat','new-chat',0,'idle',2)")
        connection.execute("INSERT INTO account_execution_bindings VALUES ('other','chat','chat-a',0,'idle',2)")
        connection.commit()
        before = binding_rows(connection)
        assert connection.execute("PRAGMA recursive_triggers").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(statement)
        assert binding_rows(connection) == before


@pytest.mark.parametrize("statement", [
    "UPDATE app_settings SET value='{}' WHERE key='deployment.supplemental_chat_quarantine'",
    "DELETE FROM app_settings WHERE key='deployment.supplemental_chat_quarantine'",
    "INSERT OR REPLACE INTO app_settings VALUES ('deployment.supplemental_chat_quarantine','{}')",
    "UPDATE OR REPLACE app_settings SET key='deployment.supplemental_chat_quarantine' WHERE key='synthetic-other'",
])
def test_fixed_supplement_cannot_be_changed_cleared_or_replaced(databases, statement):
    with closing(sqlite3.connect(databases[0])) as connection:
        register(connection)
        connection.execute("INSERT INTO app_settings VALUES ('synthetic-other','{}')")
        connection.commit()
        before = connection.execute("SELECT * FROM app_settings ORDER BY key").fetchall()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(statement)
        assert connection.execute("SELECT * FROM app_settings ORDER BY key").fetchall() == before


def test_preflight_excludes_only_the_two_matching_records_and_still_blocks_new_cleanup(databases):
    webui, scheduler = databases
    assert [(item.check_id, item.count) for item in collect_persisted_blockers(webui, scheduler)] == [("persisted.account_cleanup", 2)]
    with closing(sqlite3.connect(webui)) as connection:
        register(connection)
        verify_chat_quarantine_records(connection, load_chat_quarantine(connection))
        assert connection.execute("SELECT count(*) FROM runtime_run_seals").fetchone()[0] == 0
    assert collect_persisted_blockers(webui, scheduler) == ()
    with closing(sqlite3.connect(webui)) as connection, connection:
        connection.execute("INSERT INTO account_execution_bindings VALUES ('owner','chat','chat-c',0,'cleanup_failed',2)")
    assert [(item.check_id, item.count) for item in collect_persisted_blockers(webui, scheduler)] == [("persisted.account_cleanup", 1)]


def test_supplement_cannot_duplicate_an_original_first_quarantine_target(databases):
    with closing(sqlite3.connect(databases[0])) as connection:
        value = manifest(connection)
        original = {**value, "accepted_risk": "first_cutover_unknown_interruption",
                    "approval_reference": "synthetic-first-approval",
                    "targets": [value["targets"][0]], "records": [value["records"][0]]}
        connection.execute("BEGIN IMMEDIATE")
        register_quarantine(connection, original)
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError):
            register_chat_quarantine(connection, value)
        assert load_quarantine(connection) == original
        assert load_chat_quarantine(connection) == EMPTY_MANIFEST


def test_changed_original_is_not_excluded_by_a_stale_supplement(databases):
    webui, scheduler = databases
    with closing(sqlite3.connect(webui)) as connection:
        register(connection)
        # 仅在临时库模拟外部破坏；正常迟到写已由数据库触发器拒绝。
        for name, in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'supplemental_chat_binding_%'").fetchall():
            connection.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
        connection.execute("UPDATE account_execution_bindings SET generation=9 WHERE resource_id='chat-a'")
        connection.commit()
        with pytest.raises(ValueError):
            verify_chat_quarantine_records(connection, load_chat_quarantine(connection))
    assert collect_persisted_blockers(webui, scheduler)


@pytest.mark.parametrize("value", [None, "", "null", "[]", "{}", "not-json", '{"version":1,"version":1}'])
def test_missing_or_corrupt_supplement_never_reopens_execution(databases, value):
    with closing(sqlite3.connect(databases[0])) as connection:
        # 仅在临时库制造损坏，正常应用不能移除这些数据库保护。
        for name, in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='app_settings'").fetchall():
            connection.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
        connection.execute("DELETE FROM app_settings WHERE key=?", (CHAT_SETTING_KEY,))
        if value is not None:
            connection.execute("INSERT INTO app_settings VALUES (?,?)", (CHAT_SETTING_KEY, value))
        connection.commit()
        with pytest.raises(QuarantinedExecutionError):
            assert_not_quarantined(connection, "owner", "chat", "new-chat")


def test_fixed_registration_cannot_be_expanded_or_reset_via_public_primitive(databases):
    with closing(sqlite3.connect(databases[0])) as connection:
        value = register(connection)
        connection.execute("BEGIN IMMEDIATE")
        changed = deepcopy(value)
        changed["approval_reference"] = "another-synthetic-approval"
        for candidate in (changed, EMPTY_MANIFEST):
            with pytest.raises(ValueError):
                register_chat_quarantine(connection, candidate)
        assert load_chat_quarantine(connection) == value
