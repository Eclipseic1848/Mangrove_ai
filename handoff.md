# Mangrove 零上下文交接

> 状态：`ACTIVE_GOAL`
>
> 最后现场核验：2026-08-26
>
> 当前分支：`main`
>
> 当前 HEAD / `origin/main`：`7efaf2fd78a8f1df0b86929b19e27cf0a7b5ca03`
>
> 当前会话终点：完成本交接冻结的 GitHub Open Issues；全部完成、验证、按需验收并关闭后，本会话才结束

## 1. 我们在做什么

本会话的长期目标不是只修一个页面或跑一次模型，而是完成 2026-08-25 现场冻结的
`Eclipseic1848/Mangrove_ai` 当前版本全部 Open Issues：

| Issue | 任务 | 当前状态 | 依赖 |
|---|---|---|---|
| #54 | P0-01 vNext 默认链路与真实普通用户闭环 | LIVE_ACCEPTED / GitHub OPEN | 子任务 #70 已完成现场门 |
| #70 | CV-10 生产迁移、真实同 Run 重验与 Owner 验收 | LIVE_ACCEPTED / GitHub OPEN | 待远端证据收口 |
| #55 | P0-04A 最小 CI 工程门 | OPEN / `ready-for-agent` | #54 |
| #56 | P0-05 显式数据库迁移体系 | OPEN / `ready-for-agent` | #55 |
| #57 | P0-02 Secret 存储统一 | OPEN / `ready-for-agent` | #56 |
| #58 | P0-03 依赖拆分与漏洞治理 | OPEN / `ready-for-agent` | #55 |
| #59 | P0-04B 主分支保护落地 | OPEN / `ready-for-human` | #55、#58 |
| #60 | P0-06 当前状态与交接收口 | OPEN / `ready-for-agent` | #54～#59 |

冻结的推荐顺序：

```text
#54 / #70
    └─ #55
        ├─ #56 ─ #57
        └─ #58 ─ #59
                    └─ #60
```

后续新建 Issue 不自动并入本目标，必须先由用户确认是否扩大范围。GitHub 当前没有 Milestone，
也没有 Release；不要臆造版本里程碑或发布状态。

## 2. 新会话第一步

按顺序读取并现场复核，不要从历史聊天猜状态：

1. 本文件；
2. `AGENTS.md`；
3. `docs/status/current.md`；
4. `CONTEXT.md` 与 `docs/agents/`；
5. 当前任务引用的规格、ADR 和执行报告；
6. `git status --short`、`git rev-parse HEAD`、`git rev-parse origin/main`；
7. `gh issue list --repo Eclipseic1848/Mangrove_ai --state open`，并逐张读取正文和评论；
8. 8088、数据库、活动 Attempt/Grant 和 Provider 状态的只读现场探针。

`docs/plans/2026-08-23-post-issues-productization-roadmap.md` 是 P0/P1/P2 路线来源，但其中
“Open Issues 为 0”“下一任务为 P0-01”等是 2026-08-23 快照，已过期。当前进度以本文件、
`docs/status/current.md`、GitHub 和现场代码/数据库为准。

2026-08-25 再次只读核对 GitHub：#54 的最新评论仍写 `main=6f29a94b`，#70 的最新评论仍写
“CandidateVerification 0001/0002 尚未执行”，两者都是 Gate A 前的历史快照。现场
`main/origin/main=7efaf2fd78a8f1df0b86929b19e27cf0a7b5ca03`，Gate A 已按第 4.3 节完成；不得用旧评论
覆盖现场 Git、迁移报告和数据库事实。

## 3. 项目是什么

Mangrove 是统一数据任务平台：用户提交文件或其他来源，用自然语言定义目标；系统冻结
TaskRevision、来源、Owner、模型连接、外发确认和能力身份，运行 Pi 或兼容 Runtime 生成
Candidate，再由独立 Verifier 验证，最后只有完整性和 QA 都通过的结果才能发布为正式
Delivery。

稳定边界：

- `8088` 是统一产品入口，`5173` 只用于前端开发；
- `/data-prep` 是当前主工作台，历史任务和 Legacy Delivery 在迁移完成前继续兼容读取；
- Candidate、验证通过、`eligible_for_delivery` 都不是正式交付；只有
  `delivery_published` 且通过完整性/QA 的 `output_id` 才是正式 Delivery；
