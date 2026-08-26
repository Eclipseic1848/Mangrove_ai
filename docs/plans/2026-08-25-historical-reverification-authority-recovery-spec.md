# 历史候选重验权威恢复规格

> 状态：`ENGINEERING_VERIFIED_PRODUCTION_GATE_REQUIRED`
>
> 日期：2026-08-25
>
> 当前阶段：六项决定与 TDD 实现已完成工程验证；生产迁移、生产数据写入和 Provider 调用仍未执行
>
> 上游：
> [历史 inconclusive Candidate 语义重试恢复规格](2026-08-25-historical-inconclusive-semantic-retry-spec.md)、
> [ADR-0033](../adr/0033-candidate-reverification-and-verifier-ruleset.md)

## 1. 目标

为 RuntimeRouting 上线前已经形成 Pi Candidate、但没有 `RuntimeAssignment` 的历史任务建立一条
追加式、可审计、失败关闭的重验权威恢复路径，使 TaskOwner 可以在不伪造历史路由事实的前提下，
仅对精确的旧 TaskRevision、Run 和 CandidateSet 发起 CandidateVerification。

本规格解决的是“谁现在有权让这个冻结旧候选进入独立重验”，不是证明“旧任务执行时曾经写入过
RuntimeAssignment”。它不得回填普通 RuntimeAssignment，不得恢复 Pi 执行权，也不得扩大到创建
revision、修改候选或发布 Delivery。

## 2. 事实、推断与建议

### 2.1 已验证事实

1. `RuntimeAssignment` 的当前领域含义是“任务 revision 创建时冻结的 Legacy 或 vNext 执行归属”，
   并绑定当时的 RolloutMode 与 GateSnapshot；数据库禁止更新和删除。
2. CandidateVerification 的现有权威 Adapter 要求 TaskRevision、来源、Provider 和
   RuntimeAssignment 全部一致；缺少 Assignment 时返回 `runtime_assignment_drift`。
3. 当前重验 Interface 已把资格集中在 `inspect_reverification` 与
   `request_reverification`，Worker 启动前会再次执行同一权威检查。
4. 真实目标 `u_9505fd620899 / workspace_c115f33be1004f51 / revision 1` 的 Runtime 行：
   - `runtime_version=pi`；
   - `status=candidate_ready`；
   - `run_id=pi_run_42daee348b9a45bc`；
   - 创建于 `2026-08-17T05:43:43.605882+00:00`；
   - 存在从 `runtime.preparing` 到 `verification.completed`、`candidate.ready` 的运行事件链；
   - 精确 Assignment 数量为 0。
5. 生产库 `0001_runtime_routing` 首次迁移记录应用于
   `2026-08-22T20:39:02.745740+00:00`，晚于目标 Runtime 行创建时间，并记录了备份摘要。
6. 该目标的 Candidate、来源、旧 inconclusive 报告、连接和模型已经由上游纵切片完成只读复核；
   当前唯一权威 blocker 是 `runtime_assignment_drift`。

### 2.2 基于代码的推断

1. 直接向 `runtime_assignments` 插入当前时间和当前 GateSnapshot，会把“现在的恢复决定”伪装成
   “任务创建时的路由事实”，与 `RuntimeAssignment` 既有语义冲突。
2. 只在 Adapter 中忽略缺失 Assignment，会使任何因新 Bug、数据损坏或越权删除导致的缺失记录
   都获得同样放行，无法区分合法历史数据与当前系统故障。
3. 把恢复凭据放入 RuntimeRouting 的普通 `resolve` Interface，会使它可能被 Pi 恢复、默认路由、
   能力装载等其他消费者误当成通用运行权威；把它收敛在 CandidateVerification 内可以维持最小权限。

### 2.3 已确认的领域决定

已按用户确认新增 `HistoricalReverificationAuthority`（历史重验权威凭据），作为
CandidateVerification Module 内的追加式事实。它只表达：TaskOwner 在 RuntimeRouting 上线后，
基于一组当前仍可核验的冻结身份，授权精确 CandidateSet 进入指定类型的独立重验。

六项决定已确认，并已写入 `CONTEXT.md` 与 accepted ADR-0034；生产迁移和真实写入仍保持独立门禁。

