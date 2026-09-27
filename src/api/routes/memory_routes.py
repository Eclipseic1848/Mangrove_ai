"""记忆路由：全局偏好（管理员维护，对所有人生效）+ 个人记忆（每用户自己写，只对自己的任务生效）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.memory import add_preference, load_preferences
from src.memory.loader import preferences_digest, replace_preferences

from ..auth import get_current_user, get_store, require_admin

router = APIRouter(prefix="/api/memory", tags=["memory"])


class PreferenceIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class MemoryCorrectionIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    # 旧版本允许超长记忆，纠正时必须接纳完整原文以保留并发保护。
    expected_text: str = Field(min_length=1)


class GlobalCorrectionIn(BaseModel):
    text: str = Field(max_length=40000)
    expected_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.get("")
def get_memory(page: int | None = Query(default=None, ge=1),
               page_size: int = Query(default=10, ge=10, le=100),
               q: str = Query(default="", max_length=200), user=Depends(get_current_user)):
    """读取全局共享偏好 + 当前用户自己的个人记忆。"""
    if page_size not in (10, 20, 50, 100):
        raise HTTPException(status_code=422, detail="每页条数仅支持10、20、50、100")
    result = get_store().memory_page(user["user_id"], page=page, page_size=page_size, query=q) if page is not None else {"personal": get_store().memory_list(user["user_id"])}
    try:
        preferences = load_preferences(strict=True)
    except (OSError, UnicodeError) as exc:
        raise HTTPException(status_code=503, detail="平台规范暂时无法读取，请稍后重试") from exc
    return {"preferences": preferences, "preferences_digest": preferences_digest(preferences), **result}


@router.patch("")
def correct_global_memory(body: GlobalCorrectionIn, request: Request, admin=Depends(require_admin)):
    """只有管理员可修改共享规范，操作审计只记录摘要，不记录私人正文。"""
    try:
        updated = replace_preferences(body.text, body.expected_digest)
    except (OSError, UnicodeError) as exc:
        raise HTTPException(status_code=503, detail="平台规范保存失败，请重新读取后核对") from exc
    if not updated:
        raise HTTPException(status_code=409, detail="平台规范已变化，请刷新后重新编辑")
    if hasattr(request.state, "operations_changes"):
        request.state.operations_changes.append({"field": "preferences_digest", "before": body.expected_digest, "after": preferences_digest(body.text.strip())})
    return {"ok": True}


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
