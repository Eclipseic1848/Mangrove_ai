# Mangrove 零上下文交接

> 状态：`P0_CLOSEOUT_FINAL_GATE`
>
> 起草日期：2026-08-26
>
> 适用仓库：`Eclipseic1848/Mangrove_ai`
> 说明：本文件写给完全没有上下文的新会话；会随 #60 文档收口进入 `main`。文档自身未来
> merge SHA 与 Issue 关闭状态以 GitHub 现场读取为准，不保留会在合并瞬间过期的占位符。

## 1. 当前任务

本会话要完成目标启动时冻结的五张 GitHub Open Issue：#56～#60。后续新建 Issue 不自动并入。

```text
#55（已关闭）
├─ #56 显式数据库迁移 ─ #57 SecretRef 统一
└─ #58 依赖与漏洞治理 ─ #59 main 保护
                              └─ #60 状态、公共入口与交接收口
```

| Issue | 完成定义 | 最终状态 |
| --- | --- | --- |
| #56 | 中央显式迁移、失败关闭、备份/恢复、生产库副本演练、工程与双轴审查、远端收口 | `CLOSED / ENGINEERING_VERIFIED`；PR #76、`main@453e9008`、CI 33039608828 |
| #57 | 25 个配置 Secret 迁为 Owner/键绑定 SecretRef；真实生产副本、Vault key、备份/WAL/journal 扫描；工程与双轴审查、远端收口 | `CLOSED / COPY_REHEARSED`；副本读取/重放/扫描/清理完成，生产原库与 key 未改写 |
| #58 | 五组依赖、干净解析/安装、生产镜像、Python/Node 漏洞处置、限期风险、工程与双轴审查、远端收口 | `CLOSED / ENGINEERING_VERIFIED`；最终 CI 修复已纳入 PR #76，合并后 CI 三项全绿 |
| #59 | `main` 强制 PR、三项 CI、讨论解决、禁止强推、无常驻 bypass，并用真实测试 PR 验证 | `CLOSED / REMOTE_ENFORCED`；用户确认单维护者审批数 0，Ruleset `21624053`，PR #78 验收完成 |
| #60 | 精简权威状态、同步公共入口、逐项完成审计、最终 `handoff.md` | 公共/权威文档已完成；以本文件所在 PR 与 GitHub Issue #60 的现场状态为最终远端证据 |

接手时先核对的远端事实：

- #56～#58 当前合入 `main` SHA：`453e900837109be18da47985db997e58863fbf30`
- 当前文档收口分支：`codex/p0-06-closeout`；编制基线 `origin/main` 为
  `453e900837109be18da47985db997e58863fbf30`。本文件进入 `main` 后必须重新 fetch，不能把
  编制基线当成最终公开 SHA
- 本地名为 `main` 的分支仍为 `4e8e5f9c878002d9781dca622bafe7cd035ddb66`，落后
  `origin/main` 5 个提交；接手时必须现场核验，不得把本地分支名当成公开主线事实
- #56/#57/#58/#59：均为 CLOSED；#60 以 GitHub Issue 现场读取为准
- #56～#58 工程 PR：[PR #76](https://github.com/Eclipseic1848/Mangrove_ai/pull/76)，状态 MERGED
- #59 Ruleset：[`main-required-ci-single-maintainer`](https://github.com/Eclipseic1848/Mangrove_ai/rules/21624053)，ID `21624053`，active
- #59 测试 PR：[PR #78](https://github.com/Eclipseic1848/Mangrove_ai/pull/78)，CLOSED、未合并、探针分支已删除
- #56～#58 合并后 CI：[运行 33039608828](https://github.com/Eclipseic1848/Mangrove_ai/actions/runs/33039608828)，`backend-fast`、`frontend-build`、`secret-scan` 全绿
- #59 失败门：[运行 33040744184](https://github.com/Eclipseic1848/Mangrove_ai/actions/runs/33040744184)；成功门：[运行 33040840714](https://github.com/Eclipseic1848/Mangrove_ai/actions/runs/33040840714)

