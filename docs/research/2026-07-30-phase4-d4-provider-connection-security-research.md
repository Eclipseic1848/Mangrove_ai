# Phase 4 D4：Provider Connection、个人 API Key 与受控外发调研

- 日期：2026-07-30
- 状态：调研结论，供后续 `domain-modeling` 与 ADR 决策使用；不是实施授权
- 权威任务：[Issue #16：外部服务、个人 API Key 与受控外发契约](https://github.com/Eclipseic1848/Mangrove_platform/issues/16)
- 已确认上游契约：`base_url`、`model`、`api_key` 与 `api_format` 独立；`api_format` 至少包含
  `anthropic_messages`、`openai_chat_completions`、`openai_responses`、
  `gemini_generate_content`

## 1. 结论先行

### 1.1 已验证事实

1. 四种 API 格式不是同一个 JSON 契约换 URL：
   - OpenAI Chat Completions 使用 `/chat/completions` 和 message/choice 结构；
   - OpenAI Responses 使用 `/responses`、类型化 Item 和事件；
   - Anthropic Messages 使用 `/v1/messages`、content block 和
     `tool_use`/`tool_result`；
   - Gemini `generateContent` 使用
     `models/{model}:generateContent`、Content/Part 和
     `functionCall`/`functionResponse`。
   因此不能把“兼容 OpenAI”自动推断成同时兼容 Chat 与 Responses，也不能把协议翻译描述成
   无损。官方依据见[ OpenAI Chat](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、
   [OpenAI Responses](https://developers.openai.com/api/reference/resources/responses/methods/create)、
   [Anthropic Messages](https://platform.claude.com/docs/en/api/messages/create) 和
   [Gemini generateContent](https://ai.google.dev/api/generate-content)。
2. OpenAI 与 Anthropic 官方 Python SDK 当前都会默认跟随重定向，并默认重试一部分瞬态失败；
   但其生成请求没有公开承诺的通用幂等语义，SDK 基类也没有默认幂等请求头。生成调用不能套用
   “失败就自动重试两次”的通用规则。[OpenAI SDK 重试说明](https://github.com/openai/openai-python)、
   [OpenAI SDK 幂等头](https://github.com/openai/openai-python/blob/4f404262955cb711c56c07cce52076b6107303e5/src/openai/_base_client.py#L397-L404)、
   [OpenAI SDK 重定向默认值](https://github.com/openai/openai-python/blob/4f404262955cb711c56c07cce52076b6107303e5/src/openai/_base_client.py#L854-L859)、
   [Anthropic SDK 幂等头](https://github.com/anthropics/anthropic-sdk-python/blob/f5c30d0490fb7bcd8e0b65d8d8e63c0e7d1bfe59/src/anthropic/_base_client.py#L405-L412)、
   [Anthropic SDK 重定向默认值](https://github.com/anthropics/anthropic-sdk-python/blob/f5c30d0490fb7bcd8e0b65d8d8e63c0e7d1bfe59/src/anthropic/_base_client.py#L906-L912)。
3. 用户可配置 `base_url` 会把普通模型调用升级为 SSRF 边界。OWASP 明确要求同时做输入校验、
   所有 A/AAAA 地址校验、DNS 重绑定防护、禁用重定向与网络层纵深防御；仅用 URL 正则或仅在
   保存时解析一次 DNS 不足以关闭风险。[OWASP SSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
4. LiteLLM 已具备多 Provider 统一接口、协议转换、路由、回退、虚拟 Key、预算等成熟能力；
   其当前源码也有 URL 校验、地址解析和逐跳重定向校验，不能笼统称其“没有 SSRF 防护”。
   但它不是 Mangrove 所需的 owner-scoped Connection/AccessGrant/Secret 生命周期成品，
   且自动回退和预算能力与本期边界冲突。[LiteLLM 官方文档](https://docs.litellm.ai/)、
   [URL 防护源码](https://github.com/BerriAI/litellm/blob/4d543245153d5974f247feefad876d5d4475a73c/litellm/litellm_core_utils/url_utils.py#L240-L321)。
5. 现有 Smokescreen 已在本项目 Pi 运行链路验证了允许指定依赖站点、拒绝未批准域名/云元数据、
   阻止直接出网旁路以及仅放行固定本地模型；它适合继续承担网络 PolicyGate，但不负责密钥代理、
   owner 隔离、协议转换或内容审计。证据见
   `docs/plans/2026-07-29-agentic-runtime-vnext-pg05-live-cancel-egress-slice-report.md`；
   工具能力见 [Smokescreen 官方仓库](https://github.com/stripe/smokescreen)。

### 1.2 基于代码的推断

1. 当前 `runtime_config` 以 `TEXT` 保存配置值，`config_all()` 返回原值；
   `mask_value()` 只做接口展示遮罩。因此把个人 API Key 直接复用这张表只会“看起来被遮罩”，
   不等于密文落库（`src/api/store.py:67-74,752-765`，
   `src/config/runtime_config.py:281-288`）。
2. 当前数据库连接密码已有 Fernet 加密先例，但密钥会回退到 `jwt_secret`。这不足以满足 D4 的
   独立、可版本化 KEK、轮换、重包裹和 owner 绑定要求，不能原样复用
   （`src/services/db_connections.py:18-61`）。
3. 普通用户验证配置时，代码已经先加载该用户自己的覆盖再执行验证；这是“测试连接必须走同一
   owner”可复用的行为先例，但不是未来的 SecretStore/ConnectionBroker
   （`src/api/routes/config_routes.py:411-423`）。
4. Pi Runtime 目前仍把 `api_key` 写入运行时模型配置。D4 要实现“Agent 永远拿不到明文 Key”，
   必须把此处迁移为 broker 内部引用或短期受众令牌，而不是只隐藏 UI 字段
   （`src/agentic_runtime/pi_runtime.py:580-584,816-827`）。
5. 项目已有 `cryptography`、OpenAI、Anthropic 和 `httpx` 依赖，没有 `google-genai` 或
   LiteLLM。最小实施可以复用官方 SDK/成熟密码库，不需要手写四套序列化器；但必须覆盖 SDK
   默认重试、重定向和环境代理行为（`requirements.txt`）。

### 1.3 尚未验证的建议

采用“单体内逻辑 Broker + 独立 SecretStore + 协议 Adapter + 现有 Smokescreen”的最小架构，
先不新建微服务：

```text
UI / Task / Agent
       │  connection_id + 短期内部授权，不含 api_key/base_url
       ▼
ConnectionBroker
  ├─ Owner / AccessGrant / secret_generation 复核
  ├─ SecretStore：即时解密并只在本进程短暂使用
  ├─ ProtocolAdapter：四种协议的原生请求与响应
  ├─ EgressPolicy：URL/DNS/IP/TLS/数据外发决策
  └─ Audit + Usage：低敏元数据，缺失值为 unknown
       │  显式传输，trust_env=false，禁止重定向
       ▼
Smokescreen / 网络层 PolicyGate
       ▼
用户明确选择的 Provider endpoint
```

“本地服务”“平台公共外部连接”“用户自有外部连接”统一使用
`ProviderConnection`，但 owner、Secret、可用范围和网络策略不同。任何执行都冻结到一个明确的
`connection_id + secret_generation + capability_snapshot`；个人连接失败时关闭，不得暗中换成
平台 Key、本地模型或另一协议。

## 2. 范围与非目标

### 2.1 已确认范围

- 一个统一 Connection 契约描述本地模型、平台公共外部模型和用户自有外部模型。
- 用户可填写 endpoint、model、API Key 和 API 格式，平台验证连通后仅本人可使用。
- 应用管理员只能看遮罩元数据、状态、使用记录并禁用连接；不能查看、导出、冒充使用个人明文
  Key。
- Key 不进入任务契约、日志、证据、Delivery、Agent 提示词或任务容器。
- 使用量只记录请求实际返回的原生计量；未返回就是 `unknown`，不是 0。
- 记录 token、请求及适用的页数、图片数、音频时长、帧数等运行事实，不做钱包、价格、预算、
  计费或扣费。

### 2.2 明确不在 D4 首期

- WebSocket/WebRTC/双向 Live/Realtime API。
- 模型托管、购买额度、计费中心、充值和预算控制。
- 自动 Provider 回退、个人 Key 到平台 Key 的兜底。
- 任意私网 endpoint 对普通用户开放。
- 把四种协议强行转换成一个“最小公共子集”后宣称能力等价。

普通 HTTP SSE 流式响应与 Realtime 不是同一能力。补充核对当前固定的
`pi-ai==0.80.10` 后确认：OpenAI Chat、OpenAI Responses、Anthropic Messages Adapter
会设置流式调用，Gemini Adapter 使用 `generateContentStream`；`complete()` 也只是消费该
流直到完成。因此，如果个人外部连接要直接供当前 Pi Runtime 使用，Broker 首期必须透明转发
普通 SSE/分块响应。它不等于建设用户可见的实时产品；WebSocket/WebRTC/双向 Live/Realtime
仍明确排除。若用户坚持连普通 SSE 也禁用，则个人外部连接首期不能供当前 Pi Runtime 使用，
需要另行授权改写 Runtime Adapter。

## 3. 四协议官方差异

以下均为截至 2026-07-30 的官方公开契约。表中的“未知”必须通过具体 endpoint、model 和版本
探测，不能按协议名或厂商名推断。

| API 格式 | 生成端点与认证 | 流式 | 工具调用 | 文件/多模态 | 原生 usage | 错误与请求关联 | 生成幂等性 |
|---|---|---|---|---|---|---|---|
| `openai_chat_completions` | `POST {base}/chat/completions`；`Authorization: Bearer` | `stream=true`，SSE | `tools`，响应为 tool call；应用回传工具结果 | 消息支持文本、图像等，具体取决于模型；不可据此推断 Responses 文件能力 | `prompt_tokens`、`completion_tokens`、`total_tokens`；流式最终 usage 可能因中断缺失 | 标准错误对象；响应头有 `x-request-id`；客户端可传 `X-Client-Request-Id` 关联 | 官方未承诺 create 幂等；关联 ID 不是幂等键 |
| `openai_responses` | `POST {base}/responses`；Bearer | 类型化 SSE event | function/custom/built-in tools，输出为 Item | 原生文本、图像、文件输入；能力随模型/端点变化 | `input_tokens`、`output_tokens`、`total_tokens` | 标准错误和请求 ID | 官方未承诺 create 幂等 |
| `anthropic_messages` | `POST {base}/v1/messages`；`x-api-key` 和 `anthropic-version` | SSE：message/content block/delta/stop；200 后仍可能出现流内 error | 模型产出 `tool_use`；客户端执行并在后续 user content 中送 `tool_result` | 文本、图像；Files API 为 beta，文件持久到删除且消息使用会计入 token | `input_tokens`、`output_tokens` 等；流内 delta 可增量报告 | JSON error，`request-id`；429/5xx/529 等有独立语义 | 官方未承诺 Messages create 幂等 |
| `gemini_generate_content` | `POST {base}/v1beta/models/{model}:generateContent`；`x-goog-api-key` | `streamGenerateContent?alt=sse` | Part 中的 `functionCall`/`functionResponse`，部分模型要求 thought signature | Content/Part 支持多模态；Files API 上传得到 URI，文件通常保存 48 小时 | `usageMetadata` 含 prompt/candidate/total 及按 modality 明细 | HTTP 状态与 Google error；响应可有 `responseId` | 官方未承诺 generateContent 幂等 |

近处来源：

- OpenAI 认证、服务端 Key 与请求 ID：
  [API Overview](https://developers.openai.com/api/reference/overview)。
- OpenAI Chat、Responses 与文件输入：
  [Chat create](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、
  [Responses create](https://developers.openai.com/api/reference/resources/responses/methods/create)、
  [File inputs](https://developers.openai.com/api/docs/guides/file-inputs)。
- OpenAI 官方说明 Responses 的 Item、内置工具和状态语义不同，Chat 仍受支持；这证明二者不能只
  改路径：
  [Migrate to Responses](https://developers.openai.com/api/docs/guides/migrate-to-responses)。
- Anthropic 认证、流式、工具、文件和错误：
  [Authentication](https://platform.claude.com/docs/en/manage-claude/authentication)、
  [Streaming Messages](https://platform.claude.com/docs/en/build-with-claude/streaming)、
  [Tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works)、
  [Files API](https://platform.claude.com/docs/en/build-with-claude/files)、
  [Errors](https://platform.claude.com/docs/en/api/errors)。
- Gemini 认证和 API 总览、文件及错误：
  [Gemini API](https://ai.google.dev/api)、
  [Files API](https://ai.google.dev/gemini-api/docs/files)、
  [Troubleshooting](https://ai.google.dev/gemini-api/docs/troubleshooting)。

### 3.1 Base URL 契约

**已验证事实：** 官方 SDK 都会把用户提供的 base URL 与资源 path 合并；OpenAI SDK 还会规范
尾斜线。路径合并行为可见
[OpenAI SDK](https://github.com/openai/openai-python/blob/4f404262955cb711c56c07cce52076b6107303e5/src/openai/_base_client.py#L494-L503)、
[Anthropic SDK](https://github.com/anthropics/anthropic-sdk-python/blob/f5c30d0490fb7bcd8e0b65d8d8e63c0e7d1bfe59/src/anthropic/_base_client.py#L492-L501) 和
[Google Gen AI SDK](https://github.com/googleapis/python-genai/blob/95a335d809d75b987303d5a52e533a938585b7b5/google/genai/_api_client.py#L188-L203)。
错误地同时在 base URL 和 Adapter 中包含 `/v1`，会形成重复或错位路径。

**尚未验证的建议：**

- 用户填写的是“协议根地址”，不是任意完整 operation URL；Adapter 独占固定资源路径。
- 保存前规范 scheme、主机、端口和尾斜线，保留明确的业务 path，不接受 userinfo、fragment、
  内嵌凭证或 query 中的 Key。
- UI 必须实时显示最终请求预览，例如：
  - OpenAI：`{base}/chat/completions` 或 `{base}/responses`；
  - Anthropic：`{base}/v1/messages`；
  - Gemini：`{base}/v1beta/models/{model}:generateContent`。
- 对 `/v1` 究竟属于 base 还是 Adapter 作一个全局约定，不静默猜测或反复剥离路径。
- 兼容网关路径差异确实存在；若标准根地址不能覆盖，应新增管理员可审计的 adapter 配置，而不是
  让普通用户填写任意模板。

该输入契约会影响既有兼容网关，必须由用户在 domain-modeling 阶段确认。

### 3.2 原生协议与网关翻译

**已验证事实：** LiteLLM 的公开 Issue 已存在 Responses→Chat 工具类型丢失以及
Anthropic→Responses 元数据/drop 参数异常，说明翻译层需要逐版本、逐能力验证，不能假定无损：
[Issue #27276](https://github.com/BerriAI/litellm/issues/27276)、
[Issue #26241](https://github.com/BerriAI/litellm/issues/26241)、
[Issue #23841](https://github.com/BerriAI/litellm/issues/23841)。

**尚未验证的建议：**

- `api_format` 描述 Mangrove 发出的线协议；另设
  `wire_mode = native | gateway_translated`，不能从 host 或 provider 名推断。
- “第三方 OpenAI-compatible endpoint，但 Mangrove 直接发送 OpenAI Responses”仍属于
  `native` wire mode；“Mangrove 发送 Responses、网关再转 Anthropic”才是 translated。
- CapabilitySnapshot 记录网关名称、精确版本、源/目标协议、已知损失和探测时间。
- translated usage 只能标记 `gateway_reported`，不能冒充 Provider 原生计量。
- 首期优先四个 Adapter 直接发原生线协议；LiteLLM 只作为日后显式选择的网关 Adapter，不成为
  所有连接的强制中间层。

### 3.3 错误、重试与幂等

**已验证事实：**

- OpenAI 支持 `X-Client-Request-Id` 用于排障关联，但官方没有把它描述为生成幂等键
  [API Overview](https://developers.openai.com/api/reference/overview)。
- Anthropic 流式响应可能在 HTTP 200 之后产生错误
  [Streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)。
- Gemini 官方建议对部分 429/5xx 做指数退避，但这不等于重复生成具有业务幂等性
  [Troubleshooting](https://ai.google.dev/gemini-api/docs/troubleshooting)。

**尚未验证的建议：**

1. 关闭官方 SDK 自动重试与自动重定向，由 Broker 统一决策。
2. 内部每次调用生成 `attempt_id`、payload hash 和关联 ID；这用于审计，不宣称上游幂等。
3. 只有“确认请求尚未发出”或 Provider 对该具体状态明确保证安全时才自动重试。
4. 请求体可能已被上游接受后发生超时/断连时，结果标记 `outcome_unknown`、usage 为
   `unknown`，不得盲目再次生成。
5. D3 的“同一幂等键最多重试两次”只能保护 Mangrove 自身命令，不自动授权重放外部生成 POST。
6. 工具调用尤其要失败关闭：上游生成的同一个 tool call 不得因传输重试而重复执行具有副作用的
   工具。

## 4. 统一领域契约建议

以下为尚未验证的领域建议，需在下一阶段确认名称和字段含义。

### 4.1 ProviderConnection

```text
ProviderConnection
  id
  connection_scope       local | platform | user
  owner_user_id          user scope 必填；其他 scope 为空
  display_name
  api_format             四值枚举
  wire_mode              native | gateway_translated
  normalized_base_url
  model
  status                 draft | validating | active | disabled | revoked | deleted
  secret_ref             只引用 Secret，不存明文
  secret_generation
  capability_snapshot_id
  egress_policy_id
  created_at / updated_at
```

- `local`：管理员定义的精确 LAN 地址/端口，可无 Key；只允许明确的私网例外。
- `platform`：平台持有 Key，由平台 AccessGrant 决定谁可用；用户永远看不到 Secret。
- `user`：`owner_user_id` 必填，查询、测试、执行、禁用和删除都做 owner 条件约束。
- 任务 Revision 固定 Connection ID，不允许仅保存 provider 名后在运行时挑选连接。

### 4.2 CredentialSecret

```text
CredentialSecret
  secret_id
  owner_user_id
  ciphertext
  nonce
  wrapped_dek
  aad_schema_version
  kek_version
  generation
  status                 active | revoked | deleted
  created_at / rotated_at / revoked_at / deleted_at
```

Secret API 永不返回 ciphertext 以外可利用的内部材料，更不返回明文。应用管理员的列表接口只返回
“已配置/末四位可选指纹/更新时间/验证状态”，不得提供“临时查看”“导出”“代用户测试”入口。

这里的保证边界是**应用管理员**。掌握主机 root、进程调试权或 KEK 的基础设施管理员理论上仍可
读取运行时明文；如果业务要求连基础设施运维也无法接触 Key，需要外部 HSM/KMS 和更强职责分离，
不能靠 RBAC 文案承诺。

### 4.3 AccessGrant 与 EgressDecision

```text
AccessGrant
  grant_id
  user_id / revision_id / run_id
  connection_id / secret_generation
  purpose
  allowed_operations
  allowed_data_classes
  capability_snapshot_id
  expires_at / policy_hash

EgressDecision
  decision_id
  user_id / revision_id / run_id
  connection_id
  destination_origin
  data_classes / purpose
  confirmation_source / expires_at
```

AccessGrant 是“谁可以调用哪个连接”；EgressDecision 是“本次任务能否把哪些数据发到该外部目的
地”。两者不可合并，否则拥有 Key 会被错误解释为自动获得全部数据外发权。

### 4.4 CapabilitySnapshot

每个能力用四态，不用布尔默认值：

```text
verified | unsupported | unknown | not_probed
```

Snapshot 至少绑定精确 endpoint、model、api_format、wire_mode、adapter/gateway 版本和探测时间；
记录 text、image、file、audio、tool calling、structured output、SSE、usage 等能力。一次最小文本
生成只能证明认证、路径、模型和文本推理，不能顺带把工具、文件、流式标成已验证。

### 4.5 ProviderUsage

```text
ProviderUsage
  run_id / attempt_id / connection_id
  metric                 input_tokens | output_tokens | total_tokens | ...
  value                  nullable
  unit
  status                 observed | unknown | not_applicable
  provenance             upstream_native | gateway_reported | locally_measured
  provider_request_id
```

- 只有 `upstream_native` 才计入“Provider 原生 usage”。
- 网关报告值可以保存，但必须单列来源，不能混入原生汇总。
- 本地可测的输入页数/图片数/音频时长属于工作负载事实，不冒充 Provider 计费量。
- 请求断开、流式末帧缺失或 endpoint 不返回 usage 时，token 值为 null 且状态
  `unknown`；绝不写 0。
- 不保存单价、余额、预算、账单或金额。

## 5. BYOK Secret 生命周期

### 5.1 已验证事实

- OWASP 建议 Secret 实施最小权限、全生命周期、轮换/撤销、审计和绝不写日志
  [Secrets Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)。
- Envelope encryption 使用随机 DEK 加密数据，再由 KEK 包裹 DEK；数据库保存密文和 wrapped
  DEK，而不是明文 DEK。[Google Cloud KMS Envelope Encryption](https://docs.cloud.google.com/kms/docs/envelope-encryption)
- `cryptography` 的 AES-GCM 提供机密性和完整性，AAD 可认证但不加密；nonce 不能在同一 Key 下
  重复。[AESGCM 文档](https://cryptography.io/en/stable/hazmat/primitives/aead/)
- OpenBao Transit 提供版本化加解密、轮换和 rewrap，并由 ACL 控制调用；它不保存业务明文
  [OpenBao Transit](https://openbao.org/docs/secrets/transit/)。

### 5.2 尚未验证的最小实现建议

1. 每条 Secret 生成独立 256-bit DEK，用 AES-256-GCM 加密 API Key。
2. AAD 绑定 `secret_id + owner_user_id + connection_id + generation + schema_version`；
   防止数据库行被跨用户或跨连接替换。
3. 用独立、版本化 KEK 包裹 DEK。KEK 不进业务数据库，绝不能回退复用 `jwt_secret`。
4. 当前低资源单机可先用严格权限挂载的版本化 KEK 文件或 OS Secret，抽象为
   `KeyWrappingPort`；将来无需改业务表即可切 OpenBao Transit 或云 KMS。
5. 新写入只用 active KEK；旧 KEK 只解密。KEK 轮换时 rewrap DEK，无需解密并重写全部 API Key。
6. 首期不做明文缓存。若性能证据要求缓存，只能存在 Broker 内存，最长 60 秒，以
   `(secret_id, generation)` 为键，并在每次调用复核 generation。
7. 用户更新 Key：创建新 generation、验证成功后原子切换；旧 generation 立即失效。
8. 撤销：事务内置为 revoked、generation 递增、清缓存、使绑定旧 generation 的内部令牌失效；
   已发出的上游请求无法追回，必须在 UI 说明。
9. 删除：删除 ciphertext/wrapped DEK 并保留不含秘密的审计墓碑。删除 Mangrove 副本**不会**
   自动使 Provider 端 API Key 失效，用户仍需到 Provider 撤销该 Key。

### 5.3 同一路径验证

“验证连接”和正式任务必须调用同一个 ConnectionBroker、同一个 Adapter、同一 owner 条件、
同一 SecretStore 与同一 EgressPolicy，只把 `purpose` 标成 `connection_test`。不得另写一个把
明文 Key 直接传给 SDK 的测试快捷路径。

推荐验证顺序：

1. 静态校验并规范 base URL；
2. 按正式连接规则解析 DNS、检查 IP/TLS 和外发策略；
3. Broker 即时解密；
4. 发送一个输出上限极小的真实文本生成请求；
5. 校验协议结构、模型、最小输出和 usage；
6. 丢弃响应正文，只保存低敏状态、请求 ID、耗时和 capability snapshot。

模型列表接口不能证明指定模型能生成；因此建议用最小真实生成。它可能产生极少 token 成本，必须
由用户确认后才能定为产品行为。

## 6. SSRF、DNS rebinding 与最小外发策略

### 6.1 必须关闭的入口

云元数据不只一个地址：AWS 和 Azure 使用 `169.254.169.254`，AWS 还记录
`fd00:ec2::254`；GCP 还使用 `metadata.google.internal` 和 `fd20:ce::254`。
依据见 [AWS Instance Metadata](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instancedata-data-retrieval.html)、
[Azure IMDS](https://learn.microsoft.com/en-us/azure/virtual-machines/instance-metadata-service) 和
[GCP Metadata](https://docs.cloud.google.com/compute/docs/metadata/overview)。
此外要按 IANA 特殊用途地址注册表处理非公网地址，并包括
`100.64.0.0/10` CGNAT，而不是只拦 RFC1918：
[RFC 6890](https://www.rfc-editor.org/info/rfc6890/)、
[RFC 6598](https://www.rfc-editor.org/rfc/rfc6598.html)。

**尚未验证的建议：**

- 用户自有外部 Connection 首期只接受 `https`，默认仅 443；禁止 userinfo、fragment、URL 中
  Key、异常数制 IP、IPv4-mapped IPv6。
- 保存时和**每次连接前**都解析全部 A/AAAA；只要任一结果不是允许的全局公网地址就拒绝。
- 网络层必须连接已验证的地址或由可信代理完成解析与复核，同时保持正确 Host/SNI；否则校验后
  二次 DNS 解析仍会遭受 rebinding。
- 禁止 loopback、private、link-local、multicast、unspecified、reserved、CGNAT、云元数据地址
  与名称。
- 重定向默认为 0 次。若未来确有需求，每一跳都重新做全套校验，且跨 origin 绝不转发认证头；
  D4 首期不启用。
- HTTP 客户端固定 `trust_env=false`，不读取用户或任务容器的
  `HTTP_PROXY/HTTPS_PROXY/NO_PROXY`；代理由 Broker 显式配置。
- TLS 证书和主机名验证永远开启；不向普通用户提供 `verify=false`。企业自签 CA 如要支持，只能
  由管理员配置为平台级策略。
- 普通用户的 BYOK endpoint 不允许私网。LAN/本机模型作为独立 `local` scope，由管理员精确
  allowlist host/CIDR/port；不能与“任意外部 URL”共享放行规则。
- 响应体、超时和上传大小有硬限制。Provider 返回的新 URL 或 file URI 不自动下载；任何二次
  fetch 都必须重新进入 EgressPolicy。

### 6.2 Smokescreen 的定位

**已验证事实：** 项目现有 Smokescreen 方案已经证明任务容器的直连旁路可被阻断，但现有报告也
指出 hostname ACL 不能单独严格限制 HTTPS CONNECT 端口，需要自定义 Decider/网络规则补齐。

**尚未验证的建议：**

- 保留 Smokescreen 作为网络层第二道门，复用既有 Docker、代理日志和旁路测试。
- 业务层 Broker 先做 owner、URL、DNS、数据外发决策；Smokescreen 再做实际网络强制。
- 对用户 endpoint 增加 D4 专用 Decider 或固定目标令牌，不能把用户 host 直接加入全局静态
  allowlist。
- 网络日志只记录目标 origin、决策和请求关联，不记录 path/query/header/body。

### 6.3 外发授权

一个 Connection 验证成功只证明“可以连接”，不代表任意数据都可外发。建议 EgressDecision 绑定：

- 用户、Revision、Run；
- 明确 Connection 和 destination origin；
- 数据分类（纯文本、原文件、图像、音频、结构化数据等）；
- 用途（connection test、模型推理、文件上传等）；
- 授权来源、有效期和 policy hash。

换外部目的地、换个人/平台 Connection、扩大数据类别或上传原文件，都创建新 Revision 并重新
确认；不能在运行时静默升级权限。

## 7. Broker：让 Agent 永远拿不到 Key

### 7.1 最小调用方式

Agent/任务容器只获得一个短期内部令牌或 opaque `connection_ref`，令牌至少绑定：

```text
audience=connection-broker
user_id
revision_id
run_id
connection_id
secret_generation
allowed_operation
expiry
nonce
```

Agent 向 Broker 提交结构化操作和模型 payload。Broker 重新查 owner、Connection 状态、
AccessGrant、secret generation、capability snapshot 和 EgressDecision；通过后才在本进程即时
解密、注入认证头并调用 Provider。Key 不回传 Agent，也不写入临时配置文件或任务容器环境变量。

用户首次提交 Key 时，接收 Secret 的 API 进程不可避免会短暂看到明文；该专用入口必须关闭请求体
日志、APM body 捕获和异常回显。这里的“Agent 看不到 Key”不能错误宣传成“平台任何进程都从未见
过 Key”。

### 7.2 认证注入

- OpenAI Chat/Responses：Broker 注入 `Authorization: Bearer <secret>`。
- Anthropic Messages：Broker 注入 `x-api-key`，并由 Adapter 固定支持的
  `anthropic-version`；用户不能通过自定义 header 覆盖。
- Gemini generateContent：Broker 注入 `x-goog-api-key`；Key 不放 query。
- 用户不能配置任意 header 名和值，否则会重新打开 Host、Authorization、代理和走私边界。

### 7.3 失败关闭

- owner 不匹配、Secret generation 过期、Connection disabled/revoked、Capability 不满足、
  外发未确认或 PolicyGate 拒绝时，均失败关闭。
- 不允许 personal → platform、Responses → Chat、external → local 的隐式降级。
- 如果产品要提供“重新选择连接”，它是用户可见的新 Revision，不是一次隐藏 retry。

## 8. 审计与低敏观测

建议审计：

- actor/user、connection_id、revision/run/attempt；
- operation、api_format、wire_mode、adapter/gateway 精确版本；
- destination 的 scheme/host/port，不保存 path/query；
- AccessGrant/EgressDecision/policy hash；
- secret generation 和 KEK version，不保存任何密钥材料；
- 开始/结束、耗时、状态、HTTP 分类、净化后的 provider error type/code；
- Provider request ID、usage 的数值/状态/provenance；
- validate/activate/rotate/revoke/delete/deny 事件。

禁止进入日志、trace、证据和 Delivery：

- API Key、Authorization/x-api-key/x-goog-api-key；
- prompt、文件正文、响应正文；
- 包含 query 的完整 URL；
- Provider 原始错误体（可能回显输入或凭证）；
- ciphertext、wrapped DEK、内部短期令牌。

Provider 错误应归一为 `auth_failed`、`model_not_found`、`rate_limited`、
`policy_denied`、`upstream_unavailable`、`protocol_mismatch`、`outcome_unknown` 等稳定类型；
原始 status、provider code/type、request ID 可在脱敏后保留。

## 9. 成熟开源工具取舍

| 工具 | 已验证能力 | 与 D4 的缺口/风险 | 建议定位 |
|---|---|---|---|
| 官方 OpenAI/Anthropic/Google SDK | 官方协议模型、认证、流式和错误；支持自定义 base URL | 默认重试/重定向需覆盖；不会提供 Mangrove owner、AccessGrant、Secret 生命周期；Google SDK 尚未加入项目 | 四种原生 Adapter 的首选，不手写完整协议 |
| LiteLLM Proxy | 多 Provider、统一 OpenAI 格式、翻译、Router、虚拟 Key、预算、URL 防护 | 能力过重；默认回退/预算不符范围；Secret/owner 模型不等同 D4；翻译有已知语义损失；需额外服务和版本治理 | 首期不做核心 Broker；未来作为显式 `gateway_translated` Adapter |
| Smokescreen | 成熟出站代理、地址/域名策略；本项目已有真实旁路门证据 | 不保存 Secret、不做用户授权/协议/内容治理；现有 CONNECT 端口限制需补强 | 继续作为网络 PolicyGate |
| OpenBao Transit | 版本化加解密、ACL、rotate/rewrap，业务库不持 KEK | 增加独立服务、初始化、备份、解封和运维成本 | 作为 `KeyWrappingPort` 的生产增强选项；低资源首期不强制 |
| `cryptography` AESGCM | 项目已依赖；标准 AEAD，可用 AAD 绑定 owner/connection | 需要自己实现很薄但严格的 envelope 元数据、轮换和恢复流程 | 单机首期 SecretStore 的密码原语，不自行发明算法 |

LiteLLM 当前加密工具会从配置/主 Key 派生对称 Key；可选 AES-GCM，但不是按 Secret 随机 DEK、
独立版本化 KEK、owner AAD 和 rewrap 的完整 D4 envelope 方案：
[LiteLLM encrypt/decrypt 源码](https://github.com/BerriAI/litellm/blob/4d543245153d5974f247feefad876d5d4475a73c/litellm/proxy/common_utils/encrypt_decrypt_utils.py#L23-L31)。
LiteLLM 近期还出现过 Proxy API Key 验证 SQL 注入并在 1.83.7 修复；如后续采用，必须固定已修复
版本并把安全回归列为升级门：
[GHSA-r75f-5x8p-qvmc](https://github.com/BerriAI/litellm/security/advisories/GHSA-r75f-5x8p-qvmc)。

结论不是“自己手搓全部代码”，而是：

- 协议层复用官方 SDK；
- 网络层复用 Smokescreen；
- 密码原语复用 `cryptography`，可换 OpenBao/KMS；
- Mangrove 只实现不可外包的薄领域层：Connection、Owner、Grant、Secret 引用、Policy 与审计。

## 10. 威胁模型与关闭条件

| 威胁 | 典型路径 | 最小控制 | 必须验证的证据 |
|---|---|---|---|
| 跨用户 IDOR | 猜 connection/secret ID | 所有查询和命令同时约束 ID + owner；Grant 再校验 | A 无法查看、测试、调用、禁用、删除 B |
| 应用管理员窃取个人 Key | 管理后台查看/导出/代测 | 管理员 API 永不返回明文；代测仍必须 owner 授权 | 管理员响应、日志、导出均无 Key |
| 数据库泄露 | 读取 runtime_config/Secret 表 | envelope encryption；KEK 不在 DB；AAD 绑定 owner/connection | 只有 DB 备份无法解密；换行攻击失败 |
| 日志/trace 泄露 | 请求体、header、异常、APM | 专用 Secret 入口；header/body/query/原始错误不记录 | 全链路 secret canary 扫描为 0 |
| Prompt 注入索取 Key | Agent 要求打印环境变量/配置 | Agent 无 Key、无 SecretStore 权限，只持短期 broker token | 恶意 TXT/PDF 无法获得 Key |
| SSRF/云元数据 | 用户 base URL 指向内网或 metadata | 全 A/AAAA 校验、特殊地址拒绝、每次连接复核、代理纵深 | IPv4/IPv6/CGNAT/metadata 全拒绝 |
| DNS rebinding | 保存时公网，连接时私网 | 连接时重解析并由代理校验/固定目标 | 重绑定域名无法连接私网 |
| 重定向绕过 | 公网 30x 到内网/另一 origin | 首期完全禁用重定向 | 301/302/307/308 均不跟随 |
| 代理旁路 | `NO_PROXY`、直连 IP、IPv6/DNS | `trust_env=false`、容器内网、Smokescreen、出网防火墙 | 直连域名/IP/IPv6 均失败 |
| TLS 降级 | `verify=false` 或恶意 CA | HTTPS、证书/主机名校验；自定义 CA 管理员级 | 过期/错主机/自签证书默认拒绝 |
| 静默回退 | 个人 Key 失败后用平台 Key | Revision 固定 connection + generation；失败关闭 | 故障时无第二 Provider 调用 |
| 重试重复生成/扣费/工具副作用 | 超时后 SDK 自动重放 | 关闭 SDK 自动重试；`outcome_unknown`；工具幂等门 | 模糊断连只产生一个上游 attempt |
| 撤销后缓存仍可用 | Broker 缓存明文或旧令牌 | generation、短 TTL、撤销清缓存 | 撤销后下一调用必失败 |
| usage 缺失写 0 | 流中断或兼容端点不返回 | nullable + `unknown` + provenance | 无 usage 的响应不出现 0 token |
| 网关翻译丢语义 | Responses/Anthropic 工具转换 | 显式 translated、版本锁定、能力探测、损失清单 | 不支持能力失败关闭 |
| 本地删除但外部 Key 仍有效 | 只删 Mangrove 密文 | UI 明示并引导 Provider 撤销 | 删除确认和审计含该事实 |
| Provider 返回 URL 再取数 | 响应诱导访问内网/带凭证 URL | 二次 fetch 重新走 Policy；不转发认证头 | 恶意返回 URL 无法绕过 |

## 11. 最小实施门与验证证据

以下是尚未验证的实施门，不代表本报告授权开始编码：

1. **契约门**：四个 Adapter 有独立 fixture/contract test；Chat 不能被标成 Responses。
2. **Owner 门**：两个并发用户的保存、验证、调用、撤销、下载审计全隔离；管理员不能代用。
3. **Secret 门**：数据库、日志、异常、trace、任务 JSON、容器环境和 Delivery 的 canary 扫描均为
   0；KEK 轮换 rewrap 后仍可用，旧 KEK 删除前后行为符合恢复计划。
4. **同路门**：连接测试与正式任务经过同一个 Broker/Adapter/EgressPolicy；测试不能另取明文。
5. **网络门**：公网 allow；private/loopback/link-local/CGNAT/metadata、IPv4-mapped IPv6、
   DNS rebinding、30x、错误 TLS、环境代理和直连旁路全拒绝。
6. **撤销门**：撤销后下一次调用失败；旧短期令牌失败；在途调用被明确记录而不虚假承诺追回。
7. **回退门**：个人连接故障时没有平台、本地或其他协议的第二次调用。
8. **usage 门**：四协议原生 usage 正确归一；缺失和流中断为 unknown；gateway/local provenance 不
   混入 native；无金额字段。
9. **模糊失败门**：上游可能已接收后的断连不自动重放，状态为 `outcome_unknown`。
10. **UI 门**：用户始终看得见当前 connection、协议、最终 endpoint origin、外发数据类别、
    验证状态和失败原因；不会“不知所踪”。

## 12. 必须由用户确认的决策

| 决策 | 推荐默认 | 为什么必须人工确认 |
|---|---|---|
| D4 是否允许任意公网自定义 endpoint | 允许，但仅 HTTPS/443，并受严格 SSRF、DNS 与管理员禁用策略 | 决定外发面和运维责任 |
| 私网/LAN endpoint | 只允许管理员创建 `local/platform` 精确 allowlist；普通用户 BYOK 禁止 | 直接改变内网访问权限 |
| Base URL 输入语义 | 输入协议根地址，UI 预览最终 endpoint，Adapter 独占 operation path | 可能影响现有兼容网关 |
| 首期协议启用 | 四个枚举都建模；只对通过真实 contract/probe 的 Adapter 标 active | 枚举存在不等于生产可用 |
| Gemini 演进 | 首期仍按已确认的 `gemini_generate_content`；不自动增加 Interactions | Google 正推荐更新的 Interactions，但扩大枚举属业务范围变更 |
| LiteLLM | 首期不用作核心 Broker；以后仅显式 gateway adapter | 会引入翻译、回退、预算和额外服务 |
| KEK 托管 | 单机版本化 KEK + 可替换 `KeyWrappingPort` 起步；生产条件成熟再选 OpenBao/KMS | 成本、恢复和运维边界不同 |
| 删除语义 | 立即撤销并删除在线 ciphertext/wrapped DEK、清缓存、留审计墓碑；旧 WAL/备份按删除台账防复活并随保留期清除；明确要求用户去 Provider 撤销 Key | 普通 SQLite 行删除不能冒充全部旧备份瞬时密码学擦除；若要求后者需外部逐 Secret Key/HSM/端侧方案 |
| 外发确认粒度 | 每个 Revision + Connection + 数据类别 + 用途确认 | 属于数据含义与权限决策 |
| 连接验证 | 同 Broker 做一次极小真实生成，不以 model list 代替 | 可能产生少量 token 成本 |
| OpenAI Responses 存储 | 在兼容且不影响任务时显式 `store=false` | 会影响状态化能力与数据驻留 |
| 企业自签 CA/自定义端口 | 首期普通用户禁用；如需仅管理员策略开放 | 改变 TLS 与 SSRF 边界 |
| 普通 SSE | 作为当前 Pi Runtime 必需的内部传输透明转发；不建设用户可见实时功能，Live/Realtime 仍不做 | 若连 SSE 也禁用，个人外部连接不能直接供当前 Pi Runtime 使用，需另改 Adapter |

Google 当前仍完整记录 `generateContent`，同时在新能力文档中推荐 Interactions 作为更新接口；
因此这是演进风险，不应由调研自动改掉用户已经确认的枚举：
[Gemini API overview](https://ai.google.dev/api)、
[Function calling / API evolution](https://ai.google.dev/gemini-api/docs/generate-content/function-calling)。

## 13. 建议给下一阶段的输入

进入 `domain-modeling` 前，只需用户确认第 12 节，不需要先写实现。确认后下一阶段产物应是：

1. `ProviderConnection`、`CredentialSecret`、`AccessGrant`、`CapabilitySnapshot`、
   `ProviderUsage`、`EgressDecision` 的词汇和不变量；
2. 本地、平台、用户三种 scope 的权限矩阵；
3. 连接验证、激活、轮换、撤销、删除和运行调用的状态机；
4. native/translated、usage unknown、`outcome_unknown` 的正式语义；
5. Base URL 和最终 endpoint 的规范化契约；
6. 需要 ADR 固化的 KEK、LiteLLM、Smokescreen 与私网策略决策。

在这些人工决策完成前，不应进入实现，也不应把当前 `runtime_config` 遮罩、数据库密码 Fernet 或
Pi 的 `api_key` 传递方式描述为 D4 已满足。
