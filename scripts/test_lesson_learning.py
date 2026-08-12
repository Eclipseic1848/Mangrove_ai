#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""教训分流单元测试（B2 阶段：语义召回/关键词兜底、创建-合并-转正、planner/analyze 消费注入）。

运行：python scripts/test_lesson_learning.py
用临时目录隔离，不污染真实 data/lessons/。
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import settings
from src.conductor.task_spec import DataType, TaskSpec
import src.memory.lessons as lesson
import src.memory.embeddings as emb


def _setup_tmp():
    d = Path(tempfile.mkdtemp(prefix="mg_lesson_"))
    lesson.LESSONS_DIR = d
    return d


def _write_lesson(d, slug, title, data_type, keywords, body, status="active", occurrences=1):
    front = yaml.safe_dump(
        {"title": title, "data_type": data_type, "keywords": keywords,
         "status": status, "occurrences": occurrences},
        allow_unicode=True, sort_keys=False,
    ).strip()
    (d / f"{slug}.md").write_text(f"---\n{front}\n---\n{body}\n", encoding="utf-8")


def _fake_achat(payload: dict):
    async def fake(messages, **kwargs):
        return json.dumps(payload, ensure_ascii=False)
    return fake


def _fake_achat_sequence(payloads):
    """依次返回 payloads 里的负载，用尽后重复最后一个（供需要区分蒸馏调用次数的测试使用）。"""
    calls = {"n": 0}

    async def fake(messages, **kwargs):
        i = min(calls["n"], len(payloads) - 1)
        calls["n"] += 1
        return json.dumps(payloads[i], ensure_ascii=False)

    return fake


def test_find_similar_lesson_filters_by_data_type():
    d = _setup_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["小众品牌"], "正文甲", status="draft", occurrences=1)
    _write_lesson(d, "b", "教训乙", "bid", ["小众品牌"], "正文乙", status="draft", occurrences=1)
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False  # 走关键词 Jaccard 兜底，确定性
    try:
        hit = lesson.find_similar_lesson("comment", ["小众品牌"], "抖音小众品牌评论")
        assert hit is not None and hit["slug"] == "a", hit
    finally:
        settings.embedding_enabled = old_enabled


def test_find_similar_lesson_semantic_hit():
    d = _setup_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["小众品牌"], "正文甲", status="draft", occurrences=1)
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    old_is_rerank = emb.is_rerank_configured
    old_rerank = emb.rerank_scores
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test-model", [[1.0, 0.0] for _ in texts])
    emb.is_rerank_configured = lambda: True
    emb.rerank_scores = lambda query, docs, instruct=None: [0.9] * len(docs)
    try:
        hit = lesson.find_similar_lesson("comment", ["完全不同措辞"], "换一种说法的同类查询")
        assert hit is not None and hit["slug"] == "a", hit
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed
        emb.is_rerank_configured = old_is_rerank
        emb.rerank_scores = old_rerank


def test_find_similar_lesson_falls_back_to_jaccard_when_embedding_unavailable():
    d = _setup_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["小众品牌", "抖音"], "正文甲", status="draft", occurrences=1)
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: None  # 端点不可用
    try:
        hit = lesson.find_similar_lesson("comment", ["小众品牌", "抖音", "评论"], "任意意图文本")
        assert hit is not None and hit["slug"] == "a", hit
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed


def test_find_active_lessons_excludes_draft():
    d = _setup_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["小众品牌"], "正文甲", status="draft", occurrences=1)
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    try:
        results, _ = lesson.find_active_lessons("comment", ["小众品牌"], "抖音小众品牌评论")
        assert results == [], "draft 教训不应被消费侧召回"
    finally:
        settings.embedding_enabled = old_enabled


def test_find_active_lessons_falls_back_to_keyword_when_embedding_unavailable():
    """方案 C 起消费侧行为：语义不可用时退回关键词匹配（与旧版 find_active_lesson 的
    "直接放弃不兜底"不同——方案 C 上线时把这个设计取舍改成了和创建侧一致的关键词兜底，
    见 a832e9e 提交，degrade_path 会标为 keyword 供埋点区分。"""
    d = _setup_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["小众品牌", "抖音"], "正文甲", status="active", occurrences=2)
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: None
    try:
        results, degrade = lesson.find_active_lessons("comment", ["小众品牌", "抖音"], "任意意图文本")
        assert len(results) == 1 and results[0]["slug"] == "a", "语义端点不可用应退回关键词匹配命中"
        assert degrade == "keyword"
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed


