"""HITL 确认路由：入库 / 邮件 / Slack / 沉淀模板。均复用现有后端函数。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from src.conductor.db_writer import write_items
from src.conductor.email_sender import send_report as send_email
from src.conductor.slack_sender import send_report as send_slack
from src.memory import distill_template, save_template

from ..auth import get_current_user
from ..schemas import ConfirmIn
from ..session_store import pending_store

router = APIRouter(prefix="/api/confirm", tags=["confirm"])


@router.post("/db")
def confirm_db(body: ConfirmIn, user=Depends(get_current_user)):
    pend = pending_store.pop_action(user["user_id"], body.task_id, "db")
    if not pend or not pend.get("items"):
        raise HTTPException(status_code=404, detail="没有待入库的数据或已处理")
    try:
        n = write_items(pend["task_id"], pend["items"], source=pend.get("source", ""))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"入库失败：{e}")
    return {"ok": True, "message": f"已写入 {n} 条数据到数据库。"}


@router.post("/email")
def confirm_email(body: ConfirmIn, user=Depends(get_current_user)):
    pend = pending_store.pop_action(user["user_id"], body.task_id, "email")
    if not pend or not pend.get("to"):
        raise HTTPException(status_code=404, detail="没有待发送的邮件或已处理")
    try:
        n = send_email(pend["to"], pend["subject"], pend.get("body", ""),
                       attachments=pend.get("attachments"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"邮件发送失败：{e}")
    return {"ok": True, "message": f"已发送报告邮件至 {n} 位收件人：{', '.join(pend['to'])}。"}


@router.post("/slack")
async def confirm_slack(body: ConfirmIn, user=Depends(get_current_user)):
    pend = pending_store.pop_action(user["user_id"], body.task_id, "slack")
    if not pend:
        raise HTTPException(status_code=404, detail="没有待推送的 Slack 消息或已处理")
    try:
        await send_slack(pend["title"], pend.get("body", ""))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Slack 推送失败：{e}")
    return {"ok": True, "message": "已推送报告到 Slack 频道。"}


@router.post("/template")
async def confirm_template(body: ConfirmIn, user=Depends(get_current_user)):
    pend = pending_store.pop_action(user["user_id"], body.task_id, "template")
    if not pend or not pend.get("analysis"):
        raise HTTPException(status_code=404, detail="没有可沉淀的模板或已处理")
    try:
        tpl = await distill_template(pend["intent"], pend["data_type"], pend["analysis"],
                                     provider=pend.get("provider"), model=pend.get("model"))
        if not tpl:
            raise HTTPException(status_code=422, detail="未能提炼出有效模板结构，请重试")
        slug = await save_template(title=tpl["title"], data_type=pend["data_type"],
                                   keywords=tpl["keywords"] or pend.get("keywords") or [], body=tpl["body"])
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"沉淀模板失败：{e}")
    if slug is None:
        return {"ok": True, "message": f"「{tpl['title']}」的内容已被现有模板库覆盖，未新增/更新模板。",
                "slug": None, "title": tpl["title"]}
    return {"ok": True, "message": f"已沉淀模板「{tpl['title']}」（{slug}），下次同类任务自动复用。",
            "slug": slug, "title": tpl["title"]}
