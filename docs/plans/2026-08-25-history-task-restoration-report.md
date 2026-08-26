# 历史任务详情恢复兼容报告

> 状态：`ENGINEERING_VERIFIED`
>
> 核验日期：2026-08-25
>
> 固定基线：`7efaf2fd78a8f1df0b86929b19e27cf0a7b5ca03`

## 问题与边界

生产任务列表可以列出部分旧任务，但详情投影会在计算 Candidate 重验资格时抛出
`ReverificationContractError`。前端没有详情错误态，因而把接口失败显示成空白区域。

本次只修复历史详情读取和错误反馈，不回填、推断或改写旧冻结运行信息；缺少可信冻结上下文的
Candidate 仍不得重新验证，也没有创建 Attempt、调用 Provider 或发布 Delivery。

## 实现结果

- 详情投影只在最新不可变 Attempt 能证明为 `legacy_unversioned` 时降级读取；任务、版本、事件、
  Candidate 和最新 Attempt 继续可读。现代 `versioned` 记录损坏仍显式失败，不被兼容分支掩盖。
- 重验 Offer 保持关闭，并返回“该历史任务缺少可证明的冻结运行信息，暂不能重新验证”。
- 工作台详情失败时显示持久错误面板、可理解原因和“重新加载”，不再退回空白占位。
- Candidate 页面显示重验不可用原因，不提供“使用最新规则重新验证”按钮。

## 验证证据

- 后端工作台回归：`34 passed`，包含 legacy 正向兼容和 versioned 损坏反向门控。
- 前端数据工作台 Playwright：`29 passed`，含持续失败、手动重试恢复和旧 Candidate 失败关闭。
- 前端 `npm.cmd run build`：通过。
- 生产库只读恢复探针：23 条未删除任务 `23/23` 详情可读；11 条旧任务明确标为暂不能重验，
  且 `11/11` 均保留最新 `legacy_unversioned` Attempt 投影。
- 生产数据库逻辑指纹前后均为
  `5f7cb89eb7aff840460e2b4ab61d28c14d123ec0f4aa5be8737bcbd1f75a7473`。
- 8088 后端已重启加载新代码；8088 与 5173 均返回 HTTP 200。
- Standards 与 Spec 双轴复审均确认先前问题已关闭，无剩余可复现问题。
- Frontend Design Premium 严格审计仍为既有 26 项；与现有审计快照的规则 ID 和文件集合一致，
  本次没有新增审计项。无关旧债未修改。

## 未解决事项

本修复只恢复“读取”。目标 `liyi111` Candidate 的 `legacy_unversioned` 资格阻断仍然成立；是否
建立正式 `legacy_rebaseline` 能力仍需独立规格和用户确认，不能由本修复推断进入 Gate B。
