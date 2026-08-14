# AC-07-05 个人能力自动晋级 verified 领域/接口设计

> 日期：2026-08-14
>
> 对应工单：GitHub Issue #10（`Eclipseic1848/Mangrove_ai`，`[AC07-05]`）
>
> 状态：`design_confirmed`（用户于 2026-08-14 确认，含四处按仓库既有风格的修订）
>
> 前置：`2026-08-14-agentic-capability-ac07-05-requirements-review.md`（需求复核，已确认）
>
> 边界：本文只记录 #10 的设计决策，不授权实现、迁移、提交或发布；这些动作按后续阶段
> 分别取得授权。

## Problem Statement

#10 需要在既有 CapabilityGovernance 主 Seam 上新增"验证五步 + 供应链证据全部通过后，
系统确定性把同一精确 digest 从 draft 晋级为 verified"的能力。当前事件流只允许
`registered` 事件（强制 draft/active/eligible 初始态），晋级命令、判定门与缺口投影均不存在；
投影层按 `events[-1]` 计算三轴状态，晋级只需追加新事件即可生效，不需要改写目录或历史事实。

## Solution

晋级 = 读取已存在证据 + 追加一个治理事件：

```text
验证运行 SUCCEEDED 落库 ─┐
                          ├─→ maybe_promote(target) ─→ 判定门 ─┬─ 全过 → 晋级事件 → 投影 verified
供应链证据 passed 落库 ───┘        （幂等+预期状态）           └─ 有缺口 → 保持 draft + 缺口枚举
```

不新增证据采集逻辑、不改能力目录、不新增 HTTP 写端点。缺口以服务端脱敏枚举带出，
Owner 卡片显示"已验证"或具体缺口。

## Implementation Decisions

### 1. 事件类型扩展与领域模型

- `CapabilityGovernanceEvent.event_type` 扩展为
  `"registered" | "promoted_to_verified"`。validator 分支校验：
  - `registered` 维持现有约束（只能建立 draft/active/eligible 初始态）；
  - `promoted_to_verified` 要求 `maturity=verified` 且 `lifecycle=active`、
    `eligibility=eligible`（#13/#14 未实现前这是唯一合法组合），并必填
    `source_validation_run_id` 与 `source_supply_chain_evidence_id`（审计依据，
    `registered` 事件两者为 None）。
- 新增 `PromotionGap` 枚举，字面量天然脱敏（不含路径、命令、Token、原始日志）：
  `validation_incomplete` / `evidence_reference_mismatch` /
  `supply_chain_evidence_missing` / `secret_detected` / `critical_vulnerability` /
  `fixable_high_vulnerability` / `misconfiguration_failure` / `trivy_database_stale`。
- 新增 `PromotionOutcome`：`status: "promoted" | "already_verified" | "held"`、
  `gaps`、`event`（promoted/already_verified 时给出事件）。三种结果显式区分
  "本次晋级 / 早已晋级（幂等命中）/ 证据不足保持草稿"，调用方必须处理，不使用
  None 隐式语义。
- `CapabilityGovernanceView` 新增 `promotion_gaps: tuple[str, ...] = ()`；
  verified 时为空，draft 时由缺口评估填充。

### 2. 晋级判定门 `evaluate_promotion(target, now)`

| 检查 | 缺口 |
| --- | --- |
| 无 succeeded 验证运行 / 五步证据不齐 | `validation_incomplete` |
| succeeded 运行存在但五步 evidence 与库中记录不一致 | `evidence_reference_mismatch` |
| 无供应链证据 | `supply_chain_evidence_missing` |
| 证据 BLOCKED | 每个 blocker 映射一个缺口 |
| 证据 PASSED 但判定时刻漏洞库已过期（按 `updated_at`，>7 天） | `trivy_database_stale` |
| 全部通过 | 可晋级 |

漏洞库时效按判定时刻复查（不是采集时刻）：证据采集时未过期、晋级判定时已过期，
保持 draft 并提示重采。判定门放在 `service.py` 主 Seam 内（与三轴投影同层），不新建文件。

### 3. 晋级命令 `maybe_promote(target, actor)`

- 输入：目标（owner + pack + version + digest）、归因 actor（= 验证运行的 Owner；
  语义为 Owner 发起验证即授权自动晋级，审计归因于 Owner）。
- 流程：投影已 verified → 返回 `already_verified` + 已有晋级事件（不新增）；
  否则 `evaluate_promotion` → 全过才写 `promoted_to_verified` 事件并返回 `promoted`；
  有缺口 → 返回 `held` + 缺口，不写任何事件。
