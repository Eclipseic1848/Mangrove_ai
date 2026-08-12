# 模板库自学习闭环优化 · B1 阶段（Curator 合并裁决）设计

## 背景

A 阶段（止血修复，已交付）解决了"去重形同虚设/失败任务污染库/质量门死区"三个真实缺陷，但保存逻辑本质上仍是二元的：`save_template()` 命中语义重复就复用旧 slug（丢弃新蒸馏出的洞察）、不命中就新建一条。用户提出的核心诉求是"不可能每天都看这些模板"——A 阶段能防止库继续变脏，但没有让库"越用越聪明"：每次命中重复时，新报告里可能有的增量信息（更全的关键词覆盖、更细的结构要点）被直接丢弃了。

本设计参考 ACE（Agentic Context Engineering）范式，把"保存"从二元判定升级为一次 Curator（馆长）裁决：新建 / 合并进已有模板（吸收新洞察） / 丢弃（新内容零增量）。

原三阶段设计中的 B 阶段被进一步拆分为 B1（本设计，Curator 合并裁决）与 B2（教训分流，独立后续 spec），因为两者虽共享触发点但产出物、消费方完全不同，合并到一个 spec/plan 周期会过大。

## 目标（本阶段范围）

- `save_template()` 保存前先做 Curator 裁决：新建 / 合并 / 丢弃
- 合并时由 Curator 产出完整重写的正文，融合新旧内容
- 两层降级：语义候选召回不可用 / Curator LLM 调用失败，都退回 A 阶段已有的二元逻辑，不致瘫
- checker.py 自动沉淀分支与 confirm.py 人工确认按钮**共用**这套新逻辑（因为改动点在 `save_template()` 内部，两个调用方无需各自感知）

**不做**（留给 B2 或明确排除）：
- 不做失败任务的"教训"分流（B2 单独设计）
- 不改变 A 阶段已有的失败判定 `_looks_like_collection_failure`、质量门转正/淘汰/死区逻辑
- 不改变 `match_template`/`_match_semantic` 召回逻辑本身（Curator 只影响"保存"，不影响"使用"）
- 不做批量/离线重新审视历史库存（若需要，届时按 A 阶段清库脚本的模式另开一次性脚本，非本阶段范围）

## 设计

### 1. 候选召回

新增私有辅助函数（从 `find_duplicate_semantic` 里提取共享逻辑，避免重复实现embedding 调用与降级判断），按 `data_type` 过滤非淘汰模板，取**余弦相似度 top-3**（不经过 rerank 精排——精排是为了在"二选一"场景下压低误召回，Curator 本身就是终裁，喂给它粗筛的 top-3 全文即可，没必要多一次 rerank 调用）。新增配置 `template_curator_candidate_min_cosine`（默认 0.3）过滤掉明显不相关的候选（避免把风马牛不相及的模板塞进 Curator 的 prompt 干扰判断）。

候选为空（新库/新 `data_type`，或全部候选低于余弦下限）时，**不调用 Curator LLM**，直接判定 `NEW`，与今天的行为等价（省一次调用）。

### 2. Curator 裁决

新增 `curate_template(title, data_type, keywords, body, *, provider=None, model=None) -> Dict`（`src/memory/templates.py`），把新内容与候选各自的 `{title, keywords, body, uses, quality_avg, status}` 一起交给 LLM（新增 `TEMPLATE_CURATOR_SYSTEM`，`src/conductor/prompts.py`，紧邻现有 `TEMPLATE_DISTILL_SYSTEM`），复用 `parse_json_obj`（`src/conductor/utils.py`）解析结构化输出，与 `distill_template` 现有模式一致：

```json
{
  "decision": "new" | "merge" | "discard",
  "slug": "被合并的候选 slug（仅 decision=merge 时需要）",
  "title": "合并后标题（仅 merge；沿用旧标题即可，除非新内容显著扩大了适用范围）",
  "keywords": ["合并后关键词列表（仅 merge；新旧并集去重）"],
  "body": "完整重写的融合正文（仅 merge，一段中文 system prompt，通用化描述结构，不复述本次具体数据）"
}
```