- 普通用户、管理员、超级管理员是产品角色；“高级用户”不是权限角色；
- Owner、TaskRevision、Run、CandidateSet、连接版本、外发确认、Ruleset/能力身份和来源必须
  冻结并失败关闭。

## 4. 已经完成了什么

### 4.1 历史主线能力

- G1 独立盲集正式运行合格：功能 30/31，安全 5/5；这不是整个平台发布资格。
- G2 Word/Excel 代表任务和 AC-05 生产迁移、恢复、并发、Docker 探针完成。
- G3/G4 完成 `vnext_default`、P0 回滚/恢复和 DeepSeek/Qwen Provider 安全资格。
- G5 本机 Linux/Compose、真实浏览器、并发、超时、重启、SQLite 备份恢复和路径可移植性工程门
  完成；真实目标 Linux/GPU 服务器仍未验收。
- AC-07 新仓库 #9～#17 已完成并关闭，两条真实能力治理纵切面已走过验证、供应链、签名、
  发布、装载、隔离、恢复和撤销链；普通用户能力市场仍未开放。

### 4.2 P0-01 / Candidate 重验主线

- CV-01～CV-09（GitHub #61～#69）已实现、写入证据并关闭。
- 已形成正式 CandidateVerification Module：追加式不可变 Attempt、只读 Offer、同 Run 完整
  重验、逐 Attempt Provider Grant/Usage、`outcome_unknown` 零自动重试、精确 Attempt 独立发布
  和普通用户工作台。
- CV-09 工程门证据：后端 `1999 passed, 7 skipped, 4 deselected`，前端完整 E2E 64 passed，
  Standards/Spec 双轴审查无剩余 P1/P2。工程绿色不等于生产验收。
- 实现基线已推送；当前 `main` 与 `origin/main` 一致。没有创建版本标签或 Release。

### 4.3 CV-10 Gate A

- 用户已授权并完成生产 CandidateVerification `0001/0002` 显式迁移。
- 唯一恢复点：`data/backups/webui-before-cv10-20260825-010051.db`；SHA-256：
  `09838edfad1826b876821e7857993aa8b858cf18f98335d1815bd535ce6342d1`。
- 原 71 张表、10,313 行逻辑指纹零改写；35 条历史报告导入为 27 passed、4 failed、
  4 inconclusive；迁移重放、完整性、外键和服务恢复均通过。
- Gate A 没有调用 Provider、创建真实重验 Attempt 或发布 Delivery。
- 权威证据：
  `docs/plans/2026-08-25-cv-10-gate-a-production-migration-report.md`。

### 4.4 历史任务详情空白修复

- 23 条未删除生产任务详情已从部分可读恢复为 23/23 可读；缺少可信冻结上下文的旧 Candidate
  只关闭重验 Offer，不回填、不补猜历史字段。
- 前端详情错误会显示原因和“重新加载”，不再表现为空白。
- 已有证据：后端 34 passed、数据工作台 Playwright 29 passed、前端构建通过、生产库逻辑
  指纹前后一致。
- 权威证据：`docs/plans/2026-08-25-history-task-restoration-report.md`。

## 5. 当前正在做什么、卡在哪里

当前存在两个不同账号、不同 Candidate、不同业务原因的恢复任务。绝对不能混用授权或实现。

### 5.1 已完成真实执行：历史 `inconclusive` 只重跑语义门

精确对象：

- Owner：`liyi / u_9505fd620899`；
- Task：`workspace_c115f33be1004f51`，revision 1；
- Run：`pi_run_42daee348b9a45bc`；
- CandidateSet：1 个 CSV，冻结 88 条来源证据摘要；
- 连接：`7d45d047-68de-4db0-b8b0-b1e4638aa591`；
- 模型：`deepseek-v4-flash`；
- 前序 Attempt：`inconclusive + legacy_unversioned`，只有 `semantic_goal` 未形成可靠结论。

该一次性 DeepSeek 授权已经执行并消耗。不得把它用于新 Attempt、`liyi111`、Pi 重跑、CSV/证据
修改、新 revision 或正式发布。

