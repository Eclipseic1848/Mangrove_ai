# Phase 4 D4：多套模型连接与多模型目录正式规格

- 状态：`published_ready_for_agent`
- 日期：2026-07-30
- 对应现有 Issue：GitHub #16 `[Wayfinder][D4] 外部服务、个人 API Key 与受控外发契约`
- 上游决策：Phase 4 D4 Provider 连接契约、ADR-0020、统一任务域与 D3 状态机
- 适用范围：现有 D4 模型连接纵切面的增量升级，不重新设计整个 Agentic Runtime
- 发布状态：已发布到 GitHub #16，并添加 `ready-for-agent`

## Problem Statement

当前设置页已经允许用户配置个人 Provider 连接、管理员发布平台连接并登记自定义/LAN
连接，但产品和数据结构仍把“一套连接”近似等同于“一个 Provider 的一个模型”。这造成
四个直接问题：

1. 同一个用户无法为同一 Provider 保存生产、测试、不同账户等多套独立连接；
2. 一把 Key 只能绑定一个模型，不能表达“同一连接下多个模型分别验证、一个作为默认”；
3. 普通用户需要承担过多模型与接口知识，自定义/LAN 又没有完整恢复四种已确认协议；
4. 现有 DeepSeek、Qwen、本地模型和已保存 Key 如果不能自动导入，用户会被迫重新填写
   敏感凭证，既增加负担，也容易造成配置中断。

从用户视角看，期望不是建设一个完整模型市场，而是：选择 Provider、粘贴一次 Key，平台
自动提供少量推荐模型并验证可用性；同一 Provider 可以保存多套连接，任务默认使用自己的
默认连接和模型，同时允许在需要时显式切换。

## Solution

把模型连接升级为“命名连接聚合”：

- `ProviderPreset` 继续由平台维护 Provider、原生协议路线、Base URL 和 2–4 个推荐模型；
- 一个用户或平台可以为同一 Provider 创建多套不同名称的 `ModelConnection`；
- 每套 `ModelConnection` 只有一份独立 Secret，但包含一个或多个逐项验证的
  `ConnectionModel`；
- 每套连接有一个默认连接模型，每个用户还有一个 `ModelUsagePreference`，指向其新任务
  默认使用的连接；
- 任务创建时继续把连接身份、连接版本和模型身份冻结到 `TaskRevision` 的
  `RuntimeAssignment`，偏好变化不改写历史；
- DeepSeek、Qwen 和本地模型的现有配置自动导入新结构，用户无需重新填写 Key；
- 自定义/LAN 连接恢复 Anthropic Messages、OpenAI Chat Completions、OpenAI Responses
  和 Gemini `generateContent` 四种真实协议，先尝试模型发现和协议探测，失败后允许管理员
  手工输入模型 ID；
- 不建设价格、预算、自动 Failover、完整在线模型市场或多媒体/实时产品能力。

## User Stories

