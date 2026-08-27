# Mangrove 当前状态台账

> status: `P0_CLOSEOUT_IN_PROGRESS`
>
> last_verified: 2026-08-26
>
> authoritative_branch: `main`
>
> p0_engineering_baseline: `453e900837109be18da47985db997e58863fbf30`
>
> identity_rule: 公开身份以 GitHub `main` 与本文件所在提交现场读取结果为准

本文件只保留当前能力、在制门禁和下一阶段路线。历史细节以第 8 节列出的不可变规格、ADR 与
执行报告为证据，不再在滚动台账中重复时间线。`ENGINEERING_VERIFIED`、`LIVE_ACCEPTED`、
`REMOTE_ENFORCED` 和 `RELEASED` 是不同证据等级，不能互相替代。

## 1. 当前公开身份

- 公开仓库：`Eclipseic1848/Mangrove_ai`；默认分支：`main`；当前访问权限：管理员。
- P0 #56～#58 工程基线为 `main@453e900837109be18da47985db997e58863fbf30`；#59 是
  GitHub Ruleset 远端状态，#60 是本文件所在的文档收口提交。当前检出收口分支为
  `codex/p0-06-closeout`，编制基线与 `origin/main` 均为上述 SHA。
  本地名为 `main` 的分支仍停在 `4e8e5f9c878002d9781dca622bafe7cd035ddb66`，落后远端
  5 个提交；接手者必须重新 `fetch` 并现场读取 GitHub `main`，不得仅凭本地分支名或本文
  编制基线判断当前公开 SHA。
- 远端当前没有 Tag 或 GitHub Release；本地 `v0.0.4` 只保留历史版本语义，不是远端当前
  Tag、Release 或本轮发布事实。不得把路线图版本名、本地标签或工程验证结果表述为已发布版本。
- GitHub About 当前描述为“面向在线/离线与公域/私域数据的智能数据任务平台”，无主页；Topics
  为 `ai-agent`、`data-engineering`、`data-pipeline`、`document-processing`、`fastapi`、
  `mcp`、`react`。P0 公共入口复核确认 About 无需语义变化。
- #56～#58 已由 PR #76 合入工程基线；#59 已完成远端强制；#60 收口只包含公共/权威文档。
  工作树仍有用户持有的 G1 文件改动与本地产物，均不属于 P0 提交。最终公开 SHA 和 Issue 状态
  以 GitHub `main`、PR 与 Issues 现场读取为准，本文不复制会因自身合并而变化的未来 SHA。

## 2. 产品与稳定边界

Mangrove 是统一数据任务平台。用户从文件或其他来源创建不可变 TaskRevision，系统冻结 Owner、
来源、连接版本、外发确认和能力身份，经 Runtime 形成 Candidate，再由独立 Verifier 验证；只有
完整性与 QA 通过并进入 `delivery_published` 的 `output_id` 才是正式 Delivery。

- `8088` 是统一产品入口；`5173` 只用于前端开发。
- `/data-prep` 是当前主工作台；历史任务和 Legacy Delivery 在迁移完成前保持兼容读取。
- Candidate、验证通过、`eligible_for_delivery`、中间 AST 或 Parquet 都不是正式交付。
- 普通用户、管理员、超级管理员是产品角色；“高级用户”不是权限角色。
- TaskRevision、来源快照、Owner、Run、CandidateSet、连接版本、外发确认、Ruleset/能力 digest
  必须冻结并失败关闭。
- Mangrove 自有 Schema 只经 `src/database_migrations` 显式迁移；应用和 Repository 只验证版本。
  LangGraph checkpoint 和用户连接器数据库不属于该迁移体系。
- 25 个 `runtime_config secret=True` 键只允许业务表保存 Owner/配置键绑定的 SecretRef；原值由
  共享 Vault 密文边界持有。生产迁移、旧备份处置和 Key/Secret 轮换仍是独立人工门。

## 3. 当前能力矩阵

