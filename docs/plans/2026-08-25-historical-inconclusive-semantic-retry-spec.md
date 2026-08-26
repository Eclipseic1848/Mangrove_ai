# 历史 inconclusive Candidate 语义重试恢复规格

> 状态：`ENGINEERING_VERIFIED_PRODUCTION_GATE_REQUIRED`
>
> 日期：2026-08-25
>
> 范围决定：用户已确认作为独立纵切片加入当前工作
>
> 当前阶段：语义重试与历史重验权威恢复均已完成工程验证；真实目标等待生产 `0003` 迁移、
> TaskOwner 精确写入门和随后一次已授权 Provider 调用；尚未执行生产写入或正式发布
>
> 诊断：[历史 inconclusive 重试阻断](2026-08-25-legacy-inconclusive-retry-diagnosis.md)

## 1. 目标

恢复旧任务中 `inconclusive` Candidate 的语义重试能力：当 artifact、数量、表格契约（如有）和
来源证据门已经通过、只有 `semantic_goal` 未形成可靠结论时，由 TaskOwner 重新确认本次
Provider 外发，在同一 TaskRevision、Run 和不可变 CandidateSet 上只重跑语义门，追加一个
versioned VerificationAttempt，并在通过后等待独立发布。

它不是 `legacy_rebaseline`。旧 Ruleset 仍然未知；新 Attempt 只证明“当前 Ruleset 对既有已验证
输入完成了一次新的语义判断”。

## 2. 已验证代码现状

1. 旧 `retry_candidate_verification` 入口只重跑语义门，但在业务校验前追加 started 事件，且
   passed 后进入旧自动发布路径。
2. 新 CandidateVerification 请求入口具备只读 Offer、Owner/P0/漂移门、requested/running/
   终态 Attempt、幂等、Worker 恢复、Provider Grant/Usage、`outcome_unknown` 和独立发布。
3. 新 Worker 当前无论 `semantic_inconclusive` 还是 `ruleset_changed` 都调用完整 `verify`，尚未
   按 Attempt reason 选择“语义门重试”。
4. 历史目标旧 `request_json` 缺少 `external_api_confirmed`，但 Runtime 冻结列保留旧值，且任务
   原有 Provider Grant/Usage 可证明旧运行确实走外部连接。旧确认不能授权新 Attempt。
5. 现有 Attempt Schema 已包含 reason、前序 Attempt、当前 Ruleset、连接版本、模型、
   `egress_confirmed_at`、Provider Attempt、请求哈希和状态机；本纵切片预计不新增数据库字段。

## 3. 产品行为

### 3.1 只读 Offer

服务端只在以下条件同时满足时返回 `eligible=true`、
`reason=semantic_inconclusive`：

- 调用者是 TaskOwner；
- 当前任务仍是同一 active revision、Run 和 `candidate_ready`；
- 最新报告是 `inconclusive`，且只有 `semantic_goal` 未通过；
- artifact set/count、必要表格契约和 source grounding 均已通过；
- 最新 Attempt 与当前报告哈希、Run、CandidateSet 精确对应；
- Candidate、Manifest、来源文件、目标和交付契约均未漂移；
- 没有正式 Delivery、活动 Attempt、未收口 `outcome_unknown` 或 P0 阻断；
- 当前 Ruleset、连接版本、模型和 Provider Authority 可用。

最新 Attempt 为 `legacy_unversioned` 不单独阻断语义重试，因为本动作不声称新旧 Ruleset 已变化；
新 Attempt 必须冻结当前可证明 Ruleset。若最新状态是 `failed`，仍走独立
`legacy_rebaseline` 决策，不得借本入口放宽。

Offer 必须展示：只重跑语义门、不重跑 Pi、不修改文件、不创建 revision、不自动发布，以及
精确 CandidateSet、连接、模型、外发范围和潜在费用。Offer 查询零写入、零 Grant、零 Provider
调用。

### 3.2 历史请求兼容

不得回填旧 `request_json`。当且仅当旧请求唯一缺少 `external_api_confirmed` 时，服务端可在
内存中使用同一 Runtime 行的冻结 `external_api_confirmed` 列完成只读结构解析；其他缺失或冲突
继续失败关闭。

该历史值只证明旧 Run 的绑定，不授权新调用。写命令仍必须由 Owner 显式提交新的
`external_api_confirmed=true`；新 Attempt 的 `egress_confirmed_at`、request hash 和 Provider
Attempt 共同冻结本次授权。

### 3.3 写命令与状态

写命令至少包含：

