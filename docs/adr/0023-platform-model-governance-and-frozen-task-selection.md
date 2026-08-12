# ADR-0023：平台模型连接采用多实例治理，任务冻结用户选择

- 状态：`accepted_implemented_pending_user_acceptance`
- 日期：2026-07-30
- 实施工单：
  [GitHub #26](https://github.com/Eclipseic1848/Mangrove_platform/issues/26)、
  [GitHub #27](https://github.com/Eclipseic1848/Mangrove_platform/issues/27)
- 上游：
  [ADR-0020](0020-provider-connection-broker-and-credential-isolation.md)、
  [ADR-0022](0022-connection-model-validation-and-default-selection.md)

## 背景

个人连接已经支持同 Provider 多套命名连接和逐模型验证，但平台共享连接仍缺少同等治理；
任务也不能只冻结连接默认模型，否则用户修改偏好或管理员调整默认模型后，历史任务的实际
运行含义会漂移。

## 决策

1. 管理员和超级管理员可以为同一 Provider 发布多套平台共享连接，每套连接拥有独立
   Secret、模型集合、默认模型、版本和启停状态。
2. 发布使用逐模型极小合成请求；部分成功时只发布成功模型，全部失败时不创建连接或
   Secret。普通用户只看到已验证且已启用连接的脱敏摘要。
3. 停用或删除平台连接会撤销该连接的存量 Purpose Grant；运行不得自动切换到另一套连接。
4. 每个用户保存一条“新任务默认连接 + 默认模型”偏好。偏好失效时明确要求重新选择，
   不自动替换。
5. 创建任务时允许显式覆盖偏好。TaskRevision 冻结连接 ID、连接版本、模型 ID 和外发
   确认；后续偏好变化不改写旧 Revision。
6. Agent 与 Verifier 为同一冻结模型分别签发 Purpose Grant，不共享 Token。

## 后果

- 管理员可以按环境、账户或用途维护多套共享连接，普通用户无需接触 Key、Base URL；
- 新任务选择稳定且可解释，历史 Revision 可重放；
- 连接撤销会失败关闭，不能用“自动故障转移”掩盖权限或成本变化；
- 真实外部 Provider Smoke、默认入口切换、版本和发布仍不在本 ADR 的实施授权内。
