# Mangrove 零上下文交接

> 最后现场核验：2026-08-13
>
> 当前阶段：公开工程基线已建立；主线回到 AC-07 能力信任与发布治理收口
>
> 当前分支：`codex/ac07-35-review-fixes`
>
> 当前公开远端：`origin` → `https://github.com/Eclipseic1848/Mangrove_ai.git`
>
> 当前公开基线：`origin/main` 为 `ce71188faeec5a63409cef8405972a3d1c5fe1ae`；精确工作分支头以 `git rev-parse HEAD` 为准
>
> 版本状态：`v0.0.8` 的当前能力已纳入首次公开快照；没有创建或移动版本标签

## 0. 一句话结论

本轮“清理工程、建立公开仓库、补齐社区与安全资料”的任务已经完成。AC-07 #35 已在本工作区
完成工程实现、最终双轴复审、带备份生产迁移、两份灰度包真实供应链证据和 8088 用户验收；当前
没有代码级硬阻塞。#35 已提交并推送到 `origin/codex/ac07-35-review-fixes`，PR #6 已创建
且发布门复核通过。PR #6 承载主线交付，实时合并状态必须现场核对 GitHub 与 `origin/main`；
旧仓库工单尚未处理。下一条工程主线是补齐 #34 两项真实能力验证闭环。任何新的工单操作、
签名密钥、能力晋级或权限扩大都必须重新取得用户明确授权。

## 1. 新会话必须先做什么

按以下顺序接手，不要先改代码：

1. 完整阅读 `AGENTS.md`、本文件、`docs/status/current.md` 和 `CONTEXT.md`。
2. 阅读 `docs/agents/`，再阅读当前工单对应的规格、ADR 和执行报告。
3. 现场执行：

   ```powershell
   git status --short --branch --untracked-files=all
   git branch --show-current
   git rev-parse HEAD
   git remote -v
   ```

4. 运行环境需要验证时，检查 `http://127.0.0.1:8088/api/health`；不要把 `5173` 当产品入口。
5. 先判断任务处于需求澄清、规格、拆票、实现、诊断还是审查阶段，再选择流程。
6. 区分已验证事实、基于代码的推断和尚未验证的建议；测试通过不等于用户验收或生产资格。

当前分支包含 #35 的实现、测试、最终审查修复及状态文档提交，并已推送到同名 `origin` 分支。
接手时仍必须现场核对 `git status`、分支、远端与 PR #6；不得根据本文件所在分支推断合并状态。
未授权修改旧 Issue、晋级或发布能力，也不得使用 `git add .` 处理后续混合工作树。

## 2. 我们在做什么

Mangrove 是统一处理在线/离线、公域/私域、结构化/非结构化数据的智能数据任务平台。用户用
自然语言描述目标，平台负责来源获取、任务规划、能力调用、数据处理、证据绑定、独立验证和
正式交付，让懂业务的数据工程师或开发者把精力放在数据应用与价值创造上。

当前工程主线不是继续堆叠采集器，而是完成 Agentic Runtime 与能力治理闭环：

```text
用户目标
  → 不可变 GoalContract / TaskRevision
  → Agent 按目标选择 Tool、MCP、Skill 或 Procedure
  → 任务级隔离运行与可恢复 Loop
  → Candidate + 来源证据
  → 独立 Verifier
  → 正式 Delivery Publisher
  → 能力验证、供应链证据、签名、治理和受众控制
```

当前 AC-07 要解决的是最后一段“能力如何从个人草稿变成可信、可审计、可回滚的平台能力”，而
不是把一次运行成功直接当成能力安全、晋级或发布。

## 3. 已经完成了什么

### 3.1 产品与运行主链

- Conductor 公域采集主链可用，支持理解、规划、采集、清洗、分析和输出。
- `/data-prep` 正式工作台可用，支持不可变 revision、取消、恢复、版本、来源、结果、回收站
  和正式交付。
- CSV、JSONL、Parquet、XLSX、JSON、DOCX、PDF、HTML、Markdown、TXT、PPTX 共 11 种
  交付格式的在线预览已完成工程验证；TSV 仍不是界面正式交付格式。
