# Agentic 能力获取、SOP 与对话上下文任务拆分

> 日期：2026-08-02
> 阶段：AC-00～AC-05 已完成工程实现；AC-06 用户灰度验收通过；AC-07 #33 已关闭，#34 工程验证通过
> 状态：`ac07_02_production_migrated_pending_user_acceptance`
> 上游规格：[能力获取、SOP 与对话上下文规格](2026-08-02-agentic-capability-sop-context-spec.md)
> 架构决策：[ADR-0026](../adr/0026-agentic-capability-acquisition-and-procedure-governance.md)、
> [ADR-0027](../adr/0027-conversation-steering-and-context-compilation.md)
> 开源调研：[能力获取开源组件调研](../research/2026-08-02-agentic-capability-acquisition-open-source-research.md)
> 本任务图自身不授权实施或发布；后续用户已单独授权 #33 完整收口及 #34 工程实现和生产迁移。
> #34 用户灰度、提交推送与关闭，以及真实依赖下载、平台发布、普通用户开放、默认切换、
> 版本、标签和外部发布仍未授权。

> AC-00～AC-03 证据：
> [对话转向与渐进式进度执行报告](2026-08-02-conversation-steering-ac00-ac03-execution-report.md)。
> AC-04 证据：
> [CapabilityCatalog 执行报告](2026-08-02-agentic-capability-ac04-execution-report.md)。
> AC-05 证据：
> [能力获取状态机与共享缓存执行报告](2026-08-02-agentic-capability-ac05-execution-report.md)。
> AC-06 本地纵切面证据：
> [本地真实 Adapter 执行报告](2026-08-04-agentic-capability-ac06-local-adapters-execution-report.md)。

## 1. 当前事实与边界

### 已验证事实

- 内置 `CapabilityRegistry`、`CapabilityManifest`、DependencyBundle 概念、Pi Extension、
  任务 Docker、Smokescreen、Owner 校验、TaskRevision 和 SSE 已存在；
- `dependency_acquisition` 策略存在，但没有独立获取状态机和 `PiRuntime` 主链接入；
- 旧 Skill/模板是全局可变文件，没有个人所有权、不可变版本或平台发布快照；
- 当前工作台不能区分状态追问与修改要求，revision 仍采用字符串追加；
- 当前任务进度按固定阶段压缩，不能展示能力获取和完整结构化事件；
- vNext 正式 Publisher 尚未接通，任何本专项结果都不能冒充正式 Delivery。

### 基于代码的推断

- 复用现有 Repository、Owner 校验、Pi Extension 与 Egress Controller 比另建插件平台更深；
- 新目录必须与 Legacy 模板/Skill 分开，再用 Adapter 显式导入；
- 对话转向、能力获取和方案学习应分别形成可测试 Seam，不能全部堆入
  `SemanticWorkspaceManager` 或 `PiRuntime`。

### 尚未验证的建议

- 本地内容寻址仓库与 OCI/ORAS 导入导出如何分工；
- Python/Node/CLI/MCP 四类 Adapter 的真实性能；
- 自动化方案是否采用一级导航；
- 默认资源预算和验证过期周期。

## 2. 测试 Seam

| Seam | Interface | 外部可观察结果 |
|---|---|---|
| S1 对话转向 | `handle_turn(request)` | 即时回答、自动转写、revision、新任务或权限请求 |
| S2 上下文编译 | `compile(request)` | 有界 ContextRef、组成清单、token 统计和保真门 |
| S3 能力解析 | `resolve(need)` | Owner 可见且权限匹配的冻结选择或能力缺口 |
| S4 能力获取 | `acquire/cancel` | 无来源联网、预算、digest、验证结果和完整清理 |
| S5 方案学习 | `propose/validate/publish` | 个人草稿、验证版本、独立平台快照 |
| S6 进度投影 | `project(events, audience)` | 普通用户/管理员差异化且可恢复的视图 |
| S7 产品 Interface | 工作台与自动化方案 HTTP/SSE | Owner/角色、revision、选择、取消和审核 |
| S8 浏览器体验 | `/data-prep` 与自动化方案入口 | 用户不迷路、可追问、可展开、无 Emoji |

