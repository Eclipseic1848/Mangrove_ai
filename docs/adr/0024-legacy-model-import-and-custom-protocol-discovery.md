# ADR-0024：旧模型配置采用待验证导入，自定义连接采用协议探测加人工覆盖

- 状态：`accepted_implemented_pending_user_acceptance`
- 日期：2026-07-30
- 实施工单：
  [GitHub #28](https://github.com/Eclipseic1848/Mangrove_platform/issues/28)、
  [GitHub #29](https://github.com/Eclipseic1848/Mangrove_platform/issues/29)
- 上游：
  [ADR-0020](0020-provider-connection-broker-and-credential-isolation.md)、
  [ADR-0023](0023-platform-model-governance-and-frozen-task-selection.md)

## 背景

旧 `runtime_config`、`.env` 和本地模型配置已经保存了 Key、端点与模型名。要求用户重新
填写会增加迁移成本，但直接把旧配置标为可用又会绕过新连接的验证门。自定义网关也不能仅
凭 `/models` 返回值判断其实际支持的请求协议。

## 决策

1. 只有用户点击“导入现有配置”才扫描旧配置；导入复制到新隔离存储并保持旧值不变。
2. 导入以来源范围、来源键和内容指纹幂等；新连接一律为 `pending_validation`，不在导入
   过程中访问 Provider。
3. 官方端点匹配 Preset；非官方端点标记为 `legacy_imported`。本地/LAN 连接允许无 Key。
4. 用户点击“验证并启用”后复用已导入密文，不要求重新填写 Key；至少一个模型验证成功后
   才可作为新任务默认。
5. 自定义/LAN 支持 Anthropic Messages、OpenAI Chat Completions、OpenAI Responses 和
   Gemini generateContent 四种协议。
6. 自动发现先读取模型列表，再用极小合成请求分别探测四种协议；模型列表本身不作为协议
   兼容证据。用户可覆盖协议并手工填写最多 8 个模型。
7. 公网连接必须是精确 HTTPS 且有 Key；管理员可配置精确 LAN/本地 HTTP 且 Key 可空。
   重定向、云元数据地址和不符合目标策略的请求失败关闭。

## 后果

- 用户迁移已有 Key 时无需再次查看或输入秘密；
- 导入与启用分离，旧链路可回退且不会因扫描行为产生外部费用；
- 自定义协议判断有真实请求证据，同时保留高级用户的人工覆盖能力；
- DNS rebinding、证书生命周期、备份擦除和真实 Provider Smoke 仍是后续独立安全门。
