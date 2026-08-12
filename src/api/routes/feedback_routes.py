"""反馈管理路由：开发者视角查看全局点赞/点踩统计与明细，仅管理员可读。

与 /api/chat/feedback（用户提交/取消自己的反馈）区分：本路由是管理员全局只读视图，
用于基于反馈数据驱动优化与评估。
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..auth import get_store, require_admin

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


class FeedbackStatusIn(BaseModel):
    status: str
    admin_note: str | None = None


@router.get("/overview")
def overview(admin=Depends(require_admin)):
    """全局反馈统计：赞/踩总数、点踩率、原因分布、按天趋势。"""
    return get_store().feedback_overview()


@router.get("/list")
def list_feedback(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    rating: str | None = Query(None),
    reason: str | None = Query(None),
    user_id: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    status: str | None = Query(None),
    admin=Depends(require_admin),
):
    """反馈明细分页列表（带原始问答），支持筛选。"""
    return get_store().feedback_list(
        limit=limit, offset=offset, rating=rating, reason=reason,
        user_id=user_id, date_from=date_from, date_to=date_to, status=status,
    )


@router.get("/export")
def export_feedback(admin=Depends(require_admin)):
    """导出全部点踩明细为 CSV（问题+回复+原因+描述），供构造 bad case 评测集/离线分析。"""
    data = get_store().feedback_list(limit=10000, offset=0, rating="down")
    buf = io.StringIO()
    buf.write("﻿")  # BOM，Excel 正确识别 UTF-8
    writer = csv.writer(buf)
    writer.writerow(["时间", "用户", "模型", "原因", "描述", "问题", "AI回复"])
    for it in data["items"]:
        writer.writerow([
            it.get("created_at", ""),
            it.get("user_id", ""),
            it.get("model") or "",
            "、".join(it.get("reasons") or []),
            it.get("comment") or "",
            (it.get("question") or "")[:500],
            (it.get("answer") or "")[:1000],
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=feedback.csv"},
    )


@router.patch("/{fb_id}")
def update_feedback(fb_id: int, body: FeedbackStatusIn, admin=Depends(require_admin)):
    """管理员更新反馈处理状态（pending/resolved/ignored）与备注。"""
    if body.status not in ("pending", "resolved", "ignored"):
        raise HTTPException(status_code=400, detail="status 必须为 pending/resolved/ignored")
    get_store().update_feedback_status(fb_id, body.status, body.admin_note)
    return {"ok": True}


@router.delete("/{fb_id}")
def delete_feedback(fb_id: int, admin=Depends(require_admin)):
    """管理员删除一条反馈（按 feedback id，区别于用户取消自己的反馈）。"""
    get_store().delete_feedback_admin(fb_id)
    return {"ok": True}
