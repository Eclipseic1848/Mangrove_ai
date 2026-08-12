# 模板库/教训库自学习曲线评测 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建 `scripts/test_learning_curve.py`，用轻量确定性方式（mock LLM，真实调用 `templates.py`/`lessons.py`/`library_dedup_scanner.py` 的合并/去重/转正逻辑）验证模板库/教训库自学习机制在多轮同类任务下确实收敛/转正/不碎片化，带硬性断言，纳入常规回归。

**Architecture:** 单个新文件，按子系统分两个任务顺序交付（模板库曲线 → 教训库曲线），第三个任务补文档（本任务是 ABC 三阶段规划里 C 阶段最后一个、也是整个 ABC 规划最后一个子项目，文档需要点出"ABC 全部收尾"这一事实）。

**Tech Stack:** Python，无 pytest（项目惯例：`def test_x(): assert` + `main()` 收集 PASS/FAIL + `sys.exit`），`unittest.mock.patch`/`tempfile` 隔离，asyncio 跑异步函数。

## Global Constraints

- 不联网、不真调 LLM：所有 LLM 调用（`achat`/`embedding`/`rerank`）必须 mock 为确定性响应。
- 断言失败即 `sys.exit(1)`，与项目现有 `test_*.py` 系列回归拦截方式一致，不做纯信息性报表。
- 不改动 `templates.py`/`lessons.py`/`library_dedup_scanner.py` 任何既有逻辑，纯新增观测脚本。
- 不做 JSON 报表落盘（与 `eval_e2e.py` 的"跑一次看当时基线数值"性质不同，这是回归门槛脚本）。
- 每个测试函数用完都要在 `finally` 里还原被 monkeypatch 的全局状态（`settings.xxx`/`emb.xxx`），与项目现有 `test_template_learning.py`/`test_lesson_learning.py`/`test_library_dedup_scanner.py` 的既定写法完全一致。

---

## Task 1: 模板库自学习曲线

**Files:**
- Create: `scripts/test_learning_curve.py`

**Interfaces:**
- Consumes：`src.memory.templates`（`save_template`/`record_template_use`/`load_templates`/`TEMPLATES_DIR`）、`src.memory.embeddings`（`embed_texts_with_model`/`is_rerank_configured`/`rerank_scores`）、`src.api.library_dedup_scanner.LibraryDedupScanner`（`_dedup_pass_templates`）、`src.config.settings.settings`（`embedding_enabled`/`template_promote_uses`/`template_promote_quality`）——全部为既有函数，本任务不新增/不修改任何一个。
- Produces：`_setup_tpl_tmp()`、`_write_template(d, slug, title, data_type, keywords, body, status="active", uses=0, quality_avg=0)` 两个辅助函数（Task 2 会在同文件里加对称的教训库版本，函数名不同不冲突）；`main()` 里的 `tests` 列表（Task 2 会往这个列表追加自己的测试函数，不改动 Task 1 已加入的项）。

- [ ] **Step 1: 写文件骨架 + 第一个测试（命中收敛）**

创建 `scripts/test_learning_curve.py`：

```python
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


def main():
    tests = [
        test_template_hit_rate_converges_across_rounds,
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
```

- [ ] **Step 2: 运行确认第一个测试通过**

Run: `python scripts/test_learning_curve.py`
Expected: `1/1 通过`

- [ ] **Step 3: 加第二个测试（转正曲线）**

在 `test_template_hit_rate_converges_across_rounds` 函数后面（`main()` 函数前面）插入：

```python
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
```

同时把 `main()` 里的 `tests` 列表改为：

```python
    tests = [
        test_template_hit_rate_converges_across_rounds,
        test_template_promotion_curve,
    ]
```

- [ ] **Step 4: 运行确认两个测试都通过**

Run: `python scripts/test_learning_curve.py`
Expected: `2/2 通过`

- [ ] **Step 5: 加第三个测试（巡检去重收敛+幂等）**

在 `test_template_promotion_curve` 函数后面（`main()` 函数前面）插入：

```python
def test_template_patrol_dedup_converges_and_is_idempotent():
    """绕过 save_template 直接落盘写入3条近似重复的模板文件（模拟"曾经漏检的存量"这种
    真实历史场景），第一轮巡检应合并收敛为1条，第二轮巡检不应再产生新合并（幂等）。"""
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
            scanner = LibraryDedupScanner()
            scanned1, merged1 = asyncio.run(scanner._dedup_pass_templates())
            assert scanned1 == 3 and merged1 == 2, (scanned1, merged1)
            assert len(tpl.load_templates()) == 1, tpl.load_templates()

            _scanned2, merged2 = asyncio.run(scanner._dedup_pass_templates())
            assert merged2 == 0, "第二轮巡检不应再发现新的重复对（幂等）"
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed
        emb.is_rerank_configured = old_is_rerank
        emb.rerank_scores = old_rerank
```

同时把 `main()` 里的 `tests` 列表改为：

```python
    tests = [
        test_template_hit_rate_converges_across_rounds,
        test_template_promotion_curve,
        test_template_patrol_dedup_converges_and_is_idempotent,
    ]
```