| 能力 | 当前证据等级 | 当前边界或尚缺 |
|---|---|---|
| 公域采集与 Conductor | 可用 | 企业 API、业务系统、对象存储和统一生产 Adapter 尚未完成 |
| 数据工作台 `/data-prep` | 可用 | 历史任务与 Legacy Delivery 仍保留兼容路径 |
| vNext 默认正式 Delivery | `LIVE_ACCEPTED` | 默认/Pi/Legacy、P0 回滚与 Owner 隔离已验收；不代表稳定 Release |
| Candidate 同 Run 重验 | `LIVE_ACCEPTED` | 追加式 Attempt、独立 Provider 授权、精确 Attempt 发布；未知结果不自动重试 |
| 11 种交付预览 | `ENGINEERING_VERIFIED` | 不等于每种格式均有生产用户验收 |
| 覆盖感知文档检索 | 代表任务验证 | 不等于所有文档类型和部署环境资格 |
| 多模型连接与 Provider 安全门 | 工程验证 + 受控真实资格 | 新 Provider、真实业务外发和费用仍逐次授权 |
| Agentic Capability #9～#17 | 已完成并关闭 | admin_gray 治理纵切面完成；普通用户能力市场、远程 MCP/Registry 未开放 |
| G1/G2/G3/G4 与 G5 本机门 | 已取得正式或真实证据 | 目标 Linux/GPU 服务器、长期容量与灾难恢复仍待部署时验收 |
| 显式数据库迁移 #56 | `ENGINEERING_VERIFIED / CLOSED` | 生产副本已验；生产原库尚未迁入中央 revision 链 |
| SecretRef 统一 #57 | `COPY_REHEARSED / CLOSED` | 高敏生产副本、匹配 key、恢复重放、明文扫描与清理已验；生产原库未迁移 |
| 依赖与漏洞治理 #58 | `ENGINEERING_VERIFIED / CLOSED` | Python/最终 Linux 镜像 0 已知漏洞；Node 限期风险仍按 2026-09-25 复查 |
| 远端 CI 与主分支保护 | `REMOTE_ENFORCED` | Ruleset `21624053` 强制 PR、strict 三项 CI、讨论解决和禁止强推；单维护者审批数为 0且无 bypass |

## 4. P0 #54～#60 收口矩阵

| Issue | 目标 | 当前事实 | 尚缺的完成证据 |
|---|---|---|---|
| #54 P0-01 | vNext 默认链路与真实普通用户闭环 | `CLOSED / LIVE_ACCEPTED`；实现基线 `20d3a2a9` | 无；保留历史证据，不重复执行 |
| #55 P0-04A | 最小 CI 工程门 | `CLOSED / REMOTE_CI_VERIFIED`；运行 `32947317082` 三项全绿 | 无；required checks 由 #59 落地 |
| #56 P0-05 | 显式数据库迁移体系 | `CLOSED / ENGINEERING_VERIFIED`；PR #76 合入 `453e9008`，生产原库只读副本、幂等/恢复和双轴审查完成 | 无；生产原库迁移仍是独立人工门 |
| #57 P0-02 | 配置中心 SecretRef 统一 | `CLOSED / COPY_REHEARSED`；PR #76 合入 `453e9008`，生产高敏副本、匹配 key、读取/重放/扫描/清理和双轴审查完成 | 无；生产原库迁移及 Secret/key 轮换仍是独立人工门 |
| #58 P0-03 | 依赖拆分与漏洞治理 | `CLOSED / ENGINEERING_VERIFIED`；PR #76 合入 `453e9008`，五组 clean install、真实 Linux 镜像、完整核心回归、最终审查和风险记录完成 | 无；Node 限期风险仍须复查 |
| #59 P0-04B | `main` 分支保护 | `CLOSED / REMOTE_ENFORCED`；用户确认单维护者模式，Ruleset `21624053` active；PR #78 证明失败阻断、修复后可合并且未合并探针 | 无；未来有第二维护者时另开权限变更把审批数从 0 提升为 1 |
| #60 P0-06 | 当前状态与交接收口 | P0 公共/权威文档已精简并经双轴审查；Code of Conduct、MIT License、About 无需变化，Security 已同步 | 本文件进入 `main` 后以 GitHub Issue #60 的关闭评论作为最终远端证据 |

## 5. 生产差异与风险

### 5.1 数据库与 Secret

- #56 已在生产 `data/webui.db` 的只读一致性副本上应用 `webui_0001..0003`：原 74 张表、
  10,216 行历史数据无意外改写，重放零 revision 且字节不变，恢复副本与备份逐字节一致；真实
  合法 `vnext_default` 缺口已修复并回归。生产原库 SHA、大小和 mtime 全程未变。
