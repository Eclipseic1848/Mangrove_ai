# 模板库自学习闭环优化 · B1 阶段（Curator 合并裁决）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `save_template()` 的二元去重（复用旧的/新建）升级为 Curator 三态裁决（新建/合并吸收新洞察/丢弃零增量内容），让模板库越用越浓缩而不是越用越臃肿。

**Architecture:** 新增候选召回辅助函数（从现有 `find_duplicate_semantic` 提取共享逻辑）→ 新增 `curate_template()` 用 LLM 对 top-3 候选做三态裁决（复用现有 `distill_template`/`achat`/`parse_json_obj` 模式）→ `save_template()` 改为 async，按裁决结果做四种文件操作（新建/合并重写/复用不改动/丢弃不写）→ 两个调用方（`checker.py` 自动沉淀、`confirm.py` 人工确认按钮）改为 `await`。任何环节失效都归一退回 A 阶段已验证的 `find_duplicate_semantic()` 二元逻辑，不致瘫。

**Tech Stack:** Python 3.13，复用现有 embedding/rerank HTTP 基建，无新依赖。

## Global Constraints

- Python 解释器用 `E:/python3.13/python.exe`（不用裸 `python`/`python3`）
- 测试遵循项目无 pytest 约定：`scripts/test_*.py`，`def test_x(): assert ...`，`main()` 收集 PASS/FAIL 并 `sys.exit(1)` 失败退出
- 写入含中文内容的文件后用 `iconv -f utf-8 -t utf-8 <file> > /dev/null` 校验编码
- Curator 候选召回：按 `data_type` 过滤非淘汰模板，取余弦相似度 **top-3**（`_CURATOR_TOP_K = 3`，模块内常量非配置项），过滤掉余弦低于 `template_curator_candidate_min_cosine`（默认 **0.3**）的候选
- Curator 裁决三态：`new`（新建）/ `merge`（合并，LLM 产出完整重写正文）/ `discard`（丢弃，不写任何文件）；降级路径产出的第四态 `reuse`（复用旧内容、不改动任何文件）仅供 `save_template()` 内部消费，不出现在 Curator 正常裁决里
- 合并时**保留**目标模板原有的 `uses`/`quality_avg`/`status`（模板身份未变，历史使用统计不应被内容更新清零），只更新 `title`/`keywords`（新旧并集去重）/`body`；同时清除该 slug 在 `_vectors.json` 里的缓存条目
- 两层降级（都归一到调用 `find_duplicate_semantic()`）：① 候选召回不可用（`embedding_enabled=False` 或 embedding 端点调用失败）；② 候选召回成功但 Curator LLM 调用失败 / 输出解析失败（含 `decision=merge` 但 `slug` 不在候选列表里的情况）
- `save_template()` 签名从 `-> str` 改为 `-> Optional[str]`，且从同步函数改为 **async**（因为内部要 `await curate_template()`）；两个调用方 `checker.py`/`confirm.py` 相应改为 `await save_template(...)` 并处理 `None`（`None` 表示 Curator 判定丢弃，未写任何文件）
- 不改变 A 阶段已有的 `_looks_like_collection_failure`、质量门转正/淘汰/死区逻辑、`match_template`/`_match_semantic` 召回逻辑

---

### Task 1: 候选召回辅助函数 `_semantic_candidates`

**Files:**
- Modify: `src/memory/templates.py`（新增 `_semantic_candidates`，重构 `find_duplicate_semantic` 内部实现改为调用它，行为完全不变）
- Modify: `scripts/test_template_learning.py`（新增 `_write_template` 测试辅助函数 + 1 个新测试）

**Interfaces:**
- Consumes：`src/memory/templates.py` 现有 `load_templates()`、`_template_text(t)`、`find_duplicate(data_type, keywords)`；`src/memory/embeddings.py` 现有 `embed_texts_with_model`、`cosine`、`is_rerank_configured`、`rerank_scores`
- Produces：`_semantic_candidates(data_type: str, keywords: List[str], title: str, top_k: int, min_cosine: float = 0.0) -> Optional[List[Dict]]` —— 返回 `None` 表示候选召回不可用（`embedding_enabled=False` 或 embedding 调用失败），返回 `[]` 或非空列表表示可用（哪怕列表为空，也不该被调用方当作"不可用"处理）。Task 2 的 `curate_template` 将直接调用此函数。

- [ ] **Step 1: 写候选过滤的失败测试**

`scripts/test_template_learning.py` 顶部 import 区，把：

```python
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import settings
from src.conductor.task_spec import DataType, TaskSpec
import src.memory.templates as tpl
import src.memory.embeddings as emb
```

改为（新增 `yaml` import，供下面新增的 `_write_template` 辅助函数使用）：

```python
import sys
import tempfile
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import settings
from src.conductor.task_spec import DataType, TaskSpec
import src.memory.templates as tpl
import src.memory.embeddings as emb
```

在 `_setup_tmp()` 函数之后插入一个测试辅助函数（不是 `test_` 开头，不会被当作测试用例执行）：

```python
def _write_template(d, slug, title, data_type, keywords, body, status="active", uses=0, quality_avg=0):
    """直接写一个模板文件到临时目录（跳过 save_template，避免测试耦合到保存/去重逻辑本身）。"""
    front = yaml.safe_dump(
        {"title": title, "data_type": data_type, "keywords": keywords,
         "status": status, "uses": uses, "quality_avg": quality_avg},
        allow_unicode=True, sort_keys=False,
    ).strip()
    (d / f"{slug}.md").write_text(f"---\n{front}\n---\n{body}\n", encoding="utf-8")
```

在 `test_dead_zone_forces_retire` 函数之后（`main()` 之前）插入新测试：

```python
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
```

在 `main()` 的 `tests = [...]` 列表末尾追加：

```python
        test_semantic_candidates_filters_by_min_cosine,
```

- [ ] **Step 2: 运行确认测试因函数不存在而失败**

Run: `E:/python3.13/python.exe scripts/test_template_learning.py`
Expected: `AttributeError: module 'src.memory.templates' has no attribute '_semantic_candidates'`

- [ ] **Step 3: 实现 `_semantic_candidates`，重构 `find_duplicate_semantic` 复用它**

