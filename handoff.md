# Mangrove 零上下文接手说明

> 状态：active
>
> 最后现场核验：2026-08-25
>
> 公开主线：`main`
>
> 核验实现 HEAD：`7c2a25ce6e87cc001124b8b81d5715283d5876fc`（文档同步提交以现场 HEAD 为准）
>
> 当前阶段：Issues 清零后的产品化与生产安全收敛
>
> 唯一下一任务：P0-01/CV-10「生产迁移、真实同 Run 重验与 Owner 验收」

## 0. 2026-08-25 CV-09 工程恢复点与当前门禁（优先于下文旧现场值）

> 实现 HEAD：`7c2a25ce6e87cc001124b8b81d5715283d5876fc`
>
> 分支：`main`；`origin/main=f7aa895ed2af23786c5c6c47856824d1146957b3`；本地提交尚未推送
>
> 恢复进度：规格达到 SPEC_APPROVED，任务拆分达到 TICKETS_PUBLISHED；CV-01 已完成，
> CV-02 已达到 ENGINEERING_VERIFIED，CV-03/CV-04 已获用户接受，CV-05～CV-08 已达到
> ENGINEERING_VERIFIED；CV-09 已达到 ENGINEERING_VERIFIED，下一门为 CV-10 人工生产/外发门
>
> GitHub：P0 顶层任务 #54～#60、P0-01/CV-01～CV-10 #61～#70 已创建并反读核验

### 已完成

1. P0-01 默认 vNext 主链实现和本地提交已完成：
   - `2660ac6c73863f72540af8116ff4d614a847c88b`：`feat: complete vNext default task flow`
2. 真实普通用户多格式任务暴露 `artifact_count` Verifier 缺陷；TDD 修复、双轴审查和本地提交
   已完成：
   - `51d327d54aa298ab734f30f106f5405bb12619de`：
     `fix: respect frozen multi-output contracts`
3. 8088 已受控重启并绑定上述提交；当前活动 GateSnapshot 为：
   - `e366bbfc3999917861b2464a3ae2b63886165aa5747074b26c956b3291cfd3c8`
   - `vnext_default`、`p0_blocked=false`、7 项 Gate 通过、无回归项
4. 现有真实 Candidate 保持 `candidate_ready`，未重新验证、未创建新 revision、未发布正式
   Delivery。
5. 已完成只读可行性诊断：现有重验入口只接受 `inconclusive`，当前报告为
   `failed/artifact_count`，因此不能使用当前产品入口同 Run 重验；业务语义也不应为平台
   Verifier 缺陷创建新 revision。
6. CV-01～CV-09 正式能力、全仓回归、迁移演练与双轴审查已完成；实现提交：
   - `3242eb374f48e004c3f1bac594c4f06b412d96b6`：完整 Candidate 重验工作流；
   - `2d80288d6398b0c68d8de556ff867001818387ca`：Runtime Gate 测试接缝；
   - `7c2a25ce6e87cc001124b8b81d5715283d5876fc`：P0 监督与显式 `0002` 迁移安全修复。

### 当前阶段与产物

当前已完成 **CV-01 架构决策**、**CV-02 追加式 Attempt/显式迁移工程实现**、
**CV-03 既有验证入口统一接入**、**CV-04 只读重验 Offer**，并完成 **CV-05 不重跑 Pi 的完整
候选重验**、**CV-06 Provider 重验安全闭环**、**CV-07 精确 Attempt 显式正式发布**和
**CV-08 普通用户重验与发布工作台**，并以 **CV-09 工程门** 完成跨切片回归、迁移演练和
双轴终审。用户明确
要求：不要以放宽条件、一次性脚本或人工改库的“打补丁”方式修复，要设计正式可复用能力。

已批准规格：

- `docs/plans/2026-08-24-p0-01-same-run-candidate-reverification-spec.md`

规格选择正式 `CandidateVerification` Module：以不可变终态的 `VerificationAttempt` 追加记录
同 Run 完整重验，保留旧失败；不重跑 Pi、不生成新 Candidate、不创建 revision；重验通过与
正式发布分成两个动作。六项高风险业务决定均已确认，规格状态为 `SPEC_APPROVED`。

已批准任务拆分：

- `docs/plans/2026-08-24-p0-01-same-run-candidate-reverification-task-breakdown.md`

