# Mangrove 当前状态台账

> status: `P1_01_COMPLETE / OPEN_ISSUES_ZERO`
>
> last_verified: 2026-09-04（P1-01 工程闭环；P0 历史治理证据未重跑）
>
> authoritative_branch: `main`
>
> p0_engineering_baseline: `453e900837109be18da47985db997e58863fbf30`
>
> identity_rule: 公开身份以 GitHub `main` 与本文件所在提交现场读取结果为准

本文件只保留当前能力、有效治理门和下一阶段路线。历史细节以第 8 节列出的不可变规格、ADR 与
执行报告为证据，不再在滚动台账中重复时间线。`ENGINEERING_VERIFIED`、`LIVE_ACCEPTED`、
`REMOTE_ENFORCED` 和 `RELEASED` 是不同证据等级，不能互相替代。

## 0. 当前 P1 状态

- P1-01 的决策、规格、原型与实现工单 #83～#98 已全部关闭；2026-09-04 现场查询 GitHub Open
  Issues 为 0。关闭 #81 只表示 P1 决策地图与首个纵切片完成，不代表 P1-02～P1-05 已实现。
- CoreMind 0.7.1 已通过锁定制品接入。PR #107 合入
  `main@12170eebf1d2f4bad5c86c2a6c38d0bef0a4f998`，Issue #97 已关闭；精确源码、协议、Worker、
  wheel 与 digest 见 [PR #107](https://github.com/Eclipseic1848/Mangrove_ai/pull/107)，架构原型见
  [原型报告](../plans/2026-08-27-p1-coremind-agentkernel-prototype-report.md)。本任务没有修改本机
  CoreMind 仓库，也没有发布制品。
- #98 由 PR #110 合入 `main@5adeacf3aecbd55bd5fe771a35d25a4caa195af3` 并关闭。它新增统一
  收口测试、启动前数据库迁移只读预检与缺失的 PDF 开发测试依赖，并移除固定旧局域网 IP、
  直接访问已被 AgentKernel 取代的 `_pi_runtime` 两条过期测试假设。
- 后端固定种子全量回归为 `2352 passed, 13 skipped, 1 deselected`；唯一 deselect 是被其他任务
  修改且受保护的 G1 freeze 自校验。Issue #98 相关后端组合为 `235 passed, 1 skipped`。
- 前端正式构建通过；完整 Playwright 为 `77 passed`，其中统一数据工作台 40 项，含 light/dark
  axe 检查。固定 Pi/CoreMind 黄金对照为 `1 passed`，覆盖两种 Runtime 的同场景运行中取消、
  无迟到事件与资源清理。
- `start_all.bat --no-pause` 实际返回 0；8088、5173、8080、3002、1200 均监听，8088 与 5173
  返回 HTTP 200，`/api/health` 返回后端健康。脚本继续是本机忽略文件；可提交的只读迁移预检
  位于 `scripts/check_dev_database_migrations.py`。
- UTF-8 检查通过 1249 个文件，`git diff --check` 通过；Standards + Spec 双轴终审均无问题。
  PR #110 的 minimum-ci run `33925622082` 中 `backend-fast`、`frontend-build`、`secret-scan`
  全部成功。
- 上述结论仅为 `ENGINEERING_VERIFIED`。Issue #98 没有执行真实外部网页/Provider 用户旅程、
  新的本机生产迁移、用户验收、部署、Tag、GitHub Release 或包发布；2026-08-26 的历史本机
  生产迁移已完成，本工单未授权的是新增迁移、恢复覆盖、备份处置与 Key/Secret 轮换。
  这些证据等级不得互相替代。
- Agent-Reach 调研继续作为有效候选知识源；不整包安装，不自动启用 OpenCLI/xiaohongshu-mcp。
  认证来源未来仍须按 Owner 隔离 Cookie/SecretRef，明确失效时暂停同一 Run 并由该 Owner 扫码。
- 已清理可再生旧产物：`.scratch/**`、`test-results/**`、`frontend/premium-audit.json`，共约
  118 MB，进入 Windows 回收站；源代码、测试、ADR、规格、正式证据、数据库、备份与他人改动未删。

### 0.1 本轮可复现命令

以下命令均从仓库根目录执行并返回 exit code 0；前端命令先进入 `frontend/`。完整后端仅排除一项
被其他任务修改的 G1 freeze 自校验。

```powershell
.\.artifacts\ci-clean-venv\Scripts\python.exe -X utf8 -m pytest -q tests/test_source_acquisition.py tests/test_source_acquisition_api.py tests/test_web_source_delivery_api.py tests/test_work_trace.py tests/test_agent_kernel.py tests/test_coremind_agent_kernel_adapter.py tests/test_pi_runtime_workspace_api.py tests/test_semantic_workspace_api.py tests/test_database_migrations.py tests/test_dev_database_preflight.py tests/test_issue98_workbench_closeout.py
.\.artifacts\ci-clean-venv\Scripts\python.exe -X utf8 -m pytest -q --randomly-seed=0 --deselect tests/test_g1_independent_runner.py::test_independent_g1_dry_run_verifies_frozen_blind_set
Push-Location frontend
npm run build
npx playwright test --workers=1
Pop-Location
$env:MANGROVE_RUNTIME_ADAPTER_GOLDEN_TEST='1'; $env:PYTHONPATH=(Resolve-Path '.\.venv-coremind-host-verification\Lib\site-packages').Path; .\.artifacts\ci-clean-venv\Scripts\python.exe -X utf8 -m pytest -q tests\test_runtime_adapter_golden.py
.\.artifacts\ci-clean-venv\Scripts\python.exe -X utf8 scripts\ci\check_utf8.py
git diff --check 12170eebf1d2f4bad5c86c2a6c38d0bef0a4f998..HEAD
cmd.exe /d /c start_all.bat --no-pause
```

端口证据：8088、5173、8080、3002、1200 均处于 Listen；8088/5173 HTTP 200；
`http://127.0.0.1:8088/api/health` 返回 `{"ok":true,"service":"mangrove-webui"}`。

## 1. 当前公开身份

- 公开仓库：`Eclipseic1848/Mangrove_ai`；默认分支：`main`；当前访问权限：管理员。
- P1-01 实现基线为 `main@5adeacf3aecbd55bd5fe771a35d25a4caa195af3`，随后只有状态/交接收口；
  当前公开 SHA 按顶部 identity rule 现场查询，不依据本地 `main` 名称判断。P0 #56～#60 的历史
  基线保留在第 4 节。
- 远端当前没有 Tag 或 GitHub Release；本地 `v0.0.4` 只保留历史版本语义，不是远端当前
  Tag、Release 或本轮发布事实。不得把路线图版本名、本地标签或工程验证结果表述为已发布版本。
- GitHub About 当前描述为“面向在线/离线与公域/私域数据的智能数据任务平台”，无主页；Topics
  为 `ai-agent`、`data-engineering`、`data-pipeline`、`document-processing`、`fastapi`、
  `mcp`、`react`。P1-01 收口再次检查 README、Code of Conduct、Contributing、MIT License、
  Security 与 GitHub About，均无需语义变化。
- 当前没有 Open Issues。仍有 Dependabot PR #27、#77、#104、#108、#109；它们不是本轮 Issue
  目标，也未经过依赖与安全评估，后续应逐项分诊，不能因自动生成而直接合并。

## 2. 产品与稳定边界

Mangrove 是统一数据任务平台。用户从文件或其他来源创建不可变 TaskRevision，系统冻结 Owner、
来源、连接版本、外发确认和能力身份，经 Runtime 形成 Candidate，再由独立 Verifier 验证；只有
完整性与 QA 通过并进入 `delivery_published` 的 `output_id` 才是正式 Delivery。

- `8088` 是统一产品入口；`5173` 只用于前端开发。
- `/data-prep` 是统一工作台；按 ADR-0036 不把未上线历史任务/Delivery 的迁移作为新平台完成门，
  但未经授权不删除真实数据，移除旧入口前须证明对应用户能力仍可用。
- Candidate、验证通过、`eligible_for_delivery`、中间 AST 或 Parquet 都不是正式交付。
- 普通用户、管理员、超级管理员是产品角色；“高级用户”不是权限角色。
- TaskRevision、来源快照、Owner、Run、CandidateSet、连接版本、外发确认、Ruleset/能力 digest
  必须冻结并失败关闭。
- Mangrove 自有 Schema 只经 `src/database_migrations` 显式迁移；应用和 Repository 只验证版本。
  LangGraph checkpoint 和用户连接器数据库不属于该迁移体系。
- 25 个 `runtime_config secret=True` 键只允许业务表保存 Owner/配置键绑定的 SecretRef；原值由
  共享 Vault 密文边界持有。本机生产迁移已于 2026-08-26 显式完成；旧备份处置和 Key/Secret
  轮换仍是独立人工门。

## 3. 当前能力矩阵

| 能力 | 当前证据等级 | 当前边界或尚缺 |
|---|---|---|
| 公域采集与 Conductor | 可用 | 企业 API、业务系统、对象存储和统一生产 Adapter 尚未完成 |
| 数据工作台 `/data-prep` | 可用 | 历史任务与 Legacy Delivery 仍保留兼容路径 |
| P1-01 匿名网页统一工作台 | `ENGINEERING_VERIFIED / CLOSED` | 受控工程闭环已验；真实外部网页、Provider 与用户体验未验收 |
| vNext 默认正式 Delivery | `LIVE_ACCEPTED` | 默认/Pi/Legacy、P0 回滚与 Owner 隔离已验收；不代表稳定 Release |
| Candidate 同 Run 重验 | `LIVE_ACCEPTED` | 追加式 Attempt、独立 Provider 授权、精确 Attempt 发布；未知结果不自动重试 |
| 11 种交付预览 | `ENGINEERING_VERIFIED` | 不等于每种格式均有生产用户验收 |
| 覆盖感知文档检索 | 代表任务验证 | 不等于所有文档类型和部署环境资格 |
| 多模型连接与 Provider 安全门 | 工程验证 + 受控真实资格 | 新 Provider、真实业务外发和费用仍逐次授权 |
| Agentic Capability #9～#17 | 已完成并关闭 | admin_gray 治理纵切面完成；普通用户能力市场、远程 MCP/Registry 未开放 |
| G1/G2/G3/G4 与 G5 本机门 | 已取得正式或真实证据 | 目标 Linux/GPU 服务器、长期容量与灾难恢复仍待部署时验收 |
| 显式数据库迁移 #56 | `LOCAL_PRODUCTION_MIGRATED / CLOSED` | 体系已完成；本机 WebUI 后续已显式迁至 `webui_0009`，Scheduler 为 `scheduler_0001`，当前启动修复见第 0 节 |
| SecretRef 统一 #57 | `LOCAL_PRODUCTION_MIGRATED / CLOSED` | 高敏副本演练后，本机生产 `webui_0004` 已真实应用；原 Vault key 保留，未轮换或销毁 |
| 依赖与漏洞治理 #58 | `ENGINEERING_VERIFIED / CLOSED` | 历史固定制品扫描通过；不代表当前仓库零漏洞。新增依赖告警待诊断，Node 限期风险按 2026-09-25 复查 |
| 远端 CI 与主分支保护 | `REMOTE_ENFORCED` | Ruleset `21624053` 强制 PR、strict 三项 CI、讨论解决和禁止强推；单维护者审批数为 0且无 bypass |

## 4. P0 #54～#60 收口矩阵

| Issue | 目标 | 当前事实 | 尚缺的完成证据 |
|---|---|---|---|
| #54 P0-01 | vNext 默认链路与真实普通用户闭环 | `CLOSED / LIVE_ACCEPTED`；实现基线 `20d3a2a9` | 无；保留历史证据，不重复执行 |
| #55 P0-04A | 最小 CI 工程门 | `CLOSED / REMOTE_CI_VERIFIED`；运行 `32947317082` 三项全绿 | 无；required checks 由 #59 落地 |
| #56 P0-05 | 显式数据库迁移体系 | `CLOSED / LOCAL_PRODUCTION_MIGRATED`；PR #76 合入 `453e9008`，副本演练后于 2026-08-26 显式迁移本机生产 WebUI 与 Scheduler | 无；恢复覆盖仍须重新授权 |
| #57 P0-02 | 配置中心 SecretRef 统一 | `CLOSED / LOCAL_PRODUCTION_MIGRATED`；PR #76 合入 `453e9008`，生产 `webui_0004` 已应用，原 key 保留 | 无；Secret/key 轮换和备份处置仍是独立人工门 |
| #58 P0-03 | 依赖拆分与漏洞治理 | `CLOSED / ENGINEERING_VERIFIED`；PR #76 合入 `453e9008`，五组 clean install、真实 Linux 镜像、完整核心回归、最终审查和风险记录完成 | 无；Node 限期风险仍须复查 |
| #59 P0-04B | `main` 分支保护 | `CLOSED / REMOTE_ENFORCED`；用户确认单维护者模式，Ruleset `21624053` active；PR #78 证明失败阻断、修复后可合并且未合并探针 | 无；未来有第二维护者时另开权限变更把审批数从 0 提升为 1 |
| #60 P0-06 | 当前状态与交接收口 | `CLOSED`；PR #79 普通合入 `main@a2f13e3d`，合并后 CI 33041624585 三项全绿；公共/权威文档与 Security 已同步，Code of Conduct、MIT License、About 无需变化 | 无；最终状态只从本文件所在提交和 GitHub #60 现场读取 |

## 5. 生产差异与风险

### 5.1 数据库与 Secret

- 以下为 2026-08-26 P0 历史迁移证据；当前 Schema 与启动修复以第 0 节、0.1 节和 PR #110 为准。
- #56 已在生产 `data/webui.db` 的只读一致性副本上应用 `webui_0001..0003`：原 74 张表、
  10,216 行历史数据无意外改写，重放零 revision 且字节不变，恢复副本与备份逐字节一致；真实
  合法 `vnext_default` 缺口已修复并回归。生产原库 SHA、大小和 mtime 全程未变。
- #57 高敏生产副本演练已完成：生产库与匹配 key 在 8088 无监听、源库静止时复制到受限目录；
  副本从 legacy 迁至 `webui_0004`，实际 1 条配置 Secret 形成 1 个 opaque ref 与 1 条密文，
  孤儿密文为 0，受信读取、恢复点重放、完整性和外键验证通过。
- 迁移副本、迁移后新备份和恢复点重放副本的明文命中均为 0；现场不存在 WAL/journal/shm，
  因而没有可扫描 sidecar。迁移前恢复点按预期命中 1 项旧明文，仅在受限目录用于恢复重放。
  完成后已不可恢复地删除受限目录内 11 个临时文件（145,882,653 bytes）并确认目录不存在；
  演练当时生产原库与原 key 未改写。
- 用户随后于 2026-08-26 单独授权本机生产迁移：`webui.db` 从 legacy 显式迁至
  `webui_0004`，恢复点为 `data/backups/webui-before-startup-20260826-225554.db`（SHA-256
  `3133e3db82f20699b7554d8916a065260e643e0224190e048f2ce9d6858302bc`）；`scheduler.db` 迁至
  `scheduler_0001`，恢复点为 `data/backups/scheduler-before-startup-20260826-225954.db`
  （SHA-256 `477f923f55f43f04ef4a527a4b154ba79ed400ebcda1e615a0b29d2b7fce6a79`）。两库
  `status` 均为 current，相邻 receipt 均已生成；恢复覆盖、旧备份处置和 key 轮换未授权。
- `start_all.bat` 已在本机接入 `scripts/check_dev_database_migrations.py`：服务启动前一次检查
  WebUI 与 Scheduler，任一落后就列全显式迁移命令并失败关闭，不再让监督器隐藏 Schema
  异常。历史迁移回归 49 passed，干净启动曾返回 0；真正 API 健康检查使用 `/api/health`，
  不把 `/health` 的 SPA HTML 200 当作后端健康证明。

### 5.2 依赖与供应链

- 以下扫描只证明 P0 当时的固定依赖/制品，不是当前持续零漏洞声明。
- Python runtime/collectors/dev/evaluation/gpu 五组已从空环境安装，`pip check`、深导入和
  `pip-audit==2.10.1` 通过；最终生产镜像实际 site-packages 为 0 个已知漏洞，GPU overlay
  为空，生产镜像不含评测/GPU 开发依赖。
- 前端 React Router 6.30.6 仍有 2 个 Moderate，当前 CSR/internal-route 接缝没有识别到公告
  可达路径；Promptfoo 0.122.1 隔离评测树仍有 5 个 High 传递节点，冻结六案例不进入相关 ZIP、
  图像或模型路径。两项均不进入无限期接受：Owner 最晚 2026-09-25 复查，触发条件变化立即停门。
- 公共证据只提交可复现摘要；本地 venv、漏洞 JSON、容器审计产物和 Promptfoo 结果不得入 Git。

### 5.3 工作树与提交安全

- 用户持有的 10 个 G1 freeze/fixture 修改和 `docker/phase4b/entrypoint.sh` 不属于本轮提交范围，
  禁止覆盖、清理或暂存。已确认可再生的 `.scratch/**`、`test-results/**` 与
  `frontend/premium-audit.json` 已按用户授权移入回收站。
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
   33039608828 三项全绿。#57 副本演练本身不等于生产迁移；后续生产迁移有单独授权和恢复点，
   但仍不等于 Secret/key 轮换。
2. #59 已按用户确认的单维护者模式完成 Ruleset 与不合并测试 PR 验收并关闭。
3. #60 已同步 `README.md`、`CONTRIBUTING.md`、`AGENTS.md`、`CONTEXT.md`、`SECURITY.md`、
   本台账和 `handoff.md`；`CODE_OF_CONDUCT.md`、MIT `LICENSE`、GitHub About 已检查且无需变化。
4. P0 退出只表示“可持续迭代的产品基线”，不自动产生 Tag、Release、部署或普通用户能力开放。

现场复核时远端无 Tag/Release；本地 `v0.0.4` 仅为历史语义。本轮没有版本号决定、Tag、Release
或部署；这些动作以后仍需重新现场核验和独立授权，不得从 P0 完成自动推断。

### P1 与 P2

- P1-01：匿名网页首片已完成 `Source → TaskRevision → Delivery` 工程闭环；HTTP、数据库和认证
  来源继续作为独立纵切片，未上线历史兼容负担按 ADR-0036 退出。
- P1-02：深化 Semantic Workspace 生命周期模块，收窄路由、持久化和执行接缝，不做全量重写。
- P1-03：生产可观测性、SLO、认证、TLS/CSP 与远程多人使用安全。
- P1-04：在受众、配额、成本、外发、审计和回滚齐备后，分批开放已验证平台能力给普通用户。
- P1-05：组件测试、包体预算、性能与无障碍治理。
- P2 仅在真实条件满足时启动：目标 Linux/GPU 服务器验收、远程 MCP/Registry/SecretRef、
  多媒体、多节点、对象存储/PostgreSQL 或新增正式输出格式。

P1-01 已完成并关闭。下一步应从 P1-02～P1-05 或 HTTP/数据库/认证来源中选择一个明确纵切片，
重新规格化后再实现；不得把本次工程验证自动扩大为整个 P1、真实用户验收或发布完成。

## 8. 历史证据索引

### 当前 P1

- P1-01 规格：`docs/plans/2026-08-27-p1-01-anonymous-web-source-unified-workbench-spec.md`
- Runtime 决策：`docs/adr/0035-unified-data-workbench-and-coremind-runtime-adapter.md`
- 未上线迁移边界：`docs/adr/0036-single-workspace-with-verified-runtime-inheritance.md`
- CoreMind 0.7.1：`docs/plans/2026-08-27-p1-coremind-agentkernel-prototype-report.md`、PR #107。
- 工程收口：PR #110、CI run `33925622082`、已关闭 Issues #98/#83/#81。
- Agent-Reach：`docs/research/2026-08-31-agent-reach-mangrove-assessment.md`

### 历史 P0

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