def test_record_failure_creates_draft_when_no_match():
    _setup_tmp()
    payload = {"title": "抖音小众品牌评论采集不足", "keywords": ["小众品牌", "抖音"], "body": "应对建议正文"}
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    try:
        with patch("src.memory.lessons.achat", new=_fake_achat(payload)):
            asyncio.run(lesson.record_failure("抖音小众品牌评论分析", "comment", ["小众品牌"], "未采集到有效数据"))
        lessons = lesson.load_lessons()
        assert len(lessons) == 1
        assert lessons[0]["status"] == "draft" and lessons[0]["occurrences"] == 1
        assert lessons[0]["body"] == "应对建议正文"
    finally:
        settings.embedding_enabled = old_enabled


def test_record_failure_merges_and_promotes_to_active():
    """B 阶段：record_failure 不再自动转正（需 helped_avoid≥1 由 record_lesson_helped 控制）。"""
    d = _setup_tmp()
    _write_lesson(d, "existing", "旧教训", "comment", ["小众品牌"], "旧正文", status="draft", occurrences=1)
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False  # 关键词 Jaccard 命中旧教训（Jaccard=1.0）
    fresh_payload = {"title": "旧教训", "keywords": ["小众品牌"], "body": "临时草稿正文（会被合并覆盖）"}
    merge_payload = {"title": "旧教训", "keywords": ["小众品牌", "抖音"], "body": "融合后的新正文"}
    try:
        with patch("src.memory.lessons.achat", new=_fake_achat_sequence([fresh_payload, merge_payload])):
            asyncio.run(lesson.record_failure("同类失败任务", "comment", ["小众品牌"], "未采集到有效数据"))
        lessons = lesson.load_lessons()
        assert len(lessons) == 1, lessons
        t = lessons[0]
        assert t["slug"] == "existing"
        assert t["occurrences"] == 2
        # B：仅 occurrences 达标但 helped_avoid=0，保持 draft 不再自动转正
        assert t["status"] == "draft", f"期望 draft（因 helped_avoid=0），实际 {t['status']}"
        assert t["helped_avoid"] == 0
        assert t["body"] == "融合后的新正文"
        assert set(t["keywords"]) == {"小众品牌", "抖音"}
    finally:
        settings.embedding_enabled = old_enabled


def test_record_failure_keeps_accumulating_when_already_active():
    d = _setup_tmp()
    _write_lesson(d, "existing", "旧教训", "comment", ["小众品牌"], "旧正文", status="active", occurrences=2)
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    fresh_payload = {"title": "旧教训", "keywords": ["小众品牌"], "body": "临时草稿正文（会被合并覆盖）"}
    merge_payload = {"title": "旧教训", "keywords": ["小众品牌"], "body": "再次融合的正文"}
    try:
        with patch("src.memory.lessons.achat", new=_fake_achat_sequence([fresh_payload, merge_payload])):
            asyncio.run(lesson.record_failure("同类失败任务", "comment", ["小众品牌"], "未采集到有效数据"))
        t = lesson.load_lessons()[0]
        assert t["occurrences"] == 3 and t["status"] == "active", t
        assert t["body"] == "再次融合的正文"
    finally:
        settings.embedding_enabled = old_enabled


def test_record_failure_skips_when_distill_returns_none():
    _setup_tmp()
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False

    async def _empty_body(messages, **kwargs):
        return json.dumps({"title": "x", "keywords": [], "body": ""}, ensure_ascii=False)

    try:
        with patch("src.memory.lessons.achat", new=_empty_body):
            asyncio.run(lesson.record_failure("失败任务", "comment", ["k"], "未采集到有效数据"))
        assert lesson.load_lessons() == []
    finally:
        settings.embedding_enabled = old_enabled


def test_lesson_for_analyze_hits_active():
    d = _setup_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["小众品牌"], "别忘了这样应对", status="active", occurrences=2)
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    old_is_rerank = emb.is_rerank_configured
    old_rerank = emb.rerank_scores
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test-model", [[1.0, 0.0] for _ in texts])
    emb.is_rerank_configured = lambda: True
    emb.rerank_scores = lambda query, docs, instruct=None: [0.9] * len(docs)
    try:
        spec = TaskSpec(intent="抖音小众品牌评论分析", data_type=DataType.COMMENT, keywords=["小众品牌"])
        text, slug = lesson.lesson_for_analyze(spec)
        assert "别忘了这样应对" in text
        assert slug == "a"
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed
        emb.is_rerank_configured = old_is_rerank
        emb.rerank_scores = old_rerank


