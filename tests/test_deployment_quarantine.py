"""固定历史隔离只追加部署配置，原未知与其他独立执行保持。"""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
import hashlib
import json
import shutil
import sqlite3

import pytest

from src.deployment_quarantine import (
    ACCEPTED_RISK, EMPTY_MANIFEST, SETTING_KEY, QuarantinedExecutionError,
    assert_not_quarantined, assert_task_not_quarantined, is_quarantined,
    load_quarantine, register_quarantine, snapshot_record, verify_quarantine_records,
)
from tests.database_migration_helpers import migrated_profile_database, migrated_webui_database


@pytest.fixture(scope="module")
def quarantine_template(tmp_path_factory):
    return migrated_webui_database(tmp_path_factory.mktemp("quarantine-schema") / "webui.db")


@pytest.fixture
def database(tmp_path, quarantine_template):
    path = tmp_path / "webui.db"
    shutil.copyfile(quarantine_template, path)
    return path


def _manifest(*targets):
    return {"version": 1, "approval_reference": "issue-244-synthetic-approval", "actor_id": "synthetic-super-admin",
            "accepted_risk": ACCEPTED_RISK, "targets": list(targets), "records": []}


def _target(owner="owner-a", kind="chat", resource="old-chat", generation=0):
    return {"owner_id": owner, "kind": kind, "resource_id": resource, "generation": generation}


def _force_pin(conn, value):
    # 仅在临时库制造缺失/损坏；先移除该KV的数据库保护，不将其冒充正常应用操作。
    for name, in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='app_settings'").fetchall():
        conn.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
    conn.execute("DELETE FROM app_settings WHERE key=?", (SETTING_KEY,))
    if value is not None:
        conn.execute("INSERT INTO app_settings VALUES (?,?)", (SETTING_KEY, value if isinstance(value, str) else json.dumps(value)))
    conn.commit()


def test_migration_initializes_explicit_empty_manifest_without_read_writes(database):
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    with sqlite3.connect(database) as conn:
        assert load_quarantine(conn) == EMPTY_MANIFEST
        assert not is_quarantined(conn, "owner-a", "chat", "new-chat")
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("value", [None, "", "null", "[]", "{}", "not-json", '{"version":1,"version":1}'])
def test_missing_or_corrupted_pin_cannot_reopen_execution(database, value):
    with sqlite3.connect(database) as conn:
        _force_pin(conn, value)
        with pytest.raises(QuarantinedExecutionError):
            assert_not_quarantined(conn, "owner-a", "chat", "old-chat")


def test_matching_is_exact_owner_kind_and_resource_not_owner_wide(database):
    with sqlite3.connect(database) as conn:
        _force_pin(conn, _manifest(_target()))
        assert is_quarantined(conn, "owner-a", "chat", "old-chat")
        with pytest.raises(QuarantinedExecutionError):
            assert_not_quarantined(conn, "owner-a", "chat", "old-chat")
        for identity in (("owner-b", "chat", "old-chat"), ("owner-a", "chat", "new-chat"), ("owner-a", "data", "old-chat")):
            assert_not_quarantined(conn, *identity)


def test_task_target_blocks_old_task_and_keeps_new_independent_task_available(database):
    with sqlite3.connect(database) as conn:
        _force_pin(conn, _manifest(_target(kind="task", resource="old-task", generation=None)))
        with pytest.raises(QuarantinedExecutionError):
            assert_task_not_quarantined(conn, "owner-a", "old-task")
        assert_task_not_quarantined(conn, "owner-a", "new-task")
        assert_task_not_quarantined(conn, "owner-b", "old-task")


def test_changed_generation_or_state_does_not_unquarantine_the_old_identity(database):
    with sqlite3.connect(database) as conn:
        conn.execute("INSERT INTO account_execution_bindings VALUES ('owner-a','chat','old-chat',0,'cleanup_failed',1)")
        _force_pin(conn, _manifest(_target()))
        conn.execute("UPDATE account_execution_bindings SET generation=1,state='active'")
        assert is_quarantined(conn, "owner-a", "chat", "old-chat")


@pytest.mark.parametrize("generation", [None, 0, 3])
def test_recorded_generation_is_metadata_not_a_requirement_to_claim_a_historical_pid(database, generation):
    with sqlite3.connect(database) as conn:
        _force_pin(conn, _manifest(_target(kind="source_read", resource="old-use", generation=generation)))
        assert is_quarantined(conn, "owner-a", "source_read", "old-use")


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(version=True),
    lambda value: value.update(accepted_risk="historical_stop_proven"),
    lambda value: value.update(approval_reference=""),
    lambda value: value.update(actor_id=None),
    lambda value: value.update(extra="unexpected"),
    lambda value: value["targets"][0].update(kind="owner"),
    lambda value: value["targets"][0].update(generation=True),
    lambda value: value["targets"][0].update(generation=-1),
    lambda value: value["targets"][0].update(resource_id=""),
    lambda value: value["targets"].append(dict(value["targets"][0], generation=2)),
])
def test_invalid_or_ambiguous_manifest_is_fail_closed(database, mutate):
    manifest = _manifest(_target())
    mutate(manifest)
    with sqlite3.connect(database) as conn:
        _force_pin(conn, manifest)
        with pytest.raises(QuarantinedExecutionError):
            load_quarantine(conn)


