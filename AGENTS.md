# Mangrove 工程协作约定

> 状态：active
>
> 最后核验：2026-08-24

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
- 缺少适配工具或必须新增依赖时，先向用户说明用途、收益、版本与风险并询问是否安装；未经
  确认不得安装，也不得静默改走明显更低效或降低验证质量的替代路线。`agent-browser` 等能改善
  整体实现或验收质量的工具属于可提请安装的正常方案，不应因当前缺失而直接放弃。

## 4. Git 与发布边界

- 当前公开开发分支为 `main`；首次公开快照承接 `v0.0.8` 的开发能力，但没有创建同名标签或封板。
- `v0.0.4` 只表示历史稳定版本语义，不能据此推断远端仍有同名 Tag 或 Release；Tag/Release
  必须现场查询，未经授权不得创建、移动或回写。
- 只有用户明确授权才能创建分支、标签、版本、PR、Release、提交或推送。
- 公开开发远端为 `origin`（`Eclipseic1848/Mangrove_ai`）；执行前仍必须现场核对，不能套用历史记忆。
- `main` 当前由 repository Ruleset 强制 PR、讨论解决、strict 三项 minimum-ci 与禁止强推，
  `bypass_actors=[]`。仓库只有一名维护者，经用户确认采用审批数 0 的单维护者模式；不得误述为
  已有独立人工审批。未来提升审批数必须先有第二位真人维护者并另行授权、复验。
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
- Mangrove 自有 Schema 只允许经 `src/database_migrations` 显式迁移；Repository 和 startup
  只验证版本并失败关闭。LangGraph checkpoint 与用户连接器数据库不属于该迁移体系。
- `runtime_config` 的 25 个 `secret=True` 键只保存 Owner/配置键绑定的 SecretRef，原值由共享
  Vault 密文边界持有；生产原库迁移、旧备份处置和 Secret/Key 轮换仍是独立人工门。
- 依赖职责分为 runtime、collectors、dev、evaluation、gpu；生产镜像只安装 runtime 与
  collectors。GPU overlay 当前为空，不得无证据加入 CUDA/Triton。
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
- 显式数据库迁移：`src/database_migrations/`
- 配置 SecretRef：`src/config/secret_refs.py`
- 依赖分组：`requirements.txt`、`requirements-collectors.txt`、`requirements-dev.txt`、
  `requirements-evaluation.txt`、`requirements-gpu.txt`
- 测试：`tests/`，以及仍由 `pytest.ini` 收集的 `scripts/test_*.py`
- 当前状态：`docs/status/current.md`

## 7. 当前版本不可误述事项

- AC-07 #33 已完成、迁移、推送并关闭。
- #34 已完成工程实现、双轴审查、带备份生产迁移、真实灰度与用户验收。
- #35 已完成 Trivy/Syft 工程实现、真实双包扫描、双轴审查、带备份生产迁移与用户验收。
- 新仓库 #9 已完成标准 OCI image signature 本地 PoC、双轴审查和用户验收；该结论不代表
  能力晋级、平台发布或普通用户开放。
- 新仓库 #10 已完成个人能力自动晋级机制（判定门、幂等/并发、缺口投影、双轴审查、生产迁移
  0004 与 PR #20 合并）；该结论不代表任何能力已自动晋级、平台已发布或普通用户受众扩大。
  真实灰度包晋级已在 #15/#16 纵切面真实发生（gray-python-table 2.0.0/3.0.0、
  gray-everything-mcp 2026.7.4 均经真实验证链晋级 verified）。
- 新仓库 #11 已完成管理员审核与业务内容审计查看（跨 Owner 审核聚合、审计查看命令、不可变
  审计记录、双轴审查修复复核、零 DDL 与 8088 验收）；审计查看只能读取验证证据绑定的冻结
  任务正文，Secret/宿主路径/原始工具日志连审计查看都不提供；该结论不代表平台已发布或
  普通用户受众扩大。
- 新仓库 #12 已完成独立平台快照、签名与 admin_gray 发布机制（脱敏快照、平台六步验证、
  #9 签名直用、发布/受众变更命令、生产接线与迁移 0005、四轮双轴复核、8088 验收）；发布
  受众固定 admin_gray，受众变更命令无产品入口；装载结构探针为目录级实现，真实 Capability
  Host 执行探针与真实发布纵切面已在 #15/#16 真实完成（真实装载调用、篡改→自动隔离、
  独立 Layout 复验）；该结论不代表平台已发布或普通用户受众扩大。
- 新仓库 #13 已完成运行时装载治理门（唯一装载 Seam 的个人三轴/平台受众与签名门、冻结
  三轴可选谓词、30s 只读投影运行期监督、legacy 平台包旧路径放行、验证重放前置门；两轮
  双轴审查修复 A1-A5/B1-B6、8088 验收：历史包真实物化、422/404/409 拒绝场景与取消零
  残留）；deprecated 只允许历史冻结任务恢复装载，不进入新任务选择；该结论不代表平台已
  发布或普通用户受众扩大。
