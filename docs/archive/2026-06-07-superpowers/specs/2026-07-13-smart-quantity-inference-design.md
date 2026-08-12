# 智能数量推断 设计文档

**功能**: 意图理解/规划节点不再固定追问"要多少条数据"，改为基于语义推断合理数量，仅在大规模采集需求时追问。

---

## 问题

`planner_node` 硬编码了"总是追问"逻辑（`planner.py:89` 注释直接写"总是追问"），同时 `PLANNER_SYSTEM` 告诉 LLM "没提数量就填 null，不要臆造"。两层协同一刀切——每次用户没说具体数字，系统就问。

## 方案

### ① `PLANNER_SYSTEM` prompt：让 LLM 推断数量

把 `max_items` 规则从"没提就 null → 等系统追问"改为"没提就基于语义推断一个合理值"：

```
- max_items：基于用户语义推断一个合理的采集数量：
  - 明确给了数字（"50条""前20个""十几篇"）→ 原样填入
  - 探索性语气（"看看""试试""简单了解""查一下"）→ 填 5-15
  - 常规任务（"搜集评价""汇总新闻"）→ 填 20-40
  - 大规模需求（"全部""所有""尽可能多""全面搜集""统统"）→ 填 null（代码层追问上限）
  - 给了具体 URL 列表 → 不必填此项
```

### ② `planner.py`：仅大规模追问，其余静默推断

```python
qty = _detect_quantity(draft, user_input)
cap = settings.collector_max_items
if qty is not None:
    spec.max_items = max(1, min(qty, cap))
elif spec.urls:
    pass  # 有 URL 不追问
elif _looks_like_massive(user_input):
    out["needs_clarification"] = True
    out["clarification_question"] = f"本次大概需要采集多少条数据？回一个数字（1–{cap}），越多耗时越长。"
else:
    spec.max_items = min(20, cap)  # 静默认
```

新增 `_looks_like_massive` 关键词检测：
```python
_MASSIVE_HINTS = ("大量", "全部", "所有", "尽可能多", "全面", "穷举", "统统", "一个不落")
```

### ③ 透明告知

`planner_node` 返回 `inferred_quantity`，chat.py 在 reply 中附加提示：

> 本次将采集约{X}条数据（系统默认，可随时告知调整）

用户没主动说数量时附加这条，让用户知道可以改。

---

## 效果

| 输入 | 推断 | 行为 |
|------|------|------|
| "看看小米SU7口碑" | 10 | 直接采 10 条，告知 |
| "汇总最近汽车新闻" | 25 | 直接采 25 条，告知 |
| "采集50条" | 50 | 直接采 50 条 |
| "把所有差评找出来" | null | 追问上限 |

---

## 涉及文件

- `src/conductor/prompts.py` — PLANNER_SYSTEM 的 max_items 规则
- `src/conductor/nodes/planner.py` — 追问逻辑 + `_looks_like_massive` + `inferred_quantity`
- `src/api/routes/chat.py` — `_build_result` 附加透明告知