def test_lesson_for_analyze_ignores_draft():
    d = _setup_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["小众品牌"], "别忘了这样应对", status="draft", occurrences=1)
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    try:
        spec = TaskSpec(intent="抖音小众品牌评论分析", data_type=DataType.COMMENT, keywords=["小众品牌"])
        text, slug = lesson.lesson_for_analyze(spec)
        assert text == ""
        assert slug is None
    finally:
        settings.embedding_enabled = old_enabled


def test_lesson_for_planner_ignores_data_type():
    d = _setup_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["小众品牌", "抖音"], "规划阶段的提醒", status="active", occurrences=2)
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    old_is_rerank = emb.is_rerank_configured
    old_rerank = emb.rerank_scores
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test-model", [[1.0, 0.0] for _ in texts])
    emb.is_rerank_configured = lambda: True
    emb.rerank_scores = lambda query, docs, instruct=None: [0.9] * len(docs)
    try:
        text = lesson.lesson_for_planner("帮我看看抖音上小众品牌的评论")
        assert "规划阶段的提醒" in text
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed
        emb.is_rerank_configured = old_is_rerank
        emb.rerank_scores = old_rerank


def test_lesson_for_planner_empty_when_no_match():
    _setup_tmp()
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    try:
        assert lesson.lesson_for_planner("完全无关的查询") == ""
    finally:
        settings.embedding_enabled = old_enabled


def test_delete_lesson_removes_file_returns_true():
    d = _setup_tmp()
    _write_lesson(d, "existing", "旧教训", "comment", ["小众品牌"], "旧正文", status="active", occurrences=2)
    assert lesson.delete_lesson("existing") is True
    assert lesson.load_lessons() == []


def test_delete_lesson_missing_returns_false():
    _setup_tmp()
    assert lesson.delete_lesson("not-a-real-slug") is False


# ---- 方案 B：教训闭环反馈 + 退役 ----

def test_lesson_helped_avoid_defaults_to_zero():
    """旧教训（无 helped_avoid 字段）加载时默认值为 0。"""
    d = _setup_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["k"], "正文", status="active", occurrences=2)
    lessons = lesson.load_lessons()
    assert lessons[0].get("helped_avoid") == 0


def test_lesson_helped_avoid_reads_correctly():
    """有 helped_avoid 字段的教训正确读取。"""
    d = _setup_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["k"], "正文", status="active", occurrences=2)
    # 手动修改文件写入 helped_avoid
    path = d / "a.md"
    raw = path.read_text(encoding="utf-8")
    raw = raw.replace("occurrences: 2", "occurrences: 2\nhelped_avoid: 3")
    path.write_text(raw, encoding="utf-8")
    lesson._lessons_cache.invalidate()
    lessons = lesson.load_lessons()
    assert lessons[0]["helped_avoid"] == 3


def test_lesson_retired_status_reads_correctly():
    """retired 状态的教训正常加载。"""
    d = _setup_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["k"], "正文", status="retired", occurrences=10)
    lessons = lesson.load_lessons()
    assert lessons[0]["status"] == "retired"
    assert lessons[0]["helped_avoid"] == 0


def test_record_lesson_helped_increments_counter():
    """record_lesson_helped 使 helped_avoid +1。"""
    d = _setup_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["k"], "正文", status="draft", occurrences=1)
    lesson.record_lesson_helped("a")
    lessons = lesson.load_lessons()
    assert lessons[0]["helped_avoid"] == 1


def test_record_lesson_helped_nonexistent_returns_false():
    """不存在的教训返回 False。"""
    _setup_tmp()
    assert lesson.record_lesson_helped("nope") is False


def test_record_failure_no_longer_promotes_by_occurrences_alone():
    """仅 occurrences 达标不再自动转正（需要 helped_avoid≥1）。"""
    d = _setup_tmp()
    _write_lesson(d, "existing", "旧教训", "comment", ["k"], "旧正文", status="draft", occurrences=1)
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    payload = {"title": "旧教训", "keywords": ["k"], "body": "临时草稿正文"}
    merge_payload = {"title": "旧教训", "keywords": ["k"], "body": "融合后的新正文"}
    try:
        with patch("src.memory.lessons.achat", new=_fake_achat_sequence([payload, merge_payload])):
            asyncio.run(lesson.record_failure("同类失败任务", "comment", ["k"], "未采集到有效数据"))
        t = lesson.load_lessons()[0]
        assert t["occurrences"] == 2
        # 仅次数达标但不满足 helped_avoid≥1，仍保持 draft
        assert t["status"] == "draft", f"期望 draft，实际 {t['status']}"
    finally:
        settings.embedding_enabled = old_enabled