测试只穿过这些 Seam，不断言缓存目录、LLM Prompt 文字、包管理命令或内部 Adapter 调用顺序。

## 3. 依赖图

```text
AC-00 契约与冻结夹具
  ├─→ AC-01 非破坏性追问与语义转写
  │     └─→ AC-02 Revision 差异门与安全点切换
  │             └─→ AC-03 结构化事件与渐进式进度
  └─→ AC-04 能力目录、不可变版本与作用域
        └─→ AC-05 隔离获取状态机与共享缓存
              └─→ AC-06 Tool/MCP/Skill 真实 Adapter
                    └─→ AC-07 验证、成熟、升级与平台发布
                          └─→ AC-08 SOP 学习、组合与选择
                                └─→ AC-09 自动化方案与审核 UX

AC-03 + AC-06 + AC-08 + AC-09
  └─→ AC-10 安全、性能、恢复、正式交付边界与用户验收
```

AC-01–AC-03 与 AC-04–AC-06 可以在 AC-00 后分别开发，但 AC-10 必须等待全部前置通过。

## 4. 工单总览

| 工单 | 独立价值 | 主要完成证据 | 人工控制点 |
|---|---|---|---|
| AC-00 | 冻结契约与评测语料 | Schema、迁移草案、内存 Adapter、冻结夹具 | 字段语义和权限变化 |
| AC-01 | 运行中可问进度且不重跑 | 状态/依据追问、RawTurn/Delta、真实 LLM 语料 | 外部模型转写 |
| AC-02 | 修改要求安全形成 revision | 语义差异门、安全点、恢复和回退 | 业务范围、权限、外发 |
| AC-03 | 用户持续感知真实执行 | 事件信封、普通/管理员投影、SSE/刷新 | 新确认步骤和导航 |
| AC-04 | 个人/平台能力正确隔离 | 不可变版本、digest、Owner/角色矩阵 | 数据迁移和平台导入 |
| AC-05 | Pi 可安全获取并复用能力 | 无来源联网、预算、缓存命中、取消清理 | 来源/预算扩大 |
| AC-06 | 工具/MCP/Skill 真正可用 | 四类真实 Adapter、健康检查、任务装载 | 新网络和 Secret |
| AC-07 | 单次成功不会污染平台 | 合成/真实/失败门、升级、平台快照 | 管理员发布/弃用 |
| AC-08 | 成功经验形成可组合 SOP | 草稿、选择、组合、失败候选、无业务泄露 | 方案含义和平台共享 |
| AC-09 | 普通用户和管理员都不迷路 | 方案库、审核、引导、可访问性 | 一级导航最终形态 |
| AC-10 | 形成可接受的端到端闭环 | 安全/恢复/性能/真实任务/用户验收 | 实现发布和阶段切换 |

## 5. 详细工单

### AC-00：契约、Schema 与冻结夹具

目标：先冻结业务语义、Interface 和验证真值，不先安装任何工具。

Red：

1. 现有模型不能表达 CapabilityPack 版本、个人/平台作用域、SOP 版本和获取预算；
2. 现有 revision 只有追加文本，没有 RawUserTurn、ContextDelta 和 revision 草案；
3. 旧模板状态把成熟度和共享范围混在全局文件里。

Green：

- 建立 CapabilityPack、AutomationProcedure、AcquisitionRun、RawUserTurn、ContextDelta、
  RevisionProposal 和 StructuredProgressEvent 的严格契约；
- 成熟度与作用域正交，个人版本必须有 Owner，平台版本必须无个人任务引用；
- 为 S1–S6 提供内存 Adapter，测试与生产调用相同 Interface；
- 冻结至少 24 条对话转写语料和 12 条能力选择/权限语料，预期值由规格人工给出；
- 形成前向幂等、可空且不删除旧数据的迁移草案，但本工单不执行生产迁移。

完成证据：契约序列化、非法状态、Owner/作用域、不变量和迁移 dry-run 测试。任何字段含义、
默认权限或平台发布语义改变必须暂停确认。

### AC-01：非破坏性追问与 LLM 语义转写

用户场景：任务运行中询问“现在做到哪了？”、“为什么使用这个 OCR？”或使用不专业表达
“上次那些也算上，这次只要王总的，做成表格”。

