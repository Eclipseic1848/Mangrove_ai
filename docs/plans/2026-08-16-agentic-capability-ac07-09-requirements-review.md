# AC07-09（#14）需求/规格复核

- 工单：Eclipseic1848/Mangrove_ai#14 [AC07-09] 弃用、回滚、隔离、撤销与限期风险接受
- 复核依据：Issue #14 验收标准、AC-07 规格 §2/§4/§6、ADR-0029、#13 运行时门实现现状
- 日期：2026-08-16
- 状态：用户已确认（Q1-Q7）

## 代码现状调研结论（只读阶段）

1. 事件模型 6 种类型，validator 强制 active/eligible；REVOKED/QUARANTINED/DEPRECATED
   的产生端完全不存在（#13 监督靠测试 monkeypatch 投影激活）。
2. 推荐版本指针机制不存在；`deprecate_platform_pack`/`is_platform_pack_deprecated`
   是悬空 Seam（零调用者）。
3. 7 天 Trivy 时效链已实现（采集/晋级/重采集检查），但发布门与受众变更门无 DB 时效复查。
4. 自动隔离、风险接受零实现；扫描结果与治理投影零联动。
5. #13 只读消费者（装载门/监督/replay_guard）零改动可复用——产生端补齐后自动生效。
6. 迁移最后版本 0005；幂等键、Outcome 显式结果、admin 路由 + Idempotency-Key 头均可复用。

## 需求决策（Q 列表，用户确认）

| # | 决策 | 确认 |
|---|---|---|
| Q1 | 自动隔离触发范围：本议题只做「证据→隔离」机制+命令（重扫调度留 #15/#16 纵切面） | A |
| Q2 | 风险接受的「路径不可达」由管理员人工判定并引用验证运行证据，系统存档 | A |
| Q3 | 推荐版本指针用治理事件流折叠（`recommendation_changed`），零 DDL | A |
| Q4 | 推荐指针仅平台 Pack（新任务选择列表是平台能力） | A |
| Q5 | 风险接受到期在投影读取时惰性判定（零调度零 DDL） | A |
| Q6 | 恢复命令完整复查：扫描库时效+签名+生命周期+当前证据 | A |
| Q7 | 新命令 deprecate/rollback/revoke/restore/risk_accept 五条 + 补 change_audience 路由 | 同意 |

## 关键语义（来自 Issue 验收标准与 ADR-0029）

- deprecated：不进入新任务推荐，历史冻结任务与恢复任务可继续使用。
- revoked：禁止新任务、重试和恢复。
- quarantined：自动或人工安全刹车；最终撤销或恢复必须由管理员决定。
- 回滚只原子改变新任务推荐指针；目标必须 verified、active、eligible、签名有效且受众匹配；
  历史 TaskRevision 不变。
- 风险接受：仅无修复且有失败关闭证据证明路径不可达的 High；admin_gray 范围；默认 30 天、
  最长 90 天、不能自动续期；到期转为 quarantined。
- Trivy 数据库以内容 UpdatedAt 计算 7 天有效期；过期阻止新晋级、发布和解除隔离，
  既有 eligible 灰度任务继续运行。
- 全部治理命令要求 actor、原因、范围、时间、幂等键和预期状态。