## 3. 考虑过的方案

| 方案 | 优点 | 主要问题 | 结论 |
|---|---|---|---|
| A. 回填普通 RuntimeAssignment | 改动最少，现有 Adapter 可直接通过 | 伪造任务创建时的 Rollout/GateSnapshot/时间事实；扩大到其他 Runtime 消费者 | 拒绝 |
| B. 历史任务缺 Assignment 时直接忽略 blocker | 无需新表 | 无审计、无 Owner 确认、无法区分旧数据和新故障，形成长期绕过 | 拒绝 |
| C. 创建新 revision 或重跑 Pi | 会自然得到新 Assignment | 改变业务含义、增加生成和费用，违背“同一 Candidate 只重验” | 拒绝 |
| D. CandidateVerification 内追加历史重验权威凭据 | 不改写历史、权限最小、可审计、可失败关闭 | 需要新领域契约、显式迁移和完整反向测试 | 推荐 |

## 4. 领域模型

### 4.1 HistoricalReverificationAuthority

`HistoricalReverificationAuthority` 是不可变、不可删除的恢复凭据，至少冻结：

- `authority_id`：由规范化身份内容计算的稳定摘要；
- `owner_id / task_id / revision / run_id`；
- `purpose`：首版只允许 `semantic_inconclusive_reverification`；
- `legacy_runtime_created_at`；
- `runtime_routing_migration_id`、`runtime_routing_applied_at` 和迁移记录摘要；
- `runtime_request_hash`、`task_revision_hash`、`source_binding_hash`；
- `candidate_set_hash`、`candidate_manifest_hash`；
- `goal_contract_hash`、`delivery_spec_hash`；
- `previous_attempt_id` 与 `previous_report_hash`；
- `connection_id`、`connection_version`、`model_id`；
- `evidence_manifest_json` 与其 `evidence_hash`；
- `actor_id`、`idempotency_key`、`recorded_at`。

Evidence Manifest 只保存规范化身份、时间、状态、事件 ID/类型及摘要，不复制目标正文、来源正文、
候选内容、Secret、宿主绝对路径或 Provider 原始日志。需要正文的重验仍从 Owner 隔离的既有冻结
对象读取，并重新计算摘要。

### 4.2 它不是什么

该凭据：

- 不是 RuntimeAssignment，也不进入 `runtime_assignments`；
- 不声明旧 Run 实际执行代码等于任何 GateSnapshot commit；
- 不证明旧 VerifierRuleset，因此不能把 `legacy_unversioned` 变成 `ruleset_changed`；
- 不是 Provider Grant，不能替代每个 Attempt 的外发确认、费用确认和 Usage；
- 不是 Delivery 发布权，不能被 Publisher 单独接受；
- 不是通用任务恢复凭据，不能用于 Pi resume/replay、CapabilityHost 装载或新 revision 路由。

### 4.3 有效重验权威投影

CandidateVerification 内部使用一个窄 Interface 解析有效权威：

```text
ReverificationAuthorityResolver
  ├─ 普通路径：有效 RuntimeAssignment
  └─ 历史路径：有效 HistoricalReverificationAuthority
                     + 当前 Task/Run/Candidate/Provider/P0 复核
```

两条路径只在“允许 CandidateVerification 继续检查”这一条 Seam 汇合。Adapter 返回通过或结构化
blocker，不向调用者暴露一个可被当作 RuntimeAssignment 使用的联合领域对象。

## 5. 恢复资格

### 5.1 历史时间边界

只有以下条件同时成立时，缺失 Assignment 才可被识别为历史恢复候选：

1. `agentic_runtime_runs.created_at < 0001_runtime_routing.applied_at`；
2. `0001_runtime_routing` 迁移记录存在，首次恢复点摘要非空且可校验；
3. 精确 Owner/Task/revision 没有 RuntimeAssignment；
4. RuntimeRouting 上线后创建的任务一律不允许使用历史恢复路径。

时间边界只证明该任务“可能合法早于 Assignment 机制”，不证明旧 Assignment 曾存在。最终权限来自
Owner 当前确认和第 5.2 节全部冻结事实的一致性。

### 5.2 精确身份与状态

记录凭据前必须在同一只读预检中确认：

