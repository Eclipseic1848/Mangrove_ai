# Mangrove 零上下文交接

> 状态：`P1_01_COMPLETE / OPEN_ISSUES_ZERO`
>
> 最后核验：2026-09-04
>
> 公开仓库：`Eclipseic1848/Mangrove_ai`

本文件写给完全没有上下文的新会话。先读 `AGENTS.md`、`docs/status/current.md`、`CONTEXT.md`，
再现场读取 GitHub、Git 与运行态。历史计划是证据，不是当前状态。

## 1. 我们在做什么

本轮目标是自主完成 GitHub 当时剩余的 Issues，执行相称验证、PR/CI/受保护合并与关闭，然后清理
过期产物并留下零上下文交接。该目标已完成：2026-09-04 现场查询 Open Issues 为 0。

这不是“整个 P1 已完成”。完成的是 P1 决策地图与 P1-01 匿名网页首个纵切片；P1-02～P1-05、
HTTP/数据库/认证来源、真实用户验收与发布仍是后续路线。

## 2. 已完成什么

### GitHub 收口

- #83～#98 已全部关闭；父票 #81 已在全部决策子票完成后关闭。
- CoreMind 0.7.1 薄 Adapter：PR #107 合入
  `main@12170eebf1d2f4bad5c86c2a6c38d0bef0a4f998`，关闭 #97。
- 锁定 CoreMind 身份：源码 `75b706a20ca4cdddef71cbcc0dd90b8b424ddd99`；wheel
  `3fa5301c444da2e3bdaca51bd4800b1bdbcb6dc68e3abef4b39197bde3625e74`；Worker
  `ba4590a68841e520dcd3a91e206ca9e346d10fd9a23b3ed4c560f59707cfa71e`；Protocol 2.0 fingerprint
  `sha256:94c8e093979be73a13ecc1090167454567d0602a70b065ceffeed4cb1eca4ce3`。完整身份见 PR #107。
- P1-01 工程收口：PR #110 合入
  `main@5adeacf3aecbd55bd5fe771a35d25a4caa195af3`，关闭 #98；随后以证据评论关闭 #83 与 #81。
- PR #110 minimum-ci run `33925622082`：`backend-fast`、`frontend-build`、`secret-scan` 全绿。

### 产品与工程结果

- `/data-prep` 保持统一数据工作台：文件/来源、自然语言任务、预览、任务修订、追问、模板/记忆、
  Candidate、正式 Delivery、预览与下载能力继续沿同一生命周期工作。
- 受控匿名网页可幂等形成唯一正式 Delivery；Publisher 重启只恢复发布，不重复抓取、模型调用或
  Delivery；Owner B 无法读取、恢复、取消、重试、验证、发布、预览、下载或复用 Owner A 的事实。
- CoreMind 通过 `AgentKernel` 薄 Adapter 接入；Mangrove 继续拥有 Owner、TaskRevision、模型选择、
  外发确认、Verifier、Publisher 和正式 Delivery 权威。
- 工作记录默认折叠，展示时间、实际行动、工具、模型、Token 与 unknown，不展示逐字思维链、
  Cookie、Secret、系统 Prompt、宿主路径或原始大日志。
- 启动前数据库迁移预检会只读检查 WebUI/Scheduler schema；落后时给出受源 SHA 保护的显式命令并
  失败关闭。`start_all.bat` 继续是本机忽略文件，不进入 Git。
- 已保留 Agent-Reach 调研。结论是吸收渠道目录、后端路由、真实体检与修复处方；具体开源工具按
  精确版本逐个进入现有 Capability/Source 边界，不整包安装，不自动启用 OpenCLI 或
  xiaohongshu-mcp。

### 验证证据

- Issue #98 后端组合：`235 passed, 1 skipped`。
- 后端固定种子全量：`2352 passed, 13 skipped, 1 deselected`。唯一 deselect 是其他任务正在修改的
  G1 freeze 自校验；未覆盖或重建该资产。
- 前端正式构建：exit 0；完整 Playwright：`77 passed`，其中统一数据工作台 40 项并含明暗主题 axe。
- 固定 Pi/CoreMind 0.7.1 golden：`1 passed`，覆盖运行中取消、无迟到事件与资源清理。
- UTF-8：1249 个文件通过；`git diff --check` 通过。
- Standards + Spec 双轴终审：无问题。
- `start_all.bat --no-pause`：exit 0。最后复核时 8088、5173、8080、3002、1200 均监听；8088
  与 5173 返回 HTTP 200；`/api/health` 返回 `{"ok":true,"service":"mangrove-webui"}`。

精确根命令保留在 `docs/status/current.md` 第 0.1 节。本轮结论是 `ENGINEERING_VERIFIED`，不是
真实外部网页/Provider 用户验收、生产资格或发布证明。

### 清理结果

已确认并移入 Windows 回收站：

- `.scratch/**`：旧 P0/P1 草稿、临时数据库副本、CI/PR 载荷与可抛弃原型；
- `test-results/**`：旧 Playwright 失败上下文；
- `frontend/premium-audit.json`：旧审计生成物。

共约 118 MB，可从回收站恢复。没有删除源代码、测试、ADR、规格、正式执行证据、生产数据库、
备份或他人未提交改动。

## 3. 当前卡在哪

没有阻塞，也没有未完成的 GitHub Issue。

以下事项故意不在本轮完成：

- Issue #98 没有调用真实外部网页或真实 Provider，没有新增生产迁移或用户验收；
- 没有部署、Tag、GitHub Release、npm/PyPI 或其他真实外部发布；
- 没有处置历史备份、轮换 Key/Secret 或改变权限；
- 仍有 Dependabot PR #27、#77、#104、#108、#109，尚未逐项做依赖与安全评估。

