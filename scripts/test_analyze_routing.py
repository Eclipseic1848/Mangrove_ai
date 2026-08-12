#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分析模板选择路由单元测试（_select_system + _has_voc_signal）。

重点：强领域(招投标/新闻/商品)不被误判的 analysis_type=voc 带偏；真口碑仍走 VOC；
三重验证（analysis_type + data_type + VOC意图信号）后，弱模型误判不会带偏流水线。
运行：python scripts/test_analyze_routing.py
"""
import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.conductor.nodes.analyze import _select_system, _has_voc_signal
from src.conductor.prompts import (
    ANALYZE_ARTICLE_SYSTEM,
    ANALYZE_BID_SYSTEM,
    ANALYZE_PRODUCT_SYSTEM,
    ANALYZE_SUMMARY_SYSTEM,
    ANALYZE_VOC_SYSTEM,
)
from src.conductor.task_spec import AnalysisType, DataType, TaskSpec


def _spec(data_type, analysis_type, intent="x", keywords=None):
    """构造测试 TaskSpec；需 VOC 信号时传 intent 含信号词（如"口碑槽点"）。"""
    return TaskSpec(intent=intent, keywords=keywords or [], data_type=data_type, analysis_type=analysis_type)


def test_bid_not_overridden_by_voc():
    """招投标即便被误判 analysis_type=voc，也用扫标模板。"""
    sys_p, src, _ = _select_system(_spec(DataType.BID, AnalysisType.VOC))
    assert sys_p == ANALYZE_BID_SYSTEM and src == "builtin", src


def test_article_product_strong_domain():
    """新闻/商品即便被误判 analysis_type=voc，也用各自专属模板。"""
    assert _select_system(_spec(DataType.ARTICLE, AnalysisType.VOC))[0] == ANALYZE_ARTICLE_SYSTEM
    assert _select_system(_spec(DataType.PRODUCT, AnalysisType.VOC))[0] == ANALYZE_PRODUCT_SYSTEM


def test_real_voc_still_works():
    """真口碑任务（评论+voc+意图有口碑/槽点信号）仍走 VOC（三重验证全过）。"""
    sys_p, src, _ = _select_system(
        _spec(DataType.COMMENT, AnalysisType.VOC, intent="分析用户口碑和槽点")
    )
    assert sys_p == ANALYZE_VOC_SYSTEM and src == "voc", src
    # 帖子 + 口碑意图
    assert _select_system(
        _spec(DataType.POST, AnalysisType.VOC, intent="看看大家对XX的吐槽和评价")
    )[0] == ANALYZE_VOC_SYSTEM


def test_comment_without_voc_signal_goes_summary():
    """评论数据 但 意图无 VOC 信号（弱模型误判）→ 不能走 VOC，应走自学习/兜底。
    这是本次修复的核心回归测试：赛事分析被 Planner 误判为 voc+comment 时不被 VOC 截胡。"""
    sys_p, src, _ = _select_system(
        _spec(DataType.COMMENT, AnalysisType.VOC, intent="分析世界杯比赛结果及相关解析评论")
    )
    assert sys_p != ANALYZE_VOC_SYSTEM, "无VOC信号的评论任务不应被VOC截胡"
    assert src in ("learned", "fallback"), f"应走自学习路径，实际 src={src}"


def test_comment_summary_shows_summary_not_voc():
    """评论 + summary → 非 VOC 摘要路径（不再强行走 VOC 内置模板）。"""
    sys_p, _, _ = _select_system(_spec(DataType.COMMENT, AnalysisType.SUMMARY))
    # 无 VOC 信号时，comment 不应自动走 VOC
    assert sys_p != ANALYZE_VOC_SYSTEM, f"COMMENT+SUMMARY 无VOC信号不应走VOC，实际: {sys_p[:50]}"


def test_unknown_type_with_misjudged_voc_goes_learning():
    """核心：未知/通用类型即便被误判 analysis_type=voc，也不能用 VOC 兜底，
    必须走自学习路径（learned 召回 / fallback 通用兜底→沉淀新模板）。"""
    sys_p, src, _ = _select_system(_spec(DataType.GENERIC, AnalysisType.VOC))
    assert sys_p != ANALYZE_VOC_SYSTEM, "未知类型不应被 VOC 截胡"
    assert src in ("learned", "fallback"), src


def test_summary_fallback():
    """没有可用自学习模板时，通用 + summary → 通用摘要兜底。"""
    with patch("src.conductor.nodes.analyze.match_template", return_value=None):
        sys_p, src, _ = _select_system(_spec(DataType.GENERIC, AnalysisType.SUMMARY))
    assert sys_p == ANALYZE_SUMMARY_SYSTEM and src == "fallback", src


def test_bid_summary_still_bid():
    """招投标 + summary 也用扫标模板。"""
    assert _select_system(_spec(DataType.BID, AnalysisType.SUMMARY))[0] == ANALYZE_BID_SYSTEM


def test_has_voc_signal():
    """_has_voc_signal 确定性规则单元测试。"""
    # 有信号
    assert _has_voc_signal(TaskSpec(intent="分析用户口碑", data_type=DataType.COMMENT))
    assert _has_voc_signal(TaskSpec(intent="看看吐槽", keywords=["差评"], data_type=DataType.POST))
    # 有信号但有反信号 → False
    assert not _has_voc_signal(TaskSpec(
        intent="分析比赛的口碑评价", keywords=["比赛"], data_type=DataType.COMMENT
    ))
    # 无信号
    assert not _has_voc_signal(TaskSpec(intent="查天气", data_type=DataType.GENERIC))
    # 有信号无反信号
    assert _has_voc_signal(TaskSpec(intent="用户对XX的投诉和差评", data_type=DataType.COMMENT))


def test_llm_route_voc_covers_wordlist_gap():
    """双通道（P0-4）：词表未命中但 LLM 分类判 voc → 走 VOC（覆盖词表盲区）。"""
    spec = _spec(DataType.COMMENT, AnalysisType.VOC, intent="看看车主们对这款车的真实看法")  # 无词表信号词
    # 纯词表通道：放行到自学习
    assert _select_system(spec)[0] != ANALYZE_VOC_SYSTEM
    # LLM 通道补上
    sys_p, src, _ = _select_system(spec, llm_route="voc")
    assert sys_p == ANALYZE_VOC_SYSTEM and src == "voc", src


def test_llm_route_fixes_generic_datatype():
    """双通道（P0-4）：data_type 误判为 generic 时，LLM 分类判强领域 → 用对应领域模板。"""
    sys_p, src, _ = _select_system(_spec(DataType.GENERIC, AnalysisType.SUMMARY), llm_route="bid")
    assert sys_p == ANALYZE_BID_SYSTEM and src == "builtin", src
    sys_p, _, _ = _select_system(_spec(DataType.GENERIC, AnalysisType.SUMMARY), llm_route="article")
    assert sys_p == ANALYZE_ARTICLE_SYSTEM


def test_llm_route_does_not_hijack_comment_data():
    """双通道（P0-4）：评论/帖子数据不被 LLM 的强领域分类劫持（保留自学习空间）。"""
    sys_p, src, _ = _select_system(_spec(DataType.COMMENT, AnalysisType.SUMMARY), llm_route="article")
    assert sys_p != ANALYZE_ARTICLE_SYSTEM, "评论数据不应被 article 路由劫持"
    assert src in ("learned", "fallback"), src


def test_llm_route_none_keeps_behavior():
    """双通道（P0-4）：llm_route=None（LLM 失败/未启用）行为与纯词表通道完全一致。"""
    for dt, at in [(DataType.BID, AnalysisType.VOC), (DataType.GENERIC, AnalysisType.SUMMARY),
                   (DataType.COMMENT, AnalysisType.VOC)]:
        assert _select_system(_spec(dt, at)) == _select_system(_spec(dt, at), llm_route=None)


def main():
    tests = [
        test_bid_not_overridden_by_voc,
        test_article_product_strong_domain,
        test_real_voc_still_works,
        test_comment_without_voc_signal_goes_summary,
        test_comment_summary_shows_summary_not_voc,
        test_unknown_type_with_misjudged_voc_goes_learning,
        test_summary_fallback,
        test_bid_summary_still_bid,
        test_has_voc_signal,
        test_llm_route_voc_covers_wordlist_gap,
        test_llm_route_fixes_generic_datatype,
        test_llm_route_does_not_hijack_comment_data,
        test_llm_route_none_keeps_behavior,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print("=" * 50)
    print(f"{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
