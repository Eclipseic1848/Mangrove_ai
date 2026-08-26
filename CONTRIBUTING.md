<p align="center">
  <strong>Mangrove（红树林）</strong><br>
  <sub>统一数据任务平台</sub>
</p>

<p align="center">
  <a href="./README.md">README</a> ·
  <a href="./CONTRIBUTING.md">参与贡献</a> ·
  <a href="./CODE_OF_CONDUCT.md">行为准则</a> ·
  <a href="./SECURITY.md">安全策略</a> ·
  <a href="./THIRD_PARTY_NOTICES.md">第三方许可</a> ·
  <a href="./LICENSE">MIT License</a>
</p>

---

# 为 Mangrove 贡献

感谢你关注 Mangrove。项目仍处于快速迭代期，贡献应优先保持任务语义、权限边界、证据链和
正式交付契约的稳定。

## 开始之前

1. 阅读 `README.md`、`AGENTS.md`、`docs/status/current.md` 和 `CONTEXT.md`。
2. 搜索现有 Issue，避免重复工作；较大的功能或架构变化应先开 Issue 对齐范围。
3. 安全问题不要开公开 Issue，请按 `SECURITY.md` 私密报告。
4. 不要提交真实 API Key、Cookie、用户文件、数据库、日志、浏览器状态或任务制品。

## 本地开发

```powershell
Copy-Item .env.example .env
py -3.13 -X utf8 -m pip install -r requirements.txt
Set-Location frontend
npm install
npm run build
```

公开仓库使用 `py -3.13 -X utf8 scripts/dev_reload.py` 启动后端；构建后的产品入口是
`8088`，`5173` 仅用于前端开发。维护者本机的一键启停脚本不随仓库发布。可选外部采集器
按 `external/README.md` 准备。

## 变更原则

- 一次 PR 只解决一个明确问题，不夹带无关重构。
- 业务范围、数据含义、权限、数据外发和不可逆操作必须显式说明。
- 关键权限、安全、状态转换、失败关闭及降级逻辑应使用简洁中文注释解释“为什么”。
- 代码、文档和注释使用 UTF-8；面向用户的文字与项目文档使用简体中文。
- Candidate、验证通过或中间制品不能被表述为正式 Delivery。
- 测试源码属于工程安全网，应保留；缓存、日志和运行结果不得提交。

## 验证

`minimum-ci` 是普通 PR 的快速工程门：使用 Python 3.13 安装 `requirements-ci.txt`，检查该清单
与主依赖版本一致、执行 `pip check`、严格 UTF-8 检查、核心契约与 CandidateVerification 迁移
dry-run；前端使用 `npm ci`、TypeScript 检查和生产构建；Gitleaks 扫描完整 Git 历史。该门不启动
服务、不占用端口、不读取生产数据库、不调用外部模型，也不使用生产 Secret。缓存只加速下载，
锁文件、checksum 和测试结果仍是正确性来源。

本地快速门：

```powershell
py -3.13 -X utf8 scripts/ci/check_requirement_consistency.py `
  --base requirements.txt --subset requirements-ci.txt
py -3.13 -X utf8 scripts/ci/check_utf8.py
py -3.13 -X utf8 -m pytest tests/test_ci_contract.py `
  tests/test_data_prep_contracts.py tests/test_candidate_verification_migration.py
Set-Location frontend
npm ci
npm run build
```

完整门按改动风险增加完整 pytest、浏览器 E2E、Docker、OCR、Runtime 或 G1 冻结集。涉及启动
和端口时，应检查 `http://127.0.0.1:8088/api/health`，并在停止后确认端口、子进程、容器和任务级
网络无残留。真实 Provider、生产迁移、Secret 使用、发布和远端规则变更属于人工授权门，不得
因快速门或完整门通过而自动执行。

GitHub 的 `heavy-ci-manual` 只允许维护者人工选择完整回归、G1 冻结契约或 Docker 构建，不在
PR、push 或定时事件自动运行，也不接收生产 Secret。真实 G1 资格运行、真实 Provider 和生产
环境验收仍需先确认精确数据、连接、费用与恢复边界，不能用该 workflow 替代。

CI 上传的 `.artifacts/ci` 只包含依赖/构建日志、UTF-8/依赖 JSON、JUnit 和已脱敏的 Gitleaks
报告，保留 14 天；不得把用户文件、数据库、原始工具日志或 Secret 放入证据制品。自动化测试
通过不等于用户验收、生产资格或远端合并保护已经生效。

## 提交与 Pull Request

- 使用清晰的提交说明，例如 `feat: ...`、`fix: ...`、`docs: ...`、`test: ...`。
- PR 描述至少包括：问题、范围、方案、安全/数据影响、验证证据和未完成项。
- 不要强推维护者分支，不要移动既有标签。
- 若变更改变架构决策、领域词汇或当前状态，应分别更新 ADR、`CONTEXT.md` 或
  `docs/status/current.md`，不要在多个文件重复维护滚动状态。

提交贡献即表示你同意将自己的贡献按项目 `LICENSE` 发布；第三方组件仍遵循其各自许可证。
