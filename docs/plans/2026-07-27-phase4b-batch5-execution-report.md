# Phase 4B 批次 5：有界 Harness Loop 执行报告

> 日期：2026-07-27
>
> 分支：`v0.0.6`
>
> 状态：后端灰度入口已完成；未替换 Phase 4A 正式流程
>
> 功能提交：`826df13`

## 1. 本批交付

批次 5 已把批次 1–4 的语义计划、来源绑定、表格执行和文档证据执行串成同一套后端
Harness：

```text
interpret → inspect → bind → plan → execute → verify
                                      ↑          ↓
                                      └─ repair ┘
                                            ↓
                              needs_user / deliver
```

主要实现：

1. `HarnessRun`、`HarnessLoopPolicy`、`RepairProposal/Decision`、
   `HarnessQuestion/Resume` 强类型契约及六份 JSON Schema；
2. LangGraph 九节点控制流、SQLite Checkpointer、稳定
   `thread_id=run_id` 和 `interrupt/Command(resume=...)`；
3. 表格 `table.duckdb` 与文档 `document.evidence` 统一能力适配器，复用既有编译器、
   执行器和确定性验证器，不复制 DuckDB 或文档业务逻辑；
4. 暂时性、资源耗尽、工具不兼容、无效计划、数据不足、需用户和政策拒绝七类失败；
5. 暂时性故障最多重试 2 次、语义重规划预算最多 2 次、总修复轮数最多 5 次、
   同一失败指纹连续 2 次硬停；
6. `semantic_harness_runs/attempts/events` 三张业务审计表与 LangGraph checkpoint 分离；
7. 执行幂等键、append-only 尝试/事件、验证失败不允许权威交付；
8. 独立 `/api/semantic-harness` 灰度 API，支持创建、状态、事件、尝试和恢复，全部按
   `user_id` 隔离；
9. 外部 OpenAPI 按当前 run 再次结构化确认，任意自由文本不能绕过外发确认；
10. `deliver` 只设置 `eligible_for_delivery=true`，不生成或开放正式下载。

## 2. 关键故障与修复

首轮实现把批次 3 的执行 Graph 嵌套在带 SQLite checkpointer 的外层 Graph 中，
LangGraph 继承上下文后尝试序列化内部 `ExecutionBundle`，报：

```text
Type is not msgpack serializable: ExecutionBundle
```

Harness 按策略执行一次物理计划重编译，并在同指纹第二次出现时停止，没有形成无限循环或
假成功。最终修复是让能力适配器直接复用既有编译器、执行器和验证器，只把
`ToolResult/VerificationReport` 的 JSON 契约交给外层 Graph；没有放宽 checkpoint
序列化，也没有把大对象或服务端路径塞进状态。

## 3. 自动化门禁

### 3.1 批次 5 与契约门

- 批次 5 扩展定向：`21 passed`；
- Phase 4B 批次 -1 至 5 定向：`78 passed`；
- JSON Schema 均通过 Draft 2020-12 校验；
- 表格与文档成功链均通过同一 Harness；
- 临时超时：首轮失败、一次 append-only 修复、第二轮成功；
- 固定无效计划：同指纹连续两次后硬停；
- 外部边界：暂停前能力执行次数为 0，恢复令牌和固定选项校验后继续；
- 重复 resume 返回冲突，不产生第二份结果；
- 跨用户 run、events、attempts 和 resume 均返回 404；
- 客户端提交 SQL、物理路径或循环策略返回 422。

### 3.2 全仓与前端

- 全仓后端：`925 passed, 4 skipped, 0 failed`，209.50 秒；
- 默认跳过仍是显式性能门和 MySQL/PostgreSQL 真库门，没有新增 skip/xfail；
- 前端 `tsc --noEmit && vite build` 通过；
- `git diff --check` 通过；
- Phase 4A 正式路由和前端入口未被替换。

## 4. 依赖实况

实机版本：

- Tenacity `9.1.2`
- LangGraph `1.0.5`
- langgraph-checkpoint-sqlite `3.0.3`
- DeepDiff `9.1.0`
- Pydantic `2.12.5`

`pip check` 当前返回四条：

1. `pandas-stubs` 缺少 `types-pytz`；
2. `crawl4ai 0.9.0` 要求 `lxml~=5.3`，当前为 `6.1.1`；
3. `spider-dcd 0.1.0` 要求 `httpx<0.28`，当前为 `0.28.1`；
4. `spider-dcd 0.1.0` 要求 `tenacity<9`，当前为 `9.1.2`。

`spider-dcd` 是 `E:\PythonProject\spider_dcd` 的外部可编辑安装，不在 Mangrove
`requirements.txt` 中，仓库生产代码也没有导入它。本批没有越权修改或卸载另一个项目；
后续应通过项目独立虚拟环境彻底隔离共享解释器污染。

> **会话收尾更新（2026-07-27）**：用户确认该外部包不属于 Mangrove 后，已从
> `E:\python3.13` 共享解释器卸载其 editable 安装；外部源码目录仍保留。复核确认
> distribution、import spec 和 site-packages 残留均为空。当前 `pip check` 只剩两条：
> 缺少 `types-pytz`，以及 crawl4ai/lxml 版本约束不兼容。本节前四条仍保留为批次 5
> 验收当时的历史环境证据，不应解释为当前状态。

## 5. 明确边界

- 没有正式 DOCX/PDF/XLSX/PPTX 渲染、重开 QA 或下载；
- 没有正式前端进度、暂停问题或修复轨迹页面；
- 没有替换 Phase 4A 当前数据准备执行入口；
- 没有接入旧 Conductor、认证来源、图片、音频或视频；
- 当前生产注册表每类能力只有一个执行工具，因此“兼容工具切换”只保留强类型动作和安全
  策略边界；没有伪造不存在的替代工具；
- 语义重规划预算已冻结为最多 2 次，但任何可能改变用户目标的重规划仍要求新 revision，
  不原地覆盖当前计划；
- `eligible_for_delivery` 不是正式交付成功，批次 6 必须完成输出、重开 QA、Manifest 和下载。

## 6. 下一步

进入批次 6“输出、转换和下载闭环”，消费已经验证通过的
`eligible_for_delivery=true` 结果，完成常用结构化/文档输出、重开或渲染 QA、Manifest、
下载权限和幂等交付。批次 7 再把运行状态、问题弹窗和修复轨迹接入正式前端。