@pytest.mark.parametrize("record", [
    {"profile": "webui", "table": "app_settings", "key": {"key": SETTING_KEY}, "sha256": "a" * 64},
    {"profile": "webui", "table": "source_read_uses", "key": {"owner_id": "owner-a", "use_id": "old-use"}, "sha256": "a" * 64},
    {"profile": "webui", "table": "source_read_uses", "key": {"use_id": "old-use"}, "sha256": "not-a-digest"},
    {"profile": "scheduler", "table": "scheduled_task_runs", "key": {"run_id": True}, "sha256": "a" * 64},
])
def test_record_whitelist_uses_actual_fixed_primary_keys(database, record):
    with sqlite3.connect(database) as conn:
        manifest = _manifest(_target())
        manifest["records"] = [record]
        _force_pin(conn, manifest)
        with pytest.raises(QuarantinedExecutionError):
            load_quarantine(conn)


def test_hold_dependency_and_scheduler_record_shapes_are_supported(database):
    manifest = _manifest(_target())
    manifest["records"] = [
        {"profile": "webui", "table": "account_execution_holds", "key": {"operation_id": "old-hold"}, "sha256": "a" * 64},
        {"profile": "scheduler", "table": "scheduled_tasks", "key": {"task_id": "old-schedule"}, "sha256": "b" * 64},
    ]
    with sqlite3.connect(database) as conn:
        _force_pin(conn, manifest)
        assert load_quarantine(conn) == manifest


def test_registration_requires_explicit_transaction(database):
    with sqlite3.connect(database) as conn:
        _force_pin(conn, EMPTY_MANIFEST)
        with pytest.raises(ValueError, match="显式事务"):
            register_quarantine(conn, _manifest(_target()))


