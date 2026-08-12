# 模板库自学习闭环优化 · B2 阶段（教训分流 lesson channel）设计

## 背景

A 阶段（止血修复，已交付）新增了 `_looks_like_collection_failure()` 判定：一旦本次任务被判定为"采集失败/数据不足"，就阻止把这份报告沉淀为模板——防止模板库被失败任务的残缺结构污染。B1 阶段（Curator 合并裁决，已交付）解决了"命中重复时新洞察被丢弃"的问题，但两者都只处理"走了通用兜底且质量通过"的成功路径。

失败路径本身携带的信息——"这类任务（某平台+某类查询）历史上容易采集失败，应该如何提前应对"——目前被完全丢弃，没有被利用。原三阶段设计的 B 阶段拆分时已把这部分单列为 B2（教训分流），本设计是其具体方案。

## 目标（本阶段范围）

- `checker.py` 判定任务"采集失败"时，不再是死胡同，而是触发一条独立的"教训"沉淀流程
- 教训存放在新目录 `data/lessons/`（与 `data/templates/` 平行、彻底分开），一条一文件，帯 YAML frontmatter
- 教训在 `planner`（采集前）与 `analyze`（分析时）两个节点分别注入，帮助系统提前调整策略、约束报告措辞
- 语义召回贯穿创建判重与消费匹配全流程，复用 `src/memory/embeddings.py` 现有 embedding/rerank 基础设施
- 累积同类失败达到阈值才"转正"注入，避免偶发一次失败永久误导后续同类任务
- 全程无人工审核，与 checker.py 判定后全自动完成

**不做**（留给未来或明确排除）：
- 不做教训退役/淘汰机制（教训是"温和提醒"，不像模板错了会直接写进报告，长期保留无实质危害）
- 不做人工审核/确认入口（与本项目"不可能每天都看这些"的自动化方向一致）
- 不做教训库的前端管理页面
- 不改变 `_looks_like_collection_failure()` 本身的判定逻辑
- 不改变模板库（A/B1 阶段）已有的任何行为

## 设计

### 1. 存储结构

新增 `src/memory/lessons.py`，结构参照 `templates.py` 但更简单（无 discard 分支）。

`LESSONS_DIR = PROJECT_ROOT / "data" / "lessons"`，一条教训一个 `.md` 文件：

```yaml
---
title: <教训标题，LLM 生成>
data_type: comment
keywords: [小众品牌, 抖音]
status: draft | active
occurrences: 1
---
<正文：通用化的应对建议，不复述本次具体数据>
```

- `status`：`draft`（刚创建，未注入）→ `active`（累积次数达标，开始注入）。无 `retired`。
- `occurrences`：累计命中同类失败的次数。
- slug 由标题 slugify 而来，与 `templates.py` 现有做法一致，冲突时追加序号。

### 2. 语义召回

`find_similar_lesson(data_type, keywords, intent) -> Optional[Dict]`：复用 `embeddings.py` 的 `embed_texts_with_model`/`cosine`/`is_rerank_configured`/`rerank_scores`，模式与 `templates.py` 的 `_semantic_candidates`/`find_duplicate_semantic` 一致：

- 有 `data_type` 时先按 `data_type` 过滤候选（消费侧注入必然有 `data_type`，来自 `TaskSpec`）
- 无 `data_type` 时（planner 阶段，见下）不过滤，全库候选参与
- 取余弦相似度 top-3，超过 `settings.lesson_candidate_min_cosine`（默认 0.3）的候选再走 rerank 精判，精判阈值复用 `settings.template_dedup_rerank_threshold`（不新增重复配置项，两者语义一致：判断"是不是同一类"）
- 创建/合并判重（checker.py 调用）：候选范围是 **draft + active** 全部教训
- 消费侧注入（planner/analyze 调用）：候选范围**只筛 active**，draft 教训尚未验证过，不参与注入
- 语义端点不可用（`embedding_enabled=False` 或调用失败）时：创建/合并判重退回关键词 Jaccard（比对 `data_type` 相同 + `keywords` 集合重叠比例，阈值参照 `templates.py` A 阶段之前的 `find_duplicate` 实现风格）；消费侧注入直接返回空串，不阻塞主流程

### 3. 教训产出：创建与合并蒸馏

新增 `LESSON_DISTILL_SYSTEM`（`src/conductor/prompts.py`，紧邻 `TEMPLATE_DISTILL_SYSTEM`），输出结构化 JSON：

```json
{"title": "...", "keywords": ["...", "..."], "body": "..."}
```

`distill_lesson(intent, data_type, keywords, failure_signal, *, existing=None, provider=None, model=None) -> Optional[Dict]`：

- `existing=None`（未命中已有教训）：提示词要求"用一段通用化的中文描述这类任务容易踩的坑，以及应对建议；不要复述本次具体的查询内容或数据细节"
- `existing={title, body, ...}`（命中已有教训）：提示词额外带入旧教训的 `title`/`body`，要求"融合新旧两次失败信息，产出一份更完整、仍然通用化的教训，而不是简单拼接旧正文和新细节"
- 解析失败/LLM 调用异常：返回 `None`，调用方视为本次不落盘（不产生半截文件），与 `distill_template` 现有容错模式一致

