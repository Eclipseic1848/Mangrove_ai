#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""节点白盒视图单测（v2：自然语言字符串格式）。运行：python scripts/test_node_views.py"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.conductor.node_views import build_node_view
from src.conductor.task_spec import TaskSpec, DataType


def test_intent_view():
    v = build_node_view("intent", {"understanding": {"intent": "分析SU7口碑"}}, {})
    assert isinstance(v, str), f"应返回字符串，实际为 {type(v)}"
    assert "已理解" in v, f"应含'已理解'，实际：{v}"
    assert "分析SU7口碑" in v, f"应含意图内容，实际：{v}"


def test_intent_clarification():
    v = build_node_view("intent", {"needs_clarification": True, "clarification_question": "请问需要采集多少条数据？"}, {})
    assert isinstance(v, str)
    assert "补充信息" in v and "多少条" in v, f"应含澄清提示，实际：{v}"


def test_planner_view():
    spec = TaskSpec(intent="分析SU7口碑", platforms=["抖音"], data_type=DataType.COMMENT, keywords=["SU7"])
    v = build_node_view("planner", {"task_spec": spec, "plan_reasoning": "口碑→VOC"}, {})
    assert isinstance(v, str)
    assert "抖音" in v and "SU7" in v, f"应含平台和关键词，实际：{v}"
    assert "口碑→VOC" in v, f"应含推理，实际：{v}"


def test_router_view():
    v = build_node_view("router", {"collector_candidates": ["mediacrawler", "search"]}, {})
    assert isinstance(v, str)
    assert "mediacrawler" in v and "search" in v, f"应含候选采集器，实际：{v}"


def test_clean_view_counts():
    v = build_node_view("clean", {"cleaned_dataset": [1, 2]}, {"raw_dataset": [1, 2, 3, 4]})
    assert isinstance(v, str)
    assert "4" in v and "2" in v, f"应含清洗前后数量，实际：{v}"
    assert "2 条" in v, f"应提及去除数量，实际：{v}"


def test_collect_view():
    v = build_node_view("collect", {"collector_used": "mediacrawler", "raw_dataset": [{"a": 1}, {"b": 2}]}, {})
    assert isinstance(v, str)
    assert "2 条" in v and "mediacrawler" in v, f"应含采集器和条数，实际：{v}"


def test_checker_view():
    v = build_node_view("checker", {"quality": {"score": 85, "passed": True, "issues": ["数据不完整"], "summary": "整体良好"}}, {})
    assert isinstance(v, str)
    assert "85" in v and "通过" in v, f"应含分数和通过状态，实际：{v}"


def test_unknown_node_empty():
    v = build_node_view("nope", {}, {})
    assert isinstance(v, str), f"未知节点也应返回字符串，实际：{type(v)}"
    assert len(v) > 0, "未知节点不应返回空字符串"


def test_output_view():
    v = build_node_view("output", {"outputs": {"report_md": "/tmp/r.md", "json": "/tmp/d.json"}}, {})
    assert isinstance(v, str)
    assert "报告" in v and "JSON" in v, f"应含产出名，实际：{v}"


def test_analyze_view():
    v = build_node_view("analyze", {"analysis_source": "voc", "analysis": "这是一份详细的口碑分析报告……" * 50}, {})
    assert isinstance(v, str)
    assert "VOC" in v, f"应含模板来源，实际：{v}"


def main():
    tests = [
        test_intent_view, test_intent_clarification,
        test_planner_view, test_router_view,
        test_clean_view_counts, test_collect_view,
        test_checker_view, test_output_view,
        test_analyze_view, test_unknown_node_empty,
    ]
    failed = 0
    for t in tests:
        try:
            t(); print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1; print(f"FAIL  {t.__name__}: {e}")
    print("=" * 50); print(f"{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
