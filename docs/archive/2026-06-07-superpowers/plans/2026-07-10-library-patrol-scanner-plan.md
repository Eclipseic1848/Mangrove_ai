# 模板库/教训库定时巡检 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给模板库（`data/templates/`）、教训库（`data/lessons/`）新增独立的后台定时巡检器，定期做语义去重扫描（自动合并高置信度重复项）与长期停滞草稿清理，结果落库并在前端展示。

**Architecture:** 新增 `src/api/library_dedup_scanner.py`（`LibraryDedupScanner` 类，结构参照 `CookieHealthScanner` 但业务完全独立）在 `main.py` 的 `lifespan` 里常驻；模板库/教训库各自新增"巡检专用"的去重发现+合并原语（`src/memory/templates.py`、`src/memory/lessons.py`），复用两个新的融合专用 Prompt（与业务已有的 Curator/蒸馏 Prompt 分开，避免混用不同任务框架的 Prompt 导致模型输出对不上）；巡检结果写入 `webui.db` 新表，新增只读接口与前端第 3 个 Tab 展示。

**Tech Stack:** Python 3.13、sqlite3（`src/api/store.py`）、现有 embedding/rerank 基础设施（`src/memory/embeddings.py`）、FastAPI、React + TypeScript。

## Global Constraints

- 新增配置（`src/config/settings.py`）：`library_dedup_scan_enabled: bool = False`、`library_dedup_scan_interval_hours: int = 24`、`library_stale_draft_days: int = 30`（模板库/教训库公用同一阈值）、`library_dedup_scan_max_merges_per_run: int = 5`。
- `created_at` 字段仅在**新建**条目时写入（ISO 格式字符串），旧文件没有这个字段时巡检的停滞判定直接跳过该条目，不做批量回填、不追溯删除历史数据。
- 巡检的语义去重**不复用**任务实时路径的 `curate_template()`/`record_failure()`（那是"新内容 vs 库内候选"的判定流程，直接拿库内已有条目当"新内容"传入会连自己一起搜进候选造成自我匹配）；巡检去重发现与合并走两个新的、专门给巡检用的原语，且合并环节使用**两个新增的专用 Prompt**（`TEMPLATE_PAIR_MERGE_SYSTEM`/`LESSON_PAIR_MERGE_SYSTEM`），不复用 `TEMPLATE_CURATOR_SYSTEM`/`LESSON_DISTILL_SYSTEM`——那两个 Prompt 期望的输入输出框架（"要不要合并"的三态决策/"是否有旧教训"的蒸馏）与巡检"已确认是重复、只需要融合正文"的场景不同，混用容易让模型输出对不上格式。
- 巡检去重扫描在 `settings.embedding_enabled` 为 False（或语义端点不可用）时**直接跳过本轮的去重扫描**，不退回关键词 Jaccard 兜底——巡检是低频后台任务，没有"必须给出结果"的实时压力，宁可这一轮不查也不要用弱匹配误判合并（停滞清理不受此影响，照常执行，因为它不依赖语义判断）。
- 合并后**保留方**（模板库按 `uses` 更高者、教训库按 `occurrences` 更高者，相等则取 `load_templates()`/`load_lessons()` 排序中先出现的一条）的 `uses`/`quality_avg`/`status`（模板）或 `occurrences`/`status`（教训）**保持不变**，只更新 `title`/`keywords`/`body`——与 B1/B2 已有的"合并不清零历史统计"原则一致。
- 每轮巡检每个知识库最多处理 `settings.library_dedup_scan_max_merges_per_run` 对合并（默认5），处理完一对后 `await self._sleep(...)` 稍作等待再处理下一对；超出上限的留到下一轮巡检继续（已合并的对不会再被判定为重复，不会重复处理同一对）。
- 测试运行方式沿用项目惯例：无 pytest 框架，`def test_x(): assert ...` + `main()` 收集 PASS/FAIL，`sys.exit(1 if failed else 0)`，用 `E:/python3.13/python.exe scripts/xxx.py` 直接跑。

---

## Task 1: 配置项 + created_at 落盘 + 巡检日志表

**Files:**
- Modify: `src/config/settings.py:141`（在 `lesson_candidate_min_cosine` 那一行之后插入新配置）
- Modify: `src/memory/templates.py`（顶部 import 加 `datetime`；`load_templates()` 读取 `created_at`；`save_template()` 新建分支写入 `created_at`）
- Modify: `src/memory/lessons.py`（顶部 import 加 `datetime`；`load_lessons()` 读取 `created_at`；`record_failure()` 新建分支写入 `created_at`）
- Modify: `src/api/store.py`（新表 DDL + `library_dedup_scan_log_add`/`library_dedup_scan_log_recent` 方法）
- Test: `scripts/test_library_dedup_scanner.py`（新建）

**Interfaces:**
- Produces：`load_templates()`/`load_lessons()` 返回的每条 dict 新增 `"created_at": str`（无该字段的旧文件返回空串 `""`）；`WebUIStore.library_dedup_scan_log_add(templates_scanned: int, templates_merged: int, lessons_scanned: int, lessons_merged: int, stale_drafts_deleted: int) -> None`；`WebUIStore.library_dedup_scan_log_recent(limit: int = 20) -> List[Dict[str, Any]]`（供 Task 5 的路由使用）。

- [ ] **Step 1: 新增配置项**

在 `src/config/settings.py` 第 141 行（`lesson_candidate_min_cosine` 那一行）之后插入：

```python
    # ===== 模板库/教训库定时巡检（C阶段：结构性升级）=====
    # 独立巡检器，与下方 cookie_health 巡检完全独立（业务不相关），风格仿照其异步轮询+开关热读。
    library_dedup_scan_enabled: bool = Field(default=False, description="是否启用模板库/教训库定时巡检（语义去重扫描+停滞草稿清理）")
    library_dedup_scan_interval_hours: int = Field(default=24, description="巡检间隔（小时），默认每天一次")
    library_stale_draft_days: int = Field(default=30, description="草稿条目创建后超过此天数仍未转正则视为长期停滞，巡检时自动清理（模板库/教训库公用）")
    library_dedup_scan_max_merges_per_run: int = Field(default=5, description="每轮巡检每个知识库最多处理的确认合并对数，防止历史积压一次性触发过多LLM调用")
```

