# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3

import pytest
from pydantic import ValidationError

from src.agentic_runtime.models import (
    PermissionProfile,
    PiRuntimeRequest,
    SourceInput,
)
from src.api import semantic_workspace_runtime as runtime_mod
from src.api.routes.semantic_workspace import CandidateReverificationIn
from src.config.settings import settings
from src.candidate_verification import HistoricalReverificationBinding


_MIGRATED_AT = datetime(2026, 8, 22, 20, 39, tzinfo=timezone.utc)


def _request(tmp_path) -> PiRuntimeRequest:
    source = tmp_path / "source.txt"
    source.write_text("已确认来源", encoding="utf-8")
    return PiRuntimeRequest(
        user_id="owner-a",
        task_id="task-old",
        revision=1,
        objective_text="读取来源并输出 CSV",
        requested_output_formats=("csv",),
        sources=(
            SourceInput(
                upload_id="upload-a",
                original_name=source.name,
                host_path=source,
                sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            ),
        ),
        permission_profile=PermissionProfile.STANDARD,
        model_connection_id="connection-a",
        model_connection_version="version-a",
        model_connection_model="deepseek-v4-flash",
        external_api_confirmed=True,
    )


def _install_evidence_database(database, request: PiRuntimeRequest) -> None:
    request_json = json.dumps(
        request.model_dump(mode="json", exclude={"api_key"}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE semantic_workspace_tasks (
                task_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                upload_ids_json TEXT NOT NULL,
                active_revision INTEGER NOT NULL,
                PRIMARY KEY (task_id, user_id)
            );
            CREATE TABLE semantic_workspace_revisions (
                task_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                run_id TEXT,
                objective_text TEXT NOT NULL,
                output_formats_json TEXT NOT NULL,
                table_output_contracts_json TEXT NOT NULL,
                PRIMARY KEY (task_id, user_id, revision)
            );
            CREATE TABLE agentic_runtime_runs (
                user_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                run_id TEXT,
                runtime_version TEXT NOT NULL,
                status TEXT NOT NULL,
                request_json TEXT,
                external_api_confirmed INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, task_id, revision)
            );
            CREATE TABLE agentic_runtime_events (
                sequence INTEGER PRIMARY KEY,
                event_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE runtime_routing_migrations (
                migration_id TEXT PRIMARY KEY,
                ddl_sha256 TEXT NOT NULL,
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
            "INSERT INTO semantic_workspace_tasks VALUES (?, ?, ?, ?)",
            ("task-old", "owner-a", '["upload-a"]', 1),
        )
        connection.execute(
            "INSERT INTO semantic_workspace_revisions VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "task-old",
                "owner-a",
                1,
                "pi-run-old",
                request.objective_text,
                '["csv"]',
                "[]",
            ),
        )
        connection.execute(
            "INSERT INTO agentic_runtime_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "owner-a",
                "task-old",
                1,
                "pi-run-old",
                "pi",
                "candidate_ready",
                request_json,
                1,
                (_MIGRATED_AT - timedelta(days=1)).isoformat(),
            ),
        )
        for sequence, event_type in enumerate(
            (
                "runtime.preparing",
                "agent.started",
                "verification.completed",
                "candidate.ready",
            ),
            start=1,
        ):
            connection.execute(
                "INSERT INTO agentic_runtime_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    sequence,
                    f"event-{sequence}",
                    "owner-a",
                    "task-old",
                    1,
                    event_type,
                    (_MIGRATED_AT - timedelta(hours=1)).isoformat(),
                ),
            )
        connection.execute(
            "INSERT INTO runtime_routing_migrations VALUES (?, ?, ?, ?)",
            (
                "0001_runtime_routing",
                "0" * 64,
                "1" * 64,
                _MIGRATED_AT.isoformat(),
            ),
        )


def _binding() -> HistoricalReverificationBinding:
    return HistoricalReverificationBinding(
        candidate_set_hash="2" * 64,
        candidate_manifest_hash="3" * 64,
        goal_contract_hash="4" * 64,
        delivery_spec_hash="5" * 64,
        previous_attempt_id="verification-old",
        previous_report_hash="6" * 64,
    )


