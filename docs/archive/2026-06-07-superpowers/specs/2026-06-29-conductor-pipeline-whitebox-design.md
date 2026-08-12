# 设计：Conductor 主链路准确性重构 + 节点白盒流式

日期：2026-06-29
状态：已与用户确认，待转实现计划

## 背景（为什么做）

用户反馈：从自然语言开始，智能体「理解不准」，且怀疑 planner 规划、router 路由、clean 清洗这条主通路都有问题。

经代码核查确认的真实弱点：

1. **planner 名不副实**：[planner.py](../../../../src/conductor/nodes/planner.py) 只做 pydantic 校验（`TaskSpec.from_draft`），不规划、不调 LLM。全部「理解+决策」压在 intent 一次 LLM 调用上；intent 出错则后续全错，且无补救。
2. **静默吞错**：`TaskSpec.from_draft`（[task_spec.py:101](../../../../src/conductor/task_spec.py#L101)）逐字段「出错兜默认」，会把模型判错悄悄变成 `generic`/`summary` 等默认值，用户与日志都看不到。
3. **intent 脆弱**：[intent.py:30](../../../../src/conductor/nodes/intent.py#L30) 单发零样本 LLM 直接吐 12 字段 JSON，无 few-shot、无重试；解析失败直接转追问。
4. **router 精确字符串匹配**：专用采集器靠硬编码别名表精确匹配平台名（如 [social_media_collector.py:33](../../../../src/collectors/social_media_collector.py#L33) 的 `_PLATFORM_MAP`）。平台名对不上就静默降级到通用引擎。
5. **clean 规则浅**：[clean.py](../../../../src/conductor/nodes/clean.py) 仅去空/去重/截断/封顶，无 HTML/样板噪声剥离。
6. **chat 节点黑盒**：前端只显示节点进度名，节点内部产出（理解结果、策略、候选采集器、清洗前后等）对用户不可见。

## 已与用户确认的决策

1. planner **升级为真正的 LLM 规划器**。
2. 分工：**intent 管理解，planner 管规划**（两次 LLM 调用职责不重叠）。
3. 流式粒度：**节点级白盒**（每个节点完成时推送结构化摘要；不做报告 token 级流式）。
4. 白盒卡片**不持久化进 DB**，仅本轮对话内存态可见可展开。

## 总体架构（改动聚焦在前两环 + SSE + 前端，采集/分析/产出逻辑不动）

```
intent(LLM·理解+追问闸门) → planner(LLM·真规划)+pydantic校验 → router(规则+容错归一)
  → collect(不改) → clean(增强) → analyze/checker/output(不改)
                         │
                         ▼  每节点完成 → build_node_view → SSE node 事件(含 view)
                                                              → 前端节点白盒卡片(完成自动折叠/可展开)
```

## 详细设计

### ① intent —— 理解 + 追问闸门（LLM）

- **职责收窄**：只判断「听懂没 / 要不要追问」，产出松散 `understanding`：
  - `intent`（意图一句话）、`signals`（采什么 / 从哪采 / 要什么产出，自然语言或松散字段即可）。
  - 信息严重不足 → `needs_clarification=true` + 一个最关键问题。
- **改进**：`INTENT_SYSTEM` 加 2–3 个 few-shot 示例；JSON 解析失败**重试 1 次**，再失败才降级为追问。
- **state**：新增 `understanding: dict`；**移除 `spec_draft` 产出**（planner 不再读它）。
- **接口契约**：返回 `{needs_clarification, clarification_question, understanding}`。

### ② planner —— 真 LLM 规划器（替换原 pydantic-only）

- **输入**：`understanding` + 原始 `user_input` + 用户偏好（`preferences_context()`）+ **已知平台词表**（新接口）。
- **职责**：把理解转成完整执行策略，输出草稿字段：`platforms`（归一到词表）/`urls`/`keywords`/`data_type`/`time_range`/`max_items`/`login_strategy`/`analysis_type`/`analysis_instruction`/`outputs`/`db_target`/`email_to`/`schedule`，外加 `reasoning`（决策理由，给白盒回显与日志）。
- **校验**：照旧走 `TaskSpec.from_draft(draft, fallback_text=user_input)`。
- **降级（防阻断）**：planner LLM 调用或解析失败 → 用 `understanding` 直接 `from_draft` 兜底，流程继续。
- **防静默吞错**：planner 触发降级、或校验把关键字段兜成默认值时 `logger.warning`。
- **state**：产出 `task_spec`（不变）；新增 `plan_reasoning: Optional[str]`。
- **prompt**：新增 `PLANNER_SYSTEM`（含 few-shot + 词表占位）；解析失败重试 1 次。

### 新接口：`collectors.known_platforms()`

- 位置：`src/collectors`（如 `registry.py` 或新 `platforms.py`），导出聚合各采集器别名表 key 的平台名清单（社媒 `_PLATFORM_MAP`、电商、search 域名表等）。
- 用途：注入 planner prompt，让模型输出的平台名**从源头可被路由命中**（治本）。

### ③ router —— 容错归一（主逻辑不变）

- 保留 `select_collectors = is_available + matches + tier 升序`。
- **平台名解析增强**：抽出统一 `normalize`（去空格 / 大小写 / 常见变体）+ 包含匹配，供各采集器的平台解析复用，减少专用采集器漏匹配。两层防御：planner 知道词表（治本）+ router 容错（兜底）。

### ④ clean —— 清洗增强

- 保留：去空 / `(url, 正文前200字)` 去重 / 单条 `clean_max_item_chars` 截断 / `max_items` 封顶。
- **新增噪声剥离**（截断前执行）：HTML 标签清除、连续空白折叠、超短导航/样板片段过滤、markdown 残留清理。规则集中在清洗模块内，便于调参。

### ⑤ 前端白盒化 + 节点级流式

- **SSE 协议**：`astream_conductor` 的 node 事件由 `("node", name)` 扩为携带 view；`/api/chat/stream` 的 `node` 事件数据从 `{node,label}` 扩为 `{node,label,view}`。
- **新模块 `src/conductor/node_views.py`**：`build_node_view(node, delta, values) -> dict`，按节点产出展示摘要：
  - intent→理解(`understanding`)；planner→关键策略字段 + `plan_reasoning`；router→`collector_candidates`；collect→`collector_used` + 原始条数；clean→清洗前后条数；analyze→`analysis_source` + 报告；checker→`quality`；output→产物路径；schedule→`schedule_request`。
- **前端**：Chat 流内新增节点白盒卡片序列（新组件 `NodeStream` / `NodeCard`）。进行中=展开显示 `view`；**完成自动折叠**；chevron **点击展开**。卡片随节点逐个出现。原 `PipelineTracker` 保留作总进度条。
- **作用域**：卡片本轮内存态可见可展开，**不写 DB**；刷新后以最终报告为准。

## 测试（TDD，先写测试）

- `test_intent`：mock LLM → 产 `understanding` / 触发追问 / 解析失败重试。
- `test_planner`：mock LLM → 产合法 `TaskSpec`、平台归一、**LLM 失败降级 from_draft**、降级告警。
- `test_router`：平台别名/大小写/变体 → 命中正确专用采集器。
- `test_clean`：HTML / 样板 / 空白 → 被剥离；去重/截断/封顶仍生效。
- `test_node_views`：各节点 delta → 正确的 view 结构。
- 前端 `npm run build` 通过。
- **回归**：现有 67 测试全绿（依赖 `spec_draft` 的旧测试同步更新为 `understanding`）。

## 风险与注意

- **多一次 LLM 调用**：延迟与成本上升（用户已接受）；intent 瘦身可部分抵消。
- **state 契约变更**：`spec_draft → understanding`、新增 `plan_reasoning`，需全量 grep 改到所有引用（节点、测试、可能的 checkpoint 序列化）。
- **SSE 负载变大**：node view 含报告正文，体积可控（report 仅在 analyze 节点带一次）。
- **本仓库非 git**：设计文档不提交，仅存档于 `docs/superpowers/specs/`。
