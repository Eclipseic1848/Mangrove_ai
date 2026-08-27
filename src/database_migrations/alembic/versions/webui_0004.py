"""把运行时配置明文 Secret 迁入共享 Vault/SecretRef 边界。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import uuid
from uuid import UUID

from alembic import op
from cryptography.fernet import Fernet, InvalidToken


revision: str = "webui_0004"
down_revision: str | None = "webui_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
operation_summary = (
    "创建独立 runtime_config_secrets 密文表并复用模型连接 Vault key",
    "把 25 个 secret=True 运行时配置值原子替换为 Owner/配置键绑定的 SecretRef",
    "校验未知 SecretRef、Owner 漂移与坏密文并失败关闭",
)

_EXPECTED_COLUMNS = {
    "secret_id",
    "owner_scope",
    "config_key",
    "ciphertext",
    "created_at",
}
_SECRET_REF_PREFIX = "secretref:runtime-config:"
_KEYRING_SCHEMA_VERSION = "fernet-keyring-v1"
# 历史 revision 必须自包含冻结迁移时的 25 项集合；运行时 Registry 另由测试
# 校验同步，不能让未来正常增删配置键静默改变已发布 revision 的语义。
_RUNTIME_CONFIG_SECRET_KEYS = frozenset(
    {
        "anysearch_api_key",
        "deepseek_api_key",
        "embedding_api_key",
        "firecrawl_api_key",
        "jd_cookie",
        "mc_cookie_bili",
        "mc_cookie_dy",
        "mc_cookie_ks",
        "mc_cookie_tieba",
        "mc_cookie_wb",
        "mc_cookie_xhs",
        "mc_cookie_zhihu",
        "mc_kdl_secret_id",
        "mc_kdl_signature",
        "mc_kdl_user_pwd",
        "mc_static_proxy_url",
        "mc_wandou_app_key",
        "mysql_password",
        "pdd_cookie",
        "qwen_api_key",
        "rerank_api_key",
        "slack_webhook_url",
        "smtp_password",
        "tavily_api_key",
        "tb_cookie",
    }
)


def _secret_ref(secret_id: str) -> str:
    return f"{_SECRET_REF_PREFIX}{UUID(secret_id)}"


def _parse_secret_ref(value: str) -> str:
    if not isinstance(value, str) or not value.startswith(_SECRET_REF_PREFIX):
        raise ValueError("SecretRef 格式无效")
    try:
        return str(UUID(value.removeprefix(_SECRET_REF_PREFIX)))
    except (ValueError, AttributeError) as exc:
        raise ValueError("SecretRef 格式无效") from exc


def _vault_key_path(database: Path) -> Path:
    return database.with_name(f"{database.name}.model-connections.key")


class _FrozenVault:
    """冻结 webui_0004 所需的 keyring-v1/Fernet 最小读写语义。"""

    def __init__(self, active_key: bytes, inactive_keys: tuple[bytes, ...]) -> None:
        self._encryptor = Fernet(active_key)
        self._decryptors = tuple(
            Fernet(key) for key in (active_key, *inactive_keys)
        )

    def encrypt(self, plaintext: str) -> str:
        return self._encryptor.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            encoded = ciphertext.encode("ascii")
        except UnicodeError as exc:
            raise ValueError("密文无法解密") from exc
        for decryptor in self._decryptors:
            try:
                return decryptor.decrypt(encoded).decode("utf-8")
            except (InvalidToken, UnicodeError):
                continue
        raise ValueError("密文无法解密")


def _load_vault(database: Path) -> _FrozenVault:
    key_path = _vault_key_path(database)
    if not key_path.is_file():
        raise RuntimeError("运行时配置 Vault 不可用")
    try:
        encoded = key_path.read_bytes().strip()
        if encoded.startswith(b"{"):
            payload = json.loads(encoded.decode("utf-8"))
            if payload["schema_version"] != _KEYRING_SCHEMA_VERSION:
                raise ValueError
            active_id = str(payload["active_key_id"])
            keys = {
                str(key_id): str(value).encode("ascii")
                for key_id, value in payload["keys"].items()
            }
            active = keys.pop(active_id)
            return _FrozenVault(active, tuple(keys.values()))
        return _FrozenVault(encoded, ())
    except (OSError, KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise RuntimeError("运行时配置 Vault 不可用") from exc


def _load_or_create_vault(database: Path) -> _FrozenVault:
    key_path = _vault_key_path(database)
    if key_path.is_file():
        return _load_vault(database)
    encrypted_rows = 0
    with sqlite3.connect(
        f"{database.resolve().as_uri()}?mode=ro", uri=True
    ) as readonly:
        for table in ("model_connection_secrets", "runtime_config_secrets"):
            exists = readonly.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if exists is not None:
                encrypted_rows += int(
                    readonly.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                )
    if encrypted_rows:
        raise RuntimeError("运行时配置 Vault 不可用")
    try:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with key_path.open("xb") as handle:
                handle.write(Fernet.generate_key())
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                pass
        except FileExistsError:
            pass
        return _load_vault(database)
    except (OSError, RuntimeError) as exc:
        raise RuntimeError("运行时配置 Vault 不可用") from exc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _database_path(connection: sqlite3.Connection) -> Path:
    row = connection.execute(
        "SELECT file FROM pragma_database_list WHERE name='main'"
    ).fetchone()
    if row is None or not str(row[0]).strip():
        raise RuntimeError("运行时配置 Vault 无法绑定数据库")
    return Path(str(row[0]))


def _install_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_config_secrets (
            secret_id   TEXT PRIMARY KEY,
            owner_scope TEXT NOT NULL,
            config_key  TEXT NOT NULL,
            ciphertext  TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            UNIQUE(owner_scope, config_key, secret_id)
        )
        """
    )
    columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(runtime_config_secrets)"
        ).fetchall()
    }
    if columns != _EXPECTED_COLUMNS:
        raise RuntimeError("runtime_config_secrets 检测到未知部分 Schema")