拆分含 `CV-01`～`CV-10` 十张垂直工单及冻结依赖图；生产迁移、真实 Provider 外发、真实重验
与正式发布均留在人工授权门。用户已确认任务集合与依赖关系；P0-01 父任务为 #54，CV-01～
CV-10 分别为 #61～#70，当前状态为 `TICKETS_PUBLISHED`。

CV-01 已完成并形成：

- `docs/adr/0033-candidate-reverification-and-verifier-ruleset.md`

ADR 冻结了 CandidateVerification 深 Module、VerifierRuleset Manifest、稳定符号级源码身份、
实际执行凭据失败关闭和 Publisher 精确 Attempt 绑定。

CV-02 已形成：

- `src/candidate_verification/` 领域模型、Repository 和显式迁移；
- `tests/test_candidate_verification_migration.py`、
  `tests/test_candidate_verification_repository.py`；
- `docs/plans/2026-08-24-cv-02-verification-attempt-migration-report.md`。

新增测试 27 passed，相邻回归 131 passed；生产库只读副本的 71 张既有表、10,223 行逻辑
指纹零改写，35 条旧报告均按失败关闭导入为 `legacy_unversioned`。真实 `data/webui.db` 未迁移，
CV-02 未接入既有验证主链，也未调用 Provider、重验 Candidate 或发布 Delivery。

CV-03 已形成：

- `src/candidate_verification/service.py`、`ruleset.py` 及 Repository 原子 P0/Attempt 接缝；
- 初始验证和既有语义重试统一经 Module，删除直接 Verifier 后事后补录的第二套真相；
- `tests/test_candidate_verification_service.py`、`test_candidate_verification_ruleset.py` 和
  `test_pi_runtime_workspace_api.py` 回归；
- `docs/plans/2026-08-24-cv-03-candidate-verification-module-report.md`。

CV-03 定向与相邻回归共 127 passed，Python 编译和 `git diff --check` exit 0；第三轮 Standards/
Spec 双轴审查均无阻断问题，用户已接受工程产物。真实生产库未迁移，未调用 Provider，未重验
真实 Candidate，未发布 Delivery，也未执行 Git 或 GitHub 写入。

CV-04 已形成：

- `ReverificationOffer`、稳定 blocker 枚举及严格只读资格门；
- TaskRevision、完整 RuntimeAssignment、来源、Candidate/Manifest/契约、P0、Delivery、
  Provider 连接与规则身份失败关闭检查；
- Task detail latest Attempt、Offer、外发摘要和等待发布投影；
- `tests/test_candidate_reverification_offer.py` 与工作台 API Owner/Provider/只读回归；
- `docs/plans/2026-08-24-cv-04-reverification-offer-report.md`。

CV-04 相邻回归 87 passed，Python 编译、`git diff --check` 和现场 Ruleset 解析均成功；第三轮
Standards/Spec 双轴审查无剩余 P1/P2。真实 Provider、生产库、真实 Candidate、Delivery、Git 和
GitHub 均未写入；用户已接受 CV-04。

CV-05 已形成：

- `POST /api/semantic-workspace/tasks/{task_id}/candidate-verifications` 以 HTTP 202 创建追加式
  requested Attempt，同一幂等键返回同一收据，不同活动请求冲突；
- 同 Run 完整执行 artifact set/count、table contract、source grounding 与 semantic goal，禁止
  Pi start/resume、候选写入、依赖/能力获取和 Publisher；
- requested→running 使用活动 revision/P0/冻结输入原子 CAS；按 Attempt `filelock` 租约避免滚动
  多进程误杀活跃 Worker，并在租约释放后把孤儿 running 收口为 inconclusive；
- CandidateVerification 事件使用确定性 ID、`BEGIN IMMEDIATE` 与 `INSERT OR IGNORE` 原子幂等；
- `tests/test_candidate_reverification_execution.py` 真实覆盖 CSV/JSON/XLSX 与 PDF 来源；八文件
  回归 116 passed；报告为
  `docs/plans/2026-08-24-cv-05-reverification-execution-report.md`。

CV-05 没有迁移生产库、调用真实 Provider、重验真实 Candidate、发布 Delivery 或执行 Git/
GitHub 写入；当前状态 `ENGINEERING_VERIFIED`，不是用户验收或生产资格。

CV-06 已形成：

- 每 Attempt 外发确认和冻结 connection/version/model；Verification/Provider Attempt 先落库再
  签发稳定 Grant，Usage 精确绑定 Attempt/run；
