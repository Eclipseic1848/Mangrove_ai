# P0-01：同 Run 候选完整重验任务拆分

> 状态：TICKETS_PUBLISHED，用户于 2026-08-24 确认任务集合与依赖关系；GitHub #61～#70
>
> 日期：2026-08-24
>
> 上游规格：`2026-08-24-p0-01-same-run-candidate-reverification-spec.md`（SPEC_APPROVED）
>
> 目标问题跟踪器：`Eclipseic1848/Mangrove_ai`；本阶段未创建或修改任何 GitHub Issue
>
> 执行纪律：每张工单独立上下文、TDD 红绿循环、聚焦回归、Standards/Spec 双轴审查；
> 工单间不得自动推进，Git、生产迁移、Provider 和正式发布仍分别授权

## 1. 冻结任务集合

本任务拆分冻结为 `CV-01`～`CV-10` 共 10 张工单。未经新的范围授权，不增加“顺手重构”、
通用迁移平台、批量历史重验、管理员后门或新的 Provider/依赖工单。

| 工单 | 标题 | 类型 | 建议标签 | 主要产物 |
|---|---|---|---|---|
| CV-01 | 冻结 CandidateVerification 架构与 VerifierRuleset 身份 | 决策门 | `ready-for-agent` | ADR-0033、规则身份清单 |
| CV-02 | 建立追加式 VerificationAttempt 与显式迁移 | 工程纵切片 | `ready-for-agent` | 领域模型、Repository、迁移、恢复证据 |
| CV-03 | 用 CandidateVerification Module 承接既有验证入口 | 工程纵切片 | `ready-for-agent` | 深 Module、兼容 Adapter、初验/语义重试审计 |
| CV-04 | 提供同 Run 重验资格 Offer 与失败关闭投影 | 产品纵切片 | `accepted` | 只读 Offer、任务投影、Owner/P0/漂移门 |
| CV-05 | 执行不重跑 Pi 的完整候选重验 | 产品纵切片 | `engineering-verified` | 202 Attempt、完整 Verifier、待发布状态 |
| CV-06 | 接入 Provider Attempt、重复外发确认与未知结果收口 | 安全纵切片 | `ENGINEERING_VERIFIED` | Grant/Usage 绑定、outcome_unknown、取消恢复 |
| CV-07 | 建立精确 Attempt 绑定的显式正式发布动作 | 交付纵切片 | `ENGINEERING_VERIFIED` | Publish 接口、Owner/P0/幂等/QA 门 |
| CV-08 | 完成普通用户重验与发布工作台流程 | 前端纵切片 | `ENGINEERING_VERIFIED` | Offer、确认框、时间线、显式发布、E2E |
| CV-09 | 完成工程门、双轴审查与实施交接 | 收口门 | `ENGINEERING_VERIFIED` | 相称回归、浏览器证据、审查修复、文档同步 |
| CV-10 | 生产迁移、真实同 Run 重验与 Owner 验收 | 人工门 | `ready-for-human` | 生产备份/迁移、LIVE_REVERIFIED、LIVE_ACCEPTED |

GitHub 现场映射：CV-01～CV-10 分别为 #61～#70，父任务 P0-01 为 #54。所有远端写入均已
显式指定 `--repo Eclipseic1848/Mangrove_ai`；依赖通过正文 `Part of`、`Blocked by` 和 #54
任务清单记录。

## 2. 依赖图

```text
CV-01
  ↓
CV-02
  ↓
CV-03
  ↓
CV-04
  ↓
CV-05
  ├────────→ CV-06 ────────┐
  └────────→ CV-07 ────────┤
                            ↓
                          CV-08
                            ↓
                          CV-09
                            ↓
                          CV-10
```

- CV-06 与 CV-07 可在 CV-05 完成后并行，但不能共享未冻结的 Schema 或 Interface 变更。
- CV-08 必须等待 Provider 安全与显式发布两个后端 Interface 都稳定。
- CV-10 是生产写入和真实外发人工门，不能被工程测试自动触发。

## 3. 工单详情

### CV-01 冻结 CandidateVerification 架构与 VerifierRuleset 身份

**Blocked by**：无。

