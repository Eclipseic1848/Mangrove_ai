"""分析节点：按 analysis_type 对清洗后数据做 VOC 槽点分析或通用摘要。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.config.settings import settings
from src.llm import achat
from src.memory import lesson_for_analyze, match_template, skill_for_analysis

from ..prompts import (
    ANALYZE_ARTICLE_SYSTEM,
    ANALYZE_BID_SYSTEM,
    ANALYZE_PRODUCT_SYSTEM,
    ANALYZE_SUMMARY_SYSTEM,
    ANALYZE_VOC_SYSTEM,
)
from ..state import ConductorState
from ..targets import is_direct_video_manifest
from ..task_spec import AnalysisType, DataType, TaskSpec
from ..voc_bridge import run_voc_processor

logger = logging.getLogger(__name__)

# 数据类型 → 专属领域报告模板（口碑由 analysis_type=voc 单独处理；其余按数据类型选）
_DOMAIN_SYSTEM = {
    DataType.BID: ANALYZE_BID_SYSTEM,
    DataType.ARTICLE: ANALYZE_ARTICLE_SYSTEM,
    DataType.PRODUCT: ANALYZE_PRODUCT_SYSTEM,
    DataType.COMMENT: ANALYZE_VOC_SYSTEM,  # 评论默认按口碑结构
}

# 强领域数据类型：有专属模板且本质上不是口碑（招投标/新闻/商品）。
# 这些类型的专属模板优先于 analysis_type——避免较弱模型把意图误判成 voc 时被带偏
# （如招投标任务被误判 analysis_type=voc 而错套 VOC 槽点模板）。
_STRONG_DOMAINS = {DataType.BID, DataType.ARTICLE, DataType.PRODUCT}

# VOC（口碑/槽点）只对"评论/社媒帖子"这类口碑数据成立。
# 关键：不靠孤立的 analysis_type=voc 标志（弱模型常乱打），而要求数据本身是 comment/post，
# 否则"未知/通用类型被误判 voc"会抢占自学习路径（learned→fallback 沉淀新模板）。
_VOC_DATA_TYPES = {DataType.COMMENT, DataType.POST}

# VOC 意图信号词：意图/关键词中出现这些词才认为是真正的 VOC 任务（用户声音/槽点分析）。
# 弱模型常把"解析评论""赛事评论"里的"评论"误判为 voc——但那是文章分析，不是 VOC。
_VOC_INTENT_SIGNALS = [
    "槽点", "口碑", "吐槽", "差评", "好评", "投诉", "测评",
    "满意度", "用户体验", "风评", "舆情", "评价",
]
# 反信号：如果意图中出现这些词，大概率不是 VOC 而是文章/资讯分析，
# 即使 analysis_type=voc 也应当忽略（弱模型误判）。
_VOC_ANTI_SIGNALS = [
    "比赛", "赛事", "新闻", "报道", "政策", "公告", "通知",
    "解读", "标讯", "招标", "商品", "参数", "选购",
]


# LLM 路由分类的合法取值（P0-4 双通道：小 LLM 分类补充词表盲区）
_ROUTE_VALUES = {"voc", "bid", "article", "product", "generic"}

_ROUTE_SYSTEM = (
    "你是分析报告的模板路由器。根据任务意图判断本次应产出哪类报告，只输出下面五个词之一，"
    "不要输出任何其他内容：\n"
    "voc（用户口碑/槽点/差评/满意度分析）\n"
    "bid（招投标/标讯/采购公告梳理）\n"
    "article（新闻/资讯/文章/观点汇总）\n"
    "product（商品信息/参数卖点对比）\n"
    "generic（以上都不是）"
)


async def _classify_route_llm(spec: TaskSpec, provider, model) -> Optional[str]:
    """双通道路由的 LLM 通道：小分类调用判断报告类型；失败/非法输出返回 None（回退词表通道）。"""
    user = f"任务意图：{spec.intent}\n关键词：{'、'.join(spec.keywords or []) or '无'}"
    try:
        text = await achat(
            [{"role": "system", "content": _ROUTE_SYSTEM}, {"role": "user", "content": user}],
            provider=provider, model=model, temperature=0,
        )
    except Exception as e:
        logger.warning("模板路由 LLM 分类失败，回退词表通道：%s", e)
        return None
    route = (text or "").strip().lower().split()[0] if (text or "").strip() else ""
    route = route.strip("。.，,\"'`")
    return route if route in _ROUTE_VALUES else None


def _has_voc_signal(spec: TaskSpec) -> bool:
    """检查意图/关键词中是否有真正的 VOC 信号（而非误判）。

    仅当同时满足以下条件时返回 True：
    1. 意图或关键词包含 VOC 信号词（槽点/口碑/吐槽等）；
    2. 意图不包含明显的反信号词（比赛/新闻/政策等），避免弱模型误判。
    """
    haystack = ((spec.intent or "") + " " + " ".join(spec.keywords or [])).lower()
    has_signal = any(s in haystack for s in _VOC_INTENT_SIGNALS)
    has_anti = any(s in haystack for s in _VOC_ANTI_SIGNALS)
    return has_signal and not has_anti


def _select_system(spec: TaskSpec, llm_route: Optional[str] = None) -> tuple[str, str, Optional[str]]:
    """按任务选分析模板，返回 (system_prompt, 来源, 已学模板slug)。slug 仅在 learned 时非空。

    双通道路由（P0-4）：词表信号（确定性）+ LLM 分类（llm_route，覆盖词表盲区）；
    llm_route=None 时行为与纯词表通道完全一致（LLM 失败自动回退）。

    选择顺序（让自学习不被误判的 voc 抢占）：
    1. 强领域专属模板（招投标/新闻/商品）——不被 analysis_type 带偏；
    2. 口碑意图 且 数据是评论/帖子 且（词表 VOC 信号 或 LLM 判 voc）→ VOC 槽点结构；
    3. LLM 判为强领域而 data_type 未识别 → 对应领域模板（修 data_type 误判）；
    4. data_type 内置模板（非 VOC 的走 summary 路径）；
    5. 已学模板召回（历史沉淀的同类任务结构）；
    6. 通用兜底（触发自学习：识别并记忆"新类型"的模板）。
    """
    if spec.data_type in _STRONG_DOMAINS:
        return _DOMAIN_SYSTEM[spec.data_type], "builtin", None
    # VOC 路由：analysis_type=voc + data_type 是评论/帖子 + （词表信号 或 LLM 分类判 voc）。
    # 弱模型常把"解析评论""赛事评论"误判为 voc，词表与 LLM 双通道都不认时放行到自学习路径。
    if (
        spec.analysis_type == AnalysisType.VOC
        and spec.data_type in _VOC_DATA_TYPES
        and (_has_voc_signal(spec) or llm_route == "voc")
    ):
        return ANALYZE_VOC_SYSTEM, "voc", None
    # LLM 通道判为强领域而 data_type 未识别（generic 等）：用对应领域模板，修 data_type 误判
    _ROUTE_TO_TYPE = {"bid": DataType.BID, "article": DataType.ARTICLE, "product": DataType.PRODUCT}
    if llm_route in _ROUTE_TO_TYPE and spec.data_type not in _VOC_DATA_TYPES:
        return _DOMAIN_SYSTEM[_ROUTE_TO_TYPE[llm_route]], "builtin", None
    # 被三重验证拦截的"伪 VOC"：忽略 analysis_type，按 data_type 走内置模板或自学习。
    # 这样弱模型误判不会把整个分析流水线带偏。
    builtin = _DOMAIN_SYSTEM.get(spec.data_type)
    if builtin:
        # data_type=comment/post 的内置模板也是 VOC，但此时 signalling 不足，
        # 不应强行走 VOC——改为走已学模板/兜底 summary，给自学习留下空间。
        if spec.data_type in _VOC_DATA_TYPES:
            # 跳过内置 VOC 模板，让 comment/post 也能走自学习
            pass
        else:
            return builtin, "builtin", None
    learned = match_template(spec)
    if learned:
        return learned["body"], "learned", learned["slug"]
    return ANALYZE_SUMMARY_SYSTEM, "fallback", None


def _route_desc(word_signal: bool, llm_route: Optional[str]) -> str:
    """路由依据的可读描述（白盒透出，让"为什么用这个模板"可解释）。"""
    return f"词表信号={'命中' if word_signal else '未命中'}，LLM分类={llm_route or '未启用'}"


def _build_blob(data: List[Dict[str, Any]]) -> str:
    max_blob = settings.analyze_max_blob_chars  # 喂给分析模型的总字符上限
    max_item = settings.clean_max_item_chars  # 单条正文上限（仅截断 LLM 上下文，不影响 data.json 落盘）
    parts: List[str] = []
    used = 0
    for i, item in enumerate(data, 1):
        title = item.get("title") or ""
        content = item.get("content") or ""
        url = item.get("url") or ""
        # 逐条截断：控制单条内容的 token 用量，保证 LLM 能看到更多不同条目
        if len(content) > max_item:
            content = content[:max_item] + " …(已截断)"
        # 带上链接，便于领域模板填"原文链接"字段
        block = f"[{i}] {title}\n{('链接：' + url + chr(10)) if url else ''}{content}\n"
        if used + len(block) > max_blob:
            break
        parts.append(block)
        used += len(block)
    return "\n".join(parts)


async def analyze_node(state: ConductorState) -> Dict[str, Any]:
    spec = state["task_spec"]
    data = state.get("cleaned_dataset", [])

    # 质检闭环（P1-2）：checker 打回时带问题清单重做。反馈只消费一次——
    # 所有返回路径都清空标记，保证第二轮 checker 后必然走 output（防死循环）。
    feedback = state.get("checker_feedback") or []
    consumed: Dict[str, Any] = {"checker_feedback": None} if feedback else {}

    if spec.analysis_type == AnalysisType.NONE or not data:
        return {"analysis": None, **consumed}

    # 显式视频只允许使用证据包，不能进入通用模板或自学习模板。
    direct_video = is_direct_video_manifest(state.get("target_manifest") or [])
    # 双通道路由（P0-4）：强领域 data_type 已确定则无需 LLM 分类（省一次调用）；
    # 其余场景先跑小 LLM 分类补充词表盲区，失败返回 None 自动回退纯词表通道。
    llm_route: Optional[str] = None
    if not direct_video and spec.data_type not in _STRONG_DOMAINS:
        llm_route = await _classify_route_llm(spec, state.get("provider"), state.get("model"))
    word_signal = _has_voc_signal(spec)
    logger.info("模板路由：词表VOC信号=%s，LLM分类=%s", word_signal, llm_route or "未启用/失败")

    # VOC 分析优先走 voc_processor 引擎（开关开启且依赖就绪时）；失败回退到下方 LLM 分析。
    # 与 _select_system 同一套双通道验证（analysis_type=voc + 评论/帖子数据 + 词表或LLM认定），
    # 避免招投标/新闻/赛事分析等被弱模型误判 voc 时错套槽点分析。
    # 质检打回重跑时跳过：voc_processor 是确定性引擎，同数据重跑只会产出同样的失败报告，
    # 改走下方 LLM 路径带着问题清单重写。
    if (
        not direct_video
        and not feedback
        and spec.analysis_type == AnalysisType.VOC
        and spec.data_type in _VOC_DATA_TYPES
        and (word_signal or llm_route == "voc")
    ):
        voc_text = await run_voc_processor(data)
        if voc_text:
            return {"analysis": voc_text, "analysis_source": "voc",
                    "analysis_route": _route_desc(word_signal, llm_route), **consumed}

    # 显式视频只分析已带来源标签的证据，不复用普通模板或技能。
    if direct_video:
        system = (
            "你是视频内容分析师。只能依据输入中【字幕】、【语音转写】和【画面文字】三个来源作答。"
            "分别说明证据支持的事实、视频表达的观点和不确定之处；不得把标题、简介、常识或搜索结果写成视频内容。"
        )
        source, template_slug = "video", None
    else:
        # 按任务类型选模板（口碑/招投标/新闻/商品/已学/通用），source 供前端判断是否提议沉淀
        system, source, template_slug = _select_system(spec, llm_route=llm_route)
        # 技能复用：VOC 任务注入 voc-analysis 技能正文，强化输出结构
        system += skill_for_analysis(spec)
    # 方案 D：传入 store 写命中埋点（供概览页聚合统计）
    try:
        from src.api.auth import get_store as _get_store
        _store = _get_store()
    except Exception:
        _store = None
    lesson_text, active_lesson_slug = ("", None) if direct_video else lesson_for_analyze(
        spec, store=_store, task_id=state.get("task_id") or ""
    )
    system += lesson_text

    user = f"任务目标：{spec.intent}\n"
    # 用户自然语言里的具体诉求始终叠加（覆盖/补充模板结构），不再限定 custom
    if spec.analysis_instruction:
        user += f"用户的额外/具体要求（请优先满足）：{spec.analysis_instruction}\n"
    # 质检闭环：把上一轮质检问题清单注入，要求重写时逐条修正
    if feedback:
        user += "上一轮质检未通过，重写报告时请逐条修正以下问题：\n" + \
                "\n".join(f"- {p}" for p in feedback) + "\n"
    user += f"\n以下是采集到的数据（共 {len(data)} 条，可能已截断）：\n\n{_build_blob(data)}"

    try:
        text = await achat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            provider=state.get("provider"),
            model=state.get("model"),
        )
    except Exception as e:
        logger.exception("分析 LLM 调用失败")
        return {"analysis": None, "error": f"分析失败：{e}", **consumed}

    out: Dict[str, Any] = {
        "analysis": text,
        "analysis_source": source,
        "analysis_route": "仅使用视频证据包" if direct_video else _route_desc(word_signal, llm_route),  # 路由依据（白盒/trace 可观测）
        **consumed,
    }
    if template_slug:
        out["template_slug"] = template_slug  # 命中已学模板：供 checker 回写使用统计
    if active_lesson_slug:
        out["active_lesson_slug"] = active_lesson_slug  # 命中教训：供 checker 通过时 record_lesson_helped
    return out
