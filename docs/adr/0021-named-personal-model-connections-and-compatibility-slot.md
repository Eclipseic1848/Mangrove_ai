# ADR-0021：个人模型连接采用命名多实例与旧接口兼容槽

- 状态：`accepted_implemented`
- 日期：2026-07-30
- 决策来源：
  [多套模型连接与多模型目录正式规格](../plans/2026-07-30-phase4-d4-multi-connection-multi-model-spec.md)
- 实施工单：
  [GitHub #24](https://github.com/Eclipseic1848/Mangrove_platform/issues/24)
- 上游：[ADR-0020](0020-provider-connection-broker-and-credential-isolation.md)

## 背景

首版个人 Preset 连接以 `(owner_user_id, preset_id)` 唯一，并由
`PUT /api/model-connections/presets/{preset_id}` 覆盖保存。这会把“Provider”误当成
“连接”：用户为同一 Provider 配置第二套 Key 时，第一套连接和 Secret 会被替换，无法表达
日常、备用、不同账户等真实用途。

同时，直接改变旧 `PUT` 的语义会让现有调用方从“更新”变成“不断新增”。数据库升级也必须
保留已有连接和在线 Secret，不能要求用户重新填写 Key。

## 决策

1. `ModelConnection` 是命名实例，不再按 Provider 唯一。同一 Owner 可以为同一 Preset 创建
   任意多套连接，每套连接拥有独立 `connection_id` 和 `secret_id`。
2. 新产品接口 `POST /api/model-connections/presets/{preset_id}` 表示“创建一套新连接”，
   `display_name`、Provider Preset 和 API Key 必填；成功返回 `201`。
3. 旧 `PUT /api/model-connections/presets/{preset_id}` 保持覆盖语义，但只操作
   `compatibility_slot='personal_preset_v1'` 的旧版兼容连接，不得覆盖、删除或重命名由
   `POST` 创建的新连接。
4. 数据库升级为存量个人连接补上兼容槽，删除旧的 `(owner_user_id, preset_id)` 唯一索引，
   改用 `(owner_user_id, preset_id, compatibility_slot)` 的非空部分唯一索引。新连接的
   `compatibility_slot` 为 `NULL`。升级必须可重复执行。
5. Owner 隔离、密文分表、Broker 验证和删除授权继续沿用 ADR-0020；列表以连接名称、Provider、
   当前已验证模型和 Key 尾号区分多套连接，不公开 Secret 或内部 Endpoint。
6. 本 ADR 只改变“连接基数”和兼容接口语义。一套连接启用多个模型属于 GitHub #25，
   平台共享多连接属于 #26，旧配置自动导入属于 #28，均不在本次实现范围。

## 被取代的旧决策

本 ADR 只取代 ADR-0020 实施注记中“每个用户每个 Provider 只有一套个人连接”的隐含基数，
以及设置页把个人连接操作表述为“更换 Key / 模型”的交互。ADR-0020 的 Broker、Grant、
协议透传、凭证隔离和安全边界继续有效。

## 后果

- 用户可以用清晰名称管理同 Provider 的多套个人连接，新增一套不会影响已有连接；
- 旧客户端可继续调用 `PUT`，且旧数据库升级后无需重新填写 Key；
- `compatibility_slot` 是过渡兼容机制，不作为新 UI 的业务概念展示；
- 当前每套连接仍只有一个已验证模型，不能把本工单描述为“多模型连接已完成”；
- 未调用真实外部 Provider，真实 Smoke、旧 Key 自动迁移、提交、推送和发布仍需单独授权。