- vNext Delivery Publisher 已接入：只有独立验证、完整性和 QA 通过的 Candidate 才能发布
  正式 Delivery。
- 覆盖感知文档检索已完成代表任务验证：Agent 按目标发现和精读，Verifier 用覆盖账本、证据、
  对象顺序和停止语义判定完成，避免固定先 OCR 全文。
- 对话转向与上下文编译已实现：状态追问不改变 Run；业务含义变化形成待确认的新 revision。
- 个人/平台多模型连接、同 Provider 多连接、多模型逐项验证、Preset、自定义/LAN、个人 Key
  隔离和 TaskRevision 冻结已工程实现。

### 3.2 Agentic Capability

| 项目 | 已完成事实 | 仍缺 |
|---|---|---|
| AC-04 能力目录 | Pack/Component/Procedure/Validation/Selection、Owner 隔离、digest 冻结和 OCI Layout Adapter 已实现 | 完整用户代表验收按后续票推进 |
| AC-05 隔离能力获取 | 可信来源、Grant、获取网络、离线构建、缓存、预算、取消、恢复和清理门已工程验证 | 生产迁移与用户验收 |
| AC-06 本地 Adapter + Sidecar | Python 表格 Tool、Everything MCP 协议样本、任务级 Capability Host、工作台选择和冻结 revision 已通过用户灰度验收；默认关闭 | 远程 MCP、Registry 发现、普通用户开放 |
| AC-07 #33 | 三轴治理投影与兼容读取已实现、审查、迁移、验收并关闭 | 无 |
| AC-07 #34 | 精确 digest 的可恢复 ValidationRun、Owner/TaskRevision 绑定、真实 Pi 隔离重放、独立 Verifier、幂等/Lease、取消/恢复和失败关闭清理已实现、审查并完成带备份生产迁移 | 两项能力真实灰度闭环与最终用户确认 |
| AC-07 #35 | 固定 Trivy 0.70.0、Syft 1.50.0，最终主体扫描、双格式 SBOM、不可变证据、精确隔离门和脱敏 UI 已实现；最终双轴复审、带备份生产迁移与 8088 用户验收通过；PR #6 承载主线交付且发布门复核通过 | 旧仓库 Issue #35 处理需单独授权 |

三轴治理语义已经冻结：

- 成熟度：`draft | verified`
- 生命周期：`active | deprecated | revoked`
- 运行资格：`eligible | quarantined`

一次成功验证只是一条不可变事实，不会自动改变上述三轴。

### 3.3 工程清理与首次公开发布

- 已删除或归档重复、失效、一次性原型与生成物；测试源码保留。
- 当前状态集中到 `docs/status/current.md`，`plan.md` 和 `mangrove_plan.md` 只保留历史入口。
- 首次公开 `main` 使用干净快照，没有复制旧私有 Git 历史，避免历史中的本机 Agent 配置和
  数据库进入公开仓库。
- 已补齐 `README.md`、MIT `LICENSE`、`CODE_OF_CONDUCT.md`、`CONTRIBUTING.md`、
  `SECURITY.md`、`THIRD_PARTY_NOTICES.md`、Issue/PR 模板。
- GitHub About、Topics、依赖漏洞提醒、自动安全修复和私密漏洞报告已启用。
- MediaCrawler 与 Firecrawl 的本机副本不提交；`scripts/setup_external_dependencies.ps1` 使用
  固定上游提交和仓内补丁重建，第三方许可证边界已记录。
- 当前 `origin/main` 指向 `ce71188f`；未检出的本地 `main` 仍指向 `999ad721`，当前工作分支基于
  `ce71188f`。首次公开快照共 1163 个文件，0 个 Gitlink。
- 旧仓库没有删除：原远端保留为 `legacy-origin` 和 `legacy-platform`；旧 `v0.0.8` 分支也仍在
  本机，禁止擅自重写或删除。

### 3.4 最近验证证据

