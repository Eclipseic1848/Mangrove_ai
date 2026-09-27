"""规划必须纠正未经用户授权的专用来源，不能要求用户改换来源。"""
import asyncio
import json

import pytest

from src.conductor.nodes import planner


@pytest.mark.parametrize("url", ["https://finance.sina.com.cn/", "https://reports.example.org/releases",
                                "https://reports.example.org/release?id=7&lang=zh",
                                "http://127.0.0.1:8123/news", "https://reports.example.org/新闻",
                                "HTTPS://reports.example.org/releases"])
def test_explicit_url_replans_incompatible_source(monkeypatch, url):
    drafts = iter([
        {"intent": "网页摘要", "platforms": ["小红书"], "evidence_collection": {
            "source_evidence": "小红书", "queries": [{"name": "新闻", "keyword": "新闻"}]}},
        {"intent": "网页摘要", "urls": [url], "max_items": 5,
         "analysis_type": "summary", "outputs": ["report_md"]},
    ])

    async def model(*args, **kwargs):
        return json.dumps(next(drafts), ensure_ascii=False)

    monkeypatch.setattr(planner, "achat", model)
    monkeypatch.setattr(planner, "lesson_for_planner", lambda *args, **kwargs: "")
    result = asyncio.run(planner.planner_node({"user_input": f"读取 {url}，最多5条，生成摘要报告"}))
    assert result["needs_clarification"] is False
    spec = result["task_spec"]
    assert spec.urls == [url] and spec.evidence_collection is None
    assert "小红书" not in spec.platforms and spec.max_items == 5
    assert spec.outputs == ["report_md"] and spec.analysis_type == "summary"


@pytest.mark.parametrize("second", ["conflict", "invalid_json", "unavailable"])
@pytest.mark.parametrize("scope", ["bank_benefits", "evidence_collection"])
def test_unresolved_source_conflict_never_executes(monkeypatch, second, scope):
    calls = 0

    async def model(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2 and second == "unavailable":
            raise RuntimeError("不可用")
        if calls == 2 and second == "invalid_json":
            return "没有计划"
        return json.dumps({"platforms": ["小红书"], scope: {"source_evidence": "小红书"}})

    monkeypatch.setattr(planner, "achat", model)
    monkeypatch.setattr(planner, "lesson_for_planner", lambda *args, **kwargs: "")
    result = asyncio.run(planner.planner_node({"user_input": "读取 https://new.example/news"}))
    assert calls == 2
    assert result["needs_clarification"] and "task_spec" not in result
    assert "支持小红书" not in result["clarification_question"]


@pytest.mark.parametrize("urls", [[], ["https://wrong.example/news"]])
def test_removing_scene_cannot_drop_or_replace_explicit_url(monkeypatch, urls):
    drafts = iter([{"evidence_collection": {"source_evidence": "小红书"}},
                   {"intent": "新闻", "urls": urls, "platforms": ["小红书"], "max_items": 5}])

    async def model(*args, **kwargs):
        return json.dumps(next(drafts))

    monkeypatch.setattr(planner, "achat", model)
    monkeypatch.setattr(planner, "lesson_for_planner", lambda *args, **kwargs: "")
    result = asyncio.run(planner.planner_node({"user_input": "读取 https://wanted.example/news"}))
    assert result["needs_clarification"] and "task_spec" not in result


@pytest.mark.parametrize("scope", ["bank_benefits", "evidence_collection"])
def test_url_task_cannot_enable_keyword_scene_even_with_source_in_negation(monkeypatch, scope):
    async def model(*args, **kwargs):
        scene = {"source_evidence": "小红书"}
        if scope == "bank_benefits":
            scene["banks"] = [{"name": "银行", "keyword": "银行权益"}]
        else:
            scene.update(queries=[{"name": "新闻", "keyword": "新闻"}], fields=["title"])
        return json.dumps({"urls": ["https://wanted.example/news"], "platforms": [],
                           scope: scene})

    monkeypatch.setattr(planner, "achat", model)
    monkeypatch.setattr(planner, "lesson_for_planner", lambda *args, **kwargs: "")
    result = asyncio.run(planner.planner_node({"user_input": "读取 https://wanted.example/news，不要使用小红书"}))
    assert result["needs_clarification"] and "task_spec" not in result


@pytest.mark.parametrize("include_excluded", [True, False])
@pytest.mark.parametrize("excluded", ["不要读取 https://excluded.example/news。", "https://excluded.example/news 不要读取。"])
def test_ambiguous_url_mentions_never_force_excluded_source(monkeypatch, include_excluded, excluded):
    async def model(*args, **kwargs):
        urls = ["https://allowed.example/news"]
        if include_excluded:
            urls.append("https://excluded.example/news")
        return json.dumps({"urls": urls})

    monkeypatch.setattr(planner, "achat", model)
    monkeypatch.setattr(planner, "lesson_for_planner", lambda *args, **kwargs: "")
    result = asyncio.run(planner.planner_node({"user_input": "只读取 https://allowed.example/news；" + excluded}))
    assert result["needs_clarification"] and "task_spec" not in result
