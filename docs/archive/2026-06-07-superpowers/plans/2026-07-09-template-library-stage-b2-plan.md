# 模板库自学习闭环优化 B2 阶段（教训分流 lesson channel）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让"采集失败"的任务不再是死胡同——把失败信息蒸馏成通用化的"教训"，沉淀到独立的 `data/lessons/` 目录，在 planner（采集前）与 analyze（分析时）两个节点自动召回注入，帮助系统提前调整策略/约束报告措辞。

**Architecture:** 新增 `src/memory/lessons.py`（结构参照 `src/memory/templates.py` 但更简单：无 discard 三态，只有"新建 draft / 命中已有则合并蒸馏+计数"二态）。`checker.py` 判定 `_looks_like_collection_failure()` 为真时调用 `record_failure()`；`planner.py`/`analyze.py` 各自在拼装 system prompt 时调用 `lesson_for_planner()`/`lesson_for_analyze()`。全程复用 `src/memory/embeddings.py` 现有 embedding/rerank 基础设施做语义召回，语义不可用时创建侧退关键词 Jaccard、消费侧直接跳过注入。

**Tech Stack:** Python 3.13、PyYAML（frontmatter）、现有 `src/memory/embeddings.py`（httpx 调 OpenAI 兼容 embedding/rerank 端点）、`src/llm.achat`。

## Global Constraints

- 新增配置项（`src/config/settings.py`）：`lesson_learning_enabled: bool = True`、`lesson_promote_occurrences: int = 2`、`lesson_candidate_min_cosine: float = 0.3`。
- 复用现有配置，不新增重复项：Jaccard 兜底阈值复用 `settings.template_dedup_threshold`（默认 0.6）；语义精判阈值复用 `settings.template_dedup_rerank_threshold`（默认 0.7）。
- 存储目录 `LESSONS_DIR = PROJECT_ROOT / "data" / "lessons"`，与 `data/templates/` 平行、彻底分开；一条教训一个 `.md` 文件，frontmatter 字段固定为 `title`/`data_type`/`keywords`/`status`（`draft`|`active`，无 `retired`）/`occurrences`。
- **降级策略不对称**：创建/合并判重（`find_similar_lesson`，`checker.py` 调用）语义不可用时退回关键词 Jaccard；消费侧注入（`find_active_lesson`，`planner.py`/`analyze.py` 调用）语义不可用时直接返回 `None`（不做关键词兜底），二者不可混用。
- 不引入 `_vectors.json` 式的向量缓存（模板库为效率优化引入的机制）——教训数量级远小于模板，每次现算 embedding 即可，保持 `lessons.py` 比 `templates.py` 更简单。
- 不做教训退役/淘汰、不做人工审核入口、不做前端管理页面、不改变 `_looks_like_collection_failure()` 本身的判定逻辑、不改变模板库（A/B1 阶段）任何已有行为——这些均已在 spec 中明确排除，任何任务都不应触碰。
- 测试运行方式沿用项目惯例：无 pytest 框架，`def test_x(): assert ...` + `main()` 收集 PASS/FAIL 并 `sys.exit(1 if failed else 0)`，用 `python scripts/xxx.py` 直接跑。

---

## Task 1: 教训存储与语义召回基础

**Files:**
- Create: `src/memory/lessons.py`
- Modify: `src/config/settings.py:135`（在 `template_curator_candidate_min_cosine` 之后插入新配置）
- Test: `scripts/test_lesson_learning.py`（新建）

**Interfaces:**
- Consumes：`src.config.settings.settings`（新增的 3 个配置项）、`src.memory.embeddings`（`embed_texts_with_model`/`cosine`/`is_rerank_configured`/`rerank_scores`，均为已有函数，签名不变）、`src.memory._frontmatter`（`parse_frontmatter`/`FrontmatterError`，已有）。
- Produces：`load_lessons() -> List[Dict]`（每条含 `slug/title/data_type/keywords/body/status/occurrences`）、`find_similar_lesson(data_type: Optional[str], keywords: List[str], intent: str) -> Optional[Dict]`（候选含 draft+active，语义不可用退回关键词 Jaccard，供 Task 2 的 `record_failure` 使用）、`find_active_lesson(data_type: Optional[str], keywords: List[str], intent: str) -> Optional[Dict]`（候选只含 active，语义不可用直接返回 `None`，供 Task 3 的 `lesson_for_analyze`/`lesson_for_planner` 使用）。`LESSONS_DIR: Path` 模块级常量供测试重定向。

- [ ] **Step 1: 新增配置项**

在 `src/config/settings.py` 第 135 行（`template_curator_candidate_min_cosine` 那一行）之后插入：

