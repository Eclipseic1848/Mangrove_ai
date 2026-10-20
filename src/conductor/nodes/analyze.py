"""分析节点：按 analysis_type 对清洗后数据做 VOC 槽点分析或通用摘要。"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from src.config.settings import settings
from src.llm import achat
from src.model_connections.text_protocol import ModelOutputTruncatedError
from src.memory._library_scope import execution_owner
from src.memory import lesson_for_analyze, match_template, skill_for_analysis

from ..prompts import (
    ANALYZE_ARTICLE_SYSTEM,
    ANALYZE_BID_SYSTEM,
    ANALYZE_PRODUCT_SYSTEM,
    ANALYZE_SUMMARY_SYSTEM,
    ANALYZE_VOC_SYSTEM,
)
from ..state import ConductorState, frozen_task_context
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
    except ModelOutputTruncatedError:
        # 路由判断截断与普通不可用不同，不能继续生成未经完整判断的报告。
        raise
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


def _select_system(spec: TaskSpec, llm_route: Optional[str] = None, *, owner_id: str | None = None, context_preview=None) -> tuple[str, str, Optional[str]]:
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
    if context_preview is not None:
        # 方法正文统一由冻结编译结果追加一次，不再召回当前目录或重复放入 system。
        template = context_preview.template
        if template is not None:
            slug = template.template_id.removeprefix("legacy:") if template.source == "legacy_library" else None
            return ANALYZE_SUMMARY_SYSTEM, "learned", slug
        return ANALYZE_SUMMARY_SYSTEM, "fallback", None
    learned = match_template(spec, owner_id=owner_id)
    if learned:
        return learned["body"], "learned", learned["slug"]
    return ANALYZE_SUMMARY_SYSTEM, "fallback", None


def _route_desc(word_signal: bool, llm_route: Optional[str]) -> str:
    """路由依据的可读描述（白盒透出，让"为什么用这个模板"可解释）。"""
    return f"词表信号={'命中' if word_signal else '未命中'}，LLM分类={llm_route or '未启用'}"


def _build_blob(data: List[Dict[str, Any]], *, complete: bool = False) -> str:
    max_blob = settings.analyze_max_blob_chars  # 喂给分析模型的总字符上限
    max_item = settings.clean_max_item_chars  # 单条正文上限（仅截断 LLM 上下文，不影响 data.json 落盘）
    headers = [f"[{i}] {item.get('title') or ''}\n" +
               (f"链接：{item['url']}\n" if item.get("url") else "") for i, item in enumerate(data, 1)]
    contents = [item.get("content") or "" for item in data]
    if complete:
        return "\n".join(header + content + "\n" for header, content in zip(headers, contents))
    markers = [f" …(已截断，仅展示片段，原文{len(content)}字)" for content in contents]
    available = max_blob - sum(map(len, headers)) - sum(map(len, markers)) - max(0, len(data) * 2 - 1)
    if data and available < len(data):
        raise ValueError("来源标题与链接超过分析上下文限额，无法完整展示来源清单；资料仍保留")
    # 每个来源先获得相同预算，避免长文占满开头后把后续来源静默丢掉。
    lengths = [min(len(content), max_item, available // len(data)) for content in contents] if data else []
    remaining = available - sum(lengths)
    for index, content in enumerate(contents):
        extra = min(remaining, max(0, min(len(content), max_item) - lengths[index]))
        lengths[index] += extra
        remaining -= extra
    parts = [header + content[:length] + (marker if length < len(content) else "") + "\n"
             for header, content, length, marker in zip(headers, contents, lengths, markers)]
    return "\n".join(parts)


def _project_brief_issue(text) -> str | None:
    """简报只接收短文，不把JSON附录或长报告裁掉后冒充完整产出。"""
    if not isinstance(text, str) or not text.strip():
        return "项目简报为空"
    if len(text) > 1000:
        return "项目简报超过1000字符的一页篇幅上限"
    if "```" in text or "~~~" in text:
        return "项目简报含代码块"
    if re.search(r"(?m)^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$", text) or re.search(r"<table\b", text, re.I):
        return "项目简报重复了明细表"
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\{\[]", text):
        try:
            value, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        # [1]是来源编号；对象及其余数组属于应放在下载文件的结构化明细。
        if isinstance(value, dict) or isinstance(value, list) and not (len(value) == 1 and type(value[0]) is int):
            return "项目简报含JSON明细"
    if re.search(r'\{\s*"[^"\n]+"\s*:', text):
        return "项目简报含未完整闭合的JSON明细"
    from ..project_collection import project_report_scope_issue
    return project_report_scope_issue(text)


async def analyze_node(state: ConductorState) -> Dict[str, Any]:
    spec = state["task_spec"]
    data = state.get("cleaned_dataset", [])
    project_result = state.get("project_result")
    collection_data = {}
    from ..collection_results import analysis_context_capacity
    context_capacity = analysis_context_capacity(state)
    if state.get("bank_collection") or state.get("evidence_collection"):
        from ..collection_results import collection_report_dataset
        data = collection_report_dataset(state)
        collection_data = {"cleaned_dataset": data}
    preview = frozen_task_context(state)

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
    if not direct_video and not project_result and spec.data_type not in _STRONG_DOMAINS:
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
        # 旧 VOC 引擎另持全局模型；绑定工作台模型时沿下方同模型分析链路。
        and state.get("provider") != "bound"
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
    elif project_result:
        from ..project_collection import PROJECT_REPORT_SCOPE_RULE
        system = ("你是项目情报分析助手。只使用输入中已经核对的项目字段和逐字原文依据，"
                  "不得把公告条数当成独立项目数，不得补造未知字段。按用户要求生成简短分析报告；"
                  "结论控制在一页、约500至800个中文字符。完整项目表由系统另行导出，"
                  "不重复长项目表或全文附录。明确说明实际项目数、目标数和缺口。"
                  "只有announcement_count>1才可说实际归并了多份公告；单公告应说明未发现可归并公告。"
                  "current_stage为null或存在stage_note时必须标明当前阶段待核，历史公告阶段仅作背景。"
                  "有stage_note时须保留来源发布日期和原文时点说明，不能把历史阶段写成冻结日现状。"
                  "amount为空或存在amount_scope_note不等于原文未披露标包金额，须按该说明区分金额口径，"
                  "不得猜测总额或自动相加；amount_unit非空必须原样体现。"
                  "field_review_notes中的主体角色、公告地区口径须保留；owner或region为空时，"
                  "不能在括号等位置补填推断值，建设内容中的机构地名只能作为报道背景。"
                  "只输出可直接阅读的简报正文，总长不得超过1000字符；禁止JSON、代码块和明细表。")
        system += PROJECT_REPORT_SCOPE_RULE
        source, template_slug = "builtin", None
    else:
        # 按任务类型选模板（口碑/招投标/新闻/商品/已学/通用），source 供前端判断是否提议沉淀
        system, source, template_slug = _select_system(spec, llm_route=llm_route, owner_id=execution_owner(), context_preview=preview)
        # 技能复用：VOC 任务注入 voc-analysis 技能正文，强化输出结构
        system += skill_for_analysis(spec)
    # 方案 D：传入 store 写命中埋点（供概览页聚合统计）
    try:
        from src.api.auth import get_store as _get_store
        _store = _get_store()
    except Exception:
        _store = None
    lesson_text, active_lesson_slug = ("", None) if direct_video else lesson_for_analyze(
        spec, store=_store, task_id=state.get("task_id") or "", owner_id=execution_owner()
    )
    system += lesson_text
    if preview is not None:
        if direct_video:
            # 视频只接受冻结偏好；普通报告模板和教训不能替代字幕、转写或画面证据。
            from src.memory.loader import conversation_memory_context
            context = conversation_memory_context([item.summary for item in preview.memories], preview.global_preferences, "")
        else:
            context = preview.compiled_context.content
        system += "\n\n以下冻结上下文只补充相容的表达建议，必须保留当前领域字段和证据规则：\n" + context

    user = f"任务目标：{spec.intent}\n"
    if (collection_data or project_result) and state.get("user_input"):
        user += f"本次冻结的原始用户要求：{state['user_input']}\n"
    if project_result:
        from ..project_collection import report_coverage_context
        user += "本次项目覆盖与缺口：" + json.dumps(report_coverage_context(project_result.get("coverage")), ensure_ascii=False) + "\n"
    # 用户自然语言里的具体诉求始终叠加（覆盖/补充模板结构），不再限定 custom
    if spec.analysis_instruction:
        user += f"用户的额外/具体要求（请优先满足）：{spec.analysis_instruction}\n"
    # 内部反馈只指导当前报告修订，不能冒充用户提供的业务历史。
    if feedback:
        user += ("以下是本次生成过程的内部质量反馈，不是用户提供的业务事实或历史报告。"
                 "逐条修正后只输出当前完整报告，不叙述内部质检、重写或更正过程。"
                 "用户明确要求报告对比时，仍按用户要求和已提供的报告依据完成对比。\n") + \
                "\n".join(f"- {p}" for p in feedback) + "\n"
    try:
        blob = _build_blob(data, complete=bool(context_capacity or collection_data or project_result))
    except ValueError as error:
        return {"analysis": None, "error": str(error), **consumed}
    if project_result and not context_capacity and len(blob) > settings.analyze_max_blob_chars:
        return {"analysis": None, "error": "完整项目记录超过分析上下文限额，已停止分析；项目表与原件仍保留", **consumed}
    if collection_data and not context_capacity and (len(blob) > settings.analyze_max_blob_chars
            or any(len(item.get("content") or "") > settings.clean_max_item_chars for item in data)):
        return {**collection_data, "analysis": None, "error": "完整原件超过所选模型的保守上下文限额，已停止分析；资料仍保留", **consumed}
    description = "完整项目字段与引用，原始来源另行保存" if project_result else "完整原件" if collection_data else "完整分析资料" if context_capacity else "可能已截断"
    user += f"\n以下是采集到的数据（共 {len(data)} 条，{description}）：\n\n{blob}"
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        text = await achat(
            messages,
            provider=state.get("provider"),
            model=state.get("model"),
        )
    except ModelOutputTruncatedError:
        # 分析未完成时不能生成看似成功的报告。
        if project_result:
            return {"analysis": None, "error": "项目简报未完整返回，已停止且未重发；项目表和来源仍保留", **consumed}
        raise
    except Exception as e:
        if project_result:
            return {"analysis": None, "error": "项目简报生成请求结果未知，已停止且未重发；项目表和来源仍保留", **consumed}
        logger.exception("分析 LLM 调用失败")
        return {"analysis": None, "error": f"分析失败：{e}", **consumed}

    if project_result and (issue := _project_brief_issue(text)):
        # 只有已完整返回但格式不合格才允许一次新改写，绝不重发未知请求。
        rewrite_messages = [*messages, {"role": "user", "content": (
            f"上一份完整返回的简报未通过检查：{issue}。请仅依据上面的原始项目字段重新写一份简报。"
            "保留实际项目数、缺口、未知字段和关键结论；输出500至800字左右、总长不超过1000字符的正文，"
            "不要JSON、代码块、明细表或全文附录。表格和原始数据已由系统另行保存，不需要在正文重复。"
        )}]
        try:
            text = await achat(rewrite_messages, provider=state.get("provider"), model=state.get("model"))
        except ModelOutputTruncatedError:
            return {"analysis": None, "error": "项目简报改写未完整返回，已停止且未重发；项目表和来源仍保留", **consumed}
        except Exception:
            return {"analysis": None, "error": "项目简报改写请求结果未知，已停止且未重发；项目表和来源仍保留", **consumed}
        if _project_brief_issue(text):
            return {"analysis": None, "error": "项目简报改写后仍不符合一页正文要求，已停止；项目表和来源仍保留", **consumed}

    out: Dict[str, Any] = {
        **collection_data,
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
