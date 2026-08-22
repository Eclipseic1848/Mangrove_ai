# -*- coding: utf-8 -*-
"""能力获取记录的前向 SQLite Adapter。"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sqlite3

from filelock import FileLock

from .models import (
    AcquisitionEvent,
    AcquisitionRecord,
    AcquisitionRequest,
    AcquisitionResult,
)
from src.conversation_steering import AcquisitionStatus


_DDL = (
    Path(__file__).parent / "migrations" / "0001_acquisition_runs.sql"
).read_text(encoding="utf-8")
_ACQUISITION_TABLE = "capability_acquisition_runs"
_ACQUISITION_INDEX = "idx_capability_acquisition_owner_status"
_MIGRATION_TABLE = "capability_acquisition_migrations"
_MIGRATION_ID = "0001_acquisition_runs"
_EXPECTED_TABLE_SQL = (
    "CREATE TABLE capability_acquisition_runs ( acquisition_id TEXT PRIMARY KEY, "
    "owner_id TEXT NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL, "
    "updated_at TEXT NOT NULL )"
)
_EXPECTED_INDEX_SQL = (
    "CREATE INDEX idx_capability_acquisition_owner_status ON "
    "capability_acquisition_runs(owner_id, status)"
)
_EXPECTED_MIGRATION_SQL = (
    "CREATE TABLE capability_acquisition_migrations ( migration_id TEXT PRIMARY KEY, "
    "backup_sha256 TEXT NOT NULL, applied_at TEXT NOT NULL )"
)


def _normalize_schema_sql(sql: str | None) -> str:
    return " ".join((sql or "").split())


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint_value(value: object) -> str:
    if value is None:
        return "null:"
    if isinstance(value, bytes):
        return "bytes:" + value.hex()
    if isinstance(value, float):
        return "float:" + value.hex()
    return f"{type(value).__name__}:{value}"


def _pre_migration_fingerprint_connection(
    connection: sqlite3.Connection,
) -> str:
    digest = hashlib.sha256()
    schema_rows = connection.execute(
        "SELECT type, name, tbl_name, COALESCE(sql, '') "
        "FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
        "AND name NOT IN (?, ?, ?) AND tbl_name NOT IN (?, ?) "
        "ORDER BY type, name",
        (
            _ACQUISITION_TABLE,
            _ACQUISITION_INDEX,
            _MIGRATION_TABLE,
            _ACQUISITION_TABLE,
            _MIGRATION_TABLE,
        ),
    ).fetchall()
    for row in schema_rows:
        digest.update(repr(tuple(row)).encode("utf-8"))
        digest.update(b"\n")
    table_names = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' AND name NOT IN (?, ?) ORDER BY name",
        (_ACQUISITION_TABLE, _MIGRATION_TABLE),
    ).fetchall()
    for (table_name,) in table_names:
        quoted_table = '"' + table_name.replace('"', '""') + '"'
        rows = connection.execute(f"SELECT * FROM {quoted_table}").fetchall()
        encoded_rows = sorted(
            "\x1f".join(_fingerprint_value(value) for value in row)
            for row in rows
        )
        digest.update(f"table:{table_name}\n".encode("utf-8"))
        for row in encoded_rows:
            digest.update(row.encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def _pre_migration_fingerprint(database: Path) -> str:
    """摘要 AC-05 之外的全部 Schema 与数据，用于绑定首次恢复点。"""

    with closing(
        sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    ) as connection:
        return _pre_migration_fingerprint_connection(connection)


def _acquisition_schema_is_valid(connection: sqlite3.Connection) -> bool:
    columns = connection.execute(
        f"PRAGMA table_info({_ACQUISITION_TABLE})"
    ).fetchall()
    indexes = connection.execute(
        f"PRAGMA index_list({_ACQUISITION_TABLE})"
    ).fetchall()
    index_columns = connection.execute(
        f"PRAGMA index_info({_ACQUISITION_INDEX})"
    ).fetchall()
    schema_sql = dict(
        connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE name IN (?, ?, ?)",
            (_ACQUISITION_TABLE, _ACQUISITION_INDEX, _MIGRATION_TABLE),
        ).fetchall()
    )
    migration_rows = connection.execute(
        f"SELECT migration_id, backup_sha256 FROM {_MIGRATION_TABLE}"
    ).fetchall() if _MIGRATION_TABLE in schema_sql else []
    expected_columns = [
        (0, "acquisition_id", "TEXT", 0, None, 1),
        (1, "owner_id", "TEXT", 1, None, 0),
        (2, "status", "TEXT", 1, None, 0),
        (3, "payload_json", "TEXT", 1, None, 0),
        (4, "updated_at", "TEXT", 1, None, 0),
    ]
    named_index = next(
        (row for row in indexes if row[1] == _ACQUISITION_INDEX),
        None,
    )
    return (
        columns == expected_columns
        and named_index is not None
        and named_index[2:] == (0, "c", 0)
        and index_columns == [(0, 1, "owner_id"), (1, 2, "status")]
        and _normalize_schema_sql(schema_sql.get(_ACQUISITION_TABLE))
        == _EXPECTED_TABLE_SQL
        and _normalize_schema_sql(schema_sql.get(_ACQUISITION_INDEX))
        == _EXPECTED_INDEX_SQL
        and _normalize_schema_sql(schema_sql.get(_MIGRATION_TABLE))
        == _EXPECTED_MIGRATION_SQL
        and len(migration_rows) == 1
        and migration_rows[0][0] == _MIGRATION_ID
        and len(migration_rows[0][1]) == 64
    )


def _acquisition_schema_exists(database: Path) -> bool:
    if not database.is_file():
        return False
    try:
        with closing(
            sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        ) as connection:
            return _acquisition_schema_is_valid(connection)
    except sqlite3.DatabaseError:
        return False


def _acquisition_objects_exist(database: Path) -> bool:
    if not database.is_file():
        return False
    try:
        with closing(
            sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        ) as connection:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name IN (?, ?, ?) "
                "OR tbl_name IN (?, ?) LIMIT 1",
                (
                    _ACQUISITION_TABLE,
                    _ACQUISITION_INDEX,
                    _MIGRATION_TABLE,
                    _ACQUISITION_TABLE,
                    _MIGRATION_TABLE,
                ),
            ).fetchone()
    except sqlite3.DatabaseError:
        return True
    return row is not None


def _database_integrity_ok(database: Path) -> bool:
    if not database.is_file():
        return False
    try:
        with closing(
            sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        ) as connection:
            return connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    except sqlite3.DatabaseError:
        return False


def migrate_capability_acquisition(
    db_path: str | Path,
    backup_path: str | Path,
) -> Path:
    """先创建一致性备份，再执行纯新增的 AC-05 迁移。"""

    database = Path(db_path).expanduser().resolve()
    backup = Path(backup_path).expanduser().resolve()
    if not database.is_file():
        raise FileNotFoundError("能力获取迁移源数据库不存在")
    if database == backup:
        raise ValueError("能力获取迁移备份不能覆盖源数据库")
    migration_lock = FileLock(
        str(database) + ".ac05-migration.lock",
        timeout=30,
    )
    with migration_lock:
        if not _database_integrity_ok(database):
            raise RuntimeError("能力获取迁移源数据库完整性检查失败")
        schema_exists = _acquisition_schema_exists(database)
        if backup.exists():
            if (
                not _database_integrity_ok(backup)
                or _acquisition_objects_exist(backup)
            ):
                raise RuntimeError("能力获取迁移恢复点无效")
            if schema_exists:
                with closing(
                    sqlite3.connect(f"file:{database}?mode=ro", uri=True)
                ) as source:
                    stored_digest = source.execute(
                        f"SELECT backup_sha256 FROM {_MIGRATION_TABLE} "
                        "WHERE migration_id=?",
                        (_MIGRATION_ID,),
                    ).fetchone()[0]
                if stored_digest != _file_sha256(backup):
                    raise RuntimeError("能力获取迁移恢复点与源数据库不一致")
                # 同一路径重放只返回首次恢复点，绝不覆盖迁移前备份。
                return backup
        elif schema_exists or _acquisition_objects_exist(database):
            raise RuntimeError("能力获取迁移已开始，但首次恢复点不匹配")

        with closing(sqlite3.connect(database, timeout=30)) as source:
            try:
                # 写锁覆盖备份和 DDL，业务写入不能落入两者之间的恢复缺口。
                source.execute("BEGIN IMMEDIATE")
                if _acquisition_objects_exist(database):
                    raise RuntimeError("能力获取迁移检测到不完整 Schema")
                source_fingerprint = _pre_migration_fingerprint_connection(source)
                if backup.exists():
                    if source_fingerprint != _pre_migration_fingerprint(backup):
                        raise RuntimeError("能力获取迁移恢复点与源数据库不一致")
                else:
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    temporary_backup = backup.with_name(
                        f".{backup.name}.{os.getpid()}.tmp"
                    )
                    if temporary_backup.exists():
                        raise FileExistsError("能力获取迁移临时备份已存在")
                    try:
                        with closing(
                            sqlite3.connect(database, timeout=30)
                        ) as backup_source:
                            with closing(
                                sqlite3.connect(temporary_backup, timeout=30)
                            ) as destination:
                                backup_source.backup(destination)
                        if (
                            not _database_integrity_ok(temporary_backup)
                            or source_fingerprint
                            != _pre_migration_fingerprint(temporary_backup)
                        ):
                            raise RuntimeError("能力获取迁移备份与源数据库不一致")
                        os.replace(temporary_backup, backup)
                    finally:
                        temporary_backup.unlink(missing_ok=True)
                for statement in _DDL.split(";"):
                    if statement.strip():
                        source.execute(statement)
                source.execute(
                    f"INSERT INTO {_MIGRATION_TABLE} "
                    "(migration_id, backup_sha256, applied_at) VALUES (?, ?, ?)",
                    (
                        _MIGRATION_ID,
                        _file_sha256(backup),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                if not _acquisition_schema_is_valid(source):
                    raise RuntimeError("能力获取迁移后的 Schema 无效")
                source.commit()
            except Exception:
                source.rollback()
                raise
    return backup


class SqliteAcquisitionRepository:
    def __init__(self, db_path: str | Path) -> None:
        database = Path(db_path).expanduser().resolve()
        if not _acquisition_schema_exists(database):
            # 普通读取或任务启动不能绕过生产备份门隐式修改数据库。
            raise RuntimeError("能力获取数据库尚未执行带备份迁移")
        self._db_path = str(database)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def get(self, acquisition_id: str) -> AcquisitionRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM capability_acquisition_runs "
                "WHERE acquisition_id=?",
                (acquisition_id,),
            ).fetchone()
        return (
            AcquisitionRecord.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def create(self, request: AcquisitionRequest) -> AcquisitionRecord:
        from src.conversation_steering import AcquisitionStatus

        proposed = AcquisitionRecord(
            request=request,
            status=AcquisitionStatus.DISCOVERING,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO capability_acquisition_runs "
                "(acquisition_id, owner_id, status, payload_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    request.acquisition_id,
                    request.owner_id,
                    proposed.status.value,
                    proposed.model_dump_json(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT payload_json FROM capability_acquisition_runs "
                "WHERE acquisition_id=?",
                (request.acquisition_id,),
            ).fetchone()
            assert row is not None
            saved = AcquisitionRecord.model_validate_json(row["payload_json"])
            if saved.request != request:
                raise ValueError("同一 acquisition_id 不得改写请求")
        return saved

    def save(self, record: AcquisitionRecord) -> AcquisitionRecord:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM capability_acquisition_runs "
                "WHERE acquisition_id=?",
                (record.request.acquisition_id,),
            ).fetchone()
            if row is None:
                raise ValueError("能力获取记录不存在")
            existing = AcquisitionRecord.model_validate_json(row["payload_json"])
            if existing.request != record.request:
                raise ValueError("能力获取请求身份不一致")
            if existing.status in {
                AcquisitionStatus.READY,
                AcquisitionStatus.FAILED,
                AcquisitionStatus.CANCELLED,
            } and existing != record:
                return existing
            if existing.cancel_requested and not record.cancel_requested:
                record = record.model_copy(update={"cancel_requested": True})
            connection.execute(
                "UPDATE capability_acquisition_runs "
                "SET status=?, payload_json=?, updated_at=? "
                "WHERE acquisition_id=?",
                (
                    record.status.value,
                    record.model_dump_json(),
                    datetime.now(timezone.utc).isoformat(),
                    record.request.acquisition_id,
                ),
            )
        return record

    def finalize_ready(
        self,
        record: AcquisitionRecord,
    ) -> AcquisitionRecord | None:
        """在同一 SQLite 写事务中检查取消并发布 READY。"""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM capability_acquisition_runs "
                "WHERE acquisition_id=?",
                (record.request.acquisition_id,),
            ).fetchone()
            if row is None:
                return None
            existing = AcquisitionRecord.model_validate_json(row["payload_json"])
            if (
                existing.request != record.request
                or existing.cancel_requested
            ):
                return None
            if existing.status in {
                AcquisitionStatus.READY,
                AcquisitionStatus.FAILED,
                AcquisitionStatus.CANCELLED,
            }:
                return existing if existing.status is AcquisitionStatus.READY else None
            connection.execute(
                "UPDATE capability_acquisition_runs "
                "SET status=?, payload_json=?, updated_at=? "
                "WHERE acquisition_id=?",
                (
                    record.status.value,
                    record.model_dump_json(),
                    datetime.now(timezone.utc).isoformat(),
                    record.request.acquisition_id,
                ),
            )
        return record

    def request_cancel(
        self,
        acquisition_id: str,
        owner_id: str,
    ) -> AcquisitionRecord:
        """取消检查与写入同事务完成，终态绝不被旧快照倒写。"""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM capability_acquisition_runs "
                "WHERE acquisition_id=?",
                (acquisition_id,),
            ).fetchone()
            if row is None:
                raise KeyError("能力获取记录不存在")
            record = AcquisitionRecord.model_validate_json(row["payload_json"])
            if record.request.owner_id != owner_id:
                raise PermissionError("不能取消其他用户的能力获取")
            if record.status in {
                AcquisitionStatus.READY,
                AcquisitionStatus.FAILED,
                AcquisitionStatus.CANCELLED,
            }:
                return record
            if record.status is AcquisitionStatus.AWAITING_PERMISSION:
                event = AcquisitionEvent(
                    acquisition_id=acquisition_id,
                    owner_id=owner_id,
                    sequence=len(record.events) + 1,
                    status=AcquisitionStatus.CANCELLED,
                    summary="等待权限的能力获取已取消",
                )
                result = AcquisitionResult(
                    acquisition_id=acquisition_id,
                    owner_id=owner_id,
                    status=AcquisitionStatus.CANCELLED,
                    failure_code="CANCELLED",
                )
                record = record.model_copy(
                    update={
                        "status": AcquisitionStatus.CANCELLED,
                        "events": (*record.events, event),
                        "result": result,
                        "cancel_requested": True,
                    }
                )
            else:
                record = record.model_copy(update={"cancel_requested": True})
            connection.execute(
                "UPDATE capability_acquisition_runs "
                "SET status=?, payload_json=?, updated_at=? "
                "WHERE acquisition_id=?",
                (
                    record.status.value,
                    record.model_dump_json(),
                    datetime.now(timezone.utc).isoformat(),
                    acquisition_id,
                ),
            )
        return record
