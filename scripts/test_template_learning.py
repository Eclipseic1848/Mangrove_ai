#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""模板自学习增强单元测试（草稿区 / 使用统计 / 质量门转正与淘汰 / 关键词去重）。

运行：python scripts/test_template_learning.py
用临时目录隔离，不污染真实 data/templates/。
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
import src.memory.templates as tpl
import src.memory.embeddings as emb


def _setup_tmp():
    d = Path(tempfile.mkdtemp(prefix="mg_tpl_"))
    tpl.TEMPLATES_DIR = d  # 重定向到临时目录
    return d


def _write_template(d, slug, title, data_type, keywords, body, status="active", uses=0, quality_avg=0):
    """直接写一个模板文件到临时目录（跳过 save_template，避免测试耦合到保存/去重逻辑本身）。"""
    front = yaml.safe_dump(
        {"title": title, "data_type": data_type, "keywords": keywords,
         "status": status, "uses": uses, "quality_avg": quality_avg},
        allow_unicode=True, sort_keys=False,
    ).strip()
    (d / f"{slug}.md").write_text(f"---\n{front}\n---\n{body}\n", encoding="utf-8")


def _fake_achat(payload: dict):
    async def fake(messages, **kwargs):
        return json.dumps(payload, ensure_ascii=False)
    return fake


def test_save_is_draft():
    _setup_tmp()
    slug = asyncio.run(tpl.save_template("政策解读报告", "article", ["政策", "解读"], "正文结构..."))
    t = [x for x in tpl.load_templates() if x["slug"] == slug][0]
    assert t["status"] == "draft" and t["uses"] == 0, t


def test_dedup_reuses_existing():
    _setup_tmp()
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False  # 关闭语义去重，确定性走关键词 Jaccard 兜底（不连真实网络）
    try:
        s1 = asyncio.run(tpl.save_template("产品测评A", "product", ["测评", "参数", "卖点"], "结构A"))
        # 关键词高度重叠（Jaccard=2/3≈0.67≥0.6）→ 应复用 s1，不新建
        s2 = asyncio.run(tpl.save_template("产品测评B", "product", ["测评", "参数"], "结构B"))
        assert s2 == s1, (s1, s2)
        assert len(tpl.load_templates()) == 1
    finally:
        settings.embedding_enabled = old_enabled


def test_no_dedup_different_datatype():
    _setup_tmp()
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    try:
        s1 = asyncio.run(tpl.save_template("X", "product", ["测评", "参数"], "a"))
        s2 = asyncio.run(tpl.save_template("Y", "bid", ["测评", "参数"], "b"))  # 不同 data_type → 不算重复
        assert s2 != s1 and len(tpl.load_templates()) == 2
    finally:
        settings.embedding_enabled = old_enabled


def test_semantic_dedup_reuses_similar_template():
    """语义去重命中：措辞完全不同、关键词零重叠，但语义描述同一类任务 → 应复用旧 slug。

    save_template 现在先走 Curator 全链路（会真实调用 achat）；这里 mock achat 调用失败，
    模拟 Curator LLM 暂不可用时退回 _fallback_decision，验证的是该兜底路径下的语义去重
    （find_duplicate_semantic）依然能正确命中复用，不依赖真实网络/LLM 判断（不确定性）。
    """
    _setup_tmp()
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    old_is_rerank = emb.is_rerank_configured
    old_rerank = emb.rerank_scores
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test-model", [[1.0, 0.0, 0.0] for _ in texts])
    emb.is_rerank_configured = lambda: True
    emb.rerank_scores = lambda query, docs, instruct=None: [0.9] * len(docs)

    async def _boom_achat(messages, **kwargs):
        raise RuntimeError("模拟 Curator LLM 暂不可用")

    try:
        with patch("src.memory.templates.achat", new=_boom_achat):
            s1 = asyncio.run(tpl.save_template("产品测评甲", "product", ["评测", "参数"], "结构A"))
            # 关键词零重叠，但 mock 的 embedding/rerank 判定为同一类 → 应复用 s1
            s2 = asyncio.run(tpl.save_template("完全不同措辞的产品体验报告", "product", ["体验", "口碑"], "结构B"))
        assert s2 == s1, (s1, s2)
        assert len(tpl.load_templates()) == 1
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed
        emb.is_rerank_configured = old_is_rerank
        emb.rerank_scores = old_rerank