- #57 高敏生产副本演练已完成：生产库与匹配 key 在 8088 无监听、源库静止时复制到受限目录；
  副本从 legacy 迁至 `webui_0004`，实际 1 条配置 Secret 形成 1 个 opaque ref 与 1 条密文，
  孤儿密文为 0，受信读取、恢复点重放、完整性和外键验证通过。
- 迁移副本、迁移后新备份和恢复点重放副本的明文命中均为 0；现场不存在 WAL/journal/shm，
  因而没有可扫描 sidecar。迁移前恢复点按预期命中 1 项旧明文，仅在受限目录用于恢复重放。
  完成后已不可恢复地删除受限目录内 11 个临时文件（145,882,653 bytes）并确认目录不存在；
  生产原库与原 key 未改写且仍存在。
- 生产原库中央迁移、服务切换与恢复覆盖必须另有明确授权和唯一恢复点；工程测试或副本成功不
  自动授权生产写入。

### 5.2 依赖与供应链

- Python runtime/collectors/dev/evaluation/gpu 五组已从空环境安装，`pip check`、深导入和
  `pip-audit==2.10.1` 通过；最终生产镜像实际 site-packages 为 0 个已知漏洞，GPU overlay
  为空，生产镜像不含评测/GPU 开发依赖。
- 前端 React Router 6.30.6 仍有 2 个 Moderate，当前 CSR/internal-route 接缝没有识别到公告
  可达路径；Promptfoo 0.122.1 隔离评测树仍有 5 个 High 传递节点，冻结六案例不进入相关 ZIP、
  图像或模型路径。两项均不进入无限期接受：Owner 最晚 2026-09-25 复查，触发条件变化立即停门。
- 公共证据只提交可复现摘要；本地 venv、漏洞 JSON、容器审计产物和 Promptfoo 结果不得入 Git。

### 5.3 工作树与提交安全

- 用户持有的 10 个 G1 freeze/fixture 修改、`.scratch/**`、`.artifacts/**` 和
  `frontend/premium-audit.json` 不属于 #56～#60 提交范围，禁止覆盖、清理或暂存。
- 只允许按审计过的文件 allowlist 逐文件暂存；禁止 `git add .`、`git add -A`、
  `git commit -a`、`git reset --hard` 和 `git clean`。
- #56～#58 已按同一可构建边界由 PR #76 合入；当前后续提交仍必须排除用户 G1 与本地产物。

## 6. 远端治理

