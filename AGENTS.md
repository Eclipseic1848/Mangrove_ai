# Mangrove 工程协作约定

> 状态：active
>
> 最后核验：2026-08-11

## 1. 规则优先级

1. 本文件是仓库级工程规则。
2. 可以叠加贡献者自己的本机 Agent 规则；发生冲突时以本文件为准。本机绝对路径不得写入仓库。
3. 业务范围、数据含义、权限、安全边界、外部发布和不可逆操作必须由用户确认。
4. 所有对话使用简体中文；代码注释使用简洁中文；文本文件统一 UTF-8。

## 2. 开工前必读

按顺序读取：

1. `handoff.md`：当前分支、在制工作、阻塞与下一门禁。
2. `docs/status/current.md`：唯一的当前能力和路线状态台账。
3. `CONTEXT.md`：统一任务域词汇和长期语义。
4. `docs/agents/`：Issue、标签和领域文档约定。
5. 当前工单引用的规格、ADR 与执行报告。

历史计划和报告是证据，不是当前状态来源。出现冲突时，以代码/数据库实况和
`docs/status/current.md` 的最新核验为准，并修正文档，不得静默选择方便的版本。

## 3. 变更原则

- 先说明假设、解释多种可能，不确定时停下确认。
- 只做实现目标所需的最小变更，不顺手重构、不扩大范围。
- 保留现有风格；只清理由本次变更直接产生的孤儿代码。
- 关键权限、安全、状态转换、失败关闭与降级逻辑必须用中文注释说明“为什么”。
- 任何实现都要有与风险相称的验证证据；测试通过不等于用户验收或生产资格。
- 测试源码是回归安全网，不能在验证后删除；可删除的是生成物、临时日志和一次性探针。
- 优先评估成熟开源组件，但接入前必须验证版本、适配边界、数据外发与可恢复性。

## 4. Git 与发布边界

- 当前公开开发分支为 `main`；首次公开快照承接 `v0.0.8` 的开发能力，但没有创建同名标签或封板。
- `v0.0.4` 是稳定封板标签，不得移动或回写。
- 只有用户明确授权才能创建分支、标签、版本、PR、Release、提交或推送。
- 公开开发远端为 `origin`（`Eclipseic1848/Mangrove_ai`）；执行前仍必须现场核对，不能套用历史记忆。
- 工作树可能包含用户或其他任务改动。使用明确文件允许列表，禁止 `git add .`。
- 禁止 `git reset --hard`、`git clean`、强推或未经确认覆盖本地改动。
- 本机设置、绝对路径、Secret、运行数据和本地审计报告不得进入版本控制。

## 5. 稳定产品边界

- `8088` 是统一产品入口；`5173` 只用于前端开发。
- 维护者本机的 `start_all.bat`、`stop_all.bat` 包含本地解释器、局域网地址和服务编排，必须
  留在本机并由 `.gitignore` 排除；公开环境使用 `scripts/dev_reload.py` 和明确的资源清理命令。
- 本机停止逻辑只能清理经项目路径、标记或祖先进程验证的进程树；未知端口占用只能报警。
- 当前主工作台为 `/data-prep`；历史任务兼容入口与 Legacy Delivery 读取在完成迁移前不得删除。
- 只有 `delivery_published` 且通过完整性/QA 的 `output_id` 是正式交付。
- Candidate、`eligible_for_delivery`、中间 AST、Parquet 或验证通过状态都不能冒充正式交付。
- TaskRevision、来源快照、连接版本、外发确认、能力 digest 和 Owner 隔离必须冻结且失败关闭。
- 普通用户、管理员、超级管理员是产品角色；“高级用户”不是权限角色。
- 管理员可查看跨 Owner 的任务管理元数据；读取个人业务正文必须显式说明原因并产生审计记录。

## 6. 当前技术入口

- FastAPI：`src/api/`
- 统一前端：`frontend/`
- Conductor：`src/conductor/`
- 语义工作台：`src/semantic_harness/`、`src/api/routes/semantic_workspace.py`
- Agentic Runtime：`src/agentic_runtime/`
- 能力目录与治理：`src/capability_catalog/`、`src/capability_governance/`
- 任务级能力宿主：`src/capability_host/`
- 模型连接：`src/model_connections/`
- 测试：`tests/`，以及仍由 `pytest.ini` 收集的 `scripts/test_*.py`
- 当前状态：`docs/status/current.md`

## 7. 当前版本不可误述事项

- AC-07 #33 已完成、迁移、推送并关闭。
- #34 已完成工程实现、双轴审查与带备份生产迁移；最终用户灰度状态以
  `docs/status/current.md` 为准。
- #35 已完成 Trivy/Syft 工程实现和真实双包扫描，但在 code-review、生产迁移与用户验收前，
  不能表述为已晋级、签名或发布。
- AC-06 两项历史 `admin_gray_only` 兼容包只是过渡例外，不扩大普通用户权限。
- 30 项泛化集、完整 PG-05、真实外部 Provider 安全端到端、远程 MCP 与 8B 仍未完成。

## 8. 文档职责

- `README.md`：产品、启动和最短验收。
- `AGENTS.md`：工程规则与稳定边界。
- `CONTEXT.md`：领域词汇，不维护滚动进度。
- `handoff.md`：当前工作和接手步骤。
- `docs/status/current.md`：唯一滚动状态台账。
- `docs/adr/`：不可变决策记录。
- `docs/plans/`：规格、任务拆分和执行证据；完成后不再充当当前状态。

架构、约定或产品边界发生变化时，修改对应的唯一权威文档，禁止把同一状态复制到多个文件。

## Agent skills

### Issue tracker

Issue 和 PRD 统一使用 `Eclipseic1848/Mangrove_ai` 的 GitHub Issues。参见
`docs/agents/issue-tracker.md`。

### Triage labels

使用五个默认分诊标签：`needs-triage`、`needs-info`、`ready-for-agent`、
`ready-for-human`、`wontfix`。参见 `docs/agents/triage-labels.md`。

### Domain docs

采用单上下文领域文档布局，使用根目录 `CONTEXT.md` 和 `docs/adr/`。参见
`docs/agents/domain.md`。