`src/memory/templates.py` 里，`find_duplicate_semantic` 函数（当前实现如下）：

```python
def find_duplicate_semantic(data_type: str, keywords: List[str], title: str) -> Optional[Dict]:
    """语义去重：用 embedding 余弦 + rerank 精判"是否同一类模板"，
    比 find_duplicate() 的关键词 Jaccard 更能识别"措辞不同、结构相同"的近重复。
    embedding 关闭 / 端点不可用 / rerank 未配置时，退回 find_duplicate() 的关键词 Jaccard（不致瘫）。
    """
    from src.config.settings import settings
    from . import embeddings as emb

    if not settings.embedding_enabled:
        return find_duplicate(data_type, keywords)

    dt = (data_type or "").lower()
    cands = [t for t in load_templates() if t.get("status") != "retired" and t["data_type"] == dt]
    if not cands:
        return None

    query_text = (title or "") + " " + " ".join(keywords or [])
    got = emb.embed_texts_with_model([query_text])
    if not got or not got[1]:
        return find_duplicate(data_type, keywords)
    model, qvec = got[0], got[1][0]

    cand_texts = [_template_text(t) for t in cands]
    got2 = emb.embed_texts_with_model(cand_texts)
    if not got2 or got2[0] != model or len(got2[1]) != len(cands):
        return find_duplicate(data_type, keywords)

    scored = sorted(zip(cands, got2[1]), key=lambda x: emb.cosine(qvec, x[1]), reverse=True)
    top = [t for t, _ in scored[:5]]
    if not emb.is_rerank_configured():
        return find_duplicate(data_type, keywords)

    rscores = emb.rerank_scores(query_text, [_template_text(t) for t in top], instruct=_DEDUP_RERANK_INSTRUCT)
    if not rscores:
        return find_duplicate(data_type, keywords)

    best_i = max(range(len(rscores)), key=rscores.__getitem__)
    if rscores[best_i] >= settings.template_dedup_rerank_threshold:
        return top[best_i]
    return None
```

整体替换为（提取候选召回部分为 `_semantic_candidates`，`find_duplicate_semantic` 改为调用它，行为完全不变——`cands` 为空时原来直接 `return None`，现在 `_semantic_candidates` 对应返回 `[]`，外层判断一致；`embedding_enabled=False` 时原来直接 `return find_duplicate(...)`，现在 `_semantic_candidates` 对应返回 `None`，外层同样退回 `find_duplicate`）：

```python
def _semantic_candidates(
    data_type: str, keywords: List[str], title: str, top_k: int, min_cosine: float = 0.0,
) -> Optional[List[Dict]]:
    """按语义余弦相似度取 top_k 候选（≥min_cosine），供去重/Curator 共用。
    返回 None 表示 embedding 不可用（调用方应退回更简单机制）；返回列表（可能为空）表示 embedding 可用。
    """
    from src.config.settings import settings
    from . import embeddings as emb

    if not settings.embedding_enabled:
        return None

    dt = (data_type or "").lower()
    cands = [t for t in load_templates() if t.get("status") != "retired" and t["data_type"] == dt]
    if not cands:
        return []

    query_text = (title or "") + " " + " ".join(keywords or [])
    got = emb.embed_texts_with_model([query_text])
    if not got or not got[1]:
        return None
    model, qvec = got[0], got[1][0]

    cand_texts = [_template_text(t) for t in cands]
    got2 = emb.embed_texts_with_model(cand_texts)
    if not got2 or got2[0] != model or len(got2[1]) != len(cands):
        return None

    scored = sorted(zip(cands, got2[1]), key=lambda x: emb.cosine(qvec, x[1]), reverse=True)
    return [t for t, sim in scored if sim >= min_cosine][:top_k]


def find_duplicate_semantic(data_type: str, keywords: List[str], title: str) -> Optional[Dict]:
    """语义去重：用 embedding 余弦 + rerank 精判"是否同一类模板"，
    比 find_duplicate() 的关键词 Jaccard 更能识别"措辞不同、结构相同"的近重复。
    embedding 关闭 / 端点不可用 / rerank 未配置时，退回 find_duplicate() 的关键词 Jaccard（不致瘫）。
    """
    from src.config.settings import settings
    from . import embeddings as emb

    top = _semantic_candidates(data_type, keywords, title, top_k=5)
    if top is None:
        return find_duplicate(data_type, keywords)
    if not top:
        return None

    if not emb.is_rerank_configured():
        return find_duplicate(data_type, keywords)

    query_text = (title or "") + " " + " ".join(keywords or [])
    rscores = emb.rerank_scores(query_text, [_template_text(t) for t in top], instruct=_DEDUP_RERANK_INSTRUCT)
    if not rscores:
        return find_duplicate(data_type, keywords)

    best_i = max(range(len(rscores)), key=rscores.__getitem__)
    if rscores[best_i] >= settings.template_dedup_rerank_threshold:
        return top[best_i]
    return None
```

- [ ] **Step 4: 运行确认新测试通过 + 既有测试无回归**

Run: `E:/python3.13/python.exe scripts/test_template_learning.py`
Expected: `12/12 通过`（原 11 个 + 新增 1 个）

- [ ] **Step 5: 提交**

```bash
git add src/memory/templates.py scripts/test_template_learning.py
git commit -m "refactor: 提取 _semantic_candidates 候选召回辅助函数，find_duplicate_semantic 复用之"
```

---

### Task 2: Curator 裁决函数 `curate_template`

**Files:**
- Modify: `src/config/settings.py`（模板自学习配置区）
- Modify: `src/conductor/prompts.py`（新增 `TEMPLATE_CURATOR_SYSTEM`）
- Modify: `src/memory/templates.py`（导入调整 + 新增 `curate_template`/`_fallback_decision`/`_CURATOR_TOP_K`）
- Modify: `scripts/test_template_learning.py`（新增 7 个测试）

