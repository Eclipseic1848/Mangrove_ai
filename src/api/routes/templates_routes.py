"""模板库：本人原件与经 Owner 确认的通用副本。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from src.memory import delete_template, load_templates

from ..auth import get_current_user, is_admin_role
from ..library_access import LibraryShareIn, library_entry, share_entry

router = APIRouter(prefix="/api/templates", tags=["templates"])


@router.get("")
def list_templates(response: Response, user=Depends(get_current_user)):
    """管理员也不能因角色越过个人原件的 Owner 边界。"""
    response.headers["Cache-Control"] = "no-store"
    return {"templates": [library_entry(entry, user) for entry in load_templates(owner_id=user["user_id"])]}


@router.post("/{slug}/share")
def share_template(slug: str, body: LibraryShareIn, response: Response, user=Depends(get_current_user)):
    from src.memory.templates import share_template as share

    response.headers["Cache-Control"] = "no-store"
    return share_entry(share, slug, body, user)


@router.delete("/{slug}")
def remove_template(slug: str, user=Depends(get_current_user)):
    """本人管理原件和自己的共享副本；管理员只额外管理平台副本。"""
    try:
        deleted = delete_template(slug, owner_id=user["user_id"], is_admin=is_admin_role(user.get("role")))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="模板不存在") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="模板不存在")
    return {"ok": True}
