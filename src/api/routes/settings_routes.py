"""设置自检路由：主动测试连接器连通性（邮件 / Slack / 语义召回 / 断点续跑）。

副作用提示（前端据此决定是否二次确认）：
- email      ：仅连接 + 登录，不发送邮件——无副作用。
- slack      ：会向 Webhook 绑定的频道发送一条测试消息——有副作用，前端应先让用户确认。
- embedding  ：实际请求一次 embedding 端点——无外部可见副作用。
- checkpoint ：本地 SQLite 持久化，没有远程连接可测，仅探测本地存储目录可写——无副作用。

返回统一为 {"ok": bool, "detail": str}；自检失败不抛 5xx，把原因放 detail 回前端。
"""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import get_current_user, get_store, require_admin

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SelfCheckIn(BaseModel):
    target: str  # email | slack | embedding | checkpoint


class OnboardingStateIn(BaseModel):
    state: Literal["completed", "skipped"]


def get_settings_store():
    """设置持久化 Seam，测试可替换为隔离数据库。"""

    return get_store()


_MODEL_CONNECTIONS_GUIDE_KEY = "onboarding.model_connections"


@router.get("/onboarding/model-connections")
def get_model_connections_onboarding(
    user=Depends(get_current_user),
    store=Depends(get_settings_store),
):
    state = store.get_user_ui_state(
        user["user_id"],
        _MODEL_CONNECTIONS_GUIDE_KEY,
    )
    return {"state": state or "not_started"}


@router.put("/onboarding/model-connections")
def set_model_connections_onboarding(
    body: OnboardingStateIn,
    user=Depends(get_current_user),
    store=Depends(get_settings_store),
):
    store.set_user_ui_state(
        user["user_id"],
        _MODEL_CONNECTIONS_GUIDE_KEY,
        body.state,
    )
    return {"state": body.state}


@router.post("/selfcheck")
async def selfcheck(body: SelfCheckIn, _admin=Depends(require_admin)):
    target = (body.target or "").strip().lower()
    try:
        if target == "email":
            from src.conductor.email_sender import verify_connection
            await asyncio.to_thread(verify_connection)  # 阻塞 IO 丢线程池，避免卡事件循环
            return {"ok": True, "detail": "SMTP 连接并登录成功（未发送邮件）"}

        if target == "slack":
            from src.conductor.slack_sender import is_slack_configured, send_report, unavailable_reason
            if not is_slack_configured():
                return {"ok": False, "detail": unavailable_reason()}
            await send_report("Mangrove 连接自检", "这是一条来自设置页的连接测试消息，可忽略。")
            return {"ok": True, "detail": "已向 Slack 频道发送测试消息"}

        if target == "embedding":
            from src.memory.embeddings import (
                embed_texts_with_model, is_embedding_enabled, is_rerank_configured, rerank_scores,
            )
            if not is_embedding_enabled():
                return {"ok": False, "detail": "语义召回未启用（EMBEDDING_ENABLED=false）"}
            got = await asyncio.to_thread(embed_texts_with_model, ["连接自检"])
            if not (got and got[1] and got[1][0]):
                return {"ok": False, "detail": "embedding 端点不可用或返回为空（本地与云端备选均失败）"}
            detail = f"embedding 可用（实际模型 {got[0]}，维度 {len(got[1][0])}）"
            if is_rerank_configured():
                scores = await asyncio.to_thread(rerank_scores, "连接自检", ["连接自检"])
                detail += "；rerank 可用" if scores else "；rerank 不可用（已配置但调用失败，召回将退回余弦排序）"
            return {"ok": True, "detail": detail}

        if target == "checkpoint":
            from src.config.settings import settings
            from src.conductor.graph import probe_checkpoint_storage
            if not settings.checkpoint_enabled:
                return {"ok": False, "detail": "断点续跑未启用（配置中心可重新开启）"}
            db_path = await asyncio.to_thread(probe_checkpoint_storage)
            return {"ok": True, "detail": f"本地存储目录可写（{db_path}）"}

        raise HTTPException(status_code=400, detail="不支持的自检目标")
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 自检失败把原因回前端，不返回 5xx
        return {"ok": False, "detail": str(e)[:300]}