- 超时、响应丢失、运行中取消、进程中断和持久化不确定统一收口 `outcome_unknown`，零自动
  重发；恢复必须再次确认重复费用并创建引用旧 Attempt 的新记录；
- 所有终态撤销 Grant，连接跨 Owner、漂移与权威故障分别失败关闭；
- Provider 专用 11 passed，聚焦回归 97 passed，双轴最终复核无剩余 P1/P2；报告为
  `docs/plans/2026-08-24-cv-06-provider-reverification-report.md`。

CV-06 未调用真实 Provider、未产生真实费用、未迁移生产库或执行 Git/GitHub 写入；当前状态
`ENGINEERING_VERIFIED`，不是 Provider 认证、用户验收或生产资格。

CV-07 已形成：

- 精确 Attempt 显式 publish API、Owner/expected revision/HTTP 幂等键契约；
- Owner 内幂等唯一绑定、同 publication 文件锁、QA 前后资格复核和原子提交 CAS；
- committing 的 rename 前后冻结 staging/final 恢复，Candidate/Attempt 字节零改写；
- 发布专用 8 passed，聚焦回归 131 passed / 1 deselected，双轴最终复核无剩余 P1/P2；报告为
  `docs/plans/2026-08-24-cv-07-explicit-publication-report.md`。

CV-07 未迁移生产库、发布真实 Delivery、执行普通用户浏览器验收或执行 Git/GitHub 写入；当前
状态 `ENGINEERING_VERIFIED`，不是用户验收或生产资格。

### 恢复后的精确下一步

1. 先读本恢复点、ADR-0033、已批准规格、任务拆分和 CV-09 工程门报告；
2. CV-10 先只读核对 HEAD、8088 运行提交、活动 Gate、数据库身份、活动任务/Attempt/Grant；
3. 展示当前一致性备份路径、`0001/0002` DDL、恢复命令及生产 Schema 偏差后，再执行迁移；
4. 真实 Provider 重验前展示 Owner、Candidate/Manifest、连接、模型、外发类别、费用和未知结果
   处理；正式发布必须在 passed 报告检查后作为第二个独立动作；
5. 不得把工程绿色冒充 LIVE_REVERIFIED、LIVE_ACCEPTED、Provider 资格或 Release。

截至 2026-08-24，规格中的范围、权限、逐 Attempt 外发确认、验证/发布分离、规则变化资格和
P0 阻断六项决定均已确认；权威内容见同 Run 重验规格和 ADR-0033，不在交接中复制维护。
任务拆分已达到 `TICKETS_PUBLISHED`，CV-01 已完成，CV-02 达到 `ENGINEERING_VERIFIED`，
CV-03/CV-04 已获用户接受，CV-05～CV-09 达到 `ENGINEERING_VERIFIED`；下一依赖门是 CV-10。

### 暂停边界

- CV-03 已将既有初验和语义重试接入业务 Module，但没有修改真实数据库，没有调用 Provider，
  没有重验真实 Candidate。
- 已按持续目标创建并核验 GitHub #54～#70；已形成上述本地提交，尚未推送、创建 PR、版本
  标签、Release 或部署。
- 生产 CandidateVerification `0001/0002` 尚未执行，但 CV-07 发布幂等空字段/索引已由旧代码
  静默写入，非 NULL 记录为 0；CV-10 必须先备份当前状态并显式接管，禁止静默回滚旧恢复点。
- 工作树中的 G1 评测文件、`frontend/premium-audit.json`、既有 `.scratch/` 和其他用户改动继续
  视为用户持有内容，禁止覆盖、清理或顺带提交。
- 用户新增协作要求：缺少适配工具或依赖时必须先询问，不得自行改走明显更低效或降低验证
  质量的替代路线。用户已授权安装独立 ripgrep 15.2.0 并完成哈希/执行验证；`agent-browser`
  等其他工具仍应按需提请，不得从该授权推断安装。
- 用户新增公共仓库维护要求并已确认：这里的 Phase 指 P0/P1 等顶层里程碑，不是每张 CV
  工单。每个顶层 Phase 完成时都要检查并按实际变化迭代 README、Code of Conduct、
  Contributing、MIT License、Security 和 GitHub About；稳定文件无变化时记录已检查，无需制造
  差异。提交、推送和 About 远端修改继续按 Git/外部发布门单独授权。

