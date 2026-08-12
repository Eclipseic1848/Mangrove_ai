# ADR-0017：数据工作台引入来源驱动的 Agentic Runtime

- 状态：已采纳；阶段 1 无候选直接通过生产硬门，用户已批准 Pi 全能力生产灰度路线
- 日期：2026-07-29
- 当前实施分支：`v0.0.7`；无同名标签、未封板
- 决策来源：Agentic Runtime vNext 专项整改计划
- 部分取代：[ADR-0003](0003-llm-boundary.md)、[ADR-0012](0012-semantic-task-plan-and-bounded-tool-loop.md)

## 背景

现有 Phase 4B Harness 在读取真实来源之前编译完整语义计划，并用单一 `TaskFamily`
选择文档或表格执行链。该设计可以稳定处理已经覆盖的单类来源任务，但无法组合处理
“PDF 文档中的表格抽取为 CSV”等跨模态需求：文档执行器拒绝表格目标，表格执行器又
拒绝文档来源。

2026-07-29 的真实任务要求只抽取附件 2“服务费用标准及明细”并输出一张 CSV。系统没有
完成目标，反而因寻找不存在的 `content` 字段进入没有可执行选项的确认弹窗。继续增加
任务分类、问法 Prompt 或场景分支只能延后同类故障，不能解决未知需求的工具组合问题。

问题在于 Mangrove 缺少来源驱动、可观察结果并动态修正的 Agent Harness，而不是只缺
一个新的任务分类或 Prompt。阶段 1 后续决策进一步确认：vNext 不按原 fallback 扩张
LangChain/LangGraph，而是优先利用 Pi 已验证的动态能力完成生产灰度。

## 决策

### 1. 冻结不可变目标，不冻结单一路径

新增 `GoalContract`，只保存用户明确目标、来源范围、必须包含、明确不要、结果形态、
输出格式、权限和验收条件。它不提前锁定“文档任务”或“表格任务”，也不包含工具路线。

Agent 可以在 `GoalContract` 内更新执行草案；任何扩大来源、改变字段或结果含义、外发、
删除、业务写入或提升权限的动作都必须通过 `PolicyGate` 询问用户。

### 2. 建立来源驱动的动态循环

vNext 的核心顺序为：

```text
Observe → Plan → Act → Observe → Verify → Replan
```

Agent 必须先观察真实来源，再选择已登记工具。空结果、错误表、工具失败和验证失败必须
成为新的 Observation，并驱动换工具、缩小读取、重新解析或重规划。循环受工具轮数、
Token、时间、沙箱次数、候选产物数和重复失败指纹共同限制。

### 3. 用稳定 Interface 隔离框架选择

业务层只依赖 `AgentKernel` Interface：

```text
start / resume / steer / cancel
```

Deep Agents/LangChain、OpenCode 和 Pi 通过 Adapter 接入同一 Interface。阶段 1 以
同一 `GoalContract`、Tool Catalog、Docker 沙箱和评测语料赛马。

阶段 1 的硬门只决定候选能否不加改造直接进入生产，不再作为“必须放弃该框架”的淘汰门。
Pi 与 OpenCode 都达到 16/18，证明动态工具循环有效；当前选择完整 `pi-coding-agent`
通过 RPC 进入生产资格实现，OpenCode 保留为后备 Adapter。vNext 不采用原先
LangChain/LangGraph fallback；Legacy 内既有 LangGraph 在双轨期间原样保留，不扩张到
新的 Pi Runtime。

### 4. 保留领域资产，退出独占路由

现有认证、用户隔离、上传解析、Source Inspector、EvidenceRef、DuckDB、Verifier、
Delivery、不可变版本和回收站继续作为领域 Module，通过 Adapter 暴露为可组合工具。

旧 `TaskFamily + 单次前置计划 + 固定执行器` 不再进入 vNext 核心决策链，也不得继续
扩张新的场景分支。Legacy 只接受安全、数据正确性和回归修复。

### 5. 在任务环境开放完整行动能力

Pi 可以在任务级 Docker 环境使用 `read/write/edit/bash`、Python、Node、Git、npm、
PyPI、apt、Skills、扩展和成熟开源工具。输入原件只读、工作目录和候选目录独立；标准
增强模式允许公共依赖下载和访问当前配置的本地 Qwen/LAN 解析服务。

默认不挂载宿主 `.env`、应用数据库、Cookie、其他用户目录和 Docker Socket。额外目录、
私有源凭证、扩大网络范围或宿主机执行由管理员逐任务显式提升并记录。该限制保护影响
范围，不用于削弱 Pi 完成任务所需的功能。

### 6. 候选产物不能直接成为正式交付