- 新仓库 #14 已完成生命周期治理命令（弃用/撤销/隔离/恢复/限期风险接受/回滚 + 受众变更
  路由；四类治理事件与 validator、投影逐轴折叠 + 风险接受惰性到期 + 推荐指针置顶、
  发布/受众/恢复三门补 7 天漏洞库时效复查、风险接受硬约束、零 DDL；两轮双轴审查复核
  「无新问题」、8088 验收 9 命令演练全部符合预期）；风险接受仅平台 admin_gray 范围、
  默认 30 天最长 90 天且不能自动续期；真实 risk_accept applied 链已在 #15 阶段 6 真实
  执行（惰性到期零新事件），自动隔离触发接线已在 #15/#16 篡改演示真实触发（actor=system）；
  该结论不代表平台已发布或普通用户受众扩大。
- 新仓库 #15 已完成 Python 表格 Tool 真实治理纵切面（个人 draft → 验证五步 → 供应链 →
  晋级 verified → 脱敏快照 → Cosign 签名 → 六步 → admin_gray 发布 → 真实装载 → 治理
  动作链（回滚/deprecated/revoked/跨用户拒绝/篡改→自动隔离→restore→risk_accept applied
  链→惰性到期→手动重扫）→ 零残留；PR #30 合并、Issue CLOSED）。
- 新仓库 #16 已完成 Everything MCP 真实治理纵切面（协议型能力三类验证、单 Sidecar 双能力
  装载、并行版本 2026.8.19 治理链（发布→弃用→撤销牺牲）、协议纵深（超时/取消/进程异常
  fail-closed）、零残留；PR #33 合并、Issue CLOSED）。
- 新仓库 #17 已完成 AC-06 兼容切换与 AC-07 综合验收门（AC-06 过渡白名单退役、生产库副本
  迁移演练（备份/前向/重放/零改写/恢复）、完整回归 419 passed、浏览器验收 16 项全过、
  AC1-AC7 对照 ✅；PR #34 合并、Issue CLOSED）。AC-07 主线 #9-#17 全部完成并关闭。
- AC-06 两项历史 `admin_gray_only` 兼容包只是过渡例外，不扩大普通用户权限。
- G1 独立泛化、G2 PG-05、G3 默认切换和 G4 外部 Provider 安全端到端已有正式证据；这些结论
  不等于目标 Linux/GPU 服务器部署验收、远程 MCP/Registry、平台能力普通用户开放或稳定
  生产 Release，后四项仍未完成。

## 8. 文档职责

- `README.md`：产品、启动和最短验收。
- `AGENTS.md`：工程规则与稳定边界。
- `CONTEXT.md`：领域词汇，不维护滚动进度。
- `handoff.md`：当前工作和接手步骤。
- `docs/status/current.md`：唯一滚动状态台账。
- `docs/adr/`：不可变决策记录。
- `docs/plans/`：规格、任务拆分和执行证据；完成后不再充当当前状态。

架构、约定或产品边界发生变化时，修改对应的唯一权威文档，禁止把同一状态复制到多个文件。

### 顶层 Phase 公共仓库同步门

P0、P1 等顶层 Phase 完成时，必须检查并按实际变化同步以下 GitHub 公共仓库入口：

- `README.md`；
- Code of Conduct；
- Contributing；
- MIT License；
- Security；
- GitHub About。

稳定法律、治理或安全文件没有语义变化时，记录“已检查、无需变化”，不得为了制造差异而改写。
该同步门不适用于每张 CV 工单。提交、推送和 GitHub About 等远端修改仍遵守第 4 节授权边界，
不能从 Phase 工程完成自动推断外部发布权限。

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

### 默认技能集（Matt Pocock skills）

本仓库的工程工作默认使用 Matt Pocock 技能集（`mattpocock/skills` 仓库）。装有该技能集
的 Agent 默认按以下路由工作；技能文件的本机位置不入库，按本机技能系统定位（例如
`npx skills ls -g`）：

- 打磨想法：有代码库走 `grill-with-docs`（驱动 `grilling` + `domain-modeling`，即时
  更新 `CONTEXT.md` 与 ADR）；无代码库走 `grill-me`。
- 多会话构建：`to-spec` 产出规格并发布到 GitHub Issues，`to-tickets` 拆成带阻塞边的
  垂直切片工单，再逐单 `implement`（工单之间清空上下文）；单会话则直接 `implement`。
- 实现：`implement` 内以 `tdd` 红绿循环在约定接缝处写测试，收尾 `code-review` 双轴
  审查（Standards + Spec）后再提交。
- 外部工单：`triage` 五角色分诊（标签映射见上）；`to-tickets` 产出的工单不再分诊。
- 硬 Bug：`diagnosing-bugs`，先建立能变红的紧反馈回路，再谈假设。
- 大型模糊工作：`wayfinder` 决策票地图，清空后并入 `to-spec` 主流程。
- 架构维护：`improve-codebase-architecture` 找深化机会，设计用 `codebase-design`
  深模块词汇。
- 跨会话与调研：`handoff` 写交接文档；后台调研用 `research`；路由不确定时 `ask-matt`。

以上技能与本文件既有规则冲突时，以本文件为准。技能所需的仓库配置已由上文三个小节和
`docs/agents/` 定义，无需重复。