- [ ] **Step 6: 运行确认三个测试都通过**

Run: `python scripts/test_learning_curve.py`
Expected: `3/3 通过`（本步骤会真实触发 `library_dedup_scanner.py` 内部两次 `_sleep(5秒)`（每次合并之间的等待），单次运行耗时会明显长于其它 `test_*.py` 脚本，属预期行为，与既有 `test_library_dedup_scanner.py` 的 `test_run_one_scan_merges_and_logs` 同款耗时特征）

- [ ] **Step 7: 回归确认没有影响既有测试**

Run: `python scripts/test_template_learning.py` && `python scripts/test_library_dedup_scanner.py`
Expected: 两者都全部通过（本任务只新增文件，未改动 `templates.py`/`library_dedup_scanner.py` 任何代码）

- [ ] **Step 8: Commit**

```bash
git add scripts/test_learning_curve.py
git commit -m "feat: 新增模板库自学习曲线评测（命中收敛/转正/巡检去重幂等）"
```

---

## Task 2: 教训库自学习曲线

**Files:**
- Modify: `scripts/test_learning_curve.py`

**Interfaces:**
- Consumes：Task 1 已建好的文件骨架（在其基础上追加，不改动 Task 1 已写好的模板库测试函数和辅助函数）；`src.memory.lessons`（`record_failure`/`load_lessons`/`lesson_for_analyze`/`lesson_for_planner`/`LESSONS_DIR`）、`src.conductor.task_spec`（`TaskSpec`/`DataType`）、`src.api.library_dedup_scanner.LibraryDedupScanner`（`_dedup_pass_lessons`）、`settings.lesson_promote_occurrences`——全部为既有符号，本任务不新增/不修改任何一个。
- Produces：`_setup_lesson_tmp()`、`_write_lesson(d, slug, title, data_type, keywords, body, status="active", occurrences=1)` 两个新辅助函数（与 Task 1 的模板库版本并存，命名不同不冲突）；Task 3（文档任务）不依赖本任务产出的任何代码符号。

- [ ] **Step 1: 补充 import，加第一个教训测试（命中不碎片化）**

在 `scripts/test_learning_curve.py` 顶部 import 区（`import src.memory.embeddings as emb` 那行后面）补充：

```python
import src.memory.lessons as lesson
from src.conductor.task_spec import DataType, TaskSpec
```

在 `_write_template` 函数后面（`test_template_hit_rate_converges_across_rounds` 前面）新增两个辅助函数：

```python
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
```

在 `test_template_patrol_dedup_converges_and_is_idempotent` 函数后面（`main()` 前面）新增：

```python
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
```

同时把 `main()` 里的 `tests` 列表改为：

```python
    tests = [
        test_template_hit_rate_converges_across_rounds,
        test_template_promotion_curve,
        test_template_patrol_dedup_converges_and_is_idempotent,
        test_lesson_occurrences_accumulate_without_fragmenting,
    ]
```

- [ ] **Step 2: 运行确认四个测试都通过**

Run: `python scripts/test_learning_curve.py`
Expected: `4/4 通过`

- [ ] **Step 3: 加第二个教训测试（转正+消费闭环）**

在 `test_lesson_occurrences_accumulate_without_fragmenting` 函数后面（`main()` 前面）新增：

```python
def test_lesson_promotion_and_consumption_curve():
    """occurrences 达到转正阈值后 status 应恰好转正（不早不晚）；转正后 lesson_for_analyze/
    lesson_for_planner（消费侧）都应能读到这条教训并返回含其正文的提醒——存储侧和消费侧
    都要验，不能只测存储侧。"""
    _setup_lesson_tmp()
    old_enabled = settings.embedding_enabled
    old_promote = settings.lesson_promote_occurrences
    settings.embedding_enabled = False
    settings.lesson_promote_occurrences = 3
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
            assert final["status"] == "active", final
    finally:
        settings.embedding_enabled = old_enabled
        settings.lesson_promote_occurrences = old_promote

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
        analyze_text = lesson.lesson_for_analyze(spec)
        assert "累积应对建议正文" in analyze_text, analyze_text
        planner_text = lesson.lesson_for_planner("同类失败任务二的规划阶段查询")
        assert "累积应对建议正文" in planner_text, planner_text
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed
        emb.is_rerank_configured = old_is_rerank
        emb.rerank_scores = old_rerank
```

同时把 `main()` 里的 `tests` 列表改为：

```python
    tests = [
        test_template_hit_rate_converges_across_rounds,
        test_template_promotion_curve,
        test_template_patrol_dedup_converges_and_is_idempotent,
        test_lesson_occurrences_accumulate_without_fragmenting,
        test_lesson_promotion_and_consumption_curve,
    ]
```

- [ ] **Step 4: 运行确认五个测试都通过**

Run: `python scripts/test_learning_curve.py`
Expected: `5/5 通过`

- [ ] **Step 5: 加第三个教训测试（巡检去重收敛+幂等）**

在 `test_lesson_promotion_and_consumption_curve` 函数后面（`main()` 前面）新增：

