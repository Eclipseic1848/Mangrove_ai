"""鉴权路由：注册 / 登录 / 当前用户。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import (
    create_token, get_current_user, get_store, hash_password,
    registration_allowed, verify_password,
)
from ..schemas import LoginIn, RegisterIn, TokenOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register")
def register(body: RegisterIn):
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
def login(body: LoginIn):
    store = get_store()
    user = store.get_user_by_name(body.username.strip())
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if user.get("pending"):
        raise HTTPException(status_code=403, detail="账号待管理员审批，通过后方可登录")
    if user.get("disabled"):
        raise HTTPException(status_code=403, detail="账号已被禁用，请联系管理员")
    token = create_token(user["user_id"])
    return TokenOut(
        access_token=token, user_id=user["user_id"],
        username=user["username"], display_name=user["display_name"] or user["username"],
        role=user.get("role") or "user",
    )


@router.get("/me", response_model=TokenOut)
def me(user=Depends(get_current_user)):
    # 复用 TokenOut 结构，重新签发令牌（顺带续期）
    token = create_token(user["user_id"])
    return TokenOut(
        access_token=token, user_id=user["user_id"],
        username=user["username"], display_name=user["display_name"] or user["username"],
        role=user.get("role") or "user",
    )
