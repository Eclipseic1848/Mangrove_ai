# P0-01：同 Run 候选完整重验规格

> 状态：approved，用户于 2026-08-24 逐项确认全部六项业务决定
>
> 规格日期：2026-08-24
>
> 当前证据级别：SPEC_APPROVED
>
> 上游规格：`2026-08-23-p0-01-vnext-default-user-delivery-spec.md`
>
> 上游决策：ADR-0017、ADR-0018、ADR-0019、ADR-0030
>
> 本规格决策记录：ADR-0033
>
> 本阶段只编写规格；不修改实现、数据库、现有候选、Git 或远端状态
>
> 内部复核：2026-08-24；UTF-8、尾随空白、现有幂等请求头、P0-01/ADR/权限/Provider/
> Publisher 契约已复核，六项高风险业务决定均已确认

## 1. Problem Statement

P0-01 的真实普通用户闭环暴露出一类不能由现有“语义重试”正确处理的问题：Candidate 文件、
Manifest 和冻结多格式交付要求均正确，但旧版确定性 Verifier 规则错误地拒绝了候选。规则修复
后，现有产品仍不能在同一个 TaskRevision 和 Run 上执行完整重验，因为当前重验入口只接受
`inconclusive`，且只重跑语义裁判。

现有实现把最新 `VerificationReport` 覆盖保存到 Runtime 单行的 `verification_json`。这种模型
适合对一次瞬时语义失败做兼容重试，但不能回答以下审计问题：

- 哪个规则版本形成了旧结论；
- 为什么允许再次验证；
- 重验是否针对完全相同的 CandidateSet、Manifest、GoalContract 和 DeliverySpec；
- 每次验证是否调用了 Provider、结果是否确定、产生了什么用量；
- 新结论是否替换、撤销或静默抹去了旧失败；
- 验证通过后是谁另行批准了正式 Delivery 发布。

如果直接放宽一个 `if`、覆盖旧 JSON 或编写一次性数据库脚本，当前任务可能被修复，但同类
Verifier 规则回归仍没有正式产品语义。该路线不符合本规格。

## 2. 目标

建立正式、可复用、可审计的同 Run 候选完整重验能力：

1. TaskRevision、Run、CandidateSet 和 GoalContract 不变时，可以使用新的冻结 Verifier
   规则重新执行完整独立验证；
2. 每次验证形成不可变 `VerificationAttempt`，旧报告永久保留；
3. 不重新运行 Pi，不重新生成 Candidate，不创建伪 revision；
4. 重验前重新打开并校验 Candidate、Manifest、来源和冻结输出契约；
5. 需要模型语义裁判时，先展示冻结连接、模型、外发范围和潜在费用，再由 Owner 明确确认；
6. 结果未知时不自动重试；重复动作受幂等键和活动 Lease 约束；
7. 重验通过只形成新的验证结论，不自动发布正式 Delivery；
8. 正式发布继续复用现有 Publisher，并由独立用户动作触发；
9. Owner 隔离、P0 门、取消、审计和历史不可变性失败关闭。

## 3. 领域术语草案

以下术语已在本规格确认后同步到 `CONTEXT.md`。

### 候选重验（Candidate Reverification）

针对同一 Owner、TaskRevision、Run 和不可变 CandidateSet，使用一个新的冻结 Verifier 规则集
重新执行完整独立验证。它可以重新打开原件和 Candidate，但不得重新执行 Agent、改变目标、
生成文件或创建 revision。

_Avoid_：重新运行任务、修复候选、覆盖验证报告、发布 Delivery

### 验证尝试（VerificationAttempt）

一次不可变验证事实，绑定 CandidateSet、Manifest、GoalContract、DeliverySpec、Verifier
规则集、触发原因、Actor、外发确认、Provider Attempt 和结构化 VerificationReport。

_Avoid_：Runtime 当前状态、可变验证记录、测试日志

### Verifier 规则集（VerifierRuleset）

