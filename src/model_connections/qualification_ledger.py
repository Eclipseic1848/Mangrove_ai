# -*- coding: utf-8 -*-
"""G4 资格批次的持久化台账。"""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid
from collections.abc import Iterator, Sequence
from typing import Any


SCHEMA_VERSION = "g4-qualification-ledger-v1"


class QualificationLedgerError(RuntimeError):
    """资格批次台账拒绝了状态转换。"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _provider_key(provider: dict[str, object]) -> str:
    return f"{provider['connection_id']}:{provider['connection_version']}"


class QualificationBatchLedger:
    """把批次授权、Attempt 和终态集中在一个 SQLite Module 内。"""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise QualificationLedgerError("资格批次台账路径无效")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def identity(self) -> dict[str, str]:
        """返回可由外部锚点绑定的稳定台账身份。"""

        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT schema_version, ledger_id
                FROM qualification_ledger_metadata
                WHERE singleton = 1
                """
            ).fetchone()
        if row is None:
            raise QualificationLedgerError("资格批次台账身份缺失")
        return {
            "schema_version": str(row["schema_version"]),
            "ledger_id": str(row["ledger_id"]),
        }

    def state_receipt(self) -> dict[str, object]:
        """返回外部锚点用于拒绝旧快照的单调状态凭据。"""

        with closing(self._connect()) as connection:
            return self._state_receipt(connection)

    def _initialize(self) -> None:
        created = not self.path.exists()
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS qualification_ledger_metadata (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    schema_version TEXT NOT NULL,
                    ledger_id TEXT NOT NULL UNIQUE,
                    revision INTEGER NOT NULL CHECK (revision >= 0),
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS qualification_batches (
                    batch_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_sha256 TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL,
                    provider_set_sha256 TEXT NOT NULL,
                    expected_commit TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    relay_base_url TEXT NOT NULL,
                    timeout_seconds INTEGER NOT NULL
                        CHECK (timeout_seconds > 0 AND timeout_seconds <= 7200),
                    authorized_by TEXT NOT NULL,
                    authorization_reason TEXT NOT NULL,
                    batch_kind TEXT NOT NULL CHECK (batch_kind IN ('initial', 'successor')),
                    parent_batch_id TEXT REFERENCES qualification_batches(batch_id),
                    previous_evidence_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (
                        state IN (
                            'authorized', 'in_progress', 'passed',
                            'failed', 'outcome_unknown'
                        )
                    ),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS uq_g4_active_provider_set
                ON qualification_batches(provider_set_sha256)
                WHERE state IN ('authorized', 'in_progress');

                CREATE TABLE IF NOT EXISTS qualification_batch_providers (
                    batch_id TEXT NOT NULL REFERENCES qualification_batches(batch_id),
                    provider_key TEXT NOT NULL,
                    connection_id TEXT NOT NULL,
                    connection_version TEXT NOT NULL,
                    preset_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    api_format TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (
                        state IN (
                            'authorized', 'retry_authorized', 'in_progress',
                            'passed', 'failed_after_egress', 'outcome_unknown'
                        )
                    ),
                    attempt_count INTEGER NOT NULL DEFAULT 0
                        CHECK (attempt_count >= 0 AND attempt_count <= 2),
                    check_json TEXT,
                    PRIMARY KEY (batch_id, provider_key)
                );

                CREATE TABLE IF NOT EXISTS qualification_provider_attempts (
                    batch_id TEXT NOT NULL,
                    provider_key TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL CHECK (attempt_number IN (1, 2)),
                    state TEXT NOT NULL CHECK (
                        state IN (
                            'in_progress', 'passed',
                            'failed_after_egress', 'outcome_unknown'
                        )
                    ),
                    attempt_context_json TEXT NOT NULL,
                    check_json TEXT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    PRIMARY KEY (batch_id, provider_key, attempt_number),
                    FOREIGN KEY (batch_id, provider_key)
                        REFERENCES qualification_batch_providers(batch_id, provider_key)
                );

                CREATE TABLE IF NOT EXISTS qualification_retry_authorizations (
                    batch_id TEXT NOT NULL,
                    provider_key TEXT NOT NULL,
                    retry_number INTEGER NOT NULL CHECK (retry_number = 1),
                    authorized_by TEXT NOT NULL,
                    authorization_reason TEXT NOT NULL,
                    user_confirmed_duplicate_request_and_cost INTEGER NOT NULL
                        CHECK (user_confirmed_duplicate_request_and_cost = 1),
                    previous_state TEXT NOT NULL CHECK (
                        previous_state IN (
                            'outcome_unknown', 'failed_after_egress'
                        )
                    ),
                    authorized_at TEXT NOT NULL,
                    PRIMARY KEY (batch_id, provider_key, retry_number),
                    FOREIGN KEY (batch_id, provider_key)
                        REFERENCES qualification_batch_providers(batch_id, provider_key)
                );

                CREATE TABLE IF NOT EXISTS qualification_ledger_recoveries (
                    recovery_id TEXT PRIMARY KEY,
                    recovery_kind TEXT NOT NULL CHECK (
                        recovery_kind IN (
                            'pre_egress_anchor_sync_failed',
                            'stale_in_progress_outcome_unknown'
                        )
                    ),
                    batch_id TEXT NOT NULL,
                    provider_key TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL
                        CHECK (attempt_number IN (1, 2)),
                    attempt_context_sha256 TEXT NOT NULL,
                    anchor_revision INTEGER NOT NULL CHECK (anchor_revision >= 0),
                    ledger_revision_before INTEGER NOT NULL,
                    recovered_revision INTEGER NOT NULL,
                    recovered_by TEXT NOT NULL,
                    recovery_reason TEXT NOT NULL,
                    recovered_at TEXT NOT NULL,
                    FOREIGN KEY (batch_id, provider_key)
                        REFERENCES qualification_batch_providers(batch_id, provider_key)
                );
                """
            )
            metadata = connection.execute(
                "SELECT schema_version FROM qualification_ledger_metadata WHERE singleton = 1"
            ).fetchone()
            if metadata is None:
                connection.execute(
                    """
                    INSERT INTO qualification_ledger_metadata (
                        singleton, schema_version, ledger_id, revision, created_at
                    ) VALUES (1, ?, ?, 0, ?)
                    """,
                    (SCHEMA_VERSION, f"g4ledger_{uuid.uuid4().hex}", _utc_now()),
                )
            elif metadata["schema_version"] != SCHEMA_VERSION:
                raise QualificationLedgerError("资格批次台账版本不受支持")
        if created:
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                # Windows ACL 不由 chmod 完整表达；台账不含 Secret，失败不扩大凭证暴露面。
                pass

    @contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _advance_revision(connection: sqlite3.Connection) -> None:
        updated = connection.execute(
            """
            UPDATE qualification_ledger_metadata
            SET revision = revision + 1
            WHERE singleton = 1
            """
        )
        if updated.rowcount != 1:
            raise QualificationLedgerError("资格批次台账版本推进失败")

    @staticmethod
    def _state_receipt(connection: sqlite3.Connection) -> dict[str, object]:
        metadata = connection.execute(
            """
            SELECT schema_version, ledger_id, revision, created_at
            FROM qualification_ledger_metadata
            WHERE singleton = 1
            """
        ).fetchone()
        if metadata is None:
            raise QualificationLedgerError("资格批次台账身份缺失")
        table_orders = (
            ("qualification_batches", "batch_id"),
            ("qualification_batch_providers", "batch_id, provider_key"),
            (
                "qualification_provider_attempts",
                "batch_id, provider_key, attempt_number",
            ),
            (
                "qualification_retry_authorizations",
                "batch_id, provider_key, retry_number",
            ),
            ("qualification_ledger_recoveries", "recovery_id"),
        )
        state = {
            "metadata": dict(metadata),
            "tables": {
                table: [
                    dict(row)
                    for row in connection.execute(
                        f"SELECT * FROM {table} ORDER BY {order_by}"
                    ).fetchall()
                ]
                for table, order_by in table_orders
            },
        }
        return {
            "schema_version": SCHEMA_VERSION,
            "ledger_id": str(metadata["ledger_id"]),
            "ledger_revision": int(metadata["revision"]),
            "ledger_state_sha256": _canonical_sha256(state),
        }

    def create_batch(
        self,
        *,
        manifest_sha256: str,
        providers: Sequence[dict[str, object]],
        expected_commit: str,
        owner_user_id: str,
        relay_base_url: str,
        timeout_seconds: int,
        authorized_by: str,
        authorization_reason: str,
        idempotency_key: str,
        batch_kind: str,
        parent_batch_id: str | None,
        previous_evidence: Sequence[dict[str, object]],
    ) -> dict[str, object]:
        provider_values = [dict(provider) for provider in providers]
        if batch_kind == "initial":
            if parent_batch_id is not None or previous_evidence:
                raise QualificationLedgerError("初始资格批次不得携带历史")
        elif batch_kind == "successor":
            if parent_batch_id is not None:
                raise QualificationLedgerError(
                    "持久父批次的后继执行尚未开放，拒绝绕过重试上限"
                )
            if len(previous_evidence) != 2:
                raise QualificationLedgerError(
                    "旧历史后继批次必须登记两份已耗尽证据"
                )
        else:
            raise QualificationLedgerError("资格批次类型无效")
        provider_set_sha256 = _canonical_sha256(provider_values)
        request = {
            "manifest_sha256": manifest_sha256,
            "provider_set_sha256": provider_set_sha256,
            "expected_commit": expected_commit,
            "owner_user_id": owner_user_id,
            "relay_base_url": relay_base_url,
            "timeout_seconds": timeout_seconds,
            "authorized_by": authorized_by,
            "authorization_reason": authorization_reason,
            "idempotency_key": idempotency_key,
            "batch_kind": batch_kind,
            "parent_batch_id": parent_batch_id,
            "previous_evidence": list(previous_evidence),
        }
        request_sha256 = _canonical_sha256(request)
        now = _utc_now()
        with self._write_transaction() as connection:
            existing = connection.execute(
                """
                SELECT batch_id, request_sha256
                FROM qualification_batches
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["request_sha256"] != request_sha256:
                    raise QualificationLedgerError("资格批次幂等键已绑定其他请求")
                return self._batch_summary(connection, str(existing["batch_id"]))
            history = connection.execute(
                """
                SELECT batch_id FROM qualification_batches
                WHERE provider_set_sha256 = ?
                LIMIT 1
                """,
                (provider_set_sha256,),
            ).fetchone()
            if batch_kind == "initial" and history is not None:
                raise QualificationLedgerError(
                    "同一 Provider 集合已有资格历史，不能重建初始批次"
                )
            if (
                batch_kind == "successor"
                and parent_batch_id is None
                and history is not None
            ):
                raise QualificationLedgerError(
                    "旧报告导入只能用于没有既有历史的权威台账"
                )
            active = connection.execute(
                """
                SELECT 1 FROM qualification_batches
                WHERE provider_set_sha256 = ?
                  AND state IN ('authorized', 'in_progress')
                """,
                (provider_set_sha256,),
            ).fetchone()
            if active is not None:
                raise QualificationLedgerError("同一 Provider 集合已有活动资格批次")
            batch_id = f"g4batch_{uuid.uuid4().hex}"
            connection.execute(
                """
                INSERT INTO qualification_batches (
                    batch_id, idempotency_key, request_sha256,
                    manifest_sha256, provider_set_sha256, expected_commit,
                    owner_user_id, relay_base_url, timeout_seconds,
                    authorized_by, authorization_reason, batch_kind,
                    parent_batch_id, previous_evidence_json, state,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          'authorized', ?, ?)
                """,
                (
                    batch_id,
                    idempotency_key,
                    request_sha256,
                    manifest_sha256,
                    provider_set_sha256,
                    expected_commit,
                    owner_user_id,
                    relay_base_url,
                    timeout_seconds,
                    authorized_by,
                    authorization_reason,
                    batch_kind,
                    parent_batch_id,
                    json.dumps(list(previous_evidence), ensure_ascii=False),
                    now,
                    now,
                ),
            )
            for provider in provider_values:
                connection.execute(
                    """
                    INSERT INTO qualification_batch_providers (
                        batch_id, provider_key, connection_id,
                        connection_version, preset_id, model, api_format,
                        state, attempt_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'authorized', 0)
                    """,
                    (
                        batch_id,
                        _provider_key(provider),
                        provider["connection_id"],
                        provider["connection_version"],
                        provider["preset_id"],
                        provider["model"],
                        provider["api_format"],
                    ),
                )
            self._advance_revision(connection)
            return self._batch_summary(connection, batch_id)

    def prepare_run(
        self,
        *,
        batch_id: str,
        manifest_sha256: str,
        providers: Sequence[dict[str, object]],
        run_context: dict[str, object],
    ) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
        with closing(self._connect()) as connection:
            batch = connection.execute(
                "SELECT * FROM qualification_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if batch is None:
                raise QualificationLedgerError("资格批次不存在")
            expected_context = {
                "git_commit": batch["expected_commit"],
                "git_dirty": False,
                "relay_base_url": batch["relay_base_url"],
                "timeout_seconds": batch["timeout_seconds"],
                "owner_user_id": batch["owner_user_id"],
                "expected_commit": batch["expected_commit"],
            }
            if (
                batch["manifest_sha256"] != manifest_sha256
                or batch["provider_set_sha256"]
                != _canonical_sha256([dict(provider) for provider in providers])
                or run_context != expected_context
            ):
                raise QualificationLedgerError("资格批次身份与当前运行不一致")
            rows = connection.execute(
                """
                SELECT provider_key, state, check_json
                FROM qualification_batch_providers
                WHERE batch_id = ?
                """,
                (batch_id,),
            ).fetchall()
            states = {str(row["provider_key"]): str(row["state"]) for row in rows}
            if set(states) != {_provider_key(dict(provider)) for provider in providers}:
                raise QualificationLedgerError("资格批次 Provider 身份不完整")
            blocked = {
                "in_progress",
                "failed_after_egress",
                "outcome_unknown",
            }
            if any(state in blocked for state in states.values()):
                raise QualificationLedgerError(
                    "存在未决或失败的 Provider 外发记录，拒绝重复外发"
                )
            prior_checks = {
                str(row["provider_key"]): json.loads(str(row["check_json"]))
                for row in rows
                if row["state"] == "passed" and row["check_json"] is not None
            }
            return self._batch_summary(connection, batch_id), prior_checks

    def begin_attempt(
        self,
        *,
        batch_id: str,
        provider: dict[str, object],
        attempt_context: dict[str, object],
    ) -> None:
        key = _provider_key(provider)
        with self._write_transaction() as connection:
            row = connection.execute(
                """
                SELECT state, attempt_count
                FROM qualification_batch_providers
                WHERE batch_id = ? AND provider_key = ?
                """,
                (batch_id, key),
            ).fetchone()
            if row is None or row["state"] not in {"authorized", "retry_authorized"}:
                raise QualificationLedgerError("Provider 没有可执行的批次授权")
            attempt_number = int(row["attempt_count"]) + 1
            if attempt_number > 2:
                raise QualificationLedgerError("该 Provider 的一次恢复重试次数已用完")
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO qualification_provider_attempts (
                    batch_id, provider_key, attempt_number, state,
                    attempt_context_json, started_at
                ) VALUES (?, ?, ?, 'in_progress', ?, ?)
                """,
                (
                    batch_id,
                    key,
                    attempt_number,
                    json.dumps(attempt_context, ensure_ascii=False),
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE qualification_batch_providers
                SET state = 'in_progress', attempt_count = ?
                WHERE batch_id = ? AND provider_key = ?
                """,
                (attempt_number, batch_id, key),
            )
            connection.execute(
                """
                UPDATE qualification_batches
                SET state = 'in_progress', updated_at = ?
                WHERE batch_id = ?
                """,
                (now, batch_id),
            )
            self._advance_revision(connection)

    def authorize_retry(
        self,
        *,
        batch_id: str,
        provider: dict[str, object],
        manifest_sha256: str,
        git_identity: dict[str, object],
        authorized_by: str,
        authorization_reason: str,
        user_confirmed_duplicate_request_and_cost: bool,
    ) -> dict[str, object]:
        """持久化一次由用户决定的歧义恢复重试授权。"""

        if not user_confirmed_duplicate_request_and_cost:
            raise QualificationLedgerError("未确认重复 Provider 请求和费用风险")
        key = _provider_key(provider)
        now = _utc_now()
        with self._write_transaction() as connection:
            batch = connection.execute(
                """
                SELECT manifest_sha256, expected_commit
                FROM qualification_batches
                WHERE batch_id = ?
                """,
                (batch_id,),
            ).fetchone()
            row = connection.execute(
                """
                SELECT state, attempt_count, connection_id, connection_version
                FROM qualification_batch_providers
                WHERE batch_id = ? AND provider_key = ?
                """,
                (batch_id, key),
            ).fetchone()
            if batch is None or row is None:
                raise QualificationLedgerError("资格批次或 Provider 不存在")
            if (
                batch["manifest_sha256"] != manifest_sha256
                or row["connection_id"] != provider.get("connection_id")
                or row["connection_version"] != provider.get("connection_version")
                or git_identity
                != {
                    "git_commit": batch["expected_commit"],
                    "git_dirty": False,
                }
            ):
                raise QualificationLedgerError("重试授权身份与资格批次不一致")
            existing = connection.execute(
                """
                SELECT 1 FROM qualification_retry_authorizations
                WHERE batch_id = ? AND provider_key = ?
                """,
                (batch_id, key),
            ).fetchone()
            if row["state"] == "in_progress":
                raise QualificationLedgerError(
                    "Provider Attempt 仍在进行，不能授权重试"
                )
            if (
                existing is not None
                or int(row["attempt_count"]) != 1
                or row["state"] == "retry_authorized"
            ):
                raise QualificationLedgerError(
                    "该 Provider 的一次恢复重试次数已用完"
                )
            if row["state"] not in {
                "outcome_unknown",
                "failed_after_egress",
            }:
                raise QualificationLedgerError(
                    "没有可由用户决定重试的未决 Pi 外发记录"
                )
            connection.execute(
                """
                INSERT INTO qualification_retry_authorizations (
                    batch_id, provider_key, retry_number, authorized_by,
                    authorization_reason,
                    user_confirmed_duplicate_request_and_cost,
                    previous_state, authorized_at
                ) VALUES (?, ?, 1, ?, ?, 1, ?, ?)
                """,
                (
                    batch_id,
                    key,
                    authorized_by,
                    authorization_reason,
                    row["state"],
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE qualification_batch_providers
                SET state = 'retry_authorized'
                WHERE batch_id = ? AND provider_key = ?
                """,
                (batch_id, key),
            )
            connection.execute(
                """
                UPDATE qualification_batches
                SET state = 'in_progress', updated_at = ?
                WHERE batch_id = ?
                """,
                (now, batch_id),
            )
            self._advance_revision(connection)
        return {
            "schema_version": SCHEMA_VERSION,
            "batch_id": batch_id,
            "connection_id": str(provider["connection_id"]),
            "retry_number": 1,
            "authorized_by": authorized_by,
            "authorization_reason": authorization_reason,
            "previous_state": str(row["state"]),
            "user_confirmed_duplicate_request_and_cost": True,
            "authorized_at": now,
        }

    def finish_attempt(
        self,
        *,
        batch_id: str,
        provider: dict[str, object],
        check: dict[str, object],
    ) -> None:
        key = _provider_key(provider)
        provider_state = {
            "passed": "passed",
            "outcome_unknown": "outcome_unknown",
        }.get(str(check.get("outcome")), "failed_after_egress")
        now = _utc_now()
        with self._write_transaction() as connection:
            row = connection.execute(
                """
                SELECT attempt_count, state
                FROM qualification_batch_providers
                WHERE batch_id = ? AND provider_key = ?
                """,
                (batch_id, key),
            ).fetchone()
            if row is None or row["state"] != "in_progress":
                raise QualificationLedgerError("Provider 外发台账缺少进行中记录")
            attempt_number = int(row["attempt_count"])
            attempt_update = connection.execute(
                """
                UPDATE qualification_provider_attempts
                SET state = ?, check_json = ?, completed_at = ?
                WHERE batch_id = ? AND provider_key = ? AND attempt_number = ?
                  AND state = 'in_progress'
                """,
                (
                    provider_state,
                    json.dumps(check, ensure_ascii=False),
                    now,
                    batch_id,
                    key,
                    attempt_number,
                ),
            )
            if attempt_update.rowcount != 1:
                raise QualificationLedgerError("Provider Attempt 终态写入失败")
            connection.execute(
                """
                UPDATE qualification_batch_providers
                SET state = ?, check_json = ?
                WHERE batch_id = ? AND provider_key = ?
                """,
                (
                    provider_state,
                    json.dumps(check, ensure_ascii=False),
                    batch_id,
                    key,
                ),
            )
            states = [
                str(item["state"])
                for item in connection.execute(
                    """
                    SELECT state FROM qualification_batch_providers
                    WHERE batch_id = ?
                    """,
                    (batch_id,),
                ).fetchall()
            ]
            if states and all(state == "passed" for state in states):
                batch_state = "passed"
            elif any(state == "outcome_unknown" for state in states):
                batch_state = "outcome_unknown"
            elif any(state in {"authorized", "retry_authorized"} for state in states):
                batch_state = "in_progress"
            else:
                batch_state = "failed"
            connection.execute(
                """
                UPDATE qualification_batches
                SET state = ?, updated_at = ?
                WHERE batch_id = ?
                """,
                (batch_state, now, batch_id),
            )
            self._advance_revision(connection)

    def recover_anchor_gap(
        self,
        *,
        anchor_revision: int,
        recovered_by: str,
        recovery_reason: str,
        allow_pre_egress_cancel: bool,
    ) -> dict[str, object]:
        """在无活动执行锁时收口锚点同步失败留下的单调版本缺口。"""

        if not all(
            isinstance(value, str) and bool(value.strip())
            for value in (recovered_by, recovery_reason)
        ):
            raise QualificationLedgerError("资格台账恢复身份和原因不能为空")
        with self._write_transaction() as connection:
            metadata = connection.execute(
                """
                SELECT revision FROM qualification_ledger_metadata
                WHERE singleton = 1
                """
            ).fetchone()
            if metadata is None:
                raise QualificationLedgerError("资格批次台账身份缺失")
            ledger_revision = int(metadata["revision"])
            completed_recovery = connection.execute(
                """
                SELECT recovery_kind
                FROM qualification_ledger_recoveries
                WHERE anchor_revision = ? AND recovered_revision = ?
                """,
                (anchor_revision, ledger_revision),
            ).fetchone()
            if (
                ledger_revision in {
                    anchor_revision + 1,
                    anchor_revision + 2,
                }
                and completed_recovery is not None
            ):
                recovery_kind = str(completed_recovery["recovery_kind"])
                return {
                    "anchor_revision_advance": (
                        ledger_revision - anchor_revision
                    ),
                    "pre_egress_attempt_cancelled": (
                        recovery_kind == "pre_egress_anchor_sync_failed"
                    ),
                    "stale_attempt_closed_outcome_unknown": (
                        recovery_kind == "stale_in_progress_outcome_unknown"
                    ),
                }
            revision_gap = ledger_revision - anchor_revision
            allowed_gaps = {1} if allow_pre_egress_cancel else {0, 1}
            if revision_gap not in allowed_gaps:
                raise QualificationLedgerError(
                    "资格台账与锚点的版本缺口不能安全前滚"
                )
            in_progress = connection.execute(
                """
                SELECT p.batch_id, p.provider_key, p.attempt_count,
                       a.attempt_context_json
                FROM qualification_batch_providers AS p
                JOIN qualification_provider_attempts AS a
                  ON a.batch_id = p.batch_id
                 AND a.provider_key = p.provider_key
                 AND a.attempt_number = p.attempt_count
                WHERE p.state = 'in_progress'
                  AND a.state = 'in_progress'
                """
            ).fetchall()
            if not in_progress:
                if revision_gap == 0:
                    raise QualificationLedgerError(
                        "资格台账与锚点已经一致，无需恢复"
                    )
                return {
                    "anchor_revision_advance": 1,
                    "pre_egress_attempt_cancelled": False,
                    "stale_attempt_closed_outcome_unknown": False,
                }
            if len(in_progress) != 1:
                raise QualificationLedgerError(
                    "资格台账包含多个未锚定 Attempt，拒绝自动收口"
                )
            row = in_progress[0]
            attempt_number = int(row["attempt_count"])
            now = _utc_now()
            if not allow_pre_egress_cancel:
                check = {
                    "outcome": "outcome_unknown",
                    "error_code": "stale_in_progress_after_process_exit",
                }
                connection.execute(
                    """
                    UPDATE qualification_provider_attempts
                    SET state = 'outcome_unknown', check_json = ?,
                        completed_at = ?
                    WHERE batch_id = ? AND provider_key = ?
                      AND attempt_number = ? AND state = 'in_progress'
                    """,
                    (
                        json.dumps(check, ensure_ascii=False),
                        now,
                        row["batch_id"],
                        row["provider_key"],
                        attempt_number,
                    ),
                )
                connection.execute(
                    """
                    UPDATE qualification_batch_providers
                    SET state = 'outcome_unknown', check_json = ?
                    WHERE batch_id = ? AND provider_key = ?
                    """,
                    (
                        json.dumps(check, ensure_ascii=False),
                        row["batch_id"],
                        row["provider_key"],
                    ),
                )
                connection.execute(
                    """
                    UPDATE qualification_batches
                    SET state = 'outcome_unknown', updated_at = ?
                    WHERE batch_id = ?
                    """,
                    (now, row["batch_id"]),
                )
                recovered_revision = ledger_revision + 1
                connection.execute(
                    """
                    INSERT INTO qualification_ledger_recoveries (
                        recovery_id, recovery_kind, batch_id, provider_key,
                        attempt_number, attempt_context_sha256,
                        anchor_revision, ledger_revision_before,
                        recovered_revision, recovered_by, recovery_reason,
                        recovered_at
                    ) VALUES (?, 'stale_in_progress_outcome_unknown', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"g4recovery_{uuid.uuid4().hex}",
                        row["batch_id"],
                        row["provider_key"],
                        attempt_number,
                        _canonical_sha256(
                            json.loads(str(row["attempt_context_json"]))
                        ),
                        anchor_revision,
                        ledger_revision,
                        recovered_revision,
                        recovered_by,
                        recovery_reason,
                        now,
                    ),
                )
                self._advance_revision(connection)
                return {
                    "anchor_revision_advance": revision_gap + 1,
                    "pre_egress_attempt_cancelled": False,
                    "stale_attempt_closed_outcome_unknown": True,
                }
            prior_state = "authorized" if attempt_number == 1 else "retry_authorized"
            deleted = connection.execute(
                """
                DELETE FROM qualification_provider_attempts
                WHERE batch_id = ? AND provider_key = ?
                  AND attempt_number = ? AND state = 'in_progress'
                """,
                (row["batch_id"], row["provider_key"], attempt_number),
            )
            if deleted.rowcount != 1:
                raise QualificationLedgerError("未外发 Attempt 收口失败")
            connection.execute(
                """
                UPDATE qualification_batch_providers
                SET state = ?, attempt_count = attempt_count - 1
                WHERE batch_id = ? AND provider_key = ?
                """,
                (prior_state, row["batch_id"], row["provider_key"]),
            )
            provider_states = [
                str(item["state"])
                for item in connection.execute(
                    """
                    SELECT state FROM qualification_batch_providers
                    WHERE batch_id = ?
                    """,
                    (row["batch_id"],),
                ).fetchall()
            ]
            if provider_states and all(
                state == "authorized" for state in provider_states
            ):
                batch_state = "authorized"
            else:
                batch_state = "in_progress"
            connection.execute(
                """
                UPDATE qualification_batches
                SET state = ?, updated_at = ?
                WHERE batch_id = ?
                """,
                (batch_state, now, row["batch_id"]),
            )
            recovered_revision = ledger_revision + 1
            connection.execute(
                """
                INSERT INTO qualification_ledger_recoveries (
                    recovery_id, recovery_kind, batch_id, provider_key,
                    attempt_number, attempt_context_sha256,
                    anchor_revision, ledger_revision_before, recovered_revision,
                    recovered_by, recovery_reason, recovered_at
                ) VALUES (?, 'pre_egress_anchor_sync_failed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"g4recovery_{uuid.uuid4().hex}",
                    row["batch_id"],
                    row["provider_key"],
                    attempt_number,
                    _canonical_sha256(
                        json.loads(str(row["attempt_context_json"]))
                    ),
                    anchor_revision,
                    ledger_revision,
                    recovered_revision,
                    recovered_by,
                    recovery_reason,
                    now,
                ),
            )
            self._advance_revision(connection)
            return {
                "anchor_revision_advance": 2,
                "pre_egress_attempt_cancelled": True,
                "stale_attempt_closed_outcome_unknown": False,
            }

    def validate_passed_batch(
        self,
        *,
        batch_id: str,
        manifest_sha256: str,
        providers: Sequence[dict[str, object]],
        expected_commit: str,
        expected_ledger_id: str,
    ) -> dict[str, object]:
        """确认最终报告引用的是本台账中已通过的完整批次。"""

        with closing(self._connect()) as connection:
            summary = self._batch_summary(connection, batch_id)
            metadata = connection.execute(
                """
                SELECT ledger_id FROM qualification_ledger_metadata
                WHERE singleton = 1
                """
            ).fetchone()
            rows = connection.execute(
                """
                SELECT provider_key, state, attempt_count, check_json
                FROM qualification_batch_providers
                WHERE batch_id = ?
                """,
                (batch_id,),
            ).fetchall()
            expected_keys = {
                _provider_key(dict(provider)) for provider in providers
            }
            actual_keys = {str(row["provider_key"]) for row in rows}
            checks_valid = bool(rows) and all(
                row["state"] == "passed"
                and 1 <= int(row["attempt_count"]) <= 2
                and row["check_json"] is not None
                and json.loads(str(row["check_json"])).get("outcome")
                == "passed"
                for row in rows
            )
            if (
                metadata is None
                or metadata["ledger_id"] != expected_ledger_id
                or summary["state"] != "passed"
                or summary["manifest_sha256"] != manifest_sha256
                or summary["expected_commit"] != expected_commit
                or actual_keys != expected_keys
                or not checks_valid
            ):
                raise QualificationLedgerError(
                    "资格批次台账未形成完整通过终态"
                )
            return summary

    def _batch_summary(
        self,
        connection: sqlite3.Connection,
        batch_id: str,
    ) -> dict[str, object]:
        row = connection.execute(
            "SELECT * FROM qualification_batches WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
        metadata = connection.execute(
            "SELECT ledger_id FROM qualification_ledger_metadata WHERE singleton = 1"
        ).fetchone()
        if row is None or metadata is None:
            raise QualificationLedgerError("资格批次台账内容不完整")
        return {
            "schema_version": SCHEMA_VERSION,
            "ledger_id": str(metadata["ledger_id"]),
            "batch_id": str(row["batch_id"]),
            "manifest_sha256": str(row["manifest_sha256"]),
            "expected_commit": str(row["expected_commit"]),
            "owner_user_id": str(row["owner_user_id"]),
            "relay_base_url": str(row["relay_base_url"]),
            "timeout_seconds": int(row["timeout_seconds"]),
            "state": str(row["state"]),
            "batch_kind": str(row["batch_kind"]),
            "previous_evidence": json.loads(str(row["previous_evidence_json"])),
        }