**Interfaces:**
- Consumes：Task 1 的 `_semantic_candidates(data_type, keywords, title, top_k, min_cosine=0.0) -> Optional[List[Dict]]`；现有 `src.llm.achat`、`src.conductor.utils.parse_json_obj`
- Produces：`curate_template(title: str, data_type: str, keywords: List[str], body: str, *, provider: Optional[str] = None, model: Optional[str] = None) -> Dict`（async），返回值三态：`{"decision": "new"}` / `{"decision": "merge", "slug": str, "title": str, "keywords": List[str], "body": str}` / `{"decision": "discard"}`；降级路径额外产出 `{"decision": "reuse", "slug": str}`。Task 3 的 `save_template()` 消费这个返回值。

- [ ] **Step 1: 新增 Curator 候选下限配置**

`src/config/settings.py`，在 `template_dead_zone_uses` 字段（Task/A 阶段已有）之后插入：

```python
    template_curator_candidate_min_cosine: float = Field(default=0.3, description="Curator 候选召回的余弦相似度下限，低于此值的候选不纳入裁决（避免明显不相关内容干扰判断）")
```

- [ ] **Step 2: 新增 Curator 系统提示词**

`src/conductor/prompts.py`，在 `TEMPLATE_DISTILL_SYSTEM`（第 104-111 行）之后插入：

```python
# 模板库 Curator：蒸馏出新模板后，与库内相似候选一起交给它裁决新建/合并/丢弃（自学习阶段3）。
TEMPLATE_CURATOR_SYSTEM = """你是"分析报告模板库"的馆长（Curator）。给你一份新提炼出的模板与库内若干相似候选，
请判断三者之一：
- new：新内容与所有候选都不是同一类分析任务，应该新建一条模板
- merge：新内容与某个候选是同一类任务，但新内容有该候选未覆盖的信息（新的关键词维度、更完整的结构要点），
  应该把两者融合，产出一份完整重写的正文（不要简单拼接，要写成一份自洽、通用化的新版本）
- discard：新内容完全被某个候选覆盖，没有任何增量信息，不需要做任何改动

只输出一个 JSON 对象，不要任何额外文字：
{
  "decision": "new" | "merge" | "discard",
  "slug": "被合并候选的 slug（仅 decision=merge 时填写，必须是给你的候选列表里的某一个 slug）",
  "title": "合并后的标题（仅 merge；没有明显理由就沿用旧标题）",
  "keywords": ["合并后的关键词，新旧并集去重（仅 merge）"],
  "body": "完整重写的融合正文（仅 merge，一段中文 system prompt，描述这类任务应输出的报告结构，通用化、不要复述具体数据）"
}"""
```

- [ ] **Step 3: 调整 templates.py 顶部导入，为 `curate_template` 铺路**

`src/memory/templates.py` 顶部（第 16-29 行）当前是：

```python
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from src.config.settings import PROJECT_ROOT
from src.conductor.task_spec import TaskSpec
from src.memory._frontmatter import FrontmatterError, parse_frontmatter
```

改为（新增 3 个此前分散在 `distill_template` 函数体内的延迟 import，提到模块级——已确认 `src.llm`/`src.conductor.prompts`/`src.conductor.utils` 均不反向依赖 `src.memory`，无循环依赖风险；提到模块级同时是为了让 `curate_template` 与既有 `distill_template` 共用同一份、可在测试里通过 `patch("src.memory.templates.achat", ...)` 直接打桩的模块级名字）：

```python
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from src.config.settings import PROJECT_ROOT
from src.conductor.prompts import TEMPLATE_CURATOR_SYSTEM, TEMPLATE_DISTILL_SYSTEM
from src.conductor.task_spec import TaskSpec
from src.conductor.utils import parse_json_obj
from src.llm import achat
from src.memory._frontmatter import FrontmatterError, parse_frontmatter
```

然后把 `distill_template` 函数体（第 220-257 行）内、现在已变成重复的 3 行延迟 import 删掉。当前：

```python
async def distill_template(
    intent: str,
    data_type: str,
    analysis: str,
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Optional[Dict]:
    """用 LLM 把一次报告蒸馏成可复用模板，返回 {title, keywords, body}；失败或无正文返回 None。

    前端「沉淀为模板」按钮与后端 Checker 自动沉淀共用此函数（延迟 import 避免顶层循环依赖）。
    """
    from src.llm import achat
    from src.conductor.prompts import TEMPLATE_DISTILL_SYSTEM
    from src.conductor.utils import parse_json_obj

    raw = await achat(
```

改为：

```python
async def distill_template(
    intent: str,
    data_type: str,
    analysis: str,
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Optional[Dict]:
    """用 LLM 把一次报告蒸馏成可复用模板，返回 {title, keywords, body}；失败或无正文返回 None。

    前端「沉淀为模板」按钮与后端 Checker 自动沉淀共用此函数。
    """
    raw = await achat(
```

（函数体其余部分不变。）

- [ ] **Step 4: 写 Curator 裁决的失败测试**

在 `scripts/test_template_learning.py` 顶部 import 区，把：

```python
import sys
import tempfile
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import settings
from src.conductor.task_spec import DataType, TaskSpec
import src.memory.templates as tpl
import src.memory.embeddings as emb
```

改为（新增 `asyncio`/`json`、`unittest.mock.patch`）：

```python
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
```

在 `_write_template` 函数之后插入一个测试辅助函数：

```python
def _fake_achat(payload: dict):
    async def fake(messages, **kwargs):
        return json.dumps(payload, ensure_ascii=False)
    return fake
```

在 `test_semantic_candidates_filters_by_min_cosine` 函数之后（`main()` 之前）插入 7 个新测试：

```python
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
```

在 `main()` 的 `tests = [...]` 列表末尾追加：

```python
        test_curate_no_candidates_returns_new_without_llm,
        test_curate_llm_decides_new,
        test_curate_llm_decides_merge,
        test_curate_llm_decides_discard,
        test_curate_merge_rejects_slug_not_in_candidates,
        test_curate_falls_back_when_candidate_retrieval_unavailable,
        test_curate_falls_back_when_llm_call_fails,
```

- [ ] **Step 5: 运行确认测试因函数不存在而失败**

Run: `E:/python3.13/python.exe scripts/test_template_learning.py`
Expected: `AttributeError: module 'src.memory.templates' has no attribute 'curate_template'`

- [ ] **Step 6: 实现 `curate_template` 与 `_fallback_decision`**

