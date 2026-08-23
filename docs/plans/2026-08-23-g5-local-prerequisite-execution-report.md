# G5 本机前置包执行报告

> 状态：本机前置包 PASS
>
> 日期：2026-08-23
>
> 对应工单：#36
>
> 边界：不等于完整 8B-1、目标服务器 8B-2、G5 合格或 Phase 4 完成

## 1. 目标与范围

本轮只建立目标服务器验收前可重复执行的本机前置包，并验证本机能够在隔离 Linux 容器内
完成真实产品闭环。目标服务器、生产容量、长期运行、RAID/灾难恢复和可信 LAN PC 不在本轮
环境中，因此继续记为 `pending_8b2`。

本轮没有更换生产 Vault Key，没有切换 `vnext_default`，没有扩大受众，也没有提交本机模型
地址、型号、Secret、原始输入、登录态、运行日志或备份。

## 2. 实现

- 增加干净 Linux 多阶段镜像和隔离 Compose；运行用户固定为非 root，根文件系统只读，
  去除 Linux capabilities，不挂载源码或 Docker Socket。
- 增加 `/api/readiness`，核验 WebUI 数据库、两个语义工作进程和上传、执行、交付三个可写根
  目录后返回 ready。
- 上传、执行和交付目录改为由运行配置统一提供，同时保留旧调用兼容。
- 外部文档抽取关闭 SDK 盲重试；失败是否重试交给前端用户决定。
- 增加真实浏览器闭环、并发 Owner 隔离、重复请求、进程重启、模型超时、在线备份和全新卷
  恢复验证。
- Run ID 同时绑定独立文件锁和带原始值摘要的 Docker 资源身份；清理前核验 Compose
  project/service 标签，清理后检查容器、网络、卷、镜像和临时备份。
- 模型地址和型号改为运行时必填，仓库不保存维护者本机或局域网配置。
- 报告标题和结论限定为“G5 本机前置包”，不再冒充完整 8B-1。

## 3. 最终真实运行

最终低敏 Run ID：`g5-prereq-20260823-final2`。

| 检查面 | 结果 |
|---|---|
| 干净 Linux 镜像、依赖和接单就绪 | PASS |
| 非 root、只读根、无源码挂载 | PASS |
| 真实登录、上传、外部模型抽取和结果展示 | PASS |
| JSONL、XLSX、Manifest 下载 | PASS |
| 20 用户交叉读取 | 跨 Owner 成功数 0 |
| 40 次并发重复抽取 | 全部 409；终态与产物路径不变 |
| API 进程重启 | 用户、任务、完成态和文件计数不变 |
| 模型超时 | 只发送 1 次 HTTP 请求；无假任务；服务恢复 ready |
| 两个 SQLite 在线快照与全新卷恢复 | quick_check、业务哈希和 Owner 隔离复验通过 |
| 本次资源清理 | 容器、网络、卷、临时备份和镜像残留 0 |

结构化汇总为 18 项本机前置检查 `passed`、6 项目标服务器检查 `pending_8b2`。原始运行目录
只用于本机核验，交付前删除，不进入 Git。

## 4. 工程验证

- G5 验收与报告聚焦回归：`33 passed`；连同 readiness 和文档抽取回归：`56 passed`。
- 真实前置运行：退出码 0；结构化总状态 `passed`。
- 全仓后端：`1872 passed, 5 skipped, 1 deselected`，0 failed，用时 493.38 秒。唯一排除项
  `test_independent_g1_dry_run_verifies_frozen_blind_set` 会因维护者持有的 10 个 G1 运行后冻结
  文件身份变化而失败；本轮未改写、暂存或清理这些文件。
- 前端 TypeScript 与 Vite 生产构建通过；默认 Playwright 单 worker `60 passed`。
- `npm audit --omit=dev --audit-level=high`：High 及以上为 0；保留 2 项 React Router Moderate，
  自动修复要求升级到不兼容的 7.x，本轮不扩大为主版本迁移。
- `git diff --check` 通过。
- Standards 与 Spec 双轴终审均为 PASS；验收 runner 偏大作为非阻断维护性风险保留，不在本轮
  为拆分而扩大改动。

## 5. 未完成项

- 旧 Windows `.meta`、旧 Delivery 和 `managed:v1/` 的完整跨根、跨系统可移植性门未由本轮
  真实运行覆盖；不得用全仓单测代替目标 Linux 复验。
- 目标 Linux/GPU/驱动/CUDA 未提供。
- 生产并发、容量、长期运行、RAID 和灾难恢复未执行。
- 另一台可信 LAN PC 的 8088 人工验收未执行。
- G4 最终 Vault 轮换证据缺失；生产 Key 保持不变。

因此 #36、#39 均必须保持 OPEN 和 `ready-for-human`。
