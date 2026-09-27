# 中国区模型价格官网核验：Qwen、GLM、Kimi

核验日期：2026-09-20（北京时间）。范围：官网公开页面与公开 HTML，只读调研；未调用模型、登录账户或修改产品配置。单位均为人民币元 / 1,000,000 tokens，普通同步按量调用，不含套餐、赠金或限时折扣。型号存在表示官网明确列出，不代表当前账户已有调用权限。

## 已核实报价

| 精确模型 ID | 官网存在 | 中国区未命中输入 | 输出 | 缓存命中输入 | 价格范围与额外计费 |
|---|---|---:|---:|---:|---|
| `qwen3.8-max` | 是 | 12 | 36 | 1.5（隐式） | 北京；显式缓存创建 15、读取 1；Batch File 输入 6、输出 18 |
| `qwen3.8-flash` | 是 | 0.8 | 2.7 | 0.1（隐式） | 北京；显式缓存创建 1.25、读取 0.1；该型号不支持批量推理 |
| `glm-5.2` | 是 | 8 | 28 | 2 | 国内 BigModel；缓存存储限时免费 |
| `glm-5.3` | 是 | 8 | 28 | 2 | 国内 BigModel；缓存存储限时免费 |
| `glm-5.3-flash` | 是 | 0.8 | 2.8 | 0.23 | 国内 BigModel；缓存存储限时免费 |
| `kimi-k3` | 是 | 20 | 100 | 2 | 国内 Moonshot；缓存写入另列 TTL 5min：20，TTL 1h：40 |
| `kimi-k2.7-code` | 是 | 6.5 | 27 | 1.3 | 国内 Moonshot；不可套用 highspeed 价格 |
| `kimi-k2.6` | 是 | 6.5 | 27 | 1.1 | 国内 Moonshot |

Qwen 价格来自 [Qwen3.8-Max 官方模型页](https://help.aliyun.com/en/model-studio/qwen3-8-max) 与 [Qwen3.8-Flash 官方模型页](https://help.aliyun.com/zh/model-studio/qwen3-8-flash)。两页的北京价表均未列输入长度阶梯；均列上下文 1,000,000、普通最大输入 991,808、思考模式最大输入 983,616、最大输出 131,072。模型页明确只展示原价，控制台活动价不包含在内。新加坡价格不同，不能因同为人民币显示就套用北京价格。

GLM 价格来自 [智谱 API 定价](https://docs.bigmodel.cn/cn/guide/start/pricing)。上述三型号均列 1M 上下文，无长度阶梯；不能继承 GLM-5.1 等其他型号的 32K 阶梯。缓存存储限时免费，免费结束后的标准价未公布。精确型号另由 [GLM-5.2](https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2)、[GLM-5.3](https://docs.bigmodel.cn/cn/guide/models/text/glm-5.3)、[GLM-5.3-Flash/FlashX](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash) 官方调用示例或 Model Code 核实。

Kimi 价格来自 [Kimi 模型推理价格说明](https://platform.kimi.com/docs/pricing/chat)。旧中国站 `platform.moonshot.cn/docs/pricing/chat` 现场重定向至该地址。网页文本提取没有输出表格，已只读读取公开 HTML 内的表格数据，逐列核实币种与价格。K3 上下文 1,048,576，K2.7 Code / K2.6 为 262,144；[K3 官方指南](https://platform.kimi.com/docs/guide/kimi-k3-quickstart) 明确不按上下文长度分段。K3 缓存写入按 TTL 另计，不指定 TTL 默认 5min；命中后续期且不再次收取写入费用。K2 两型号价表未列长度阶梯。精确型号另见 [K2.7 Code 官方指南](https://platform.kimi.com/docs/guide/kimi-k2-7-code-quickstart) 与 [K2.6 官方指南](https://platform.kimi.com/docs/guide/kimi-k2-6-quickstart)。

## API 端点与价格适用边界

| 服务商 | 中国区 OpenAI 兼容 Base URL | 本次价格适用条件 |
|---|---|---|
| Qwen | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 北京按量计费 Key；官方同时推荐业务空间专属北京域名 |
| GLM | `https://open.bigmodel.cn/api/paas/v4` | 国内 BigModel 按量计费 API；不是 Coding Plan 套餐价格 |
| Kimi | `https://api.moonshot.cn/v1` | 国内 Moonshot 账户与端点 |

Qwen 端点及地域 Key 匹配规则见 [Base URL 总览](https://help.aliyun.com/zh/model-studio/base-url)；旧北京共享域名仍可使用。GLM 端点见上述官方模型调用示例。Kimi 国内端点见上述 K3 调用示例。

**平台现有 Kimi 端点为 `api.moonshot.ai`（国际端点）时，上表中国区人民币报价不能直接用于当前连接的费用估算，应保留“未估价 / 地域不匹配”。** 此处国内报价仅作国内接入参考；没有切换连接，也没有用美元汇率换算冒充国内官方价。

## 核验限制

- 以上是公开官网价格快照；最终账单仍受账户合同、调用时优惠及实际用量影响。
- Kimi 国际端点本次未核实其价格；国内价格已核实，但不能消除当前国际连接的适用性缺口。
- Qwen 显式缓存存储等未在上述模型单价表列出的附加项目，本次没有补造金额；真实估算应结合实际缓存机制和账单项。
- GLM 普通 pricing 营销页为 JavaScript 页面，改由官方文档中的 API 定价表取得数字；没有使用第三方汇总作为价格证据。
