# -*- coding: utf-8 -*-
"""RuntimeRouting 的显式迁移与 SQLite Adapter。"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from filelock import FileLock

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


_MIGRATION_ID = "0001_runtime_routing"
_DDL = (
    """CREATE TABLE runtime_routing_migrations (
        migration_id TEXT PRIMARY KEY,
        ddl_sha256 TEXT NOT NULL,
        backup_sha256 TEXT NOT NULL,
        applied_at TEXT NOT NULL
    )""",
    """CREATE TABLE runtime_gate_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        payload_json TEXT NOT NULL,
        recorded_by TEXT NOT NULL,
        recorded_at TEXT NOT NULL
    )""",
    """CREATE TABLE runtime_rollout_state (
        state_id INTEGER PRIMARY KEY CHECK (state_id = 1),
        mode TEXT NOT NULL,
        p0_blocked INTEGER NOT NULL CHECK (p0_blocked IN (0, 1)),
        active_gate_snapshot_id TEXT NOT NULL,
        updated_by TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE runtime_rollout_approvals (
        approval_id TEXT PRIMARY KEY,
        payload_json TEXT NOT NULL,
        recorded_at TEXT NOT NULL
    )""",
    """CREATE TABLE runtime_rollout_events (
        event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE runtime_assignments (
        owner_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        payload_json TEXT NOT NULL,
        runtime_version TEXT NOT NULL,
        rollout_mode TEXT NOT NULL,
        gate_snapshot_id TEXT NOT NULL,
        assigned_at TEXT NOT NULL,
        PRIMARY KEY (owner_id, task_id, revision)
    )""",
    """CREATE TRIGGER runtime_gate_snapshots_no_update
        BEFORE UPDATE ON runtime_gate_snapshots BEGIN
        SELECT RAISE(ABORT, 'GateSnapshot 不可改写'); END""",
    """CREATE TRIGGER runtime_gate_snapshots_no_delete
        BEFORE DELETE ON runtime_gate_snapshots BEGIN
        SELECT RAISE(ABORT, 'GateSnapshot 不可删除'); END""",
    """CREATE TRIGGER runtime_assignments_no_update
        BEFORE UPDATE ON runtime_assignments BEGIN
        SELECT RAISE(ABORT, 'RuntimeAssignment 不可改写'); END""",
    """CREATE TRIGGER runtime_assignments_no_delete
        BEFORE DELETE ON runtime_assignments BEGIN
        SELECT RAISE(ABORT, 'RuntimeAssignment 不可删除'); END""",
    """CREATE TRIGGER runtime_rollout_events_no_update
        BEFORE UPDATE ON runtime_rollout_events BEGIN
        SELECT RAISE(ABORT, 'RolloutEvent 不可改写'); END""",
    """CREATE TRIGGER runtime_rollout_events_no_delete
        BEFORE DELETE ON runtime_rollout_events BEGIN
        SELECT RAISE(ABORT, 'RolloutEvent 不可删除'); END""",
    """CREATE TRIGGER runtime_rollout_approvals_no_update
        BEFORE UPDATE ON runtime_rollout_approvals BEGIN
        SELECT RAISE(ABORT, 'RolloutApproval 不可改写'); END""",
    """CREATE TRIGGER runtime_rollout_approvals_no_delete
        BEFORE DELETE ON runtime_rollout_approvals BEGIN
        SELECT RAISE(ABORT, 'RolloutApproval 不可删除'); END""",
    """CREATE TRIGGER runtime_routing_migrations_no_update
        BEFORE UPDATE ON runtime_routing_migrations BEGIN
        SELECT RAISE(ABORT, 'RuntimeRoutingMigration 不可改写'); END""",
    """CREATE TRIGGER runtime_routing_migrations_no_delete
        BEFORE DELETE ON runtime_routing_migrations BEGIN
        SELECT RAISE(ABORT, 'RuntimeRoutingMigration 不可删除'); END""",
)
_DDL_SHA256 = hashlib.sha256("\n".join(_DDL).encode("utf-8")).hexdigest()
_REQUIRED_OBJECTS = {
    "runtime_routing_migrations",
    "runtime_gate_snapshots",
    "runtime_rollout_state",
    "runtime_rollout_approvals",
    "runtime_rollout_events",
    "runtime_assignments",
    "runtime_gate_snapshots_no_update",
    "runtime_gate_snapshots_no_delete",
    "runtime_assignments_no_update",
    "runtime_assignments_no_delete",
    "runtime_rollout_events_no_update",
    "runtime_rollout_events_no_delete",
    "runtime_rollout_approvals_no_update",
    "runtime_rollout_approvals_no_delete",
    "runtime_routing_migrations_no_update",
    "runtime_routing_migrations_no_delete",
}
_G3_TABLES = {
    "runtime_routing_migrations",
    "runtime_gate_snapshots",
    "runtime_rollout_state",
    "runtime_rollout_approvals",
    "runtime_rollout_events",
    "runtime_assignments",
}
_DDL_OBJECT_NAMES = (
    "runtime_routing_migrations",
    "runtime_gate_snapshots",
    "runtime_rollout_state",
    "runtime_rollout_approvals",
    "runtime_rollout_events",
    "runtime_assignments",
    "runtime_gate_snapshots_no_update",
    "runtime_gate_snapshots_no_delete",
    "runtime_assignments_no_update",
    "runtime_assignments_no_delete",
    "runtime_rollout_events_no_update",
    "runtime_rollout_events_no_delete",
    "runtime_rollout_approvals_no_update",
    "runtime_rollout_approvals_no_delete",
    "runtime_routing_migrations_no_update",
    "runtime_routing_migrations_no_delete",
)


def _normalize_sql(value: str | None) -> str:
    return " ".join((value or "").split())


_EXPECTED_OBJECT_SQL = {
    name: _normalize_sql(statement)
    for name, statement in zip(_DDL_OBJECT_NAMES, _DDL, strict=True)
}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integrity_ok(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
            return connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    except sqlite3.DatabaseError:
        return False


def _schema_valid_connection(connection: sqlite3.Connection) -> bool:
    schema = {
        row[0]: _normalize_sql(row[1])
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE name LIKE 'runtime_%'"
        )
    }
    if any(
        schema.get(name) != expected
        for name, expected in _EXPECTED_OBJECT_SQL.items()
    ):
        return False
    row = connection.execute(
        "SELECT ddl_sha256, backup_sha256 FROM runtime_routing_migrations "
        "WHERE migration_id=?",
        (_MIGRATION_ID,),
    ).fetchone()
    state = connection.execute(
        "SELECT mode, p0_blocked, active_gate_snapshot_id "
        "FROM runtime_rollout_state WHERE state_id=1"
    ).fetchone()
    return (
        row is not None
        and row[0] == _DDL_SHA256
        and len(row[1]) == 64
        and state is not None
        and state[0] in {item.value for item in RolloutMode}
        and state[1] in {0, 1}
        and len(state[2]) == 64
    )


def _schema_valid(path: Path) -> bool:
    if not _integrity_ok(path):
        return False
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
            return _schema_valid_connection(connection)
    except sqlite3.DatabaseError:
        return False


def _routing_objects_exist(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
            placeholders = ",".join("?" for _ in _REQUIRED_OBJECTS)
            return connection.execute(
                f"SELECT 1 FROM sqlite_master WHERE name IN ({placeholders}) LIMIT 1",
                tuple(sorted(_REQUIRED_OBJECTS)),
            ).fetchone() is not None
    except sqlite3.DatabaseError:
        return True


def _logical_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
        object_placeholders = ",".join("?" for _ in _REQUIRED_OBJECTS)
        table_placeholders = ",".join("?" for _ in _G3_TABLES)
        schema = connection.execute(
            "SELECT type, name, tbl_name, COALESCE(sql, '') FROM sqlite_master "
            f"WHERE name NOT LIKE 'sqlite_%' AND name NOT IN ({object_placeholders}) "
            f"AND tbl_name NOT IN ({table_placeholders}) ORDER BY type, name",
            tuple(sorted(_REQUIRED_OBJECTS)) + tuple(sorted(_G3_TABLES)),
        ).fetchall()
        for row in schema:
            digest.update(repr(tuple(row)).encode("utf-8") + b"\n")
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            f"AND name NOT LIKE 'sqlite_%' AND name NOT IN ({table_placeholders}) "
            "ORDER BY name",
            tuple(sorted(_G3_TABLES)),
        ).fetchall()
        for (table_name,) in tables:
            quoted = '"' + table_name.replace('"', '""') + '"'
            rows = connection.execute(f"SELECT * FROM {quoted}").fetchall()
            digest.update(f"table:{table_name}\n".encode("utf-8"))
            for row in sorted(repr(tuple(item)) for item in rows):
                digest.update(row.encode("utf-8") + b"\n")
    return digest.hexdigest()


def migrate_runtime_routing(db_path: str | Path, backup_path: str | Path) -> Path:
    """创建一致性恢复点后，纯新增 G3 Schema；绝不隐式迁移。"""

    database = Path(db_path).expanduser().resolve()
    backup = Path(backup_path).expanduser().resolve()
    if not database.is_file():
        raise FileNotFoundError("RuntimeRouting 迁移源数据库不存在")
    if database == backup:
        raise ValueError("RuntimeRouting 迁移备份不能覆盖源数据库")
    with FileLock(str(database) + ".g3-migration.lock", timeout=30):
        if not _integrity_ok(database):
            raise RuntimeError("RuntimeRouting 迁移源数据库完整性检查失败")
        if _schema_valid(database):
            if not _integrity_ok(backup):
                raise RuntimeError("RuntimeRouting 首次恢复点不存在或无效")
            with closing(
                sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            ) as connection:
                stored = connection.execute(
                    "SELECT backup_sha256 FROM runtime_routing_migrations "
                    "WHERE migration_id=?",
                    (_MIGRATION_ID,),
                ).fetchone()[0]
            if stored != _file_sha256(backup):
                raise RuntimeError("RuntimeRouting 恢复点与迁移记录不一致")
            return backup
        if _routing_objects_exist(database):
            raise RuntimeError("RuntimeRouting 检测到不完整或变形 Schema")
        now = datetime.now(timezone.utc).isoformat()
        with closing(sqlite3.connect(database, timeout=30)) as connection:
            try:
                # 写锁覆盖一致性备份和 DDL，避免业务写入落入恢复缺口。
                connection.execute("BEGIN IMMEDIATE")
                if _routing_objects_exist(database):
                    raise RuntimeError("RuntimeRouting 迁移期间检测到 Schema 冲突")
                source_fingerprint = _logical_fingerprint(database)
                if backup.exists():
                    if (
                        not _integrity_ok(backup)
                        or _routing_objects_exist(backup)
                        or source_fingerprint != _logical_fingerprint(backup)
                    ):
                        raise RuntimeError("RuntimeRouting 迁移恢复点与源数据库不一致")
                else:
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    temporary = backup.with_name(f".{backup.name}.{os.getpid()}.tmp")
                    if temporary.exists():
                        raise FileExistsError("RuntimeRouting 临时恢复点已存在")
                    try:
                        # Backup API 使用独立只读连接；写锁由上面的连接持续持有。
                        with closing(sqlite3.connect(database, timeout=30)) as source:
                            with closing(
                                sqlite3.connect(temporary, timeout=30)
                            ) as destination:
                                source.backup(destination)
                        if (
                            not _integrity_ok(temporary)
                            or source_fingerprint != _logical_fingerprint(temporary)
                        ):
                            raise RuntimeError("RuntimeRouting 恢复点校验失败")
                        os.replace(temporary, backup)
                    finally:
                        temporary.unlink(missing_ok=True)
                for statement in _DDL:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO runtime_rollout_state VALUES (1, ?, 0, ?, ?, ?)",
                    (RolloutMode.ADMIN_GRAY.value, "0" * 64, "migration", now),
                )
                connection.execute(
                    "INSERT INTO runtime_routing_migrations VALUES (?, ?, ?, ?)",
                    (_MIGRATION_ID, _DDL_SHA256, _file_sha256(backup), now),
                )
                if not _schema_valid_connection(connection):
                    raise RuntimeError("RuntimeRouting 迁移后的 Schema 校验失败")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        if not _schema_valid(database):
            raise RuntimeError("RuntimeRouting 迁移后的 Schema 校验失败")
    return backup


class SqliteRuntimeRoutingRepository:
    def __init__(self, db_path: str | Path) -> None:
        database = Path(db_path).expanduser().resolve()
        if not _schema_valid(database):
            raise RuntimeError("RuntimeRouting 数据库尚未执行带备份迁移")
        self._db_path = str(database)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=30)
        connection.row_factory = sqlite3.Row
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
                "INSERT INTO runtime_gate_snapshots VALUES (?, ?, ?, ?)",
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
                "INSERT OR IGNORE INTO runtime_rollout_events VALUES (?, ?, ?, ?, ?)",
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
                "INSERT INTO runtime_rollout_events VALUES (?, ?, ?, ?, ?)",
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
            "INSERT INTO runtime_assignments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
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
                "INSERT INTO runtime_rollout_approvals VALUES (?, ?, ?)",
                (approval.approval_id, approval.model_dump_json(), now),
            )
            connection.execute(
                "INSERT INTO runtime_rollout_events VALUES (?, ?, ?, ?, ?)",
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
                "INSERT OR IGNORE INTO runtime_rollout_events VALUES (?, ?, ?, ?, ?)",
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
    if not _routing_objects_exist(database):
        return None
    return SqliteRuntimeRoutingRepository(database)


def runtime_routing_is_p0_blocked(db_path: str | Path) -> bool:
    repository = open_runtime_routing_repository(db_path)
    return bool(repository and repository.get_rollout().p0_blocked)