- [ ] **Step 2: `src/memory/templates.py` 补充 `created_at`**

顶部 import 块（第 16-25 行附近）：

```python
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
```

改为：

```python
import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
```

`load_templates()` 内 `out.append({...})` 字典（约第 72-82 行），在 `"quality_avg": float(meta.get("quality_avg") or 0),` 之后追加一行：

```python
            "created_at": str(meta.get("created_at") or ""),
```

`save_template()` 末尾 `kind == "new"` 分支的 `meta = {...}`（约第 483-490 行）：

```python
    meta = {
        "title": title,
        "data_type": (data_type or "").lower(),
        "keywords": [k for k in (keywords or []) if k],
        "status": "draft",   # 新模板先进草稿区，达标后转正（见 record_template_use）
        "uses": 0,
        "quality_avg": 0,
    }
```

改为（新增 `created_at`）：

```python
    meta = {
        "title": title,
        "data_type": (data_type or "").lower(),
        "keywords": [k for k in (keywords or []) if k],
        "status": "draft",   # 新模板先进草稿区，达标后转正（见 record_template_use）
        "uses": 0,
        "quality_avg": 0,
        "created_at": datetime.now().isoformat(),
    }
```

- [ ] **Step 3: `src/memory/lessons.py` 补充 `created_at`**

顶部 import 块：

```python
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
```

改为：

```python
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
```

`load_lessons()` 内 `out.append({...})` 字典（第 77-85 行），在 `"occurrences": int(meta.get("occurrences") or 0),` 之后追加一行：

```python
            "created_at": str(meta.get("created_at") or ""),
```

`record_failure()` 里 `if not existing:` 分支的 `meta = {...}`（第 275-281 行）：

```python
        meta = {
            "title": fresh["title"],
            "data_type": (data_type or "").lower(),
            "keywords": [k for k in (fresh["keywords"] or keywords or []) if k],
            "status": "draft",
            "occurrences": 1,
        }
```

改为：

```python
        meta = {
            "title": fresh["title"],
            "data_type": (data_type or "").lower(),
            "keywords": [k for k in (fresh["keywords"] or keywords or []) if k],
            "status": "draft",
            "occurrences": 1,
            "created_at": datetime.now().isoformat(),
        }
```

- [ ] **Step 4: `src/api/store.py` 新增巡检日志表**

在 `_DDL` 字符串（约第 22-77 行）里，`CREATE TABLE IF NOT EXISTS user_memory (...)` 那一段之后、`CREATE INDEX IF NOT EXISTS idx_conv_user ...` 之前插入：

```sql
CREATE TABLE IF NOT EXISTS library_dedup_scan_log (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at               TEXT NOT NULL,
    templates_scanned    INTEGER NOT NULL,
    templates_merged     INTEGER NOT NULL,
    lessons_scanned      INTEGER NOT NULL,
    lessons_merged       INTEGER NOT NULL,
    stale_drafts_deleted INTEGER NOT NULL
);
```

在 `memory_add`/`memory_list` 等个人记忆方法之后（文件末尾附近，`class WebUIStore` 内）追加：

```python
    # ---------- 模板库/教训库定时巡检日志 ----------
    def library_dedup_scan_log_add(
        self,
        templates_scanned: int,
        templates_merged: int,
        lessons_scanned: int,
        lessons_merged: int,
        stale_drafts_deleted: int,
    ) -> None:
        """每轮巡检结束后写一行记录，即便本轮计数全为0也写（用于确认巡检确实在跑）。"""
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO library_dedup_scan_log "
                "(ran_at, templates_scanned, templates_merged, lessons_scanned, lessons_merged, stale_drafts_deleted) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (_now(), templates_scanned, templates_merged, lessons_scanned, lessons_merged, stale_drafts_deleted),
            )

    def library_dedup_scan_log_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        """返回最近 limit 轮巡检记录，按写入顺序倒序（最新的在前）。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, ran_at, templates_scanned, templates_merged, lessons_scanned, "
                "lessons_merged, stale_drafts_deleted FROM library_dedup_scan_log "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 5: 写测试**

创建 `scripts/test_library_dedup_scanner.py`：

```python
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
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.store import WebUIStore
from src.config.settings import settings
import src.memory.templates as tpl
import src.memory.lessons as lesson
import src.memory.embeddings as emb


def _tmp_store() -> WebUIStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return WebUIStore(tmp.name)


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
        templates_scanned=10, templates_merged=2, lessons_scanned=5, lessons_merged=1, stale_drafts_deleted=1,
    )
    store.library_dedup_scan_log_add(
        templates_scanned=8, templates_merged=0, lessons_scanned=4, lessons_merged=0, stale_drafts_deleted=0,
    )
    rows = store.library_dedup_scan_log_recent(limit=20)
    assert len(rows) == 2
    # 按 id 倒序，最新一条（后写入的）在前
    assert rows[0]["templates_scanned"] == 8
    assert rows[1]["templates_scanned"] == 10


