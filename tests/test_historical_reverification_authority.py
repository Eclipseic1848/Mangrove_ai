# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from src.agentic_runtime.models import (
    PermissionProfile,
    RuntimeTaskConfig,
    RuntimeVersion,
)
from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.candidate_verification import (
    HistoricalReverificationAuthority,
    HistoricalReverificationEvidence,
    HistoricalReverificationPurpose,
    SqliteCandidateVerificationRepository,
    migrate_candidate_verification,
)


def _evidence() -> HistoricalReverificationEvidence:
    return HistoricalReverificationEvidence(
        owner_id="owner-a",
        task_id="task-old",
        revision=1,
        run_id="pi-run-old",
        purpose=(
            HistoricalReverificationPurpose.SEMANTIC_INCONCLUSIVE_REVERIFICATION
        ),
        legacy_runtime_created_at=datetime(
            2026,
            8,
            17,
            5,
            43,
            tzinfo=timezone.utc,
        ),
        runtime_routing_migration_id="0001_runtime_routing",
        runtime_routing_applied_at=datetime(
            2026,
            8,
            22,
            20,
            39,
            tzinfo=timezone.utc,
        ),
        runtime_routing_backup_sha256="1" * 64,
        runtime_request_hash="2" * 64,
        task_revision_hash="3" * 64,
        source_binding_hash="4" * 64,
        runtime_event_chain_hash="5" * 64,
        candidate_set_hash="6" * 64,
        candidate_manifest_hash="7" * 64,
        goal_contract_hash="8" * 64,
        delivery_spec_hash="9" * 64,
        previous_attempt_id="legacy-attempt-old",
        previous_report_hash="a" * 64,
        connection_id="connection-deepseek",
        connection_version="version-1",
        model_id="deepseek-v4-flash",
    )


def _seed_runtime_boundary(
    database,
    *,
    created_at: datetime | None = None,
    with_assignment: bool = False,
) -> None:
    evidence = _evidence()
    AgenticRuntimeRepository(database).register(
        RuntimeTaskConfig(
            user_id=evidence.owner_id,
            task_id=evidence.task_id,
            revision=evidence.revision,
            runtime_version=RuntimeVersion.PI,
            permission_profile=PermissionProfile.STANDARD,
        )
    )
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE runtime_routing_migrations (
                migration_id TEXT PRIMARY KEY,
                backup_sha256 TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE runtime_assignments (
                owner_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (owner_id, task_id, revision)
            );
            """
        )
        connection.execute(
            "UPDATE agentic_runtime_runs SET run_id=?, created_at=? "
            "WHERE user_id=? AND task_id=? AND revision=?",
            (
                evidence.run_id,
                (created_at or evidence.legacy_runtime_created_at).isoformat(),
                evidence.owner_id,
                evidence.task_id,
                evidence.revision,
            ),
        )
        connection.execute(
            "INSERT INTO runtime_routing_migrations VALUES (?, ?, ?)",
            (
                evidence.runtime_routing_migration_id,
                evidence.runtime_routing_backup_sha256,
                evidence.runtime_routing_applied_at.isoformat(),
            ),
        )
        if with_assignment:
            connection.execute(
                "INSERT INTO runtime_assignments VALUES (?, ?, ?, ?)",
                (
                    evidence.owner_id,
                    evidence.task_id,
                    evidence.revision,
                    "{}",
                ),
            )


def test_historical_authority_builds_stable_content_bound_identity() -> None:
    evidence = _evidence()
    recorded_at = datetime(2026, 8, 25, 8, tzinfo=timezone.utc)

    first = HistoricalReverificationAuthority.build(
        evidence=evidence,
        actor_id="owner-a",
        idempotency_key="recover-old-candidate",
        recorded_at=recorded_at,
    )
    replayed = HistoricalReverificationAuthority.build(
        evidence=evidence,
        actor_id="owner-a",
        idempotency_key="another-transport-retry",
        recorded_at=recorded_at,
    )

    assert first.authority_id == replayed.authority_id
    assert first.evidence_hash == replayed.evidence_hash
    assert json.loads(first.evidence_manifest_json) == evidence.model_dump(
        mode="json"
    )


def test_repository_records_one_immutable_historical_authority(
    tmp_path,
    monkeypatch,
) -> None:
    database = tmp_path / "authority.db"
    sqlite3.connect(database).close()
    _seed_runtime_boundary(database)
    migrate_candidate_verification(database, tmp_path / "before-authority.db")
    repository = SqliteCandidateVerificationRepository(database)
    monkeypatch.setattr(
        "src.candidate_verification.repository."
        "_historical_database_evidence_matches",
        lambda connection, _evidence: connection.in_transaction,
    )
    authority = HistoricalReverificationAuthority.build(
        evidence=_evidence(),
        actor_id="owner-a",
        idempotency_key="recover-old-candidate",
        recorded_at=datetime(2026, 8, 25, 8, tzinfo=timezone.utc),
    )

    with sqlite3.connect(database) as connection:
        values = authority.model_dump(mode="json")
        columns = tuple(values)
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(
                "INSERT INTO candidate_reverification_authorities ("
                + ", ".join(columns)
                + ") VALUES ("
                + ", ".join("?" for _ in columns)
                + ")",
                tuple(values[column] for column in columns),
            )

    recorded = repository.create_historical_authority(authority)

    assert repository.get_historical_authority(
        owner_id="owner-a",
        task_id="task-old",
        revision=1,
        run_id="pi-run-old",
        candidate_set_hash="6" * 64,
        purpose=(
            HistoricalReverificationPurpose.SEMANTIC_INCONCLUSIVE_REVERIFICATION
        ),
    ) == recorded
    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="不可改写"):
            connection.execute(
                "UPDATE candidate_reverification_authorities "
                "SET actor_id='other-owner' WHERE authority_id=?",
                (recorded.authority_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="不可删除"):
            connection.execute(
                "DELETE FROM candidate_reverification_authorities "
                "WHERE authority_id=?",
                (recorded.authority_id,),
            )


@pytest.mark.parametrize("post_migration,with_assignment", [(True, False), (False, True)])
def test_repository_rechecks_time_boundary_and_assignment_before_recording(
    tmp_path,
    post_migration: bool,
    with_assignment: bool,
) -> None:
    database = tmp_path / "authority-rejected.db"
    sqlite3.connect(database).close()
    evidence = _evidence()
    _seed_runtime_boundary(
        database,
        created_at=(
            evidence.runtime_routing_applied_at + timedelta(microseconds=1)
            if post_migration
            else evidence.legacy_runtime_created_at
        ),
        with_assignment=with_assignment,
    )
    migrate_candidate_verification(database, tmp_path / "before-authority.db")
    repository = SqliteCandidateVerificationRepository(database)
    authority = HistoricalReverificationAuthority.build(
        evidence=evidence,
        actor_id="owner-a",
        idempotency_key="recover-old-candidate",
        recorded_at=datetime(2026, 8, 25, 8, tzinfo=timezone.utc),
    )

    with pytest.raises(ValueError, match="历史重验权威恢复资格已变化"):
        repository.create_historical_authority(authority)

    assert repository.get_historical_authority(
        owner_id="owner-a",
        task_id="task-old",
        revision=1,
        run_id="pi-run-old",
        candidate_set_hash="6" * 64,
        purpose=(
            HistoricalReverificationPurpose.SEMANTIC_INCONCLUSIVE_REVERIFICATION
        ),
    ) is None
