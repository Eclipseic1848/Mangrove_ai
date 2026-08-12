# -*- coding: utf-8 -*-
"""把认证用户身份收敛为能力目录 Actor。"""
from __future__ import annotations

from typing import Any

from src.capability_catalog import CatalogActor


def catalog_actor_from_user(user: dict[str, Any]) -> CatalogActor:
    role = str(user.get("role") or "user")
    catalog_role = "superadmin" if role == "super_admin" else role
    if catalog_role not in {"user", "admin", "superadmin"}:
        # 未知角色失败关闭为普通用户，避免异常或新增角色意外获得跨 Owner 治理权限。
        catalog_role = "user"
    return CatalogActor(owner_id=str(user["user_id"]), role=catalog_role)