决定确定性文件、数量、结构、来源、禁止项和语义门行为的冻结身份。身份至少包含规则版本、
允许列表/符号闭包版本和规范化规则摘要；依赖或 Prompt 会改变结论时也必须进入摘要。实际
代码提交属于独立 ExecutionIdentity，不得因无关 commit 变化制造 `ruleset_changed`。

_Avoid_：仅 Git 分支名、当前进程、人工口头声明

### 有效验证结论（Effective Verification）

针对精确 CandidateSet 和当前 VerifierRuleset 可用于后续决策的确定性
VerificationAttempt。任务投影可以显示其中一个明确身份的当前有效结论，但 Publisher 必须
绑定具体 Attempt 和报告哈希，不能把“最新”指针本身当成权威后静默漂移。

_Avoid_：覆盖旧报告、验证通过即发布

## 4. 已验证事实

- `TaskRevision` 只在来源范围、数据含义、权限或交付要求发生实质变化时创建；单纯修复平台
  Verifier 规则不构成新 revision。
- `Run` 允许在不改写 GoalContract 的前提下恢复和重新规划；本次候选及 Run 身份仍存在。
- 当前 `retry_semantic_verification` 只接受 `VerificationStatus.INCONCLUSIVE`，并要求文件
  集合、数量和来源证据门此前已经通过。
- 当前真实失败是确定性 `artifact_count` 失败，不满足上述准入条件。
- 当前 Candidate 文件、大小、SHA-256 和 Manifest 仍完整。
- 当前 Runtime Repository 用单列 `verification_json` 保存报告；更新会覆盖该列。
- Publisher 已把 `verification_report_hash` 纳入 `publication_key`，能够绑定精确报告身份。
- 当前候选语义重验通过后会直接调用 Publisher；尚没有“重验通过后等待用户发布”的独立动作。
- 现有工作台已有 Candidate 卡片、Radix AlertDialog、共享 Toast 和任务进度投影，可在不新增
  前端依赖的情况下承载该流程。

## 5. 方案比较

### 方案 A：放宽现有语义重试条件

把 `inconclusive` 放宽为 `failed | inconclusive`，然后继续覆盖 `verification_json`。

拒绝原因：

- 文件、来源和结构门没有完整重跑；
- 旧失败被覆盖，无法审计规则变化；
- 任意内容失败都可能被无意义重复执行；
- Provider 外发、未知结果和正式发布仍耦合在一个请求中；
- 复杂度泄漏到路由和条件分支，属于补丁式修复。

### 方案 B：创建新 revision 并重跑 Pi

拒绝原因：

- 用户没有改变目标或数据含义，不符合 TaskRevision 语义；
- 重新读取、规划、生成和多次模型调用会增加时间与费用；
- 新 Candidate 不能证明旧 Candidate 在修正规则下是否正确；
- 会把平台缺陷错误记录成用户业务变更。

### 方案 C：一次性维护脚本或人工改库

拒绝原因：

- 没有稳定 Interface、权限、幂等、并发和恢复契约；
- 容易绕过 Provider Attempt、Publisher 和审计事件；
- 不能服务后续同类规则回归。

### 方案 D：追加式 CandidateVerification Module

选择该方案。Module 以小 Interface 隐藏资格判定、快照重开、规则冻结、Attempt 持久化、
Provider 外发、并发、取消和报告投影。初始验证、瞬时语义重试和完整重验最终都通过同一
Module 形成 VerificationAttempt；现有路由只作为兼容 Adapter。

## 6. Module 与 Seam

### 6.1 CandidateVerification Module

外部 Interface 只暴露两个业务动作：

```text
inspect_reverification(owner, task, revision)
    -> ReverificationOffer

request_reverification(ReverificationCommand)
    -> VerificationAttemptReceipt
```

`inspect_reverification` 是只读查询，返回是否可重验、阻断原因、将要重开的对象、Verifier
规则变化、是否需要 Provider、冻结连接/模型和外发摘要。它不签发 Grant、不创建 Attempt。

`request_reverification` 接收 Owner 明确确认后的命令，负责资格 CAS、幂等、Attempt/Lease、
完整验证和确定状态持久化。调用者不直接操作 Repository、CandidateVerifier、Broker 或
Publisher。`reason_code` 只能由 Module 根据旧报告状态和新旧 VerifierRuleset 身份推导，
客户端不能声明“规则已变化”来绕过资格门。

