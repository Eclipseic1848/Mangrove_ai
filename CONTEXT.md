# Mangrove 统一任务域

Mangrove 把用户的数据处理要求表示为来源无关、可验证、可追溯并能形成正式交付的语义任务。
本词汇表是 Issue、规格、界面和代码共享的统一语言。

## 产品呈现边界

**统一数据任务平台**：
产品层统一面对在线/离线、公域/私域、结构化/非结构化数据；统一的是目标、权限、证据、
版本、验证和交付语义，不表示所有来源已经接入同一个读取实现。
_Avoid_: 全能数据平台、所有企业数据源已支持

当前实现范围和规划边界只在 `docs/status/current.md` 维护。产品文案、演示稿或架构图必须
区分已实现、工程验证、用户验收和规划状态，不能用统一领域模型暗示所有来源均已接入。

## 任务与所有权

**语义任务（SemanticTask）**：
由任务所有者创建、用于组织不可变修订、执行运行、候选和正式交付的生命周期容器。
_Avoid_: 提示词任务、模型任务

**任务修订（TaskRevision）**：
一次已确认业务含义的不可变版本；来源范围、操作含义、连接或粒度、冲突策略、权限或交付
要求发生实质变化时创建新修订。
_Avoid_: 可变任务配置、执行草案

**目标契约（GoalContract）**：
任务修订中的业务真相，记录来源范围、任务操作、必须包含、明确不要、结果语义、证据策略、
交付规格和获准权限；不预先选择工具路线。
_Avoid_: 完整执行计划、任务分类、提示词

**执行运行（Run）**：
针对一个任务修订的一次执行尝试；重试、恢复和重新规划属于同一运行，不得改写目标契约。
_Avoid_: 任务修订、模型调用

**任务所有者（TaskOwner）**：
拥有任务、来源授权、候选和交付访问权的用户；管理员治理权不自动等于内容使用权。
_Avoid_: 当前登录用户、任意管理员

## 来源、制品与模态

**来源绑定（SourceBinding）**：
任务获准读取的逻辑来源，包含来源通道、定位引用、选择范围、连接引用和读取上限，但不含
凭证明文。
_Avoid_: 已读取内容、来源快照

**来源快照（SourceSnapshot）**：
某次运行对来源绑定实际观察到的不可变身份；数据库或 HTTP API 快照只覆盖本次获准读取的
结果，不代表整个外部系统。
_Avoid_: 可变连接、整库副本

**制品（Artifact）**：
系统中可通过身份和 SHA-256 重开的不可变内容；来源、中间转换、候选和交付是不同的制品
角色。
_Avoid_: 任意文件路径、可变工作文件

**内容单元（ContentUnit）**：
制品中可被选择、操作和引用的最小稳定地址；它只统一身份、模态和位置，不把所有内容压成
一个巨型 AST。
_Avoid_: 通用内容对象、完整文档副本

**内容模态（Modality）**：
内容单元的表达形态，例如结构化内容、文档文本、图片、音频、视频或归档；一个制品可以
同时包含多个模态。
_Avoid_: 文件扩展名、TaskFamily

**单一来源任务（SingleSourceTask）**：
一个任务修订只消费一个逻辑来源；该来源自身仍可以包含多个文件或多种内容模态。
_Avoid_: 单文件任务、单模态任务

**复合来源任务（CompositeSourceTask）**：
一个任务修订消费两个或更多逻辑来源或来源类别；结果可以比较、校验、补全、连接、分别
输出或显式合并，不要求一定生成一个合并结果。
_Avoid_: 文件集、多文件任务、必然合并

## 操作、结果与证据

**任务操作（Operation）**：
用户要求施加在所选内容上的业务操作，例如提取、过滤、投影、连接、比较、OCR、转写、
转换、总结或翻译；它不等于工具或脚本步骤。
_Avoid_: 工具调用、执行计划

**结果项（ResultItem）**：
候选或交付中可独立验证的一项结果，必须且只能标记为来源观察、来源视图或派生结果之一。
_Avoid_: 未分类输出

**来源观察（SourceObservation）**：
从来源内容直接复制、确定性解析或经 OCR、ASR 等识别得到并绑定证据的结构化主张；它带
状态和置信度，不因被抽取就自动成为客观真相。
_Avoid_: 派生结论、无证据事实