- 幂等键：`promotion:{digest}:validation:{run_id}`。
- 预期状态 + 并发（数据库层硬保证）：事件表新增 `event_type` 列（默认
  `'registered'`，旧行自动兼容）+ 部分唯一索引
  `UNIQUE(owner_key, pack_id, version, digest) WHERE event_type='promoted_to_verified'`，
  同一 digest 至多一个晋级事件；并发触发时后写者拿到已有事件，不以最后写入覆盖。
- 晋级事件记录 `source_validation_run_id` 与 `source_supply_chain_evidence_id`，
  事件流可追溯"哪次验证证据促成了晋级"。

### 4. Repository 协议与双实现

`CapabilityGovernanceRepository` 新增三个方法（InMemory 与 SQLite 双实现）：

- `get_latest_succeeded_validation_run(target)`：按 target 查最新 succeeded 运行
  （复用 `idx_capability_validation_owner_target` 索引）。
- `get_latest_promotion_event(target)`。
- `save_promotion_event(event)`：专用写入，事务内"查已有 promoted → 有则返回、
  无则插入"；不动通用 `save_event` 语义。

### 5. 迁移 0004

`src/capability_governance/migrations/0004_promotion_gate.sql` 纯新增：

- `ALTER TABLE capability_governance_events ADD COLUMN event_type TEXT NOT NULL
  DEFAULT 'registered'`（由迁移入口先检查列是否存在，保证可重放）；
- `CREATE UNIQUE INDEX IF NOT EXISTS idx_capability_governance_single_promotion ...`
  部分唯一索引。

沿用既有"先一致性备份、纯新增、可重放、旧数据零改写"的迁移流程；生产执行单独授权。

### 6. 触发点集成（worker）

`CapabilityValidationManager.run_once` 内两个自然触发点，无新线程/新进程：

1. 每条 run 执行后：状态 `SUCCEEDED` → 立即 `maybe_promote`。
2. worker preflight 采集供应链证据落库后 → 立即 `maybe_promote`
   （此时验证未完成则返回 held，不写事件，验证终态时再次判定）。

不新增任何后台补采路径：证据缺失时缺口为 `supply_chain_evidence_missing`，
Owner 重发验证时 preflight 自然重采（沿用 #35"采集失败留待晋级门处理"行为）。

### 7. 产品 Interface

- HTTP 零新端点：`GET /api/capability-governance/packs` 的 View 自动带出
  `promotion_gaps`。
- 前端设置页能力卡片：显示"已验证"或缺口中文列表（枚举→中文文案映射），替换
  现有"该结果只形成验证证据，不会自动晋级"提示文案。

### 8. 明确不改的

- 能力目录 `catalog.py` 零改动：登记强制 DRAFT 保留；晋级只写治理事件流；
  运行门读治理投影属于 #13。
- `_projection_for_pack` 零改动（`events[-1]` 自动生效）。
- 供应链采集逻辑零改动（只挂触发点）。
- 五步验证执行器零改动。

## Testing Decisions

- 只在公共 Seam 断言外部可观察结果：投影状态、缺口、审计、权限；不测私有函数。
- 成功路径：五步 succeeded + 供应链 passed → 投影 verified + 晋级事件 + 审计引用。
- 失败路径：单次任务成功、部分步骤通过、扫描器异常、过期库（判定时刻复查）、
  证据引用失配均保持 draft；幂等重放只产生一个晋级结果（双实现 + 并发线程）；
  冲突不以最后写入覆盖；新 digest/新 version 不继承旧 verified；旧 verified 与
  冻结 TaskRevision 不因新版本失败而改变。
- 夹具：Python Tool 与 MCP 冻结夹具成功/失败双向覆盖；断言数据来自冻结夹具与
  既有证据记录，不来自实现自身摘要。
- 每个纵向切片先写失败测试，再做最小实现；聚焦回归通过后运行全部已完成
  Capability 测试集合。

## Out of Scope

- 平台快照、签名与 admin_gray（#12）；管理员审核与审计查看（#11）；
  运行门装载强制（#13）；弃用/回滚/撤销/风险接受（#14）；AC-06 兼容切换（#17）。
- 两项真实灰度包的实际晋级（#15/#16 纵切面）。
- 对已 SUCCEEDED 但缺证据的 digest 自动补采。
- 普通用户受众、外部发布、版本标签。
