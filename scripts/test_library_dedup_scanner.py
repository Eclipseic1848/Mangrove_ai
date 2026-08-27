#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""模板库/教训库定时巡检单元测试。

运行：python scripts/test_library_dedup_scanner.py
用临时目录/临时 store 隔离，不污染真实 data/templates/、data/lessons/、data/webui.db。
"""
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.store import WebUIStore
from src.config.settings import settings
import src.memory.templates as tpl
import src.memory.lessons as lesson
import src.memory.embeddings as emb
from src.api.library_dedup_scanner import LibraryDedupScanner
from tests.database_migration_helpers import migrated_webui_database


def _tmp_store() -> WebUIStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return WebUIStore(str(migrated_webui_database(tmp.name)))


def _setup_tpl_tmp():
    d = Path(tempfile.mkdtemp(prefix="mg_tpl_patrol_"))
    tpl.TEMPLATES_DIR = d
    return d


def _setup_lesson_tmp():
    d = Path(tempfile.mkdtemp(prefix="mg_lesson_patrol_"))
    lesson.LESSONS_DIR = d
    return d


def test_save_template_writes_created_at():
    _setup_tpl_tmp()
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    try:
        slug = asyncio.run(tpl.save_template("巡检测试模板", "generic", ["巡检"], "正文"))
        t = [x for x in tpl.load_templates() if x["slug"] == slug][0]
        assert t["created_at"], "新建模板应写入 created_at"
    finally:
        settings.embedding_enabled = old_enabled


def test_legacy_template_without_created_at_reads_empty():
    d = _setup_tpl_tmp()
    (d / "legacy.md").write_text(
        "---\ntitle: 老模板\ndata_type: article\nkeywords: [旧格式]\n---\n正文\n",
        encoding="utf-8",
    )
    t = [x for x in tpl.load_templates() if x["slug"] == "legacy"][0]
    assert t["created_at"] == ""


def test_record_failure_writes_created_at():
    _setup_lesson_tmp()
    payload = {"title": "巡检测试教训", "keywords": ["巡检"], "body": "应对建议"}
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False

    async def _fake(messages, **kwargs):
        return json.dumps(payload, ensure_ascii=False)

    try:
        with patch("src.memory.lessons.achat", new=_fake):
            asyncio.run(lesson.record_failure("巡检测试任务", "generic", ["巡检"], "未采集到有效数据"))
        t = lesson.load_lessons()[0]
        assert t["created_at"], "新建教训应写入 created_at"
    finally:
        settings.embedding_enabled = old_enabled


def test_legacy_lesson_without_created_at_reads_empty():
    d = _setup_lesson_tmp()
    (d / "legacy.md").write_text(
        "---\ntitle: 老教训\ndata_type: generic\nkeywords: [旧格式]\nstatus: draft\noccurrences: 1\n---\n正文\n",
        encoding="utf-8",
    )
    t = [x for x in lesson.load_lessons() if x["slug"] == "legacy"][0]
    assert t["created_at"] == ""


def test_scan_log_add_and_recent():
    store = _tmp_store()
    store.library_dedup_scan_log_add(
        templates_scanned=10, templates_merged=2, lessons_scanned=5, lessons_merged=1,
        stale_drafts_deleted=1,
        details=json.dumps([{"action": "merge_template", "survivor_slug": "a", "loser_slug": "b"}]),
    )
    store.library_dedup_scan_log_add(
        templates_scanned=8, templates_merged=0, lessons_scanned=4, lessons_merged=0, stale_drafts_deleted=0,
    )
    rows = store.library_dedup_scan_log_recent(limit=20)
    assert len(rows) == 2
    # 按 id 倒序，最新一条（后写入的）在前
    assert rows[0]["templates_scanned"] == 8
    assert rows[0]["details"] == ""  # 未传 details 时为空串
    assert rows[1]["templates_scanned"] == 10
    assert json.loads(rows[1]["details"])[0]["action"] == "merge_template"


def _write_template(d, slug, title, data_type, keywords, body, status="active", uses=0, quality_avg=0):
    import yaml
    front = yaml.safe_dump(
        {"title": title, "data_type": data_type, "keywords": keywords,
         "status": status, "uses": uses, "quality_avg": quality_avg},
        allow_unicode=True, sort_keys=False,
    ).strip()
    (d / f"{slug}.md").write_text(f"---\n{front}\n---\n{body}\n", encoding="utf-8")


def test_find_patrol_duplicate_excludes_self_and_finds_other():
    d = _setup_tpl_tmp()
    _write_template(d, "a", "模板甲", "product", ["评测"], "正文甲")
    _write_template(d, "b", "完全不同措辞的模板", "product", ["体验"], "正文乙")
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    old_is_rerank = emb.is_rerank_configured
    old_rerank = emb.rerank_scores
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test-model", [[1.0, 0.0] for _ in texts])
    emb.is_rerank_configured = lambda: True
    emb.rerank_scores = lambda query, docs, instruct=None: [0.9] * len(docs)
    try:
        entry = [x for x in tpl.load_templates() if x["slug"] == "a"][0]
        result = tpl.find_patrol_duplicate(entry)
        assert result is not None
        dup, score = result
        assert dup["slug"] == "b", dup
        assert score == 0.9, score
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed
        emb.is_rerank_configured = old_is_rerank
        emb.rerank_scores = old_rerank


def test_find_patrol_duplicate_returns_none_when_embedding_disabled():
    d = _setup_tpl_tmp()
    _write_template(d, "a", "模板甲", "product", ["评测"], "正文甲")
    _write_template(d, "b", "模板乙", "product", ["评测"], "正文乙")
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    try:
        entry = [x for x in tpl.load_templates() if x["slug"] == "a"][0]
        assert tpl.find_patrol_duplicate(entry) is None
    finally:
        settings.embedding_enabled = old_enabled


def test_merge_template_pair_returns_fused_content():
    _setup_tpl_tmp()
    a = {"title": "模板甲", "keywords": ["评测"], "body": "正文甲"}
    b = {"title": "模板乙", "keywords": ["体验"], "body": "正文乙"}
    payload = {"title": "模板甲", "keywords": ["评测", "体验"], "body": "融合正文"}

    async def _fake(messages, **kwargs):
        return json.dumps(payload, ensure_ascii=False)

    with patch("src.memory.templates.achat", new=_fake):
        result = asyncio.run(tpl.merge_template_pair(a, b))
    assert result == {"title": "模板甲", "keywords": ["评测", "体验"], "body": "融合正文"}


def test_merge_template_pair_returns_none_on_llm_failure():
    _setup_tpl_tmp()
    a = {"title": "模板甲", "keywords": ["评测"], "body": "正文甲"}
    b = {"title": "模板乙", "keywords": ["体验"], "body": "正文乙"}

    async def _boom(messages, **kwargs):
        raise RuntimeError("boom")

    with patch("src.memory.templates.achat", new=_boom):
        result = asyncio.run(tpl.merge_template_pair(a, b))
    assert result is None


def test_apply_patrol_merge_keeps_stats_updates_content():
    d = _setup_tpl_tmp()
    _write_template(d, "survivor", "旧标题", "product", ["旧词"], "旧正文", status="active", uses=5, quality_avg=80)
    ok = tpl.apply_patrol_merge("survivor", {"title": "新标题", "keywords": ["旧词", "新词"], "body": "新正文"})
    assert ok is True
    t = [x for x in tpl.load_templates() if x["slug"] == "survivor"][0]
    assert t["title"] == "新标题" and t["body"] == "新正文"
    assert set(t["keywords"]) == {"旧词", "新词"}
    assert t["uses"] == 5 and t["quality_avg"] == 80.0 and t["status"] == "active"


def _write_lesson(d, slug, title, data_type, keywords, body, status="active", occurrences=1):
    import yaml
    front = yaml.safe_dump(
        {"title": title, "data_type": data_type, "keywords": keywords,
         "status": status, "occurrences": occurrences},
        allow_unicode=True, sort_keys=False,
    ).strip()
    (d / f"{slug}.md").write_text(f"---\n{front}\n---\n{body}\n", encoding="utf-8")


def test_find_patrol_duplicate_lesson_excludes_self_and_finds_other():
    d = _setup_lesson_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["小众品牌"], "正文甲", status="active", occurrences=2)
    _write_lesson(d, "b", "完全不同措辞的教训", "comment", ["抖音"], "正文乙", status="active", occurrences=2)
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    old_is_rerank = emb.is_rerank_configured
    old_rerank = emb.rerank_scores
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test-model", [[1.0, 0.0] for _ in texts])
    emb.is_rerank_configured = lambda: True
    emb.rerank_scores = lambda query, docs, instruct=None: [0.9] * len(docs)
    try:
        entry = [x for x in lesson.load_lessons() if x["slug"] == "a"][0]
        result = lesson.find_patrol_duplicate_lesson(entry)
        assert result is not None
        dup, score = result
        assert dup["slug"] == "b", dup
        assert score == 0.9, score
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed
        emb.is_rerank_configured = old_is_rerank
        emb.rerank_scores = old_rerank


def test_find_patrol_duplicate_lesson_returns_none_when_embedding_disabled():
    d = _setup_lesson_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["小众品牌"], "正文甲")
    _write_lesson(d, "b", "教训乙", "comment", ["小众品牌"], "正文乙")
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    try:
        entry = [x for x in lesson.load_lessons() if x["slug"] == "a"][0]
        assert lesson.find_patrol_duplicate_lesson(entry) is None
    finally:
        settings.embedding_enabled = old_enabled


def test_merge_lesson_pair_returns_fused_content():
    _setup_lesson_tmp()
    a = {"title": "教训甲", "keywords": ["小众品牌"], "body": "正文甲"}
    b = {"title": "教训乙", "keywords": ["抖音"], "body": "正文乙"}
    payload = {"title": "教训甲", "keywords": ["小众品牌", "抖音"], "body": "融合正文"}

    async def _fake(messages, **kwargs):
        return json.dumps(payload, ensure_ascii=False)

    with patch("src.memory.lessons.achat", new=_fake):
        result = asyncio.run(lesson.merge_lesson_pair(a, b))
    assert result == {"title": "教训甲", "keywords": ["小众品牌", "抖音"], "body": "融合正文"}


def test_merge_lesson_pair_returns_none_on_llm_failure():
    _setup_lesson_tmp()
    a = {"title": "教训甲", "keywords": ["小众品牌"], "body": "正文甲"}
    b = {"title": "教训乙", "keywords": ["抖音"], "body": "正文乙"}

    async def _boom(messages, **kwargs):
        raise RuntimeError("boom")

    with patch("src.memory.lessons.achat", new=_boom):
        result = asyncio.run(lesson.merge_lesson_pair(a, b))
    assert result is None


def test_apply_patrol_merge_lesson_keeps_stats_updates_content():
    d = _setup_lesson_tmp()
    _write_lesson(d, "survivor", "旧标题", "comment", ["旧词"], "旧正文", status="active", occurrences=3)
    ok = lesson.apply_patrol_merge_lesson("survivor", {"title": "新标题", "keywords": ["旧词", "新词"], "body": "新正文"})
    assert ok is True
    t = [x for x in lesson.load_lessons() if x["slug"] == "survivor"][0]
    assert t["title"] == "新标题" and t["body"] == "新正文"
    assert set(t["keywords"]) == {"旧词", "新词"}
    assert t["occurrences"] == 3 and t["status"] == "active"


def test_run_one_scan_merges_and_logs():
    """整轮巡检：模板库+教训库各放一对语义重复项，确认自动合并、保留方统计不变、
    另一方文件消失、巡检日志写入一行正确计数。"""
    td = _setup_tpl_tmp()
    _write_template(td, "tpl-a", "模板甲", "product", ["评测"], "正文甲", status="active", uses=5, quality_avg=80)
    _write_template(td, "tpl-b", "完全不同措辞的模板", "product", ["体验"], "正文乙", status="draft", uses=0, quality_avg=0)
    ld = _setup_lesson_tmp()
    _write_lesson(ld, "lsn-a", "教训甲", "comment", ["小众品牌"], "正文甲", status="active", occurrences=3)
    _write_lesson(ld, "lsn-b", "完全不同措辞的教训", "comment", ["抖音"], "正文乙", status="draft", occurrences=1)

    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    old_is_rerank = emb.is_rerank_configured
    old_rerank = emb.rerank_scores
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test-model", [[1.0, 0.0] for _ in texts])
    emb.is_rerank_configured = lambda: True
    emb.rerank_scores = lambda query, docs, instruct=None: [0.9] * len(docs)

    tpl_payload = {"title": "模板甲", "keywords": ["评测", "体验"], "body": "融合正文-模板"}
    lsn_payload = {"title": "教训甲", "keywords": ["小众品牌", "抖音"], "body": "融合正文-教训"}

    async def _fake_tpl_achat(messages, **kwargs):
        return json.dumps(tpl_payload, ensure_ascii=False)

    async def _fake_lsn_achat(messages, **kwargs):
        return json.dumps(lsn_payload, ensure_ascii=False)

    class _FakeStore:
        def __init__(self):
            self.calls = []

        def library_dedup_scan_log_add(self, **kwargs):
            self.calls.append(kwargs)

    fake_store = _FakeStore()

    try:
        with patch("src.memory.templates.achat", new=_fake_tpl_achat), \
             patch("src.memory.lessons.achat", new=_fake_lsn_achat), \
             patch("src.api.auth.get_store", new=lambda: fake_store):
            scanner = LibraryDedupScanner()
            asyncio.run(scanner._run_one_scan())

        tpl_remaining = tpl.load_templates()
        assert len(tpl_remaining) == 1, tpl_remaining
        assert tpl_remaining[0]["slug"] == "tpl-a"
        assert tpl_remaining[0]["body"] == "融合正文-模板"
        assert tpl_remaining[0]["uses"] == 5 and tpl_remaining[0]["quality_avg"] == 80.0

        lsn_remaining = lesson.load_lessons()
        assert len(lsn_remaining) == 1, lsn_remaining
        assert lsn_remaining[0]["slug"] == "lsn-a"
        assert lsn_remaining[0]["body"] == "融合正文-教训"
        assert lsn_remaining[0]["occurrences"] == 3

        assert len(fake_store.calls) == 1
        call = fake_store.calls[0]
        assert call["templates_scanned"] == 2 and call["templates_merged"] == 1
        assert call["lessons_scanned"] == 2 and call["lessons_merged"] == 1
        assert call["stale_drafts_deleted"] == 0
        # 明细：模板合并 1 条 + 教训合并 1 条，均带相似度分数与阈值
        details = json.loads(call["details"])
        actions = sorted(d["action"] for d in details)
        assert actions == ["merge_lesson", "merge_template"], actions
        tpl_merge = [d for d in details if d["action"] == "merge_template"][0]
        assert tpl_merge["score"] == 0.9 and tpl_merge["threshold"] is not None
        assert tpl_merge["survivor_slug"] == "tpl-a" and tpl_merge["loser_slug"] == "tpl-b"
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed
        emb.is_rerank_configured = old_is_rerank
        emb.rerank_scores = old_rerank


def test_run_one_scan_deletes_stale_drafts():
    """整轮巡检：一条超过阈值天数仍为 draft 的模板/教训应被清理，计入 stale_drafts_deleted。"""
    from datetime import datetime, timedelta

    td = _setup_tpl_tmp()
    old_created = (datetime.now() - timedelta(days=31)).isoformat()
    (td / "stale.md").write_text(
        f"---\ntitle: 停滞模板\ndata_type: generic\nkeywords: [x]\nstatus: draft\nuses: 0\n"
        f"quality_avg: 0\ncreated_at: {old_created}\n---\n正文\n",
        encoding="utf-8",
    )
    ld = _setup_lesson_tmp()

    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False  # 本测试不涉及去重，关闭语义召回，确定性走跳过分支

    class _FakeStore:
        def __init__(self):
            self.calls = []

        def library_dedup_scan_log_add(self, **kwargs):
            self.calls.append(kwargs)

    fake_store = _FakeStore()
    try:
        with patch("src.api.auth.get_store", new=lambda: fake_store):
            scanner = LibraryDedupScanner()
            asyncio.run(scanner._run_one_scan())
        assert tpl.load_templates() == []
        assert fake_store.calls[0]["stale_drafts_deleted"] == 1
        details = json.loads(fake_store.calls[0]["details"])
        assert len(details) == 1 and details[0]["action"] == "stale_delete_template"
        assert details[0]["slug"] == "stale" and details[0]["stale_days"] > 30
    finally:
        settings.embedding_enabled = old_enabled


def test_run_one_scan_skips_dedup_when_embedding_disabled():
    """embedding 关闭时，去重扫描应直接跳过（不做关键词兜底），但停滞清理照常执行。"""
    td = _setup_tpl_tmp()
    _write_template(td, "tpl-a", "模板甲", "product", ["评测"], "正文甲")
    _write_template(td, "tpl-b", "模板乙", "product", ["评测"], "正文乙")  # 关键词完全重合，若走 Jaccard 兜底会被误判为重复
    ld = _setup_lesson_tmp()

    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False

    class _FakeStore:
        def __init__(self):
            self.calls = []

        def library_dedup_scan_log_add(self, **kwargs):
            self.calls.append(kwargs)

    fake_store = _FakeStore()
    try:
        with patch("src.api.auth.get_store", new=lambda: fake_store):
            scanner = LibraryDedupScanner()
            asyncio.run(scanner._run_one_scan())
        assert len(tpl.load_templates()) == 2, "embedding 关闭时不应发生任何合并"
        assert fake_store.calls[0]["templates_merged"] == 0
    finally:
        settings.embedding_enabled = old_enabled


def test_patrol_duplicate_lookup_does_not_block_event_loop():
    """同步语义判重较慢时，巡检不得阻塞 API 所在的事件循环。"""
    d = _setup_tpl_tmp()
    _write_template(d, "slow", "慢判重模板", "generic", ["巡检"], "正文")

    def _slow_lookup(entry):
        time.sleep(0.2)
        return None

    async def _run():
        scanner = LibraryDedupScanner()
        started = time.perf_counter()
        task = asyncio.create_task(scanner._dedup_pass_templates())
        await asyncio.sleep(0.02)
        elapsed = time.perf_counter() - started
        await task
        assert elapsed < 0.1, f"事件循环被同步判重阻塞了 {elapsed:.3f}s"

    with patch("src.memory.templates.find_patrol_duplicate", new=_slow_lookup):
        asyncio.run(_run())


def main():
    tests = [
        test_save_template_writes_created_at,
        test_legacy_template_without_created_at_reads_empty,
        test_record_failure_writes_created_at,
        test_legacy_lesson_without_created_at_reads_empty,
        test_scan_log_add_and_recent,
        test_find_patrol_duplicate_excludes_self_and_finds_other,
        test_find_patrol_duplicate_returns_none_when_embedding_disabled,
        test_merge_template_pair_returns_fused_content,
        test_merge_template_pair_returns_none_on_llm_failure,
        test_apply_patrol_merge_keeps_stats_updates_content,
        test_find_patrol_duplicate_lesson_excludes_self_and_finds_other,
        test_find_patrol_duplicate_lesson_returns_none_when_embedding_disabled,
        test_merge_lesson_pair_returns_fused_content,
        test_merge_lesson_pair_returns_none_on_llm_failure,
        test_apply_patrol_merge_lesson_keeps_stats_updates_content,
        test_run_one_scan_merges_and_logs,
        test_run_one_scan_deletes_stale_drafts,
        test_run_one_scan_skips_dedup_when_embedding_disabled,
        test_patrol_duplicate_lookup_does_not_block_event_loop,
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