**来源视图（SourceView）**：
对来源观察进行选择、排序、重排、格式转换或经用户明确连接后的结果，不增加新的业务判断。
_Avoid_: 总结、推断

**派生结果（DerivedResult）**：
总结、改写、翻译、分类、观点或推断等新增语义；必须由用户明确要求、标记为派生并引用
支持证据。
_Avoid_: 来源原文、来源事实

**证据引用（EvidenceRef）**：
把结果项指回来源快照、源制品和确定位置的不可变引用。
_Avoid_: 中间摘要引用、模型回答引用

**证据策略（EvidencePolicy）**：
目标契约对证据覆盖率、位置精度、允许的不确定状态和复核要求的约束；它不指定具体解析器。
_Avoid_: 解析器配置、提示词

**覆盖契约（CoverageContract）**：
Pi 根据用户目标和上下文形成、经校验后冻结的来源范围、结果数量、完备性、顺序与停止语义；
它约束任务必须达到什么结果，但不预先选择读取路线或工具。
_Avoid_: TaskFamily、关键词路由、OCR 模式、固定执行计划

**来源发现索引（SourceDiscoveryIndex）**：
对来源全部可寻址内容单元形成的可复用轻量观察，用于定位候选证据；它不是权威正文，也
不能直接作为结果项或正式证据。
_Avoid_: OCR 全文、来源快照、搜索结果即证据

**证据读取集（EvidenceReadSet）**：
根据覆盖契约对候选内容单元进行权威读取后形成的证据集合，记录已读范围、未覆盖范围和
停止依据；结果项只能引用其中通过质量门的证据。
_Avoid_: 模型上下文、页面摘要、候选页列表

**覆盖账本（CoverageLedger）**：
一次运行对获准内容单元已观察、已权威读取、质量不足和仍未知状态的可验证记录；Pi 可以
据此重规划，但只有独立验证者可以据此判定覆盖契约是否满足。
_Avoid_: Agent 自评、执行进度文案、固定页数规则

**同 Run 恢复与权威复核（RunResumeAndAuthoritativeReplay）**：
恢复是同一 Run 的继续执行，不是新 Run。它可以撤销并重建由 Owner、Task、revision、Run
和 phase 唯一确定的遗留运行资源，但不得删除其他任务资源或改变 GoalContract。候选验证
先重开原件；文本层不足时，只能在同一 Grant 内重读 CoverageLedger 已经标记为权威读取的
ContentUnit，并要求唯一可信结果。Manifest 使用规范来源标识，旧原文件名只能作兼容别名。
_Avoid_: 恢复即新建 Run、跨 Owner 清理、跳过原件只验证候选

**正式结果预览（DeliveryPreview）**：有携带逐行来源的 Legacy Parquet 时优先用于预览；
否则读取 Owner 隔离的正式 Delivery output，并先核验根目录、大小与 SHA-256。CSV、JSONL、
Parquet、XLSX 统一返回可分页、搜索和排序的 `table`；JSON、DOCX、PDF、HTML、Markdown、
TXT、PPTX 返回分段 `document`。TSV 只有底层枚举，界面和正式 Renderer 尚未支持。
_Avoid_: 读取 Candidate 充当正式预览、跳过 Owner 和完整性校验

**能力进度投影（CapabilityProgressProjection）**：“准备能力”是按需阶段。只有真实选择、挂载或获取冻结
Tool/MCP/Skill/AutomationProcedure 时才展示；没有能力事件时隐藏且不计入总阶段数。普通用户
可查看实际采用的能力名称、类型、版本和用途；digest、路径、调用参数、Secret 和网络地址
不进入用户投影。Harness 内部编排原语仍显示为业务动作，原始技术事件保留在管理员运行记录中。
_Avoid_: 固定空阶段、向普通用户暴露宿主路径或 Secret、展示原始思维链

## 候选、验证与交付

**交付规格（DeliverySpec）**：
目标契约对结果分类、格式、数量、命名、必须包含、明确不要和重开 QA 的要求；它不指定
渲染器或转换器。结构化表格需要时还会冻结精确列顺序和 JSON 表示形态，执行者与独立验证者
必须读取同一份契约。
_Avoid_: 输出工具配置、候选文件

