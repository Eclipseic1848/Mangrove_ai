# Phase 4B 批次 5：有界 Harness Loop 详细实施方案

> 状态：已完成（2026-07-27）
>
> 日期：2026-07-27
>
> 当前分支：`v0.0.6`，未封板、无同名标签
>
> 实施入口：后端灰度 API，不替换 Phase 4A 当前正式流程
>
> 前置批次：Phase 4B 批次 -1 至 4 已完成

## 1. 本批目标

把已经完成的语义计划、来源检查与绑定、确定性表格执行、证据文档执行连接为一个真实、
可恢复、可验证且不会无限循环的后端 Harness：

```text
interpret → inspect → bind → plan → execute → verify
                                      ↑          ↓
                                      └─ repair ┘
                                            ↓
                                         deliver
```

这里的 `deliver` 只表示“验证通过、允许进入后续交付层”，不生成正式 DOCX/PDF/XLSX，
也不开放正式下载；文件渲染、重开 QA 和下载属于批次 6。

## 2. 已确认产品决策

1. 批次 5 采用独立后端灰度入口，不接管 Phase 4A 当前正式流程。
2. 首批只接入已经具备强类型计划和验证器的表格、文档能力；旧 Conductor、认证网站和
   图片/音视频后续按相同能力协议注册。
3. 不改变用户目标的技术修复可以自动执行；可能改变范围、字段、结果含义或权限时必须暂停询问。
4. 本地/LAN 模型失败时不得自动切换 DeepSeek、阿里百炼等外部 OpenAPI；只有用户确认
   具体外发内容、服务和目的后才能继续。
5. 允许保留诊断性部分结果，但整体状态必须为未通过，不得登记权威交付。
6. 每一步持久化并支持服务重启后恢复；同一步重复触发不得生成重复正式结果。
7. 临时故障最多重试 2 次，语义重新规划最多 2 次，同一失败指纹连续出现 2 次停止，
   单任务总修复轮数最多 5 轮。
8. 每次修复保留 append-only 审计记录；原始正文、Cookie、API Key 默认不进入日志。
9. 后端实现结构化暂停/恢复契约；批次 7 只负责把它接成用户界面。

## 3. 成熟组件选型

本批不引入新的 Agent 框架：

| 需求 | 复用组件 | 用法 |
|---|---|---|
| 状态图与条件路由 | LangGraph 1.0.5 | 统一九节点主图和有界回路 |
| 暂停/恢复 | LangGraph `interrupt` + `Command(resume=...)` | 需要用户、外部 API 确认时暂停 |
| 持久化 | langgraph-checkpoint-sqlite 3.0.3 `AsyncSqliteSaver` | 每个节点 checkpoint，稳定 `thread_id=run_id` |
| 暂时性重试 | Tenacity `AsyncRetrying` | 只重试明确分类的暂时性/资源错误 |
| 强类型边界 | Pydantic 2 现有契约 | 所有节点输入、修复动作和暂停答案先校验 |
| 差异审计 | DeepDiff 9.1.0 | 比较修复前后计划，不负责执行修改 |
| 执行/验证 | 现有表格和文档 Graph | 作为 Harness 的能力适配器，不复制业务逻辑 |
| 运行记录 | 现有 SQLite `WebUIStore` | 用户隔离、append-only 运行与事件账本 |

`jsonpatch` 虽已安装，但不允许模型提交任意 JSON Patch 直接改计划。模型只能提出强类型
`RepairProposal`，由策略门判断后交给确定性修复器生成新 revision。

本批不需要新增 npm 包、Node sidecar、Celery、Redis、Kafka、Kubernetes、Docling 或
LibreOffice。

实机探针确认 LangGraph 1.0.5、langgraph-checkpoint-sqlite 3.0.3、DeepDiff 9.1.0 和
Pydantic 2.12.5 与仓库一致，`interrupt/Command/AsyncSqliteSaver` 可导入。仓库要求
`tenacity==9.1.2`，当前 Python 3.13 环境实际为 8.5.0；开始编码前必须按
`requirements.txt` 对齐为 9.1.2，并先跑受影响回归。不能把此漂移混入 Harness 故障。

