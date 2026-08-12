# Phase 4 D4 GitHub #26–#31 执行报告

- 状态：`implemented_engineering_verified_pending_user_acceptance`
- 日期：2026-07-30
- 工单：GitHub #26、#27、#28、#29、#30、#31
- 决策：
  [ADR-0023](../adr/0023-platform-model-governance-and-frozen-task-selection.md)、
  [ADR-0024](../adr/0024-legacy-model-import-and-custom-protocol-discovery.md)

## 已实现

### #26 平台共享多连接与多模型

- 管理员和超级管理员可为同一 Provider 发布多套平台连接，逐模型验证并允许部分成功；
- 普通用户只看到可用连接、模型与 Key 尾部遮罩，不得到端点、密文或失败连接；
- 整套连接和单模型均可启停；停用或删除会撤销现有 Grant，且不自动切换连接。

### #27 默认选择与任务冻结

- 用户可设置新任务默认连接和模型，创建任务时可显式覆盖；
- 默认项失效时返回明确的重新选择状态；
- Runtime TaskRevision 冻结连接 ID、连接版本、模型和外发确认；
- Agent 与 Verifier 使用同一冻结模型，但分别签发 Purpose Grant。

### #28 旧配置无须重填 Key 导入

- 显式导入个人、全局、`.env` 和本地模型配置，按来源指纹幂等；
- 导入不访问 Provider、不删除旧配置，新连接保持待验证；
- 验证复用导入密文；本地/LAN 无 Key 连接也可验证；
- 官方端点进入 Preset，非官方端点标记为 `legacy_imported`。

### #29 自定义/LAN 四协议

- 支持 Anthropic Messages、OpenAI Chat Completions、OpenAI Responses API 和
  Gemini generateContent；
- 模型发现与协议探测分离；可人工覆盖协议和最多 8 个模型；
- 公网 HTTPS + Key 与精确 LAN/本地无 Key 两类边界均通过产品 API 验证；
- 重定向和云元数据目标继续失败关闭。

### #30 统一设置体验

- 设置页稳定区分个人连接与平台连接，普通用户无平台管理入口；
- 顶部持续展示新任务默认模型，支持同 Provider 多套连接及逐模型状态；
- 低频 Base URL、协议和 LAN 字段只在管理员高级入口出现；
- 旧配置一键导入，错误按 Preset、连接列表、偏好和引导独立恢复；
- Provider 标识采用兼容 React 18 的 `@icons-pack/react-simple-icons`，未强装要求
  React 19 的候选包。

### #31 引导与产品验收门

- 引导可跳过、可重放，并覆盖默认模型、Provider、Key、验证和多连接列表；
- 模态框支持焦点进入、Tab 圈定、Esc 关闭与焦点归还；
- 三角色、明暗主题通过 axe；浅色品牌主色压深后达到 WCAG AA 对比度；
- API 返回 HTML 时展示可恢复错误，不再暴露 `Unexpected token '<'`。

## 验证证据

- 聚焦后端：`91 passed, 4 warnings`；
- 全仓后端：`1056 passed, 4 skipped, 0 failed, 4 warnings`；
- 设置页 Playwright：`10 passed`；
- 完整 Playwright：`50 passed`；
- `tsc --noEmit && vite build`：通过，仅保留既有大 chunk 警告；
- `npm audit --omit=dev --offline`：生产依赖 `0` 项已知漏洞；
- 四协议、部分模型失败、跨用户隔离、管理员/超管权限、Grant 撤销、导入幂等、
  LAN 无 Key、任务模型冻结均有自动化覆盖。

四项跳过用例均为既有显式门：真实 MySQL/PostgreSQL 容器两项，以及两项大规模性能测试。
它们未被本次实现静默跳过。

## 已验证事实

- #26–#31 的代码、数据库迁移、产品 API、设置页和任务工作台均已实现；
- 自动化使用真实 SQLite、Broker、SecretStore、TaskRevision 和浏览器，只替换外部
  Provider 为假服务；
- Provider Secret 不进入产品响应、任务、事件、候选或 Docker 参数；
- 停用/删除连接和偏好失效均失败关闭，不自动故障转移；
- 未修改或删除旧 Key，未创建 commit、版本、标签，未 push 或外部发布。

## 基于代码的推断

- 逐模型验证和任务冻结可避免“Key 有效等于全部模型可用”以及历史任务模型漂移；
- 统一设置页已显著降低普通用户理解 Base URL 和协议的负担，但实际易用性仍应由用户
  在真实页面中验收。

## 尚未验证或未实施

- 未调用真实 DeepSeek、Qwen、OpenAI、Anthropic、Gemini、Kimi 或智谱 Provider；
- 未执行真实 Pi→Relay→外部 Provider Smoke；
- 未完成 DNS rebinding、证书生命周期、备份擦除等完整生产安全加固；
- 工程门通过不等于用户验收通过，也不表示 D4 或整个 Phase 4 已完成；
- Ruff 未安装，因此无 Ruff 结果；Python 导入、聚焦测试和全仓测试均已通过。

## 用户验收纠偏：Clash Fake-IP 与导入连接重验

用户验收发现，导入的平台 DeepSeek/Qwen 首次验证后显示“网络不可达”，再次点击
“验证并启用”没有反应。诊断确认有两个独立根因：

1. 前端只收集 `pending_validation` 子模型；首次失败后的子模型已经是
   `network_unreachable`，列表为空后静默返回；
2. 本机 Clash 将两个官方域名解析到 `198.18.0.0/15` Fake-IP，通用 SSRF 守卫将其按
   私网拒绝。

修复后，导入连接会重试所有非可用、非停用、非验证中的失败模型，并始终给出加载或提示。
SSRF 守卫只对 ProviderPreset 冻结的精确 HTTPS 官方域名接受 `198.18.0.0/15`；
任意域名、HTTP、RFC1918 私网、回环、云元数据和用户自定义目标仍失败关闭。未使用真实
Provider Key 做自动 Smoke。

纠偏验证：

- HTTP 安全边界：`47 passed`；
- 模型连接、HTTP Connector、Pi 工作台与 Verifier 聚焦回归：`140 passed, 4 warnings`；
- 设置页 Playwright：`10 passed`；
- 前端生产构建：通过。

## 2026-08-02 收口复核

当前工作树重新通过全仓后端 `672 passed, 4 skipped`、完整 Playwright `51 passed` 和
前端生产构建，没有发现 D4 自动化回归。整体状态仍为
`implemented_engineering_verified_pending_user_acceptance`：未执行真实 Provider Key
Smoke 或 Pi→Relay→外部 Provider 端到端调用。GitHub #24–#31 目前仍为开放且带
`ready-for-agent` 标签，远端状态尚未与本报告同步；本轮未获授权修改 Issue。统一问题分级
见 [Phase 4 当前问题与优化审计](2026-08-02-phase4-current-issues-audit.md)。