def main():
    tests = [
        test_save_template_writes_created_at,
        test_legacy_template_without_created_at_reads_empty,
        test_record_failure_writes_created_at,
        test_legacy_lesson_without_created_at_reads_empty,
        test_scan_log_add_and_recent,
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

- [ ] **Step 6: 运行测试确认通过**

Run: `E:/python3.13/python.exe scripts/test_library_dedup_scanner.py`
Expected: `5/5 通过`

- [ ] **Step 7: Commit**

```bash
git add src/config/settings.py src/memory/templates.py src/memory/lessons.py \
        src/api/store.py scripts/test_library_dedup_scanner.py
git commit -m "feat: 新增巡检配置项+created_at落盘+巡检日志表"
```

---

## Task 2: 模板库巡检去重发现与合并原语

**Files:**
- Modify: `src/conductor/prompts.py`（新增 `TEMPLATE_PAIR_MERGE_SYSTEM`）
- Modify: `src/memory/templates.py`（新增 `find_patrol_duplicate`、`merge_template_pair`、`apply_patrol_merge`）
- Test: `scripts/test_library_dedup_scanner.py`（追加 5 个测试）

**Interfaces:**
- Consumes：Task 1 的 `created_at`（不直接用，但同模块内 `load_templates()` 已含此字段）；模板库既有的 `_semantic_candidates`、`_template_text`、`_DEDUP_RERANK_INSTRUCT`、`TEMPLATES_DIR`、`_load_vectors`/`_save_vectors`、`achat`、`parse_json_obj`（均为 `templates.py` 内已有的模块级函数/常量，不新增导入）。
- Produces：`find_patrol_duplicate(entry: Dict) -> Optional[Dict]`（在同 `data_type` 的其余模板里找与 `entry` 语义重复的一条，排除自身；语义不可用返回 `None`）；`merge_template_pair(a: Dict, b: Dict, *, provider=None, model=None) -> Optional[Dict]`（返回 `{title, keywords, body}` 或 `None`）；`apply_patrol_merge(slug: str, merged: Dict) -> bool`（把融合结果写回目标文件，`uses`/`quality_avg`/`status` 不变；供 Task 4 的巡检器调用）。

- [ ] **Step 1: 在 `src/conductor/prompts.py` 新增 Prompt**

在 `LESSON_DISTILL_SYSTEM` 定义块结束处（`}"""` 之后）、`PLANNER_SYSTEM` 定义之前插入：

```python
# 模板库巡检专用融合：给定两条"已确认属于同一类"的模板，只需要融合正文，不需要再判断
# 要不要合并（巡检已用确定性的 rerank 阈值确认过了）——与 TEMPLATE_CURATOR_SYSTEM 的三态决策
# 框架不同，专用独立 Prompt 避免混淆模型输出格式。
TEMPLATE_PAIR_MERGE_SYSTEM = """你是"分析报告模板库"的整理员。给你两条已确认属于同一类分析任务的模板，
请把两者融合成一份完整重写的正文（不要简单拼接，要写成一份自洽、通用化的新版本，尽量吸收两者
各自的信息量）。只输出一个 JSON 对象，不要任何额外文字：
{
  "title": "融合后的标题（没有明显理由就沿用其中一条的标题）",
  "keywords": ["融合后的关键词，两条并集去重"],
  "body": "完整重写的融合正文"
}"""
```

- [ ] **Step 2: 在 `src/memory/templates.py` 末尾追加三个函数**

```python
async def merge_template_pair(
    a: Dict, b: Dict, *, provider: Optional[str] = None, model: Optional[str] = None,
) -> Optional[Dict]:
    """定时巡检专用：给定两条已确认属于同一类的模板，直接融合成一份正文，不再判断
    "要不要合并"（巡检已用确定性的 rerank 阈值确认过是重复）。
    返回 {title, keywords, body} 或 None（LLM 调用失败/无正文）。"""
    from src.conductor.prompts import TEMPLATE_PAIR_MERGE_SYSTEM

    user = (
        f"模板A：\n标题：{a['title']}\n关键词：{', '.join(a['keywords'])}\n正文：{a['body']}\n\n"
        f"模板B：\n标题：{b['title']}\n关键词：{', '.join(b['keywords'])}\n正文：{b['body']}\n\n"
        "以上两条模板已确认属于同一类分析任务，请融合成一份完整重写的正文。"
    )
    try:
        raw = await achat(
            [
                {"role": "system", "content": TEMPLATE_PAIR_MERGE_SYSTEM},
                {"role": "user", "content": user},
            ],
            provider=provider,
            model=model,
        )
    except Exception:
        logger.warning("巡检模板融合 LLM 调用失败", exc_info=True)
        return None
    data = parse_json_obj(raw)
    body = (data.get("body") or "").strip()
    if not body:
        return None
    kws = data.get("keywords") or list({*a["keywords"], *b["keywords"]})
    if isinstance(kws, str):
        kws = [kws]
    return {
        "title": (data.get("title") or a["title"]).strip(),
        "keywords": [str(k).strip() for k in kws if str(k).strip()],
        "body": body,
    }


def find_patrol_duplicate(entry: Dict) -> Optional[Dict]:
    """定时巡检专用：在同 data_type 的其余模板里找与 entry 语义重复的一条（排除自身）。
    embedding 不可用/rerank 未配置时返回 None——巡检是低频后台任务，宁可这轮不查也不要用
    较弱的兜底误判合并，不像 find_duplicate_semantic 那样退回关键词 Jaccard。"""
    from src.config.settings import settings
    from . import embeddings as emb

    if not settings.embedding_enabled:
        return None
    top = _semantic_candidates(entry["data_type"], entry["keywords"], entry["title"], top_k=6)
    if not top:
        return None
    top = [t for t in top if t["slug"] != entry["slug"]]
    if not top:
        return None
    if not emb.is_rerank_configured():
        return None
    query_text = (entry["title"] or "") + " " + " ".join(entry["keywords"] or [])
    rscores = emb.rerank_scores(query_text, [_template_text(t) for t in top], instruct=_DEDUP_RERANK_INSTRUCT)
    if not rscores:
        return None
    best_i = max(range(len(rscores)), key=rscores.__getitem__)
    if rscores[best_i] >= settings.template_dedup_rerank_threshold:
        return top[best_i]
    return None


def apply_patrol_merge(slug: str, merged: Dict) -> bool:
    """定时巡检专用：把融合结果写回目标模板文件，uses/quality_avg/status 保持不变
    （与 save_template 的 Curator merge 分支同样的"合并不清零历史统计"原则）。
    文件不存在/解析失败返回 False。"""
    path = TEMPLATES_DIR / f"{slug}.md"
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = parse_frontmatter(raw)
    except (OSError, FrontmatterError):
        parsed = None
    if parsed is None:
        logger.warning("巡检合并的目标模板读取/解析失败，跳过：%s", slug)
        return False
    meta, _old_body = parsed
    meta["title"] = merged["title"] or meta.get("title")
    meta["keywords"] = merged["keywords"] or meta.get("keywords")
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    path.write_text(f"---\n{front}\n---\n{merged['body'].strip()}\n", encoding="utf-8")
    cache = _load_vectors()
    if slug in cache:
        cache.pop(slug, None)
        _save_vectors(cache)
    logger.info("巡检去重合并：%s", slug)
    return True
```

- [ ] **Step 3: 写失败的测试（追加到 `scripts/test_library_dedup_scanner.py`）**

在 `test_scan_log_add_and_recent` 函数之后、`def main():` 之前插入：

```python
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
        dup = tpl.find_patrol_duplicate(entry)
        assert dup is not None and dup["slug"] == "b", dup
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
```

同时把 `main()` 里的 `tests` 列表更新为：

```python
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
    ]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `E:/python3.13/python.exe scripts/test_library_dedup_scanner.py`
Expected: `10/10 通过`

- [ ] **Step 5: Commit**

```bash
git add src/conductor/prompts.py src/memory/templates.py scripts/test_library_dedup_scanner.py
git commit -m "feat: 新增模板库巡检去重发现与合并原语 find_patrol_duplicate/merge_template_pair/apply_patrol_merge"
```

---

## Task 3: 教训库巡检去重发现与合并原语

**Files:**
- Modify: `src/conductor/prompts.py`（新增 `LESSON_PAIR_MERGE_SYSTEM`）
- Modify: `src/memory/lessons.py`（新增 `find_patrol_duplicate_lesson`、`merge_lesson_pair`、`apply_patrol_merge_lesson`）
- Test: `scripts/test_library_dedup_scanner.py`（追加 5 个测试）

**Interfaces:**
- Consumes：`lessons.py` 既有的 `load_lessons`、`_lesson_text`、`_LESSON_RERANK_INSTRUCT`、`LESSONS_DIR`、`achat`、`parse_json_obj`（均模块内已有，不新增导入）。
- Produces：`find_patrol_duplicate_lesson(entry: Dict) -> Optional[Dict]`（同 Task 2 的 `find_patrol_duplicate`，教训库版本）；`merge_lesson_pair(a: Dict, b: Dict, *, provider=None, model=None) -> Optional[Dict]`；`apply_patrol_merge_lesson(slug: str, merged: Dict) -> bool`（`status`/`occurrences` 保持不变；供 Task 4 使用）。

- [ ] **Step 1: 在 `src/conductor/prompts.py` 新增 Prompt**

在 `TEMPLATE_PAIR_MERGE_SYSTEM` 定义块结束处（`}"""` 之后）、`PLANNER_SYSTEM` 定义之前插入：

```python
# 教训库巡检专用融合：与 TEMPLATE_PAIR_MERGE_SYSTEM 同样的道理，专用独立 Prompt。
LESSON_PAIR_MERGE_SYSTEM = """你是"失败教训整理员"。给你两条已确认描述同一类失败场景的教训，
请把两者融合成一份更完整的教训（不要简单拼接）。只输出一个 JSON 对象，不要任何额外文字：
{
  "title": "融合后的标题（没有明显理由就沿用其中一条的标题）",
  "keywords": ["融合后的关键词，两条并集去重"],
  "body": "融合后的教训正文"
}"""
```

- [ ] **Step 2: 在 `src/memory/lessons.py` 末尾追加三个函数**

```python
async def merge_lesson_pair(
    a: Dict, b: Dict, *, provider: Optional[str] = None, model: Optional[str] = None,
) -> Optional[Dict]:
    """定时巡检专用：给定两条已确认描述同一类失败场景的教训，直接融合成一份正文。
    返回 {title, keywords, body} 或 None（LLM 调用失败/无正文）。"""
    from src.conductor.prompts import LESSON_PAIR_MERGE_SYSTEM

    user = (
        f"教训A：\n标题：{a['title']}\n关键词：{', '.join(a['keywords'])}\n正文：{a['body']}\n\n"
        f"教训B：\n标题：{b['title']}\n关键词：{', '.join(b['keywords'])}\n正文：{b['body']}\n\n"
        "以上两条教训已确认描述同一类失败场景，请融合成一份更完整的教训。"
    )
    try:
        raw = await achat(
            [
                {"role": "system", "content": LESSON_PAIR_MERGE_SYSTEM},
                {"role": "user", "content": user},
            ],
            provider=provider,
            model=model,
        )
    except Exception:
        logger.warning("巡检教训融合 LLM 调用失败", exc_info=True)
        return None
    data = parse_json_obj(raw)
    body = (data.get("body") or "").strip()
    if not body:
        return None
    kws = data.get("keywords") or list({*a["keywords"], *b["keywords"]})
    if isinstance(kws, str):
        kws = [kws]
    return {
        "title": (data.get("title") or a["title"]).strip(),
        "keywords": [str(k).strip() for k in kws if str(k).strip()],
        "body": body,
    }


def find_patrol_duplicate_lesson(entry: Dict) -> Optional[Dict]:
    """定时巡检专用：在同 data_type 的其余教训里找与 entry 语义重复的一条（排除自身）。
    embedding 不可用/rerank 未配置时返回 None（同模板库巡检哲学：宁可不查也不要弱兜底误判）。"""
    from src.config.settings import settings
    from . import embeddings as emb

    if not settings.embedding_enabled:
        return None
    dt = (entry["data_type"] or "").lower()
    cands = [t for t in load_lessons() if t["slug"] != entry["slug"] and (not dt or t["data_type"] == dt)]
    if not cands:
        return None
    if not emb.is_rerank_configured():
        return None
    query_text = (entry["title"] or "") + " " + " ".join(entry["keywords"] or [])
    got = emb.embed_texts_with_model([query_text])
    if not got or not got[1]:
        return None
    model, qvec = got[0], got[1][0]
    got2 = emb.embed_texts_with_model([_lesson_text(t) for t in cands])
    if not got2 or got2[0] != model or len(got2[1]) != len(cands):
        return None
    scored = sorted(
        ((t, emb.cosine(qvec, v)) for t, v in zip(cands, got2[1])),
        key=lambda x: x[1], reverse=True,
    )
    top = [t for t, sim in scored if sim >= settings.lesson_candidate_min_cosine][:3]
    if not top:
        return None
    rscores = emb.rerank_scores(query_text, [_lesson_text(t) for t in top], instruct=_LESSON_RERANK_INSTRUCT)
    if not rscores:
        return None
    best_i = max(range(len(rscores)), key=rscores.__getitem__)
    if rscores[best_i] >= settings.template_dedup_rerank_threshold:
        return top[best_i]
    return None


def apply_patrol_merge_lesson(slug: str, merged: Dict) -> bool:
    """定时巡检专用：把融合结果写回目标教训文件，status/occurrences 保持不变。
    文件不存在/解析失败返回 False。"""
    path = LESSONS_DIR / f"{slug}.md"
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = parse_frontmatter(raw)
    except (OSError, FrontmatterError):
        parsed = None
    if parsed is None:
        logger.warning("巡检合并的目标教训读取/解析失败，跳过：%s", slug)
        return False
    meta, _old_body = parsed
    meta["title"] = merged["title"] or meta.get("title")
    meta["keywords"] = merged["keywords"] or meta.get("keywords")
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    path.write_text(f"---\n{front}\n---\n{merged['body'].strip()}\n", encoding="utf-8")
    logger.info("巡检去重合并：%s", slug)
    return True
```

- [ ] **Step 3: 写失败的测试（追加到 `scripts/test_library_dedup_scanner.py`）**

在 `test_apply_patrol_merge_keeps_stats_updates_content` 函数之后、`def main():` 之前插入：

```python
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
        dup = lesson.find_patrol_duplicate_lesson(entry)
        assert dup is not None and dup["slug"] == "b", dup
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
```

同时把 `main()` 里的 `tests` 列表追加最后 5 项：

```python
        test_find_patrol_duplicate_lesson_excludes_self_and_finds_other,
        test_find_patrol_duplicate_lesson_returns_none_when_embedding_disabled,
        test_merge_lesson_pair_returns_fused_content,
        test_merge_lesson_pair_returns_none_on_llm_failure,
        test_apply_patrol_merge_lesson_keeps_stats_updates_content,
```

- [ ] **Step 4: 运行测试确认通过**

Run: `E:/python3.13/python.exe scripts/test_library_dedup_scanner.py`
Expected: `15/15 通过`

- [ ] **Step 5: Commit**

```bash
git add src/conductor/prompts.py src/memory/lessons.py scripts/test_library_dedup_scanner.py
git commit -m "feat: 新增教训库巡检去重发现与合并原语 find_patrol_duplicate_lesson/merge_lesson_pair/apply_patrol_merge_lesson"
```

---

## Task 4: LibraryDedupScanner 整合 + main.py 挂载

**Files:**
- Create: `src/api/library_dedup_scanner.py`
- Modify: `src/api/main.py:32-33,42-50`（导入并在 lifespan 里启动）
- Test: `scripts/test_library_dedup_scanner.py`（追加 3 个测试）

**Interfaces:**
- Consumes：Task 1-3 的全部原语（`tpl.load_templates`/`tpl.find_patrol_duplicate`/`tpl.merge_template_pair`/`tpl.apply_patrol_merge`/`tpl.delete_template`；`lesson.load_lessons`/`lesson.find_patrol_duplicate_lesson`/`lesson.merge_lesson_pair`/`lesson.apply_patrol_merge_lesson`/`lesson.delete_lesson`；`WebUIStore.library_dedup_scan_log_add`（经 `src.api.auth.get_store()` 取实例））。
- Produces：`start_library_dedup_scanner() -> None`（幂等启动函数，供 `main.py` 调用）；`LibraryDedupScanner` 类（供测试直接实例化调用 `_run_one_scan()`）。

- [ ] **Step 1: 新建 `src/api/library_dedup_scanner.py`**

```python
"""
模板库/教训库定时巡检：默认关闭的独立轮询协程。

