from pathlib import Path
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import time

from filelock import FileLock, Timeout
import pytest
import src.database_migrations as database_migrations
from src.database_migrations.__main__ import main as migration_main

from src.database_migrations import (
    DatabaseTarget,
    SchemaNotCurrentError,
    apply_migrations,
    inspect_database,
    plan_database,
    verify_restored_copy,
)
from src.api.store import WebUIStore
from src.scheduler.store import ScheduleStore
from src.conductor import db_writer
from src.model_connections.qualification_ledger import QualificationBatchLedger
from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.capability_catalog.sqlite_repository import SqliteCapabilityCatalogRepository
from src.conversation_steering.repository import SqliteSteeringRepository
from src.delivery_publishing.repository import DeliveryPublishingRepository
from src.model_connections.storage import ModelConnectionRepository


_LEGACY_SIMPLE_COLUMNS = (
    ("users", "role"),
    ("users", "disabled"),
    ("users", "pending"),
    ("messages", "task_id"),
    ("messages", "meta_json"),
    ("memory_hit_log", "hit"),
    ("library_dedup_scan_log", "details"),
    ("message_feedback", "status"),
    ("message_feedback", "admin_note"),
    ("data_prep_tasks", "checkpoint_json"),
    ("data_prep_tasks", "unit_id"),
    ("document_workspaces", "checked_upload_ids_json"),
    ("document_workspaces", "active_unit_id"),
    ("semantic_harness_attempts", "artifact_paths_json"),
    ("semantic_workspace_tasks", "failure_json"),
    ("semantic_workspace_tasks", "source_refs_json"),
    ("semantic_workspace_tasks", "table_output_contracts_json"),
    ("semantic_workspace_revisions", "table_output_contracts_json"),
    ("document_task_units", "archived_at"),
)

_CONCURRENT_APPLY_SCRIPT = r"""
from pathlib import Path
import json
import sys
import time

from src.database_migrations import DatabaseTarget, apply_migrations

database, backup, ready, start, result = map(Path, sys.argv[1:])
ready.write_text("ready", encoding="utf-8")
while not start.exists():
    time.sleep(0.01)
try:
    receipt = apply_migrations(DatabaseTarget("webui", database), backup)
    payload = {
        "outcome": "succeeded",
        "database_name": database.name,
        "backup_sha256": receipt.backup_sha256,
    }
except Exception as exc:
    payload = {"outcome": "failed", "error_type": type(exc).__name__}
result.write_text(json.dumps(payload), encoding="utf-8")
"""