- 调用 Actor 就是 TaskOwner；管理员和超级管理员不得代替 Owner；
- Task 存在、未被清理、revision 是当前 active revision；
- TaskRevision 的目标、格式、表格契约和 Run 与 Runtime 请求一致；
- Task 的 upload ID 顺序与 Runtime 来源绑定一致；来源和 Candidate 文件字节摘要仍匹配；
- Runtime 仍为 `pi + candidate_ready`，精确绑定同一 run_id；
- Runtime 事件链至少包含同一身份下的准备、执行、验证完成和 Candidate ready 事实；
- CandidateSet、Manifest、GoalContract、DeliverySpec 与前序 Attempt/Report 精确对应；
- 前序报告为 `inconclusive`，且只有 `semantic_goal` 未形成结论；
- 没有正式 Delivery、活动 Attempt 或 `outcome_unknown`；
- 当前 P0 未阻断；当前连接仍属于 Owner、状态允许、版本和模型未漂移；
- 当前 VerifierRuleset 可以由实际执行进程冻结。

任一字段缺失、解析失败、冲突或并发变化都失败关闭；不得人工补猜、选择“更可信”的一侧或回填
旧 Runtime/Task 行。

### 5.3 并发与幂等

- `authority_id` 对精确 Owner/Task/revision/run/CandidateSet/purpose 唯一；
- `owner_id + idempotency_key` 唯一；同键不同请求返回冲突；
- 记录前在 `BEGIN IMMEDIATE` 中重查 Assignment 不存在、时间边界和所有数据库身份；
- 若并发写入普通 RuntimeAssignment，历史恢复失败，不覆盖也不择优；
- 相同请求重复提交返回同一凭据，不新增第二条记录；
- 表级 Trigger 禁止 UPDATE/DELETE，纠正只能追加新的、用途不同且重新授权的未来凭据；首版不提供
  纠正入口。

## 6. Interface 与流程

### 6.1 保留两个用户业务动作

不新增第三个普通用户入口，继续使用 ADR-0033 的两个动作：

```text
inspect_reverification(owner, task, revision) -> ReverificationOffer
request_reverification(ReverificationCommand) -> VerificationAttemptReceipt
```

`inspect_reverification` 在普通 Assignment 缺失、但第 5 节只读条件全部可满足时，不再只返回无法
行动的 `runtime_assignment_drift`，而是返回：

- `eligible=false`；
- `blocker=historical_authority_recovery_required`；
- 精确恢复身份摘要；
- 明确说明恢复不等于旧 Assignment、不重跑 Pi、不授权 Provider、不发布。

Offer 查询仍保持零写入、零 Grant、零 Provider 调用。

### 6.2 Owner 写命令

`request_reverification` 可携带一个严格结构化的 `historical_authority_recovery` 确认段，至少包含：

- 服务端 Offer 返回的 `expected_evidence_hash`；
- `acknowledge_no_historical_assignment=true`；
- `acknowledge_reverification_only=true`；
- 独立 Idempotency-Key；
- 原有 Provider 外发确认字段。

服务端先重算第 5 节证据，再追加 HistoricalReverificationAuthority；随后按同一精确身份创建
requested VerificationAttempt。Provider 外发仍只会在 Worker 认领、第二次权威/P0/文件/连接复核
全部通过后发生。

恢复确认和 Provider 外发确认是两个不同语义。一般产品流程仍分别展示；当前真实目标已经在本次
会话获得一次精确 Provider 调用授权，工程完成后不需要再次展示模型、外发范围和费用，但仍必须
由目标 TaskOwner 的产品身份提交写命令，且 `outcome_unknown` 后不得自动重试。

### 6.3 Worker 再检查

Worker 不信任 requested Attempt 创建时的临时状态，必须再次解析凭据并重算：

- authority 的用途、Owner、Task、revision、Run 和 CandidateSet；
- TaskRevision、来源、Candidate、Manifest、合同和前序报告；
- 当前 P0、连接和实际 VerifierRuleset；
- 没有新 Delivery、竞争 Attempt 或普通 Assignment 冲突。

authority 缺失、摘要不一致或证据对象漂移时，在任何 Provider 外发前取消 requested Attempt；如果
请求可能已经到达 Provider，则只能进入 `outcome_unknown`，不得猜测为未调用。

