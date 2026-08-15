# -*- coding: utf-8 -*-
"""能力治理事件的显式迁移与 SQLite Adapter。"""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

from .models import (
    CapabilityGovernanceEvent,
    CapabilityGovernanceTarget,
    CapabilityValidationRun,
    CapabilitySupplyChainEvidence,
    PlatformValidationRun,
    ValidationRunStatus,
)


_MIGRATIONS = Path(__file__).parent / "migrations"
_DDL = "\n".join(
    path.read_text(encoding="utf-8")
    for path in sorted(_MIGRATIONS.glob("*.sql"))
    # 0004 依赖迁移入口先幂等补充 event_type 列，因此单独执行，不参与基础拼接。
    if path.name != "0004_promotion_gate.sql"
)
_PROMOTION_GATE_DDL = (
    _MIGRATIONS / "0004_promotion_gate.sql"
).read_text(encoding="utf-8")


def _ensure_promotion_gate(connection: sqlite3.Connection) -> None:
    """幂等补充晋级门列与部分唯一索引；旧库升级与全量重放均可安全执行。"""

    columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(capability_governance_events)"
        )
    }
    if "event_type" not in columns:
        # 默认值保证旧行自动视为 registered，历史事实零改写。
        connection.execute(
            "ALTER TABLE capability_governance_events "
            "ADD COLUMN event_type TEXT NOT NULL DEFAULT 'registered'"
        )
    connection.executescript(_PROMOTION_GATE_DDL)


def _validation_request_hash(run: CapabilityValidationRun) -> str:
    payload = json.dumps(
        {
            "target": run.target.model_dump(mode="json"),
            "task_ref": run.task_ref.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _owner_key(target: CapabilityGovernanceTarget) -> str:
    return target.owner_id or "__platform__"


def _governance_schema_exists(database: Path) -> bool:
    if not database.is_file():
        return False
    with sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro",
        uri=True,
        timeout=30,
    ) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('capability_governance_events', "
            "'capability_validation_runs', 'capability_validation_leases', "
            "'capability_validation_idempotency', "
            "'capability_supply_chain_evidence', "
            "'capability_platform_validation_runs', "
            "'capability_platform_validation_leases')"
        ).fetchall()
    return {row[0] for row in rows} == {
        "capability_governance_events",
        "capability_validation_runs",
        "capability_validation_leases",
        "capability_validation_idempotency",
        "capability_supply_chain_evidence",
        "capability_platform_validation_runs",
        "capability_platform_validation_leases",
    }


def migrate_capability_governance(
    db_path: str | Path,
    backup_path: str | Path,
) -> Path:
    """先创建一致性备份，再执行纯新增、可重复的治理迁移。"""

    database = Path(db_path).expanduser().resolve()
    backup = Path(backup_path).expanduser().resolve()
    if database == backup:
        raise ValueError("能力治理迁移备份不能覆盖源数据库")
    if backup.exists():
        if _governance_schema_exists(database):
            # 同一路径重放只确认迁移已完成并返回原备份，绝不覆盖首次迁移前的恢复点。
            return backup
        raise FileExistsError("能力治理迁移备份已存在，拒绝覆盖")
    database.parent.mkdir(parents=True, exist_ok=True)
    backup.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database, timeout=30) as source:
        with sqlite3.connect(backup, timeout=30) as destination:
            source.backup(destination)
        # 只有备份完整关闭后才触碰源库，迁移脚本本身仅包含新增 DDL。
        source.executescript(_DDL)
        _ensure_promotion_gate(source)
    return backup