def _drop_legacy_simple_columns(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute("DROP INDEX IF EXISTS idx_dpt_unit")
        for table, column in _LEGACY_SIMPLE_COLUMNS:
            existing = {
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if column in existing:
                connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")


def _tamper_sqlite_master(
    database: Path,
    *,
    object_name: str,
    old: str,
    new: str,
) -> None:
    """模拟已盖版本戳的数据库对象被越权改写。"""
    with sqlite3.connect(database) as connection:
        original = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name=?",
            (object_name,),
        ).fetchone()
        assert original is not None and old in str(original[0])
        replacement_name = f"{object_name}_tampered"
        replacement_sql = str(original[0]).replace(old, new).replace(
            f"CREATE TABLE {object_name}",
            f"CREATE TABLE {replacement_name}",
            1,
        )
        connection.execute(replacement_sql)
        connection.execute(f'DROP TABLE "{object_name}"')
        connection.execute(
            f'ALTER TABLE "{replacement_name}" RENAME TO "{object_name}"'
        )


def test_inspect_uninitialized_database_is_read_only(tmp_path: Path) -> None:
    database = tmp_path / "webui.db"

    status = inspect_database(DatabaseTarget(profile="webui", path=database))

    assert status.state == "uninitialized"
    assert status.current_revision is None
    assert status.target_revision == "webui_0004"
    assert status.pending_revisions == (
        "webui_0001", "webui_0002", "webui_0003", "webui_0004",
    )
    assert not database.exists()
    with pytest.raises(
        SchemaNotCurrentError,
        match=r"python -m src\.database_migrations apply --profile webui",
    ):
        status.require_current()


def test_status_cli_is_read_only_and_does_not_expose_absolute_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "private" / "webui.db"

    result = migration_main(
        ["status", "--profile", "webui", "--database", str(database)]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["database_name"] == "webui.db"
    assert payload["state"] == "uninitialized"
    assert str(tmp_path) not in json.dumps(payload)
    assert not database.exists()


def test_plan_lists_full_ordered_revision_chain_without_writing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "private" / "webui.db"

    plan = plan_database(DatabaseTarget(profile="webui", path=database))
    result = migration_main(
        ["plan", "--profile", "webui", "--database", str(database)]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert plan.pending_revisions == (
        "webui_0001", "webui_0002", "webui_0003", "webui_0004",
    )
    assert [item.revision for item in plan.revisions] == [
        "webui_0001",
        "webui_0002",
        "webui_0003",
        "webui_0004",
    ]
    assert all(len(item.content_sha256) == 64 for item in plan.revisions)
    assert all(item.requires_copy_validation for item in plan.revisions)
    assert payload["pending_revisions"] == [
        "webui_0001", "webui_0002", "webui_0003", "webui_0004",
    ]
    assert payload["revisions"][0]["revision"] == "webui_0001"
    assert str(tmp_path) not in json.dumps(payload)
    assert not database.exists()


def test_revision_manifest_freezes_every_registered_revision(
    tmp_path: Path,
) -> None:
    manifest_path = Path(database_migrations.__file__).with_name(
        "revision_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    planned: dict[str, str] = {}
    for profile in ("webui", "scheduler", "legacy_app", "qualification_ledger"):
        plan = plan_database(
            DatabaseTarget(profile=profile, path=tmp_path / f"{profile}.db")
        )
        planned.update(
            {item.revision: item.content_sha256 for item in plan.revisions}
        )

    assert planned == manifest
    assert all(len(value) == 64 for value in manifest.values())


def test_inspect_current_database_is_read_only(tmp_path: Path) -> None:
    database = tmp_path / "webui.db"
    apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "before-current.db",
    )
    before = database.read_bytes()

    status = inspect_database(DatabaseTarget(profile="webui", path=database))

    assert status.state == "current"
    assert status.current_revision == "webui_0004"
    assert status.pending_revisions == ()
    status.require_current()
    assert database.read_bytes() == before


def test_apply_empty_database_creates_backup_and_current_revision(
    tmp_path: Path,
) -> None:
    database = tmp_path / "webui.db"
    backup = tmp_path / "backups" / "webui-before.db"

    receipt = apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        backup,
    )

    assert receipt.source_revision is None
    assert receipt.target_revision == "webui_0004"
    assert receipt.applied_revisions == (
        "webui_0001", "webui_0002", "webui_0003", "webui_0004",
    )
    assert receipt.backup_path == backup.resolve()
    assert receipt.backup_sha256 == hashlib.sha256(backup.read_bytes()).hexdigest()
    assert receipt.receipt_path.is_file()
    persisted = json.loads(receipt.receipt_path.read_text(encoding="utf-8"))
    assert persisted["outcome"] == "succeeded"
    assert persisted["profile"] == "webui"
    assert persisted["database_name"] == "webui.db"
    assert persisted["backup_name"] == "webui-before.db"
    assert persisted["backup_sha256"] == receipt.backup_sha256
    assert "database_path" not in persisted
    inspect_database(DatabaseTarget(profile="webui", path=database)).require_current()


def test_apply_fails_closed_when_database_migration_lock_is_held(
    tmp_path: Path,
) -> None:
    database = tmp_path / "webui.db"
    lock_path = Path(f"{database.resolve()}.migration.lock")

    with FileLock(lock_path, timeout=0):
        with pytest.raises(Timeout):
            apply_migrations(
                DatabaseTarget(profile="webui", path=database),
                tmp_path / "locked-before.db",
            )

    assert not database.exists()
    assert not (tmp_path / "locked-before.db").exists()


def test_apply_rejects_existing_backup_even_when_it_matches_source(
    tmp_path: Path,
) -> None:
    database = tmp_path / "webui.db"
    backup = tmp_path / "occupied-before.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unrelated_data (value TEXT NOT NULL)")
        connection.execute("INSERT INTO unrelated_data VALUES ('kept')")
    shutil.copyfile(database, backup)
    before = backup.read_bytes()

    with pytest.raises(FileExistsError, match="备份已存在"):
        apply_migrations(DatabaseTarget(profile="webui", path=database), backup)

    assert backup.read_bytes() == before
    assert not backup.with_name(f"{backup.name}.receipt.json").exists()


def test_two_source_databases_cannot_race_for_one_backup_and_receipt(
    tmp_path: Path,
) -> None:
    backup = tmp_path / "shared" / "before.db"
    start = tmp_path / "start"
    processes: list[subprocess.Popen[str]] = []
    result_paths: list[Path] = []
    for index in (1, 2):
        database = tmp_path / f"source-{index}.db"
        with sqlite3.connect(database) as connection:
            connection.execute(
                "CREATE TABLE external_marker (source_id INTEGER NOT NULL, payload BLOB)"
            )
            connection.execute(
                "INSERT INTO external_marker VALUES (?, zeroblob(16777216))",
                (index,),
            )
        ready = tmp_path / f"ready-{index}"
        result = tmp_path / f"result-{index}.json"
        result_paths.append(result)
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    _CONCURRENT_APPLY_SCRIPT,
                    str(database),
                    str(backup),
                    str(ready),
                    str(start),
                    str(result),
                ],
                cwd=Path(__file__).parents[1],
                text=True,
            )
        )
    deadline = time.monotonic() + 20
    while not all((tmp_path / f"ready-{index}").exists() for index in (1, 2)):
        if time.monotonic() >= deadline:
            pytest.fail("并发迁移子进程未就绪")
        time.sleep(0.02)
    start.write_text("start", encoding="utf-8")
    for process in processes:
        assert process.wait(timeout=60) == 0

    results = [
        json.loads(path.read_text(encoding="utf-8")) for path in result_paths
    ]
    succeeded = [item for item in results if item["outcome"] == "succeeded"]
    failed = [item for item in results if item["outcome"] == "failed"]
    assert len(succeeded) == 1
    assert len(failed) == 1
    assert failed[0]["error_type"] in {"Timeout", "FileExistsError"}
    receipt_path = backup.with_name(f"{backup.name}.receipt.json")
    persisted = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert persisted["outcome"] == "succeeded"
    assert persisted["database_name"] == succeeded[0]["database_name"]
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == persisted["backup_sha256"]


