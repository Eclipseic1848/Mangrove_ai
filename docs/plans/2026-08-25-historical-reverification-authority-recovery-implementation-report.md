# 历史候选重验权威恢复实现报告

> 状态：`PRODUCTION_EXECUTED_FAILED_NO_PUBLICATION`
>
> 日期：2026-08-25
>
> 规格：`2026-08-25-historical-reverification-authority-recovery-spec.md`
>
> 决策：`../adr/0034-historical-reverification-authority.md`

## 1. 结论

六项业务决定已经按 TDD 实现并完成相称工程验证。系统现在可以为 RuntimeRouting 上线前、精确
缺少普通 `RuntimeAssignment` 的历史 Candidate 投影一个 TaskOwner 恢复门，并在 Owner 双确认后
追加不可变 `HistoricalReverificationAuthority`。该凭据只允许
`semantic_inconclusive_reverification`，不回填历史路由事实，不恢复 Pi，不创建 revision，也不发布
Delivery。

生产迁移、真实 authority 和一次精确 Provider 重验已经发生。结果为 `failed`，没有发布正式
Delivery，也没有剩余自动重试授权。

## 2. 已验证事实

### 2.1 领域、迁移与 Repository

- `HistoricalReverificationEvidence`、authority、Offer 和严格双确认均为 `extra=forbid` 的冻结模型；
- `0003_historical_reverification_authorities` 显式迁移记录备份摘要、DDL SHA-256 和应用时间；
- 迁移记录与 authority 均由 Trigger 禁止 UPDATE/DELETE；原始 SQLite 连接不能绕过受控
  Repository 直接 INSERT authority；
- authority 写入在 `BEGIN IMMEDIATE` 内重建并核对 TaskRevision、Runtime 请求、事件链、来源绑定、
  CandidateSet、Manifest、合同、前序 Attempt/Report、连接、P0、Delivery、历史时间边界和普通
  Assignment 缺失；
- 同键同请求返回同一事实；同键省略或改变恢复确认返回冲突；
- Worker 认领事务再次核对精确 authority 和普通 Assignment 缺失。认领前并发出现 Assignment 时，
  Attempt 取消且 Verifier/Provider 调用为 0。

### 2.2 Service、API 与前端

- 保留 `inspect_reverification` 和 `request_reverification` 两个业务动作；没有新增通用补权威入口；
- 只有 TaskOwner 可以恢复；管理员或超级管理员代替 Owner 返回 403，零 authority、零 Attempt；
- 缺少双确认、Offer 摘要变化、迁移后新任务缺 Assignment、事件链不完整或任一身份漂移都失败关闭；
- 前端在原重验对话框内解释“不补造旧 Assignment”，展示精确 Owner/Task/revision/Run、Candidate
  数量/格式和 Evidence SHA，不展示正文、Secret、宿主路径或 Provider 原始日志；
- 恢复确认与 Provider 外发确认保持两个语义，取消后焦点返回触发按钮；窄屏、200% 等效视口、
  明暗主题和 axe 均有 Playwright 覆盖。

### 2.3 验证证据

| 范围 | 结果 |
|---|---|
| CandidateVerification + 完整工作台 API 合并影响面 | `166 passed` |
| Semantic Workspace Playwright 完整文件 | `30 passed` |
| 前端 TypeScript + Vite build | 通过；只有既有 chunk-size 警告 |
| Frontend Premium strict/no-write | 既有 26 项；新增规则/文件组合 0 |
| 生产库只读副本演练 | `0003` 迁移、重放、恢复点、完整性和外键通过 |

生产副本演练确认源库 `0003` 迁移记录前后均为 0；所有迁移只发生在 `.scratch` 副本。演练生成的
生产数据副本和迁移前恢复点验证后已经删除。

## 3. 双轴审查与修复

首次 Standards/Spec 审查发现并已修复：

