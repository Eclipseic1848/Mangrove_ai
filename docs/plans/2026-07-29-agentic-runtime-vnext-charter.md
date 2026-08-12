# Mangrove Agentic Runtime vNext 专项 Charter

> 日期：2026-07-29
>
> 状态：阶段 1 赛马已完成；用户已批准 Pi 全能力生产灰度并授权开工
>
> 当前基线分支：`v0.0.6`；无同名标签
>
> 专项历史分支：`feature/agentic-runtime-vnext`
>
> 当前继续开发分支：`v0.0.7`；无同名标签、未封板
>
> 决策：[ADR-0017](../adr/0017-agentic-runtime-vnext.md)

## 1. 专项结论

Mangrove 当前最严重的问题不是 LangGraph 版本，而是工作台把“单次前置计划”和
`TaskFamily` 独占路由当成了通用智能执行器。真实 PDF 附件中的表格转 CSV 因此进入
结构性死路；补一条 Prompt、关键词或业务分支无法根治。

本专项建立双轨 vNext：保留已验证的领域资产，通过稳定 Interface 接入来源驱动的动态
Agent Runtime。严格生产门通过前，Legacy 保持默认入口。每一阶段交付产物、差异、验证
证据和未决问题后必须等待用户确认，不自动进入下一阶段。

2026-07-29 用户进一步确认以真实可用为首要目标：完整 `pi-coding-agent` 通过任务级
Docker/RPC 进入生产灰度实现，容器内开放文件、Shell、代码、Skills 和成熟开源工具；
网络按阶段治理，挂载来源的业务阶段只允许固定本地模型。Mangrove 继续控制所有权、
权限提升、验证和正式交付。

## 2. 问题证据

### 已验证事实

- 真实任务要求从附件 2“服务费用标准及明细”只输出一张 CSV；
- 现有系统错误寻找 `content` 字段，并显示没有真实操作选项的确认弹窗；
- 当前工作台在检查真实文件前编译语义计划；
- `TaskFamily` 二选一决定整个执行能力；
- 文档执行器拒绝表格任务，表格执行器拒绝文档来源；
- 批次 8A 已通过用户验收；该结论不等于所有未知场景均已覆盖；
- 服务器、干净镜像、并发容量和最终实机验收已由用户明确后置。

### 基于代码的判断

- `TaskFamily + 单次前置计划 + 固定能力路由` 必须退出 vNext 核心决策链；
- Source Inspector、EvidenceRef、DuckDB、Verifier 和 Delivery 等现有能力可以转成工具；
- Legacy 内既有 LangGraph 保持原样；新的 Pi Runtime 不采用 LangGraph fallback；
- PDF 表格识别质量必须由成熟工具 A/B 决定，不能靠任务专属解析规则补丁。

### 阶段 1 已验证事实

- Deep Agents/LangChain、OpenCode 和 Pi 都能使用本地 Qwen 调用统一领域工具；
- Pi、OpenCode、Deep Agents 在 18 次冻结用例中分别通过 16、16、12 次；
- 三者均未满足核心 P0 连续三次全过，没有候选直接获得生产资格；
- 三者均通过统一 Supervisor 的运行中取消，共同 Docker 沙箱基础边界通过；
- Pi 与 OpenCode 的已执行正确率相同，Pi 的中位耗时和嵌入规模更低；
- 用户已选择 Pi 进入完整能力生产资格实现，OpenCode 保留后备。

### 尚未验证的建议

- 完整 Pi RPC/Docker 灰度能否通过长上下文、截断、进程重启和幂等恢复门；
- Docling、GMFT、现有 pdfplumber/MinerU/Paddle 在用户附件上的最佳组合；
- 30 项保留泛化集、生产用户隔离和正式 Delivery 的完整结果。上述项目只允许在后续
  阶段形成结论。

## 3. 目标与非目标

### 3.1 目标

- 用户目标不再被提前压缩成单一任务类别；
- Agent 先观察真实来源，再规划、调用工具、观察结果并修正；
- 文档、PDF、Excel/CSV 和混合来源可通过基础能力组合完成；
- 本地 Qwen 具备稳定工具调用、上下文压缩和截断恢复；
- 每个动作受权限、网络、副作用、预算、幂等和资源策略约束；
- 候选产物经独立验证后才能形成正式交付；
- 旧工作台 Interface、用户所有权和历史任务继续可用；
- 用户能看到精简、真实、可取消的行动摘要，不暴露隐藏思维链。

### 3.2 非目标