def test_apply_holds_sqlite_write_lock_while_revision_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "webui.db"
    original_upgrade = database_migrations.command.upgrade

    def assert_locked_then_upgrade(config: object, revision: str) -> None:
        with sqlite3.connect(database, timeout=0) as competitor:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                competitor.execute("BEGIN IMMEDIATE")
        original_upgrade(config, revision)

    monkeypatch.setattr(
        database_migrations.command,
        "upgrade",
        assert_locked_then_upgrade,
    )

    apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "locked-during-upgrade.db",
    )


def test_verify_restored_copy_checks_sha_schema_and_readability(
    tmp_path: Path,
) -> None:
    database = tmp_path / "webui.db"
    backup = tmp_path / "before.db"
    receipt = apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        backup,
    )
    restored = tmp_path / "restored.db"
    shutil.copyfile(backup, restored)

    verification = verify_restored_copy(receipt.receipt_path, restored)

    assert verification.backup_sha256 == receipt.backup_sha256
    assert verification.verification_receipt_path.is_file()
    persisted_verification = json.loads(
        verification.verification_receipt_path.read_text(encoding="utf-8")
    )
    assert persisted_verification["restored_name"] == "restored.db"
    assert str(tmp_path) not in json.dumps(persisted_verification)
    assert verification.integrity_check == "ok"
    assert verification.foreign_key_violations == 0
    assert verification.schema_state == "uninitialized"

    restored.write_bytes(restored.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="SHA-256"):
        verify_restored_copy(receipt.receipt_path, restored)


def test_current_database_replay_is_idempotent_and_restored_copy_can_open(
    tmp_path: Path,
) -> None:
    database = tmp_path / "webui.db"
    apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "initial-before.db",
    )
    store = WebUIStore(str(database))
    user = store.create_user("restore-user", "hash", "恢复用户")
    before_replay = database.read_bytes()

    replay = apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "current-before-replay.db",
        expected_source_sha256=hashlib.sha256(before_replay).hexdigest(),
    )

    assert replay.applied_revisions == ()
    assert database.read_bytes() == before_replay
    restored = tmp_path / "restored-current.db"
    shutil.copyfile(replay.backup_path, restored)
    verification = verify_restored_copy(replay.receipt_path, restored)
    assert verification.schema_state == "current"
    restored_store = WebUIStore(str(restored))
    assert restored_store.get_user(user["user_id"])["username"] == "restore-user"