`src/memory/templates.py` 里，`find_duplicate_semantic` 函数之后、`save_template` 函数之前，插入：

```python
_CURATOR_TOP_K = 3


def _fallback_decision(data_type: str, keywords: List[str], title: str) -> Dict:
    """Curator 候选召回不可用 / LLM 调用或解析失败时的降级：退回 A 阶段的语义/关键词二元去重。
    命中即视为"复用"（不改动目标模板任何内容），未命中则新建。"""
    dup = find_duplicate_semantic(data_type, keywords, title)
    if dup:
        return {"decision": "reuse", "slug": dup["slug"]}
    return {"decision": "new"}


async def curate_template(
    title: str, data_type: str, keywords: List[str], body: str,
    *, provider: Optional[str] = None, model: Optional[str] = None,
) -> Dict:
    """Curator 裁决：新建(new) / 合并进已有模板(merge) / 丢弃(discard) / 复用不改动(reuse，仅降级路径产出)。

    候选为空（新库/新 data_type，或全部候选低于余弦下限）直接判 new，不调用 LLM。
    候选召回不可用、或 LLM 调用/输出解析失败，均退回 _fallback_decision（A 阶段二元逻辑），不致瘫。
    """
    from src.config.settings import settings

    candidates = _semantic_candidates(
        data_type, keywords, title,
        top_k=_CURATOR_TOP_K,
        min_cosine=settings.template_curator_candidate_min_cosine,
    )
    if candidates is None:
        return _fallback_decision(data_type, keywords, title)
    if not candidates:
        return {"decision": "new"}

    cand_desc = "\n\n".join(
        f"候选{i + 1}（slug={c['slug']}）：\n标题：{c['title']}\n关键词：{', '.join(c['keywords'])}\n"
        f"正文：{c['body']}\n（已使用{c['uses']}次，平均质量分{c['quality_avg']}，状态{c['status']}）"
        for i, c in enumerate(candidates)
    )
    user = (
        f"新内容：\n标题：{title}\n关键词：{', '.join(keywords or [])}\n正文：{body}\n\n"
        f"库内候选：\n{cand_desc}"
    )
    try:
        raw = await achat(
            [
                {"role": "system", "content": TEMPLATE_CURATOR_SYSTEM},
                {"role": "user", "content": user},
            ],
            provider=provider,
            model=model,
        )
    except Exception:
        logger.warning("Curator 调用失败，退回二元去重逻辑", exc_info=True)
        return _fallback_decision(data_type, keywords, title)

    data = parse_json_obj(raw)
    decision = str(data.get("decision") or "").strip().lower()
    if decision == "new":
        return {"decision": "new"}
    if decision == "discard":
        return {"decision": "discard"}
    if decision == "merge":
        slug = str(data.get("slug") or "").strip()
        new_body = str(data.get("body") or "").strip()
        valid_slugs = {c["slug"] for c in candidates}
        if slug in valid_slugs and new_body:
            kws = data.get("keywords") or []
            if isinstance(kws, str):
                kws = [kws]
            return {
                "decision": "merge",
                "slug": slug,
                "title": str(data.get("title") or "").strip(),
                "keywords": [str(k).strip() for k in kws if str(k).strip()],
                "body": new_body,
            }

    logger.warning("Curator 输出解析失败或字段不完整，退回二元去重逻辑：%r", data)
    return _fallback_decision(data_type, keywords, title)
```

- [ ] **Step 7: 运行确认全部通过**

Run: `E:/python3.13/python.exe scripts/test_template_learning.py`
Expected: `19/19 通过`（原 12 个 + 新增 7 个）

- [ ] **Step 8: 提交**

```bash
git add src/config/settings.py src/conductor/prompts.py src/memory/templates.py scripts/test_template_learning.py
git commit -m "feat: 新增 Curator 裁决函数 curate_template（新建/合并/丢弃三态 + 两层降级）"
```

---

### Task 3: `save_template` 异步四态改造 + 调用方适配

**Files:**
- Modify: `src/memory/templates.py`（`save_template` 改为 async，四态应用逻辑）
- Modify: `src/conductor/nodes/checker.py`（`await save_template`，处理 `None`）
- Modify: `src/api/routes/confirm.py`（`await save_template`，处理 `None`）
- Modify: `scripts/test_template_learning.py`（9 个既有测试的 `save_template` 调用改为 `asyncio.run(...)`，新增 2 个测试）
- Modify: `scripts/test_checker_rerun.py`（1 个既有测试的 mock 改为 `AsyncMock`，新增 1 个测试）

**Interfaces:**
- Consumes：Task 2 的 `curate_template(...) -> Dict`（`decision` 字段为 `new`/`merge`/`discard`/`reuse` 之一）
- Produces：`save_template(title: str, data_type: str, keywords: List[str], body: str) -> Optional[str]`（**async**，`None` 表示 Curator 判定丢弃，未写任何文件）——`checker.py`/`confirm.py` 消费

- [ ] **Step 1: 写 `save_template` 新增行为的失败测试**

在 `scripts/test_template_learning.py` 里，把现有 9 个调用 `tpl.save_template(...)` 的测试改为用 `asyncio.run(...)` 包裹（Task 1/2 新增的测试不受影响，它们没有直接调用 `save_template`）。

把：

```python
def test_save_is_draft():
    _setup_tmp()
    slug = tpl.save_template("政策解读报告", "article", ["政策", "解读"], "正文结构...")
    t = [x for x in tpl.load_templates() if x["slug"] == slug][0]
    assert t["status"] == "draft" and t["uses"] == 0, t
```

改为：

```python
def test_save_is_draft():
    _setup_tmp()
    slug = asyncio.run(tpl.save_template("政策解读报告", "article", ["政策", "解读"], "正文结构..."))
    t = [x for x in tpl.load_templates() if x["slug"] == slug][0]
    assert t["status"] == "draft" and t["uses"] == 0, t
```

把：

```python
def test_dedup_reuses_existing():
    _setup_tmp()
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False  # 关闭语义去重，确定性走关键词 Jaccard 兜底（不连真实网络）
    try:
        s1 = tpl.save_template("产品测评A", "product", ["测评", "参数", "卖点"], "结构A")
        # 关键词高度重叠（Jaccard=2/3≈0.67≥0.6）→ 应复用 s1，不新建
        s2 = tpl.save_template("产品测评B", "product", ["测评", "参数"], "结构B")
        assert s2 == s1, (s1, s2)
        assert len(tpl.load_templates()) == 1
    finally:
        settings.embedding_enabled = old_enabled
```

