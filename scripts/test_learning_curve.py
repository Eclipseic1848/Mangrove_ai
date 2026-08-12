#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""模板库/教训库自学习曲线评测：多轮合成任务观察收敛/转正/巡检去重行为，带硬性断言。

与 test_template_learning.py/test_lesson_learning.py（单轮孤立场景单测）不同，这里验证的是
"同类任务反复出现多轮后，自学习机制本身是否真的在收敛/转正/去重"——这类"曲线异常"只有连续
多轮观察库存变化才能发现（B2 阶段上线当天真实暴露过一次：record_failure 语义错配导致
20 条几乎相同的教训永远各建各的、从未合并，见 README_AGENT.md 相关章节）。

运行：python scripts/test_learning_curve.py
不联网：mock LLM 调用（achat/embedding/rerank），真实调用 templates.py/lessons.py/
library_dedup_scanner.py 的合并/去重/转正逻辑。
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
import src.memory.templates as tpl
import src.memory.embeddings as emb
import src.memory.lessons as lesson
from src.conductor.task_spec import DataType, TaskSpec
from src.api.library_dedup_scanner import LibraryDedupScanner


def _setup_tpl_tmp():
    d = Path(tempfile.mkdtemp(prefix="mg_tpl_curve_"))
    tpl.TEMPLATES_DIR = d
    return d


def _write_template(d, slug, title, data_type, keywords, body, status="active", uses=0, quality_avg=0):
    front = yaml.safe_dump(
        {"title": title, "data_type": data_type, "keywords": keywords,
         "status": status, "uses": uses, "quality_avg": quality_avg},
        allow_unicode=True, sort_keys=False,
    ).strip()
    (d / f"{slug}.md").write_text(f"---\n{front}\n---\n{body}\n", encoding="utf-8")


def _setup_lesson_tmp():
    d = Path(tempfile.mkdtemp(prefix="mg_lesson_curve_"))
    lesson.LESSONS_DIR = d
    return d


def _write_lesson(d, slug, title, data_type, keywords, body, status="active", occurrences=1):
    front = yaml.safe_dump(
        {"title": title, "data_type": data_type, "keywords": keywords,
         "status": status, "occurrences": occurrences},
        allow_unicode=True, sort_keys=False,
    ).strip()
    (d / f"{slug}.md").write_text(f"---\n{front}\n---\n{body}\n", encoding="utf-8")


def test_template_hit_rate_converges_across_rounds():
    """连续8轮"同一 data_type + 高度重叠关键词"的合成任务喂给 save_template，
    应始终复用第1轮建的草稿，模板总数收敛在1条，不随轮次线性增长到8条。"""
    _setup_tpl_tmp()
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False  # 关闭语义去重，确定性走关键词 Jaccard 兜底
    try:
        first_slug = None
        for i in range(8):
            slug = asyncio.run(tpl.save_template(
                f"产品测评{i}", "product", ["测评", "参数", "卖点"], f"结构{i}",
            ))
            if first_slug is None:
                first_slug = slug
            assert slug == first_slug, f"第{i + 1}轮未复用第1轮的模板，新建/复用了 {slug}"
            assert len(tpl.load_templates()) == 1, f"第{i + 1}轮后库存应仍为1条，实际 {len(tpl.load_templates())}"
    finally:
        settings.embedding_enabled = old_enabled


def test_template_promotion_curve():
    """同一模板被反复使用（record_template_use）直到达到转正阈值，status 应恰好在该轮
    转正，不早不晚。"""
    _setup_tpl_tmp()
    old_enabled = settings.embedding_enabled
    old = (settings.template_promote_uses, settings.template_promote_quality)
    settings.embedding_enabled = False
    settings.template_promote_uses, settings.template_promote_quality = 5, 70
    try:
        slug = asyncio.run(tpl.save_template("新闻梳理", "article", ["新闻"], "结构"))
        for round_i in range(1, 5):
            tpl.record_template_use(slug, 90)
            t = [x for x in tpl.load_templates() if x["slug"] == slug][0]
            assert t["status"] == "draft", f"第{round_i}轮不应提前转正，实际 status={t['status']}"
        st = tpl.record_template_use(slug, 90)  # 第5轮，uses 达到阈值
        assert st == "active", f"第5轮应转正，实际 status={st}"
    finally:
        settings.embedding_enabled = old_enabled
        settings.template_promote_uses, settings.template_promote_quality = old


