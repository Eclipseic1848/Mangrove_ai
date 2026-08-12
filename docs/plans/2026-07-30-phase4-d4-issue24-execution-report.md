# Phase 4 D4 GitHub #24 执行报告：同 Provider 多套命名个人连接

- 状态：`accepted`
- 日期：2026-07-30
- 工单：[GitHub #24](https://github.com/Eclipseic1848/Mangrove_platform/issues/24)
- 决策：[ADR-0021](../adr/0021-named-personal-model-connections-and-compatibility-slot.md)
- 后续状态：用户已通过继续执行 #25 确认本工单前置验收；#25 已实现并等待用户验收

## 已实现

1. 新增命名个人连接创建接口：
   `POST /api/model-connections/presets/{preset_id}`。同一用户可为同一 Provider 保存多套
   独立连接和 Secret。
2. 保留旧 `PUT` 覆盖语义，但限定到 `personal_preset_v1` 兼容槽；旧接口不会覆盖由新接口
   创建的同 Provider 连接。
3. SQLite 初始化执行幂等升级：保留旧行和 Secret，移除旧 Provider 唯一约束，建立兼容槽
   部分唯一索引。
4. 设置页个人连接表单新增必填“连接名称”和减负型建议名称；保存改为创建新连接。
   列表可以同时展示同 Provider 多张命名卡、独立 Key 尾号和单独删除入口。
5. 原“更换 Key / 模型”改为“同 Provider 新建”，避免用户误以为会编辑当前卡片。
6. 平台连接、自定义/LAN、Grant/Relay 和 TaskRevision 语义未扩展。

## TDD 与验证证据

### 红灯

- 产品 HTTP 测试首次调用新 `POST` 时返回 `405 Method Not Allowed`；
- 浏览器首次查找“连接名称”时超时，证明旧设置页没有命名多连接入口。

### 绿灯

- `tests/test_model_connections_api.py` 覆盖：
  - 同 Owner、同 Provider 两次创建得到不同连接 ID 和 Key 尾号；
  - 两张连接同时可读，旧 `PUT` 不覆盖它们；
  - 另一用户列表为空；
  - 旧单连接数据库连续升级两次后数据仍可读，并可继续创建同 Provider 新连接；
  - 旧 `PUT` 替换 Secret 时仍销毁旧在线密文。
- 后端模型连接与 Runtime 回归：
  `63 passed, 4 warnings`。
- 设置页完整 Playwright：
  `6 passed`，包含普通用户多连接、管理员入口、新手引导、非 JSON 恢复和 axe 可访问性。
- 前端生产构建：
  `tsc --noEmit && vite build` 通过。

## 已验证事实

- 新建两套同 Provider 个人连接不会互相覆盖；
- 旧接口和旧数据库可兼容，升级幂等；
- 个人连接继续按 Owner 隔离；
- API 与列表不返回明文 Key；
- 模型连接变化没有破坏 Pi Runtime 的连接、Grant 和 Verifier 回归。

## 未验证或未实施

- 未调用真实外部 Provider；验证使用假 Provider Transport；
- 一套连接的多模型目录与逐项验证尚未实现，属于 #25；
- 平台共享多连接属于 #26；
- 旧 `runtime_config` / `.env` Key 自动导入属于 #28；
- 未创建 commit、未 push、未创建版本或标签、未发布外部内容。
