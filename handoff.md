# Mangrove 零上下文交接

> 状态：`P1_01_ENGINEERING_VERIFIED / ISSUE_98_REMOTE_CLOSEOUT_PENDING`
>
> 最后核验：2026-09-04
>
> 仓库：`Eclipseic1848/Mangrove_ai`

本文件写给完全没有上下文的新会话。先读 `AGENTS.md`、`docs/status/current.md`、`CONTEXT.md`，
再现场读取 GitHub、Git 与运行态；历史计划是证据，不是当前状态。

## 1. 我们在做什么

当前目标是完成 GitHub 仍开放的 Issues，并在全部关闭后清理过期内容、留下可直接接手的状态。
当前阶段是 **Issue #98 的远端收口**，不是需求澄清，也不是暂停。

产品方向已经冻结：`/data-prep` 是统一数据工作台，保留文件、来源、自然语言交互、预览、模板、
记忆、追问、Candidate、正式 Delivery 与下载；逐步继承旧对话/分析能力，但不建立第二套任务真相。
CoreMind 只经 `AgentKernel` 薄 Adapter 提供 Runtime 能力，不能替代 Mangrove 的 Owner、
TaskRevision、Verifier、Publisher、权限或正式 Delivery。

## 2. 已完成什么

### P1-01

- 决策与原型 #83～#88 已完成；实现工单 #89～#97 已关闭。
- CoreMind 0.7.1 薄 Adapter 已由 PR #107 合入
  `main@12170eebf1d2f4bad5c86c2a6c38d0bef0a4f998`，Issue #97 已关闭。
- #98 当前实现提交：
  - `a6df9ad2`：匿名网页到唯一正式 Delivery、Publisher 重启恢复、Owner 全面隔离；
  - `99e17490`：声明 PDF 测试所需 `fpdf2==2.8.7`；
  - `c9cdb56f`：移除旧固定局域网 IP 与旧 `_pi_runtime` 测试假设。
- 数据库启动预检只读检查 WebUI/Scheduler schema；落后时给出带源 SHA 的显式迁移命令并失败关闭。
- 工作记录仍默认折叠，展示行动、工具、模型、时间、Token/unknown 与恢复事实，不展示逐字思维链、
  Cookie、Secret、系统 Prompt、宿主路径或原始大日志。

### 当前验证证据

- 后端全量固定种子：`2352 passed, 13 skipped, 1 deselected`；唯一 deselect 是其他任务正在修改的
  G1 freeze 自校验，未覆盖或重建该资产。
- #98 相关后端组合：`235 passed, 1 skipped`。
- 前端正式构建：通过；完整 Playwright：`77 passed`，其中统一数据工作台 40 项并含明暗主题 axe。
- 固定 Pi/CoreMind 黄金对照：`1 passed`，覆盖运行中取消、无迟到事件和资源清理。
- UTF-8：1248 个文件通过；`git diff --check` 通过。
- `start_all.bat --no-pause` 返回 0；8088、5173、8080、3002、1200 监听，8088/5173 HTTP 200，
  `/api/health` 返回 `ok=true`。
- Standards 轴审查：无问题。Spec 轴唯一阻断是旧状态文档，本次已修正，仍需复审。

这些证据只支持 `ENGINEERING_VERIFIED`。Issue #98 未执行真实外部网页/真实 Provider 用户旅程、
新的本机生产迁移、用户验收、部署、Tag、GitHub Release、npm/PyPI 或其他外部发布。历史本机
生产迁移已于 2026-08-26 完成；本工单未授权新增迁移、恢复覆盖、备份处置或 Key/Secret 轮换。

### 可复现根命令

以下命令均返回 exit code 0；前端命令先进入 `frontend/`。完整后端仅排除一项被其他任务修改的
G1 freeze 自校验。

```powershell
.\.artifacts\ci-clean-venv\Scripts\python.exe -X utf8 -m pytest -q tests/test_source_acquisition.py tests/test_source_acquisition_api.py tests/test_web_source_delivery_api.py tests/test_work_trace.py tests/test_agent_kernel.py tests/test_coremind_agent_kernel_adapter.py tests/test_pi_runtime_workspace_api.py tests/test_semantic_workspace_api.py tests/test_database_migrations.py tests/test_dev_database_preflight.py tests/test_issue98_workbench_closeout.py
.\.artifacts\ci-clean-venv\Scripts\python.exe -X utf8 -m pytest -q --randomly-seed=0 --deselect tests/test_g1_independent_runner.py::test_independent_g1_dry_run_verifies_frozen_blind_set
Push-Location frontend; npm run build; npx playwright test --workers=1; Pop-Location
$env:MANGROVE_RUNTIME_ADAPTER_GOLDEN_TEST='1'; $env:PYTHONPATH=(Resolve-Path '.\.venv-coremind-host-verification\Lib\site-packages').Path; .\.artifacts\ci-clean-venv\Scripts\python.exe -X utf8 -m pytest -q tests\test_runtime_adapter_golden.py
.\.artifacts\ci-clean-venv\Scripts\python.exe -X utf8 scripts\ci\check_utf8.py
git diff --check 12170eebf1d2f4bad5c86c2a6c38d0bef0a4f998..HEAD
cmd.exe /d /c start_all.bat --no-pause
```

端口证据：8088、5173、8080、3002、1200 均 Listen；8088/5173 HTTP 200；
`/api/health` 返回 `{"ok":true,"service":"mangrove-webui"}`。

## 3. 当前卡在哪

没有代码阻塞。当前只差：