1. 幂等快速路径未冻结历史恢复确认；
2. authority 写锁内未复核全部数据库身份；
3. Worker 出网前复核与 Attempt 认领不原子；
4. 迁移记录缺 DDL 摘要和 append-only 保护；
5. 原始 SQL 可直接伪造 authority；
6. 管理员代替 Owner 未返回明确 403；
7. current/handoff/规格仍停留在“等待六项决定”；
8. authority 已写入但 Attempt 创建前失败时，同幂等请求无法续接；
9. Repository 启动时未核对 `0003` DDL 摘要；
10. authority 写锁内未确认前序 Attempt 仍为最新且无并发活动/未知 Attempt。

每项均补了最小回归接缝；没有顺手修改 G1 评测文件、既有 Frontend Premium 债或其他模块。
修复后的 Standards 与 Spec 最终复审均为 PASS，无剩余 P1/P2。

## 4. 基于代码的推断

- `HistoricalReverificationAuthority` 解决的是“Owner 现在是否授权精确旧 Candidate 进入独立重验”，
  不是证明历史运行当时拥有普通 Assignment；
- authority 与 requested Attempt 分两次持久化。如果 authority 成功后 Verifier/连接资格变化，
  authority 会保留而 Attempt 不创建或 Worker 取消；同一 Owner 使用同一幂等键和原结构化确认可续接
  原请求，这是追加式授权事实与执行事实分离的预期结果；
- 生产迁移成功也不会自动触发 DeepSeek。只有精确 Owner 请求和 Worker 第二次权威复核通过后才会
  发生一次已授权外发。

## 5. 生产执行事实

- `0003` 已使用恢复点
  `data/backups/webui-before-cv10-authority-20260825-211250.db` 显式迁移；备份 SHA-256 为
  `f9487724fc3a7975f799916e1a4a477ac300661980c9d530bc6e891a57067882`；
- authority ID 为
  `historical_authority_119a855f4f7356cecf06cc69fe6d19540f39c90bd849a406a3f349ca84e5091e`，
  actor 为 `u_9505fd620899`；
- Attempt `verification_4f29b69d56306a8583ce7a4b45a237d3` 精确链接旧 Attempt 和同一
  CandidateSet，冻结当前 versioned Ruleset，终态为 `failed`；
- Provider 只调用 1 次，Usage 为 input 3,625 / output 1,441 / total 5,066 tokens；对应 Grant 已撤销；
- `artifact_set`、`artifact_count`、`source_grounding` 通过，88 条证据重新确认；`semantic_goal`
  未通过，报告指出候选混入非技术指标，同时存在无来源对应的技术项；
- `formal_delivery_eligible=false`，正式 Delivery 为 0；旧 Attempt、TaskRevision、Run、Candidate、
  Manifest 和 CSV 均未改写。

## 6. 生产夹具暴露的缺口与修复

生产执行前两次都在 Provider 前失败关闭，均为工程夹具未覆盖的合法旧数据形态，不是模型调用：

1. 旧 Runtime JSON 唯一缺少 `external_api_confirmed`，Service、Workspace Adapter 和 Repository
   解析不一致；现已共用严格解析器，只接受 Runtime 列 bool 或整数 0/1，其他损坏值拒绝；
2. 合法 `legacy_unversioned` Attempt 的 Manifest/Goal/Delivery 版本列为空，写锁错误要求这些空列
   等于当前摘要；现由写锁重读当前 Manifest、重算冻结 Goal/Delivery 摘要，versioned 非空列仍需
   精确一致。

两项均先建立红灯，再修复并纳入 166 项合并回归；最终 Standards/Spec 复审 PASS。`diagnosing-bugs`
技能使本轮坚持从写锁谓词建立紧反馈，而没有人工改库或绕过失败关闭。

## 7. 下一门禁

本次一次性 DeepSeek 授权已经消耗。failed 结果不得发布或自动重试。如果用户决定修改 CSV，应先
确认新 revision/新 Candidate 的业务范围，再独立确认新的 Provider 外发和费用。提交、推送、PR、
Issue 更新、标签、Release、部署和 GitHub About 均未执行。