### 6.2 ReverificationCommand

命令至少冻结：

- owner_id、task_id、revision、run_id；
- previous_attempt_id；
- candidate_set_hash、manifest_hash；
- goal_contract_hash、delivery_spec_hash；
- target_verifier_ruleset_hash；
- 由服务端规则比较得到的 reason_code；
- actor_id；
- idempotency_key；
- expected_task_status 与 expected_latest_attempt_id；
- 是否需要 Provider；若需要，冻结 connection_id、connection_version、model 和本次外发确认。

客户端不得提供文件路径、Secret、验证通过结论、Publisher 命令或规则摘要正文。

### 6.3 兼容 Adapter

- 现有 `POST .../candidate-verification/retry` 保留兼容期，但内部只映射到 Module 的
  `semantic_inconclusive` 原因；不得继续拥有独立状态逻辑。
- 新正式资源采用 `POST .../candidate-verifications`，不以“修复”“强制通过”或管理员后门
  命名。
- 任务详情投影返回 `reverification_offer`、最新 Attempt 摘要和是否等待发布。
- Publisher Adapter 从精确 passed Attempt 构造 PublishCommand，不读取可漂移的“最新报告”。

## 7. 不可变数据模型

### 7.1 candidate_verification_attempts

建议使用显式编号迁移新增追加式表，核心字段如下：

| 字段 | 含义 |
|---|---|
| `attempt_id` | 不透明稳定身份 |
| `owner_id/task_id/revision/run_id` | 所属冻结执行身份 |
| `previous_attempt_id` | 前一验证尝试；初次验证为空 |
| `reason_code` | `initial`、`semantic_inconclusive`、`ruleset_changed` |
| `actor_id` | 发起者；系统初验与 Owner 重验可区分 |
| `candidate_set_hash` | 规范化 CandidateSet 身份 |
| `manifest_hash` | 实际 Manifest 字节哈希 |
| `goal_contract_hash` | 冻结目标身份 |
| `delivery_spec_hash` | 冻结交付规格身份 |
| `verifier_ruleset_hash` | 规范化规则语义身份；不含无关代码提交 |
| `verifier_code_commit` | 实际执行代码提交身份 |
| `verifier_source_hash` | 会影响结论的 Verifier 源码、Prompt、配置和依赖摘要 |
| `verifier_execution_identity_hash` | 提交、规则集与 Python 执行环境的审计身份 |
| `status` | Attempt 状态 |
| `report_json/report_hash` | 确定终态报告及哈希 |
| `connection_id/version/model` | 仅保存引用，不保存 Secret |
| `egress_confirmed_at` | 本次重复外发确认时间 |
| `provider_attempt_id` | 与 Broker/Usage 的稳定关联 |
| `idempotency_key/request_hash` | 重复请求身份 |
| `created_at/started_at/finished_at` | 审计时间 |

### 7.2 Attempt 状态

```text
requested -> running -> passed
                     -> failed
                     -> inconclusive
                     -> outcome_unknown
                     -> cancelled
```

- `failed`：系统确定验证未通过；可以显示具体失败门。
- `inconclusive`：系统确定没有形成语义结论，且已知没有成功结论可复用。
- `outcome_unknown`：请求可能已到 Provider，但无法确认结果；不得自动再发。
- `cancelled`：仅在尚未进入不可取消的 Provider/提交点时成立；不能把未知结果标成取消成功。
- 所有终态不可修改；后续动作创建新 Attempt。

### 7.3 兼容投影与迁移

- 显式迁移把现有非空 `verification_json` 按原字节语义导入为一个不可变 legacy Attempt，
  保留原状态、报告哈希和 Runtime 时间。