改为：

```python
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
```

把：

```python
def test_no_dedup_different_datatype():
    _setup_tmp()
    old_enabled = settings.embedding_enabled
    settings.embedding_enabled = False
    try:
        s1 = tpl.save_template("X", "product", ["测评", "参数"], "a")
        s2 = tpl.save_template("Y", "bid", ["测评", "参数"], "b")  # 不同 data_type → 不算重复
        assert s2 != s1 and len(tpl.load_templates()) == 2
    finally:
        settings.embedding_enabled = old_enabled
```

改为：

```python
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
```

把：

```python
def test_semantic_dedup_reuses_similar_template():
    """语义去重命中：措辞完全不同、关键词零重叠，但语义描述同一类任务 → 应复用旧 slug。"""
    _setup_tmp()
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    old_is_rerank = emb.is_rerank_configured
    old_rerank = emb.rerank_scores
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test-model", [[1.0, 0.0, 0.0] for _ in texts])
    emb.is_rerank_configured = lambda: True
    emb.rerank_scores = lambda query, docs, instruct=None: [0.9] * len(docs)
    try:
        s1 = tpl.save_template("产品测评甲", "product", ["评测", "参数"], "结构A")
        # 关键词零重叠，但 mock 的 embedding/rerank 判定为同一类 → 应复用 s1
        s2 = tpl.save_template("完全不同措辞的产品体验报告", "product", ["体验", "口碑"], "结构B")
        assert s2 == s1, (s1, s2)
        assert len(tpl.load_templates()) == 1
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed
        emb.is_rerank_configured = old_is_rerank
        emb.rerank_scores = old_rerank
```

改为：

```python
def test_semantic_dedup_reuses_similar_template():
    """语义去重命中：措辞完全不同、关键词零重叠，但语义描述同一类任务 → 应复用旧 slug。"""
    _setup_tmp()
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    old_is_rerank = emb.is_rerank_configured
    old_rerank = emb.rerank_scores
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: ("test-model", [[1.0, 0.0, 0.0] for _ in texts])
    emb.is_rerank_configured = lambda: True
    emb.rerank_scores = lambda query, docs, instruct=None: [0.9] * len(docs)
    try:
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
```

把：

```python
def test_semantic_dedup_falls_back_to_jaccard_when_embedding_unavailable():
    """embedding 端点不可用（返回 None）→ 退回关键词 Jaccard，不应报错或漏判。"""
    _setup_tmp()
    old_enabled = settings.embedding_enabled
    old_embed = emb.embed_texts_with_model
    settings.embedding_enabled = True
    emb.embed_texts_with_model = lambda texts: None
    try:
        s1 = tpl.save_template("产品测评A", "product", ["测评", "参数", "卖点"], "结构A")
        s2 = tpl.save_template("产品测评B", "product", ["测评", "参数"], "结构B")
        assert s2 == s1, (s1, s2)
    finally:
        settings.embedding_enabled = old_enabled
        emb.embed_texts_with_model = old_embed
```

改为：

```python
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
```

把：

```python
def test_promote_draft_to_active():
    _setup_tmp()
    old = (settings.template_promote_uses, settings.template_promote_quality)
    settings.template_promote_uses, settings.template_promote_quality = 3, 70
    try:
        slug = tpl.save_template("新闻梳理", "article", ["新闻"], "结构")
```

改为：

```python
def test_promote_draft_to_active():
    _setup_tmp()
    old = (settings.template_promote_uses, settings.template_promote_quality)
    settings.template_promote_uses, settings.template_promote_quality = 3, 70
    try:
        slug = asyncio.run(tpl.save_template("新闻梳理", "article", ["新闻"], "结构"))
```

（该函数其余部分不变。）

把：

```python
def test_retire_low_quality():
    _setup_tmp()
    old = (settings.template_promote_uses, settings.template_retire_quality)
    settings.template_promote_uses, settings.template_retire_quality = 3, 50
    try:
        slug = tpl.save_template("烂模板", "generic", ["X"], "结构")
```

改为：

```python
def test_retire_low_quality():
    _setup_tmp()
    old = (settings.template_promote_uses, settings.template_retire_quality)
    settings.template_promote_uses, settings.template_retire_quality = 3, 50
    try:
        slug = asyncio.run(tpl.save_template("烂模板", "generic", ["X"], "结构"))
```

（该函数其余部分不变。）

把：

```python
def test_match_includes_draft_excludes_retired():
    _setup_tmp()
    slug = tpl.save_template("招商报告", "generic", ["招商", "园区"], "结构")  # draft
```

改为：

```python
def test_match_includes_draft_excludes_retired():
    _setup_tmp()
    slug = asyncio.run(tpl.save_template("招商报告", "generic", ["招商", "园区"], "结构"))  # draft
```

（该函数其余部分不变。）

把：

```python
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
        slug = tpl.save_template("死区模板", "generic", ["死区测试"], "结构")
```

改为：

```python
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
```

（该函数其余部分不变。）

现在新增 2 个测试。在 `test_curate_falls_back_when_llm_call_fails` 函数之后（`main()` 之前）插入：

```python
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
```

在 `main()` 的 `tests = [...]` 列表末尾追加：

```python
        test_save_template_discard_writes_nothing,
        test_save_template_merge_updates_body_keeps_stats_and_invalidates_cache,
```

- [ ] **Step 2: 运行确认测试失败（TypeError，因为 `save_template` 还不是 async）**

Run: `E:/python3.13/python.exe scripts/test_template_learning.py`
Expected: 大量 FAIL（`asyncio.run()` 包裹了一个非协程对象，或新增两个测试因裁决逻辑不存在而失败）

- [ ] **Step 3: 把 `save_template` 改为 async 四态实现**

`src/memory/templates.py` 里，当前的 `save_template`：

