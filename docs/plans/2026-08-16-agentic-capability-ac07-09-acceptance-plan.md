# AC07-09（#14）8088 验收方案

- 前置：S1-S7 完成，双轴审查两轮复核「无新问题，可合入」
- 原则：全部演练作用于 #13 遗留验收样本（accept-s8-draft-sample 个人、
  accept-s8-deprecated-sample 平台），不触碰生产 gray-python-table /
  gray-everything-mcp 的治理状态；restore 完整复查链缺证据场景预期 rejected。

## 验收 1：真实命令演练（HTTP 层，8088）

| # | 命令 | 目标 | 预期 |
|---|---|---|---|
| 1a | quarantine | accept-s8-deprecated-sample（平台 legacy） | applied（资格轴隔离） |
| 1b | quarantine 同键重试 | 同上 | already_applied（幂等） |
| 1c | quarantine 再发新键 | 同上 | rejected already_quarantined（409） |
| 1d | deprecate | 同上（已 deprecated legacy） | rejected not_active（409） |
| 1e | restore | 同上 | rejected（复查链缺供应链证据，409） |
| 1f | risk-accept | 同上（隔离中，finding_ref 指向真实存在 run 或不存在） | rejected non_waivable / finding_ref_unknown / evidence_missing（409） |
| 1g | rollback | gray-python-table（无发布事件） | rejected publication_missing（409，零副作用） |
| 1h | rollback | accept-s8-deprecated-sample | rejected not_active（409） |
| 1i | 非管理员调用 | deprecate | 403 |

## 验收 2：投影与列表生效

- 2a：GET /api/capability-governance/packs（管理员）确认 1a 后样本投影
  eligibility=quarantined、lifecycle 保持 deprecated。
- 2b：GET /api/semantic-workspace/capabilities 仍只显示 2 个 legacy 生产包
  （样本不进入选择列表）。
- 2c：#13 装载门对隔离样本的装载拒绝（draft 样本 409 已有基线）。

## 验收 3：零残留与状态清理

- 3a：演练只写治理事件（幂等键可审计），无容器/网络/临时目录变化。
- 3b：演练后样本状态：quarantined（事件留痕）；如需复原由用户决定
  （可对样本执行 restore 或保留为 #15/#16 夹具）。

## 不包含

- 真实 risk_accept applied 链（需真实 High 证据与验证运行，留 #15/#16 纵切面）。
- 生产 gray 包的弃用/撤销/隔离（真实治理动作需独立授权）。
