# 模板库自学习闭环优化 · A 阶段（止血修正）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复模板库自学习闭环里的三个真实缺陷（去重失效、失败任务污染库、质量门死区/漏洞），并清理现有库存中的近重复模板。

**Architecture:** 复用现有 embedding+rerank 语义基建（`src/memory/embeddings.py`）新增一条"判断是否同一类模板"的精判通道，替代原有关键词 Jaccard 去重；在 Checker 自动沉淀分支前加一道纯函数式的"采集是否失败"判定；修复 `templates.py` 质量门的两处状态判定逻辑；最后写一次性脚本对现有库存做语义去重扫描。

**Tech Stack:** Python 3.13、httpx（已有）、PyYAML（已有）、无新依赖。

## Global Constraints

- Python 解释器用 `E:/python3.13/python.exe`（项目约定，不用裸 `python`/`python3`）
- 测试遵循项目无 pytest 约定：`scripts/test_*.py`，`def test_x(): assert ...`，`main()` 收集 PASS/FAIL 并 `sys.exit(1)` 失败退出
- 写入含中文内容的文件后用 `iconv -f utf-8 -t utf-8 <file> > /dev/null` 校验编码
- 语义去重降级路径：embedding/rerank 端点不可用时（`embed_texts_with_model` 返回 `None`，或 `is_rerank_configured()` 为 `False`）必须退回关键词 Jaccard 逻辑（`find_duplicate()`），不能让整个保存流程失败
- 去重与召回使用**不同**的 rerank instruct 与**不同**的阈值：召回用现有 `rerank_match_threshold=0.35`（不变）；去重新增配置 `template_dedup_rerank_threshold`，默认 `0.7`（去重要求"是同一件事"，比召回"沾边即可用"更严格）
- 失败判定用**并集**：`len(cleaned_dataset) < settings.template_min_data_count`（默认 `3`）**或** `analysis` 正文命中失败叙事关键词，任一命中即视为失败，跳过模板沉淀（但不影响报告本身照常产出给用户）
- 质量门死区：`uses >= settings.template_dead_zone_uses`（默认 `10`，需大于 `template_promote_uses`）时若仍是 `draft` → 强制 `retired`
- 旧格式模板（无 `status` 字段）现在默认按 `draft` 对待（原先是 `active`，会绕过质量门）
- 现有 13 个模板的清库：一次性脚本只输出"重复组+保留建议"清单，**不自动删除**，需人工确认后才执行删除

---

### Task 1: 语义去重替代关键词 Jaccard

**Files:**
- Modify: `src/memory/embeddings.py`（`rerank_scores` 函数）
- Modify: `src/config/settings.py`（模板自学习配置区，`template_dedup_threshold` 字段之后）
- Modify: `src/memory/templates.py`（新增 `find_duplicate_semantic`，`save_template` 内的调用点）
- Modify: `scripts/test_template_learning.py`（新增 2 个测试，调整 2 个既有测试使其不依赖真实网络端点）

**Interfaces:**
- Consumes：`src/memory/embeddings.py` 现有 `embed_texts_with_model(texts) -> Optional[Tuple[str, List[List[float]]]]`、`cosine(a, b) -> float`、`is_rerank_configured() -> bool`；`src/memory/templates.py` 现有 `load_templates()`、`_template_text(t)`、`find_duplicate(data_type, keywords)`、`_jaccard`
- Produces：`src/memory/embeddings.py` 的 `rerank_scores(query, documents, instruct=None)`（新增可选形参，向后兼容，默认值保持原行为不变）；`src/memory/templates.py` 的 `find_duplicate_semantic(data_type: str, keywords: List[str], title: str) -> Optional[Dict]`（Task 4 会直接复用其中的 `_DEDUP_RERANK_INSTRUCT` 常量）；`src/config/settings.py` 的 `settings.template_dedup_rerank_threshold: float`

- [ ] **Step 1: 给 `rerank_scores` 加可选 `instruct` 形参**

修改 `src/memory/embeddings.py` 第 144-158 行（`rerank_scores` 函数体）：

