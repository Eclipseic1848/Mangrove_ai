# ADR-0035：统一数据工作台复用共享产品能力与 CoreMind Runtime

- 状态：`accepted`，用户于 2026-08-27 明确确认
- 日期：2026-08-27
- 决策来源：GitHub Issue #83 的 P1 产品流程澄清
- 上游：
  [ADR-0017](0017-agentic-runtime-vnext.md)、
  [ADR-0018](0018-unified-task-domain-contract.md)、
  [ADR-0019](0019-vnext-delivery-and-default-cutover-state-machine.md)、
  [ADR-0020](0020-provider-connection-broker-and-credential-isolation.md)、
  [ADR-0027](0027-conversation-steering-and-context-compilation.md)

## 背景

Mangrove 当前同时存在数据工作台、对话工作区和“分析报告（旧）”入口。数据工作台已经拥有
来源、预览、TaskRevision、Run、Candidate、独立验证和正式 Delivery 主链；对话工作区与旧
分析链则保留多轮交互、模板、个人记忆、自动任务、反馈和旧报告能力。把这些能力继续留在互不
相通的页面和执行图中，会形成两套任务真相、两套恢复语义和两套产品体验。

数据工作台现有 Agentic Runtime 直接通过任务级 Docker 和 JSONL/RPC 使用固定版本的
`pi-coding-agent`。公开 CoreMind 项目使用同一家族的 `pi-agent-core` 与 `pi-ai`，并在其上
提供 Run、Session、预算、工具权限、Checkpoint、Trace、恢复、Replay、Protocol 和 Python
SDK 等通用 Runtime 能力。CoreMind 仍在持续迭代；Mangrove 若复制这些机制，会长期重复维护，
若让业务代码直接依赖 CoreMind 内部类型，又会把产品域绑定到一个快速变化的依赖。

用户确认的目标是：保留数据工作台现有价值，把对话与旧分析的产品能力迁入数据工作台，让
Mangrove 面向在线/离线、公域/私域数据共享同一任务生命周期；同时优先复用 CoreMind 的通用
Runtime 能力，并为其未来能力预留稳定接入方式。

## 决策

### 1. 数据工作台是唯一主产品工作区

`/data-prep` 继续作为 Mangrove 的统一数据工作台。它必须保留来源选择、文件和数据预览、
自然语言交互、任务状态、结果预览与下载，并逐项继承对话工作区和旧分析链的模板、个人记忆、
多轮追问、自动任务、反馈和报告能力。

继承的是用户能力和领域事实，不是复制旧页面、旧 Prompt 或旧 Conductor 图。历史入口在每项
能力完成映射、回归和用户验收前保持兼容；不得先删除后重做，也不得继续产生第二套正式交付
语义。

### 2. Mangrove 保持产品域与正式交付权威

Mangrove 继续独占以下事实与决策：

- TaskOwner、SourceBinding、SourceSnapshot、Artifact 与认证来源会话；
- GoalContract、TaskRevision、ConversationSteering、TaskTemplate 与 OwnerMemory；
- ModelConnection、AccessGrant、外发确认和用户选择的精确模型；
- Candidate、VerificationReport、PublishIntent 与正式 Delivery；
- Owner 隔离、权限、审计、扫码重新认证和外部发布确认。

CoreMind 或其他 AgentKernel 只能执行冻结目标并生成 Candidate。其成功、质量评分或
`releaseReadiness` 都不能替代 Mangrove 的独立 Verifier 和 Publisher。

### 3. CoreMind 是优先复用的 Runtime 内核，不是业务框架

业务 Module 只依赖 Mangrove 的 `AgentKernel` Interface。CoreMind 通过
`CoreMindAgentKernelAdapter` 接入，现有 `PiRuntime` 在兼容性验证和能力迁移完成前作为历史
Adapter 保留。迁移期可以存在多个 Adapter，但一个 Run 只能冻结一个 RuntimeBinding，运行中
不得自动换内核或静默回退。

首要复用候选包括：

- Run、Session、暂停、恢复、取消与安全终态；
- 结构化事件、顺序、时间戳、Trace 与只读 Projection；
- 工具调用生命周期、审批、副作用声明、Checkpoint 与收据；
- turn、tool、token、费用、超时和重试预算；
- Context 生命周期、压缩、Replay 和未来 Child Run 等经过产品批准的能力；
- Python SDK 到同一 Node Runtime 的协议桥，而不是在 Python 中建立第二套 Agent Loop。

Mangrove 不直接复用 CoreMind 的业务模板、发布判断或原始用户界面，也不把 CoreMind 的默认
Provider 选择当作 Mangrove 用户选择。模型连接、协议路线和外发仍以 Mangrove 冻结事实为准。

### 4. 每个 Run 冻结精确 RuntimeBinding

RuntimeBinding 至少记录：

- `kernel_family` 与 Mangrove Adapter 版本；
- 实际内核版本与协议版本；
- Mangrove Run ID 与外部 Runtime Run ID 的稳定绑定；
- AgentKernelCapabilityManifest 版本与摘要；
- 事件 Schema 版本和可恢复性声明；
- 运行所需能力的满足结果。

暂停、扫码等待、恢复、有界重试和结果未知处理都沿用同一 RuntimeBinding。只有创建新 Run 才
能选择经批准的新内核版本。原版本无法安全恢复时必须失败关闭并请求人工处理，不得用新版本
猜测性继续。