**候选（Candidate）**：
一次运行生成、尚未正式发布的一组结果制品、结果分类、证据和血缘；验证通过本身不等于
正式交付，只有独立 Publisher 完成 QA 和可恢复提交后才成为 Delivery。
_Avoid_: 正式交付、验证通过即发布

**验证报告（VerificationReport）**：
独立于执行者，重新打开来源快照和候选后，对目标覆盖、语义、证据、格式、数量、所有权和
禁止项作出的结构化结论。
_Avoid_: 执行者自评、测试日志

**正式交付（Delivery）**：
通过发布门后面向任务所有者发布的不可变结果包；它引用唯一任务修订、候选、验证报告、
输出制品和 Manifest。
_Avoid_: 候选下载、内部 AST、Parquet 中间结果

**任务档案（TaskArchive）**：
随语义任务保留的权威生产记录，引用目标修订、来源身份、运行摘要、验证和交付身份；它
不是业务正文的第二份副本。
_Avoid_: 监控日志、正文备份

**精简审计记录（AuditTombstone）**：
任务内容按生命周期策略物理清理后保留的最小身份记录，不含业务正文、凭证或可恢复内容。
_Avoid_: 回收站备份、永久任务副本

## 连接与授权

**Provider 预设（ProviderPreset）**：
平台维护且不含秘密的版本化模型连接模板，固定 Provider、协议路线、端点和友好模型目录；
普通用户只选择预设并配置自己的凭证。
_Avoid_: 用户角色、个人连接、可变全局配置

**来源连接（SourceConnection）**：
访问数据库、HTTP API 或其他受控来源的用户级或管理员级连接配置；任务只保存引用。
_Avoid_: 模型连接、任务内凭证

**模型连接（ModelConnection）**：
用于推理的一套命名凭证与路线配置；同一 Provider 可以存在多套模型连接，一套连接可以
包含多个独立验证的连接模型和一个默认模型。API 格式、端点、连接模型和秘密引用彼此显式；
已识别格式为 `anthropic_messages`、`openai_chat_completions`、`openai_responses` 和
`gemini_generate_content`。
_Avoid_: Provider 预设、单个模型、OpenAPI、业务数据 API、把 Base URL 当 API 格式

**连接模型（ConnectionModel）**：
模型连接中一个可独立验证、启用或停用的模型；同一 API Key 有效不表示该连接的全部模型
均可用。
_Avoid_: Provider 完整模型目录、账号套餐、未经验证的模型

**模型使用偏好（ModelUsagePreference）**：
用户为新任务选择的默认模型连接及其默认连接模型；偏好可以改变，但不能改写既有
任务修订冻结的运行时分配。
_Avoid_: 平台唯一默认、任务修订、自动故障转移

**导入模型连接（ImportedModelConnection）**：
从旧个人、全局、`.env` 或本地模型配置显式复制得到的待验证模型连接；导入不访问
Provider、不删除旧值，只有验证成功后才可进入任务选择。
_Avoid_: 已验证连接、配置同步、旧配置删除、自动迁移

**访问授权（AccessGrant）**：
连接代理针对任务所有者、任务修订、运行、连接版本（`connection_version`）、用途和有效期签发的临时使用权，不向
任务、事件、证据、候选或正式交付暴露原始密钥。
_Avoid_: 永久共享凭证、管理员代用

**Provider 原生用量（ProviderUsage）**：
模型 Provider 对一次调用返回的原生计量，例如输入、输出和总 Token 或请求数；Provider
未返回时记为未知，不据此推算价格或生成计费账本。
_Avoid_: 估算费用、预算系统

**Provider 资格批次（ProviderQualificationBatch）**：
一组冻结模型连接、干净代码提交、运行参数和一次人工授权共同形成的不可变外发验收代际；
批次台账固定在工作目录外的 Mangrove 用户状态目录，独立于连接数据库，先记录 Attempt
再外发；连接数据库另存 Ledger ID、单调版本和状态摘要锚点，台账缺失、身份不符或恢复旧
快照时失败关闭。同一批次每个 Provider 只有一次初始 Attempt；进行中的 Attempt 不得并发
重试，只有终态失败或结果未知且用户明确承担重复请求与费用风险后，才增加一次恢复重试。
本轮从旧台账迁入的新批次必须完整保留初始与恢复重试两份历史摘要，并按内容和任务 ID 去重，
不能换格式、删除历史或回滚快照后重新计数。
外发前锚点同步失败时，未调用 Runtime 的 Attempt 应撤回并留下恢复审计；进程中断后的锚点
前滚只能由超级管理员在全局执行锁空闲、数据库路径身份一致时完成；遗留 `in_progress` 必须
保留次数并变成结果未知，不能猜测为未外发，也不能发送或重试 Provider 请求。
_Avoid_: Runtime 自动重试、进行中并发重试、换目录重跑、删除台账、把旧失败当成从未执行