```python
def test_lesson_patrol_dedup_converges_and_is_idempotent():
    """镜像模板库场景：绕过 record_failure 直接落盘写入3条近似重复的教训草稿文件，
    第一轮巡检应合并收敛为1条，第二轮巡检不应再产生新合并（幂等）。"""
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
            scanner = LibraryDedupScanner()
            scanned1, merged1 = asyncio.run(scanner._dedup_pass_lessons())
            assert scanned1 == 3 and merged1 == 2, (scanned1, merged1)
            assert len(lesson.load_lessons()) == 1, lesson.load_lessons()

            _scanned2, merged2 = asyncio.run(scanner._dedup_pass_lessons())
            assert merged2 == 0, "第二轮巡检不应再发现新的重复对（幂等）"
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed
        emb.is_rerank_configured = old_is_rerank
        emb.rerank_scores = old_rerank
```

同时把 `main()` 里的 `tests` 列表改为：

```python
    tests = [
        test_template_hit_rate_converges_across_rounds,
        test_template_promotion_curve,
        test_template_patrol_dedup_converges_and_is_idempotent,
        test_lesson_occurrences_accumulate_without_fragmenting,
        test_lesson_promotion_and_consumption_curve,
        test_lesson_patrol_dedup_converges_and_is_idempotent,
    ]
```

- [ ] **Step 6: 运行确认全部六个测试都通过**

Run: `python scripts/test_learning_curve.py`
Expected: `6/6 通过`

- [ ] **Step 7: 回归确认没有影响既有测试**

Run: `python scripts/test_lesson_learning.py` && `python scripts/test_library_dedup_scanner.py` && `python scripts/test_template_learning.py`
Expected: 全部通过（本任务只追加代码，未改动 `lessons.py`/`library_dedup_scanner.py`/`templates.py` 任何逻辑）

- [ ] **Step 8: Commit**

```bash
git add scripts/test_learning_curve.py
git commit -m "feat: 新增教训库自学习曲线评测（命中不碎片化/转正消费/巡检去重幂等）"
```

---

## Task 3: 文档同步

**Files:**
- Modify: `README_AGENT.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes：Task 1、Task 2 的产出（本任务只写文档，不改代码）。
- Produces：无（终结任务，同时是整个 ABC 三阶段规划的收尾）。

- [ ] **Step 1: README_AGENT.md 新增章节**

在 `README_AGENT.md` 现有的 `## 15.9 模板库权限收紧 + 巡检开关前端化（2026-07-10 新增）` 章节之后，插入新章节 `## 15.10 模板库/教训库自学习曲线评测（2026-07-10 新增，C 阶段收尾/ABC 三阶段全部交付）`，内容需包含：
- 背景：既有 `eval_e2e.py`（6个黄金任务真实全链路单次达标检测）/单元测试（`test_template_learning.py` 等孤立场景确定性单测）都没有覆盖"自学习机制本身随时间是否真的在起作用"这个维度；B2 阶段上线当天真实暴露过一次这类"曲线异常"（教训语义错配导致 20 条近乎相同教训从未合并）。
- 实现要点：新建 `scripts/test_learning_curve.py`，mock LLM 但真实调用 `templates.py`/`lessons.py`/`library_dedup_scanner.py` 的合并/去重/转正逻辑，注入合成的同类任务序列，6 个场景：模板库命中收敛/转正曲线/巡检去重收敛+幂等，教训库命中不碎片化（直接对应 B2 那个 bug 的回归防护）/转正+消费闭环/巡检去重收敛+幂等。带硬性断言，纳入常规回归（不像 `eval_e2e.py` 那样只输出报表）。
- 回归：`scripts/test_learning_curve.py` 6/6 通过；`test_template_learning.py`/`test_lesson_learning.py`/`test_library_dedup_scanner.py` 保持通过（纯新增观测脚本，未改动任何既有逻辑）。
- 收尾说明：至此模板库/教训库自学习闭环优化的 A/B1/B2/C 四阶段规划（C 阶段拆成教训库前端管理页面、定时巡检、评测基线扩展三个子项目）全部交付完成。

具体文字表述可参考 `## 15.9` 章节的写作风格（背景→实现要点→回归结果的结构）。

- [ ] **Step 2: AGENTS.md 新增条目**

在 `AGENTS.md` 中找到"模板库/教训库定时巡检"那一条描述所在的段落附近，新增一条：

```
- 模板库/教训库自学习曲线评测 `scripts/test_learning_curve.py`（mock LLM，真实跑合并/去重/转正逻辑，验证多轮同类任务下命中收敛/转正/巡检去重是否真的收敛，带硬性断言纳入常规回归）；
```

- [ ] **Step 3: Commit**

```bash
git add README_AGENT.md AGENTS.md
git commit -m "docs: 模板库/教训库自学习曲线评测文档同步（ABC三阶段全部交付）"
```

---

## 验证

1. `python scripts/test_learning_curve.py` → 6/6 通过
2. 既有回归全部保持通过：`test_template_learning.py`、`test_lesson_learning.py`、`test_library_dedup_scanner.py`、`test_embeddings.py`
3. `README_AGENT.md`/`AGENTS.md` 文档同步到位，明确标注 ABC 三阶段规划全部交付完成