- 历史规则候选可沿该 TaskRevision 自身冻结的
  `RuntimeAssignment.gate_snapshot_id → GateSnapshot.code_commit` 证据链，从不可变提交对象
  读取 Verifier 相关源码并计算摘要；但现有 Runtime 没有强制执行进程与 Gate commit 一致，
  因此还必须存在执行前或执行时写入、不可变且可校验的 Run 级执行身份凭据，并与该链一致。
  Assignment、GateSnapshot、commit 对象、相关源码、执行凭据或一致性检查任一缺失时，规则
  身份保持 `legacy_unversioned`，不得用当前工作树、重启说明或人工口头声明补猜。
- `legacy_unversioned` 不能自动满足 `ruleset_changed`；若未来需要补充外部证据绑定，必须另立
  规格与授权。本任务不增加管理员手填旧 ruleset 的入口。
- 现有缺少 Run 级执行身份的确定性 failed Candidate 因此不能凭本任务自动重验；该失败关闭
  后果由用户于 2026-08-24 明确确认。
- 原 `verification_json` 在兼容期只作为最新有效报告投影，由 Module 在同一事务更新；它不再
  是审计权威。
- `verified_candidate_set_hash` 继续保护 Publisher，但必须与具体 Attempt 一致。
- 迁移不得改写 Candidate、TaskRevision、Run、Delivery、Usage 或旧事件。
- 应用启动只校验 Schema，不得为本能力静默创建或回填生产表。

该数据结构变更与原 P0-01 规格“不新增数据库表或迁移”的边界冲突。只有用户确认本子规格后，
该条边界才对 CandidateVerification 子能力作有限例外；不扩大到 P0-05 的通用迁移重构。

## 8. 重验资格门

只有以下条件全部满足，`ReverificationOffer.eligible` 才能为 true：

1. Actor 是 TaskOwner，且服务端再次校验 Owner；管理员角色不自动取得正文或连接使用权；
2. TaskRevision、Run 和 RuntimeAssignment 仍存在且相互一致；
3. Runtime 是 Pi，任务仍持有 Candidate，且没有正式 Delivery；
4. CandidateSet、每个文件大小/SHA-256、Manifest 字节和 Manifest 内身份全部一致；
5. GoalContract、DeliverySpec、来源快照引用和外发范围未改变；
6. 没有同一 CandidateSet 的活动 Attempt 或未解决 `outcome_unknown`；
7. 目标 VerifierRuleset 与旧规则身份不同，且原因是 `ruleset_changed`；或者旧结论为
   `inconclusive`，原因是 `semantic_inconclusive`；
8. 当前规则集绑定可审计的代码提交和 Verifier 相关源码摘要；相关文件存在未提交变化时失败
   关闭，无关用户文件的工作树变化不得阻断或进入规则身份；
9. 当前 P0/Gate 状态允许验证；若 P0 阻断，默认失败关闭，不借重验绕过生产硬门；
10. 需要 Provider 时，冻结连接版本和模型仍可用、属于 Owner 或获准平台共享范围，并取得
    本次重复请求确认；
11. Candidate 已通过正式重验所需的安全与完整性预检；不安全 Candidate 不开放下载或重验。

以下情况必须创建新 revision，而不是重验：来源范围、数据含义、字段/数量/格式、权限、
Provider 外发类别、DeliverySpec 或用户目标发生实质变化。

## 9. 完整验证协议

同 Run 完整重验必须按顺序执行：

1. 重开冻结 TaskRevision、GoalContract、DeliverySpec 和 RuntimeAssignment；
2. 重算 CandidateSet 和 Manifest 哈希；
3. 重新检查 Candidate 集合、格式、数量、命名、禁止项和表格输出契约；
4. 重新打开来源快照和 Candidate，执行来源证据与所有权检查；
5. 执行当前冻结 VerifierRuleset 的确定性门；
6. 只有确定性门全部通过时才进入语义裁判；
7. 语义裁判仅接收任务获准的有界内容；不扩大来源或连接；
8. 写入新的不可变 VerificationReport 和 report_hash；
9. 更新兼容投影和任务可见摘要，但保留旧 Attempt；
10. 若通过，把任务保持为 `candidate_ready` 并投影“验证已通过，等待发布”；不得在此命令内
    调用 Publisher。