def test_template_patrol_dedup_converges_and_is_idempotent():
    """绕过 save_template 直接落盘写入3条近似重复的模板文件（模拟"曾经漏检的存量"这种
    真实历史场景）。因 library_dedup_scanner._dedup_pass_templates 的 merged_slugs 守卫
    （已合并过的 survivor 本轮不再作为新的合并目标），3条互相相似的模板收敛需要两轮：
    第一轮 3→2（merged=1），第二轮 2→1（merged=1）；第三轮巡检不应再产生新合并（幂等，
    此时只剩1条模板，找不到候选）。"""
    d = _setup_tpl_tmp()
    _write_template(d, "tpl-a", "模板甲", "product", ["评测"], "正文甲",
                     status="active", uses=5, quality_avg=80)
    _write_template(d, "tpl-b", "模板乙（措辞不同）", "product", ["体验"], "正文乙",
                     status="draft", uses=0, quality_avg=0)
    _write_template(d, "tpl-c", "模板丙（措辞也不同）", "product", ["口碑"], "正文丙",
                     status="draft", uses=0, quality_avg=0)

    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    old_is_rerank = emb.is_rerank_configured
    old_rerank = emb.rerank_scores
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test-model", [[1.0, 0.0] for _ in texts])
    emb.is_rerank_configured = lambda: True
    emb.rerank_scores = lambda query, docs, instruct=None: [0.9] * len(docs)

    merge_payload = {"title": "模板甲", "keywords": ["评测", "体验", "口碑"], "body": "融合正文"}

    async def _fake_achat(messages, **kwargs):
        return json.dumps(merge_payload, ensure_ascii=False)

    try:
        with patch("src.memory.templates.achat", new=_fake_achat):
            async def _run_dedup_passes():
                scanner = LibraryDedupScanner()
                async def _no_sleep(_seconds):
                    return None
                scanner._sleep = _no_sleep
                scanned1, merged1, _details1 = await scanner._dedup_pass_templates()
                assert scanned1 == 3 and merged1 == 1, (scanned1, merged1)
                assert len(tpl.load_templates()) == 2, tpl.load_templates()

                _scanned2, merged2, _details2 = await scanner._dedup_pass_templates()
                assert merged2 == 1, "第二轮巡检应再发现一对重复并合并，最终收敛为1条"
                assert len(tpl.load_templates()) == 1, "两轮巡检后应收敛为1条"

                _scanned3, merged3, _details3 = await scanner._dedup_pass_templates()
                assert merged3 == 0, "第三轮巡检不应再发现新的重复对（幂等）"

            asyncio.run(_run_dedup_passes())
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed
        emb.is_rerank_configured = old_is_rerank
        emb.rerank_scores = old_rerank


def test_lesson_occurrences_accumulate_without_fragmenting():
    """连续5轮"同一失败症状模式"的合成任务喂给 record_failure，最终该症状对应的教训
    文件数应 == 1、occurrences == 5——直接对应 B2 上线当天真实修复过的那个 bug（原始意图
    vs 已蒸馏教训语义不同质导致永不合并），防止同一根因回归。"""
    _setup_lesson_tmp()
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False  # 关闭语义召回，确定性走关键词 Jaccard 兜底
    payload = {"title": "同一失败模式", "keywords": ["同一失败模式"], "body": "固定应对建议正文"}

    async def _fake_achat(messages, **kwargs):
        return json.dumps(payload, ensure_ascii=False)

    try:
        with patch("src.memory.lessons.achat", new=_fake_achat):
            for round_i in range(5):
                asyncio.run(lesson.record_failure(
                    "同类失败任务", "comment", ["同一失败模式"], "未采集到有效数据",
                ))
                lessons = lesson.load_lessons()
                assert len(lessons) == 1, f"第{round_i + 1}轮后应仍只有1条教训，实际 {len(lessons)}"
        final = lesson.load_lessons()[0]
        assert final["occurrences"] == 5, final
    finally:
        settings.embedding_enabled = old_enabled


