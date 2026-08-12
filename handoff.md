# Mangrove 零上下文交接

> 最后核验：2026-08-12
>
> 当前公开开发分支：`main`
>
> 远端：`origin` → `https://github.com/Eclipseic1848/Mangrove_ai.git`
>
> 版本状态：`v0.0.8` 开发能力已纳入首次公开快照；没有创建或移动版本标签

## 1. 接手顺序

1. 阅读 `AGENTS.md`、本文件、`docs/status/current.md` 和 `CONTEXT.md`。
2. 运行 `git status --short --branch --untracked-files=all`，不要覆盖本机运行数据或他人改动。
3. 阅读当前工单引用的规格、ADR 和执行报告。
4. 按需准备 `.env`、Docker Desktop 与可选外部依赖，再验证 `http://localhost:8088/api/health`。

## 2. 当前主线

- AC-07 #33 已完成、迁移、验收、推送并关闭。
- #34 可恢复 ValidationRun 已完成工程实现、双轴审查和生产纯新增迁移；最终灰度状态以
  `docs/status/current.md` 为准。
- #35 Trivy/Syft 供应链证据已完成工程实现和真实双包扫描；仍需正式 code-review、生产 `0003`
  迁移和用户验收，不能表述为已晋级、签名或平台发布。
- #36～#44 未自动进入；30 项泛化集、完整 PG-05、真实外部 Provider 安全端到端和 8B 仍未完成。

## 3. 公开发布与本机数据边界

首次公开 `main` 使用干净快照，不复制旧私有 Git 历史，因为历史曾跟踪本机 Agent 配置和数据库。
以下内容永远不能随普通提交发布：`.env`、`.claude/`、数据库、上传/下载、日志、任务制品、浏览器
登录态、运行学习库、个人偏好、虚拟环境和 `local-audits/`。

MediaCrawler 和 Firecrawl 的本机工作副本不提交。运行
`scripts/setup_external_dependencies.ps1` 会固定上游提交并应用 `external/patches/` 中的补丁；
不得擅自更换版本、镜像、来源或许可证。

## 4. 已验证证据

- 2026-08-11 清理后：后端 1249 passed / 4 skipped。
- 前端生产构建通过；完整 Playwright 单 worker 54/54 passed。
- 维护者本机私有 `start_all.bat --no-pause` 的 8088 API/HTML 与 5173 开发服务通过，停止后
  端口和 Pi 资源无残留；该脚本由 `.gitignore` 排除，不进入公开仓库。
- 首次公开发布另执行公开快照密钥扫描、外部补丁校验和 GitHub 远端复核；以发布回执为准。

## 5. 下一门禁

1. 对 #35 进行 Standards/Spec 双轴 code-review。
2. 用户明确授权后执行 #35 带备份生产迁移和 8088 用户验收。
3. 完成后再决定进入 #36；不得夹带普通用户权限扩大、外部发布或签名密钥生成。

## 6. 绝对不要再踩的坑

- 不要把 Candidate、验证通过或内部 Parquet 当成正式 Delivery。
- 不要先 OCR 全文再让 Agent 处理；按目标发现、精读并由覆盖门判定完成。
- 不要用固定页数或关键词规则替代 Agent 判断；确定性门约束证据、覆盖、权限和停止语义。
- 不要删除测试源码来追求目录体积；只清理可重建缓存和运行产物。
- 不要把 `5173` 改成产品入口；统一入口始终是 `8088`。
- 不要把维护者本机的 `start_all.bat`、`stop_all.bat` 加入公开提交，也不要为发布改写其本地配置。
- 不要在 Docker 清理失败时写成功终态。
- 不要把管理员可见任务元数据扩大为无审计读取个人业务正文。
- 不要使用 `git add .`、强推、硬重置或批量 clean 处理混合工作树。