**目标**：形成 ADR-0033，冻结追加式 VerificationAttempt、CandidateVerification Module
Seam、历史规则身份重建和验证/发布分离，防止实施退回“放宽一个条件”的补丁路线。

**范围**：

- 新增 `docs/adr/0033-candidate-reverification-and-verifier-ruleset.md`；
- 明确 `inspect_reverification` 与 `request_reverification` 是 Module 外部 Interface；
- 冻结 VerifierRuleset Manifest 的规范化字段：代码提交、相关源码/Prompt/配置/依赖摘要；
- 分离规则语义身份与执行来源身份：`ruleset_changed` 只比较规范化规则摘要，无关 commit 变化
  只改变 execution identity；
- 冻结相关源码允许列表的归属和变更规则；不能把整个脏工作树纳入身份；
- 冻结历史证据链：
  `RuntimeAssignment.gate_snapshot_id → GateSnapshot.code_commit → Git commit object →
  verifier_source_hash` 只能形成门禁规则候选；还必须有不可变 Run 级实际执行身份凭据并一致；
- commit 对象、Assignment、GateSnapshot、相关源码、Run 执行凭据或一致性检查缺失时保持
  `legacy_unversioned`；
- 冻结 Publisher 只能绑定精确 VerificationAttempt/report hash，不能绑定可漂移最新指针。

**红灯/验收**：

- 用规格对照表证明 ADR 覆盖六项已确认业务决定；
- 用两个具体反例验证决策：只有 GateSnapshot、没有 Run 级执行身份的旧报告不得自动获得规则
  变化资格；无关 G1 文件修改不得改变 VerifierRuleset；
- ADR 不包含本机绝对路径、真实任务 ID、Provider 地址或 Secret；
- 文档 UTF-8、无重复术语、无历史 ADR 回写。

**主要文件**：

- `docs/adr/0033-candidate-reverification-and-verifier-ruleset.md`
- `docs/plans/2026-08-24-p0-01-same-run-candidate-reverification-spec.md`（仅必要交叉引用）

**不做**：业务代码、数据库、GitHub、生产操作。

### CV-02 建立追加式 VerificationAttempt 与显式迁移

**Blocked by**：CV-01。

**目标**：在不覆盖 `agentic_runtime_runs.verification_json` 历史事实的前提下，建立不可变
VerificationAttempt Repository，并提供带恢复点的唯一显式迁移入口。

**范围**：

- 新建 `src/candidate_verification/` Module 基础：`models.py`、`repository.py`、
  `migrations/0001_candidate_verification_attempts.sql`、公开导出；
- 表含 attempt/owner/task/revision/run、previous attempt、reason、CandidateSet/Manifest/
  GoalContract/DeliverySpec/Ruleset、Actor、外发/Provider、幂等、报告和时间字段；
- 数据库约束：终态值域、主键、同请求幂等、同 CandidateSet 单活动 Attempt 的部分唯一索引；
- 状态只允许 `requested → running → 终态` 的受约束前向 CAS；终态后 UPDATE 和所有 DELETE
  由触发器拒绝，纠正只能追加后继 Attempt；
- 显式迁移沿用 AC-05 已验证模式：文件锁、`BEGIN IMMEDIATE`、SQLite online backup、首次
  backup SHA-256 绑定、完整性/外键/逻辑指纹、同路径幂等重放、失败恢复；
- legacy `verification_json` 导入为初始 Attempt；能由 CV-01 证据链证明时写入旧 Ruleset
  身份，不能证明时写 `legacy_unversioned`；
- Repository 初始化只校验 Schema；未迁移时报可执行错误，不得静默建表或回填。

**TDD 红灯**：

- 空库、当前结构副本、重复迁移、损坏源库、错误备份、备份后 DDL 前崩溃、锁竞争；
- `passed/failed/inconclusive` legacy 报告逐字语义导入；旧报告为空时不伪造 Attempt；
- Assignment/Gate/commit/Run 执行身份任一缺失保持 unversioned；完整且一致的链得到稳定
  source hash；
- 非法跳转、终态 UPDATE 和所有 DELETE 被数据库拒绝；合法前向 CAS 可执行且并发只产生一个
  活动 Attempt；