**任务外发确认（TaskEgressConfirmation）**：
用户在创建具体任务 revision 时，对所选模型连接、冻结模型和本次外发内容类别作出的显式
确认；只授权当前 revision，不是账号级永久同意，也不能被其他连接复用。
_Avoid_: 全局隐私开关、Provider Key 授权、一次同意永久有效

## 执行与生产资格

**执行草案（ExecutionDraft）**：
Agent 在目标契约内，根据已观察来源和工具结果形成的可更新临时步骤。
_Avoid_: 目标契约、不可变计划

**能力工具（CapabilityTool）**：
登记输入输出、证据、副作用、网络、审批、幂等和资源限制的可组合执行能力。
_Avoid_: 业务场景分支、正式发布能力

**AgentKernel**：
在目标、权限、能力工具和验证边界内执行动态工具循环的可替换内核 Interface。
_Avoid_: 具体框架、完整产品、TaskFamily 路由器

**运行状态（RunState）**：
一次 Run 的执行生命周期状态，独立于正式交付状态和平台灰度状态。
_Avoid_: 工作台聚合状态、Delivery 状态

**发布意图（PublishIntent）**：
把一个已验证 Candidate 按冻结的 DeliverySpec 发布为正式 Delivery 的不可变请求；它承载
准备、QA 和提交身份，本身不是正式 Delivery。
_Avoid_: Candidate、Delivery、可变发布表单

**发布幂等键（PublicationKey）**：
由 owner、任务 revision、Candidate、VerificationReport 和 DeliverySpec 的稳定身份共同
计算的摘要；相同语义重试必须命中同一 Delivery，任一身份变化不得静默复用。
_Avoid_: 随机请求 ID、仅文件名去重

**发布提交点（PublishCommitPoint）**：
所有发布 QA 通过后进入 `committing` 的线性化边界；边界前取消必须保持零正式输出，边界后
取消不能撤销已经提交的不可变 Delivery。
_Avoid_: 上传开始、用户点击取消

**运行时分配（RuntimeAssignment）**：
任务 revision 创建时冻结的 Legacy 或 vNext 执行归属；使用外部模型时还冻结连接版本
（`connection_version`）和该修订的数据外发确认（`external_api_confirmed`）。后续默认路由或连接配置变化不重写既有分配。
_Avoid_: 当前平台默认值、可变用户偏好

**灰度模式（RolloutMode）**：
平台为新 revision 选择 RuntimeAssignment 的路由策略，包括 Legacy、管理员灰度、vNext 默认
和 Legacy 回滚；不完整的用户显式试用状态只作为历史失败关闭值，不再是可进入的产品阶段。
_Avoid_: 单次 Run 状态、用户正式交付状态

**门禁快照（GateSnapshot）**：
固定版本、语料和环境下生产硬门结果的不可变记录；通过快照是默认切换的必要条件，但不替代
用户对默认切换的单独确认。
_Avoid_: 实时健康值、自动上线授权

**影子运行（ShadowRun）**：
只用于比较和验证的 Run，其 Candidate 不得进入 Publisher，也不得成为用户正式 Delivery。
_Avoid_: 用户显式试用、正式任务

**依赖包（DependencyBundle）**：
在不挂载用户来源的隔离阶段获取并以哈希冻结的依赖与工具环境；它不是业务 Candidate。
_Avoid_: 运行中联网安装、来源文件快照

**能力获取（CapabilityAcquisition）**：
Pi 为一个已识别能力缺口发现、取得并验证可执行能力的隔离过程；它受来源、权限和资源预算
约束，不得同时读取用户业务来源。
_Avoid_: 业务执行中联网安装、宿主机全局安装

