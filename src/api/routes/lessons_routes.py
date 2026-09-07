"""经验库：本人原件与经 Owner 确认的通用副本。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from src.memory import delete_lesson, load_lessons

from ..auth import get_current_user, is_admin_role
from ..library_access import LibraryShareIn, library_entry, share_entry

router = APIRouter(prefix="/api/lessons", tags=["lessons"])


@router.get("")
def list_lessons(response: Response, user=Depends(get_current_user)):
    """本人可读自己的经验；平台副本须已获确切内容授权。"""
    response.headers["Cache-Control"] = "no-store"
    return {"lessons": [library_entry(entry, user) for entry in load_lessons(owner_id=user["user_id"])]}


@router.post("/{slug}/share")
def share_lesson(slug: str, body: LibraryShareIn, response: Response, user=Depends(get_current_user)):
    from src.memory.lessons import share_lesson as share

    response.headers["Cache-Control"] = "no-store"
    return share_entry(share, slug, body, user)


@router.delete("/{slug}")
def remove_lesson(slug: str, user=Depends(get_current_user)):
    """删除本人经验或撤回自己的共享副本，不能借管理员身份读写他人原件。"""
    try:
        deleted = delete_lesson(slug, owner_id=user["user_id"], is_admin=is_admin_role(user.get("role")))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="教训不存在") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="教训不存在")
    return {"ok": True}
