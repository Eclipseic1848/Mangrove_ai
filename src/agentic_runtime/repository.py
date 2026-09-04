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

from src.database_migrations import DatabaseTarget, inspect_database

from .models import (
    CandidateArtifact,
    PermissionProfile,
    RuntimeStatus,
    RuntimeTaskConfig,
    RuntimeVersion,
    VerificationReport,
)
from .coverage import (
    CoverageContract,
    CoverageLedger,
    PartialCandidateAssessment,
)


_LOCK = RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgenticRuntimeRepository:
    """持久化 Runtime 选择、运行状态和精简事件。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        inspect_database(
            DatabaseTarget(profile="webui", path=self.db_path)
        ).require_current()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn


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

    def save_candidate_coverage(
        self,
        *,
        user_id: str,
        task_id: str,
        revision: int,
        run_id: str,
        candidate_set_hash: str,
        assessment: PartialCandidateAssessment,
    ) -> None:
        """冻结 Candidate 对应的覆盖结论；同一身份只允许完全一致的重放。"""

        encoded = assessment.model_dump_json()
        with _LOCK, self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO candidate_coverage_assessments (
                    owner_id, task_id, revision, run_id,
                    candidate_set_hash, assessment_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    task_id,
                    revision,
                    run_id,
                    candidate_set_hash,
                    encoded,
                    _now(),
                ),
            )
            row = conn.execute(
                """
                SELECT run_id, assessment_json
                FROM candidate_coverage_assessments
                WHERE owner_id=? AND task_id=? AND revision=?
                  AND candidate_set_hash=?
                """,
                (user_id, task_id, revision, candidate_set_hash),
            ).fetchone()
        assert row is not None
        if row["run_id"] != run_id or row["assessment_json"] != encoded:
            raise ValueError("同一 Candidate 的覆盖结论不可修改")

    def get_candidate_coverage(
        self,
        *,
        user_id: str,
        task_id: str,
        revision: int,
        candidate_set_hash: str,
    ) -> PartialCandidateAssessment | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT assessment_json
                FROM candidate_coverage_assessments
                WHERE owner_id=? AND task_id=? AND revision=?
                  AND candidate_set_hash=?
                """,
                (user_id, task_id, revision, candidate_set_hash),
            ).fetchone()
        if row is None:
            return None
        return PartialCandidateAssessment.model_validate_json(
            row["assessment_json"]
        )

    def claim_gap_action(
        self,
        *,
        user_id: str,
        task_id: str,
        idempotency_key: str,
        request_hash: str,
        source_revision: int,
        candidate_set_hash: str,
        action: str,
    ) -> tuple[dict[str, Any], bool]:
        """原子占用 Owner 动作；同一幂等键不能表达另一个决定。"""

        now = _now()
        with _LOCK, self._conn() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO candidate_gap_actions (
                    owner_id, task_id, idempotency_key, request_hash,
                    source_revision, candidate_set_hash, action, status,
                    target_revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, ?)
                """,
                (
                    user_id,
                    task_id,
                    idempotency_key,
                    request_hash,
                    source_revision,
                    candidate_set_hash,
                    action,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                """
                SELECT * FROM candidate_gap_actions
                WHERE owner_id=? AND task_id=? AND idempotency_key=?
                """,
                (user_id, task_id, idempotency_key),
            ).fetchone()
        assert row is not None
        saved = dict(row)
        if any(
            (
                saved["request_hash"] != request_hash,
                int(saved["source_revision"]) != source_revision,
                saved["candidate_set_hash"] != candidate_set_hash,
                saved["action"] != action,
            )
        ):
            raise ValueError("该幂等键已用于另一个缺口决定")
        return saved, cursor.rowcount == 1

    def complete_gap_action(
        self,
        *,
        user_id: str,
        task_id: str,
        idempotency_key: str,
        target_revision: int | None = None,
        status: str = "completed",
    ) -> dict[str, Any]:
        if status not in {"completed", "rejected"}:
            raise ValueError("缺口动作只能完成或拒绝")
        with _LOCK, self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE candidate_gap_actions
                SET status=?, target_revision=?, updated_at=?
                WHERE owner_id=? AND task_id=? AND idempotency_key=?
                  AND status='pending'
                """,
                (
                    status,
                    target_revision,
                    _now(),
                    user_id,
                    task_id,
                    idempotency_key,
                ),
            )
            row = conn.execute(
                """
                SELECT * FROM candidate_gap_actions
                WHERE owner_id=? AND task_id=? AND idempotency_key=?
                """,
                (user_id, task_id, idempotency_key),
            ).fetchone()
        if row is None:
            raise KeyError("缺口动作不存在或无权访问")
        if cursor.rowcount != 1 and row["status"] != status:
            raise ValueError("缺口动作已经进入其他终态")
        return dict(row)

    def list_gap_actions(
        self,
        *,
        user_id: str,
        task_id: str,
        source_revision: int,
    ) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT action, status, target_revision, created_at, updated_at
                FROM candidate_gap_actions
                WHERE owner_id=? AND task_id=? AND source_revision=?
                ORDER BY created_at, idempotency_key
                """,
                (user_id, task_id, source_revision),
            ).fetchall()
        return [dict(row) for row in rows]

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
                external_api_confirmed, run_id, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                config.run_id,
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
                   external_api_confirmed, run_id
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
            row["run_id"],
        )
        requested = (
            config.runtime_version.value,
            config.permission_profile.value,
            config.model_connection_id,
            config.model_connection_version,
            config.model_connection_model,
            config.external_api_confirmed,
            config.run_id,
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
            if run_id is not None:
                frozen_row = conn.execute(
                    """
                    SELECT details_json FROM agentic_runtime_events
                    WHERE user_id=? AND task_id=? AND revision=?
                      AND event_type='kernel.binding.frozen'
                    ORDER BY sequence LIMIT 1
                    """,
                    (user_id, task_id, revision),
                ).fetchone()
                if frozen_row is not None:
                    frozen_details = json.loads(
                        frozen_row["details_json"] or "{}"
                    )
                    frozen_run_id = frozen_details.get("binding", {}).get(
                        "external_run_id"
                    )
                    if frozen_run_id != run_id:
                        # Run ID 是 RuntimeBinding 的一部分，冻结后只能写回同值。
                        raise ValueError("冻结 RuntimeBinding 后 Run ID 不可修改")
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
        event_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_event_id = event_id or f"agent_event_{uuid.uuid4().hex[:16]}"
        payload = details or {}
        created_at = _now()
        with _LOCK, self._conn() as conn:
            cursor = conn.execute(
                f"""
                {"INSERT OR IGNORE" if event_id is not None else "INSERT"}
                INTO agentic_runtime_events (
                    event_id, user_id, task_id, revision, event_type,
                    summary, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resolved_event_id,
                    user_id,
                    task_id,
                    revision,
                    event_type,
                    summary,
                    json.dumps(payload, ensure_ascii=False),
                    created_at,
                ),
            )
            inserted = cursor.rowcount == 1
            if inserted:
                sequence = int(cursor.lastrowid)
            else:
                existing = conn.execute(
                    """
                    SELECT sequence, user_id, task_id, revision, event_type,
                           summary, details_json, created_at
                    FROM agentic_runtime_events WHERE event_id=?
                    """,
                    (resolved_event_id,),
                ).fetchone()
                if existing is None or (
                    existing["user_id"],
                    existing["task_id"],
                    existing["revision"],
                    existing["event_type"],
                    existing["summary"],
                    json.loads(existing["details_json"] or "{}"),
                ) != (
                    user_id,
                    task_id,
                    revision,
                    event_type,
                    summary,
                    payload,
                ):
                    raise ValueError("Runtime 事件身份发生冲突")
                sequence = int(existing["sequence"])
                created_at = str(existing["created_at"])
        return {
            "sequence": sequence,
            "event_id": resolved_event_id,
            "event_type": event_type,
            "summary": summary,
            "details": payload,
            "created_at": created_at,
            "inserted": inserted,
        }

    def freeze_runtime_binding(
        self,
        user_id: str,
        task_id: str,
        revision: int,
        *,
        run_id: str,
        binding: dict[str, Any],
        capability_manifest: dict[str, Any],
        adopted_existing_run: bool,
        preallocated_run: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        """原子冻结 Run ID 与绑定事件；同一 revision 不允许改绑。"""

        if connection is None:
            with _LOCK, self._conn() as own_connection:
                return self.freeze_runtime_binding(
                    user_id,
                    task_id,
                    revision,
                    run_id=run_id,
                    binding=binding,
                    capability_manifest=capability_manifest,
                    adopted_existing_run=adopted_existing_run,
                    preallocated_run=preallocated_run,
                    connection=own_connection,
                )
        event_id = f"agent_event_{uuid.uuid4().hex[:16]}"
        created_at = _now()
        details = {
            "binding": binding,
            "capability_manifest": capability_manifest,
            "adopted_existing_run": adopted_existing_run,
        }
        if preallocated_run:
            details["preallocated_run"] = True
        row = connection.execute(
            """
            SELECT run_id FROM agentic_runtime_runs
            WHERE user_id=? AND task_id=? AND revision=?
            """,
            (user_id, task_id, revision),
        ).fetchone()
        if row is None:
            raise KeyError("Agentic Runtime 记录不存在或无权访问")
        existing = connection.execute(
            """
            SELECT sequence, event_id, event_type, summary,
                   details_json, created_at
            FROM agentic_runtime_events
            WHERE user_id=? AND task_id=? AND revision=?
              AND event_type='kernel.binding.frozen'
            ORDER BY sequence LIMIT 1
            """,
            (user_id, task_id, revision),
        ).fetchone()
        if existing is not None:
            frozen = json.loads(existing["details_json"] or "{}")
            if frozen != details:
                raise ValueError("同一 Run 的 RuntimeBinding 不可修改")
            return {
                "sequence": existing["sequence"],
                "event_id": existing["event_id"],
                "event_type": existing["event_type"],
                "summary": existing["summary"],
                "details": frozen,
                "created_at": existing["created_at"],
            }
        current_run_id = row["run_id"]
        if preallocated_run:
            if current_run_id != run_id:
                raise ValueError("预分配 Run ID 与待冻结绑定不一致")
        elif adopted_existing_run:
            if current_run_id != run_id:
                raise ValueError("历史 Run ID 与待接管绑定不一致")
        elif current_run_id is not None:
            # 新 Run 只能从未绑定状态进入；已有身份必须走显式历史接管。
            raise ValueError("Runtime revision 已存在未证明的 Run 身份")
        connection.execute(
            """
            UPDATE agentic_runtime_runs
            SET run_id=?, updated_at=?
            WHERE user_id=? AND task_id=? AND revision=?
            """,
            (run_id, created_at, user_id, task_id, revision),
        )
        summary = (
            "已在任务事务内冻结 RuntimeBinding"
            if preallocated_run
            else (
                "已接管历史运行并冻结 RuntimeBinding"
                if adopted_existing_run
                else "已冻结本次运行的 RuntimeBinding"
            )
        )
        cursor = connection.execute(
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
                "kernel.binding.frozen",
                summary,
                json.dumps(details, ensure_ascii=False),
                created_at,
            ),
        )
        sequence = int(cursor.lastrowid)
        return {
            "sequence": sequence,
            "event_id": event_id,
            "event_type": "kernel.binding.frozen",
            "summary": summary,
            "details": details,
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