- 既有 Candidate、Run、TaskRevision、Delivery、Usage 和旧事件逻辑指纹零改写。

**绿灯/证据**：

- 新建 `tests/test_candidate_verification_migration.py` 和
  `tests/test_candidate_verification_repository.py` 全绿；
- 恢复副本可读、迁移重放不覆盖首次恢复点；
- 本工单只在临时数据库和生产副本上演练，不迁移 `data/webui.db`。
- 2026-08-24 工程验证结果见
  `docs/plans/2026-08-24-cv-02-verification-attempt-migration-report.md`；当前状态为
  `ENGINEERING_VERIFIED`，不等于生产迁移或用户验收。

**主要文件**：

- `src/candidate_verification/models.py`
- `src/candidate_verification/repository.py`
- `src/candidate_verification/migrations/0001_candidate_verification_attempts.sql`
- `src/candidate_verification/__init__.py`
- `tests/test_candidate_verification_migration.py`
- `tests/test_candidate_verification_repository.py`

**授权门**：实际生产迁移留给 CV-10，不能从本工单推断。

### CV-03 用 CandidateVerification Module 承接既有验证入口

**Blocked by**：CV-02。

**目标**：让初始 Candidate 验证与既有 `inconclusive` 语义重试都经同一深 Module 追加
VerificationAttempt；现有 API 和用户行为保持兼容，不形成两套验证真相。

**范围**：

- 新增 `src/candidate_verification/service.py`，以依赖注入接收 Repository、Verifier、
  Ruleset resolver、P0 reader、Broker Adapter 和事件写入 Adapter；
- 初始验证 reason=`initial`；现有语义重试 reason=`semantic_inconclusive`；
- Module 原子追加 Attempt，并在同一事务维护 `verification_json` 和
  `verified_candidate_set_hash` 兼容投影；Attempt 是审计权威；
- 现有 `POST .../candidate-verification/retry` 变为薄兼容 Adapter，不再拥有状态判断；
- 现有 `inconclusive` 重试继续不重跑 Pi，并保持当前成功后发布行为，直到 CV-07 只为完整
  重验新增显式发布；不得借本工单改变所有初始任务发布体验。

**TDD 红灯**：

- initial、passed、failed、inconclusive 都形成精确 Attempt；
- 相同请求幂等返回同 Attempt；不同请求冲突；
- Repository 写 Attempt 成功但兼容投影失败时事务回滚；反向亦然；
- 当前语义重试的文件/来源复用门、候选哈希和 `PiRuntime.start_calls == 1` 零回归；
- Owner、task、revision 串线被拒绝。

**绿灯/证据**：

- 新建 `tests/test_candidate_verification_service.py`；
- `tests/test_candidate_verifier.py`、`tests/test_pi_runtime_workspace_api.py` 相关集合全绿；
- 删除 Module 后复杂度会回到多个调用点，证明 Seam 具有深度而非透传层。

**主要文件**：

- `src/candidate_verification/service.py`
- `src/candidate_verification/adapters.py`（仅确有两个 Adapter 时创建）
- `src/agentic_runtime/candidate_verifier.py`
- `src/agentic_runtime/repository.py`
- `src/api/semantic_workspace_runtime.py`
- `src/api/routes/semantic_workspace.py`
- 上述三类测试

### CV-04 提供同 Run 重验资格 Offer 与失败关闭投影

**Blocked by**：CV-03。

**当前状态**：`ENGINEERING_VERIFIED`。工程报告见
`docs/plans/2026-08-24-cv-04-reverification-offer-report.md`；等待用户接受，不自动进入 CV-05。

**目标**：TaskOwner 在任何写操作前都能看到服务端计算的 `ReverificationOffer`，并明确知道
是否可重验、为什么、会不会调用 Provider；前端不得自行猜测资格。

**范围**：

- 实现 `inspect_reverification(owner, task, revision) -> ReverificationOffer`；
- 检查 Owner、Pi Runtime、Candidate/Manifest/来源/GoalContract/DeliverySpec、活动 Attempt、
  outcome_unknown、已有 Delivery、P0/Gate 和 Ruleset 变化；