## 7. 持久化与迁移

推荐在 CandidateVerification 的显式迁移中新增
`candidate_reverification_authorities`，而不是改动 `runtime_assignments`。表包含第 4.1 节字段、
两个唯一约束和 no-update/no-delete Trigger；规范化 Manifest 与摘要由领域模型和 Repository 双重
校验。

迁移要求：

1. 新数据库从空库安装完整 Schema；
2. 已有 CandidateVerification 0001/0002 数据库前向迁移；
3. 生产库副本演练前先生成独立备份和 SHA-256；
4. 迁移记录 append-only，记录 migration ID、SQL 摘要、备份摘要和 applied_at；
5. 重复运行幂等；Schema 缺项、DDL 被改写或恢复点不匹配时失败关闭；
6. 迁移不自动扫描、回填或创建任何 authority；真实目标的 authority 只能在 Owner 写命令中追加；
7. 迁移与恢复演练验证 `PRAGMA integrity_check`、业务表指纹、前向重放和备份恢复。

P0-05 仍会建立统一生产迁移/备份/恢复基础设施。本纵切片在 P0-05 前只允许沿用现有
CandidateVerification 带备份显式迁移机制，不能声称已经完成 P0-05，也不能把本表启动时静默创建。

## 8. API 与前端

首版复用现有重验 Offer、确认对话框、Toast、SSE 和 Attempt 恢复，不新增独立管理页面。

- 缺普通 Assignment 但满足历史条件：显示“需要恢复历史重验权威”；
- 解释“系统不会补造旧 Assignment，只会记录你现在对这个 CandidateSet 的重验确认”；
- 展示 Owner、任务、revision、Run、Candidate 数量/格式和摘要，不展示业务正文或 Secret；
- 恢复确认后仍执行现有 Provider 外发确认规则；
- 不满足历史时间边界、证据链不完整或当前任务缺 Assignment：继续显示
  `runtime_assignment_drift`，不给恢复按钮；
- 通过后仍只显示等待独立发布。

实现前端时必须继续使用 `frontend-design` 与 `frontend-design-premium`，沿用现有视觉系统；本规格
阶段不修改页面。

## 9. 失败场景

| 场景 | 预期 |
|---|---|
| 旧 Runtime 早于 RuntimeRouting、无 Assignment、证据全一致 | Offer 提示 Owner 可恢复重验权威 |
| 新 Runtime 漏写或 Assignment 被异常移除 | 不满足时间边界，失败关闭 |
| 直接插入普通 RuntimeAssignment | 不属于本机制；实现和运维流程禁止 |
| 管理员代替 Owner 操作 | 403；零 authority、零 Attempt、零外发 |
| Offer 后 Task/Run/Candidate/来源/连接漂移 | 409；零 authority 或复用原幂等事实 |
| 并发出现普通 Assignment | 历史恢复失败；不覆盖、不择优 |
| Evidence Manifest 被改写或删除 | SQLite Trigger 拒绝；读取校验失败时停止重验 |
| 凭据被用于 Pi resume、新 revision 或 Publisher | 类型/Interface 不接受，失败关闭 |
| 恢复成功但 Provider 未确认 | authority 保留，零 Provider 调用 |
| Provider 结果未知 | Attempt 为 outcome_unknown；authority 不等于自动重试授权 |
| 语义重验 passed | 等待 Owner 独立发布；零自动 Delivery |

## 10. 验证矩阵与完成定义

### 10.1 领域与 Repository

- 规范化 Evidence Manifest、稳定 hash、extra-forbid、严格 bool；
- 新旧时间边界等号两侧、时区、损坏时间和缺迁移记录；
- 普通 Assignment 已存在/并发出现、Owner 不匹配、purpose 越界；
- 幂等重复、同键不同请求、并发唯一性；
- UPDATE/DELETE、直接 SQL 伪造、Schema 漂移与迁移记录不可改写；
- 空库、0001、0002、真实生产副本的迁移、重放、备份恢复和完整性。

### 10.2 Service 与 API

