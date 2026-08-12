# 对话转向与渐进式进度 AC-00～AC-03 执行报告

> 日期：2026-08-02
> 状态：`engineering_verified_pending_user_acceptance`
> 范围：仅 AC-00～AC-03；不包含 AC-04 能力目录、能力获取、SOP 学习或平台发布

## 1. 本轮结论

运行中的工作台任务现在可以继续接收追问。状态和依据问题只读当前持久化事实，不改变
`run_id` 或 `revision`；非专业表达先保留不可变原话，再由当前用户选择的模型生成结构化
`ContextDelta`。来源、选择范围、覆盖、字段含义、输出、权限或外发发生实质变化时，只生成
待确认草案，不直接改写当前任务。

用户确认草案时可以选择：

1. 立即停止旧 Run 并创建 V2/V3；
2. 当前原子步骤完成后切换；
3. 保持当前 Run 不变，创建独立任务。

工作台进度统一投影为“理解要求、检查来源、准备能力、执行、验证、交付”六个顶层阶段，
详细行动按需展开。普通用户与管理员基于同一事实事件获得不同安全视图；普通用户不会收到
Token、Secret、宿主路径、命令、控制台或原始 Prompt。界面和事件不使用 Emoji。

## 2. 已验证事实

### AC-00 契约与夹具

- 新增冻结契约：`CapabilityPack`、`AutomationProcedure`、`AcquisitionRun`、
  `RawUserTurn`、`ContextDelta`、`RevisionProposal`、`RevisionDecision`、
  `StructuredProgressEvent` 和 `CompiledContext`；
- 个人/平台作用域与成熟度正交；平台对象拒绝个人 Owner 和任务引用；
- SQLite 与内存 Adapter 穿过同一 `ConversationSteering` Interface；
- 冻结 24 条对话语义样本和 12 条能力/权限样本；
- 新增只建表、不删旧数据的幂等迁移草案；内存 dry-run 连续执行两次，旧表记录保持不变。

### AC-01 非破坏性追问

- `POST /api/semantic-workspace/tasks/{task_id}/turns` 先保存原话，再生成 Delta 和最终回执；
- 相同 Owner、Task、幂等键和原话只处理一次；冲突重放失败关闭；
- 外部 Provider 未确认时不调用外部模型，返回权限请求；本地失败不切换外部 Provider；
- 状态/依据回答只使用任务状态、事件摘要和选择依据；没有相应依据时明确说明没有，禁止
  用 OCR 或其他邻近事实编造模型选择理由；
- 本地 `Qwen3.6-35B-A3B` 冻结语料连续三轮 `24/24、24/24、24/24`。

### AC-02 Revision 差异与安全点

- `SemanticDiffGate` 基于结构化差异，而非关键词路由；
- 实质变化只保存草案，用户确认前活动 Run 和 revision 不变；
- “立即停止”保留旧 revision/Run 审计记录，创建新 revision 后再排队；
- “步骤结束后切换”把决策持久化，进程重开仍可恢复；Legacy 工作台在语义编译和来源绑定
  后切换，Pi 在 `tool.completed` 后切换并显式终止旧容器；旧工作区不会登记为新候选；
- “独立任务”复制已授权来源与不冲突配置，当前 Run 不取消；外部连接仍要求对新任务单独
  确认外发并冻结连接版本。

### AC-03 结构化进度与 UX

- GET 详情和 SSE 使用同一 `ProgressProjection`；重复、乱序旧事件可恢复为唯一活动阶段；
- 工作台与 Harness 的独立 sequence 在兼容投影中重新形成单一事实顺序；
- 只有真实 `total` 存在时才展示分母；未知总量只显示已处理数量；
- 输入框在运行中、终态和查看历史版本时保持可用；历史版本场景明确说明追问基于最新版本；
- Revision 草案在输入框下方提供三种清晰选择；行动记录渐进展开；
- 深浅主题、1366 宽度、键盘基础路径和严重/致命 axe 门通过。

### 插入修补：DeepSeek 0731 正式版

- DeepSeek 官方直连模型 ID 保持 `deepseek-v4-flash`；设置页显示名更新为
  `DeepSeek V4 Flash（0731 正式版）`，避免使用 DeepSeek 官方模型列表中不存在的
  OpenRouter 风格 dated ID；
- 只有 DeepSeek Preset 目录版本更新为 `2026-08-02.1`；其他 Provider 保持原目录版本，
  旧连接不会被静默改写或要求重填 Key。

### 审查后失败关闭补强

- 混合追问即使被模型标记为“状态询问”，只要结构化差异改变业务语义，仍生成待确认草案；
- 外部连接的新版本确认在取消旧 Run 之前校验，未确认时返回 422 且旧 Run 保持不变；
- 普通用户进度投影改为字段白名单，未知引用默认不透出；
- 增加对话记录恢复和草案拒绝 Interface，拒绝/过期草案不影响当前 Run；
- SSE 网络错误保持协议重连，不把一次断线误判为任务终止。

## 3. 验证证据

| 门禁 | 结果 |
|---|---:|
| AC-00～AC-03 + 工作台/Pi 工作台后端 | 39 passed |
| 模型连接全量测试 | 48 passed |
| Agentic Runtime + 工作台遥测 | 25 passed |
| 工作台完整 Playwright | 20 passed |
| 前端 TypeScript + Vite 生产构建 | passed |
| 本地 Qwen 冻结语料三轮 | 24/24 × 3 |

真实模型评测明细曾保存在本地
`.artifacts/conversation-steering-evaluation.json`，不作为正式 Delivery 或用户业务制品；
2026-08-04 工作树收口时已按可重建测试生成物清理，正式报告只保留上述汇总门禁。

## 4. 基于代码的推断

- 新版本继续使用相同上传引用，覆盖发现缓存按既有 Owner + 原件哈希规则可复用；但本轮没有
  为所有来源类型新增“缓存复用率”指标，不能声称所有任务都避免重读；
- Pi 安全点以完成的工具事件为原子边界，能够避免中途改写目标；第三方工具若错误地在真正
  完成前发送 `tool.completed`，仍需由该 Adapter 的契约测试约束；
- 旧 `/revisions` 写接口仍为终态任务和旧客户端保留，新工作台不再使用它。运行中产品路径
  已统一走 ConversationSteering。

## 5. 尚未验证与明确未做

- 用户尚未在真实工作台手动验收追问、三种草案选择和行动流；
- 未做跨进程真实 Pi 容器在安全点切换的人工操作验收；自动化已覆盖决策恢复和 Runtime 回归；
- 未进入 AC-04～AC-10：没有实现能力目录、联网获取、Tool/MCP/Skill Adapter、SOP 学习、
  平台审核或新一级导航；
- 没有执行真实外部 Provider Smoke，没有新增外发授权；
- 用户已授权建立并推送 `v0.0.8` 分支；没有创建同名标签、封板或 PR；
- 整个 Phase 4、正式 Publisher、完整 PG-05 和服务器 8B 仍未完成。

## 6. 用户验收建议

在 `/data-prep` 选择一个运行中的本地 Pi 任务，依次操作：

1. 询问“现在做到哪了”和“为什么用了 OCR”，确认 Run/revision 不变；
2. 输入“增加部门字段”，确认先出现草案，不立即中断任务；
3. 分别验证“当前步骤结束后切换”和“作为独立任务”；
4. 刷新页面，确认等待安全点的选择和行动记录仍存在；
5. 在设置页确认 DeepSeek 显示 0731 正式版，已有 Key 无需重填。

用户完成以上代表操作前，本轮状态保持
`engineering_verified_pending_user_acceptance`。