- 相关源码存在未提交变化时拒绝；无关工作树变化不进入身份；
- Task detail 投影增加 latest Attempt 摘要、Offer、规则变化说明、Provider/本地模型摘要；
- 普通用户只见产品字段；不返回路径、Secret、Base URL、Prompt 或内部规则文件列表。

**TDD 红灯**：

- `failed + proven ruleset_changed` eligible；同 Ruleset failed 不 eligible；
- `inconclusive` 映射既有 semantic retry；`legacy_unversioned` 不自动 eligible；
- Candidate/Manifest/来源任一漂移、P0 blocked、已有 Delivery、活动/未知 Attempt 均拒绝；
- Owner A 读取 Owner B 返回 404/403 且零内容泄露；
- 本地模型显示不外发，Provider 显示连接/模型/外发类别但不显示敏感值。

**绿灯/证据**：

- `tests/test_candidate_reverification_offer.py`；
- `tests/test_pi_runtime_workspace_api.py` 增加任务详情投影矩阵；
- Offer 查询是严格只读，重复查询数据库逻辑指纹不变。

**主要文件**：

- `src/candidate_verification/service.py`
- `src/candidate_verification/models.py`
- `src/api/routes/semantic_workspace.py`
- `src/api/semantic_workspace_runtime.py`
- 对应测试

### CV-05 执行不重跑 Pi 的完整候选重验

**Blocked by**：CV-04。

**目标**：TaskOwner 能以 HTTP 202 创建一个完整重验 Attempt；系统重跑全部独立验证门，
但不重新执行 Agent、生成文件或创建 revision。无外部 Provider 的本地模型路径在本工单闭环。

**范围**：

- 新增 `POST /api/semantic-workspace/tasks/{task_id}/candidate-verifications`；
- 请求只接收 expected revision/previous attempt、外发确认和 `Idempotency-Key`；reason 由服务端
  推导，客户端不能自报 `ruleset_changed`；
- 资格 CAS 后创建 requested Attempt，交由现有任务 Worker/SSE 体系异步执行；
- 重新执行 artifact set/count、table contract、source grounding、semantic goal；
- 明确禁止调用 `PiRuntime.start/resume`、候选写入、OCR/来源发现、修复循环、依赖/能力获取；
- passed 后保持 `candidate_ready`，投影“验证通过，等待发布”；不调用 Publisher；
- failed/inconclusive/cancelled 追加终态和事件，旧 Attempt 保留。

**TDD 红灯**：

- 真实多格式回归夹具在旧规则失败、新规则通过；旧 Attempt 仍可读取；
- 候选字节和 revision 前后完全一致；Pi start/resume/write 调用次数为 0；
- 幂等、并发、锁超时、资格预检后状态漂移和 Worker 崩溃窗口；
- P0 在请求前或 requested→running 间触发时安全拒绝/收口；
- 本地模型路径不创建 Provider Grant/Usage，界面投影“不外发”。

**绿灯/证据**：

- `tests/test_candidate_reverification_execution.py`；
- `tests/test_candidate_verifier.py` 完整文件全绿；
- `tests/test_pi_runtime_workspace_api.py` 新旧入口回归；
- 原 Candidate 文件树 SHA-256 清单前后一致。

**完成记录（2026-08-24）**：达到 `ENGINEERING_VERIFIED`。HTTP 202、完整本地 Verifier、
幂等/并发/P0/锁超时/活动 revision CAS、按 Attempt 跨进程租约恢复、确定性原子事件和 422
契约错误均已实现；八文件回归 116 passed。详见
`docs/plans/2026-08-24-cv-05-reverification-execution-report.md`。该状态不代表生产迁移、真实
Provider 外发、真实 Candidate 重验、正式发布或用户验收。

**主要文件**：

- `src/candidate_verification/service.py`
- `src/candidate_verification/models.py`
- `src/api/semantic_workspace_runtime.py`
- `src/api/routes/semantic_workspace.py`
- 对应测试

### CV-06 接入 Provider Attempt、重复外发确认与未知结果收口

**Blocked by**：CV-05。