```python
def rerank_scores(query: str, documents: List[str], instruct: Optional[str] = None) -> Optional[List[float]]:
    """对候选文档按 query 相关性打分，返回与 documents 同序的分数列表；失败返回 None。

    走 vLLM 的 Cohere 兼容 /rerank：results 里每项带原始 index 与 relevance_score。
    instruct 为空时用默认的"是否适用于该任务"判据（召回场景）；传入自定义 instruct
    可复用同一个 rerank 端点做别的判断（如模板去重场景的"是否同一类任务"）。
    """
    if not is_rerank_configured() or not documents:
        return None
    base = settings.rerank_base_url.rstrip("/")
    url = f"{base}/rerank"
    headers = {"Authorization": f"Bearer {settings.rerank_api_key or 'local'}",
               "Content-Type": "application/json"}
    q, docs = query, documents
    if "qwen3-reranker" in settings.rerank_model.lower():
        used_instruct = instruct or _QWEN3_RERANK_INSTRUCT
        q = f"{_QWEN3_RERANK_PREFIX}<Instruct>: {used_instruct}\n<Query>: {query}\n"
        docs = [f"<Document>: {d}{_QWEN3_RERANK_SUFFIX}" for d in documents]
```

（其余函数体、`try/except` 部分不变，`Optional` 已在文件顶部 import 过，不需要新增 import。）

- [ ] **Step 2: 新增去重阈值配置**

在 `src/config/settings.py` 第 131 行（`template_dedup_threshold` 字段）之后插入一行：

```python
    template_dedup_rerank_threshold: float = Field(default=0.7, description="语义去重 rerank 相关性分数阈值（≥则判定为同一类模板，比召回阈值更严格）")
```

- [ ] **Step 3: 在 templates.py 新增语义去重函数**

在 `src/memory/templates.py` 的 `find_duplicate` 函数（第 269-282 行）之后插入新函数：

```python
# 去重场景的 rerank instruct：与召回场景（"是否适用于该任务"）不同，
# 去重要求判断"是否描述同一类任务"，判据更严格，专用独立阈值 template_dedup_rerank_threshold。
_DEDUP_RERANK_INSTRUCT = "判断这两段模板描述是否属于同一类分析任务（措辞不同但结构和适用场景相同即算同一类）"


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

- [ ] **Step 4: 接入 `save_template`**

`src/memory/templates.py` 的 `save_template` 函数内（第 291 行附近），把：

```python
    dup = find_duplicate(data_type, keywords)
```

改为：

```python
    dup = find_duplicate_semantic(data_type, keywords, title)
```

- [ ] **Step 5: 调整 2 个既有去重测试，避免依赖真实网络端点**

真实 `.env` 里 `EMBEDDING_ENABLED=True` 且配置了真实 rerank 端点，Step 4 接入后，`test_dedup_reuses_existing` 和 `test_no_dedup_different_datatype` 这两个连续 `save_template` 两次的测试会真的打网络请求。改为显式关闭 `embedding_enabled`，让它们确定性地走关键词 Jaccard 兜底路径（这本来就是这两个测试要验证的逻辑）。

`scripts/test_template_learning.py` 里，把：

```python
def test_dedup_reuses_existing():
    _setup_tmp()
    s1 = tpl.save_template("产品测评A", "product", ["测评", "参数", "卖点"], "结构A")
    # 关键词高度重叠（Jaccard=2/3≈0.67≥0.6）→ 应复用 s1，不新建
    s2 = tpl.save_template("产品测评B", "product", ["测评", "参数"], "结构B")
    assert s2 == s1, (s1, s2)
    assert len(tpl.load_templates()) == 1


def test_no_dedup_different_datatype():
    _setup_tmp()
    s1 = tpl.save_template("X", "product", ["测评", "参数"], "a")
    s2 = tpl.save_template("Y", "bid", ["测评", "参数"], "b")  # 不同 data_type → 不算重复
    assert s2 != s1 and len(tpl.load_templates()) == 2
```

改为：

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

- [ ] **Step 6: 新增语义去重的 2 个测试**

在 `scripts/test_template_learning.py` 顶部 import 区加一行（`import src.memory.templates as tpl` 之后）：

```python
import src.memory.embeddings as emb
```

在 `test_no_dedup_different_datatype` 之后插入：

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

在 `main()` 的 `tests = [...]` 列表里追加这两个函数名：

```python
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
    ]
