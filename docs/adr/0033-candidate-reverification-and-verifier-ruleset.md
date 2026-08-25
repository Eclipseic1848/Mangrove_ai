# ADR-0033：候选重验采用追加式 Attempt 与实际执行规则身份

- 状态：`accepted`，用户于 2026-08-24 确认六项业务决定，并确认历史身份缺证时失败关闭
- 日期：2026-08-24
- 关联：[ADR-0017](0017-agentic-runtime-vnext.md)、
  [ADR-0018](0018-unified-task-domain-contract.md)、
  [ADR-0019](0019-vnext-delivery-and-default-cutover-state-machine.md)、
  [ADR-0030](0030-direct-vnext-default-cutover.md)
- 规格：
  [同 Run 候选完整重验规格](../plans/2026-08-24-p0-01-same-run-candidate-reverification-spec.md)

## 背景

现有 Runtime 把最新 `VerificationReport` 覆盖到单行 `verification_json`，语义重试还会在通过
后直接进入 Publisher。它无法证明旧结论由哪套规则形成，也无法在同一 TaskRevision、Run 和
CandidateSet 上保留多次完整验证事实。把 `failed` 放宽进现有重试、人工改库或创建新 revision
都会混淆平台规则修正、用户业务变更和正式发布。

## 决策

### CandidateVerification 是一个深 Module

Module 的外部 Interface 只提供两个用户业务动作：

```text
inspect_reverification(owner, task, revision) -> ReverificationOffer
request_reverification(ReverificationCommand) -> VerificationAttemptReceipt
```

`inspect_reverification` 只读判断资格和展示将被冻结的 CandidateSet、规则变化、Provider、模型
与外发范围；它不签发 Grant 或创建 Attempt。`request_reverification` 在 Owner 确认后统一负责
资格 CAS、幂等、冻结身份、Attempt 生命周期、完整验证、Provider 未知结果和兼容投影。路由、
Worker 和现有语义重试是 Adapter，不得各自复制资格或状态逻辑，也不得直接操作 Repository、
CandidateVerifier、Broker 或 Publisher。

任务只读投影和按 `attempt_id` 读取不可变事实属于同一 Interface 的查询面，不增加第三个用户
业务动作。正式发布属于 DeliveryPublishing Module 的独立动作；其 Adapter 只能解析并再次
校验一个精确的 passed VerificationAttempt、`report_hash` 和 CandidateSet，不能读取会漂移的
“最新验证”指针。

### VerificationAttempt 追加记录，验证与发布分离

初始验证、`semantic_inconclusive` 重试和 `ruleset_changed` 完整重验最终都形成追加式
VerificationAttempt。Attempt 只允许 `requested → running → 终态` 的受约束前向 CAS；终态
不可修改或删除，纠正只能追加后继 Attempt。完整重验不得重跑 Agent、修改目标、生成 Candidate
或创建 revision；通过后停在“等待发布”，由 TaskOwner 另行执行发布动作。

只有 TaskOwner 可以请求真实重验。管理员和超级管理员不因角色获得 Owner 正文、连接或费用
权限。P0/Gate 阻断时禁止启动新 Attempt；已运行 Attempt 按取消点或 `outcome_unknown` 收口，
不得借重验绕过生产硬门。

### VerifierRuleset Manifest 绑定实际执行身份

每个新 Attempt 在执行前冻结一个规范化 VerifierRuleset Manifest，至少包含：

- `schema_version` 和实际执行进程的 `code_commit`；
- 按仓库相对路径和符号名排序的 `source_entries`，每项保存提取策略、规范化 AST SHA-256，
  并可附原始 Git blob 身份作来源校验；
- 从源码提取并以稳定 ID 排序的 `prompt_entries` 与 `config_entries` 摘要；
- Python 版本/ABI，以及会影响验证结论的直接依赖名称、精确版本和声明摘要；
- `verifier_source_hash`：上述 source、prompt、config、dependency 项的规范化 JSON SHA-256；
- `verifier_ruleset_hash`：`schema_version`、允许列表/符号闭包版本与
  `verifier_source_hash` 的规范化 JSON SHA-256；它不包含 `code_commit`，只表示规则语义；
- `execution_identity_hash`：`code_commit`、`verifier_ruleset_hash`、Python 执行环境身份的
  规范化 JSON SHA-256，用于证明实际执行来源，不用于单独制造 `ruleset_changed`。

Manifest 必须在 Attempt 创建后、任何候选读取或 Provider 外发前持久化，并由实际执行进程
确认自身提交和相关源码字节与 Manifest 一致。仅有分支名、当前 HEAD、部署说明、GateSnapshot
或事后人工声明都不能替代实际执行绑定。

重验资格只比较 `verifier_ruleset_hash`；两个不同 commit 若规范化规则语义相同，仍是同一
Ruleset。审计和重放同时比较 `execution_identity_hash`，因此规则语义与实际执行来源不会混为
一个身份。

CandidateVerification Module 拥有 Verifier 源码允许列表。CV-01 的旧版基线只包含：

- `src/agentic_runtime/candidate_verifier.py`；
- `src/agentic_runtime/models.py` 中 `PermissionProfile`、`VerificationStatus`、`SourceInput`、
  `PiRuntimeRequest`、`CandidateArtifact`、`VerificationCheck`、`SemanticDecision` 和
  `VerificationReport` 的稳定符号级契约；
- `src/delivery_publishing/models.py` 中 `FrozenModel` 和被验证输入直接引用的
  `TableOutputContract` 稳定符号级契约；
- `requirements.txt` 中 `httpx`、`instructor`、`openai`、`openpyxl`、`pdfplumber`、
  `python-docx` 和 `pydantic` 的精确声明。