Red：

1. 运行中没有任意追问入口；
2. 当前 revision 接口会取消任务并拼接字符串；
3. 只保存改写 Prompt 无法追溯用户原话。

Green：

- 新增 `ConversationSteering.handle_turn`，先保存不可变 RawUserTurn；
- LLM 生成带来源、继承 revision、置信状态和开放问题的 ContextDelta；
- 状态/依据追问只读持久事件、能力选择和证据摘要，Run/revision 保持不变；
- 高置信度无实质变化转写自动应用并展示“我理解为”，提供纠正入口；
- 转写模型遵循当前用户模型选择与外发确认；本地失败不得静默切外部 Provider；
- 保存最终 SteeringResult，刷新后不重复处理相同 turn idempotency key。

完成证据：24 条冻结语料连续 3 次语义等价；状态追问 Run ID 不变；原话/转写/结果可追溯；
跨用户 Turn 读取全部拒绝。若要新增外部模型做转写必须取得用户确认。

### AC-02：SemanticDiffGate、Revision 草案与安全点切换

用户场景：运行中要求“增加部门和人民币大写金额”或“再处理另一份合同”。

Red：

1. 当前后端只能立即取消后创建新 revision；
2. LLM 可能把范围变化误判为语言润色；
3. 工具执行中途切换会产生半写候选。

Green：

- SemanticDiffGate 检查来源、范围、基数、字段/计算语义、输出、权限、外发和不可逆动作；
- 实质变化只生成 RevisionProposal，不自动修改 TaskRevision；
- 用户确认时选择“立即取消”“当前原子步骤结束后切换”或“创建独立任务”；
- 安全点结束前保持旧 revision，确认结果与选择持久化并可恢复；
- V2/V3 只复用不冲突的来源快照、发现索引和 EvidenceRef，不机械重跑全部阶段；
- 拒绝或过期草案不影响当前 Run。

完成证据：状态追问、字段新增、范围扩大、新来源、外发、删除动作和独立新目标矩阵；进程重启
后草案与安全点恢复；半写候选为零。业务含义或权限默认改变必须人工确认。

### AC-03：结构化事件、ProgressProjection 与渐进式工作台

目标：把当前固定阶段最后摘要升级为可恢复的行动事件流，同时保持旧客户端可读。

Red：

1. 能力搜索、下载、验证和 SOP 选择没有统一事件；
2. 相同原始事件无法形成普通用户和管理员的不同安全视图；
3. 未知总量容易被 UI 伪装成百分比；
4. 当前输入框的语义只表示“创建新版本”，运行中追问不清晰。

Green：

- 新事件信封包含 revision/run、阶段、事件类型、摘要、可选进度、引用、操作和受众；
- ProgressProjection 从同一事实事件生成用户视图与管理员视图；
- 顶层统一为理解、检查来源、准备能力、执行、验证、交付，内部子事件渐进披露；
- 只有 `total` 已知才显示比例；刷新/SSE 重连恢复唯一活动阶段；
- 对话内紧凑任务卡持续更新，展开显示事件，输入框始终可用；
- 回执明确区分“不影响任务、revision 草案、新任务建议、权限请求”；
- 不展示原始模型思考、Token、宿主路径、控制台全文或 Emoji。

完成证据：事件投影单测，旧事件兼容，SSE 断线/乱序/重复，普通/管理员快照，运行中追问、
reduced-motion、键盘、axe、深浅主题和 1366 宽度 Playwright。

### AC-04：CapabilityCatalog、不可变版本与作用域

状态：`engineering_verified_production_migrated`；目录路径已随 AC-06 灰度纳入用户验收。
AC-07 #33 治理迁移也已完成，验证晋级和平台发布仍按依赖图留在后续工单。

目标：建立与 Legacy 模板分离的能力目录，冻结个人/平台可见性和 TaskRevision 引用。

Red：

1. 当前固定注册表不能保存动态版本；
2. 旧 Skill/模板没有 Owner，无法安全共享；
3. 任务只记录能力名称时，目录升级会改变历史行为。

Green：

