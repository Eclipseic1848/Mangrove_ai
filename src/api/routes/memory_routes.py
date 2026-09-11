"""记忆路由：全局偏好（管理员维护，对所有人生效）+ 个人记忆（每用户自己写，只对自己的任务生效）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.memory import add_preference, load_preferences

from ..auth import get_current_user, get_store, require_admin

router = APIRouter(prefix="/api/memory", tags=["memory"])


class PreferenceIn(BaseModel):
    text: str


class MemoryCorrectionIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    expected_text: str = Field(min_length=1, max_length=4000)


@router.get("")
def get_memory(user=Depends(get_current_user)):
    """读取全局共享偏好 + 当前用户自己的个人记忆。"""
    personal = get_store().memory_list(user["user_id"])
    return {"preferences": load_preferences() or "", "personal": personal}


@router.post("")
def add_memory(body: PreferenceIn, admin=Depends(require_admin)):
    """追加一条偏好到全局共享记忆。仅管理员可写。"""
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="偏好内容为空")
    ok = add_preference(text)
    if not ok:
        raise HTTPException(status_code=500, detail="保存失败")
    return {"ok": True}


@router.post("/self")
def add_my_memory(body: PreferenceIn, user=Depends(get_current_user)):
    """追加一条个人记忆，只对自己发起的任务生效。任意登录用户可写。"""
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="记忆内容为空")
    item = get_store().memory_add(user["user_id"], text)
    return {"ok": True, "item": item}


@router.delete("/self/{memory_id}")
def delete_my_memory(memory_id: int, user=Depends(get_current_user)):
    """删除自己的一条个人记忆；按 user_id 校验归属，防止越权删除他人的。"""
    ok = get_store().memory_delete(user["user_id"], memory_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"ok": True}


@router.patch("/self/{memory_id}")
def correct_my_memory(
    memory_id: int,
    body: MemoryCorrectionIn,
    user=Depends(get_current_user),
):
    """只纠正本人当前版本；并发变化时失败，避免覆盖另一页面的新内容。"""
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="记忆内容为空")
    result, item = get_store().memory_update(
        user["user_id"],
        memory_id,
        text,
        body.expected_text,
    )
    if result == "missing":
        raise HTTPException(status_code=404, detail="记忆不存在")
    if result == "conflict":
        raise HTTPException(status_code=409, detail="记忆已变化，请刷新后再纠正")
    return {"ok": True, "item": item}