def _resolve_existing(
    connection: sqlite3.Connection,
    *,
    scope: str,
    key: str,
    value: str,
    vault,
) -> None:
    try:
        secret_id = _parse_secret_ref(value)
    except ValueError as exc:
        raise RuntimeError("运行时配置 SecretRef 格式无效") from exc
    row = connection.execute(
        "SELECT ciphertext FROM runtime_config_secrets "
        "WHERE secret_id=? AND owner_scope=? AND config_key=?",
        (secret_id, scope, key),
    ).fetchone()
    if row is None:
        raise RuntimeError("运行时配置 SecretRef 身份无效")
    try:
        vault.decrypt(str(row[0]))
    except ValueError as exc:
        raise RuntimeError("运行时配置 SecretRef 密文无法解密") from exc


def _migrate_plaintext(
    connection: sqlite3.Connection,
    *,
    scope: str,
    key: str,
    plaintext: str,
    vault,
) -> None:
    secret_id = str(uuid.uuid4())
    opaque_ref = _secret_ref(secret_id)
    connection.execute(
        "INSERT INTO runtime_config_secrets "
        "(secret_id, owner_scope, config_key, ciphertext, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (secret_id, scope, key, vault.encrypt(plaintext), _now()),
    )
    changed = connection.execute(
        "UPDATE runtime_config SET value=? "
        "WHERE scope=? AND key=? AND value=?",
        (opaque_ref, scope, key, plaintext),
    ).rowcount
    if changed != 1:
        raise RuntimeError("运行时配置 Secret 迁移检测到并发改写")


def _validate_no_orphans(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT secret_id, owner_scope, config_key FROM runtime_config_secrets"
    ).fetchall()
    for secret_id, scope, key in rows:
        expected_ref = _secret_ref(str(secret_id))
        linked = connection.execute(
            "SELECT 1 FROM runtime_config "
            "WHERE scope=? AND key=? AND value=?",
            (scope, key, expected_ref),
        ).fetchone()
        if linked is None:
            raise RuntimeError("runtime_config_secrets 存在未绑定密文")


def upgrade() -> None:
    """在 Alembic 单事务内完成加密与 ref 替换，任一坏值整批回滚。"""

    connection = op.get_bind().connection.driver_connection
    # SQLite 可能在自由页保留被 UPDATE 替换的旧 payload；迁移 Secret 前强制
    # secure_delete，保证新数据库副本/备份的字节扫描不会再命中旧明文。
    if int(connection.execute("PRAGMA secure_delete=ON").fetchone()[0]) != 1:
        raise RuntimeError("SQLite secure_delete 无法启用")
    _install_schema(connection)
    placeholders = ", ".join("?" for _ in _RUNTIME_CONFIG_SECRET_KEYS)
    rows = connection.execute(
        "SELECT scope, key, value FROM runtime_config "
        f"WHERE key IN ({placeholders}) ORDER BY scope, key",
        tuple(sorted(_RUNTIME_CONFIG_SECRET_KEYS)),
    ).fetchall()
    if not rows:
        _validate_no_orphans(connection)
        return
    try:
        database_path = _database_path(connection)
        vault = (
            _load_vault(database_path)
            if any(str(row[2]).startswith("secretref:") for row in rows)
            else _load_or_create_vault(database_path)
        )
    except RuntimeError as exc:
        raise RuntimeError("运行时配置 Vault 不可用") from exc
    for scope, key, value in rows:
        scope = str(scope)
        key = str(key)
        value = str(value)
        if value.startswith("secretref:"):
            _resolve_existing(
                connection,
                scope=scope,
                key=key,
                value=value,
                vault=vault,
            )
        else:
            _migrate_plaintext(
                connection,
                scope=scope,
                key=key,
                plaintext=value,
                vault=vault,
            )
    _validate_no_orphans(connection)


def downgrade() -> None:
    raise RuntimeError("Mangrove 迁移不支持把 SecretRef 还原为明文；请显式恢复备份")
