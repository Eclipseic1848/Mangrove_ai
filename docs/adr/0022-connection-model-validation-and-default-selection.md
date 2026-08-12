# ADR-0022：模型连接采用逐模型验证与显式默认模型

- 状态：`accepted_implemented_pending_user_acceptance`
- 日期：2026-07-30
- 决策来源：
  [多套模型连接与多模型目录正式规格](../plans/2026-07-30-phase4-d4-multi-connection-multi-model-spec.md)
- 实施工单：
  [GitHub #25](https://github.com/Eclipseic1848/Mangrove_platform/issues/25)
- 上游：
  [ADR-0020](0020-provider-connection-broker-and-credential-isolation.md)、
  [ADR-0021](0021-named-personal-model-connections-and-compatibility-slot.md)

## 背景

ADR-0021 允许同一 Provider 存在多套命名连接，但每套连接仍只有一个 `model` 字段。
一把 API Key 有效不表示该账户对 Provider 目录中的全部模型都有权限；只验证一个模型并把
整套连接描述为可用，会把无权限、限流、协议不兼容或网络失败的模型冒充为可用。

同时，直接用新版 Provider 目录覆盖旧连接，会静默改变已有任务使用的模型。默认模型变化也
必须形成新的连接版本，使旧 TaskRevision 失败关闭。

## 决策

1. `ModelConnection` 聚合新增多个 `ConnectionModel` 子项。每个子项保存模型 ID、友好名称、
   目录角色、目录版本、验证状态、启停状态、验证时间、脱敏错误分类和 Usage 是否已报告。
2. ProviderPreset 目录版本升级为 `2026-07-30.2`，首批包含 DeepSeek、Qwen、OpenAI、
   Anthropic、Gemini、月之暗面 Kimi 和智谱 GLM；每家只提供 2–4 个面向普通用户的推荐
   模型和一个平台推荐默认模型。
3. 创建个人 Preset 连接时逐项发送极小合成请求。至少一个模型成功才保存连接与 Secret；
   部分成功只启用成功模型；全部失败返回脱敏逐模型结果，不创建连接或 Secret。
4. 稳定验证状态包括 `pending_validation`、`validating`、`available`、
   `model_access_denied`、`credentials_invalid`、`protocol_incompatible`、
   `rate_limited`、`network_unreachable` 和 `disabled`。
5. 默认模型必须是已启用的可用模型。创建时首选模型失败而其他模型成功，可明确选择首个
   可用模型并在响应中展示；连接建立后停用当前默认模型会进入
   `needs_default_model`，不得静默切换。
6. 用户可以只重试失败模型、显式修改默认模型、独立停用或重新启用模型。重试复用 Broker
   内部解密的当前 Secret，不把 Key 返回浏览器。
7. 兼容字段 `model` 暂时保存当前默认模型，供既有 Runtime 使用；权威模型集合在
   `model_connection_models`。连接版本同时绑定 Secret 版本和默认模型，默认模型变化后
   旧 TaskRevision 无法继续签发 Grant。
8. 只持久化 Provider Usage 中的数值字段；响应正文和错误正文不落库。Usage 缺失时保存
   `unknown`，不估算价格。
9. 旧单模型连接在数据库初始化时幂等补成一个可用 ConnectionModel。目录升级不自动增加、
   删除或切换旧连接模型。

## 被取代的旧决策

本 ADR 取代 ADR-0020 中“保存连接只验证一个推荐或用户选择模型”的实施边界，并补充
ADR-0021 的多连接模型基数。ADR-0020 的 Broker、Secret 隔离、Grant、协议透传和外发安全
边界继续有效。

## 后果

- 用户能看到真实的逐模型可用性，不会因一个模型成功而误判整家 Provider；
- 部分成功仍可形成有价值的连接，失败模型可稍后单独重试；
- 设置页需要展示可用数量、默认模型、逐模型状态和低频操作；
- 平台共享连接多模型属于 #26，用户默认连接与任务显式选模属于 #27；
- 真实 Provider Smoke、旧 Key 自动导入、提交、推送、版本和发布仍需单独授权。
