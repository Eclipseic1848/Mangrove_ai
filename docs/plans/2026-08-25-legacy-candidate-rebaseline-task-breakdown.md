# Legacy Candidate 再基线任务拆分

> 状态：`TICKETS_READY`
>
> 日期：2026-08-25
>
> 上游规格：`2026-08-25-legacy-candidate-rebaseline-spec.md`（`SPEC_APPROVED`）
>
> 归属：GitHub #70；本拆分不创建或修改 GitHub Issue

## 1. 冻结范围

本次只为最新 `failed + legacy_unversioned` Candidate 链增加一次 Owner 控制的完整再基线。
不重跑 Pi、不修复 Candidate、不开放其他旧终态、不自动重试未知 Provider 结果、不自动发布。

## 2. 依赖顺序

```text
LR-01 持久化与资格门
  ↓
LR-02 执行与 API
  ↓
LR-03 普通用户前端
  ↓
LR-04 工程收口
  ↓
LR-05 生产人工门
```

## 3. 纵切片

### LR-01 持久化与资格门

**目标**：让 Module 能严格识别唯一合格 legacy 链，并以不可变结构化证据追加第一条
versioned Attempt。

**实现**：

- 增加 `legacy_rebaseline` Attempt 原因；
- 通过显式 `0004` 迁移为 Attempt 增加可空授权 JSON 和匹配哈希，保留旧行；
- Repository 强制原因与证据成对、哈希一致、终态不可改；
- Offer 仅允许最新 `failed + legacy_unversioned`，并拒绝链上已有 versioned Attempt；
- 冻结旧 Attempt、CandidateSet、目标 Ruleset、Actor 和授权文案版本。

**TDD 接缝**：`tests/test_candidate_verification_repository.py`、
`tests/test_candidate_reverification_offer.py`、新增迁移测试；先覆盖合法一次、重复、并发、篡改、
跨 Owner、其他终态和已有 versioned 链。

### LR-02 执行与 API

**Blocked by**：LR-01。

**目标**：Owner 经现有 candidate-verifications 接口创建唯一再基线 Attempt，并复用完整 Verifier
执行，不重跑 Pi。

**实现**：

- 请求增加旧规则未知确认、授权文案版本和目标 Ruleset 乐观锁；
- 服务端推导 `legacy_rebaseline`，客户端不能自报原因；
- 本地路线只要求旧规则未知确认；Provider 路线另外要求本次外发确认；
- requested→running 前重验冻结身份，漂移即失败关闭；
- Provider 未知结果进入既有 `outcome_unknown` 恢复，不得再次 legacy 再基线；
- passed 仍停在待发布，不调用 Publisher。

**TDD 接缝**：`tests/test_candidate_reverification_execution.py`、
`tests/test_candidate_reverification_provider.py`、`tests/test_semantic_workspace_api.py`；证明候选字节、
revision、旧 Attempt 不变，Pi start/resume/write 为零。

### LR-03 普通用户前端

**Blocked by**：LR-02。

**目标**：历史任务详情不再只显示不可操作 blocker；合格 Owner 能在一次对话框中明确完成再基线
确认，看到执行进度和待发布结果。

**实现**：

- 扩展 `ReverificationOffer` 类型与 API 请求；
- `SemanticWorkspacePage` 显示独立“建立当前验证基线”动作；
- 同一对话框分别表达旧规则未知确认和 Provider 外发确认，本地路线隐藏后者；
- 显示单次边界、不会重跑任务、不会自动发布；
- 409/422/Provider 未知结果保留可恢复错误态，不显示伪成功。

**验证接缝**：前端单元/集成测试、构建、8088 Owner 浏览器流程；涉及页面实现时同时使用
`frontend-design` 与 `frontend-design-premium`，复用现有视觉语言，不引入新组件依赖。

### LR-04 工程收口

**Blocked by**：LR-03。

**目标**：用相称回归和双轴审查证明新增路径没有放宽 ADR-0033、Owner、P0、Provider 或发布门。

**验证**：

- CandidateVerification 聚焦测试、API 回归、前端测试与构建；
- 迁移在生产副本上演练：备份、前向、重放、完整性、外键、旧逻辑指纹零改写；
- 8088 浏览器验证合格/不合格 Offer、一次确认、失败恢复和待发布状态；
- Standards + Spec 双轴审查并修复发现；
- 更新实施报告、`handoff.md` 和 `docs/status/current.md`。

### LR-05 生产人工门

**Blocked by**：LR-04。

**目标**：只对冻结的 `liyi111` 目标执行一次真实纵切面，并由 Owner 决定是否发布。

**独立授权**：

1. 生产数据库备份与 `0004` 迁移；
2. 精确 Provider、模型、外发内容类别和费用风险确认；
3. 只执行一个 `legacy_rebaseline` Attempt，未知结果不自动重试；
4. Owner 检查报告；仅 passed 时另行授权正式发布；
5. GitHub #70/#54 更新或关闭仍属于远端写操作。

## 4. 完成定义

- LR-01～LR-04 通过：`ENGINEERING_VERIFIED`，不等于生产已迁移或真实重验；
- LR-05 形成 versioned Attempt：`LIVE_REBASELINED`；
- Attempt 确定性收口并经 Owner 检查：`LIVE_REVERIFIED`；
- Owner 另行发布并验收：`LIVE_ACCEPTED`。
