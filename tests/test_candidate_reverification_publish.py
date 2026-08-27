# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.database_migrations import DatabaseTarget, apply_migrations
from tests.database_migration_helpers import migrated_webui_database

from src.delivery_publishing.models import (
    CandidateRef,
    DeliverySpec,
    PublicationGate,
    PublishCommand,
)
from src.delivery_publishing.repository import DeliveryPublishingRepository
from src.delivery_publishing.service import DeliveryPublisher


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _command(
    candidate: Path,
    *,
    idempotency_key: str,
    attempt_id: str = "attempt-a",
) -> PublishCommand:
    return PublishCommand.build(
        owner_id="owner-a",
        task_id="task-a",
        task_revision=1,
        task_revision_hash="1" * 64,
        goal_contract_hash="2" * 64,
        run_id="pi-run-a",
        candidates=(CandidateRef(
            artifact_id="candidate_json",
            filename="result.json",
            format="json",
            sha256=_sha256(candidate),
            size_bytes=candidate.stat().st_size,
        ),),
        verification_report_id=attempt_id,
        verification_report_hash="3" * 64,
        verification_status="passed",
        delivery_spec=DeliverySpec(
            requested_formats=("json",),
            output_name="报销结果",
            requested_file_count=1,
        ),
        source_snapshot_refs=("upload-a:" + "4" * 64,),
        verification_attempt_id=attempt_id,
        request_idempotency_key=idempotency_key,
    )


def _install_explicit_cas_state(
    database: Path,
    command: PublishCommand,
) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO semantic_workspace_tasks "
            "(task_id, user_id, title, objective_text, active_revision, "
            "cancel_requested, created_at, updated_at) "
            "VALUES (?, ?, '测试任务', '测试交付', ?, 0, ?, ?)",
            (
                command.task_id,
                command.owner_id,
                command.task_revision,
                "2026-08-24T12:00:00+00:00",
                "2026-08-24T12:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO candidate_verification_attempts ("
            "owner_id, attempt_id, task_id, revision, run_id, reason_code, "
            "candidate_set_hash, ruleset_identity_status, actor_id, "
            "idempotency_key, request_hash, status, report_hash, created_at"
            ") VALUES (?, ?, ?, ?, ?, 'initial', ?, 'legacy_unversioned', "
            "?, ?, ?, 'passed', ?, ?)",
            (
                command.owner_id,
                command.verification_attempt_id,
                command.task_id,
                command.task_revision,
                command.run_id,
                command.candidate_set_hash,
                command.owner_id,
                "seed-" + command.verification_attempt_id,
                "0" * 64,
                command.verification_report_hash,
                "2026-08-24T12:00:00+00:00",
            ),
        )


def test_existing_publish_intent_schema_adds_hashed_http_idempotency_binding(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy-publisher.db"
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

    with pytest.raises(RuntimeError, match="显式迁移"):
        DeliveryPublishingRepository(database)

    backup = tmp_path / "legacy-publisher.backup.db"
    apply_migrations(DatabaseTarget(profile="webui", path=database), backup)
    DeliveryPublishingRepository(database)

    with sqlite3.connect(database) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(delivery_publish_intents)"
            ).fetchall()
        }
        legacy = connection.execute(
            "SELECT publication_key, request_idempotency_hash "
            "FROM delivery_publish_intents"
        ).fetchone()
        indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(delivery_publish_intents)"
            ).fetchall()
        }
    assert "request_idempotency_hash" in columns
    assert legacy == ("legacy-key", None)
    assert "idx_dpi_owner_request_idempotency" in indexes