- 建立 CapabilityPack/Version/Component、Procedure/Version/Validation 和 Selection Repository；
- 版本内容寻址且不可变，TaskRevision 冻结 ID、version 和 digest；
- 个人查询强制 `owner_id + id`，平台查询只返回管理员已发布版本；
- 管理员治理平台元数据不自动获得个人业务制品读取权；
- 内置 CapabilityManifest 通过 Adapter 映射为平台内置版本；
- 能力内容按 OCI digest 冻结，单机首版使用 ORAS OCI Image Layout；Mangrove 数据库继续
  负责 Owner、scope、maturity、审核和审计；
- Legacy Skill/模板只提供显式导入草稿动作，不自动迁移或公开。

完成证据：并发创建、digest 去重、不可变写、Owner/管理员/超管矩阵、历史版本冻结和旧数据
零改写。AC-06 目录迁移与 AC-07 #33 治理迁移均已在后续独立授权下完成并保留备份。

### AC-05：独立能力获取状态机与共享缓存

目标：让 Pi 在不接触用户来源和业务 Secret 的环境中获取能力，并在后续任务复用。

Red：

1. `EgressPolicy.for_dependency_acquisition` 没有 Runtime 编排调用；
2. 业务阶段缺依赖只能失败或违规联网安装；
3. 没有预算、取消、内容寻址和残留资源门。

Green：

- 实现 `CapabilityAcquisition.acquire/cancel` 深 Module，不把来源路径或 Provider Key 放入请求；
- 状态覆盖发现、等待权限、获取、构建、验证、就绪、失败和取消；
- 获取环境使用 dependency Egress，明确断言来源未挂载、Secret 未注入；
- 使用 BuildKit 隔离构建并复用锁文件/构建层缓存；BuildKit cache 只优化速度，不作为信任证据；
- 官方/登记来源自动，陌生 URL 进入权限请求；重定向最终地址再次校验；
- 时间、下载、解包、候选、重试和并发预算失败关闭；
- digest 命中复用共享缓存；取消和失败清理 Lease、进程和临时目录，不发布半成品；
- ORAS 只写不可变 OCI digest；单机 OCI Layout 写入先串行，必须实测 Windows 路径和并发行为；
- 业务阶段只读装载冻结包，不能重新打开公共依赖网络。

完成证据：真实小包首次获取与二次零下载、篡改、重定向、预算、取消、服务重启、并发去重、
无来源/无 Secret 扫描和残留资源为零。具体来源和预算扩大需确认。

### AC-06：Python、Node、CLI 与 MCP Adapter

目标：证明统一 CapabilityPack Interface 能隐藏不同运行机制，而不是只支持一个演示包。

Red：

1. 包管理器、二进制、MCP 和 Skill 的安装/健康语义不同；
2. 每工具一容器会造成不必要冷启动；
3. 远程 MCP 需要 Secret 与外发治理。

Green：

- Python Adapter 使用 uv、`uv.lock` 和 frozen sync 固定解释器与独立依赖环境；
- Node Adapter 使用 npm、`package-lock.json` 和 `npm ci`，禁止生命周期脚本绕过声明权限；
- CLI Adapter 固定官方归档/Release、平台架构和 digest；
- 本地 MCP Adapter 在任务期间启动一次、健康检查、超时、取消并回收；
- 远程 MCP 只保存 ConnectionRef/SecretRef，逐任务确认外发内容和精确目标；
- Skill Adapter 遵循 Agent Skills 目录并用 `skills-ref validate` 校验；脚本与可执行工具使用
  同一安全门；
- MCP 官方 Registry 只作为 DiscoveryFeed；候选必须同步到 Mangrove 私有目录并冻结底层
  package/image 版本，不能从 Registry 直接执行；
- 一个任务的多个原生能力共用一个 Capability Host Sidecar，不为每个普通工具启动容器；
  Sidecar 不挂载业务来源、模型配置或 Docker Socket，Pi 只持有任务级短期 Relay。

完成证据：每类至少一项成熟开源真实样本，冷/热调用时间、磁盘、缓存、健康失败、取消和恶意
权限请求；不得用全 Mock 代替真实 Adapter。新增外部 MCP、网络或 Key 必须确认。