1. As an 普通用户, I want 只选择 Provider 并填写 API Key, so that 我不需要理解 Base URL 和协议格式。
2. As an 普通用户, I want 同一 Provider 保存多套命名连接, so that 我可以区分生产、测试或不同账户。
3. As an 普通用户, I want 同时配置多个 Provider, so that 我可以按任务选择不同模型服务。
4. As an 普通用户, I want 一套连接自动包含平台推荐的多个模型, so that 我不必逐个手工创建连接。
5. As an 普通用户, I want 平台只展示 2–4 个常用推荐模型, so that 我不会被完整厂商模型目录淹没。
6. As an 普通用户, I want 每个模型显示独立验证结果, so that 我知道当前 Key 实际有权使用哪些模型。
7. As an 普通用户, I want 至少一个模型可用时保存连接, so that 个别模型无权限不会阻塞整套连接。
8. As an 普通用户, I want 只启用验证通过的模型, so that 任务不会选择未经证明可用的模型。
9. As an 普通用户, I want 为每套连接选择默认模型, so that 日常任务不必重复选择。
10. As an 普通用户, I want 设置“我的默认连接”, so that 新任务自动采用我的常用连接。
11. As an 普通用户, I want 在任务提交前展开并更换连接或模型, so that 特殊任务仍有控制权。
12. As an 普通用户, I want 连接失败时得到明确错误, so that 平台不会偷偷消耗另一把 Key。
13. As an 普通用户, I want 查看平台共享连接但看不到平台 Key, so that 我能使用公共能力而不会接触秘密。
14. As an 普通用户, I want 现有 DeepSeek 和 Qwen Key 自动生成个人连接, so that 我不必重新填写凭证。
15. As an 普通用户, I want 导入失败时仍看到原配置来源和修复入口, so that 升级不会让已有能力突然消失。
16. As an 普通用户, I want 新手引导可以跳过并重新播放, so that 忘记操作后可以重新学习。
17. As an 管理员, I want 为同一 Provider 发布多套平台连接, so that 平台可以提供不同账户或用途的公共能力。
18. As an 管理员, I want 平台连接发布后供所有普通用户使用, so that 当前阶段不需要额外维护访问名单。
19. As an 管理员, I want 平台 Preset 同样只要求名称、Provider 和 API Key, so that 我不必重复填写平台已知字段。
20. As an 管理员, I want 平台 Key 的推荐模型逐项验证, so that 普通用户只看到真实可用模型。
21. As an 管理员, I want 现有全局 DeepSeek/Qwen Key 自动生成平台连接, so that 平台升级不要求重新录入秘密。
22. As an 管理员, I want 现有默认本地模型和额外本地端点自动生成管理连接, so that 当前 LAN 模型服务继续可用。
23. As an 管理员, I want 自定义/LAN 连接支持四种已确认 API 格式, so that 我可以接入兼容 Anthropic、OpenAI 和 Gemini 的服务。
24. As an 管理员, I want 平台先自动探测 API 格式, so that 我不必完全依赖技术记忆。
25. As an 管理员, I want 自动探测后仍可覆盖 API 格式, so that 多协议网关或非标准服务仍可正确登记。
26. As an 管理员, I want 平台优先查询模型列表, so that 我可以直接从真实可用模型中选择。
27. As an 管理员, I want 模型列表查询失败后手工输入多个模型 ID, so that 不实现模型枚举的本地服务也能接入。
28. As an 管理员, I want 精确 LAN 和本地无鉴权服务允许空 Key, so that 本地模型不会被公网规则错误阻断。
29. As an 管理员, I want 公网自定义服务强制要求 Key 和 HTTPS, so that 高级入口不会降低现有安全边界。
30. As an 管理员, I want 同一个自定义地址创建多套连接, so that 不同账户、协议或模型组合可以独立管理。
31. As an 管理员, I want 查看导入结果、来源、状态和错误分类, so that 我能判断升级是否完整。
32. As an 管理员, I want 导入前后旧配置暂不自动删除, so that 升级失败时仍有可恢复证据。
33. As an 超级管理员, I want 与管理员使用同一连接治理能力, so that 产品不发明不存在的新角色。
34. As an 任务所有者, I want 每个任务修订冻结连接和模型, so that 后续改默认值不会改变历史任务。
35. As an 任务所有者, I want 外部连接任务继续逐修订确认外发内容, so that 保存 Key 不等于永久同意发送所有数据。
36. As an 任务所有者, I want Agent 和 Verifier 使用同一冻结连接但不同 Purpose Grant, so that 验证可追踪且不会暴露 Key。
37. As an 任务所有者, I want Provider 未返回 Usage 时显示未知, so that 平台不会把未知消耗记成零。
38. As an 运维维护者, I want Provider 推荐目录有版本和官方来源日期, so that 模型上下架不会静默改变历史连接。
39. As an 运维维护者, I want 目录更新只提示同步和重新验证, so that 新模型不会未经授权进入已有连接。
40. As an 安全审查者, I want 导入和验证过程不把 Key 写入日志、事件、任务或浏览器, so that 自动迁移不会扩大秘密暴露面。

## Implementation Decisions

### 1. 角色与产品范围

- 系统角色仍只有普通用户、管理员和超级管理员。
- 管理员与超级管理员在模型连接治理上属于同一管理权限类型。
- 普通用户可以创建和管理自己的 ProviderPreset 个人连接，也可以使用平台共享连接。
- 普通用户不能创建任意自定义公网或 LAN Endpoint。
- 管理员和超级管理员可以创建平台 Preset 连接以及自定义公网/LAN 连接。
- 平台连接当前阶段发布后对所有普通用户可用，不增加用户白名单、部门或组织范围。

### 2. ProviderPreset 目录