def test_exact_attempt_and_idempotency_key_bind_explicit_publication(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate.json"
    candidate.write_text(
        json.dumps({"items": [{"name": "张三"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    repository = DeliveryPublishingRepository(
        migrated_webui_database(tmp_path / "webui.db")
    )
    publisher = DeliveryPublisher(
        repository=repository,
        output_root=tmp_path / "deliveries",
        candidate_resolver=lambda _command: {"candidate_json": candidate},
        gate_reader=lambda _command: PublicationGate(),
    )

    command = _command(candidate, idempotency_key="publish-request-a")
    replay = _command(candidate, idempotency_key="publish-request-a")
    conflicting = _command(candidate, idempotency_key="publish-request-b")
    _install_explicit_cas_state(tmp_path / "webui.db", command)

    assert replay.publication_key == command.publication_key
    assert replay.frozen_hash() == command.frozen_hash()
    assert conflicting.publication_key == command.publication_key
    assert conflicting.frozen_hash() != command.frozen_hash()

    first = publisher.publish(command, actor_id="owner-a")
    second = publisher.publish(replay, actor_id="owner-a")

    assert second.delivery_id == first.delivery_id
    assert first.provenance["verification_attempt_id"] == "attempt-a"
    with pytest.raises(ValueError, match="幂等键已用于不同冻结输入"):
        publisher.publish(conflicting, actor_id="owner-a")


def test_same_http_idempotency_key_cannot_bind_another_attempt(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate.json"
    candidate.write_text('{"ok":true}', encoding="utf-8")
    repository = DeliveryPublishingRepository(
        migrated_webui_database(tmp_path / "webui.db")
    )
    publisher = DeliveryPublisher(
        repository=repository,
        output_root=tmp_path / "deliveries",
        candidate_resolver=lambda _command: {"candidate_json": candidate},
        gate_reader=lambda _command: PublicationGate(),
    )
    first = _command(
        candidate,
        idempotency_key="one-http-request",
        attempt_id="attempt-a",
    )
    another_attempt = _command(
        candidate,
        idempotency_key="one-http-request",
        attempt_id="attempt-b",
    )
    assert another_attempt.publication_key != first.publication_key
    _install_explicit_cas_state(tmp_path / "webui.db", first)

    publisher.publish(first, actor_id="owner-a")
    with pytest.raises(ValueError, match="幂等键已绑定其他发布请求"):
        publisher.publish(another_attempt, actor_id="owner-a")


def test_concurrent_same_explicit_publication_returns_one_delivery(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate.json"
    candidate.write_text('{"ok":true}', encoding="utf-8")
    database = tmp_path / "webui.db"
    repository = DeliveryPublishingRepository(migrated_webui_database(database))
    publisher = DeliveryPublisher(
        repository=repository,
        output_root=tmp_path / "deliveries",
        candidate_resolver=lambda _command: {"candidate_json": candidate},
        gate_reader=lambda _command: PublicationGate(),
    )
    command = _command(candidate, idempotency_key="concurrent-publish")
    _install_explicit_cas_state(database, command)

    with ThreadPoolExecutor(max_workers=2) as executor:
        deliveries = tuple(
            executor.map(
                lambda _index: publisher.publish(
                    command,
                    actor_id="owner-a",
                ),
                range(2),
            )
        )

    assert len({item.delivery_id for item in deliveries}) == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM formal_delivery_runs"
        ).fetchone()[0] == 1


def test_explicit_publication_qa_failure_has_zero_formal_output(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate.json"
    candidate.write_text('{"broken":', encoding="utf-8")
    database = tmp_path / "webui.db"
    repository = DeliveryPublishingRepository(migrated_webui_database(database))
    command = _command(candidate, idempotency_key="qa-failure")
    _install_explicit_cas_state(database, command)
    publisher = DeliveryPublisher(
        repository=repository,
        output_root=tmp_path / "deliveries",
        candidate_resolver=lambda _command: {"candidate_json": candidate},
        gate_reader=lambda _command: PublicationGate(),
    )

    with pytest.raises(json.JSONDecodeError):
        publisher.publish(command, actor_id="owner-a")

    assert repository.latest_delivery("owner-a", "pi-run-a") is None
    intent = repository.get_intent(command.publication_key)
    assert intent is not None
    assert intent["status"] == "failed"


@pytest.mark.parametrize("blocker", ["revision", "p0"])
def test_atomic_commit_cas_blocks_stale_revision_or_p0(
    tmp_path: Path,
    blocker: str,
) -> None:
    candidate = tmp_path / f"{blocker}.json"
    candidate.write_text('{"ok":true}', encoding="utf-8")
    database = tmp_path / f"{blocker}.db"
    repository = DeliveryPublishingRepository(migrated_webui_database(database))
    command = _command(candidate, idempotency_key=f"atomic-{blocker}")
    _install_explicit_cas_state(database, command)
    with sqlite3.connect(database) as connection:
        if blocker == "revision":
            connection.execute(
                "UPDATE semantic_workspace_tasks SET active_revision=2"
            )
        else:
            connection.execute(
                "UPDATE runtime_rollout_state SET p0_blocked=1 "
                "WHERE state_id=1"
            )
    publisher = DeliveryPublisher(
        repository=repository,
        output_root=tmp_path / f"{blocker}-deliveries",
        candidate_resolver=lambda _command: {"candidate_json": candidate},
        # 模拟读取门后状态翻转；最终数据库 CAS 仍必须拒绝。
        gate_reader=lambda _command: PublicationGate(),
    )

    with pytest.raises(ValueError, match="活动版本已变化|P0 发布门已阻断"):
        publisher.publish(command, actor_id="owner-a")
    assert repository.latest_delivery("owner-a", "pi-run-a") is None


def test_crash_after_commit_point_recovers_only_from_frozen_staging(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate.json"
    candidate.write_text('{"ok":true}', encoding="utf-8")
    database = tmp_path / "webui.db"
    repository = DeliveryPublishingRepository(migrated_webui_database(database))
    command = _command(candidate, idempotency_key="commit-window-crash")
    _install_explicit_cas_state(database, command)
    original_begin_commit = repository.begin_commit

    def crash_after_begin_commit(*args, **kwargs) -> None:
        original_begin_commit(*args, **kwargs)
        raise RuntimeError("模拟 committing 后、rename 前崩溃")

    repository.begin_commit = crash_after_begin_commit  # type: ignore[method-assign]
    publisher = DeliveryPublisher(
        repository=repository,
        output_root=tmp_path / "deliveries",
        candidate_resolver=lambda _command: {"candidate_json": candidate},
        gate_reader=lambda _command: PublicationGate(),
    )
    with pytest.raises(RuntimeError, match="模拟 committing"):
        publisher.publish(command, actor_id="owner-a")
    intent = repository.get_intent(command.publication_key)
    assert intent is not None
    assert intent["status"] == "committing"

    repository.begin_commit = original_begin_commit  # type: ignore[method-assign]
    candidate.unlink()

    def forbidden(_command):
        raise AssertionError("越过提交点后不得重新读取 Candidate 或业务门")

    recovered = DeliveryPublisher(
        repository=repository,
        output_root=tmp_path / "deliveries",
        candidate_resolver=forbidden,
        gate_reader=forbidden,
    ).publish(command, actor_id="owner-a")

    assert recovered.provenance["verification_attempt_id"] == "attempt-a"
    assert repository.latest_delivery("owner-a", "pi-run-a") is not None


def test_explicit_publication_cancel_or_candidate_drift_has_zero_delivery(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate.json"
    candidate.write_text('{"ok":true}', encoding="utf-8")
    command = _command(candidate, idempotency_key="publish-cancelled")

    cancelled_repository = DeliveryPublishingRepository(
        migrated_webui_database(tmp_path / "cancel.db")
    )
    cancelled = DeliveryPublisher(
        repository=cancelled_repository,
        output_root=tmp_path / "cancel-deliveries",
        candidate_resolver=lambda _command: {"candidate_json": candidate},
        gate_reader=lambda _command: PublicationGate(cancel_requested=True),
    )
    with pytest.raises(ValueError, match="任务已取消"):
        cancelled.publish(command, actor_id="owner-a")
    assert cancelled_repository.latest_delivery("owner-a", "pi-run-a") is None

    drift_repository = DeliveryPublishingRepository(
        migrated_webui_database(tmp_path / "drift.db")
    )
    drifted = DeliveryPublisher(
        repository=drift_repository,
        output_root=tmp_path / "drift-deliveries",
        candidate_resolver=lambda _command: {"candidate_json": candidate},
        gate_reader=lambda _command: PublicationGate(),
    )
    candidate.write_text('{"ok":false}', encoding="utf-8")
    with pytest.raises(ValueError, match="候选文件哈希已变化"):
        drifted.publish(command, actor_id="owner-a")
    assert drift_repository.latest_delivery("owner-a", "pi-run-a") is None