def test_semantic_dedup_falls_back_to_jaccard_when_embedding_unavailable():
    """embedding 端点不可用（返回 None）→ 退回关键词 Jaccard，不应报错或漏判。"""
    _setup_tmp()
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: None
    try:
        s1 = asyncio.run(tpl.save_template("产品测评A", "product", ["测评", "参数", "卖点"], "结构A"))
        s2 = asyncio.run(tpl.save_template("产品测评B", "product", ["测评", "参数"], "结构B"))
        assert s2 == s1, (s1, s2)
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed


def test_promote_draft_to_active():
    _setup_tmp()
    old = (settings.template_promote_uses, settings.template_promote_quality)
    settings.template_promote_uses, settings.template_promote_quality = 3, 70
    try:
        slug = asyncio.run(tpl.save_template("新闻梳理", "article", ["新闻"], "结构"))
        tpl.record_template_use(slug, 90)
        tpl.record_template_use(slug, 80)
        assert [x for x in tpl.load_templates() if x["slug"] == slug][0]["status"] == "draft"
        st = tpl.record_template_use(slug, 85)  # 第3次，avg=85≥70 → 转正
        assert st == "active", st
        t = [x for x in tpl.load_templates() if x["slug"] == slug][0]
        assert t["uses"] == 3 and t["status"] == "active"
    finally:
        settings.template_promote_uses, settings.template_promote_quality = old


def test_retire_low_quality():
    _setup_tmp()
    old = (settings.template_promote_uses, settings.template_retire_quality)
    settings.template_promote_uses, settings.template_retire_quality = 3, 50
    try:
        slug = asyncio.run(tpl.save_template("烂模板", "generic", ["X"], "结构"))
        tpl.record_template_use(slug, 30)
        tpl.record_template_use(slug, 40)
        st = tpl.record_template_use(slug, 20)  # 第3次 avg=30<50 → 淘汰
        assert st == "retired", st
        # 淘汰后不再被 match 召回
        spec = TaskSpec(intent="X 任务", data_type=DataType.GENERIC, keywords=["X"])
        assert tpl.match_template(spec) is None
    finally:
        settings.template_promote_uses, settings.template_retire_quality = old


def test_match_includes_draft_excludes_retired():
    _setup_tmp()
    slug = asyncio.run(tpl.save_template("招商报告", "generic", ["招商", "园区"], "结构"))  # draft
    spec = TaskSpec(intent="园区招商分析", data_type=DataType.GENERIC, keywords=["招商"])
    m = tpl.match_template(spec)
    assert m is not None and m["slug"] == slug, "草稿应可被召回"


def test_legacy_template_without_status_defaults_to_draft():
    """无 status 字段的旧格式模板文件，加载后应按 draft 对待（不再默认 active 绕过质量门）。"""
    d = _setup_tmp()
    (d / "legacy.md").write_text(
        "---\ntitle: 老模板\ndata_type: article\nkeywords: [旧格式]\n---\n正文内容\n",
        encoding="utf-8",
    )
    t = [x for x in tpl.load_templates() if x["slug"] == "legacy"][0]
    assert t["status"] == "draft", t


def test_record_use_on_legacy_template_keeps_draft_default():
    """无 status 字段的旧格式模板被使用一次（未达转正门槛）后，record_template_use 不应把它绕过质量门写成 active。"""
    d = _setup_tmp()
    (d / "legacy.md").write_text(
        "---\ntitle: 老模板\ndata_type: article\nkeywords: [旧格式]\n---\n正文内容\n",
        encoding="utf-8",
    )
    tpl.record_template_use("legacy", 60)  # 只调用1次，默认 template_promote_uses=3，不触发转正/淘汰
    t = [x for x in tpl.load_templates() if x["slug"] == "legacy"][0]
    assert t["status"] == "draft", t


def test_dead_zone_forces_retire():
    """draft 模板 uses 达到死区阈值、均分卡在 50~70 之间 → 强制淘汰，不再无限期停留 draft。"""
    _setup_tmp()
    old = (settings.template_promote_uses, settings.template_promote_quality,
           settings.template_retire_quality, settings.template_dead_zone_uses)
    settings.template_promote_uses = 3
    settings.template_promote_quality = 70
    settings.template_retire_quality = 50
    settings.template_dead_zone_uses = 5
    try:
        slug = asyncio.run(tpl.save_template("死区模板", "generic", ["死区测试"], "结构"))
        st = None
        for _ in range(5):  # 连续 5 次都打 60 分：avg=60，卡在 50~70 死区
            st = tpl.record_template_use(slug, 60)
        assert st == "retired", f"应在 uses=5 时强制淘汰，实际 status={st}"
    finally:
        (settings.template_promote_uses, settings.template_promote_quality,
         settings.template_retire_quality, settings.template_dead_zone_uses) = old