## 2. 项目和不可变产品边界

Mangrove 是统一数据任务平台：用户提交来源和自然语言目标，系统冻结 TaskRevision、Owner、来源、模型连接、外发确认与能力身份，执行生成 Candidate，独立 Verifier 验证，最后由 Publisher 形成正式 Delivery。

- `8088` 是统一产品入口；`5173` 仅是前端开发入口。
- `/data-prep` 是当前主工作台；历史任务和 Legacy Delivery 在正式迁移完成前继续兼容读取。
- Candidate、验证通过或 `eligible_for_delivery` 都不是正式交付；只有 `delivery_published` 且完整性/QA 通过的 `output_id` 才是 Delivery。
- Mangrove 自有 Schema 只允许经 `src/database_migrations` 显式迁移；Repository/startup 只验版本并失败关闭。LangGraph checkpoint 与用户连接器库不在该体系内。
- `runtime_config` 的 25 个 `secret=True` 键只在业务表保存 Owner/配置键绑定的 SecretRef；原值由共享 Vault 密文边界持有。
- 生产镜像只安装 runtime + collectors；dev、evaluation、gpu 是独立 overlay。GPU overlay 当前有意为空，不得无真实进程内 GPU workload 证据加入 CUDA/Triton。
- 普通用户、管理员、超级管理员是产品角色；“高级用户”不是权限角色。

## 3. 已完成并有证据的工作

### 3.1 前置依赖

- #54、#55、#70 已完成并关闭；#55 的最小 CI 固定 `backend-fast`、`frontend-build`、`secret-scan` 三项检查。
- G1～G4 和 AC-07 的既有结果继续由 `docs/status/current.md` 与历史报告负责；本交接不再复制逐次 Attempt、Token、恢复点和阶段流水，以免历史快照冒充当前状态。

### 3.2 #56 显式数据库迁移

已验证事实：

- `src/database_migrations` 是 Mangrove 自有 Schema 的唯一写入口，具有 profile、不可变 revision/manifest、状态/计划/应用/验证、唯一备份、receipt、恢复和并发锁。
- Repository 与服务启动不再静默建表或 `ALTER`；旧/超前/损坏 Schema 失败关闭。
- 生产 `data/webui.db` 的只读副本演练已完成：原库 SHA、大小和 mtime 未变化；副本完成 `webui_0001..0003`，74 张既有表、10,216 行无意外改写；重放零 revision；备份恢复逐字节一致；旧 Schema 启动被拒绝。
- 演练首次暴露合法 `runtime_rollout_state.mode='vnext_default'` 未纳入 revision 契约，已通过 TDD 将冻结权威枚举修正，不能再用错误的 `gray/all` 集合。

最终收口：Standards/Spec 终审无剩余 P1/P2；PR #76 已合入
`main@453e900837109be18da47985db997e58863fbf30`，合并后 CI 33039608828 三项全绿，#56 已关闭。

### 3.3 #57 SecretRef 工程实现

已验证事实：

- 冻结 25 个 Registry Secret 键；业务表只保存 `secretref:runtime-config:<uuid>`，Ref 同时绑定 Owner scope 与配置键。
- 新配置密文和模型连接共享同一个 key 与 `FernetCredentialVault` 边界，但不共享 Provider 密文表、删除或 Grant 生命周期。
- `webui_0004` 原子迁移旧明文；缺/坏 key、坏密文、跨 Owner/跨键、未知 Ref、部分 Schema、并发或中断均失败关闭并回滚。
- 更新/删除先验证旧 Ref 和 Vault，单事务替换并清理旧密文；API、诊断、Cookie 健康与后台异常均有脱敏门。
- 工程回归与生产副本后的 Standards/Spec 双轴终审均为 0 P1 / 0 P2。
- 经单独授权完成高敏生产副本演练：8088 无监听且源库静止；生产库与匹配 key 复制到仅当前
  Windows 用户可访问的受限目录，原生产库 SHA、大小、mtime 与原 key 均未改写。