当前工程实现已收口为 `ENGINEERING_VERIFIED`：

- 后端允许合格的历史 `inconclusive` 进入 `semantic_inconclusive` Attempt，只调用
  `retry_semantic_verification`；确定性门失败继续阻断；
- 旧请求仅缺 `external_api_confirmed` 时可从同一 Runtime 冻结列内存解析；非布尔值或字段冲突
  失败关闭；
- 预检在 Attempt 认领前完成，避免无效报告把 requested Attempt 卡成 running；
- 旧 `/candidate-verification/retry` API 已退役为 HTTP 410，不能再绕过新 Attempt 自动发布；
- 前端改成服务端 Offer 驱动的“只重跑语义验证”确认流，展示 Candidate 文件、ID 和完整 SHA；
- 无效旧报告测试改用 Repository 只读投影模拟，没有修改终态 Attempt 或放宽数据库 Trigger；
- 实际 `CandidateVerifier` 测试证明原来源不存在时仍只复用冻结 Manifest/旧报告，候选树零改写；
- API 纵切面证明 Pi start 保持 1、resume 为 0、revision/Candidate/Manifest 不变、passed 零
  Delivery、显式发布后唯一 Delivery；
- 后端聚焦回归 84 passed，Semantic Workspace E2E 29 passed，前端 build、Python compile、
  `git diff --check` 通过；Standards/Spec 最终复审均无发现；
- Frontend Design Premium strict audit 仍是既有 26 项，规则 ID 和文件集合未新增；不要顺手修
  无关旧债。

六项业务决定和 TDD 实现已经完成。CandidateVerification 现有追加式
`HistoricalReverificationAuthority`：只记录 TaskOwner 现在对精确旧 CandidateSet 的窄重验授权，
不补写、不合成普通 RuntimeAssignment，也不能用于 Pi 恢复、新 revision 或发布。`0003` 迁移记录
DDL 摘要并保护迁移/authority 不可改删；authority 只能由受控 Repository 追加。请求写锁内重建
TaskRevision、Runtime 请求/事件、Candidate/Manifest/合同/前序报告、连接、P0、Delivery 和历史边界；
Worker 认领事务再次原子确认精确 authority 且普通 Assignment 仍不存在。

生产门与结果：

- CandidateVerification `0003` 已显式迁移；恢复点为
  `data/backups/webui-before-cv10-authority-20260825-211250.db`，SHA-256
  `f9487724fc3a7975f799916e1a4a477ac300661980c9d530bc6e891a57067882`；72 张既有非迁移表、
  10,169 行零改写，重放、完整性和外键通过；
- 生产夹具暴露两处仅真实旧数据触发的失败关闭缺口：旧 `request_json` 唯一缺外发字段时三处解析
  不一致，以及合法 `legacy_unversioned` Attempt 的 Manifest/Goal/Delivery 版本列为空时写锁误拒。
  两处均以 `diagnosing-bugs` 紧反馈和 TDD 修复；现在只兼容精确 legacy 缺口，损坏列、冲突值和
  versioned 缺字段继续失败关闭；
- 合并影响面回归 `166 passed`，Python compile、`git diff --check` 和 Standards/Spec 双轴终审 PASS；
- 唯一 authority：
  `historical_authority_119a855f4f7356cecf06cc69fe6d19540f39c90bd849a406a3f349ca84e5091e`，
  actor 为精确 TaskOwner `u_9505fd620899`；
- 唯一新 Attempt：`verification_4f29b69d56306a8583ce7a4b45a237d3`，状态 `failed`，精确链接前序
  `legacy_65e0903778f50e42b140ed96549ea3af2249e65dd21bad62555e52b6d65fc441`；
- DeepSeek Usage：1 request，input 3,625 / output 1,441 / total 5,066 tokens；Grant
  `grant_cv_43a0b9e820584ab35ef23d402825ec30` 已以 `candidate_verify_closed` 撤销；
- 结果：文件集、数量和 88 条来源证据通过；`semantic_goal` 失败。CSV 混入“业务用户量”“未来5年
  用户量增长”“免费系统维护期”“在线专家支持”等非技术内容，且多项技术架构/中间件/前端框架
  在已验证来源中无对应，不能证明完整提取所有技术指标；
