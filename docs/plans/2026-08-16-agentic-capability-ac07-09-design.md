# AC07-09（#14）设计：弃用、回滚、隔离、撤销与限期风险接受

- 前置：需求复核 2026-08-16-agentic-capability-ac07-09-requirements-review.md（Q1-Q7 已确认）
- 状态：用户已确认（D1-D8）
- 原则：零 DDL（Q3A/Q5A）、产生端与消费端分离（#13 只读消费者零改动）、全部命令幂等可审计

## D1 事件模型扩展（零 DDL）

`CapabilityGovernanceEvent`（src/capability_governance/models.py）：

- `event_type` Literal 新增：
  - `lifecycle_changed`：弃用（DEPRECATED）/撤销（REVOKED）/恢复（ACTIVE）
  - `eligibility_changed`：隔离（QUARANTINED）/解除（ELIGIBLE）
  - `risk_accepted`：限期风险接受
  - `recommendation_changed`：新任务推荐指针变更（回滚）
- 事件新增字段：
  - `expires_at: datetime | None`（risk_accepted 专用，必填）
  - `recommended_version: str | None`（recommendation_changed 专用，必填）
  - `finding_ref: str | None`（risk_accepted 引用验证运行证据，必填）
- validator 分支（与既有 per-type validator 同构）：
  - `lifecycle_changed`：maturity 保持 verified，lifecycle ∈ 三值，eligibility 保持
    eligible（撤销/弃用不改运行资格；恢复前隔离状态由 eligibility_changed 单独表达）
  - `eligibility_changed`：maturity 保持 verified，lifecycle 保持 active/deprecated，
    eligibility ∈ {eligible, quarantined}
  - `risk_accepted`：maturity verified、lifecycle active、eligibility eligible、
    expires_at/finding_ref 必填
  - `recommendation_changed`：三轴随当前投影（verified/active/eligible），
    recommended_version 必填

## D2 投影折叠升级

`runtime_projection_for_pack`（service.py）：

- 逐事件折叠：三轴分别取事件流中最后一次改变该轴的事件值（现逻辑取最后一条事件的三轴，
  语义等价但为独立轴事件解耦）
- `risk_accepted` 惰性到期：读取时刻 `now >= expires_at` → eligibility 按 quarantined
  投影；未过期 → eligible。不写事件、不改投影存储（Q5A）
- `recommendation_changed` 折叠出 `recommended_version`（最新指针）
- audience 反向扫描、legacy_compat 降级保持不变

## D3 治理命令（CapabilityGovernance 公开方法）

全部签名形态与既有命令一致：`(actor, *, pack_ref, reason, idempotency_key)`，返回
显式 Outcome + 不可变事件；先查幂等键，再校验预期当前状态（状态不符 → 拒绝）。

| 命令 | 前置 | 写入事件 |
|---|---|---|
| `deprecate_pack` | lifecycle == active | lifecycle_changed(DEPRECATED，携带当前资格快照) |
| `revoke_pack` | lifecycle ∈ {active, deprecated} | lifecycle_changed(REVOKED，携带当前资格快照) |
| `restore_pack` | lifecycle ∈ {deprecated, revoked} 或 quarantined；完整复查链（D5）；双键幂等（lifecycle 键 + eligibility 派生键） | 若隔离中先 eligibility_changed(ELIGIBLE) 再 lifecycle_changed(ACTIVE)，快照与当时投影一致 |
| `quarantine_pack` | eligibility == eligible、lifecycle ≠ revoked | eligibility_changed(QUARANTINED，携带当前生命周期快照) |
| `accept_pack_risk` | 平台 admin_gray 受众、eligibility == quarantined、Secret/Critical 无、finding_ref 实引验证运行、days ∈ [1,90]（默认 30） | risk_accepted(expires_at) |
| `rollback_recommendation` | 目标版本投影 verified/active/eligible + 发布事件与签名证据存在 + 受众已确定；当前推荐版本 ≠ 目标 | recommendation_changed(目标 version) |

- 签名与受众说明（修正）：服务层做「发布事件存在 + 签名证据字段完整 + 受众已确定」的
  证据存在性检查；真实密码学验签由装载门（#13 唯一 Seam，`check_mount` 的
  `verify_local`）在装载时执行——回滚目标被选中装载时仍会验签失败关闭。
- 事件快照纪律（AC7 预期状态真实性）：每个治理事件携带的完整三轴必须与事件写入
  时刻的投影一致（如隔离中的弃用携带 quarantined），不得冒充他态。