`record_failure(intent, data_type, keywords, failure_signal, *, provider=None, model=None) -> None`（`checker.py` 唯一调用入口）：

1. `find_similar_lesson(data_type, keywords, intent)`（draft+active 范围）
2. 未命中 → `distill_lesson(..., existing=None)` → 成功则新建文件，`status=draft`，`occurrences=1`
3. 命中 → `distill_lesson(..., existing=旧教训)` → 成功则更新目标文件的 `body`/`keywords`（标题保留旧标题，除非新内容显著扩大适用范围——与 B1 Curator merge 的取舍一致），`occurrences += 1`；若更新前 `status=draft` 且更新后 `occurrences >= settings.lesson_promote_occurrences`（默认 2），则 `status` 转为 `active`
4. 任一步 LLM 调用失败/解析失败：整体跳过，`logger.warning` 留痕，不影响 checker 主产出（try/except 包裹）

### 4. 教训消费：注入 planner 与 analyze

- `lesson_for_analyze(spec: TaskSpec) -> str`：`find_similar_lesson(spec.data_type.value, spec.keywords, spec.intent)`（只筛 active）命中则返回 `"\n\n# 历史教训提醒\n{body}\n"`，未命中/语义不可用返回空串。`analyze` 节点在现有 `skill_for_analysis(spec)` 拼接之后追加。
- `lesson_for_planner(understanding: dict, user_input: str) -> str`：`planner` 阶段 `TaskSpec` 尚未生成，只有松散的 `understanding` 字典和原始用户输入，因此调用 `find_similar_lesson(data_type=None, keywords=[], intent=user_input)`——**不按 `data_type` 过滤，直接把原始用户输入文本嵌入去做全库语义比对**。这不是权宜之计，而是语义召回相对现有 `skills_for_planner()` 只支持 `trigger.always` 恒触发的一个真实优势（`skills/README.md` 里写明"规划阶段不支持关键词式精确匹配"，语义嵌入不需要结构化字段就能比对文本相似度，天然绕开这个限制）。命中则返回同样格式的文本，`planner.py` 在现有 `skills_for_planner()` 拼接之后追加。
- 两处注入都只影响对应节点的 system prompt 文本，不改变 `TaskSpec`/`understanding` 的数据结构，不影响下游任何解析逻辑。

### 5. checker.py 改动点

`_looks_like_collection_failure(...)` 判定为真的分支（当前只是阻止模板沉淀、无其他动作的分支）新增：

```python
if lesson_settings_enabled:
    try:
        await record_failure(
            spec.intent, spec.data_type.value, list(spec.keywords or []),
            failure_signal=analysis, provider=state.get("provider"), model=state.get("model"),
        )
    except Exception:
        logger.warning("教训沉淀失败（不影响产出）", exc_info=True)
```

不改变该分支原有的"不沉淀模板"行为，只是新增一条并行动作。

### 6. 新增配置（`src/config/settings.py`）

- `lesson_learning_enabled: bool = True`（总开关）
- `lesson_promote_occurrences: int = 2`（转正所需累计次数）
- `lesson_candidate_min_cosine: float = 0.3`（候选粗筛下限）

## 测试计划

新增 `scripts/test_lesson_learning.py`（模式参照 `scripts/test_template_learning.py`，无 pytest，`def test_x(): assert` + `main()` 收集 PASS/FAIL）：

- `find_similar_lesson`：`data_type` 过滤生效；余弦+rerank 精判命中/不命中；语义不可用时退回关键词 Jaccard 判重
- `record_failure`：
  - 未命中 → 新建文件，`status=draft`，`occurrences=1`，文件内容包含 LLM 蒸馏结果
  - 命中 draft，合并后 `occurrences` 达到 `lesson_promote_occurrences` → `status` 转 `active`，正文被合并蒸馏更新
  - 命中 active → `occurrences` 继续累加，正文继续合并更新，`status` 保持 `active`
  - `distill_lesson` 返回 `None`（LLM 失败/解析失败）→ 不落盘、不抛异常
- `lesson_for_analyze`：命中 active 返回文本；只有 draft（未转正）不返回；语义不可用返回空串
- `lesson_for_planner`：不依赖 `data_type`，命中 active 返回文本；语义不可用返回空串
- 回归：`scripts/test_checker_rerun.py`（确认 checker 新分支不影响原有质检/模板逻辑全绿）、`scripts/test_embeddings.py`、`scripts/test_template_learning.py` 全绿

## 验证

1. 单元测试全绿
2. 手工验证：故意构造一个会被判定为"采集失败"的任务跑两次（同类关键词/data_type），确认第一次生成 `draft` 教训文件、第二次合并蒸馏后转 `active`；再跑一次同类任务（不失败），确认 planner/analyze 的 prompt 中出现历史教训提醒（查看 trace/日志）