def test_semantic_candidates_filters_by_min_cosine():
    """_semantic_candidates 按 min_cosine 过滤明显不相关候选（Curator 场景用，find_duplicate_semantic 默认不设下限）。"""
    d = _setup_tmp()
    _write_template(d, "tpl-a", "模板甲", "product", ["关键词甲"], "结构甲")
    _write_template(d, "tpl-b", "模板乙", "product", ["关键词乙"], "结构乙")
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    settings.embedding_enabled = True

    def fake_embed(texts):
        return ("test-model", [[1.0, 0.0] if "甲" in t else [0.0, 1.0] for t in texts])

    emb.embed_texts_with_model = fake_embed
    try:
        top = tpl._semantic_candidates("product", ["关键词甲"], "查询甲", top_k=5, min_cosine=0.5)
        assert len(top) == 1 and top[0]["slug"] == "tpl-a", top
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed


def test_curate_no_candidates_returns_new_without_llm():
    """库内无同 data_type 候选 → 直接判 new，不调用 LLM。"""
    _setup_tmp()
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = True

    def _boom(messages, **kwargs):
        raise AssertionError("无候选时不应调用 achat")

    old_achat = tpl.achat
    tpl.achat = _boom
    try:
        result = asyncio.run(tpl.curate_template("新标题", "product", ["新关键词"], "新正文"))
        assert result == {"decision": "new"}
    finally:
        settings.embedding_enabled = old_enabled
        tpl.achat = old_achat


def test_curate_llm_decides_new():
    d = _setup_tmp()
    _write_template(d, "existing", "旧模板", "product", ["旧关键词"], "旧正文")
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test-model", [[1.0, 0.0] for _ in texts])
    try:
        with patch("src.memory.templates.achat", new=_fake_achat({"decision": "new"})):
            result = asyncio.run(tpl.curate_template("新标题", "product", ["新关键词"], "新正文"))
        assert result == {"decision": "new"}
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed


def test_curate_llm_decides_merge():
    d = _setup_tmp()
    _write_template(d, "existing", "旧模板", "product", ["旧关键词"], "旧正文", uses=2, quality_avg=75)
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test-model", [[1.0, 0.0] for _ in texts])
    merge_payload = {
        "decision": "merge", "slug": "existing", "title": "旧模板",
        "keywords": ["旧关键词", "新关键词"], "body": "融合正文",
    }
    try:
        with patch("src.memory.templates.achat", new=_fake_achat(merge_payload)):
            result = asyncio.run(tpl.curate_template("新标题", "product", ["新关键词"], "新正文"))
        assert result["decision"] == "merge"
        assert result["slug"] == "existing"
        assert result["body"] == "融合正文"
        assert set(result["keywords"]) == {"旧关键词", "新关键词"}
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed


def test_curate_llm_decides_discard():
    d = _setup_tmp()
    _write_template(d, "existing", "旧模板", "product", ["旧关键词"], "旧正文")
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test-model", [[1.0, 0.0] for _ in texts])
    try:
        with patch("src.memory.templates.achat", new=_fake_achat({"decision": "discard"})):
            result = asyncio.run(tpl.curate_template("新标题", "product", ["旧关键词"], "新正文"))
        assert result == {"decision": "discard"}
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed


def test_curate_merge_rejects_slug_not_in_candidates():
    """LLM 返回的 merge slug 不在候选列表里 → 视为解析失败，退回二元逻辑。"""
    d = _setup_tmp()
    _write_template(d, "existing", "旧模板", "product", ["旧关键词"], "旧正文")
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    old_is_rerank = emb.is_rerank_configured
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test-model", [[1.0, 0.0] for _ in texts])
    emb.is_rerank_configured = lambda: False  # 降级到 find_duplicate_semantic 内部的关键词 Jaccard 兜底
    bogus_payload = {"decision": "merge", "slug": "not-a-real-slug", "body": "x"}
    try:
        with patch("src.memory.templates.achat", new=_fake_achat(bogus_payload)):
            result = asyncio.run(tpl.curate_template("新标题", "product", ["旧关键词"], "新正文"))
        # 关键词完全重合（Jaccard=1.0）应判定 reuse
        assert result["decision"] == "reuse" and result["slug"] == "existing"
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed
        emb.is_rerank_configured = old_is_rerank


