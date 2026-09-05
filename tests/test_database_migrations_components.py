from __future__ import annotations

from contextlib import closing
from pathlib import Path
import hashlib
import shutil
import sqlite3

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from src.database_migrations import (
    DatabaseTarget, SchemaNotCurrentError, apply_migrations, inspect_database,
    verify_restored_copy,
)


_CENTRAL_BACKUP_SHA256 = "a" * 64


def _upgrade(
    database: Path,
    revision: str,
    backup_sha256: str | None = _CENTRAL_BACKUP_SHA256,
) -> None:
    config = Config()
    config.set_main_option(
        "script_location",
        str(
            Path(__file__).parents[1]
            / "src"
            / "database_migrations"
            / "alembic"
        ),
    )
    engine = create_engine(URL.create("sqlite", database=str(database)))
    try:
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            if backup_sha256 is not None:
                config.attributes["backup_sha256"] = backup_sha256
            command.upgrade(config, revision)
            connection.commit()
    finally:
        engine.dispose()


def test_webui_0010_preserves_source_history_and_supports_recovery(tmp_path: Path) -> None:
    from src.api.store import WebUIStore

    database = tmp_path / "webui.db"
    _upgrade(database, "webui_0009")
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO semantic_workspace_tasks "
            "(task_id, user_id, title, objective_text, created_at, updated_at) "
            "VALUES ('task-old', 'owner-test', '历史任务', '保留任务目标', '2026-09-05', '2026-09-05')"
        )
        connection.execute(
            "INSERT INTO source_acquisition_attempts "
            "(attempt_id, owner_id, idempotency_key, request_hash, request_url, "
            "normalized_url, allowed_scope_json, purpose, status, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("attempt-old", "owner-test", "request-old", "a" * 64,
             "https://example.invalid/", "https://example.invalid/", "{}",
             "历史来源", "succeeded", "2026-09-05T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO source_snapshots "
            "(snapshot_id, owner_id, attempt_id, allowed_scope_json, "
            "valid_page_count, failed_page_count, created_at) "
            "VALUES ('snapshot-old', 'owner-test', 'attempt-old', '{}', 1, 0, '2026-09-05')"
        )
        connection.execute(
            "INSERT INTO source_artifacts "
            "(artifact_id, owner_id, snapshot_id, request_url, final_url, read_at, "
            "content_sha256, media_type, size_bytes, title, text_preview, content_blob) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("artifact-old", "owner-test", "snapshot-old", "https://example.invalid/",
             "https://example.invalid/", "2026-09-05", "b" * 64, "text/plain",
             7, "历史标题", "历史正文", b"history"),
        )
        history = {
            table: connection.execute(f"SELECT * FROM {table}").fetchall()
            for table in (
                "source_acquisition_attempts", "source_snapshots", "source_artifacts",
                "semantic_workspace_tasks",
            )
        }
        connection.commit()
    before = database.read_bytes()
    target = DatabaseTarget("webui", database)
    assert inspect_database(target).pending_revisions == ("webui_0010",)
    with pytest.raises(SchemaNotCurrentError):
        WebUIStore(str(database))
    assert database.read_bytes() == before

    receipt = apply_migrations(
        target, tmp_path / "before-0010.db",
        expected_source_sha256=hashlib.sha256(before).hexdigest(),
    )
    assert receipt.source_revision == "webui_0009"
    assert receipt.applied_revisions == ("webui_0010",)
    with closing(sqlite3.connect(database)) as connection:
        columns = {row[1]: row for row in connection.execute(
            "PRAGMA table_info(source_acquisition_attempts)"
        )}
        assert columns["cancel_requested_at"][2:5] == ("TEXT", 0, None)
        assert columns["request_context"][2:5] == ("TEXT", 1, "''")
        rows = connection.execute("SELECT * FROM source_acquisition_attempts").fetchall()
        assert rows == [(*row, None, "") for row in history["source_acquisition_attempts"]]
        task_columns = {row[1]: row for row in connection.execute(
            "PRAGMA table_info(semantic_workspace_tasks)"
        )}
        assert task_columns["cancel_generation"][2:5] == ("INTEGER", 1, "0")
        assert connection.execute("SELECT * FROM semantic_workspace_tasks").fetchall() == [
            (*row, 0) for row in history["semantic_workspace_tasks"]
        ]
        for table in ("source_snapshots", "source_artifacts"):
            assert connection.execute(f"SELECT * FROM {table}").fetchall() == history[table]
    inspect_database(target).require_current()

    restored = tmp_path / "restored-0009.db"
    shutil.copyfile(receipt.backup_path, restored)
    verification = verify_restored_copy(receipt.receipt_path, restored)
    assert verification.integrity_check == "ok"
    assert verification.backup_sha256 == receipt.backup_sha256
    assert inspect_database(DatabaseTarget("webui", restored)).current_revision == "webui_0009"
    with closing(sqlite3.connect(restored)) as connection:
        for table, rows in history.items():
            assert connection.execute(f"SELECT * FROM {table}").fetchall() == rows
    current = database.read_bytes()
    replay = apply_migrations(target, tmp_path / "before-replay.db")
    assert replay.applied_revisions == ()
    assert database.read_bytes() == current