- 普通 RuntimeAssignment 路径完全不变；
- 合法历史恢复 Offer 与所有 blocker 的反向矩阵；
- 恢复确认严格布尔值，Owner 会话隔离；
- authority 与 requested Attempt 的失败窗口、重复请求和进程重启；
- Worker 前第二次权威复核；任何失败均断言零 Provider 调用；
- 明确断言 Pi start/resume、revision、Candidate 文件和 Delivery 写入次数均为 0；
- Provider Grant/Usage/Attempt、`outcome_unknown` 和独立发布回归。

### 10.3 前端与真实目标

- Playwright 覆盖恢复说明、确认、键盘/焦点、窄屏、200% 缩放、深浅主题和 axe；
- 前端 build、聚焦后端回归、完整风险相关回归和双轴 code-review；
- 生产迁移前再次只读核对目标创建时间、migration、Assignment 数量和全部证据 hash；
- 经用户另行确认生产迁移/写入门后，只对精确目标追加一条 authority 和一条 requested Attempt；
- 使用既有一次 Provider 授权只调用一次 DeepSeek，核对旧 Attempt、CSV、88 条证据、Pi、revision
  和 Delivery 均未被改写；
- 真实语义结论由 Verifier 决定，不能预设 passed。

### 10.4 完成定义

- `SPEC_APPROVED`：用户确认第 11 节；不等于授权实现；
- `ENGINEERING_VERIFIED`：TDD、迁移副本、相称回归、浏览器验收和双轴审查通过；
- `LIVE_AUTHORITY_RECORDED`：用户单独确认生产迁移/写入后，目标 Owner 追加精确 authority；
- `LIVE_SEMANTIC_RETRIED`：既有单次 Provider 授权形成新 Attempt 且确定收口；
- `LIVE_ACCEPTED`：Owner 另行授权发布并验收正式 Delivery。

## 11. 已由用户确认的决定

以下六项已于 2026-08-25 全部确认；该确认只授权工程 TDD，不自动授权生产写入或 Provider 调用。

1. **事实模型**：采用追加式 `HistoricalReverificationAuthority`，不回填、不合成普通
   RuntimeAssignment。推荐同意。
2. **历史边界**：仅允许 Runtime 创建时间早于本库 `0001_runtime_routing.applied_at`、且精确
   Assignment 缺失的任务；新任务缺记录一律视为故障。推荐同意。
3. **最小权限**：首版 purpose 只允许 `semantic_inconclusive_reverification` 的精确
   CandidateSet；不开放 failed legacy rebaseline、Pi 恢复、新 revision 或发布。推荐同意。
4. **Owner 权限**：只有 TaskOwner 可记录；管理员和超级管理员不能代替。推荐同意。
5. **Interface**：保留现有 inspect/request 两个业务动作；Owner 在 request 中提交结构化恢复确认，
   不新增通用“补权威”管理入口。推荐同意。
6. **实施与生产分门**：本轮确认后只进入 TDD 实现；生产数据库迁移、追加真实 authority 与已经
   授权的单次 DeepSeek 调用，仍要在工程证据完成后作为生产写入门单独确认。推荐同意。

工程验证证据见
`docs/plans/2026-08-25-historical-reverification-authority-recovery-implementation-report.md`。

## 12. 明确排除

- 批量扫描或批量补历史权威；
- 人工改库、一次性放行脚本或管理员标绿；
- 回填 RuntimeAssignment、GateSnapshot 或旧 VerifierRuleset；
- failed + legacy_unversioned 再基线；
- 重跑 Pi、修改/生成 Candidate、创建 revision；
- 自动 Provider 重试、自动发布或普通用户受众扩大；
- 提交、推送、PR、Issue 修改、标签、Release、部署或外部发布。

## 13. 开源与依赖判断

该问题是本仓库 RuntimeAssignment、TaskRevision、CandidateSet 和 Owner 权限的特定领域一致性问题；
成熟通用库不能替代该业务事实模型。实现预计复用现有 Pydantic、SQLite、FileLock、Broker、Radix、
TanStack Query、SSE 和 Playwright，不新增 Python/npm 依赖。

若实现阶段发现现有工具不足以完成可靠迁移或浏览器验收，必须先向用户说明拟安装工具、版本、
收益、风险和数据外发范围，不得静默改走低质量路线。