class SqliteCapabilityGovernanceRepository:
    """读取不隐式写库；真实迁移必须先调用显式备份入口。"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _schema_exists(connection: sqlite3.Connection) -> bool:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='capability_governance_events'"
        ).fetchone()
        return row is not None

    def save_event(
        self,
        event: CapabilityGovernanceEvent,
    ) -> CapabilityGovernanceEvent:
        if event.event_type != "registered":
            # 晋级事件只能走专用入口，防止把 promoted 事实落成默认 registered 行。
            raise ValueError("通用事件入口只接受能力登记事件")
        target = event.target
        with self._connect() as connection:
            if not self._schema_exists(connection):
                raise RuntimeError("能力治理数据库尚未执行带备份迁移")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO capability_governance_events "
                "(event_id, owner_key, scope, pack_id, version, digest, "
                "idempotency_key, payload_json, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    _owner_key(target),
                    target.scope.value,
                    target.pack_id,
                    target.version,
                    target.digest,
                    event.idempotency_key,
                    event.model_dump_json(),
                    event.occurred_at.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT payload_json FROM capability_governance_events "
                "WHERE owner_key=? AND pack_id=? AND version=? AND digest=? "
                "AND idempotency_key=?",
                (
                    _owner_key(target),
                    target.pack_id,
                    target.version,
                    target.digest,
                    event.idempotency_key,
                ),
            ).fetchone()
            assert row is not None
            return CapabilityGovernanceEvent.model_validate_json(
                row["payload_json"]
            )

    def get_by_idempotency(
        self,
        target: CapabilityGovernanceTarget,
        idempotency_key: str,
    ) -> CapabilityGovernanceEvent | None:
        with self._connect() as connection:
            if not self._schema_exists(connection):
                return None
            row = connection.execute(
                "SELECT payload_json FROM capability_governance_events "
                "WHERE owner_key=? AND pack_id=? AND version=? AND digest=? "
                "AND idempotency_key=?",
                (
                    _owner_key(target),
                    target.pack_id,
                    target.version,
                    target.digest,
                    idempotency_key,
                ),
            ).fetchone()
        return (
            CapabilityGovernanceEvent.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def list_events(
        self,
        target: CapabilityGovernanceTarget | None = None,
    ) -> tuple[CapabilityGovernanceEvent, ...]:
        with self._connect() as connection:
            if not self._schema_exists(connection):
                return ()
            if target is None:
                rows = connection.execute(
                    "SELECT payload_json FROM capability_governance_events "
                    "ORDER BY occurred_at, event_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT payload_json FROM capability_governance_events "
                    "WHERE owner_key=? AND pack_id=? AND version=? AND digest=? "
                    "ORDER BY occurred_at, event_id",
                    (
                        _owner_key(target),
                        target.pack_id,
                        target.version,
                        target.digest,
                    ),
                ).fetchall()
        return tuple(
            CapabilityGovernanceEvent.model_validate_json(row["payload_json"])
            for row in rows
        )

    def create_validation_run(
        self,
        run: CapabilityValidationRun,
    ) -> CapabilityValidationRun:
        with self._connect() as connection:
            if not self._schema_exists(connection):
                raise RuntimeError("能力治理数据库尚未执行带备份迁移")
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='capability_validation_runs'"
            ).fetchone()
            if table is None:
                raise RuntimeError("能力验证数据库尚未执行带备份迁移")
            connection.execute("BEGIN IMMEDIATE")
            alias = connection.execute(
                "SELECT run_id, request_sha256 FROM capability_validation_idempotency "
                "WHERE owner_id=? AND digest=? AND idempotency_key=?",
                (run.owner_id, run.target.digest, run.idempotency_key),
            ).fetchone()
            if alias is not None:
                row = connection.execute(
                    "SELECT payload_json FROM capability_validation_runs WHERE run_id=?",
                    (alias["run_id"],),
                ).fetchone()
                assert row is not None
                existing = CapabilityValidationRun.model_validate_json(
                    row["payload_json"]
                )
                if alias["request_sha256"] != _validation_request_hash(run):
                    raise ValueError("同一验证幂等键不得改写请求")
                return existing
            active = connection.execute(
                "SELECT payload_json FROM capability_validation_runs "
                "WHERE owner_id=? AND digest=? AND status IN "
                "('queued', 'running', 'cancelling') ORDER BY created_at LIMIT 1",
                (run.owner_id, run.target.digest),
            ).fetchone()
            if active is not None:
                existing = CapabilityValidationRun.model_validate_json(
                    active["payload_json"]
                )
                connection.execute(
                    "INSERT INTO capability_validation_idempotency "
                    "(owner_id, digest, idempotency_key, run_id, request_sha256) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        run.owner_id,
                        run.target.digest,
                        run.idempotency_key,
                        existing.run_id,
                        _validation_request_hash(run),
                    ),
                )
                return existing
            connection.execute(
                "INSERT OR IGNORE INTO capability_validation_runs "
                "(run_id, owner_id, pack_id, version, digest, idempotency_key, "
                "status, payload_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.run_id,
                    run.owner_id,
                    run.target.pack_id,
                    run.target.version,
                    run.target.digest,
                    run.idempotency_key,
                    run.status.value,
                    run.model_dump_json(),
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT payload_json FROM capability_validation_runs "
                "WHERE owner_id=? AND digest=? AND idempotency_key=?",
                (run.owner_id, run.target.digest, run.idempotency_key),
            ).fetchone()
            assert row is not None
            saved = CapabilityValidationRun.model_validate_json(row["payload_json"])
            if saved.target != run.target or saved.task_ref != run.task_ref:
                raise ValueError("同一验证幂等键不得改写请求")
            connection.execute(
                "INSERT OR IGNORE INTO capability_validation_idempotency "
                "(owner_id, digest, idempotency_key, run_id, request_sha256) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    saved.owner_id,
                    saved.target.digest,
                    run.idempotency_key,
                    saved.run_id,
                    _validation_request_hash(run),
                ),
            )
            return saved

    def get_validation_run(self, run_id: str) -> CapabilityValidationRun | None:
        with self._connect() as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='capability_validation_runs'"
            ).fetchone()
            if table is None:
                return None
            row = connection.execute(
                "SELECT payload_json FROM capability_validation_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
        return (
            CapabilityValidationRun.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def list_validation_runs(self) -> tuple[CapabilityValidationRun, ...]:
        with self._connect() as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='capability_validation_runs'"
            ).fetchone()
            if table is None:
                return ()
            rows = connection.execute(
                "SELECT payload_json FROM capability_validation_runs "
                "ORDER BY created_at DESC, run_id DESC"
            ).fetchall()
        return tuple(
            CapabilityValidationRun.model_validate_json(row["payload_json"])
            for row in rows
        )

    def save_validation_run(
        self,
        run: CapabilityValidationRun,
        *,
        lease_worker_id: str | None = None,
        lease_now: datetime | None = None,
    ) -> CapabilityValidationRun:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if lease_worker_id is not None:
                lease = connection.execute(
                    "SELECT run_id, worker_id, expires_at "
                    "FROM capability_validation_leases WHERE digest=?",
                    (run.target.digest,),
                ).fetchone()
                current = lease_now or datetime.now(run.updated_at.tzinfo)
                if (
                    lease is None
                    or lease["run_id"] != run.run_id
                    or lease["worker_id"] != lease_worker_id
                    or datetime.fromisoformat(lease["expires_at"]) <= current
                ):
                    raise RuntimeError("能力验证 Lease 已失效或不属于当前 worker")
            row = connection.execute(
                "SELECT payload_json FROM capability_validation_runs WHERE run_id=?",
                (run.run_id,),
            ).fetchone()
            if row is None:
                raise ValueError("能力验证运行不存在")
            existing = CapabilityValidationRun.model_validate_json(row["payload_json"])
            if existing.target != run.target:
                raise ValueError("能力验证目标身份不一致")
            if existing.status in {
                ValidationRunStatus.SUCCEEDED,
                ValidationRunStatus.FAILED,
                ValidationRunStatus.CANCELLED,
            } and existing != run:
                return existing
            if existing.cancel_requested and not run.cancel_requested:
                run = run.model_copy(update={"cancel_requested": True})
            connection.execute(
                "UPDATE capability_validation_runs SET status=?, payload_json=?, "
                "updated_at=? WHERE run_id=?",
                (
                    run.status.value,
                    run.model_dump_json(),
                    run.updated_at.isoformat(),
                    run.run_id,
                ),
            )
        return run

    def acquire_validation_lease(
        self,
        *,
        run_id: str,
        digest: str,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        expires_at = now + timedelta(seconds=lease_seconds)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT run_id, worker_id, expires_at "
                "FROM capability_validation_leases WHERE digest=?",
                (digest,),
            ).fetchone()
            if row is not None and datetime.fromisoformat(row["expires_at"]) > now:
                return row["run_id"] == run_id and row["worker_id"] == worker_id
            connection.execute(
                "INSERT INTO capability_validation_leases "
                "(digest, run_id, worker_id, acquired_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(digest) DO UPDATE SET run_id=excluded.run_id, "
                "worker_id=excluded.worker_id, acquired_at=excluded.acquired_at, "
                "expires_at=excluded.expires_at",
                (
                    digest,
                    run_id,
                    worker_id,
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )
            return True

    def release_validation_lease(self, run_id: str, worker_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM capability_validation_leases "
                "WHERE run_id=? AND worker_id=?",
                (run_id, worker_id),
            )

    def renew_validation_lease(
        self,
        *,
        run_id: str,
        digest: str,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        expires_at = now + timedelta(seconds=lease_seconds)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE capability_validation_leases SET expires_at=? "
                "WHERE digest=? AND run_id=? AND worker_id=?",
                (expires_at.isoformat(), digest, run_id, worker_id),
            )
            return cursor.rowcount == 1

    def save_supply_chain_evidence(
        self,
        evidence: CapabilitySupplyChainEvidence,
    ) -> CapabilitySupplyChainEvidence:
        target = evidence.target
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO capability_supply_chain_evidence "
                "(evidence_id, owner_key, scope, pack_id, version, digest, "
                "status, payload_json, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    evidence.evidence_id,
                    _owner_key(target),
                    target.scope.value,
                    target.pack_id,
                    target.version,
                    target.digest,
                    evidence.status.value,
                    evidence.model_dump_json(),
                    evidence.occurred_at.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT payload_json FROM capability_supply_chain_evidence "
                "WHERE evidence_id=?",
                (evidence.evidence_id,),
            ).fetchone()
        assert row is not None
        existing = CapabilitySupplyChainEvidence.model_validate_json(
            row["payload_json"]
        )
        if existing != evidence:
            raise ValueError("供应链证据 ID 不可覆盖")
        return existing

    def get_latest_supply_chain_evidence(
        self,
        target: CapabilityGovernanceTarget,
    ) -> CapabilitySupplyChainEvidence | None:
        with self._connect() as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='capability_supply_chain_evidence'"
            ).fetchone()
            if table is None:
                return None
            row = connection.execute(
                "SELECT payload_json FROM capability_supply_chain_evidence "
                "WHERE owner_key=? AND pack_id=? AND version=? AND digest=? "
                "ORDER BY occurred_at DESC, evidence_id DESC LIMIT 1",
                (
                    _owner_key(target),
                    target.pack_id,
                    target.version,
                    target.digest,
                ),
            ).fetchone()
        return (
            CapabilitySupplyChainEvidence.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def save_promotion_event(
        self,
        event: CapabilityGovernanceEvent,
    ) -> CapabilityGovernanceEvent:
        if event.event_type != "promoted_to_verified":
            raise ValueError("晋级事件专用入口只接受 promoted_to_verified 事件")
        target = event.target
        with self._connect() as connection:
            if not self._schema_exists(connection):
                raise RuntimeError("能力治理数据库尚未执行带备份迁移")
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload_json FROM capability_governance_events "
                "WHERE owner_key=? AND pack_id=? AND version=? AND digest=? "
                "AND event_type='promoted_to_verified' "
                "ORDER BY occurred_at DESC, event_id DESC LIMIT 1",
                (
                    _owner_key(target),
                    target.pack_id,
                    target.version,
                    target.digest,
                ),
            ).fetchone()
            if existing is not None:
                # 同一 digest 至多一个晋级结果；并发后写者拿到已有事件，不覆盖。
                return CapabilityGovernanceEvent.model_validate_json(
                    existing["payload_json"]
                )
            connection.execute(
                "INSERT INTO capability_governance_events "
                "(event_id, owner_key, scope, pack_id, version, digest, "
                "idempotency_key, event_type, payload_json, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    _owner_key(target),
                    target.scope.value,
                    target.pack_id,
                    target.version,
                    target.digest,
                    event.idempotency_key,
                    "promoted_to_verified",
                    event.model_dump_json(),
                    event.occurred_at.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT payload_json FROM capability_governance_events "
                "WHERE event_id=?",
                (event.event_id,),
            ).fetchone()
        assert row is not None
        return CapabilityGovernanceEvent.model_validate_json(row["payload_json"])

    def get_latest_promotion_event(
        self,
        target: CapabilityGovernanceTarget,
    ) -> CapabilityGovernanceEvent | None:
        with self._connect() as connection:
            if not self._schema_exists(connection):
                return None
            row = connection.execute(
                "SELECT payload_json FROM capability_governance_events "
                "WHERE owner_key=? AND pack_id=? AND version=? AND digest=? "
                "AND event_type='promoted_to_verified' "
                "ORDER BY occurred_at DESC, event_id DESC LIMIT 1",
                (
                    _owner_key(target),
                    target.pack_id,
                    target.version,
                    target.digest,
                ),
            ).fetchone()
        return (
            CapabilityGovernanceEvent.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def get_latest_succeeded_validation_run(
        self,
        target: CapabilityGovernanceTarget,
    ) -> CapabilityValidationRun | None:
        with self._connect() as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='capability_validation_runs'"
            ).fetchone()
            if table is None:
                return None
            if target.owner_id is not None:
                row = connection.execute(
                    "SELECT payload_json FROM capability_validation_runs "
                    "WHERE owner_id=? AND pack_id=? AND version=? AND digest=? "
                    "AND status='succeeded' "
                    "ORDER BY updated_at DESC, run_id DESC LIMIT 1",
                    (
                        target.owner_id,
                        target.pack_id,
                        target.version,
                        target.digest,
                    ),
                ).fetchone()
            else:
                # 平台能力没有个人 Owner；验证运行的 owner 是发起验证的管理员，
                # 按能力身份（pack/version/digest）过滤而非 owner 列。
                row = connection.execute(
                    "SELECT payload_json FROM capability_validation_runs "
                    "WHERE pack_id=? AND version=? AND digest=? "
                    "AND status='succeeded' "
                    "ORDER BY updated_at DESC, run_id DESC LIMIT 1",
                    (
                        target.pack_id,
                        target.version,
                        target.digest,
                    ),
                ).fetchone()
        return (
            CapabilityValidationRun.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def save_audit_view_event(
        self,
        event: CapabilityGovernanceEvent,
    ) -> CapabilityGovernanceEvent:
        if event.event_type != "audit_viewed":
            raise ValueError("审计查看事件专用入口只接受 audit_viewed 事件")
        target = event.target
        with self._connect() as connection:
            if not self._schema_exists(connection):
                raise RuntimeError("能力治理数据库尚未执行带备份迁移")
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload_json FROM capability_governance_events "
                "WHERE owner_key=? AND pack_id=? AND version=? AND digest=? "
                "AND idempotency_key=? AND event_type='audit_viewed'",
                (
                    _owner_key(target),
                    target.pack_id,
                    target.version,
                    target.digest,
                    event.idempotency_key,
                ),
            ).fetchone()
            if existing is not None:
                # 同幂等键重试返回既有审计记录；审计不可变，不覆盖、不重复落行。
                return CapabilityGovernanceEvent.model_validate_json(
                    existing["payload_json"]
                )
            connection.execute(
                "INSERT INTO capability_governance_events "
                "(event_id, owner_key, scope, pack_id, version, digest, "
                "idempotency_key, event_type, payload_json, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    _owner_key(target),
                    target.scope.value,
                    target.pack_id,
                    target.version,
                    target.digest,
                    event.idempotency_key,
                    "audit_viewed",
                    event.model_dump_json(),
                    event.occurred_at.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT payload_json FROM capability_governance_events "
                "WHERE event_id=?",
                (event.event_id,),
            ).fetchone()
        assert row is not None
        return CapabilityGovernanceEvent.model_validate_json(row["payload_json"])

    def list_audit_view_events(
        self,
        target: CapabilityGovernanceTarget | None = None,
    ) -> tuple[CapabilityGovernanceEvent, ...]:
        with self._connect() as connection:
            if not self._schema_exists(connection):
                return ()
            if target is None:
                rows = connection.execute(
                    "SELECT payload_json FROM capability_governance_events "
                    "WHERE event_type='audit_viewed' "
                    "ORDER BY occurred_at, event_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT payload_json FROM capability_governance_events "
                    "WHERE owner_key=? AND pack_id=? AND version=? AND digest=? "
                    "AND event_type='audit_viewed' "
                    "ORDER BY occurred_at, event_id",
                    (
                        _owner_key(target),
                        target.pack_id,
                        target.version,
                        target.digest,
                    ),
                ).fetchall()
        return tuple(
            CapabilityGovernanceEvent.model_validate_json(row["payload_json"])
            for row in rows
        )

    def save_platform_event(
        self,
        event: CapabilityGovernanceEvent,
    ) -> CapabilityGovernanceEvent:
        if event.event_type not in {
            "platform_candidate",
            "platform_published",
            "audience_changed",
        }:
            raise ValueError("平台事件专用入口只接受发布类事件")
        target = event.target
        with self._connect() as connection:
            if not self._schema_exists(connection):
                raise RuntimeError("能力治理数据库尚未执行带备份迁移")
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload_json FROM capability_governance_events "
                "WHERE owner_key=? AND pack_id=? AND version=? AND digest=? "
                "AND idempotency_key=? AND event_type=?",
                (
                    _owner_key(target),
                    target.pack_id,
                    target.version,
                    target.digest,
                    event.idempotency_key,
                    event.event_type,
                ),
            ).fetchone()
            if existing is not None:
                # 同幂等键重试返回既有事件；发布事实不可变、不可覆盖。
                return CapabilityGovernanceEvent.model_validate_json(
                    existing["payload_json"]
                )
            connection.execute(
                "INSERT INTO capability_governance_events "
                "(event_id, owner_key, scope, pack_id, version, digest, "
                "idempotency_key, event_type, payload_json, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    _owner_key(target),
                    target.scope.value,
                    target.pack_id,
                    target.version,
                    target.digest,
                    event.idempotency_key,
                    event.event_type,
                    event.model_dump_json(),
                    event.occurred_at.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT payload_json FROM capability_governance_events "
                "WHERE event_id=?",
                (event.event_id,),
            ).fetchone()
        assert row is not None
        return CapabilityGovernanceEvent.model_validate_json(row["payload_json"])

    def get_latest_platform_event(
        self,
        target: CapabilityGovernanceTarget,
        event_type: str,
    ) -> CapabilityGovernanceEvent | None:
        with self._connect() as connection:
            if not self._schema_exists(connection):
                return None
            row = connection.execute(
                "SELECT payload_json FROM capability_governance_events "
                "WHERE owner_key=? AND pack_id=? AND version=? AND digest=? "
                "AND event_type=? "
                "ORDER BY occurred_at DESC, event_id DESC LIMIT 1",
                (
                    _owner_key(target),
                    target.pack_id,
                    target.version,
                    target.digest,
                    event_type,
                ),
            ).fetchone()
        return (
            CapabilityGovernanceEvent.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def list_platform_events(
        self,
        target: CapabilityGovernanceTarget,
    ) -> tuple[CapabilityGovernanceEvent, ...]:
        with self._connect() as connection:
            if not self._schema_exists(connection):
                return ()
            rows = connection.execute(
                "SELECT payload_json FROM capability_governance_events "
                "WHERE owner_key=? AND pack_id=? AND version=? AND digest=? "
                "AND event_type IN ('platform_candidate', "
                "'platform_published', 'audience_changed') "
                "ORDER BY occurred_at, event_id",
                (
                    _owner_key(target),
                    target.pack_id,
                    target.version,
                    target.digest,
                ),
            ).fetchall()
        return tuple(
            CapabilityGovernanceEvent.model_validate_json(row["payload_json"])
            for row in rows
        )

    def create_platform_validation_run(
        self,
        run: PlatformValidationRun,
    ) -> PlatformValidationRun:
        target = run.target
        with self._connect() as connection:
            if not self._schema_exists(connection):
                raise RuntimeError("能力治理数据库尚未执行带备份迁移")
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='capability_platform_validation_runs'"
            ).fetchone()
            if table is None:
                raise RuntimeError("平台验证数据库尚未执行带备份迁移")
            connection.execute("BEGIN IMMEDIATE")
            # 幂等键按能力身份（pack/version）+ 键查重；同键换 digest 是请求改写。
            row = connection.execute(
                "SELECT payload_json FROM capability_platform_validation_runs "
                "WHERE pack_id=? AND version=? AND idempotency_key=?",
                (
                    target.pack_id,
                    target.version,
                    run.idempotency_key,
                ),
            ).fetchone()
            if row is not None:
                existing = PlatformValidationRun.model_validate_json(
                    row["payload_json"]
                )
                if existing.target != target:
                    raise ValueError("同一平台验证幂等键不得改写请求")
                return existing
            connection.execute(
                "INSERT INTO capability_platform_validation_runs "
                "(run_id, pack_id, version, digest, idempotency_key, status, "
                "payload_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.run_id,
                    target.pack_id,
                    target.version,
                    target.digest,
                    run.idempotency_key,
                    run.status.value,
                    run.model_dump_json(),
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                ),
            )
            saved = connection.execute(
                "SELECT payload_json FROM capability_platform_validation_runs "
                "WHERE run_id=?",
                (run.run_id,),
            ).fetchone()
        assert saved is not None
        return PlatformValidationRun.model_validate_json(saved["payload_json"])

    def get_platform_validation_run(
        self,
        run_id: str,
    ) -> PlatformValidationRun | None:
        with self._connect() as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='capability_platform_validation_runs'"
            ).fetchone()
            if table is None:
                return None
            row = connection.execute(
                "SELECT payload_json FROM capability_platform_validation_runs "
                "WHERE run_id=?",
                (run_id,),
            ).fetchone()
        return (
            PlatformValidationRun.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def list_platform_validation_runs(self) -> tuple[PlatformValidationRun, ...]:
        with self._connect() as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='capability_platform_validation_runs'"
            ).fetchone()
            if table is None:
                return ()
            rows = connection.execute(
                "SELECT payload_json FROM capability_platform_validation_runs "
                "ORDER BY created_at DESC, run_id DESC"
            ).fetchall()
        return tuple(
            PlatformValidationRun.model_validate_json(row["payload_json"])
            for row in rows
        )

    def save_platform_validation_run(
        self,
        run: PlatformValidationRun,
    ) -> PlatformValidationRun:
        target = run.target
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM capability_platform_validation_runs "
                "WHERE run_id=?",
                (run.run_id,),
            ).fetchone()
            if row is None:
                raise ValueError("平台验证运行不存在")
            existing = PlatformValidationRun.model_validate_json(
                row["payload_json"]
            )
            if existing.target != target:
                raise ValueError("平台验证目标身份不一致")
            if existing.status in {
                ValidationRunStatus.SUCCEEDED,
                ValidationRunStatus.FAILED,
                ValidationRunStatus.CANCELLED,
            } and existing != run:
                # 终态运行只允许补充签名证据（从无到有），其他变化一律拒绝；
                # 只有 SUCCEEDED 运行有资格获得签名证据。
                signing_only = (
                    existing.status is ValidationRunStatus.SUCCEEDED
                    and existing.signing_signature_digest is None
                    and run.signing_signature_digest is not None
                    and existing.model_copy(
                        update={
                            "signing_signature_digest": (
                                run.signing_signature_digest
                            ),
                            "signing_public_key_sha256": (
                                run.signing_public_key_sha256
                            ),
                            "updated_at": run.updated_at,
                        }
                    )
                    == run
                )
                if not signing_only:
                    return existing
            connection.execute(
                "UPDATE capability_platform_validation_runs SET status=?, "
                "payload_json=?, updated_at=? WHERE run_id=?",
                (
                    run.status.value,
                    run.model_dump_json(),
                    run.updated_at.isoformat(),
                    run.run_id,
                ),
            )
        return run

    def acquire_platform_validation_lease(
        self,
        *,
        run_id: str,
        digest: str,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        expires_at = now + timedelta(seconds=lease_seconds)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT run_id, worker_id, expires_at "
                "FROM capability_platform_validation_leases WHERE digest=?",
                (digest,),
            ).fetchone()
            if row is not None and datetime.fromisoformat(row["expires_at"]) > now:
                return row["run_id"] == run_id and row["worker_id"] == worker_id
            connection.execute(
                "INSERT INTO capability_platform_validation_leases "
                "(digest, run_id, worker_id, acquired_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(digest) DO UPDATE SET run_id=excluded.run_id, "
                "worker_id=excluded.worker_id, acquired_at=excluded.acquired_at, "
                "expires_at=excluded.expires_at",
                (
                    digest,
                    run_id,
                    worker_id,
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )
            return True

    def release_platform_validation_lease(
        self,
        run_id: str,
        worker_id: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM capability_platform_validation_leases "
                "WHERE run_id=? AND worker_id=?",
                (run_id, worker_id),
            )

    def renew_platform_validation_lease(
        self,
        *,
        run_id: str,
        digest: str,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        expires_at = now + timedelta(seconds=lease_seconds)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE capability_platform_validation_leases SET expires_at=? "
                "WHERE digest=? AND run_id=? AND worker_id=?",
                (expires_at.isoformat(), digest, run_id, worker_id),
            )
            return cursor.rowcount == 1