def test_current_webui_installs_component_schemas_and_evidence(tmp_path: Path) -> None:
    from src.candidate_verification.repository import (
        SqliteCandidateVerificationRepository,
    )
    from src.capability_acquisition.sqlite_repository import (
        SqliteAcquisitionRepository,
    )
    from src.capability_governance.sqlite_repository import (
        SqliteCapabilityGovernanceRepository,
    )
    from src.runtime_routing.sqlite_repository import (
        SqliteRuntimeRoutingRepository,
    )

    database = tmp_path / "webui.db"
    receipt = apply_migrations(
        DatabaseTarget(profile="webui", path=database),
        tmp_path / "before-webui-0001.db",
    )

    candidate_repository = SqliteCandidateVerificationRepository(database)
    runtime_repository = SqliteRuntimeRoutingRepository(database)
    acquisition_repository = SqliteAcquisitionRepository(database)
    governance_repository = SqliteCapabilityGovernanceRepository(str(database))
    assert candidate_repository.list_requested_local() == ()
    assert runtime_repository.get_rollout().active_gate_snapshot_id == "0" * 64
    assert acquisition_repository.get("missing") is None
    assert governance_repository.list_validation_runs() == ()

    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("webui_0010",)
        candidate_rows = connection.execute(
            "SELECT migration_id, backup_sha256 "
            "FROM candidate_verification_migrations ORDER BY migration_id"
        ).fetchall()
        assert candidate_rows == [
            ("0001_candidate_verification_attempts", receipt.backup_sha256),
            ("0002_delivery_publication_idempotency", receipt.backup_sha256),
            ("0003_historical_reverification_authorities", receipt.backup_sha256),
            ("0004_legacy_candidate_rebaseline", receipt.backup_sha256),
        ]
        assert connection.execute(
            "SELECT backup_sha256 FROM runtime_routing_migrations "
            "WHERE migration_id='0001_runtime_routing'"
        ).fetchone() == (receipt.backup_sha256,)
        assert connection.execute(
            "SELECT backup_sha256 FROM capability_acquisition_migrations "
            "WHERE migration_id='0001_acquisition_runs'"
        ).fetchone() == (receipt.backup_sha256,)


