# AC-07-01 三轴治理投影与兼容读取执行报告

> 日期：2026-08-06
> GitHub：[Issue #33](https://github.com/Eclipseic1848/Mangrove_platform/issues/33)
> 状态：`completed_user_accepted_production_migrated_issue_closed`
> 边界：已执行带备份的生产纯新增迁移；未下载供应链工具、未生成密钥、未发布平台能力、未提交或推送

## 1. 交付结果

本轮以兼容扩展方式新增 `CapabilityGovernance` 深 Module，没有修改旧 `CapabilityPack`、
TaskRevision 或 OCI 制品：

- 新治理事实严格绑定 Owner、scope、Pack ID、version 和 digest，并使用只追加事件持久化；
- 成熟度固定为 `draft | verified`，生命周期固定为 `active | deprecated | revoked`，运行资格固定为
  `eligible | quarantined`；
- AC07-01 公开登记命令只能建立 `draft/active/eligible`，不能借登记绕过后续验证晋级或安全门；
- 没有治理事件的旧 Pack 只在读取时映射为 `legacy_compat`，不写回旧 payload；
- 管理员读取跨 Owner 精确 digest，普通用户只看到自己可见的个人/平台能力，响应删除 Owner 和
  digest；
- 管理员设置新增只读“能力治理”分区，展示三轴状态、版本、来源和精确 digest，不提供晋级、
  发布或风险操作。

## 2. SQLite 迁移边界

治理迁移是独立、显式入口，不在普通读取或任务启动时隐式执行。迁移按以下顺序失败关闭：

1. 使用 SQLite 在线备份 API 创建独立备份；
2. 备份连接完整关闭后，才在源库执行纯新增 `CREATE TABLE/INDEX IF NOT EXISTS`；
3. 同一数据库和备份路径的已完成请求可安全重放并返回原备份，不覆盖首次恢复点；
4. 备份目标已存在但治理 Schema 未建立、备份与源库相同或迁移前写事件，均拒绝继续；
5. 重复迁移不改写目录 Pack、冻结 Selection 或历史任务。

用户于 2026-08-07 明确授权后，生产 `data/webui.db` 已执行上述迁移：

- 迁移前备份：`data/backups/webui-before-ac07-20260807-003912.db`；
- 备份 SHA-256：`129a302c2c69d50b380fd00fbdf20cb48483be8f92c67213e328e65ab2d8f39a`；
- 源库与备份 `integrity_check=ok`，备份不含治理表，源库包含治理表及目标索引；
- Pack、Selection、Component、Validation 和 Procedure 五类旧数据迁移前后摘要完全一致；
- 同一数据库和备份路径重复调用命中安全重放，8088 `/api/health` 返回正常。

## 3. TDD 与验证证据

本轮按已确认的三个公共 Seam 逐条完成 Red → Green：

- CapabilityGovernance：精确 digest、幂等登记、三轴投影、Legacy 兼容和 Actor 隔离；
- 认证 HTTP Interface：普通用户裁字段、管理员跨 Owner 精确读取；
- 管理员设置浏览器流程：入口权限、只读状态和既有设置回归。

最终证据：

```text
CapabilityGovernance / Catalog / 设置权限聚焦后端：32 passed
Pi Runtime capability 聚焦回归：3 passed, 14 deselected
完整后端：1226 passed, 4 skipped
设置页完整 Playwright：11 passed
完整 Playwright：54 passed
前端 TypeScript + Vite 生产构建：passed
Python compileall + git diff --check：passed
```

独立 `code-review #33` 发现并已修复：治理登记改为强制携带精确 `CapabilityPackRef`，同名同版本
的平台包不再抢占个人 digest；显式迁移支持同一请求安全重放且不覆盖原备份；角色映射收敛为
共享失败关闭入口，并补齐 Legacy 映射和普通用户裁字段的安全原因注释。修复后新增证据：

```text
CapabilityGovernance / Catalog 聚焦回归：25 passed
Semantic Workspace API 回归：23 passed
仓库默认完整后端：1227 passed, 4 skipped
Python compileall + git diff --check：passed
```

四项 skipped 是需要显式开关的性能和真实 MySQL/PostgreSQL 测试，不是本轮失败。完整
Playwright 首次与完整后端并行时，一个既有模型连接用例因 Toast 与卡片同时匹配同一文字而发生
严格选择器时序冲突；该用例单独重放通过，顺序运行完整 Playwright 54/54 通过，未修改该无关用例。

## 4. 未完成与下一门

- Issue #33 已完成独立 `code-review` 及本地修复，用户于 2026-08-07 在 8088 验收通过；功能提交
  `4dd40e9d` 已推送，Issue 于 `2026-08-07T08:25:59Z` 关闭；
- 生产数据库已完成带备份纯新增迁移；当前尚无治理事件，旧 Pack 继续显示 Legacy 兼容投影；
- ValidationRun、Trivy/Syft、verified 晋级、平台快照、Cosign、运行时治理门和生命周期命令分别
  属于 #34～#44，不在本票实现范围；
- #34、#35 对 #33 的依赖已解除，#36 原本无阻塞；进入任何下一票仍需用户显式确认。
