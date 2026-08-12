"""管理员路由：用户管理 + 自助注册开关（RBAC 分级）。

权限分级（super_admin > admin > user）核心规则：**只能操作严格低于自己级别的账号**。
- 管理员之间平级，互相不可改/删（杜绝互删互改密码）；普通用户无管理权。
- 赋予的新角色也必须严格低于操作者级别 → 因此 super_admin 角色不可经 UI 授予（防提权），
  仅由系统引导（首个用户/旧库迁移最早用户）设定。
- 对自己、同级、更高级账号的操作一律 403（自删/自降级因此自然被禁止）。
- 顶层 super_admin 无人能操作 → 系统始终至少保留一名超级管理员。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_store, hash_password, registration_allowed, require_admin, role_level
from ..schemas import AdminUserCreateIn, AdminUserUpdateIn, RegistrationIn

router = APIRouter(prefix="/api/admin", tags=["admin"])

_KNOWN_ROLES = {"super_admin", "admin", "user"}


def _assert_outranks(actor: dict, target: dict) -> None:
    """操作者必须严格高于目标账号级别，否则 403。"""
    if role_level(actor.get("role")) <= role_level(target.get("role")):
        raise HTTPException(status_code=403, detail="只能管理权限低于你的账号")


def _assert_can_assign(actor: dict, role: str) -> None:
    """所赋角色必须严格低于操作者级别（防止平级互授与提权）。"""
    if role not in _KNOWN_ROLES:
        raise HTTPException(status_code=400, detail="角色非法")
    if role_level(role) >= role_level(actor.get("role")):
        raise HTTPException(status_code=403, detail="无权赋予该角色（不能高于或等于你自己）")


@router.get("/users")
def list_users(
    q: str = "", role: str = "", status: str = "",
    page: int = 1, page_size: int = 20,
    admin=Depends(require_admin),
):
    store = get_store()
    users, total = store.list_users(q=q, role=role, status=status, page=page, page_size=page_size)
    return {
        "users": users, "total": total,
        "pending_total": store.count_pending(),
        "page": page, "page_size": page_size,
    }


@router.post("/users")
def create_user(body: AdminUserCreateIn, admin=Depends(require_admin)):
    store = get_store()
    username = body.username.strip()
    if len(username) < 2 or len(body.password) < 6:
        raise HTTPException(status_code=400, detail="用户名至少2位、密码至少6位")
    _assert_can_assign(admin, body.role)
    if store.get_user_by_name(username):
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = store.create_user(username, hash_password(body.password), body.display_name or "", role=body.role)
    return {"ok": True, "user_id": user["user_id"]}


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: AdminUserUpdateIn, admin=Depends(require_admin)):
    store = get_store()
    target = store.get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    _assert_outranks(admin, target)  # 仅能操作低于自己的账号（含禁止操作自己/同级/更高）

    if body.role is not None:
        _assert_can_assign(admin, body.role)
    if body.password is not None and len(body.password) < 6:
        raise HTTPException(status_code=400, detail="密码至少6位")
    display_name = body.display_name.strip() if body.display_name is not None else None
    if display_name is not None and not (1 <= len(display_name) <= 32):
        raise HTTPException(status_code=400, detail="昵称须为 1~32 个字符")

    pwd_hash = hash_password(body.password) if body.password else None
    store.update_user(
        user_id, role=body.role, disabled=body.disabled,
        pending=body.pending, password_hash=pwd_hash, display_name=display_name,
    )
    return {"ok": True}


@router.delete("/users/{user_id}")
def delete_user(user_id: str, admin=Depends(require_admin)):
    store = get_store()
    target = store.get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    _assert_outranks(admin, target)  # 不能删自己/同级/更高（自删因此被禁）
    store.delete_user(user_id)
    return {"ok": True}


@router.get("/registration")
def get_registration(admin=Depends(require_admin)):
    return {"enabled": registration_allowed()}


@router.patch("/registration")
def set_registration(body: RegistrationIn, admin=Depends(require_admin)):
    get_store().set_setting("allow_register", "1" if body.enabled else "0")
    return {"ok": True, "enabled": body.enabled}