def test_record_lesson_helped_promotes_with_min_occurrences():
    """record_lesson_helped 使 draft 教训在 occurrences≥2 且 helped_avoid≥1 时转正。"""
    d = _setup_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["k"], "正文", status="draft", occurrences=2)
    lesson.record_lesson_helped("a")
    t = lesson.load_lessons()[0]
    assert t["status"] == "active", f"期望 active，实际 {t['status']}"
    assert t["helped_avoid"] == 1


def test_lesson_retired_excluded_from_active():
    """retired 教训不被消费侧 find_active_lessons 召回。"""
    d = _setup_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["k"], "正文", status="retired", occurrences=10)
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    try:
        results, _ = lesson.find_active_lessons("comment", ["k"], "任务")
        assert results == [], "retired 教训不应被消费侧召回"
    finally:
        settings.embedding_enabled = old_enabled


def test_record_lesson_helped_retires_on_excessive_failures():
    """helped_avoid==0 且 occurrences≥10 时，record_lesson_helped 将 active 教训退役。"""
    d = _setup_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["k"], "正文", status="active", occurrences=10)
    # helped_avoid 默认为 0，多次失败从未帮到 → 退役
    lesson.record_lesson_helped("a")
    t = lesson.load_lessons()[0]
    assert t["status"] == "retired", f"期望 retired，实际 {t['status']}"
    assert t["helped_avoid"] == 1


# ---- 方案 C：多教训召回 + 有效性排序 + INDEX.md ----

def test_find_active_lessons_sorts_by_effectiveness():
    """多条 active 教训同时命中时，按 helped_avoid/occurrences 有效性降序排列。"""
    d = _setup_tmp()
    # 3 条同类教训：一条高效(高 helped_avoid)，一条中效，一条低效
    _write_lesson(d, "low", "低效教训", "comment", ["k", "common"], "低效", status="active", occurrences=5)
    _write_lesson(d, "high", "高效教训", "comment", ["k", "common"], "高效", status="active", occurrences=2)
    _write_lesson(d, "mid", "中效教训", "comment", ["k", "common"], "中效", status="active", occurrences=3)
    # 手动设 helped_avoid
    for slug, ha in [("high", 3), ("mid", 2), ("low", 1)]:
        path = d / f"{slug}.md"
        raw = path.read_text(encoding="utf-8")
        raw = raw.replace(f"occurrences:", f"helped_avoid: {ha}\noccurrences:")
        path.write_text(raw, encoding="utf-8")
    lesson._lessons_cache.invalidate()

    old_embed = emb.embed_texts_with_model
    old_rerank = emb.rerank_scores
    old_is_rerank = emb.is_rerank_configured
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = True
    # mock：rerank 返回相同分数（让排序完全由有效性决定）
    emb.embed_texts_with_model = lambda texts: ("test", [[1.0, 0.0] for _ in texts])
    emb.is_rerank_configured = lambda: True
    emb.rerank_scores = lambda q, docs, instruct=None: [0.9] * len(docs)
    try:
        results, degrade = lesson.find_active_lessons("comment", ["k", "common"], "测试任务", top_k=3)
        assert len(results) == 3, f"期望 3 条，实际 {len(results)}"
        # 按有效性排序：high(3/2=1.5) > mid(2/3=0.67) > low(1/5=0.2)
        assert results[0]["slug"] == "high", f"第1应为high，实际{results[0]['slug']}"
        assert results[1]["slug"] == "mid"
        assert results[2]["slug"] == "low"
        assert degrade == "semantic"
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed
        emb.is_rerank_configured = old_is_rerank
        emb.rerank_scores = old_rerank


def test_find_active_lessons_falls_back_to_keyword_single():
    """语义不可用时退回关键词匹配（单条）。"""
    d = _setup_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["k", "x"], "正文", status="active", occurrences=2)
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    try:
        results, degrade = lesson.find_active_lessons("comment", ["k", "x"], "测试", top_k=3)
        assert len(results) <= 1  # 退回单条匹配
        assert results[0]["slug"] == "a"
        assert degrade == "keyword"
    finally:
        settings.embedding_enabled = old_enabled


def test_find_active_lessons_empty_when_no_match():
    """无命中返回空列表。"""
    _setup_tmp()
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    try:
        results, degrade = lesson.find_active_lessons("comment", ["nonexistent"], "x", top_k=3)
        assert results == []
        assert degrade == "none"
    finally:
        settings.embedding_enabled = old_enabled


