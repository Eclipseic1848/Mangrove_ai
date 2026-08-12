# Phase 4 D4：多套模型连接与多模型目录任务拆分

- 状态：`issues_24_to_31_implemented_pending_user_acceptance`
- 日期：2026-07-30
- 父工单：[GitHub #16](https://github.com/Eclipseic1848/Mangrove_platform/issues/16)
- 上游规格：
  [多套模型连接与多模型目录正式规格](2026-07-30-phase4-d4-multi-connection-multi-model-spec.md)
- 发布范围：GitHub #24–#31
- 当前执行前沿：#24 已验收；#25–#31 已完成工程实现与统一验证，等待用户验收；
  真实 Provider Smoke、提交和发布仍未授权

## 拆分原则

1. 每个工单是一条可以独立演示和验收的纵向切片，覆盖所需数据、产品 API、界面和测试；
2. 先扩展既有 ModelConnection 聚合并保持兼容，再迁移调用方，不用一次性破坏旧路径；
3. GitHub 正文与原生 Issue Dependencies 同时记录阻塞关系；
4. 所有工单带 `ready-for-agent`，但仍需用户显式调用实现阶段；
5. 不把真实 Provider Smoke、旧 Key 删除、版本、标签、默认入口切换或外部发布混入本任务图。

## 工单与阻塞关系

| 顺序 | 工单 | Blocked by | 独立交付 |
| --- | --- | --- | --- |
| 1 | [#24 支持同 Provider 多套命名个人模型连接](https://github.com/Eclipseic1848/Mangrove_platform/issues/24) | 无 | 同一用户的同 Provider 多套个人连接互不覆盖，旧数据和接口保持兼容 |
| 2 | [#25 一套模型连接启用多个逐项验证模型](https://github.com/Eclipseic1848/Mangrove_platform/issues/25) | #24 | 多 ConnectionModel、默认模型、部分成功和七家少量推荐目录 |
| 3 | [#26 平台共享连接支持多连接与多模型](https://github.com/Eclipseic1848/Mangrove_platform/issues/26) | #25 | 已实现，等待用户验收 |
| 4 | [#27 默认连接与任务模型选择闭环](https://github.com/Eclipseic1848/Mangrove_platform/issues/27) | #25、#26 | 已实现，等待用户验收 |
| 5 | [#28 无须重填 Key 导入现有模型配置](https://github.com/Eclipseic1848/Mangrove_platform/issues/28) | #26、#27 | 已实现，等待用户验收 |
| 6 | [#29 自定义与 LAN 四协议发现和多模型验证](https://github.com/Eclipseic1848/Mangrove_platform/issues/29) | #25 | 已实现，等待用户验收 |
| 7 | [#30 统一模型连接设置体验](https://github.com/Eclipseic1848/Mangrove_platform/issues/30) | #26、#27、#28、#29 | 已实现，等待用户验收 |
| 8 | [#31 可重放引导与完整产品验收门](https://github.com/Eclipseic1848/Mangrove_platform/issues/31) | #30 | 工程验证完成，等待用户验收 |

## 依赖图

```text
#24 → #25 ┬→ #26 → #27 → #28 ┐
          └→ #29 ─────────────┴→ #30 → #31
```

#26–#31 已按上图完成；工程证据见
[执行报告](2026-07-30-phase4-d4-issues26-31-execution-report.md)。GitHub Issue 的外部关闭、
评论或发布不在本轮授权内。

## 每个工单的共同门禁

- 只测试外部可观察行为，不依赖私有函数或 UI 实现细节；
- 产品 HTTP 测试使用真实 SQLite、SecretStore、ConnectionBroker 和 TaskRevision；
- 数据升级测试从旧结构与代表性旧配置启动，并证明幂等和不丢 Key；
- 浏览器测试覆盖普通用户、管理员和超级管理员的真实可见与可操作范围；
- 外部 Provider 只使用假服务，除非用户另行授权真实 Key Smoke；
- Provider Secret 不进入浏览器响应、任务、事件、证据、候选、交付或 Agent；
- Provider 未返回 Usage 时记为 `unknown`，不估算价格；
- 每个工单完成后先展示验证证据，等待用户确认，不自动领取下一工单。

## 人工控制点

- #24 开始前：用户显式调用实现阶段；
- 真实 Provider 或 Pi→Relay→外部 Provider Smoke：单独授权；
- 清理旧 `runtime_config` 或 `.env` Key：单独不可逆操作授权；
- Git commit、push、版本分支、Git 标签或发布：单独授权；
- #31 工程验证通过不等于用户验收通过，仍需用户实际体验确认。
