# ADR-0019：vNext 分离执行、正式发布与默认路由状态机

- 状态：`accepted`；第 5 项的三段 Rollout 顺序由 ADR-0030 部分取代
- 实施状态（2026-08-04）：Pi Candidate Adapter、通用 Publisher、独立 QA、确定性幂等和
  发布恢复对账已完成工程验证，等待用户验收；Legacy 既有 Delivery 不受影响。Rollout
  GateSnapshot、P0 自动阻断和默认切换仍未实现，默认切换继续要求用户单独确认
- 日期：2026-07-30
- 决策来源：[Phase 4 D3 状态机](../plans/2026-07-30-phase4-d3-delivery-default-state-machine.md)
- 上游：[ADR-0017](0017-agentic-runtime-vnext.md)、
  [ADR-0018](0018-unified-task-domain-contract.md)

## 背景

vNext 当前能形成并独立验证 Candidate，Legacy 已有 QA、Manifest、原子目录发布和 owner
下载，但现有 Publisher 被 `SemanticTaskPlan` 与 Legacy Harness Run 外键绑定。把执行、
交付和默认路由继续压在一个任务状态中，会混淆 Candidate、正式 Delivery 和平台生产资格。

## 决策

1. Run、Delivery 和 Rollout 使用三个正交状态机，工作台状态仅为投影。
2. Legacy 与 vNext 的已验证结果分别经 Candidate Adapter 形成同一 `PublishCommand`，
   复用一个 `DeliveryPublishing` Module；Agent、沙箱和 Renderer 不拥有发布权。
3. 发布使用稳定 publication key、staging、独立 QA、`committing` 提交点和崩溃恢复对账；
   提交点前取消保持零正式输出，提交点后不能用取消撤销不可变 Delivery。
4. 依赖获取阶段不挂载用户来源；业务执行阶段不访问公共依赖站点。中途缺依赖必须退出业务
   环境后重新进入隔离依赖阶段。
5. Rollout 依次为管理员灰度、用户显式试用和 vNext 默认；默认切换要求生产硬门通过及
   用户单独确认。任一 P0 回归都把新 revision 路由回 Legacy 并阻止新 vNext 发布。
6. 回滚不迁移、删除、覆盖或重新发布旧任务、既有 Candidate 和既有 Delivery。

## 考虑过的替代方案

- **扩展单一 `task.status`**：状态组合会指数增长，并使回滚错误改写历史交付，拒绝。
- **为 vNext 复制一套 Delivery**：短期接入快，但会产生两套正式含义、QA 和下载门，拒绝。
- **验证通过即把 Candidate 改名为 Delivery**：绕过 Renderer、Manifest、完整性和发布权，
  拒绝。
- **默认回滚同时迁移或删除 vNext 历史**：增加不可逆风险并破坏审计，拒绝。

## 后果

- 需要把现有 Publisher 的 Legacy 输入适配与通用发布核心分离；
- 需要通用 Run/Candidate/Verification 引用、PublishIntent、GateSnapshot 和
  RuntimeAssignment 持久化；
- SQLite 与文件系统提交必须有恢复对账，而不是声称跨资源完全原子；
- 精确 Schema、迁移、测试和 UI 实现仍需后续 `/to-spec → /to-tickets → /implement`；
- 本 ADR 不构成默认切换、Legacy 删除、版本、发布或服务器部署授权。
