"""模型连接与密文的 SQLite Adapter。"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


_DDL = """
CREATE TABLE IF NOT EXISTS model_connections (
    connection_id   TEXT PRIMARY KEY,
    owner_scope     TEXT NOT NULL,
    owner_user_id   TEXT,
    preset_id       TEXT,
    preset_version  TEXT,
    display_name    TEXT NOT NULL,
    base_url        TEXT NOT NULL,
    model           TEXT NOT NULL,
    api_format      TEXT NOT NULL,
    locality        TEXT NOT NULL,
    secret_id       TEXT,
    status          TEXT NOT NULL,
    key_hint        TEXT NOT NULL DEFAULT '',
    verified_at     TEXT,
    compatibility_slot TEXT,
    created_by      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_connections_owner
ON model_connections(owner_user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS model_connection_secrets (
    secret_id       TEXT PRIMARY KEY,
    owner_user_id   TEXT,
    ciphertext      TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_connection_models (
    connection_id    TEXT NOT NULL,
    model_id         TEXT NOT NULL,
    display_name     TEXT NOT NULL,
    catalog_role     TEXT NOT NULL,
    catalog_version  TEXT NOT NULL,
    catalog_order    INTEGER NOT NULL,
    status           TEXT NOT NULL,
    enabled          INTEGER NOT NULL DEFAULT 0,
    verified_at      TEXT,
    error_code       TEXT,
    usage_status     TEXT NOT NULL DEFAULT 'unknown',
    native_usage_json TEXT NOT NULL DEFAULT '{}',
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (connection_id, model_id)
);
CREATE INDEX IF NOT EXISTS idx_model_connection_models_status
ON model_connection_models(connection_id, status, enabled);

CREATE TABLE IF NOT EXISTS model_connection_grants (
    grant_id         TEXT PRIMARY KEY,
    token_hash       TEXT NOT NULL UNIQUE,
    owner_user_id    TEXT NOT NULL,
    task_id          TEXT NOT NULL,
    revision         INTEGER NOT NULL,
    run_id           TEXT NOT NULL,
    connection_id    TEXT NOT NULL,
    secret_id        TEXT,
    purpose          TEXT NOT NULL,
    base_url         TEXT NOT NULL,
    model            TEXT NOT NULL,
    api_format       TEXT NOT NULL,
    locality         TEXT NOT NULL,
    expires_at       TEXT NOT NULL,
    revoked_at       TEXT,
    revoke_reason    TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_connection_grants_run
ON model_connection_grants(
    owner_user_id, task_id, revision, run_id, purpose
);

CREATE TABLE IF NOT EXISTS model_provider_usage (
    usage_id         TEXT PRIMARY KEY,
    grant_id         TEXT NOT NULL,
    owner_user_id    TEXT NOT NULL,
    task_id          TEXT NOT NULL,
    revision         INTEGER NOT NULL,
    run_id           TEXT NOT NULL,
    connection_id    TEXT NOT NULL,
    purpose          TEXT NOT NULL,
    status           TEXT NOT NULL,
    input_tokens     INTEGER,
    output_tokens    INTEGER,
    total_tokens     INTEGER,
    request_count    INTEGER NOT NULL DEFAULT 1,
    native_json      TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_provider_usage_task
ON model_provider_usage(owner_user_id, task_id, revision, created_at);

CREATE TABLE IF NOT EXISTS model_usage_preferences (
    owner_user_id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_connection_imports (
    source_scope TEXT NOT NULL,
    source_key TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    connection_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (source_scope, source_key, source_fingerprint)
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _public_row(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
) -> dict[str, object]:
    """连接公开摘要；刻意不选择 base_url、secret_id 或 ciphertext。"""

    models = conn.execute(
        "SELECT model_id, display_name, catalog_role, catalog_version, "
        "status, enabled, verified_at, error_code, usage_status "
        "FROM model_connection_models WHERE connection_id=? "
        "ORDER BY catalog_order, model_id",
        (row["connection_id"],),
    ).fetchall()
    default_model = row["model"] if row["status"] == "verified" else None
    public_models = [
        {
            "model_id": item["model_id"],
            "display_name": item["display_name"],
            "catalog_role": item["catalog_role"],
            "catalog_version": item["catalog_version"],
            "status": item["status"],
            "enabled": bool(item["enabled"]),
            "is_default": item["model_id"] == default_model,
            "verified_at": item["verified_at"],
            "error_code": item["error_code"],
            "usage_status": item["usage_status"],
        }
        for item in models
    ]
    result = {
        "connection_id": row["connection_id"],
        "owner_scope": row["owner_scope"],
        "preset_id": row["preset_id"],
        "preset_version": row["preset_version"],
        "display_name": row["display_name"],
        "model": row["model"],
        "api_format": row["api_format"],
        "locality": row["locality"],
        "status": row["status"],
        "key_hint": row["key_hint"],
        "verified_at": row["verified_at"],
        "default_model": default_model,
        "available_model_count": sum(
            item["status"] == "available" and bool(item["enabled"])
            for item in public_models
        ),
        "models": public_models,
    }
    return result


class ModelConnectionRepository:
    """Provider 连接专用 Repository，不复用通用 runtime_config KV。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as conn:
            conn.executescript(_DDL)
            self._migrate_personal_connection_cardinality(conn)
            self._migrate_connection_models(conn)

    @staticmethod
    def _migrate_personal_connection_cardinality(conn: sqlite3.Connection) -> None:
        """把旧版每用户每 Provider 单槽约束迁移成兼容槽，不改变旧连接语义。"""

        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(model_connections)").fetchall()
        }
        if "compatibility_slot" not in columns:
            conn.execute(
                "ALTER TABLE model_connections ADD COLUMN compatibility_slot TEXT"
            )
            # 升级前的个人连接来自旧 PUT；只把这些存量行归入可覆盖兼容槽。
            conn.execute(
                "UPDATE model_connections SET compatibility_slot='personal_preset_v1' "
                "WHERE owner_scope='user_personal'"
            )
        conn.execute("DROP INDEX IF EXISTS idx_model_connections_personal_preset")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_model_connections_personal_compatibility_slot "
            "ON model_connections(owner_user_id, preset_id, compatibility_slot) "
            "WHERE owner_scope='user_personal' AND compatibility_slot IS NOT NULL"
        )
        conn.commit()

    @staticmethod
    def _migrate_connection_models(conn: sqlite3.Connection) -> None:
        """把旧连接的单模型字段补成一个可用 ConnectionModel，重复执行不新增。"""

        conn.execute(
            """
            INSERT INTO model_connection_models (
                connection_id, model_id, display_name, catalog_role,
                catalog_version, catalog_order, status, enabled,
                verified_at, error_code, usage_status, native_usage_json, updated_at
            )
            SELECT
                c.connection_id, c.model, c.model, 'legacy',
                COALESCE(c.preset_version, 'legacy'), 0,
                CASE WHEN c.status='verified' THEN 'available' ELSE 'pending_validation' END,
                CASE WHEN c.status='verified' THEN 1 ELSE 0 END,
                c.verified_at, NULL, 'unknown', '{}', c.updated_at
            FROM model_connections AS c
            WHERE NOT EXISTS (
                SELECT 1 FROM model_connection_models AS m
                WHERE m.connection_id=c.connection_id
            )
            """
        )
        conn.commit()

    @staticmethod
    def _replace_connection_models(
        conn: sqlite3.Connection,
        *,
        connection_id: str,
        default_model: str,
        model_results: list[dict[str, object]],
        now: str,
        require_available_default: bool = True,
    ) -> None:
        conn.execute(
            "DELETE FROM model_connection_models WHERE connection_id=?",
            (connection_id,),
        )
        for order, item in enumerate(model_results):
            conn.execute(
                """
                INSERT INTO model_connection_models (
                    connection_id, model_id, display_name, catalog_role,
                    catalog_version, catalog_order, status, enabled,
                    verified_at, error_code, usage_status,
                    native_usage_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    connection_id,
                    item["model_id"],
                    item["display_name"],
                    item["catalog_role"],
                    item["catalog_version"],
                    order,
                    item["status"],
                    int(bool(item["enabled"])),
                    item.get("verified_at"),
                    item.get("error_code"),
                    item.get("usage_status", "unknown"),
                    item.get("native_usage_json", "{}"),
                    now,
                ),
            )
        if require_available_default and not any(
            item["model_id"] == default_model
            and item["status"] == "available"
            and bool(item["enabled"])
            for item in model_results
        ):
            raise ValueError("默认模型必须是当前连接中已启用的可用模型")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            # Windows 会锁住仍打开的 SQLite 文件，所有短事务都在退出时主动释放句柄。
            conn.close()

    def reencrypt_all_secrets(
        self,
        transform: Callable[[str], str],
    ) -> int:
        """在单一写事务中重加密全部在线 Provider Secret。"""

        with self._lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    "SELECT secret_id, ciphertext "
                    "FROM model_connection_secrets "
                    "WHERE ciphertext IS NOT NULL"
                ).fetchall()
                for row in rows:
                    conn.execute(
                        "UPDATE model_connection_secrets "
                        "SET ciphertext=? WHERE secret_id=?",
                        (transform(str(row["ciphertext"])), row["secret_id"]),
                    )
                conn.commit()
                return len(rows)
            except BaseException:
                conn.rollback()
                raise

    def upsert_personal(
        self,
        *,
        owner_user_id: str,
        preset_id: str,
        preset_version: str,
        display_name: str,
        base_url: str,
        model: str,
        api_format: str,
        ciphertext: str,
        key_hint: str,
        verified_at: str,
    ) -> dict[str, object]:
        """验证成功后原子替换个人连接及其在线密文。"""

        secret_id = str(uuid.uuid4())
        now = _now()
        with self._lock, self._conn() as conn:
            existing = conn.execute(
                "SELECT connection_id, secret_id FROM model_connections "
                "WHERE owner_scope='user_personal' AND owner_user_id=? AND preset_id=? "
                "AND compatibility_slot='personal_preset_v1'",
                (owner_user_id, preset_id),
            ).fetchone()
            connection_id = (
                existing["connection_id"] if existing else str(uuid.uuid4())
            )
            conn.execute(
                "INSERT INTO model_connection_secrets "
                "(secret_id, owner_user_id, ciphertext, created_at) VALUES (?, ?, ?, ?)",
                (secret_id, owner_user_id, ciphertext, now),
            )
            if existing:
                conn.execute(
                    "UPDATE model_connections SET preset_version=?, display_name=?, "
                    "base_url=?, model=?, api_format=?, locality='public_external', "
                    "secret_id=?, status='verified', key_hint=?, verified_at=?, updated_at=? "
                    "WHERE connection_id=?",
                    (
                        preset_version,
                        display_name,
                        base_url,
                        model,
                        api_format,
                        secret_id,
                        key_hint,
                        verified_at,
                        now,
                        connection_id,
                    ),
                )
                self._replace_connection_models(
                    conn,
                    connection_id=connection_id,
                    default_model=model,
                    model_results=[
                        {
                            "model_id": model,
                            "display_name": model,
                            "catalog_role": "legacy",
                            "catalog_version": preset_version,
                            "status": "available",
                            "enabled": True,
                            "verified_at": verified_at,
                            "error_code": None,
                            "usage_status": "unknown",
                        }
                    ],
                    now=now,
                )
                if existing["secret_id"]:
                    conn.execute(
                        "DELETE FROM model_connection_secrets WHERE secret_id=?",
                        (existing["secret_id"],),
                    )
            else:
                conn.execute(
                    "INSERT INTO model_connections "
                    "(connection_id, owner_scope, owner_user_id, preset_id, "
                    "preset_version, display_name, base_url, model, api_format, "
                    "locality, secret_id, status, key_hint, verified_at, "
                    "compatibility_slot, created_by, created_at, updated_at) "
                    "VALUES (?, 'user_personal', ?, ?, ?, ?, ?, ?, ?, "
                    "'public_external', ?, 'verified', ?, ?, 'personal_preset_v1', "
                    "?, ?, ?)",
                    (
                        connection_id,
                        owner_user_id,
                        preset_id,
                        preset_version,
                        display_name,
                        base_url,
                        model,
                        api_format,
                        secret_id,
                        key_hint,
                        verified_at,
                        owner_user_id,
                        now,
                        now,
                    ),
                )
                self._replace_connection_models(
                    conn,
                    connection_id=connection_id,
                    default_model=model,
                    model_results=[
                        {
                            "model_id": model,
                            "display_name": model,
                            "catalog_role": "legacy",
                            "catalog_version": preset_version,
                            "status": "available",
                            "enabled": True,
                            "verified_at": verified_at,
                            "error_code": None,
                            "usage_status": "unknown",
                        }
                    ],
                    now=now,
                )
            conn.commit()
        item = self.get_public(connection_id, owner_user_id)
        if item is None:
            raise RuntimeError("个人模型连接保存后无法重读")
        return item

    def create_personal(
        self,
        *,
        owner_user_id: str,
        preset_id: str,
        preset_version: str,
        display_name: str,
        base_url: str,
        model: str,
        api_format: str,
        ciphertext: str,
        key_hint: str,
        verified_at: str,
        model_results: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        """保存一套新的命名个人连接；同 Provider 的其他连接不受影响。"""

        connection_id = str(uuid.uuid4())
        secret_id = str(uuid.uuid4())
        now = _now()
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO model_connection_secrets "
                "(secret_id, owner_user_id, ciphertext, created_at) VALUES (?, ?, ?, ?)",
                (secret_id, owner_user_id, ciphertext, now),
            )
            conn.execute(
                "INSERT INTO model_connections "
                "(connection_id, owner_scope, owner_user_id, preset_id, "
                "preset_version, display_name, base_url, model, api_format, "
                "locality, secret_id, status, key_hint, verified_at, "
                "compatibility_slot, created_by, created_at, updated_at) "
                "VALUES (?, 'user_personal', ?, ?, ?, ?, ?, ?, ?, "
                "'public_external', ?, 'verified', ?, ?, NULL, ?, ?, ?)",
                (
                    connection_id,
                    owner_user_id,
                    preset_id,
                    preset_version,
                    display_name,
                    base_url,
                    model,
                    api_format,
                    secret_id,
                    key_hint,
                    verified_at,
                    owner_user_id,
                    now,
                    now,
                ),
            )
            self._replace_connection_models(
                conn,
                connection_id=connection_id,
                default_model=model,
                model_results=model_results
                or [
                    {
                        "model_id": model,
                        "display_name": model,
                        "catalog_role": "legacy",
                        "catalog_version": preset_version,
                        "status": "available",
                        "enabled": True,
                        "verified_at": verified_at,
                        "error_code": None,
                        "usage_status": "unknown",
                    }
                ],
                now=now,
            )
            conn.commit()
        item = self.get_public(connection_id, owner_user_id)
        if item is None:
            raise RuntimeError("个人模型连接创建后无法重读")
        return item

    def get_public(
        self,
        connection_id: str,
        owner_user_id: str,
    ) -> dict[str, object] | None:
        """按 Owner 读取个人连接公开摘要。"""

        with self._conn() as conn:
            row = conn.execute(
                "SELECT connection_id, owner_scope, preset_id, preset_version, "
                "display_name, model, api_format, locality, status, key_hint, verified_at "
                "FROM model_connections WHERE connection_id=? AND owner_user_id=?",
                (connection_id, owner_user_id),
            ).fetchone()
            return _public_row(conn, row) if row else None

    def get_authorized_internal(
        self,
        connection_id: str,
        owner_user_id: str,
    ) -> dict[str, object] | None:
        """只供 Broker 签发 Grant；调用者仍不能直接取得该方法的结果。"""

        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT
                    c.connection_id, c.owner_scope, c.owner_user_id,
                    c.base_url, c.model, c.api_format, c.locality,
                    c.secret_id, c.status, s.ciphertext
                FROM model_connections AS c
                LEFT JOIN model_connection_secrets AS s
                    ON s.secret_id=c.secret_id
                WHERE c.connection_id=?
                  AND c.status='verified'
                  AND (
                    (c.owner_scope='user_personal' AND c.owner_user_id=?)
                    OR c.owner_scope='platform_shared'
                  )
                """,
                (connection_id, owner_user_id),
            ).fetchone()
        return dict(row) if row else None

    def connection_model_available(self, connection_id: str, model_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM model_connection_models "
                "WHERE connection_id=? AND model_id=? "
                "AND status='available' AND enabled=1",
                (connection_id, model_id),
            ).fetchone()
        return row is not None

    def get_model_context(
        self,
        connection_id: str,
        actor_user_id: str,
        *,
        can_manage: bool,
    ) -> dict[str, object] | None:
        """只供 Broker 管理连接模型；返回密文但不穿透产品 Interface。"""

        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT
                    c.connection_id, c.owner_scope, c.owner_user_id, c.preset_id,
                    c.preset_version, c.base_url, c.api_format, c.locality, c.secret_id,
                    c.status, c.model, s.ciphertext
                FROM model_connections AS c
                LEFT JOIN model_connection_secrets AS s ON s.secret_id=c.secret_id
                WHERE c.connection_id=?
                  AND (
                    (c.owner_scope='user_personal' AND c.owner_user_id=?)
                    OR (c.owner_scope='platform_shared' AND ?)
                  )
                """,
                (connection_id, actor_user_id, int(can_manage)),
            ).fetchone()
            if row is None:
                return None
            models = conn.execute(
                "SELECT model_id, status, enabled "
                "FROM model_connection_models WHERE connection_id=?",
                (connection_id,),
            ).fetchall()
        result = dict(row)
        result["models"] = [dict(item) for item in models]
        return result

    def update_model_results(
        self,
        *,
        connection_id: str,
        actor_user_id: str,
        can_manage: bool,
        model_results: list[dict[str, object]],
    ) -> dict[str, object] | None:
        """更新指定模型的验证结果，不改动默认模型或其他子项。"""

        now = _now()
        with self._lock, self._conn() as conn:
            owner = conn.execute(
                "SELECT 1 FROM model_connections WHERE connection_id=? AND "
                "((owner_scope='user_personal' AND owner_user_id=?) "
                "OR (owner_scope='platform_shared' AND ?))",
                (connection_id, actor_user_id, int(can_manage)),
            ).fetchone()
            if owner is None:
                return None
            for item in model_results:
                cursor = conn.execute(
                    """
                    UPDATE model_connection_models
                    SET status=?, enabled=?, verified_at=?, error_code=?,
                        usage_status=?, native_usage_json=?, updated_at=?
                    WHERE connection_id=? AND model_id=?
                    """,
                    (
                        item["status"],
                        int(bool(item["enabled"])),
                        item.get("verified_at"),
                        item.get("error_code"),
                        item.get("usage_status", "unknown"),
                        item.get("native_usage_json", "{}"),
                        now,
                        connection_id,
                        item["model_id"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError("待重验模型不属于当前连接")
            current = conn.execute(
                "SELECT status, model FROM model_connections WHERE connection_id=?",
                (connection_id,),
            ).fetchone()
            available = conn.execute(
                "SELECT model_id FROM model_connection_models "
                "WHERE connection_id=? AND status='available' AND enabled=1 "
                "ORDER BY catalog_order LIMIT 1",
                (connection_id,),
            ).fetchone()
            if (
                current
                and current["status"] == "pending_validation"
                and available is not None
            ):
                conn.execute(
                    "UPDATE model_connections SET model=?, status='verified', "
                    "verified_at=?, updated_at=? WHERE connection_id=?",
                    (available["model_id"], now, now, connection_id),
                )
            conn.execute(
                "UPDATE model_connections SET updated_at=? WHERE connection_id=?",
                (now, connection_id),
            )
            conn.commit()
        return self.get_public_authorized(
            connection_id,
            actor_user_id,
            can_manage=can_manage,
        )

    def set_default_model(
        self,
        *,
        connection_id: str,
        actor_user_id: str,
        can_manage: bool,
        model_id: str,
    ) -> dict[str, object] | None:
        """显式选择一个已启用的可用模型，并恢复连接可用状态。"""

        now = _now()
        with self._lock, self._conn() as conn:
            owner = conn.execute(
                "SELECT 1 FROM model_connections WHERE connection_id=? AND "
                "((owner_scope='user_personal' AND owner_user_id=?) "
                "OR (owner_scope='platform_shared' AND ?))",
                (connection_id, actor_user_id, int(can_manage)),
            ).fetchone()
            if owner is None:
                return None
            model = conn.execute(
                "SELECT status, enabled FROM model_connection_models "
                "WHERE connection_id=? AND model_id=?",
                (connection_id, model_id),
            ).fetchone()
            if (
                model is None
                or model["status"] != "available"
                or not bool(model["enabled"])
            ):
                raise ValueError("默认模型必须是当前连接中已启用的可用模型")
            conn.execute(
                "UPDATE model_connections SET model=?, status='verified', updated_at=? "
                "WHERE connection_id=?",
                (model_id, now, connection_id),
            )
            conn.commit()
        return self.get_public_authorized(
            connection_id,
            actor_user_id,
            can_manage=can_manage,
        )

    def set_model_enabled(
        self,
        *,
        connection_id: str,
        actor_user_id: str,
        can_manage: bool,
        model_id: str,
        enabled: bool,
    ) -> dict[str, object] | None:
        """独立启停模型；停用当前默认时要求用户重新选择，禁止静默切换。"""

        now = _now()
        with self._lock, self._conn() as conn:
            connection = conn.execute(
                "SELECT model FROM model_connections WHERE connection_id=? AND "
                "((owner_scope='user_personal' AND owner_user_id=?) "
                "OR (owner_scope='platform_shared' AND ?))",
                (connection_id, actor_user_id, int(can_manage)),
            ).fetchone()
            if connection is None:
                return None
            model = conn.execute(
                "SELECT status FROM model_connection_models "
                "WHERE connection_id=? AND model_id=?",
                (connection_id, model_id),
            ).fetchone()
            if model is None:
                raise ValueError("连接模型不存在")
            if enabled:
                if model["status"] != "disabled":
                    raise ValueError("只有已停用模型可以重新启用")
                next_status = "available"
            else:
                if model["status"] != "available":
                    raise ValueError("只有可用模型可以停用")
                next_status = "disabled"
            conn.execute(
                "UPDATE model_connection_models "
                "SET status=?, enabled=?, error_code=NULL, updated_at=? "
                "WHERE connection_id=? AND model_id=?",
                (
                    next_status,
                    int(enabled),
                    now,
                    connection_id,
                    model_id,
                ),
            )
            if not enabled and connection["model"] == model_id:
                conn.execute(
                    "UPDATE model_connections "
                    "SET status='needs_default_model', updated_at=? "
                    "WHERE connection_id=?",
                    (now, connection_id),
                )
            else:
                conn.execute(
                    "UPDATE model_connections SET updated_at=? WHERE connection_id=?",
                    (now, connection_id),
                )
            conn.commit()
        return self.get_public_authorized(
            connection_id,
            actor_user_id,
            can_manage=can_manage,
        )

    def get_public_authorized(
        self,
        connection_id: str,
        actor_user_id: str,
        *,
        can_manage: bool,
    ) -> dict[str, object] | None:
        """读取调用者可管理的连接摘要。"""

        with self._conn() as conn:
            row = conn.execute(
                "SELECT connection_id, owner_scope, preset_id, preset_version, "
                "display_name, model, api_format, locality, status, key_hint, verified_at "
                "FROM model_connections WHERE connection_id=? AND "
                "((owner_scope='user_personal' AND owner_user_id=?) "
                "OR (owner_scope='platform_shared' AND ?))",
                (connection_id, actor_user_id, int(can_manage)),
            ).fetchone()
            return _public_row(conn, row) if row else None

    def list_available(
        self,
        owner_user_id: str,
        *,
        expose_managed_key_hint: bool = False,
    ) -> list[dict[str, object]]:
        """列出 Owner 的个人连接和平台已发布的管理连接。"""

        with self._conn() as conn:
            rows = conn.execute(
                "SELECT connection_id, owner_scope, preset_id, preset_version, "
                "display_name, model, api_format, locality, status, key_hint, verified_at "
                "FROM model_connections "
                "WHERE (owner_scope='user_personal' AND owner_user_id=?) "
                "OR (owner_scope='platform_shared' AND (status='verified' OR ?)) "
                "ORDER BY updated_at DESC",
                (owner_user_id, int(expose_managed_key_hint)),
            ).fetchall()
            items = [_public_row(conn, row) for row in rows]
        for item in items:
            if (
                item["owner_scope"] == "platform_shared"
                and not expose_managed_key_hint
            ):
                # 平台密钥不属于当前普通用户，连尾号也不应作为可见元数据泄露。
                item["key_hint"] = ""
        return items

    def set_usage_preference(
        self,
        owner_user_id: str,
        connection_id: str,
        model_id: str,
    ) -> dict[str, object]:
        """保存用户自己的默认连接和已验证模型。"""

        with self._lock, self._conn() as conn:
            row = conn.execute(
                """
                SELECT c.connection_id, c.owner_scope, c.display_name
                FROM model_connections AS c
                JOIN model_connection_models AS m
                  ON m.connection_id=c.connection_id
                WHERE c.connection_id=? AND c.status='verified'
                  AND m.model_id=? AND m.status='available' AND m.enabled=1
                  AND (
                    (c.owner_scope='user_personal' AND c.owner_user_id=?)
                    OR c.owner_scope='platform_shared'
                  )
                """,
                (connection_id, model_id, owner_user_id),
            ).fetchone()
            if row is None:
                raise ValueError("默认模型必须属于当前用户可用的已验证连接")
            now = _now()
            conn.execute(
                "INSERT INTO model_usage_preferences "
                "(owner_user_id, connection_id, model_id, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(owner_user_id) DO UPDATE SET "
                "connection_id=excluded.connection_id, model_id=excluded.model_id, "
                "updated_at=excluded.updated_at",
                (owner_user_id, connection_id, model_id, now),
            )
            conn.commit()
        return self.get_usage_preference(owner_user_id) or {}

    def get_usage_preference(self, owner_user_id: str) -> dict[str, object] | None:
        """读取偏好；连接失效时返回失效状态，不自动选择替代连接。"""

        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT p.connection_id, p.model_id, p.updated_at,
                       c.display_name, c.owner_scope, c.status AS connection_status,
                       m.status AS model_status, m.enabled
                FROM model_usage_preferences AS p
                LEFT JOIN model_connections AS c ON c.connection_id=p.connection_id
                LEFT JOIN model_connection_models AS m
                  ON m.connection_id=p.connection_id AND m.model_id=p.model_id
                WHERE p.owner_user_id=?
                """,
                (owner_user_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["available"] = bool(
            row["connection_status"] == "verified"
            and row["model_status"] == "available"
            and row["enabled"]
        )
        return result

    def create_managed(
        self,
        *,
        created_by: str,
        display_name: str,
        base_url: str,
        model: str,
        api_format: str,
        locality: str,
        ciphertext: str | None,
        key_hint: str,
        verified_at: str,
        preset_id: str | None = None,
        preset_version: str | None = None,
        model_results: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        """保存一个已验证、精确冻结并发布的平台管理连接。"""

        connection_id = str(uuid.uuid4())
        secret_id = str(uuid.uuid4()) if ciphertext else None
        now = _now()
        with self._lock, self._conn() as conn:
            if secret_id and ciphertext:
                conn.execute(
                    "INSERT INTO model_connection_secrets "
                    "(secret_id, owner_user_id, ciphertext, created_at) "
                    "VALUES (?, NULL, ?, ?)",
                    (secret_id, ciphertext, now),
                )
            conn.execute(
                "INSERT INTO model_connections "
                "(connection_id, owner_scope, owner_user_id, preset_id, "
                "preset_version, display_name, base_url, model, api_format, "
                "locality, secret_id, status, key_hint, verified_at, created_by, "
                "created_at, updated_at) "
                "VALUES (?, 'platform_shared', NULL, ?, ?, ?, ?, ?, ?, ?, "
                "?, 'verified', ?, ?, ?, ?, ?)",
                (
                    connection_id,
                    preset_id,
                    preset_version,
                    display_name,
                    base_url,
                    model,
                    api_format,
                    locality,
                    secret_id,
                    key_hint,
                    verified_at,
                    created_by,
                    now,
                    now,
                ),
            )
            self._replace_connection_models(
                conn,
                connection_id=connection_id,
                default_model=model,
                model_results=model_results or [
                    {
                        "model_id": model,
                        "display_name": model,
                        "catalog_role": "managed",
                        "catalog_version": preset_version or "managed",
                        "status": "available",
                        "enabled": True,
                        "verified_at": verified_at,
                        "error_code": None,
                        "usage_status": "unknown",
                    }
                ],
                now=now,
            )
            conn.commit()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT connection_id, owner_scope, preset_id, preset_version, "
                "display_name, model, api_format, locality, status, key_hint, verified_at "
                "FROM model_connections WHERE connection_id=?",
                (connection_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("管理模型连接保存后无法重读")
            return _public_row(conn, row)

    def create_imported(
        self,
        *,
        source_scope: str,
        source_key: str,
        source_fingerprint: str,
        owner_scope: str,
        owner_user_id: str | None,
        created_by: str,
        display_name: str,
        base_url: str,
        model: str,
        api_format: str,
        locality: str,
        ciphertext: str | None,
        key_hint: str,
        preset_id: str | None,
        preset_version: str | None,
        model_results: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        """幂等复制旧配置；导入项始终等待用户显式验证。"""

        with self._lock, self._conn() as conn:
            existing = conn.execute(
                "SELECT connection_id FROM model_connection_imports "
                "WHERE source_scope=? AND source_key=? AND source_fingerprint=?",
                (source_scope, source_key, source_fingerprint),
            ).fetchone()
            if existing:
                connection_id = str(existing["connection_id"])
                row = conn.execute(
                    "SELECT connection_id, owner_scope, preset_id, preset_version, "
                    "display_name, model, api_format, locality, status, key_hint, verified_at "
                    "FROM model_connections WHERE connection_id=?",
                    (connection_id,),
                ).fetchone()
                if row:
                    return _public_row(conn, row)
            connection_id = str(uuid.uuid4())
            secret_id = str(uuid.uuid4()) if ciphertext else None
            now = _now()
            if secret_id and ciphertext:
                conn.execute(
                    "INSERT INTO model_connection_secrets "
                    "(secret_id, owner_user_id, ciphertext, created_at) VALUES (?, ?, ?, ?)",
                    (secret_id, owner_user_id, ciphertext, now),
                )
            conn.execute(
                "INSERT INTO model_connections "
                "(connection_id, owner_scope, owner_user_id, preset_id, preset_version, "
                "display_name, base_url, model, api_format, locality, secret_id, status, "
                "key_hint, verified_at, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_validation', ?, NULL, ?, ?, ?)",
                (
                    connection_id, owner_scope, owner_user_id, preset_id, preset_version,
                    display_name, base_url, model, api_format, locality, secret_id,
                    key_hint, created_by, now, now,
                ),
            )
            self._replace_connection_models(
                conn,
                connection_id=connection_id,
                default_model=model,
                model_results=model_results or [{
                    "model_id": model,
                    "display_name": model,
                    "catalog_role": "legacy_imported",
                    "catalog_version": preset_version or "legacy_imported",
                    "status": "pending_validation",
                    "enabled": False,
                    "verified_at": None,
                    "error_code": None,
                    "usage_status": "unknown",
                }],
                now=now,
                require_available_default=False,
            )
            conn.execute(
                "INSERT INTO model_connection_imports "
                "(source_scope, source_key, source_fingerprint, connection_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (source_scope, source_key, source_fingerprint, connection_id, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT connection_id, owner_scope, preset_id, preset_version, "
                "display_name, model, api_format, locality, status, key_hint, verified_at "
                "FROM model_connections WHERE connection_id=?",
                (connection_id,),
            ).fetchone()
            assert row is not None
            return _public_row(conn, row)

    def set_platform_enabled(
        self,
        connection_id: str,
        *,
        enabled: bool,
    ) -> dict[str, object] | None:
        """启停平台连接；停用时立即撤销该连接全部在线 Grant。"""

        now = _now()
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT model FROM model_connections "
                "WHERE connection_id=? AND owner_scope='platform_shared'",
                (connection_id,),
            ).fetchone()
            if row is None:
                return None
            if enabled:
                model = conn.execute(
                    "SELECT 1 FROM model_connection_models "
                    "WHERE connection_id=? AND model_id=? "
                    "AND status='available' AND enabled=1",
                    (connection_id, row["model"]),
                ).fetchone()
                if model is None:
                    raise ValueError("平台连接没有可用的默认模型")
                status = "verified"
            else:
                status = "disabled"
                conn.execute(
                    "UPDATE model_connection_grants "
                    "SET revoked_at=COALESCE(revoked_at, ?), "
                    "revoke_reason=COALESCE(revoke_reason, 'connection_disabled') "
                    "WHERE connection_id=?",
                    (now, connection_id),
                )
            conn.execute(
                "UPDATE model_connections SET status=?, updated_at=? "
                "WHERE connection_id=?",
                (status, now, connection_id),
            )
            conn.commit()
        return self.get_public_authorized(connection_id, "", can_manage=True)

    def delete_authorized(
        self,
        connection_id: str,
        actor_user_id: str,
        *,
        can_manage: bool,
    ) -> bool:
        """Owner 可删个人连接；管理权限可删平台连接，其他情况统一失败。"""

        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT owner_scope, owner_user_id, secret_id "
                "FROM model_connections WHERE connection_id=?",
                (connection_id,),
            ).fetchone()
            if row is None:
                return False
            allowed = (
                row["owner_scope"] == "user_personal"
                and row["owner_user_id"] == actor_user_id
            ) or (
                row["owner_scope"] == "platform_shared" and can_manage
            )
            if not allowed:
                return False
            conn.execute(
                "UPDATE model_connection_grants "
                "SET revoked_at=COALESCE(revoked_at, ?), "
                "revoke_reason=COALESCE(revoke_reason, 'connection_deleted') "
                "WHERE connection_id=?",
                (_now(), connection_id),
            )
            conn.execute(
                "DELETE FROM model_connections WHERE connection_id=?",
                (connection_id,),
            )
            conn.execute(
                "DELETE FROM model_connection_models WHERE connection_id=?",
                (connection_id,),
            )
            if row["secret_id"]:
                conn.execute(
                    "DELETE FROM model_connection_secrets WHERE secret_id=?",
                    (row["secret_id"],),
                )
            conn.commit()
        return True

    def create_grant(
        self,
        *,
        grant_id: str,
        token_hash: str,
        owner_user_id: str,
        task_id: str,
        revision: int,
        run_id: str,
        connection: dict[str, object],
        purpose: str,
        expires_at: str,
    ) -> None:
        """只保存 Grant 哈希和冻结连接版本，不保存可用 Token。"""

        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO model_connection_grants (
                    grant_id, token_hash, owner_user_id, task_id,
                    revision, run_id, connection_id, secret_id,
                    purpose, base_url, model, api_format, locality,
                    expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    grant_id,
                    token_hash,
                    owner_user_id,
                    task_id,
                    revision,
                    run_id,
                    connection["connection_id"],
                    connection["secret_id"],
                    purpose,
                    connection["base_url"],
                    connection["model"],
                    connection["api_format"],
                    connection["locality"],
                    expires_at,
                    _now(),
                ),
            )
            conn.commit()

    def resolve_grant(self, token_hash: str) -> dict[str, object] | None:
        """按不可逆 Token 哈希读取 Grant，并校验连接版本仍在线。"""

        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT
                    g.*, c.status AS connection_status,
                    c.secret_id AS current_secret_id,
                    s.ciphertext
                FROM model_connection_grants AS g
                JOIN model_connections AS c
                    ON c.connection_id=g.connection_id
                LEFT JOIN model_connection_secrets AS s
                    ON s.secret_id=g.secret_id
                WHERE g.token_hash=?
                """,
                (token_hash,),
            ).fetchone()
        return dict(row) if row else None

    def revoke_grant(self, grant_id: str, reason: str) -> bool:
        """幂等撤销一个仍有效的 Grant。"""

        with self._lock, self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE model_connection_grants
                SET revoked_at=COALESCE(revoked_at, ?),
                    revoke_reason=COALESCE(revoke_reason, ?)
                WHERE grant_id=?
                """,
                (_now(), reason, grant_id),
            )
            conn.commit()
        return cursor.rowcount == 1

    def revoke_run_grants(
        self,
        owner_user_id: str,
        task_id: str,
        revision: int,
        run_id: str,
        *,
        reason: str,
    ) -> int:
        """撤销一次 Run 的全部用途 Grant，供终态和恢复轮换使用。"""

        with self._lock, self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE model_connection_grants
                SET revoked_at=COALESCE(revoked_at, ?),
                    revoke_reason=COALESCE(revoke_reason, ?)
                WHERE owner_user_id=? AND task_id=? AND revision=?
                  AND run_id=? AND revoked_at IS NULL
                """,
                (
                    _now(),
                    reason,
                    owner_user_id,
                    task_id,
                    revision,
                    run_id,
                ),
            )
            conn.commit()
        return cursor.rowcount

    def revoke_revision_grants(
        self,
        owner_user_id: str,
        task_id: str,
        revision: int,
        *,
        reason: str,
    ) -> int:
        """服务重启后仍可按 Owner + TaskRevision 关闭遗留 Grant。"""

        with self._lock, self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE model_connection_grants
                SET revoked_at=COALESCE(revoked_at, ?),
                    revoke_reason=COALESCE(revoke_reason, ?)
                WHERE owner_user_id=? AND task_id=? AND revision=?
                  AND revoked_at IS NULL
                """,
                (
                    _now(),
                    reason,
                    owner_user_id,
                    task_id,
                    revision,
                ),
            )
            conn.commit()
        return cursor.rowcount

    def record_usage(
        self,
        *,
        grant: dict[str, object],
        status: str,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
        native_json: str,
    ) -> None:
        """记录 Provider 原生用量；未知值保持 NULL，不进行价格推算。"""

        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO model_provider_usage (
                    usage_id, grant_id, owner_user_id, task_id,
                    revision, run_id, connection_id, purpose, status,
                    input_tokens, output_tokens, total_tokens,
                    request_count, native_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    grant["grant_id"],
                    grant["owner_user_id"],
                    grant["task_id"],
                    grant["revision"],
                    grant["run_id"],
                    grant["connection_id"],
                    grant["purpose"],
                    status,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    native_json,
                    _now(),
                ),
            )
            conn.commit()

    def list_usage(
        self,
        owner_user_id: str,
        *,
        task_id: str,
        revision: int,
    ) -> list[dict[str, object]]:
        """按任务 Owner 返回最小用量摘要，不暴露 Provider 响应正文。"""

        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT purpose, status, input_tokens, output_tokens,
                       total_tokens, request_count
                FROM model_provider_usage
                WHERE owner_user_id=? AND task_id=? AND revision=?
                ORDER BY created_at, usage_id
                """,
                (owner_user_id, task_id, revision),
            ).fetchall()
        return [dict(row) for row in rows]