- 首版 Provider 为 DeepSeek、阿里百炼 Qwen、OpenAI、Anthropic、Gemini、月之暗面 Kimi
  和智谱 GLM。
- 每个 Preset 保存稳定标识、版本、官方来源、核对日期、原生 API 格式、Base URL、鉴权方式、
  2–4 个推荐模型和一个平台推荐默认模型。
- 普通界面显示友好名称和“平衡推荐、能力优先、速度/成本优先”等角色，不要求用户理解
  原始模型 ID。
- Preview、Realtime、图像生成、视频生成、音频和其他专用模型不进入本规格的普通目录。
- 目录更新创建新版本；已有连接和任务修订不会自动切换模型。
- 连接可以主动同步新目录，只有重新验证成功的新增模型才会进入可用状态。

### 3. ModelConnection 聚合

- 删除“同一用户与同一 Preset 只能有一条个人连接”的业务约束。
- 每条连接拥有独立连接 ID、连接名称、所有者范围、Provider/Preset、路线、Secret 引用、
  连接版本、状态、默认模型和验证摘要。
- 同一用户或平台范围内允许同一 Provider 存在多条连接。
- 连接名称默认由平台生成并允许编辑；同一所有者范围内名称需要可区分，重复时由 UI 提示
  修改或自动追加序号。
- 一条连接只引用一份当前 Secret；换 Key 创建新的 Secret 版本并使连接版本递增。
- 连接删除或停用继续撤销相关在线 Grant，不触发跨连接 Failover。

### 4. ConnectionModel

- 每条连接包含一个或多个 `ConnectionModel`。
- 每个连接模型保存模型 ID、友好名称、目录角色、目录版本、验证状态、最近验证时间和脱敏
  错误分类。
- 验证状态至少区分：待验证、验证中、可用、无模型权限、凭证无效、协议不兼容、限流、
  网络不可达和已停用。
- 一把 Key 验证成功不能推导同一 Provider 的其他模型也成功。
- 默认模型必须属于当前连接的可用连接模型；默认模型失效时连接进入“需要选择默认模型”，
  不自动选择其他模型。

### 5. ModelUsagePreference

- 每个用户可以设置一个默认连接。
- 每条连接保存自己的默认连接模型。
- 新任务默认解析为“用户默认连接 + 该连接默认模型”。
- 用户可以为当前任务显式覆盖连接和模型。
- 修改偏好只影响之后创建的任务修订，不改写任何既有 RuntimeAssignment。
- 默认连接停用或不再可用时，新任务明确要求用户重新选择，不静默切换到平台连接。

### 6. 现有配置自动导入

- 导入是幂等操作，每个旧配置来源有稳定迁移身份；服务重复启动不得重复创建连接。
- 用户作用域中已有的 DeepSeek 和 Qwen Key 分别生成该用户的个人连接。
- 全局运行时配置中的 DeepSeek 和 Qwen Key生成平台共享连接。
- 若全局运行时配置没有显式 Key，但当前 `.env` 基线提供有效 Key，仍生成平台共享导入连接；
  `.env` 原文件不由迁移过程修改。
- 当前默认本地模型和额外本地模型端点生成管理员治理的自定义/LAN 连接；一条端点可包含多个
  模型时优先合并为一条连接，否则按精确端点生成多条连接。
- 旧默认 Provider 和默认模型在对应导入连接可用后转成用户或平台的默认选择。
- 旧 Provider 的 Base URL 与当前官方 Preset 一致时生成 Preset 连接。
- 旧 Base URL 与官方 Preset 不一致时，必须保留旧的精确 Endpoint，生成
  `legacy_imported` 连接；不能把 Key 静默发送到新的官方地址。
- 普通用户可以继续使用自己被导入的 `legacy_imported` 连接，但不能借此创建或修改任意
  自定义 Endpoint。
- 导入过程把旧 Key 复制到独立加密 SecretStore；API、日志、事件和迁移报告只出现掩码和
  不可逆指纹。
- 导入不会自动删除 `runtime_config` 或 `.env` 中的旧值。清理旧 Key 是后续单独的不可逆
  操作，必须再次取得用户确认。
- 未经真实 Provider 调用授权，导入连接先标记为“已导入、待验证”，但用户不需要重新填写
  Key；验证成功后才进入新任务可用目录。
- 导入完成后由用户点击一次“验证并启用”；该动作复用已导入的密文 Key，不要求重新填写，
  也不在无人值守的服务启动阶段擅自调用外部 Provider。