重验不得调用 `PiRuntime.start/resume`、OCR/来源发现、候选生成、修复循环、公共依赖获取或
能力获取。需要这些动作说明 Candidate 已不适合原地重验，应进入 Run 恢复或新 revision 的
独立流程。

## 10. Provider、费用与未知结果

- 只重用 TaskRevision 已冻结的连接、connection_version 和 model；不得静默换 Provider。
- UI 在确认前显示连接名称、模型、“将发送候选与已冻结来源证据的有界内容”、不会重新运行
  整个任务，以及 Provider 可能再次计费。
- 不显示 Key、Base URL、宿主路径、完整 Prompt 或原始工具日志。
- 用户确认只授权这一个 VerificationAttempt，不成为账号级或后续 Attempt 的永久授权。
- Provider Attempt 必须先持久化，再发送请求；同一 Attempt 不得并发发送。
- 已发送但响应丢失、进程退出或无法确认结果时进入 `outcome_unknown`；不得自动重试。
- 只有 Owner 明确承担重复请求和费用风险后，才能创建引用上一 Attempt 的恢复 Attempt。
- Usage 继续由 Broker 记录 Provider 原生字段；Provider 未返回费用时显示“费用未知”，不得
  推断为零。

## 11. 验证与发布分离

重验通过后只产生 passed VerificationAttempt。正式发布是第二个明确动作：

```text
Candidate + passed VerificationAttempt
        -> 用户选择“发布正式结果”
        -> PublishCommand（绑定精确 Attempt/report hash）
        -> 独立 QA / staging / commit point
        -> Delivery
```

发布继续满足：

- TaskOwner 权限；
- CandidateSet 和 report_hash 仍一致；
- 当前 Attempt 是该 CandidateSet 的有效 passed 结论；
- 没有 P0 阻断、取消或已有冲突 Delivery；
- publication_key 确定性幂等；
- 提交点前取消零正式输出，提交点后不能假装撤销不可变 Delivery。

已有初始执行“验证通过后自动发布”的路径是否未来统一为显式发布，不在本子规格范围。本规格
只要求重验路径不得自动发布，避免一次可能产生费用的恢复操作同时跨过两个业务门。

## 12. 权限与审计

- 普通用户只能查看和重验自己的任务。
- 管理员可以查看脱敏任务管理元数据，但不得代替 Owner 使用其连接或读取正文发起重验。
- 管理员若需诊断正文，继续走有原因和不可变事件的 AuditView；AuditView 本身不授予重验权。
- 超级管理员不拥有默认绕过；任何未来平台级批量重验必须另立规格和用户授权。
- Attempt 事件至少包含 actor、task/revision/run、reason、旧/新 ruleset、状态、外发是否发生、
  Provider Attempt 引用和 report_hash；不记录 Secret 或业务正文。
- 旧失败是历史事实，不得改名为“误报后删除”；新 Attempt 以引用关系表达纠正。

## 13. 产品交互规格

### 13.1 页面位置与视觉原则

- 复用现有 Candidate 卡片，不新建页面、不改变工作台视觉身份、不新增 `DESIGN.md` 或新 Token。
- 候选状态标题继续说明“不是正式交付”；旧失败和新 Attempt 在同一状态区域呈现。
- 使用现有颜色、圆角、字体、间距、Lucide 图标、Radix AlertDialog 和 Sonner Toast。
- 视觉重点只有一个：清晰区分“重新验证”与“发布正式结果”，不能把两个按钮做成等价主操作。

### 13.2 Offer 状态

- 可重验：显示“使用最新规则重新验证”，并以次级按钮呈现。
- 不可重验且原因可修正：按钮 disabled，并在可见说明中写明原因；不只依赖 tooltip。
- 目标或输出要求已改变：不显示重验按钮，提供“创建新版本”的既有路径。
- `outcome_unknown`：显示持久警告和“先核对状态”；不得显示普通重试按钮。
- 已通过待发布：显示 passed Attempt 摘要和独立的“发布正式结果”主操作。
- 已发布：继续进入现有正式 Delivery 预览，不再显示重验或重复发布。

### 13.3 重验确认对话框

