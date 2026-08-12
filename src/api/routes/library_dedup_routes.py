"""模板库/教训库定时巡检报告路由：只读展示最近若干轮巡检摘要。仅管理员可见（运维向内容）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import get_store, require_admin

router = APIRouter(prefix="/api/library-dedup-log", tags=["library-dedup-log"])


@router.get("")
def list_scan_log(admin=Depends(require_admin)):
    """返回最近 200 轮巡检记录（按时间倒序）。仅管理员可读。"""
    return {"log": get_store().library_dedup_scan_log_recent(limit=200)}