## 1. 新会话先做什么

这是一个已经完成大量工程建设、但还没有完成产品化和生产化收敛的数据任务平台。
不要从旧 Issue、旧执行报告或旧分支继续工作。

按以下顺序接手：

1. 读本文件；
2. 读 `AGENTS.md`；
3. 读 `docs/status/current.md`；
4. 读 `CONTEXT.md` 和 `docs/agents/`；
5. 读总路线图：
   `docs/plans/2026-08-23-post-issues-productization-roadmap.md`；
6. P0-01 开工前再读 `docs/adr/0030-direct-vnext-default-cutover.md` 和相关代码/测试；
7. 重新现场核对 Git、GitHub、8088、数据库和工作树。本文中的 SHA、运行态和告警数量会过期。

当前 `docs/status/current.md` 仍夹有较长历史时间线和“#39 可关闭”等历史表述；GitHub 现场
已经没有 Open Issue。历史内容只作证据，新的执行顺序以本文件和上述产品化路线图为入口，
最终仍要在 P0-06 精简状态台账。

## 2. 项目是做什么的

Mangrove 是统一数据任务平台：用户提供文件或其他来源，用自然语言描述目标，系统冻结
TaskRevision、来源、权限、模型连接和外发确认，动态执行任务，独立验证 Candidate，最后只把
通过完整性与 QA 门的结果发布为正式 Delivery。

当前主要入口和能力：

- `8088`：统一产品入口；`5173` 只用于前端开发。
- `/data-prep`：当前主数据工作台，支持上传、不可变 revision、取消、版本、回收站、来源和
  正式结果预览。
- Semantic Harness：计划、检查、绑定、执行、验证、有限修复和 Delivery 发布。
- Agentic Runtime Pi：覆盖感知文档工具、能力调用、Candidate 和独立 Verifier。
- 模型连接：个人/平台连接、DeepSeek、百炼/Qwen、本地或自定义 LAN 模型、Grant、Relay、
  Usage 和 Vault。
- 能力体系：Catalog、Acquisition、Host、OCI/SBOM/签名、晋级、发布、装载和生命周期治理。
- 权限：普通用户、管理员、超级管理员三类角色；Owner 隔离和审计查看边界已实现。
- Runtime Rollout：GateSnapshot、P0 自动回滚、`admin_gray`、`vnext_default` 和人工恢复。

产品目标和统一语言见 `CONTEXT.md`。不要把当前尚未统一的网页、HTTP、数据库入口表述为已经
共享同一生产 Adapter。

## 3. 已经完成了什么

### 3.1 历史工程门

- G1：独立盲集正式运行合格。DeepSeek V4 Flash 功能 30/31（96.8%），安全 5/5；
  PR #41 已合并，#37/#40 已关闭。
- G2：Word/Excel 各 3/3 真实任务通过；AC-05 带备份生产迁移、恢复、并发和真实 Docker
  探针通过；PR #44 已合并，#38/#43 已关闭。
- G3：GateSnapshot、P0 自动回退、RuntimeAssignment 原子冻结、`vnext_default` 切换、
  受控回滚和人工恢复完成。
- G4：平台共享 DeepSeek 与百炼/Qwen 的 Pi→Grant→Relay→Provider→Usage 正式资格、传输
  安全和保留 Vault Key 补偿控制通过，`g4_qualified=true`。
- G5：本机干净 Linux/Compose、非 root/只读根、真实浏览器、并发、超时、进程重启、SQLite
  备份恢复、路径可移植性和零残留工程门通过。
- AC-07：能力治理 #9～#17 全部完成并关闭；真实 Python 表格和 Everything MCP 纵切面已经
  走过验证、供应链、晋级、签名、发布、装载、隔离、恢复和撤销链。

这些结论不等于真实目标 Linux/GPU 服务器已经验收，也不等于产品已发布稳定版本。

### 3.2 本会话刚完成的工作

P0-01 默认 vNext 主链和真实普通用户多格式闭环已在 `2660ac6c`、`51d327d5` 完成；随后针对
平台 Verifier 缺陷，正式实现同 Run Candidate 重验 CV-01～CV-09：

- `3242eb37`：追加式 Attempt、统一 Module、Offer、完整本地/Provider 重验、精确发布与工作台；
- `2d80288d`：保留 Runtime Gate 监督测试接缝；
- `7c2a25ce`：运行期 P0 监督、提交 CAS 和独立显式 `0002` 发布幂等迁移。

