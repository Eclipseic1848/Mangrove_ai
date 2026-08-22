# -*- coding: utf-8 -*-
"""Agentic Runtime 独立审计仓库。

该模块在现有 Web UI SQLite 中使用独立表，避免把 vNext 运行细节塞入旧工作台表；
业务路由仍通过 user_id + task_id + revision 三元组校验所有权。
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any
import uuid

from .models import (
    CandidateArtifact,
    PermissionProfile,
    RuntimeStatus,
    RuntimeTaskConfig,
    RuntimeVersion,
    VerificationReport,
)
from .coverage import CoverageContract, CoverageLedger


_LOCK = RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgenticRuntimeRepository:
    """持久化 Runtime 选择、运行状态和精简事件。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        with _LOCK, self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agentic_runtime_runs (
                    user_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    runtime_version TEXT NOT NULL,
                    permission_profile TEXT NOT NULL,
                    model_connection_id TEXT,
                    model_connection_version TEXT,
                    model_connection_model TEXT,
                    external_api_confirmed INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    run_id TEXT,
                    container_name TEXT,
                    workspace_root TEXT,
                    session_file TEXT,
                    request_json TEXT,
                    candidates_json TEXT NOT NULL DEFAULT '[]',
                    verification_json TEXT,
                    verified_candidate_set_hash TEXT,
                    failure_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, task_id, revision)
                );

                CREATE TABLE IF NOT EXISTS agentic_runtime_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_agentic_runtime_events_owner
                ON agentic_runtime_events(user_id, task_id, revision, sequence);

                CREATE TABLE IF NOT EXISTS agentic_runtime_idempotency (
                    user_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, idempotency_key)
                );

                CREATE TABLE IF NOT EXISTS agentic_runtime_coverage (
                    user_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    run_id TEXT NOT NULL,
                    contract_json TEXT NOT NULL,
                    ledger_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, task_id, revision, run_id)
                );
                """
            )
            columns = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(agentic_runtime_runs)"
                )
            }
            if "verification_json" not in columns:
                conn.execute(
                    "ALTER TABLE agentic_runtime_runs "
                    "ADD COLUMN verification_json TEXT"
                )
            if "verified_candidate_set_hash" not in columns:
                conn.execute(
                    "ALTER TABLE agentic_runtime_runs "
                    "ADD COLUMN verified_candidate_set_hash TEXT"
                )
            if "model_connection_id" not in columns:
                conn.execute(
                    "ALTER TABLE agentic_runtime_runs "
                    "ADD COLUMN model_connection_id TEXT"
                )
            if "model_connection_version" not in columns:
                conn.execute(
                    "ALTER TABLE agentic_runtime_runs "
                    "ADD COLUMN model_connection_version TEXT"
                )
            if "model_connection_model" not in columns:
                conn.execute(
                    "ALTER TABLE agentic_runtime_runs "
                    "ADD COLUMN model_connection_model TEXT"
                )
            if "external_api_confirmed" not in columns:
                conn.execute(
                    "ALTER TABLE agentic_runtime_runs "
                    "ADD COLUMN external_api_confirmed "
                    "INTEGER NOT NULL DEFAULT 0"
                )

    def save_coverage(
        self,
        *,
        user_id: str,
        task_id: str,
        revision: int,
        run_id: str,
        contract: CoverageContract,
        ledger: CoverageLedger,
    ) -> None:
        """幂等保存冻结契约和可追加账本；既有契约身份不可替换。"""

        with _LOCK, self._conn() as conn:
            row = conn.execute(
                """
                SELECT contract_json FROM agentic_runtime_coverage
                WHERE user_id=? AND task_id=? AND revision=? AND run_id=?
                """,
                (user_id, task_id, revision, run_id),
            ).fetchone()
            if row is not None:
                frozen = CoverageContract.model_validate_json(
                    row["contract_json"]
                )
                if frozen != contract:
                    raise ValueError("同一 Run 的覆盖契约不可修改")
            conn.execute(
                """
                INSERT INTO agentic_runtime_coverage (
                    user_id, task_id, revision, run_id,
                    contract_json, ledger_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, task_id, revision, run_id)
                DO UPDATE SET ledger_json=excluded.ledger_json,
                              updated_at=excluded.updated_at
                """,
                (
                    user_id,
                    task_id,
                    revision,
                    run_id,
                    contract.model_dump_json(),
                    ledger.model_dump_json(),
                    _now(),
                ),
            )

    def get_coverage(
        self,
        *,
        user_id: str,
        task_id: str,
        revision: int,
        run_id: str,
    ) -> tuple[CoverageContract, CoverageLedger] | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT contract_json, ledger_json
                FROM agentic_runtime_coverage
                WHERE user_id=? AND task_id=? AND revision=? AND run_id=?
                """,
                (user_id, task_id, revision, run_id),
            ).fetchone()
        if row is None:
            return None
        return (
            CoverageContract.model_validate_json(row["contract_json"]),
            CoverageLedger.model_validate_json(row["ledger_json"]),
        )

    def claim_idempotency(
        self,
        user_id: str,
        idempotency_key: str,
        *,
        request_hash: str,
        proposed_task_id: str,
    ) -> tuple[str, bool]:
        """原子占用创建键；相同键只能代表完全相同的用户动作。"""

        with _LOCK, self._conn() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO agentic_runtime_idempotency (
                    user_id, idempotency_key, request_hash,
                    task_id, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    idempotency_key,
                    request_hash,
                    proposed_task_id,
                    _now(),
                ),
            )
            row = conn.execute(
                """
                SELECT request_hash, task_id
                FROM agentic_runtime_idempotency
                WHERE user_id=? AND idempotency_key=?
                """,
                (user_id, idempotency_key),
            ).fetchone()
        assert row is not None
        if row["request_hash"] != request_hash:
            raise ValueError("幂等键已用于不同的任务请求")
        return str(row["task_id"]), cursor.rowcount == 1

    def release_idempotency(
        self,
        user_id: str,
        idempotency_key: str,
        *,
        task_id: str,
    ) -> None:
        """仅在任务创建失败时释放自己刚占用的键。"""

        with _LOCK, self._conn() as conn:
            conn.execute(
                """
                DELETE FROM agentic_runtime_idempotency
                WHERE user_id=? AND idempotency_key=? AND task_id=?
                """,
                (user_id, idempotency_key, task_id),
            )

    def register(self, config: RuntimeTaskConfig) -> dict[str, Any]:
        with _LOCK, self._conn() as conn:
            self.register_in_transaction(conn, config)
        saved = self.get(config.user_id, config.task_id, config.revision)
        assert saved is not None
        return saved

    def register_in_transaction(
        self,
        connection: sqlite3.Connection,
        config: RuntimeTaskConfig,
    ) -> None:
        """在调用方事务内冻结运行配置；本方法不得自行提交。"""

        now = _now()
        connection.execute(
            """
            INSERT INTO agentic_runtime_runs (
                user_id, task_id, revision, runtime_version,
                permission_profile, model_connection_id,
                model_connection_version, model_connection_model,
                external_api_confirmed, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, task_id, revision) DO NOTHING
            """,
            (
                config.user_id,
                config.task_id,
                config.revision,
                config.runtime_version.value,
                config.permission_profile.value,
                config.model_connection_id,
                config.model_connection_version,
                config.model_connection_model,
                int(config.external_api_confirmed),
                RuntimeStatus.QUEUED.value,
                now,
                now,
            ),
        )
        row = connection.execute(
            """
            SELECT runtime_version, permission_profile,
                   model_connection_id, model_connection_version,
                   model_connection_model,
                   external_api_confirmed
            FROM agentic_runtime_runs
            WHERE user_id=? AND task_id=? AND revision=?
            """,
            (config.user_id, config.task_id, config.revision),
        ).fetchone()
        assert row is not None
        frozen = (
            row["runtime_version"],
            row["permission_profile"],
            row["model_connection_id"],
            row["model_connection_version"],
            row["model_connection_model"],
            bool(row["external_api_confirmed"]),
        )
        requested = (
            config.runtime_version.value,
            config.permission_profile.value,
            config.model_connection_id,
            config.model_connection_version,
            config.model_connection_model,
            config.external_api_confirmed,
        )
        if frozen != requested:
            # RuntimeAssignment 属于不可变 TaskRevision，变化必须创建新修订。
            raise ValueError("同一 Runtime revision 的冻结配置不可修改")

    def get(
        self,
        user_id: str,
        task_id: str,
        revision: int,
    ) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM agentic_runtime_runs
                WHERE user_id=? AND task_id=? AND revision=?
                """,
                (user_id, task_id, revision),
            ).fetchone()
        return self._row(row)

    def update(
        self,
        user_id: str,
        task_id: str,
        revision: int,
        *,
        status: RuntimeStatus | None = None,
        run_id: str | None = None,
        container_name: str | None = None,
        workspace_root: str | Path | None = None,
        session_file: str | None = None,
        request: dict[str, Any] | None = None,
        candidates: tuple[CandidateArtifact, ...] | None = None,
        verification: VerificationReport | None = None,
        failure: dict[str, Any] | None = None,
        clear_failure: bool = False,
    ) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if status is not None:
            changes["status"] = status.value
        if run_id is not None:
            changes["run_id"] = run_id
        if container_name is not None:
            changes["container_name"] = container_name
        if workspace_root is not None:
            changes["workspace_root"] = str(workspace_root)
        if session_file is not None:
            changes["session_file"] = session_file
        if request is not None:
            changes["request_json"] = json.dumps(
                request, ensure_ascii=False, default=str
            )
        if candidates is not None:
            changes["candidates_json"] = json.dumps(
                [item.model_dump(mode="json") for item in candidates],
                ensure_ascii=False,
            )
        if verification is not None:
            changes["verification_json"] = verification.model_dump_json()
        if candidates is not None and verification is not None:
            # Verification 与候选集合必须在同一次持久化动作中冻结，后续只替换候选时
            # Publisher 会检测到哈希不一致并拒绝发布旧验证结论。
            candidate_payload = [
                {
                    "artifact_id": item.artifact_id,
                    "filename": item.filename,
                    "format": item.format,
                    "sha256": item.sha256,
                    "size_bytes": item.size_bytes,
                }
                for item in sorted(
                    candidates,
                    key=lambda value: value.artifact_id,
                )
            ]
            encoded = json.dumps(
                candidate_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            changes["verified_candidate_set_hash"] = hashlib.sha256(
                encoded
            ).hexdigest()
        if failure is not None:
            changes["failure_json"] = json.dumps(
                failure, ensure_ascii=False
            )
        elif clear_failure:
            changes["failure_json"] = None
        if not changes:
            current = self.get(user_id, task_id, revision)
            if current is None:
                raise KeyError("Agentic Runtime 记录不存在或无权访问")
            return current
        changes["updated_at"] = _now()
        assignments = ", ".join(f"{key}=?" for key in changes)
        values = [*changes.values(), user_id, task_id, revision]
        with _LOCK, self._conn() as conn:
            cursor = conn.execute(
                f"""
                UPDATE agentic_runtime_runs SET {assignments}
                WHERE user_id=? AND task_id=? AND revision=?
                """,
                values,
            )
        if cursor.rowcount != 1:
            raise KeyError("Agentic Runtime 记录不存在或无权访问")
        saved = self.get(user_id, task_id, revision)
        assert saved is not None
        return saved

    def append_event(
        self,
        user_id: str,
        task_id: str,
        revision: int,
        *,
        event_type: str,
        summary: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event_id = f"agent_event_{uuid.uuid4().hex[:16]}"
        created_at = _now()
        with _LOCK, self._conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO agentic_runtime_events (
                    event_id, user_id, task_id, revision, event_type,
                    summary, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    user_id,
                    task_id,
                    revision,
                    event_type,
                    summary,
                    json.dumps(details or {}, ensure_ascii=False),
                    created_at,
                ),
            )
            sequence = int(cursor.lastrowid)
        return {
            "sequence": sequence,
            "event_id": event_id,
            "event_type": event_type,
            "summary": summary,
            "details": details or {},
            "created_at": created_at,
        }

    def list_events(
        self,
        user_id: str,
        task_id: str,
        revision: int,
    ) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM agentic_runtime_events
                WHERE user_id=? AND task_id=? AND revision=?
                ORDER BY sequence
                """,
                (user_id, task_id, revision),
            ).fetchall()
        return [
            {
                "sequence": row["sequence"],
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "summary": row["summary"],
                "details": json.loads(row["details_json"] or "{}"),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        candidates = tuple(
            CandidateArtifact.model_validate(item)
            for item in json.loads(row["candidates_json"] or "[]")
        )
        return {
            "user_id": row["user_id"],
            "task_id": row["task_id"],
            "revision": row["revision"],
            "runtime_version": RuntimeVersion(row["runtime_version"]),
            "permission_profile": PermissionProfile(
                row["permission_profile"]
            ),
            "model_connection_id": row["model_connection_id"],
            "model_connection_version": row["model_connection_version"],
            "model_connection_model": row["model_connection_model"],
            "external_api_confirmed": bool(row["external_api_confirmed"]),
            "status": RuntimeStatus(row["status"]),
            "run_id": row["run_id"],
            "container_name": row["container_name"],
            "workspace_root": row["workspace_root"],
            "session_file": row["session_file"],
            "request": (
                json.loads(row["request_json"])
                if row["request_json"]
                else None
            ),
            "candidates": candidates,
            "verification": (
                VerificationReport.model_validate_json(
                    row["verification_json"]
                )
                if row["verification_json"]
                else None
            ),
            "verified_candidate_set_hash": row[
                "verified_candidate_set_hash"
            ],
            "failure": (
                json.loads(row["failure_json"])
                if row["failure_json"]
                else None
            ),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