## 4. 新增最小契约

### 4.1 HarnessRun

- `run_id/user_id/thread_id`
- 固定的 `logical_plan_id/revision/hash`
- 固定的 `binding_revision/hash`
- `capability_id/version`
- 状态：`running / needs_user / succeeded / failed`
- 当前节点、修复轮数、语义重规划次数、失败指纹重复次数
- 创建/更新时间和最终验证报告引用

### 4.2 HarnessLoopPolicy

- `max_transient_retries=2`
- `max_semantic_replans=2`
- `max_total_repair_rounds=5`
- `max_same_failure=2`
- `allow_external_api=false`

策略在 run 创建时冻结，恢复时不得被客户端静默改变。

### 4.3 RepairProposal / RepairDecision

修复类型仅允许：

- `retry_same_tool`：暂时性故障，原参数不变；
- `switch_compatible_tool`：同能力、同输入/输出契约的已注册工具切换；
- `rebind_source`：不改变语义字段的确定性或高置信重新绑定；
- `recompile_physical_plan`：逻辑计划不变，仅重新生成物理计划；
- `semantic_replan`：只能生成新逻辑 revision，最多两次；
- `request_user`：任何可能改变用户原意或权限的情况。

每个决定必须记录失败指纹、原因、修复前后哈希、是否改变用户语义、策略判定和证据。

### 4.4 HarnessQuestion / HarnessResume

- 一个最高价值问题；
- 暂停原因和受影响范围；
- 2–3 个候选项及自由文本能力；
- `question_id/run_id/checkpoint_id`；
- 回答 Schema 和恢复令牌；
- 外部 API 场景额外包含服务名、外发数据类型、目的和风险。

## 5. 九节点职责

### 5.1 interpret

- 读取服务端已保存的 STP revision，不相信客户端提交的计划正文；
- 校验计划哈希、预算、风险政策和未解决歧义；
- 未解决实质歧义直接进入 `needs_user`。

### 5.2 inspect

- 复用批次 2 Source Inspector；
- 优先命中相同制品哈希的不可变 inspection；
- 来源已变更或无权限时失败，不自动扩大来源范围。

### 5.3 bind

- 复用批次 2 Binder；
- 高置信且可验证的绑定自动通过；
- 同名对象、缺字段或多候选会改变结果含义时暂停询问。

### 5.4 plan

- 按任务类型编译现有 `PhysicalPlan` 或 `DocumentPhysicalPlan`；
- 所有工具参数必须通过 Pydantic Schema；
- 客户端不能提交 SQL、文件路径或未注册 capability。

### 5.5 execute

- 根据 CapabilityManifest 只调用 `tabular.duckdb` 或 `document.evidence`；
- 幂等键为 `run_id + node + attempt + input_hash`；
- 同一幂等键已有成功结果时直接复用，不重复写制品。

### 5.6 verify

- 只接受现有确定性 `VerificationReport`；
- 全部后置条件通过才允许成功；
- 部分结果保存为诊断制品，但 `authoritative_output_allowed=false`。

### 5.7 repair

- 先按 `FailureKind` 分类，再由策略矩阵选择动作；
- 先做不改变语义的最小修复；
- 失败指纹连续两次相同、预算耗尽或无安全动作时停止；
- 语义重规划必须保留旧 revision，禁止原地覆盖。

### 5.8 needs_user

- 使用 LangGraph `interrupt()` 保存结构化问题；
- 节点在恢复时会从头执行，因此暂停前的数据库写入和制品保存必须幂等；
- 用相同 `run_id/thread_id` 和 `Command(resume=...)` 恢复。

### 5.9 deliver

- 仅把已通过验证的内部结果标记为 `eligible_for_delivery=true`；
- 不生成正式查看副本，不开放下载；
- 批次 6 消费该状态完成渲染、重开 QA、Manifest 和下载。

## 6. 失败分类与处理矩阵