- 自动隔离触发：本议题提供机制面（事件 + 命令 + 投影）；触发调用点由 #15/#16 纵切面
  接入（Q1A）。命令 `quarantine_pack` 即人工刹车入口。
- 个人 Pack：全部命令仅管理员（superadmin 同权限）作用；个人 owner 命令入口不在本议题
  （Q 列表未要求，D8 边界）。

## D4 推荐指针

- 指针 = `recommendation_changed` 事件流折叠（零 DDL，Q3A）
- `GET /capabilities`（管理员选择列表）对同 pack 多版本返回 `recommended: true` 标记
  并排序置顶；deprecated 版本不进入列表（#13 已实现）
- 冻结（create_task）仍允许显式选择任何 selectable 版本——推荐是默认值不是约束；
  历史 TaskRevision 不变
- 无 recommendation_changed 事件的 pack：不标 recommended（旧路径零回归）

## D5 门检查强化

- 发布门 `publish_platform`：补 Trivy DB 时效复查（7 天 UpdatedAt 判定复用
  supply_chain 判定链；过期 → 拒绝发布）
- 受众变更门 `change_audience`：同样补时效复查
- 恢复命令 `restore_pack`：完整复查链（Q6A）= 扫描库时效 + 平台发布事件与签名证据
  存在性 + lifecycle 现状 + 当前证据（验证运行与供应链证据全绿）；真实密码学验签
  由装载门（#13 唯一 Seam）在装载时执行
- 风险接受到期后：恢复必须重查（同上）；晋级门时效复查已存在（#10，零改动）

## D6 HTTP 路由（admin）

`src/api/routes/capability_governance.py`，全部 `Idempotency-Key` 头 + actor/原因/预期状态：

- `POST /deprecate`、`/revoke`、`/restore`、`/quarantine`、`/risk-accept`、`/rollback`
- 补 #12 遗留：`POST /change-audience`（change_audience 命令的 HTTP 入口）

## D7 测试策略

- 事件 validator 矩阵（四种新类型 × 合法/非法形态）
- 投影折叠：逐轴折叠、risk_accepted 到期惰性（时间注入）、recommended_version 折叠、
  legacy_compat 零回归
- 命令层：幂等（同键重复返回同事件）、预期状态不符拒绝、恢复复查链矩阵、
  risk_accept 天数边界（1/30/90/91 拒绝）
- 门检查：发布/受众变更的 DB 时效拒绝用例
- HTTP：六个新端点 + change_audience（幂等头、404/409/403）
- #13 只读消费者零改动回归（test_runtime_gate_* 全套保持 monkeypatch 夹具，另加
  「真实事件产生端 → 投影 → 监督」串联用例替换夹具）
- Playwright 设置页治理回归（不新增页面）

## D8 范围边界（明确不做）

- 真实重扫调度器与自动隔离触发接线（#15/#16 纵切面，Q1A）
- 双人审批（ADR-0029 留到团队部署阶段）
- 普通用户受众开放（独立治理决定）
- 个人 owner 命令入口（无工单要求）
- 新数据库表/迁移（Q3A/Q5A 零 DDL）
- 前端新页面（治理命令只走 admin API，产品入口后续授权）

## Seam 变更清单

| 文件 | 变更 |
|---|---|
| `src/capability_governance/models.py` | event_type Literal + expires_at/recommended_version/finding_ref 字段 + validator 分支 |
| `src/capability_governance/service.py` | 投影折叠升级 + 六个治理命令 + 发布/受众门时效复查 |
| `src/api/routes/capability_governance.py` | 六个新端点 + change_audience 路由 |
| `src/api/routes/semantic_workspace.py` | /capabilities 列表 recommended 标记（只读） |
| 不动 | 数据库、迁移、#13 只读消费者（装载门/监督/replay_guard）、前端页面、晋级门 |

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| 投影折叠从「最后一条」改为「逐轴」改变既有行为 | 现全部事件三轴同时携带且同值，逐轴折叠对既有事件流结果等价；用既有测试全集回归证明 |
| 惰性到期改变投影语义（读取时变） | 到期判定只影响 risk_accepted 事件的 eligibility 轴；无 risk_accepted 事件时行为与现状逐字节一致 |
| 回滚指针被误用指向危险版本 | 目标版本投影三轴 + 发布/签名证据存在性 + 受众已确定（服务层前置）；真实密码学验签由装载门（#13 唯一 Seam）在装载时执行；推荐仅是默认值，冻结仍显式选择 |
| 零 DDL 事件字段膨胀 | 事件 payload JSON 增量字段，既有事件不受影响；idempotency 唯一约束不变 |