**目标**：外部 Provider 重验在请求发送前有不可变 Attempt 和本次 Owner 确认；传输结果不确定
时停止自动重试并形成可恢复的 `outcome_unknown`。

**范围**：

- Offer 和命令冻结 connection_id/version/model，不接受客户端替换；
- 每个 VerificationAttempt 单独记录外发确认，旧 TaskRevision 确认不能代替本次重复请求确认；
- 先落 Provider Attempt/VerificationAttempt，再签发有界 Grant 和发送请求；
- Usage 绑定 verification attempt/provider attempt/run；Provider 未返回费用时显示未知；
- 响应丢失、超时、进程退出或持久化不确定进入 outcome_unknown；零自动再发；
- Owner 明确承担重复费用风险后只能创建引用旧 Attempt 的恢复 Attempt；
- 成功、失败、取消和未知终态都撤销 Grant；密钥不进入事件、报告或日志。

**TDD 红灯**：

- 缺确认 422、连接跨 Owner 403/404、版本/模型漂移 409；
- Attempt 持久化失败时 Provider 调用数 0；同 Attempt 并发调用数 1；
- Provider 已收到后超时 → outcome_unknown，重放相同请求不再次调用；
- 恢复未获确认被拒；获确认后新 Attempt 引用旧 Attempt，旧记录不改；
- Usage recorded/unknown 两类投影，费用未知不显示为 0；
- Grant 在所有终态和异常路径均无活动残留。

**绿灯/证据**：

- `tests/test_candidate_reverification_provider.py`；
- 复用 `tests/test_g4_provider_safety_cli.py` 中 Broker/Usage/Grant 安全夹具，不复制 Secret；
- 相关 Provider 安全集合与现有 G4 不确定结果规则全绿。

**主要文件**：

- `src/candidate_verification/service.py`
- `src/candidate_verification/provider.py`（只有隔离 Broker 语义确有深度时创建）
- `src/model_connections/` 中现有 Broker Adapter 的最小扩展
- `src/api/semantic_workspace_runtime.py`
- 对应测试

**授权门**：自动测试只用 Fake/纯合成数据；真实 Provider 留给 CV-10。

### CV-07 建立精确 Attempt 绑定的显式正式发布动作

**Blocked by**：CV-05。可与 CV-06 并行。

**目标**：完整重验 passed 后不自动发布；TaskOwner 通过第二个明确动作把精确 Attempt 交给
现有 DeliveryPublisher，形成幂等、可恢复的正式 Delivery。

**范围**：

- 新增
  `POST /api/semantic-workspace/tasks/{task_id}/candidate-verifications/{attempt_id}/publish`；
- 使用 `Idempotency-Key`、expected revision 和服务端当前状态 CAS；
- PiCandidateAdapter/PublishCommand 显式接收 attempt_id、report_hash、candidate_set_hash；
- 拒绝 failed/inconclusive/outcome_unknown、非有效 Ruleset、旧 CandidateSet、已有冲突
  Delivery、非 Owner、P0 blocked、取消或文件漂移；
- 复用现有 publication_key、独立 QA、staging、commit point 和恢复对账；
- 提交点前失败/取消零正式输出；提交点后返回既有不可变 Delivery。

**TDD 红灯**：

- 重验 passed 后 Delivery 数仍为 0；显式 publish 后才为 1；
- 精确 Attempt/report/candidate 身份任一漂移被拒；latest 指针竞态不能替换请求身份；
- 同幂等键返回同一 Delivery；不同请求冲突；
- P0、QA 失败、取消、崩溃恢复和 Owner 隔离矩阵；
- 既有初始自动发布和 legacy Delivery 零回归。

**绿灯/证据**：

- `tests/test_candidate_reverification_publish.py`；
- `tests/test_vnext_delivery_publisher.py` 和 Workspace API 相关集合全绿；
- 发布前后旧 VerificationAttempt 与 Candidate 字节零改写。

**主要文件**：

- `src/delivery_publishing/pi_adapter.py`
- `src/delivery_publishing/models.py`（仅精确 Attempt 身份确需新增字段时）
- `src/candidate_verification/service.py`
- `src/api/routes/semantic_workspace.py`
- `src/api/semantic_workspace_runtime.py`
- 对应测试