- `formal_delivery_eligible=false`，正式 Delivery 为 0；revision、run、CandidateSet、旧 Attempt 和
  CSV SHA `9d5820f799e811303933efd99084836759286d09043e30a4fd53b6aefe83e71f` 均未改写。

这条 `liyi` 任务现在没有待发布结果，也没有剩余重试授权。若业务上要修正 CSV，必须创建新
revision/新 Candidate，重新确认范围与 Provider 外发；不得把 failed Attempt 改绿或复用本次授权。

规格：`docs/plans/2026-08-25-historical-inconclusive-semantic-retry-spec.md`；实现证据：
`docs/plans/2026-08-25-historical-inconclusive-semantic-retry-implementation-report.md`；权威恢复规格：
`docs/plans/2026-08-25-historical-reverification-authority-recovery-spec.md`；实现证据：
`docs/plans/2026-08-25-historical-reverification-authority-recovery-implementation-report.md`。

### 5.2 #70 主目标：`failed + legacy_unversioned` 再基线

精确对象：`liyi111 / workspace_8363695f133645ac / revision 1 /
pi_run_c033ae394ae94cf4`，CandidateSet 为 CSV + JSON。Gate A 后只读 Offer 的唯一 blocker 是
`legacy_unversioned`。

这是 ADR-0033 的正确失败关闭结果：旧实际 Ruleset 无法证明，不能把当前 Ruleset、服务重启、
GateSnapshot 或人工声明冒充旧执行身份。它又与 #70 要求真实重验的验收目标冲突，因此不能直接
进入 Gate B。

规格 `docs/plans/2026-08-25-legacy-candidate-rebaseline-spec.md` 已完成五项人工业务决定并批准；
LR-01～LR-04 已达到 `ENGINEERING_VERIFIED`。实现新增正式 `legacy_rebaseline`：承认旧 Ruleset
未知，保留旧 failed Attempt，对同一不可变 CandidateSet 使用当前完整 Verifier 建立第一条
versioned 基线；链级 CAS、Worker 授权复核、并发幂等、`provider_outcome_recovery`、普通用户双
确认和 0004 显式迁移均已有回归。后端 153 passed、工作台 E2E 31 passed、前端构建、严格设计审计
和生产一致性副本迁移/重放均通过，Standards/Spec 终审 PASS，无剩余 P1/P2。生产 0004 随后已
独立授权并完成：恢复点为 `data/backups/webui-before-cv10-rebaseline-20260825-235401.db`，
SHA-256 为 `106bb38f50d523e383e36c0a549188fa389b53265a018152b55f905e9fe35a68`；73 张既有业务表、
10,197 行逻辑摘要在恢复点/迁移后/重放后一致，完整性、外键和服务恢复通过。当前未执行真实
Provider 或发布。工程证据：
`docs/plans/2026-08-25-legacy-candidate-rebaseline-implementation-report.md`；生产迁移证据：
`docs/plans/2026-08-25-legacy-candidate-rebaseline-production-migration-report.md`。
迁移并恢复服务后的只读 Offer 为 `eligible=true / legacy_rebaseline / blockers=[]`，目标 Ruleset 为
`891b0a5874681f14839d3b322a62f10602486a799db92a688973f811550fa88d`。

Gate B 已按 Owner 授权真实执行并达到 `LIVE_REVERIFIED`：唯一 Attempt
`verification_4b4f150b993422fc41ff2dc58b93a915` 为 `passed`，四项验证门全过，重新确认 6 条来源证据；
Qwen 只调用 1 次，Usage 为 input 421 / output 1,869 / total 2,290 tokens，Grant 已撤销。Pi、
revision、Run、CandidateSet 和 Candidate SHA 均未改写，正式 Delivery 与发布意图仍为 0。真实执行
证据：`docs/plans/2026-08-26-legacy-candidate-rebaseline-live-execution-report.md`。

