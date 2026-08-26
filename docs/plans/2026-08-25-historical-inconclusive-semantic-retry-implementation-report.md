# 历史 inconclusive Candidate 语义重试恢复实现报告

> 状态：`ENGINEERING_VERIFIED_LIVE_AUTHORITY_DECISION_REQUIRED`
>
> 核验日期：2026-08-25
>
> 固定代码基线：`7efaf2fd78a8f1df0b86929b19e27cf0a7b5ca03` 上的未提交工作树
>
> 范围：历史 `inconclusive` Candidate 的语义门重试恢复；不含历史 RuntimeAssignment 权威
> 恢复、真实 Provider 调用、正式发布、提交或推送

## 1. 实现结果

### 服务端

- 只读 Offer 只有在旧报告为 `inconclusive`、artifact set/count、必要表格契约和
  source grounding 均通过、唯一失败项为 `semantic_goal` 时才返回
  `reason=semantic_inconclusive`。
- `failed + legacy_unversioned` 继续失败关闭，不借语义重试入口进入完整再基线。
- 旧 `request_json` 仅缺 `external_api_confirmed` 时，可在内存使用同一 Runtime 冻结列；不回填
  JSON。该兼容只对 `legacy_unversioned` 生效。
- JSON 已有外发字段时必须是原生布尔值，并与 Runtime 列严格一致；字符串 `"true"`、字段冲突
  或其他结构缺失均失败关闭。
- Worker 按 Attempt reason 分派：`semantic_inconclusive` 调
  `retry_semantic_verification`，其他正式原因继续调用完整 `verify`。
- 旧报告身份、哈希和资格在 requested Attempt 原子认领为 running 之前完成校验，预检失败不会
  留下卡住的 running Attempt。
- 旧 `/candidate-verification/retry` API 返回 HTTP 410；客户端必须使用追加式
  CandidateVerification API，不能再走旧自动发布路径。
- passed 只进入“等待发布”，不会创建 Delivery；正式发布仍绑定精确 Attempt 和独立幂等键。

### 前端

- 重试按钮完全由服务端 Offer 驱动，动作统一命名为“只重跑语义验证”。
- 确认界面明确说明：不重跑 Pi、不修改文件、不创建 revision、不重新执行完整 Verifier、
  不自动发布。
- 展示精确 Candidate 文件、格式、Candidate ID、完整 SHA-256、连接、模型、外发范围和费用风险。
- 继续复用现有 Radix AlertDialog、共享按钮、Toast 和状态恢复路径，没有新增前端依赖或第二套
  视觉系统。
- 旧同步重试调用已从页面删除；HTTP 410 不再被产品入口使用。

## 2. 风险接缝证据

- 非布尔冻结外发字段先红后绿：字符串 `"true"` 曾被 Python/Pydantic 宽松转换放行；现在严格
  要求原生 bool。
- 无效旧报告通过 Repository 只读投影模拟，不修改不可变终态 Attempt；数据库 Trigger 保持
  原样并继续拒绝终态 UPDATE。
- 真实 `CandidateVerifier.retry_semantic_verification` 在 `SourceInput` 指向不存在的原来源文件
  时仍成功，证明它只复用冻结 Manifest 和旧报告中的证据摘要；Candidate/Manifest 文件树零
  改写。
- API 纵切面证明：Pi `start` 总次数保持 1、`resume` 为 0，只调用语义重试、完整 `verify` 为 0，
  TaskRevision 数量、Candidate 和 Manifest 字节均不变；passed 后 Delivery 为 0，显式幂等发布后
  唯一 Delivery 为 1。
- `outcome_unknown` 继续零自动重试，恢复仍要求新 Attempt 和重复费用确认。

## 3. 验证结果

### 后端

```powershell
E:\python3.13\python.exe -m pytest `
  tests/test_candidate_reverification_offer.py `
  tests/test_candidate_reverification_provider.py `
  tests/test_candidate_reverification_execution.py `
  tests/test_pi_runtime_workspace_api.py -q
```