能力获取必须把公共依赖网络与业务来源阶段分开：官方来源按冻结清单，登记来源由平台可信
登记解析，陌生精确 URL 由 Owner 绑定 Grant 授权；获取网络销毁成功后才可进入 READY。
业务阶段只能只读装载冻结能力目录，且不得重新开放公共依赖网络。

**能力宿主（CapabilityHost）**：
同一任务的原生 Tool/MCP/Skill 共用一个任务级 Sidecar；容器不挂载业务来源、模型配置或
Docker Socket，Pi 只获得有范围和期限的 Relay。TaskRevision 冻结能力 OCI digest，新版本
继承精确 digest；归档展开、恢复、取消和清理必须失败关闭。
_Avoid_: 每工具一个任意容器、直接给 Pi Docker Socket、宿主全局安装

当前实现与验收状态只在 `docs/status/current.md` 维护。

**能力包（CapabilityPack）**：
一个原子能力的不可变版本，引用能力声明、工具、MCP、Skill、依赖包和验证证据，但不包含
用户业务数据、凭证明文或固定任务路线。
_Avoid_: 自动化方案、全局 Python 环境、任意下载目录

**能力目录（CapabilityCatalog）**：
按 Owner、作用域、版本和 OCI digest 保存并解析能力包、组件与自动化方案的产品目录；
TaskRevision 只冻结精确身份。管理员默认可查看跨 Owner 的任务管理信息，但个人业务正文只能
通过有原因和审计记录的显式审计查看访问。
_Avoid_: 公共插件市场、可变标签、管理员无痕全盘读取

**能力成熟度（CapabilityMaturity）**：
能力版本证据充分程度，只允许 `draft | verified`；成熟度严格绑定不可变 digest，新 digest
不得继承旧版本的验证状态。
_Avoid_: 生命周期、运行资格、一次成功即验证通过

**能力晋级门（CapabilityPromotionGate）**：
同一精确 digest 的五步验证全部 succeeded 且供应链证据 passed（漏洞库按判定时刻未过期）后，
系统确定性把个人能力成熟度从 `draft` 晋级为 `verified` 的有界判定；任何缺口都保持 `draft`
并以脱敏字面量解释。晋级只追加治理事件流，不改目录、不自动发布，也不改变生命周期与运行
资格。
_Avoid_: 单次任务成功即晋级、部分证据通过即晋级、把"已验证"表述为"已发布"

**能力验证缺口（PromotionGap）**：
晋级判定门返回的脱敏缺口字面量（如 `validation_incomplete`、`supply_chain_evidence_missing`、
`trivy_database_stale`），Owner 与管理员可见，但不会暴露内部路径、命令、Token 或原始扫描
日志。
_Avoid_: 向 Owner 暴露原始扫描报告、内部路径、命令细节

**能力生命周期（CapabilityLifecycle）**：
能力版本的治理状态，只允许 `active | deprecated | revoked`；弃用允许历史冻结任务继续使用，
安全撤销则禁止新任务、重试和恢复。
_Avoid_: 成熟度、删除历史版本、安全隔离

**管理员能力审核视图（AdminCapabilityReview）**：
管理员在设置页能力治理内按成熟度/生命周期分组的跨 Owner 审核投影：默认只展示三轴状态、
脱敏缺口、验证摘要、供应链摘要与任务管理元数据（任务身份、Owner、状态、时间、输入/输出
类型与数量），按待验证/已晋级/已弃用·撤销分组并渐进披露。业务正文永不进入该投影；
"平台候选/管理员灰度"分组待 #12 引入数据后补充。
_Avoid_: 通用跨用户任务管理中心（AC-09）、正文直出列表、批量导出

**平台快照（PlatformSnapshot）**：
把 `verified/active/eligible` 个人能力复制为不依赖 Owner 的脱敏平台 OCI 内容的不可变制品：
删除 Owner、个人 TaskRef、业务字段值（purpose）、连接与 Secret 引用，清空环境变量、归一
工作目录，重打包后形成新 digest；同一来源确定性重打包必须得到同一平台 digest。个人后续
修改、弃用或删除不影响平台快照。
_Avoid_: 复用个人 digest、保留个人配置、同源重打包 digest 漂移