def test_find_active_lessons_uses_recall_threshold_not_dedup_threshold():
    """E1 修复验证：消费侧召回应使用独立的召回阈值(settings.lesson_recall_rerank_threshold，
    与模板召回场景的 rerank_match_threshold 同构)，而不是复用去重阈值
    (template_dedup_rerank_threshold=0.7)。真实场景下"任务→教训是否有帮助"的
    rerank 分数通常低于"两条教训是否描述同一类失败"的判据分数，用去重阈值会导致
    库里明明有相关教训却召不回（复验时发现的真实 bug：小红书教训 rerank=0.5 被 0.7 阈值拒绝）。"""
    d = _setup_tmp()
    _write_lesson(d, "a", "跨平台采集目标路由漂移", "post", ["小红书", "定向采集"], "正文", status="active", occurrences=2)
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    old_is_rerank = emb.is_rerank_configured
    old_rerank = emb.rerank_scores
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test-model", [[1.0, 0.0] for _ in texts])
    emb.is_rerank_configured = lambda: True
    # 模拟真实场景：0.5 分——低于去重阈值 0.7（会被旧逻辑拒绝），但应高于召回阈值（应被新逻辑接受）
    emb.rerank_scores = lambda query, docs, instruct=None: [0.5] * len(docs)
    try:
        results, degrade = lesson.find_active_lessons("post", ["小红书"], "去小红书采集内容", top_k=3)
        assert len(results) == 1, f"0.5分应能召回（召回阈值应<0.5），实际召回{len(results)}条"
        assert results[0]["slug"] == "a"
        assert degrade == "semantic"
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed
        emb.is_rerank_configured = old_is_rerank
        emb.rerank_scores = old_rerank


def test_find_active_lessons_uses_recall_instruct_not_dedup_instruct():
    """E1 修复验证：消费侧召回的 rerank instruct 应是"该教训对此任务是否有价值"
    （任务→教训的适用性判断），而不是创建侧判重用的"两条教训是否描述同一类失败场景"
    （教训→教训的同类判断）——后者用于消费侧存在语义错配（复验记忆库记录的
    "去重/召回比对两侧必须同质"原则同类问题）。"""
    d = _setup_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["k"], "正文", status="active", occurrences=2)
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    old_is_rerank = emb.is_rerank_configured
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test", [[1.0, 0.0] for _ in texts])
    emb.is_rerank_configured = lambda: True

    captured = {}

    def _capture_rerank(query, docs, instruct=None):
        captured["instruct"] = instruct
        return [0.9] * len(docs)

    old_rerank = emb.rerank_scores
    emb.rerank_scores = _capture_rerank
    try:
        lesson.find_active_lessons("comment", ["k"], "测试任务", top_k=3)
        assert captured.get("instruct") != lesson._LESSON_RERANK_INSTRUCT, \
            "消费侧不应复用创建侧判重的 instruct（判据是任务→教训适用性，不是教训→教训同类性）"
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed
        emb.is_rerank_configured = old_is_rerank
        emb.rerank_scores = old_rerank


def main():
    tests = [
        test_find_similar_lesson_filters_by_data_type,
        test_find_similar_lesson_semantic_hit,
        test_find_similar_lesson_falls_back_to_jaccard_when_embedding_unavailable,
        test_find_active_lessons_excludes_draft,
        test_find_active_lessons_falls_back_to_keyword_when_embedding_unavailable,
        test_record_failure_creates_draft_when_no_match,
        test_record_failure_merges_and_promotes_to_active,
        test_record_failure_keeps_accumulating_when_already_active,
        test_record_failure_skips_when_distill_returns_none,
        test_lesson_for_analyze_hits_active,
        test_lesson_for_analyze_ignores_draft,
        test_lesson_for_planner_ignores_data_type,
        test_lesson_for_planner_empty_when_no_match,
        test_delete_lesson_removes_file_returns_true,
        test_delete_lesson_missing_returns_false,
        test_lesson_helped_avoid_defaults_to_zero,
        test_lesson_helped_avoid_reads_correctly,
        test_lesson_retired_status_reads_correctly,
        test_record_lesson_helped_increments_counter,
        test_record_lesson_helped_nonexistent_returns_false,
        test_record_failure_no_longer_promotes_by_occurrences_alone,
        test_record_lesson_helped_promotes_with_min_occurrences,
        test_lesson_retired_excluded_from_active,
        test_record_lesson_helped_retires_on_excessive_failures,
        test_find_active_lessons_sorts_by_effectiveness,
        test_find_active_lessons_falls_back_to_keyword_single,
        test_find_active_lessons_empty_when_no_match,
        test_find_active_lessons_uses_recall_threshold_not_dedup_threshold,
        test_find_active_lessons_uses_recall_instruct_not_dedup_instruct,
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
