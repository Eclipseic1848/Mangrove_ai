# 学到的分析报告模板（自演进技能 · 阶段1 + v1.1.0 方案 A/C）

本目录**独立于** `skills/`（那里放工程师手写、无 frontmatter 的技能文档，见 `skills/README.md`）。
本目录存放 Agent **自学习沉淀**的分析报告模板。当一次任务走了通用兜底（没命中内置领域模板）时，
前端会出现「沉淀为模板」按钮；用户确认后，Agent 把本次报告结构蒸馏成模板写入这里，
下次同类任务自动命中复用（详见 `src/memory/templates.py`、`AGENTS.md`）。

每个模板是一个带 frontmatter 的 `.md` 文件：

```markdown
---
title: 政策解读报告
data_type: article        # 对应 TaskSpec.data_type
keywords: [政策, 解读, 通知]  # 用于匹配同类任务（命中 intent/keywords 即复用）
status: draft              # draft | active | retired
uses: 0                    # 累计使用次数
quality_avg: 0             # 平均质量分
created_at: '2026-07-13T...'
---
<一段中文 system prompt：描述这类任务应输出的报告结构>
```

匹配规则：`data_type` 一致 **且** 至少一个 keyword 出现在任务的 intent/keywords 中；多个命中取关键词命中数最多者。
启用 embedding 后先走语义召回（向量余弦 + rerank 精判），端点不可用退回关键词匹配。

## 并发安全（v1.1.0 方案 A）

所有写操作（`save_template`/`record_template_use`/`apply_patrol_merge`/`delete_template`/
`_save_vectors`）加 `threading.RLock` 保护 read-modify-write，写盘用 `atomic_write`（临时文件 +
`os.replace`）防止并发读看到半截文件。`load_templates` 加 `MtimeCache` 缓存，写后主动失效。

## INDEX.md（v1.1.0 方案 C）

每次写操作后自动重建 `INDEX.md`（一行一条概要：slug/标题/类型/状态/使用次数/质量分），
管理员可一眼看全貌。无 frontmatter 的 INDEX.md 会被加载器自动跳过，不影响功能。

向量缓存文件 `_vectors.json` 按「模型+文本」哈希隔离，后端切换时自动重算不混用。

本 README 无 frontmatter，会被加载器自动忽略，不影响功能。

## 版本控制边界

该目录除本 README 外均为运行中学习数据，可能派生自用户任务，不进入公开版本。内置、可审核
且不含业务数据的模板应作为源码模块单独维护；用户确认沉淀的模板只能通过受控迁移共享。
