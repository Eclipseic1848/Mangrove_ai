# ADR-0020：模型连接采用凭证隔离代理与原生协议透传

- 状态：`accepted`，用户于 2026-07-30 确认价值优先的 D4 架构基线
- 日期：2026-07-30
- 决策来源：
  [Phase 4 D4 外部服务、个人 API Key 与受控外发契约](../plans/2026-07-30-phase4-d4-provider-connection-and-controlled-egress-contract.md)
- 上游：
  [ADR-0018](0018-unified-task-domain-contract.md)、
  [ADR-0019](0019-vnext-delivery-and-default-cutover-state-machine.md)

## 背景

当前个人模型 Key 只在 API 返回时遮罩，静态存储仍是通用 SQLite 明文；Pi Runtime 类型和
容器配置会直接接收原始 Key。现有 SSRF 守卫可以预检 URL 和 DNS，但实际 HTTP 连接没有
钉住已验证目的地址。继续扩展按用户配置覆盖无法提供连接版本、轮换、撤销、Owner Grant、
受控外发和原生 Usage 的统一边界。

D2 已确认四类 `api_format`、`ModelConnection`、`AccessGrant` 和“个人连接不得静默回退”；
D3 已确认新增外发对象或连接必须形成新 Revision，且暂时性失败最多重试两次。Pi 当前固定
版本已原生覆盖四类格式，没有必要为了首批支持引入 Mangrove 自己的协议转换层。

## 决策

1. 所有本地、平台共享和用户个人模型连接均经逻辑 `ConnectionBroker` 使用；它可以先存在于
   当前后端进程，不要求拆成独立微服务。
2. Provider Secret 迁出通用 `runtime_config`，进入与连接元数据分离的在线密文存储；
   加密主材料不与数据库同库保存，也不复用 JWT Secret。产品管理员不能查看、导出或代用
   个人 Secret。逐 Secret DEK、自动 KEK 轮换、历史备份密码学擦除与外部 Vault/HSM 后置。
3. Agent 只接收绑定 Owner、TaskRevision、Run、Purpose、连接版本、载荷类别和有效期的
   `AccessGrant` Token；Broker 仅在内部网络可达，Token 同时绑定 Runtime/任务网络身份。
   Provider 原始 Key 不进入 Agent、任务、事件、证据或交付。
4. 默认产品路径采用版本化 `ProviderPreset`：用户选择 Provider 卡片后只填写 API Key，
   平台提供 URL、协议路线、推荐默认模型和友好模型目录；不通过 Key 前缀猜 Provider，不为
   整张模型表自动产生验证调用。首批 Preset 已确认为 DeepSeek、阿里百炼 Qwen、OpenAI、
   Anthropic 和 Gemini；ADR 不写死易变化的模型 ID。
5. 默认折叠的“自定义兼容接口”功能继续支持长尾 Provider 和自建网关；它不是新用户角色。
   普通用户只使用平台发布的 Preset 并配置自己的 Key；管理员和超级管理员作为同一连接治理
   权限类型管理自定义公网和精确 LAN/本地连接。首批底层原生支持
   `anthropic_messages`、`openai_chat_completions`、`openai_responses` 和
   `gemini_generate_content`；Mangrove 不做跨协议自动转换，网关显式登记为
   `gateway_translated`。只有自定义连接输入 `base_url`，其语义为协议根地址，Adapter 独占
   操作路径并预览最终 Endpoint。
6. 公网自定义连接只允许 HTTPS，关闭自动重定向并强制 TLS。管理员与超级管理员作为同一管理
   权限类型，可以精确登记 `192.168.*` LAN、本机或其他 managed private 模型服务；放行绑定
   具体 scheme、host/IP、port 和协议路线，不开放整个私网。实际连接必须经 Smokescreen 或
   等价 EgressEnforcer，不能只依赖连接前 URL/DNS 校验。
7. 保存连接经同一 Broker 使用无业务数据合成探针，只对推荐或用户选择的模型做一次极小真实
   生成，并提示可能产生少量 Token；任务外发以 TaskRevision 为边界确认目的地、用途和
   载荷类别，扩大任一维度需要新 Revision 或重新确认。
8. ProviderUsage 逐 Attempt 保存原生字段；未返回为 `unknown`，不估算价格、预算或账单。
   个人连接失败不跨连接 Failover；外部生成只在可证明请求未发出或 Provider 明确保证安全时
   重放，可能已到达后的模糊失败记为 `outcome_unknown`。