使用应用自有 AlertDialog，标题为“重新验证现有候选？”，内容必须列出：

- 不会重新执行整个任务或生成新文件；
- 将重新检查的 Candidate 文件数量与格式；
- 旧 Verifier 规则与新规则的可理解变化原因；
- 是否调用模型；若调用，显示连接名称、模型、外发内容类别和费用未知/可能计费；
- 重验通过后仍需单独发布；
- 取消与结果未知的处理。

按钮使用“取消”和“开始重新验证”，默认焦点位于“取消”。运行中按钮尺寸保持稳定、阻止重复
点击，显示不可伪造百分比的阶段状态。对话框关闭后，长期进度仍在任务时间线中可恢复查看。

### 13.4 反馈与可访问性

- 任务时间线记录 requested/running/passed/failed/inconclusive/outcome_unknown；Toast 只做短暂
  确认，不承载唯一错误或恢复动作。
- 使用 native button、可见焦点、文本状态和 `aria-live`；不只用颜色表达状态。
- 403、409、422、503 和 outcome_unknown 分别显示不同恢复建议。
- 窄屏时确认内容自然滚动，操作区保持可达；200% 缩放不得遮挡主操作。
- 深浅主题、键盘、Escape、焦点恢复和 reduced-motion 遵循现有工作台契约。

## 14. API 与错误契约

建议资源：

```text
POST /api/semantic-workspace/tasks/{task_id}/candidate-verifications
```

请求只包含：

```json
{
  "expected_revision": 1,
  "expected_previous_attempt_id": "verification_attempt_xxx",
  "external_api_confirmed": true
}
```

幂等身份沿用现有 `Idempotency-Key` 请求头。服务端从数据库解析其余冻结身份，并根据旧报告
状态和新旧 Ruleset 比较推导原因。接口以 HTTP 202 返回 Attempt receipt 和最新任务投影；
长操作按现有任务事件/SSE 展示，不要求 HTTP 连接保持到完成。

错误语义：

| HTTP | 含义 |
|---|---|
| 403 | 非 Owner 或无权使用冻结连接 |
| 404 | 任务、Run、Candidate 或 Attempt 不存在/不可见 |
| 409 | 状态漂移、活动 Attempt、已有 Delivery、P0 阻断、规则未变化或 outcome_unknown 未解决 |
| 422 | 请求身份、原因、确认或冻结契约不完整 |
| 503 | 规则身份、Repository、Broker 或验证执行环境不可用 |

同一幂等键与相同 request_hash 返回同一 Attempt；相同键配不同请求返回冲突。并发请求只允许
一个活动 Attempt，不能产生双 Provider 请求。

## 15. 实施纵切片

本规格确认后再进入任务拆分。建议纵切片顺序：

1. **VerificationAttempt 领域与显式迁移**：追加式 Repository、legacy 报告导入、不可变性和
   备份/重放/恢复测试；不接生产库。
2. **CandidateVerification Module**：初始验证与现有 semantic retry 通过同一 Module 记录
   Attempt，兼容投影不回归。
3. **同 Run 完整重验**：规则变化资格门、完整文件/来源/契约重验、无 Pi 重跑证明。
4. **Provider 安全与恢复**：Attempt 先落库、外发确认、Usage 绑定、并发与 outcome_unknown。
5. **验证/发布分离**：重验通过停在待发布，精确 Attempt 绑定 Publisher，发布幂等。
6. **工作台纵切面**：Offer、确认对话框、时间线、失败恢复和显式发布。
7. **生产候选演练**：另行授权后迁移生产库，并对一条受影响候选执行重验；正式发布再单独确认。

每个纵切片完成 TDD 红绿证据和 Standards/Spec 双轴审查后才能进入下一个；工单、分支、提交、
推送和生产迁移仍是独立授权门。

## 16. 测试与验证矩阵

### 16.1 领域与 Repository

