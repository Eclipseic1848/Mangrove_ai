"""
鉴权：密码哈希（标准库 pbkdf2，无 passlib 依赖）+ JWT（PyJWT）+ FastAPI 当前用户依赖。

密码以 pbkdf2_hmac(sha256, 200k 轮) 存储为 "salt$hash"（均 hex）。
JWT 用 settings.jwt_secret 签名，sub=user_id，exp 按 settings.jwt_expire_hours。
"""
from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.config.settings import settings

from .store import WebUIStore

_PBKDF2_ROUNDS = 200_000
_ALGO = "HS256"

# RBAC 角色分级：数值越大权限越高。super_admin（超级管理员）为顶层，仅由系统引导设定、不可经 UI 授予。
ROLE_LEVEL = {"super_admin": 3, "admin": 2, "user": 1}


def role_level(role: str | None) -> int:
    return ROLE_LEVEL.get(role or "user", 1)


def is_admin_role(role: str | None) -> bool:
    """是否具备管理权限（管理员或超级管理员）。"""
    return role_level(role) >= ROLE_LEVEL["admin"]

_bearer = HTTPBearer(auto_error=False)

# 进程内单例 store（与路由共享）
_store: WebUIStore | None = None


def get_store() -> WebUIStore:
    global _store
    if _store is None:
        _store = WebUIStore(settings.webui_db_path)
    return _store


# ---------- 密码 ----------
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, hash_hex = stored.split("$", 1)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), _PBKDF2_ROUNDS)
    return hmac.compare_digest(dk.hex(), hash_hex)


# ---------- JWT ----------
def create_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(hours=settings.jwt_expire_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGO)


def _decode_token(token: str) -> str:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[_ALGO])
        return payload["sub"]
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录")


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Dict[str, Any]:
    """FastAPI 依赖：从 Authorization: Bearer <token> 解析并返回当前用户字典。"""
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    user_id = _decode_token(creds.credentials)
    user = get_store().get_user(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    if user.get("pending"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号待管理员审批，通过后方可使用")
    if user.get("disabled"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已被禁用，请联系管理员")
    return user


def require_admin(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """FastAPI 依赖：要求当前用户具备管理权限（管理员或超级管理员），否则 403。"""
    if not is_admin_role(user.get("role")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


def registration_allowed() -> bool:
    """是否允许自助注册：DB 运行时设置优先，未设置则回落 .env 的默认值。"""
    val = get_store().get_setting("allow_register")
    if val is None:
        return bool(settings.webui_allow_register)
    return val == "1"
