"""意图节点：理解用户意图（产出松散 understanding），要么追问澄清，要么交给规划节点。"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any, Dict, Optional

from src.llm import achat
from src.memory import personal_context, preferences_context

from ..prompts import INTENT_SYSTEM
from ..state import ConductorState, frozen_task_context
from ..utils import parse_json_obj

logger = logging.getLogger(__name__)


async def _understand(llm_messages: list, provider, model) -> Optional[dict]:
    """调 LLM 理解意图，JSON 解析失败重试 1 次；最终失败返回 None。"""
    for attempt in range(2):
        text = await achat(llm_messages, provider=provider, model=model, temperature=0)
        obj = parse_json_obj(text)
        if obj:
            return obj
        logger.warning("意图理解 JSON 解析失败（第 %d 次）", attempt + 1)
    return None


async def intent_node(state: ConductorState) -> Dict[str, Any]:
    provider = state.get("provider")
    history = state.get("messages", [])
    user_input = state.get("user_input", "")

    context_update = {}
    try:
        from src.config.settings import settings
        from src.memory._library_scope import execution_owner
        from src.task_context import TaskContextRepository, TaskContextService

        preview = frozen_task_context(state)
        if preview is None and (owner := execution_owner()) is not None:
            preview = TaskContextService(TaskContextRepository(settings.webui_db_path)).conversation_preview(
                owner_id=owner, objective_text=user_input,
                purpose=state.get("task_context_purpose", "web_research"),
            )
        if preview is not None:
            context_update = {"task_context_preview": preview.model_dump(mode="json")}
            advice = "\n\n" + preview.compiled_context.content
        else:
            # 无执行身份的离线旧入口只沿用原 ContextVar，不猜测或读取个人账号。
            advice = preferences_context(user_input) + personal_context(user_input)
    except (OSError, UnicodeError, sqlite3.Error, ValueError):
        return {"needs_clarification": False, "error": "记忆或冻结上下文暂时无法读取，已停止本次执行。"}
    system = INTENT_SYSTEM + advice
    llm_messages: list = [{"role": "system", "content": system}]
    llm_messages.extend(history)
    if not history and user_input:
        llm_messages.append({"role": "user", "content": user_input})

    try:
        obj = await _understand(llm_messages, provider, state.get("model"))
    except Exception as e:
        logger.exception("意图理解 LLM 调用失败")
        return {
            **context_update,
            "needs_clarification": False,
            "error": f"模型调用失败：{e}。请检查 .env 中所选供应商的 API Key 与地址。",
        }

    if not obj:
        return {
            **context_update,
            "needs_clarification": True,
            "clarification_question": (
                "我没太理解你的需求。能再具体说明一下：想采集什么数据、"
                "从哪个平台或网址、以及想要什么形式的产出（报告/数据/入库）？"
            ),
        }

    if obj.get("need_clarification"):
        return {
            **context_update,
            "needs_clarification": True,
            "clarification_question": obj.get("question") or "请补充更多细节。",
        }

    return {
        **context_update,
        "needs_clarification": False,
        "clarification_question": None,
        "understanding": obj.get("understanding") or {},
    }
