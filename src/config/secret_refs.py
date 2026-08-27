"""运行时配置 SecretRef 的稳定格式与安全边界。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from uuid import UUID

from src.model_connections.vault import (
    FernetCredentialVault,
    VaultDecryptionError,
)


SECRET_REF_PREFIX = "secretref:runtime-config:"

# 与 runtime_config.REGISTRY 中 secret=True 的键同步冻结。迁移脚本只依赖这个
# 无运行时副作用的集合，避免在 Alembic 中加载 settings 或 API 模块。
RUNTIME_CONFIG_SECRET_KEYS = frozenset(
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


class SecretRefResolutionError(RuntimeError):
    """SecretRef、Owner 或 Vault 无法形成唯一可信解析结果。"""


@dataclass(frozen=True)
class SecretArtifactScan:
    """单个受控制品的脱敏扫描结论，不包含路径或 Secret。"""

    artifact_name: str
    exposed_secret_count: int

    @property
    def passed(self) -> bool:
        return self.exposed_secret_count == 0


def secret_ref(secret_id: str) -> str:
    """把 UUID 转为不透明的运行时配置 SecretRef。"""

    normalized = str(UUID(secret_id))
    return f"{SECRET_REF_PREFIX}{normalized}"


def parse_secret_ref(value: str) -> str:
    """验证 SecretRef 格式；错误信息刻意不回显输入。"""

    if not isinstance(value, str) or not value.startswith(SECRET_REF_PREFIX):
        raise SecretRefResolutionError("SecretRef 无法解析")
    try:
        return str(UUID(value.removeprefix(SECRET_REF_PREFIX)))
    except (ValueError, AttributeError) as exc:
        raise SecretRefResolutionError("SecretRef 无法解析") from exc


def vault_key_path(database: str | Path) -> Path:
    """配置 Secret 与模型连接共享同一主密钥文件，不共享业务密文表。"""

    path = Path(database)
    return path.with_name(f"{path.name}.model-connections.key")


def load_vault(database: str | Path) -> FernetCredentialVault:
    """只加载既有 Vault；缺失时不得静默生成替代 key。"""

    key_path = vault_key_path(database)
    if not key_path.is_file():
        raise SecretRefResolutionError("运行时配置 Vault 不可用")
    try:
        return FernetCredentialVault.from_key_file(key_path)
    except Exception as exc:
        raise SecretRefResolutionError("运行时配置 Vault 不可用") from exc


def load_or_create_vault(database: str | Path) -> FernetCredentialVault:
    """仅在数据库还没有任何 Vault 密文时创建首个共享 key。"""

    database_path = Path(database)
    key_path = vault_key_path(database_path)
    if key_path.is_file():
        return load_vault(database_path)
    try:
        uri = f"{database_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            encrypted_rows = 0
            for table in ("model_connection_secrets", "runtime_config_secrets"):
                if table in tables:
                    encrypted_rows += int(
                        connection.execute(
                            f'SELECT COUNT(*) FROM "{table}"'
                        ).fetchone()[0]
                    )
    except (OSError, sqlite3.DatabaseError) as exc:
        raise SecretRefResolutionError("运行时配置 Vault 不可用") from exc
    if encrypted_rows:
        raise SecretRefResolutionError("运行时配置 Vault 不可用")
    try:
        return FernetCredentialVault.from_key_file(key_path)
    except Exception as exc:
        raise SecretRefResolutionError("运行时配置 Vault 不可用") from exc


def scan_artifacts_for_plaintext_secrets(
    database: str | Path,
    artifacts: list[str | Path] | tuple[str | Path, ...],
) -> tuple[SecretArtifactScan, ...]:
    """用当前 Vault 中的 Secret 值扫描显式指定的数据库/备份制品。"""

    database_path = Path(database)
    try:
        uri = f"{database_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            rows = connection.execute(
                "SELECT ciphertext FROM runtime_config_secrets ORDER BY secret_id"
            ).fetchall()
    except (OSError, sqlite3.DatabaseError) as exc:
        raise SecretRefResolutionError("运行时配置 Secret 扫描源不可用") from exc
    if not rows:
        plaintexts: set[bytes] = set()
    else:
        vault = load_vault(database_path)
        try:
            plaintexts = set()
            for row in rows:
                plaintext = vault.decrypt(str(row[0]))
                if plaintext:
                    plaintexts.add(plaintext.encode("utf-8"))
        except VaultDecryptionError as exc:
            raise SecretRefResolutionError(
                "运行时配置 Secret 扫描无法解密"
            ) from exc
    results: list[SecretArtifactScan] = []
    for artifact in artifacts:
        path = Path(artifact)
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise SecretRefResolutionError("受控 Secret 扫描制品不可读") from exc
        results.append(
            SecretArtifactScan(
                artifact_name=path.name,
                exposed_secret_count=sum(value in content for value in plaintexts),
            )
        )
    return tuple(results)


__all__ = [
    "RUNTIME_CONFIG_SECRET_KEYS",
    "SECRET_REF_PREFIX",
    "SecretArtifactScan",
    "SecretRefResolutionError",
    "load_vault",
    "load_or_create_vault",
    "parse_secret_ref",
    "secret_ref",
    "scan_artifacts_for_plaintext_secrets",
    "vault_key_path",
]