当前结果：Python、Node、CLI、无脚本 Skill 与 legacy/modern stdio MCP 已通过真实样例；
任务级单 Capability Host Sidecar、Pi Bridge、恢复、取消、Docker 超时、清理失败关闭和
零残留已完成工程验证。2026-08-06 又完成管理员能力目录、工作台选择、TaskRevision 不可变
冻结/继承和 OCI 安全展开，并让 Python Tool 与 Everything MCP 从冻结 OCI 包进入同一
Sidecar 真实调用。开关默认关闭；用户已确认工作台灰度验收通过。AC-05 自动获取→选择仍未贯通，
远程 MCP 未启用。证据见
[AC-06 执行报告](2026-08-04-agentic-capability-ac06-local-adapters-execution-report.md) 和
[ADR-0028](../adr/0028-task-level-capability-host-sidecar.md)。

### AC-07：验证、成熟度、版本升级与平台发布

状态：`ac07_03_engineering_verified_pending_code_review_production_migration_and_user_acceptance`。决策见
[ADR-0029](../adr/0029-capability-validation-lifecycle-and-platform-publication.md)，可实施规格见
[AC-07 增量规格](2026-08-06-agentic-capability-ac07-spec.md)，远端规格为
[GitHub Issue #32](https://github.com/Eclipseic1848/Mangrove_platform/issues/32)。纵向实施票已按依赖
建立为 #33～#44；#33 已完成工程实现、code-review 修复、用户验收、带备份生产迁移并关闭。
#34 已完成工程实现、双轴审查和带备份生产迁移，真实能力灰度闭环仍待完成；#35 已完成固定
Trivy/Syft、真实最终目录扫描、双格式 SBOM、持久化证据与脱敏摘要的工程实现，等待 code-review、
生产迁移和用户验收；#36 仍未开始。尚未生成项目签名密钥或发布平台能力。

实施票依赖图：

- #33 三轴治理投影与兼容读取：完成并关闭，功能提交 `4dd40e9d`；
- #34 可恢复 ValidationRun：工程验证和生产迁移完成，等待用户灰度、提交推送和 Issue 关闭；
- #35 Trivy/Syft 证据闭环：工程实现与真实双包扫描完成，等待 code-review、生产迁移和用户验收；
- #36 Cosign 本地 OCI 签名路径 PoC：无阻塞；
- #37 个人能力晋级 verified：blocked by #34、#35；
- #38 管理员审核与审计查看：blocked by #34；
- #39 平台快照、签名与 admin_gray 发布：blocked by #36、#37、#38；
- #40 运行时治理门：blocked by #39；
- #41 生命周期与限期风险接受：blocked by #35、#40；
- #42 Python Tool 真实纵切面、#43 Everything MCP 真实纵切面：blocked by #41；
- #44 AC-06 兼容切换与综合验收：blocked by #42、#43。

#33 已新增只追加精确 digest 治理事件、Legacy 只读兼容、显式备份迁移、Actor 投影、认证 API
与管理员只读设置；code-review 后补齐精确 PackRef 解析、迁移请求安全重放和权限注释。完整后端
1227 passed/4 skipped，既有完整 Playwright 54 passed、生产构建通过；
用户于 2026-08-07 在 8088 验收通过并授权完成生产迁移；GitHub Issue #33 已关闭。证据见
[AC-07-01 执行报告](2026-08-06-agentic-capability-ac07-01-execution-report.md)。

#34 已新增精确 digest/TaskRevision 绑定、Owner 隔离、持久化幂等与 Lease、真实 Pi 重放、独立
Verifier、取消/恢复、失败关闭清理和设置页验证入口；全仓后端 1243 passed/4 skipped、完整
Playwright 54 passed、生产构建与双轴 code-review 通过。生产迁移已带备份完成，用户灰度尚未执行，证据见
[AC-07-02 执行报告](2026-08-07-agentic-capability-ac07-02-execution-report.md)。

#35 已新增固定来源工具锁、最终能力目录三类 Trivy 扫描、Syft JSON/CycloneDX 1.6、七天 DB
时效门、不可变 SQLite 证据和 Owner/管理员脱敏摘要。两份 AC-06 冻结能力包真实扫描通过；生产
迁移与用户验收尚未执行，证据见
[AC-07-03 执行报告](2026-08-07-agentic-capability-ac07-03-execution-report.md)。

目标：保证一次成功只产生个人草稿，平台共享需要真实证据和管理员动作。

Red：

1. 旧模板可按次数/平均分自动晋级为全局 active；
2. 更新可能覆盖旧内容；
3. 管理员审核可能意外读取个人正文。

Green：

- ValidationRun 分开记录合成 Smoke、Owner 真实 TaskRef 和失败关闭证据；
- 成熟度 `draft | verified`、生命周期 `active | deprecated | revoked`、运行资格
  `eligible | quarantined` 三轴分离，失败只产生新候选、验证失败或安全隔离；
- 新版本并行；旧任务继续使用冻结版本，目录默认不静默升级；
- 平台发布从已验证个人版本生成脱敏独立快照，并重新计算 digest；
- Trivy 扫描最终目录/镜像并按 ADR-0029 的 Secret/Critical/High 分级门失败关闭；进入
  `verified` 或平台候选时用 Syft 生成 SBOM；只有管理员发布的平台 digest 必须用 Cosign
  本地密钥签名并在装载前验证；本地 OCI Layout 签名先通过回环临时 Registry PoC；
- 管理员默认可读任务管理信息，业务正文只能显式审计查看；发布、弃用、回滚、撤销、恢复、
  风险接受和受众变化都记录 actor、时间、原因与范围；
- 平台发布默认只进入 `admin_gray`，普通用户开放是独立动作；
- 平台回滚只改变新任务推荐版本，不修改历史任务。

完成证据：Python Tool 与 Everything MCP 两条真实闭环；单次成功不晋级、缺失败门不晋级、
并行升级、审核权限、审计查看、脱敏扫描、SBOM、签名、发布/弃用/回滚/隔离/撤销、限期风险
接受、并发幂等和跨用户拒绝 100%。平台发布、弃用、撤销、恢复、风险接受和普通用户开放是
人工控制点。

### AC-08：SOP 蒸馏、组合、选择与失败学习

目标：把成功经验沉淀为可组合个人方案，同时保留 Pi 的来源驱动自主性。

Red：

1. 当前模板蒸馏读取报告正文并写入全局文件，可能携带业务数据；
2. 关键词/TaskSpec 触发会重建固定路由；
3. 失败后原地合并可能破坏已验证方案。

Green：

- ProcedureLearning 只消费结构化 ExecutionTraceRef、能力引用、权限、Observation 和完成门；
- 生成内容经过业务正文、Secret、宿主路径和个人标识扫描；
- 方案可以组合多个 CapabilityPack，preferred sequence 不限制 Pi 重规划；
- CapabilityResolution 先执行 Owner/权限/健康硬门，再做语义适用度和历史质量排序；
- 个人与平台方案同时匹配时记录选择原因并允许用户切换；
- 失败修复生成并行个人候选，旧验证/平台版本不变；
- 不为冻结评测问法增加关键词或 TaskFamily 分支。

完成证据：相同目标换问法、相同关键词不同上下文、组合两能力、个人/平台冲突、旧版本回放、
恶意来源提示注入和业务数据扫描；真实 Pi 连续重复选择达到冻结门。

### AC-09：自动化方案库、管理员审核与新手引导

目标：让普通用户知道系统复用了什么，让管理员能治理平台方案，但不把复杂度塞进任务主线。

Red：

1. 设置页已包含大量模型、采集和诊断配置；继续追加会降低可发现性；
2. 当前任务详情没有方案选择原因、版本和能力证据；
3. 个人/平台/待审核如果混在一页，用户会误以为都可编辑。

Green：

- 文档建议的“自动化方案”入口实现为独立信息架构，任务卡保留上下文快捷入口；
- 普通用户看到“我的方案、平台方案、草稿与待验证”，不能看到他人方案；
- 管理员/超管增加审核队列、来源、digest、权限、验证和版本差异；
- 任务卡展示本次方案、选择原因、版本、活动能力和是否新获取；
- 首次生成个人方案提供三步可跳过引导，并可从方案页重新打开；
- 发布、弃用、切换和失败都有可恢复反馈，不使用 Emoji；
- 设置页只承载来源策略、预算和缓存等治理项。

完成证据：普通/管理员/超管权限，空状态、大量方案、长名称、审核、版本切换、引导重放、深浅
主题、键盘、axe、1366 和窄屏。一级导航最终形态需用户验收后冻结。

### AC-10：端到端安全、恢复、性能与阶段收口

目标：用真实任务证明 Pi 能获取、复用、学习、追问并保持正式交付边界。

场景至少包括：

1. 缺 Python 能力的 PDF/表格任务：首次获取，候选和证据正确；
2. 相同版本第二次任务：零下载并明显复用缓存；
3. 一个任务组合两个能力包，不为每个普通工具启动容器；
4. 本地 MCP 任务期复用与取消；
5. 陌生 URL、恶意 Skill、越权 MCP、跨用户方案和篡改 digest 全拒绝；
6. 运行中问进度和选择依据，Run 不变；
7. 运行中修改字段，在安全点创建新 revision 并只重做受影响阶段；
8. 成功生成个人 draft，完成三类验证后 verified，管理员发布独立平台快照；
9. 页面刷新、SSE 断线、服务重启和 Pi 会话恢复后状态一致；
10. Candidate 仍不能绕过 ADR-0019；只有正式 Publisher 成功才显示正式交付。

首轮 PoC 还必须回答：Windows 上 OCI Layout 写入与恢复、uv/npm 冷热缓存收益、ORAS/BuildKit
证据关联、Cosign 本地 Layout 行为、MCP `server.json` 规范化、远程 MCP OAuth/DNS rebinding、
平台脱敏与版本撤销语义。没有实测结果时不得把研究建议写成生产事实。

验证证据：Interface 单测、HTTP/SSE 集成、Owner/角色安全矩阵、真实 Docker/Egress、四类真实
Adapter、固定本地 Qwen 语义语料、Playwright、生产构建、依赖检查、冷/热性能、磁盘和残留
资源报告。安全、所有权、外发和假成功必须 100%；工程通过后仍需用户实际操作验收。

## 6. 共同 TDD 与实施规则

1. 每个工单先在对应 Seam 写失败测试，再写最小纵向实现；
2. 不测试私有函数、Prompt 文案、具体下载命令或 Adapter 内部顺序；
3. 业务预期来自冻结夹具、真实来源或人工确认，不用实现计算自己验证自己；
4. 不新增问法特判、TaskFamily、静默外发、静默升级或全局安装；
5. 一个工单只交付其独立价值，不顺手建设市场、团队共享、计费或服务器部署；
6. 每个工单完成后运行聚焦回归和全部已完成切片；
7. 执行报告分开已验证事实、代码推断、未验证建议和用户验收状态；
8. 代码审查固定实施基线后再进入，不用当前混合工作树冒充正式 review。

## 7. 人工控制点

必须暂停并取得用户确认：

- 改变业务范围、数据含义、字段、结果基数、输出语义或默认解释；
- 新增外部服务、外发内容、Secret、网络目标、目录、宿主或 Docker 权限；
- 放宽来源策略、资源上限或允许陌生可执行 URL；
- 把个人方案、真实样本或个人任务引用发布到平台；
- 不可逆数据库迁移、物理删除能力缓存或历史版本；
- 新一级导航最终定稿、默认入口切换或正式交付含义变化；
- GitHub Issue 写入、提交、推送、版本、标签和外部发布。

在已确认 Seam 内写测试、内存 Adapter、文档、合成夹具和失败关闭不需要逐项确认；但本任务图
整体仍需用户另行明确“开始实现”后才能执行。

## 8. 当前阶段完成门

本任务拆分阶段完成条件：

- AC-00–AC-10 每项有独立价值、依赖、Red、Green、证据和人工控制点；
- 能力包、SOP、成熟度、作用域、对话追问和上下文转写语义不冲突；
- 旧 Capability/Skill/模板只通过 Adapter 兼容，不原地迁移；
- 性能隔离采用共享缓存和任务级装载，不默认每能力一容器；
- 进度采用结构化事件和受众投影，不展示原始思考或 Emoji；
- vNext 正式 Publisher、D6、8B、版本和发布仍保持原边界；
- Markdown 链接、UTF-8 和 `git diff --check` 通过。