- 副本从 legacy 依次迁至 `webui_0004`；实际 1 条配置 Secret 形成 1 个 opaque ref 和 1 条密文，
  孤儿为 0；受信读取、恢复点重放、完整性和外键验证通过，且没有输出 Secret、密文、Ref 或 Owner。
- 迁移副本、迁移后新备份和恢复点重放副本明文命中均为 0；当时不存在 WAL/journal/shm，
  所以没有可扫描 sidecar。迁移前恢复点按预期命中 1 项旧明文，仅在受限目录内用于恢复重放。
- 完成证据后已不可恢复地删除受限目录内 11 个临时文件（145,882,653 bytes）并确认目录不存在；
  生产原库与原 key 仍存在。PR #76 已合入 `main@453e9008`，CI 33039608828 全绿，#57 已关闭。

生产原库迁移、旧备份处置、Secret/key 轮换或销毁不是副本演练的隐含授权。

### 3.4 #58 依赖拆分与漏洞治理

已验证事实：

- 依赖已分为 runtime、collectors、dev、evaluation、gpu；五个 Python 3.13 空环境均可解析和安装，`pip check`、深 import smoke 与 `pip-audit==2.10.1` 为 0 个已知漏洞。
- 最终 Linux Phase4B 镜像以 `10001:10001` 非 root 运行，实际镜像 Python 环境 audit 为 0；runtime/collectors、禁入包、无 CUDA/Triton、Chromium、只读 rootfs、断网启动和 readiness 均有真实证据。
- 完整核心回归为 `2165 passed, 7 skipped, 3 deselected`。三个 deselect 分别有独立干净 dev 环境验证、待最终提交 SHA 的 VerifierRuleset 身份门、以及不得改写的用户 G1 冻结集，不能静默计作通过。
- Promptfoo lockfile 由 npm 正常重建并真实 `npm ci`；冻结六案例 6/6、12/12 通过，无模型/HTTP 调用。

Node 限期风险不是“零漏洞”：

| 风险 | 隔离/缓解 | Owner | 最晚复查 | 提前触发 |
| --- | --- | --- | --- | --- |
| React Router 6.30.6：2 Moderate | 保持 CSR BrowserRouter；跳转只来自受跟踪内部常量；不接收外部 redirect/returnTo/to | 前端依赖维护者 | 2026-09-25 | 开始 SSR/hydration、加入用户可控跳转，或上游发布兼容修复 |
| Promptfoo 0.122.1：5 High 传递节点 | 只在隔离评测目录运行固定六案例；`--no-cache --no-share`；不处理不可信 ZIP/图像/模型；不进入生产镜像或最小 CI | 评测工具维护者 | 2026-09-25 | 扩展输入/可达路径、PoC 升正式门、目录进入生产/最小 CI，或上游发布修复 |

到期前必须重跑三个 Node 工作区的 `npm audit --json`；这不是无限期接受。Standards/Spec
终审无剩余 P1/P2；PR #76 已包含 Alembic、Gitleaks 窄误报和 env ignore 等最终 CI 修复并合入
`main@453e9008`，合并后 CI 33039608828 三项全绿，#58 已关闭。

### 3.5 P0 公共入口预审

- 需要按最终能力同步：`README.md`、`CONTRIBUTING.md`、`AGENTS.md`、`CONTEXT.md`、`docs/status/current.md`、最终 `handoff.md`。
- 已检查、当前无需语义变化：`CODE_OF_CONDUCT.md`、MIT `LICENSE`。
- `SECURITY.md` 已按现场事实修正支持范围：公开开发阶段不等于存在同名 Tag、Release 或稳定
  生产版本；该改动仍须随 #60 最终文档 PR 一并验证和收口。
