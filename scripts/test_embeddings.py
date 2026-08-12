#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""模板 embedding 语义召回单元测试（方案A）。

mock embed_texts，不真连端点。覆盖：余弦、语义命中(关键词不重叠也能召回)、端点失败回退关键词、关闭时走关键词、缓存生成。
运行：python scripts/test_embeddings.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import settings
from src.conductor.task_spec import DataType, TaskSpec
import src.memory.embeddings as emb
import src.memory.templates as tpl


def _setup_tmp():
    d = Path(tempfile.mkdtemp(prefix="mg_emb_"))
    tpl.TEMPLATES_DIR = d
    return d


def _topic_vec(text: str):
    """确定性"伪 embedding"：按主题词给正交向量（仅测试用）。"""
    t = text
    if any(w in t for w in ("新闻", "要闻", "时事")):
        return [1.0, 0.0, 0.0]
    if any(w in t for w in ("招标", "标讯", "投标")):
        return [0.0, 1.0, 0.0]
    return [0.0, 0.0, 1.0]


def _mock_embed(texts):
    return [_topic_vec(t) for t in texts]


def _mock_embed_with_model(texts):
    """适配新 API embed_texts_with_model：返回 (model_name, vectors)。"""
    return ("test-model", _mock_embed(texts))


def test_cosine():
    assert abs(emb.cosine([1, 0], [1, 0]) - 1.0) < 1e-9
    assert abs(emb.cosine([1, 0], [0, 1])) < 1e-9
    assert emb.cosine([], [1]) == 0.0


def test_semantic_recall_without_keyword_overlap():
    """语义命中：查询与模板关键词无字面重叠，仍能按语义召回正确模板。"""
    _setup_tmp()
    old_enabled = settings.embedding_enabled
    # 建立固定测试库存时禁用真实 embedding/Curator，避免读取开发机端点配置。
    settings.embedding_enabled = False
    asyncio.run(tpl.save_template("新闻事件梳理", "generic", ["新闻", "时事"], "新闻结构"))
    asyncio.run(tpl.save_template("招投标扫标", "generic", ["招标", "标讯"], "扫标结构"))
    old_embed, old_embed2 = emb.embed_texts, emb.embed_texts_with_model
    settings.embedding_enabled = True
    emb.embed_texts_with_model = _mock_embed_with_model  # 新 API
    try:
        # 查询用"要闻"——不在任何模板关键词里（关键词匹配会 miss），但语义属新闻
        spec = TaskSpec(intent="今日要闻速递汇总", data_type=DataType.GENERIC, keywords=[])
        # 先确认关键词匹配确实 miss
        assert tpl._match_keyword(spec) is None
        m = tpl.match_template(spec)
        assert m is not None and "新闻" in m["title"], m
        # 向量缓存已生成
        assert tpl._vectors_path().exists()
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts = old_embed
        emb.embed_texts_with_model = old_embed2


def test_fallback_to_keyword_when_endpoint_fails():
    """端点不可用（embed 返回 None）→ 回退关键词匹配。"""
    _setup_tmp()
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    asyncio.run(tpl.save_template("招投标扫标", "generic", ["招标", "标讯"], "扫标结构"))
    old_embed, old_embed2 = emb.embed_texts, emb.embed_texts_with_model
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: None  # 模拟端点失败
    try:
        spec = TaskSpec(intent="帮我做招标分析", data_type=DataType.GENERIC, keywords=["招标"])
        m = tpl.match_template(spec)
        assert m is not None and "招标" in m["keywords"], m  # 关键词兜底命中
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts = old_embed
        emb.embed_texts_with_model = old_embed2


def test_disabled_uses_keyword():
    """未启用 embedding → 直接关键词路径（不调 embed）。"""
    _setup_tmp()
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    asyncio.run(tpl.save_template("招投标扫标", "generic", ["招标"], "扫标结构"))

    def _boom(texts):
        raise AssertionError("未启用时不应调用 embed_texts")

    old_embed = emb.embed_texts
    emb.embed_texts = _boom
    try:
        spec = TaskSpec(intent="招标分析", data_type=DataType.GENERIC, keywords=["招标"])
        assert tpl.match_template(spec) is not None
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts = old_embed


def main():
    tests = [
        test_cosine,
        test_semantic_recall_without_keyword_overlap,
        test_fallback_to_keyword_when_endpoint_fails,
        test_disabled_uses_keyword,
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