- 后端全仓：`1249 passed, 4 skipped`；跳过项仅为需显式开启的真实数据库容器和大规模性能门。
- AC-07/Runtime 聚焦：`75 passed`。
- 前端 TypeScript + Vite 生产构建：通过。
- 完整 Playwright 单 worker：`54 passed`。
- Gitleaks 8.30.1 对最终公开快照扫描：`0 leaks`。
- 本机私有启停脚本专项：`3 passed`。
- 最后现场检查：8088 `/api/health` 返回 `200`。
- #35 最终聚焦回归：后端供应链/治理 `92 passed`，设置页角色与治理 Playwright `13 passed`，
  TypeScript 与 Vite 生产构建通过，Standards/Spec 最终双轴复审均为 PASS。
- PR #6 发布门复核：七个 Capability 测试文件 `118 passed`，设置页 Playwright
  `13 passed`，TypeScript 与 Vite 生产构建通过，8088 健康检查返回 `200`，`git diff --check`
  通过；PR 为 `CLEAN`、`MERGEABLE`。公开仓库当前没有 Actions、`main` 分支保护或 Ruleset，
  因此没有远端 CI 结果。
- 固定 digest 的真实 Docker 五步均通过；生产迁移前后一致性与完整性检查通过，8 张既有能力表
  零改写；两条生产供应链证据均为 `passed`，最终容器、网络和临时目录零残留。
- 用户于 2026-08-13 确认 8088 管理员摘要和普通用户隔离验收通过；该验收不代表能力晋级、
  签名、平台发布、普通用户开放或整个 AC-07/Phase 4 完成。

## 4. 当前卡在哪里

### 4.1 没有代码级硬阻塞，主要是人工门禁

当前没有需要靠猜测继续编码的故障。#35 已完成审查、生产迁移、真实运行和用户验收，但仍是
由 PR #6 承载的主线交付；实时合并状态必须现场核对。旧工单处理和后续工单仍必须保持人工
控制。局部闭环不能被表述为能力已经晋级、签名、平台发布，或整个 AC-07/Phase 4 已完成。

### 4.2 #34 缺少两条真实能力闭环证据

#34 已实现并迁移，但历史任务只有“冻结过能力”，不一定在正确 Runtime run segment 中真正成功
调用过目标原生 Tool/MCP。候选条件已经收紧为存在对应的 `tool.completed` 成功事件，因此旧 PDF
任务不再被错误列为可重放证据。

要关闭 #34，需要管理员使用自己拥有的 TaskRevision，分别创建并完成：

1. 一项真实调用 `gray-python-table@1.0.0` 的任务；
2. 一项真实调用 `gray-everything-mcp@2026.7.4` 的协议验证任务；
3. 再从设置页发起能力验证，检查进度、结果、取消、恢复和资源零残留。

特别注意：当前 `@modelcontextprotocol/server-everything@2026.7.4` 是 MCP 协议测试服务器，不是
Voidtools Everything 本地文件搜索工具。若要建设真实本地文件搜索能力，必须单独调研、冻结新的
包和 digest、重新走验证，不得静默替换。

### 4.3 #35 由 PR #6 承载主线交付

#35 已完成：

1. Standards/Spec 最终双轴复审均为 PASS；
2. 生产库在 SQLite 在线一致性备份后完成幂等 `0003_supply_chain_evidence.sql` 迁移，旧能力表
   零改写；
3. `gray-python-table@1.0.0` 与 `gray-everything-mcp@2026.7.4` 的生产供应链证据均为
   `passed`；
4. 用户确认 8088 管理员摘要、普通用户隔离和页面无异常验收通过；
5. 面向 `main` 的 PR #6 已创建，发布门现场复核通过。

PR #6 的实时合并状态以 GitHub 和 `origin/main` 为准；旧仓库 Issue #35 尚未处理。本票没有
晋级能力、生成 Cosign 密钥、发布平台快照或扩大普通用户受众。

### 4.4 工单跟踪已经分成两个仓库

- 代码的新权威仓库是公开的 `Eclipseic1848/Mangrove_ai`。
- AC-07 #32～#44 仍存在旧的 `Eclipseic1848/Mangrove_platform`，由本机
  `legacy-platform` 远端对应；#33 已关闭，#34～#44 仍开放。
