# Legacy Candidate 再基线实施报告

> 状态：`ENGINEERING_VERIFIED`
>
> 日期：2026-08-25
>
> 范围：LR-01～LR-04；不包含生产迁移、真实 Provider、正式发布或 GitHub 写入

## 1. 结果

已为精确 `latest failed + legacy_unversioned` Candidate 链实现一次性
`legacy_rebaseline`：TaskOwner 承认旧 Ruleset 身份未知后，系统把结构化授权证据、同一
CandidateSet 和当前 Ruleset 冻结到第一条 versioned Attempt，并由完整 Verifier 执行。它不重跑
Pi、不修改 Candidate、不创建 revision，passed 后也只进入待发布态。

Provider `outcome_unknown` 的后续人工恢复使用独立 `provider_outcome_recovery` 原因；它不会再次
执行 `legacy_rebaseline`，且仍要求重复费用风险确认。

## 2. 关键失败关闭门

- 创建写锁内证明前序仍是追加账本最新 Attempt，且链上不存在任何 versioned Attempt；
- Worker 认领写锁内重新解析并逐字段核对 Owner 授权、前序、CandidateSet、目标 Ruleset、外发
  确认和授权时间；同时要求当前 Attempt 是唯一 versioned 链头；
- Offer 哈希、请求哈希和前端幂等指纹绑定 CandidateSet 与目标 Ruleset；相同幂等命令不受并发
  时钟差异影响；
- 0004 迁移追加原因和授权列，保持既有 Attempt 原字段不变；0003 与 0004 各自绑定自己的生产
  恢复点，0004 重放不会错误要求两次恢复点相同；
- 页面把“旧规则未知”和“本次 Provider 外发”分成两项确认，并展示完整 Target Ruleset SHA-256。

## 3. 验证证据

- CandidateVerification、迁移、Repository、Offer、执行、Provider、工作台 API 合并回归：
  `153 passed`；
- Semantic Workspace Playwright：`31 passed`，覆盖 light/dark、可访问性、双确认、冻结哈希请求和
  待发布边界；
- 前端 `tsc --noEmit && vite build`：通过；仅保留既有大 chunk 提示；
- Frontend Design Premium strict audit：0 finding；
- Python compile、`git diff --check`：通过；仅有工作树既有 LF/CRLF 提示；
- 真实生产库的一致性只读副本执行 0004：73 张非迁移业务表、10,196 行逻辑摘要前后同为
  `c91b3757bc1e240cb272310ff0798a80c0e531212c18c4ca9411fe62aee0c40e`；前向、重放、
  `integrity_check=ok`、外键违规 0；一次性演练副本仍保留在 `.scratch/`，不是生产恢复点且未纳入
  版本控制；客户端阻止了本轮清理，没有绕过，也没有改写或删除任何生产恢复点；
- `agent-browser 0.34.0` 只读连接 8088 成功；独立会话被正确重定向到登录页。该会话不共享用户
  Chrome 登录态，因此没有冒用凭据或提交真实请求。

## 4. 审查中发现并修复

Standards/Spec 双轴审查共发现 6 项实质缺口，均已补回归并修复：单链最新/唯一 CAS、Worker
授权复核、稳定幂等命令身份、Provider 未知结果独立恢复原因、未知结果恢复的并发链 CAS、当前
Ruleset 展示。生产副本演练另发现并修复 0003/0004 不同恢复点导致的重放误拒绝。
修复后的 Standards 与 Spec 终审均为 `PASS`，无剩余 P1/P2。

## 5. 后续生产状态

- 用户另行授权后，`data/webui.db` 已完成 0004，恢复点、数据零改写、重放和服务恢复均通过；
- 生产迁移证据：
  `docs/plans/2026-08-25-legacy-candidate-rebaseline-production-migration-report.md`；
- 未对 `liyi111` 创建真实 Attempt、调用 Provider 或产生费用；
- 未发布正式 Delivery；
- 未提交、推送、修改 GitHub Issue、PR、标签、Release 或 About。

下一门是 LR-05B / Gate B：在用户已登录会话核对精确 Owner、Task、Run、CandidateSet、连接、
模型、外发范围和费用，再由用户授权唯一真实 Attempt。passed 后仍需独立发布授权和 Owner 验收。
