# P0-04A 最小 CI 工程门实施报告

> status: REMOTE_CI_VERIFIED / ISSUE_CLOSED
>
> issue: GitHub #55
>
> verified_at: 2026-08-26
>
> baseline: `38b478f5b8a2b9d5aceebe555a2c1cb667a14920`

## 1. 范围与结论

本切片把现有后端契约/迁移测试、前端锁文件安装与生产构建、依赖一致性、UTF-8 和 Secret
扫描固化为最小 GitHub Actions 工程门。实现提交 `0c815bfd` 已推送到 `main`；GitHub Actions
运行 `32947317082` 的 backend-fast、frontend-build、secret-scan 三个 Job 全部成功，#55 已写入
证据并关闭。分支保护仍未配置，由 #59 独立处理。

本次没有修改业务代码、生产数据库、端口或服务，没有调用外部模型，没有读取或使用生产
Secret，也没有配置分支保护或发布；远端写入仅限获授权的提交、推送、CI 运行与 #55 收口。

## 2. 实现

- `.github/workflows/ci.yml`：`pull_request`、`main` push 与人工触发；全局权限仅
  `contents: read`，并发新运行取消旧运行。
- `.github/workflows/ci-heavy.yml`：只允许人工选择完整 pytest + 前端 E2E、G1 冻结契约或 Docker
  构建；不响应 PR、push 或 schedule，不接收生产 Secret，也不调用真实 Provider。
- 后端快速门：Python 3.13、`requirements-ci.txt`、版本一致性、隔离 `pip check`、严格 UTF-8、
  核心数据契约和 CandidateVerification 显式迁移全套测试。
- 前端门：Node 22、`npm ci`、`tsc --noEmit` 与 Vite 生产构建。
- Secret 门：Gitleaks CLI 8.30.1，从官方 Release 下载并验证 Linux x64 SHA-256；完整 Git 历史
  扫描使用 100% redaction。
- 供应链：`checkout`、`setup-python`、`setup-node`、`upload-artifact` 全部固定到经官方 tag
  现场解析的不可变 commit。
- 证据：安装/构建日志、依赖/UTF-8 JSON、JUnit 和脱敏 Gitleaks JSON 保存 14 天；不包含用户
  数据库、用户文件或 Secret。

## 3. TDD 与验证证据

### 3.1 红绿循环

1. CI workflow、UTF-8 和依赖一致性入口不存在时：`3 failed`。
2. 最小实现后：`tests/test_ci_contract.py` 为 `3 passed`。
3. 增加窄 Gitleaks 白名单契约：配置不存在时 `1 failed`，实现后转绿。
4. 增加安装失败日志契约：workflow 缺日志时 `1 failed`，实现后转绿。

### 3.2 干净 Python 环境

- Python 3.13 临时 venv；仅安装 `requirements-ci.txt`。
- `pip check`：`No broken requirements found`。
- CI 子集与 `requirements.txt`：14 项精确版本全部一致。
- `tests/test_ci_contract.py`、`tests/test_data_prep_contracts.py`、
  `tests/test_candidate_verification_migration.py`：`31 passed in 6.59s`。
- 当前维护者全局 Python 的 `pip check` 存在既有额外包冲突，因此未用全局环境冒充干净 CI。

### 3.3 UTF-8、前端与 Secret

- UTF-8：1,163 个受跟踪非二进制文本文件严格解码通过；默认覆盖无扩展名和点文件，显式
  `-text` 与 Git 二进制启发式文件不误判。
- 当前 8088 使用中的 `esbuild.exe` 锁住本地 `node_modules`，直接 `npm ci` 返回 EPERM；没有强杀
  服务。改用 `git archive HEAD frontend` 生成隔离源码，`npm ci` 安装 342 个包，TypeScript 与
  Vite 构建通过，5,387 个模块完成转换。
- `npm audit` 报 2 个 moderate；未运行破坏性的 `npm audit fix --force`，交由 #58 统一治理。
- Gitleaks 工具 checksum：
  `d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e`（Windows x64 本地验证）；
  workflow 固定 Linux x64 checksum：
  `551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb`。
- 完整历史：330 commits、约 38.18 MB，最终 `no leaks found`。
- 本次 13 个未提交任务文件独立目录扫描约 115.85 KB，`no leaks found`；未扫描用户 G1、
  `.scratch` 或本地审计文件。
- 既有误报只使用三条“规则 + 精确路径 + 精确行模式 AND”白名单，以及 9 条经人工核对的精确
  fingerprint；没有按提交、测试目录或评测目录整体跳过。任何新位置或新提交仍会阻断。

## 4. 门禁分层

- 快速自动门证明：干净最小环境可安装、核心契约/当前显式迁移可重放、前端可正式构建、受跟踪
  文本为 UTF-8、当前 Git 历史无未处理 Secret。
- 完整门证明：`heavy-ci-manual` 可人工选择完整 pytest + 浏览器 E2E、Docker 构建或 G1 冻结
  契约；OCR、Runtime 和真实 G1 资格运行仍按任务单独授权。任何启动服务的门必须检查并清理
  端口、子进程、容器和网络。
- 人工门控制：真实 Provider/费用、生产迁移、Secret 使用/轮换/销毁、发布、Git/GitHub 写入与
  分支保护。快速门和完整门不能替代这些授权。

## 5. 未完成与后续边界

- GitHub 托管 runner 运行 `32947317082` 已成功，#55 已更新并关闭。
- `main` required checks、禁止强推和测试 PR 属于 #59，不能由本实现自动配置。
- 完整生产依赖拆分、Critical/High/Moderate 漏洞处置与可达性结论属于 #58。
- 统一迁移注册表、所有子系统迁移入口和应用启动只验 Schema 属于 #56；本门只执行当前最完整的
  CandidateVerification 迁移 dry-run。
- 顶层 P0 尚未完成，因此 README 之外的 Code of Conduct、MIT License、Security 和 GitHub About
  Phase 同步门当前不触发。

## 6. 双轴审查

首轮 Standards 发现流水线经 `tee` 时失败传播未显式锁定；现已增加 workflow 级
`defaults.run.shell: bash`、关键块 `set -euo pipefail` 和契约测试。首轮 Spec 发现缺少独立手动
重门，以及 UTF-8 默认枚举会漏无扩展名/点文件；现已增加 `heavy-ci-manual`，并改为扫描全部
受跟踪路径、尊重 Git `-text` 与二进制启发式。修复后本地回归为 34 passed，YAML/TOML 语法、
UTF-8、依赖一致性、隔离 `pip check` 和 `git diff --check` 均通过。最终 Standards/Spec 双轴
复核均为 PASS，无剩余 P1/P2。
