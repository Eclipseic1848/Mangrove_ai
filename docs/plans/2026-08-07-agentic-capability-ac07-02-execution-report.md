# AC-07-02 可恢复能力验证运行执行报告

> 日期：2026-08-07
>
> 对应工单：GitHub Issue #34
>
> 状态：`production_migrated_pending_user_acceptance`
>
> 边界：本报告只证明 #34 工程纵切面及带备份纯新增迁移；未把任何能力晋级为
> `verified`，未发布平台能力，也未进入 #35～#44。

## 1. 本轮交付

- 新增精确绑定 Owner、Pack、version、digest、Actor、TaskRevision 和幂等键的
  `CapabilityValidationRun`，并把合成 Smoke、真实任务重放、失败关闭、独立 Verifier 和
  资源清理拆成独立证据步骤；
- 新增 SQLite 运行、幂等别名和 digest Lease 持久化。相同 Owner + digest 的活动请求合并，
  响应丢失后的同键重试仍返回原 Run；worker/进程中断后从已持久化步骤继续；
- 真实任务选项与重放重新校验 Owner、完成 Revision、冻结能力选择、来源快照、输入 hash、
  正式输出 hash 和当前授权。外部模型连接只有原 TaskRevision 已确认数据外发时才允许重放；
- 生产重放在独立临时状态域运行冻结 Pi 请求，只装载目标 digest，并要求本轮实际调用目标
  Tool/MCP、产生新候选且再次通过独立 Verifier；历史 Candidate 或历史 Verifier 不能替代本轮证据；
- 取消、失败和恢复都执行确定性清理。Docker 容器、网络、Capability Host、模型短期 Grant
  和临时目录分别尝试；Docker daemon/CLI 错误不得冒充资源不存在。清理未完成时 Run 保持
  可恢复状态，Lease 到期后由 worker 幂等重试，不会过早写成终态；
- 设置页新增 Owner 自己的能力验证入口；管理员保留跨 Owner 只读治理视图。界面按步骤渐进
  展示受控证据与缺口，并明确真实任务重放可能再次调用原模型、消耗 Token，但不会覆盖原任务
  或自动发布平台能力。

## 2. 安全与产品边界

- 普通用户只能发起、查看、取消自己的个人能力验证；管理员/超级管理员可查看跨 Owner 的
  任务管理信息，但不能代替 Owner 发起或取消；
- AC-06 把两项预验证灰度包登记成历史平台兼容身份，因此 #34 增加严格过渡桥：仅
  `gray-python-table@1.0.0` 与 `gray-everything-mcp@2026.7.4`，且同时带
  `admin_gray_only` 和 `created_by=ac06-gray-preparation` 时，管理员/超管才可使用自己拥有的
  TaskRevision 发起验证；其他平台包、普通用户和跨 Owner 任务仍失败关闭；
- 对外证据只保留受控引用、步骤状态和摘要，不暴露业务文件名、宿主路径、连接标识或 Secret；
- 一个验证成功只形成验证运行事实，不改变三轴治理状态；能力晋级属于 #37，平台发布属于 #39；
- 当前可达工作台任务必须包含上传来源，因此未新增 `source_refs`-only 重放路径；这不是 #34
  的可达产品契约，也不能借本票扩大数据来源范围；
- 未引入新的第三方库。现有 Pi Runtime、Capability Host、SQLite、Docker 和 Playwright 已能
  完整承载本票，额外引入框架只会增加迁移与运行风险。

## 3. 验证证据

- 聚焦后端：`69 passed`；
- 全仓后端：`1243 passed, 4 skipped`；跳过项仅为需显式开启的大规模性能测试和真实数据库容器；
- 前端生产构建：TypeScript 检查与 Vite 构建通过；
- 完整 Playwright：`54 passed`，覆盖三角色设置、能力验证入口与既有工作台回归；
- Standards 与 Spec 两轴独立 code-review 均通过；
- `git diff --check` 无错误，仅有 Windows 工作区既有 LF/CRLF 提示。

## 4. 尚未执行

1. 未在 8088 使用 Python 表格 Tool 与 Everything MCP 各创建一次真实能力验证 Run；该操作会
   重放 Owner 原任务并可能再次调用原模型，应由用户明确触发；
2. 未提交或推送 Git，未更新/关闭 GitHub Issue #34；
3. 未开始 #35 Trivy/Syft、#36 Cosign、#37 晋级或后续平台发布。

## 5. 生产迁移证据

用户于 2026-08-07 明确授权后，生产 `data/webui.db` 已执行 #34 纯新增迁移：

- 迁移前备份：`data/backups/webui-before-ac07-02-20260807-173433.db`；
- 备份 SHA-256：`e210df10301ad7ce36dbacc58d230f42c7590deae7649e4e97dde12d85056c2f`；
- 源库与备份 `integrity_check=ok`；备份不含 #34 三表，源库包含
  `capability_validation_runs`、`capability_validation_idempotency`、
  `capability_validation_leases` 及 `idx_capability_validation_owner_target`；
- 迁移前后 `capability_selections=1`，三个新表初始均为 0 行；
- 同一备份路径安全重放返回原备份且 SHA-256 不变；8088 `/api/health` 正常。

## 6. 下一人工控制点

生产迁移已完成。下一步由 Owner 在 8088 分别对 Python 表格 Tool 与 Everything MCP 选择一条
已完成且冻结该 digest 的真实任务，验收进度、
取消/恢复、证据缺口和零残留；通过后才能关闭 #34，并决定是否进入 #35。

## 7. 2026-08-07 灰度纠偏

用户从设置页发起验证后，真实运行暴露出“任务只冻结过能力、但本次运行并未成功调用目标能力”
仍会进入下拉框的问题。已将候选条件收紧为：必须在正确 Runtime run segment 中存在目标原生
Tool/MCP 的成功 `tool.completed` 事件；仅选择、挂载、调用失败或由其他文档工具完成任务均不能
作为该能力的真实重放证据。当前生产投影下两项灰度包均为 0 条合格历史任务，因此旧 PDF 任务
不再误导用户，也不会再次产生无意义 Token 消耗。

同时确认当前 [`@modelcontextprotocol/server-everything@2026.7.4`](https://www.npmjs.com/package/%40modelcontextprotocol/server-everything)
是 MCP 协议能力测试服务器，
不是 Voidtools Everything 文件搜索服务。它只能作为协议型能力验证样本，不能向用户表述为
可用于真实本地文件搜索的业务能力；后续若替换为业务型 Everything MCP，必须单独调研、冻结
新 digest 并重新验证，不能静默替换现有包。