9. 普通 HTTP SSE/分块响应是 Agent 内部传输能力；WebSocket、WebRTC、Realtime 和实时
   音视频产品能力不在本决策范围。
10. 首个价值批次分为两个连续纵切面：第一纵切面完成在线密文、Owner 隔离、
    停用/删除后的在线撤销和精确 LAN 登记；第二纵切面以 Grant/Relay 取代 Pi 持有的
    Provider Key。完整外发交互、自动密钥轮换、备份擦除、证书/重定向/DNS rebinding
    全矩阵与安全运营 UI 作为后续加固批次，不阻塞核心功能。

## 考虑过的替代方案

- **继续扩展 `runtime_config` 并只做遮罩**：不能关闭数据库明文、轮换、撤销、账户删除和
  跨用户误用，拒绝。
- **让所有用户先理解并填写 URL、协议和模型 ID**：把平台已经知道的技术信息转嫁给新手，
  也扩大误配置和 SSRF 面，拒绝作为默认路径。
- **根据 API Key 前缀自动猜 Provider**：Key 格式不是稳定身份协议，兼容网关也可能复用格式，
  拒绝。
- **完全删除自定义连接入口**：无法满足长尾 Provider、自建网关和自定义 URL 的既有需求，
  拒绝；改为渐进披露。
- **让 Pi 直接持有个人 Key**：Prompt 注入或容器逃逸会暴露长期凭证，且无法按任务撤销，
  拒绝。
- **把 LiteLLM 嵌入为强制翻译核心**：增加安全关键依赖和跨协议语义漂移，而 Pi 已原生支持
  四格式，拒绝作为默认；保留为用户自建网关选项。
- **普通用户可访问任意私网 Base URL**：会把平台变成 SSRF/内网探针，拒绝。
- **每次模型调用都弹窗确认**：安全提示疲劳且破坏 Agent Loop；采用每个 TaskRevision 一次
  不可变 Disclosure。
- **保存 Key 时永久同意一切外发**：连接存在不等于知道每个任务发送的数据，拒绝。
- **立即部署 OpenBao/Vault**：安全能力完整但对当前单机和资源边界过重；保留
  `CredentialVault` Adapter，首批使用成熟本地认证加密实现。

## 后果

- 后端与 UI 纵切面已经新增 Preset、个人/平台共享连接、在线密文、TaskRevision 外发确认、
  Grant/Relay、最小原生 Usage 和最终 Disclosure UI；完整安全运营仍后置；
- 需要新增可版本化的 ProviderPreset、Route 和友好模型目录；Preset 更新不能改写既有
  TaskRevision；
- 需要把现有个人 DeepSeek/Qwen Key 从明文 KV 迁出，并确保账户删除级联销毁；
- Pi Runtime 请求和容器配置需要从 Provider Key 改为 Broker Grant；
- 业务阶段 Egress Policy 需要允许精确 Broker/Provider 路线，同时继续拒绝公共依赖站点；
- 四协议需要独立验证、错误、流式响应和 Usage 契约测试，但不需要 Mangrove 自己翻译协议；
- Server 主机与主加密 Key 的共同控制者仍在信任边界；若未来要求运维者也无法解密，需另立
  ADR 评估端侧代理、HSM 或外部 Vault；
- 本 ADR 已确认，但不授权不可逆迁移旧 Secret、启用真实外部 Provider、进入 D5、修改外部
  Issue、创建版本或发布。

## 实施注记（2026-07-30）

两个后端纵切面已实现，状态为 `implemented_pending_user_acceptance`。RuntimeAssignment
冻结连接版本和该修订的外发确认；Agent 与 Verifier 使用独立 Purpose Grant；四协议原生
Relay、生命周期撤销和 Usage `unknown` 已接入。自动化未使用真实外部 Provider，也没有
执行真实 Pi→Relay→外部 Provider Smoke。实现与验证证据见
[D4 Grant/Relay 执行报告](../plans/2026-07-30-phase4-d4-grant-relay-pi-execution-report.md)。

后续 UI 纵切面已实现个人 Provider Preset 和管理员平台共享 Provider Preset：Provider
选择后由平台冻结 Base URL 与 API 格式，用户只选择模型并填写必填 Key；自定义/LAN 作为
独立高级入口，公网连接 Key 必填，精确 LAN/本地无鉴权服务可以留空。未知 `/api/*` 不再
被 SPA 回退为 HTML 200，前端对非 JSON 响应提供可恢复错误。