- 在导入连接验证成功和路由切换前，现有 Legacy 使用路径保持不变；切换后新任务只使用
  ModelConnection，不同时从两处读取同一 Key。

### 7. Preset 连接验证

- 创建个人或平台 Preset 连接时，对目录中的 2–4 个推荐模型分别发出极小、无业务数据的
  合成请求。
- UI 在发起前说明会产生少量请求和 Token。
- 模型验证使用与正式任务相同的 Provider 路线、鉴权和 EgressEnforcer。
- 至少一个模型成功才创建新的主动连接；部分成功时只启用成功模型。
- 全部失败时不创建新的主动连接；自动导入连接保留为“导入失败/待修复”，以便用户无需
  重填 Key 后重新验证。
- 验证结果只保存状态、错误分类、时间和 Provider 原生 Usage，不保存响应正文。
- 用户可以只重试失败模型。

### 8. 自定义/LAN 协议发现

- 支持 `anthropic_messages`、`openai_chat_completions`、`openai_responses` 和
  `gemini_generate_content`。
- 发现流程先根据四类官方模型查询能力尝试获取模型目录，再以一个候选模型执行协议级最小
  探针。
- OpenAI `/models` 成功不能证明 Chat Completions 或 Responses 可用；两条路线分别探测。
- 同一 Endpoint 同时支持多个格式时，UI 展示检测结果和推荐路线，管理员可以手动覆盖。
- 模型查询失败时允许输入一个或多个模型 ID，再执行所选协议验证。
- 一次发现最多自动验证 8 个模型；超过部分仍可搜索或手工添加后单独验证，避免一次操作
  产生不可控的请求数和 Token。
- 公网自定义连接要求 HTTPS 和非空 Key；精确 LAN、本机或其他受管理私网服务允许空 Key。
- 仍复用现有 SSRF、云元数据、重定向和精确私网登记边界；本规格不扩大普通用户私网权限。
- 自动发现不会把完整厂商模型广场永久保存为平台推荐目录。

### 9. 产品 API

- Preset 列表接口返回七家 Provider 的公开目录，不返回内部 Secret 或不需要展示的内部地址。
- 连接列表接口按当前用户返回自己的个人连接和可用平台连接；管理员额外返回治理字段。
- 新建个人 Preset 连接使用创建语义，不再使用会覆盖同 Provider 旧连接的 upsert 语义。
- 平台 Preset 连接使用独立的管理员创建语义。
- 连接详情返回多个连接模型、默认模型和验证摘要。
- 提供修改连接名称、修改默认模型、停用、删除、替换 Key、重验全部模型和重验单模型接口。
- 提供用户默认连接读取和修改接口。
- 提供自定义 Endpoint 发现接口，返回候选 API 格式、模型目录和不能确定的原因。
- 提供现有配置导入预览和执行状态接口；预览不返回 Key。
- 旧 API 在迁移窗口内保留兼容包装，但不得继续覆盖同 Provider 的其他连接。
- 所有错误响应保持 JSON 产品错误契约，未知 API 路径不能返回 SPA HTML 200。

### 10. 设置页 UX

- 页面顶部始终显示“当前默认连接”，包括连接名称、Provider、默认模型、个人/平台范围和
  更换入口。
- 普通用户主视图为“我的连接”和“平台可用连接”；管理员增加“平台连接”和“自定义/LAN”。
- 连接卡展示 Provider 图标、连接名称、范围、可用模型数量、默认模型、Key 尾号、最近验证
  时间和连接状态。
- 同 Provider 多套连接主要通过连接名称区分，不把连接 ID 暴露给用户。
- Preset 创建流程只显示连接名称、Provider、API Key 和可选的默认模型；Base URL 与 API
  格式默认折叠为“平台已配置”。
- 验证完成后显示逐模型结果；部分成功使用中性提示，不把可用连接误报为整体失败。
- 自动导入连接显示“已从旧配置导入，Key 无需重填”，并提供“验证并启用”。
- 创建、验证或平台列表中的一项失败不得清空整个模块；每个查询和操作独立显示错误与重试。
- 设置页保留可跳过、可重放的新手引导，覆盖默认连接、多套连接和高级入口的位置。
- 不引入新的整套 UI 框架；优先复用现有查询状态、对话框、Toast、引导和样式体系。
- Provider 品牌图标优先采用成熟的 AI Provider 图标包，并在原型阶段核对七家图标和暗色主题。