- GitHub repository Ruleset
  [`main-required-ci-single-maintainer`](https://github.com/Eclipseic1848/Mangrove_ai/rules/21624053)
  （ID `21624053`）为 active，仅命中 `refs/heads/main`，`bypass_actors=[]` 且当前用户不可绕过。
- Ruleset 强制 PR、讨论解决、strict `backend-fast` / `frontend-build` / `secret-scan`（GitHub
  Actions Integration ID `15368`）和 `non_fast_forward`。仓库只有一个 collaborator，经用户
  明确确认采用单维护者模式：审批数为 0，`require_last_push_approval=false`。
- 临时 [PR #78](https://github.com/Eclipseic1848/Mangrove_ai/pull/78) 未合并：运行
  [33040744184](https://github.com/Eclipseic1848/Mangrove_ai/actions/runs/33040744184) 中
  `backend-fast` 失败且 PR 为 `BLOCKED`；修复后的运行
  [33040840714](https://github.com/Eclipseic1848/Mangrove_ai/actions/runs/33040840714) 三项全绿，
  PR 为 `CLEAN/MERGEABLE`。PR、远端/本地分支和临时 worktree 已安全清理，`main` 未含探针。
- #59 已关闭。未来实际加入第二位维护者时，应另开权限变更把审批数提升为 1 并重跑测试 PR；
  在此之前不得声称已有独立人工审批。

## 7. 下一路线与版本门

### P0 退出结果

1. #54～#58 均已关闭；#56～#58 由 PR #76 合入 `main@453e9008`，合并后 CI
   33039608828 三项全绿。#57 副本演练不等于生产原库迁移或 Secret/key 轮换。
2. #59 已按用户确认的单维护者模式完成 Ruleset 与不合并测试 PR 验收并关闭。
3. #60 已同步 `README.md`、`CONTRIBUTING.md`、`AGENTS.md`、`CONTEXT.md`、`SECURITY.md`、
   本台账和 `handoff.md`；`CODE_OF_CONDUCT.md`、MIT `LICENSE`、GitHub About 已检查且无需变化。
4. P0 退出只表示“可持续迭代的产品基线”，不自动产生 Tag、Release、部署或普通用户能力开放。

现场复核时远端无 Tag/Release；本地 `v0.0.4` 仅为历史语义。本轮没有版本号决定、Tag、Release
或部署；这些动作以后仍需重新现场核验和独立授权，不得从 P0 完成自动推断。

### P1 与 P2

- P1-01：按网页 → HTTP → 数据库纵切片统一 `Source → TaskRevision → Delivery`，Legacy 在迁移
  完成前保持兼容。
- P1-02：深化 Semantic Workspace 生命周期模块，收窄路由、持久化和执行接缝，不做全量重写。
- P1-03：生产可观测性、SLO、认证、TLS/CSP 与远程多人使用安全。
- P1-04：在受众、配额、成本、外发、审计和回滚齐备后，分批开放已验证平台能力给普通用户。
- P1-05：组件测试、包体预算、性能与无障碍治理。
- P2 仅在真实条件满足时启动：目标 Linux/GPU 服务器验收、远程 MCP/Registry/SecretRef、
  多媒体、多节点、对象存储/PostgreSQL 或新增正式输出格式。

P1-01 的精确开工条件：#60 已关闭、最终状态 PR 已合入并现场复核 P0 基线；先形成产品流程、
领域契约和迁移 ADR，冻结来源快照、连接版本、Owner、外发确认与能力 digest；随后建立/确认
只覆盖“网页来源”首个纵切片的工单。HTTP 与数据库切片必须分别规格化和验收，不得自动并入首片。

## 8. 历史证据索引

### 当前 P0

- 路线图与 DoD：`docs/plans/2026-08-23-post-issues-productization-roadmap.md`
- P0-01/Candidate：`docs/plans/2026-08-23-p0-01-vnext-default-user-delivery-spec.md`、
  `docs/plans/2026-08-26-legacy-candidate-rebaseline-live-execution-report.md`
- #55：`docs/plans/2026-08-26-p0-04a-minimum-ci-implementation-report.md`
- #56：`docs/plans/2026-08-26-p0-05-explicit-database-migrations-spec.md`、
  `docs/plans/2026-08-26-p0-05-migration-tool-research.md`
- #57：`docs/plans/2026-08-26-p0-02-secretref-unification-spec.md`、
  `docs/plans/2026-08-26-p0-02-secretref-unification-implementation-report.md`
- #58：`docs/plans/2026-08-26-p0-03-dependency-security-evidence.md`
- #59：Ruleset `21624053`、PR #78、CI 33040744184 / 33040840714、已关闭 Issue #59。
- #60：本文件、根目录 `handoff.md`、公共入口文档和 GitHub Issue #60。

### 历史能力与不可变决策

- 领域与当前工程边界：`CONTEXT.md`、`AGENTS.md`
- Candidate 重验：`docs/adr/0033-candidate-reverification-and-verifier-ruleset.md`、
  `docs/adr/0034-historical-reverification-authority.md`
- vNext 默认切换：`docs/adr/0030-direct-vnext-default-cutover.md`、
  `docs/plans/2026-08-23-g3-vnext-cutover-execution-report.md`
- G1：`docs/plans/2026-08-20-g1-generalization-execution-report.md`
- G5 本机门：`docs/plans/2026-08-23-g5-local-prerequisite-execution-report.md`
- AC-07 总规格与生命周期：`docs/plans/2026-08-06-agentic-capability-ac07-spec.md`、
  `docs/adr/0029-capability-validation-lifecycle-and-platform-publication.md`

状态改变时先更新本文件，再同步精简 `handoff.md`。不要把滚动状态复制进 README、CONTEXT、ADR
或历史执行报告；文档自身未来 merge SHA 与 Issue 关闭状态必须从 GitHub 现场读取，不能用静态
占位符制造看似精确但一合并就过期的事实。