def test_curate_falls_back_when_candidate_retrieval_unavailable():
    """embedding 不可用 → 直接退回 _fallback_decision（不调用 LLM）。"""
    _setup_tmp()
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False

    def _boom(messages, **kwargs):
        raise AssertionError("候选召回不可用时不应调用 achat")

    old_achat = tpl.achat
    tpl.achat = _boom
    try:
        result = asyncio.run(tpl.curate_template("新标题", "product", ["关键词"], "正文"))
        assert result == {"decision": "new"}  # 空库，退回逻辑里 find_duplicate_semantic 也判 new
    finally:
        settings.embedding_enabled = old_enabled
        tpl.achat = old_achat


def test_curate_falls_back_when_llm_call_fails():
    d = _setup_tmp()
    _write_template(d, "existing", "旧模板", "product", ["旧关键词"], "旧正文")
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test-model", [[1.0, 0.0] for _ in texts])

    async def _raise(messages, **kwargs):
        raise RuntimeError("boom")

    try:
        with patch("src.memory.templates.achat", new=_raise):
            result = asyncio.run(tpl.curate_template("新标题", "product", ["旧关键词"], "新正文"))
        assert result["decision"] == "reuse" and result["slug"] == "existing"
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed


def test_save_template_discard_writes_nothing():
    """Curator 判定丢弃 → save_template 返回 None，不新建任何文件。"""
    d = _setup_tmp()
    _write_template(d, "existing", "旧模板", "product", ["旧关键词"], "旧正文")
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test-model", [[1.0, 0.0] for _ in texts])
    try:
        with patch("src.memory.templates.achat", new=_fake_achat({"decision": "discard"})):
            result = asyncio.run(tpl.save_template("新标题", "product", ["旧关键词"], "新正文"))
        assert result is None
        assert len(tpl.load_templates()) == 1  # 仍只有最初那一个，没有新增文件
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed


def test_save_template_merge_updates_body_keeps_stats_and_invalidates_cache():
    """Curator 判定合并：目标模板 body/title/keywords 更新，uses/quality_avg/status 保持不变，
    向量缓存该 slug 条目被清除。"""
    d = _setup_tmp()
    _write_template(d, "existing", "旧模板", "product", ["旧关键词"], "旧正文",
                     status="active", uses=1, quality_avg=80)
    tpl._save_vectors({"existing": {"model": "test-model", "hash": "x", "vector": [1.0, 0.0]}})
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test-model", [[1.0, 0.0] for _ in texts])
    merge_payload = {
        "decision": "merge", "slug": "existing", "title": "旧模板",
        "keywords": ["旧关键词", "新增维度"], "body": "融合后的新正文",
    }
    try:
        with patch("src.memory.templates.achat", new=_fake_achat(merge_payload)):
            result_slug = asyncio.run(tpl.save_template("新内容", "product", ["新增维度"], "新内容正文"))
        assert result_slug == "existing"
        t = [x for x in tpl.load_templates() if x["slug"] == "existing"][0]
        assert t["body"] == "融合后的新正文"
        assert set(t["keywords"]) == {"旧关键词", "新增维度"}
        assert t["uses"] == 1 and t["quality_avg"] == 80.0 and t["status"] == "active", t
        cache = tpl._load_vectors()
        assert "existing" not in cache, "合并后应清除该 slug 的向量缓存"
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed


def main():
    tests = [
        test_save_is_draft,
        test_dedup_reuses_existing,
        test_no_dedup_different_datatype,
        test_promote_draft_to_active,
        test_retire_low_quality,
        test_match_includes_draft_excludes_retired,
        test_semantic_dedup_reuses_similar_template,
        test_semantic_dedup_falls_back_to_jaccard_when_embedding_unavailable,
        test_legacy_template_without_status_defaults_to_draft,
        test_record_use_on_legacy_template_keeps_draft_default,
        test_dead_zone_forces_retire,
        test_semantic_candidates_filters_by_min_cosine,
        test_curate_no_candidates_returns_new_without_llm,
        test_curate_llm_decides_new,
        test_curate_llm_decides_merge,
        test_curate_llm_decides_discard,
        test_curate_merge_rejects_slug_not_in_candidates,
        test_curate_falls_back_when_candidate_retrieval_unavailable,
        test_curate_falls_back_when_llm_call_fails,
        test_save_template_discard_writes_nothing,
        test_save_template_merge_updates_body_keeps_stats_and_invalidates_cache,
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
