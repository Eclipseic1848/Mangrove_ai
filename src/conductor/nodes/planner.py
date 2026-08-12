"""规划节点：LLM 把意图理解转成完整执行策略草稿，再确定性校验为 TaskSpec。"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

from src.collectors import known_platforms, normalize_platform
from src.config.settings import settings
from src.llm import achat
from src.memory import lesson_for_planner, skills_for_planner

from ..prompts import PLANNER_SYSTEM
from ..state import ConductorState
from ..task_spec import TaskSpec
from ..utils import parse_json_obj

logger = logging.getLogger(__name__)

# 纯数字回复（澄清数量时用户直接回"50"/"50条"）：确定性识别为显式数量，
# 不依赖 LLM 二次解析，避免漏判导致反复追问。
_BARE_NUM = re.compile(r"^\s*(\d{1,4})\s*(条|个|篇|份|项)?\s*$")
# 用户表示"用默认/不限/你定"等：按默认数量处理，同样避免反复追问。
_DEFAULT_HINTS = ("默认", "随便", "都行", "你定", "你决定", "随意", "看着办", "不限", "尽量多", "越多越好")
# 大规模采集语义信号：用户表达了要穷尽/全量，但没说具体数字时追问上限
_MASSIVE_HINTS = ("大量", "全部", "所有", "尽可能多", "全面", "穷举", "统统", "一个不落")


def _detect_quantity(draft: dict, user_input: str) -> Optional[int]:
    """识别用户显式数量；未明确返回 None（触发追问）。

    优先级：最新输入是纯数字 > 表示"用默认" > LLM 草稿里的具体 max_items。
    """
    text = (user_input or "").strip()
    m = _BARE_NUM.match(text)
    if m:
        return max(1, int(m.group(1)))
    if any(h in text for h in _DEFAULT_HINTS):
        return settings.collector_max_items
    raw = draft.get("max_items")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return max(1, int(raw))
    if isinstance(raw, str) and raw.strip().isdigit():
        return max(1, int(raw.strip()))
    return None


def _looks_like_massive(user_input: str) -> bool:
    """检测用户是否表达了大批量采集需求（需追问上限）。"""
    text = user_input.strip()
    return any(h in text for h in _MASSIVE_HINTS)


async def _plan(understanding: dict, user_input: str, provider, model, lesson_text: str = "") -> Tuple[dict, Optional[str]]:
    """调 LLM 产策略草稿；解析失败重试 1 次；最终失败返回 ({}, None) 触发兜底。"""
    platforms = "、".join(known_platforms())
    system = PLANNER_SYSTEM.format(platforms=platforms) + skills_for_planner() + lesson_text
    user = (
        f"用户原始诉求：{user_input}\n"
        f"上游理解：{json.dumps(understanding, ensure_ascii=False)}"
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for attempt in range(2):
        text = await achat(messages, provider=provider, model=model, temperature=0)
        obj = parse_json_obj(text)
        if obj:
            return obj, obj.get("reasoning")
        logger.warning("规划 JSON 解析失败（第 %d 次）", attempt + 1)
    return {}, None


async def planner_node(state: ConductorState) -> Dict[str, Any]:
    understanding = state.get("understanding") or {}
    user_input = state.get("user_input", "")

    # 方案 D：planner 侧教训注入 + 命中埋点
    try:
        from src.api.auth import get_store as _get_store
        _store = _get_store()
    except Exception:
        _store = None
    lesson_text = lesson_for_planner(user_input, store=_store, task_id=state.get("task_id") or "")

    try:
        draft, reasoning = await _plan(understanding, user_input, state.get("provider"), state.get("model"), lesson_text)
    except Exception:
        logger.warning("规划 LLM 调用失败，降级为确定性兜底", exc_info=True)
        draft, reasoning = {}, None

    if not draft:
        logger.warning("规划草稿为空，使用 understanding/用户输入兜底构造 TaskSpec")

    # 平台名归一（即便 LLM 没用规范名，也尽量让路由命中专用采集器）
    if draft.get("platforms"):
        draft["platforms"] = [normalize_platform(p) for p in draft["platforms"] if str(p).strip()]

    spec = TaskSpec.from_draft(draft, fallback_text=user_input)
    out: Dict[str, Any] = {"task_spec": spec}
    if reasoning:
        out["plan_reasoning"] = reasoning

    # 数量策略：LLM 语义推断 + 仅大规模需求追问，其余静默默认 + 透明告知
    qty = _detect_quantity(draft, user_input)
    cap = settings.collector_max_items
    if qty is not None:
        spec.max_items = max(1, min(qty, cap))
        out["needs_clarification"] = False
    elif spec.urls:
        out["needs_clarification"] = False
    elif _looks_like_massive(user_input):
        out["needs_clarification"] = True
        out["clarification_question"] = (
            f"本次大概需要采集多少条数据？回一个数字（1–{cap}），越多耗时越长。"
        )
    else:
        spec.max_items = min(20, cap)
        out["needs_clarification"] = False
        out["inferred_quantity"] = spec.max_items
    return out