- `expected_revision`；
- `expected_previous_attempt_id`；
- `external_api_confirmed`；
- Provider 未知结果恢复时的 `accept_duplicate_provider_cost`；
- 独立 `Idempotency-Key`。

服务端必须在追加任何“重试开始”事件前完成 Owner、资格、外发确认、当前 Ruleset、连接和文件
身份检查，并先创建 `reason=semantic_inconclusive` 的 requested Attempt。事件只投影已存在的
Attempt，不得再出现“有 started 事件但没有 Attempt”的孤立状态。

```text
legacy/versioned inconclusive Attempt（不可变）
  └─ Owner 本次外发确认
       └─ versioned semantic_inconclusive Attempt
            requested → running → passed | failed | inconclusive
                                  ↘ outcome_unknown
```

### 3.4 Worker

Worker 按 `reason_code` 选择操作：

- `semantic_inconclusive`：调用 `retry_semantic_verification`，复用旧报告中已经通过的确定性门，
  重新核验 Candidate/Manifest 哈希后只调用语义 Judge；
- `ruleset_changed` 与未来 `legacy_rebaseline`：继续调用完整 `verify`。

认领前再次校验 P0、Owner 权威、TaskRevision、Run、CandidateSet、原文件、Manifest、来源、
合同、连接和实际 Ruleset 身份。任何漂移都取消或失败关闭。Provider 可能已收到请求而结果不明
时进入 `outcome_unknown`，禁止自动重试；恢复需要 Owner 再确认重复费用风险。

### 3.5 发布

语义重试 passed 后只显示“验证通过，等待发布”，不得调用旧
`_publish_verified_candidates` 自动发布路径。Owner 检查新报告后，另行调用现有精确 Attempt
发布动作；Publisher 继续校验 P0、Owner、CandidateSet、DeliverySpec、文件完整性和 QA。

## 4. API 与前端

- 工作台按钮资格完全来自服务端 Offer，不再只凭 `verification.status=inconclusive` 猜测；
- Provider 路线必须使用确认对话框展示连接、模型、外发类别、费用风险和“只重跑语义门”；
- busy 防重复，刷新后从 Attempt 恢复 requested/running/outcome_unknown/终态；
- 合同缺失、连接失效、P0、漂移、外发未确认和 Provider 未知结果显示不同的可行动说明；
- 通过后展示独立“发布正式结果”，不自动切换到正式预览；
- 键盘、焦点、Escape、窄屏、200% 缩放、深浅主题和 axe 基线必须验证。

前端实现阶段继续强制使用 `frontend-design` 与 `frontend-design-premium`，沿用现有组件和视觉
语言；本规格不授权现在修改页面。

## 5. 失败场景

| 场景 | 预期 |
|---|---|
| 结果看起来正确但语义门无结论 | 仍是 Candidate；Owner 可发起语义重试，不能人工标绿 |
| 除 semantic_goal 外还有确定性门失败 | 不允许语义重试 |
| 旧 request 仅缺 external_api_confirmed | 只读解析可使用 Runtime 冻结列；新外发仍重新确认 |
| 旧 request 缺少目标、来源、连接版本等语义字段 | 失败关闭，不补猜、不回填 |
| 点击时资格已漂移 | 409，重新读取 Offer；零 Attempt/零事件/零外发 |
| 外发未确认 | 422；零 Provider 调用 |
| Worker 前 P0 或文件漂移 | requested 安全取消；零 Provider 调用 |
| Provider 已发送但结果不明 | outcome_unknown；零自动重发 |
| 新语义结论 failed | 保留新 failed；不得自动重跑或发布 |
| 新语义结论 passed | 等待 Owner 独立发布 |

## 6. 验证矩阵

### 6.1 服务端

- legacy/versioned inconclusive 的 Offer 正向矩阵；failed、确定性门失败、跨 Owner、P0、
  Delivery、活动 Attempt、未知结果和各类漂移反向矩阵；
- 只缺外发字段的兼容解析与其他字段缺失失败关闭；
- Owner 本次外发确认、幂等冲突、并发单 Attempt、Grant/Usage/Provider Attempt 绑定；
- Worker 按 reason 选择语义重试或完整验证，明确断言 Pi、Candidate 写入、revision 创建和
  确定性来源重读调用次数；
- started 事件必须引用已存在 Attempt，预检失败零孤立事件；
- outcome_unknown、取消、进程恢复和重复费用确认；
- passed 零 Delivery，显式发布后唯一 Delivery。

### 6.2 前端与真实目标