这些是后续工作或人工门，不是本轮失败。

## 4. 接手后怎么开始

没有需要自动继续的在制 Issue。新会话先运行只读检查：

```powershell
git status --short
git fetch origin main
git rev-parse origin/main
gh issue list --repo Eclipseic1848/Mangrove_ai --state open --limit 100
gh pr list --repo Eclipseic1848/Mangrove_ai --state open --limit 100
```

然后由用户选择下一条明确纵切片或依赖 PR 分诊。不要凭本文件自动进入新阶段、安装工具、迁移数据
或发布版本。

## 5. 必须保护的本地状态

下列未提交改动属于用户或其他任务，不得暂存、覆盖、恢复、清理或据此重建：

- `docker/phase4b/entrypoint.sh`；
- `evals/generalization-g1-independent*/freeze.json`；
- `evals/generalization-g1-independent*/heldout_manifest.json`；
- `evals/generalization-g1-independent*/self-check-report.json`；
- `evals/generalization-g1/fixtures.json`。

本机 CoreMind checkout 由专属任务持有；Mangrove 任务不得编辑、测试、切分支、提交、推送、回滚、
清理或 stash。任何 Git 提交都使用精确文件 allowlist。

## 6. 总体 Roadmap

| 阶段 | 状态 | 目标与边界 |
|---|---|---|
| P0 | 已完成 | 可持续迭代基线、显式迁移、SecretRef、依赖安全、CI 与主分支保护 |
| P1-01 | 已完成工程闭环 | 匿名网页统一工作台、部分结果语义、工作记录、CoreMind Adapter |
| 后续来源纵切片 | 未规格化 | HTTP、数据库、认证网页分别进入同一 Source→TaskRevision→Delivery 生命周期 |
| P1-02 | 未实现 | 深化统一任务生命周期，逐项迁入仍有价值的旧工作区能力，不做全量重写 |
| P1-03 | 未实现 | 认证、可观测性、SLO、TLS/CSP 与远程多人运行 |
| P1-04 | 未实现 | 配额、成本、外发、审计、回滚齐备后分批开放平台能力 |
| P1-05 | 未实现 | 组件测试、包体预算、性能与无障碍治理 |
| P2 | 未启动 | Linux/GPU、远程 MCP/Registry、多媒体、多节点、对象存储/PostgreSQL；须有真实需求和环境 |

小红书等认证来源的既定产品语义：只有明确 Cookie 失效时才暂停同一 Run，并向当前 Owner 展示
二维码；谁扫码，登录态就只保存到谁的 Owner 隔离连接，后续任务复用到下次明确失效。网络或平台
异常只能标记“登录状态未知”，不能误报失效。真实账号、条款、外发和费用仍须用户确认。

## 7. 版本计划

- 远端当前没有 Tag 或 GitHub Release；本地历史版本语义不是公开版本。
- P1-01 完成是工程里程碑，不自动产生版本或发布。
- 下一版本号应在后续 P1 范围、真实验收与发布清单冻结后决定。
- Tag、GitHub Release、npm/PyPI、部署及其他外部发布始终是人工门。
- P1-01 收口已检查 README、Code of Conduct、Contributing、MIT License、Security 与 GitHub
  About；现有内容仍准确，无需为了制造差异而改写。

## 8. 绝对不要再踩的坑

1. 不要创建 worktree 或额外“分支文件夹”；只在现有 checkout 工作。
2. 不要触碰 CoreMind 本机仓库；只使用正式提交、制品 digest、CI 和锁定测试证据。
3. 不要把 Candidate、Verifier 通过、Runtime 成功或 CI 绿色说成正式 Delivery 或用户验收。
4. 不要把 Token unknown 记成 0，也不要用 Runtime 估算替代 Provider 原生账本。
5. 不要硬编码维护者旧局域网 IP；8088 是产品入口，地址由本机当前 IPv4 决定。
6. 不要绕过 AgentKernel 直接依赖 `_pi_runtime` 或 CoreMind 内部类型。
7. 不要用 `git add .`、`git add -A`、`git commit -a`、`git clean` 或 `git reset --hard`。
8. 不要为了“清理”删除有效 ADR、规格、测试、恢复点、真实数据或他人未提交改动。
9. 不要整包安装 Agent-Reach 或自动跟随浮动 `main/latest`；逐个候选固定版本、digest、许可证、
   只读能力、Owner/SecretRef 与外发边界。
10. 不要把“能扫码”简化成共享 Cookie。每个 Owner 的登录态、版本、撤销与恢复必须隔离。
11. 不要为了简短省略验证、安全、权限、可访问性或数据保护。

## 9. 权威证据

- 当前状态：`docs/status/current.md`
- 领域语义：`CONTEXT.md`
- P1-01 规格：`docs/plans/2026-08-27-p1-01-anonymous-web-source-unified-workbench-spec.md`
- Runtime 决策：`docs/adr/0035-unified-data-workbench-and-coremind-runtime-adapter.md`
- 单一工作台边界：`docs/adr/0036-single-workspace-with-verified-runtime-inheritance.md`
- CoreMind 原型：`docs/plans/2026-08-27-p1-coremind-agentkernel-prototype-report.md`
- Agent-Reach：`docs/research/2026-08-31-agent-reach-mangrove-assessment.md`
- 远端实现：PR #107、PR #110、CI run `33925622082`。

GitHub Issue、PR、CI、默认分支 SHA、依赖告警与服务状态都是易变事实，接手时必须现场重取。
