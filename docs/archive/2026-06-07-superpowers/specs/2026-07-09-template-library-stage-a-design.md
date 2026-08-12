# 模板库自学习闭环优化 · A 阶段（止血修正）设计

## 背景

对 `data/templates/` 自学习模板库做了一次全链路验证（沉淀→去重→召回→质量门），发现 4 个真实问题：

1. **去重形同虚设**：`save_template()` 用关键词 Jaccard（阈值 0.6）去重，但 LLM 每次蒸馏措辞不同，实测 8 个语义高度雷同的模板两两 Jaccard 最高只有 0.22，去重从未触发，近重复模板持续堆积。
2. **失败任务也会沉淀模板**：Checker 只评估"报告写得好不好"，不评估"任务是否采集成功"。采集失败但报告写得工整（结构完整地描述"未采集到数据"）照样能通过质检、被蒸馏成模板，污染库。库存 13 个模板里有 5 个是"数据缺失/采集失败/有效性核查"类。
3. **质量门有漏洞和死区**：① 旧格式模板（无 `status` 字段）被 `load_templates()` 默认当作 `active`，绕开草稿门；② `uses>=3` 后若均分卡在 50~70 之间（不达淘汰线、不达转正线），会永久停留在 draft 状态但仍参与召回，无法自愈。
4. **库存已脏**：现有 13 个模板中多组语义重复，需要一次性清理。

本设计是三阶段优化（A 止血修正 / B Curation 闭环 / C 结构升级）中的第一阶段，聚焦最小止血改动。B、C 各自单独 brainstorming。

## 目标（本阶段范围）

- 语义去重替代关键词 Jaccard 去重
- 失败任务不进入模板沉淀流程
- 修复质量门的"旧模板绕过草稿"和"死区永久卡住"两个漏洞
- 清理现有 13 个模板中的近重复项

**不做**（留给 B/C 或明确排除）：
- 不引入 LLM Curator 裁决合并（B 阶段）
- 不做失败经验的"教训"分流注入（B 阶段）
- 不做后台定期巡检协程（C 阶段）
- 不改变现有召回逻辑（`match_template`/`_match_semantic`）本身

## 设计

### 1. 语义去重

新增 `find_duplicate_semantic(data_type, keywords, title) -> Optional[Dict]`，位置 `src/memory/templates.py`（`find_duplicate` 函数旁）：

- 候选集合：与现有 `find_duplicate()` 相同的过滤方式——`load_templates()` 中 `status != "retired"` 且 `data_type` 相同的模板（注意：这里入参是原始 `data_type: str`，不是 `TaskSpec`，不能直接复用 `_candidates()`，需要单独写等价的过滤代码）
- 粗筛：新模板文本 `title + " " + " ".join(keywords)` 与每个候选的 `_template_text(t)` 算余弦相似度（复用 `src/memory/embeddings.py` 的 `embed_texts_with_model` + `cosine`），取相似度最高的 top-5
- 精判：调 `rerank_scores(query, documents)`，**新增专用 instruct 文案**（区别于召回场景的"是否适用于该任务"）：
  ```
  判断这两段模板描述是否属于同一类分析任务（措辞不同但结构和适用场景相同即算同一类）
  ```
  新增配置项 `template_dedup_rerank_threshold: float = 0.7`（比召回用的 `rerank_match_threshold=0.35` 更严格——去重要求"是同一件事"，召回只要求"沾边即可用"）
- rerank 分数 ≥ 阈值的候选中取分数最高者，返回该模板；否则返回 `None`
- **降级路径**：embedding/rerank 端点不可用（`embed_texts_with_model` 返回空，或 `is_rerank_configured()` 为 False）时，退回现有的关键词 Jaccard 逻辑（`_jaccard` + `template_dedup_threshold=0.6`），保证不致瘫
- `save_template()` 内 `find_duplicate(data_type, keywords)` 调用替换为 `find_duplicate_semantic(data_type, keywords, title)`（`find_duplicate` 函数本身保留，作为降级路径的实现细节被内部调用，不删除、不改签名——避免破坏其他潜在调用方）

### 2. 失败任务不入库

`src/conductor/nodes/checker.py` 的自动沉淀分支（原第 88-112 行）前，新增判定函数 `_looks_like_collection_failure(dataset: list, analysis: str) -> bool`（放在 checker.py 内，仅供本节点使用）：