风格参照 src/api/cookie_health_scanner.py（异步轮询、开关关闭时空转、可被 stop() 打断），
但业务完全独立、不共用类——这里巡检的是知识库自身的语义去重与停滞清理，与 Cookie 健康
无关。每轮巡检对模板库、教训库各自：① 按 data_type 语义去重，发现重复对自动融合合并
（保留使用统计更高的一方，另一方删除）；② 清理长期停滞（创建后超过阈值天数仍是 draft）
的条目。结果写入 webui.db 的 library_dedup_scan_log 表。
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Optional

from src.config.settings import settings

logger = logging.getLogger(__name__)

_IDLE_CHECK_SECONDS = 300.0   # 巡检关闭/未到点时，多久再检查一次
_BETWEEN_ITEM_SECONDS = 5.0   # 同一轮巡检内，相邻两次合并之间的等待


class LibraryDedupScanner:
    """异步轮询协程：开关关闭时空转，开启且到点时对模板库/教训库各跑一轮去重+停滞清理。"""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._last_scan_at: float = 0.0

    def start(self) -> None:
        """启动后台轮询（幂等）。需在已有事件循环内调用。"""
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _dedup_pass_templates(self) -> tuple[int, int]:
        """模板库去重扫描：返回 (scanned, merged)。"""
        from src.memory import templates as tpl

        entries = tpl.load_templates()
        scanned = len(entries)
        merged_slugs: set = set()
        merged = 0
        for entry in entries:
            if merged >= settings.library_dedup_scan_max_merges_per_run:
                break
            if entry["slug"] in merged_slugs or entry["status"] == "retired":
                continue
            dup = tpl.find_patrol_duplicate(entry)
            if not dup or dup["slug"] in merged_slugs:
                continue
            survivor, loser = (entry, dup) if entry["uses"] >= dup["uses"] else (dup, entry)
            fused = await tpl.merge_template_pair(survivor, loser)
            if not fused:
                continue
            if not tpl.apply_patrol_merge(survivor["slug"], fused):
                continue
            tpl.delete_template(loser["slug"])
            merged_slugs.add(survivor["slug"])
            merged_slugs.add(loser["slug"])
            merged += 1
            await self._sleep(_BETWEEN_ITEM_SECONDS)
        return scanned, merged

    async def _dedup_pass_lessons(self) -> tuple[int, int]:
        """教训库去重扫描：返回 (scanned, merged)。"""
        from src.memory import lessons as lsn

        entries = lsn.load_lessons()
        scanned = len(entries)
        merged_slugs: set = set()
        merged = 0
        for entry in entries:
            if merged >= settings.library_dedup_scan_max_merges_per_run:
                break
            if entry["slug"] in merged_slugs:
                continue
            dup = lsn.find_patrol_duplicate_lesson(entry)
            if not dup or dup["slug"] in merged_slugs:
                continue
            survivor, loser = (entry, dup) if entry["occurrences"] >= dup["occurrences"] else (dup, entry)
            fused = await lsn.merge_lesson_pair(survivor, loser)
            if not fused:
                continue
            if not lsn.apply_patrol_merge_lesson(survivor["slug"], fused):
                continue
            lsn.delete_lesson(loser["slug"])
            merged_slugs.add(survivor["slug"])
            merged_slugs.add(loser["slug"])
            merged += 1
            await self._sleep(_BETWEEN_ITEM_SECONDS)
        return scanned, merged

    def _stale_pass_templates(self) -> int:
        """模板库停滞草稿清理：返回删除数量。"""
        from src.memory import templates as tpl

        now = datetime.now()
        deleted = 0
        for entry in tpl.load_templates():
            if entry["status"] != "draft" or not entry.get("created_at"):
                continue
            try:
                created = datetime.fromisoformat(entry["created_at"])
            except ValueError:
                continue
            if (now - created).days > settings.library_stale_draft_days:
                if tpl.delete_template(entry["slug"]):
                    deleted += 1
        return deleted

    def _stale_pass_lessons(self) -> int:
        """教训库停滞草稿清理：返回删除数量。"""
        from src.memory import lessons as lsn

        now = datetime.now()
        deleted = 0
        for entry in lsn.load_lessons():
            if entry["status"] != "draft" or not entry.get("created_at"):
                continue
            try:
                created = datetime.fromisoformat(entry["created_at"])
            except ValueError:
                continue
            if (now - created).days > settings.library_stale_draft_days:
                if lsn.delete_lesson(entry["slug"]):
                    deleted += 1
        return deleted

    async def _run_one_scan(self) -> None:
        from src.api.auth import get_store

        logger.info("模板库/教训库定时巡检：开始一轮扫描")
        templates_scanned, templates_merged = await self._dedup_pass_templates()
        lessons_scanned, lessons_merged = await self._dedup_pass_lessons()
        stale_deleted = self._stale_pass_templates() + self._stale_pass_lessons()
        try:
            get_store().library_dedup_scan_log_add(
                templates_scanned=templates_scanned,
                templates_merged=templates_merged,
                lessons_scanned=lessons_scanned,
                lessons_merged=lessons_merged,
                stale_drafts_deleted=stale_deleted,
            )
        except Exception:
            logger.warning("巡检日志落库失败（不影响本轮巡检结果）", exc_info=True)
        logger.info(
            "模板库/教训库定时巡检：本轮完成（模板 %d扫/%d合，教训 %d扫/%d合，停滞清理 %d条）",
            templates_scanned, templates_merged, lessons_scanned, lessons_merged, stale_deleted,
        )

    async def _loop(self) -> None:
        while not self._stop.is_set():
            if not settings.library_dedup_scan_enabled:
                await self._sleep(_IDLE_CHECK_SECONDS)
                continue
            interval_seconds = max(1, settings.library_dedup_scan_interval_hours) * 3600.0
            if time.time() - self._last_scan_at < interval_seconds:
                await self._sleep(min(_IDLE_CHECK_SECONDS, interval_seconds))
                continue
            try:
                await self._run_one_scan()
            except Exception as e:  # noqa: BLE001 扫描本身意外出错也不能让循环死掉
                logger.exception("模板库/教训库定时巡检：本轮扫描异常：%s", e)
            self._last_scan_at = time.time()