**平台发布（PlatformPublication）**：
平台快照经六步验证（合成 Smoke、失败关闭、Trivy、Syft、装载结构探针、独立验证）全绿并
完成 Cosign 标准签名后，由管理员以原因、幂等键与预期状态发布；受众固定为 `admin_gray`，
不自动推荐、不开放普通用户。受众变更（admin_gray↔users）是独立治理命令，必须重查当前
验证与签名证据，产品入口留待人工授权。装载结构探针在 #12 是目录级实现；真实 Capability
Host 执行探针已在 #15/#16 纵切面完成（真实装载调用、篡改→自动隔离→restore、独立
Layout 密码学复验、单 Sidecar 双能力）。
_Avoid_: 发布即开放普通用户、复用个人签名、跳过平台重验证

**审计查看（AuditView）**：
管理员在确有排障或审核需要时，对验证证据绑定的冻结任务（task_id + revision 必须与最新
成功验证运行的 task_ref 一致）读取业务正文的独立有界命令：必须填写非空原因，实时读取
Prompt/来源/输出正文（单对象 2 MiB 按块截断、不落副本），无论成功失败都追加不可变
`audit_viewed` 治理事件（记录 actor、时间、任务、对象、用途、结果与失败类型），且不参与
三轴投影。Secret、宿主路径与原始工具日志连审计查看都不提供。
_Avoid_: 无痕读取、批量导出、审计记录可修改、借能力包读取任意任务正文

**能力运行资格（CapabilityEligibility）**：
能力 digest 当前是否允许执行的安全门，只允许 `eligible | quarantined`；系统可自动隔离，
但撤销或恢复仍是管理员治理决定。
_Avoid_: 生命周期、验证结果、自动撤销

**运行时装载治理门（RuntimeMountGate）**：
创建、重试、恢复与每次原生能力调用都必须经过的唯一装载 Seam（CapabilityMountResolver
注入的同一公开 Interface，API 与 Pi Runtime 均不可绕过）：个人 Pack 校验 Owner、
verified、`active | deprecated`、eligible；平台 Pack 还必须满足受众与 Cosign 签名证据
比对；legacy 平台 Pack（无发布事件）维持 AC-06 旧路径放行。新任务冻结还要通过三轴可选
谓词（deprecated/revoked/quarantined/draft 一律拒绝）。运行中由 30s 节奏的只读投影监督
检测隔离/撤销，命中即停 Sidecar、取消执行并禁止发布 Candidate/Delivery。代码注释中
「装载门/治理门/运行时门」均指本概念。
_Avoid_: 绕过 Seam 直连物化、装载与冻结两套语义、deprecated 进入新任务选择

**生命周期治理命令（GovernanceCommand）**：
管理员（superadmin 同权限）通过 CapabilityGovernance 公开接口改变三轴状态与推荐指针的
六个有界命令：弃用（新任务不再推荐，历史冻结可恢复）、撤销（禁止新任务/重试/恢复）、
隔离（安全刹车，最终由管理员决定撤销或恢复）、恢复（完整复查链：漏洞库 7 天时效 +
发布/签名证据 + 供应链证据 + 验证运行全绿）、限期风险接受（仅平台 admin_gray 受众、
隔离中、任何 blocker 不可接受、引用本包验证运行证据、1-90 天默认 30）与回滚（推荐指针
原子变更，目标全绿且证据齐备，历史 TaskRevision 不变）。全部命令要求 actor、原因、
幂等键和预期状态；事件快照与写入时刻投影一致。风险接受到期在投影读取时惰性判定为
quarantined（零调度零 DDL）。推荐指针由 `recommendation_changed` 事件折叠，选择列表
置顶标记。
_Avoid_: 修改历史事件、风险接受自动续期、跨包引用证据、回滚改写 TaskRevision

**能力验证运行（CapabilityValidationRun）**：
针对一个精确能力 digest 保存合成 Smoke、Owner 真实任务重放、失败关闭、独立 Verifier 和
资源清理证据的不可变验证记录；Run 通过持久化幂等键与 digest Lease 在中断后恢复，清理失败
保持可恢复状态而不是过早终态。安全扫描和 SBOM 是独立供应链证据，不得混写成 #34 的五步
验证已经自动完成能力晋级。
_Avoid_: 单次任务成功、能力获取 READY、平台发布批准

