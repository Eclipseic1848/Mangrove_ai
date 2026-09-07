# #159 云端与本地模型配置任务单

## 目标

非技术用户可在既有设置页选择服务商、取得密钥、选择模型、明确测试并保存，然后设为新任务默认。用户已有的本地或局域网模型使用同一入口；#127 接续原任务返回和输入旁选择。

## 允许输入

现有 ModelConnectionsPanel、版本化 Provider 目录、Broker、Vault、Grant、资格台账；下列官方资料；用户明确填写的服务商、地域、业务空间、模型和密钥；管理员明确指定的本地服务地址。真实密钥与付费或本地服务调用须具体授权。

## 要求输出

- 八家版本化目录、易懂名称与适用任务、推荐依据说明、官方 Key 入口、资料日期和过期提示。
- 新连接只测试所选模型；其他型号待验证且可逐项验证。保存不等于工具资格，不自动换模型或重复收费。
- 百炼按密钥所属地域选择北京或新加坡，并填写业务空间；地址来自可信模板。其他地域及专属套餐走管理员明确配置，不自动猜测。
- 本地先启动 API 服务，再填精确地址，读取模型列表或手填 ID，最后明确测试。普通用户看到管理员登记路径；不扩大权限。
- 安全错误提示、保存中防重复提交、失败保留输入、切换服务商或地域清除密钥；复用旧连接与历史任务身份。

## 验收标准

1. 官方目录矩阵与精确 ID 可追溯，至少八家各三个独立型号，实验型号明确标识。目录核验不等于账户可用。
2. 八家浏览器配置、管理员地域配置、本地无 Key、待验证型号、失败处理、重复提交及明暗/窄屏/键盘回归通过。
3. 默认单次所选模型测试、不隐式探测四协议、不自动改选；Owner 隔离、账户停用、Vault、冻结模型、DNS/重定向/元数据防护回归通过。
4. Standards 与 Spec 两轴审查问题关闭，同提交 CI 通过；真实云端账号与真实本地服务另记证据，未完成不关闭 #159。

## 边界

不下载、部署或训练模型，不安装运行时，不扫描设备，不改防火墙，不操作生产秘密与迁移。localhost 指 Mangrove 后端的电脑或容器；跨设备必须填写后端可达且获准的地址。无鉴权私网模型允许 Key 为空，公网仍要求 HTTPS 与 Key。架构决策见 [ADR-0041](../adr/0041-selected-model-onboarding-and-local-discovery.md)。

## 官方目录核验：2026-09-07，版本 2026-09-07.1

“当前”指官方当前目录/价格表仍列出，不保证任意地域和账户权限。日期未由本轮材料明确给出时不猜测；已有日期写在型号或说明中。推荐由平台根据官方定位给出用途起点，未经用户任务评测，不保证性能或最低费用。

| 服务商 | 精确 API 型号 | 资料与状态 | 使用协议/适用范围 |
| --- | --- | --- | --- |
| DeepSeek | `deepseek-v4-flash`、`deepseek-v4-pro`、`deepseek-v4-flash-vision-exp` | [官方文档](https://api-docs.deepseek.com/)；Flash 0731、Pro 0813；Vision 明确实验版 | Chat Completions；官方 API 账户 |
| 阿里百炼 | `qwen3.7-plus-2026-05-26`、`qwen3.8-max-0902`、`qwen3.8-flash` | [官方文本模型](https://help.aliyun.com/zh/model-studio/text-generation-model/)；三个独立模型，不用别名凑数 | Responses；[按量地域与业务空间地址](https://help.aliyun.com/zh/model-studio/base-url)，北京/新加坡；不混用 Token Plan / Coding Plan |
| OpenAI | `gpt-6-astra`、`gpt-5.6-terra`、`gpt-5.6-sol`、`gpt-5.6-luna` | [官方模型目录](https://developers.openai.com/api/docs/models/all)；当前目录型号 | 原生 Responses；API 账户与聊天订阅分开 |
| Anthropic | `claude-fable-5-1`、`claude-sonnet-5`、`claude-opus-5`、`claude-haiku-4-5-20251001` | [官方模型概览](https://platform.claude.com/docs/en/models/overview)；当前目录型号 | 原生 Messages；Claude API 控制台 |
| Google Gemini | `gemini-3.8-flash`、`gemini-3.7-flash`、`gemini-3.6-flash`、`gemini-3.5-flash-lite` | [官方模型目录](https://ai.google.dev/gemini-api/docs/models)；稳定型号 | generateContent；AI Studio Gemini API，非 Vertex 自动切换 |
| Moonshot Kimi | `kimi-k3`、`kimi-k2.7-code`、`kimi-k2.6` | [官方模型目录](https://platform.kimi.ai/docs/models)；不推荐已于 8 月 31 日退役的 K2.5 / moonshot-v1 | Chat Completions；国际 `api.moonshot.ai`，国内/Code 套餐不混用 |
| 智谱 GLM | `glm-5.3`、`glm-5.3-flash`、`glm-5.2` | [模型概览](https://docs.bigmodel.cn/cn/guide/start/model-overview)、[发布记录](https://docs.bigmodel.cn/cn/update/new-releases)；分别 8 月 19 日、8 月 26 日、6 月 16 日 | Chat Completions；中国 BigModel 通用 API，非国际 Z.ai / Coding 套餐 |
| xAI Grok | `grok-4.6`、`grok-4.5`、`grok-4.3` | [官方价格目录](https://docs.x.ai/developers/pricing)、[发布记录](https://docs.x.ai/developers/release-notes)；4.6 于 8 月 12 日、4.5 于 7 月 8 日；三项仍列出 | 原生 Responses；xAI API 账户 |

百炼协议与上述三个型号另据[官方 Responses 兼容说明](https://help.aliyun.com/zh/model-studio/compatibility-with-openai-responses-api)核对：使用 `/compatible-mode/v1/responses`，未切换为 Chat Completions。业务空间专属域名需要匹配该空间的 Key；旧共享域名仍保留给已有连接。

## 本地服务指引

| 服务 | 地址示例 | 官方依据 |
| --- | --- | --- |
| Ollama | `http://localhost:11434/v1` | [OpenAI 兼容接口](https://docs.ollama.com/api/openai-compatibility)，只承诺已实现的兼容范围 |
| LM Studio | `http://localhost:1234/v1` | [开发服务接口](https://lmstudio.ai/docs/developer/openai-compat)，须先启动 API 服务 |
| vLLM / 兼容服务 | `http://localhost:8000/v1` | [在线服务](https://docs.vllm.ai/en/latest/serving/online_serving/)，模型名及鉴权以该服务实际配置为准 |

这些地址只适用于后端与模型位于同一环境；不是从浏览器自动访问用户电脑。`GET /models` 不支持时可手填 ID；列表成功不证明推理或工具调用成功。

## 目录维护与验收记录

维护者在更新目录前核对官方当前目录、退役说明、精确 ID、地区/套餐、协议和 Key 入口；一起更新目录版本、核验日期与本表。超过 30 天向用户提示复核，不增加后台收费探测。退役型号从新推荐中移除，不改历史冻结记录；旧连接重验仍可收到服务商实际退役/无权限结果。

工程测试、浏览器截图、审查与真实服务证据按 #159 分别记录。没有真实账号和真实本地服务调用证据时，保持工单开放。
