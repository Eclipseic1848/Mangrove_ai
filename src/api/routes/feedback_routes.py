"""反馈管理路由：管理员默认仅查看统计和管理元数据。

与 /api/chat/feedback（用户提交/取消自己的反馈）区分：正文只能通过单条审计动作读取。
"""
from __future__ import annotations

import csv
import io
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..auth import get_store, require_admin
from ..feedback_audit import AuditedFeedbackUnavailable
from ..feedback_task import FeedbackTaskIn, read_task

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


class FeedbackStatusIn(BaseModel):
    status: str | None = None
    admin_note: str | None = Field(default=None, max_length=5000)


class FeedbackAuditIn(BaseModel):
    reason: str
    idempotency_key: str


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
    q: str | None = Query(None, max_length=200),
    admin=Depends(require_admin),
):
    """反馈管理元数据列表，正文需单条显式审计。"""
    try:
        return get_store().feedback_list(
            limit=limit, offset=offset, rating=rating, reason=reason,
            user_id=user_id, date_from=date_from, date_to=date_to, status=status, q=q,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("/export")
def export_feedback(
    rating: str | None = Query(None), reason: str | None = Query(None),
    user_id: str | None = Query(None), date_from: str | None = Query(None),
    date_to: str | None = Query(None), status: str | None = Query(None),
    q: str | None = Query(None, max_length=200),
    admin=Depends(require_admin),
):
    """仅导出筛选范围的管理元数据，不提供批量正文导出。"""
    try:
        data = get_store().feedback_list(limit=10000, offset=0, rating=rating, reason=reason,
            user_id=user_id, date_from=date_from, date_to=date_to, status=status, q=q)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    buf = io.StringIO()
    if data['total'] > 10000:
        raise HTTPException(409, '符合条件的反馈超过10000条，请缩小筛选范围后导出；本次未生成不完整文件')
    buf.write("﻿")  # BOM，Excel 正确识别 UTF-8
    writer = csv.writer(buf)
    writer.writerow(["编号", "消息编号", "会话编号", "用户编号", "时间", "评价", "状态", "原因", "有描述", "有备注"])
    for it in data["items"]:
        writer.writerow([
            it['id'], it['message_id'], it['conv_id'], it['user_id'], it['created_at'], it['rating'], it['status'] if it['rating'] == 'down' else '无需处理',
            "、".join(it.get("reasons") or []),
            it['has_comment'], it['has_admin_note'],
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=feedback.csv"},
    )


@router.post('/{fb_id}/audit-content')
def audit_feedback(fb_id: int, body: FeedbackAuditIn, admin=Depends(require_admin)):
    try:
        result = get_store().audit_feedback_content(fb_id, actor_id=admin['user_id'],
            reason=body.reason, idempotency_key=body.idempotency_key)
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})
    except PermissionError:
        raise HTTPException(403, '需要管理员权限') from None
    except AuditedFeedbackUnavailable as exc:
        raise HTTPException(404, '反馈正文不可用', headers={'X-Audit-Event-ID': exc.event_id}) from None
    except ValueError as exc:
        if '冲突' in str(exc):
            raise HTTPException(409, '审计幂等键冲突，请重新明确查看') from None
        raise HTTPException(400, '查看原因或幂等键无效') from None
    except sqlite3.Error:
        raise HTTPException(503, '审计未完成，未返回正文；可使用原幂等键重试') from None


@router.post('/{fb_id}/task-context')
def feedback_task_context(fb_id: int, body: FeedbackTaskIn, request: Request, admin=Depends(require_admin)):
    from src.operations import request_metadata
    try:
        return read_task(get_store(), fb_id, body, admin, request_metadata(request))
    except sqlite3.Error:
        raise HTTPException(503, '审计未完成，未返回原任务内容，请重试') from None
    except PermissionError:
        raise HTTPException(403, '无权查看或资料已不可用') from None
    except LookupError:
        raise HTTPException(404, '原任务内容已不可用') from None
    except (OSError, ValueError, RuntimeError):
        raise HTTPException(409, '原件无法预览或完整性校验未通过；可重试或下载可用原件') from None


@router.patch("/{fb_id}")
def update_feedback(fb_id: int, body: FeedbackStatusIn, request: Request, admin=Depends(require_admin)):
    """管理员更新反馈处理状态（pending/resolved/ignored）与备注。"""
    if body.status is not None and body.status not in ("pending", "resolved", "ignored", "fixed", "no_change", "deferred"):
        raise HTTPException(status_code=400, detail="反馈处理状态无效")
    try:
        change = get_store().update_feedback_status(fb_id, body.status,
            body.admin_note if 'admin_note' in body.model_fields_set else ..., actor_id=admin['user_id'])
    except PermissionError:
        raise HTTPException(403, '请先审计查看当前反馈与备注') from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except LookupError:
        raise HTTPException(404, '反馈正文不可用') from None
    except sqlite3.Error:
        raise HTTPException(503, '反馈更新未完成') from None
    # 审计只存状态和备注是否变更，不记录用户说明或管理员备注正文。
    request.state.operations_object_ref = f'反馈:{fb_id}'
    request.state.operations_changes = [change]
    if 'admin_note' in body.model_fields_set:
        request.state.operations_changes.append({'field': 'admin_note', 'changed': True})
    return {"ok": True}


@router.delete("/{fb_id}")
def delete_feedback(fb_id: int, request: Request, admin=Depends(require_admin)):
    """管理员删除一条反馈（按 feedback id，区别于用户取消自己的反馈）。"""
    get_store().delete_feedback_admin(fb_id)
    request.state.operations_object_ref = f'反馈:{fb_id}'
    return {"ok": True}
