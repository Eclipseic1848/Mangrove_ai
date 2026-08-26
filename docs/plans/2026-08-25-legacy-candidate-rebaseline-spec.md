# Legacy Candidate 再基线规格

> 状态：`SPEC_APPROVED`
>
> 日期：2026-08-25
>
> 当前阶段：规格已批准；未授权任务拆分、实现、数据库迁移、Provider 外发或正式发布
>
> 关联：[ADR-0033](../adr/0033-candidate-reverification-and-verifier-ruleset.md)、
> [CV-10 阻断诊断](2026-08-25-cv-10-legacy-unversioned-diagnosis.md)

## 1. 目标

为最新验证记录是 `failed + legacy_unversioned` 的同 Run Candidate 提供一次正式、可复用、
Owner 控制的“再基线”入口：承认旧实际 VerifierRuleset 无法证明，保留旧 Attempt，不重跑 Pi，
使用当前可证明的 VerifierRuleset 对原不可变 CandidateSet 执行完整验证，并追加一个 versioned
VerificationAttempt。

本能力解决的是“建立第一条可信 versioned 验证基线”，不是证明规则发生变化，也不是修复旧
Candidate、覆盖旧失败或绕过发布门。

## 2. 已验证事实

1. ADR-0033 要求旧 Run 缺少实际执行身份时保持 `legacy_unversioned`，不得用 GateSnapshot、
   当前工作树、服务重启或人工声明补猜。
2. 生产目标 `liyi111 / workspace_8363695f133645ac / revision 1` 的 CandidateSet、连接版本、模型
   和文件未漂移；最新 Attempt 是 `failed + legacy_unversioned`，当前唯一 blocker 是
   `legacy_unversioned`。
3. 现有 CandidateVerification 已具备追加式 Attempt、当前 Ruleset 身份、完整验证、Owner/P0/
   漂移门、Provider Grant/Usage、未知结果收口和精确 Attempt 发布能力。
4. Gate A 已完成生产显式迁移；当前没有创建新 Attempt、调用 Provider 或发布 Delivery。

## 3. 领域语言

### Legacy Candidate 再基线（LegacyCandidateRebaseline）

Owner 在明确承认旧 VerifierRuleset 身份未知后，对同一 TaskRevision、Run 和不可变 CandidateSet
追加一次使用当前可证明 VerifierRuleset 的完整验证，以建立第一条可信 versioned 验证基线。

它不是 `ruleset_changed`：新旧规则不可比较。它也不是迁移、报告修复、Candidate 修复、任务重跑
或正式发布。

### 再基线授权证据（RebaselineAuthorizationEvidence）

随新 Attempt 冻结的结构化 Owner 授权事实，至少绑定授权文案版本、旧 Attempt、旧规则身份未知
确认、精确 CandidateSet、当前 Ruleset、TaskRevision、Run、Actor、时间，以及本次 Provider
外发确认状态。它只证明 Owner 授权本次再基线，不证明旧 Ruleset 身份。

## 4. 推荐的最小 V1 范围

### 4.1 允许

- 最新 Attempt 必须是 `failed + legacy_unversioned`；
- 只处理同一 Owner、TaskRevision、Run 和 CandidateSet；
- 只执行一次从 legacy 链到第一条 versioned Attempt 的转换；
- 使用当前完整 Verifier 执行 artifact set/count、输出契约、来源、禁止项和语义门；
- 新 Attempt 的原因使用独立枚举 `legacy_rebaseline`；
- 新 Attempt 通过后停在“验证通过，等待发布”；发布仍是独立 Owner 动作。

### 4.2 不允许

- 不开放 legacy `passed`、`inconclusive`、`cancelled` 或 `outcome_unknown` 的再基线；
- 不批量扫描或批量重验 35 条 legacy Attempt；
- 不允许管理员或超级管理员代替 TaskOwner 操作；
- 不补写、改写或删除旧 Attempt、旧 VerificationReport 或 Runtime；
- 不重跑 Pi、不生成或修改 Candidate、不创建 TaskRevision；
- 不把旧规则描述为“已变化”或“与当前相同”；
- 不自动发布 Delivery，不自动重试未知 Provider 结果；
- 不新增一次性脚本、人工改库、管理员后门或特殊目标白名单。

## 5. 资格 Offer

现有 `inspect_reverification` 查询面继续作为唯一公开资格入口，但返回独立原因
`legacy_rebaseline`，不得复用 `ruleset_changed` 文案。

只有同时满足以下条件才可 `eligible=true`：

1. 调用者是 TaskOwner，任务、revision、Run 和 CandidateSet 精确匹配；
2. 最新 Attempt 是终态 `failed + legacy_unversioned`；
3. 该 Candidate 链尚不存在任何 versioned Attempt；
4. Candidate、Manifest、来源快照、GoalContract 和 DeliverySpec 均可从冻结权威数据验证且未
   漂移；缺失时失败关闭，不从当前 UI、口头说明或可变配置补猜；
5. 当前 VerifierRuleset 和实际执行身份可解析；
6. 没有正式 Delivery、活动 Attempt 或未收口的 `outcome_unknown`；
7. P0/Gate 未阻断，任务未取消，连接版本和模型仍可用；
8. Provider 路线能明确展示外发类别、连接、模型和潜在费用。