- Offer、确认对话框、错误恢复、刷新恢复、独立发布和可访问性 E2E；
- 旧按钮不再在无服务端资格时出现；
- 对 `liyi / workspace_c115f33be1004f51 / revision 1` 做生产只读 preflight；
- 工程门通过后，按第 10 节已经给出的单次授权直接执行真实 Provider 外发，不再重复展示确认；
- 核对只新增一条 versioned Attempt、旧 Attempt/CSV/88 条证据保留、Pi/文件/revision 零改写、
  Grant 撤销、Usage 完整、零未知状态；
- 真实语义结果由 Verifier 决定，不能因用户或开发者认为 CSV 可用而预设 passed；
- 正式发布与 Owner 验收继续单独授权。

## 7. 开源与依赖判断

本纵切片复用现有 CandidateVerification、SQLite/Pydantic、Worker/FileLock、Broker/Grant/Usage、
DeliveryPublisher、Radix AlertDialog、TanStack Query、SSE 和 Sonner。核心问题是仓库自有领域
状态机和历史契约兼容，没有成熟开源工具能替代；预计无需新增 Python/npm 依赖。

若实现阶段发现现有工具无法达到验证质量，必须先向用户说明工具、版本、收益、风险和数据外发
后请求安装，不得静默改走低效路线。

## 8. 明确排除

- 人工接受 Candidate、跳过语义门或管理员标绿；
- `failed + legacy_unversioned` 完整再基线；
- 重跑 Pi、修复/生成 Candidate、创建 revision；
- 批量历史重试、自动扫描或定时任务；
- 自动发布、批量生产外发、提交、推送、PR、标签、Release 或部署。

## 9. 完成定义

- `SPEC_APPROVED`：用户确认第 10 节决定；不等于授权任务拆分或实现。
- `ENGINEERING_VERIFIED`：TDD、相称回归、浏览器验收和双轴审查通过。
- `LIVE_SEMANTIC_RETRIED`：独立授权后，真实旧 Candidate 形成 versioned 语义重试 Attempt。
- `LIVE_REVERIFIED`：新 Attempt 已确定性收口并由 Owner 检查。
- `LIVE_ACCEPTED`：Owner 另行授权发布并验收正式 Delivery。

## 10. 用户决定

1. **独立范围（已确认，2026-08-25）**：作为独立纵切片加入，不并入
   `legacy_rebaseline`。
2. **验证深度（已确认，2026-08-25）**：只重跑未形成结论的语义门，复用并重新校验已通过门的冻结
   报告与文件哈希；不重新读取 88 条来源或执行完整 Verifier。
3. **外发规则（已确认，2026-08-25）**：产品继续保留每个新 Provider Attempt 的独立确认，
   旧任务当年的确认不能自动复用。对于第 5 项精确目标，用户已经在当前对话直接给出本次授权，
   要求工程门通过后直接调用，不再重复展示连接、模型、外发范围和费用确认。
4. **发布（已确认，2026-08-25）**：语义重试通过后等待发布，不走旧自动发布路径；这次调用
   授权不包含正式发布。
5. **真实目标（已确认，2026-08-25）**：工程门通过后，对
   `liyi / u_9505fd620899 / workspace_c115f33be1004f51 / revision 1 /
   pi_run_42daee348b9a45bc` 使用既有连接 `7d45d047-68de-4db0-b8b0-b1e4638aa591` 和模型
   `deepseek-v4-flash` 执行且只执行一次语义重试。外发范围限现有 Candidate 预览和冻结的 88 条
   证据摘要；不得重跑 Pi、修改 CSV、创建 revision、自动发布或在 `outcome_unknown` 后自动重试。

上述决定使规格达到 `SPEC_APPROVED`，用户随后明确要求“直接开搞”，因此当前状态为
`ENGINEERING_VERIFIED_PRODUCTION_GATE_REQUIRED`。语义重试与 ADR-0034 定义的追加式历史重验权威
恢复均已完成 TDD、相称回归、前端浏览器验证和 Standards/Spec 双轴复核；证据见
`2026-08-25-historical-inconclusive-semantic-retry-implementation-report.md` 与
`2026-08-25-historical-reverification-authority-recovery-implementation-report.md`。

生产只读 preflight 已确认目标缺少普通 RuntimeAssignment；工程实现不会补写、合成或绕过该历史
事实，而是只允许 TaskOwner 对精确旧 CandidateSet 追加用途受限、不可改删的
`HistoricalReverificationAuthority`。当前下一门禁是用户独立确认生产 CandidateVerification `0003`
显式迁移和 `liyi` TaskOwner 精确 authority/request 写入。确认前尚未创建真实 Attempt、调用
Provider、产生费用或发布 Delivery；本纵切片不另拆 GitHub 工单，提交、推送和正式发布仍未授权。
