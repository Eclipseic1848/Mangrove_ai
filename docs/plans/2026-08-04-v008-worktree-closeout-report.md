# v0.0.8 在制工作树白名单收口报告

> 2026-08-06 说明：本文记录 2026-08-04 的白名单提交快照。其“Publisher 仍需实施”和
> “AC-06 尚未开始”已经过期；最新发布范围与下一步见
> `2026-08-06-v008-runtime-reliability-and-candidate-retry-closeout.md`。历史文件清单与当时
> 验证证据继续保留，不应用来覆盖当前分支事实。

> 日期：2026-08-04
> 固定点：`aa530ac8d5f6923de6cc3cfc016dbd390f8ad899`
> 分支：`v0.0.8`
> 状态：白名单、生成物清理、验证、提交与推送完成
> 功能收口提交：`10d5fdd3`
> 远端：`platform/v0.0.8`
> 边界：未创建或移动标签，未封板

## 1. 结论

当前工作树包含多项已经完成但尚未收口的工程成果，也混有本机授权配置、运行学习数据和
测试生成物。现已形成精确拟提交白名单，并通过忽略规则把 21,221 个 `.pytest-tmp/`
生成文件从 Git 视图中排除。用户随后明确确认：测试源码和运行必要脚本保留，测试/构建
生成物删除；已清理 `.pytest-tmp*`、`.artifacts`、`frontend/test-results`、`frontend/dist`
和 `__pycache__`，没有删除或回滚用户业务数据。

双轴审查发现 `stop_dev_processes.ps1` 曾在验证项目归属前接收端口监听 PID，可能误杀占用
5173/8088 的其他项目进程。该问题已按 TDD 修复并重新通过真实启停门。AC-05 的准确边界是
“独立获取深 Module、隔离环境、缓存和只读挂载完成工程验证并等待用户验收”；Pi 自动发现
并调用真实 Python/Node/CLI/MCP/Skill 属于 AC-06 以后，不得提前宣称完成。

## 2. 拟提交白名单

以下路径均按精确文件暂存，不使用 `git add .`。

### 2.1 仓库治理与权威状态

- `.gitignore`
- `.dockerignore`
- `AGENTS.md`
- `CONTEXT.md`
- `handoff.md`
- `plan.md`
- `mangrove_plan.md`
- `docs/adr/0026-agentic-capability-acquisition-and-procedure-governance.md`
- `docs/adr/README.md`
- `docs/plans/2026-08-02-agentic-capability-sop-context-spec.md`
- `docs/plans/2026-08-02-agentic-capability-sop-context-task-breakdown.md`
- `docs/plans/2026-08-02-conversation-steering-ac00-ac03-execution-report.md`
- `docs/plans/2026-08-02-phase4-current-issues-audit.md`
- `docs/plans/2026-08-02-agentic-capability-ac04-execution-report.md`
- `docs/plans/2026-08-02-agentic-capability-ac05-execution-report.md`
- `docs/plans/2026-08-03-pi-runtime-recovery-and-verifier-execution-report.md`
- `docs/plans/2026-08-03-windows-start-stop-reliability-report.md`
- `docs/plans/2026-08-03-customer-demo-product-positioning-report.md`
- `docs/plans/2026-08-04-v008-worktree-closeout-report.md`
- `docs/assets/mangrove-unified-data-task-architecture.png`

### 2.2 AC-04 能力目录

- `src/capability_catalog/__init__.py`
- `src/capability_catalog/catalog.py`
- `src/capability_catalog/default_mounts.py`
- `src/capability_catalog/legacy.py`
- `src/capability_catalog/migrations/0001_capability_catalog.sql`
- `src/capability_catalog/models.py`
- `src/capability_catalog/mount_resolver.py`
- `src/capability_catalog/oci_store.py`
- `src/capability_catalog/repository.py`
- `src/capability_catalog/sqlite_repository.py`
- `tests/fixtures/capability_catalog/sample-tool.txt`
- `tests/test_capability_catalog.py`

### 2.3 AC-05 独立获取 Module

- `src/capability_acquisition/__init__.py`
- `src/capability_acquisition/docker_environment.py`
- `src/capability_acquisition/migrations/0001_acquisition_runs.sql`
- `src/capability_acquisition/models.py`
- `src/capability_acquisition/repository.py`
- `src/capability_acquisition/service.py`
- `src/capability_acquisition/sqlite_repository.py`
- `scripts/verify_capability_acquisition_ac05.py`
- `tests/test_capability_acquisition.py`

### 2.4 Pi Runtime、覆盖验证与对话进度纠偏

