# 智能数量推断 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) to implement this plan task-by-task.

**Goal:** planner 不再固定追问"要多少条"，改为 LLM 语义推断 + 仅大规模需求追问 + 透明告知。

**Architecture:** 改 `PLANNER_SYSTEM` prompt 让 LLM 推断 max_items；改 `planner.py` 追问逻辑为仅大规模关键词触发；改 `chat.py` 附加透明告知。零新增 LLM 调用。

**Tech Stack:** Python + FastAPI

## Global Constraints

- 不改动 `intent_node`（它不管数量）
- 不新增 LLM 调用
- `_looks_like_massive` 用简单关键词匹配，不调模型
- 透明告知仅在用户未主动指定数量时附加
- `spec.max_items` 始终被 `collector_max_items`（默认 30）封顶

---

### Task 1：planner prompt + 代码逻辑 + 透明告知（单任务）

**Files:**
- Modify: `src/conductor/prompts.py` — PLANNER_SYSTEM 的 max_items 规则
- Modify: `src/conductor/nodes/planner.py` — 追问逻辑 + `_looks_like_massive` + `inferred_quantity`
- Modify: `src/api/routes/chat.py` — `_build_result` 附加透明告知

- [ ] **Step 1: 改 PLANNER_SYSTEM prompt**

`src/conductor/prompts.py` 第 201 行，把：

```
- max_items：用户**明确提到条数**（如"50条""前20个""十几篇"）才填该数字；**没提数量就填 null**，
  不要自己臆造默认值（系统会按需追问用户）。给了具体 URL 列表的任务可不必纠结此项。
```

改为：

```
- max_items：基于用户语义推断一个合理的采集数量：
  - 明确给了数字（"50条""前20个""十几篇"）→ 原样填入
  - 探索性语气（"看看""试试""简单了解""查一下""快速"）→ 填 5-15
  - 常规任务（"搜集评价""汇总新闻""采集数据"）→ 填 20-40
  - 大规模需求（"全部""所有""尽可能多""全面搜集""统统""一个不落"）→ 填 null
  - 给了具体 URL 列表 → 不必填此项
```

- [ ] **Step 2: 改 planner.py — 新增 `_looks_like_massive` + 追问逻辑**

`src/conductor/nodes/planner.py`，在 `_DEFAULT_HINTS`（第 25 行）之后新增：

```python
# 大规模采集语义信号：用户表达了要穷尽/全量，但没说具体数字时追问上限
_MASSIVE_HINTS = ("大量", "全部", "所有", "尽可能多", "全面", "穷举", "统统", "一个不落")
```

新增 `_looks_like_massive` 函数（在 `_detect_quantity` 之后）：

```python
def _looks_like_massive(user_input: str) -> bool:
    """检测用户是否表达了大批量采集需求（需追问上限）。"""
    text = user_input.strip()
    return any(h in text for h in _MASSIVE_HINTS)
```

然后把第 89-101 行：

```python
    # 数量策略：总是追问。用户未明确数量时短路追问；给了具体 URL 的任务除外（数量≈URL数）。
    qty = _detect_quantity(draft, user_input)
    cap = settings.collector_max_items
    if qty is not None:
        spec.max_items = max(1, min(qty, cap))
        out["needs_clarification"] = False
        out["clarification_question"] = None
    elif not spec.urls:
        out["needs_clarification"] = True
        out["clarification_question"] = (
            f"本次要采集多少条数据？请回一个数字（1–{cap}），数量越多耗时越长；"
            f"也可直接回"默认"按 {cap} 条处理。"
        )
    return out
```

改为：

```python
    # 数量策略：LLM 语义推断 + 仅大规模需求追问
    qty = _detect_quantity(draft, user_input)
    cap = settings.collector_max_items
    if qty is not None:
        spec.max_items = max(1, min(qty, cap))
        out["needs_clarification"] = False
    elif spec.urls:
        out["needs_clarification"] = False
    elif _looks_like_massive(user_input):
        out["needs_clarification"] = True
        out["clarification_question"] = (
            f"本次大概需要采集多少条数据？回一个数字（1–{cap}），越多耗时越长。"
        )
    else:
        spec.max_items = min(20, cap)
        out["needs_clarification"] = False
        out["inferred_quantity"] = spec.max_items
    return out
```

- [ ] **Step 3: 改 chat.py — `_build_result` 附加透明告知**

`src/api/routes/chat.py`，在 `_build_result` 返回 dict 之前（约第 140 行 `return {` 之前），新增对 `inferred_quantity` 的处理：

```python
    # 透明告知：系统自动推断了采集数量，让用户知道可以改
    if state.get("inferred_quantity") and not state.get("needs_clarification"):
        n = state["inferred_quantity"]
        reply = f"{reply}\n\n> 💡 本次将采集约 {n} 条数据（系统默认，可随时告知调整数量）。"
```

- [ ] **Step 4: 跑回归测试**

```bash
python scripts/test_planner.py
```

预期：所有测试通过。`test_plan_asks_quantity_when_unspecified` 的行为会因本次改动而变化——该测试断言"未指定数量时必定追问"，改动后变为"未指定时不再追问"。需要更新该测试用例。

- [ ] **Step 5: 更新测试用例**

找到 `scripts/test_planner.py` 中 `test_plan_asks_quantity_when_unspecified`，将其改为验证"未指定数量时不再追问，而是使用默认值"：

```python
def test_plan_defaults_quantity_when_unspecified():
    """未明确数量时不再追问，静默使用默认值并透明告知。"""
    ...
```

- [ ] **Step 6: 验证全量回归**

```bash
python scripts/test_planner.py
python scripts/test_e2e.py  # 如有端到端测试
```

- [ ] **Step 7: Commit**

```bash
git add src/conductor/prompts.py src/conductor/nodes/planner.py src/api/routes/chat.py scripts/test_planner.py
git commit -m "feat: planner 智能数量推断替代固定追问，仅大规模需求追问 + 透明告知"
```
