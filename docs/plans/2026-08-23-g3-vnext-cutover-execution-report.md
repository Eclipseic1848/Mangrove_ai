# G3 vNext 默认入口生产切换验收报告

> 状态：PASS
>
> 验收日期：2026-08-23
>
> 代码提交：`ecdd4eecfd7468cf6d8cc30d843e7af3637e5c1f`

## 1. 结论

生产 Rollout 已从 `admin_gray` 切换为 `vnext_default`。普通用户的新 TaskRevision 默认使用
Pi Runtime，仍可显式选择 Legacy；旧 TaskRevision、既有 RuntimeAssignment 和正式 Delivery
没有迁移、覆盖、删除或重新发布。

## 2. 切换门

- G4 最终报告：`g4_qualified=true`，阻塞项为 0；
- Provider：复用 Qwen 正式批次，只执行一次当前 DeepSeek 正式批次；两个批次均通过；
- 组合 Manifest SHA-256：
  `367b8bf8d11db4b68fb37eb3e5bf57a70c505228d1185c005dfa882d8b00f297`；
- Vault：保留现有生产主密钥，权限、错误密钥拒绝、备份恢复、无密钥备份、并发锁和一次性
  合成轮换演练全部通过，生产 Key 未改写；
- 新活动 GateSnapshot 增加 `g4-provider-safety`，累计 7 项硬门全部合格。

## 3. 生产验收

- 切换前恢复点：`webui-before-g3-vnext-20260824-031247.db`；
- 恢复点 SHA-256：
  `6a44ce9b17c1444ff641ad997376020e5d6d2d4b4c2d3d8b3d22e0e86acfb40d`；
- 首次切换快照：
  `0d1aba3a4cd232411f4ebd1a217cbdae8ca7d1235b4ee2dac3c56d7782f56274`；
- 受控失败快照：
  `a2794aa1db188a31dfd8c7fdf0629057582142f85fbdad9069ce60d9a3fee189`；
- 最终恢复快照：
  `d6c456f9ada172fd9c037045ef7d43571cd4a57b3fa1d7e190fb42763eec1729`；
- 8088 验证：默认请求走 Pi、显式 Pi 走 Pi、显式 Legacy 走 Legacy、跨 Owner 返回 404；
- 失败探针在业务写入前停止，没有创建任务、RuntimeAssignment 或 Provider 请求；
- 注入 G3 P0 失败后自动进入 `legacy_rollback`，默认请求回到 Legacy，显式 Pi 返回 409；
- 恢复快照不会自动解除回滚；经两份独立授权先恢复 `admin_gray`，再恢复
  `vnext_default`；
- 数据库 `integrity_check=ok`；除 Rollout 审计表外，所有生产表与切换前恢复点逐表哈希一致；
- 32 个既有正式交付文件全部与数据库 SHA-256 记录一致。

## 4. 边界与剩余风险

- 本次只改变切换后新 TaskRevision 的默认 Runtime，不改变能力包受众或 Provider 配置；
- G5 本机工程门已完成；真实目标 Linux/GPU 服务器验收在未来实际部署时执行；
- 当前 Python 环境仍有已知依赖告警，本次没有静默升级依赖。
