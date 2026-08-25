# CV-08 普通用户重验与发布工作台工程验证报告

> 状态：ENGINEERING_VERIFIED
>
> 日期：2026-08-24
>
> 工单：GitHub #68
>
> 固定审查点：`51d327d54aa298ab734f30f106f5405bb12619de`
>
> 用户验收：尚未执行

## 1. 已验证完成

- 在既有 Candidate 卡片中展示服务端 `ReverificationOffer`、最新 Attempt、不可重验原因、
  `requested/running/outcome_unknown/passed-awaiting-publish` 和显式发布状态。
- Provider 重验在每个 Attempt 前使用 Radix AlertDialog 展示文件、规则变化、连接/模型、外发
  类别和费用提示；未勾选确认不能提交。本地重验明确显示“本次不外发”。
- 重验和发布是两个独立动作，分别使用按冻结业务身份稳定复用的 `reverify-*` 与 `publish-*`
  幂等键；busy 状态阻止重复提交且不改变按钮尺寸。
- 成功后的长期状态只来自 `task.agentic_runtime` 服务端投影；Toast 只做短确认。刷新后可恢复
  Attempt，`outcome_unknown` 会停止普通重试并提示先核对 Provider 状态和用量。
- 403、409、422、503 在对话框内保留上下文并给出可执行恢复建议，不向普通用户暴露内部路径、
  Secret 或技术堆栈。
- 对话框默认焦点为“取消”，支持 Escape 关闭并把焦点归还触发按钮；窄屏内容可滚动、操作按钮
  可达，新增加载动效遵守 reduced-motion。

## 2. 主要变更

- `frontend/src/types/semanticWorkspace.ts`
- `frontend/src/lib/semanticWorkspaceApi.ts`
- `frontend/src/components/workspace/ResultPreview.tsx`
- `frontend/src/pages/SemanticWorkspacePage.tsx`
- `frontend/e2e/semantic-workspace.spec.ts`

没有新增第三方依赖、设计系统或 `DESIGN.md`，也没有改变 CV-06/CV-07 冻结的 API、Owner 权限、
逐 Attempt 外发确认或显式发布语义。

## 3. 验证证据

| 验证集合 | 结果 |
|---|---:|
| TypeScript 检查与 Vite production build | exit 0 |
| CV-08 定向 Chromium E2E | 3 passed |
| 前端完整 Playwright E2E | 64 passed |
| Candidate 重验与 Workspace API 相邻后端回归 | 80 passed，2 warnings |
| `git diff --check` | exit 0 |
| Standards 最终复核 | 无剩余/新增 P1/P2 |
| Spec 最终复核 | 无剩余/新增 P1/P2 |

E2E 覆盖 Provider 确认、服务端状态恢复、未知结果、独立发布、错误恢复、键盘焦点、Escape、
深浅主题、axe、390 CSS px 窄屏和 reduced-motion。390 CSS px 比 1280 布局在 200% 缩放时的
640 CSS px 更严格；对话框高度和底部操作区均有可达性断言。

`frontend-design-premium` strict/no-write 审计仍报告 26 个既有错误，未发现本次新增阻断项；
其中所改页面的一项是既有永久删除触发器。未扩大 CV-08 范围处理旧设计债。

已用本地浏览器自动化核对 8088 可加载且非空白；独立浏览器配置不会继承维护者账号登录态，
因此没有把该探针冒充真实账号验收。Playwright 使用受控 Mock 完成可重复的产品流程验证。

## 4. 尚未验证与人工门

- 未迁移或写入生产数据库，未重验真实 Candidate，未发布真实 Delivery。
- 未调用真实 Provider、未发送业务数据、未产生费用，也未改变 P0 Rollout 状态。
- 未使用维护者账号完成真实浏览器产品验收。
- 未创建提交、推送、PR、标签或 Release；GitHub #68 未评论、改标签或关闭。
- 工程验证不等于用户验收、生产资格或发布资格。

下一工程依赖门是 CV-09：完成跨 CV-01～CV-08 的工程门、双轴审查和实施交接。生产迁移、
真实 Provider 外发、真实 Candidate 重验与正式发布仍只属于 CV-10 人工门。
