# Mangrove 接手说明

> 状态：active
>
> 核验日期：2026-08-23
>
> 公开主线：`main`
>
> 当前工作分支：`codex/g4-key-retention-gate`
>
> 分支基线：`b321989e5a195ead9c4845fb9c412b5df1f4cd3c`
>
> 当前工作：G4 保留生产密钥补偿控制门

## 1. 开始前

按顺序读取 `docs/status/current.md`、`CONTEXT.md`、`docs/agents/` 和当前工单引用的规格。
然后现场核验 Git、GitHub Issue、运行服务和数据库状态；本文件中的 SHA 与运行态会过期。

不得提交或清理以下 10 个维护者持有的 G1 运行后冻结文件：

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

## 2. 当前结论

- G1 已通过独立盲集正式运行并关闭 #37/#40。
- G2 Office 3/3、AC-05 生产迁移与恢复已通过，#38 已关闭。
- G3 工程门与 `admin_gray` 恢复验收已通过；生产仍保持 `admin_gray`，默认切换未执行，
  #39 保持 OPEN。
- G4 的 DeepSeek/百炼历史真实 Pi 链均曾通过；用户明确不更换现有生产 Vault Key，现按
  ADR-0032 采用补偿控制。Qwen 的正式批次可复用；旧 DeepSeek 报告因无持久批次且当前连接
  身份和资格执行代码已变化而不可复用。多报告逐份验真代码与 67 项 G4 回归已通过，用户已
  授权只补跑一次当前 DeepSeek 正式批次；正式报告尚未生成，暂不写成完整合格。
- G5 本机工程门已通过；真实目标服务器验收移到未来部署阶段，不冒充已完成。

GitHub 当前仅 #36、#39 为 OPEN，均标记 `ready-for-human`。

## 3. G5 本机前置包已完成

实现入口：

- `docker/phase4b/`：干净 Linux 多阶段镜像、隔离 Compose、非 root/只读根运行；
- `scripts/acceptance/run_phase4b_8b1.py`：确定性验收与精确资源清理；
- `scripts/acceptance/report_phase4b_8b1.py`：低敏 Markdown/HTML 汇总；
- `frontend/e2e/phase4b-8b1-*-real.spec.ts`：真实浏览器闭环和模型超时失败关闭；
- `/api/readiness`：接单所需依赖的就绪状态；
- `docs/plans/2026-08-23-g5-local-prerequisite-execution-report.md`：正式低敏结论。

最终真实运行通过：

- 干净镜像构建、Linux 依赖、非 root、只读根、无源码挂载；
- 真实登录、上传、外部模型抽取、结果展示和 JSONL/XLSX/Manifest 下载；
- 20 用户交叉读取成功数为 0；
- 完成态后 40 次并发重复抽取全部返回 409，终态和产物路径不变；
- 模型超时只产生 1 次 HTTP 请求，页面给出失败提示，不创建假任务；
- API 进程重启后用户、任务、完成态和文件计数不变；
- 两个 SQLite 在线快照、全新卷恢复、业务文件哈希与 Owner 隔离复验通过；
- 上传、Harness 和 Legacy/vNext Delivery 的受管路径代码门、本机整根迁移及旧 Windows
  路径兼容回归通过；Owner、哈希、UNC、设备路径、父级穿越和软链接逃逸失败关闭；
- 本轮容器、网络、卷、临时备份和镜像残留均为 0。

验收脚本不保存维护者局域网模型地址或型号；执行时必须显式传入。模型超时默认 1800 秒，
允许 1 至 7200 秒。失败请求不盲重试，是否重试由用户在前端决定。

## 4. 未完成与部署时事项

### #36 / G5

当前没有目标服务器，以下 8B-2 项改为部署时执行：

- 目标 Linux、GPU、驱动和 CUDA；
- 生产并发、容量和长期运行；
- RAID、备份与灾难恢复；
- 另一台可信 LAN PC 的 8088 人工验收；
- 路径可移植性代码门已完成，但仍缺目标 Linux 环境的真实跨系统复验。

### #39 / G4

保留密钥补偿控制实现已完成。仍需在干净候选提交上：

- 重新生成当前传输安全报告（不请求 Provider）；
- 生成生产保留密钥报告（不改写 Key 或数据库）；
- 复用 `a0560852` 的 Qwen 报告，只补跑一次当前 DeepSeek 正式批次，再完成最终 G4 汇总；
- G4 通过后，另行执行并验收 `vnext_default`，不能自动切换。

## 5. 精确下一步

1. 提交并双轴审查当前 G4 候选；
2. 在干净提交上生成当前传输安全报告和生产保留密钥报告；
3. 汇总最终 G4 报告；
4. G4 通过后，按独立生产动作执行并验收 `vnext_default`；
5. 未来取得目标服务器并进入部署阶段时，以新 Run ID 完成 8B-2，不复用本机报告。

## 6. 验证与证据边界

- 本机前置报告只摘取低敏结论；`runtime/` 原始结果、输入、登录态、日志和备份不进入 Git。
- 最新全仓后端为 `1893 passed, 7 skipped, 1 deselected`；两项软链接测试因当前 Windows
  权限跳过，目标 Linux/CI 必须实际执行。
- `pip check` 仍有 `types-pytz` 缺失和 `crawl4ai`/`lxml` 版本冲突，不得静默升级。
- 测试通过不是用户验收、生产资格、默认受众切换或版本发布。
- G1 冻结文件导致的身份漂移测试必须明确排除，禁止改写冻结文件制造绿色结果。
- 任何清理只能使用本次生成的 Compose project/service 标签和精确资源名；禁止全局 Docker
  prune、按端口或模糊名称删除。
- 生产 Vault Key、Secret、本机脚本、绝对路径和局域网地址不得进入仓库。

## 7. 路线与版本

当前路线是：完成 G4 保留密钥正式证据；通过后独立执行 `vnext_default`；真实服务器 8B-2
在部署阶段补验。远程 MCP/Secret、Registry 自动发现、普通用户平台能力开放、AC-08/AC-09、
Phase 4C 和 Phase 5 均为后续范围。

稳定标签仍为 `v0.0.4`，不得移动或回写。当前没有获准的新版本号、RC、Tag 或 Release。
