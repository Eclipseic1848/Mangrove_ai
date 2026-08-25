# -*- coding: utf-8 -*-
"""CandidateVerification 的 SQLite Adapter。"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from filelock import FileLock

from .models import AttemptReason, AttemptStatus, VerificationAttempt


_MIGRATION_ID = "0001_candidate_verification_attempts"
_PUBLICATION_MIGRATION_ID = "0002_delivery_publication_idempotency"
_MIGRATION_SQL = (
    Path(__file__).parent
    / "migrations"
    / "0001_candidate_verification_attempts.sql"
).read_text(encoding="utf-8")
_REQUIRED_SCHEMA_OBJECTS = {
    "candidate_verification_migrations",
    "candidate_verification_attempts",
    "idx_candidate_verification_owner_candidate",
    "uq_candidate_verification_active_candidate",
    "candidate_verification_previous_attempt_guard",
    "candidate_verification_identity_no_update",
    "candidate_verification_status_transition_guard",
    "candidate_verification_state_shape_guard",
    "candidate_verification_terminal_no_update",
    "candidate_verification_no_delete",
}


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


def _database_fingerprint_connection(connection: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    schema_rows = connection.execute(
        "SELECT type, name, tbl_name, COALESCE(sql, '') "
        "FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
        "ORDER BY type, name"
    ).fetchall()
    for row in schema_rows:
        digest.update(repr(tuple(row)).encode("utf-8"))
        digest.update(b"\n")
    table_names = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
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


def _database_fingerprint(database: Path) -> str:
    with closing(
        sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    ) as connection:
        return _database_fingerprint_connection(connection)


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


def _sql_statements(script: str) -> tuple[str, ...]:
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise RuntimeError("CandidateVerification 迁移 SQL 不完整")
    return tuple(statements)


def _normalize_schema_sql(value: str | None) -> str:
    return " ".join((value or "").split())


def _schema_signature(connection: sqlite3.Connection) -> dict[str, tuple[str, str, str]]:
    placeholders = ", ".join("?" for _ in _REQUIRED_SCHEMA_OBJECTS)
    rows = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        f"WHERE name IN ({placeholders})",
        tuple(sorted(_REQUIRED_SCHEMA_OBJECTS)),
    ).fetchall()
    return {
        row[1]: (row[0], row[2], _normalize_schema_sql(row[3]))
        for row in rows
    }


def _expected_schema_signature() -> dict[str, tuple[str, str, str]]:
    with closing(sqlite3.connect(":memory:")) as connection:
        for statement in _sql_statements(_MIGRATION_SQL):
            connection.execute(statement)
        return _schema_signature(connection)


def _legacy_candidate_set_hash(row: sqlite3.Row) -> str:
    frozen = row["verified_candidate_set_hash"]
    if (
        isinstance(frozen, str)
        and len(frozen) == 64
        and all(character in "0123456789abcdef" for character in frozen)
    ):
        return frozen
    try:
        candidates = json.loads(row["candidates_json"] or "[]")
        payload = [
            {
                "artifact_id": item["artifact_id"],
                "filename": item["filename"],
                "format": item["format"],
                "sha256": item["sha256"],
                "size_bytes": item["size_bytes"],
            }
            for item in sorted(candidates, key=lambda value: value["artifact_id"])
        ]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("legacy CandidateSet 身份不可重建") from exc
    if not payload:
        raise RuntimeError("legacy VerificationReport 缺少 CandidateSet")
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _import_legacy_attempts(connection: sqlite3.Connection) -> None:
    table_exists = connection.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type='table' AND name='agentic_runtime_runs'"
    ).fetchone()
    if table_exists is None:
        return
    required_columns = {
        "user_id",
        "task_id",
        "revision",
        "run_id",
        "candidates_json",
        "verification_json",
        "verified_candidate_set_hash",
        "model_connection_id",
        "model_connection_version",
        "model_connection_model",
        "created_at",
        "updated_at",
    }
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(agentic_runtime_runs)")
    }
    if not required_columns.issubset(columns):
        raise RuntimeError("legacy Runtime Schema 缺少候选验证迁移字段")
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT * FROM agentic_runtime_runs "
        "WHERE verification_json IS NOT NULL "
        "AND TRIM(verification_json) <> '' "
        "ORDER BY user_id, task_id, revision"
    ).fetchall()
    for row in rows:
        report_json = row["verification_json"]
        try:
            report = json.loads(report_json)
            status = report["status"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("legacy VerificationReport 无效") from exc
        if status not in {"passed", "failed", "inconclusive"}:
            raise RuntimeError("legacy VerificationReport 状态无效")
        if not row["run_id"]:
            raise RuntimeError("legacy VerificationReport 缺少 Run 身份")
        candidate_set_hash = _legacy_candidate_set_hash(row)
        report_hash = hashlib.sha256(report_json.encode("utf-8")).hexdigest()
        identity_payload = json.dumps(
            {
                "owner_id": row["user_id"],
                "task_id": row["task_id"],
                "revision": row["revision"],
                "run_id": row["run_id"],
                "candidate_set_hash": candidate_set_hash,
                "report_hash": report_hash,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        identity_hash = hashlib.sha256(identity_payload).hexdigest()
        connection.execute(
            "INSERT INTO candidate_verification_attempts ("
            "attempt_id, owner_id, task_id, revision, run_id, "
            "previous_attempt_id, reason_code, candidate_set_hash, "
            "manifest_hash, goal_contract_hash, delivery_spec_hash, "
            "ruleset_identity_status, verifier_ruleset_hash, "
            "verifier_code_commit, verifier_source_hash, "
            "verifier_execution_identity_hash, verifier_ruleset_manifest_json, "
            "actor_id, connection_id, "
            "connection_version, model_id, egress_confirmed_at, "
            "provider_attempt_id, idempotency_key, request_hash, status, "
            "report_json, report_hash, created_at, started_at, finished_at"
            ") VALUES ("
            + ", ".join("?" for _ in range(31))
            + ")",
            (
                "legacy_" + identity_hash,
                row["user_id"],
                row["task_id"],
                row["revision"],
                row["run_id"],
                None,
                "initial",
                candidate_set_hash,
                None,
                None,
                None,
                "legacy_unversioned",
                None,
                None,
                None,
                None,
                None,
                "system:legacy-migration",
                row["model_connection_id"],
                row["model_connection_version"],
                row["model_connection_model"],
                None,
                None,
                "legacy_import_" + identity_hash,
                identity_hash,
                status,
                report_json,
                report_hash,
                row["created_at"],
                None,
                row["updated_at"],
            ),
        )


def _migrate_delivery_publication_idempotency(
    connection: sqlite3.Connection,
) -> None:
    """把 CV-07 发布幂等字段纳入同一个带恢复点的显式生产迁移。"""

    table_exists = connection.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type='table' AND name='delivery_publish_intents'"
    ).fetchone()
    if table_exists is None:
        return
    columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(delivery_publish_intents)"
        ).fetchall()
    }
    if "request_idempotency_hash" not in columns:
        connection.execute(
            "ALTER TABLE delivery_publish_intents "
            "ADD COLUMN request_idempotency_hash TEXT"
        )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "idx_dpi_owner_request_idempotency "
        "ON delivery_publish_intents(owner_id, request_idempotency_hash) "
        "WHERE request_idempotency_hash IS NOT NULL"
    )


def _publication_schema_complete(connection: sqlite3.Connection) -> bool:
    table_exists = connection.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type='table' AND name='delivery_publish_intents'"
    ).fetchone()
    if table_exists is None:
        return True
    columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(delivery_publish_intents)"
        ).fetchall()
    }
    indexes = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA index_list(delivery_publish_intents)"
        ).fetchall()
    }
    return (
        "request_idempotency_hash" in columns
        and "idx_dpi_owner_request_idempotency" in indexes
    )


def migrate_candidate_verification(
    db_path: str | Path,
    backup_path: str | Path,
) -> Path:
    """先创建一致性恢复点，再显式安装 CandidateVerification Schema。"""

    database = Path(db_path).expanduser().resolve()
    backup = Path(backup_path).expanduser().resolve()
    if not database.is_file():
        raise FileNotFoundError("候选验证迁移源数据库不存在")
    if database == backup:
        raise ValueError("候选验证迁移备份不能覆盖源数据库")
    migration_lock = FileLock(
        str(database) + ".candidate-verification-migration.lock",
        timeout=30,
    )
    with migration_lock:
        if not _database_integrity_ok(database):
            raise RuntimeError("候选验证迁移源数据库完整性检查失败")
        candidate_schema_installed = _schema_exists(database)
        if candidate_schema_installed:
            try:
                with closing(
                    sqlite3.connect(f"file:{database}?mode=ro", uri=True)
                ) as migrated:
                    row = migrated.execute(
                        "SELECT backup_sha256 "
                        "FROM candidate_verification_migrations "
                        "WHERE migration_id=?",
                        (_PUBLICATION_MIGRATION_ID,),
                    ).fetchone()
                    publication_complete = _publication_schema_complete(migrated)
            except sqlite3.DatabaseError as exc:
                raise RuntimeError("候选验证迁移 Schema 不完整") from exc
            if row is not None:
                if not _database_integrity_ok(backup):
                    raise RuntimeError("候选验证迁移首次恢复点无效")
                if row[0] != _file_sha256(backup):
                    raise RuntimeError("候选验证迁移首次恢复点不匹配")
                if not publication_complete:
                    raise RuntimeError("Delivery 发布迁移 Schema 不完整")
                return backup

        with closing(sqlite3.connect(database, timeout=30)) as source:
            source.execute("BEGIN IMMEDIATE")
            source_fingerprint = _database_fingerprint_connection(source)
            temporary_backup = backup.with_name(
                f".{backup.name}.{os.getpid()}.tmp"
            )
            try:
                if temporary_backup.exists():
                    raise RuntimeError("候选验证迁移临时恢复点已存在")
                if backup.exists():
                    if (
                        not _database_integrity_ok(backup)
                        or _database_fingerprint(backup) != source_fingerprint
                    ):
                        raise RuntimeError("候选验证迁移恢复点与源数据库不一致")
                else:
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    with closing(
                        sqlite3.connect(database, timeout=30)
                    ) as backup_source:
                        with closing(
                            sqlite3.connect(temporary_backup, timeout=30)
                        ) as destination:
                            backup_source.backup(destination)
                    if (
                        not _database_integrity_ok(temporary_backup)
                        or _database_fingerprint(temporary_backup)
                        != source_fingerprint
                    ):
                        raise RuntimeError("候选验证迁移恢复点与源数据库不一致")
                    os.replace(temporary_backup, backup)
                if not candidate_schema_installed:
                    for statement in _sql_statements(_MIGRATION_SQL):
                        source.execute(statement)
                    _import_legacy_attempts(source)
                    source.execute(
                        "INSERT INTO candidate_verification_migrations "
                        "(migration_id, backup_sha256, applied_at) VALUES (?, ?, ?)",
                        (
                            _MIGRATION_ID,
                            _file_sha256(backup),
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                _migrate_delivery_publication_idempotency(source)
                source.execute(
                    "INSERT INTO candidate_verification_migrations "
                    "(migration_id, backup_sha256, applied_at) VALUES (?, ?, ?)",
                    (
                        _PUBLICATION_MIGRATION_ID,
                        _file_sha256(backup),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                if _schema_signature(source) != _expected_schema_signature():
                    raise RuntimeError("候选验证迁移最终 Schema 不完整")
                if not _publication_schema_complete(source):
                    raise RuntimeError("Delivery 发布迁移最终 Schema 不完整")
                if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("候选验证迁移后完整性检查失败")
                if source.execute("PRAGMA foreign_key_check").fetchall():
                    raise RuntimeError("候选验证迁移后外键检查失败")
                source.commit()
            except Exception:
                source.rollback()
                raise
            finally:
                temporary_backup.unlink(missing_ok=True)
    return backup


def _schema_exists(database: Path) -> bool:
    if not database.is_file():
        return False
    try:
        with closing(
            sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        ) as connection:
            migration = connection.execute(
                "SELECT backup_sha256 FROM candidate_verification_migrations "
                "WHERE migration_id=?",
                (_MIGRATION_ID,),
            ).fetchone()
            return (
                _schema_signature(connection) == _expected_schema_signature()
                and migration is not None
                and connection.execute("PRAGMA integrity_check").fetchone()[0]
                == "ok"
                and not connection.execute("PRAGMA foreign_key_check").fetchall()
            )
    except sqlite3.DatabaseError:
        return False


class SqliteCandidateVerificationRepository:
    """只接受已经执行显式迁移的数据库。"""

    def __init__(self, db_path: str | Path) -> None:
        database = Path(db_path).expanduser().resolve()
        if not _schema_exists(database):
            raise RuntimeError("候选验证数据库尚未执行带备份迁移")
        self._db_path = str(database)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def claim_runtime_binding(
        self,
        owner_id: str,
        task_id: str,
        revision: int,
        run_id: str,
    ) -> None:
        """初验原子认领尚未落库的 Run ID，并拒绝覆盖既有身份。"""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE agentic_runtime_runs SET run_id=? "
                "WHERE user_id=? AND task_id=? AND revision=? "
                "AND runtime_version='pi' AND run_id IS NULL",
                (run_id, owner_id, task_id, revision),
            )
            row = connection.execute(
                "SELECT 1 FROM agentic_runtime_runs "
                "WHERE user_id=? AND task_id=? AND revision=? "
                "AND run_id=? AND runtime_version='pi'",
                (owner_id, task_id, revision, run_id),
            ).fetchone()
            connection.commit()
        if row is None:
            raise PermissionError("Runtime 不存在或冻结身份不一致")

    def assert_runtime_binding(
        self,
        owner_id: str,
        task_id: str,
        revision: int,
        run_id: str,
    ) -> None:
        """验证精确 Runtime 身份，不泄露其他 Owner 的记录是否存在。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM agentic_runtime_runs "
                "WHERE user_id=? AND task_id=? AND revision=? "
                "AND run_id=? AND runtime_version='pi'",
                (owner_id, task_id, revision, run_id),
            ).fetchone()
        if row is None:
            raise PermissionError("Runtime 不存在或冻结身份不一致")

    def get_runtime_context(
        self,
        owner_id: str,
        task_id: str,
        revision: int,
    ) -> dict[str, object] | None:
        """读取语义重试所需的冻结 Runtime 字段。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT runtime_version, status, run_id, workspace_root, "
                "request_json, candidates_json, verification_json, "
                "verified_candidate_set_hash "
                "FROM agentic_runtime_runs "
                "WHERE user_id=? AND task_id=? AND revision=?",
                (owner_id, task_id, revision),
            ).fetchone()
        return dict(row) if row is not None else None

    def has_succeeded_delivery(self, owner_id: str, run_id: str) -> bool:
        """只读检查新旧正式交付；任一路径命中都必须阻断重复重验。"""

        with self._connect() as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                    "('formal_delivery_runs', 'semantic_delivery_runs')"
                ).fetchall()
            }
            if "formal_delivery_runs" in tables:
                row = connection.execute(
                    "SELECT 1 FROM formal_delivery_runs "
                    "WHERE owner_id=? AND run_id=? AND status='succeeded' LIMIT 1",
                    (owner_id, run_id),
                ).fetchone()
                if row is not None:
                    return True
            if "semantic_delivery_runs" in tables:
                row = connection.execute(
                    "SELECT 1 FROM semantic_delivery_runs "
                    "WHERE user_id=? AND run_id=? LIMIT 1",
                    (owner_id, run_id),
                ).fetchone()
                if row is not None:
                    return True
        return False

    def create(self, attempt: VerificationAttempt) -> VerificationAttempt:
        result, _created = self.create_with_result(attempt)
        return result

    def create_with_result(
        self,
        attempt: VerificationAttempt,
    ) -> tuple[VerificationAttempt, bool]:
        self._validate_requested_attempt(attempt)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT 1 FROM candidate_verification_attempts "
                "WHERE owner_id=? AND idempotency_key=?",
                (attempt.owner_id, attempt.idempotency_key),
            ).fetchone()
            result = self._create_with_connection(connection, attempt)
            connection.commit()
        return result, existing is None

    @staticmethod
    def _validate_requested_attempt(attempt: VerificationAttempt) -> None:
        if attempt.status is not AttemptStatus.REQUESTED:
            raise ValueError("公开 create 只能创建 requested Attempt")
        if any(
            value is not None
            for value in (
                attempt.started_at,
                attempt.finished_at,
                attempt.report_json,
                attempt.report_hash,
            )
        ):
            raise ValueError("requested Attempt 状态字段不一致")

    @staticmethod
    def _create_with_connection(
        connection: sqlite3.Connection,
        attempt: VerificationAttempt,
    ) -> VerificationAttempt:
        values = attempt.model_dump(mode="json")
        columns = tuple(values)
        existing_row = connection.execute(
            "SELECT * FROM candidate_verification_attempts "
            "WHERE owner_id=? AND idempotency_key=?",
            (attempt.owner_id, attempt.idempotency_key),
        ).fetchone()
        if existing_row is not None:
            existing = VerificationAttempt.model_validate(dict(existing_row))
            if existing.request_hash != attempt.request_hash:
                raise ValueError("幂等键已绑定其他候选验证请求")
            return existing
        if attempt.previous_attempt_id is not None:
            # 前序链同时是权限与审计边界，绝不能只依赖全局外键串接其他 Owner。
            previous = connection.execute(
                "SELECT task_id, revision, run_id, candidate_set_hash, status "
                "FROM candidate_verification_attempts "
                "WHERE owner_id=? AND attempt_id=?",
                (attempt.owner_id, attempt.previous_attempt_id),
            ).fetchone()
            if previous is None:
                raise PermissionError("前序 Attempt 不存在或 Owner 不匹配")
            if (
                previous["task_id"] != attempt.task_id
                or previous["revision"] != attempt.revision
                or previous["run_id"] != attempt.run_id
                or previous["candidate_set_hash"] != attempt.candidate_set_hash
            ):
                raise ValueError("前序 Attempt 与冻结 Candidate 身份不一致")
            if previous["status"] not in {
                "passed",
                "failed",
                "inconclusive",
                "outcome_unknown",
                "cancelled",
            }:
                raise RuntimeError("前序 Attempt 尚未进入不可变终态")
        active = connection.execute(
            "SELECT 1 FROM candidate_verification_attempts "
            "WHERE owner_id=? AND task_id=? AND revision=? AND run_id=? "
            "AND candidate_set_hash=? AND status IN ('requested', 'running')",
            (
                attempt.owner_id,
                attempt.task_id,
                attempt.revision,
                attempt.run_id,
                attempt.candidate_set_hash,
            ),
        ).fetchone()
        if active is not None:
            raise RuntimeError("该 CandidateSet 已有活动 Attempt")
        connection.execute(
            "INSERT INTO candidate_verification_attempts ("
            + ", ".join(columns)
            + ") VALUES ("
            + ", ".join("?" for _ in columns)
            + ")",
            tuple(values[column] for column in columns),
        )
        return attempt

    def create_and_start_if_p0_allowed(
        self,
        attempt: VerificationAttempt,
        *,
        started_at: datetime,
    ) -> VerificationAttempt:
        """在同一写事务内重查 P0，并创建、启动一个新 Attempt。"""

        self._validate_requested_attempt(attempt)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            routing_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='runtime_rollout_state'"
            ).fetchone()
            if routing_table is not None:
                state = connection.execute(
                    "SELECT p0_blocked FROM runtime_rollout_state WHERE state_id=1"
                ).fetchone()
                if state is None or bool(state["p0_blocked"]):
                    raise PermissionError("P0/Gate 当前阻断新的候选验证 Attempt")
            current = self._create_with_connection(connection, attempt)
            if current.status is not AttemptStatus.REQUESTED:
                connection.commit()
                return current
            connection.execute(
                "UPDATE candidate_verification_attempts "
                "SET status='running', started_at=? "
                "WHERE owner_id=? AND attempt_id=? AND status='requested'",
                (started_at.isoformat(), attempt.owner_id, current.attempt_id),
            )
            row = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE owner_id=? AND attempt_id=?",
                (attempt.owner_id, current.attempt_id),
            ).fetchone()
            connection.commit()
        assert row is not None
        return VerificationAttempt.model_validate(dict(row))

    def start_requested_if_current(
        self,
        owner_id: str,
        attempt_id: str,
        *,
        started_at: datetime,
        expected_workspace_root: str,
        expected_request_json: str,
        expected_candidates_json: str,
        expected_verification_json: str,
    ) -> tuple[VerificationAttempt, bool, bool]:
        """原子重查运行身份、正式交付与 P0，并认领 requested Attempt。"""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE owner_id=? AND attempt_id=?",
                (owner_id, attempt_id),
            ).fetchone()
            if row is None:
                raise PermissionError("候选验证 Attempt 不存在或 Owner 不匹配")
            attempt = VerificationAttempt.model_validate(dict(row))
            if attempt.status is not AttemptStatus.REQUESTED:
                connection.commit()
                return attempt, False, False

            def cancel_requested() -> tuple[VerificationAttempt, bool, bool]:
                # Schema 只允许 requested→running→终态；同一事务内跨过 running，
                # 但不释放事务锁，也不执行任何候选读取、Verifier 或外发。
                connection.execute(
                    "UPDATE candidate_verification_attempts "
                    "SET status='running', started_at=? "
                    "WHERE owner_id=? AND attempt_id=? AND status='requested'",
                    (started_at.isoformat(), owner_id, attempt_id),
                )
                connection.execute(
                    "UPDATE candidate_verification_attempts "
                    "SET status='cancelled', finished_at=? "
                    "WHERE owner_id=? AND attempt_id=? AND status='running'",
                    (started_at.isoformat(), owner_id, attempt_id),
                )
                cancelled_row = connection.execute(
                    "SELECT * FROM candidate_verification_attempts "
                    "WHERE owner_id=? AND attempt_id=?",
                    (owner_id, attempt_id),
                ).fetchone()
                connection.commit()
                assert cancelled_row is not None
                return (
                    VerificationAttempt.model_validate(dict(cancelled_row)),
                    False,
                    True,
                )

            state = connection.execute(
                "SELECT p0_blocked FROM runtime_rollout_state WHERE state_id=1"
            ).fetchone()
            if state is None or bool(state["p0_blocked"]):
                return cancel_requested()
            runtime = connection.execute(
                "SELECT 1 FROM agentic_runtime_runs "
                "WHERE user_id=? AND task_id=? AND revision=? AND run_id=? "
                "AND runtime_version='pi' AND status='candidate_ready' "
                "AND verified_candidate_set_hash=? AND workspace_root=? "
                "AND request_json=? AND candidates_json=? AND verification_json=? "
                "AND EXISTS (SELECT 1 FROM semantic_workspace_tasks AS task "
                "WHERE task.user_id=agentic_runtime_runs.user_id "
                "AND task.task_id=agentic_runtime_runs.task_id "
                "AND task.active_revision=agentic_runtime_runs.revision "
                "AND task.cancel_requested=0)",
                (
                    attempt.owner_id,
                    attempt.task_id,
                    attempt.revision,
                    attempt.run_id,
                    attempt.candidate_set_hash,
                    expected_workspace_root,
                    expected_request_json,
                    expected_candidates_json,
                    expected_verification_json,
                ),
            ).fetchone()
            if runtime is None:
                return cancel_requested()
            delivery = connection.execute(
                "SELECT 1 FROM formal_delivery_runs "
                "WHERE owner_id=? AND run_id=? AND status='succeeded' LIMIT 1",
                (attempt.owner_id, attempt.run_id),
            ).fetchone()
            if delivery is None:
                delivery = connection.execute(
                    "SELECT 1 FROM semantic_delivery_runs "
                    "WHERE user_id=? AND run_id=? LIMIT 1",
                    (attempt.owner_id, attempt.run_id),
                ).fetchone()
            if delivery is not None:
                return cancel_requested()
            connection.execute(
                "UPDATE candidate_verification_attempts "
                "SET status='running', started_at=? "
                "WHERE owner_id=? AND attempt_id=? AND status='requested'",
                (started_at.isoformat(), owner_id, attempt_id),
            )
            row = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE owner_id=? AND attempt_id=?",
                (owner_id, attempt_id),
            ).fetchone()
            connection.commit()
        assert row is not None
        return VerificationAttempt.model_validate(dict(row)), True, False

    def get(self, owner_id: str, attempt_id: str) -> VerificationAttempt | None:
        with self._connect() as connection:
            # Owner 条件是数据隔离边界，不回退全局查询以免泄露 Attempt 是否存在。
            row = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE owner_id=? AND attempt_id=?",
                (owner_id, attempt_id),
            ).fetchone()
        return VerificationAttempt.model_validate(dict(row)) if row else None

    def get_by_idempotency(
        self,
        owner_id: str,
        idempotency_key: str,
    ) -> VerificationAttempt | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE owner_id=? AND idempotency_key=?",
                (owner_id, idempotency_key),
            ).fetchone()
        return VerificationAttempt.model_validate(dict(row)) if row else None

    def cancel_requested(
        self,
        owner_id: str,
        attempt_id: str,
        *,
        finished_at: datetime,
    ) -> tuple[VerificationAttempt, bool]:
        """只收口尚未开始的 Attempt；已认领或终态记录保持原样。"""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            started = connection.execute(
                "UPDATE candidate_verification_attempts "
                "SET status='running', started_at=? "
                "WHERE owner_id=? AND attempt_id=? AND status='requested'",
                (finished_at.isoformat(), owner_id, attempt_id),
            )
            if started.rowcount == 1:
                connection.execute(
                    "UPDATE candidate_verification_attempts "
                    "SET status='cancelled', finished_at=? "
                    "WHERE owner_id=? AND attempt_id=? AND status='running'",
                    (finished_at.isoformat(), owner_id, attempt_id),
                )
            row = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE owner_id=? AND attempt_id=?",
                (owner_id, attempt_id),
            ).fetchone()
            connection.commit()
        if row is None:
            raise PermissionError("候选验证 Attempt 不存在或 Owner 不匹配")
        return VerificationAttempt.model_validate(dict(row)), started.rowcount == 1

    def list_requested_local(self) -> tuple[VerificationAttempt, ...]:
        """恢复尚未认领的 requested；Provider 尚未运行时允许安全接管。"""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE status='requested' "
                "ORDER BY created_at, rowid"
            ).fetchall()
        return tuple(
            VerificationAttempt.model_validate(dict(row)) for row in rows
        )

    def list_running_local(self) -> tuple[VerificationAttempt, ...]:
        """进程启动时收口上一个 Worker 遗留的 running Attempt。"""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE status='running' "
                "ORDER BY started_at, rowid"
            ).fetchall()
        return tuple(
            VerificationAttempt.model_validate(dict(row)) for row in rows
        )

    def list_for_candidate(
        self,
        owner_id: str,
        *,
        task_id: str,
        revision: int,
        run_id: str,
        candidate_set_hash: str,
    ) -> tuple[VerificationAttempt, ...]:
        with self._connect() as connection:
            # 历史列表也必须在 Owner 边界内过滤，管理员诊断另走审计接口。
            rows = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE owner_id=? AND task_id=? AND revision=? AND run_id=? "
                "AND candidate_set_hash=? ORDER BY created_at, rowid",
                (owner_id, task_id, revision, run_id, candidate_set_hash),
            ).fetchall()
        return tuple(
            VerificationAttempt.model_validate(dict(row)) for row in rows
        )

    def start(
        self,
        owner_id: str,
        attempt_id: str,
        *,
        started_at: datetime,
    ) -> VerificationAttempt:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE candidate_verification_attempts "
                "SET status='running', started_at=? "
                "WHERE owner_id=? AND attempt_id=? AND status='requested'",
                (started_at.isoformat(), owner_id, attempt_id),
            )
            if updated.rowcount != 1:
                visible = connection.execute(
                    "SELECT 1 FROM candidate_verification_attempts "
                    "WHERE owner_id=? AND attempt_id=?",
                    (owner_id, attempt_id),
                ).fetchone()
                if visible is None:
                    raise PermissionError("候选验证 Attempt 不存在或 Owner 不匹配")
                raise RuntimeError("候选验证 Attempt 已不处于 requested")
            row = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE owner_id=? AND attempt_id=?",
                (owner_id, attempt_id),
            ).fetchone()
            connection.commit()
        assert row is not None
        return VerificationAttempt.model_validate(dict(row))

    def finish(
        self,
        owner_id: str,
        attempt_id: str,
        *,
        status: AttemptStatus,
        report_json: str | None,
        report_hash: str | None,
        finished_at: datetime,
    ) -> VerificationAttempt:
        terminal_statuses = {
            AttemptStatus.PASSED,
            AttemptStatus.FAILED,
            AttemptStatus.INCONCLUSIVE,
            AttemptStatus.OUTCOME_UNKNOWN,
            AttemptStatus.CANCELLED,
        }
        if status not in terminal_statuses:
            raise ValueError("finish 只能写入候选验证终态")
        has_report = report_json is not None or report_hash is not None
        if status in {
            AttemptStatus.PASSED,
            AttemptStatus.FAILED,
            AttemptStatus.INCONCLUSIVE,
        }:
            if report_json is None or report_hash is None:
                raise ValueError("确定性候选验证终态必须冻结报告")
            actual_hash = hashlib.sha256(report_json.encode("utf-8")).hexdigest()
            if actual_hash != report_hash:
                raise ValueError("候选验证报告哈希不匹配")
            try:
                report = json.loads(report_json)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("候选验证报告不是有效 JSON") from exc
            if not isinstance(report, dict) or report.get("status") != status.value:
                raise ValueError("候选验证报告状态与 Attempt 终态不一致")
        elif has_report:
            raise ValueError("未知或取消终态不得伪造确定性报告")

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE candidate_verification_attempts "
                "SET status=?, report_json=?, report_hash=?, finished_at=? "
                "WHERE owner_id=? AND attempt_id=? AND status='running'",
                (
                    status.value,
                    report_json,
                    report_hash,
                    finished_at.isoformat(),
                    owner_id,
                    attempt_id,
                ),
            )
            if updated.rowcount != 1:
                visible = connection.execute(
                    "SELECT 1 FROM candidate_verification_attempts "
                    "WHERE owner_id=? AND attempt_id=?",
                    (owner_id, attempt_id),
                ).fetchone()
                if visible is None:
                    raise PermissionError("候选验证 Attempt 不存在或 Owner 不匹配")
                raise RuntimeError("候选验证 Attempt 已不处于 running")
            row = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE owner_id=? AND attempt_id=?",
                (owner_id, attempt_id),
            ).fetchone()
            connection.commit()
        assert row is not None
        return VerificationAttempt.model_validate(dict(row))

    def finish_with_runtime_projection(
        self,
        owner_id: str,
        attempt_id: str,
        *,
        status: AttemptStatus,
        report_json: str,
        report_hash: str,
        finished_at: datetime,
        candidates_json: str,
        candidate_set_hash: str,
        require_reverification_current: bool = False,
    ) -> VerificationAttempt:
        """原子冻结 Attempt 终态并维护旧 Runtime 读取投影。"""

        if status not in {
            AttemptStatus.PASSED,
            AttemptStatus.FAILED,
            AttemptStatus.INCONCLUSIVE,
        }:
            raise ValueError("兼容投影只接受确定性候选验证终态")
        actual_hash = hashlib.sha256(report_json.encode("utf-8")).hexdigest()
        if actual_hash != report_hash:
            raise ValueError("候选验证报告哈希不匹配")
        try:
            report = json.loads(report_json)
        except json.JSONDecodeError as exc:
            raise ValueError("候选验证报告不是有效 JSON") from exc
        if not isinstance(report, dict) or report.get("status") != status.value:
            raise ValueError("候选验证报告状态与 Attempt 终态不一致")

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            attempt_row = connection.execute(
                "SELECT task_id, revision, run_id, candidate_set_hash, reason_code "
                "FROM candidate_verification_attempts "
                "WHERE owner_id=? AND attempt_id=? AND status='running'",
                (owner_id, attempt_id),
            ).fetchone()
            if attempt_row is None:
                raise PermissionError("候选验证 Attempt 不存在或状态不可完成")
            if attempt_row["candidate_set_hash"] != candidate_set_hash:
                raise ValueError("Attempt 与兼容投影的 CandidateSet 不一致")

            if require_reverification_current:
                state = connection.execute(
                    "SELECT p0_blocked FROM runtime_rollout_state WHERE state_id=1"
                ).fetchone()
                if state is None or bool(state["p0_blocked"]):
                    raise PermissionError("P0/Gate 已阻断候选重验结论提交")
                current = connection.execute(
                    "SELECT 1 FROM agentic_runtime_runs AS runtime "
                    "WHERE runtime.user_id=? AND runtime.task_id=? "
                    "AND runtime.revision=? AND runtime.run_id=? "
                    "AND runtime.runtime_version='pi' "
                    "AND runtime.status='candidate_ready' "
                    "AND runtime.verified_candidate_set_hash=? "
                    "AND EXISTS (SELECT 1 FROM semantic_workspace_tasks AS task "
                    "WHERE task.user_id=runtime.user_id "
                    "AND task.task_id=runtime.task_id "
                    "AND task.active_revision=runtime.revision "
                    "AND task.cancel_requested=0)",
                    (
                        owner_id,
                        attempt_row["task_id"],
                        attempt_row["revision"],
                        attempt_row["run_id"],
                        candidate_set_hash,
                    ),
                ).fetchone()
                if current is None:
                    raise PermissionError("运行期任务权威身份已漂移或取消")
                delivery = connection.execute(
                    "SELECT 1 FROM formal_delivery_runs "
                    "WHERE owner_id=? AND run_id=? AND status='succeeded' LIMIT 1",
                    (owner_id, attempt_row["run_id"]),
                ).fetchone()
                if delivery is None:
                    delivery = connection.execute(
                        "SELECT 1 FROM semantic_delivery_runs "
                        "WHERE user_id=? AND run_id=? LIMIT 1",
                        (owner_id, attempt_row["run_id"]),
                    ).fetchone()
                if delivery is not None:
                    raise PermissionError("候选已存在正式 Delivery，拒绝提交重验结论")

            # 两张表必须共享一个提交点；任一触发器或身份门拒绝时整体回滚。
            updated_projection = connection.execute(
                "UPDATE agentic_runtime_runs SET candidates_json=?, "
                "verification_json=?, verified_candidate_set_hash=?, updated_at=? "
                "WHERE user_id=? AND task_id=? AND revision=? AND run_id=?",
                (
                    candidates_json,
                    report_json,
                    candidate_set_hash,
                    finished_at.isoformat(),
                    owner_id,
                    attempt_row["task_id"],
                    attempt_row["revision"],
                    attempt_row["run_id"],
                ),
            )
            if updated_projection.rowcount != 1:
                raise PermissionError("Runtime 投影不存在或冻结身份不一致")
            updated_attempt = connection.execute(
                "UPDATE candidate_verification_attempts "
                "SET status=?, report_json=?, report_hash=?, finished_at=? "
                "WHERE owner_id=? AND attempt_id=? AND status='running'",
                (
                    status.value,
                    report_json,
                    report_hash,
                    finished_at.isoformat(),
                    owner_id,
                    attempt_id,
                ),
            )
            if updated_attempt.rowcount != 1:
                raise RuntimeError("候选验证 Attempt 终态提交发生并发冲突")
            row = connection.execute(
                "SELECT * FROM candidate_verification_attempts "
                "WHERE owner_id=? AND attempt_id=?",
                (owner_id, attempt_id),
            ).fetchone()
            connection.commit()
        assert row is not None
        return VerificationAttempt.model_validate(dict(row))
