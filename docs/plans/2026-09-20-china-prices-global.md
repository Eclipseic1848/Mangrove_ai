# 国际模型官方中国区报价核验

核验日期：2026-09-20；口径：中国大陆区域官方 API 报价。只读取公开官网，未调用模型、登录账号或修改产品代码。

## 结论

本次 15 个精确型号均能在厂商官方 API 文档确认，但均未核实到官方中国大陆区域人民币报价。不能把国际美元价乘汇率、第三方转发价或其他近似型号价格写成中国区官方价格；缺失值应保持“未核实”，不能填零。

OpenAI、Anthropic、Gemini 的官方 API 支持地区列表没有中国大陆。xAI 的地区文档公开全球及美国端点，未给出中国区端点或中国区报价；这不等同于已经核实其中国大陆账号可用性。

## 逐型号结果

下表 USD 数字仅作为找到精确型号及国际报价的证据，单位为美元／百万 Token，顺序为输入／输出；不是中国区价格，也不是完整计费配置。

| 厂商 | 精确 API 型号 | 国际价格证据 | 官方中国区人民币价 | 来源 |
| --- | --- | --- | --- | --- |
| OpenAI | `gpt-6-astra` | Standard 短上下文 10／50 | 未核实 | O1、O2 |
| OpenAI | `gpt-5.6-terra` | Standard 短上下文 2／12 | 未核实 | O1、O2 |
| OpenAI | `gpt-5.6-sol` | Standard 短上下文 4／20 | 未核实 | O1、O2 |
| OpenAI | `gpt-5.6-luna` | Standard 短上下文 0.20／1.20 | 未核实 | O1、O2 |
| Anthropic | `claude-fable-5-1` | 基础输入／输出 10／50 | 未核实 | A1、A2、A4 |
| Anthropic | `claude-sonnet-5` | 基础输入／输出 2／10 | 未核实 | A1、A3、A4 |
| Anthropic | `claude-opus-5` | 基础输入／输出 5／25 | 未核实 | A1、A3、A4 |
| Anthropic | `claude-haiku-4-5-20251001` | Haiku 4.5 基础输入／输出 1／5；日期 ID 由型号版本文档确认 | 未核实 | A1、A3、A4 |
| Google | `gemini-3.8-flash` | Standard 0.75／3.75，截止 2026-12-31 | 未核实 | G1、G2 |
| Google | `gemini-3.7-flash` | Standard 0.75／3.75，截止 2026-12-31 | 未核实 | G1、G2 |
| Google | `gemini-3.6-flash` | Standard 0.75／3.75，截止 2026-12-31 | 未核实 | G1、G2 |
| Google | `gemini-3.5-flash-lite` | Standard 0.30／2.50 | 未核实 | G1、G2 |
| xAI | `grok-4.6` | 全球短上下文 2／6 | 未核实 | X1、X2 |
| xAI | `grok-4.5` | 全球短上下文 2／6 | 未核实 | X1、X2 |
| xAI | `grok-4.3` | 全球短上下文 1.25／2.50 | 未核实 | X1、X2 |

## 价格边界

- OpenAI 存在长上下文、缓存读取、缓存写入和处理档位差异；不能只用上表两列实现完整计费。O1 的旧入口 `openai.com/api/pricing/` 本次跳转商业订阅页，因此以开发者 API 定价页为依据。
- Anthropic A1 明确所有价格为 USD；缓存写入、读取分别计费，不能并入普通输入价。
- Gemini G1 明确付费档单位为 USD／百万 Token。3.8、3.7、3.6 Flash 在 2027-01-01 起 Standard 输入／输出变为 1.50／7.50；另有缓存存储时长、工具和其他处理档位费用。
- xAI X1 明确所有价格为 USD。三个目标型号达到 200k 提示 Token 后按长上下文费率对该请求所有 Token 计费；美国区域端点 Token 费率比全球高 10%。这些均不构成中国区价格。

## 一手来源

- O1：[OpenAI API Pricing](https://developers.openai.com/api/docs/pricing)：精确模型 ID、Standard 及其他处理档位、长短上下文价格。
- O2：[OpenAI API 支持国家和地区](https://help.openai.com/en/articles/5347006-openai-api-supported-countries-and-territories)：明确未列地区不受 API 支持；列表未列中国大陆。
- A1：[Claude API Pricing](https://platform.claude.com/docs/en/about-claude/pricing)：USD 价格与缓存费率。
- A2：[Claude Fable 5.1](https://platform.claude.com/docs/en/models/fable-5-1/overview)：精确 `claude-fable-5-1` ID。
- A3：[Claude Model IDs and versioning](https://platform.claude.com/docs/en/about-claude/models/model-ids-and-versions)：精确 Sonnet 5、Opus 5、Haiku 日期 ID。
- A4：[Anthropic 支持国家和地区](https://www.anthropic.com/supported-countries)：商业 API 地区列表未列中国大陆。
- G1：[Gemini Developer API Pricing](https://ai.google.dev/gemini-api/docs/pricing)：四个精确型号 ID、USD 定价及日期变化。
- G2：[Gemini API 可用地区](https://ai.google.dev/gemini-api/docs/available-regions)：列表未列中国大陆；该结论限定 Gemini Developer API，不外推到其他云平台的合同。
- X1：[xAI API Pricing](https://docs.x.ai/developers/pricing)：三个精确型号、USD、长上下文门槛、美国地区加价。
- X2：[xAI Regional Endpoints](https://docs.x.ai/developers/advanced-api-usage/regions)：公开全球和美国端点，无中国区报价。

## 后续使用约束

本记录可用于标识中国区报价缺口，不能作为把这 15 型写入人民币官方价表的依据。若业务允许“国际官方 USD 原价 + 单独汇率换算展示”，需要明确该口径后另做计费设计；本次没有实施或默认接受该方案。