- GitHub About 的描述、topics、homepage 已只读核验；只有最终现场仍一致且确无语义变化时，才记录“已检查、无需变化”。About 属于远端写入，不能由本地文档修改代替。

## 4. #59 远端保护结果与当前剩余边界

仓库只有 `Eclipseic1848` 一个 collaborator，PR 作者不能批准自己的 PR。用户确认没有第二位
真人 reviewer，并明确选择单维护者模式，避免把 `main` 锁死：

- Ruleset `21624053` active，仅命中 `refs/heads/main`；无 bypass，当前用户也不能绕过。
- 强制 PR、讨论解决、strict `backend-fast` / `frontend-build` / `secret-scan`（Integration ID
  `15368`）和 `non_fast_forward`；审批数 0、最后提交他人批准关闭。
- PR #78 首次运行 33040744184 的 `backend-fast` 故意失败，PR 为 `BLOCKED`；修复后运行
  33040840714 三项全绿，PR 为 `CLEAN/MERGEABLE`，没有审批也不会锁死。
- PR #78 未合并，远端/本地探针分支与 `%TEMP%` worktree 已删除，`main@453e9008` 未含探针；
  #59 已关闭。

当前没有 P0 工程卡点。未来真实加入第二位维护者时，应另开权限变更把审批数从 0 提升为 1，
重新验证独立批准；在此之前不得声称当前规则包含人工审批。生产原库迁移、Secret/key 轮换、
目标服务器部署、Tag/Release 和普通用户能力开放仍是独立人工门，不属于本轮完成事实。

## 5. 新会话的精确下一步

1. 读取 GitHub `main`、Open Issues、Ruleset `21624053` 和 `docs/status/current.md`；不要信任本地旧
   `main` 或本文编制基线。
2. P0 #54～#59 已收口，不重复执行迁移副本、Secret 扫描、依赖 clean install 或 Ruleset 探针。
   当前唯一动作是让 #60 文档 PR 三项 CI 全绿、普通合并并关闭 Issue；随后用极小纯状态 PR
   同时把 `docs/status/current.md` 切为 `P0_COMPLETE`、把本交接切为 `P0_COMPLETE_HANDOFF`，
   并勾选最后验收项，不得夹带工程改动。
3. 只有 GitHub #60 已关闭且最终状态 PR 已合入，才能继续产品路线。届时先为 P1-01 的“网页来源”
   纵切片编写产品流程、领域契约和迁移 ADR，
   冻结来源快照、连接版本、Owner、外发确认与能力 digest，再建立/确认单一工单；HTTP 与数据库
   不自动并入。
4. 若用户要生产迁移、部署、发布、提升审批数或开放普通用户能力，先单独确认精确范围、恢复和
   权限边界；不得从 P0 完成自动推断授权。

## 6. Roadmap 与版本计划

### P0：产品真实性与生产安全基线

当前五张冻结 Issue 的共同目标，是把正式 Delivery 主链的 CI、显式 Schema 迁移、SecretRef、依赖安全、`main` 保护和权威状态台账收口。P0 完成不自动等于生产 Release、目标服务器部署验收或普通用户能力市场开放。

版本现场事实：远端当前没有 Tag 或 Release；本地 `v0.0.4` 只保留历史版本语义，不是远端当前
Tag、Release 或本轮发布事实。是否创建新标签、Release 或部署必须重新现场核验并取得独立授权。

### P1：统一产品主流程

- 统一文件、网页、HTTP、数据库的 Source → TaskRevision → Delivery。
- 深化 Semantic Workspace，收窄路由、生命周期、Repository 和前端状态机接口。
- 建立生产可观测性、SLO、告警与认证安全加固。
- 在受众、配额、成本、外发、审计、回滚闭环下逐步开放平台能力。
- 治理组件测试、包体、按需加载、性能与无障碍。