### 11. 任务使用与冻结

- 新任务只列出当前用户可用的个人连接和平台连接，以及每条连接中验证通过的模型。
- UI 默认折叠模型选择，但始终显示当前有效连接和模型摘要。
- 外部任务继续在提交前展示 Provider、连接名称、模型、外发内容类别和仅限当前修订的确认。
- TaskRevision 冻结连接 ID、连接版本、模型 ID和外发确认。
- Agent 与 Verifier 使用同一冻结连接，但分别签发 Purpose Grant。
- 连接换 Key、换默认模型、目录升级或用户偏好改变都不能改写既有修订。
- 连接失败明确返回当前连接错误，不自动使用其他个人连接、平台连接或本地模型。

### 12. Secret 与用量

- 一套连接的 Secret 只能由该连接使用；同 Provider 的不同连接不能共享可替换的 Secret
  引用。
- 个人 Secret 仍按 Owner 隔离，管理员不能查看、导出或代用明文。
- Provider Secret 不进入浏览器响应、任务、事件、证据、候选、交付、Docker argv 或 Agent。
- 导入、验证、Agent 和 Verifier Usage 继续按用途和 Attempt 分开记录。
- Provider 未返回 Usage 时记为 `unknown`，不估算价格。
- 本规格不增加钱包、余额、预算、限额或计费账本。

### 13. 成熟开源组件

- 服务端请求/变更状态复用现有 TanStack Query，保证个人连接、平台连接、Preset 和迁移状态
  独立失败、独立重试。
- 对话框、提示、Toast、新手引导和样式继续复用现有 Radix、Sonner、React Joyride 和
  Tailwind。
- Provider Logo 优先评估 `@lobehub/icons`；只有核对七家 Provider、构建体积和暗色主题后
  才纳入实现。
- 不把 LiteLLM 引入为强制协议翻译核心；四种协议继续由 ConnectionBroker 原生透传。

## Testing Decisions

### 测试原则

- 测试外部可观察行为，不断言内部私有函数、SQL 语句顺序或 React 组件实现细节。
- 优先复用三个已确认接缝，不为每个模型或页面动作增加新的底层测试接缝。
- 自动化只使用合成输入和假 Provider；真实 Provider Smoke 需要单独授权。
- 测试数据库、SecretStore、ConnectionBroker、API 和任务冻结使用真实实现；只替换外部
  Provider HTTP 和浏览器之外的不可控系统边界。

### 接缝一：产品 HTTP Interface

通过真实产品 API、SQLite、SecretStore、ConnectionBroker 和 TaskRevision 流程验证：

- 同一用户创建两套同 Provider 个人连接；
- 管理员创建两套同 Provider 平台连接；
- 一条连接包含多个逐项验证模型和一个默认模型；
- 部分模型成功、全部失败、Key 无效、模型无权限和限流；
- 普通用户、管理员和超级管理员权限；
- 跨 Owner 读取、使用、删除和默认设置失败关闭；
- 自定义四协议发现、手动覆盖、模型查询失败后的手工模型；
- 公网 Key 必填、LAN 无 Key、云元数据和混合公网/私网拒绝；
- TaskRevision 冻结连接与模型，后续偏好或连接版本变化不改写历史；
- Secret 和响应正文不出现在 API、数据库明文扫描、事件或 Runtime 请求中；
- 一个接口失败不改变其他连接记录。

现有 Provider 连接 API 集成测试和工作台任务 API 测试作为先例。

### 接缝二：数据库升级 Interface

从真实旧表结构和代表性旧数据启动升级，验证：

- 用户 DeepSeek/Qwen Key 自动生成个人连接；
- 全局覆盖或 `.env` 基线 Key 自动生成平台连接；
- 本地默认和额外模型端点自动生成管理连接；
- 官方 Endpoint 与自定义 Endpoint 被正确区分；
- 重复执行迁移不重复创建连接或 Secret；
- 同 Provider 已存在新连接时不覆盖；
- 旧 Key 不出现在迁移报告和日志；
- 迁移失败不会删除旧配置；
- 导入连接在未验证前不会被冒充为可用；
- 验证成功后默认连接/模型按旧配置恢复。

这是新增且不可被普通 API 测试替代的升级接缝。