**能力供应链证据（CapabilitySupplyChainEvidence）**：
针对一个精确能力 digest，由哈希锁定的 Trivy 直接扫描最终目录，并由 Syft 对同一主体生成
Syft JSON 与 CycloneDX 1.6 后形成的不可变安全摘要。它记录工具与配置、Trivy DB version/
UpdatedAt、结果 hash 和风险计数；原始受控报告不进入普通产品投影。证据通过只是 #37 晋级的
必要条件，不等于能力已 `verified`、已签名或已发布。
_Avoid_: 扫描 SBOM 代替最终目录、工具运行成功即晋级、原始报告公开下载

**平台能力快照（PlatformCapabilitySnapshot）**：
管理员从已验证个人版本生成的独立脱敏能力版本，拥有新的 digest、平台签名和独立生命周期，
不因个人版本修改或删除而改变。
_Avoid_: 修改个人 scope、共享个人任务正文、可移动标签

**平台能力受众（PlatformCapabilityAudience）**：
平台能力允许被选择的用户范围，只允许管理员灰度或普通用户可用；平台发布与扩大普通用户
权限是两个独立的显式动作。
_Avoid_: 发布即全员开放、自动推荐

**审计查看（AuditedContentAccess）**：
管理员为排障、审核或安全调查显式读取个人任务业务内容的受控动作，必须记录原因、操作者、
时间、任务和读取对象。
_Avoid_: 普通任务列表、批量导出、管理员默认正文访问

**自动化方案（AutomationProcedure）**：
由一个或多个能力包组成的可复用 SOP，描述适用条件、优先路线、权限要求、完成门和失败
处理；Pi 可以在冻结目标内调整执行草案，它不是固定工作流。
_Avoid_: 模式、分析模板、提示词、不可变执行计划

**方案作用域（ProcedureScope）**：
自动化方案的可见和可用范围，只允许个人或平台两类；个人方案仅所有者可用，平台方案必须
由管理员或超级管理员审核发布。
_Avoid_: 高级用户方案、默认全局共享、团队作用域

**用户原始回合（RawUserTurn）**：
用户在对话中提交且不可改写的原始表达，是语义转写、任务追问和争议追溯的权威输入。
_Avoid_: 优化后 Prompt、模型摘要

**上下文变更草案（ContextDelta）**：
LLM 根据用户原始回合和已确认上下文生成的结构化语义变化，必须保留来源回合和置信状态；
它本身不能修改任务修订。
_Avoid_: 新任务修订、自由改写后的 Prompt

**修订草案（RevisionProposal）**：
语义差异门确认存在实质变化后形成的待确认对象；它冻结基础 revision、来源 Delta 和受影响
语义，但在用户选择立即停止、安全点切换或独立任务之前不修改当前 Run。
_Avoid_: 已生效任务修订、自动重跑指令

**安全点（RevisionSafePoint）**：
一个不可再细分的工具或阶段已经完整结束、尚未把下一步写入当前候选的切换边界；安全点只
允许应用已持久化且由用户确认的修订决策。
_Avoid_: 任意事件、模型输出结束、工具写入中途

**语义差异门（SemanticDiffGate）**：
判断上下文变更草案是无实质变化、只回答追问，还是改变范围、数据含义、权限、外发或结果
语义的确定性完成门；实质变化只能形成待确认修订草案。
_Avoid_: LLM 自行批准、关键词路由

**编译上下文（CompiledContext）**：
一次模型调用所需的有界上下文视图，引用冻结任务语义、相关对话、能力摘要和按需证据；它
不替代原始回合、任务修订、事件或制品。
_Avoid_: 完整聊天拼接、永久记忆、原始工具日志

**结构化进度事件（StructuredProgressEvent）**：
带 Task/revision/Run、业务阶段、事实类型、摘要、可选真实分母、引用、操作和受众的持久化
事件；普通用户与管理员视图由同一投影生成。
_Avoid_: 原始思考流、控制台全文、前端自猜阶段

**生产硬门（ProductionGate）**：
优先于加权总分的强制通过条件；任何核心失败都不能由其他维度补偿。
_Avoid_: 综合分达标即可上线

**工程验证通过（EngineeringVerified）**：
约定的自动化门禁和真实来源闭环已由开发流程执行并取得通过证据，不代表任务所有者已经
认可产品体验。
_Avoid_: 用户验收通过

**用户验收通过（UserAccepted）**：
任务所有者按照约定操作代表任务，并明确确认结果与体验可接受。
_Avoid_: 测试通过、页面可打开