- legacy `failed/passed/inconclusive` 报告确定性导入，原 JSON 和业务表零改写；
- Attempt 终态不可更新，只能追加后继；
- previous_attempt、candidate_set、ruleset 和 report hash 一致性；
- 同幂等键重放、不同请求冲突、并发单 Attempt、锁超时和中途失败；
- 显式迁移：空库、当前生产副本、重复执行、失败恢复、完整性和外键检查；
- 启动时缺迁移失败关闭，不静默 DDL。

### 16.2 资格门

- `inconclusive + semantic_inconclusive` 可进入语义重验；
- `failed + ruleset_changed` 可进入完整重验；
- 同 ruleset 的确定性 failed 不可无意义重验；
- Candidate、Manifest、来源、GoalContract 或 DeliverySpec 任一漂移均拒绝；
- 已有 Delivery、活动 Attempt、outcome_unknown、P0 blocked、Verifier 相关源码身份漂移均拒绝；
- 目标或权限变化提示创建 revision；
- Owner A 不能查看或触发 Owner B 的 Offer/Attempt。

### 16.3 执行与外发

- 完整重验重新执行 artifact_set、artifact_count、table contract、source_grounding 和 semantic；
- 明确断言 Pi `start/resume`、候选写入和公共依赖获取调用次数为 0；
- 无 Provider 路线不创建 Grant/Usage；
- Provider 路线冻结 connection_version/model，Attempt 先于请求写入；
- 已发送后超时进入 outcome_unknown，零自动重试；
- 用户授权恢复创建后继 Attempt，不覆盖未知 Attempt；
- 取消前、发送中、响应后和持久化崩溃窗口分别失败关闭。

### 16.4 Publisher

- passed Attempt 不自动创建 Delivery；
- 显式发布绑定精确 attempt_id/report_hash/candidate_set_hash；
- 旧 failed Attempt 不能发布；latest 指针竞态不能替换命令身份；
- 重复发布命中同一 publication_key；不同报告不得静默复用旧 Delivery；
- P0、取消、QA 失败和文件漂移保持零正式输出。

### 16.5 API 与浏览器

- Offer 的 eligible 和阻断原因来自服务端，不由前端猜测；
- Owner/403、状态冲突/409、确认缺失/422、依赖不可用/503；
- 确认框完整显示无重跑、文件范围、规则变化、连接/模型/外发/费用和独立发布门；
- busy 防重复、任务时间线持久进度、刷新恢复、outcome_unknown 不显示普通重试；
- passed 后显示“等待发布”，只有显式发布后进入正式预览；
- 键盘、焦点、Escape、窄屏、200% 缩放、深浅主题和 axe 基线；
- 现有 inconclusive 语义重验、初始自动验证、历史任务和 Legacy Delivery 零回归。

### 16.6 真实验收

真实验收是独立授权门，顺序固定：

1. 只读 preflight 展示 Candidate/Manifest 完整性、目标 ruleset 和拟用连接；
2. 用户确认一次重验外发与潜在费用；
3. 对现有同 Run Candidate 创建一个 VerificationAttempt；
4. 证明未重新执行 Pi、未生成文件、未创建 revision；
5. 核对新 Attempt、旧失败保留、Usage、Grant 撤销、数据库完整性和零未知状态；
6. 用户先验收验证结论；
7. 用户另行确认后才发布正式 Delivery；
8. 预览/下载并给出 LIVE_ACCEPTED 或整改意见。

## 17. Out of Scope

- 不增加“强制通过”“管理员改报告”或批量重验后门。
- 不修复、改写或重新生成 Candidate。
- 不因规则变化自动扫描并重验所有历史任务。
- 不自动使用 Owner 的连接或代替 Owner 确认费用。
- 不把管理员 AuditView 变成任务执行权。
- 不删除旧 `verification_json` 兼容字段；淘汰需独立迁移计划。
- 不统一重做所有初始验证与发布交互；只把状态权威收敛到 Module。
- 不重构整个 Semantic Workspace、Worker、Store 或前端页面。
- 不新增第三方依赖；Pydantic、FastAPI、SQLite、现有 Broker、Publisher、Radix 和前端状态库
  已覆盖所需基础能力。
