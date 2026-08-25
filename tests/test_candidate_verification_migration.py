# -*- coding: utf-8 -*-
"""CandidateVerification 显式迁移的公共行为。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import sqlite3

import pytest

from src.candidate_verification import (
    AttemptReason,
    AttemptStatus,
    SqliteCandidateVerificationRepository,
    VerificationAttempt,
    migrate_candidate_verification,
)


def _versioned_requested_attempt() -> VerificationAttempt:
    return VerificationAttempt(
        attempt_id="attempt-1",
        owner_id="owner-a",
        task_id="task-a",
        revision=1,
        run_id="run-a",
        reason_code=AttemptReason.RULESET_CHANGED,
        candidate_set_hash="1" * 64,
        manifest_hash="2" * 64,
        goal_contract_hash="3" * 64,
        delivery_spec_hash="4" * 64,
        verifier_ruleset_hash="5" * 64,
        verifier_code_commit="6" * 40,
        verifier_source_hash="7" * 64,
        verifier_execution_identity_hash="8" * 64,
        verifier_ruleset_manifest_json=(
            '{"schema_version":1,"verifier_ruleset_hash":"'
            + "5" * 64
            + '"}'
        ),
        actor_id="owner-a",
        idempotency_key="idem-1",
        request_hash="9" * 64,
        status=AttemptStatus.REQUESTED,
        created_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )


def test_explicit_migration_preserves_recovery_point_before_installing_ledger(
    tmp_path,
) -> None:
    database = tmp_path / "production.db"
    backup = tmp_path / "production-before-candidate-verification.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE existing_data (value TEXT NOT NULL)")
        connection.execute("INSERT INTO existing_data VALUES ('kept')")

    migrated_backup = migrate_candidate_verification(database, backup)

    assert migrated_backup == backup.resolve()
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT value FROM existing_data").fetchone() == (
            "kept",
        )
        assert connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='candidate_verification_attempts'"
        ).fetchone() is None
    SqliteCandidateVerificationRepository(database)


@pytest.mark.parametrize("legacy_status", ["passed", "failed", "inconclusive"])
def test_migration_imports_legacy_report_without_guessing_ruleset_identity(
    tmp_path,
    legacy_status,
) -> None:
    database = tmp_path / "legacy.db"
    backup = tmp_path / "legacy-before-candidate-verification.db"
    report_json = (
        '{"status":"' + legacy_status + '", "summary":"旧规则结论", "checks":[], '
        '"evidence_count":0, "formal_delivery_eligible":false}'
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE agentic_runtime_runs ("
            "user_id TEXT NOT NULL, task_id TEXT NOT NULL, revision INTEGER NOT NULL, "
            "run_id TEXT, candidates_json TEXT NOT NULL, verification_json TEXT, "
            "verified_candidate_set_hash TEXT, model_connection_id TEXT, "
            "model_connection_version TEXT, model_connection_model TEXT, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO agentic_runtime_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "owner-a",
                "task-a",
                1,
                "run-a",
                '[{"artifact_id":"a","filename":"out.csv","format":"csv",'
                '"sha256":"' + "b" * 64 + '","size_bytes":1}]',
                report_json,
                "c" * 64,
                None,
                None,
                None,
                "2026-08-24T00:00:00+00:00",
                "2026-08-24T01:00:00+00:00",
            ),
        )

    migrate_candidate_verification(database, backup)
    attempts = SqliteCandidateVerificationRepository(database).list_for_candidate(
        "owner-a",
        task_id="task-a",
        revision=1,
        run_id="run-a",
        candidate_set_hash="c" * 64,
    )

    assert len(attempts) == 1
    assert attempts[0].report_json == report_json
    assert attempts[0].status is AttemptStatus(legacy_status)
    assert attempts[0].ruleset_identity_status.value == "legacy_unversioned"
    assert attempts[0].verifier_ruleset_hash is None
    assert attempts[0].manifest_hash is None


def test_migration_does_not_invent_attempt_for_empty_legacy_report(tmp_path) -> None:
    database = tmp_path / "legacy-empty.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE agentic_runtime_runs ("
            "user_id TEXT NOT NULL, task_id TEXT NOT NULL, revision INTEGER NOT NULL, "
            "run_id TEXT, candidates_json TEXT NOT NULL, verification_json TEXT, "
            "verified_candidate_set_hash TEXT, model_connection_id TEXT, "
            "model_connection_version TEXT, model_connection_model TEXT, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO agentic_runtime_runs VALUES "
            "('owner-a', 'task-a', 1, 'run-a', '[]', NULL, NULL, "
            "NULL, NULL, NULL, '2026-08-24T00:00:00+00:00', "
            "'2026-08-24T01:00:00+00:00')"
        )

    migrate_candidate_verification(database, tmp_path / "before.db")
    attempts = SqliteCandidateVerificationRepository(database).list_for_candidate(
        "owner-a",
        task_id="task-a",
        revision=1,
        run_id="run-a",
        candidate_set_hash="0" * 64,
    )

    assert attempts == ()


def test_migration_rebuilds_malformed_legacy_candidate_set_hash(tmp_path) -> None:
    database = tmp_path / "legacy-malformed-hash.db"
    report_json = (
        '{"status":"passed","summary":"旧规则结论","checks":[],'
        '"evidence_count":0,"formal_delivery_eligible":false}'
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE agentic_runtime_runs ("
            "user_id TEXT NOT NULL, task_id TEXT NOT NULL, revision INTEGER NOT NULL, "
            "run_id TEXT, candidates_json TEXT NOT NULL, verification_json TEXT, "
            "verified_candidate_set_hash TEXT, model_connection_id TEXT, "
            "model_connection_version TEXT, model_connection_model TEXT, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO agentic_runtime_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "owner-a",
                "task-a",
                1,
                "run-a",
                '[{"artifact_id":"a","filename":"out.csv","format":"csv",'
                '"sha256":"' + "b" * 64 + '","size_bytes":1}]',
                report_json,
                "z" * 64,
                None,
                None,
                None,
                "2026-08-24T00:00:00+00:00",
                "2026-08-24T01:00:00+00:00",
            ),
        )

    migrate_candidate_verification(database, tmp_path / "before.db")
    attempts = SqliteCandidateVerificationRepository(database).list_for_candidate(
        "owner-a",
        task_id="task-a",
        revision=1,
        run_id="run-a",
        candidate_set_hash=(
            "d61b1e6abbceae9d053371e95b8d85e22c2566b15b3fb00cdae7677a1933980f"
        ),
    )

    assert len(attempts) == 1


def test_migration_replay_preserves_first_recovery_point(tmp_path) -> None:
    database = tmp_path / "production.db"
    backup = tmp_path / "production-before-candidate-verification.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE existing_data (value TEXT NOT NULL)")
        connection.commit()
        connection.execute("INSERT INTO existing_data VALUES ('kept')")

    first = migrate_candidate_verification(database, backup)
    backup_before_replay = backup.read_bytes()
    second = migrate_candidate_verification(database, backup)

    assert first == second == backup.resolve()
    assert backup.read_bytes() == backup_before_replay


def test_migration_upgrades_existing_0001_with_separate_recovery_point(
    tmp_path,
) -> None:
    database = tmp_path / "production.db"
    first_backup = tmp_path / "before-0001.db"
    publication_backup = tmp_path / "before-0002.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE delivery_publish_intents (
                publication_key TEXT PRIMARY KEY,
                command_hash TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                task_revision INTEGER NOT NULL,
                run_id TEXT NOT NULL,
                status TEXT NOT NULL,
                commit_token TEXT,
                staging_dir TEXT,
                final_dir TEXT,
                delivery_id TEXT,
                manifest_json TEXT,
                error_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO delivery_publish_intents VALUES (
                'legacy-key', 'legacy-command', 'owner-a', 'task-a', 1,
                'run-a', 'failed', NULL, NULL, NULL, NULL, NULL, NULL,
                '2026-08-24T12:00:00+00:00',
                '2026-08-24T12:00:00+00:00'
            );
            """
        )
    migrate_candidate_verification(database, first_backup)
    with sqlite3.connect(database) as connection:
        # 模拟已执行旧版 0001、但尚未发布 CV-07 迁移的合法阶段状态。
        connection.execute(
            "DELETE FROM candidate_verification_migrations "
            "WHERE migration_id='0002_delivery_publication_idempotency'"
        )
        connection.execute("DROP INDEX idx_dpi_owner_request_idempotency")
        connection.execute(
            "ALTER TABLE delivery_publish_intents "
            "DROP COLUMN request_idempotency_hash"
        )

    migrated = migrate_candidate_verification(database, publication_backup)
    backup_before_replay = publication_backup.read_bytes()
    replayed = migrate_candidate_verification(database, publication_backup)

    assert migrated == replayed == publication_backup.resolve()
    assert publication_backup.read_bytes() == backup_before_replay
    with sqlite3.connect(database) as connection:
        migrations = {
            row[0]
            for row in connection.execute(
                "SELECT migration_id FROM candidate_verification_migrations"
            )
        }
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(delivery_publish_intents)"
            )
        }
        indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(delivery_publish_intents)"
            )
        }
        legacy = connection.execute(
            "SELECT publication_key, request_idempotency_hash "
            "FROM delivery_publish_intents"
        ).fetchone()
    assert migrations == {
        "0001_candidate_verification_attempts",
        "0002_delivery_publication_idempotency",
    }
    assert "request_idempotency_hash" in columns
    assert "idx_dpi_owner_request_idempotency" in indexes
    assert legacy == ("legacy-key", None)