_scanner: Optional[LibraryDedupScanner] = None


def start_library_dedup_scanner() -> None:
    """幂等启动。即使巡检开关关闭也会启动循环本身（循环内部自己空转直到开关打开），
    这样管理员切换开关不需要重启进程。需在已有事件循环内调用（FastAPI 启动钩子）。"""
    global _scanner
    if _scanner is not None:
        return
    _scanner = LibraryDedupScanner()
    _scanner.start()
```

- [ ] **Step 2: 挂载进 `src/api/main.py`**

第 32-33 行：

```python
from src.api.services import start_scheduler  # noqa: E402
from src.api.cookie_health_scanner import start_cookie_health_scanner  # noqa: E402
```

改为：

```python
from src.api.services import start_scheduler  # noqa: E402
from src.api.cookie_health_scanner import start_cookie_health_scanner  # noqa: E402
from src.api.library_dedup_scanner import start_library_dedup_scanner  # noqa: E402
```

第 42-50 行的 `lifespan`：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 先套用管理员在前端保存的全局运行时配置（.env 为兜底基线），再拉起调度器
    from src.api.auth import get_store
    from src.config.runtime_config import apply_global_overrides
    apply_global_overrides(get_store())
    start_scheduler()  # 启用时拉起定时任务后台轮询
    start_cookie_health_scanner()  # Cookie 健康巡检：循环常驻，开关关闭时内部自己空转
    yield
```