- 新公开仓库当前没有迁移这些 Issue。文档里的 `#34`、`#35` 等编号都是旧仓库编号，不能直接
  假设为新仓库 Issue。
- 是否把工单迁移到公开仓库属于外部发布和项目管理决策，必须先问用户；不应因此阻塞 #35 审查。

## 5. 下一步计划

### 下一人工控制点：确认 #35 主线落地并决定旧工单处理

1. PR #6 完成主线落地后，核对 `origin/main` 包含合并结果且 PR 状态为 merged。
2. 旧仓库 `Eclipseic1848/Mangrove_platform` 的 Issue #35 是否更新或关闭仍是独立外部动作，
   必须单独授权；不得把旧编号静默解释为公开仓库工单。

### 随后：补齐 #34 真实灰度闭环

在不改写历史任务的前提下，先创建确实调用两项目标能力的新任务，再完成验证运行、取消、恢复、
证据和清理验收。#34 与 #35 都形成可信事实后，才具备进入 #37 自动晋级的前提。

### 所有必须由用户确认的控制点

- 是否迁移旧 Issue 到公开仓库；
- 生产数据库迁移与恢复；
- 创建真实业务任务或再次调用外部模型并消耗 Token；
- 下载新工具、改变固定版本、生成或使用 Cosign 私钥；
- 能力晋级、平台快照、平台发布、普通用户受众开放；
- Commit、Push、PR、Release、分支、标签；
- Secret、数据外发、跨 Owner 正文读取和任何不可逆删除。

## 6. AC-07 详细路线与依赖

旧仓库工单只用于当前路线识别；迁移前使用完整仓库名，不要裸写编号执行外部操作。

| 顺序 | 旧工单 | 目标 | 依赖/状态 |
|---:|---|---|---|
| 1 | #33 | 三轴治理投影与兼容读取 | 已完成并关闭 |
| 2 | #34 | 精确 digest 的可恢复 ValidationRun | 工程和迁移完成，待真实灰度闭环 |
| 3 | #35 | Trivy、Syft 供应链证据闭环 | 工程、审查、生产迁移和用户验收完成；PR #6 承载主线交付且发布门复核通过，旧工单尚未处理 |
| 4 | #36 | Cosign 本地 OCI 签名路径 PoC | 未开始；独立验证短期 localhost Registry 路线，不得擅自生成密钥 |
| 5 | #37 | 个人能力自动晋级 `verified` | 依赖 #34、#35 的可信验证和供应链证据 |
| 6 | #38 | 管理员审核与业务内容审计查看 | 需要原因、对象和不可变审计，不能提供无痕正文浏览 |
| 7 | #39 | 独立平台快照、签名与 `admin_gray` 发布 | 依赖 verified 个人版本、#36 签名路线和管理员控制 |
| 8 | #40 | CapabilityMountResolver 运行治理门 | 装载时验证三轴、受众、签名、Owner 和冻结 digest，失败关闭 |
| 9 | #41 | 弃用、回滚、隔离、撤销和限期风险接受 | 不改写历史 TaskRevision；风险接受必须限时、留因、可审计 |
| 10 | #42 | Python 表格 Tool 真实治理纵切面 | 从验证到平台灰度的完整业务闭环 |
| 11 | #43 | Everything MCP 真实治理纵切面 | 当前协议服务器与真实本地搜索能力必须明确区分 |
| 12 | #44 | AC-06 兼容切换与 AC-07 综合验收门 | 只有全部硬门通过后才收口 AC-07 |

## 7. 整体 Roadmap

### R0：统一数据任务与 Agentic Runtime 基础层——大部分已完成

- GoalContract、TaskRevision、Run、来源快照、证据、Candidate、Verifier、Delivery。
- Pi Coding Agent、任务级 Docker、Capability Host、受控外发、覆盖感知检索和可恢复 Loop。
- 正式数据工作台、多模型连接、对话转向、预览和 Publisher。

### R1：AC-07 能力信任与平台发布——当前主线

- #35 已完成本地事实闭环；继续补齐 #34 两项真实能力验证闭环；
- 完成签名 PoC、个人晋级、管理员审核、平台快照和运行装载治理；
- 用 Python Tool 和 MCP 做两条真实纵切面；
- 通过 #44 综合门后才能称 AC-07 完成。