def test_migration_replay_releases_database_file_handle(tmp_path) -> None:
    database = tmp_path / "production.db"
    backup = tmp_path / "before.db"
    moved = tmp_path / "moved.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE existing_data (value TEXT NOT NULL)")
        connection.commit()

    migrate_candidate_verification(database, backup)
    migrate_candidate_verification(database, backup)

    database.replace(moved)
    assert moved.is_file()


def test_migration_resumes_after_backup_completed_before_schema(tmp_path) -> None:
    database = tmp_path / "production.db"
    backup = tmp_path / "production-before-candidate-verification.db"
    with sqlite3.connect(database) as source:
        source.execute("CREATE TABLE existing_data (value TEXT NOT NULL)")
        source.execute("INSERT INTO existing_data VALUES ('kept')")
        source.commit()
        with sqlite3.connect(backup) as destination:
            source.backup(destination)
    backup_before_resume = backup.read_bytes()

    resumed = migrate_candidate_verification(database, backup)

    assert resumed == backup.resolve()
    assert backup.read_bytes() == backup_before_resume
    SqliteCandidateVerificationRepository(database)


def test_migration_rejects_corrupt_source_without_creating_backup(tmp_path) -> None:
    database = tmp_path / "corrupt.db"
    backup = tmp_path / "corrupt-before-candidate-verification.db"
    database.write_bytes(b"not-a-sqlite-database")

    with pytest.raises(RuntimeError, match="源数据库完整性检查失败"):
        migrate_candidate_verification(database, backup)

    assert not backup.exists()