- 不扩大平台能力受众，不新增 Provider、远程 MCP、多媒体或来源类型。
- 不创建 GitHub Issue、分支、提交、推送、PR、标签、Release 或部署。
- 不在本规格授权生产数据库迁移、Provider 外发、真实候选重验或正式 Delivery 发布。

## 18. 开源与依赖判断

该能力的核心是 Mangrove 自有 TaskRevision、Candidate、Verification、Provider Grant 和
Delivery 状态机之间的业务一致性。通用工作流库或状态机库不能替代这些领域不变量，新增依赖
只会增加另一层 Adapter 和供应链面。

因此本规格选择复用成熟的现有基础组件：

- SQLite：事务、唯一约束和追加式持久化；
- Pydantic：冻结命令和报告校验；
- FastAPI：Owner 隔离的 HTTP Adapter；
- 现有 Broker/Grant/Usage：Provider 安全与计量；
- 现有 DeliveryPublisher：正式发布、QA、幂等和提交点；
- Radix AlertDialog：成本/外发确认与焦点管理；
- TanStack Query、SSE 和 Sonner：任务投影、持久进度和短暂反馈。

当前没有发现能“完美适配”上述领域身份和迁移约束的外部开源工具；本规格不新增 npm 或
Python 依赖，也不自制新的通用工作流引擎。

## 19. 完成定义

- **SPEC_DRAFT**：本文件存在，未决业务决定未确认。
- **SPEC_APPROVED**：用户确认范围、权限、迁移和验证/发布分离。
- **TICKETS_READY**：纵切片工单、依赖边和每片验收门已冻结；不等于授权实现。
- **IMPLEMENTED**：Module、显式迁移、API 和 UI 代码存在；不等于生产库已迁移。
- **ENGINEERING_VERIFIED**：迁移恢复、聚焦/相称回归、浏览器和双轴审查通过。
- **LIVE_REVERIFIED**：经独立授权，对一条真实同 Run Candidate 完成重验且旧报告保留。
- **LIVE_ACCEPTED**：Owner 验收验证结论，并另行授权形成、预览和下载正式 Delivery。
- **RELEASED**：经独立授权完成提交、推送、合并、部署或版本发布；本规格不自动授权。

## 20. 用户已确认的决定

1. **范围（已确认，2026-08-24）**：把 CandidateVerification 追加式记录和显式迁移作为
   P0-01 的有限扩展，取代原规格中“本任务不新增数据库表或迁移”的局部边界；应用启动不得
   静默建表或回填，生产迁移仍需独立授权。
2. **权限（已确认，2026-08-24）**：只有 TaskOwner 可以发起真实重验；管理员和超级管理员
   不得代替 Owner 使用其业务内容、模型连接或费用权限。管理员对自己拥有的任务仍以
   TaskOwner 身份操作。
3. **外发（已确认，2026-08-24）**：每个需要 Provider 的 VerificationAttempt 都必须再次
   展示连接、模型、外发类别和潜在费用，并由 Owner 针对该 Attempt 单独确认；确定性门已
   失败且不会调用模型时不要求确认，平台本地模型明确显示“本次不外发”。
4. **发布（已确认，2026-08-24）**：重验通过后停在“验证通过，等待发布”，不得自动创建
   正式 Delivery；由 TaskOwner 检查验证结论后另行触发“发布正式结果”。
5. **规则资格（已确认，2026-08-24）**：确定性 `failed` 只有在 VerifierRuleset 身份确实
   发生变化后才允许完整重验；同规则失败不得反复重跑。瞬时语义服务异常形成的
   `inconclusive` 继续走独立语义重试流程。
6. **P0（已确认，2026-08-24）**：P0/Gate 阻断时禁止启动新的候选重验，而不只是禁止最终
   发布；已运行 Attempt 按取消点和 `outcome_unknown` 规则安全收口。

六项决定已全部确认，本规格达到 `SPEC_APPROVED`，任务拆分达到 `TICKETS_READY`，用户已授权
并完成 CV-01 决策工单。是否进入 CV-02 仍需用户显式授权；上述确认不自动授权业务代码实现、
数据库迁移、Provider 外发、真实候选重验或正式发布。