def test_apply_failure_preserves_backup_and_writes_failed_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "webui.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE users ("
            "user_id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, "
            "password_hash TEXT NOT NULL, display_name TEXT, created_at TEXT NOT NULL)"
        )
    backup = tmp_path / "failed-before.db"

    def fail_upgrade(config: object, _revision: str) -> None:
        config.attributes["connection"].exec_driver_sql(
            "CREATE TABLE must_rollback (id INTEGER PRIMARY KEY)"
        )
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(database_migrations.command, "upgrade", fail_upgrade)

    with pytest.raises(RuntimeError, match="injected migration failure"):
        apply_migrations(DatabaseTarget(profile="webui", path=database), backup)

    assert backup.is_file()
    receipt_path = backup.with_name(f"{backup.name}.receipt.json")
    failed = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert failed["outcome"] == "failed"
    assert failed["error_type"] == "RuntimeError"
    assert failed["applied_revisions"] == []
    assert failed["backup_sha256"] == hashlib.sha256(backup.read_bytes()).hexdigest()
    assert inspect_database(DatabaseTarget(profile="webui", path=database)).state != "current"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='must_rollback'"
        ).fetchone() is None


def test_apply_rejects_corrupt_source_before_backup_and_records_failure(
    tmp_path: Path,
) -> None:
    database = tmp_path / "corrupt.db"
    backup = tmp_path / "corrupt-before.db"
    database.write_bytes(b"not-a-sqlite-database")

    with pytest.raises(RuntimeError, match="源数据库不可读取"):
        apply_migrations(DatabaseTarget(profile="webui", path=database), backup)

    assert not backup.exists()
    failed = json.loads(
        backup.with_name(f"{backup.name}.receipt.json").read_text(encoding="utf-8")
    )
    assert failed["outcome"] == "failed"
    assert failed["backup_created"] is False
    assert failed["error_type"] == "DatabaseError"


def test_preflight_failure_creates_nested_receipt_parent_without_masking_error(
    tmp_path: Path,
) -> None:
    database = tmp_path / "corrupt.db"
    backup = tmp_path / "nested" / "recovery" / "corrupt-before.db"
    database.write_bytes(b"not-a-sqlite-database")

    with pytest.raises(RuntimeError, match="迁移源数据库不可读取"):
        apply_migrations(DatabaseTarget(profile="webui", path=database), backup)

    receipt_path = backup.with_name(f"{backup.name}.receipt.json")
    failed = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert failed["outcome"] == "failed"
    assert failed["error_type"] == "DatabaseError"


def test_apply_rejects_foreign_key_violations_before_creating_backup(
    tmp_path: Path,
) -> None:
    database = tmp_path / "webui.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE external_parent (id INTEGER PRIMARY KEY);
            CREATE TABLE external_child (
                id INTEGER PRIMARY KEY,
                parent_id INTEGER REFERENCES external_parent(id)
            );
            INSERT INTO external_child VALUES (1, 999);
            """
        )
    backup = tmp_path / "invalid-before.db"

    with pytest.raises(RuntimeError, match="迁移前数据库外键检查失败"):
        apply_migrations(DatabaseTarget(profile="webui", path=database), backup)

    assert not backup.exists()
    failed_receipt = backup.with_name(f"{backup.name}.receipt.json")
    failed = json.loads(failed_receipt.read_text(encoding="utf-8"))
    assert failed["outcome"] == "failed"
    assert failed["backup_created"] is False
    assert failed["backup_sha256"] is None


def test_apply_preserves_non_target_table_logical_fingerprint(
    tmp_path: Path,
) -> None:
    database = tmp_path / "webui.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE external_records (
                record_key TEXT PRIMARY KEY,
                payload BLOB,
                note TEXT
            );
            INSERT INTO external_records VALUES ('r-1', X'00FF', '不得改写');
            """
        )
        before = connection.execute(
            "SELECT record_key, hex(payload), note FROM external_records"
        ).fetchall()

    receipt = apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "fingerprint-before.db",
    )

    with sqlite3.connect(database) as connection:
        after = connection.execute(
            "SELECT record_key, hex(payload), note FROM external_records"
        ).fetchall()
    persisted = json.loads(receipt.receipt_path.read_text(encoding="utf-8"))
    assert after == before
    assert len(persisted["non_target_fingerprint_sha256"]) == 64