```python
def save_template(title: str, data_type: str, keywords: List[str], body: str) -> str:
    """保存一个学到的模板到 data/templates/<slug>.md（初始 status=draft），返回 slug。

    去重：若已存在同 data_type 且关键词高度重叠（Jaccard≥阈值）的非淘汰模板，则不新建、直接复用其 slug，
    避免近重复模板堆积。否则新建（同名自动加序号避免覆盖）。
    """
    dup = find_duplicate_semantic(data_type, keywords, title)
    if dup:
        logger.info("已存在近重复模板，复用不新建：%s", dup["slug"])
        return dup["slug"]

    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slugify(title)
    path = TEMPLATES_DIR / f"{slug}.md"
    i = 2
    while path.exists():
        path = TEMPLATES_DIR / f"{slug}-{i}.md"
        i += 1
    meta = {
        "title": title,
        "data_type": (data_type or "").lower(),
        "keywords": [k for k in (keywords or []) if k],
        "status": "draft",   # 新模板先进草稿区，达标后转正（见 record_template_use）
        "uses": 0,
        "quality_avg": 0,
    }
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    path.write_text(f"---\n{front}\n---\n{body.strip()}\n", encoding="utf-8")
    logger.info("已沉淀分析模板（草稿）：%s", path.name)
    return path.stem
```

整体替换为：

```python
async def save_template(title: str, data_type: str, keywords: List[str], body: str) -> Optional[str]:
    """经 Curator 裁决后保存一个学到的模板，返回其 slug；Curator 判定"丢弃"时返回 None（不写任何文件）。

    Curator 裁决新建/合并/丢弃（见 curate_template）；合并时更新目标模板的 title/keywords/body，
    使用统计（uses/quality_avg/status）保持不变；Curator 全链路不可用时退回 A 阶段的语义/关键词二元去重
    （降级路径产出 decision=reuse，等价于原来的"命中即复用、不改动内容"）。
    """
    decision = await curate_template(title, data_type, keywords, body)
    kind = decision.get("decision")

    if kind == "discard":
        logger.info("Curator 判定新内容对现有模板库无增量信息，不沉淀")
        return None

    if kind == "reuse":
        slug = decision["slug"]
        logger.info("降级逻辑判定已存在近重复模板，复用不新建：%s", slug)
        return slug

    if kind == "merge":
        slug = decision["slug"]
        path = TEMPLATES_DIR / f"{slug}.md"
        try:
            raw = path.read_text(encoding="utf-8")
            parsed = parse_frontmatter(raw)
        except (OSError, FrontmatterError):
            parsed = None
        if parsed is None:
            logger.warning("Curator 判定合并的目标模板读取/解析失败，跳过本次沉淀：%s", slug)
            return None
        meta, _old_body = parsed
        meta["title"] = decision["title"] or meta.get("title")
        meta["keywords"] = decision["keywords"] or meta.get("keywords")
        front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
        path.write_text(f"---\n{front}\n---\n{decision['body'].strip()}\n", encoding="utf-8")
        cache = _load_vectors()
        if slug in cache:
            cache.pop(slug, None)
            _save_vectors(cache)
        logger.info("Curator 裁决合并，已更新模板正文：%s", slug)
        return slug

    # kind == "new"（含任何未识别值的兜底，此时 curate_template 内部已保证只会是上述四态之一）
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slugify(title)
    path = TEMPLATES_DIR / f"{slug}.md"
    i = 2
    while path.exists():
        path = TEMPLATES_DIR / f"{slug}-{i}.md"
        i += 1
    meta = {
        "title": title,
        "data_type": (data_type or "").lower(),
        "keywords": [k for k in (keywords or []) if k],
        "status": "draft",   # 新模板先进草稿区，达标后转正（见 record_template_use）
        "uses": 0,
        "quality_avg": 0,
    }
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    path.write_text(f"---\n{front}\n---\n{body.strip()}\n", encoding="utf-8")
    logger.info("已沉淀分析模板（草稿）：%s", path.name)
    return path.stem
```

- [ ] **Step 4: 更新 `checker.py` 调用点**

`src/conductor/nodes/checker.py` 第 118-126 行，当前：

```python
            if tpl:
                slug = save_template(
                    title=tpl["title"],
                    data_type=spec.data_type.value,
                    keywords=tpl["keywords"] or list(spec.keywords or []),
                    body=tpl["body"],
                )
                out["template_saved"] = {"slug": slug, "title": tpl["title"]}
                logger.info("Checker 通过，已自动沉淀模板：%s", slug)
```

改为：

```python
            if tpl:
                slug = await save_template(
                    title=tpl["title"],
                    data_type=spec.data_type.value,
                    keywords=tpl["keywords"] or list(spec.keywords or []),
                    body=tpl["body"],
                )
                if slug:
                    out["template_saved"] = {"slug": slug, "title": tpl["title"]}
                    logger.info("Checker 通过，已自动沉淀模板：%s", slug)
                else:
                    logger.info("Curator 判定新内容对现有模板库无增量信息，本次不沉淀")
```

- [ ] **Step 5: 更新 `confirm.py` 调用点**

`src/api/routes/confirm.py` 第 55-73 行，当前：

```python
@router.post("/template")
async def confirm_template(body: ConfirmIn, user=Depends(get_current_user)):
    pend = pending_store.pop_action(user["user_id"], body.task_id, "template")
    if not pend or not pend.get("analysis"):
        raise HTTPException(status_code=404, detail="没有可沉淀的模板或已处理")
    try:
        tpl = await distill_template(pend["intent"], pend["data_type"], pend["analysis"],
                                     provider=pend.get("provider"), model=pend.get("model"))
        if not tpl:
            raise HTTPException(status_code=422, detail="未能提炼出有效模板结构，请重试")
        slug = save_template(title=tpl["title"], data_type=pend["data_type"],
                             keywords=tpl["keywords"] or pend.get("keywords") or [], body=tpl["body"])
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"沉淀模板失败：{e}")
    return {"ok": True, "message": f"已沉淀模板「{tpl['title']}」（{slug}），下次同类任务自动复用。",
            "slug": slug, "title": tpl["title"]}
```

改为：

