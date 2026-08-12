# 失败教训（自学习 · B2 阶段 + v1.1.0 方案 B/C/D）

本目录**独立于** `data/templates/`（那里存放报告结构的自学习模板，见 `data/templates/README.md`）
与 `skills/`（工程师手写、无 frontmatter 的经验技能，见 `skills/README.md`）。

本目录存放 Agent 从**采集失败**任务中自动蒸馏出的教训（这类任务容易踩的坑、应如何应对）。
当 Checker 判定某次任务"采集失败/数据不足"（`_looks_like_collection_failure`）时，会自动调用
`record_failure()`（`src/memory/lessons.py`）把失败现象蒸馏成一条通用化的教训写到这里，
下次同类任务在 `planner`/`analyze` 节点自动召回注入提醒--全程无需人工确认。

每条教训是一个带 frontmatter 的 `.md` 文件：

```markdown
---
title: 抖音小众品牌评论采集不足
data_type: comment        # 对应 TaskSpec.data_type
keywords: [小众品牌, 抖音]   # 用于语义匹配同类任务
status: draft              # draft | active | retired（v1.1.0 方案 B）
occurrences: 1              # 累计命中失败次数
helped_avoid: 0             # 累计帮后续任务避免同类失败的次数（v1.1.0 方案 B）
created_at: '2026-07-13T...'
---
<一段中文提醒：通用化描述这类任务容易踩的坑以及应对建议>
```

## 生命周期（v1.1.0 方案 B 改造后）

- **创建**：`record_failure` 蒸馏失败现象 -> 落盘 `draft`，`occurrences=1`、`helped_avoid=0`。
- **累积**：同类任务再次失败 -> `occurrences+1`（不再自动转正）。
- **转正**：某次任务命中该 active 教训且 Checker 判定通过 -> `record_lesson_helped` 使
  `helped_avoid+1`；当 `occurrences≥2 && helped_avoid≥1` 时 `draft -> active` 开始被注入。
- **退役**：`active` 教训 `occurrences≥10 && helped_avoid==0`（多次失败从未帮到）-> `retired`，
  不再被消费侧召回注入（类比模板库死区清理）。

## 召回与注入（v1.1.0 方案 C 改造，2026-07-13 复验修复判据错配）

`find_active_lessons` 返回多条 active 教训，按 `helped_avoid/occurrences` 有效性降序取 top-3，
`lesson_for_analyze`/`lesson_for_planner` 召回多条拼接提醒。语义不可用时退回关键词匹配（单条）。

**消费侧召回判据与创建侧判重判据不同质，不能共用**：消费侧用独立的 `_LESSON_RECALL_INSTRUCT`
（"这条教训对当前任务是否有帮助"）+ `lesson_recall_rerank_threshold`（默认 0.35），不是创建侧
`find_similar_lesson` 判重用的 `_LESSON_RERANK_INSTRUCT`（"两条教训是否同类失败场景"）+
`template_dedup_rerank_threshold`（0.7）。曾误用后者导致库中已有的 active 教训因阈值过严召不回，
2026-07-13 复验时用真实端点冒烟发现并修复。

## INDEX.md（v1.1.0 方案 C）

每次写操作后自动重建 `INDEX.md`（一行一条概要：slug/标题/类型/状态/命中次数/有效次数），
管理员可一眼看全貌。无 frontmatter 的 INDEX.md 会被加载器自动跳过，不影响功能。

## 命中埋点（v1.1.0 方案 D，2026-07-13 补未命中记录）

`lesson_for_analyze`/`lesson_for_planner` 无论命中与否都写一条记录到 `webui.db.memory_hit_log` 表
（hit_type/slug/threshold/degrade_path/task_id/**hit**）。`hit=1` 为命中、`hit=0` 为未命中
（未命中时 slug 为空，`degrade_path` 保留诊断信息：`none`=库中无同类型候选、`semantic`=语义召回
执行过但候选被 rerank/阈值筛空）。`memory_hit_log_stats()` 按 hit_type 聚合 `count`（总尝试数）/
`hit_count`（真命中数）供概览页算真实命中率——未命中也要记，否则命中率永远是"分子/分子"看不出
召回是否有问题。

本 README 无 frontmatter，会被加载器自动忽略，不影响功能。

## 版本控制边界

该目录除本 README 外均为运行中学习数据，可能派生自用户任务，不进入公开版本。新环境从空库
开始学习；需要迁移时应使用经过脱敏、Owner 授权和审计的独立数据迁移流程，不能直接提交 Git。