Gate C 已按 Owner 独立授权发布：正式 Delivery `delivery_84956666b2f34ed7` 为 `succeeded`；CSV
`output_184a1dd3ece24095` 与 JSON `output_48e9010d0bf74d82` 均通过非空、SHA-256 与重开 QA，
无警告，CSV 为 2 行。当前只差 Owner 对该正式结果给出 `LIVE_ACCEPTED` 或整改意见；GitHub Issue
更新/关闭仍未授权。

TaskOwner 随后明确回复“同意”，#70 达到 `LIVE_ACCEPTED`。现场只读核对 GitHub 后，#70 三个人工
门与 #54 十个子任务及真实闭环的完成条件均已满足；两张 Issue 仍为 OPEN，远端评论/关闭尚未授权。

#70 的两个独立人工门已完成：

- Gate B：Owner 已对该 VerificationAttempt 的真实 Provider 外发单独授权并执行；
- Gate C：Owner 已检查 passed 结果、单独授权正式发布并给出 `LIVE_ACCEPTED`。

`liyi` 的一次语义重试授权绝不能用于 `liyi111` 的完整再基线或正式发布。

## 6. 精确下一步计划

### A. 处理 `liyi` failed 结果

1. 保留 failed Attempt、authority、Usage 和撤销 Grant，不自动重试、不发布；
2. 如果用户希望得到合格 CSV，先由用户确认是否创建新 revision 并修改目标/抽取规则；这会形成新
   Candidate，不属于本次历史重验授权；
3. 新 revision 若涉及 Provider，重新展示并确认该次外发和费用；不能复用本次一次性授权。

### B. 完成 #54 / #70

1. 已完成：规格、任务拆分、LR-01～LR-04 TDD 实现、相称回归、生产副本迁移演练和双轴审查；
2. 已完成：生产 CandidateVerification 0004 独立授权迁移、唯一恢复点、前向、重放、完整性、
   外键、既有业务逻辑零改写和服务恢复；
3. 已完成：Gate B 精确 Owner/CandidateSet/Ruleset/连接/模型授权，以及唯一真实 Attempt；
4. 已完成：Attempt `passed`，Usage 已记录，Grant 已撤销，无自动重试；
5. 已完成：Gate C 正式发布与 Owner `LIVE_ACCEPTED`；
6. 下一门：更新 #70/#54 证据并关闭；这是 GitHub 远端写操作，执行前仍需用户明确授权。

### C. 按依赖完成剩余 P0 Issues

1. #55：把 Python 3.13 快速测试、前端类型/构建、依赖一致性、Secret 扫描、迁移 dry-run、
   UTF-8 检查固化为 CI；真实 Provider 与重门不进入 PR Secret；
2. #56：建立唯一显式迁移注册表、版本、备份、重放、恢复和并发锁；应用启动只验 Schema；
3. #57：配置中心 Secret 迁入现有 Vault/SecretRef；是否轮换或销毁旧 Secret 必须另行确认；
4. #58：拆分 runtime/dev/GPU/evaluation/可选依赖，处理 Critical/High 或形成可达性风险结论；
5. #59：经独立授权配置 `main` 分支保护并用测试 PR 验证；禁止静默绕过；
6. #60：最终精简 `docs/status/current.md` 和本交接，核对 README、AGENTS 与现场；
7. 全部 Issue 的工程、真实验收和远端关闭证据齐备后，才把本会话目标标记完成。

P0-02 与 P0-03 理论上可并行，但默认串行；未经用户确认不要扩大并行写面或共享未冻结的迁移/
依赖变更。

## 7. 总体 Roadmap 与版本计划

### 当前 P0：产品真实性与生产安全基线

目标是让普通用户从统一入口稳定完成正式 Delivery，并建立 CI、显式数据库迁移、SecretRef、
依赖治理、主分支保护和可信状态台账。当前冻结的 8 个 Open Issues 就是 P0 剩余范围。

公开版本边界：`v0.0.4` 是稳定封板标签，不得移动或回写；当前 `main` 承接 `v0.0.8` 开发能力，
但没有创建同名标签、Release 或封板。P0 全部完成不自动授权版本号、标签或 Release；版本发布
必须另行确认。

### P1：统一产品主流程