```python
@router.post("/template")
async def confirm_template(body: ConfirmIn, user=Depends(get_current_user)):
    pend = pending_store.pop_action(user["user_id"], body.task_id, "template")
    if not pend or not pend.get("analysis"):
        raise HTTPException(status_code=404, detail="没有可沉淀的模板或已处理")
    try:
        tpl = await distill_template(pend["intent"], pend["data_type"], pend["analysis"],
                                     provider=pend.get("provider"), model=pend.get("model"))
        if not tpl:
            raise HTTPException(status_code=422, detail="未能提炼出有效模板结构，请重试")
        slug = await save_template(title=tpl["title"], data_type=pend["data_type"],
                                   keywords=tpl["keywords"] or pend.get("keywords") or [], body=tpl["body"])
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"沉淀模板失败：{e}")
    if slug is None:
        return {"ok": True, "message": f"「{tpl['title']}」的内容已被现有模板库覆盖，未新增/更新模板。",
                "slug": None, "title": tpl["title"]}
    return {"ok": True, "message": f"已沉淀模板「{tpl['title']}」（{slug}），下次同类任务自动复用。",
            "slug": slug, "title": tpl["title"]}
```

- [ ] **Step 6: 修复 `test_checker_rerun.py` 里因 `save_template` 变 async 而失效的 mock**

`scripts/test_checker_rerun.py` 顶部 import 区，把：

```python
from unittest.mock import patch
```

改为：

```python
from unittest.mock import AsyncMock, patch
```

把 `test_checker_still_distills_healthy_fallback` 函数：

```python
def test_checker_still_distills_healthy_fallback():
    """走兜底且质检通过，数据量充足、报告无失败叙事 → 仍应正常沉淀（回归锁定，不被新判定误拦）。"""
    payload = {"score": 90, "issues": [], "summary": "很好"}
    dataset = [{"title": "a"}, {"title": "b"}, {"title": "c"}]

    async def fake_distill(*a, **k):
        return {"title": "测试模板", "keywords": ["k"], "body": "结构正文"}

    old = settings.template_min_data_count
    settings.template_min_data_count = 3
    try:
        with patch("src.conductor.nodes.checker.achat", new=_fake_achat(payload)), \
             patch("src.conductor.nodes.checker.distill_template", new=fake_distill), \
             patch("src.conductor.nodes.checker.save_template", return_value="test-slug") as mock_save:
            out = _run(checker_node(_checker_state(
                analysis_source="fallback", cleaned_dataset=dataset,
            )))
        mock_save.assert_called_once()
        assert out.get("template_saved") == {"slug": "test-slug", "title": "测试模板"}
    finally:
        settings.template_min_data_count = old
```

改为（`save_template` 现在是 `await` 调用，mock 必须是 `AsyncMock` 而不是默认的同步 `MagicMock`，否则 `await "test-slug"` 会抛 `TypeError`）：

```python
def test_checker_still_distills_healthy_fallback():
    """走兜底且质检通过，数据量充足、报告无失败叙事 → 仍应正常沉淀（回归锁定，不被新判定误拦）。"""
    payload = {"score": 90, "issues": [], "summary": "很好"}
    dataset = [{"title": "a"}, {"title": "b"}, {"title": "c"}]

    async def fake_distill(*a, **k):
        return {"title": "测试模板", "keywords": ["k"], "body": "结构正文"}

    old = settings.template_min_data_count
    settings.template_min_data_count = 3
    try:
        mock_save = AsyncMock(return_value="test-slug")
        with patch("src.conductor.nodes.checker.achat", new=_fake_achat(payload)), \
             patch("src.conductor.nodes.checker.distill_template", new=fake_distill), \
             patch("src.conductor.nodes.checker.save_template", new=mock_save):
            out = _run(checker_node(_checker_state(
                analysis_source="fallback", cleaned_dataset=dataset,
            )))
        mock_save.assert_called_once()
        assert out.get("template_saved") == {"slug": "test-slug", "title": "测试模板"}
    finally:
        settings.template_min_data_count = old
```

在这个函数之后插入一个新测试（验证 `checker.py` 新增的 `None` 分支）：

```python
def test_checker_no_template_saved_when_curator_discards():
    """Curator 判定丢弃（save_template 返回 None）→ 不产生 template_saved。"""
    payload = {"score": 90, "issues": [], "summary": "很好"}
    dataset = [{"title": "a"}, {"title": "b"}, {"title": "c"}]

    async def fake_distill(*a, **k):
        return {"title": "测试模板", "keywords": ["k"], "body": "结构正文"}

    async def fake_save(*a, **k):
        return None

    old = settings.template_min_data_count
    settings.template_min_data_count = 3
    try:
        with patch("src.conductor.nodes.checker.achat", new=_fake_achat(payload)), \
             patch("src.conductor.nodes.checker.distill_template", new=fake_distill), \
             patch("src.conductor.nodes.checker.save_template", new=fake_save):
            out = _run(checker_node(_checker_state(
                analysis_source="fallback", cleaned_dataset=dataset,
            )))
        assert "template_saved" not in out
    finally:
        settings.template_min_data_count = old
```

（`test_checker_rerun.py` 用 `globals()` 自动发现 `test_` 开头的函数，不需要手动登记到某个列表。）

- [ ] **Step 7: 运行确认全部通过**

Run: `E:/python3.13/python.exe scripts/test_template_learning.py`
Expected: `21/21 通过`（原 19 个 + 新增 2 个）

Run: `E:/python3.13/python.exe scripts/test_checker_rerun.py`
Expected: `12/12 通过`（原 11 个 + 新增 1 个）

- [ ] **Step 8: 提交**

```bash
git add src/memory/templates.py src/conductor/nodes/checker.py src/api/routes/confirm.py scripts/test_template_learning.py scripts/test_checker_rerun.py
git commit -m "feat: save_template 改为异步四态（新建/合并/复用/丢弃），checker.py/confirm.py 调用点适配"
```

---

### Task 4: 文档同步 + 全量回归

**Files:**
- Modify: `AGENTS.md`
- Modify: `README_AGENT.md`

**Interfaces:**
- 无新增代码接口，仅文档与验证

- [ ] **Step 1: 更新 AGENTS.md**

在 AGENTS.md 里 A 阶段止血修复的改动日志条目（"模板库自学习闭环 A 阶段止血修复（2026-07-09）"）之后追加一条：