def test_scheduler_store_requires_and_uses_explicit_migration(
    tmp_path: Path,
) -> None:
    database = tmp_path / "scheduler.db"
    with pytest.raises(SchemaNotCurrentError):
        ScheduleStore(str(database))
    assert not database.exists()

    apply_migrations(
        DatabaseTarget(profile="scheduler", path=database),
        tmp_path / "scheduler-before.db",
    )
    before_constructor = database.read_bytes()

    store = ScheduleStore(str(database))

    assert database.read_bytes() == before_constructor
    task_id = store.add(
        user_input="每天汇总",
        provider=None,
        model=None,
        trigger_type="interval",
        cron_expr=None,
        run_at=None,
        next_run_at=None,
        interval_seconds=3600,
    )
    assert store.get(task_id)["user_input"] == "每天汇总"


def test_legacy_app_writer_requires_explicit_sqlite_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "app.db"
    monkeypatch.setattr(db_writer, "_DB_PATH", database)
    monkeypatch.setattr(db_writer.settings, "db_backend", "sqlite")
    row = {
        "url": "https://example.test/item",
        "title": "标题",
        "content": "正文",
        "metadata": {"source": "test"},
    }

    with pytest.raises(SchemaNotCurrentError):
        db_writer.write_items("task-1", [row], "fixture")
    assert not database.exists()

    apply_migrations(
        DatabaseTarget(profile="legacy_app", path=database),
        tmp_path / "app-before.db",
    )
    assert db_writer.write_items("task-1", [row], "fixture") == 1
    with sqlite3.connect(database) as connection:
        stored = connection.execute(
            "SELECT task_id, title, metadata FROM collected_items"
        ).fetchone()
    assert stored == ("task-1", "标题", '{"source": "test"}')


def test_qualification_ledger_requires_explicit_migration(
    tmp_path: Path,
) -> None:
    database = tmp_path / "qualification.db"
    with pytest.raises(SchemaNotCurrentError):
        QualificationBatchLedger(database)
    assert not database.exists()

    apply_migrations(
        DatabaseTarget(profile="qualification_ledger", path=database),
        tmp_path / "qualification-before.db",
    )
    before_constructor = database.read_bytes()

    ledger = QualificationBatchLedger(database)

    assert database.read_bytes() == before_constructor
    assert ledger.identity()["schema_version"] == "g4-qualification-ledger-v1"


def test_webui_repository_uses_explicitly_migrated_schema_without_startup_ddl(
    tmp_path: Path,
) -> None:
    database = tmp_path / "webui.db"
    apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "webui-before.db",
    )
    before_constructor = database.read_bytes()

    store = WebUIStore(str(database))

    assert database.read_bytes() == before_constructor
    created = store.create_user("alice", "hash", "Alice")
    assert store.get_user(created["user_id"])["username"] == "alice"


def test_webui_connections_enforce_foreign_keys_and_busy_timeout(
    tmp_path: Path,
) -> None:
    database = tmp_path / "webui.db"
    apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "webui-pragmas-before.db",
    )
    store = WebUIStore(str(database))

    with store._conn() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal"
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO document_task_unit_members "
                "(unit_id, upload_id, ordinal, added_at) VALUES (?, ?, 0, ?)",
                ("missing-unit", "upload-1", "2026-01-01T00:00:00"),
            )
    assert not Path(f"{database}-wal").exists()
    assert not Path(f"{database}-shm").exists()


def test_shared_webui_repositories_only_validate_explicit_schema(
    tmp_path: Path,
) -> None:
    database = tmp_path / "webui.db"
    apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "shared-webui-before.db",
    )
    before = database.read_bytes()

    AgenticRuntimeRepository(database)
    ModelConnectionRepository(str(database))
    SqliteSteeringRepository(str(database))
    DeliveryPublishingRepository(database)
    SqliteCapabilityCatalogRepository(str(database))

    assert database.read_bytes() == before