最终聚焦后端 196 passed、前端完整 Playwright 64 passed；后端全仓只排除 4 个已在固定基线
干净复现的失败后为 `1999 passed, 7 skipped, 4 deselected`。Standards/Spec 双轴终审无剩余
P1/P2。权威报告为 `docs/plans/2026-08-25-cv-09-engineering-gate-report.md`。

## 4. 当前卡在哪里

工程实现没有剩余 P1/P2；当前唯一阻塞是 CV-10 的生产和真实外发人工门：

- 生产库尚无 CandidateVerification `0001/0002`，但已存在旧 Repository 静默写入的 CV-07
  发布幂等空字段/索引；非 NULL 记录为 0，既有恢复点均不含该 Schema；
- 必须先备份当前生产状态并让显式迁移接管，不能回滚到会丢失后续业务数据的旧恢复点；
- 真实 Candidate 重验涉及 Owner、Provider、模型、外发正文和费用；结果未知不得自动重试；
- passed 只表示可发布，正式 Delivery 发布和 Owner LIVE_ACCEPTED 仍是后续独立动作。

## 5. 精确下一步：P0-01/CV-10

### 5.1 生产迁移门

- 现场核对当前 HEAD、8088 运行提交、Gate、数据库身份、活动任务/Attempt/Grant 和工作树；
- 生成当前一致性恢复点，记录 SHA-256、71 张旧表数据指纹、Schema 与恢复命令；
- 显式执行 `0001_candidate_verification_attempts` 和
  `0002_delivery_publication_idempotency`，验证旧空字段/索引由迁移记录接管；
- 完整性、外键、旧数据零改写、幂等重放和恢复副本均通过后才恢复服务。

### 5.2 真实重验外发门

- Owner 登录后展示冻结 Candidate/Manifest、旧/新 Ruleset、连接版本、模型、外发类别和费用；
- 仅为该 Attempt 获取逐次确认，先落 Attempt/Grant 再外发；
- 核对 Pi 调用次数 0、Run/revision/Candidate 文件哈希不变、旧失败 Attempt 保留；
- `outcome_unknown` 时停止，由 Owner 决定是否承担重复请求和费用。

### 5.3 正式发布与验收门

- Owner 检查 passed VerificationReport 后另行触发精确 Attempt 发布；
- 核对 publication key、QA、Delivery/output、预览/下载、数据库和历史零改写；
- Owner 明确给出 LIVE_ACCEPTED 或整改意见；CV-10 完成后才能关闭 P0-01 #54。

P0-01 当前状态是 `CV-09 ENGINEERING_VERIFIED`，不是 LIVE_REVERIFIED、LIVE_ACCEPTED 或
RELEASED。

## 6. 整体 Roadmap

权威详细版：`docs/plans/2026-08-23-post-issues-productization-roadmap.md`。

### P0：产品真实性和生产安全基线

推荐顺序：

1. **P0-01**：vNext 默认链路与真实普通用户闭环；
2. **P0-04A**：最小 CI 工程门；
3. **P0-05**：显式数据库迁移体系；
4. **P0-02**：配置中心 Secret 统一到 Vault/SecretRef；
5. **P0-03**：依赖拆分和漏洞治理；
6. **P0-04B**：经独立授权后设置主分支保护；
7. **P0-06**：精简 current/handoff/README，收口当前真相。

P0 预计约 3～5 周。P0 完成后，普通用户主入口、Secret、依赖、CI 和数据库演进应具备可持续
迭代的产品基线。

### P1：统一产品主流程

- P1-01：文件、网页、HTTP、数据库统一为 Source Adapter，共享
  Source→TaskRevision→Delivery；
- P1-02：按纵切片深化 Semantic Workspace，收窄 Route/生命周期/Repository/Worker 接口；
- P1-03：结构化日志、指标、SLO、告警、登录限流、Token 撤销、TLS/CSP；
- P1-04：普通用户按受众、配额、成本提示、外发确认和回滚安全使用平台能力；
- P1-05：补前端单元/组件测试、按需加载和包体预算。

P1 预计约 8～14 周。不要进行一次性全量重写；每次只完成一个真实纵切面。

### P2：条件满足后才启动