专用 `candidate_verifier.py` 按整模块 AST 计算；共享模型文件不得按整文件计算。实现必须用
冻结 Python 版本的 AST 解析模块或上述命名符号，以
`ast.dump(..., include_attributes=False)` 的稳定结果按“仓库相对路径 + 符号名”排序后摘要；
Prompt、常量和控制流仍在 AST 中，格式、注释和同文件无关类型变化不进入身份。符号缺失、
重名、语法解析失败、Python 版本不一致，或命名符号引用了未纳入的本地契约时，历史身份直接
保持 `legacy_unversioned`，不得退回整文件哈希来放宽资格。

后续若把共享契约移入 `src/candidate_verification/`，必须在同一提交中更新允许列表、符号闭包、
Manifest Schema 和契约测试。增加、删除或重命名相关项都属于规则身份变化；允许列表自身必须
版本化并接受 Standards/Spec 审查。G1 冻结集、计划、交接、前端、共享文件中的其他符号和其他
不影响 Verifier 结论的文件不在允许列表内，它们的工作树变化不得进入 Ruleset 身份。相关允许
文件或命名符号存在未提交变化时失败关闭；不能把整个脏工作树纳入摘要。

### 历史规则身份缺少 Run 级凭据时失败关闭

现有代码已证明：RuntimeAssignment 不可变地绑定 `gate_snapshot_id`，GateSnapshot 不可变地
绑定 `code_commit`，并可从仍存在的 Git commit object 读取相关源码。但现有 Runtime 没有
强制“实际执行进程代码 = GateSnapshot.code_commit”，也没有把执行时提交或 Ruleset Manifest
写入 Run/VerificationReport。因此：

```text
RuntimeAssignment.gate_snapshot_id
  → GateSnapshot.code_commit
  → Git commit object
  → verifier_source_hash
```

只能形成“分配时门禁规则候选”，不能单独证明实际执行身份。历史 `failed` 只有在该链之外还
存在执行前或执行时写入、不可变且可校验的 Run 级执行身份凭据，并且其 commit/source hash 与
该链一致时，才能得到可比较的旧 VerifierRuleset。任一对象、源码、执行凭据或一致性检查缺失
都必须记为 `legacy_unversioned`。

`legacy_unversioned` 不自动满足 `ruleset_changed`，不得用当前工作树、服务重启说明、管理员
手填或人工口头声明补猜。本任务不新增外部证据导入或一次性历史放行入口。直接后果是：缺少
Run 级执行身份的现有确定性 failed Candidate 不能凭本能力自动取得完整重验资格；若未来需要
恢复，必须另立规格、证据模型和用户授权。

### Provider 与发布保持独立授权

需要 Provider 的每个 Attempt 都重新展示并冻结连接、版本、模型、外发类别和潜在费用，由
Owner 单独确认。Attempt 必须在外发前持久化并绑定 Grant/Usage；请求可能已经到达 Provider
但结果不可确认时记为 `outcome_unknown`，不得自动重试。恢复 Attempt 需要 Owner 再次确认
重复请求和费用风险。

重验 passed 不创建 Delivery。显式发布动作必须再次校验 Owner、P0、CandidateSet、
DeliverySpec、完整性与 QA，并把发布幂等身份绑定到精确 `attempt_id`、`report_hash` 和
CandidateSet；任何身份变化都不得复用既有 Delivery。

## 已确认决定覆盖

| 已确认决定 | 本 ADR 的冻结结论 |
|---|---|
| 追加式表与显式迁移 | Attempt 追加记录，终态不可变；启动不得静默建表或回填 |
| 仅 TaskOwner 重验 | 管理角色不得代替 Owner 使用正文、连接或费用权限 |
| 每次 Provider 外发重新确认 | 每 Attempt 冻结外发范围；未知结果不自动重试 |
| 重验通过后等待发布 | DeliveryPublishing 保持独立显式动作 |
| failed 仅规则变化后重验 | 必须比较两个有实际执行身份的 Ruleset；`legacy_unversioned` 不合格 |
| P0/Gate 阻断禁止新重验 | 新 Attempt 失败关闭，运行中 Attempt 安全收口 |

## 反例

1. 旧报告有 RuntimeAssignment 和 GateSnapshot，但没有 Run 级实际执行身份凭据。即使 Git
   commit 和源码仍可读取，也只能得到门禁规则候选；结果必须是 `legacy_unversioned`，不能
   自动出现 `ruleset_changed` Offer。
2. 只修改 `evals/generalization-g1*`、计划或交接文件。它们不在允许列表中，
   `verifier_source_hash` 和 `verifier_ruleset_hash` 必须保持不变；commit 变化只产生新的
   `execution_identity_hash`，不得因整个工作树为 dirty 而误判规则变化。

## 未采用的方案

- 放宽现有语义重试并覆盖 `verification_json`：丢失完整验证和历史审计。
- 用 GateSnapshot commit 直接冒充执行身份：现有代码没有强制二者一致，会把推定写成事实。
- 创建新 revision 或重跑 Pi：错误表达为用户业务变化，并增加生成与 Provider 成本。
- 一次性脚本、人工改库或管理员放行：绕过权限、幂等、Provider 和 Publisher 门。

## 后果

该决定增加显式迁移、Ruleset Manifest 和 Run 级执行绑定，但把资格、规则身份、外发与发布
复杂度集中到一个深 Module。新 Attempt 可以形成严格可比较的规则身份；现有缺证 failed 记录
会保守地失去自动重验资格。工程测试、迁移副本和浏览器验收都不等于生产迁移、真实 Provider
外发、真实重验、Owner 验收或正式发布，这些仍是独立授权门。
