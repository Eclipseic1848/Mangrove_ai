# Mangrove 零上下文接手说明

> 状态：active
>
> 最后现场核验：2026-08-24
>
> 公开主线：`main`
>
> 核验 HEAD：`51d327d54aa298ab734f30f106f5405bb12619de`
>
> 当前阶段：Issues 清零后的产品化与生产安全收敛
>
> 唯一下一任务：P0-01「vNext 默认链路与真实普通用户闭环」

## 0. 2026-08-24 客户端更新恢复点与当前门禁（优先于下文旧现场值）

> 暂停时间：2026-08-24T01:09:18-07:00
>
> 暂停原因：用户更新 Codex 客户端；用户回来前不得自动继续
>
> 现场 HEAD：`51d327d54aa298ab734f30f106f5405bb12619de`
>
> 分支：`main`，比 `origin/main` 领先 2 个本地提交；未推送
>
> 恢复进度：规格达到 SPEC_APPROVED，任务拆分达到 TICKETS_PUBLISHED；CV-01 已完成，
> CV-02 已达到 ENGINEERING_VERIFIED，CV-03/CV-04 已获用户接受，CV-05～CV-08 已达到
> ENGINEERING_VERIFIED；下一工程工单为 CV-09，真实 Provider 外发仍需单独授权
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

### 当前阶段与产物

当前已完成 **CV-01 架构决策**、**CV-02 追加式 Attempt/显式迁移工程实现**、
**CV-03 既有验证入口统一接入**、**CV-04 只读重验 Offer**，并完成 **CV-05 不重跑 Pi 的完整
候选重验**、**CV-06 Provider 重验安全闭环**、**CV-07 精确 Attempt 显式正式发布**和
**CV-08 普通用户重验与发布工作台**。用户明确
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

1. 先读本恢复点、ADR-0033、已批准规格、任务拆分和 CV-07 工程验证报告；
2. CV-09 完成 CV-01～CV-08 的工程门、双轴审查与实施交接，不得把工程绿色冒充生产或用户
   资格；
3. 可以继续本地 TDD 与离线验证，但真实发布、连接、模型、外发输入范围和费用必须先展示并
   取得单独授权；
4. 不得自动迁移生产库、真实外发、写 GitHub 或执行 Git 操作。

截至 2026-08-24，规格中的范围、权限、逐 Attempt 外发确认、验证/发布分离、规则变化资格和
P0 阻断六项决定均已确认；权威内容见同 Run 重验规格和 ADR-0033，不在交接中复制维护。
任务拆分已达到 `TICKETS_PUBLISHED`，CV-01 已完成，CV-02 达到 `ENGINEERING_VERIFIED`，
CV-03/CV-04 已获用户接受，CV-05～CV-08 达到 `ENGINEERING_VERIFIED`；下一工程依赖门是
CV-09。

### 暂停边界

- CV-03 已将既有初验和语义重试接入业务 Module，但没有修改真实数据库，没有调用 Provider，
  没有重验真实 Candidate。
- 已按持续目标创建并核验 GitHub #54～#70；没有创建分支、提交、推送、PR、版本标签、
  Release 或部署。
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

本会话没有改业务代码，完成了两件事：

1. 对代码、目录、README、配置、运行态、数据库、GitHub、安全和文档做了全项目审计；
2. 新增产品化路线图：
   `docs/plans/2026-08-23-post-issues-productization-roadmap.md`。

审计时验证证据：

- `main` 与 `origin/main` 均为 `f7aa895ed2af23786c5c6c47856824d1146957b3`；
- GitHub Open Issues 为 0；
- `/api/health` 与 `/api/readiness` 通过；
- 数据库 Rollout 为 `vnext_default`、`p0_blocked=0`、RuntimeAssignment 数量为 0；
- OpenAPI 有 154 条路径、184 个操作；
- pytest 可收集 1906 项；运行时路由/API 定向测试 53 项通过；前端正式构建通过；
- 本轮没有执行全部 1906 项测试，没有创建真实业务任务，也没有进行新的 Provider 外发。

## 4. 当前卡在哪里

当前没有环境故障型阻塞，卡点是一个必须先修正的真实产品不一致：

> 生产 Rollout 已是 `vnext_default`，但主界面创建任务时仍显式提交 `legacy`。

证据：

- `frontend/src/components/workspace/TaskComposer.tsx` 把运行时初值设为 `legacy`；
- `frontend/src/pages/SemanticWorkspacePage.tsx` 始终发送 `runtime_version`；
- `src/runtime_routing/service.py` 会尊重显式 Legacy；
- ADR-0030 明确要求所有普通用户的新任务默认使用 vNext，同时允许显式选择 Legacy；
- 生产库 RuntimeAssignment 当前为 0，说明切换后还没有一条真实普通用户任务证明默认
  vNext 正式 Delivery 已闭环。

因此，G3 的状态切换和回滚验收是成立的，但“普通用户从当前主界面默认进入 vNext 并完成
正式 Delivery”尚未得到真实证明。不要先做多媒体、远程 MCP 或大规模架构扩展。

## 5. 精确下一步：P0-01

### 5.1 开工前冻结范围

先把 P0-01 细化成单一实施工单，写清 Scope、非 Scope、改动文件、测试矩阵、纯合成外发
数据、拟用 Provider、验收步骤和授权门。是否创建 GitHub Issue、分支、提交或推送，必须
分别取得明确授权，不能从“继续”或本交接文档推断。

### 5.2 最小实现目标

1. 前端改为“平台默认 / 显式 Pi / 显式 Legacy”；未覆盖时不发送 `runtime_version`。
2. API 请求模型不再暴露误导客户端的 Legacy 默认值；服务端 Rollout 是默认决策权威。
3. 界面显示任务最终采用的运行时，并继续允许用户显式选择 Legacy。
4. 保留 P0 自动回退：阻断时默认 Legacy，显式 Pi 失败关闭；恢复必须人工授权。
5. 不删除 Legacy，不迁移历史任务，不扩大平台能力普通用户受众，不借机重构整个工作台。

### 5.3 必须覆盖的测试与验收

- 字段省略、显式 Pi、显式 Legacy、P0 阻断、人工恢复和 Owner 隔离；
- 空值、重复请求、并发创建、锁超时、中途异常、取消、幂等和失败关闭；
- 浏览器从主工作台默认创建任务，确认 RuntimeAssignment 持久化为 Pi；
- 显式 Legacy 仍正常，历史任务和既有 Delivery 零改写；
- 如要真实外发，先向用户展示连接、模型、纯合成输入范围和潜在费用；取得授权后只执行
  一条上传→Pi→Candidate→独立 Verifier→Delivery→预览/下载链；
- Provider 已收到请求但结果未知时不得自动重试，必须把选择交给用户；
- 完成定向回归、与风险相称的完整回归、Standards/Spec 双轴审查和用户验收。

P0-01 当前状态是 `READY_TO_SPEC`，不是 `IMPLEMENTED`。本会话只完成路线图和交接文档。

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

本会话新增/修改但尚未提交：

```text
handoff.md
docs/plans/2026-08-23-post-issues-productization-roadmap.md
```

这两份 Markdown 是本次正式交付内容，不是临时备份。当前没有授权提交或推送。

## 11. 第一次需要用户确认的门

新会话读完并完成现场重验后，应先向用户确认：

> 是否按路线图开始 P0-01 的本地实现与验证；以及是否同时创建 GitHub Issue/分支。

本地代码实现、真实 Provider 外发、生产数据库写入、GitHub 操作、提交推送和版本发布是不同
授权门，必须分别说明后执行。