Offer 必须明确展示：

- “旧验证规则身份无法证明，因此无法判断规则是否变化”；
- 将验证的精确文件数量、格式和 CandidateSet 摘要；
- 当前可证明 Ruleset 摘要；
- 不重跑任务、不修改文件、不自动发布；
- Provider 连接、模型、外发范围和潜在费用；本地路线明确显示“不外发”。

Offer 是只读投影，不创建 Attempt、Grant、Usage 或授权记录。

## 6. Owner 授权与命令

写命令必须同时携带并由服务端 CAS 校验：

- `expected_revision`；
- `expected_previous_attempt_id`；
- `expected_candidate_set_hash`；
- `expected_target_ruleset_hash`；
- `legacy_ruleset_unknown_acknowledged=true`；
- `authorization_text_version`；
- `external_api_confirmed`；
- Provider 未知结果恢复时既有的 `accept_duplicate_provider_cost`；
- 独立 `Idempotency-Key`。

UI 推荐在同一确认对话框中提供两个语义独立的确认项：

1. 我理解旧验证规则身份未知，本次是在当前规则下建立新的可信基线；
2. 若使用外部 Provider，我确认本次连接、模型、外发范围和潜在费用。

本地路线不显示第二个勾选项，但第一个再基线确认始终必需。管理员角色不能替代 Owner 确认。

## 7. 授权证据与持久化

推荐在 VerificationAttempt 增加可空的结构化再基线证据及摘要，而不是单独建立可漂移的 UI
审计表：

- `rebaseline_authorization_json`；
- `rebaseline_authorization_hash`。

当且仅当 `reason_code=legacy_rebaseline` 时二者必须非空、哈希匹配，且证据中的 Owner、前序
Attempt、CandidateSet、TaskRevision、Run 和目标 Ruleset 与 Attempt 列完全一致。其他原因的
Attempt 二者必须为空。

该变化需要新的显式数据库迁移；应用启动不得静默 DDL。旧 Attempt 保持原字节和不可变约束，
新字段对旧行保持 NULL。生产迁移需要独立授权、唯一恢复点、完整性、外键、逻辑指纹、重放和
恢复验证。

## 8. Attempt 状态与执行

```text
legacy failed Attempt（不可变）
  └─ Owner 授权 legacy_rebaseline
       └─ versioned Attempt: requested → running → 终态
```

- 新 Attempt 的 `previous_attempt_id` 必须指向精确 legacy Attempt；
- 在读取 Candidate 或调用 Provider 前，先冻结当前 Ruleset、授权证据和 requested Attempt；
- Worker 认领时再次校验 P0、Owner、TaskRevision、Run、CandidateSet、文件/Manifest、来源、
  合同、连接版本、授权证据和当前执行身份；任一漂移则取消或失败关闭；
- 完整验证必须证明 Pi start/resume、Candidate 写入和 revision 创建调用次数为 0；
- 确定性结果形成 versioned `passed | failed | inconclusive` Attempt；
- 请求可能已到 Provider 但结果不明时使用现有 `outcome_unknown` 语义，禁止自动重发；
- 一旦链上存在 versioned Attempt，后续只允许既有 `semantic_inconclusive`、`ruleset_changed` 或
  Provider 未知结果恢复规则，不能再次执行 `legacy_rebaseline`。

## 9. 发布与验收边界

- 再基线 passed 只产生“等待发布”，不创建 Delivery；
- Owner 检查报告后另行调用现有精确 Attempt 发布动作；
- Publisher 继续校验 P0、Owner、CandidateSet、DeliverySpec、文件完整性和 QA；
- 正式发布、预览和下载仍需独立确认，不能由本规格批准推断；
- CV-10 的状态顺序保持 `GATE_A_COMPLETED → LIVE_REVERIFIED → LIVE_ACCEPTED`，其中
  `LIVE_REVERIFIED` 不等于发布或 Owner 最终接受。

## 10. 失败与边界场景

| 场景 | 预期 |
|---|---|
| 最新 legacy Attempt 不是 failed | 不提供 V1 再基线 |
| 链上已有 versioned Attempt | 拒绝再次再基线，转既有资格语义 |
| Owner 未确认旧规则未知 | 422，零 Attempt、零外发 |
| 前端声称确认但命令身份漂移 | 409，重新读取 Offer |
| Candidate/Manifest/来源/合同缺失或漂移 | 失败关闭，不补猜、不补写 |
| 管理员读取他人任务后尝试代办 | 403，零正文扩权、零外发 |
| P0 在提交或 Worker 认领前变为阻断 | 不启动；requested 安全取消 |
| Provider 发送后超时 | outcome_unknown，零自动重试 |
| 新验证 failed | 保留新 failed；同 Ruleset 不得再次运行 |
| 新验证 passed | 等待 Owner 独立发布 |

## 11. 验证矩阵

### 11.1 领域与 Repository