### 5. 未来能力通过显式能力协商接入

Adapter 必须把实际支持能力投影为版本化 AgentKernelCapabilityManifest，至少覆盖：

```text
start / resume / steer / cancel / events / query
session / checkpoint / usage / tool_effect / replay / child_run
```

任务在启动前声明必需能力和可选能力。缺少必需能力时不创建 Run；缺少可选能力时只允许产品
规格已经定义的显式降级，不得由 Adapter 自行猜测。未知事件不能被当成成功证据，也不能原样
展示；需要经过版本化映射、脱敏和受众投影。

CoreMind 每次升级采用以下门禁：

1. 固定候选版本和完整依赖清单，不自动跟随 `main` 或浮动版本；
2. 在隔离环境运行 AgentKernel 契约测试和 Mangrove 黄金任务；
3. 重放暂停、恢复、取消、工具副作用、事件顺序、Token 未知和结果未知场景；
4. 对比旧版本的 RuntimeBinding、事件、Candidate 与资源清理差异；
5. 由用户确认能力范围、依赖、安全和数据外发变化；
6. 只允许新 Run 使用通过门禁的版本，保留旧任务恢复路径。

源码存在、CI 通过或 CoreMind 发布新版本都不自动构成 Mangrove 升级授权。

### 6. 工作记录与用量由 Mangrove 归一化

CoreMind 事件先转换为 StructuredProgressEvent，再形成默认折叠的 AgentWorkTrace。普通用户
只看到行动、判断摘要、结果、证据、真实时间和恢复事实；不展示原始思维链、系统 Prompt、
Cookie、Token、宿主路径、完整命令或原始大日志。

WorkSession 仍等于一个 Mangrove Run。CoreMind 的 RunMetrics 可作为执行观测，但
ProviderUsage 与 WorkSessionUsage 仍由 Mangrove 按 Owner、TaskRevision、Run、连接和 Purpose
保存。任何调用 Usage 未知时显示已知下限和未知调用数，不把未知当作零，也不估算账单。

### 7. 模板、记忆和自动任务复用同一任务主链

- TaskTemplate 只提出 GoalContract、DeliverySpec、结构或方法草案，应用后由用户确认并冻结；
- OwnerMemory 按 Owner、来源、用途和相关性检索后进入 CompiledContext，不全量注入；
- 旧分析经验或学习结果不能自动成为全局模板或跨用户记忆；
- Scheduler/Automation 只作为创建或继续同一 SemanticTask 生命周期的 Adapter；
- Feedback 绑定精确 TaskRevision、Run、Candidate 或 Delivery，不形成旁路结果。

模板和记忆都不能扩大来源、权限、外发、不可逆操作或正式发布范围，也不能替代 EvidenceRef。

### 8. 采用逐能力迁移与验收

迁移维护显式能力矩阵，至少包含自然语言交互、来源/文件、预览、多轮追问、模板、个人记忆、
自动任务、反馈、报告、预览下载、历史会话与正式 Delivery。每一项分别记录旧入口、共享
Module、数据迁移方式、回归证据、用户验收和旧入口处置条件。

未通过矩阵的能力继续留在兼容入口；不得以“统一工作台已经上线”为由删除。旧任务、旧
Delivery 和历史审计记录不迁移、不覆盖、不重新发布。

## 考虑过的替代方案

- **把对话页面整体嵌入数据工作台**：界面看似统一，但仍保留两套任务与交付真相，拒绝。
- **直接用 CoreMind 类型重写 Mangrove 业务层**：短期代码少，但版本迭代会穿透业务域，拒绝。
- **永久维护 PiRuntime 与 CoreMind 两套同等主链**：重复修复恢复、事件和用量问题，拒绝；
  多 Adapter 只用于有退出条件的迁移。
- **自动跟随 CoreMind 最新版本**：无法保证旧 Run 恢复、协议和安全语义，拒绝。
- **复制 CoreMind Runtime 源码到 Mangrove**：失去上游迭代收益并制造长期分叉，拒绝。
- **用 CoreMind 会话或模板取代 Mangrove TaskRevision**：会混淆模型上下文与业务真相，拒绝。
- **先删除旧分析再补功能**：会丢失用户现有能力与历史访问路径，拒绝。

## 后果

### 正面

- 数据工作台保留现有优势，并获得对话、模板、记忆和自动任务的统一体验；
- Mangrove 不再重复建设通用 Runtime 可靠性能力；
- CoreMind 后续能力可通过稳定 Adapter 和能力清单逐项引入；
- 运行内核升级不会改写 TaskRevision、权限、来源、验证和正式交付语义；
- 旧能力有明确迁移和验收门，而不是一次性重构。

### 代价

- 首次需要建立 AgentKernel 契约套件、RuntimeBinding 和能力清单持久化；
- CoreMind 与现有 PiRuntime 的版本、协议、事件、工具和容器边界必须做兼容性原型；
- 迁移期需要维护有限的旧 Adapter 和能力矩阵；
- CoreMind 的新功能不会“安装即获得”，仍需要产品范围和安全验证。

## 实施与授权边界

本 ADR 只冻结架构方向，不证明 CoreMind 当前发布包已适配 Mangrove，也不授权安装依赖、修改
生产数据库、调用真实 Provider、迁移旧任务、删除旧入口、创建分支/提交/PR、发布或部署。
具体首片范围、测试和完成门见 P1-01 匿名网页来源规格。
