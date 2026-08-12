# ADR-0012：语义任务采用强类型计划与有界工具 Loop

- 状态：已采纳并落地；固定前置计划与禁止任务临时代码于 2026-07-29 被 ADR-0017 部分取代
- 日期：2026-07-24
- 决策来源：Phase 4B 语义任务 Harness 专项执行计划

## 背景

Phase 4A 的 `ResultContract` 可以区分字段、记录、原表、连续文档和汇总结果，但不能完整表达
过滤、投影、行粒度、合并、聚合、内容政策和可执行后置条件。真实“谢超群”任务因此进入
完整原表分支，绕过了用户要求的行过滤和列投影，错误结果仍因“非空”被判定为完成。

继续增加关键词和结果形态分支会把同类故障扩散到 Word 条款整理、跨文档比较和后续媒体任务。
系统需要一份来源无关、可绑定、可验证且不可被工具静默改写的任务契约。

## 决策

### 三层计划

1. `SemanticTaskPlan` 保存用户语义和后置条件，不绑定具体工具。
2. `BoundPlan` 把语义字段绑定到真实制品、表、列、章节或元素，并保存绑定证据。
3. Physical Plan 在后续批次选择能力包、批次和并发；它可以重建，但不得改变 Logical Plan。

逻辑计划和绑定计划按 revision 保存，使用规范化 JSON 的 SHA-256 形成身份。任何会改变范围、
过滤、行粒度、聚合或内容政策的修复都必须创建新 revision。

### 受控工具协议

- 每个工具必须登记 `CapabilityManifest`，声明输入、输出、操作、确定性、证据保留、
  网络、副作用、资源等级、上限和健康检查。
- LLM 只能选择已登记能力并生成符合 JSON Schema 的参数，不能直接执行任意 Python、
  Shell、SQL 或文件操作。
- `ToolResult` 必须返回制品引用、账本、lineage、工具版本、资源使用和机器可验证 facts。
- 只有 `VerificationReport.status=pass` 才允许登记权威输出；模型或工具自报成功无效。

### 有界 Loop

运行顺序固定为：

```text
interpret → inspect → bind → plan → execute → verify → repair → deliver
```

- 暂时性错误可按策略重试；
- 不改变用户语义的确定性修复可自动执行；
- 语义修复最多两次；
- 同一失败指纹连续出现时立即停止；
- 需要改变范围、粒度、聚合、外发或输出格式时必须询问用户。

### 内容与风险边界

- 默认 `content_policy=verbatim`；整理成 DOCX/PDF 不自动等于总结或改写。
- 默认只使用本机或 LAN。外部 OpenAPI 必须先记录用户确认。
- Phase 4B 工具只读或无副作用，不允许业务写入。
- 来源文本、文档、HTML、OCR 和模型输出都是不可信数据，不能改变目标、工具权限和网络策略。

### 不可信转换 sidecar 基线

LibreOffice、Pandoc、Docling、Tika、Node 转换器等重工具必须与主 Python 进程隔离，并满足：

- 非 root 用户、只读根文件系统、禁止 privileged 和宿主 Docker Socket；
- 输入目录只读挂载，输出仅写任务专属目录，禁止挂载项目根、用户目录和凭证目录；
- 默认 `network_mode: none`；确需 LAN/外网的能力必须使用单独 profile 和显式允许列表；
- 设置 CPU、内存、PID、临时磁盘、输入字节、执行超时和并发上限；
- 临时目录使用有上限的 tmpfs 或任务专属目录，任务结束清理；
- 镜像固定版本和 digest，健康接口返回工具版本；
- stdout/stderr、错误、trace 和 Manifest 不记录业务正文或凭证明文；
- 超时、OOM、损坏输出和进程异常必须转成结构化 `ToolResult`，不能返回模糊成功。

## 后果

- 正面：表格、文档、转换和后续媒体任务共享同一任务语义、工具和验证协议。
- 正面：过滤、投影、粒度和聚合可独立测试，质量门能阻断语义错误和假成功。
- 正面：工具可以替换或 A/B，而不会改变用户的 Logical Plan。
- 代价：执行前需要来源检查和绑定；任务会多保存计划、调用、验证和 lineage 制品。
- 代价：重型转换工具需要独立容器和资源治理。
- 边界：Binder、表格/文档执行器、完整后端 Harness 和正式文件交付已在 Phase 4B
  批次 2–6 落地；正式前端和扩展封板评测仍分别属于批次 7/8。

## 当前契约

- `src/semantic_harness/models.py`
- `src/semantic_harness/harness_models.py`
- `src/semantic_harness/harness_graph.py`
- `docs/schemas/SemanticTaskPlan.json`
- `docs/schemas/BoundPlan.json`
- `docs/schemas/CapabilityManifest.json`
- `docs/schemas/ToolResult.json`
- `docs/schemas/VerificationReport.json`
- `docs/schemas/HarnessRun.json`
- `docs/schemas/HarnessLoopPolicy.json`
- `docs/schemas/RepairProposal.json`
- `docs/schemas/RepairDecision.json`
- `docs/schemas/HarnessQuestion.json`
- `docs/schemas/HarnessResume.json`

## 部分取代说明（2026-07-29）

[ADR-0017](0017-agentic-runtime-vnext.md) 继续保持本 ADR 的强类型工具、证据、预算、
幂等、用户确认、独立验证和正式交付边界，但 vNext 不再用单一前置
`SemanticTaskPlan + TaskFamily` 锁定整个执行链。vNext 先观察真实来源，再在不可变
`GoalContract` 内动态更新执行草案，并允许通过任务级 Docker 沙箱运行受控临时代码。

本文定义的 Legacy Harness 继续运行，直到 vNext 通过严格门并经用户明确确认切换。

## 2026-07-30 统一任务域补充

[ADR-0018](0018-unified-task-domain-contract.md) 进一步取代本 ADR 中以 `TaskFamily`
作为跨来源、跨模态业务分类和 vNext 路由依据的部分。Legacy 仍可读取原字段；新任务修订
按来源通道、内容模态、任务操作、证据策略和交付形式表达，且只有独立发布模块能创建正式
Delivery。

## 相关

- [ADR-0002：原始制品不可变](0002-raw-artifact-immutable.md)
- [ADR-0003：LLM 边界](0003-llm-boundary.md)
- [ADR-0007：语义抽取必须绑定证据](0007-evidence-bound-semantic-extraction.md)
- [ADR-0017：数据工作台引入来源驱动的 Agentic Runtime](0017-agentic-runtime-vnext.md)
- [ADR-0018：统一任务域采用正交五轴模型与独立发布权](0018-unified-task-domain-contract.md)
- [Phase 4B 专项执行计划](../plans/2026-07-24-phase4b-semantic-task-harness-plan.md)
