# -*- coding: utf-8 -*-
"""RuntimeRouting 的显式迁移与 SQLite Adapter。"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .models import (
    GateComparison,
    GateRecord,
    GateSnapshot,
    RolloutApproval,
    RolloutMode,
    RolloutSnapshot,
    RuntimeAssignment,
    RuntimeTaskRevisionRef,
)
from src.agentic_runtime import RuntimeVersion


def migrate_runtime_routing(db_path: str | Path, backup_path: str | Path) -> Path:
    """兼容旧调用方；所有写入统一委托中央 webui 迁移 Seam。"""

    from src.database_migrations import _apply_compatibility_adapter

    return _apply_compatibility_adapter(db_path, backup_path)


class SqliteRuntimeRoutingRepository:
    def __init__(self, db_path: str | Path) -> None:
        database = Path(db_path).expanduser().resolve()
        from src.database_migrations import DatabaseTarget, inspect_database

        inspect_database(DatabaseTarget("webui", database)).require_current()
        self._db_path = str(database)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _rollout(row: sqlite3.Row) -> RolloutSnapshot:
        return RolloutSnapshot(
            mode=RolloutMode(row["mode"]),
            p0_blocked=bool(row["p0_blocked"]),
            active_gate_snapshot_id=row["active_gate_snapshot_id"],
        )

    def get_rollout(self) -> RolloutSnapshot:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_rollout_state WHERE state_id=1"
            ).fetchone()
        assert row is not None
        return self._rollout(row)

    def apply_gate(
        self,
        snapshot: GateSnapshot,
        *,
        actor_id: str,
    ) -> tuple[GateRecord, RolloutSnapshot]:
        now = datetime.now(timezone.utc)
        payload = snapshot.model_dump_json()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute(
                "SELECT * FROM runtime_rollout_state WHERE state_id=1"
            ).fetchone()
            assert state is not None
            row = connection.execute(
                "SELECT * FROM runtime_gate_snapshots WHERE snapshot_id=?",
                (snapshot.snapshot_id,),
            ).fetchone()
            if row is not None:
                existing = GateSnapshot.model_validate_json(row["payload_json"])
                if existing != snapshot:
                    raise ValueError("GateSnapshot 身份已绑定不同内容")
                if state["active_gate_snapshot_id"] != snapshot.snapshot_id:
                    raise ValueError("历史 GateSnapshot 不得重新激活")
                return (
                    GateRecord(
                        snapshot=existing,
                        recorded_by=row["recorded_by"],
                        recorded_at=datetime.fromisoformat(row["recorded_at"]),
                    ),
                    self._rollout(state),
                )
            prior_rows = connection.execute(
                "SELECT payload_json FROM runtime_gate_snapshots"
            ).fetchall()
            required_gate_ids = {
                check.gate_id
                for prior_row in prior_rows
                for check in GateSnapshot.model_validate_json(
                    prior_row["payload_json"]
                ).checks
            }
            passed_gate_ids = {
                check.gate_id for check in snapshot.checks if check.passed
            }
            regressed_gate_ids = tuple(
                sorted(required_gate_ids - passed_gate_ids)
            )
            effective_qualified = (
                snapshot.qualified and not regressed_gate_ids
            )
            connection.execute(
                "INSERT INTO runtime_gate_snapshots "
                "(snapshot_id, payload_json, recorded_by, recorded_at) "
                "VALUES (?, ?, ?, ?)",
                (snapshot.snapshot_id, payload, actor_id, now.isoformat()),
            )
            mode = (
                RolloutMode(state["mode"])
                if effective_qualified
                else RolloutMode.LEGACY_ROLLBACK
            )
            connection.execute(
                "UPDATE runtime_rollout_state SET mode=?, p0_blocked=?, "
                "active_gate_snapshot_id=?, updated_by=?, updated_at=? "
                "WHERE state_id=1",
                (
                    mode.value,
                    int(mode is RolloutMode.LEGACY_ROLLBACK),
                    snapshot.snapshot_id,
                    actor_id,
                    now.isoformat(),
                ),
            )
            connection.execute(
                "INSERT OR IGNORE INTO runtime_rollout_events "
                "(event_id, event_type, payload_json, actor_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    "gate:" + snapshot.snapshot_id,
                    "gate_recorded",
                    json.dumps(
                        {
                            "snapshot_id": snapshot.snapshot_id,
                            "mode": mode.value,
                            "regressed_gate_ids": regressed_gate_ids,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    actor_id,
                    now.isoformat(),
                ),
            )
            connection.commit()
        return (
            GateRecord(
                snapshot=snapshot,
                recorded_by=actor_id,
                recorded_at=now,
            ),
            RolloutSnapshot(
                mode=mode,
                p0_blocked=mode is RolloutMode.LEGACY_ROLLBACK,
                active_gate_snapshot_id=snapshot.snapshot_id,
            ),
        )

    def get_gate(self, snapshot_id: str) -> GateRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_gate_snapshots WHERE snapshot_id=?",
                (snapshot_id,),
            ).fetchone()
        if row is None:
            return None
        return GateRecord(
            snapshot=GateSnapshot.model_validate_json(row["payload_json"]),
            recorded_by=row["recorded_by"],
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
        )

    def is_gate_effectively_qualified(self, snapshot_id: str) -> bool:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT snapshot_id, payload_json FROM runtime_gate_snapshots"
            ).fetchall()
        snapshots = {
            row["snapshot_id"]: GateSnapshot.model_validate_json(
                row["payload_json"]
            )
            for row in rows
        }
        snapshot = snapshots.get(snapshot_id)
        if snapshot is None:
            return False
        required_gate_ids = {
            check.gate_id
            for recorded_snapshot in snapshots.values()
            for check in recorded_snapshot.checks
        }
        passed_gate_ids = {
            check.gate_id for check in snapshot.checks if check.passed
        }
        return (
            snapshot.qualified
            and required_gate_ids.issubset(passed_gate_ids)
        )

    def record_comparison(self, comparison: GateComparison) -> GateComparison:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO runtime_rollout_events "
                "(event_id, event_type, payload_json, actor_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    comparison.comparison_id,
                    "gate_compared",
                    comparison.model_dump_json(),
                    comparison.compared_by,
                    comparison.compared_at.isoformat(),
                ),
            )
        return comparison

    def get_assignment(
        self,
        task_revision: RuntimeTaskRevisionRef,
    ) -> RuntimeAssignment | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM runtime_assignments "
                "WHERE owner_id=? AND task_id=? AND revision=?",
                (
                    task_revision.owner_id,
                    task_revision.task_id,
                    task_revision.revision,
                ),
            ).fetchone()
        return (
            RuntimeAssignment.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def has_legacy_assignment(self, *, owner_id: str, task_id: str) -> bool:
        with self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM runtime_assignments WHERE owner_id=? AND task_id=? "
                "AND runtime_version=? LIMIT 1",
                (owner_id, task_id, RuntimeVersion.LEGACY.value),
            ).fetchone() is not None

    def create_assignment(self, assignment: RuntimeAssignment) -> RuntimeAssignment:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = self.create_assignment_in_transaction(connection, assignment)
            connection.commit()
        return result

    def create_assignment_in_transaction(
        self,
        connection: sqlite3.Connection,
        assignment: RuntimeAssignment,
    ) -> RuntimeAssignment:
        """在调用方事务内绑定 assignment；本方法不得自行提交。"""

        ref = assignment.task_revision
        row = connection.execute(
            "SELECT payload_json FROM runtime_assignments "
            "WHERE owner_id=? AND task_id=? AND revision=?",
            (ref.owner_id, ref.task_id, ref.revision),
        ).fetchone()
        if row is not None:
            existing = RuntimeAssignment.model_validate_json(row["payload_json"])
            if existing.task_revision != assignment.task_revision:
                raise ValueError("同一任务修订不得改写 requested_runtime")
            if (
                existing.runtime_version is not assignment.runtime_version
                or existing.rollout_mode is not assignment.rollout_mode
                or existing.gate_snapshot_id != assignment.gate_snapshot_id
            ):
                raise RuntimeError("任务修订已绑定其他 Rollout")
            return existing
        state = connection.execute(
            "SELECT mode, active_gate_snapshot_id FROM runtime_rollout_state "
            "WHERE state_id=1"
        ).fetchone()
        assert state is not None
        if (
            state["mode"] != assignment.rollout_mode.value
            or state["active_gate_snapshot_id"] != assignment.gate_snapshot_id
        ):
            raise RuntimeError("Rollout 已并发变化")
        connection.execute(
            "INSERT INTO runtime_assignments "
            "(owner_id, task_id, revision, payload_json, runtime_version, "
            "rollout_mode, gate_snapshot_id, assigned_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ref.owner_id,
                ref.task_id,
                ref.revision,
                assignment.model_dump_json(),
                assignment.runtime_version.value,
                assignment.rollout_mode.value,
                assignment.gate_snapshot_id,
                assignment.assigned_at.isoformat(),
            ),
        )
        return assignment

    def get_approval(self, approval_id: str) -> RolloutApproval | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM runtime_rollout_approvals "
                "WHERE approval_id=?",
                (approval_id,),
            ).fetchone()
        return (
            RolloutApproval.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def record_approval(
        self,
        approval: RolloutApproval,
        *,
        actor_id: str,
    ) -> RolloutApproval:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM runtime_rollout_approvals "
                "WHERE approval_id=?",
                (approval.approval_id,),
            ).fetchone()
            if row is not None:
                existing = RolloutApproval.model_validate_json(row["payload_json"])
                if existing != approval:
                    raise ValueError("同一 approval_id 不得绑定不同授权")
                return existing
            connection.execute(
                "INSERT INTO runtime_rollout_approvals "
                "(approval_id, payload_json, recorded_at) VALUES (?, ?, ?)",
                (approval.approval_id, approval.model_dump_json(), now),
            )
            connection.execute(
                "INSERT INTO runtime_rollout_events "
                "(event_id, event_type, payload_json, actor_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    "approval-recorded:" + approval.approval_id,
                    "approval_recorded",
                    approval.model_dump_json(),
                    actor_id,
                    now,
                ),
            )
            connection.commit()
        return approval

    def change_rollout(
        self,
        *,
        expected_mode: RolloutMode,
        target_mode: RolloutMode,
        snapshot_id: str,
        approval: RolloutApproval,
        actor_id: str,
    ) -> RolloutSnapshot:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM runtime_rollout_approvals "
                "WHERE approval_id=?",
                (approval.approval_id,),
            ).fetchone()
            if row is None or RolloutApproval.model_validate_json(
                row["payload_json"]
            ) != approval:
                raise PermissionError("Rollout 授权尚未由确认人独立记录")
            state = connection.execute(
                "SELECT mode, active_gate_snapshot_id FROM runtime_rollout_state "
                "WHERE state_id=1"
            ).fetchone()
            assert state is not None
            if state["mode"] != expected_mode.value:
                raise RuntimeError("Rollout 已并发变化")
            if state["active_gate_snapshot_id"] != snapshot_id:
                raise RuntimeError("GateSnapshot 已并发变化")
            connection.execute(
                "UPDATE runtime_rollout_state SET mode=?, p0_blocked=?, updated_by=?, "
                "updated_at=? WHERE state_id=1",
                (
                    target_mode.value,
                    int(target_mode is RolloutMode.LEGACY_ROLLBACK),
                    actor_id,
                    now,
                ),
            )
            connection.execute(
                "INSERT OR IGNORE INTO runtime_rollout_events "
                "(event_id, event_type, payload_json, actor_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    "approval:" + approval.approval_id,
                    "mode_changed",
                    json.dumps(
                        {
                            "from": expected_mode.value,
                            "to": target_mode.value,
                            "snapshot_id": snapshot_id,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    actor_id,
                    now,
                ),
            )
            connection.commit()
        return RolloutSnapshot(
            mode=target_mode,
            p0_blocked=target_mode is RolloutMode.LEGACY_ROLLBACK,
            active_gate_snapshot_id=snapshot_id,
        )


def open_runtime_routing_repository(
    db_path: str | Path,
) -> SqliteRuntimeRoutingRepository | None:
    """迁移前保持旧路径；一旦出现 G3 对象，任何变形都失败关闭。"""

    database = Path(db_path).expanduser().resolve()
    if not database.is_file():
        return None
    with closing(
        sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=5)
    ) as connection:
        routing_object = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name IN ("
            "'runtime_routing_migrations', 'runtime_gate_snapshots', "
            "'runtime_rollout_state', 'runtime_rollout_approvals', "
            "'runtime_rollout_events', 'runtime_assignments') LIMIT 1"
        ).fetchone()
    if routing_object is None:
        return None
    return SqliteRuntimeRoutingRepository(database)


def runtime_routing_is_p0_blocked(db_path: str | Path) -> bool:
    repository = open_runtime_routing_repository(db_path)
    return bool(repository and repository.get_rollout().p0_blocked)