### R2：AC-08 AutomationProcedure——尚未开始

- 把成功的能力调用路径提炼为可版本化 SOP/Procedure；
- 支持组合、选择、失败学习、重新验证和实际发布；
- 严格区分个人 Procedure 与平台通用 Procedure，个人内容只能 Owner 使用；
- 运行中追问继续使用 RawUserTurn、ContextDelta、SemanticDiffGate 和 CompiledContext，不能
  暴露原始思维链。

### R3：AC-09 产品化——尚未开始

- 自动化方案一级入口、个人方案库、平台方案库、审核队列和新手引导；
- 执行进度采用渐进式披露，展示真实 Tool/MCP/Skill/Procedure 名称与用途；
- 普通用户只见安全投影，管理员诊断视图保留技术细节和审计；
- 普通用户开放与默认推荐必须独立授权，不能随平台发布自动发生。

### R4：来源与模态扩展——规划中

- 补齐企业数据库从“连接测试”到受控只读任务执行；
- 建设 API、企业系统、URL、本地路径、对象存储等统一 Source Adapter；
- 多媒体作为独立应用场景推进图像、音频、视频工具链，不把实时流强塞进当前范围；
- 支持复合来源 Join、比较、冲突、补全和证据语义。

### R5：Phase 4 生产硬门——未完成

- 30 项泛化集；
- Word/Excel 连续生产门和完整 PG-05；
- 真实外部 Provider 的 Pi→Relay→Provider 安全端到端；
- Rollout P0 GateSnapshot、影子运行和默认入口切换；
- 数据生命周期、物理删除与审计墓碑；
- 远程 MCP/Secret 和 Registry 发现。

### R6：8B 部署与正式封板——后置

- Linux/Compose、干净镜像、并发、故障注入和目标服务器验证；
- 在目标环境和生产硬门明确前，不继续历史 8B-1a 试验，也不宣称服务器就绪；
- Phase 4 是否封板、创建版本分支或标签必须由用户单独确认。

## 8. 稳定边界

- 产品角色只有普通用户、管理员、超级管理员；管理员和超管属于同一治理类型。
- 管理员可查看跨 Owner 的任务管理元数据；读取个人 Prompt、来源正文、Candidate 或 Delivery
  必须填写原因并生成审计，不能因管理员身份无痕读取。
- AC-06 两项 `admin_gray_only` 包只是迁移兼容例外，不扩大普通用户权限。
- 平台 Provider Preset 让普通用户通常只填 API Key；自定义/LAN 是独立高级入口。个人 Key、
  连接元数据、Purpose Grant 和使用记录必须 Owner 隔离。
- 本地/LAN 模型允许精确地址访问，但公共平台能力不能默认开放任意内网地址；安全治理与功能
  建设分阶段实施，不能用尚未实现的全面 SSRF 加固阻塞核心架构。
- Candidate、`eligible_for_delivery`、内部 AST、Parquet 和“验证通过”均不是正式交付；只有
  `delivery_published` 且通过完整性/QA 的 `output_id` 才能面向用户称为结果。
- “准备能力”是按需阶段，没有真实 Tool/MCP/Skill/Procedure 事件时必须隐藏，不得一直显示
  “尚未开始”。

## 9. 绝对不要再踩的坑

### Git、公开发布与本机文件

- 不要使用 `git add .`、`git add -A`、强推、`git reset --hard` 或 `git clean` 处理混合工作树。
- 不要把 `.env`、`.claude/`、数据库、日志、上传/下载、任务制品、浏览器状态、运行学习库、
  个人偏好、虚拟环境或 `local-audits/` 提交到 Git。
- 不要把本机 `start_all.bat`、`stop_all.bat` 加入公开仓库，也不要为了公开发布改写它们。它们
  包含本机解释器、局域网和服务编排；由 `.gitignore` 排除。