- 有目标服务器后完成 G5 Linux/GPU、容量、长期运行、灾难恢复和可信 LAN 终端验收；
- 有真实业务对象和信任边界后再做远程 MCP/Registry/SecretRef；
- 有明确样本集和正式输出定义后再做 Phase 4C 多媒体；
- 只有出现多实例、持续 SQLite 写竞争、跨进程恢复或持续高并发时，才引入分布式队列、
  PostgreSQL 或对象存储；
- SQLite/TSV 正式输出只在有用户需求时实现。

## 7. 版本计划

### 7.1 当前已验证事实

- 稳定标签只有 `v0.0.4`，不得移动或回写。
- 当前 `main` 是公开开发基线，不是稳定 Release。
- 没有 `v0.0.8` 标签，也没有 GitHub Release。
- 当前没有获准的 RC、Tag、Release 或部署操作。

### 7.2 建议版本路线，尚待用户确认

| 建议版本 | 进入条件 | 代表含义 |
|---|---|---|
| `v0.1.0-rc.1` | P0 全部工程门完成，完整回归和迁移恢复演练通过 | 首个产品化候选，不代表已发布 |
| `v0.1.0` | RC 真实普通用户验收通过，残余风险接受，取得 Tag/Release 授权 | vNext 默认、Secret、CI、迁移和供应链完成收敛 |
| `v0.2.0` | P1-01/P1-02 完成 | 文件、网页、HTTP、数据库进入统一任务与 Delivery 主流程 |
| `v0.3.0` | P1-03/P1-04/P1-05 完成 | 远程多人使用的可观测性、认证、能力开放和前端质量基线 |
| `v1.0.0` | 真实目标服务器验收、备份灾备、容量与 7～14 天稳定运行完成，并通过发布评审 | 首个明确声明生产可用的稳定版本 |

不要为了补齐历史编号而追补 `v0.0.8` 标签。每个版本都必须绑定准确 Git 提交、数据库迁移
版本、Runtime/GateSnapshot、依赖锁、验收摘要和已知风险；创建 RC、Tag 或 Release 都需要
独立授权。

Phase 4C、远程 MCP 等可按真实产品需求进入独立 minor 版本，不应为了“路线完整”强行阻塞
或塞进 `v1.0.0`。

## 8. 当前主要风险和半成品

- **前端默认运行时错误**：P0-01，当前最高优先级。
- **Secret 明文风险**：配置中心的 Secret 目前可能原样写入 `runtime_config` 数据库表；
  `secret=True` 只是展示遮蔽，不是加密。
- **依赖供应链**：现场有 161 个 Dependabot Open 告警，其中 5 Critical、75 High；告警不
  等于全部可利用，但未使用的 MLflow 等依赖应优先移除并做可达性分析。
- **没有远端门禁**：当前没有 GitHub Actions、Ruleset 或 `main` 分支保护。
- **数据库演进分裂**：`WebUIStore` 仍可能在启动时建表、ALTER 和回填，其他模块使用显式
  迁移；P0-05 要收敛成一条迁移链。
- **产品入口分裂**：`/chat`、`/data-prep` 和 Legacy HTTP/数据库入口尚未共享同一正式
  Delivery 主流程。
- **模块接口过宽**：Store、Semantic Workspace 路由/运行时和大型前端页面职责过多；只在
  P1 按纵切片深化，不做大爆炸重构。
- **运维不足**：缺完整指标、SLO、告警、登录限流和 Token 撤销，更适合本地工程环境，
  不能直接宣称公网多用户生产就绪。
- **目标服务器缺失**：真实 Linux/GPU、容量、长期运行和灾难恢复尚未执行。

## 9. 绝对不要再踩的坑

1. **不要相信静态交接中的分支、SHA、Issue 或运行态。** 每次先现场重查；旧 handoff 曾把
   已关闭 #36/#39 和旧 G3 分支写成下一步。
2. **不要改写或清理用户持有的 G1 冻结文件来让测试变绿。** 身份漂移是应被看见的失败，
   不是要抹平的数据。
3. **不要另建 G2、Gx 或其他副本目录开发。** 所有代码回到当前原仓库，避免两套真相。
4. **不要把测试绿色冒充用户验收、Provider 资格、服务器资格或 Release。** 必须标清
   IMPLEMENTED、ENGINEERING_VERIFIED、LIVE_ACCEPTED、RELEASED。
