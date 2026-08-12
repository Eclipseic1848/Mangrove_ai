#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""方案 D：记忆命中埋点单元测试。

运行：python scripts/test_memory_hit_log.py
用临时 store 隔离，不碰真实 data/webui.db。
"""
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.store import WebUIStore
from src.config.user_ctx import set_user_memories


def test_lesson_for_analyze_logs_hit():
    """lesson_for_analyze 命中 active 教训时写一条埋点。"""
    import src.memory.lessons as lesson
    import src.memory.embeddings as emb
    from src.config.settings import settings
    from src.conductor.task_spec import DataType, TaskSpec
    import yaml, tempfile

    d = Path(tempfile.mkdtemp(prefix="mg_hit_"))
    lesson.LESSONS_DIR = d
    lesson._lessons_cache.invalidate()
    front = yaml.safe_dump(
        {"title": "教训甲", "data_type": "comment", "keywords": ["k"],
         "status": "active", "occurrences": 2, "helped_avoid": 1},
        allow_unicode=True, sort_keys=False,
    ).strip()
    (d / "a.md").write_text(f"---\n{front}\n---\n正文\n", encoding="utf-8")

    store = _tmp_store()
    old_embed = emb.embed_texts_with_model
    old_rerank = emb.rerank_scores
    old_is_rerank = emb.is_rerank_configured
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test", [[1.0, 0.0] for _ in texts])
    emb.is_rerank_configured = lambda: True
    emb.rerank_scores = lambda q, docs, instruct=None: [0.9] * len(docs)
    try:
        spec = TaskSpec(intent="测试", data_type=DataType.COMMENT, keywords=["k"])
        text, slug = lesson.lesson_for_analyze(spec, store=store, task_id="t_hit")
        assert slug == "a"
        rows = store.memory_hit_log_recent()
        assert len(rows) == 1
        assert rows[0]["hit_type"] == "lesson"
        assert rows[0]["slug"] == "a"
        assert rows[0]["task_id"] == "t_hit"
        assert rows[0]["degrade_path"] == "semantic"
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed
        emb.is_rerank_configured = old_is_rerank
        emb.rerank_scores = old_rerank


def test_lesson_for_analyze_no_candidates_logs_miss_none():
    """无候选教训时（库为空/无同类型）仍写一条 miss 埋点，degrade_path=none。"""
    import src.memory.lessons as lesson
    from src.config.settings import settings
    from src.conductor.task_spec import DataType, TaskSpec
    import tempfile

    d = Path(tempfile.mkdtemp(prefix="mg_hit2_"))
    lesson.LESSONS_DIR = d
    lesson._lessons_cache.invalidate()
    store = _tmp_store()
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    try:
        spec = TaskSpec(intent="测试", data_type=DataType.COMMENT, keywords=["k"])
        text, slug = lesson.lesson_for_analyze(spec, store=store, task_id="t_miss")
        assert slug is None
        rows = store.memory_hit_log_recent()
        assert len(rows) == 1
        assert rows[0]["hit"] == 0
        assert rows[0]["degrade_path"] == "none"
    finally:
        settings.embedding_enabled = old_enabled


def _tmp_store() -> WebUIStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return WebUIStore(tmp.name)


def test_hit_log_add_and_recent():
    """写入一条埋点，recent 能读回。"""
    store = _tmp_store()
    store.memory_hit_log_add(
        hit_type="lesson", slug="lesson-a", threshold=0.92,
        degrade_path="semantic", task_id="t_001",
    )
    rows = store.memory_hit_log_recent(limit=10)
    assert len(rows) == 1
    r = rows[0]
    assert r["hit_type"] == "lesson"
    assert r["slug"] == "lesson-a"
    assert r["threshold"] == 0.92
    assert r["degrade_path"] == "semantic"
    assert r["task_id"] == "t_001"


def test_hit_log_recent_orders_desc():
    """recent 按时间倒序（最新的在前）。"""
    store = _tmp_store()
    store.memory_hit_log_add("lesson", "a", 0.9, "semantic", "t1")
    store.memory_hit_log_add("template", "b", 0.8, "keyword", "t2")
    rows = store.memory_hit_log_recent(limit=10)
    assert len(rows) == 2
    assert rows[0]["slug"] == "b"  # 后写的在前
    assert rows[1]["slug"] == "a"


def test_hit_log_stats_aggregates():
    """stats 聚合：按 hit_type 统计命中数与降级路径分布。"""
    store = _tmp_store()
    store.memory_hit_log_add("lesson", "a", 0.9, "semantic", "t1")
    store.memory_hit_log_add("lesson", "b", 0.8, "keyword", "t2")
    store.memory_hit_log_add("template", "c", 0.7, "semantic", "t3")
    stats = store.memory_hit_log_stats()
    # 按 hit_type 聚合
    by_type = {s["hit_type"]: s for s in stats}
    assert by_type["lesson"]["count"] == 2
    assert by_type["lesson"]["semantic_count"] == 1
    assert by_type["lesson"]["keyword_count"] == 1
    assert by_type["template"]["count"] == 1
    assert by_type["template"]["semantic_count"] == 1


def test_hit_log_stats_empty():
    """空表 stats 返回空列表。"""
    store = _tmp_store()
    assert store.memory_hit_log_stats() == []


def test_hit_log_add_defaults_to_hit():
    """memory_hit_log_add 默认 hit=True（未命中埋点需显式传 hit=False）。"""
    store = _tmp_store()
    store.memory_hit_log_add("lesson", "a", 0.9, "semantic", "t1")
    rows = store.memory_hit_log_recent()
    assert rows[0]["hit"] == 1


def test_hit_log_add_records_miss():
    """未命中埋点：hit=False，slug 可为空。"""
    store = _tmp_store()
    store.memory_hit_log_add("lesson", "", 0.0, "semantic", "t1", hit=False)
    rows = store.memory_hit_log_recent()
    assert rows[0]["hit"] == 0
    assert rows[0]["degrade_path"] == "semantic"


def test_hit_log_stats_computes_hit_rate():
    """stats 按 hit_type 聚合 count(总尝试数)/hit_count(命中数)，可算命中率。"""
    store = _tmp_store()
    store.memory_hit_log_add("lesson", "a", 0.9, "semantic", "t1", hit=True)
    store.memory_hit_log_add("lesson", "", 0.0, "semantic", "t2", hit=False)  # rerank筛空的未命中
    store.memory_hit_log_add("lesson", "", 0.0, "none", "t3", hit=False)      # 无候选的未命中
    stats = store.memory_hit_log_stats()
    by_type = {s["hit_type"]: s for s in stats}
    assert by_type["lesson"]["count"] == 3
    assert by_type["lesson"]["hit_count"] == 1


def test_lesson_for_analyze_logs_miss_with_degrade_path():
    """未命中时（语义跑过但被筛空）写一条 miss 埋点，保留 degrade_path 供诊断。"""
    import src.memory.lessons as lesson
    import src.memory.embeddings as emb
    from src.config.settings import settings
    from src.conductor.task_spec import DataType, TaskSpec
    import yaml, tempfile

    d = Path(tempfile.mkdtemp(prefix="mg_hit3_"))
    lesson.LESSONS_DIR = d
    lesson._lessons_cache.invalidate()
    front = yaml.safe_dump(
        {"title": "教训甲", "data_type": "comment", "keywords": ["k"],
         "status": "active", "occurrences": 2, "helped_avoid": 1},
        allow_unicode=True, sort_keys=False,
    ).strip()
    (d / "a.md").write_text(f"---\n{front}\n---\n正文\n", encoding="utf-8")

    store = _tmp_store()
    old_embed = emb.embed_texts_with_model
    old_rerank = emb.rerank_scores
    old_is_rerank = emb.is_rerank_configured
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test", [[1.0, 0.0] for _ in texts])
    emb.is_rerank_configured = lambda: True
    emb.rerank_scores = lambda q, docs, instruct=None: [0.0] * len(docs)  # rerank 全筛空
    try:
        spec = TaskSpec(intent="测试", data_type=DataType.COMMENT, keywords=["k"])
        text, slug = lesson.lesson_for_analyze(spec, store=store, task_id="t_miss_semantic")
        assert slug is None
        rows = store.memory_hit_log_recent()
        assert len(rows) == 1
        assert rows[0]["hit"] == 0
        assert rows[0]["degrade_path"] == "semantic"
        assert rows[0]["task_id"] == "t_miss_semantic"
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed
        emb.is_rerank_configured = old_is_rerank
        emb.rerank_scores = old_rerank


def main():
    tests = [
        test_hit_log_add_and_recent,
        test_hit_log_recent_orders_desc,
        test_hit_log_stats_aggregates,
        test_hit_log_stats_empty,
        test_hit_log_add_defaults_to_hit,
        test_hit_log_add_records_miss,
        test_hit_log_stats_computes_hit_rate,
        test_lesson_for_analyze_logs_hit,
        test_lesson_for_analyze_no_candidates_logs_miss_none,
        test_lesson_for_analyze_logs_miss_with_degrade_path,
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