- 本专项首期不迁移采集分析 Conductor；
- 不在阶段 0 编写 Runtime、数据库迁移、前端或沙箱代码；
- 不为当前 PDF 用例增加点对点 Prompt、TaskFamily 或业务 if/else；
- 不自动使用外部模型，不放宽外发确认；
- 不执行最终服务器部署、生产并发容量或 Linux/GPU 实机验收；
- 不创建新版本、标签或外部发布；
- 不删除 Legacy，不迁移旧任务，不顺手重构无关代码。

## 4. 目标架构

```text
用户请求 + 来源文件
        ↓
不可变 GoalContract
        ↓
Agentic Runtime
Observe → Plan → Act → Observe → Verify → Replan
        ↓                ↑
Tool Catalog ─ Policy Gate ─ Agent Workspace
        ↓
Candidate Artifact
        ↓
独立 Verifier
        ↓
Delivery Publisher
```

### 4.1 Module 与 Interface

- `GoalContract` Module：保存不可变用户目标和验收约束，提供创建 revision 与读取
  Interface，不包含工具路线。
- `AgentKernel` Interface：统一 `start / resume / steer / cancel`；每个候选通过
  Adapter 接入，形成框架替换 Seam。
- `ToolCatalog` Module：登记工具输入输出 Schema、证据、网络、副作用、审批、幂等、
  重试安全、资源上限和来源适用性。
- `AgentWorkspace` Module：按用户、任务和 revision 隔离原始只读输入、临时步骤、
  Observation 与 Candidate Artifact。
- `PolicyGate` Module：把技术重试与需要用户确认的业务决策分开。
- `ContextManager` Module：保持 Goal 和证据的完整性，只压缩模型工作上下文。
- `Verifier` 与 `Delivery Publisher` Module：继续独立于 Agent，形成防止假成功的
  高 Leverage Seam。

该设计把框架变化集中在 `AgentKernel` Adapter，保持业务规则的 Locality；领域工具对
Agent 隐藏文件、证据和交付内部复杂度，从而增加 Module 的 Depth。

### 4.2 保留与退出

保留：

- 认证、用户隔离、上传、原文件预览和不可变来源；
- Source Inspector、解析器、EvidenceRef 和 lineage；
- DuckDB、确定性清洗与格式 Renderer；
- Verifier、Delivery、Manifest、QA、版本和回收站；
- 现有工作台公共 HTTP/SSE Interface。

退出 vNext 核心链：

- 用单一 `TaskFamily` 决定全部工具；
- 在观察真实来源前锁定完整执行链；
- 用场景关键词或专属 Prompt 解决未知业务问法；
- 模型或临时脚本直接发布正式文件；
- 无操作选项、只能关闭的确认弹窗。

## 5. 治理边界

可自动执行：

- 不改变用户目标的技术重试；
- 在已授权范围内重新读取、缩小读取、换解析器、重建临时索引；
- 运行无副作用、可重试、已登记且在预算内的工具；
- 验证失败后的同目标重规划。

必须用户确认：

- 扩大来源、页码、文件或时间范围；
- 改变字段、聚合、结果含义、文件数量或输出格式；
- 向外部服务发送任何业务内容；
- 删除、业务写入、不可逆动作或权限提升；
- 从本地模型切换到外部模型。

## 6. 实施阶段与出口

| 阶段 | 产物 | 出口 |
|---|---|---|
| 0 建档 | ADR、调研、Charter、评测规格、任务图、验收说明、分支、Issue | 文档一致，零 Runtime 代码，用户确认 |
| 1 赛马 | 三条可抛弃 PoC、同语料结果、评分卡 | 已完成；无候选直接获得生产资格 |
| 1B Pi 灰度纵切面 | 完整 Pi RPC、任务容器、权限档位、真实候选 | 用户已授权；真实任务能够执行和取消 |
| 2 控制面 | 领域契约、持久记录、兼容 Seam、管理员灰度 | Legacy 默认不变，生命周期门通过 |
| 3 工具与环境 | 完整容器工具、PDF 工具 A/B、成熟开源能力 | 安全/幂等/取消/证据门通过 |
| 4 动态 Loop 与前端 | 动态重规划、Context、事件与交互 | PC 深浅主题和真实用户流程通过 |
| 5 影子与切换 | 离线/影子/管理员灰度/显式试用/切换报告 | 严格门通过且用户明确批准切换 |

阶段 5 后，数据工作台稳定并经用户验收，才另行制定 Conductor 迁移方案。服务器部署与
最终实机验收继续作为工程最后阶段。

## 7. 阶段 0 交付清单

