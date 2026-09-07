"""
鉴权：密码哈希（标准库 pbkdf2，无 passlib 依赖）+ JWT（PyJWT）+ FastAPI 当前用户依赖。

密码以 pbkdf2_hmac(sha256, 200k 轮) 存储为 "salt$hash"（均 hex）。
访问 JWT 绑定可撤销设备会话；只经 HttpOnly Cookie 使用，最长 30 分钟。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import ipaddress
import math
import time
from urllib.parse import urlsplit
from typing import Any, Dict

import jwt
from fastapi import Depends, HTTPException, Request, Response, status

from src.config.settings import settings
from src.services.managed_paths import ManagedPathCodec

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

ACCESS_COOKIE = "mangrove_access"
REFRESH_COOKIE = "mangrove_refresh"
ACCESS_SECONDS = 30 * 60
SESSION_SECONDS = 7 * 24 * 60 * 60

# 进程内单例 store（与路由共享）
_store: WebUIStore | None = None


def get_store() -> WebUIStore:
    global _store
    if _store is None:
        _store = WebUIStore(
            settings.webui_db_path,
            semantic_paths=ManagedPathCodec(
                settings.semantic_execution_root,
                legacy_anchor=("data", "semantic-executions"),
            ),
        )
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


# ---------- 平台凭证与浏览器边界 ----------
def _invalid_session(kind: str = "session-invalid") -> HTTPException:
    return HTTPException(status_code=401, detail="登录已失效，请重新登录", headers={"X-Mangrove-Auth": kind})


def rate_limited(retry: int) -> HTTPException:
    return HTTPException(status_code=429, detail="请求过于频繁，请稍后重试", headers={"Retry-After": str(retry)})


def _loopback(value: str | None) -> bool:
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value or "").is_loopback
    except ValueError:
        return False


def secure_cookie(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    # 仅真实连接来源与 Host 都是环回的 HTTP 开发环境可不使用 Secure。
    if request.url.scheme == "http" and _loopback(request.url.hostname) and request.client and _loopback(request.client.host):
        return False
    raise HTTPException(status_code=403, detail="登录需要安全连接")


def _origin(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return None
        return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


def require_csrf(request: Request) -> None:
    secure_cookie(request)
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = _origin(request.headers.get("origin", ""))
    allowed = {_origin(str(request.base_url))}
    allowed.update(_origin(value.strip()) for value in (settings.webui_cors_origins or "").split(",") if value.strip() != "*")
    if request.headers.get("x-mangrove-csrf") != "1" or origin is None or origin not in allowed:
        raise HTTPException(status_code=403, detail="请求来源校验失败")


def login_keys(request: Request, username: str) -> tuple[str, str]:
    secret = settings.require_jwt_secret().encode("utf-8")
    def key(kind: str, value: str) -> str:
        return hmac.new(secret, f"platform-login:{kind}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()
    # 不采信任意转发头；地址来自受控 ASGI 连接信息。
    return key("account", username), key("source", request.client.host if request.client else "unknown")


def refresh_identity(value: str) -> tuple[str, str]:
    sid, separator, secret = value.partition(".")
    if not separator or not sid or not secret or len(value) > 1024:
        return "", ""
    return sid, hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_token(user_id: str, *, session_id: str, issued_at: float, expires_at: float) -> str:
    return jwt.encode({"typ": "access", "sub": user_id, "sid": session_id, "iat": issued_at, "exp": expires_at}, settings.require_jwt_secret(), algorithm=_ALGO)


def _decode_token(token: str) -> dict:
    secret = settings.require_jwt_secret()
    try:
        payload = jwt.decode(token, secret, algorithms=[_ALGO], options={"require": ["typ", "sub", "sid", "iat", "exp"], "verify_exp": False, "verify_iat": False})
        if payload["typ"] != "access" or not isinstance(payload["sub"], str) or not payload["sub"] or not isinstance(payload["sid"], str) or not payload["sid"]:
            raise ValueError
        if any(isinstance(payload[key], bool) or not isinstance(payload[key], (int, float)) or not math.isfinite(payload[key]) for key in ("iat", "exp")):
            raise ValueError
        if payload["iat"] > time.time() or not 0 < payload["exp"] - payload["iat"] <= ACCESS_SECONDS:
            raise ValueError
        return payload
    except (jwt.PyJWTError, ValueError, TypeError):
        raise _invalid_session() from None


def access_identity(request: Request, *, allow_expired: bool = False) -> tuple[dict, dict, dict]:
    settings.require_jwt_secret()
    payload = _decode_token(request.cookies.get(ACCESS_COOKIE, ""))
    store = get_store()
    session = store.get_platform_session(payload["sid"], payload["sub"])
    user = store.get_user(payload["sub"])
    now = time.time()
    if session is None or session["revoked_at"] is not None or session["absolute_expires_at"] <= now or payload["exp"] > session["absolute_expires_at"] or user is None or user.get("disabled") or user.get("pending"):
        raise _invalid_session()
    if not allow_expired and payload["exp"] <= now:
        # 只有持久会话仍有效的已过期 access 才允许前端刷新并重发。
        raise _invalid_session("access-expired")
    return user, payload, session


def get_current_user(request: Request) -> Dict[str, Any]:
    require_csrf(request)
    cached = getattr(request.state, "platform_user", None)
    if cached is not None:
        return cached
    user, payload, session = access_identity(request)
    expected_owner = request.headers.get("x-mangrove-owner")
    if expected_owner is not None and expected_owner != user["user_id"]:
        # Cookie 跨标签共享；旧页面不能把自己的操作自动提交到新登录的 Owner。
        raise _invalid_session()
    route = request.scope.get("route")
    metadata = getattr(route, "openapi_extra", None) or {}
    control = metadata.get("x-mangrove-task-control") is True or getattr(request.state, "platform_task_control", False) is True
    retry = get_store().platform_request_limit(owner_user_id=user["user_id"], control=control, now=time.time())
    if retry:
        raise rate_limited(retry)
    request.state.platform_user = user
    request.state.platform_claims = payload
    request.state.platform_session = session
    return user


async def get_execution_user(user: Dict[str, Any] = Depends(get_current_user)):
    """在异步请求上下文冻结鉴权时的代数，后台 Task 与线程继承同一授权。"""
    from src.account_execution import ExecutionAuthorization, ExecutionDenied, execution_context

    try:
        if "execution_generation" not in user:
            raise ExecutionDenied("账号执行代数缺失")
        with execution_context(ExecutionAuthorization(user["user_id"], user["execution_generation"])):
            yield user
    except ExecutionDenied as exc:
        raise HTTPException(status_code=409, detail="账号执行授权已变化，请刷新后显式恢复任务") from exc


def public_user(user: dict, *, access_expires_at: float, session_expires_at: float) -> dict:
    return {"user_id": user["user_id"], "username": user["username"], "display_name": user["display_name"] or user["username"], "role": user.get("role") or "user", "access_expires_at": access_expires_at, "session_expires_at": session_expires_at}


def platform_session_valid(request: Request) -> bool:
    """流式读取只复核冻结会话，不重复消耗请求额度。"""
    claims = getattr(request.state, "platform_claims", None)
    owner = getattr(request.state, "platform_user", None)
    if not claims or not owner or owner["user_id"] != claims["sub"] or claims["exp"] <= time.time():
        return False
    try:
        session = get_store().get_platform_session(claims["sid"], claims["sub"])
        user = get_store().get_user(claims["sub"])
        return bool(session and session["revoked_at"] is None and session["absolute_expires_at"] > time.time() and user and not user.get("disabled") and not user.get("pending"))
    except Exception:  # noqa: BLE001
        # 无法核对身份时停止向浏览器披露，后台任务不随读取失效而取消。
        return False


def set_session_cookies(response: Response, request: Request, *, user_id: str, session_id: str, refresh: str, now: float, access_expires_at: float, session_expires_at: float) -> None:
    secure = secure_cookie(request)
    access = create_token(user_id, session_id=session_id, issued_at=now, expires_at=access_expires_at)
    # Cookie 保留到绝对截止，过期 JWT 只用于核验 sid 并返回 access-expired；权限仍最长30分钟。
    for name, value, path, expires in ((ACCESS_COOKIE, access, "/api", session_expires_at), (REFRESH_COOKIE, refresh, "/api/auth", session_expires_at)):
        response.set_cookie(name, value, max_age=max(0, int(expires-now)), path=path, secure=secure, httponly=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"


def clear_session_cookies(response: Response, request: Request) -> None:
    for name, path in ((ACCESS_COOKIE, "/api"), (REFRESH_COOKIE, "/api/auth")):
        response.delete_cookie(name, path=path, secure=secure_cookie(request), httponly=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"


def require_admin(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """FastAPI 依赖：要求当前用户具备管理权限（管理员或超级管理员），否则 403。"""
    if not is_admin_role(user.get("role")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


def require_execution_admin(user: Dict[str, Any] = Depends(get_execution_user)) -> Dict[str, Any]:
    return require_admin(user)


def registration_allowed() -> bool:
    """是否允许自助注册：DB 运行时设置优先，未设置则回落 .env 的默认值。"""
    val = get_store().get_setting("allow_register")
    if val is None:
        return bool(settings.webui_allow_register)
    return val == "1"