1. 复审本次状态文档修正；
2. 精确暂存 #98 文件，推送并创建受保护 PR；
3. 等 `backend-fast`、`frontend-build`、`secret-scan` 全绿后合并；
4. 核对 #98 自动关闭，再按完成证据关闭父票 #83 与决策地图 #81；
5. 清理确认可再生的生成物，完成最终状态/交接 PR。

## 4. 接手后立刻执行

```powershell
git status --short
git rev-parse HEAD
git rev-parse origin/main
gh issue list --repo Eclipseic1848/Mangrove_ai --state open --limit 100
```

然后检查 `docs/status/current.md` 与本文件的差异，只暂存本轮明确文件。不要重新实现 #89～#97，
不要重跑未受影响的 CoreMind 上游矩阵，也不要创建 worktree。

## 5. 保护范围

以下本地改动属于用户或其他任务，绝不能暂存、覆盖、恢复、清理或据此重建：

- `docker/phase4b/entrypoint.sh`；
- `evals/generalization-g1-independent*/freeze.json`；
- `evals/generalization-g1-independent*/heldout_manifest.json`；
- `evals/generalization-g1-independent*/self-check-report.json`；
- `evals/generalization-g1/fixtures.json`。

有效的 Agent-Reach 调研 `docs/research/2026-08-31-agent-reach-mangrove-assessment.md` 要保留；它是
候选工具知识，不是安装或启用授权。本机 `F:\new branch\CoreMind` 由专属任务持有，本任务禁止
编辑、测试、切分支、提交、推送、回滚、清理或 stash。

## 6. 下一步计划

### 立即收口

1. 完成 #98 双轴复审、PR、CI 与受保护合并。
2. 关闭 #83 与 #81，确保 GitHub Open Issues 为 0。
3. 检查 README、Code of Conduct、Contributing、MIT License、Security 与 GitHub About；无语义
   变化则记录“已检查、无需变化”，不制造无意义 diff。
4. 删除确认过期或可再生的 `.scratch`、`test-results`、`frontend/premium-audit.json`；不删除测试、
   ADR、规格、执行证据、业务数据、备份或用户改动。
5. 把 `docs/status/current.md` 与本文件写成最终完成态，经 PR/CI 合入。

### 工程 Roadmap

| 阶段 | 目标 | 进入条件 |
|---|---|---|
| P0 | 可持续迭代基线、迁移、SecretRef、依赖安全、CI/保护 | 已完成 |
| P1-01 | 匿名网页统一工作台与 CoreMind Adapter | 工程已验证，待远端收口 |
| P1-02 | 深化统一任务生命周期，迁入仍有价值的旧工作区能力 | 单独规格与验收，不做全量重写 |
| P1-03 | 认证、可观测性、SLO、TLS/CSP、远程多人运行 | 明确部署环境和安全门 |
| P1-04 | 配额、成本、外发、审计、回滚后分批开放平台能力 | 用户确认受众与权限 |
| P1-05 | 组件测试、包体预算、性能与无障碍治理 | 建立基线与可执行预算 |
| P2 | Linux/GPU、远程 MCP/Registry、多媒体、多节点、对象存储/PostgreSQL | 真实需求与环境具备后另立规格 |

### 版本计划

- 当前没有远端 Tag 或 GitHub Release；本地历史版本语义不能当作公开版本。
- P1-01 完成只产生工程里程碑，不自动发布版本。
- 下一版本号、Tag、Release、npm/PyPI 和部署都是人工门；必须在完整 P1 范围、真实验收和发布清单
  冻结后另行决定。

## 7. 绝对不要再踩的坑

1. 不要创建 worktree 或额外“分支文件夹”；只在当前 checkout 工作。
2. 不要触碰 CoreMind 本机仓库；只使用正式提交、制品 digest、CI 与锁定测试证据。
3. 不要把 Candidate、Verifier 通过、CI 绿色或 Runtime 成功说成正式 Delivery 或用户验收。
4. 不要把 Token unknown 记成 0，也不要用 Runtime 估算替代 Provider 原生账本。
5. 不要硬编码维护者旧局域网 IP；产品入口是 8088，地址由本机当前 IPv4 决定。
6. 不要绕过 AgentKernel 直接依赖 `_pi_runtime` 或 CoreMind 内部类型。
7. 不要用 `git add .`、`git add -A`、`git commit -a`、`git clean` 或 `git reset --hard`。
8. 不要为了“清理”删除有效 ADR、规格、测试、恢复点、真实数据或他人未提交改动。
9. 不要整包安装 Agent-Reach 或自动启用 OpenCLI/xiaohongshu-mcp；先固定版本、digest、许可证、
   只读能力、Owner/SecretRef 与外发边界。
10. 小红书等认证来源只有明确 Cookie 失效时才要求当前 Owner 扫码；网络或平台异常只能显示状态
    未知。扫码结果只保存到该 Owner 的隔离连接，直到下次明确失效。
11. 不要为了简短省略验证、安全、权限、可访问性或数据保护。

## 8. 权威证据

- 当前状态：`docs/status/current.md`
- 领域语义：`CONTEXT.md`
- P1-01 规格：`docs/plans/2026-08-27-p1-01-anonymous-web-source-unified-workbench-spec.md`
- Runtime 决策：`docs/adr/0035-unified-data-workbench-and-coremind-runtime-adapter.md`
- CoreMind 0.7.1：`docs/plans/2026-09-03-coremind-0.7.1-resumption-preflight.md`
- Agent-Reach：`docs/research/2026-08-31-agent-reach-mangrove-assessment.md`

GitHub Issue、PR、CI、默认分支 SHA 与服务状态都是易变事实，接手时必须现场重取。