### CV-08 完成普通用户重验与发布工作台流程

**Blocked by**：CV-06、CV-07。

**目标**：普通用户在现有 Candidate 卡片中看懂资格、确认一次重验外发、观察持久进度、检查
验证结论，再独立发布正式结果；不暴露内部路径、Secret 或技术堆栈。

**实施技能门**：本工单涉及前端，实施时必须联合使用 `frontend-design` 与
`frontend-design-premium`；复用现有工作台视觉身份和 Canonical UI，不新增设计系统或依赖。

**范围**：

- 扩展 Workspace Type/API：ReverificationOffer、Attempt summary、202 receipt、显式 publish；
- CandidatePreview 状态：不可重验原因、可重验、running、failed、inconclusive、
  outcome_unknown、passed-awaiting-publish、published；
- Radix AlertDialog 显示：不重跑、文件数量/格式、规则变化、连接/模型、外发类别、费用可能
  未知、通过后仍需发布；默认焦点“取消”；
- 本地模型显示“本次不外发”；Provider 每个 Attempt 单独确认；
- 重验和发布使用不同幂等键与不同按钮；busy 尺寸稳定、阻止重复提交；
- 任务时间线/SSE 是长期状态权威；Toast 只做短确认；
- outcome_unknown 显示持久警告和先核对状态，不显示普通重试；
- 403/409/422/503 分别给可执行恢复提示。

**TDD/浏览器红灯**：

- 普通 Owner 可见且可操作；其他 Owner/管理员代发无入口且服务端仍拒绝；
- failed+ruleset changed Offer → 确认 → running → passed-awaiting-publish → 显式 publish；
- 本地模型无外发确认；Provider 缺确认不能开始；
- 刷新/关闭对话框后进度恢复；重复点击只产生一个 Attempt/Delivery；
- outcome_unknown、P0 blocked、Candidate 漂移、会话过期和网络失败；
- 键盘、焦点恢复、Escape、可见 focus、aria-live、窄屏、200% 缩放、深浅主题、reduced motion、
  axe 基线。

**绿灯/证据**：

- `frontend/e2e/semantic-workspace.spec.ts` 新增真实用户行为断言；
- TypeScript 检查、Vite production build、定向 Chromium E2E；
- frontend-design-premium 静态审计只处理本次阻断项，不借机重构页面；
- 真实浏览器截图/键盘证据留本地，不把登录态或业务正文入库。

**主要文件**：

- `frontend/src/types/semanticWorkspace.ts`
- `frontend/src/lib/semanticWorkspaceApi.ts`
- `frontend/src/components/workspace/ResultPreview.tsx`
- `frontend/src/pages/SemanticWorkspacePage.tsx`
- `frontend/e2e/semantic-workspace.spec.ts`

**完成记录（2026-08-24）**：达到 `ENGINEERING_VERIFIED`。服务端 Offer/Attempt 投影、逐
Attempt Provider 确认、本地不外发、未知结果停止重试、独立发布、稳定幂等键和可恢复错误均已
接入现有 Candidate 卡片；production build、3 条定向 E2E、64 条完整前端 E2E 和 80 条相邻
后端回归通过，双轴复核无剩余/新增 P1/P2。详见
`docs/plans/2026-08-24-cv-08-user-reverification-workbench-report.md`。该状态不代表生产迁移、真实
Provider 外发、真实 Candidate 重验、正式 Delivery 发布或用户验收。

### CV-09 完成工程门、双轴审查与实施交接

**Blocked by**：CV-08，且 CV-01～CV-08 全部聚焦门通过。

**目标**：证明正式能力在迁移、并发、权限、外发、发布和 UI 上满足批准规格，并把测试绿色
与生产/用户资格明确分开。

**验证集合**：

1. CandidateVerification 新增测试全集；
2. CandidateVerifier、Pi Runtime Workspace API、DeliveryPublisher、RuntimeRouting、
   Provider safety 相邻回归；
