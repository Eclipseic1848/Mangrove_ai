#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Checker 质检闭环单测（P1-2：不达标带问题清单自动重跑分析 1 次）。

覆盖：checker 不达标下发反馈 / 已重跑过不再下发 / analyze 消费反馈注入 prompt 并清空标记 /
_route_after_checker 三态路由 / 反馈为空不重跑。
运行：python scripts/test_checker_rerun.py
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import settings
from src.conductor.graph import _route_after_checker
from src.conductor.nodes.analyze import analyze_node
from src.conductor.nodes.checker import _looks_like_collection_failure, checker_node
from src.conductor.task_spec import AnalysisType, TaskSpec


def _spec() -> TaskSpec:
    return TaskSpec(intent="分析产品口碑", keywords=["k"], analysis_type=AnalysisType.SUMMARY)


def _run(coro):
    return asyncio.run(coro)


def _checker_state(**extra):
    st = {"task_spec": _spec(), "analysis": "报告正文" * 50}
    st.update(extra)
    return st


def _fake_achat(payload: dict):
    async def fake(messages, **kwargs):
        return json.dumps(payload, ensure_ascii=False)
    return fake


def test_checker_dispatches_rerun():
    """不达标且未重跑过：下发问题清单 + analyze_reruns=1，且本轮不做模板沉淀。"""
    payload = {"score": 40, "issues": ["数据引用不足", "结构缺失"], "summary": "质量差"}
    with patch("src.conductor.nodes.checker.achat", new=_fake_achat(payload)):
        out = _run(checker_node(_checker_state()))
    assert out["quality"]["passed"] is False
    assert out.get("analyze_reruns") == 1, "应标记已重跑 1 次"
    assert out.get("checker_feedback") == ["数据引用不足", "结构缺失"], f"实际：{out.get('checker_feedback')}"
    assert "template_saved" not in out, "触发重跑的轮次不应做模板沉淀"


def test_checker_no_second_rerun():
    """已重跑过（analyze_reruns=1）：即使再不达标也不再下发反馈。"""
    payload = {"score": 40, "issues": ["仍有问题"], "summary": ""}
    with patch("src.conductor.nodes.checker.achat", new=_fake_achat(payload)):
        out = _run(checker_node(_checker_state(analyze_reruns=1)))
    assert out["quality"]["passed"] is False
    assert "checker_feedback" not in out, "重跑过一次后不应再下发反馈（防死循环）"


def test_checker_pass_no_rerun():
    """达标：不下发反馈。"""
    payload = {"score": 90, "issues": [], "summary": "很好"}
    with patch("src.conductor.nodes.checker.achat", new=_fake_achat(payload)):
        out = _run(checker_node(_checker_state()))
    assert out["quality"]["passed"] is True
    assert "checker_feedback" not in out


def test_checker_no_feedback_no_rerun():
    """不达标但 issues 和 summary 都为空：无可操作反馈，不重跑。"""
    payload = {"score": 40, "issues": [], "summary": ""}
    with patch("src.conductor.nodes.checker.achat", new=_fake_achat(payload)):
        out = _run(checker_node(_checker_state()))
    assert out["quality"]["passed"] is False
    assert "checker_feedback" not in out, "无问题清单时重跑无意义"


def test_analyze_consumes_feedback():
    """analyze 消费反馈：问题清单注入 prompt，输出清空 checker_feedback。"""
    captured = {}

    async def fake_achat(messages, **kwargs):
        captured["user"] = messages[-1]["content"]
        return "重写后的报告"

    async def fake_route(*a, **k):
        return None

    state = {
        "task_spec": _spec(),
        "cleaned_dataset": [{"title": "t", "content": "正文" * 30, "url": "https://x.com/1"}],
        "checker_feedback": ["数据引用不足", "结构缺失"],
    }
    with patch("src.conductor.nodes.analyze.achat", new=fake_achat), \
         patch("src.conductor.nodes.analyze._classify_route_llm", new=fake_route):
        out = _run(analyze_node(state))
    assert out["analysis"] == "重写后的报告"
    assert out.get("checker_feedback") is None, "消费后必须清空标记（防死循环）"
    assert "数据引用不足" in captured["user"], "问题清单应注入 prompt"
    assert "逐条修正" in captured["user"]


def test_route_after_checker():
    """路由三态：有反馈且未过→analyze；已过→output；无反馈→output。"""
    assert _route_after_checker(
        {"quality": {"passed": False}, "checker_feedback": ["p"]}) == "analyze"
    assert _route_after_checker(
        {"quality": {"passed": True}, "checker_feedback": None}) == "output"
    # 第二轮：analyze 已清空反馈，即使仍未过也走 output（防死循环的最终保障）
    assert _route_after_checker(
        {"quality": {"passed": False}, "checker_feedback": None}) == "output"


def test_looks_like_collection_failure_by_low_data_count():
    """数据条数低于阈值 → 判定为采集失败，即使报告文字正常。"""
    old = settings.template_min_data_count
    settings.template_min_data_count = 3
    try:
        assert _looks_like_collection_failure([{"title": "a"}], "报告结构完整，内容详实。") is True
    finally:
        settings.template_min_data_count = old


def test_looks_like_collection_failure_by_narrative_keyword():
    """数据条数达标，但报告正文命中失败叙事关键词 → 判定为采集失败。"""
    old = settings.template_min_data_count
    settings.template_min_data_count = 3
    try:
        dataset = [{"title": "a"}, {"title": "b"}, {"title": "c"}]
        assert _looks_like_collection_failure(dataset, "本次未采集到有效数据，无法展开分析。") is True
    finally:
        settings.template_min_data_count = old