- `legacy_rebaseline` 原因与授权证据的互斥/必填约束；
- 授权摘要、Attempt 列和前序链一致性；
- 终态不可改写，纠正只能追加；
- 幂等重放、不同请求冲突、并发只创建一个活动 Attempt；
- 显式迁移空库/生产副本/重放/恢复/旧表逻辑指纹与旧 Attempt 原字节不变。

### 11.2 Offer 与权限

- `failed + legacy_unversioned` 且所有门满足时返回独立再基线 Offer；
- passed/inconclusive/cancelled/outcome_unknown、已有 versioned、跨 Owner、P0、Delivery、漂移和
  Ruleset 不可用均失败关闭；
- Offer 重复读取零数据库变化、零 Grant、零 Provider 调用。

### 11.3 执行与恢复

- 完整验证覆盖所有确定性和语义门；
- Pi/候选写入/revision 创建调用次数为 0，输入文件哈希前后不变；
- Provider Attempt 先落库，Grant/Usage 精确绑定，外发确认缺失时调用次数为 0；
- 取消和崩溃窗口分别收口，outcome_unknown 零自动重试；
- 新 versioned Attempt 形成后无法再次 legacy 再基线。

### 11.4 API、前端和真实验收

- 独立文案不把 unknown 说成 changed；两个确认项、焦点、Escape、键盘、窄屏、200% 缩放、
  深浅主题和 axe 基线；
- 刷新恢复、busy 防重、错误可理解且可重试；
- 现有初验、语义重试、规则变化重验、历史读取和精确发布零回归；
- 生产只读 preflight 后，由 Owner 单独确认 Provider 外发，完成一条目标 Candidate 再基线；
- 核对旧 Attempt 保留、新 versioned Attempt、Usage/Grant、零未知状态、数据库完整性和零文件/
  revision/Pi 改写；正式发布仍另行确认。

## 12. 开源与依赖判断

该问题是 Mangrove 自有 Owner、TaskRevision、CandidateSet、VerificationAttempt、Provider Grant
和 Delivery 状态机之间的领域一致性，不存在能直接替代这些业务不变量的通用开源工具。

推荐复用现有 SQLite 约束、Pydantic 冻结模型、FastAPI Adapter、CandidateVerification Module、
Broker/Grant/Usage、DeliveryPublisher、Radix AlertDialog、TanStack Query、SSE 和 Sonner。
V1 不需要新增 Python 或 npm 依赖；若任务拆分阶段发现现有组件无法提供必要验证质量，必须先向
用户说明用途、版本、收益、数据外发与风险并请求安装，不得静默换低效路线。

## 13. 明确排除

- legacy passed/inconclusive 的恢复策略；
- 批量再基线、自动扫描、定时任务和管理员批处理；
- 外部历史执行身份证据导入；
- 修复 Candidate、重跑任务、创建 revision 或修改旧报告；
- 新 Provider、新来源类型、新能力受众或普通用户权限扩大；
- GitHub Issue、分支、提交、推送、PR、标签、Release、部署；
- 生产数据库迁移、真实 Provider 外发、真实再基线和正式 Delivery 发布。

历史 `inconclusive` Candidate 的现有语义重试阻断已单独记录在
[历史 inconclusive Candidate 重试阻断诊断](2026-08-25-legacy-inconclusive-retry-diagnosis.md)。
它只需重跑语义门，不应借 `legacy_rebaseline` 扩大为完整再基线；是否加入当前工作必须由用户
独立确认。

## 14. 完成定义

- `SPEC_APPROVED`：用户确认第 15 节五项业务决定；不等于授权任务拆分或实现。
- `TICKETS_READY`：后续显式调用任务拆分流程并冻结纵切片、依赖和验收门。
- `ENGINEERING_VERIFIED`：实现、迁移演练、相称回归、浏览器验收和双轴审查通过。
- `LIVE_REBASELINED`：独立授权后，对真实目标形成第一条 versioned Attempt，旧 Attempt 保留。
- `LIVE_REVERIFIED`：新 Attempt 已确定性收口并由 Owner 检查结果。
- `LIVE_ACCEPTED`：Owner 另行授权发布并验收正式 Delivery。

## 15. 必须由用户确认的业务决定

1. **V1 范围（已确认）**：只允许最新 `failed + legacy_unversioned`，不顺带开放其他终态。
2. **单次边界（已确认）**：每条 Candidate 链最多一次 legacy 再基线；出现第一条 versioned
   Attempt 后永久回到 ADR-0033 既有资格规则。
3. **授权证据（已确认）**：把结构化 Owner 再基线授权及哈希冻结在新 Attempt，并通过新的显式
   迁移增加可空字段；不依赖前端日志或普通审计事件。
4. **确认交互（已确认）**：旧规则未知确认与 Provider 外发确认语义独立，但在同一个提交对话框
   中完成；本地路线只要求旧规则未知确认。
5. **发布边界（已确认）**：再基线通过后等待发布，正式 Delivery 必须由 Owner 另行
   确认。

五项均已由用户确认，本规格状态为 `SPEC_APPROVED`。下一阶段是任务拆分；在用户显式同意进入
该阶段前，不得开始实现、数据库迁移、Provider 外发或正式发布。