模型、工具和临时脚本只能生成 `CandidateArtifact`。独立 Verifier 必须检查目标覆盖、
来源证据、格式、文件数量、必须包含和明确不要；只有通过验证的候选结果才能由
Delivery Publisher 原子发布。

正式发布能力不向 Agent 开放。取消、验证失败、格式重开失败、完整性失败或所有权失败
都必须保持零正式交付。

### 7. Context 与 Skill 受控演进

完整 Goal、工具参数、事件和证据永久保留；ContextManager 只压缩模型工作上下文。
大型工具输出落盘，模型只接收摘要和可继续读取的引用。

Skill 只描述方法和工具选择经验，不能增加权限或补足缺失工具。成功轨迹生成的 Skill
只能进入草稿；必须通过回放评测、污染检查、权限检查和管理员批准后才能启用。

### 8. 双轨灰度，不原地替换

保留现有 `/api/semantic-workspace/tasks`、详情、SSE、取消、版本、预览和下载 Interface。
vNext 新增字段和事件必须保持旧前端可忽略。Legacy 继续默认，vNext 先仅对管理员灰度；
严格生产门通过前不得切换默认入口。

旧任务不迁移。Legacy 与 vNext 使用独立 Run、工作区、事件和候选产物；任一 P0 回归
可切回 Legacy，且不得影响旧正式交付。

## 后果

### 正面

- 未知需求可以通过来源观察和基础工具组合完成，不再要求每种问法建立专属 Prompt；
- 文档、PDF、Excel/CSV 和混合来源可以共享一个目标与验证协议；
- 框架赛马局限在 Adapter Seam，业务资产和公共 Interface 不随候选切换；
- 临时代码带来组合能力，但仍受任务隔离、权限、预算和审计约束；
- Verifier 与 Delivery 保持独立，模型自报成功不能变成正式下载。

### 代价

- 需要新增 Agent Run、Step、Tool Call、Observation、Context Snapshot 和 Skill Draft
  的持久化记录；
- 工具必须补齐 Schema、副作用、网络、审批、幂等、重试安全和资源声明；
- 本地 Qwen 的工具调用、长上下文、截断恢复和三种 Kernel 的恢复语义必须实测；
- 双轨期间会同时维护 Legacy 和 vNext，但只允许在明确 Seam 上复用能力。

### 不变边界

- 原始制品不可变、证据约束、用户所有权、独立验证和正式交付门保持不变；
- 不自动切换外部模型；外发仍需说明服务、内容、目的和风险并取得确认；
- 当前不做最终服务器部署、生产容量结论、版本标签或外部发布；
- 许可证不是筛选项，但安全、恢复、版本稳定性和供应链可信度仍是一票否决依据。

## 被部分取代的旧决策

- ADR-0003 的“LLM 不直接改写事实、不能绕过质量门”继续有效；其中“复杂语义修复必须先
  固化为确定性 Recipe”不再覆盖 vNext 的任务级受控临时代码和动态执行草案。
- ADR-0012 的强类型工具、证据、验证、预算和用户确认继续有效；其中“单一前置 STP
  决定固定执行链”以及“禁止任何临时 Python/Shell/SQL”不再适用于 vNext。
- Legacy 仍按旧决策运行，直到 vNext 通过严格门并经用户确认切换。

## 验证与阶段门

阶段 1 必须用三条候选路线运行相同语料，本地 Qwen 为强制模型。核心 P0 连续三次全部
通过，扩展泛化集至少 30 个且正式交付正确率不低于 90%；安全、权限、隔离、禁止项和
失败不得冒充成功必须 100%。任何一票否决项都淘汰候选。

阶段 0 只建立文档、ADR、分支和 Issue；不得据此宣称 Runtime 已实现或框架已经胜出。

## 阶段 1 验证后记

2026-07-29 使用同一 `GoalContract`、本地 `Qwen3.6-35B-A3B`、四项领域工具和独立
Verifier 完成三路线可抛弃 PoC。Pi、OpenCode、Deep Agents 在 18 次冻结运行中分别
通过 16、16、12 次，均未满足核心 P0 连续三次全过：

- Pi 的模糊目标暂停只通过 1/3；
- OpenCode 的 Word 证据和模糊目标暂停各有一次失败；
- Deep Agents 的 PDF→CSV 和模糊目标暂停均为 0/3。

三个候选都通过统一 Supervisor 的运行中取消，共同 Docker 沙箱基础边界通过；但这些
共同控制能力来自 Mangrove 原型外壳，不构成某个候选胜出证据。真实解析器、长上下文、
进程重启、幂等恢复、30 项泛化集和生产用户隔离没有在本阶段完成验证。

