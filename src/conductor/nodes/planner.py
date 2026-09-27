"""规划节点：LLM 把意图理解转成完整执行策略草稿，再确定性校验为 TaskSpec。"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from src.timezone import now as beijing_now
from typing import Any, Dict, Optional, Tuple
from pydantic import ValidationError

from src.collectors import known_platforms, normalize_platform
from src.collectors.platforms import resolve_domains, url_in_domains
from src.config.settings import settings
from src.llm import achat
from src.model_connections.text_protocol import ModelOutputTruncatedError
from src.memory._library_scope import execution_owner
from src.memory import lesson_for_planner, skills_for_planner

from ..prompts import PLANNER_SYSTEM
from ..state import ConductorState
from ..task_spec import AnalysisType, DataType, OutputFormat, TaskSpec
from ..utils import parse_json_obj

logger = logging.getLogger(__name__)

# 纯数字回复（澄清数量时用户直接回"50"/"50条"）：确定性识别为显式数量，
# 不依赖 LLM 二次解析，避免漏判导致反复追问。
_BARE_NUM = re.compile(r"^\s*(\d{1,4})\s*(条|个|篇|份|项)?\s*$")
# 用户表示"用默认/不限/你定"等：按默认数量处理，同样避免反复追问。
_DEFAULT_HINTS = ("默认", "随便", "都行", "你定", "你决定", "随意", "看着办", "不限", "尽量多", "越多越好")
# 大规模采集语义信号：用户表达了要穷尽/全量，但没说具体数字时追问上限
_MASSIVE_HINTS = ("大量", "全部", "所有", "尽可能多", "全面", "穷举", "统统", "一个不落")
_EXPLICIT_URL = re.compile(r'https?://[^\s<>"\x27`，。；！？）\]]+', re.I)


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
    """解析或来源冲突时最多纠正一次，不通过删除约束迁就错误规划。"""
    platforms = "、".join(known_platforms())
    system = PLANNER_SYSTEM.format(platforms=platforms) + skills_for_planner() + lesson_text
    # 使用与执行端相同的契约，避免文字说明遗漏层级、范围和null约束。
    system += "\n任务字段必须满足以下JSON Schema；不需要的缺省字段请省略，不能用null代替非空类型。reasoning可另附简短说明。\n" + json.dumps(TaskSpec.model_json_schema(), ensure_ascii=False)
    user = (
        f"当前北京时间：{beijing_now().isoformat()}；调度统一按 Asia/Shanghai（UTC+8）执行。\n"
        f"用户原始诉求：{user_input}\n"
        f"上游理解：{json.dumps(understanding, ensure_ascii=False)}"
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    rejected = {}
    for attempt in range(2):
        try:
            text = await achat(messages, provider=provider, model=model, temperature=0)
        except ModelOutputTruncatedError:
            raise
        except Exception:
            if rejected:
                # 纠正调用失败不能落入空草稿兜底，避免丢失已知冲突。
                return rejected, None
            raise
        obj = parse_json_obj(text)
        if obj:
            if _source_conflict(obj, user_input) and attempt == 0:
                rejected = obj
                messages.extend([
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": "规划来源与用户原文不一致。请重新规划，严格保留原始URL、站点、数量、时间、预算与输出要求；不要要求用户改用小红书。非小红书任务不得设置evidence_collection或bank_benefits。无法表示的约束不可静默删除。只返回完整JSON草稿。"},
                ])
                continue
            return obj, obj.get("reasoning")
        logger.warning("规划 JSON 解析失败（第 %d 次）", attempt + 1)
    return rejected, None


def _source_conflict(draft: dict, user_input: str) -> bool:
    # ponytail: 只做字面来源保全；含排除链接等歧义时宁可澄清，不推测授权。
    explicit_urls = {url.rstrip(").,;") for url in _EXPLICIT_URL.findall(user_input)}
    for clause in re.split(r"[，。；！？\n]", user_input):
        if _EXPLICIT_URL.search(clause) and re.search(
                r"不要|不得|禁止|排除|勿|不读取|不访问|不能|不需要|不作为|示例|例如|仅供参考|\b(?:not|don't|except|exclude)\b",
                _EXPLICIT_URL.sub("", clause), re.I):
            return True
    planned_urls = draft.get("urls") or []
    if explicit_urls and (not isinstance(planned_urls, list)
                          or set(map(str, planned_urls)) != explicit_urls):
        return True
    if explicit_urls:
        domains = resolve_domains(draft.get("platforms"), draft.get("site_domains"))
        if domains and not all(url_in_domains(url, domains) for url in explicit_urls):
            return True
    for field in ("evidence_collection", "bank_benefits"):
        scene = draft.get(field)
        if isinstance(scene, dict):
            # 专用场景按查询词发现来源，不消费显式URL，不能替代定向抓取。
            if explicit_urls:
                return True
            source = str(scene.get("source_evidence") or "").strip()
            if not source or source not in user_input or normalize_platform(source) != "小红书":
                return True
    return False


def _validation_hint(exc: ValueError) -> str:
    """只公开契约字段与固定错误类别，不能回显模型值或未知字段名。"""
    from ..task_spec import BankBenefitsScope, EvidenceCollectionScope, SearchQuery
    if isinstance(exc, ValidationError):
        known = set().union(*(set(model.model_fields) for model in
                              (TaskSpec, BankBenefitsScope, EvidenceCollectionScope, SearchQuery)))
        hints = []
        for error in exc.errors(include_input=False, include_url=False):
            path = ".".join(str(part) if isinstance(part, int) or part in known else "未知字段"
                            for part in error["loc"]) or "任务约束"
            hints.append(path + "（" + error["type"] + "）")
        return "；".join(dict.fromkeys(hints))[:500]
    safe = {"任务草稿必须为对象", "任务草稿包含未支持字段", "任务文本参数必须为文本",
            "银行范围与顶层采集参数冲突", "采集范围与顶层采集参数冲突", "列表项必须为非空文本", "列表参数必须为文本或文本列表",
            "输出格式不能为空", "数量和页码必须为整数", "是否采集评论必须为布尔值", "任务意图必须为文本"}
    return str(exc) if str(exc) in safe else "参数类型或取值不符合任务契约"


async def planner_node(state: ConductorState) -> Dict[str, Any]:
    understanding = state.get("understanding") or {}
    user_input = state.get("user_input", "")

    # 方案 D：planner 侧教训注入 + 命中埋点
    try:
        from src.api.auth import get_store as _get_store
        _store = _get_store()
    except Exception:
        _store = None
    lesson_text = lesson_for_planner(user_input, store=_store, task_id=state.get("task_id") or "", owner_id=execution_owner())

    try:
        draft, reasoning = await _plan(understanding, user_input, state.get("provider"), state.get("model"), lesson_text)
    except ModelOutputTruncatedError:
        # 规划不完整时不能用默认范围替代用户授权。
        raise
    except Exception:
        logger.warning("规划 LLM 调用失败，降级为确定性兜底", exc_info=True)
        draft, reasoning = {}, None

    if not draft:
        logger.warning("规划草稿为空，使用 understanding/用户输入兜底构造 TaskSpec")

    # 平台名归一（即便 LLM 没用规范名，也尽量让路由命中专用采集器）
    if draft.get("platforms"):
        draft["platforms"] = [normalize_platform(p) for p in draft["platforms"] if str(p).strip()]

    # 纠正后仍冲突则停止；删掉场景会遗留错误平台并丢失场景内的约束。
    if _source_conflict(draft, user_input):
        return {"needs_clarification": True, "clarification_question": "未能生成符合原始来源约束的采集计划，已停止执行。请核对任务来源与采集范围后重试。"}
    try:
        spec = TaskSpec.from_draft(draft, fallback_text=user_input)
    except ValueError as exc:
        problems = ([{"field": ".".join(map(str, error["loc"])), "type": error["type"]}
                     for error in exc.errors(include_input=False, include_url=False)]
                    if isinstance(exc, ValidationError) else [{"field": "task_spec", "type": "invalid_parameters"}])
        logger.warning("规划参数校验失败：%s", problems)
        # 不把无法冻结的额度或来源参数静默降级成另一种采集。
        return {"needs_clarification": True, "clarification_question": "任务参数未能校验：" + _validation_hint(exc) + "。请核对后重试。"}
    out: Dict[str, Any] = {"task_spec": spec}
    if reasoning:
        out["plan_reasoning"] = reasoning
    if spec.evidence_collection:
        spec.platforms = ["小红书"]
        spec.keywords = [query.keyword for query in spec.evidence_collection.queries]
        spec.data_type = DataType.POST
        spec.analysis_type = AnalysisType.NONE
        spec.outputs = [OutputFormat.JSON]
        spec.xhs_sort = spec.xhs_sort or "general"
        spec.xhs_note_type = spec.xhs_note_type or "image"
        spec.max_items = len(spec.evidence_collection.queries) * spec.evidence_collection.target_count
        out["needs_clarification"] = False
        return out
    if spec.bank_benefits:
        spec.platforms = ["小红书"]
        spec.keywords = [bank.keyword for bank in spec.bank_benefits.banks]
        spec.data_type = DataType.POST
        spec.analysis_type = AnalysisType.NONE
        spec.outputs = [OutputFormat.JSON]
        spec.xhs_sort = spec.xhs_sort or "general"
        spec.xhs_note_type = spec.xhs_note_type or "image"
        spec.max_items = len(spec.bank_benefits.banks) * spec.bank_benefits.target_count
        out["needs_clarification"] = False
        return out

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
