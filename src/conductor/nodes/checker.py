"""
Checker 节点（Phase 3，maker≠checker）。

以独立视角对分析报告打质量分（忠于原文/结构完整/满足诉求/可用性），产出 quality={score,passed,issues,summary}。
不达标时带问题清单自动重跑分析 1 次（P1-2 闭环，CHECKER_RERUN_ENABLED 可关；限 1 次防死循环），
重跑后仍不达标则仅标记提示（由前端/用户决定）。

自学习阶段2 衔接：当报告"走了通用兜底"（analysis_source=fallback）且本次质量通过时，
自动把报告结构蒸馏沉淀为模板（替代阶段1 的人工确认），下次同类任务自动复用。
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from src.config.settings import settings
from src.llm import achat
from src.memory import distill_template, record_failure, record_lesson_helped, record_template_use, save_template

from ..prompts import CHECKER_SYSTEM
from ..state import ConductorState
from ..targets import is_direct_video_manifest
from ..utils import parse_json_obj

logger = logging.getLogger(__name__)

# 走了兜底且质检通过的报告，若"采集本身就失败了"（叙事完整但没数据），不应沉淀为模板——
# checker 评估的是"报告写得好不好"，不是"任务是否采集成功"，两者需要分开判断。
_FAILURE_NARRATIVE_KEYWORDS = (
    "未采集到", "数据缺失", "采集失败", "无有效数据", "未找到相关内容",
    "样本不足", "数据不足", "未获取到", "采集异常", "数据核查未通过",
)


def _looks_like_collection_failure(dataset: list, analysis: str) -> bool:
    """判断本次任务是否"采集失败/数据不足"，用于阻止把失败报告沉淀为模板。
    两个信号取并集：数据条数过少，或报告正文命中失败叙事关键词。"""
    if len(dataset) < settings.template_min_data_count:
        return True
    return any(kw in (analysis or "") for kw in _FAILURE_NARRATIVE_KEYWORDS)


async def checker_node(state: ConductorState) -> Dict[str, Any]:
    analysis = state.get("analysis")
    # 无分析产出 或 开关关闭 → 跳过评估
    if not analysis or not settings.checker_enabled:
        return {}

    spec = state["task_spec"]
    if is_direct_video_manifest(state.get("target_manifest") or []) and not state.get("evidence_ready"):
        return {
            "quality": {
                "score": 0,
                "passed": False,
                "issues": ["未取得可验证的视频证据，已禁止生成视频内容结论"],
                "summary": "任务已按证据不足处理，未调用报告质量模型。",
            }
        }
    user = (
        f"任务目标：{spec.intent}\n"
        + (f"用户的具体要求：{spec.analysis_instruction}\n" if spec.analysis_instruction else "")
        + f"\n待审查的分析报告：\n{analysis[:settings.checker_max_report_chars]}"
    )
    try:
        raw = await achat(
            [{"role": "system", "content": CHECKER_SYSTEM}, {"role": "user", "content": user}],
            provider=state.get("provider"),
            model=state.get("model"),
        )
    except Exception:
        logger.warning("Checker 评估调用失败，跳过质量评估", exc_info=True)
        return {}

    data = parse_json_obj(raw)
    if not data:
        return {}
    try:
        score = int(data.get("score", 0))
    except Exception:
        score = 0
    issues = data.get("issues") or []
    if isinstance(issues, str):
        issues = [issues]
    # 以阈值为准判定通过（兼容模型给的 passed 字段，但分数阈值更可控）
    passed = score >= settings.checker_pass_threshold
    quality: Dict[str, Any] = {
        "score": score,
        "passed": passed,
        "issues": [str(i).strip() for i in issues if str(i).strip()],
        "summary": str(data.get("summary") or "").strip(),
    }
    out: Dict[str, Any] = {"quality": quality}

    # 质检闭环（P1-2）：不达标且尚未重跑过 → 带问题清单打回 analyze 重做 1 次（限 1 次防死循环）。
    # 本轮先不做模板统计/沉淀（留给重跑后的终轮做，避免一次任务重复计数）。
    if not passed and settings.checker_rerun_enabled and not state.get("analyze_reruns"):
        feedback = quality["issues"] or ([quality["summary"]] if quality["summary"] else [])
        if feedback:
            logger.info("质检未达标（%s 分），带 %d 条问题重跑分析", score, len(feedback))
            out["analyze_reruns"] = 1
            out["checker_feedback"] = feedback
            return out

    # 自学习增强：本次命中了已学模板 → 回写使用统计（uses+1、更新质量均分），据此质量门转正/淘汰
    if state.get("analysis_source") == "learned" and state.get("template_slug"):
        try:
            new_status = record_template_use(state["template_slug"], score)
            if new_status in ("active", "retired"):
                out["template_status"] = {"slug": state["template_slug"], "status": new_status}
        except Exception:
            logger.warning("回写模板使用统计失败（不影响产出）", exc_info=True)

    # 方案 B：本次命中了 active 教训且判定通过 → 标记教训有效（helped_avoid +1）
    if passed and state.get("active_lesson_slug"):
        try:
            record_lesson_helped(state["active_lesson_slug"])
        except Exception:
            logger.warning("记录教训有效标记失败（不影响产出）", exc_info=True)

    # 自学习阶段2：走了兜底 且 质量通过 且非采集失败 → 自动沉淀模板（无需人工确认）
    if (
        passed
        and settings.template_learning_enabled
        and state.get("analysis_source") == "fallback"
        and not _looks_like_collection_failure(state.get("cleaned_dataset") or [], analysis)
    ):
        try:
            tpl = await distill_template(
                spec.intent,
                spec.data_type.value,
                analysis,
                provider=state.get("provider"),
                model=state.get("model"),
            )
            if tpl:
                slug = await save_template(
                    title=tpl["title"],
                    data_type=spec.data_type.value,
                    keywords=tpl["keywords"] or list(spec.keywords or []),
                    body=tpl["body"],
                )
                if slug:
                    out["template_saved"] = {"slug": slug, "title": tpl["title"]}
                    logger.info("Checker 通过，已自动沉淀模板：%s", slug)
                else:
                    logger.info("Curator 判定新内容对现有模板库无增量信息，本次不沉淀")
        except Exception:
            logger.warning("自动沉淀模板失败（不影响产出）", exc_info=True)

    # 自学习B2：本轮判定"采集失败"（叙事完整但没数据）→ 教训分流，不要求 passed/analysis_source=="fallback"
    # （模板沉淀要求 passed+fallback；教训只要"确实采集失败"就有沉淀价值，与报告质量分/分析通道无关）
    if settings.lesson_learning_enabled and _looks_like_collection_failure(
        state.get("cleaned_dataset") or [], analysis
    ):
        try:
            await record_failure(
                spec.intent,
                spec.data_type.value,
                list(spec.keywords or []),
                failure_signal=analysis,
                provider=state.get("provider"),
                model=state.get("model"),
            )
        except Exception:
            logger.warning("教训沉淀失败（不影响产出）", exc_info=True)

    return out
