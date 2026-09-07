"""平台设备登录、刷新与撤销；浏览器凭证只经 HttpOnly Cookie。"""
from __future__ import annotations

import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..auth import (
    ACCESS_COOKIE, REFRESH_COOKIE, ACCESS_SECONDS, SESSION_SECONDS,
    _invalid_session, access_identity, clear_session_cookies, get_current_user,
    get_store, hash_password, login_keys, public_user, rate_limited,
    refresh_identity, registration_allowed, require_csrf, set_session_cookies,
    verify_password,
)
from ..schemas import LoginIn, PasswordChangeIn, RegisterIn, TokenOut

router = APIRouter(prefix="/api/auth", tags=["auth"])
_DUMMY_PASSWORD_HASH = "00" * 16 + "$" + "00" * 32


@router.post("/register")
def register(body: RegisterIn, request: Request):
    require_csrf(request)
    store = get_store()
    # 公开注册不能承担首管初始化，也不能因空库绕过注册开关。
    if not registration_allowed():
        raise HTTPException(status_code=403, detail="已关闭自助注册，请联系管理员开通账号")
    username = body.username.strip()
    if len(username) < 2 or len(body.password) < 6:
        raise HTTPException(status_code=400, detail="用户名至少2位、密码至少6位")
    if store.get_user_by_name(username):
        raise HTTPException(status_code=409, detail="用户名已存在")
    store.create_user(
        username, hash_password(body.password), body.display_name or "", role="user", pending=True,
    )
    return {"pending": True, "message": "注册成功，待管理员审批通过后即可登录"}


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, response: Response):
    require_csrf(request)
    username = body.username.strip()
    account_key, source_key = login_keys(request, username)
    store = get_store()
    retry = store.platform_login_retry(account_key=account_key, source_key=source_key, now=time.time())
    if retry:
        raise rate_limited(retry)
    user = store.get_user_by_name(username)
    # 不存在的账号也做同成本校验；不通过响应暴露账号存在性。
    verified = verify_password(body.password, user["password_hash"] if user else _DUMMY_PASSWORD_HASH)
    now = time.time()
    session_id = secrets.token_hex(24)
    refresh = session_id + "." + secrets.token_urlsafe(48)
    _, digest = refresh_identity(refresh)
    access_expires_at = now + ACCESS_SECONDS
    session_expires_at = now + SESSION_SECONDS
    retry, user = store.platform_login_commit(
        username=username, expected_password_hash=user["password_hash"] if user and verified else None,
        session_id=session_id, refresh_digest=digest, now=now,
        access_expires_at=access_expires_at, absolute_expires_at=session_expires_at,
        account_key=account_key, source_key=source_key,
    )
    if retry:
        raise rate_limited(retry)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    set_session_cookies(response, request, user_id=user["user_id"], session_id=session_id, refresh=refresh, now=now, access_expires_at=access_expires_at, session_expires_at=session_expires_at)
    return public_user(user, access_expires_at=access_expires_at, session_expires_at=session_expires_at)


@router.get("/me", response_model=TokenOut)
def me(request: Request, response: Response, user=Depends(get_current_user)):
    response.headers["Cache-Control"] = "no-store"
    return public_user(user, access_expires_at=request.state.platform_claims["exp"], session_expires_at=request.state.platform_session["absolute_expires_at"])


@router.post("/refresh", response_model=TokenOut)
def refresh_session(request: Request, response: Response):
    require_csrf(request)
    # 先校验配置，避免提交轮换后才发现无法签发新 access。
    login_keys(request, "")
    session_id, digest = refresh_identity(request.cookies.get(REFRESH_COOKIE, ""))
    if not session_id:
        raise _invalid_session()
    refresh = session_id + "." + secrets.token_urlsafe(48)
    _, next_digest = refresh_identity(refresh)
    now = time.time()
    retry, rotated = get_store().platform_rotate_refresh(session_id=session_id, digest=digest, next_digest=next_digest, now=now, access_expires_at=now + ACCESS_SECONDS, expected_owner=request.headers.get("x-mangrove-owner"))
    # 返回后事务已提交，重放撤销事实不能被 HTTPException 回滚。
    if retry == -1:
        raise _invalid_session()
    if retry:
        raise rate_limited(retry)
    if rotated is None:
        raise _invalid_session()
    user, session = rotated
    set_session_cookies(response, request, user_id=user["user_id"], session_id=session_id, refresh=refresh, now=now, access_expires_at=session["access_expires_at"], session_expires_at=session["absolute_expires_at"])
    return public_user(user, access_expires_at=session["access_expires_at"], session_expires_at=session["absolute_expires_at"])


@router.post("/logout")
def logout(request: Request, response: Response):
    require_csrf(request)
    now = time.time()
    retry = 0
    try:
        user, payload, _ = access_identity(request)
    except HTTPException:
        session_id, digest = refresh_identity(request.cookies.get(REFRESH_COOKIE, ""))
        if session_id:
            retry = get_store().platform_logout(session_id=session_id, refresh_digest=digest, now=now, expected_owner=request.headers.get("x-mangrove-owner"))
    else:
        retry = get_store().platform_logout(session_id=payload["sid"], owner_user_id=user["user_id"], now=now, expected_owner=request.headers.get("x-mangrove-owner"))
    if retry == -1:
        raise _invalid_session()
    if retry:
        raise rate_limited(retry)
    clear_session_cookies(response, request)
    return {"ok": True}


@router.post("/logout-all")
def logout_all(request: Request, response: Response, user=Depends(get_current_user)):
    get_store().platform_logout_all(user["user_id"], now=time.time())
    clear_session_cookies(response, request)
    return {"ok": True}


@router.post("/password")
def change_password(body: PasswordChangeIn, request: Request, response: Response, user=Depends(get_current_user)):
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少6位")
    if not verify_password(body.current_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="当前密码错误")
    changed = get_store().platform_change_password(owner_user_id=user["user_id"], session_id=request.state.platform_claims["sid"], expected_password_hash=user["password_hash"], password_hash=hash_password(body.new_password), now=time.time())
    if not changed:
        raise _invalid_session()
    clear_session_cookies(response, request)
    return {"ok": True}