```

- [ ] **Step 7: 运行测试确认全绿**

Run: `E:/python3.13/python.exe scripts/test_template_learning.py`
Expected: `8/8 通过`（原 6 个 + 新增 2 个）

- [ ] **Step 8: 提交**

```bash
git add src/memory/embeddings.py src/config/settings.py src/memory/templates.py scripts/test_template_learning.py
git commit -m "feat: 模板去重改用语义相似度+rerank精判，替代失效的关键词Jaccard"
```

---

### Task 2: 失败任务不进入模板沉淀

**Files:**
- Modify: `src/config/settings.py`（模板自学习配置区）
- Modify: `src/conductor/nodes/checker.py`
- Modify: `scripts/test_checker_rerun.py`（新增 3 个测试）

**Interfaces:**
- Consumes：`ConductorState` 的 `cleaned_dataset: List[Dict[str, Any]]` 字段（`src/conductor/state.py:37`）；`checker_node` 现有的自动沉淀分支（`state.get("analysis_source") == "fallback"`）
- Produces：`src/conductor/nodes/checker.py` 的 `_looks_like_collection_failure(dataset: list, analysis: str) -> bool`（纯函数，无外部调用，仅本任务内部使用）；`settings.template_min_data_count: int`

- [ ] **Step 1: 新增最少数据量配置**

在 `src/config/settings.py` 的 `template_dedup_rerank_threshold` 字段（Task 1 Step 2 新增）之后插入：

```python
    template_min_data_count: int = Field(default=3, description="沉淀模板所需的最少采集数据条数，低于此值视为采集失败，不沉淀模板")
```

- [ ] **Step 2: 写判定函数的失败测试**

`scripts/test_checker_rerun.py` 当前 import 区（第 18-21 行）是：

```python
from src.conductor.graph import _route_after_checker
from src.conductor.nodes.analyze import analyze_node
from src.conductor.nodes.checker import checker_node
from src.conductor.task_spec import AnalysisType, TaskSpec
```

改为（新增 settings 导入 + `_looks_like_collection_failure` 导入）：

```python
from src.config.settings import settings
from src.conductor.graph import _route_after_checker
from src.conductor.nodes.analyze import analyze_node
from src.conductor.nodes.checker import _looks_like_collection_failure, checker_node
from src.conductor.task_spec import AnalysisType, TaskSpec
```

在文件末尾 `test_route_after_checker` 函数之后插入：

```python
def test_looks_like_collection_failure_by_low_data_count():
    """数据条数低于阈值 → 判定为采集失败，即使报告文字正常。"""
    old = settings.template_min_data_count
    settings.template_min_data_count = 3
    try:
        assert _looks_like_collection_failure([{"title": "a"}], "报告结构完整，内容详实。") is True
    finally:
        settings.template_min_data_count = old


def test_looks_like_collection_failure_by_narrative_keyword():
    """数据条数达标，但报告正文命中失败叙事关键词 → 判定为采集失败。"""
    old = settings.template_min_data_count
    settings.template_min_data_count = 3
    try:
        dataset = [{"title": "a"}, {"title": "b"}, {"title": "c"}]
        assert _looks_like_collection_failure(dataset, "本次未采集到有效数据，无法展开分析。") is True
    finally:
        settings.template_min_data_count = old


def test_looks_like_collection_failure_false_when_healthy():
    """数据量充足且报告无失败叙事 → 不判定为失败。"""
    old = settings.template_min_data_count
    settings.template_min_data_count = 3
    try:
        dataset = [{"title": "a"}, {"title": "b"}, {"title": "c"}, {"title": "d"}]
        assert _looks_like_collection_failure(dataset, "本次分析共覆盖4条评论，用户反馈集中在续航方面。") is False
    finally:
        settings.template_min_data_count = old