- 信号 A（数据量）：`len(dataset) < settings.template_min_data_count`
- 信号 B（叙事关键词）：`analysis` 正文命中失败关键词表 `_FAILURE_NARRATIVE_KEYWORDS`（模块级常量）：
  ```python
  _FAILURE_NARRATIVE_KEYWORDS = [
      "未采集到", "数据缺失", "采集失败", "无有效数据", "未找到相关内容",
      "样本不足", "数据不足", "未获取到", "采集异常", "数据核查未通过",
  ]
  ```
- 两信号**并集**（任一命中即判定为失败）：`return len(dataset) < settings.template_min_data_count or any(kw in analysis for kw in _FAILURE_NARRATIVE_KEYWORDS)`

新增配置 `template_min_data_count: int = 3`（`src/config/settings.py`，模板自学习配置区）。

自动沉淀分支改为：
```python
if (
    passed
    and settings.template_learning_enabled
    and state.get("analysis_source") == "fallback"
    and not _looks_like_collection_failure(state.get("cleaned_dataset", []), analysis)
):
    ...（蒸馏逻辑不变）
```

注意：这个判定**只影响是否沉淀模板**，不影响报告本身的产出、不影响 `quality`/`passed` 字段——采集失败的任务仍然照常给用户看失败报告，只是不会被学进模板库。

### 3. 质量门修复

`src/memory/templates.py`：

**3a. 旧模板不再绕过草稿门**（`load_templates()` 第 76 行）：
```python
"status": str(meta.get("status") or "draft").strip().lower(),  # 原为 "active"
```
无 `status` 字段的模板（历史遗留格式）现在按 draft 对待，需重新走质量门验证后才能转正。

**3b. 死区强制退场**（`record_template_use()` 质量门判断处，约第 369-373 行）：
```python
if uses >= settings.template_promote_uses:
    if avg < settings.template_retire_quality:
        status = "retired"
    elif status == "draft" and avg >= settings.template_promote_quality:
        status = "active"
    elif status == "draft" and uses >= settings.template_dead_zone_uses:
        status = "retired"  # 新增：死区内积累够多次数仍未转正，判定为不合格
```
新增配置 `template_dead_zone_uses: int = 10`（须 > `template_promote_uses`）。

### 4. 现有模板库清理（一次性脚本，非生产代码）

`scripts/` 下写一次性脚本（如 `dedup_templates_oneoff.py`），复用第 1 点的 `find_duplicate_semantic`（去重逻辑对内跑），对当前 `data/templates/` 下 13 个模板两两比对（不局限于按顺序增量比对，而是全量两两算相似度），输出：

```
重复组 1（rerank=0.97）：
  - 社交媒体主题帖子采集与分析报告.md
  - 社交媒体内容采集结果分析报告.md
  建议保留：社交媒体内容采集结果分析报告（关键词更完整）
...
```

脚本只打印建议，不自动删除。用户看清单确认后，由脚本第二次运行（带 `--apply` 参数）或人工用现有 `delete_template()` 执行。此脚本用完即弃，不进入长期维护范围。

## 测试计划

- `scripts/test_template_learning.py` 新增用例：
  - `test_semantic_dedup_reuses_similar_template`：mock embedding+rerank 返回高分，验证复用不新建
  - `test_semantic_dedup_falls_back_to_jaccard_when_embedding_unavailable`：mock embedding 端点失败，验证走关键词 Jaccard
  - `test_collection_failure_by_low_data_count_skips_distillation`：dataset 长度 <3，验证不调用 `distill_template`
  - `test_collection_failure_by_narrative_keyword_skips_distillation`：dataset 足量但 analysis 含"未采集到"，验证不调用 `distill_template`
  - `test_legacy_template_without_status_defaults_to_draft`：手写一个无 status 字段的模板文件，验证 `load_templates()` 返回 draft
  - `test_dead_zone_forces_retire`：uses 达到 `template_dead_zone_uses`、avg 卡在 50~70 之间，验证 status 变为 retired
- 全量回归：`test_template_learning.py` + `test_embeddings.py` 全绿

## 验证

1. 单元测试全绿
2. 一次性清库脚本跑出的建议清单需人工确认后再执行删除（不在本计划内自动执行）
3. 手工验证：故意让一个任务采集到 0 条数据触发失败兜底，确认 checker 通过质检但**不产生**新模板文件