def test_registration_is_once_idempotent_and_preserves_original_facts(database):
    manifest = _manifest(_target())
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("INSERT INTO users(user_id,username,password_hash,role,created_at) "
                     "VALUES ('synthetic-super-admin','synthetic-super-admin','synthetic','super_admin','before')")
        conn.execute("INSERT INTO account_execution_bindings VALUES ('owner-a','chat','old-chat',0,'cleanup_failed',1)")
        original = dict(conn.execute("SELECT * FROM account_execution_bindings").fetchone())
        manifest["records"] = [{"profile": "webui", "table": "account_execution_bindings",
                                "key": {"owner_user_id": "owner-a", "resource_kind": "chat", "resource_id": "old-chat"},
                                "sha256": hashlib.sha256(json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()}]
        conn.commit()
        _force_pin(conn, EMPTY_MANIFEST)
        conn.execute("BEGIN IMMEDIATE")
        assert register_quarantine(conn, manifest) == manifest
        conn.commit()
        before = conn.total_changes
        conn.execute("BEGIN IMMEDIATE")
        assert register_quarantine(conn, deepcopy(manifest)) == manifest
        assert conn.total_changes == before
        assert dict(conn.execute("SELECT * FROM account_execution_bindings").fetchone()) == original
        assert load_quarantine(conn) == manifest
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        changed = deepcopy(manifest)
        changed["targets"].append(_target(resource="another-old-chat"))
        with pytest.raises(ValueError, match="已固定"):
            register_quarantine(conn, changed)
        with pytest.raises(ValueError, match="已固定"):
            register_quarantine(conn, EMPTY_MANIFEST)
        assert dict(conn.execute("SELECT * FROM account_execution_bindings").fetchone()) == original


@pytest.fixture(scope="module")
def scheduler_template(tmp_path_factory):
    return migrated_profile_database(tmp_path_factory.mktemp("quarantine-scheduler") / "scheduler.db", profile="scheduler")


@pytest.fixture
def scheduler_database(tmp_path, scheduler_template):
    path = tmp_path / "scheduler.db"
    shutil.copyfile(scheduler_template, path)
    return path


def _seed_related_record(conn, scheduler, case):
    if case == "scheduler":
        scheduler.execute("INSERT INTO scheduled_tasks(task_id,user_input,owner_user_id,trigger_type,created_at) VALUES ('old-schedule','synthetic','owner-a','once','before')")
        scheduler.execute("INSERT INTO scheduled_task_runs(run_id,task_id,run_at,success) VALUES (1,'old-schedule','before',0)")
        return dict(source=scheduler, profile="scheduler", table="scheduled_task_runs", key={"run_id": 1},
                    parent="scheduled_tasks", parent_key={"task_id": "old-schedule"}, owner_field="owner_user_id", kind="schedule", resource="old-schedule")
    platform = case == "platform"
    prefix = "capability_platform_validation" if platform else "capability_validation"
    if platform:
        conn.execute("INSERT INTO capability_platform_validation_runs VALUES ('old-run','pack','1','old-digest','key','running',?,'before','before')", (json.dumps({"actor_id": "owner-a"}),))
    else:
        conn.execute("INSERT INTO capability_validation_runs VALUES ('old-run','owner-a','pack','1','old-digest','key','running','{}','before','before')")
    conn.execute(f"INSERT INTO {prefix}_leases VALUES ('old-digest','old-run','worker','before','later')")
    return dict(source=conn, profile="webui", table=prefix + "_leases", key={"digest": "old-digest"},
                parent=prefix + "_runs", parent_key={"run_id": "old-run"}, owner_field="payload_json" if platform else "owner_id",
                kind="validation", resource="platform:old-run" if platform else "old-run")


@pytest.fixture(params=["scheduler", "personal", "platform"])
def related_record(database, scheduler_database, request):
    with closing(sqlite3.connect(database)) as conn, closing(sqlite3.connect(scheduler_database)) as scheduler:
        related = _seed_related_record(conn, scheduler, request.param)
        conn.execute("INSERT INTO users(user_id,username,password_hash,role,created_at) VALUES ('synthetic-super-admin','synthetic-super-admin','synthetic','super_admin','before')")
        conn.execute("INSERT INTO account_execution_bindings VALUES ('owner-a',?,?,0,'cleanup_failed',1)", (related["kind"], related["resource"]))
        conn.commit()
        scheduler.commit()
        yield conn, scheduler, related


def _related_packet(conn, related, *, owner="owner-a", include_parent=True):
    manifest = _manifest(_target(owner=owner, kind=related["kind"], resource=related["resource"]))
    manifest["records"] = [snapshot_record(conn, "webui", "account_execution_bindings",
                                         {"owner_user_id": owner, "resource_kind": related["kind"], "resource_id": related["resource"]}),
                           snapshot_record(related["source"], related["profile"], related["table"], related["key"])]
    if include_parent:
        manifest["records"].append(snapshot_record(related["source"], related["profile"], related["parent"], related["parent_key"]))
    return manifest


def _change_related_owner(related, value):
    source, key = related["source"], related["parent_key"]
    column = next(iter(key))
    if related["owner_field"] == "payload_json":
        value = json.dumps({"actor_id": value})
    source.execute(f"UPDATE {related['parent']} SET {related['owner_field']}=? WHERE {column}=?", (value, key[column]))
    source.commit()


def test_related_original_registers_with_its_real_parent_owner(related_record):
    conn, scheduler, related = related_record
    manifest = _related_packet(conn, related)
    conn.execute("BEGIN IMMEDIATE")
    assert register_quarantine(conn, manifest, scheduler_connection=scheduler) == manifest
    conn.commit()
    assert load_quarantine(conn) == manifest


def test_related_original_digest_binds_parent_owner(related_record):
    conn, scheduler, related = related_record
    before = snapshot_record(related["source"], related["profile"], related["table"], related["key"])
    _change_related_owner(related, "owner-b")
    assert snapshot_record(related["source"], related["profile"], related["table"], related["key"]) != before


def test_related_original_rejects_missing_parent(related_record):
    conn, scheduler, related = related_record
    column = next(iter(related["parent_key"]))
    related["source"].execute(f"DELETE FROM {related['parent']} WHERE {column}=?", (related["parent_key"][column],))
    related["source"].commit()
    with pytest.raises(ValueError):
        snapshot_record(related["source"], related["profile"], related["table"], related["key"])


def test_related_original_rejects_empty_parent_owner(related_record):
    conn, scheduler, related = related_record
    _change_related_owner(related, "")
    with pytest.raises(ValueError):
        snapshot_record(related["source"], related["profile"], related["table"], related["key"])


def test_related_original_cannot_be_claimed_by_another_owner(related_record):
    conn, scheduler, related = related_record
    conn.execute("UPDATE account_execution_bindings SET owner_user_id='owner-b'")
    conn.commit()
    manifest = _related_packet(conn, related, owner="owner-b", include_parent=False)
    with pytest.raises(ValueError):
        verify_quarantine_records(conn, manifest, scheduler_connection=scheduler)


@pytest.mark.parametrize("case", ["personal", "platform"])
def test_validation_lease_rejects_different_parent_digest(database, scheduler_database, case):
    with closing(sqlite3.connect(database)) as conn, closing(sqlite3.connect(scheduler_database)) as scheduler:
        related = _seed_related_record(conn, scheduler, case)
        conn.execute(f"UPDATE {related['parent']} SET digest='other-digest' WHERE run_id='old-run'")
        conn.commit()
        with pytest.raises(ValueError):
            snapshot_record(conn, related["profile"], related["table"], related["key"])