改为：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 先套用管理员在前端保存的全局运行时配置（.env 为兜底基线），再拉起调度器
    from src.api.auth import get_store
    from src.config.runtime_config import apply_global_overrides
    apply_global_overrides(get_store())
    start_scheduler()  # 启用时拉起定时任务后台轮询
    start_cookie_health_scanner()  # Cookie 健康巡检：循环常驻，开关关闭时内部自己空转
    start_library_dedup_scanner()  # 模板库/教训库定时巡检：循环常驻，开关关闭时内部自己空转
    yield
```

- [ ] **Step 3: 写失败的测试（追加到 `scripts/test_library_dedup_scanner.py`）**

在顶部 import 块的 `import src.memory.embeddings as emb` 之后追加：

```python
from src.api.library_dedup_scanner import LibraryDedupScanner
```

在 `test_apply_patrol_merge_lesson_keeps_stats_updates_content` 函数之后、`def main():` 之前插入：

```python
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
```

同时把 `main()` 里的 `tests` 列表追加最后 3 项：

```python
        test_run_one_scan_merges_and_logs,
        test_run_one_scan_deletes_stale_drafts,
        test_run_one_scan_skips_dedup_when_embedding_disabled,
```

- [ ] **Step 4: 运行测试确认通过**

Run: `E:/python3.13/python.exe scripts/test_library_dedup_scanner.py`
Expected: `18/18 通过`

- [ ] **Step 5: Commit**

```bash
git add src/api/library_dedup_scanner.py src/api/main.py scripts/test_library_dedup_scanner.py
git commit -m "feat: 新增 LibraryDedupScanner 定时巡检器，main.py lifespan 挂载"
```

---

## Task 5: 巡检报告路由 + 前端第 3 个 Tab + 文档同步

**Files:**
- Create: `src/api/routes/library_dedup_routes.py`
- Modify: `src/api/main.py:34-37,63-66`（导入并挂载新路由）
- Modify: `frontend/src/pages/Templates.tsx`（全量重写，加第 3 个 Tab）
- Modify: `README_AGENT.md`、`AGENTS.md`（新增变更记录）

**Interfaces:**
- Consumes：Task 1 的 `WebUIStore.library_dedup_scan_log_recent(limit=20)`。
- Produces：`GET /api/library-dedup-log`（任意登录用户可读，返回 `{"log": [...]}`）。

- [ ] **Step 1: 新建 `src/api/routes/library_dedup_routes.py`**

```python
"""模板库/教训库定时巡检报告路由：只读展示最近若干轮巡检摘要。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import get_current_user, get_store

router = APIRouter(prefix="/api/library-dedup-log", tags=["library-dedup-log"])


@router.get("")
def list_scan_log(user=Depends(get_current_user)):
    """返回最近 20 轮巡检记录（按时间倒序）。所有登录用户可读。"""
    return {"log": get_store().library_dedup_scan_log_recent(limit=20)}
```

- [ ] **Step 2: 挂载进 `src/api/main.py`**

第 34-37 行：

```python
from src.api.routes import (  # noqa: E402
    admin_routes, auth_routes, chat, config_routes, confirm, conversations, downloads,
    lessons_routes, memory_routes, models, overview, settings_routes, tasks, templates_routes,
)
```

改为：

```python
from src.api.routes import (  # noqa: E402
    admin_routes, auth_routes, chat, config_routes, confirm, conversations, downloads,
    lessons_routes, library_dedup_routes, memory_routes, models, overview, settings_routes,
    tasks, templates_routes,
)
```

第 63-66 行：

```python
for r in (auth_routes, conversations, chat, confirm, tasks, models, downloads,
          memory_routes, overview, templates_routes, lessons_routes, settings_routes,
          admin_routes, config_routes):
    app.include_router(r.router)
```

改为：

```python
for r in (auth_routes, conversations, chat, confirm, tasks, models, downloads,
          memory_routes, overview, templates_routes, lessons_routes, library_dedup_routes,
          settings_routes, admin_routes, config_routes):
    app.include_router(r.router)
```

- [ ] **Step 3: 全量替换 `frontend/src/pages/Templates.tsx`**

在文件当前内容基础上（模板库/教训库两个 Tab 不变），追加第 3 个 Tab。把 `type TabKey = "templates" | "lessons";` 改为：

```tsx
type TabKey = "templates" | "lessons" | "scanLog";
```

新增接口（放在 `interface Lesson {...}` 之后）：

```tsx
interface ScanLogRow {
  id: number;
  ran_at: string;
  templates_scanned: number;
  templates_merged: number;
  lessons_scanned: number;
  lessons_merged: number;
  stale_drafts_deleted: number;
}
```

在 `pendingLessonDel` 状态声明之后追加巡检报告的状态：

```tsx
  // 巡检报告状态（只读，无 preview/delete）
  const [scanLog, setScanLog] = useState<ScanLogRow[]>([]);
  const [scanLogLoading, setScanLogLoading] = useState(true);
```

在 `loadLessons`/`useEffect(loadLessons, [])` 之后追加：

```tsx
  const loadScanLog = () => {
    setScanLogLoading(true);
    api
      .get("/api/library-dedup-log")
      .then((d) => setScanLog(d.log || []))
      .catch(() => {})
      .finally(() => setScanLogLoading(false));
  };
  useEffect(loadScanLog, []);
```

header 里的标题/说明三元表达式（`{tab === "templates" ? "模板库" : "教训库"}` 那两处）改为按三态判断：

```tsx
          <h1 className="text-lg font-semibold tracking-tight">
            {tab === "templates" ? "模板库" : tab === "lessons" ? "教训库" : "巡检报告"}
          </h1>
          <p className="text-sm text-muted-foreground">
            {tab === "templates"
              ? "自学习沉淀的分析模板（草稿达标转正、低质淘汰）· 全局共享"
              : tab === "lessons"
              ? "自学习沉淀的失败教训（累计命中2次转正、planner/analyze 自动注入提醒）· 全局共享"
              : "定时巡检最近记录（语义去重合并 + 长期停滞草稿清理，默认关闭）"}
          </p>
```

Tab 切换按钮组，在"教训库"按钮之后追加"巡检报告"按钮：

```tsx
            <Button
              variant={tab === "scanLog" ? "default" : "ghost"}
              size="sm"
              onClick={() => setTab("scanLog")}
              className="h-7"
            >
              巡检报告
            </Button>
```

刷新按钮的 `onClick` 从二态改为三态：

```tsx
          <Button
            variant="outline"
            size="sm"
            onClick={tab === "templates" ? load : tab === "lessons" ? loadLessons : loadScanLog}
            className="gap-1.5"
          >
            <RefreshCw className="h-4 w-4" /> 刷新
          </Button>
```

模板库/教训库两个 `{tab === "templates" ? (...) : (...)}` 内容块外层的三元判断结构改为：先判断 `tab === "templates"`，再判断 `tab === "lessons"`，否则渲染巡检报告列表。即把原来的：

```tsx
      {tab === "templates" ? (
        <div className="flex-1 overflow-y-auto px-7 py-6">
          {/* 模板库内容，不变 */}
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto px-7 py-6">
          {/* 教训库内容，不变 */}
        </div>
      )}
```

改为：

```tsx
      {tab === "templates" ? (
        <div className="flex-1 overflow-y-auto px-7 py-6">
          {/* 模板库内容，不变 */}
        </div>
      ) : tab === "lessons" ? (
        <div className="flex-1 overflow-y-auto px-7 py-6">
          {/* 教训库内容，不变 */}
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto px-7 py-6">
          {scanLogLoading ? (
            <p className="text-sm text-muted-foreground">加载中…</p>
          ) : !scanLog.length ? (
            <div className="mx-auto max-w-md py-16 text-center">
              <Library className="mx-auto mb-3 h-10 w-10 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">
                暂无巡检记录（巡检开关默认关闭，需在 .env 开启
                <code className="mx-1 rounded bg-muted px-1">LIBRARY_DEDUP_SCAN_ENABLED</code>
                后台生效）。
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {scanLog.map((row) => (
                <Card key={row.id}>
                  <CardContent className="flex flex-wrap items-center gap-4 p-4 text-sm">
                    <span className="text-muted-foreground">{row.ran_at}</span>
                    <span>
                      模板：扫描 {row.templates_scanned} · 合并 {row.templates_merged}
                    </span>
                    <span>
                      教训：扫描 {row.lessons_scanned} · 合并 {row.lessons_merged}
                    </span>
                    <span>清理停滞草稿 {row.stale_drafts_deleted} 条</span>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </div>
      )}
```

（模板库/教训库两处 `{/* ... 不变 */}` 占位注释代表当前文件里原有的完整卡片渲染代码，原样保留，不做任何改动。）

- [ ] **Step 4: 编译验证**

Run: `cd frontend && npm run build`
Expected: 编译通过，无 TypeScript 报错。

- [ ] **Step 5: 手工验证**

1. 后端重启进程
2. 浏览器访问 8088，进入模板库页面，确认出现第 3 个"巡检报告"Tab
3. 巡检开关默认关闭，`data/webui.db` 里 `library_dedup_scan_log` 应为空表，确认前端显示空状态文案
4. （可选）手动开启 `LIBRARY_DEDUP_SCAN_ENABLED=True` 并调小间隔，等一轮巡检后确认报告 Tab 出现一行记录

- [ ] **Step 6: 更新 `README_AGENT.md`**

在第 15.6 节（"教训库前端管理页面"或最新一节，需先确认当前 README_AGENT.md 实际的最新章节编号）之后，追加新一节（编号顺延），内容参照以下模板撰写（若实际测试项数量与本步骤所写不一致，以实际跑出的数字为准回填）：

```markdown
## 15.7 模板库/教训库定时巡检（2026-07-10 新增）

模板库、教训库此前完全被动——只有任务真实触发时才去重/累积统计。新增独立后台巡检器
`src/api/library_dedup_scanner.py`（`LibraryDedupScanner`，风格仿照 `CookieHealthScanner`
但业务完全独立），默认关闭（`LIBRARY_DEDUP_SCAN_ENABLED`），开启后按
`LIBRARY_DEDUP_SCAN_INTERVAL_HOURS`（默认24小时）定期对 `data/templates/`、`data/lessons/`
各自：① 按 `data_type` 两两语义去重，发现重复自动融合合并（LLM 融合正文，保留使用统计更高
的一方且统计不清零，复用两个新增的巡检专用 Prompt `TEMPLATE_PAIR_MERGE_SYSTEM`/
`LESSON_PAIR_MERGE_SYSTEM`，不复用业务实时路径的 Curator/蒸馏 Prompt——巡检已用确定性
rerank 阈值确认过重复，不需要再判一次"要不要合并"，混用不同框架的 Prompt 容易让模型输出
对不上）；② 清理创建超过 `LIBRARY_STALE_DRAFT_DAYS`（默认30天）仍未转正的草稿（新建条目
新增 `created_at` frontmatter 字段，历史条目没有这个字段则跳过停滞判定，不追溯删除）。
去重扫描在 embedding 不可用时直接跳过本轮（不退回关键词兜底，避免弱匹配误合并）。
每轮巡检结果写入 `webui.db` 新表 `library_dedup_scan_log`，新增只读接口
`GET /api/library-dedup-log`，前端模板库页面新增第 3 个"巡检报告"Tab 展示最近记录。
回归：新增 `scripts/test_library_dedup_scanner.py`（18 项）。
```

- [ ] **Step 7: 更新 `AGENTS.md`**

在教训分流那一行（`- 失败教训分流 src/memory/lessons.py（...）；`）之后新增一行：

```markdown
- 模板库/教训库定时巡检 `src/api/library_dedup_scanner.py`（默认关闭，开启后定期对两个知识库做语义去重合并 + 长期停滞草稿清理，结果记 `webui.db.library_dedup_scan_log` 供前端"巡检报告"Tab 展示）；
```

- [ ] **Step 8: Commit**

```bash
git add src/api/routes/library_dedup_routes.py src/api/main.py \
        frontend/src/pages/Templates.tsx README_AGENT.md AGENTS.md
git commit -m "feat: 巡检报告只读接口 + 前端第3个Tab，文档同步"
```