P1 不属于当前冻结范围。第一项 P1-01 的精确开工条件是：#60 已关闭、最终状态 PR 已合入并
现场复核 P0 基线；
先形成产品流程、领域契约和迁移 ADR，冻结来源快照、连接版本、Owner、外发确认与能力 digest；
再建立/确认只覆盖“网页来源”首个纵切片的工单。HTTP 与数据库切片不得自动并入。

### P2：条件型扩展

只有真实触发后才启动：目标 Linux/GPU 服务器验收、远程 MCP/Registry/SecretRef、多媒体、多节点队列、对象存储/PostgreSQL、SQLite/TSV 正式输出。不要为假设中的未来规模提前引入分布式复杂度。

## 7. 证据等级

- `IMPLEMENTED`：代码存在，尚不能证明行为正确。
- `ENGINEERING_VERIFIED`：自动测试、静态检查、工程探针和双轴审查通过。
- `COPY_REHEARSED`：真实生产源的只读副本完成迁移/恢复/扫描，原源未改写；不等于生产原库迁移。
- `REMOTE_ENFORCED`：远端规则真实生效并经测试 PR 证明；本地配置稿不算。
- `LIVE_REVERIFIED`：真实 Candidate 的新 Attempt 确定性收口。
- `LIVE_ACCEPTED`：Owner 验收真实正式 Delivery。
- `RELEASED`：经独立授权完成相应提交/合并/标签/Release/部署动作。

测试、审查、镜像 audit 或副本演练都不能跨级冒充生产迁移、用户验收、远端保护或 Release。

## 8. 人工控制边界

以下事项始终由用户控制：业务范围、数据含义、角色/权限与安全边界、真实数据/模型外发、Provider 费用、生产原库迁移、恢复覆盖、Secret/key 使用/轮换/销毁、Git/GitHub 写入、协作者邀请、Ruleset、正式发布、标签/Release/部署和其他不可逆操作。

缺工具或依赖时，说明名称、版本、用途、收益、风险与外发后询问；不得静默换成更低效或降低验证质量的路线。一次授权不能扩展到新 Provider、新数据、新 Attempt、生产原库或邻接阶段。

## 9. 工作树所有权与必须保留内容

以下 10 个 tracked G1 文件属于用户，绝不能暂存、覆盖、恢复或删除：

```text
evals/generalization-g1-independent/freeze.json
evals/generalization-g1-independent/heldout_manifest.json
evals/generalization-g1-independent/self-check-report.json
evals/generalization-g1-independent-v2/freeze.json
evals/generalization-g1-independent-v2/heldout_manifest.json
evals/generalization-g1-independent-v2/self-check-report.json
evals/generalization-g1-independent-v3/freeze.json
evals/generalization-g1-independent-v3/heldout_manifest.json
evals/generalization-g1-independent-v3/self-check-report.json
evals/generalization-g1/fixtures.json
```

`.scratch/**`、`.artifacts/**`、`frontend/premium-audit.json`、`data/**`、`logs/**`、`.env*`、数据库/备份/WAL/journal/key/Secret、本机启停脚本均不得提交。只能用正向精确 allowlist；ignore 规则不是所有权保护。

## 10. 绝不能重踩的坑

1. 不要相信旧交接里的 SHA、Issue、服务、数据库或 Ruleset；每次现场重取。
2. 不要让 Repository/startup 隐式 DDL，也不要改写已执行 revision；revision 必须自包含并由内容摘要约束。
3. 迁移真实副本暴露合法值后，应修正权威契约；不能把真实合法数据当坏数据，也不能放宽到未知值。
4. 不要把副本演练说成生产迁移；不要覆盖旧恢复点，也不要擅自处理旧备份或 key。
5. SecretRef 不是凭据或 Provider Grant；业务表、日志、异常、审计和证据都不得出现原值。
6. 不要把 Python/生产镜像 0 漏洞说成仓库 0 漏洞；Node 的 2 Moderate 与 5 High 必须保留到 2026-09-25 或提前触发复查。
7. 不要接受 npm 建议的破坏性降级冒充安全修复；Promptfoo 必须以 clean lock、真实安装和冻结案例证明。
8. 不要把三个 deselect 静默计作通过；尤其不得为本轮提交改写用户 G1 冻结集。
9. 不要在只有一个 collaborator 时强开 1 人审批导致维护死锁；当前经用户确认采用审批数 0 的
   单维护者模式。只有第二位真人实际获得可计入审批的权限后，才可另行提升为 1。
