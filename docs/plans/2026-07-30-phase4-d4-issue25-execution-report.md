# Phase 4 D4 GitHub #25 执行报告：一套连接启用多个逐项验证模型

- 状态：`implemented_pending_user_acceptance`
- 日期：2026-07-30
- 工单：[GitHub #25](https://github.com/Eclipseic1848/Mangrove_platform/issues/25)
- 决策：[ADR-0022](../adr/0022-connection-model-validation-and-default-selection.md)
- 后续状态：用户随后授权连续完成 #26–#31，工程实现与统一验证已经完成，仍待用户验收

## 已实现

1. 新增 `model_connection_models` 子表；旧单模型连接初始化时幂等补成一个可用模型，
   不自动同步新目录。
2. 个人 Preset 连接逐项验证目录中 2–4 个推荐模型，至少一个成功才原子保存连接、Secret
   和逐模型结果。
3. 部分成功只启用成功模型；全部失败不创建连接或 Secret，并返回不含 Provider 正文的
   脱敏结果。
4. 支持无模型权限、凭证无效、协议不兼容、限流、网络不可达、待验证、验证中、可用和
   已停用等产品状态。
5. 新增失败模型单独重试、修改默认模型、独立停用和重新启用模型的产品 API。
6. 停用默认模型后连接进入“需要选择默认模型”，不自动切换；默认模型变化会改变连接版本，
   旧 TaskRevision 无法继续签发 Grant。
7. ProviderPreset 升级为七家：DeepSeek、Qwen、OpenAI、Anthropic、Gemini、Kimi 和
   智谱；每家只展示 2–4 个推荐模型及友好名称。
8. 设置页展示“可用数量 / 总数”、友好默认模型、逐模型状态，并提供重试、设为默认、
   停用和启用操作。创建前明确提示会逐模型产生少量合成验证请求。
9. Provider 响应正文和错误正文不落库；只保存数值 Usage，缺失时为 `unknown`。

## TDD 证据

### 红灯

- 首个产品 HTTP 测试读取 `default_model` 时得到 `KeyError`，证明连接只有单模型字段；
- 浏览器找不到“保存并验证全部推荐模型”，证明旧 UI 没有逐模型语义；
- 失败模型重试测试少一次 Provider 请求，证明缺少单模型重试接口。

### 绿灯

- 模型连接产品 API：`36 passed, 4 warnings`；
- 模型连接、Agentic Runtime、Verifier、Pi 工作台、设置权限与 API 回退联合回归：
  `80 passed, 4 warnings`；
- 完整设置页 Playwright：`7 passed`，包含部分成功、单模型重试和 axe 可访问性；
- `tsc --noEmit && vite build` 生产构建通过。

## 已验证事实

- 一套个人连接可以包含多个独立模型和一个默认模型；
- 同一 Key 下，一个模型失败不会阻断其他模型保存；
- 全部失败不会遗留连接或 Secret；
- 失败模型可以单独重试，停用默认模型不会自动切换；
- 默认模型变化后，旧连接版本签发 Grant 会失败关闭；
- 旧单模型数据库升级幂等，目录版本升级不改写旧连接；
- API、SQLite 和 Runtime 不包含 Provider 响应正文或错误正文。

## 未验证或未实施

- 自动化只使用假 Provider；未调用真实外部 Provider；
- #26–#31 已在后续工作中实现，证据见
  [联合执行报告](2026-07-30-phase4-d4-issues26-31-execution-report.md)；
- Python 环境未安装 Ruff，因此没有 Ruff 结果；Python 导入与相关测试均已通过；
- 未 commit、未 push、未创建版本或标签、未发布外部内容。