- [x] 建立 ADR-0017 并说明部分取代关系；
- [x] 建立专项 Charter；
- [x] 建立评测语料与三路线赛马规格；
- [x] 建立阶段任务拆分；
- [x] 建立面向用户的阶段 0 验收说明；
- [x] 完成官方源调研报告；
- [x] 同步 handoff、AGENTS、CONTEXT、总计划、延期台账和 8A 后记；
- [x] 通过 UTF-8、Markdown 相对链接、`git diff --check` 和代码零改动检查；
- [x] 白名单提交并推送 `platform/v0.0.6`；
- [x] 从该文档提交建立并推送 `feature/agentic-runtime-vnext`；
- [x] 建立 GitHub 总 Issue 和子 Issue。

这些复选框只能按真实证据更新。阶段 0 完成不表示阶段 1 已获开工授权。

## 8. 阶段 0 远端证据

- 主体文档提交：`5d4f8546`；
- 基线分支：`platform/v0.0.6`；
- 专项分支：`platform/feature/agentic-runtime-vnext`；
- GitHub 总任务：[#2](https://github.com/Eclipseic1848/Mangrove_platform/issues/2)；
- 子任务：[#3](https://github.com/Eclipseic1848/Mangrove_platform/issues/3) 至
  [#11](https://github.com/Eclipseic1848/Mangrove_platform/issues/11)。

总 Issue 和每个子 Issue 均注明阶段依赖、未开工状态和人工控制点。下一步只有用户明确
确认阶段 0 通过后，才允许开始 #3 三路线赛马。

## 9. 阶段 1 结果

主体提交：`beeafd05`。

- [x] 建立三条可抛弃 Adapter 和统一运行入口；
- [x] 冻结 GoalContract、来源观察、Tool Bridge 和独立 Verifier；
- [x] 使用本地 Qwen 对已执行 P0 小集连续运行三次；
- [x] 验证统一取消和任务级 Docker 沙箱基础边界；
- [x] 记录 Adapter 规模、依赖差异、耗时和失败分类；
- [x] 按硬门判定三候选均未胜出；
- [x] 建立执行报告、结构化证据摘要和用户验收说明；
- [x] 用户确认 Pi 全能力生产灰度路线；
- [x] Pi 灰度纵切面获得显式开工授权。

阶段 1 因三个候选已经重复违反核心 P0 硬门而提前结束候选扩展测试。真实 PDF/OCR、
复合来源、长上下文/截断、重启幂等、30 项泛化集和生产所有权未被当前 PoC 验证，
不得用本阶段结果冒充阶段 5 严格门。

后续实施以
[Pi 全能力生产灰度计划](2026-07-29-agentic-runtime-vnext-pi-full-capability-gray-plan.md)
为权威入口。原 LangChain/LangGraph fallback 不再执行。

## 10. Pi 灰度与 PG-05 当前状态

PG-02 至 PG-04 的候选链已实现；PG-05 又增加独立来源/语义 Verifier、同会话
Verify→Replan、Pi 官方上下文门和通用候选清单 CLI。用户原始 PDF 附件表格核心回放
连续 3/3、上下文门回归 1/1；真实 Word 六类商务条款候选重放验证通过；真实 19
工作表 Excel 完整任务 1/1、精确 17 行。随后 Pi 官方 JSONL 会话真实恢复 1/1，
一项未知 TXT 提示注入任务 1/1，HTTP 幂等和跨用户取消/下载隔离已通过。当前后端
相关用例 23 passed、前端完整 Playwright 39 passed。随后运行中真实容器取消、
Egress 独立组合门和 `PiRuntime.start/resume/cancel` 业务主链接入也已通过；
主链真实 CSV 候选和独立 Verifier 回放成功。

这只证明 PG-05 已取得阶段性工程证据。正式 Delivery、Word/Excel 连续 3/3、
独立依赖获取状态机、更多未知任务和 30 项泛化集仍未完成；不得将
`candidate_ready` 或候选验证通过表述为生产资格或用户验收通过。证据见
[PG-05 独立验证纵切面报告](2026-07-29-agentic-runtime-vnext-pg05-verifier-slice-report.md)
和
[PG-05 恢复与安全纵切面报告](2026-07-29-agentic-runtime-vnext-pg05-recovery-security-slice-report.md)。
业务 Egress 证据见
[PG-05 真实取消与 Egress 纵切面报告](2026-07-29-agentic-runtime-vnext-pg05-live-cancel-egress-slice-report.md)。

证据：

- [阶段 1 执行报告](2026-07-29-agentic-runtime-vnext-stage1-execution-report.md)
- [阶段 1 用户验收说明](2026-07-29-agentic-runtime-vnext-stage1-user-acceptance.md)
- [结构化证据摘要](../../evals/agentic-runtime-vnext/stage1-evidence-summary.json)