```python
    # ===== 失败教训分流（阶段B2：lesson channel）=====
    # 任务被判定"采集失败"时（见 checker.py 的 _looks_like_collection_failure），不再是死胡同——
    # 蒸馏成通用化的教训存到 data/lessons/，下次同类任务在 planner/analyze 节点提前注入提醒。
    lesson_learning_enabled: bool = Field(default=True, description="是否启用失败教训的自学习沉淀")
    lesson_promote_occurrences: int = Field(default=2, description="草稿教训累计命中该次数后转正(active)，开始被 planner/analyze 注入")
    lesson_candidate_min_cosine: float = Field(default=0.3, description="教训候选召回的余弦相似度下限，低于此值不纳入精判（避免明显不相关内容干扰）")
```

- [ ] **Step 2: 写失败的测试**

创建 `scripts/test_lesson_learning.py`（此时 `src/memory/lessons.py` 还不存在，`import src.memory.lessons as lesson` 会失败）：

```python
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


def test_find_active_lesson_excludes_draft():
    d = _setup_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["小众品牌"], "正文甲", status="draft", occurrences=1)
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    try:
        hit = lesson.find_active_lesson("comment", ["小众品牌"], "抖音小众品牌评论")
        assert hit is None, "draft 教训不应被消费侧召回"
    finally:
        settings.embedding_enabled = old_enabled


def test_find_active_lesson_returns_none_when_embedding_unavailable():
    """消费侧注入场景：语义不可用时直接放弃，不做关键词兜底（与创建侧行为不同）。"""
    d = _setup_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["小众品牌", "抖音"], "正文甲", status="active", occurrences=2)
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: None
    try:
        hit = lesson.find_active_lesson("comment", ["小众品牌", "抖音"], "任意意图文本")
        assert hit is None, "消费侧语义不可用应直接放弃，不应退回关键词匹配"
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed


def main():
    tests = [
        test_find_similar_lesson_filters_by_data_type,
        test_find_similar_lesson_semantic_hit,
        test_find_similar_lesson_falls_back_to_jaccard_when_embedding_unavailable,
        test_find_active_lesson_excludes_draft,
        test_find_active_lesson_returns_none_when_embedding_unavailable,
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

- [ ] **Step 3: 运行测试确认失败**

Run: `E:/python3.13/python.exe scripts/test_lesson_learning.py`
Expected: `ModuleNotFoundError: No module named 'src.memory.lessons'`（`lessons.py` 尚未创建）

- [ ] **Step 4: 写 `src/memory/lessons.py`（含语义召回，先不含蒸馏/写入，那是 Task 2）**

```python
"""
失败教训的自学习沉淀（阶段B2：教训分流 lesson channel）。

当任务被判定为"采集失败/数据不足"时（见 checker.py 的 _looks_like_collection_failure），
不再是死胡同——把这类失败的教训（这类任务容易踩的坑+应对建议）蒸馏沉淀到 data/lessons/，
下次同类任务在 planner/analyze 节点提前注入提醒，帮助系统调整策略/约束报告措辞。

与 data/templates/（报告结构自学习）彻底分开维护：教训描述的是"失败模式"，不是"报告结构"。
存储格式（一条一文件）：

    ---
    title: 抖音小众品牌评论采集不足
    data_type: comment
    keywords: [小众品牌, 抖音]
    status: draft
    occurrences: 1
    ---
    <正文：通用化的应对建议>

不引入模板库那样的向量缓存文件——教训数量级远小于模板，每次现算 embedding 即可。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from src.config.settings import PROJECT_ROOT
from src.memory._frontmatter import FrontmatterError, parse_frontmatter

logger = logging.getLogger(__name__)

LESSONS_DIR = PROJECT_ROOT / "data" / "lessons"

# 教训判重/召回场景的 rerank instruct："是不是同一类容易失败的场景"，与模板召回/去重的判据不同。
_LESSON_RERANK_INSTRUCT = "判断这两条教训是否描述同一类容易采集失败的任务场景（措辞不同但触发条件相同即算同一类）"


def _slugify(text: str) -> str:
    """生成文件名 slug：保留中英文/数字，空白转连字符，限长。"""
    text = (text or "lesson").strip()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^\w-]", "", text)
    return text[:40] or "lesson"


def load_lessons() -> List[Dict]:
    """加载 data/lessons/*.md，返回 [{slug,title,data_type,keywords,body,status,occurrences}]；
    无 frontmatter 或解析失败的跳过。"""
    out: List[Dict] = []
    if not LESSONS_DIR.exists():
        return out
    for p in sorted(LESSONS_DIR.glob("*.md")):
        try:
            raw = p.read_text(encoding="utf-8")
        except Exception:
            continue
        try:
            parsed = parse_frontmatter(raw)
        except FrontmatterError:
            logger.warning("教训 frontmatter 解析失败：%s", p.name)
            continue
        if parsed is None:
            continue
        meta, body = parsed
        if not body:
            continue
        kws = meta.get("keywords") or []
        if isinstance(kws, str):
            kws = [kws]
        out.append({
            "slug": p.stem,
            "title": str(meta.get("title") or p.stem),
            "data_type": str(meta.get("data_type") or "").strip().lower(),
            "keywords": [str(k).strip() for k in kws if str(k).strip()],
            "body": body,
            "status": str(meta.get("status") or "draft").strip().lower(),
            "occurrences": int(meta.get("occurrences") or 0),
        })
    return out


def _lesson_text(t: Dict) -> str:
    """教训的语义表示：标题 + 关键词。"""
    return (t.get("title") or "") + " " + " ".join(t.get("keywords") or [])


def _jaccard(a: List[str], b: List[str]) -> float:
    """两组关键词的 Jaccard 相似度（忽略大小写/空白）。"""
    sa = {k.strip().lower() for k in (a or []) if k and k.strip()}
    sb = {k.strip().lower() for k in (b or []) if k and k.strip()}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _match_keyword(data_type: Optional[str], keywords: List[str], statuses: Tuple[str, ...]) -> Optional[Dict]:
    """关键词 Jaccard 兜底：同 data_type（data_type 为空则不限）、关键词重叠度最高且达阈值者命中。"""
    from src.config.settings import settings

    dt = (data_type or "").lower()
    best: Optional[Dict] = None
    best_sim = 0.0
    for t in load_lessons():
        if t["status"] not in statuses or (dt and t["data_type"] != dt):
            continue
        sim = _jaccard(keywords, t["keywords"])
        if sim >= settings.template_dedup_threshold and sim > best_sim:
            best, best_sim = t, sim
    return best


def _semantic_match(
    data_type: Optional[str], keywords: List[str], intent: str, statuses: Tuple[str, ...],
) -> Tuple[bool, Optional[Dict]]:
    """语义召回单条最相似的教训。返回 (是否完成了确定性的语义判断, 命中或None)。

    第一个返回值为 False 表示"语义端点不可用/rerank 未配置/调用失败"——此时无法给出确定性判断，
    调用方各自决定降级策略（find_similar_lesson 退回关键词 Jaccard，find_active_lesson 直接放弃）。
    第一个返回值为 True 时，第二个值就是最终结论（含"确实没有相关教训"的 None）。
    """
    from src.config.settings import settings
    from . import embeddings as emb

    if not settings.embedding_enabled:
        return False, None

    dt = (data_type or "").lower()
    cands = [t for t in load_lessons() if t["status"] in statuses and (not dt or t["data_type"] == dt)]
    if not cands:
        return True, None  # 无候选，无需调用 embedding 也能确定"没有"

    query_text = (intent or "") + " " + " ".join(keywords or [])
    got = emb.embed_texts_with_model([query_text])
    if not got or not got[1]:
        return False, None
    model, qvec = got[0], got[1][0]

    got2 = emb.embed_texts_with_model([_lesson_text(t) for t in cands])
    if not got2 or got2[0] != model or len(got2[1]) != len(cands):
        return False, None

    scored = sorted(
        ((t, emb.cosine(qvec, v)) for t, v in zip(cands, got2[1])),
        key=lambda x: x[1], reverse=True,
    )
    top = [t for t, sim in scored if sim >= settings.lesson_candidate_min_cosine][:3]
    if not top:
        return True, None

    if not emb.is_rerank_configured():
        return False, None  # 无 rerank 精判能力，交给调用方的降级策略

    rscores = emb.rerank_scores(query_text, [_lesson_text(t) for t in top], instruct=_LESSON_RERANK_INSTRUCT)
    if not rscores:
        return False, None

    best_i = max(range(len(rscores)), key=rscores.__getitem__)
    if rscores[best_i] >= settings.template_dedup_rerank_threshold:
        return True, top[best_i]
    return True, None


def find_similar_lesson(data_type: Optional[str], keywords: List[str], intent: str) -> Optional[Dict]:
    """创建/合并判重用（checker.py 的 record_failure 调用）：候选含 draft+active。
    语义不可用时退回关键词 Jaccard 判重，不致瘫。"""
    statuses: Tuple[str, ...] = ("draft", "active")
    ok, result = _semantic_match(data_type, keywords, intent, statuses)
    if ok:
        return result
    return _match_keyword(data_type, keywords, statuses)


def find_active_lesson(data_type: Optional[str], keywords: List[str], intent: str) -> Optional[Dict]:
    """消费侧注入用（planner.py/analyze.py 调用）：候选只筛 active（draft 尚未验证过，不参与注入）。
    语义不可用时直接放弃注入，不做关键词兜底——教训只是提醒，宁可不提醒也不要模糊命中。"""
    ok, result = _semantic_match(data_type, keywords, intent, ("active",))
    return result if ok else None
```

- [ ] **Step 5: 运行测试确认通过**

Run: `E:/python3.13/python.exe scripts/test_lesson_learning.py`
Expected: `5/5 通过`

- [ ] **Step 6: Commit**

```bash
git add src/memory/lessons.py src/config/settings.py scripts/test_lesson_learning.py
git commit -m "feat: 新增教训存储与语义召回基础 find_similar_lesson/find_active_lesson"
```

---

## Task 2: 教训蒸馏与创建/合并写入

**Files:**
- Modify: `src/conductor/prompts.py`（在 `TEMPLATE_CURATOR_SYSTEM` 块之后插入 `LESSON_DISTILL_SYSTEM`）
- Modify: `src/memory/lessons.py`（追加 `distill_lesson`、`record_failure`）
- Test: `scripts/test_lesson_learning.py`（追加 4 个测试）

**Interfaces:**
- Consumes：Task 1 的 `find_similar_lesson`、`load_lessons`、`_slugify`、`LESSONS_DIR`；`src.llm.achat`（签名 `achat(messages, *, provider=None, model=None) -> str`，与 `templates.py` 用法一致）；`src.conductor.utils.parse_json_obj`（`parse_json_obj(raw: str) -> dict`，解析失败返回 `{}`）。
- Produces：`distill_lesson(intent, data_type, keywords, failure_signal, *, existing=None, provider=None, model=None) -> Optional[Dict]`（返回 `{title, keywords, body}` 或 `None`）、`record_failure(intent, data_type, keywords, failure_signal, *, provider=None, model=None) -> None`（供 Task 3 的 `checker.py` 调用）。

- [ ] **Step 1: 在 `src/conductor/prompts.py` 新增 Prompt**

在 `TEMPLATE_CURATOR_SYSTEM` 定义块结束处（该常量三引号字符串的 `}"""` 之后，`PLANNER_SYSTEM` 定义之前）插入：

```python
# 失败教训蒸馏：把一次"采集失败"的现象提炼成通用化的应对建议（自学习阶段B2）。
# existing 场景（合并）由调用方在 user 消息里追加旧教训内容，system prompt 本身对两种场景通用。
LESSON_DISTILL_SYSTEM = """你是"失败教训提炼"助手。给你一次任务的目标、数据类型与本次采集失败的现象
（如果还给了一条已有的旧教训，说明这类失败历史上已出现过，请把新旧两次信息融合成一份更完整的教训，
不要简单拼接）。请把它提炼成一条可复用的教训（帮助以后同类任务提前规避/应对），只输出一个 JSON 对象，
不要任何额外文字：
{
  "title": "教训标题（简短，体现失败场景，如 抖音小众品牌评论采集不足）",
  "keywords": ["命中该教训的主题关键词，3-6 个，用于以后匹配同类任务"],
  "body": "教训正文：一段中文提醒，通用化描述这类任务容易踩的坑以及应对建议（如换数据源/提前提示用户/放宽条件），不要复述本次具体的查询内容或数据细节。"
}"""
```

- [ ] **Step 2: 在 `src/memory/lessons.py` 顶部补齐新增依赖的 import**

把文件顶部的 import 块：

```python
from src.config.settings import PROJECT_ROOT
from src.memory._frontmatter import FrontmatterError, parse_frontmatter
```

改为：

```python
from src.config.settings import PROJECT_ROOT
from src.conductor.prompts import LESSON_DISTILL_SYSTEM
from src.conductor.utils import parse_json_obj
from src.llm import achat
from src.memory._frontmatter import FrontmatterError, parse_frontmatter
```

- [ ] **Step 3: 在 `src/memory/lessons.py` 末尾追加蒸馏与写入函数**

```python
async def distill_lesson(
    intent: str,
    data_type: str,
    keywords: List[str],
    failure_signal: str,
    *,
    existing: Optional[Dict] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Optional[Dict]:
    """用 LLM 把一次失败蒸馏成教训，返回 {title, keywords, body}；调用失败或无正文返回 None。

    existing 传入命中的旧教训（含 title/body）时，提示词要求融合新旧信息产出更完整的一份，
    而不是简单拼接（合并语义与 B1 阶段 Curator merge 一致）。
    """
    user = (
        f"任务目标：{intent}\n数据类型：{data_type}\n失败现象：\n{(failure_signal or '')[:2000]}"
    )
    if existing:
        user += f"\n\n已有的旧教训：\n标题：{existing['title']}\n正文：{existing['body']}"
    try:
        raw = await achat(
            [
                {"role": "system", "content": LESSON_DISTILL_SYSTEM},
                {"role": "user", "content": user},
            ],
            provider=provider,
            model=model,
        )
    except Exception:
        logger.warning("教训蒸馏 LLM 调用失败", exc_info=True)
        return None
    data = parse_json_obj(raw)
    body = (data.get("body") or "").strip()
    if not body:
        return None
    kws = data.get("keywords") or []
    if isinstance(kws, str):
        kws = [kws]
    return {
        "title": (data.get("title") or (intent or "")[:20] or "失败教训").strip(),
        "keywords": [str(k).strip() for k in kws if str(k).strip()],
        "body": body,
    }


async def record_failure(
    intent: str,
    data_type: str,
    keywords: List[str],
    failure_signal: str,
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> None:
    """checker.py 判定"采集失败"后的唯一调用入口。

    命中已有教训（draft 或 active）→ 合并蒸馏更新正文，occurrences+1，若原为 draft 且累计
    达到 settings.lesson_promote_occurrences 则转正 active；未命中 → 蒸馏新教训，落盘为
    draft、occurrences=1。蒸馏 LLM 调用失败/输出无正文时整体跳过，不落盘、不抛异常。
    """
    from src.config.settings import settings

    existing = find_similar_lesson(data_type, keywords, intent)
    tpl = await distill_lesson(
        intent, data_type, keywords, failure_signal,
        existing=existing, provider=provider, model=model,
    )
    if not tpl:
        logger.warning("教训蒸馏无有效正文，本次跳过")
        return

    if existing:
        path = LESSONS_DIR / f"{existing['slug']}.md"
        occurrences = existing["occurrences"] + 1
        status = existing["status"]
        if status == "draft" and occurrences >= settings.lesson_promote_occurrences:
            status = "active"
        meta = {
            "title": tpl["title"] or existing["title"],
            "data_type": (data_type or "").lower(),
            "keywords": tpl["keywords"] or existing["keywords"],
            "status": status,
            "occurrences": occurrences,
        }
        front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
        path.write_text(f"---\n{front}\n---\n{tpl['body'].strip()}\n", encoding="utf-8")
        logger.info("教训合并更新：%s（occurrences=%d, status=%s）", existing["slug"], occurrences, status)
        return

    LESSONS_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slugify(tpl["title"])
    path = LESSONS_DIR / f"{slug}.md"
    i = 2
    while path.exists():
        path = LESSONS_DIR / f"{slug}-{i}.md"
        i += 1
    meta = {
        "title": tpl["title"],
        "data_type": (data_type or "").lower(),
        "keywords": [k for k in (tpl["keywords"] or keywords or []) if k],
        "status": "draft",
        "occurrences": 1,
    }
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    path.write_text(f"---\n{front}\n---\n{tpl['body'].strip()}\n", encoding="utf-8")
    logger.info("已沉淀新教训（草稿）：%s", path.stem)
```

- [ ] **Step 4: 写失败的测试（追加到 `scripts/test_lesson_learning.py`）**

在 `test_find_active_lesson_returns_none_when_embedding_unavailable` 函数之后、`def main():` 之前插入：

```python
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
    d = _setup_tmp()
    _write_lesson(d, "existing", "旧教训", "comment", ["小众品牌"], "旧正文", status="draft", occurrences=1)
    old_promote = settings.lesson_promote_occurrences
    old_enabled = settings.embedding_enabled
    settings.lesson_promote_occurrences = 2
    settings.embedding_enabled = False  # 关键词 Jaccard 命中旧教训（Jaccard=1.0）
    merge_payload = {"title": "旧教训", "keywords": ["小众品牌", "抖音"], "body": "融合后的新正文"}
    try:
        with patch("src.memory.lessons.achat", new=_fake_achat(merge_payload)):
            asyncio.run(lesson.record_failure("同类失败任务", "comment", ["小众品牌"], "未采集到有效数据"))
        lessons = lesson.load_lessons()
        assert len(lessons) == 1, lessons
        t = lessons[0]
        assert t["slug"] == "existing"
        assert t["occurrences"] == 2
        assert t["status"] == "active", t
        assert t["body"] == "融合后的新正文"
        assert set(t["keywords"]) == {"小众品牌", "抖音"}
    finally:
        settings.lesson_promote_occurrences = old_promote
        settings.embedding_enabled = old_enabled


def test_record_failure_keeps_accumulating_when_already_active():
    d = _setup_tmp()
    _write_lesson(d, "existing", "旧教训", "comment", ["小众品牌"], "旧正文", status="active", occurrences=2)
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    merge_payload = {"title": "旧教训", "keywords": ["小众品牌"], "body": "再次融合的正文"}
    try:
        with patch("src.memory.lessons.achat", new=_fake_achat(merge_payload)):
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
```

同时把 `main()` 里的 `tests` 列表更新为：

```python
    tests = [
        test_find_similar_lesson_filters_by_data_type,
        test_find_similar_lesson_semantic_hit,
        test_find_similar_lesson_falls_back_to_jaccard_when_embedding_unavailable,
        test_find_active_lesson_excludes_draft,
        test_find_active_lesson_returns_none_when_embedding_unavailable,
        test_record_failure_creates_draft_when_no_match,
        test_record_failure_merges_and_promotes_to_active,
        test_record_failure_keeps_accumulating_when_already_active,
        test_record_failure_skips_when_distill_returns_none,
    ]
```

- [ ] **Step 5: 运行测试确认通过**

Run: `E:/python3.13/python.exe scripts/test_lesson_learning.py`
Expected: `9/9 通过`

- [ ] **Step 6: Commit**

```bash
git add src/conductor/prompts.py src/memory/lessons.py scripts/test_lesson_learning.py
git commit -m "feat: 新增教训蒸馏 distill_lesson 与创建/合并写入 record_failure"
```

---

## Task 3: 消费侧注入 + 三个节点接入

**Files:**
- Modify: `src/memory/lessons.py`（追加 `lesson_for_analyze`、`lesson_for_planner`）
- Modify: `src/memory/__init__.py`（导出 `record_failure`、`lesson_for_analyze`、`lesson_for_planner`）
- Modify: `src/conductor/nodes/checker.py`（新增 `record_failure` 调用分支）
- Modify: `src/conductor/nodes/analyze.py:209`（追加 `lesson_for_analyze(spec)`）
- Modify: `src/conductor/nodes/planner.py:52`（追加 `lesson_for_planner(user_input)`）
- Test: `scripts/test_lesson_learning.py`（追加 4 个测试）
- Test: `scripts/test_checker_rerun.py`（追加 3 个测试）

**Interfaces:**
- Consumes：Task 1 的 `find_active_lesson`；Task 2 的 `record_failure`；`src.conductor.task_spec.TaskSpec`（`spec.data_type.value`/`spec.keywords`/`spec.intent`，已有）；`checker.py` 已有的 `_looks_like_collection_failure`、`spec`、`analysis`、`state.get("provider"/"model")`。
- Produces：`lesson_for_analyze(spec: TaskSpec) -> str`、`lesson_for_planner(user_input: str) -> str`（两者均：命中返回 `"\n\n# 历史教训提醒\n{body}\n"`，未命中/语义不可用返回空串，供 `analyze.py`/`planner.py` 直接字符串拼接）。

- [ ] **Step 1: 在 `src/memory/lessons.py` 末尾追加消费侧函数**

```python
def lesson_for_analyze(spec) -> str:
    """analyze 节点用：命中同类失败教训（仅 active）则追加提醒；未命中/语义不可用返回空串。"""
    t = find_active_lesson(spec.data_type.value, spec.keywords, spec.intent)
    if not t:
        return ""
    return f"\n\n# 历史教训提醒\n{t['body']}\n"


def lesson_for_planner(user_input: str) -> str:
    """planner 节点用：此时尚无 TaskSpec，只有原始用户输入，因此不按 data_type 过滤，直接
    用原始文本做语义比对——这正好绕开了 skills_for_planner() 因缺少结构化字段而只能靠
    trigger.always 恒触发的限制（见 skills/README.md）。命中同类失败教训（仅 active）
    则追加提醒；未命中/语义不可用返回空串。"""
    t = find_active_lesson(None, [], user_input)
    if not t:
        return ""
    return f"\n\n# 历史教训提醒\n{t['body']}\n"
```

`spec` 参数不加类型注解以避免在 `lessons.py` 里引入对 `src.conductor.task_spec` 的模块级 import（`loader.py`/`templates.py` 已各自 import 过 `TaskSpec`，这里用鸭子类型即可，减少一处循环 import 的表面风险）。

- [ ] **Step 2: 更新 `src/memory/__init__.py` 导出**

把文件内容改为：

```python
"""记忆与技能层：跨会话用户偏好 + 个人记忆 + 任务技能复用 + 分析模板自学习 + 失败教训分流。"""
from .lessons import lesson_for_analyze, lesson_for_planner, record_failure
from .loader import (
    add_preference,
    load_preferences,
    load_skills,
    personal_context,
    preferences_context,
    skill_for_analysis,
    skills_for_planner,
)
from .templates import (
    delete_template,
    distill_template,
    find_duplicate,
    load_templates,
    match_template,
    record_template_use,
    save_template,
)

__all__ = [
    "load_preferences",
    "preferences_context",
    "personal_context",
    "add_preference",
    "load_skills",
    "skill_for_analysis",
    "skills_for_planner",
    "load_templates",
    "match_template",
    "save_template",
    "distill_template",
    "record_template_use",
    "find_duplicate",
    "delete_template",
    "record_failure",
    "lesson_for_analyze",
    "lesson_for_planner",
]
```

- [ ] **Step 3: 改造 `src/conductor/nodes/checker.py`**

第 18 行的 import：

```python
from src.memory import distill_template, record_template_use, save_template
```

改为：

```python
from src.memory import distill_template, record_failure, record_template_use, save_template
```

在现有"自学习阶段2：走了兜底 且 质量通过 且非采集失败 → 自动沉淀模板"整个 `if` 块（第 103-131 行）**之后**、`return out`（第 133 行）**之前**，插入一个独立的新分支（不嵌套在模板沉淀的 `if` 里，因为教训沉淀不要求 `passed`/`analysis_source=="fallback"`——采集失败与报告质量分、走哪条分析通道无关）：

```python
    # 自学习B2：本轮判定"采集失败"（叙事完整但没数据）→ 教训分流，不要求 passed/analysis_source=="fallback"
    # （模板沉淀要求 passed+fallback；教训只要"确实采集失败"就有沉淀价值，与报告质量分/分析通道无关）
    if settings.lesson_learning_enabled and _looks_like_collection_failure(
        state.get("cleaned_dataset") or [], analysis
    ):
        try:
            await record_failure(
                spec.intent,
                spec.data_type.value,
                list(spec.keywords or []),
                failure_signal=analysis,
                provider=state.get("provider"),
                model=state.get("model"),
            )
        except Exception:
            logger.warning("教训沉淀失败（不影响产出）", exc_info=True)
```

- [ ] **Step 4: 改造 `src/conductor/nodes/analyze.py`**

第 9 行的 import：

```python
from src.memory import match_template, skill_for_analysis
```

改为：

```python
from src.memory import lesson_for_analyze, match_template, skill_for_analysis
```

第 209 行 `system += skill_for_analysis(spec)` 之后新增一行：

```python
    system += skill_for_analysis(spec)
    system += lesson_for_analyze(spec)
```

- [ ] **Step 5: 改造 `src/conductor/nodes/planner.py`**

第 12 行的 import：

```python
from src.memory import skills_for_planner
```

改为：

```python
from src.memory import lesson_for_planner, skills_for_planner
```

第 52 行：

```python
    system = PLANNER_SYSTEM.format(platforms=platforms) + skills_for_planner()
```

改为：

```python
    system = PLANNER_SYSTEM.format(platforms=platforms) + skills_for_planner() + lesson_for_planner(user_input)
```

- [ ] **Step 6: 写失败的测试（追加到 `scripts/test_lesson_learning.py`）**

在 `test_record_failure_skips_when_distill_returns_none` 函数之后、`def main():` 之前插入：

```python
def test_lesson_for_analyze_hits_active():
    d = _setup_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["小众品牌"], "别忘了这样应对", status="active", occurrences=2)
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    try:
        spec = TaskSpec(intent="抖音小众品牌评论分析", data_type=DataType.COMMENT, keywords=["小众品牌"])
        text = lesson.lesson_for_analyze(spec)
        assert "别忘了这样应对" in text
    finally:
        settings.embedding_enabled = old_enabled


def test_lesson_for_analyze_ignores_draft():
    d = _setup_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["小众品牌"], "别忘了这样应对", status="draft", occurrences=1)
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    try:
        spec = TaskSpec(intent="抖音小众品牌评论分析", data_type=DataType.COMMENT, keywords=["小众品牌"])
        assert lesson.lesson_for_analyze(spec) == ""
    finally:
        settings.embedding_enabled = old_enabled


def test_lesson_for_planner_ignores_data_type():
    d = _setup_tmp()
    _write_lesson(d, "a", "教训甲", "comment", ["小众品牌", "抖音"], "规划阶段的提醒", status="active", occurrences=2)
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    try:
        text = lesson.lesson_for_planner("帮我看看抖音上小众品牌的评论")
        assert "规划阶段的提醒" in text
    finally:
        settings.embedding_enabled = old_enabled


def test_lesson_for_planner_empty_when_no_match():
    _setup_tmp()
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    try:
        assert lesson.lesson_for_planner("完全无关的查询") == ""
    finally:
        settings.embedding_enabled = old_enabled
```

同时把 `main()` 里的 `tests` 列表更新为（追加最后 4 项）：

```python
    tests = [
        test_find_similar_lesson_filters_by_data_type,
        test_find_similar_lesson_semantic_hit,
        test_find_similar_lesson_falls_back_to_jaccard_when_embedding_unavailable,
        test_find_active_lesson_excludes_draft,
        test_find_active_lesson_returns_none_when_embedding_unavailable,
        test_record_failure_creates_draft_when_no_match,
        test_record_failure_merges_and_promotes_to_active,
        test_record_failure_keeps_accumulating_when_already_active,
        test_record_failure_skips_when_distill_returns_none,
        test_lesson_for_analyze_hits_active,
        test_lesson_for_analyze_ignores_draft,
        test_lesson_for_planner_ignores_data_type,
        test_lesson_for_planner_empty_when_no_match,
    ]
```

- [ ] **Step 7: 写失败的测试（追加到 `scripts/test_checker_rerun.py`）**

在 `test_checker_no_template_saved_when_curator_discards` 函数之后、`if __name__ == "__main__":` 之前插入：

```python
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
```

`test_checker_rerun.py` 顶部已有 `from unittest.mock import AsyncMock, patch`，无需新增 import。

- [ ] **Step 8: 运行测试确认通过**

Run: `E:/python3.13/python.exe scripts/test_lesson_learning.py`
Expected: `13/13 通过`

Run: `E:/python3.13/python.exe scripts/test_checker_rerun.py`
Expected: `15/15 通过`（原 12 项 + 新增 3 项）

- [ ] **Step 9: Commit**

```bash
git add src/memory/lessons.py src/memory/__init__.py src/conductor/nodes/checker.py \
        src/conductor/nodes/analyze.py src/conductor/nodes/planner.py \
        scripts/test_lesson_learning.py scripts/test_checker_rerun.py
git commit -m "feat: 教训消费侧注入 lesson_for_analyze/lesson_for_planner，接入 checker/analyze/planner 三节点"
```

---

## Task 4: 文档同步 + 全量回归

**Files:**
- Modify: `README_AGENT.md`（新增 "15.6" 小节，紧接 15.5 之后）
- Modify: `AGENTS.md`（在现有自学习相关条目附近补充一句提及教训分流）
- Verify: 全量回归 `test_lesson_learning.py`/`test_checker_rerun.py`/`test_template_learning.py`/`test_embeddings.py`

**Interfaces:**
- 无新代码接口，纯文档 + 回归验证。

- [ ] **Step 1: 更新 `README_AGENT.md`**

把第 394 行的：

```markdown
对照 plan 仍未做：**B2（教训分流）**——失败任务的"教训"改走 skills 声明式注入通道，独立 spec，暂未开始。
```

改为（把"未做"陈述换成"已完成"小节，插入到该行原位置，紧接 15.5 之后、"## 当前阶段"之前）：

```markdown
## 15.6 模板库自学习闭环 B2 阶段（教训分流 lesson channel）（2026-07-09 新增）

A/B1 两阶段处理的都是"走了兜底且质量通过"的成功路径；`checker.py` 判定"采集失败"
（`_looks_like_collection_failure`）时，此前只是阻止模板沉淀，失败本身携带的信息——
"这类任务历史上容易采集失败，应该如何应对"——被完全丢弃。本阶段把这部分利用起来。

- **独立目录**：新增 `src/memory/lessons.py` + `data/lessons/`，与 `data/templates/`
  （报告结构自学习）彻底分开——教训描述的是"失败模式"，不是"报告结构"。一条一文件，
  frontmatter 含 `status`（`draft`|`active`，无 `retired`）与 `occurrences`（累计命中次数）。
- **创建/合并**：`checker.py` 新增独立分支（不嵌套进模板沉淀的 `if`，因为教训沉淀不要求
  `passed`/`analysis_source=="fallback"`）——判定失败即调用 `record_failure()`：命中已有
  教训（`find_similar_lesson`，候选含 draft+active）则用 LLM（新增 `LESSON_DISTILL_SYSTEM`）
  把新旧失败信息融合蒸馏成更完整的正文，`occurrences+1`，累计达 `LESSON_PROMOTE_OCCURRENCES`
  （默认2）转正 `active`；未命中则蒸馏新教训落盘为 `draft`。累计阈值防止"偶发一次失败就永久
  误导后续同类任务"；转正后不做自动退役——教训是温和提醒，长期保留无实质危害。
- **消费侧注入**：`planner.py`/`analyze.py` 各自在拼装 system prompt 时追加
  `lesson_for_planner()`/`lesson_for_analyze()`，只召回 `active` 教训。**planner 阶段
  尚无 TaskSpec**（只有原始用户输入），因此不按 `data_type` 过滤、直接对原始文本做语义
  比对——这正好绕开了 `skills_for_planner()` 因缺少结构化字段只能靠 `trigger.always`
  恒触发的限制（技能体系声明式化，见 15.3 节），是语义召回相对现有技能匹配机制的一个
  真实优势。
- **降级策略不对称**：创建/合并判重语义不可用时退回关键词 Jaccard（复用
  `TEMPLATE_DEDUP_THRESHOLD`/`TEMPLATE_DEDUP_RERANK_THRESHOLD`，不新增重复配置）；
  消费侧注入语义不可用时**直接放弃**（不做关键词兜底）——教训只是提醒，宁可不提醒也不
  要模糊命中。不引入模板库那样的 `_vectors.json` 向量缓存，教训数量级小，现算即可。
- 全程自动，无人工审核环节，与 A/B1 阶段的自动化方向一致。
- 回归：新增 `test_lesson_learning.py`（13 项），`test_checker_rerun.py` 扩容至 15 项，
  既有 `test_template_learning.py`(21)/`test_embeddings.py`(4) 回归无破坏。纯后端改动，
  不涉及 `npm run build`。
```

（若实现过程中测试数量与本步骤所写不一致，以实际跑出的数字为准回填，其余表述不变。）

- [ ] **Step 2: 更新 `AGENTS.md`**

找到第 174 行：

```markdown
- 模板自学习自动沉淀（Checker 通过且走兜底时自动蒸馏沉淀，替代人工确认）；
```

在其后新增一行：

```markdown
- 失败教训分流 `src/memory/lessons.py`（Checker 判定"采集失败"时蒸馏教训存 `data/lessons/`，累计2次命中转正后在 planner/analyze 节点自动注入提醒，与模板库自学习彻底分开维护）；
```

- [ ] **Step 3: 全量回归验证**

Run: `E:/python3.13/python.exe scripts/test_lesson_learning.py`
Expected: 全部 PASS

Run: `E:/python3.13/python.exe scripts/test_checker_rerun.py`
Expected: 全部 PASS

Run: `E:/python3.13/python.exe scripts/test_template_learning.py`
Expected: `21/21 通过`（无回归破坏）

Run: `E:/python3.13/python.exe scripts/test_embeddings.py`
Expected: `4/4 通过`（无回归破坏）

- [ ] **Step 4: Commit**

```bash
git add README_AGENT.md AGENTS.md
git commit -m "docs: 同步模板库自学习闭环 B2 阶段（教训分流）到说明文档"
```