- P1-01：文件、网页、HTTP、数据库统一 Source → TaskRevision → Delivery；
- P1-02：深化 Semantic Workspace，收窄路由、生命周期、Repository 和前端状态机接口；
- P1-03：生产可观测性、SLO、告警与认证安全加固；
- P1-04：在受众、配额、成本、外发、审计和回滚闭环下开放平台能力给普通用户；
- P1-05：组件测试、包体预算、按需加载、性能和无障碍治理。

P1 是 P0 完成后的路线，不属于当前冻结的 GitHub Issue 完成范围；要启动必须重新规格化和创建/
确认工单。

### P2：条件型扩展

只有真实触发条件出现后才启动：目标 Linux/GPU 服务器验收、远程 MCP/Registry/SecretRef、
多媒体、多节点/分布式队列、对象存储/PostgreSQL、SQLite/TSV 正式输出。不要为了预期扩容提前
引入分布式复杂度。

### 顶层 Phase 公共仓库同步门

P0、P1 等顶层 Phase 完成时必须检查 README、Code of Conduct、Contributing、MIT License、
Security 和 GitHub About。稳定法律/治理/安全文件无语义变化时记录“已检查、无需变化”，不要
为了制造差异改写。提交、推送、About、标签和 Release 仍是独立远端授权门。

## 8. 工作树所有权与授权边界

当前工作树是脏的，至少包含：

- 本会话的历史任务读取/语义重试代码、测试、规格和状态文档；
- 用户持有的 G1 评测文件修改；
- `.scratch/`、`frontend/premium-audit.json` 等本地或审计内容。

不得 `git reset --hard`、`git clean`、广泛 checkout、`git add .` 或覆盖不明改动。提交时必须使用
明确文件允许列表；但当前没有提交、推送、PR、Issue 写入、标签、Release 或部署授权。

以下动作始终由用户控制：业务范围、数据含义、权限与安全边界、真实数据外发、Provider 费用、
生产迁移、Secret 使用/轮换/销毁、正式发布、Git/GitHub 远端写入和不可逆操作。

缺少工具或依赖时，先说明工具名称、版本、用途、收益、风险和数据外发，再询问是否安装；不得
静默改走明显更低效或降低验证质量的路线。已安装工具的既有授权不能自动扩展到新工具。

## 9. 绝对不要再踩的坑

1. **不要混淆两个目标。** `liyi` 是 inconclusive 语义重试；`liyi111` 是 failed legacy 再基线。
2. **不要把 Candidate 当 Delivery。** 页面看起来正确、CSV 可下载或 Verifier passed 都不等于
   正式发布。
3. **不要绕过权威门。** 缺 RuntimeAssignment、旧 Ruleset 身份或冻结字段时必须失败关闭；禁止
   人工改库、临时白名单、目标特判或把当前状态倒填成历史事实。
4. **不要复用旧外发授权。** 只可使用用户对精确 Owner/Task/Attempt 给出的授权；未知结果不
   自动重试。
5. **不要走旧重试自动发布路径。** 旧 API 已退役；重验与发布必须是两个动作。
6. **不要在业务校验前写 started/running。** 先完成资格、身份、哈希、P0、连接和报告校验，再
   原子认领 Attempt。
7. **不要破坏不可变终态。** 测试需要伪造损坏读取时 monkeypatch Repository 模型，不得 UPDATE
   终态 Attempt 或放宽数据库 Trigger。
8. **不要把测试绿色冒充更高证据。** `ENGINEERING_VERIFIED`、`LIVE_REVERIFIED`、
   `LIVE_ACCEPTED`、`RELEASED` 分开记录。
9. **不要信任旧 SHA、Issue、服务或数据库快照。** 每次开工重新查询；GitHub #70 现有评论仍
   写着“迁移未执行”，已被 Gate A 现场事实取代，远端评论尚未更新。
10. **不要只相信停止脚本的成功文案。** 外层 Backend Supervisor 可能继续拉起 8088；必须复核
    端口、精确进程树和数据库连续静止性，再迁移。
11. **迁移探针必须跟随真实 Schema 契约。** 0004 使用结构化 JSON + 哈希两列，不是九个拆分列；
    探针预期错误不能冒充生产迁移失败。
10. **不要顺手重构或清旧债。** Frontend Premium 26 项、停止脚本端口枚举缺口和用户评测文件
    都不是当前语义重试切片范围。