3. 显式迁移在空库、当前生产库只读副本、重复执行、中断恢复和恢复副本启动上的完整演练；
4. 前端 TypeScript、production build、定向 Playwright、axe、窄屏/键盘；
5. 与实际差异相称的后端完整回归，记录 exit code、skip/deselect 和基线失败；
6. `git diff --check`、严格 UTF-8、文件允许列表、Secret/绝对路径/业务正文扫描；
7. Standards + Spec 双轴 code-review，修复阻断问题后再次复核；
8. 更新实施报告、`docs/status/current.md` 和 `handoff.md`；不把工程通过写成 LIVE_ACCEPTED。

**完成门**：

- 新老验证路径、Legacy、既有 Delivery、P0 回滚和 Owner 隔离零回归；
- 无活动 Grant、Attempt Lease、临时文件、测试端口或子进程残留；
- 双轴审查无阻断问题；
- 状态为 ENGINEERING_VERIFIED，尚未迁移生产库、调用真实 Provider 或发布真实 Delivery。

**主要文件**：测试、实施报告和权威状态文档；除审查修复外不新增业务范围。

**完成记录（2026-08-25）**：达到 `ENGINEERING_VERIFIED`。聚焦回归 196 passed，前端完整
Playwright 64 passed，最终后端全仓在只排除 4 个已于固定基线干净复现的失败后为
`1999 passed, 7 skipped, 4 deselected`；显式迁移覆盖空库、生产只读副本、旧 `0001`→新
`0002`、重复执行和恢复副本，双轴终审无剩余 P1/P2，活动 Grant/Lease/测试端口/进程/临时目录
为 0。详见 `docs/plans/2026-08-25-cv-09-engineering-gate-report.md`。

生产只读核验同时披露一项历史偏差：CandidateVerification Schema 尚未迁移，但 CV-07 发布
幂等空字段/索引已被旧 Repository 静默写入，非 NULL 记录为 0；当前代码已改为显式 `0002`，
CV-10 必须以当前一致性恢复点接管，不能把该偏差隐去或自动回滚。该状态不代表真实 Provider
外发、真实 Candidate 重验、正式 Delivery 发布或用户验收。

### CV-10 生产迁移、真实同 Run 重验与 Owner 验收

**Blocked by**：CV-09。

**标签**：`ready-for-human`。该工单不能交给 Agent 在无人确认下连续执行。

**目标**：在分离的人工授权门下，对已冻结的受影响普通用户 Candidate 完成生产迁移、同 Run
重验和正式发布验收，证明旧失败保留且没有重跑 Pi 或创建 revision。

**人工门 A——生产迁移**：

- 现场核对 HEAD、服务进程、Gate、数据库身份、活动任务/Attempt 和工作树；
- 展示唯一备份路径、预计 DDL、旧表逻辑指纹和恢复命令；
- 获得生产数据库写入授权后，受控停写/备份/完整性/迁移/重放/恢复副本验证；
- 未授权不得运行迁移；失败按恢复点回退，不清理恢复点。

**人工门 B——真实重验外发**：

- Owner 登录，preflight 展示 Candidate/Manifest、旧/新 Ruleset 证据、连接、模型、外发类别、
  潜在费用和未知结果处理；
- 获得该 VerificationAttempt 的明确授权后才发送；
- 核对旧 failed Attempt 保留、新 Attempt 状态、Provider Usage、Grant 撤销、Candidate 文件树、
  Run/revision 不变、Pi 未重跑；
- outcome_unknown 时停止，不自动重试，由 Owner 决定是否承担重复费用。

**人工门 C——正式发布**：

- Owner 先检查 passed VerificationReport；
- 另行确认“发布正式结果”后才调用 Publisher；
- 核对 publication key、QA、Delivery/output、预览/下载、数据库完整性和历史零改写；
- Owner 明确给出 LIVE_ACCEPTED 或整改意见。

**明确不授权**：Git commit/push、PR、标签、Release、部署、批量历史重验、其他用户任务。

## 4. 规格覆盖矩阵