```

- [ ] **Step 3: 运行确认测试因函数不存在而失败**

Run: `E:/python3.13/python.exe scripts/test_checker_rerun.py`
Expected: `ImportError: cannot import name '_looks_like_collection_failure'`

- [ ] **Step 4: 实现判定函数并接入自动沉淀分支**

`src/conductor/nodes/checker.py` 的 `logger = logging.getLogger(__name__)`（第 24 行）之后插入：

```python
# 走了兜底且质检通过的报告，若"采集本身就失败了"（叙事完整但没数据），不应沉淀为模板——
# checker 评估的是"报告写得好不好"，不是"任务是否采集成功"，两者需要分开判断。
_FAILURE_NARRATIVE_KEYWORDS = (
    "未采集到", "数据缺失", "采集失败", "无有效数据", "未找到相关内容",
    "样本不足", "数据不足", "未获取到", "采集异常", "数据核查未通过",
)


def _looks_like_collection_failure(dataset: list, analysis: str) -> bool:
    """判断本次任务是否"采集失败/数据不足"，用于阻止把失败报告沉淀为模板。
    两个信号取并集：数据条数过少，或报告正文命中失败叙事关键词。"""
    if len(dataset) < settings.template_min_data_count:
        return True
    return any(kw in (analysis or "") for kw in _FAILURE_NARRATIVE_KEYWORDS)
```

然后把原有自动沉淀分支的判断条件（原第 88-93 行）：

```python
    # 自学习阶段2：走了兜底 且 质量通过 → 自动沉淀模板（无需人工确认）
    if (
        passed
        and settings.template_learning_enabled
        and state.get("analysis_source") == "fallback"
    ):
```

改为：

```python
    # 自学习阶段2：走了兜底 且 质量通过 且非采集失败 → 自动沉淀模板（无需人工确认）
    if (
        passed
        and settings.template_learning_enabled
        and state.get("analysis_source") == "fallback"
        and not _looks_like_collection_failure(state.get("cleaned_dataset") or [], analysis)
    ):
```

- [ ] **Step 5: 运行确认判定函数测试通过**

Run: `E:/python3.13/python.exe scripts/test_checker_rerun.py`
Expected: 全部 PASS

- [ ] **Step 6: 补两个 checker_node 端到端测试，锁定"是否真的跳过/仍然沉淀"**

在同一文件里、`test_looks_like_collection_failure_false_when_healthy` 之后追加：

```python
def test_checker_skips_distillation_when_data_count_low():
    """走兜底且质检通过，但采集数据条数不足 → 不应调用 distill_template，不产生 template_saved。"""
    payload = {"score": 90, "issues": [], "summary": "很好"}
    old = settings.template_min_data_count
    settings.template_min_data_count = 3
    try:
        with patch("src.conductor.nodes.checker.achat", new=_fake_achat(payload)), \
             patch("src.conductor.nodes.checker.distill_template") as mock_distill:
            out = _run(checker_node(_checker_state(
                analysis_source="fallback", cleaned_dataset=[{"title": "a"}],
            )))
        mock_distill.assert_not_called()
        assert "template_saved" not in out
    finally:
        settings.template_min_data_count = old


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

- [ ] **Step 7: 运行全部测试确认通过**

Run: `E:/python3.13/python.exe scripts/test_checker_rerun.py`
Expected: `11/11 通过`（原 6 个 + Step 2 新增 3 个 `_looks_like_collection_failure` 单测 + Step 6 新增 2 个 `checker_node` 端到端测试）

- [ ] **Step 8: 提交**

```bash
git add src/config/settings.py src/conductor/nodes/checker.py scripts/test_checker_rerun.py
git commit -m "feat: 采集失败的兜底任务不再自动沉淀为模板"
```

---

### Task 3: 修复质量门的旧模板绕过与死区问题

**Files:**
- Modify: `src/config/settings.py`（模板自学习配置区）
- Modify: `src/memory/templates.py`（`load_templates`、`record_template_use`）
- Modify: `scripts/test_template_learning.py`（新增 2 个测试）

**Interfaces:**
- Consumes：`src/memory/templates.py` 现有 `load_templates()`、`record_template_use(slug, quality_score)`
- Produces：`settings.template_dead_zone_uses: int`（供 `record_template_use` 内部使用，无外部消费方）

- [ ] **Step 1: 新增死区配置**

在 `src/config/settings.py` 的 `template_min_data_count` 字段（Task 2 Step 1 新增）之后插入：

