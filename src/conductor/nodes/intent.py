"""意图节点：理解用户意图（产出松散 understanding），要么追问澄清，要么交给规划节点。"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from src.llm import achat
from src.memory import personal_context, preferences_context

from ..prompts import INTENT_SYSTEM
from ..state import ConductorState
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

    system = INTENT_SYSTEM + preferences_context() + personal_context()
    llm_messages: list = [{"role": "system", "content": system}]
    llm_messages.extend(history)
    if not history and user_input:
        llm_messages.append({"role": "user", "content": user_input})

    try:
        obj = await _understand(llm_messages, provider, state.get("model"))
    except Exception as e:
        logger.exception("意图理解 LLM 调用失败")
        return {
            "needs_clarification": False,
            "error": f"模型调用失败：{e}。请检查 .env 中所选供应商的 API Key 与地址。",
        }

    if not obj:
        return {
            "needs_clarification": True,
            "clarification_question": (
                "我没太理解你的需求。能再具体说明一下：想采集什么数据、"
                "从哪个平台或网址、以及想要什么形式的产出（报告/数据/入库）？"
            ),
        }

    if obj.get("need_clarification"):
        return {
            "needs_clarification": True,
            "clarification_question": obj.get("question") or "请补充更多细节。",
        }

    return {
        "needs_clarification": False,
        "clarification_question": None,
        "understanding": obj.get("understanding") or {},
    }