| 规格能力 | 负责工单 | 完成证据 |
|---|---|---|
| 正式 Module 与 Ruleset 身份 | CV-01、CV-03 | ADR、Interface 测试、两条既有入口统一 |
| 追加式历史与显式迁移 | CV-02 | 迁移/恢复/不可变 Repository 测试 |
| Owner/P0/漂移资格门 | CV-04 | Offer/API/只读逻辑指纹测试 |
| 同 Run 完整重验且不重跑 Pi | CV-05 | 完整 Verifier、调用次数 0、文件哈希不变 |
| 每 Attempt 外发确认与未知结果 | CV-06 | Provider Attempt/Usage/Grant/恢复测试 |
| 验证与正式发布分离 | CV-07 | 发布前 Delivery=0、显式发布后唯一 Delivery |
| 普通用户产品流程 | CV-08 | Playwright、可访问性、视觉/状态证据 |
| 工程资格 | CV-09 | 相称回归、双轴审查、零残留 |
| 生产与用户验收 | CV-10 | 备份迁移、LIVE_REVERIFIED、LIVE_ACCEPTED |

## 5. 全局文件与范围边界

### 预计允许触及

- `src/candidate_verification/`（新正式 Module）
- `src/agentic_runtime/candidate_verifier.py`
- `src/agentic_runtime/repository.py`
- `src/api/semantic_workspace_runtime.py`
- `src/api/routes/semantic_workspace.py`
- `src/delivery_publishing/pi_adapter.py`
- `src/delivery_publishing/models.py`（仅精确 Attempt 身份确需）
- `src/model_connections/`（仅 CV-06 所需 Adapter 最小扩展）
- 前端五个明确文件与相关测试
- `docs/adr/0033-*`、本规格/拆分/实施报告、`CONTEXT.md`、`docs/status/current.md`、`handoff.md`

### 禁止顺带触及

- G1 冻结评测文件；
- Legacy 执行器或历史 Delivery 数据；
- Capability 受众、远程 MCP、多媒体、来源 Adapter；
- Secret 存储、通用迁移平台、CI、依赖告警；
- `start_all.bat`、`stop_all.bat` 等本机私有编排；
- GitHub、远端、版本、标签、Release 或部署。

### 依赖判断

不新增 Python/npm 依赖。现有 SQLite、Pydantic、FastAPI、Broker/Grant/Usage、
DeliveryPublisher、Radix AlertDialog、TanStack Query/SSE/Sonner 已覆盖基础能力。若实施中发现
必须新增第三方依赖，应停止对应工单，展示版本、许可证、供应链、数据外发和锁文件差异，
等待用户确认。

## 6. 执行与授权纪律

- 每张工程工单开始前先声明文件允许列表和红灯命令；先看到精确失败，再做最小实现。
- 每张工单只证明自己的可观察纵切面；不为后续工单预埋未使用抽象。
- 工单完成后展示产物、测试 exit code、未决风险和下一张依赖门，等待用户确认。
- 本拆分最初批准只表示 `TICKETS_READY`；后续持续目标已明确授权创建 P0 Issues，2026-08-24
  已发布为父任务 #54 与 CV 子任务 #61～#70。该授权不自动包含分支、提交、推送或生产操作。
- 创建 GitHub Issues、创建分支、本地提交、推送、PR、生产迁移、Provider 外发、正式发布分别
  需要明确授权。
- 工作树中的既有 G1、handoff、roadmap、`.scratch` 和前端审计文件视为用户持有内容；使用
  精确文件允许列表，禁止 `git add .`、reset、clean 或未经确认覆盖。

## 7. 任务拆分完成定义

- `CV-01`～`CV-10` 的目标、依赖、Scope、非 Scope、主要文件和验证门均明确；
- 六项已确认业务决定都有唯一负责工单和验收证据；
- 生产迁移、真实外发和正式发布被隔离在 `ready-for-human` 工单；
- 没有把测试绿色冒充生产迁移、真实重验、用户验收或发布；
- 用户已确认本任务集合并发布为 GitHub Issues；CV-01 已完成，CV-02 达到
  `ENGINEERING_VERIFIED`，CV-03/CV-04 已获用户接受，CV-05～CV-09 达到
  `ENGINEERING_VERIFIED`；真实连接、外发范围、费用、生产迁移和用户验收仍须
  在 CV-10 单独确认。