| FailureKind | 自动动作 | 上限 | 最终状态 |
|---|---|---:|---|
| transient | 同工具指数退避重试 | 2 次重试 | exhausted 后 failed |
| resource_exhausted | 短退避；已有等价低资源 profile 才能切换 | 2 次重试 | failed |
| tool_incompatible | 仅切换同契约已注册工具 | 每工具 1 次 | 无候选则 failed |
| invalid_plan | 先重编物理计划；必要时语义 replan | 语义最多 2 次 | needs_user/failed |
| insufficient_data | 不扩大来源、不编造数据 | 0 | needs_user/failed |
| needs_user | 结构化 interrupt | 无自动重试 | needs_user |
| policy_denied | 禁止绕过政策 | 0 | needs_user/failed |

Tenacity 只包裹远程调用或明确暂时性异常，不能对 `ValueError`、Schema 失败、权限失败或
验证不通过做笼统重试，避免把确定性错误放大成资源浪费。

## 7. 持久化与恢复

新增三张最小表：

- `semantic_harness_runs`：每个灰度任务一行，保存当前状态和冻结引用；
- `semantic_harness_attempts`：每次执行/验证/修复一行，只追加不覆盖；
- `semantic_harness_events`：面向后续前端的脱敏节点事件和自然语言说明。

LangGraph checkpoint 保存运行状态，业务表保存审计和查询视图，两者职责分离。恢复时：

1. 校验当前用户仍拥有 run 和全部来源；
2. 使用稳定 `thread_id=run_id` 读取最新 checkpoint；
3. 校验提交答案对应当前未完成问题；
4. `Command(resume=validated_answer)` 恢复；
5. 幂等门阻止暂停节点前置逻辑重复产生副作用。

SQLite 仅用于当前 Windows 灰度开发。未来服务器迁移 PostgreSQL Checkpointer 时保持
`run_id/thread_id` 和业务契约不变，不在批次 5 提前引入 PostgreSQL。

## 8. 灰度 API

建议新增独立路由 `/api/semantic-harness`：

- `POST /runs`：从服务端已保存的 `plan_id/revision` 创建并启动；
- `GET /runs/{run_id}`：读取状态、当前节点、预算和最终摘要；
- `GET /runs/{run_id}/events`：读取脱敏 append-only 事件；
- `POST /runs/{run_id}/resume`：提交结构化回答并恢复；
- `GET /runs/{run_id}/attempts`：读取失败、修复和验证轨迹。

所有接口按 `user_id` 隔离；不存在和无权访问统一返回 404。请求只提交 ID 和回答，
不能提交任意计划 JSON、SQL、文件路径、Provider URL 或密钥。

## 9. 安全边界

- 来源正文视为不可信数据，文档中的“忽略规则、调用外部工具”等文字不能影响控制流；
- CapabilityManifest、风险政策和循环预算只由服务端生成；
- 自动修复不得扩大 `artifact_ids/source_ids/pages/sections`；
- 不得新增写业务系统、发邮件、发消息或删除文件等副作用；
- 外部 Provider 必须已有用户确认记录，并绑定本次 run，不能复用模糊的历史同意；
- 日志默认只存 ID、哈希、计数、错误分类和脱敏摘要；
- checkpoint SQLite metadata filter key 只能使用服务端白名单常量。

## 10. 实施顺序

### 步骤 0：环境对齐

- 将当前 Python 3.13 的 Tenacity 8.5.0 对齐到仓库锁定的 9.1.2；
- 复核 LangGraph/Checkpoint/Tenacity 导入和版本；
- 运行现有 Phase 4B 定向回归，确认升级没有改变既有执行结果。

验收：运行版本与 `requirements.txt` 一致，现有批次 1–4 回归 0 failed。

### 步骤 1：契约和状态机

- 新增 HarnessRun、LoopPolicy、RepairProposal/Decision、Question/Resume；
- 导出 JSON Schema；
- 建立状态迁移和循环预算单元测试。

验收：非法状态跳转、超预算、未知修复类型和任意计划补丁全部被拒绝。

### 步骤 2：能力适配器

- 把现有表格/文档 Graph 包装为相同 Harness capability adapter；
- 统一 ToolResult、VerificationReport 和异常到 FailureKind 的映射；
- 不修改底层执行器业务逻辑。

验收：同一 Harness 输入可分别正确路由表格和文档，旧定向测试保持全绿。