```
  - **模板库自学习闭环 B1 阶段（Curator 合并裁决）**（2026-07-09）：`save_template()` 保存前新增
    Curator 裁决层（新增 `curate_template()`，`src/memory/templates.py`），把 A 阶段的二元去重
    （复用旧的/新建）升级为三态（新建/合并吸收新洞察/丢弃零增量内容）。合并时 LLM 产出完整重写的
    融合正文，目标模板的 `uses`/`quality_avg`/`status` 保持不变（模板身份未变，历史使用统计不应被
    内容更新清零），向量缓存对应条目失效。候选召回复用新提取的 `_semantic_candidates()`（从 A 阶段
    `find_duplicate_semantic()` 里提取共享逻辑），取余弦 top-3（新配置 `TEMPLATE_CURATOR_CANDIDATE_MIN_COSINE`
    默认 0.3 过滤明显不相关候选）；候选为空直接判新建，不调用 LLM。两层降级（候选召回不可用/LLM
    调用或输出解析失败）都归一退回 A 阶段的 `find_duplicate_semantic()` 二元逻辑，不致瘫。
    `save_template()` 因此从同步函数改为 async（内部要 `await curate_template()`），返回值从
    `str` 改为 `Optional[str]`（`None` 表示 Curator 判定丢弃，未写任何文件）；`checker.py` 自动
    沉淀分支与 `confirm.py` 人工确认按钮两个调用点均改为 `await` 并处理 `None`。
```

- [ ] **Step 2: 更新 README_AGENT.md**

在 README_AGENT.md 的 "## 15.4 模板库自学习闭环 A 阶段止血修复" 章节之后插入新章节：

```markdown
## 15.5 模板库自学习闭环 B1 阶段（Curator 合并裁决）（2026-07-09 新增）

A 阶段修好了去重/失败拦截/质量门三个真实缺陷，但保存逻辑本质仍是二元的：命中语义重复就复用旧
slug（丢弃新蒸馏出的洞察），不命中就新建。参考 ACE（Agentic Context Engineering）范式，把"保存"
升级为一次 Curator（馆长）裁决：新建 / 合并进已有模板（吸收新洞察）/ 丢弃（新内容零增量）。

- **候选召回**：从 A 阶段 `find_duplicate_semantic()` 提取共享逻辑为 `_semantic_candidates()`
  （按 `data_type` 过滤非淘汰模板，取余弦相似度 top-k，`find_duplicate_semantic` 自身沿用 top-5
  无下限，`curate_template` 用 top-3 + 新配置 `TEMPLATE_CURATOR_CANDIDATE_MIN_COSINE`（默认 0.3）
  过滤明显不相关候选）。候选为空时直接判 `new`，不调用 LLM，省一次调用。
- **Curator 裁决**：新增 `curate_template()`，把新内容与候选各自的
  `{title, keywords, body, uses, quality_avg, status}` 交给 LLM（新增 `TEMPLATE_CURATOR_SYSTEM`），
  复用 `parse_json_obj` 解析三态输出：`new`（新建）/ `merge`（LLM 产出完整重写的融合正文）/
  `discard`（新内容被现有候选完全覆盖，不做任何改动）。
- **合并语义**：目标模板的 `title`/`keywords`（新旧并集去重）/`body` 被更新，但 `uses`/`quality_avg`/
  `status` 保持原值不变——模板身份未变，历史使用统计不该因一次内容更新被清零；同时清除该 slug 在
  `_vectors.json` 的缓存条目（内容变了，旧向量不能代表新内容）。
- **两层降级**：候选召回不可用（embedding 关闭/端点失败）、或候选召回成功但 Curator LLM 调用失败/
  输出解析失败（含返回的 merge slug 不在候选列表里的情况），都归一退回 `find_duplicate_semantic()`
  的二元逻辑（命中即复用旧内容不改动、不命中则新建），不致瘫，行为不会比 A 阶段更差。
- **架构影响**：`save_template()` 因此从同步函数改为 async，返回值从 `str` 改为 `Optional[str]`
  （`None`=Curator 判定丢弃）；`checker.py` 自动沉淀分支、`confirm.py` 人工确认按钮两个调用点均改
  为 `await` 并处理 `None`——两处原本共用 `save_template()` 内部逻辑，无需各自感知 Curator 的存在。
- 回归：`test_template_learning.py` 扩容至 21 项，`test_checker_rerun.py` 扩容至 12 项。

对照 plan 仍未做：**B2（教训分流）**——失败任务的"教训"改走 skills 声明式注入通道，独立 spec，暂未开始。
```

- [ ] **Step 3: 全量回归验证**

Run: `E:/python3.13/python.exe scripts/test_template_learning.py`
Expected: `21/21 通过`

Run: `E:/python3.13/python.exe scripts/test_checker_rerun.py`
Expected: `12/12 通过`

Run: `E:/python3.13/python.exe scripts/test_embeddings.py`
Expected: `4/4 通过`（确认未回归）

- [ ] **Step 4: 编码校验 + 提交**

```bash
iconv -f utf-8 -t utf-8 AGENTS.md > /dev/null && echo "AGENTS.md OK"
iconv -f utf-8 -t utf-8 README_AGENT.md > /dev/null && echo "README_AGENT.md OK"
git add AGENTS.md README_AGENT.md
git commit -m "docs: 同步模板库自学习闭环 B1 阶段（Curator 合并裁决）到说明文档"
```

## 验证（全部任务完成后）

1. `E:/python3.13/python.exe scripts/test_template_learning.py` → 全 PASS（21/21）
2. `E:/python3.13/python.exe scripts/test_checker_rerun.py` → 全 PASS（12/12）
3. `E:/python3.13/python.exe scripts/test_embeddings.py` → 全 PASS（4/4，确认未回归）
4. 手工验证：构造一个与库内某模板高度相似但带新关键词维度的报告，走一次自动沉淀（或人工「沉淀为模板」按钮），确认目标模板的 body 被合并更新、uses/quality_avg 未被清零；构造一个明显重复且零增量的报告，确认「沉淀为模板」按钮返回"内容已被现有模板库覆盖"提示而非新建/合并
