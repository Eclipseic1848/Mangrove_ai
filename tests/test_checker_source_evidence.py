"""质量门核对原件和来源覆盖，不能只评价报告文笔。"""
import asyncio
import json

import pytest

from src.config.settings import settings
from src.conductor.nodes import checker
from src.conductor.task_spec import TaskSpec


@pytest.mark.parametrize("mode", ["normal", "no_sources", "missing_checks", "unusable", "truncated", "bad_json", "unavailable",
                                 "duplicate", "bool_id", "string_usable", "empty_reason", "wrong_id"])
def test_checker_requires_complete_source_review(monkeypatch, mode):
    captured = []

    async def judge(messages, **kwargs):
        captured.extend(messages)
        if mode == "unavailable":
            raise RuntimeError("模型不可用")
        if mode == "bad_json":
            return "无法判断"
        verdict = {"score": 95, "passed": True, "issues": [], "summary": "看起来很好"}
        if mode != "missing_checks":
            verdict["source_checks"] = [{"source_id": 1, "usable": mode != "unusable", "reason": "核对正文和日期"}]
        if mode == "duplicate":
            verdict["source_checks"] *= 2
        if mode in ("bool_id", "wrong_id"):
            verdict["source_checks"][0]["source_id"] = True if mode == "bool_id" else 2
        if mode == "string_usable":
            verdict["source_checks"][0]["usable"] = "true"
        if mode == "empty_reason":
            verdict["source_checks"][0]["reason"] = ""
        return json.dumps(verdict, ensure_ascii=False)

    monkeypatch.setattr(checker, "achat", judge)
    monkeypatch.setattr(settings, "checker_enabled", True)
    monkeypatch.setattr(settings, "checker_rerun_enabled", False)
    monkeypatch.setattr(settings, "template_learning_enabled", False)
    monkeypatch.setattr(settings, "lesson_learning_enabled", False)
    monkeypatch.setattr(settings, "analyze_max_blob_chars", 80 if mode == "truncated" else 10000)
    state = {"task_spec": TaskSpec(intent="总结今日新闻", time_range="今天", max_items=5),
             "analysis": "新闻内容及结论", "cleaned_dataset": [] if mode == "no_sources" else [
                 {"title": "原始新闻标题", "content": "唯一原件证据：销售额15万元。",
                  "url": "https://evidence.example/news", "published_at": "2026-09-25"}]}
    result = asyncio.run(checker.checker_node(state))
    assert result["quality"]["passed"] is (mode == "normal")
    if mode == "normal":
        prompt = captured[-1]["content"]
        for value in ("唯一原件证据", "https://evidence.example/news", "2026-09-25", "今天"):
            assert value in prompt
        assert result["quality"]["source_checks"][0]["usable"] is True


def test_empty_cleaned_sources_cannot_skip_quality_gate(monkeypatch):
    monkeypatch.setattr(settings, "checker_enabled", True)
    result = asyncio.run(checker.checker_node({"task_spec": TaskSpec(intent="提取网页"),
                                              "cleaned_dataset": [], "analysis": None}))
    assert result["quality"]["passed"] is False


def test_model_cannot_accept_source_outside_frozen_domain(monkeypatch):
    captured = []
    async def judge(messages, **kwargs):
        captured.extend(messages)
        return json.dumps({"score": 99, "passed": True, "issues": [], "summary": "通过",
                           "source_checks": [{"source_id": 1, "usable": True, "reason": "声称可用"}]})

    monkeypatch.setattr(checker, "achat", judge)
    monkeypatch.setattr(settings, "checker_enabled", True)
    monkeypatch.setattr(settings, "checker_rerun_enabled", False)
    monkeypatch.setattr(settings, "template_learning_enabled", False)
    monkeypatch.setattr(settings, "lesson_learning_enabled", False)
    result = asyncio.run(checker.checker_node({"task_spec": TaskSpec(intent="汇总新闻", site_domains=["allowed.example"]),
        "analysis": "新闻总结", "cleaned_dataset": [{"url": "https://outside.example/news", "content": "新闻正文"}]}))
    assert result["quality"]["passed"] is False
    assert "allowed.example" in captured[-1]["content"]


@pytest.mark.parametrize("mode", ["report_issue", "unusable", "missing_checks", "evidence_limit",
                                 "report_limit", "already_rewritten"])
def test_rewrite_only_when_available_evidence_can_support_report_repair(monkeypatch, mode):
    async def judge(*args, **kwargs):
        verdict = {"score": 40, "passed": False, "issues": ["报告字段需修正"], "summary": "未通过"}
        if mode != "missing_checks":
            verdict["source_checks"] = [{"source_id": 1, "usable": mode != "unusable", "reason": "已核对原件"}]
        return json.dumps(verdict, ensure_ascii=False)

    monkeypatch.setattr(checker, "achat", judge)
    monkeypatch.setattr(settings, "checker_enabled", True)
    monkeypatch.setattr(settings, "checker_rerun_enabled", True)
    monkeypatch.setattr(settings, "template_learning_enabled", False)
    monkeypatch.setattr(settings, "lesson_learning_enabled", False)
    monkeypatch.setattr(settings, "analyze_max_blob_chars", 20 if mode == "evidence_limit" else 10000)
    monkeypatch.setattr(settings, "checker_max_report_chars", 3 if mode == "report_limit" else 6000)
    state = {"task_spec": TaskSpec(intent="整理公告"), "analysis": "有原件支撑，但报告字段需修正。",
             "cleaned_dataset": [{"url": "https://evidence.example/notice", "content": "完整公告原件。"}],
             "analyze_reruns": int(mode == "already_rewritten")}
    result = asyncio.run(checker.checker_node(state))
    assert result["quality"]["passed"] is False
    assert bool(result.get("checker_feedback")) is (mode in ("report_issue", "report_limit"))