def test_current_webui_resumes_known_history_without_rewriting_evidence(
    tmp_path: Path,
) -> None:
    from src.candidate_verification.repository import (
        SqliteCandidateVerificationRepository,
    )
    acquisition_ddl = (
        Path(__file__).parents[1]
        / "src"
        / "capability_acquisition"
        / "migrations"
        / "0001_acquisition_runs.sql"
    ).read_text(encoding="utf-8")
    from src.database_migrations.alembic.versions.webui_0003 import (
        _import_legacy_candidate_attempts,
        _RUNTIME_DDL,
        _RUNTIME_DDL_SHA256,
    )

    database = tmp_path / "legacy-webui.db"
    _upgrade(database, "webui_0002")
    old_candidate_sha256 = "b" * 64
    old_runtime_sha256 = "c" * 64
    old_acquisition_sha256 = "d" * 64
    report_json = (
        '{"status":"passed","summary":"历史结论","checks":[],'
        '"evidence_count":1,"formal_delivery_eligible":false}'
    )
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO agentic_runtime_runs ("
            "user_id, task_id, revision, runtime_version, permission_profile, "
            "status, run_id, candidates_json, verification_json, "
            "verified_candidate_set_hash, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "owner-a",
                "task-a",
                1,
                "legacy",
                "legacy",
                "succeeded",
                "run-a",
                '[{"artifact_id":"artifact-a","filename":"out.csv",'
                '"format":"csv","sha256":"' + "e" * 64 + '","size_bytes":1}]',
                report_json,
                "f" * 64,
                "2026-08-24T00:00:00+00:00",
                "2026-08-24T01:00:00+00:00",
            ),
        )
        candidate_sql = (
            Path(__file__).parents[1]
            / "src"
            / "candidate_verification"
            / "migrations"
            / "0001_candidate_verification_attempts.sql"
        ).read_text(encoding="utf-8")
        connection.executescript(candidate_sql)
        _import_legacy_candidate_attempts(connection)
        connection.execute(
            "INSERT INTO candidate_verification_migrations "
            "(migration_id, backup_sha256, applied_at) VALUES (?, ?, ?)",
            (
                "0001_candidate_verification_attempts",
                old_candidate_sha256,
                "2026-08-24T02:00:00+00:00",
            ),
        )
        for statement in _RUNTIME_DDL:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO runtime_rollout_state VALUES (1, ?, 0, ?, ?, ?)",
            (
                "admin_gray",
                "0" * 64,
                "legacy-migration",
                "2026-08-24T02:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO runtime_routing_migrations VALUES (?, ?, ?, ?)",
            (
                "0001_runtime_routing",
                _RUNTIME_DDL_SHA256,
                old_runtime_sha256,
                "2026-08-24T02:00:00+00:00",
            ),
        )
        connection.executescript(acquisition_ddl)
        connection.execute(
            "INSERT INTO capability_acquisition_migrations "
            "(migration_id, backup_sha256, applied_at) VALUES (?, ?, ?)",
            (
                "0001_acquisition_runs",
                old_acquisition_sha256,
                "2026-08-24T02:00:00+00:00",
            ),
        )
        governance_dir = (
            Path(__file__).parents[1]
            / "src"
            / "capability_governance"
            / "migrations"
        )
        for name in (
            "0001_capability_governance.sql",
            "0002_validation_runs.sql",
            "0003_supply_chain_evidence.sql",
        ):
            connection.executescript(
                (governance_dir / name).read_text(encoding="utf-8")
            )
        connection.commit()

    _upgrade(database, "webui_0010")

    attempts = SqliteCandidateVerificationRepository(database).list_for_candidate(
        "owner-a",
        task_id="task-a",
        revision=1,
        run_id="run-a",
        candidate_set_hash="f" * 64,
    )
    assert len(attempts) == 1
    assert attempts[0].report_json == report_json
    with closing(sqlite3.connect(database)) as connection:
        evidence = dict(
            connection.execute(
                "SELECT migration_id, backup_sha256 "
                "FROM candidate_verification_migrations"
            ).fetchall()
        )
        assert evidence["0001_candidate_verification_attempts"] == old_candidate_sha256
        assert evidence["0002_delivery_publication_idempotency"] == _CENTRAL_BACKUP_SHA256
        assert evidence["0003_historical_reverification_authorities"] == _CENTRAL_BACKUP_SHA256
        assert evidence["0004_legacy_candidate_rebaseline"] == _CENTRAL_BACKUP_SHA256
        assert connection.execute(
            "SELECT backup_sha256 FROM runtime_routing_migrations "
            "WHERE migration_id='0001_runtime_routing'"
        ).fetchone() == (old_runtime_sha256,)
        assert connection.execute(
            "SELECT backup_sha256 FROM capability_acquisition_migrations "
            "WHERE migration_id='0001_acquisition_runs'"
        ).fetchone() == (old_acquisition_sha256,)
        governance_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(capability_governance_events)"
            )
        }
        assert "event_type" in governance_columns
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='capability_platform_validation_runs'"
        ).fetchone() == (1,)


def test_webui_0003_refuses_unbound_recovery_point(tmp_path: Path) -> None:
    database = tmp_path / "unbound-webui.db"
    _upgrade(database, "webui_0002")

    with pytest.raises(
        RuntimeError,
        match="webui_0003 必须绑定中央恢复点 SHA-256",
    ):
        _upgrade(database, "webui_0003", backup_sha256=None)
