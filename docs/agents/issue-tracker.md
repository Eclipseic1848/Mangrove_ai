# 问题跟踪器：GitHub

本仓库当前和新增的 Issue、PRD 统一记录在
`Eclipseic1848/Mangrove_ai` 的 GitHub Issues。

仓库还保留旧 GitHub 远端。所有 Issue 写操作必须显式指定
`--repo Eclipseic1848/Mangrove_ai`，不得依赖 `gh` 自动推断。

历史文档中明确属于 `Eclipseic1848/Mangrove_platform` 的旧工单仍是历史证据；
不得把旧编号静默解释为新仓库 Issue，也不得未经授权迁移、修改或关闭旧工单。

## 常用操作

- 创建：
  `gh issue create --repo Eclipseic1848/Mangrove_ai --title "..." --body "..."`
- 查看：
  `gh issue view <编号> --repo Eclipseic1848/Mangrove_ai --comments`
- 列表：
  `gh issue list --repo Eclipseic1848/Mangrove_ai --state open`
- 评论：
  `gh issue comment <编号> --repo Eclipseic1848/Mangrove_ai --body "..."`
- 增删标签：
  `gh issue edit <编号> --repo Eclipseic1848/Mangrove_ai --add-label "..."`
  或 `--remove-label "..."`
- 关闭：
  `gh issue close <编号> --repo Eclipseic1848/Mangrove_ai --comment "..."`

多行正文应使用不会破坏 UTF-8 中文内容的临时文件或 PowerShell here-string，并通过
`--body-file` 传入。

## Pull Request 是否进入分诊

**否。** Pull Request 不作为需求或问题入口，不进入与 Issues 相同的分诊队列。

若未来改变此约定，应先更新本文件，再启用对应 PR 分诊流程。

## 工程技能约定

- “发布到问题跟踪器”：在 `Eclipseic1848/Mangrove_ai` 创建 GitHub Issue；
- “读取相关工单”：读取指定 Issue 的正文、标签和评论；
- 创建、编辑、评论、加标签或关闭 Issue 都属于远端写操作，必须符合当前任务授权；
- 不得因读取本文件而自动创建 Issue 或标签。

## Wayfinder 约定

- 地图使用一个带 `wayfinder:map` 标签的 Issue；
- 子任务优先使用 GitHub sub-issue；不可用时，使用任务列表并在子 Issue 顶部写
  `Part of #<地图编号>`；
- 阻塞关系优先使用 GitHub 原生 issue dependency；不可用时在正文顶部维护
  `Blocked by: #<编号>`；
- 领取任务使用 assignee，解决任务时先写明结论，再关闭 Issue；
- 所有操作显式指定 `--repo Eclipseic1848/Mangrove_ai`。