def test_migration_rejects_unrelated_existing_backup(tmp_path) -> None:
    database = tmp_path / "production.db"
    backup = tmp_path / "occupied.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE source_data (value TEXT NOT NULL)")
        connection.execute("INSERT INTO source_data VALUES ('source')")
    with sqlite3.connect(backup) as connection:
        connection.execute("CREATE TABLE other_data (value TEXT NOT NULL)")
        connection.execute("INSERT INTO other_data VALUES ('other')")
    backup_before = backup.read_bytes()

    with pytest.raises(RuntimeError, match="恢复点与源数据库不一致"):
        migrate_candidate_verification(database, backup)

    assert backup.read_bytes() == backup_before


def test_migration_replay_rejects_modified_first_backup(tmp_path) -> None:
    database = tmp_path / "production.db"
    backup = tmp_path / "before.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE existing_data (value TEXT NOT NULL)")
    migrate_candidate_verification(database, backup)
    with sqlite3.connect(backup) as connection:
        connection.execute("INSERT INTO existing_data VALUES ('tampered')")

    with pytest.raises(RuntimeError, match="首次恢复点不匹配"):
        migrate_candidate_verification(database, backup)


def test_concurrent_migrations_share_one_recovery_point(tmp_path) -> None:
    database = tmp_path / "production.db"
    backup = tmp_path / "before.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE existing_data (value TEXT NOT NULL)")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(
                lambda _index: migrate_candidate_verification(database, backup),
                range(2),
            )
        )

    assert results == (backup.resolve(), backup.resolve())
    SqliteCandidateVerificationRepository(database)