### 步骤 3：主 Graph 与策略门

- 实现九节点 StateGraph、条件路由、失败指纹和修复策略；
- Tenacity 只处理暂时性异常；
- 连接总 5 轮、语义 2 轮、同指纹 2 次硬上限。

验收：所有路径必然到达 succeeded、failed 或 needs_user，不存在无限回边。

### 步骤 4：checkpoint、幂等和人工暂停

- 接入 AsyncSqliteSaver；
- 实现 interrupt/resume、节点幂等键和 append-only 审计；
- 用独立进程重启测试证明恢复。

验收：重启后从最后安全节点继续；重复 resume/请求不会生成重复结果。

### 步骤 5：灰度 API

- 新增 run/status/events/attempts/resume；
- 用户隔离、路径隐藏、错误脱敏；
- 不接入正式数据准备 UI。

验收：跨用户访问全部 404；客户端不能越过服务端保存的计划和来源。

### 步骤 6：Golden 与全仓回归

- 表格：筛选/投影成功、错列安全重绑、零行假成功阻断；
- 文档：原文、比较、审查成功，有证据语义失败后降级；
- 故障：临时超时、工具不兼容、计划错误、数据不足、需要用户、资源耗尽；
- 策略：外部 API 未确认、来源扩大、正文 Prompt Injection；
- 恢复：服务重启、重复调用、重复答案、过期问题；
- 兼容：Phase 4A 正式流程未被替换。

## 11. 封板门禁

批次 5 只有同时满足以下条件才能宣布完成：

- 固定 Golden 中假成功为 0；
- `succeeded` 的后置条件通过率 100%；
- 要求证据的结果证据覆盖率 100%；
- 所有修复路径均受 5 轮总上限约束；
- 同一失败指纹连续两次后必停；
- 外部 API 未确认时真实调用次数为 0；
- 需要用户的任务可跨进程重启恢复；
- 重复执行和重复 resume 不产生重复权威结果；
- 每次自动修复都能解释修改内容及为何不改变用户语义；
- 跨用户读取、恢复和事件访问均被拒绝；
- 全仓后端回归 0 failed；
- Tenacity 实际版本与仓库锁定的 9.1.2 一致；
- `git diff --check` 通过，3 项既有 `pip check` 冲突继续如实登记；
- Phase 4A 当前正式入口行为不变。

## 12. 本批不做

- 不替换正式数据准备入口；
- 不做正式前端进度、问题弹窗或修复轨迹页面；
- 不做 DOCX/PDF/XLSX/PPTX 正式渲染和下载；
- 不接入旧全网采集、认证来源、图片、音频或视频；
- 不自动切换到外部 OpenAPI；
- 不引入队列、分布式锁、PostgreSQL、Redis 或 Kubernetes；
- 本批不修复 MinerU Hyper high/device 和启动时登记的依赖冲突，除非它们实际阻断本批门禁。
  会话收尾时外部 editable 包已卸载，当前 `pip check` 还剩 2 条，与本批功能无关。

## 13. 对效果的实际影响

批次 5 主要提升“用户要求是否被完整执行”和“失败能否被正确修复或停止”，不会直接提升
MinerU/Paddle 的 OCR 精度，也不会改善最终文件版式。完成后应显著减少：

- 计划正确但执行遗漏过滤/列/章节；
- 工具返回内容就被误判成功；
- 失败后无边界反复重试；
- 修复时擅自改变用户范围或把敏感内容发到云端；
- 服务重启导致整项任务丢失或重复执行。

正式用户体验、可下载文件和版式效果仍需批次 6/7 完成后才能整体体现。

## 14. 实施结果

批次 5 已按后端灰度边界完成，执行证据见
`docs/plans/2026-07-27-phase4b-batch5-execution-report.md`。

- Phase 4B 定向：78 passed；
- 全仓后端：925 passed、4 skipped、0 failed；
- 前端生产构建通过；
- 表格/文档成功链、临时超时重试、同指纹硬停、外部确认暂停/恢复、幂等和用户隔离均有
  自动化门禁；
- Phase 4A 正式流程、正式下载和正式前端没有改动。
