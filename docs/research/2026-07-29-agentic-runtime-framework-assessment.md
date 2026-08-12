# Mangrove Agentic Runtime vNext 框架评估与赛马规格

> 日期：2026-07-29
> 状态：阶段 0 调研结论；尚未运行任何候选框架，不能据此宣布获胜者
> 范围：数据工作台 vNext 首期纵切面，不包含 Conductor 迁移、服务器部署或默认入口切换

## 1. 执行摘要

这次整改不应被简化为“把 LangGraph 换成另一个框架”。

当前项目锁定的是 `langchain==1.2.2` 和 `langgraph==1.0.5`，不是因为依赖版本陈旧而失败。LangGraph 官方仍把自己定义为面向长时、有状态 Agent 的低层编排 Runtime，并明确区分 Runtime、Framework 与 Agent Harness。Mangrove 当前真正缺少的是一层能够先观察真实资料、动态选择工具、接收结果、校验并重规划的现代 Harness；同时，旧链路又用单一 `TaskFamily` 和前置计划过早锁死了能力路由。参见项目[依赖锁定](../../requirements.txt)、[LangGraph 官方概览](https://docs.langchain.com/oss/python/langgraph/overview)。

本报告建议让以下三条路线进入同条件、可抛弃的真实 PoC：

1. Deep Agents / LangChain 1.x + LangGraph；
2. OpenCode headless；
3. Pi Agent Core（必要时只取 Pi Coding Agent 的会话压缩与 JSONL RPC 能力）。

纸面上，三者各有明显优势：

- Deep Agents 与 Mangrove 的 Python/LangGraph 资产最接近，预计迁移成本最低；
- OpenCode 的 headless HTTP/SSE、会话、权限响应和结构化输出接口最完整；
- Pi Agent Core 最轻，动态工具循环清晰，最利于 Mangrove 自己掌控边界。

这些都只是待 PoC 验证的工程判断，不能预先当成选型结论。本地 Qwen 的真实工具调用、截断恢复、取消、持久化、隔离和产物正确率必须用同一语料实测。

无论最终选择哪一条路线，它都只能实现 `AgentKernel`，不能替代 Mangrove 的用户所有权、来源证据、独立 Verifier、正式 Delivery、审计留存和业务审批。正式发布能力必须继续留在 Agent 之外。

## 2. 证据口径

本文用三个标签区分证据强度：

- **[事实]**：已经由项目代码、本地候选源码或候选官方文档直接验证。
- **[推断]**：由已验证接口和 Mangrove 当前架构推导出的工程判断，尚未经过真实接入。
- **[待测]**：只有运行统一 PoC 后才能回答，任何纸面分数都不得冒充验证结果。

本机独立 Pi 0.80.10 源码快照没有 `.git` 元数据，无法证明对应的上游提交；只能确认本地 `packages/agent/package.json` 声明版本 `0.80.10`。为保证后续复核，本轮记录：

- `package-lock.json` SHA-256：`C7DAC09E5997160A227430DF2448141AFCB87246BB3E861EE176FAC5E6138677`
- `packages/agent/package.json` SHA-256：`EBF287B8C71604C90CBB272127D5A5682E1CCC9075FC1C4EBEB36B7B50B5CAA7`

进入 PoC 前还必须把来源固定为可复现的 Git 提交或带校验和的归档，不能只依赖目录名。

## 3. Mangrove 保留边界

### 3.1 已验证的现有领域资产

- **[事实]** 现有 Harness 契约包含 `TaskFamily`、语义计划、能力清单和验证报告，见 [models.py](../../src/semantic_harness/models.py)。
- **[事实]** 文档证据使用独立的 `EvidenceRef` 表达来源定位，见 [document_models.py](../../src/data_prep/document_models.py)。
- **[事实]** 正式交付具有独立 `DeliveryOutput` 模型和 `output_id`，见 [delivery/models.py](../../src/semantic_harness/delivery/models.py)。
- **[事实]** 工作台和交付持久层广泛使用 `user_id` 约束所有权，见 [store.py](../../src/api/store.py)。
- **[事实]** 当前图中存在独立的 `delivery_published` 终态，而不是把模型生成内容直接当下载结果，见 [harness_graph.py](../../src/semantic_harness/harness_graph.py)。

### 3.2 为什么候选框架不能替代这些资产

候选框架提供的是 Agent 循环、会话、工具协议、事件或上下文管理。它们不知道 Mangrove 中“这份文件属于谁”“附件 2 指向哪一页”“哪些行支撑最终 CSV”“验证失败时能否发布”“旧版本和回收站保留多久”。因此必须维持以下边界：

```text
AgentKernel Adapter
        │ 只提交工具调用意图
        ▼
Mangrove Tool Bridge
        ▼
PolicyGate ── 用户所有权 / 审批 / 网络 / 资源 / 幂等
        ▼
现有来源、解析、证据、DuckDB 等领域能力
        ▼
CandidateArtifact
        ▼
独立 Verifier
        ▼
Delivery Publisher
```

- **[事实]** Deep Agents 的文件系统允许列表只约束其内建文件工具；通过 `tools=` 注入的自定义工具不受该允许列表影响，而且文件权限规则不约束可执行任意命令的 sandbox backend。官方要求用额外 backend policy hook 处理自定义校验。因此 Mangrove 仍需统一 `PolicyGate`。[Deep Agents 官方概览](https://docs.langchain.com/oss/python/deepagents/overview)
- **[事实]** OpenCode 的权限系统是 `allow / ask / deny` 决策层，官方默认规则中多类操作是允许的；这不是用户隔离数据库，也不是 Docker 的操作系统级边界。[OpenCode 权限文档](https://opencode.ai/docs/permissions/)
- **[事实]** Pi Coding Agent 明确不提供权限弹窗，官方建议在容器中运行，或由集成方自己实现确认机制。[Pi Coding Agent README](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/README.md)、[容器化文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/containerization.md)
- **[推断]** 如果把任何候选框架的“工具已成功”直接映射为“正式交付已发布”，就会绕过现有 Verifier 和 Delivery，重新制造“失败冒充成功”的风险。

结论：框架适配器只能产生候选产物和标准事件；`delivery.publish` 不得出现在模型可见的工具目录中。

## 4. 候选一：Deep Agents / LangChain 1.x + LangGraph

### 4.1 已验证事实

- **[事实]** LangGraph 是低层、长时、有状态 Agent 的编排 Runtime，提供持久化、流式、人工介入和故障后恢复，并允许在同一图中混合确定性步骤与模型驱动步骤。[LangGraph 官方概览](https://docs.langchain.com/oss/python/langgraph/overview)
- **[事实]** LangGraph 官方把 Deep Agents 定义为建立在 LangGraph 之上的 Agent Harness，而不是 LangGraph 的替代品。[LangGraph 官方概览](https://docs.langchain.com/oss/python/langgraph/overview)
- **[事实]** Deep Agents 内建任务规划、文件系统、Skills、记忆、上下文摘要与大结果卸载、子 Agent、人工介入和类型化事件流。[Deep Agents 官方概览](https://docs.langchain.com/oss/python/deepagents/overview)
- **[事实]** Deep Agents 支持自定义 LangChain 工具和 MCP，可使用可插拔虚拟文件系统；代码执行可以接 sandbox backend，也可以使用受限 QuickJS 解释器。[Deep Agents 官方概览](https://docs.langchain.com/oss/python/deepagents/overview)
- **[事实]** 官方示例列出了 Ollama 等本地模型入口，但这只证明存在 Provider 接口，不证明 Mangrove 当前 Qwen 端点能稳定完成复杂工具调用。[Deep Agents 官方概览](https://docs.langchain.com/oss/python/deepagents/overview)

### 4.2 基于当前代码的推断

- **[推断]** Mangrove 已经使用 Python、LangChain 和 LangGraph，因此这条路线最容易复用当前模型 Provider、Checkpoint、SSE 和服务进程，适配层可能最薄。
- **[推断]** LangGraph 可继续负责外层生命周期和持久恢复，Deep Agents 或较小的 LangChain 动态循环负责内层 `Observe → Plan → Act → Verify → Replan`；这样无需把确定性 Verifier 和 Delivery 放进模型循环。
- **[推断]** Deep Agents 内建能力较多，若直接启用完整文件系统、记忆写入、子 Agent 和 shell，会扩大权限面。首个 PoC 应只开放统一 Tool Bridge、只读来源和候选工作区，显式关闭非必要工具。

### 4.3 尚待 PoC 验证

- **[待测]** 当前本地 Qwen 是否能稳定输出 Deep Agents 所需的工具调用，遇到 `max_tokens` 截断后能否保留 Goal 并继续。
- **[待测]** LangGraph 外层 Checkpoint 与工作台现有任务 revision、取消和幂等键能否无歧义映射。
- **[待测]** Deep Agents 摘要/卸载是否会保留“只要附件 2、只输出一个 CSV、其余不要”等不可压缩约束。
- **[待测]** 在不开放内建 shell 的情况下，是否仍能通过 Mangrove 的 `sandbox.execute` 完成临时数据处理。

## 5. 候选二：OpenCode headless

### 5.1 已验证事实

- **[事实]** OpenCode 可通过 `opencode serve` 以 headless HTTP Server 运行，提供 OpenAPI 3.1 规范、会话 API 和 SSE 事件；Server 默认绑定 `127.0.0.1`，可配置 HTTP Basic Auth。[OpenCode Server 文档](https://opencode.ai/docs/server/)
- **[事实]** Server API 包含会话创建、消息、取消、摘要和权限响应等端点；官方 SDK 可启动本地 Server，也可连接既有 Server，并支持事件订阅和 `AbortSignal`。[OpenCode Server 文档](https://opencode.ai/docs/server/)、[OpenCode SDK 文档](https://opencode.ai/docs/sdk/)
- **[事实]** SDK 支持以 JSON Schema 请求结构化输出；文档说明其通过内建 Structured Output 工具和重试生成结构化结果。[OpenCode SDK 文档](https://opencode.ai/docs/sdk/)
- **[事实]** 权限配置支持 `allow / ask / deny`，可按 `read`、`edit`、`bash`、`task`、`skill` 等工具和模式配置；用户可以对一次请求允许、持续允许或拒绝。[OpenCode 权限文档](https://opencode.ai/docs/permissions/)
- **[事实]** OpenCode 支持自动上下文压缩、Skills 和自定义工具。[OpenCode 配置文档](https://opencode.ai/docs/config/)、[Skills 文档](https://opencode.ai/docs/skills/)、[自定义工具文档](https://opencode.ai/docs/custom-tools/)

### 5.2 基于官方接口的推断

- **[推断]** 三个候选中，OpenCode 提供的现成 headless 会话协议最完整，适合验证“框架进程与 Mangrove API 分离”的 Adapter。
- **[推断]** OpenCode 的 Server 身份验证和 Session ID 不等同于 Mangrove 的 `user_id + task revision` 所有权模型。生产接入不应让多个用户共享一个可相互枚举的裸 Server；PoC 至少应按评测租约启动隔离进程或容器，并由 Mangrove 保存 Session 映射。
- **[推断]** OpenCode 原生面向编码 Agent，默认工具和上下文假设与“只读业务文件、生成候选数据产物”不同。需要隐藏文件编辑/宿主 shell，把领域能力全部收口到 Tool Bridge。
- **[推断]** HTTP/SSE 边界增加了进程管理、版本固定、健康检查和事件转译成本，但也降低了 Node Runtime 对 Python 业务代码的侵入。

### 5.3 尚待 PoC 验证

- **[待测]** 其 OpenAI-compatible Provider 与当前本地 Qwen 的工具 Schema、流式响应和长上下文是否兼容。
- **[待测]** Server 重启后 Session、摘要和待审批状态能否按 Mangrove 的恢复语义继续，而不是只能新建会话。
- **[待测]** `abort` 是否能终止模型请求、工具调用、Docker 沙箱和子进程，而不仅是标记会话状态。
- **[待测]** 权限事件能否无损转换为 Mangrove 的“修改目标、重新扫描/OCR、扩大来源、停止任务”等业务操作。
- **[待测]** 结构化输出重试是否会在本地模型截断时产生空转，且相同失败指纹能否由 Mangrove 硬预算终止。

## 6. 候选三：Pi Agent Core

### 6.1 已验证事实

- **[事实]** 本地 `@earendil-works/pi-agent-core` 0.80.10 实现有状态 Agent 与工具循环，发出消息、工具开始/更新/结束和 Agent 生命周期事件，支持中止、steering、follow-up、continue 与 retry。参见本地 [Agent README](../../../pi-0.80.10/packages/agent/README.md) 和 [agent-loop.ts](../../../pi-0.80.10/packages/agent/src/agent-loop.ts)，上游对应说明见 [Pi Agent Core README](https://github.com/earendil-works/pi/blob/main/packages/agent/README.md)。
- **[事实]** 本地循环可以顺序或并行执行工具；`beforeToolCall` 可阻断调用，`afterToolCall` 可加工结果。高层 `Agent` 类会等待异步订阅者完成，而低层流主要用于观察事件。参见本地 [Agent README](../../../pi-0.80.10/packages/agent/README.md)。
- **[事实]** Pi Coding Agent 在 Core 之上提供持久 Session、上下文压缩、扩展和 JSONL RPC；RPC 包含中止、follow-up、状态、压缩和自动重试相关事件。[Pi Coding Agent README](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/README.md)、[RPC 文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md)、[压缩文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/compaction.md)
- **[事实]** Pi Coding Agent 默认提供 `read / write / edit / bash` 等编码工具，但不内置权限弹窗；官方把进程整体放入容器、Gondolin 或 OpenShell 作为隔离选项。[Pi Coding Agent README](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/README.md)、[容器化文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/containerization.md)
- **[事实]** Pi 的扩展机制可以注册工具、命令、事件处理器和 Provider，但这是可扩展接口，不是 Mangrove 所需权限和审计策略的现成实现。[Pi 扩展文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md)

### 6.2 基于本地源码的推断

- **[推断]** Pi Agent Core 是三者中最接近“只提供动态工具循环”的轻量内核，便于 Mangrove 完全控制 Tool Catalog、PolicyGate、持久化和事件模型。
- **[推断]** 这种轻量也是成本：用户隔离、任务恢复、Context Snapshot、审批、Docker 租约和 Delivery 边界几乎都需要 Mangrove 自己实现或从 Coding Agent 中有选择地适配。
- **[推断]** 不宜把完整 Pi Coding Agent 的编码工具直接暴露给普通用户；更稳妥的赛马实现是 Core + Mangrove 工具，必要时复用其 Session/compaction/RPC 设计，而不是照搬宿主文件和 bash 能力。
- **[推断]** `beforeToolCall` 适合做框架内的第一道拦截，但最终授权仍必须由 Mangrove Tool Bridge 基于服务端 `user_id`、revision、幂等键和工作区租约重新校验，不能信任模型进程传入的身份。

### 6.3 尚待 PoC 验证

- **[待测]** Pi 当前 Provider 与本地 Qwen 的流式工具调用、并行工具调用和长上下文行为。
- **[待测]** 压缩前后能否完整保留 GoalContract、审批状态、证据引用和禁止项。
- **[待测]** Core 自定义持久化与 Coding Agent Session 复用，哪一种更容易满足 Mangrove 的进程崩溃恢复和不可变 revision。
- **[待测]** TypeScript 进程与 Python 领域工具之间采用 HTTP、JSONL RPC 还是 MCP 时，取消传播、错误分类和大 Observation 引用的实际复杂度。
- **[待测]** 本地目录没有 Git 来源元数据；固定上游提交后，行为是否与当前 0.80.10 快照一致。

## 7. 设计参照，不进入首轮赛马

### 7.1 Codex

- **[事实]** Codex App Server 使用 JSON-RPC 风格的双向协议，围绕 thread、turn、item 和服务端事件组织运行，并能向客户端发起命令执行或文件变更审批请求；请求还可携带结构化输出 Schema。[Codex App Server README](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)
- **[事实]** Codex 把 sandbox 与 approval policy 作为两层独立控制：审批决定是否允许，sandbox 决定命令在操作系统层实际能接触什么；子进程继承该边界。[Codex 沙箱说明](https://developers.openai.com/codex/concepts/sandboxing)
- **[推断]** Mangrove 应借鉴其“稳定协议事件 + 服务端审批 + 独立强制沙箱”的分层，不把前端弹窗、模型自觉或工具描述当安全边界。
- **[说明]** Codex 不参加首轮框架赛马；本项目要求本地 Qwen 必须通过，而 Codex 在此只作为协议和隔离设计基准。

### 7.2 Claude Agent SDK

- **[事实]** Claude Agent SDK 暴露 Claude Code 的 Agent Loop 和上下文管理，包含内建工具、Hooks、子 Agent、MCP、权限、Session、Skills/Plugins、结构化输出和可观测性。[Claude Agent SDK 概览](https://code.claude.com/docs/en/agent-sdk/overview)
- **[事实]** 其权限处理包含 PreToolUse Hook、deny/ask/allow 规则、permission mode 和应用回调等层次。[Claude Agent SDK 权限文档](https://code.claude.com/docs/en/agent-sdk/permissions)
- **[推断]** Mangrove 可借鉴 Hooks 对工具调用前后进行稳定拦截，以及将权限决策做成可测试协议；但 SDK 绑定 Claude 服务，不适合作为“本地 Qwen 必须通过”的首轮候选。

### 7.3 Hermes

- **[事实]** Hermes Agent 官方描述包含多 Provider/自定义端点、工具与程序化工具调用、持久记忆、Skills、自学习循环、隔离子 Agent，以及 Docker/SSH 等执行环境。[Hermes 官方文档](https://hermes-agent.nousresearch.com/docs/)、[Hermes 官方仓库](https://github.com/NousResearch/hermes-agent)
- **[推断]** 它适合作为渐进披露 Skills、程序化批量工具调用和“成功轨迹形成候选经验”的设计参照。
- **[推断]** Hermes 捆绑的通用工具、消息渠道、记忆和自学习范围明显大于数据工作台首期纵切面。把整套能力直接接入会同时扩大权限与评测面，因此不进入首轮赛马；这不是对其质量的否定。

### 7.4 Claw Code

- **[事实]** “Claw Code”不是唯一明确的上游项目名称。检索到的实现包括自称受 Claude Code 启发、支持本地 OpenAI-compatible 模型的 [HarnessLab/claw-code-agent](https://github.com/HarnessLab/claw-code-agent)，以及明确标注 experimental/unvalidated 的 [SocialGouv/claw-code-go](https://github.com/SocialGouv/claw-code-go)。
- **[推断]** 名称、来源和成熟度尚不唯一，当前无法建立与其他候选同等稳定的供应链基线，所以只保留在调研长名单。若后续指定确切仓库、提交和维护主体，可重新进入候选资格审查。

## 8. 纸面对照矩阵

表中“已具备”只表示官方接口或本地源码存在，不表示已经适配 Mangrove。

| 维度 | Deep Agents + LangGraph | OpenCode headless | Pi Agent Core |
|---|---|---|---|
| 动态模型-工具循环 | **[事实]** 已具备 | **[事实]** 已具备 | **[事实]** 已具备 |
| 规划与上下文卸载 | **[事实]** 内建规划、摘要、文件卸载 | **[事实]** Session 与自动压缩 | **[事实]** Coding Agent 层提供 Session/压缩；Core 较轻 |
| 持久运行/恢复 | **[事实]** LangGraph Checkpoint 能力 | **[待测]** Session API 存在，崩溃恢复语义待测 | **[待测]** 需适配 Coding Agent Session 或自建 |
| 取消/steer | **[事实]** interrupt/HITL | **[事实]** abort、权限响应、事件 | **[事实]** abort、steering、follow-up |
| 结构化事件 | **[事实]** typed streams | **[事实]** SSE | **[事实]** Agent/tool 生命周期事件 |
| 工具审批钩子 | **[事实]** HITL/中间件，但仍需中央 Gate | **[事实]** allow/ask/deny，但仍需中央 Gate | **[事实]** beforeToolCall 可阻断，但仍需中央 Gate |
| 操作系统级沙箱 | **[事实]** 可接 sandbox backend，不是自动生产隔离 | **[待测]** 权限不是硬沙箱，须外置 Docker | **[事实]** 官方建议外置容器等隔离 |
| 本地 Qwen | **[待测]** 有本地 Provider 路径，不代表本任务通过 | **[待测]** 有兼容 Provider 配置，真实行为待测 | **[待测]** Provider 行为待测 |
| Mangrove 用户隔离 | **不提供**，必须保留项目所有权层 | **不提供**，Basic Auth/Session 不等价 | **不提供**，必须由项目实现 |
| 证据、Verifier、Delivery | **不提供业务语义** | **不提供业务语义** | **不提供业务语义** |
| 预计接入成本 | **[推断]** 最低 | **[推断]** 中高，增加独立服务适配 | **[推断]** 中高，控制强但自建面更大 |

## 9. 公平赛马的统一接口

三条候选必须位于同一个业务适配边界之后。不得为某个候选开放更多工具、更多上下文或外部模型，也不得用框架自带的文件/bash 能力绕过统一 Tool Bridge。

### 9.1 AgentKernel

以下为语言无关契约，阶段 2 再确定具体 Pydantic/OpenAPI 定义：

```text
start(RunRequest) -> RunHandle + EventStream
resume(run_id, checkpoint_ref) -> EventStream
steer(run_id, UserDecision) -> Ack
cancel(run_id, reason) -> Ack
```

`RunRequest` 至少包含：

- `run_id`、`user_id`、任务 revision 和幂等键；
- 不可变 `GoalContract`；
- 只读 `SourceManifest`，包括来源 ID、类型、哈希和允许读取范围；
- 同一版本的 `ToolCatalogSnapshot`；
- 同一个本地 Qwen `ModelRef`，禁止静默外部回退；
- 轮数、Token、时长、沙箱次数和候选产物数预算；
- 按用户和任务隔离的 `WorkspaceLease`；
- 可恢复的 `ContextSnapshotRef`。

### 9.2 统一工具调用

```text
ToolCall {
  call_id,
  run_id,
  user_id,
  revision,
  tool_name,
  schema_version,
  arguments,
  idempotency_key
}
```

每次调用由 Mangrove 服务端重新验证：

1. Tool 是否存在于本次快照；
2. 用户是否拥有来源和任务；
3. 是否需要审批、网络或沙箱；
4. 是否违反只读输入、资源预算或输出目录；
5. 重试是否安全、幂等键是否已经完成。

工具输出大对象必须落到受控存储，只向模型返回摘要、Schema、证据计数和可继续定向读取的引用；不得把完整 PDF/Excel 重复塞入上下文。

### 9.3 统一事件

候选原生事件必须转换为下列稳定事件，不把隐藏思维链传给前端：

- `run.started / resumed / cancelled / failed`
- `source.observed`
- `plan.updated`
- `tool.started / completed / failed`
- `approval.required / resolved`
- `context.compacted`
- `candidate.created`
- `verification.completed`
- `run.completed`

面向普通用户的摘要只说明“正在查看什么、调用了什么能力、发现了什么、下一步是什么”；原始工具参数、错误和审计信息按角色隔离保存。

### 9.4 同条件约束

- 同一 GoalContract、原始文件、工具实现、Docker 镜像和资源预算；
- 同一个本地 Qwen 端点、模型参数、上下文上限和最大输出；
- 每个核心 P0 用例连续运行 3 次；
- 不允许外部模型兜底，不允许手工修改某一路的中间结果；
- 每条路线只写 Adapter，不得加入用例专属 Prompt、关键词分支或 TaskFamily；
- Agent 只能生成 `CandidateArtifact`，统一 Verifier 决定是否允许交给 Delivery Publisher；
- 保存完整标准事件、工具调用、预算消耗、失败指纹和产物哈希，供盲评和复盘。

## 10. 评分规则

每项按 0–5 分打分，只有实际证据才可得分：

- 0：缺失或触发一票否决；
- 1：大量人工干预，无法形成完整纵切面；
- 2：能完成部分用例，但不稳定或边界明显不完整；
- 3：达到基本门槛，存在可管理缺口；
- 4：稳定通过，只有轻量适配或运维缺口；
- 5：稳定通过且证据完整、边界清晰、维护成本低。

总分公式：

```text
总分 = Σ（单项得分 / 5 × 权重）
```

| 评分项 | 权重 | 必须提供的证据 |
|---|---:|---|
| 真实任务正确率与未知需求泛化 | 35% | 固定 P0 + 未参与调优的扩展集，逐项文件级验收 |
| 本地 Qwen 工具调用、长上下文与截断恢复 | 20% | 连续 3 次、压缩前后 Goal 对比、截断恢复轨迹 |
| 权限、沙箱、用户隔离与外发治理 | 15% | 越权、提示注入、断网、只读输入和跨用户负向测试 |
| 取消、恢复、幂等和进程崩溃处理 | 10% | 中途取消、杀进程、重放同幂等键、容器清理证据 |
| 结构化事件、追踪和调试 | 10% | 标准事件覆盖率、错误定位、普通用户/管理员视图 |
| 嵌入、部署、依赖与维护成本 | 10% | Adapter 变更量、进程/镜像、升级固定方式和运维清单 |

两条路线总分差距不超过 3 分时，选择运维和维护成本更低的一条。若三者都未通过硬门，使用 LangChain 1.x 的开源循环原语实现最小 Mangrove-owned Kernel；这同样要通过全部门禁。

本报告不填写候选总分。没有运行真实 PoC 时给出分数会制造虚假精确度。

## 11. 一票否决项

发生任一项，该路线立即判为不合格，不以总分抵消：

1. 跨用户读取、事件泄露、候选产物串用或 Session 越权；
2. 未经逐次说明和用户批准向外部服务发送原文、证据或提示；
3. 普通用户能够在宿主机执行代码，或逃逸任务级 Docker 工作区；
4. 模型或框架能绕过 Tool Bridge、PolicyGate、只读来源或资源预算；
5. Verifier 失败仍生成正式 `DeliveryOutput` 或可下载文件；
6. 无法可靠取消模型请求、工具、容器和子进程，或取消后继续发布；
7. 进程重启后无法安全恢复/终止，重复请求产生不受控重复副作用；
8. 本地 Qwen 无法稳定使用，或系统静默切换外部模型掩盖失败；
9. GoalContract 的必须包含、明确不要、来源范围或输出格式在压缩/恢复后丢失；
10. 密钥、跨用户路径或超出策略允许的业务原文进入普通事件、日志或上下文摘要。

## 12. 赛马初始建议

### 12.1 不预设赢家

三条路线都满足“值得做一个受控纵切面”的最低纸面条件，但没有一条已经证明能解决 Mangrove 的真实问题：

- **Deep Agents**：优先验证最低迁移成本和能否把现有 LangGraph 从固定业务路由改为稳定外层 Runtime；
- **OpenCode headless**：优先验证独立进程、HTTP/SSE、权限响应和结构化输出能否形成清晰的 Kernel Seam；
- **Pi Agent Core**：优先验证最小循环是否让 Mangrove 以较少隐藏默认值掌控工具、上下文和恢复。

### 12.2 建议的 PoC 顺序

为尽快排除最大风险，建议并行准备统一夹具，再按以下顺序接通：

1. **Pi Agent Core**：最先检验本地 Qwen 工具循环和 Mangrove Tool Bridge，避免被高级 Harness 默认能力掩盖基础模型兼容问题；
2. **Deep Agents + LangGraph**：复用同一桥接层，验证内建上下文、持久化和 Python 集成收益；
3. **OpenCode headless**：最后验证进程外协议方案，明确其额外运维成本是否换来足够的会话和事件收益。

这里的“顺序”只表示降低研究浪费，不构成优先选型。评分必须盲看统一结果。

### 12.3 阶段 1 退出产物

每条路线至少交付：

- 固定 commit、依赖锁和镜像摘要；
- 仅实现统一 `AgentKernel` 的可删除 Adapter；
- 本地 Qwen 连续 3 次原始事件和标准化事件；
- 同一 P0 小集的候选产物、Verifier 结果和失败分类；
- 取消、重启、幂等、断网和跨用户负向测试；
- 上下文压缩前后的 Goal/证据引用一致性报告；
- 代码变更量、常驻进程、镜像、启动和升级成本；
- 按本报告规则生成的评分表与一票否决审查。

阶段 1 完成后必须先展示证据和未决问题，由用户确认是否进入控制面实现；不得自动把纸面领先者接入默认入口。

## 13. 仍需真实 PoC 回答的问题

1. 当前本地 Qwen 对三种工具 Schema 和事件流的稳定性是否存在显著差异？
2. 哪一路能在输出被截断后保留完整 GoalContract，而不是重新猜测用户目标？
3. 哪一路的取消真正传播到模型、Python 工具、Docker 容器及其子进程？
4. 哪一路在进程重启后能以最少自定义代码恢复到“上一个已提交 Observation”？
5. 哪一路能把大型 PDF/表格结果卸载为引用，同时保持证据定位和验证可重现？
6. 哪一路最容易关闭内建高风险工具，只保留 Mangrove Tool Catalog？
7. 哪一路产生的事件最容易转换成普通用户可理解、管理员可审计的双层视图？
8. 哪一路在 30 个未知任务上真正通过工具组合泛化，而不是依赖框架模板或用例专属 Prompt？

在这些问题有真实证据之前，最诚实的结论是：LangGraph 不应因名称被淘汰，Deep Agents、OpenCode 和 Pi 也不应因功能清单被直接采纳。专项成功与否取决于统一领域边界、严格赛马和“候选产物不能绕过独立验证与发布”的强制架构。

## 14. 阶段 1 实测后记

2026-07-29 的可抛弃 PoC 已回答部分问题：

- Pi、OpenCode、Deep Agents 均能连接本地 `Qwen3.6-35B-A3B` 并调用统一 Tool Bridge；
- 18 次冻结运行分别通过 16、16、12 次，三者均未通过连续三次 P0 硬门；
- Pi 的已执行中位耗时最低、嵌入最轻；OpenCode 带来约 174 MB 原生进程包和额外运行
  配置；Deep Agents 当前依赖集会升级 Mangrove 锁定的 LangChain/LangGraph；
- 统一 Supervisor 的取消和共同 Docker 沙箱基础门均通过，但它们是 Mangrove 控制面
  能力，不属于某个框架的胜出优势。

因此“纸面候选不直接采纳”的初始判断得到运行证据支持。阶段 1 未验证长上下文压缩、
输出截断、进程重启、幂等恢复、完整复合来源、30 项泛化集和生产用户隔离。当前推荐按
ADR-0017 使用现有项目锁定版本的开源循环原语实现最小 Mangrove-owned Kernel，并把
上述未验证项保留为后续严格门；该推荐仍等待用户确认。

详细结果见
[阶段 1 执行报告](../plans/2026-07-29-agentic-runtime-vnext-stage1-execution-report.md)。

## 15. 用户后续决策

用户于 2026-07-29 明确把真实可用置于首要位置，并授权完整 `pi-coding-agent` RPC +
任务级 Docker 的全能力生产灰度。该决策保留本报告的官方源事实和阶段 1 分数，但取代
第 14 节的最小 LangChain/LangGraph Kernel 推荐：

- Pi 进入生产资格实现，OpenCode 保留后备；
- 容器内开放文件、Shell、代码、依赖安装、公共网络、Skills 和成熟开源工具；
- Mangrove 保持所有权、权限提升、候选验证和正式交付；
- 未通过真实纵切面、恢复、隔离和泛化门前，不宣称 Pi 已生产合格。

实施入口见
[Pi 全能力生产灰度计划](../plans/2026-07-29-agentic-runtime-vnext-pi-full-capability-gray-plan.md)。
