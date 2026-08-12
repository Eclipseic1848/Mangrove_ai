# -*- coding: utf-8 -*-
"""数据库连接服务：凭证加解密（Fernet）、命名连接解析与公开脱敏。

Phase 3 核心安全组件（Task 2）。credential_ref 是客户端唯一合法的连接引用；
resolve_credential() 是服务端唯一明文出口。密码永不进入 API 响应、日志、Manifest
与 LLM 上下文。
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from src.config.settings import settings

_CREDENTIAL_REF_RE = re.compile(r"^dbconn:[a-fA-F0-9-]+$")


# ---------------- Fernet key ----------------


def _derive_key(secret: str) -> bytes:
    """从 secret 派生 32 字节 Fernet 密钥。兼容 settings.data_prep_db_secret_key 为空场景。"""
    if isinstance(secret, str) and not isinstance(secret, bytes):
        secret = secret.encode("utf-8")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"mangrove.db.v2",
        iterations=480_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(secret))


def _get_fernet() -> Fernet:
    secret = settings.data_prep_db_secret_key or settings.jwt_secret
    if not settings.data_prep_db_secret_key:
        import logging
        logging.getLogger("db_connections").warning(
            "DATA_PREP_DB_SECRET_KEY 未设置，已从 jwt_secret 派生 Fernet 密钥；"
            "生产环境请设置独立密钥以保证轮换安全。"
        )
    return Fernet(_derive_key(secret))


def encrypt_password(password: str) -> str:
    """加密明文密码，返回 Fernet token 字符串（可直接存 TEXT 列）。"""
    return _get_fernet().encrypt(password.encode("utf-8")).decode("ascii")


def decrypt_password(token: str) -> str:
    """解密 Fernet token 字符串为明文密码。token 损坏或密钥不匹配时抛异常。"""
    return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")


# ---------------- dataclass ----------------


@dataclass
class DbConnectionIn:
    """创建/更新连接的输入模型。"""
    name: str
    dialect: str  # sqlite | mysql | postgresql
    host: str = ""
    port: int = 0
    database_name: str = ""
    username: str = ""
    password: str = ""
    sqlite_relpath: str = ""


@dataclass
class DbConnectionPublic:
    """对外展示的连接摘要——绝不包含密码或加密 token。"""
    connection_id: str
    user_id: str
    name: str
    dialect: str
    host: str
    port: int
    database_name: str
    username: str
    sqlite_relpath: str
    created_at: str
    updated_at: str


@dataclass
class DbCredentials:
    """内部凭证——只存在于 resolve_credential 返回值，不落盘、不进 API 响应。"""
    dialect: str
    host: str
    port: int
    database: str
    username: str
    password: str
    sqlite_relpath: str


# ---------------- 公开脱敏 ----------------


def to_public_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    """从 db_connections 行转换为对外安全的 dict（不含 password / password_enc）。"""
    return {
        "connection_id": row["connection_id"],
        "user_id": row["user_id"],
        "name": row["name"],
        "dialect": row["dialect"],
        "host": row.get("host") or "",
        "port": int(row.get("port") or 0),
        "database_name": row.get("database_name") or "",
        "username": row.get("username") or "",
        "sqlite_relpath": row.get("sqlite_relpath") or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# ---------------- credential 解析 ----------------


def resolve_credential(
    credential_ref: str,
    user_id: str,
    *,
    _store=None,
) -> DbCredentials:
    """把 credential_ref 解析为 DbCredentials。跨用户返回 ValueError。

    credential_ref 格式仅接受 "dbconn:<uuid>"；其他格式与不存在的连接均抛 ValueError。
    """
    if not _CREDENTIAL_REF_RE.match(credential_ref):
        raise ValueError(f"无效的 credential_ref 格式: {credential_ref}")

    connection_id = credential_ref[len("dbconn:"):]

    # 延迟导入避免循环依赖（WebUIStore）
    from src.api.auth import get_store as _get_store

    store = _store if _store is not None else _get_store()
    row = store.get_db_connection(connection_id)

    if not row:
        raise ValueError(f"数据库连接不存在: {connection_id}")

    if row["user_id"] != user_id:
        raise ValueError(f"数据库连接找不到或不属于当前用户: {connection_id}")

    password_enc = row.get("password_enc")
    password = ""
    if password_enc:
        try:
            password = decrypt_password(password_enc)
        except Exception as exc:
            raise ValueError(f"无法解密数据库连接密码: {connection_id}") from exc

    return DbCredentials(
        dialect=row["dialect"],
        host=row.get("host") or "",
        port=int(row.get("port") or 0),
        database=row.get("database_name") or "",
        username=row.get("username") or "",
        password=password,
        sqlite_relpath=row.get("sqlite_relpath") or "",
    )
