# ADR-0018：统一任务域采用正交五轴模型与独立发布权

- 状态：`accepted`，用户于 2026-07-30 确认
- 日期：2026-07-30
- 决策来源：[Phase 4 D2 统一能力模型与领域契约](../plans/2026-07-30-phase4-unified-domain-contract.md)
- 若采纳将部分取代：[ADR-0012](0012-semantic-task-plan-and-bounded-tool-loop.md)、
  [ADR-0017](0017-agentic-runtime-vnext.md) 中仍依赖单一任务类别或未统一跨模态身份的部分

## 背景

现有 Legacy Harness 用 `TaskFamily` 和 `TABULAR/DOCUMENT` 来源种类锁定执行链，无法稳定
表达一个 PDF 同时含文本、表格和图片，也无法自然组合上传、URL、API、数据库和媒体。
继续增加任务类别会复制执行器、验证器和交付逻辑。

## 决策

1. 用 `来源通道 × 内容模态 × 任务操作 × 证据策略 × 交付形式` 五个正交维度表达任务；
   来源或模态都不再决定唯一 TaskFamily。
2. `SourceBinding` 表达获准读取的逻辑来源，`SourceSnapshot` 表达一次运行实际观察到的
   不可变来源身份，`Artifact` 表达可按哈希重开的内容，`ContentUnit` 只统一模态与位置
   引用而不建立巨型跨模态 AST。
3. `TaskRevision` 包含唯一、不可变的 `GoalContract`；Agent 只能修改执行草案。
4. 结果逐项区分 `SourceObservation`、`SourceView` 和 `DerivedResult`。
5. Agent 与工具只能生成 `Candidate`；独立 `CandidateVerification` Module 重新读取来源
   与候选；只有 `DeliveryPublishing` Module 拥有正式发布权。
6. `TaskFamily` 保留为 Legacy 兼容字段，不再参与 vNext 路由；旧任务、证据和 Delivery
   不迁移，新修订通过 Adapter 使用统一契约。
7. 模型连接把 `api_format` 与 `base_url` 分开；已识别格式为
   `anthropic_messages`、`openai_chat_completions`、`openai_responses` 和
   `gemini_generate_content`。只兼容 Chat Completions 的端点不得冒充 Responses
   兼容端点；原生调用或经网关转换也必须显式记录。

## 考虑过的替代方案

- **继续扩展 TaskFamily**：改动小，但每个新来源/模态都会复制路由和执行链，拒绝。
- **文本与多媒体各一条主链**：媒体应用看似独立，但 PDF、API 音频和文档图片会再次跨链，
  拒绝。
- **把所有内容压成一个统一记录结构**：会丢失页、表格、时间轴和图像区域的深结构，拒绝；
  只统一引用信封。

## 后果

- 表格、文档、图片、音频、视频和复合来源共享任务修订、候选、验证和交付边界。
- Source Adapter、AgentKernel Adapter 和模态读取器可以替换，而不改变业务契约。
- 需要新增统一身份/映射 Schema，并以兼容 Adapter 维持 Legacy 双轨。
- 精确状态机、个人连接、媒体工具、生命周期和生产门仍由 D3–D10 分别决定；本 ADR 不构成
  代码实施授权。