5. **不要看到失败就判断是模型能力问题。** 先区分模型内容、Provider/网络、Relay、超时、
   工具、Verifier 和测试驱动；百炼 502、模型截断和业务断言失败不是同一种问题。
6. **不要盲目缩短超时。** 长任务可能超过 20 分钟；保留可配置上限和取消能力。超时后如果
   请求可能已外发，状态应为 unknown，由用户决定是否承担重复请求和费用风险。
7. **不要自动重试结果不确定的外部请求。** Attempt 必须先持久化并冻结 Owner、任务、
   Revision、连接和输入摘要；自动重试会产生重复费用和不可审计结果。
8. **不要只修表面用例。** 每项改动都检查空值、重复请求、并发、幂等、权限、Owner 隔离、
   超时、取消、中途异常、恢复、状态不确定和失败关闭。
9. **不要静默更换用户选定模型或 Provider。** 本地模型不满足时，先用证据判断原因；改用
   DeepSeek/百炼仍要说明模型、连接、外发范围和费用。维护者有局域网 Qwen 模型，其地址只
   属于本机配置，绝不能写入仓库。
10. **不要把 Secret、本机绝对路径、局域网地址、Cookie、登录态或原始工具日志写入仓库、
    Issue、报告或命令输出。** DeepSeek/百炼 Key 已由平台配置，不代表可以读取、复制或持久化。
11. **不要用 `git add .`、`git reset --hard`、`git clean`、强推或未经确认的 checkout。**
    工作树有用户数据；提交必须使用文件允许列表。
12. **不要未经授权创建分支、Issue、PR、提交、推送、合并、标签或 Release。** “继续”只授权
    当前本地任务范围，不自动授权远端和发布动作。
13. **不要删除 Legacy、历史任务、既有 Delivery 或不可变审计事件。** 默认切换只影响新
    TaskRevision。
14. **不要启动时静默改生产库。** 任何生产迁移先做 SQLite 在线一致性备份、SHA、完整性、
    逻辑指纹、幂等重放和恢复验证。
15. **不要按端口、模糊进程名或全局 Docker prune 清理。** 只清理能由项目路径、祖先进程、
    Compose project/service 标签或精确资源名证明属于本次运行的资源。
16. **不要把本机 Compose 验收冒充目标服务器验收。** 没有真实 Linux/GPU 环境就明确写
    NOT_RUN。
17. **不要把历史计划和执行报告当当前台账。** ADR/报告保留证据，不回写历史结论；当前
    状态只更新 current 和 handoff。
18. **不要在交付物保留沟通记录、开发过程草稿、一次性探针日志和无用备份。** 只删除本次
    明确产生且可证明无用的临时物；测试源码、生产恢复点和用户文件不能清理。
19. **沟通说本质。** 开始改代码前说明要改什么；开始测试前说明验证目标；不要堆术语，
    不要把需要用户决策的风险藏在实现细节里。

## 10. 当前工作树，必须保护

现场 `git status --short` 有以下 10 个用户持有修改，禁止覆盖、还原、清理或顺带提交：

```text
evals/generalization-g1-independent-v2/freeze.json
evals/generalization-g1-independent-v2/heldout_manifest.json
evals/generalization-g1-independent-v2/self-check-report.json
evals/generalization-g1-independent-v3/freeze.json
evals/generalization-g1-independent-v3/heldout_manifest.json
evals/generalization-g1-independent-v3/self-check-report.json
evals/generalization-g1-independent/freeze.json
evals/generalization-g1-independent/heldout_manifest.json
evals/generalization-g1-independent/self-check-report.json
evals/generalization-g1/fixtures.json
```

CV-09 实现和报告使用精确允许列表提交；工作树仍有上列 10 个 G1 用户修改，以及用户持有的
`.scratch/` 和 `frontend/premium-audit.json`，均未暂存、提交、覆盖或清理。

## 11. 第一次需要用户确认的门

新会话读完并完成现场重验后，应先向用户展示并确认 CV-10 的三个精确门：

> 当前生产备份与 `0001/0002` 迁移范围；真实重验使用的 Owner/Candidate/Provider/模型/外发
> 类别和费用；passed 后是否另行发布正式 Delivery。

生产迁移、真实 Provider 外发、正式 Delivery 发布、Owner LIVE_ACCEPTED 和版本发布仍是不同
结果门；必须分别保留证据，不能由本次通用加速授权互相替代。
