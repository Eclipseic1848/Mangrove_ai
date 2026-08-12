"""教训库路由：查看 / 删除已学失败教训（全局共享知识库，多用户不隔离）。

教训的产出走 checker.py 判定失败后自动蒸馏（record_failure），此处只做浏览与管理。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from src.memory import delete_lesson, load_lessons

from ..auth import require_admin

router = APIRouter(prefix="/api/lessons", tags=["lessons"])


@router.get("")
def list_lessons(admin=Depends(require_admin)):
    """返回全部已学教训（含 status/occurrences 等自学习字段）。仅管理员可读（内容偏内部/运维向）。"""
    return {"lessons": load_lessons()}


@router.delete("/{slug}")
def remove_lesson(slug: str, admin=Depends(require_admin)):
    """删除一条教训文件。全局共享知识库，仅管理员可写。"""
    if not delete_lesson(slug):
        raise HTTPException(status_code=404, detail="教训不存在")
    return {"ok": True}