def test_lesson_promotion_and_consumption_curve():
    """重复失败只累计 occurrences；经一次有效性反馈后才转正并进入消费侧。"""
    _setup_lesson_tmp()
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    payload = {"title": "同一失败模式二", "keywords": ["同一失败模式二"], "body": "累积应对建议正文"}

    async def _fake_achat(messages, **kwargs):
        return json.dumps(payload, ensure_ascii=False)

    try:
        with patch("src.memory.lessons.achat", new=_fake_achat):
            for round_i in range(1, 3):
                asyncio.run(lesson.record_failure(
                    "同类失败任务二", "comment", ["同一失败模式二"], "未采集到有效数据",
                ))
                cur = lesson.load_lessons()[0]
                assert cur["status"] == "draft", f"第{round_i}轮不应提前转正，实际 status={cur['status']}"
            asyncio.run(lesson.record_failure(
                "同类失败任务二", "comment", ["同一失败模式二"], "未采集到有效数据",
            ))  # 第3轮，occurrences 达到阈值
            final = lesson.load_lessons()[0]
            assert final["status"] == "draft", "仅重复失败不得自动转正"
            assert lesson.record_lesson_helped(final["slug"])
            final = lesson.load_lessons()[0]
            assert final["status"] == "active", final
    finally:
        settings.embedding_enabled = old_enabled

    # 消费侧：转正后 lesson_for_analyze/lesson_for_planner 都应命中并返回含正文的提醒
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    old_is_rerank = emb.is_rerank_configured
    old_rerank = emb.rerank_scores
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test-model", [[1.0, 0.0] for _ in texts])
    emb.is_rerank_configured = lambda: True
    emb.rerank_scores = lambda query, docs, instruct=None: [0.9] * len(docs)
    try:
        spec = TaskSpec(intent="同类失败任务二", data_type=DataType.COMMENT, keywords=["同一失败模式二"])
        analyze_text, active_slug = lesson.lesson_for_analyze(spec)
        assert "累积应对建议正文" in analyze_text, analyze_text
        assert active_slug == final["slug"]
        planner_text = lesson.lesson_for_planner("同类失败任务二的规划阶段查询")
        assert "累积应对建议正文" in planner_text, planner_text
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed
        emb.is_rerank_configured = old_is_rerank
        emb.rerank_scores = old_rerank


def test_lesson_patrol_dedup_converges_and_is_idempotent():
    """镜像模板库场景：绕过 record_failure 直接落盘写入3条近似重复的教训草稿文件。因
    library_dedup_scanner._dedup_pass_lessons 的 merged_slugs 守卫（已合并过的 survivor
    本轮不再作为新的合并目标），3条互相相似的教训收敛需要两轮：第一轮 3→2（merged=1），
    第二轮 2→1（merged=1）；第三轮巡检不应再产生新合并（幂等，此时只剩1条教训，找不到候选）。"""
    d = _setup_lesson_tmp()
    _write_lesson(d, "lsn-a", "教训甲", "comment", ["小众品牌"], "正文甲",
                  status="active", occurrences=3)
    _write_lesson(d, "lsn-b", "教训乙（措辞不同）", "comment", ["抖音"], "正文乙",
                  status="draft", occurrences=1)
    _write_lesson(d, "lsn-c", "教训丙（措辞也不同）", "comment", ["快手"], "正文丙",
                  status="draft", occurrences=1)

    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    old_is_rerank = emb.is_rerank_configured
    old_rerank = emb.rerank_scores
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test-model", [[1.0, 0.0] for _ in texts])
    emb.is_rerank_configured = lambda: True
    emb.rerank_scores = lambda query, docs, instruct=None: [0.9] * len(docs)

    merge_payload = {"title": "教训甲", "keywords": ["小众品牌", "抖音", "快手"], "body": "融合正文"}

    async def _fake_achat(messages, **kwargs):
        return json.dumps(merge_payload, ensure_ascii=False)

    try:
        with patch("src.memory.lessons.achat", new=_fake_achat):
            async def _run_dedup_passes():
                scanner = LibraryDedupScanner()
                async def _no_sleep(_seconds):
                    return None
                scanner._sleep = _no_sleep
                scanned1, merged1, _details1 = await scanner._dedup_pass_lessons()
                assert scanned1 == 3 and merged1 == 1, (scanned1, merged1)
                assert len(lesson.load_lessons()) == 2, lesson.load_lessons()

                _scanned2, merged2, _details2 = await scanner._dedup_pass_lessons()
                assert merged2 == 1, "第二轮巡检应再发现一对重复并合并，最终收敛为1条"
                assert len(lesson.load_lessons()) == 1, "两轮巡检后应收敛为1条"

                _scanned3, merged3, _details3 = await scanner._dedup_pass_lessons()
                assert merged3 == 0, "第三轮巡检不应再发现新的重复对（幂等）"

            asyncio.run(_run_dedup_passes())
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed
        emb.is_rerank_configured = old_is_rerank
        emb.rerank_scores = old_rerank


def main():
    tests = [
        test_template_hit_rate_converges_across_rounds,
        test_template_promotion_curve,
        test_template_patrol_dedup_converges_and_is_idempotent,
        test_lesson_occurrences_accumulate_without_fragmenting,
        test_lesson_promotion_and_consumption_curve,
        test_lesson_patrol_dedup_converges_and_is_idempotent,
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
