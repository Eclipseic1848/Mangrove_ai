# -*- coding: utf-8 -*-
"""节点白盒视图：把每个节点的产出（delta）转成人类可读的自然语言摘要。

delta 是该节点本次返回的状态增量（首选数据源）；values 是累计状态（用于跨节点计数兜底）。
返回值是自然语言字符串，前端以打字机效果逐字展示。
"""
from __future__ import annotations

from typing import Any, Dict

# 采集器名 → 中文标签
_COLLECTOR_CN = {
    "mediacrawler": "社媒采集", "ecommerce": "电商评论", "rss": "RSS订阅",
    "article": "文章提取", "site_crawler": "整站爬取", "firecrawl": "全网发现",
    "search": "站定向检索", "crawl4ai": "通用引擎", "scrapling": "反爬自愈",
    "simple_http": "轻量抓取", "browser": "浏览器兜底", "direct_video": "直接视频",
}

_ANALYSIS_SOURCE_CN = {
    "voc": "VOC口碑分析", "builtin": "内置通用模板", "learned": "自学习模板",
    "fallback": "通用兜底模板", "video": "视频证据分析", "video_evidence_blocked": "视频证据不足",
}

_OUTPUT_CN = {
    "report_md": "Markdown报告", "json": "JSON数据", "evidence_json": "视频证据", "trace_file": "执行轨迹",
}

_DATA_TYPE_CN: Dict[str, str] = {}  # 懒加载，避免循环导入


def _dt_cn(val: Any) -> str:
    """data_type 枚举值 → 中文（懒加载 TaskSpec 枚举）。"""
    if val is None:
        return ""
    raw = val.value if hasattr(val, "value") else str(val)
    if not _DATA_TYPE_CN:
        from src.conductor.task_spec import DataType  # noqa: F811
        _DATA_TYPE_CN.update({
            "article": "文章/新闻", "comment": "评论/口碑", "post": "帖子/社媒",
            "product": "商品信息", "bid": "招投标", "generic": "通用网页",
            "news": "新闻资讯",
        })
    return _DATA_TYPE_CN.get(raw, raw)