def test_apply_known_legacy_webui_preserves_user_and_adds_current_rbac(
    tmp_path: Path,
) -> None:
    database = tmp_path / "webui.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name TEXT,
                created_at TEXT NOT NULL
            );
            INSERT INTO users VALUES (
                'u_legacy', 'legacy', 'hash', 'Legacy', '2026-01-01T00:00:00'
            );
            """
        )

    apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "webui-before.db",
    )

    migrated = WebUIStore(str(database)).get_user("u_legacy")
    assert migrated is not None
    assert migrated["username"] == "legacy"
    assert migrated["role"] == "super_admin"
    assert migrated["disabled"] == 0
    assert migrated["pending"] == 0


def test_apply_known_webui_accepts_authorized_vnext_default_rollout(
    tmp_path: Path,
) -> None:
    """ADR-0030 的生产终态必须能被中央迁移接管且不得改写。"""
    database = tmp_path / "webui.db"
    apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "initial-before.db",
    )
    snapshot_id = "a" * 64
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE runtime_rollout_state SET mode=?, p0_blocked=0, "
            "active_gate_snapshot_id=? WHERE state_id=1",
            ("vnext_default", snapshot_id),
        )
        connection.execute("DELETE FROM alembic_version")

    receipt = apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "vnext-default-before.db",
    )

    assert receipt.applied_revisions == (
        "webui_0001", "webui_0002", "webui_0003", "webui_0004",
    )
    with sqlite3.connect(database) as connection:
        state = connection.execute(
            "SELECT mode, p0_blocked, active_gate_snapshot_id "
            "FROM runtime_rollout_state WHERE state_id=1"
        ).fetchone()
    assert state == ("vnext_default", 0, snapshot_id)


@pytest.mark.parametrize("invalid_mode", ["gray", "all", "totally_invalid"])
def test_apply_known_webui_rejects_invalid_rollout_modes_without_rewriting_state(
    tmp_path: Path,
    invalid_mode: str,
) -> None:
    """历史迁移只接管权威枚举，旧误值和未知值必须失败关闭。"""
    database = tmp_path / "webui.db"
    apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "initial-before.db",
    )
    snapshot_id = "b" * 64
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE runtime_rollout_state SET mode=?, p0_blocked=1, "
            "active_gate_snapshot_id=? WHERE state_id=1",
            (invalid_mode, snapshot_id),
        )
        connection.execute("DELETE FROM alembic_version")

    backup = tmp_path / f"{invalid_mode}-before.db"
    with pytest.raises(RuntimeError, match="RuntimeRouting 历史迁移证据无效"):
        apply_migrations(DatabaseTarget(profile="webui", path=database), backup)

    failed = json.loads(
        backup.with_name(f"{backup.name}.receipt.json").read_text(encoding="utf-8")
    )
    assert failed["outcome"] == "failed"
    assert failed["applied_revisions"] == []
    with sqlite3.connect(database) as connection:
        state = connection.execute(
            "SELECT mode, p0_blocked, active_gate_snapshot_id "
            "FROM runtime_rollout_state WHERE state_id=1"
        ).fetchone()
    assert state == (invalid_mode, 1, snapshot_id)


def test_apply_known_legacy_webui_adds_message_metadata_columns(
    tmp_path: Path,
) -> None:
    database = tmp_path / "webui.db"
    apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "empty-before.db",
    )
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM alembic_version")
        connection.execute("ALTER TABLE messages DROP COLUMN task_id")
        connection.execute("ALTER TABLE messages DROP COLUMN meta_json")

    apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "legacy-before.db",
    )

    store = WebUIStore(str(database))
    user = store.create_user("alice", "hash")
    conversation = store.create_conversation(user["user_id"], "测试")
    message_id = store.add_message(
        conversation["conv_id"],
        "user",
        "内容",
        task_id="task-1",
        meta={"source": "test"},
    )
    message = next(
        item
        for item in store.list_messages(conversation["conv_id"])
        if item["id"] == message_id
    )
    assert message["task_id"] == "task-1"
    assert message["meta"] == {"source": "test"}


def test_inspect_current_revision_fails_closed_on_schema_drift(
    tmp_path: Path,
) -> None:
    database = tmp_path / "webui.db"
    apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "webui-before.db",
    )
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE memory_hit_log DROP COLUMN hit")
    before = database.read_bytes()

    status = inspect_database(DatabaseTarget(profile="webui", path=database))

    assert status.state == "drift"
    assert status.current_revision == "webui_0004"
    assert status.gaps == ("column:memory_hit_log.hit",)
    with pytest.raises(SchemaNotCurrentError, match="Schema"):
        status.require_current()
    assert database.read_bytes() == before


@pytest.mark.parametrize(
    ("profile", "object_name"),
    (
        ("webui", "idx_dpt_unit"),
        ("scheduler", "idx_runs_task"),
        ("qualification_ledger", "uq_g4_active_provider_set"),
    ),
)
def test_current_revision_requires_every_owned_named_schema_object(
    tmp_path: Path,
    profile: str,
    object_name: str,
) -> None:
    database = tmp_path / f"{profile}.db"
    apply_migrations(
        DatabaseTarget(profile=profile, path=database),
        tmp_path / f"{profile}-before.db",
    )
    with sqlite3.connect(database) as connection:
        connection.execute(f'DROP INDEX "{object_name}"')

    status = inspect_database(DatabaseTarget(profile=profile, path=database))

    assert status.state == "drift"
    assert f"object:{object_name}" in status.gaps


def test_current_revision_requires_complete_legacy_app_table_definition(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy-app.db"
    apply_migrations(
        DatabaseTarget(profile="legacy_app", path=database),
        tmp_path / "legacy-app-before.db",
    )
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE collected_items ADD COLUMN injected TEXT")

    status = inspect_database(DatabaseTarget(profile="legacy_app", path=database))

    assert status.state == "drift"
    assert "object:collected_items" in status.gaps


@pytest.mark.parametrize(
    ("old", "new"),
    (
        ("INTEGER PRIMARY KEY AUTOINCREMENT", "INTEGER PRIMARY KEY"),
        ("source TEXT", "source TEXT COLLATE NOCASE"),
    ),
)
def test_current_revision_freezes_complete_column_definition_semantics(
    tmp_path: Path,
    old: str,
    new: str,
) -> None:
    database = tmp_path / (hashlib.sha256(new.encode("utf-8")).hexdigest() + ".db")
    apply_migrations(
        DatabaseTarget(profile="legacy_app", path=database),
        tmp_path / (database.stem + "-before.db"),
    )
    _tamper_sqlite_master(
        database,
        object_name="collected_items",
        old=old,
        new=new,
    )

    status = inspect_database(DatabaseTarget(profile="legacy_app", path=database))

    assert status.state == "drift"
    assert "object:collected_items" in status.gaps


def test_table_contract_distinguishes_generated_column_expressions() -> None:
    first = sqlite3.connect(":memory:")
    second = sqlite3.connect(":memory:")
    try:
        first.execute(
            "CREATE TABLE generated_values ("
            "base INTEGER, derived INTEGER GENERATED ALWAYS AS (base + 1) VIRTUAL)"
        )
        second.execute(
            "CREATE TABLE generated_values ("
            "base INTEGER, derived INTEGER GENERATED ALWAYS AS (base + 2) VIRTUAL)"
        )

        assert database_migrations._table_contract_sha256(
            first,
            "generated_values",
        ) != database_migrations._table_contract_sha256(second, "generated_values")
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize(
    ("object_name", "old", "new"),
    (
        (
            "qualification_ledger_metadata",
            "ledger_id TEXT NOT NULL UNIQUE",
            "ledger_id TEXT NOT NULL",
        ),
        (
            "qualification_ledger_metadata",
            "revision INTEGER NOT NULL CHECK (revision >= 0)",
            "revision INTEGER NOT NULL",
        ),
        (
            "qualification_batches",
            "parent_batch_id TEXT REFERENCES qualification_batches(batch_id)",
            "parent_batch_id TEXT",
        ),
    ),
)
def test_current_revision_detects_unique_check_and_foreign_key_drift(
    tmp_path: Path,
    object_name: str,
    old: str,
    new: str,
) -> None:
    suffix = hashlib.sha256(old.encode("utf-8")).hexdigest()[:8]
    database = tmp_path / f"qualification-{suffix}.db"
    apply_migrations(
        DatabaseTarget(profile="qualification_ledger", path=database),
        tmp_path / f"qualification-{suffix}-before.db",
    )
    _tamper_sqlite_master(
        database,
        object_name=object_name,
        old=old,
        new=new,
    )

    status = inspect_database(
        DatabaseTarget(profile="qualification_ledger", path=database)
    )

    assert status.state == "drift"
    assert f"object:{object_name}" in status.gaps


def test_current_revision_allows_extra_unmanaged_table(tmp_path: Path) -> None:
    database = tmp_path / "scheduler.db"
    apply_migrations(
        DatabaseTarget(profile="scheduler", path=database),
        tmp_path / "scheduler-before.db",
    )
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE external_extension (value TEXT)")

    status = inspect_database(DatabaseTarget(profile="scheduler", path=database))

    assert status.state == "current"


def test_apply_rejects_current_schema_drift_before_creating_backup(
    tmp_path: Path,
) -> None:
    database = tmp_path / "webui.db"
    apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "initial-before.db",
    )
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE capability_acquisition_runs")
    backup = tmp_path / "drift-before.db"

    with pytest.raises(SchemaNotCurrentError, match="Schema 漂移"):
        apply_migrations(DatabaseTarget(profile="webui", path=database), backup)

    assert not backup.exists()
    failed = json.loads(
        backup.with_name(f"{backup.name}.receipt.json").read_text(encoding="utf-8")
    )
    assert failed["outcome"] == "failed"
    assert failed["backup_created"] is False


def test_inspect_rejects_multiple_or_future_revision_rows(
    tmp_path: Path,
) -> None:
    database = tmp_path / "webui.db"
    apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "webui-before.db",
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO alembic_version(version_num) VALUES ('webui_9999')"
        )

    status = inspect_database(DatabaseTarget(profile="webui", path=database))

    assert status.state == "unknown"
    assert status.current_revision is None
    with pytest.raises(SchemaNotCurrentError):
        status.require_current()


def test_apply_checks_expected_source_sha256_before_backup(
    tmp_path: Path,
) -> None:
    database = tmp_path / "webui.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE users ("
            "user_id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, "
            "password_hash TEXT NOT NULL, display_name TEXT, created_at TEXT NOT NULL)"
        )

    with pytest.raises(ValueError, match="源数据库 SHA-256"):
        apply_migrations(
            DatabaseTarget(profile="webui", path=database),
            tmp_path / "wrong-source-before.db",
            expected_source_sha256="0" * 64,
        )

    assert not (tmp_path / "wrong-source-before.db").exists()


def test_inspect_reports_all_frozen_legacy_column_gaps(tmp_path: Path) -> None:
    database = tmp_path / "webui.db"
    apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "webui-before.db",
    )
    _drop_legacy_simple_columns(database)

    status = inspect_database(DatabaseTarget(profile="webui", path=database))

    assert status.state == "drift"
    assert status.gaps == tuple(
        sorted(
            [
                *(f"column:{table}.{column}" for table, column in _LEGACY_SIMPLE_COLUMNS),
                "object:idx_dpt_unit",
            ]
        )
    )


def test_apply_known_legacy_webui_repairs_all_frozen_column_gaps(
    tmp_path: Path,
) -> None:
    database = tmp_path / "webui.db"
    apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "empty-before.db",
    )
    _drop_legacy_simple_columns(database)
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM alembic_version")

    apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "legacy-before.db",
    )

    status = inspect_database(DatabaseTarget(profile="webui", path=database))
    assert status.state == "current"
    assert status.gaps == ()


def test_apply_known_legacy_webui_backfills_document_task_unit(
    tmp_path: Path,
) -> None:
    database = tmp_path / "webui.db"
    apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "empty-before.db",
    )
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM alembic_version")
        connection.execute("DELETE FROM document_task_unit_members")
        connection.execute("DELETE FROM document_task_units")
        connection.execute(
            "INSERT INTO data_prep_tasks "
            "(task_id, user_id, unit_id, spec_json, status, created_at, updated_at) "
            "VALUES (?, ?, NULL, ?, 'SUCCEEDED', ?, ?)",
            (
                "task-legacy",
                "u_legacy",
                '{"task_type":"document_extraction","upload_ids":["upload-1"]}',
                "2026-01-01T00:00:00",
                "2026-01-01T00:01:00",
            ),
        )

    apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "legacy-before.db",
    )

    store = WebUIStore(str(database))
    task = store.get_data_prep_task("task-legacy")
    assert task is not None
    assert task["unit_id"].startswith("du_")
    assert task["spec"]["unit_id"] == task["unit_id"]
    assert store.list_document_units("u_legacy") == [
        {
            "unit_id": task["unit_id"],
            "user_id": "u_legacy",
            "unit_type": "single_file",
            "name": "upload-1",
            "business_type": "",
            "archived_at": None,
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:01:00",
            "upload_ids": ["upload-1"],
        }
    ]