结果：`84 passed, 2 warnings`。两条 warning 是既有依赖弃用提示，不是本切片失败。

Python 编译通过：

```powershell
E:\python3.13\python.exe -m py_compile `
  src/candidate_verification/models.py `
  src/candidate_verification/repository.py `
  src/candidate_verification/service.py `
  src/api/routes/semantic_workspace.py
```

### 前端

```powershell
npm.cmd run test:e2e -- e2e/semantic-workspace.spec.ts
npm.cmd run build
```

结果：Semantic Workspace Playwright `29 passed`；TypeScript 检查和 Vite 正式构建通过。浏览器
矩阵覆盖语义重试、刷新恢复、独立发布、错误恢复、键盘、窄屏、深浅主题和 axe 严重/致命问题。

Frontend Design Premium 上游解析为 `MATCH`。strict audit 仍返回既有 26 项（15 unresolved、
11 violations），规则 ID 和文件集合与 `frontend/premium-audit.json` 基线一致；本切片没有新增
审计项。项目缺少 DESIGN.md/UX-CONTRACT.md 是既有治理缺口，本次没有引入系统级视觉决定，
因此未在本切片创建未经批准的设计系统文档或顺手修无关旧债。

### 工程检查与审查

- `git diff --check`：通过，仅有工作树 LF→CRLF 提示；
- Standards 最终复审：无发现；
- Spec 最终复审：无发现。

## 4. 生产只读 preflight

精确目标：

- Owner：`liyi / u_9505fd620899`；
- Task：`workspace_c115f33be1004f51`，revision 1；
- Run：`pi_run_42daee348b9a45bc`；
- Candidate：1 个 CSV；
- 连接：`7d45d047-68de-4db0-b8b0-b1e4638aa591`；
- 模型：`deepseek-v4-flash`；
- 前序 Attempt：
  `legacy_65e0903778f50e42b140ed96549ea3af2249e65dd21bad62555e52b6d65fc441`，
  `inconclusive`。

现场结果：

```text
eligible=false
reason=null
blockers=[runtime_assignment_drift]
runtime_assignment_count=0
active_or_unknown_attempt_count=0
```

除 RuntimeAssignment 外，Candidate、旧报告确定性门、连接和模型均通过当前只读 Offer。探针零
写入、零 Grant、零 Provider 调用。

## 5. 当前边界与下一决定

工程实现已经达到 `ENGINEERING_VERIFIED`，但真实调用没有达到权威前提。该历史 Task 完全没有
RuntimeAssignment；现有规格不允许补猜、回填、人工改库、目标白名单或绕过失败关闭门。

因此当前没有：

- 创建新的真实 VerificationAttempt；
- 调用 DeepSeek 或产生费用；
- 修改 CSV、88 条证据、Run、revision 或 Pi 状态；
- 发布 Delivery。

推荐下一步是另立最小“历史 RuntimeAssignment 权威恢复”规格，采用追加式、可审计的恢复证据，
明确可证明的来源、Owner 授权、适用范围、失败关闭条件和迁移/回滚方式。该决定改变历史权威状态，
必须由用户确认；它不是再次展示已经确认过的模型、外发内容或费用。

不推荐方案：

- 直接合成或 UPDATE RuntimeAssignment；
- 为该 Task 增加特殊白名单；
- 创建新 revision 或重跑 Pi 来规避历史恢复问题；
- 调用旧重试 API 或自动发布。

若用户不批准权威恢复，则该历史 Candidate 保持可读、可下载但停在候选状态；这不影响工程切片
本身的验证结论，也不能把真实调用表述为已完成。

## 6. 公共仓库同步门

本纵切片不是 P0/P1 顶层 Phase 完成，不触发 README、Code of Conduct、Contributing、MIT
License、Security 和 GitHub About 的顶层同步门。没有为了制造差异修改这些稳定入口，也没有
执行提交、推送或 GitHub 远端写入。