def build_node_view(node: str, delta: Dict[str, Any], values: Dict[str, Any]) -> str:
    """把节点产出转为人类可读的自然语言摘要，供前端打字机展示。"""
    delta = delta or {}
    values = values or {}

    if node == "intent":
        if delta.get("needs_clarification"):
            q = delta.get("clarification_question") or "请补充更多细节"
            return f"🤔 需要您补充信息：{q}"
        u = delta.get("understanding") or {}
        if not u:
            return "已理解您的需求，准备开始处理。"
        parts: list[str] = []
        intent = u.get("intent", "") or ""
        platforms = u.get("platforms", []) or []
        dtype = u.get("data_type", "") or ""
        if intent:
            parts.append(f"意图：{intent}")
        if platforms:
            parts.append(f"目标平台：{'、'.join(str(p) for p in platforms)}")
        if dtype:
            parts.append(f"数据类型：{_dt_cn(dtype)}")
        return "已理解您的需求。" + ("；".join(parts)) if parts else "已理解您的需求。"

    if node == "planner":
        spec = delta.get("task_spec") or values.get("task_spec")
        if spec is None:
            return "正在规划任务…"
        parts: list[str] = []
        if getattr(spec, "platforms", None):
            parts.append(f"在{'、'.join(str(p) for p in spec.platforms)}平台搜索")
        if getattr(spec, "keywords", None):
            parts.append(f"关键词「{'、'.join(str(k) for k in spec.keywords)}」")
        if getattr(spec, "data_type", None):
            parts.append(f"采集{_dt_cn(spec.data_type)}类型数据")
        if getattr(spec, "max_items", None) and spec.max_items > 0:
            parts.append(f"最多{spec.max_items}条")
        if getattr(spec, "analysis_type", None):
            type_cn = _ANALYSIS_SOURCE_CN.get(
                spec.analysis_type.value if hasattr(spec.analysis_type, "value") else str(spec.analysis_type),
                str(spec.analysis_type))
            parts.append(f"进行{type_cn}")
        if getattr(spec, "outputs", None):
            out_names = []
            for o in spec.outputs:
                ov = o.value if hasattr(o, "value") else str(o)
                out_names.append(_OUTPUT_CN.get(ov, ov))
            if out_names:
                parts.append(f"产出{'、'.join(out_names)}")
        text = f"📋 任务规划：{'，'.join(parts)}"
        reasoning = delta.get("plan_reasoning", "")
        if reasoning:
            text += f"\n💡 规划思路：{reasoning}"
        return text

    if node == "target_resolve":
        targets = delta.get("target_manifest") or values.get("target_manifest") or []
        if not targets:
            return "未发现显式链接目标，按普通采集流程执行。"
        return "已识别显式目标：" + "、".join(str(target.get("platform") or "网页") for target in targets)

    if node == "video_enrich":
        coverage = delta.get("target_coverage") or values.get("target_coverage") or {}
        return f"🎬 视频证据提取：{coverage.get('evidence_ready', 0)}/{coverage.get('total', 0)} 个目标证据就绪"

    if node == "router":
        cands = delta.get("collector_candidates") or []
        if not cands:
            return "未找到合适的采集引擎，将使用默认引擎尝试采集。"
        named = [f"{_COLLECTOR_CN.get(c, c)}（{c}）" for c in cands]
        arrow = " → " if len(named) > 1 else ""
        return f"🔍 已选择采集引擎：{arrow.join(named)}（按优先级依次尝试，失败自动降级到下一个）"

    if node == "collect":
        collector = delta.get("collector_used") or values.get("collector_used") or ""
        raw = delta.get("raw_dataset") or values.get("raw_dataset") or []
        notes = delta.get("collector_notes") or values.get("collector_notes") or []
        if not collector or not raw:
            text = "所有采集器均未获得数据，任务终止。"
            if notes:
                text += "\n" + "\n".join(str(n) for n in notes)
            return text
        # 补采时 collector_used 为 "a+b" 多引擎串，逐个映射中文名
        parts = [c for c in collector.split("+") if c]
        cname = "+".join(_COLLECTOR_CN.get(c, c) for c in parts)
        text = f"📥 使用{cname}（{collector}）成功采集到 {len(raw)} 条数据"
        if notes:
            text += "\n" + "\n".join(str(n) for n in notes)
        return text

    if node == "clean":
        raw = values.get("raw_dataset") or []
        cleaned = delta.get("cleaned_dataset") or []
        removed = len(raw) - len(cleaned)
        if removed > 0:
            # 质量门剔除原因统计（P0-2）：让"为什么少了 N 条"可解释
            stats = delta.get("clean_stats") or values.get("clean_stats") or {}
            reason = "、".join(f"{k} {v} 条" for k, v in stats.items()) if stats else "重复、噪声或无效内容"
            return f"🧹 数据清洗完成：{len(raw)} 条原始数据 → {len(cleaned)} 条有效数据（剔除：{reason}）"
        if len(cleaned) > 0:
            return f"🧹 数据清洗完成：{len(cleaned)} 条数据均有效，无需过滤"
        return "🧹 数据清洗完成，无有效数据留存。"

    if node == "analyze":
        source = delta.get("analysis_source") or values.get("analysis_source") or ""
        source_cn = _ANALYSIS_SOURCE_CN.get(source, source or "通用模板")
        analysis = delta.get("analysis") or values.get("analysis") or ""
        text = f"📊 使用「{source_cn}」进行分析"
        if analysis:
            text += f"（生成约 {len(analysis)} 字分析报告）"
        # 双通道路由依据（P0-4）：让"为什么用这个模板"可解释
        route = delta.get("analysis_route") or values.get("analysis_route") or ""
        if route:
            text += f"\n🧭 模板路由依据：{route}"
        return text

    if node == "checker":
        q = delta.get("quality") or values.get("quality") or {}
        if not q:
            return "质量评估已跳过"
        score = q.get("score", 0)
        passed = q.get("passed", False)
        issues = q.get("issues", []) or []
        summary = q.get("summary", "") or ""
        status = "✓ 通过" if passed else "✗ 未通过"
        text = f"✅ 质量评估：{score} 分 {status}"
        if issues:
            issue_strs = [str(i) for i in issues[:3]]
            text += f"（检查了 {len(issues)} 项：{'、'.join(issue_strs)}"
            if len(issues) > 3:
                text += f" 等"
            text += "）"
        if summary:
            text += f"\n📝 评估摘要：{summary}"
        if delta.get("checker_feedback"):
            text += "\n🔁 质检未达标，已带问题清单自动重跑分析（限 1 次）"
        return text

    if node == "output":
        outs = delta.get("outputs") or values.get("outputs") or {}
        if not outs:
            return "📦 生成产出：仅文本回复"
        named = [_OUTPUT_CN.get(k, k) for k in outs.keys() if k in _OUTPUT_CN]
        if named:
            return f"📦 已生成产出：{'、'.join(named)}"
        return "📦 产出已生成"

    if node == "schedule":
        sr = delta.get("schedule_request") or values.get("schedule_request") or ""
        return f"⏰ 已识别为定时任务，执行计划：{sr}"

    return "处理完成"
