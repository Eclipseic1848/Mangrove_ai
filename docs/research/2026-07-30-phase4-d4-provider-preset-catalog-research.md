# Phase 4 D4：首版 ProviderPreset 官方目录调研

- 核对日期：2026-07-30
- 状态：调研结论，供 `ProviderPreset` 首版目录冻结使用；不是代码实施或外部调用授权
- 范围：DeepSeek、阿里云百炼 Qwen（中国站默认）、OpenAI Responses、
  Anthropic Messages、Gemini `generateContent`、月之暗面 Kimi、智谱 GLM
- 上游契约：
  `docs/plans/2026-07-30-phase4-d4-provider-connection-and-controlled-egress-contract.md`

## 1. 结论先行

### 1.1 已验证事实

1. 五家当前都有官方公开的模型 ID、Base URL 或请求端点资料，但模型生命周期、别名指向和
   地域入口变化很快。官方“列出”只证明目录存在，不证明当前用户的 Key 有权限，也不证明
   Mangrove 的工具、结构化输出和多媒体链路已经通过真实验证。
2. DeepSeek 当前正式列出的模型只有 `deepseek-v4-flash` 和
   `deepseek-v4-pro`；旧别名 `deepseek-chat`、`deepseek-reasoner` 已注明于
   2026-07-24 15:59 UTC 退役，不应进入 2026-07-30 的首版 Preset。
   [DeepSeek 模型与价格](https://api-docs.deepseek.com/quick_start/pricing)、
   [DeepSeek 更新日志](https://api-docs.deepseek.com/updates/)
3. 百炼当前面向 Agent、文档处理等场景明确推荐 `qwen3.7-plus`，同时列出
   `qwen3.7-max` 和 `qwen3.7-flash`；三者都在中国站 Responses API 支持清单中。
   百炼同时提供可冻结的日期快照 ID。
   [百炼文本生成模型](https://help.aliyun.com/zh/model-studio/text-generation-model)、
   [百炼 Responses API](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses)
4. OpenAI 当前模型选择页列出 GPT-5.6 Sol、Terra、Luna：Sol 面向旗舰能力，Terra 平衡
   智能与成本，Luna 面向成本敏感和高吞吐；官方建议这些模型使用 Responses API。
   [OpenAI Models](https://developers.openai.com/api/docs/models)、
   [OpenAI Model guidance](https://developers.openai.com/api/docs/guides/latest-model)
5. Anthropic 当前模型页列出 Claude Fable 5、Opus 5、Sonnet 5 和 Haiku 4.5；
   Sonnet 5 被描述为速度与智能的最佳组合。Claude 4.6 及以后不带日期的正式 model ID
   也是固定快照，不是会漂移的 evergreen alias。
   [Anthropic Models overview](https://platform.claude.com/docs/en/about-claude/models/overview)、
   [Model IDs and versioning](https://platform.claude.com/docs/en/about-claude/models/model-ids-and-versions)
6. Gemini 当前稳定通用模型包括 `gemini-3.6-flash` 和
   `gemini-3.5-flash-lite`；`gemini-3.1-pro-preview` 仍是 Preview。
   Google 把 `generateContent` 文档标为 Legacy，但截至核对日仍用
   `gemini-3.6-flash` 提供官方调用示例。用户已经确认首版
   `gemini_generate_content`，本调研不能自动改成 Interactions。
   [Gemini Models](https://ai.google.dev/gemini-api/docs/models)、
   [Gemini release notes](https://ai.google.dev/gemini-api/docs/changelog)、
   [GenerateContent getting started](https://ai.google.dev/gemini-api/docs/generate-content/get-started)
7. Kimi 官方目录当前列出 `kimi-k3`、`kimi-k2.7-code`、
   `kimi-k2.7-code-highspeed`、`kimi-k2.6`、`kimi-k2.5`；其中旧 K2 系列和
   `kimi-latest` 已明确下线，不能继续作为稳定 Preset。官方 API 采用
   OpenAI Chat Completions 兼容协议，并提供 `/v1/models`。
   [Kimi 模型目录](https://platform.kimi.ai/docs/models)、
   [Kimi API 总览](https://platform.kimi.ai/docs/api/overview)
8. 智谱当前文本目录同时包含 GLM-5.2、GLM-5.1、GLM-5、GLM-4.7 等多个代际和
   高速/免费变体；官方 HTTP API 使用 Bearer API Key 和
   `/api/paas/v4/chat/completions`。这进一步说明 Mangrove 应维护少量推荐目录，
   而不是把完整厂商模型广场直接暴露给普通用户。
   [智谱模型概览](https://docs.bigmodel.cn/cn/guide/start/model-overview)、
   [智谱 HTTP API](https://docs.bigmodel.cn/cn/guide/develop/http/introduction)

### 1.2 平台选择建议

首版采用“一个平衡默认模型 + 一到两个有明确角色的候选模型”，不把厂商完整模型广场搬进
Mangrove：

| Preset | 推荐默认模型 | 少量候选 | 默认原生协议 | 默认 Base URL |
|---|---|---|---|---|
| DeepSeek | `deepseek-v4-flash` | `deepseek-v4-pro` | `openai_chat_completions` | `https://api.deepseek.com` |
| 阿里百炼 Qwen（中国站） | `qwen3.7-plus-2026-05-26` | `qwen3.7-max-2026-06-08`、`qwen3.7-flash-2026-07-15` | `openai_responses` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| OpenAI | `gpt-5.6-terra` | `gpt-5.6-sol`、`gpt-5.6-luna` | `openai_responses` | `https://api.openai.com/v1` |
| Anthropic | `claude-sonnet-5` | `claude-opus-5`、`claude-haiku-4-5-20251001` | `anthropic_messages` | `https://api.anthropic.com` |
| Gemini | `gemini-3.6-flash` | `gemini-3.5-flash-lite` | `gemini_generate_content` | `https://generativelanguage.googleapis.com/v1beta` |
| 月之暗面 Kimi | `kimi-k3` | `kimi-k2.6` | `openai_chat_completions` | `https://api.moonshot.ai/v1` |
| 智谱 GLM | `glm-5.2` | `glm-5.1-highspeed`、`glm-4.7-flash` | `openai_chat_completions` | `https://open.bigmodel.cn/api/paas/v4` |

这张表是 Mangrove 的目录选择，不是厂商替 Mangrove 做出的默认选择。选择原则是：

- 默认模型覆盖通用 Agent、文档和数据处理，同时避免默认落到最高成本档；
- 候选只提供“更强质量”和“更高效率”这类用户能理解的角色；
- 有官方快照 ID 时优先冻结快照，防止 Preset 版本未变但模型权重漂移；
- Preview、Realtime、图像生成专用、音频专用、代码工具套餐专用模型不进入普通首版目录；
- 所有模型必须在该用户首次选择时经同一路径做最小真实验证，验证前 capability 仍是
  `unknown`。

### 1.3 易变项

以下字段不能写死进 ADR，也不能从本报告永久继承：

- 推荐模型和候选模型 ID；
- model alias 指向、模型上下架和 Preview/GA 状态；
- 百炼地域、业务空间专属域名和 Key 适用范围；
- Provider 对特定账户、地域、用量层级开放的模型；
- 工具调用、结构化输出、图像/文件和普通 HTTP Token 流的实际能力；
- Provider 的参数支持和兼容程度。

Preset 必须记录 `preset_version`、`source_checked_at`、官方 `source_url` 和模型生命周期。
目录刷新只能创建新版本；不得自动改写既有 TaskRevision。

## 2. 建议冻结的首版目录

### 2.1 DeepSeek

#### 已验证事实

- OpenAI-compatible Base URL 是 `https://api.deepseek.com`，Chat Completions 资源路径是
  `/chat/completions`；官方首个调用示例直接使用这一组合。
- DeepSeek 也提供 `https://api.deepseek.com/anthropic` 的 Anthropic-compatible 路线，
  但这不要求首版为同一 Provider 同时暴露两条默认路线。
- 当前模型清单为 `deepseek-v4-flash`、`deepseek-v4-pro`。二者均支持 thinking/non-thinking、
  JSON output 和 tool calls；V4 Flash 被官方描述为快速、高效、经济，简单 Agent 任务接近
  V4 Pro；V4 Pro 面向更强推理和 Agent 能力。
- 旧 `deepseek-chat`、`deepseek-reasoner` 已越过官方退役日期。

官方来源：

- [Your First API Call](https://api-docs.deepseek.com/guides/function_calling/)
- [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing)
- [DeepSeek V4 发布说明](https://api-docs.deepseek.com/news/news260424/)
- [List Models](https://api-docs.deepseek.com/api/list-models)

#### 平台选择建议

```text
preset_id: deepseek-cn
default_route_id: deepseek-openai-chat
route.api_format: openai_chat_completions
route.transport_mode: native
route.base_url: https://api.deepseek.com
route.operation_path: /chat/completions
route.auth_profile: bearer
default_model_id: deepseek-v4-flash
model_catalog:
  - deepseek-v4-flash  # 默认：平衡速度、成本和通用 Agent 能力
  - deepseek-v4-pro    # 候选：复杂推理和质量优先
```

这里的 `native` 指 Mangrove 直接发送 DeepSeek 官方支持的 OpenAI Chat 线协议，不表示
DeepSeek 是 OpenAI 第一方服务。

首版不同时发布 DeepSeek Anthropic route，理由是：

- 两条路线使用同一模型但请求语义不同，会增加普通用户困惑；
- DeepSeek 官方说明 Anthropic 路线对不支持的模型名可能自动映射到
  `deepseek-v4-flash`，不符合 Mangrove “模型不匹配时失败关闭”的默认原则；
- 后续如 Pi 场景确实需要，可创建新的 route 和 Preset 版本，不能暗中切换。

#### 易变项

DeepSeek V4 发布页仍使用 “Preview” 字样，且没有日期快照 ID；当前模型虽已进入官方价格和
List Models 页面，仍应每周检查更新日志，并在真实 Key 验证后才把 Preset 标为 active。

### 2.2 阿里云百炼 Qwen（中国站默认）

#### 已验证事实

- 百炼中国站共享 OpenAI-compatible Base URL 是
  `https://dashscope.aliyuncs.com/compatible-mode/v1`，支持跨业务空间 API Key。
- 官方更推荐生产使用
  `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`，因为它提供
  业务空间级隔离、吞吐和时延优势；该地址需要额外的 Workspace ID。
- 原共享域名仍可使用。它是当前“用户只选择 Provider 并填写 Key”而无需再理解
  Workspace ID 的唯一官方静态中国站入口。
- Responses API 的资源路径为 `/responses`；当前中国站支持清单包含
  Qwen3.7 Max、Plus 和 Flash 及其日期快照。
- 官方针对 Agent、聊天、文档处理推荐 `qwen3.7-plus`；Max 偏质量，Flash 偏效率。

官方来源：

- [Base URL 总览](https://help.aliyun.com/zh/model-studio/base-url)
- [百炼文本生成模型](https://help.aliyun.com/zh/model-studio/text-generation-model)
- [创建 Responses 响应](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses)
- [Chat Completions 兼容接口](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)

#### 平台选择建议

```text
preset_id: aliyun-bailian-qwen-cn
default_route_id: qwen-cn-openai-responses
route.api_format: openai_responses
route.transport_mode: native
route.base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
route.operation_path: /responses
route.auth_profile: bearer
default_model_id: qwen3.7-plus-2026-05-26
model_catalog:
  - qwen3.7-plus-2026-05-26  # 默认：通用平衡
  - qwen3.7-max-2026-06-08   # 候选：质量优先
  - qwen3.7-flash-2026-07-15 # 候选：效率优先
```

选择日期快照而不是 `qwen3.7-plus` 等浮动名称，是为了让 `preset_version` 真正冻结行为。
UI 仍显示“Qwen 3.7 Plus（推荐）”，不把日期 ID 暴露给新手。

选择 Responses 而不是 Chat 作为默认 route，是因为：

- 用户已确认 Responses 为首版原生格式之一；
- 百炼当前对 Qwen3.7 三档都列出 Responses 支持；
- 官方把内置工具、简化上下文和类型化输出作为该路线的当前能力。

这不代表 Chat route 应删除。若现有业务 fixture 证明 Responses Adapter 尚未达到生产门，
应把 Qwen Preset 保持 draft，而不是静默降为 Chat。发布 Chat route 需要一个新的显式 route
决策。

#### 易变项和人工决策

百炼官方生产推荐与“用户只填 Key”的产品要求存在真实张力：

- 首版建议用仍受支持的共享域名，保持零额外配置；
- 后续可增加“百炼 Qwen（中国站·业务空间）”Preset，要求用户选择/填写 Workspace ID；
- 不能从 API Key 推断 Workspace ID，也不能在后台自动换域名。

是否首版就增加 Workspace ID 是业务交互和数据驻留决策，必须由用户确认；本调研不扩大字段。

### 2.3 OpenAI Responses

#### 已验证事实

- OpenAI API 根地址是 `https://api.openai.com/v1`，Responses 资源路径是 `/responses`，
  使用 Bearer API Key。
- 官方当前模型目录建议 GPT-5.6 Sol 用于旗舰复杂任务、Terra 用于能力与成本平衡、Luna
  用于成本敏感和高吞吐；三者都支持 Responses、普通 HTTP streaming、function calling、
  structured outputs 和 image input。
- `gpt-5.6` 是指向 Sol 的 alias；如果 UI 同时列出 Sol，不应再列一个含义重复且容易误解的
  `gpt-5.6`。
- OpenAI 建议生产 API 使用 GPT-5.6，而不是 ChatGPT 的 `chat-latest`。

官方来源：

- [OpenAI API Overview](https://developers.openai.com/api/reference/overview)
- [OpenAI Models](https://developers.openai.com/api/docs/models)
- [GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra)
- [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
- [Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)

#### 平台选择建议

```text
preset_id: openai
default_route_id: openai-responses
route.api_format: openai_responses
route.transport_mode: native
route.base_url: https://api.openai.com/v1
route.operation_path: /responses
route.auth_profile: bearer
default_model_id: gpt-5.6-terra
model_catalog:
  - gpt-5.6-terra # 默认：能力/成本平衡
  - gpt-5.6-sol   # 候选：复杂任务质量优先
  - gpt-5.6-luna  # 候选：高吞吐效率优先
```

默认选择 Terra 是 Mangrove 的平衡策略；OpenAI 自身在“不确定从哪里开始”时首先推荐 Sol。
这两个结论不冲突，但 UI 文案必须写成“平台推荐”，不能写成“OpenAI 官方默认”。

#### 易变项

- 用户组织/项目的模型权限和 rate limit 可能不同；
- 模型 ID 和别名会继续演进；
- `store=false`、工具和多媒体是运行参数/能力，不属于这份模型目录的静态保证；
- 模型通过列表或文档出现后，仍需用用户自己的 Key 对目标 model + Responses 做最小验证。

### 2.4 Anthropic Messages

#### 已验证事实

- Claude API Base URL 是 `https://api.anthropic.com`，Messages 资源路径为
  `/v1/messages`，官方请求使用 `x-api-key` 和 `anthropic-version`。
- 当前模型目录中：
  - `claude-sonnet-5`：速度与智能的最佳组合；
  - `claude-opus-5`：复杂 Agent、企业任务和长程工作；
  - `claude-haiku-4-5-20251001`：最快和成本敏感；
  - `claude-fable-5`：最高能力、长运行 Agent，但不是首版通用目录必需项。
- `claude-sonnet-5`、`claude-opus-5` 这种 4.6 以后不带日期的 ID 也是固定模型快照。

官方来源：

- [Anthropic Models overview](https://platform.claude.com/docs/en/about-claude/models/overview)
- [Choosing a model](https://platform.claude.com/docs/en/about-claude/models/choosing-a-model)
- [Model IDs and versioning](https://platform.claude.com/docs/en/about-claude/models/model-ids-and-versions)
- [Create a Message](https://platform.claude.com/docs/en/api/messages/create)
- [Claude API quickstart](https://platform.claude.com/docs/en/get-started)

#### 平台选择建议

```text
preset_id: anthropic
default_route_id: anthropic-messages
route.api_format: anthropic_messages
route.transport_mode: native
route.base_url: https://api.anthropic.com
route.operation_path: /v1/messages
route.auth_profile: anthropic_x_api_key
default_model_id: claude-sonnet-5
model_catalog:
  - claude-sonnet-5            # 默认：速度/智能平衡
  - claude-opus-5              # 候选：复杂 Agent 质量优先
  - claude-haiku-4-5-20251001  # 候选：速度/成本优先
```

首版不列 `claude-fable-5`：它面向最高能力和长运行 Agent，成本及运行特性都偏离普通默认任务。
这不是“不支持”，而是控制首版选择复杂度；待真实任务 eval 证明有明确增益后再新建 Preset
版本。

#### 易变项

模型生命周期和弃用日必须继续读取 Anthropic 官方 deprecations 页面。即使 model ID 固定，
服务基础设施、安全分类器等仍可能变化；固定 ID 不能替代真实回归。

### 2.5 Gemini `generateContent`

#### 已验证事实

- Gemini API 根地址为 `https://generativelanguage.googleapis.com/v1beta`，
  `generateContent` 资源路径为 `/models/{model}:generateContent`，认证头是
  `x-goog-api-key`。
- `gemini-3.6-flash` 和 `gemini-3.5-flash-lite` 于 2026-07-21 进入 GA：
  - 3.6 Flash 平衡速度和智能，面向 Agent 与多模态任务；
  - 3.5 Flash-Lite 面向低延迟、高吞吐和成本敏感任务。
- `gemini-3.1-pro-preview` 仍是 Preview，官方没有公布 shutdown date，但 Preview 的通知和
  限流稳定性弱于 GA。
- Google 当前把 Generate Content API 标为 Legacy，并推荐新能力迁移 Interactions；
  但 Generate Content 官方入门仍用 `gemini-3.6-flash`，所以已确认路线当前仍可建模。

官方来源：

- [Gemini API reference](https://ai.google.dev/api)
- [GenerateContent API](https://ai.google.dev/api/generate-content)
- [GenerateContent getting started](https://ai.google.dev/gemini-api/docs/generate-content/get-started)
- [Gemini Models](https://ai.google.dev/gemini-api/docs/models)
- [Gemini deprecations](https://ai.google.dev/gemini-api/docs/deprecations)
- [Gemini release notes](https://ai.google.dev/gemini-api/docs/changelog)

#### 平台选择建议

```text
preset_id: google-gemini
default_route_id: gemini-generate-content
route.api_format: gemini_generate_content
route.transport_mode: native
route.base_url: https://generativelanguage.googleapis.com/v1beta
route.operation_path: /models/{model}:generateContent
route.auth_profile: google_api_key_header
default_model_id: gemini-3.6-flash
model_catalog:
  - gemini-3.6-flash       # 默认：GA，Agent/多模态平衡
  - gemini-3.5-flash-lite  # 候选：GA，高吞吐效率优先
```

`gemini-3.1-pro-preview` 不进入普通首版目录。若用户要求质量优先的 Preview 候选，应先给
`ProviderPreset.model_catalog[]` 增加清晰的 `lifecycle_stage=preview`、风险提示和下线监控，
再单独确认；不能把 Preview 和 GA 当成同一稳定等级。

#### 易变项

- Google 的 `latest` alias 会被热切换，不适合冻结 Preset；首版使用明确 stable model ID。
- `generateContent` 已进入 Legacy 演进路线。未来迁移 Interactions 会改变
  `api_format`、Adapter 和能力语义，不能只换 Base URL。
- 当前用户已经确认 `gemini_generate_content`；本报告只登记演进风险，不请求或代替用户改
  业务范围。

### 2.6 月之暗面 Kimi

#### 已验证事实

- 官方 API Base URL 是 `https://api.moonshot.ai/v1`，采用 OpenAI Chat Completions
  兼容请求/响应格式，支持 `/v1/models` 查询当前 Key 可见模型。
- 当前官方模型页把 `kimi-k3` 描述为当前能力最强的通用模型；`kimi-k2.6` 支持文本、
  视觉、思考/非思考、对话与 Agent 任务。
- 模型生命周期变化很快：旧 K2 系列和 `kimi-latest` 已在官方页明确下线，因此不能把
  厂商 alias 当作长期稳定目录。

官方来源：

- [Kimi 模型目录](https://platform.kimi.ai/docs/models)
- [Kimi API 总览](https://platform.kimi.ai/docs/api/overview)
- [Kimi List Models](https://platform.kimi.ai/docs/api/list-models)

#### 平台选择建议

```text
preset_id: moonshot-kimi
default_route_id: kimi-openai-chat
route.api_format: openai_chat_completions
route.transport_mode: native
route.base_url: https://api.moonshot.ai/v1
route.operation_path: /chat/completions
route.auth_profile: bearer
default_model_id: kimi-k3
model_catalog:
  - kimi-k3    # 默认：当前通用能力优先
  - kimi-k2.6  # 候选：成熟通用/Agent 路线
```

`kimi-k2.7-code` 是明确的 Coding 专用模型，不应仅因为“更新”就混入当前通用数据任务的
普通下拉框；后续有代码类 TaskProfile 时再以目录新版本加入。

### 2.7 智谱 GLM

#### 已验证事实

- 官方 HTTP API Base URL 是 `https://open.bigmodel.cn/api/paas/v4`，对话资源路径是
  `/chat/completions`，使用 Bearer API Key。
- 当前模型概览把 GLM-5.2 定位为 1M 上下文和复杂长程工程任务模型，同时保留
  GLM-5.1、GLM-5、GLM-4.7 及高速/免费变体。
- 完整目录同时包含文本、视觉、图像、视频和音视频模型；当前 Phase 只应选择文本/Agent
  模型，不把多媒体模型混入同一选择面。

官方来源：

- [智谱模型概览](https://docs.bigmodel.cn/cn/guide/start/model-overview)
- [智谱 HTTP API](https://docs.bigmodel.cn/cn/guide/develop/http/introduction)
- [GLM-5.2](https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2)

#### 平台选择建议

```text
preset_id: zhipu-glm
default_route_id: zhipu-openai-chat
route.api_format: openai_chat_completions
route.transport_mode: native
route.base_url: https://open.bigmodel.cn/api/paas/v4
route.operation_path: /chat/completions
route.auth_profile: bearer
default_model_id: glm-5.2
model_catalog:
  - glm-5.2            # 默认：复杂长程任务
  - glm-5.1-highspeed  # 候选：速度优先
  - glm-4.7-flash      # 候选：免费/轻量入口
```

具体模型 ID 必须在实现开始日再次读取官方目录，并用用户自己的 Key 分模型验证；文档出现
不等于该账户当前有调用权限。

## 3. 推荐的最小目录元数据

### 已验证事实

当前 D4 规格中的 `ProviderPreset` 已含 `preset_version`、routes、model catalog、
`catalog_source` 和 status，但仅凭模型 ID 无法表达 Preview、退役和资料新鲜度。

### 平台选择建议

在实现规格阶段为目录补足以下非秘密元数据；这属于建议，尚未获实现授权：

```text
ProviderPreset
  preset_id
  preset_version
  source_checked_at
  source_urls[]
  region_code?
  requires_workspace_id: bool

model_catalog[]
  model_id
  display_name
  role: balanced | quality | efficiency
  lifecycle_stage: stable | preview | provider_current | deprecated
  source_url
  source_checked_at
  availability_status: listed | probe_required | verified | unavailable
```

约束：

1. `listed` 只表示官方目录存在；创建个人 ModelConnection 前必须变为该用户 Key 的
   `verified`。
2. capability hints 只用于说明，不替代 CapabilitySnapshot 的真实探测。
3. 厂商完整 `/models` 响应不得自动覆盖平台目录。
4. 目录更新创建新 `preset_version`；旧 Revision 保持原 model ID。
5. 模型下线后，连接进入“需要重新选择模型”，不得自动选择新默认。
6. 不在 Preset 保存价格、余额或预算；“质量/平衡/效率”只是目录角色。

## 4. 目录刷新与发布门

### 易变项治理建议

- 每周读取五家官方模型页和 deprecation/changelog；正式发布前再即时复核。
- 对官方页面内容做差异告警，但不自动发布新 Preset。
- 若默认模型退役、变 Preview、路径改变或协议被标 Legacy，创建候选新版本并等待人工确认。
- 业务空间、地域和套餐专属入口分别建 Preset，不在运行时根据 Key 或错误码猜入口。
- 不使用 `*-latest`、ChatGPT 产品模型或临时实验模型作为默认目录项。

### 首版发布证据

每个默认模型至少需要：

1. 官方模型页、Base URL/端点和认证来源仍有效；
2. 使用该 Provider 的真实用户 Key，经正式 ConnectionBroker 同一路径完成最小文本调用；
3. 普通 HTTP Token 流、非流式响应、错误归一和原生 usage 至少各有一条验证证据；
4. Mangrove 所需 tool call 做一次无副作用 fixture 验证；
5. Key、请求体和响应正文未进入日志、Event、Evidence、Delivery 或 Agent 容器；
6. Preset 版本、精确模型 ID、Adapter 版本和验证日期冻结。

候选模型在用户首次选择时再验证，符合 D4 已确认的成本边界；未验证候选显示“首次使用需验证”，
不能显示“可用”。

## 5. 尚需用户确认的两项

1. **百炼 Workspace ID：**首版是否接受共享中国站域名以实现“只填 Key”，还是增加
   Workspace ID 并默认使用官方推荐的业务空间专属域名。推荐首版共享域名，后续另建
   “中国站·业务空间”Preset。
2. **Gemini Preview：**是否把 `gemini-3.1-pro-preview` 作为带明显 Preview 标识的质量候选。
   推荐首版只放两个 GA 模型，等 Pro 稳定版或真实强需求再扩展。

除此之外，表 1.2 可作为首版 catalog 的推荐冻结输入；所有内容仍需在实施日前复核并用真实 Key
验证，不能把本次文档核对表述为连接已通过。