- 不要删除旧远端、旧分支或版本标签；首次公开快照不是授权清理私有历史。
- 新公开仓库和旧工单仓库不同；任何 Issue 创建、迁移、关闭或编号引用前都要核对完整仓库名。
- 不要把第三方源码副本直接塞回仓库；固定来源、版本、补丁和许可证，版本替换必须先获批。

### Agent、检索与交付

- 不要先对整份 PDF 做高质量 OCR 再让 Agent 思考；先理解目标、建立覆盖契约、轻量发现、候选
  精读，再由独立 Verifier 判定停止。
- 不要用固定页数、关键词、TaskFamily 或用例专属 Prompt 替代 Agent 的工具选择和重规划。
- 也不要把一切交给 LLM：权限、Owner、digest、证据、覆盖、预算、停止、发布和清理必须由
  确定性门失败关闭。
- 不要把空或无效的模型结构化输出当成业务不通过；只允许有界重试，并保留技术失败原因。
- 不要让进度阶段乱序或多个阶段同时活动；内部 Harness 原语要映射为用户能理解的业务动作。
- 不要展示原始思维链、Secret、宿主路径、Token、调用参数或网络地址；可展示安全的能力名称、
  类型、版本和用途。

### Runtime、Docker 与清理

- 不要把 `5173` 改成统一产品入口；统一入口始终是 `8088`。
- 不要让任务执行绑定浏览器 SSE 生命周期；刷新或切页不能取消后台任务。
- 不要把 Docker `rm`/`inspect` 的 daemon 或 CLI 错误当成资源已不存在。清理未完成时不得写成功
  终态，必须保留可恢复状态和 Lease 重试。
- 不要使用非确定性网络名、跨 Run 清理或忽略 PID 创建时间；只能清理 Owner/Task/revision/Run
  精确绑定的资源。
- 不要用增加 1800 秒超时掩盖 Sidecar、工具输出、模型结构化响应或恢复机制的问题。

### 能力治理与权限

- 不要把一次成功验证自动升级为 `verified`，也不要把 `verified` 自动发布为平台能力。
- 不要让新 digest 继承旧证据；可以在授权仍有效时重放冻结任务，但必须生成新验证事实。
- 不要把个人能力直接改 scope 变成平台能力；平台发布必须生成脱敏的独立快照、新 digest 和
  独立生命周期。
- 不要把平台发布和普通用户开放合并；平台初始只能 `admin_gray`，受众扩大是独立审计动作。
- 不要生成、提交或把 Cosign 私钥写入数据库、日志、任务或项目目录；#36 需要用户明确授权。
- 不要把 MCP 协议测试服务器宣传为真实 Everything 文件搜索能力。

### 测试与验收

- 不要删除测试源码；测试是长期回归安全网，验证后只清理缓存、日志和一次性生成物。
- 不要用“页面能打开、按钮能点击”代替真实任务、结果、取消、恢复、证据和资源清理验收。
- 不要因并发偶发超时随意放宽业务超时。先单测复现、检查竞争关系，再用完整单 worker 门确认。
- 不要把自动化测试、工程验证、生产迁移、用户验收和正式资格写成同一种“完成”。

## 10. 权威资料索引

- 工程规则：`AGENTS.md`
- 当前状态唯一滚动台账：`docs/status/current.md`
- 统一领域词汇：`CONTEXT.md`
- Agent 协作与 Issue 约定：`docs/agents/`
- ADR 索引：`docs/adr/README.md`
- AC-07 规格：`docs/plans/2026-08-06-agentic-capability-ac07-spec.md`
- AC-07 决策：`docs/adr/0029-capability-validation-lifecycle-and-platform-publication.md`
- #34 报告：`docs/plans/2026-08-07-agentic-capability-ac07-02-execution-report.md`
- #35 报告：`docs/plans/2026-08-07-agentic-capability-ac07-03-execution-report.md`
- vNext Publisher：`docs/plans/2026-08-04-vnext-delivery-publisher-execution-report.md`
- Runtime 可靠性：`docs/plans/2026-08-06-v008-runtime-reliability-and-candidate-retry-closeout.md`

发生状态变化时先更新 `docs/status/current.md`，再同步精简后的本文件。不要把滚动状态复制回
README、CONTEXT、ADR 或历史执行报告。