```python
    template_dead_zone_uses: int = Field(default=10, description="草稿模板使用次数达此值仍未转正则强制淘汰，防止永久卡在质量门死区（须大于 template_promote_uses）")
```

- [ ] **Step 2: 写失败测试**

在 `scripts/test_template_learning.py` 的 `test_match_includes_draft_excludes_retired` 函数之后插入：

```python
def test_legacy_template_without_status_defaults_to_draft():
    """无 status 字段的旧格式模板文件，加载后应按 draft 对待（不再默认 active 绕过质量门）。"""
    d = _setup_tmp()
    (d / "legacy.md").write_text(
        "---\ntitle: 老模板\ndata_type: article\nkeywords: [旧格式]\n---\n正文内容\n",
        encoding="utf-8",
    )
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
        slug = tpl.save_template("死区模板", "generic", ["死区测试"], "结构")
        st = None
        for _ in range(5):  # 连续 5 次都打 60 分：avg=60，卡在 50~70 死区
            st = tpl.record_template_use(slug, 60)
        assert st == "retired", f"应在 uses=5 时强制淘汰，实际 status={st}"
    finally:
        (settings.template_promote_uses, settings.template_promote_quality,
         settings.template_retire_quality, settings.template_dead_zone_uses) = old
```

在 `main()` 的 `tests = [...]` 列表末尾追加：

```python
        test_legacy_template_without_status_defaults_to_draft,
        test_dead_zone_forces_retire,
```

- [ ] **Step 3: 运行确认测试失败**

Run: `E:/python3.13/python.exe scripts/test_template_learning.py`
Expected: `test_legacy_template_without_status_defaults_to_draft` 和 `test_dead_zone_forces_retire` 均 FAIL（前者因当前默认 `active`，后者因当前无死区逻辑会一直是 `draft`）

- [ ] **Step 4: 修复旧模板默认状态**

`src/memory/templates.py` 的 `load_templates()` 函数内，把：

```python
            "status": str(meta.get("status") or "active").strip().lower(),
```

改为：

```python
            "status": str(meta.get("status") or "draft").strip().lower(),  # 无 status 字段的旧模板按草稿对待，需重新走质量门
```

- [ ] **Step 5: 修复死区**

`src/memory/templates.py` 的 `record_template_use()` 函数内，把：

```python
    if uses >= settings.template_promote_uses:
        if avg < settings.template_retire_quality:
            status = "retired"
        elif status == "draft" and avg >= settings.template_promote_quality:
            status = "active"
```

改为：

```python
    if uses >= settings.template_promote_uses:
        if avg < settings.template_retire_quality:
            status = "retired"
        elif status == "draft" and avg >= settings.template_promote_quality:
            status = "active"
        elif status == "draft" and uses >= settings.template_dead_zone_uses:
            status = "retired"  # 死区：多次使用仍卡在淘汰线和转正线之间，判定不合格
```

- [ ] **Step 6: 运行确认全部通过**

Run: `E:/python3.13/python.exe scripts/test_template_learning.py`
Expected: `10/10 通过`

- [ ] **Step 7: 提交**

```bash
git add src/config/settings.py src/memory/templates.py scripts/test_template_learning.py
git commit -m "fix: 修复模板质量门的旧格式绕过与死区永久卡住问题"
```

---

### Task 4: 现有模板库一次性语义去重扫描

**Files:**
- Create: `scripts/dedup_templates_oneoff.py`

**Interfaces:**
- Consumes：`src/memory/templates.py` 的 `load_templates()`、`_template_text(t)`、`_DEDUP_RERANK_INSTRUCT`（Task 1 新增）；`src/memory/embeddings.py` 的 `embed_texts_with_model`、`cosine`、`rerank_scores`；`settings.template_dedup_rerank_threshold`（Task 1 新增）
- Produces：无（终端输出建议清单，供人工确认，不修改任何文件）

此脚本对现有 `data/templates/` 里的模板做**两两全量比对**（而不是 Task 1 那种"一个新模板 vs 库内已有"的单向查重），因此不直接调用 `find_duplicate_semantic`（那个函数是为保存时的单向查重设计的），而是复用同一套判据常量（`_DEDUP_RERANK_INSTRUCT`、`template_dedup_rerank_threshold`）自己写一个全量两两扫描的循环。

