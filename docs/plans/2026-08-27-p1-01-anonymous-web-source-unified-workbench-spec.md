# P1-01：匿名网页来源统一数据工作台与 Runtime 复用规格

> 状态：`SPEC_APPROVED`，用户于 2026-08-27 明确确认
>
> 规格日期：2026-08-27
>
> GitHub：[#83](https://github.com/Eclipseic1848/Mangrove_ai/issues/83)，远端仍为
> `wayfinder:grilling`，本规格尚未回写
>
> 权威决策：ADR-0008、ADR-0017～ADR-0020、ADR-0027、ADR-0035
>
> 当前阶段：规格编写；不构成实现、依赖安装、生产迁移、真实 Provider 调用或发布授权

## Problem Statement

Mangrove 的数据工作台已经拥有文件和数据来源、自然语言任务、预览、TaskRevision、Run、
Candidate、独立验证和正式 Delivery 主链，但匿名网页来源仍没有完整接入这条产品流程。当前
网页采集和旧分析能力主要分散在 Conductor、对话工作区与历史采集配置中，容易产生以下问题：

- 用户输入一个网页目标后，不清楚系统访问了哪些页面、哪些失败、是否真的覆盖了要求；
- 来源获取、任务修订和执行运行边界混在一起，获取失败可能留下半任务或误导状态；
- Conductor 或 Agent 的输出可能看起来像报告，却没有进入统一 Candidate、Verifier 和
  Publisher；
- “想要 10 家”与“当前只找到 9 家”缺少可验证的缺口语义，系统容易在不输出与冒充完整之间
  二选一；
- 对话工作区的模板、个人记忆、多轮追问和自动任务与数据工作台不是同一条生命周期；
- Pi Runtime、对话事件和 Provider Usage 已有多处实现，继续分别扩展会重复建设；
- 用户看不到可靠、脱敏且可恢复的智能体工作记录，也看不到每个执行会话的时间和 Token；
- CoreMind 已提供可恢复 Runtime、Session、Trace、Checkpoint、预算和协议能力，但 Mangrove
  还没有一个稳定 Adapter 边界来复用当前与未来版本。

P1-01 需要先完成匿名公开网页纵切片，以最小但完整的产品流程证明：一个来源可以从获取、
冻结、执行、验证到正式 Delivery 全程使用统一数据工作台和统一领域语义。HTTP、数据库和认证
网页随后沿相同生命周期接入，而不是再建立新页面或新任务系统。

## Solution

### 1. 统一产品入口

普通用户继续从 `/data-prep` 数据工作台创建任务。页面保留现有文件、来源、自然语言输入、
数据预览、任务详情、结果预览和下载能力，并增加匿名网页来源选择。`/chat` 和
“分析报告（旧）”在能力迁移验收前保持兼容，但不再作为新网页主流程的任务真相或正式交付
入口。

用户启动前可以看到并修改：

- 精确网址或明确的同站范围；
- 任务目标、必须包含、明确不要和数量/完整性要求；
- 当前选择的精确模型与连接；
- 将发送给外部模型的公开内容类别与用途；
- 系统建议的交付形式；
- 将应用的模板和相关个人记忆摘要。

未指定交付格式时，结构化结果建议 XLSX，叙述性结果建议 Markdown。建议必须可见、可编辑，
用户点击启动后才冻结到 DeliverySpec。

### 2. 先获取来源，再形成任务修订

用户点击启动后，系统先建立 Owner 隔离、可幂等恢复的 SourceAcquisitionAttempt。此时界面显示
“正在获取来源”，但还没有 TaskRevision、Run、WorkSession、Candidate 或 Delivery。

匿名网页获取遵守以下边界：

- 输入精确 URL 时默认只读取该页面；
- 跟随同站链接必须由用户选择范围和上限；
- 跨站访问必须另行确认，不能由 Agent 自动扩大；
- 一个逻辑获取批次形成一个 SourceSnapshot；每个实际页面形成独立 Artifact；
- Snapshot 明确列出成功页面、失败页面、最终 URL、读取时点、内容哈希和获准范围；
- 零有效页面时获取失败，不形成 TaskRevision 或 Run；
- 显式“全部”“必须 N 项”或硬性完整性要求未满足时暂停，不冻结不合格 Snapshot；
- 探索性上限可以在披露范围、失败项和未知后继续；
- 获取结果必须幂等，同一提交重试不能重复创建任务或重复计费。

来源完整性门通过后，系统在一个事务边界中冻结 SourceSnapshot、创建 SemanticTask、
TaskRevision 1、GoalContract、DeliverySpec、RuntimeBinding 和首个 Run，然后才进入智能体执行。
中途任一步失败不得留下可执行的半修订。

### 3. 用统一 AgentKernel 执行

TaskRevision 只冻结目标、来源、权限、模型、模板/记忆引用、证据策略和交付规格，不冻结工具
路线。AgentKernel 可以在约束内观察来源、调用工具、验证中间结果并重规划。

CoreMind 是优先复用的 Runtime 内核候选，通过 ADR-0035 的 Adapter 接入；现有 PiRuntime 在
兼容性门通过前继续作为历史 Adapter。业务流程不依赖 CoreMind 或 Pi 的内部类型。每个 Run
冻结精确 RuntimeBinding，包含内核、Adapter、版本、协议、外部 Run ID、事件 Schema 和能力
清单摘要。

模型选择遵守用户已确认规则：用户当前选择什么模型，本 Run 就调用什么模型。不得因为内容被
判断为“敏感”而静默换模型，也不得因 Provider 失败切换连接或模型。只有可证明请求未发出，
或 Provider 明确返回安全可重试的暂时错误时，才允许同模型有界重试；如果请求是否到达无法
确认，则暂停并让用户决定是否接受重复调用和费用风险。

### 4. 生成部分结果也必须如实呈现

系统不能因为硬性要求暂时未满足就隐藏已经有证据的结果，也不能把部分结果冒充完整结果。

Verifier 对覆盖只形成三类结论：

1. **确认漏提**：来源中存在合格内容，但 Candidate 没有提取；允许同 Run 自动修复；
2. **确认本次范围不足**：已经完整观察一个有限范围，确认该范围只有较少结果；
3. **无法判断**：仍有未读、失败、低质量或开放范围，不能声称“全网没有”。

例如用户要求“列出 10 家”，系统在一个明确且已完整检查的获准目录中只有 9 家：页面展示
9 家有证据结果和“本次获准范围确认只有 9 家”的缺口。原硬性 TaskRevision 不能正式交付；
用户接受 9 家时形成新 TaskRevision。若存在未读网页，则只能显示“当前观察到 9 家，覆盖仍
未知”，不能说第 10 家不存在。

探索性目标可以正式交付有证据的观察结果，但 Delivery 必须披露实际来源范围、失败项和覆盖
未知。硬性全量或定量目标存在缺口/未知时只能形成 PartialCandidate，不能按原修订发布。

### 5. Candidate、Verifier 和 Publisher 保持独立

AgentKernel、工具、模板和旧分析能力只能生成 Candidate。CandidateVerification 必须重新打开
冻结来源和候选，检查：

- GoalContract 的必须包含、明确不要、数量和范围；
- ResultItem 分类、EvidenceRef 与来源覆盖；
- 文件数量、格式、列顺序、命名和可重开性；
- Owner、TaskRevision、Run、RuntimeBinding 和 Candidate 身份；
- 禁止项、Secret 泄漏、外部路径和损坏文件。

验证通过后，TaskLocalPublication 自动把结果发布为仅 TaskOwner 可见的正式 Delivery，不再
重复询问一次“是否保存”。发送邮件、写入外部数据库、上传第三方系统或公开分享继续要求单独
确认。

Publisher 失败时保持同一 Candidate、VerificationReport 和 PublicationKey，只重试未完成的
发布工作；不得重新抓网页、重新调用模型、重新执行 Agent 或产生重复 Delivery。

### 6. 工作记录默认折叠，但可检查

视觉论点为“可信的数据工作台，而不是会聊天的演示页”：来源、任务和结果是主角，智能体工作
记录提供可检查的过程证据，但不抢占数据预览空间。

推荐方向是保留现有数据工作台视觉语言，把工作记录作为任务正文中的渐进披露区：

- 默认折叠，包括运行中状态；
- 折叠态示例：
  `14:06 开始 · 14:08 完成 · 实际工作 2分18秒 · 12 个操作 · 3 次模型调用 · 8,420 Tokens`；
- 展开后按时间显示判断摘要、工具行动、脱敏输入、耗时、结果摘要、证据和恢复事实；
- 已恢复的失败保留在展开记录，折叠态只显示“已处理 1 次重试”；
- 未解决失败、暂停和 OwnerActionRequest 始终突出显示；
- 扫码、授权、接受缺口等用户动作置于折叠区外，不能藏在详情里；
- 不展示逐字思维链、系统 Prompt、Cookie、Token、完整命令、宿主路径或原始大日志；
- 技术详情使用第二层脱敏展开，仅用于有需要的 Owner 或获准管理员审计。

现有颜色、字体、间距、圆角和组件继续作为设计权威，不引入通用 SaaS 卡片墙或新的全局视觉
体系。交互动画控制在 150～300ms，并支持 `prefers-reduced-motion`。所有折叠控件、状态、
错误和扫码入口必须可键盘操作、具有可见焦点和语义化名称。

### 7. WorkSession 时间与 Token

一个 WorkSession 等于一个 Run。来源获取发生在 Run 之前，显示自己的获取时间，但不冒充
WorkSession。暂停、扫码等待、恢复和有界重试仍属于同一 WorkSession。

每个 WorkSession 至少显示：

- 开始、暂停、恢复和结束时间；
- 实际工作时长与明确用户等待时长；
- 行动数、工具调用数、恢复失败数和模型调用数；
- 按 Purpose 汇总的 Provider 原生输入、输出、缓存和总 Token（Provider 有返回时）；
- 已知 Token 下限和 Usage 未知调用数；
- Runtime、连接、模型和用量事实的可审计绑定。

如果四次模型调用中三次合计 8,420 Tokens、一次未返回 Usage，显示
`至少 8,420 Tokens · 4 次调用 · 1 次未知`，不得显示为 8,420 的确定总数、不得把未知记为零，
也不得用 CoreMind 或 Pi 的估算值冒充 Provider 账单。

### 8. 模板、记忆和旧分析能力的继承方式

本纵切片不一次性重写全部旧能力，但必须遵守 ADR-0035 的迁移矩阵，确保后续能力进入同一
SemanticTask 主链。

| 用户能力 | 当前入口/实现 | 目标共享 Module | #83 的责任 | 旧入口退出门 |
|---|---|---|---|---|
| 自然语言创建与追问 | `/chat`、数据工作台 | ConversationSteering | 网页任务从数据工作台创建并可追问 | 代表任务验收通过 |
| 文件/来源与预览 | 数据工作台 | Source/Artifact/Preview | 完整保留并加入匿名网页 | 不退出，继续作为主能力 |
| 分析模板 | 旧分析与模板页 | TaskTemplateCatalog | 模板只形成可见草案并冻结版本 | 模板清单与结果逐项等价 |
| 个人记忆 | 记忆页、Chat 注入 | OwnerMemory | 只注入相关摘要，保持 Owner 隔离 | 读写、删除、纠错与任务引用验收 |
| 学习经验 | Conductor lessons/templates | 受治理的模板/方案 | 不自动全局生效，不跨用户 | 来源、晋级和回滚证据完整 |
| 自动任务 | Scheduler | SemanticTask Adapter | 未来只能启动同一任务主链 | 定时任务与手动任务结果等价 |
| 用户反馈 | Feedback | Task/Run/Delivery Feedback | 绑定精确对象，不旁路改写 | 反馈历史和纠错入口验收 |
| 报告、预览、下载 | 旧分析、Delivery | Candidate/Delivery/Preview | 正式结果只读 Delivery | 所有在用格式和历史结果可访问 |
| 历史会话与任务 | `/chat` 与旧任务 | Compatibility Projection | 只读兼容，不改写历史 | 保留期与迁移方案另行确认 |

TaskTemplate 和 OwnerMemory 由 Mangrove 管理并编译为 Runtime 无关的 CompiledContext。
CoreMind 的 Session 或模板不能替代 TaskRevision；记忆不能覆盖来源证据、数据含义、权限、外发
确认或独立验证结论。

## User Stories

### 创建、来源与冻结

1. As an 普通用户, I want 从数据工作台提交公开网页任务, so that 我不需要切换到旧分析页面。
2. As an 普通用户, I want 数据工作台继续支持文件和预览, so that 网页能力不会牺牲现有功能。
3. As an 普通用户, I want 输入精确网址时默认只读取该页, so that 系统不会擅自爬完整网站。
4. As an 普通用户, I want 明确选择同站扩展范围和上限, so that 我知道系统可能访问哪些页面。
5. As an 普通用户, I want 跨站访问前重新确认, so that 一个链接不会变成全网授权。
6. As an 普通用户, I want 获取阶段看到成功页和失败页, so that 我能判断来源是否充分。
7. As an 普通用户, I want 获取失败时不产生空 TaskRevision 或 Run, so that 历史记录不会出现假任务。
8. As an 普通用户, I want 重复提交同一请求只产生一次获取与任务, so that 不会重复访问或计费。
9. As an 审计人员, I want Snapshot 记录页面身份、时间、哈希和范围, so that 结果可以追溯。
10. As an 平台维护者, I want Snapshot、Revision 和首个 Run 原子冻结, so that 中断不留下半状态。

### 目标、结果和缺口

11. As an 普通用户, I want 启动前看到系统理解的目标, so that 我可以纠正范围和数据含义。
12. As an 普通用户, I want 启动前修改建议输出格式, so that 系统不会完成后才决定文件类型。
13. As an 普通用户, I want 硬性“全部/N 项”未满足时看到已有结果, so that 有用证据不会被隐藏。
14. As an 普通用户, I want 部分结果明确标记缺口, so that 我不会把 9 家误认为完整 10 家。
15. As an 普通用户, I want 系统区分确认漏提、确认范围不足和无法判断, so that 结论不过度承诺。
16. As an 普通用户, I want 接受较少结果时创建新 Revision, so that 原始要求不会被静默篡改。
17. As an 普通用户, I want 探索任务可交付有证据结果和覆盖说明, so that 开放问题仍能产出价值。
18. As an 普通用户, I want 零有效来源时明确失败, so that 模型不会在没有来源时编造报告。

### 模型、Runtime 和恢复

19. As an 普通用户, I want 使用当前明确选择的模型, so that 系统不会替我换 Provider 或模型。
20. As an 普通用户, I want 外部模型调用前看到连接、模型、内容类别和用途, so that 本次外发是知情的。
21. As an 普通用户, I want Provider 暂时失败只在安全时同模型重试, so that 不会产生未知重复费用。
22. As an 普通用户, I want 请求结果未知时由我决定是否重试, so that 风险不会被系统代替接受。
23. As an 普通用户, I want 暂停和恢复继续同一 Run, so that 时间、Token、证据和结果身份连续。
24. As an 普通用户, I want 取消后工具和任务资源真正收敛, so that 后台不会继续工作或产生结果。
25. As an 平台维护者, I want 一个 Run 冻结一个 RuntimeBinding, so that 升级不会改变运行中语义。
26. As an 平台维护者, I want 必需 Runtime 能力缺失时失败关闭, so that Adapter 不会伪造支持。
27. As an 平台维护者, I want CoreMind 升级先通过契约套件和黄金重放, so that 上游迭代可以安全复用。
28. As an 平台维护者, I want 旧 Run 继续使用原版本恢复, so that 新版本不会改写历史任务。

### 工作记录、时间与用量

29. As an 普通用户, I want 工作记录默认折叠, so that 数据和结果保持主视觉焦点。
30. As an 普通用户, I want 展开后看到智能体做了什么、用了什么工具和依据摘要, so that 过程可检查。
31. As an 普通用户, I want 不看到内部逐字思维链, so that 界面只呈现可靠、可审计的信息。
32. As an 普通用户, I want 已恢复失败仍可追溯, so that 成功不会抹掉真实过程。
33. As an 普通用户, I want 未解决问题和需要我的动作始终醒目, so that 折叠不会隐藏阻塞。
34. As an 普通用户, I want 每个 WorkSession 有开始、暂停、恢复和结束时间, so that 我知道任务何时工作。
35. As an 普通用户, I want 实际工作时间和扫码等待分开, so that 等待我操作不会算成智能体工作。
36. As an 普通用户, I want Token 缺失时看到已知下限和未知次数, so that 用量不会被伪装成精确值。
37. As an 安全审计人员, I want 工作记录脱敏 Cookie、Secret、路径和命令, so that 可观测性不扩大泄漏面。

### 模板、记忆、验证与交付

38. As an 普通用户, I want 使用已有分析模板创建网页任务, so that 旧模板价值可以继续复用。
39. As an 普通用户, I want 应用模板前看到它会改变什么, so that 模板不能暗中扩大权限或范围。
40. As an 普通用户, I want 只使用与当前目标相关的个人记忆, so that 无关历史不会污染结果。
41. As an 普通用户, I want 查看、纠正和删除个人记忆, so that 记忆仍由我控制。
42. As an 普通用户, I want 自动任务与手动任务使用同一验证和 Delivery, so that 定时执行不产生旁路报告。
43. As an 普通用户, I want Agent 成功后仍由独立 Verifier 检查, so that 自报成功不能成为正式结果。
44. As an 普通用户, I want 验证通过后自动形成任务内 Delivery, so that 不需要重复确认保存。
45. As an 普通用户, I want 外部发送或公开分享仍单独确认, so that 内部交付不等于外部发布。
46. As an 普通用户, I want Publisher 失败时只恢复发布, so that 不会重新抓取、调用模型或重复文件。
47. As an 普通用户, I want 旧分析和历史报告在迁移期间仍可访问, so that 统一工作台不会丢失历史能力。
48. As an 产品验收人员, I want 从 8088 完成一条匿名网页正式 Delivery, so that 工程测试和真实体验分别被证明。

## Implementation Decisions

### 产品与前端

- `/data-prep` 是唯一新任务主入口；不新增平行网页报告页。
- 保留现有数据工作台信息架构、预览、任务列表和结果区，只增加来源类型和工作记录能力。
- 推荐视觉方向是“可信数据工作台”：来源、任务、证据和结果形成自然纵向节奏，工作记录内联
  折叠；不采用 conversation-first 全屏聊天布局。
- 复用现有组件、主题 Token、Tailwind 工具和可访问控件；不在规格阶段增加 UI 依赖。
- 工作记录使用语义化 disclosure/button，默认 `aria-expanded=false`；OwnerActionRequest 位于
  disclosure 外。
- 状态不只依赖颜色；焦点、键盘、窄屏、深浅主题和 reduced motion 都属于完成门。
- 图片参考只用于说明渐进披露交互，不是像素级完成目标。

### 来源获取与事务

- SourceAcquisitionAttempt 是任务前置事实，绑定 Owner、幂等键、来源范围、开始/结束时间和
  结果，不冒充 Run。
- SourceSnapshot 代表一个逻辑获取批次，页面是独立 Artifact；证据定位到页面内容单元。
- 跳转后的最终 URL 必须仍在获准范围；跨域跳转按未获准处理。
- 获取超时、DNS/网络错误、非 HTML、过大内容、robots/站点拒绝和解析失败分别记录，不用一个
  模糊“抓取失败”覆盖。
- 明确全量/数量要求在 SourceCompletenessGate 前冻结；通过后才创建 TaskRevision。
- TaskRevision、GoalContract、DeliverySpec、RuntimeBinding 和 Run 必须在同一聚合事务中形成。
- 重新获取最新网页是 SourceRefresh，产生新 Snapshot 和 Revision；普通 retry/resume 只读旧
  Snapshot。

### Runtime 与 CoreMind Adapter

- 复用既有 AgentKernel `start/resume/steer/cancel` 语义，并补齐事件与 Projection 查询接缝；
  不让路由或业务 Store 直接调用 CoreMind SDK。
- CoreMind Adapter 将 TaskRevision、CompiledContext、Tool Catalog、预算和模型 Grant 转换为
  Runtime 请求，将结果转换为 Mangrove Candidate。
- Adapter 必须生成 AgentKernelCapabilityManifest，并在 Run 创建前验证必需能力。
- RuntimeBinding 保存精确内核/Adapter/协议/能力身份；不得保存浮动 `latest` 或 `main`。
- 用户于 2026-08-27 补充确认：本轮 CoreMind 兼容性判断以其本机源码仓库的精确 commit 为
  候选，不以官方稳定包、PyPI 或全局旧包为权威。候选仍必须冻结源码与 Runtime Artifact
  digest、协议 Schema 和依赖；当前 PiRuntime 继续承担兼容路径，直到该精确候选通过独立门。
- 用户于 2026-08-27 确认每个 Mangrove Run 绑定一个隔离的 CoreMind Worker/Sidecar，Mangrove
  负责 Worker 生命周期与 Owner 隔离；不得让一个 Worker 承载多个并发 Run。
- Prototype 确认 v2 尚缺 Checkpoint 操作与 Python callable 动态工具桥。这两项优先作为通用
  合同在 CoreMind Protocol v2 补齐；Mangrove 只维护薄 Adapter，不建立长期私有协议分叉。
- 运行中禁止 CoreMind→Pi、Pi→CoreMind 或跨模型静默 Failover。
- CoreMind 的 Trace、RunMetrics 和 Session 是 Runtime 事实，不替代 Mangrove TaskRevision、
  ProviderUsage、WorkSession、VerificationReport 或 Delivery。
- CoreMind 依赖安装、Node worker、容器布局、版本升级和许可证/供应链检查在实施工单中单独
  展示；本规格不安装。

### 事件、会话和用量

- StructuredProgressEvent 至少增加 `purpose`、脱敏输入摘要、`duration_ms`、结果摘要、
  EvidenceRef、恢复状态和受众；不能只保存工具名和失败布尔值。
- Runtime 原始事件在 Adapter 内归一化、排序和脱敏；未知必需事件失败关闭，未知可选事件记录
  兼容缺口但不展示原文。
- WorkSession 生命周期由 Run 权威状态派生，显式保存开始、暂停、恢复、结束和等待区间。
- ProviderUsage 查询补齐 Run、Purpose、时间和未知调用语义；WorkSessionUsage 按同一 Run
  聚合执行、验证、修复和重试调用。
- 工具行动只计行动数和耗时，不虚构 Token。Provider 原生 Usage 缺失时保持 unknown。
- 刷新、SSE 重连和历史任务打开必须从持久事实恢复同一折叠摘要与展开记录。

### 模板、记忆和对话

- TaskTemplateCatalog 对模板做 Owner/平台作用域、版本、状态和来源管理；旧 Prompt 模板通过
  Adapter 生成 Runtime 无关草案，不直接写 TaskRevision。
- OwnerMemory 按 Owner、Purpose、相关性和来源检索；禁止把全部 `user_memory` 无差别拼入
  Prompt。
- ConversationSteering 继续保存 RawUserTurn、ContextDelta 和 RevisionProposal；状态追问不
  创建 Revision，实质变化由用户确认。
- 旧 Conductor lessons/templates 的自动学习不得直接成为跨用户生效内容；迁移需要治理、回放
  和回滚证据。
- Scheduler 未来只调用统一任务生命周期，不复制网页获取、Runtime、Verifier 或 Publisher。

### 验证、发布和权限

- Verifier 与 AgentKernel 使用不同 Purpose Grant；不能复用 Agent 自己的“验证成功”事件。
- 内部 TaskLocalPublication 在验证通过后自动执行，外部发送/写入/公开仍由用户控制。
- Publisher 按稳定 PublicationKey 幂等恢复，失败不会触发来源、Runtime 或模型重跑。
- 普通用户只能读取自己的来源、运行、用量、工作记录和 Delivery。
- 管理员跨 Owner 默认只看任务管理元数据；正文仍经 AuditView 原因与不可变审计记录。

### 变更面与迁移

- 第一纵切片只迁移匿名公开网页，不同时接入认证网页、通用 HTTP API 或数据库。
- 只深化完成本纵切片所需的 Semantic Workspace Module 接缝，不全量重写路由、Store 或页面。
- 旧入口保持兼容，不删除旧表、旧会话、旧 Delivery 或历史文件。
- 新 Schema 只能经 `src/database_migrations` 显式迁移；startup 和 Repository 不得补建 DDL。
- 实施工单必须冻结精确文件允许列表，避开用户与其他任务现有改动。

## Testing Decisions

### 最高层测试接缝

主要后端测试从现有 Semantic Workspace 任务 API 发起，使用迁移后的临时 SQLite、受控网页
Source Adapter、Fake AgentKernel/CoreMind Adapter 和 Fake Provider。测试观察 API、数据库
聚合、事件、Candidate、Verifier、Publisher 和正式 Delivery，不断言私有函数或 CoreMind
内部实现。

主要前端测试从 `/data-prep` 发起，使用 Playwright 覆盖状态矩阵；实现完成后再经独立授权从
8088 做一条真实匿名网页闭环。组件或纯函数测试只补状态机、用量格式化和脱敏等紧反馈，不
替代浏览器流程。

### API、事务与领域接缝

- 精确 URL 默认只获取一页；未授权同站/跨站链接不访问。
- 明确同站范围和上限只产生获准页面；越界跳转被拒绝并记录。
- 多页面批次形成一个 SourceSnapshot、多个 Artifact 和明确失败列表。
- 零有效页、硬性完整性不足和开放覆盖未知分别进入正确状态。
- 获取失败时零 TaskRevision、零 Run、零 Candidate、零 Delivery。
- 获取成功后 Snapshot、TaskRevision 1、DeliverySpec、RuntimeBinding 和 Run 原子形成。
- 同幂等键重复、并发重复和结果未知重连都不重复访问、任务或用量。
- SourceRefresh 形成新 Snapshot/Revision；resume/retry 继续旧 Snapshot/Run。
- 硬性 10 项只找到有证据 9 项：保留 PartialCandidate，原 Revision 零 Delivery。
- 用户接受 9 项后创建新 Revision，旧 Revision 和 PartialCandidate 不变。
- 探索目标 9 项可发布，但 Delivery 披露范围、失败和覆盖未知。
- 确认漏提触发同 Run 有界修复；确认范围不足和无法判断不自动假装修复。
- Candidate 必须经独立 Verifier；Agent/Runtime 成功不能直接发布。
- Publisher 中断/失败重试只恢复发布，零来源/模型/Agent 重跑，且只有一个 Delivery。
- Owner A 不能读取、恢复、取消、重试、下载或复用 Owner B 的任何事实。

### Runtime Adapter 契约套件

同一套测试必须能够运行在 Fake Adapter、现有 PiRuntime Adapter 和候选 CoreMind Adapter 上：

- `start/resume/steer/cancel/events/query` 能力声明与实际行为一致；
- RuntimeBinding 精确冻结版本、协议、外部 Run ID、事件 Schema 和 capability digest；
- 事件具有稳定顺序、事件 ID、时间戳、Run/turn/tool 关联和可重连游标或等价投影；
- 同一 Run 暂停/恢复不重复消费输入，不丢失已完成工具事实；
- cancel 返回前达到约定静止状态，迟到事件不能改写终态；
- 安全可重试、结果未知和不可重放副作用分别进入正确状态；
- 工具审批、拒绝、Checkpoint、恢复和副作用收据失败关闭；
- Provider 原生 input/output/cache/total Token 正确映射，缺失 Usage 保持 unknown；
- Trace 脱敏 Secret、Cookie、认证头、系统 Prompt、路径和敏感工具参数；
- 未知事件、缺少必需能力、协议不兼容和损坏 Projection 不被当成成功；
- 上游升级候选与当前固定版本运行同一黄金任务，比较终态、事件、Candidate、资源清理和恢复；
- 合同测试使用 Mock Provider，不默认调用真实模型或产生费用。

### 浏览器与无障碍接缝

- `/data-prep` 现有文件、来源、预览和结果功能无回归。
- 匿名网页来源可以输入精确 URL、选择同站范围/上限并看到最终范围预览。
- 获取中、获取失败、硬性不足、探索继续、运行、暂停、验证、发布和完成状态可区分。
- 工作记录初始折叠；点击、Enter 和 Space 可展开，`aria-expanded` 与内容一致。
- 展开记录显示时间、行动、工具、判断摘要、耗时、结果、证据和恢复失败。
- 不渲染原始思维链、Cookie、Secret、宿主路径、完整命令或原始大日志。
- OwnerActionRequest 在折叠区外保持可见、可聚焦并说明原因和影响。
- WorkSession 显示开始/结束、实际工作/等待、行动、调用和 Token；unknown 文案准确。
- 浏览器刷新和 SSE 重连后恢复同一任务、折叠摘要和未处理用户动作。
- Candidate 与正式 Delivery 视觉和操作明确区分；只有 Delivery 可以正式预览/下载。
- 键盘、焦点、对比度、屏幕阅读器名称、窄屏、深浅主题和 reduced motion 通过检查。
- 视觉验收以现有工作台品牌和内容层级为准，不以参考图片像素复刻为准。

### 基线、回归与审查

- 先写当前实现会失败的 API/Adapter/浏览器测试，再做最小实现。
- 运行相关 Python 测试、前端 TypeScript、正式构建、定向 Playwright 和 axe。
- 按实际差异运行与风险相称的完整后端和前端回归，核对根命令退出码与测试端口。
- 若安装 CoreMind，先展示版本、依赖、锁文件、许可证、供应链和运行时差异并取得确认；再在
  隔离环境执行干净安装与契约套件。
- 运行 Standards + Spec 双轴 code-review；阻断问题清零后才进入真实产品验收。
- 检查 UTF-8、乱码、迁移版本、Owner 隔离、Secret 扫描、diff whitespace 和精确文件列表。
- 自动测试绿色只构成 ENGINEERING_VERIFIED，不构成真实网页、真实 Provider、生产数据库、
  用户验收或发布资格。

### 真实匿名网页闭环

真实闭环是独立授权门：

1. 用户确认公开网页、精确访问范围、当前模型/连接、外发内容、用途和潜在费用；
2. 从 8088 数据工作台创建匿名网页任务；
3. 核对 SourceSnapshot、TaskRevision、RuntimeBinding 与 WorkSession；
4. 观察默认折叠工作记录，并展开核对工具、时间、Token、证据和脱敏；
5. 形成 Candidate，独立 Verifier 通过后由 Publisher 形成正式 Delivery；
6. 从普通用户界面预览和下载；
7. 核对 ProviderUsage、未知用量、幂等、Owner 隔离、资源清理和零历史改写；
8. 再执行一条不足/未知覆盖场景，确认部分结果不会冒充完整 Delivery；
9. 由用户明确给出 LIVE_ACCEPTED 或提出整改。

## Out of Scope

- #84 的认证网页、Cookie 失效监控、扫码登录和 AuthenticatedSourceSession 完整实现；本规格只
  保留同一 Run 的暂停/恢复与 OwnerActionRequest 接缝。
- 通用 HTTP API、数据库、远程 MCP、对象存储、多媒体或全网搜索来源接入。
- 一次性迁移所有模板、记忆、自动任务、反馈和历史会话；本规格冻结迁移合同和首片兼容要求。
- 删除 `/chat`、“分析报告（旧）”、Legacy Conductor、旧任务、旧 Delivery 或历史审计记录。
- 全量重构 Semantic Workspace、路由、Repository、Worker、前端状态管理或视觉系统。
- 让 CoreMind 接管 Mangrove TaskRevision、模型选择、来源授权、模板/记忆、Verifier 或 Publisher。
- 跟随 CoreMind `main`、使用浮动版本、自动升级、运行中切换 Runtime 或静默 Failover。
- 把 CoreMind `releaseReadiness`、测试通过或 Agent 自评当作 Mangrove 正式 Delivery 资格。
- 新增 Provider、改变用户当前模型、调用未确认的真实外部模型或估算 Provider 账单。
- 外部发送、业务系统写入、公开分享、生产发布或扩大普通用户平台能力受众。
- 生产数据库迁移、Secret/Cookie/Vault Key 轮换、旧备份处置或不可逆数据清理。
- 创建分支、提交、推送、PR、标签、Release、部署或修改 GitHub About。

## Further Notes

### 已验证事实

- `/data-prep` 当前默认进入 Semantic Workspace，保留 `?legacy=1` 兼容入口。
- `/chat` 当前同时提供 `data_prep` 与“分析报告（旧）”模式，分别调用 Data Prep graph 和
  Conductor graph。
- 数据工作台当前通过 Mangrove `PiRuntime` 使用任务级 Docker、JSONL/RPC 和固定
  `pi-coding-agent 0.80.10`，并拥有恢复、取消、Candidate 与 Verifier 接线。
- ConversationSteering 已有 RawUserTurn、ContextDelta、RevisionProposal 和结构化进度事件。
- ProviderUsage 已保存任务/修订/Run/连接/Purpose 与原生用量，但现有工作台投影尚未完整显示
  Run、时间、unknown 和 WorkSession 汇总。
- 旧分析链存在模板、个人记忆、学习经验、自动任务、反馈和报告入口；它们尚未统一为数据工作台
  共享 Module。
- 用户指定的本机 CoreMind 候选为
  `codex/issue-73-child-runs@7b7da43c66f594c0c43239d28439fd1cfa1d07b5`。源码 manifest 为
  0.3.2，但本机交接明确说明代码同时包含 Protocol v2、0.4.x Runtime 和 Experimental Child
  Run 能力，因此不能只用语义版本代表实际 Runtime 身份。
- GitHub Issue #83 当前仍为 OPEN、无评论、标签 `wayfinder:grilling`；本地规格尚未发布。

### 基于代码的推断

- Mangrove 与 CoreMind 共享 Pi 技术血缘，但版本、工具、容器、协议和会话语义不同，不能直接
  替换而不做 Adapter 契约验证。
- 现有 `AgentKernel`、Semantic Workspace API、临时迁移数据库测试和 Playwright 已提供足够高
  的测试接缝，不需要为首片建立第二套 API 或 UI。
- CoreMind 的聚合 RunMetrics 不能替代 Mangrove 按 Owner、连接、Run 和 Purpose 保存的
  ProviderUsage。
- 把模板和记忆编译为 Runtime 无关 CompiledContext，可以同时服务手动任务、自动任务和未来
  Runtime，而不让它们绕过 TaskRevision。
- #83 应证明匿名网页纵切片；P1 全部能力迁移需要在 #84、#85、#86、#87、#88 及后续实施工单
  中按共享架构继续，不能膨胀为一次性重写。

### 尚未验证的建议

- 本机 CoreMind 的 Python SDK + Protocol v2 已确认缺少 v2 Checkpoint 操作和 v2 Python
  callable 动态工具注册；这些接缝以及取消静止、事件脱敏、容器隔离和 Mangrove 黄金任务仍需
  由精确 Adapter 契约套件确定。
- CoreMind 应作为 Python SDK Node worker、独立 Sidecar 还是任务容器内服务接入，需以取消、
  Secret、网络、进程树和资源清理证据选择。
- AgentWorkTrace 的最终信息密度、移动端位置和两层技术详情需要 #88 原型和真实浏览器验证。
- 旧模板、个人记忆和 Conductor lessons 的实际数据质量、跨用户边界与迁移量尚未盘点完成。
- 真实匿名网页样例、当前模型、允许外发内容和可接受费用尚未由用户选择。

### 后续人工决策与授权门

1. 用户确认本规格后，才能结束 `to-spec` 阶段；不会自动进入 `to-tickets`。
2. 是否把本规格和 ADR 摘要回写 GitHub #83、调整标签或新建架构 Issue，需要独立授权。
3. CoreMind prototype 已按用户确认使用本机精确源码候选；运行形态、跨仓修改和依赖接入仍需
   在原型结论后独立确认。
4. 是否进入 `to-tickets` 拆分 P1-01 实施工单，需要用户显式调用或确认。
5. 任何新增/升级依赖必须先展示用途、收益、精确版本、锁文件、安全和回滚方式。
6. 真实网页访问、真实 Provider、连接、模型、公开内容、费用和生产写入分别需要明确授权。
7. 认证来源扫码、保存 SourceConnection、外部发布、权限扩大和不可逆操作由用户控制。
8. 分支、提交、推送、PR、合并、标签、Release、部署和公共仓库入口修改分别需要授权。

### 完成定义

- **ARCHITECTURE_ACCEPTED**：ADR-0035 的统一工作台与 CoreMind Adapter 方向已由用户确认。
- **SPEC_APPROVED**：用户确认本规格、纵切片边界、测试接缝和人工门。
- **RUNTIME_COMPATIBLE**：精确 CoreMind 候选通过隔离契约套件；不等于安装或生产切换。
- **IMPLEMENTED**：约定代码和迁移存在，未声称测试、真实网页或用户验收。
- **ENGINEERING_VERIFIED**：定向与相称回归、构建、浏览器检查和双轴审查通过。
- **LIVE_ACCEPTED**：用户授权并完成匿名网页正式 Delivery 与覆盖不足代表场景。
- **RELEASED**：经独立授权完成相应合并、发布或部署；本规格不自动授权。