10. 不要设置常驻 bypass，也不要用真实 `main` 强推测试保护规则；测试 PR 不合并探针内容。
11. 不要广泛暂存、清理或 reset 脏工作树；提交前后都要证明 G1、本地审计、数据和 Secret 未进入 index。
12. 不要把本地实现、绿色测试、PR、Issue CLOSED、Ruleset、生产迁移、Release 混为一个证据等级。
13. 不要把旧 `handoff.md` 的长历史流水原样搬回最终交接；历史细节留在 ADR/实施报告，最终交接只保留当前事实、剩余门、下一步、边界、路线和证据入口。

## 11. 权威证据入口

- 当前滚动状态：`docs/status/current.md`
- 工程规则：`AGENTS.md`
- 领域词汇：`CONTEXT.md`
- #55：`docs/plans/2026-08-26-p0-04a-minimum-ci-implementation-report.md`
- #56：`docs/plans/2026-08-26-p0-05-explicit-database-migrations-spec.md`、`docs/plans/2026-08-26-p0-05-migration-tool-research.md`、PR #76、CI 33039608828、已关闭 Issue #56
- #57：`docs/plans/2026-08-26-p0-02-secretref-unification-spec.md`、`docs/plans/2026-08-26-p0-02-secretref-unification-implementation-report.md`、PR #76、CI 33039608828、已关闭 Issue #57
- #58：`docs/plans/2026-08-26-p0-03-dependency-security-evidence.md`
- #59：Ruleset `21624053`、PR #78、CI 33040744184 / 33040840714、已关闭 Issue #59
- #56～#58 远端收口：PR #76、CI 33039608828、Issues #56/#57/#58 CLOSED
- #60：本文件、`docs/status/current.md`、公共入口文档与 GitHub Issue #60

## 12. 最终交接验收清单

- [x] #56～#58 均 CLOSED，且 PR #76、`main@453e9008`、CI 33039608828 和对应实施证据可复核。
- [x] #57 副本演练完成；生产原库/key 未改写；11 个临时高敏文件及目录已清理。
- [x] #58 最终双轴审查通过；Node 限期风险仍准确且有 Owner/截止/触发条件。
- [x] 所有临时远端证据占位符已删除；动态 SHA/Issue 状态改为现场读取，避免自引用过期。
- [x] #59 CLOSED；Ruleset active、无 bypass、三项 strict CI、审批数 0、禁止强推；PR #78 已证明并安全清理。
- [x] `README.md`、`CONTRIBUTING.md`、`AGENTS.md`、`CONTEXT.md`、`SECURITY.md`、`docs/status/current.md` 与本交接一致。
- [x] Code of Conduct、MIT License、GitHub About 已检查且无需变化；Security 的开发版本表述已同步。
- [x] 用户 G1、本地审计、生产数据、数据库、Secret/key 与本机路径未进入精确文档 allowlist。
- [x] 未创建未经授权的标签、Release、部署或普通用户能力开放声明。
- [ ] 本文档收口 PR 的三项 CI 全绿并普通合并；随后在 GitHub #60 写入最终 `main` SHA/CI 并关闭。

最后一项由当前会话在本文档 PR 合并后立即完成；新会话如果看到它仍未勾选，只需先现场读取
GitHub #60，不能重新执行已完成的 P0 工程工作。