- [ ] **Step 1: 编写脚本**

创建 `scripts/dedup_templates_oneoff.py`：

```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一次性脚本：对 data/templates/ 现有模板做语义去重扫描，输出重复组建议清单。

只打印建议，不修改/删除任何文件。用完即弃，不进入长期维护范围。
复用 Task 1（find_duplicate_semantic）同一套判据：_DEDUP_RERANK_INSTRUCT + template_dedup_rerank_threshold，
但比对方式是"库内两两全量比对"，而 find_duplicate_semantic 是"单个新模板 vs 库内已有"，形状不同不复用该函数本身。

运行：python scripts/dedup_templates_oneoff.py
"""
import itertools
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import settings
import src.memory.embeddings as emb
from src.memory.templates import _DEDUP_RERANK_INSTRUCT, _template_text, load_templates


def main():
    templates = load_templates()
    print(f"共 {len(templates)} 个模板\n")

    by_type = {}
    for t in templates:
        by_type.setdefault(t["data_type"], []).append(t)

    found_any = False
    for dt, group in by_type.items():
        if len(group) < 2:
            continue
        texts = [_template_text(t) for t in group]
        got = emb.embed_texts_with_model(texts)
        if not got or not got[1]:
            print(f"[data_type={dt}] embedding 端点不可用，跳过该组\n")
            continue
        _, vecs = got

        for i, j in itertools.combinations(range(len(group)), 2):
            cos = emb.cosine(vecs[i], vecs[j])
            if cos < 0.5:
                continue  # 余弦太低，粗筛淘汰，不必再精判
            rscores = emb.rerank_scores(texts[i], [texts[j]], instruct=_DEDUP_RERANK_INSTRUCT)
            if not rscores or rscores[0] < settings.template_dedup_rerank_threshold:
                continue
            found_any = True
            a, b = group[i], group[j]
            keep = a if len(a["keywords"]) >= len(b["keywords"]) else b
            print(f"重复组（data_type={dt}, cos={cos:.2f}, rerank={rscores[0]:.2f}）：")
            print(f"  - {a['slug']}")
            print(f"  - {b['slug']}")
            print(f"  建议保留：{keep['slug']}（关键词更完整）\n")

    if not found_any:
        print("未发现语义重复的模板组。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行脚本，获取建议清单**

Run: `E:/python3.13/python.exe scripts/dedup_templates_oneoff.py`
Expected: 打印出若干"重复组"及保留建议（真实库存里已知至少有几组 post 类模板高度雷同，参见 spec 背景部分）

- [ ] **Step 3: 把建议清单交给用户确认**

把 Step 2 的完整输出原样展示给用户，明确询问："以上重复组，是否按建议保留项删除其余的？" 用户确认后，对每个建议删除的 slug 调用一次现有 `delete_template(slug)`（`src/memory/templates.py`，无需新代码，已存在）执行删除。**不确认之前不删除任何文件。**

- [ ] **Step 4: 提交**

```bash
git add scripts/dedup_templates_oneoff.py
git commit -m "chore: 新增一次性模板库语义去重扫描脚本"
```

（若 Step 3 执行了删除，删除本身对 `data/templates/*.md` 的改动单独一次提交，commit message 如 `chore: 清理模板库中的语义重复模板（保留 xxx，删除 yyy）`，需列出具体删了哪些文件。）

---

## 验证（全部任务完成后）

1. `E:/python3.13/python.exe scripts/test_template_learning.py` → 全 PASS（10/10）
2. `E:/python3.13/python.exe scripts/test_checker_rerun.py` → 全 PASS（11/11）
3. `E:/python3.13/python.exe scripts/test_embeddings.py` → 全 PASS（4/4，确认未回归）
4. 手工验证：故意让一个任务采集到 0 条数据触发失败兜底，确认 checker 通过质检但**不产生**新模板文件（`data/templates/` 无新增 `.md`）
5. Task 4 的清库建议清单已交用户确认，按确认结果执行/跳过删除