### 接缝三：浏览器端到端 Interface

使用现有 Playwright 设置页和统一数据工作台先例，分别验证普通用户与管理角色：

- 页面顶部默认连接摘要；
- 多 Provider 和同 Provider 多连接卡片；
- Preset 添加流程不要求填写 URL 或协议；
- 逐模型验证与部分成功反馈；
- 设置默认连接和默认模型；
- 平台共享连接只读使用；
- 管理员平台连接和自定义/LAN 四协议流程；
- 自动导入连接“Key 无需重填”提示；
- 任务默认使用、展开切换和外发确认；
- 新手引导跳过、完成和重新播放；
- 单个请求失败时其他区域仍可操作；
- 明暗主题、键盘导航和自动化可访问性检查。

### 验证门

- D4 聚焦后端回归通过；
- 完整设置页与工作台 Playwright 通过；
- 前端生产构建通过；
- 数据迁移幂等与秘密扫描通过；
- 相关 Python 编译、UTF-8、Markdown 和差异检查通过；
- 使用真实 Key 的 Provider 与 Pi→Relay Smoke 必须另行取得用户授权，并记录实际 Provider、
  模型、请求内容类别和 Token 证据。

## Out of Scope

- Provider 价格、钱包、充值、预算、余额、账单和财务对账；
- 自动在不同个人连接、平台连接或本地模型之间 Failover；
- 平台连接的用户、部门或组织白名单；
- 在线维护的完整 Provider 模型市场和自动发布目录；
- Realtime、WebSocket、WebRTC、实时语音和实时视频产品；
- 图像、音频、视频生成目录和 Phase 4C 多媒体任务；
- 普通用户创建任意自定义公网或私网 Endpoint；
- Mangrove 内部跨协议自动翻译；
- 完整 DNS rebinding、证书固定、外部 Vault/HSM 和历史备份密码学擦除加固；
- 自动删除旧 `runtime_config` 或 `.env` Key；
- 默认入口切换、正式 Delivery、服务器部署、版本、标签和外部发布；
- 与本规格无关的设置页或运行时重构。

## Further Notes

### 已验证事实

- 当前个人连接存在“用户 + Preset 唯一”约束，会覆盖同 Provider 旧连接。
- 当前连接元数据只有一个模型字段。
- 当前自定义/LAN 注册只接受 OpenAI Chat Completions。
- 现有运行时配置只允许普通用户自助覆盖 DeepSeek 和 Qwen Key；全局配置还保存默认
  Provider、默认模型、本地默认端点和额外本地模型。
- 当前 D4 已实现 Owner 隔离 Secret、四协议 Relay、TaskRevision 冻结、ProviderUsage 和
  角色化设置页，本规格应扩展这些接缝而不是重做。

### 基于代码的推断

- 若不增加连接模型集合，无法同时满足一把 Key、多模型验证和任务显式选模。
- 若继续使用 Provider upsert，无论 UI 如何设计都无法保存同 Provider 多套连接。
- 若只迁移数据库全局值而忽略用户作用域和 `.env` 基线，部分现有用户仍会被迫重填 Key。
- 若把自定义旧 Base URL 强制改成官方 Preset URL，可能把用户 Key 发送给不同服务，因此
  迁移必须保留精确 Endpoint。

### 已确认的补充决策

- 自定义模型发现结果最多自动验证 8 个模型；超过部分允许搜索和手工添加。
- 自动导入只复制并生成连接，不在无人值守启动时调用外部 Provider；UI 显示
  “Key 无需重填”，由用户点击一次“验证并启用”。

### 尚未验证的建议

- 多套连接的默认名称建议采用“Provider + 用途/序号”，并允许用户立即修改；具体文案可在
  UI 原型中验证，不属于领域契约。
- 多连接、多模型和旧 Key 迁移改变了 ADR-0020 中“默认只验证一个模型、旧 Key 暂不迁移”的
  实施边界。规格确认后应新增 ADR 记录替代关系，不回写历史 ADR。

### 外部发布结果

- 用户已明确授权发布；
- 正式规格已作为评论发布到 GitHub #16；
- 远端原先不存在 `ready-for-agent` 标签，已按项目标签词汇创建并添加到 #16；
- 本次发布不包含代码提交、版本、Git 标签或下一阶段实施授权；
- 是否进入后续 `to-tickets` 仍由用户显式确认。