11. **不要把 8088 第一次探测失败当最终失败。** 重依赖加载期间监督进程可能尚未监听；要检查
    进程日志、最终 readiness 和端口归属。
12. **不要用旧恢复点覆盖后续生产数据。** 迁移失败只能按当次冻结恢复策略处理；恢复点保留，
    不得擅自清理。
13. **不要改写历史 ADR/报告结论。** 决策变化用新 ADR 表达替代关系，滚动状态只进 current/
    handoff。
14. **不要重试 `liyi` 的 failed Attempt。** 这不是未知结果；一次性授权已消耗。若要修正 CSV，
    必须走新 revision、新 Candidate 和新的 Provider 授权。

## 10. 验证和关闭标准

每个 Issue 至少经过：现场重验 → 冻结范围/非范围/DoD → TDD → 最小实现 → 定向与相称完整回归
→ Standards/Spec 双轴审查 → 必要的真实入口/Provider/数据库/浏览器验收 → 状态和 GitHub 证据
收口。

证据等级必须分开：

- `IMPLEMENTED`：代码存在；
- `ENGINEERING_VERIFIED`：自动测试和工程探针通过；
- `LIVE_REVERIFIED`：真实 Candidate 的新 Attempt 确定性收口并经 Owner 检查；
- `LIVE_ACCEPTED`：Owner 验收真实正式 Delivery；
- `RELEASED`：经独立授权完成提交/合并/标签/Release/部署中的相应动作。

只有本交接第 1 节冻结的 8 个 Open Issues 全部达到各自 DoD、必要人工门完成、远端状态经授权
收口且没有遗留 P0 阻断，本会话目标才完成。不要因为 token、时间、测试绿色或局部工单结束而
提前宣布整个目标完成。

## 11. 关键证据入口

- 当前台账：`docs/status/current.md`
- 产品化路线：`docs/plans/2026-08-23-post-issues-productization-roadmap.md`
- P0-01 规格：`docs/plans/2026-08-24-p0-01-same-run-candidate-reverification-spec.md`
- P0-01 工单拆分：`docs/plans/2026-08-24-p0-01-same-run-candidate-reverification-task-breakdown.md`
- Candidate 重验 ADR：`docs/adr/0033-candidate-reverification-and-verifier-ruleset.md`
- CV-09 工程门：`docs/plans/2026-08-25-cv-09-engineering-gate-report.md`
- CV-10 Gate A：`docs/plans/2026-08-25-cv-10-gate-a-production-migration-report.md`
- legacy 阻断诊断：`docs/plans/2026-08-25-cv-10-legacy-unversioned-diagnosis.md`
- legacy 再基线规格：`docs/plans/2026-08-25-legacy-candidate-rebaseline-spec.md`
- legacy 再基线实施证据：
  `docs/plans/2026-08-25-legacy-candidate-rebaseline-implementation-report.md`
- 历史任务读取恢复：`docs/plans/2026-08-25-history-task-restoration-report.md`
- 历史 inconclusive 诊断：`docs/plans/2026-08-25-legacy-inconclusive-retry-diagnosis.md`
- 历史语义重试规格：`docs/plans/2026-08-25-historical-inconclusive-semantic-retry-spec.md`
- 历史语义重试实现报告：
  `docs/plans/2026-08-25-historical-inconclusive-semantic-retry-implementation-report.md`
- 历史候选重验权威恢复规格：
  `docs/plans/2026-08-25-historical-reverification-authority-recovery-spec.md`
- 历史候选重验权威恢复实现报告：
  `docs/plans/2026-08-25-historical-reverification-authority-recovery-implementation-report.md`

## 12. 给下一个会话的第一条执行指令

先不要调用 Provider、发布或写 GitHub。保留全部工作树改动，读取 legacy 再基线实施报告并现场
复核 Git、生产 Schema/Attempt/Grant/Delivery。`liyi` 的真实 Attempt 已确定失败且授权已消耗，
不得重试或发布。当前第一项人工门是：是否授权只对生产 `data/webui.db` 执行 CandidateVerification
0004 显式迁移；这不授权 `liyi111` Provider、真实 Attempt 或正式发布。