- `src/agentic_runtime/__init__.py`
- `src/agentic_runtime/assets/mangrove-context-gate.ts`
- `src/agentic_runtime/assets/mangrove-document-tools.ts`
- `src/agentic_runtime/candidate_verifier.py`
- `src/agentic_runtime/document_tools.py`
- `src/agentic_runtime/egress_policy.py`
- `src/agentic_runtime/models.py`
- `src/agentic_runtime/pi_runtime.py`
- `src/agentic_runtime/repository.py`
- `src/api/routes/semantic_workspace.py`
- `src/api/semantic_workspace_runtime.py`
- `src/config/settings.py`
- `src/conversation_steering/models.py`
- `src/conversation_steering/progress.py`
- `tests/test_agentic_runtime.py`
- `tests/test_candidate_verifier.py`
- `tests/test_conversation_steering.py`
- `tests/test_document_tool_relay.py`
- `tests/test_pi_runtime_workspace_api.py`

### 2.5 模型目录、前端体验和真实启停

- `src/model_connections/catalog.py`
- `frontend/src/components/workspace/ResultPreview.tsx`
- `frontend/src/pages/Dashboard.tsx`
- `frontend/src/pages/Login.tsx`
- `frontend/e2e/semantic-workspace.spec.ts`
- `start_all.bat`
- `stop_all.bat`
- `scripts/check_dev_services.ps1`
- `scripts/clean_generated_artifacts.ps1`
- `scripts/stop_dev_processes.ps1`

## 3. 明确排除清单

以下内容保持原状，不暂存、不提交，也不擅自删除：

- `.claude/settings.local.json`：本机命令授权、绝对路径与运行权限配置；
- `data/lessons/**`：运行生成的学习记录，包含修改、删除和新增；
- `data/templates/**`：运行生成的模板状态；
- `.artifacts/**`：本地模型评测 JSON、页面截图和架构图中间 HTML/PNG，已清理；正式
  架构图已有 `docs/assets/` 副本；
- `.pytest-tmp/**` 与 `.pytest-tmp-*/**`：pytest 临时文件，已清理；
- `frontend/test-results/**`：Playwright 运行结果，已清理；
- `frontend/dist/**`：可重建生产构建产物，已清理；
- `**/__pycache__/**`：Python 字节码缓存，已清理。

测试源码和 `frontend/e2e` 继续进入 Git，用于回归；根级 `.dockerignore` 会把它们排除在旧
根 Docker 镜像构建上下文之外。运行必要的 `scripts/check_dev_services.ps1`、
`scripts/stop_dev_processes.ps1` 和 `scripts/dev_reload.py` 仍进入部署上下文。新增
`scripts/clean_generated_artifacts.ps1` 作为有界清理入口，只允许删除固定生成物和
`src/tests/scripts` 下的字节码缓存，不扫描虚拟环境或用户数据。

## 4. 验证证据

- 非项目监听保护 TDD：修复前 Red，修复后 1 passed；停止脚本告警且监听进程仍存活；
- 生成物清理与 Docker 上下文边界：`tests/test_agentic_runtime.py` 34 passed；
- Runtime、候选验证、对话转向、文档 Relay、工作台 API、AC-04/05、模型连接组合门：
  182 passed；
- 前端 TypeScript 与 Vite 生产构建：通过，5385 modules transformed；
- 完整 Playwright：51 passed；
- 真实 `stop_all.bat --no-pause` → `start_all.bat --no-pause`：通过；
- `http://127.0.0.1:8088/api/health`：HTTP 200；
- `http://127.0.0.1:5173`：HTTP 200；
- `http://192.168.1.100:5173`：HTTP 200；
- Markdown/源码 UTF-8、PowerShell UTF-8 BOM 和 `git diff --check` 在最终清单生成后复核。

## 5. 两轴审查

### Standards

- 硬违规：`.claude/settings.local.json` 含本机授权、绝对路径和删除/推送命令，必须排除；
- 硬违规：`data/lessons`、`data/templates` 和测试结果属于运行副产物，必须排除；
- 判断项：`egress_policy.py` 的运行身份参数存在 Data Clumps，可在独立设计任务处理；
- 判断项：`pi_runtime.py` 同时承担 RPC 压缩、输出契约和 Verifier 读取，存在 Divergent Change，
  本次不顺手重构。

### Spec

- 已修复：停止脚本曾可能误杀同端口非项目进程；
- 已澄清：AC-05 完成的是深 Module 工程门，不是 AC-06 真实 Adapter 或用户可见自动获取；
- 已排除：本机配置、运行学习数据和测试产物不进入基线；
- 其余登录页/概览、Pi 恢复、DeepSeek 0731、AC-04 目录和演示材料均能对应已确认规格。

## 6. 后续仍需用户确认

1. 本轮已按上述精确白名单提交 `10d5fdd3` 并推送到 `platform/v0.0.8`；
2. AC-04/AC-05 继续保持“工程验证通过、等待用户验收”状态；
3. vNext Candidate → 正式 Delivery Publisher 仍需单独实施授权。

本报告不授权创建 `v0.0.8` 标签、封板、更新 GitHub Issue、执行生产数据库迁移或启动
AC-06。