def test_database_rejects_terminal_update_and_all_attempt_deletes(tmp_path) -> None:
    database = tmp_path / "ledger.db"
    sqlite3.connect(database).close()
    migrate_candidate_verification(database, tmp_path / "before.db")
    repository = SqliteCandidateVerificationRepository(database)
    repository.create(_versioned_requested_attempt())
    repository.start(
        "owner-a",
        "attempt-1",
        started_at=datetime(2026, 8, 24, 1, tzinfo=timezone.utc),
    )
    repository.finish(
        "owner-a",
        "attempt-1",
        status=AttemptStatus.PASSED,
        report_json='{"status":"passed"}',
        report_hash="76f1805001bc4f155c8efa5651c57c4f1858af1f6786cbe4596214a4b64375a6",
        finished_at=datetime(2026, 8, 24, 2, tzinfo=timezone.utc),
    )

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="终态不可改写"):
            connection.execute(
                "UPDATE candidate_verification_attempts "
                "SET status='running' WHERE attempt_id='attempt-1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="不可删除"):
            connection.execute(
                "DELETE FROM candidate_verification_attempts "
                "WHERE attempt_id='attempt-1'"
            )


def test_database_rejects_skipping_running_state(tmp_path) -> None:
    database = tmp_path / "ledger.db"
    sqlite3.connect(database).close()
    migrate_candidate_verification(database, tmp_path / "before.db")
    repository = SqliteCandidateVerificationRepository(database)
    repository.create(_versioned_requested_attempt())

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="非法状态转换"):
            connection.execute(
                "UPDATE candidate_verification_attempts SET "
                "status='passed', report_json='{}', report_hash=?, finished_at=? "
                "WHERE attempt_id='attempt-1'",
                ("a" * 64, datetime(2026, 8, 24, 1, tzinfo=timezone.utc).isoformat()),
            )


def test_database_rejects_report_before_terminal_state(tmp_path) -> None:
    database = tmp_path / "ledger.db"
    sqlite3.connect(database).close()
    migrate_candidate_verification(database, tmp_path / "before.db")
    repository = SqliteCandidateVerificationRepository(database)
    requested = _versioned_requested_attempt()
    repository.create(requested)

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="状态字段不一致"):
            connection.execute(
                "UPDATE candidate_verification_attempts SET "
                "status='running', started_at=?, report_json='{}' "
                "WHERE attempt_id='attempt-1'",
                (datetime(2026, 8, 24, 1, tzinfo=timezone.utc).isoformat(),),
            )


def test_database_rejects_identity_change_during_legal_transition(tmp_path) -> None:
    database = tmp_path / "ledger.db"
    sqlite3.connect(database).close()
    migrate_candidate_verification(database, tmp_path / "before.db")
    SqliteCandidateVerificationRepository(database).create(
        _versioned_requested_attempt()
    )

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="冻结身份不可改写"):
            connection.execute(
                "UPDATE candidate_verification_attempts "
                "SET status='running', started_at=?, owner_id='owner-b' "
                "WHERE attempt_id='attempt-1'",
                (datetime(2026, 8, 24, 1, tzinfo=timezone.utc).isoformat(),),
            )