def test_workspace_authority_only_builds_evidence_for_exact_pre_migration_gap(
    tmp_path,
    monkeypatch,
) -> None:
    database = tmp_path / "authority.db"
    request = _request(tmp_path)
    _install_evidence_database(database, request)
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    authority = runtime_mod._WorkspaceReverificationAuthority()

    evidence = authority.historical_recovery_evidence(
        request,
        "pi-run-old",
        _binding(),
    )

    assert evidence is not None
    assert evidence.owner_id == "owner-a"
    assert evidence.runtime_routing_applied_at == _MIGRATED_AT
    assert evidence.runtime_routing_backup_sha256 == "1" * 64
    assert evidence.candidate_set_hash == "2" * 64
    assert len(evidence.runtime_event_chain_hash) == 64

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE agentic_runtime_runs SET created_at=?",
            ((_MIGRATED_AT + timedelta(microseconds=1)).isoformat(),),
        )
    assert authority.historical_recovery_evidence(
        request,
        "pi-run-old",
        _binding(),
    ) is None

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE agentic_runtime_runs SET created_at=?",
            ((_MIGRATED_AT - timedelta(days=1)).isoformat(),),
        )
        connection.execute(
            "INSERT INTO runtime_assignments VALUES (?, ?, ?, ?)",
            ("owner-a", "task-old", 1, "{}"),
        )
    assert authority.historical_recovery_evidence(
        request,
        "pi-run-old",
        _binding(),
    ) is None


def test_workspace_authority_accepts_only_legacy_missing_egress_field(
    tmp_path,
    monkeypatch,
) -> None:
    database = tmp_path / "authority.db"
    request = _request(tmp_path)
    _install_evidence_database(database, request)
    with sqlite3.connect(database) as connection:
        frozen = json.loads(
            connection.execute(
                "SELECT request_json FROM agentic_runtime_runs"
            ).fetchone()[0]
        )
        frozen.pop("external_api_confirmed")
        connection.execute(
            "UPDATE agentic_runtime_runs SET request_json=?",
            (
                json.dumps(
                    frozen,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )
    monkeypatch.setattr(settings, "webui_db_path", str(database))

    evidence = runtime_mod._WorkspaceReverificationAuthority().historical_recovery_evidence(
        request,
        "pi-run-old",
        _binding(),
    )

    assert evidence is not None
    assert evidence.runtime_request_hash == hashlib.sha256(
        json.dumps(
            request.model_dump(mode="json", exclude={"api_key"}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


@pytest.mark.parametrize("corrupt_confirmation", ["false", "0", 2])
def test_workspace_authority_rejects_corrupt_runtime_egress_column(
    tmp_path,
    monkeypatch,
    corrupt_confirmation,
) -> None:
    database = tmp_path / "authority.db"
    request = _request(tmp_path)
    _install_evidence_database(database, request)
    with sqlite3.connect(database) as connection:
        frozen = json.loads(
            connection.execute(
                "SELECT request_json FROM agentic_runtime_runs"
            ).fetchone()[0]
        )
        frozen.pop("external_api_confirmed")
        connection.execute(
            "UPDATE agentic_runtime_runs "
            "SET request_json=?, external_api_confirmed=?",
            (
                json.dumps(
                    frozen,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                corrupt_confirmation,
            ),
        )
    monkeypatch.setattr(settings, "webui_db_path", str(database))

    evidence = (
        runtime_mod._WorkspaceReverificationAuthority()
        .historical_recovery_evidence(request, "pi-run-old", _binding())
    )

    assert evidence is None


def test_reverification_api_accepts_only_strict_structured_recovery_confirmation() -> None:
    payload = {
        "expected_revision": 1,
        "expected_previous_attempt_id": "verification-old",
        "external_api_confirmed": True,
        "historical_authority_recovery": {
            "expected_evidence_hash": "7" * 64,
            "acknowledge_no_historical_assignment": True,
            "acknowledge_reverification_only": True,
        },
    }

    parsed = CandidateReverificationIn.model_validate(payload)

    assert parsed.historical_authority_recovery is not None
    assert (
        parsed.historical_authority_recovery.expected_evidence_hash
        == "7" * 64
    )
    invalid = json.loads(json.dumps(payload))
    invalid["historical_authority_recovery"][
        "acknowledge_reverification_only"
    ] = "true"
    with pytest.raises(ValidationError):
        CandidateReverificationIn.model_validate(invalid)