因此阶段 1 当时正确判定“无候选可不加改造直接进入生产”，但把生产准入门进一步解释为
框架淘汰门并机械触发 LangChain/LangGraph fallback，不能充分利用 Pi 已验证的 15/15
非歧义能力。该 fallback 建议现由本后记后续决策取代。

## 2026-07-29 后续决策：Pi 全能力生产灰度

用户明确要求先让系统能够完成真实任务，再根据生产使用证据迭代。当前决定：

- 以完整 `pi-coding-agent` RPC 作为首个生产灰度 Adapter；
- 在任务级 Docker 环境开放完整文件、Shell、代码、依赖安装和公共网络能力；
- Mangrove 保持任务所有权、权限提升、事件、取消、候选验证和正式交付；
- OpenCode 保留为后备，不首期并行建设；
- Legacy 保持默认，Pi 仅管理员显式灰度，未通过真实门不得宣称生产资格；
- 原先 LangChain/LangGraph fallback 不再执行。

详细实施范围和权限档位见
[Pi 全能力生产灰度计划](../plans/2026-07-29-agentic-runtime-vnext-pi-full-capability-gray-plan.md)。

## 2026-07-29 实施状态：PG-05 独立验证、恢复与安全纵切面

完整 Pi 0.80.10 JSONL RPC 已通过任务级 Docker 接入现有工作台，Legacy 仍为默认。
管理员可逐任务显式选择 Pi；输入只读、Run/Event 按所有者和 revision 隔离，候选必须
通过文件完整性门并以 `candidate_ready` 展示，不创建正式 Delivery。Mangrove 已在
Pi 之外重新打开 PDF/DOCX/XLSX 原件和候选，验证候选集合、来源逐字证据和目标语义；
失败原因会在同一 RPC 会话触发最多三次有界重规划。大工具输出治理复用 Pi 官方
Extension `tool_result` 钩子，不重写 Agent Loop。

用户原始 PDF 附件表格核心回放连续 3/3、上下文门回归 1/1；真实 Word 六类商务
条款候选重放验证通过；真实 19 工作表 Excel 完整任务 1/1、精确 17 行。Pi 官方
JSONL 会话恢复、HTTP 幂等、跨用户取消/下载隔离和一项真实 TXT 提示注入任务也已
通过。这次实现没有改变本 ADR 的生产资格门：正式 Delivery、运行中真实容器取消、
强制网络外发策略、30 项泛化集和 PG-05 整体当时尚未完成。执行证据见
[PG-05 独立验证纵切面报告](../plans/2026-07-29-agentic-runtime-vnext-pg05-verifier-slice-report.md)
与
[PG-05 恢复与安全纵切面报告](../plans/2026-07-29-agentic-runtime-vnext-pg05-recovery-security-slice-report.md)。

后续又完成运行中真实容器取消，以及 Smokescreen Egress 的独立组合门和业务主链接入。
`PiRuntime.start/resume` 在挂载来源时强制进入任务级 internal 网络，只经代理访问固定
本地模型；`cancel` 返回前撤销代理和网络。真实 Pi + 本地 Qwen + CSV + Verifier 回放
通过。公共依赖只允许在不挂载来源的独立阶段；该依赖获取状态机尚未实现，因此完整
PG-05 和生产资格门保持未完成。证据见
[PG-05 真实取消与 Egress 纵切面报告](../plans/2026-07-29-agentic-runtime-vnext-pg05-live-cancel-egress-slice-report.md)。

## 2026-07-30 统一任务域补充

[ADR-0018](0018-unified-task-domain-contract.md) 取代本 ADR 中尚未统一的跨来源、跨模态身份
表达，并明确 `TaskFamily` 只作为 Legacy 兼容字段。vNext 继续采用来源驱动执行，但必须
通过统一 GoalContract、CandidateVerification 和 DeliveryPublishing 接口；Agent 与沙箱
均不拥有正式发布权。

## 相关

- [vNext 专项 Charter](../plans/2026-07-29-agentic-runtime-vnext-charter.md)
- [评测语料与赛马规格](../plans/2026-07-29-agentic-runtime-vnext-evaluation-spec.md)
- [任务拆分](../plans/2026-07-29-agentic-runtime-vnext-task-breakdown.md)
- [用户验收说明](../plans/2026-07-29-agentic-runtime-vnext-stage0-user-acceptance.md)
- [阶段 1 执行报告](../plans/2026-07-29-agentic-runtime-vnext-stage1-execution-report.md)
- [阶段 1 用户验收说明](../plans/2026-07-29-agentic-runtime-vnext-stage1-user-acceptance.md)
- [框架官方源调研](../research/2026-07-29-agentic-runtime-framework-assessment.md)
- [ADR-0018：统一任务域采用正交五轴模型与独立发布权](0018-unified-task-domain-contract.md)
