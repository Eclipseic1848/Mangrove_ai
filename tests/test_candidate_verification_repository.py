# -*- coding: utf-8 -*-
"""CandidateVerification Repository 只通过公共 Interface 验证。"""
from __future__ import annotations

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


def _migrated_repository(tmp_path) -> SqliteCandidateVerificationRepository:
    database = tmp_path / "candidate-verification.db"
    sqlite3.connect(database).close()
    migrate_candidate_verification(database, tmp_path / "before.db")
    return SqliteCandidateVerificationRepository(database)


def _requested_attempt() -> VerificationAttempt:
    return VerificationAttempt(
        attempt_id="attempt-1",
        owner_id="owner-a",
        task_id="task-a",
        revision=1,
        run_id="run-a",
        previous_attempt_id=None,
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
            '{"schema_version":1,"verifier_ruleset_hash":"' + "5" * 64 + '"}'
        ),
        actor_id="owner-a",
        idempotency_key="idem-1",
        request_hash="9" * 64,
        status=AttemptStatus.REQUESTED,
        created_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )


def test_repository_refuses_database_without_explicit_migration(tmp_path) -> None:
    database = tmp_path / "not-migrated.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE existing_data (value TEXT NOT NULL)")

    with pytest.raises(RuntimeError, match="尚未执行带备份迁移"):
        SqliteCandidateVerificationRepository(database)


def test_repository_rejects_malformed_candidate_verification_schema(
    tmp_path,
) -> None:
    database = tmp_path / "malformed.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE candidate_verification_attempts "
            "(attempt_id TEXT PRIMARY KEY)"
        )

    with pytest.raises(RuntimeError, match="尚未执行带备份迁移"):
        SqliteCandidateVerificationRepository(database)


def test_created_attempt_is_retrievable_with_all_frozen_identity(tmp_path) -> None:
    repository = _migrated_repository(tmp_path)
    requested = _requested_attempt()

    created = repository.create(requested)

    assert created == requested
    assert repository.get("owner-a", "attempt-1") == requested


def test_create_rejects_requested_attempt_with_runtime_fields(tmp_path) -> None:
    repository = _migrated_repository(tmp_path)
    malformed = _requested_attempt().model_copy(
        update={
            "started_at": datetime(2026, 8, 24, 1, tzinfo=timezone.utc),
            "report_json": '{}',
            "report_hash": "a" * 64,
        }
    )

    with pytest.raises(ValueError, match="requested Attempt 状态字段不一致"):
        repository.create(malformed)


def test_same_idempotent_request_returns_existing_attempt(tmp_path) -> None:
    repository = _migrated_repository(tmp_path)
    requested = _requested_attempt()

    first = repository.create(requested)
    replayed = repository.create(requested)

    assert replayed == first


def test_candidate_allows_only_one_active_attempt(tmp_path) -> None:
    repository = _migrated_repository(tmp_path)
    repository.create(_requested_attempt())
    competing = _requested_attempt().model_copy(
        update={
            "attempt_id": "attempt-2",
            "idempotency_key": "idem-2",
            "request_hash": "a" * 64,
        }
    )

    with pytest.raises(RuntimeError, match="已有活动 Attempt"):
        repository.create(competing)


def test_previous_attempt_cannot_cross_owner_boundary(tmp_path) -> None:
    repository = _migrated_repository(tmp_path)
    previous = _requested_attempt().model_copy(
        update={
            "attempt_id": "attempt-owner-b",
            "owner_id": "owner-b",
            "actor_id": "owner-b",
            "idempotency_key": "idem-owner-b",
            "request_hash": "a" * 64,
        }
    )
    repository.create(previous)
    repository.start(
        "owner-b",
        previous.attempt_id,
        started_at=datetime(2026, 8, 24, 1, tzinfo=timezone.utc),
    )
    repository.finish(
        "owner-b",
        previous.attempt_id,
        status=AttemptStatus.FAILED,
        report_json='{"status":"failed"}',
        report_hash=(
            "759315d5ae8c31136d2a7bc803e591554894987559325cdf7e0b5965bec0eaca"
        ),
        finished_at=datetime(2026, 8, 24, 2, tzinfo=timezone.utc),
    )
    successor = _requested_attempt().model_copy(
        update={"previous_attempt_id": previous.attempt_id}
    )

    with pytest.raises(PermissionError, match="前序 Attempt 不存在或 Owner 不匹配"):
        repository.create(successor)
    values = successor.model_dump(mode="json")
    columns = tuple(values)
    with sqlite3.connect(tmp_path / "candidate-verification.db") as connection:
        with pytest.raises(sqlite3.IntegrityError, match="前序 Attempt 身份不一致"):
            connection.execute(
                "INSERT INTO candidate_verification_attempts ("
                + ", ".join(columns)
                + ") VALUES ("
                + ", ".join("?" for _ in columns)
                + ")",
                tuple(values[column] for column in columns),
            )


def test_requested_attempt_can_start_with_compare_and_set(tmp_path) -> None:
    repository = _migrated_repository(tmp_path)
    repository.create(_requested_attempt())
    started_at = datetime(2026, 8, 24, 1, tzinfo=timezone.utc)

    running = repository.start("owner-a", "attempt-1", started_at=started_at)

    assert running.status is AttemptStatus.RUNNING
    assert running.started_at == started_at


def test_running_attempt_can_finish_with_immutable_report(tmp_path) -> None:
    repository = _migrated_repository(tmp_path)
    repository.create(_requested_attempt())
    repository.start(
        "owner-a",
        "attempt-1",
        started_at=datetime(2026, 8, 24, 1, tzinfo=timezone.utc),
    )
    finished_at = datetime(2026, 8, 24, 2, tzinfo=timezone.utc)

    finished = repository.finish(
        "owner-a",
        "attempt-1",
        status=AttemptStatus.PASSED,
        report_json='{"status":"passed"}',
        report_hash="76f1805001bc4f155c8efa5651c57c4f1858af1f6786cbe4596214a4b64375a6",
        finished_at=finished_at,
    )

    assert finished.status is AttemptStatus.PASSED
    assert finished.report_json == '{"status":"passed"}'
    assert finished.finished_at == finished_at


def test_finish_rejects_report_status_that_conflicts_with_attempt(tmp_path) -> None:
    repository = _migrated_repository(tmp_path)
    repository.create(_requested_attempt())
    repository.start(
        "owner-a",
        "attempt-1",
        started_at=datetime(2026, 8, 24, 1, tzinfo=timezone.utc),
    )

    with pytest.raises(ValueError, match="候选验证报告状态与 Attempt 终态不一致"):
        repository.finish(
            "owner-a",
            "attempt-1",
            status=AttemptStatus.PASSED,
            report_json='{"status":"failed"}',
            report_hash=(
                "759315d5ae8c31136d2a7bc803e591554894987559325cdf7e0b5965bec0eaca"
            ),
            finished_at=datetime(2026, 8, 24, 2, tzinfo=timezone.utc),
        )
