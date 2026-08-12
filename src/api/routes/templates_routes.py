"""模板库路由：查看 / 删除已学分析模板（全局共享知识库，多用户不隔离）。

模板的「沉淀」走对话工作区的 HITL 确认（confirm.py），此处只做浏览与管理。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from src.memory import delete_template, load_templates

from ..auth import get_current_user, require_admin

router = APIRouter(prefix="/api/templates", tags=["templates"])


@router.get("")
def list_templates(user=Depends(get_current_user)):
    """返回全部已学模板（含 status/uses/quality_avg 等自学习字段）。所有登录用户可读。"""
    return {"templates": load_templates()}


@router.delete("/{slug}")
def remove_template(slug: str, admin=Depends(require_admin)):
    """删除一个模板文件（清理低质/重复草稿）。全局共享知识库，仅管理员可写。"""
    if not delete_template(slug):
        raise HTTPException(status_code=404, detail="模板不存在")
    return {"ok": True}