三种裁决的判据（写入 prompt，指导 LLM 判断）：
- **new**：与所有候选都不是同一类任务
- **merge**：与某候选是同一类任务，但新内容有该候选未覆盖的信息（新关键词维度、更完整的结构要点）
- **discard**：新内容被某候选完全覆盖，无任何增量价值——不新建、不改动任何文件

`decision` 之外字段缺失或格式不对时，视为解析失败（走降级）。

### 3. 应用裁决

- `new` → 走原 `save_template` 落盘逻辑（新建 draft，`uses=0`/`quality_avg=0`）
- `merge` → 更新目标 slug 文件的 `title`/`keywords`/`body`；**`uses`/`quality_avg`/`status` 保持原值不变**（模板身份未变，历史使用统计不该因一次内容更新清零，这也是 A 阶段"质量门不应被绕过"精神的延续）；同时从 `_vectors.json` 移除该 slug 的缓存条目（内容变了，旧向量不能代表新内容，下次召回时会自然重新计算）
- `discard` → 不写任何文件

### 4. 两层降级（不致瘫）

延续 A 阶段"语义端点不可用就退回更简单机制"的哲学：

- **候选召回本身不可用**（`embedding_enabled=False`，或 embedding 端点调用失败）→ 整个 Curator 流程跳过，直接退回 A 阶段 `find_duplicate_semantic()` 的二元逻辑（命中即复用旧 slug 不新建、不命中即新建，正文不变）
- **候选召回成功但 Curator LLM 调用失败 / 输出解析失败** → 直接调用 `find_duplicate_semantic()`（独立完成它自己的候选召回 + rerank 精判，不复用 Curator 阶段已算出的 top-3——多一次 embedding 调用，换取降级路径逻辑简单、不重复维护第二套候选处理代码）

两条降级路径最终都归一到调用 `find_duplicate_semantic()`，保证 Curator 层任何环节失效时，行为退回到 A 阶段已经过审查验证的逻辑，不会比 A 阶段更差。

### 5. 调用方改造

`save_template()` 内部：原来的
```python
dup = find_duplicate_semantic(data_type, keywords, title)
if dup:
    return dup["slug"]
```
改为调用 `curate_template(...)`，按其返回的 `decision` 分支处理（new/merge/discard 三态，取代原来"命中/不命中"两态）。`checker.py`（自动沉淀）与 `confirm.py`（人工确认按钮）均调用 `save_template()`，因此都自动获得新行为，两处调用代码本身不需要改动。

### 代价说明

每次自动沉淀：以前是"蒸馏"1 次 LLM 调用；现在有候选时会变成"蒸馏 + Curator 裁决"2 次。空库/新 `data_type` 任务仍是 1 次（候选为空时短路跳过 Curator）。

## 测试计划

- `curate_template` 单元测试（`patch` 掉 `achat`，参照现有 `distill_template` 测试模式）：
  - 无候选 → 直接 `new`，未调用 LLM
  - 候选存在，LLM 判 `new` → 新建
  - 候选存在，LLM 判 `merge` → 目标 slug 的 title/keywords/body 被更新，`uses`/`quality_avg`/`status` 保持不变，向量缓存条目被清除
  - 候选存在，LLM 判 `discard` → 不写任何文件
  - 候选召回不可用（embedding 关闭/端点失败）→ 退回 `find_duplicate_semantic` 二元逻辑
  - LLM 调用失败/输出解析失败 → 退回 `find_duplicate_semantic` 二元逻辑
- 回归：`test_template_learning.py`、`test_embeddings.py`、`test_checker_rerun.py` 全绿

## 验证

1. 单元测试全绿
2. 手工验证：故意构造一个与现有库内某模板高度相似但带新关键词维度的报告走一次自动沉淀，确认目标模板的 body 被合并更新、uses/quality_avg 未被清零