def test_looks_like_collection_failure_false_when_healthy():
    """数据量充足且报告无失败叙事 → 不判定为失败。"""
    old = settings.template_min_data_count
    settings.template_min_data_count = 3
    try:
        dataset = [{"title": "a"}, {"title": "b"}, {"title": "c"}, {"title": "d"}]
        assert _looks_like_collection_failure(dataset, "本次分析共覆盖4条评论，用户反馈集中在续航方面。") is False
    finally:
        settings.template_min_data_count = old


def test_checker_skips_distillation_when_data_count_low():
    """走兜底且质检通过，但采集数据条数不足 → 不应调用 distill_template，不产生 template_saved。"""
    payload = {"score": 90, "issues": [], "summary": "很好"}
    old = settings.template_min_data_count
    settings.template_min_data_count = 3
    try:
        with patch("src.conductor.nodes.checker.achat", new=_fake_achat(payload)), \
             patch("src.conductor.nodes.checker.distill_template") as mock_distill:
            out = _run(checker_node(_checker_state(
                analysis_source="fallback", cleaned_dataset=[{"title": "a"}],
            )))
        mock_distill.assert_not_called()
        assert "template_saved" not in out
    finally:
        settings.template_min_data_count = old


def test_checker_still_distills_healthy_fallback():
    """走兜底且质检通过，数据量充足、报告无失败叙事 → 仍应正常沉淀（回归锁定，不被新判定误拦）。"""
    payload = {"score": 90, "issues": [], "summary": "很好"}
    dataset = [{"title": "a"}, {"title": "b"}, {"title": "c"}]

    async def fake_distill(*a, **k):
        return {"title": "测试模板", "keywords": ["k"], "body": "结构正文"}

    old = settings.template_min_data_count
    settings.template_min_data_count = 3
    try:
        mock_save = AsyncMock(return_value="test-slug")
        with patch("src.conductor.nodes.checker.achat", new=_fake_achat(payload)), \
             patch("src.conductor.nodes.checker.distill_template", new=fake_distill), \
             patch("src.conductor.nodes.checker.save_template", new=mock_save):
            out = _run(checker_node(_checker_state(
                analysis_source="fallback", cleaned_dataset=dataset,
            )))
        mock_save.assert_called_once()
        assert out.get("template_saved") == {"slug": "test-slug", "title": "测试模板"}
    finally:
        settings.template_min_data_count = old


def test_checker_no_template_saved_when_curator_discards():
    """Curator 判定丢弃（save_template 返回 None）→ 不产生 template_saved。"""
    payload = {"score": 90, "issues": [], "summary": "很好"}
    dataset = [{"title": "a"}, {"title": "b"}, {"title": "c"}]

    async def fake_distill(*a, **k):
        return {"title": "测试模板", "keywords": ["k"], "body": "结构正文"}

    async def fake_save(*a, **k):
        return None

    old = settings.template_min_data_count
    settings.template_min_data_count = 3
    try:
        with patch("src.conductor.nodes.checker.achat", new=_fake_achat(payload)), \
             patch("src.conductor.nodes.checker.distill_template", new=fake_distill), \
             patch("src.conductor.nodes.checker.save_template", new=fake_save):
            out = _run(checker_node(_checker_state(
                analysis_source="fallback", cleaned_dataset=dataset,
            )))
        assert "template_saved" not in out
    finally:
        settings.template_min_data_count = old


def test_checker_records_lesson_on_collection_failure():
    """判定为采集失败（数据条数低于阈值）→ 应调用 record_failure，不受 passed/analysis_source 影响。"""
    payload = {"score": 90, "issues": [], "summary": "很好"}
    old = settings.template_min_data_count
    settings.template_min_data_count = 3
    mock_record = AsyncMock()
    try:
        with patch("src.conductor.nodes.checker.achat", new=_fake_achat(payload)), \
             patch("src.conductor.nodes.checker.record_failure", new=mock_record):
            _run(checker_node(_checker_state(cleaned_dataset=[{"title": "a"}])))
        mock_record.assert_called_once()
        args, kwargs = mock_record.call_args
        assert args[0] == "分析产品口碑"
        assert args[1] == "generic"
        assert args[2] == ["k"]
        assert kwargs["failure_signal"] == "报告正文" * 50
    finally:
        settings.template_min_data_count = old


def test_checker_no_lesson_when_healthy():
    """数据充足、无失败叙事 → 不应调用 record_failure。"""
    payload = {"score": 90, "issues": [], "summary": "很好"}
    dataset = [{"title": "a"}, {"title": "b"}, {"title": "c"}]
    old = settings.template_min_data_count
    settings.template_min_data_count = 3
    mock_record = AsyncMock()
    try:
        with patch("src.conductor.nodes.checker.achat", new=_fake_achat(payload)), \
             patch("src.conductor.nodes.checker.record_failure", new=mock_record):
            _run(checker_node(_checker_state(cleaned_dataset=dataset)))
        mock_record.assert_not_called()
    finally:
        settings.template_min_data_count = old


def test_checker_lesson_disabled_by_setting():
    """lesson_learning_enabled=False → 即使判定失败也不调用 record_failure。"""
    payload = {"score": 90, "issues": [], "summary": "很好"}
    old_count = settings.template_min_data_count
    old_enabled = settings.lesson_learning_enabled
    settings.template_min_data_count = 3
    settings.lesson_learning_enabled = False
    mock_record = AsyncMock()
    try:
        with patch("src.conductor.nodes.checker.achat", new=_fake_achat(payload)), \
             patch("src.conductor.nodes.checker.record_failure", new=mock_record):
            _run(checker_node(_checker_state(cleaned_dataset=[{"title": "a"}])))
        mock_record.assert_not_called()
    finally:
        settings.template_min_data_count = old_count
        settings.lesson_learning_enabled = old_enabled


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"[PASS] {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)
