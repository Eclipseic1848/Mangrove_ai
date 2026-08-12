# Agentic 能力获取、SOP 与对话上下文规格

> 日期：2026-08-02
> 阶段：AC-00～AC-05 已完成；AC-06 用户灰度验收通过；AC-07 #33 已关闭，#34 工程验证通过
> 状态：`ac07_02_production_migrated_pending_user_acceptance`
> 架构决策：[ADR-0026](../adr/0026-agentic-capability-acquisition-and-procedure-governance.md)、
> [ADR-0027](../adr/0027-conversation-steering-and-context-compilation.md)、
> [ADR-0029](../adr/0029-capability-validation-lifecycle-and-platform-publication.md)
> 开源调研：[能力获取开源组件调研](../research/2026-08-02-agentic-capability-acquisition-open-source-research.md)
> AC-07 增量规格：[能力验证、成熟度与平台发布](2026-08-06-agentic-capability-ac07-spec.md)
> 本文自身不授权实现或发布；后续用户已单独授权 #33 完整收口及 #34 工程实现和生产迁移。
> #34 用户灰度、提交推送与关闭，以及真实供应链工具、平台发布、普通用户开放、版本和外部发布仍未授权。

## 1. 目标与范围

本规格把 Pi 从“只能使用平台预装能力”升级为“能够发现能力缺口、隔离获取工具/MCP/Skill、
验证后复用，并把成功经验沉淀为个人或平台自动化方案”的 Agentic Runtime；同时补齐任务
运行中追问、LLM 语义转写、上下文窗口治理和渐进式事件流。

首期目标：

1. Pi 在有界预算和明确来源策略下自主准备缺失能力；
2. 首次获取较慢，后续相同版本直接复用，不污染宿主或项目环境；
3. 原子能力包与可组合 SOP 分层，SOP 不退化为固定任务路由；
4. 个人方案严格 Owner 隔离，平台方案经管理员审核发布；
5. 用户运行中可以追问，非专业表达由 LLM 转写，但实质变化必须进入 revision 门；
6. 对话任务以持续更新卡片和结构化事件流展示真实进度，不展示原始模型思考，不使用 Emoji。

首期明确不做：

- 不建设公共插件市场、付费、评分社区或团队级共享；
- 不允许 Pi 直接修改 Windows、系统 Python、Mangrove 主环境或 Docker Socket；
- 不让平台方案自动读取个人任务、连接或样本；
- 不把单次成功、Agent 自评或候选下载当成正式 Delivery；
- 不自动切换 vNext 默认入口，不开始 D6 多媒体生产实现或服务器 8B 验证。

## 2. 事实、推断与建议

### 2.1 已验证事实

- `src/semantic_harness/capabilities.py` 的现有 `CapabilityRegistry` 是固定内置映射，并明确
  禁止按用户输入动态导入或执行代码；
- `CapabilityManifest` 已描述格式、操作、副作用、网络、资源限制、健康检查和参数 Schema，
  可作为内置能力导入新能力目录的输入；
- `EgressPolicy` 已定义互斥的 `dependency_acquisition` 与 `business_execution`，但当前
  `PiRuntime.start/resume` 只实际使用业务执行策略；
- 当前工作台已经有 Owner 隔离的 Task、Revision、Event、SSE、取消和 Pi Run/Event；
- 当前 revision 接口把旧目标和修改文本直接拼接，运行中创建 revision 会先取消任务；
- 当前 `answer` 只处理系统待回答问题，不能承接任意状态追问和依据追问；
- `skills/*.md` 与 `data/templates/*.md` 是平台全局文件，模板可以由 LLM 合并和自动晋级，
  没有个人作用域和不可变版本；
- 当前时间线把事件压成固定里程碑，只展示每阶段最后摘要，不能完整表达能力发现、下载、
  验证、方案选择和安全点切换。

### 2.2 基于代码的推断

- 现有 CapabilityManifest、DependencyBundle、Pi Extension、Smokescreen、Owner 校验和 SSE
  足以作为 Adapter 资产，无需再引入第二套 Agent 框架；
- 把能力仓库与任务执行环境分开、但允许一个任务装载多个能力包，可以兼顾隔离与调用效率；
- 新的个人/平台 SOP 不能直接扩展旧模板文件格式，否则所有权、发布快照和版本冻结会散落到
  Conductor、模板路由和文件系统多个调用点；
- 状态追问若仍走 revision 接口，会造成不必要的取消和重跑，需要独立 ConversationSteering
  Seam。

### 2.3 尚未验证的建议

- 能力制品首期采用本地内容寻址仓库，是否同时支持 OCI Registry 导入/导出需真实 PoC；
- Python/Node/CLI/MCP 四类 Adapter 的缓存命中、冷启动和磁盘占用需要本机实测；
- 本地 Qwen 对非专业中文表达生成稳定 ContextDelta 的能力需要冻结语料连续验证；
- “自动化方案”是否应成为一级导航，需要文档线框验收后再制作可抛弃 UI 原型；
- 默认下载、磁盘、时长和候选数只在实施前通过真实小样确定，不在本规格硬编码。

## 3. 已确认产品决策

| 主题 | 决策 |
|---|---|
| 获取自主性 | 低风险来源和既有权限内由 Pi 自动获取、验证、执行，用户可查看和取消 |
| 人工控制 | 陌生可执行 URL、新外发、新权限、新目录、范围或不可逆动作必须确认 |
| 封装 | 原子 `CapabilityPack` 与可组合 `AutomationProcedure` 两层 |
| 作用域 | 只支持 `personal` 与 `platform`；没有“高级用户”或团队作用域 |
| 成熟门 | 首次成功为个人草稿；合成、真实和失败关闭测试通过后才可验证 |
| 三轴治理 | 成熟度、生命周期和运行资格分开；自动隔离不冒充管理员撤销 |
| 平台发布 | 管理员或超级管理员从已验证个人版本生成独立脱敏快照；默认只进入管理员灰度 |
| 更新 | 新版本并行验证，不静默覆盖；历史 TaskRevision 始终冻结旧版本 |
| 数据 | 真实任务留在 Owner 档案；平台方案默认只带合成或脱敏夹具 |
| 管理员查看 | 任务管理信息默认可见；业务正文只能通过有原因和审计记录的显式审计查看 |
| 选择 | Pi 综合适用度、验证状态、版本、权限和历史成功率，展示原因并允许切换 |
| 学习 | 失败可生成个人候选版本，不原地改写验证版本或平台版本 |
| 效率 | 共享能力仓库复用；一个任务可装载多个能力包，不默认每能力一容器 |
| 进度 | 对话内持续更新卡片 + 可展开事件流；无真实分母不显示百分比 |
| 表达 | 普通用户看任务语言，管理员看技术证据；全界面禁止 Emoji |
| 追问 | 任务运行中输入框可用；状态/依据追问不改变 Run，实质变化走 revision |
| 转写 | 原话不可变；LLM 生成 ContextDelta；SemanticDiffGate 决定是否需确认 |

## 4. 领域模型与状态

### 4.1 能力包

`CapabilityPackVersion` 是不可变、内容寻址的原子能力版本：

```text
pack_id
version
digest
manifest
components[]
source_provenance[]
permission_requirements
resource_requirements
entrypoint
healthcheck
validation_evidence_refs[]
created_by
created_at
```

`components.kind` 首期只允许：

- `tool`：Python/Node/CLI 等执行能力；
- `mcp_local`：任务环境内启动的 MCP Server；
- `mcp_remote`：只保存无秘密连接定义和 SecretRef；
- `skill`：说明、资源和脚本；含脚本的 Skill 与可执行代码使用相同门；
- `dependency_bundle`：已冻结的依赖环境。

任何组件都不能携带业务正文、API Key、Cookie、宿主绝对路径或其他用户引用。

### 4.2 自动化方案

`AutomationProcedureVersion` 引用一个或多个能力包版本：

```text
procedure_id
version
scope                 personal | platform
owner_id              personal 必填；platform 为空
maturity              draft | verified
lifecycle             active | deprecated | revoked
eligibility            eligible | quarantined
applicability
capability_refs[]
preferred_sequence
allowed_adaptations
permission_requirements
completion_gates
failure_handling
fixture_refs[]
validation_summary
derived_from_task_refs[]
digest
```

`preferred_sequence` 是经验路线，不是不可变执行计划。Pi 可以根据来源 Observation 调整顺序、
组合其他已获准能力或生成临时执行草案，但不能越过 GoalContract 和权限门。

### 4.3 成熟度与发布

成熟度、生命周期、运行资格和作用域必须分开，避免把质量、推荐、安全刹车和可见性混成一个状态：

```text
maturity:    draft ──验证通过──→ verified
lifecycle:   active ──版本取代──→ deprecated
               └────安全撤销──→ revoked
eligibility: eligible ←─管理员恢复─→ quarantined
```

`deprecated` 不再属于成熟度：它停止新任务推荐但保留历史冻结任务；`revoked` 禁止新任务、重试
和恢复；系统可因硬安全门自动进入 `quarantined`，但只有管理员可以撤销或恢复。精确定义见
[ADR-0029](../adr/0029-capability-validation-lifecycle-and-platform-publication.md)。

单次任务成功只证明特定输入可用。`verified` 至少要求：

1. 合成 Smoke；
2. 至少一个 Owner 隔离的真实任务回放；
3. 失败关闭或拒绝越权测试；
4. 组件来源、版本、哈希和权限一致；
5. Verifier 通过，且没有把用户业务正文写入方案。

### 4.4 对话与上下文

```text
RawUserTurn
→ ContextDelta
→ SemanticDiffGate
   ├─ answer_only
   ├─ normalized_no_material_change
   ├─ revision_proposal
   ├─ new_task_proposal
   └─ permission_request
→ 用户确认（仅实质变化）
→ TaskRevision / 当前 Run 保持不变
```

`RawUserTurn`、`ContextDelta` 和最终动作必须分别持久化，不能只保留改写后的 Prompt。

## 5. 深 Module 与 Interface

### 5.1 ConversationSteering Module

```python
handle_turn(request: SteeringRequest) -> SteeringResult
```

调用方只需提交 Owner、Task、当前 revision、原始回合和当前状态。Implementation 隐藏 LLM
转写、相关历史召回、指代消解、语义差异和安全点判断。`SteeringResult` 只能返回第 4.4 节的
五种动作之一。

### 5.2 ContextCompilation Module

```python
compile(request: ContextCompileRequest) -> CompiledContextRef
```

Interface 接受冻结目标、相关回合、当前运行摘要、能力选择和证据引用；返回有 token 统计、
组成清单与摘要哈希的上下文引用。提示词模板、摘要算法、向量召回、裁剪和大型输出落盘策略
留在 Implementation。

### 5.3 CapabilityResolution Module

```python
resolve(request: CapabilityNeed) -> CapabilityResolution
```

它按 Owner 可见性、任务权限、适用度、成熟度、版本和健康证据返回冻结选择或明确能力缺口。
调用者不感知个人/平台存储表、Embedding、reranker 或旧 Capability Adapter。

### 5.4 CapabilityAcquisition Module

```python
acquire(request: AcquisitionRequest, on_event: EventSink) -> AcquisitionResult
cancel(acquisition_id: str, owner_id: str) -> None
```

Implementation 隐藏来源发现、下载、锁定、构建、缓存、健康检查、内容寻址和 Tool/MCP/Skill
差异。Interface 明确预算、来源策略、权限需求和终态；不得接受用户来源路径或 Provider Key。

### 5.5 ProcedureLearning Module

```python
propose(trace_ref: ExecutionTraceRef) -> ProcedureDraftRef
validate(version_ref: ProcedureVersionRef) -> ProcedureValidation
publish(request: ProcedurePublishRequest) -> ProcedureVersionRef
```

`publish` 只允许管理员/超级管理员调用。自动学习、脱敏、去重、版本比较和平台快照留在
Implementation；它不拥有正式 Delivery 发布权。

### 5.6 ProgressProjection Module

```python
project(events: EventStreamRef, audience: Audience) -> TaskProgressView
```

同一持久化事件分别投影普通用户和管理员视图。它隐藏技术日志和敏感字段，不让每个前端组件
重新猜测事件含义。

## 6. 能力获取与执行流程

### 6.1 正常路径

```text
Pi 提交 CapabilityNeed
→ CapabilityResolution 查个人与平台目录
→ 已命中：冻结版本并装载
→ 未命中：创建 AcquisitionRun
→ 在无用户来源、无业务 Secret 环境中发现和获取
→ 固定来源、版本、依赖与 digest
→ 合成 Smoke 和越权失败测试
→ 形成 personal draft CapabilityPack
→ 退出获取网络并销毁 Lease
→ 业务任务只读装载能力包
→ 运行、Verifier、Candidate
→ 成功轨迹可生成 personal AutomationProcedure draft
```

### 6.2 来源策略

自动来源首期包括官方项目仓库、npm、PyPI、GitHub Release 和管理员登记的 MCP/Skill 源。
重定向后的最终来源也必须满足策略。陌生 URL 只能产生 `permission_request`，用户拒绝后不得
改换另一个未授权来源继续。

许可证不作为本学习项目的筛选门；来源可追溯、内容完整性、恶意行为、权限、恢复和版本
稳定性仍是一票否决条件。

### 6.3 资源预算

预算最少包含：

```text
max_duration_seconds
max_download_bytes
max_unpacked_bytes
max_candidates
max_retries_per_source
max_concurrency
```

达到预算返回 `needs_input` 或有证据的失败，不允许无限循环。具体默认值需用本机 Python、
Node、CLI 和 MCP 四类样本测量后确定；管理员可以配置，普通用户不能越过平台上限。

### 6.4 效率策略

- 首次获取承担网络、构建和验证成本；
- 内容 digest 相同直接复用；
- Python/Node 依赖、CLI 二进制和容器层分别使用成熟缓存 Adapter；
- 一个任务的多个原生能力只读装载到单一 Capability Host Sidecar；Pi 不直接挂载原生能力，
  Sidecar 不挂载业务来源、模型配置或 Docker Socket；
- 本地 MCP 在当前任务期间常驻，不按单次 tool call 重启；
- 大型模型继续使用共享服务或模型卷，不复制进每个能力包；
- 新版本并行保存，垃圾回收只能删除无引用版本，并受不可逆操作确认约束。

### 6.5 首版开源组件选择

一手来源调研见
[Agentic Capability Acquisition 开源方案调研](../research/2026-08-02-agentic-capability-acquisition-open-source-research.md)。
首版采用以下成熟组件，不自行重写包管理、制品、扫描或签名协议：

| 能力 | 首版选择 | Mangrove 负责的边界 |
|---|---|---|
| Skill 结构 | Agent Skills 规范与 `skills-ref validate` | 权限、Owner、版本、运行许可和 SOP 结构化契约 |
| Python | uv + `uv.lock` + frozen sync | 来源策略、构建隔离、冻结 digest 和任务装载 |
| Node | npm + `package-lock.json` + `npm ci` | 生命周期脚本许可、缓存和只读运行层 |
| 隔离构建 | Docker BuildKit | 获取阶段不挂载业务来源、不注入业务 Secret |
| 不可变制品 | OCI Artifact + ORAS；单机先用 OCI Image Layout | 数据库保存 Owner、scope、maturity、审核和审计 |
| MCP 发现 | MCP 官方 Registry 只作候选发现 Feed | 同步、规范化、扫描、验证后才进入私有目录 |
| 扫描 | Trivy 0.70.0 | 最终目录/镜像的漏洞、误配置与 Secret 失败关闭策略 |
| SBOM | Syft 1.50.0 | 保存 Syft JSON，并为 `verified` 或平台候选生成 CycloneDX JSON 1.6 |
| 平台签名 | Cosign 3.0.6 + ORAS 1.3.2 | 本地密钥绑定平台 digest；本地 Layout 签名路径先通过回环 Registry PoC |

任务、Run 和历史方案只绑定 OCI digest，tag 仅用于界面显示。OCI 保存内容，Mangrove 数据库
保存权限和产品状态；不能用仓库路径代替 Owner 校验。研究中出现的 `platform_shared` 不是
新的成熟度，统一表达为 `maturity=verified`、`scope=platform` 与明确受众。固定版本、发布物验证、
Trivy DB `UpdatedAt` 七天门和 Cosign/OCI 未决 PoC 见
[AC-07 官方工具刷新](../research/2026-08-06-ac07-supply-chain-tooling-official-refresh.md)。

首版明确后置：自建 MCP 市场/Registry、陌生 URL 自动执行、远程构建集群、完整 SLSA
等级、in-toto Layout、私有 Fulcio/Rekor、复杂 CVE/VEX 策略和 Kubernetes Admission。
这些能力只有在真实运行证据证明必要时再单独立项，不能让供应链体系先于核心用户价值膨胀。

## 7. SOP 学习、选择与隔离

### 7.1 生成个人草稿

任务成功且 Verifier 通过后，Pi 可以从结构化执行轨迹生成草稿。轨迹只提供能力引用、参数
Schema、权限、成功/失败 Observation、完成门和 TaskRef，不直接提供业务正文。草稿必须
显示“仅本人可用”。

### 7.2 失败学习

当前任务可以有界重规划；最终成功路线生成新候选版本。原验证版本继续可用，不能被当前失败
直接改写。失败任务可以记录“不要采用”的结构化路线，但不能把未完成轨迹晋级为 SOP。

### 7.3 选择

选择输入包括任务目标、来源通道、模态、操作、交付要求、权限和可用连接。建议评分只用于
候选排序，以下硬门优先：Owner 可见、权限足够、组件健康、版本可取得、验证未过期、外发已
确认。Pi 向用户展示最终选中方案和简短原因，不能展示隐藏思维链。

### 7.4 平台发布

管理员审核页必须展示：

- 个人来源已脱敏后的平台候选；
- 组件来源、版本、digest、权限和网络；
- 合成、真实和失败关闭证据；
- 与当前平台版本的语义差异；
- 发布后可见用户和回滚版本。

发布生成独立脱敏平台版本、新 digest 和签名，不改变个人版本；默认受众为管理员灰度。面向
普通用户开放是另一个显式动作。平台版本弃用只影响新选择，不重写历史任务；安全撤销禁止
新任务、重试和恢复，自动隔离等待管理员治理。

## 8. 追问、Revision 与上下文编译

### 8.1 追问行为

| 用户输入 | Steering 结果 | 当前 Run |
|---|---|---|
| “现在做到哪了？” | `answer_only` | 继续 |
| “为什么选择这个 OCR？” | `answer_only` | 继续 |
| “还是按上次的 JSON 格式” | 高置信度时 `normalized_no_material_change` | 继续 |
| “增加部门和人民币大写金额” | `revision_proposal` | 等待用户选择安全切换方式 |
| “再处理另一份合同” | `new_task_proposal` 或有解释的 revision 草案 | 不静默扩大 |
| “把音频发到新的外部 ASR” | `permission_request` | 未确认前继续本地或等待 |

### 8.2 非专业 Prompt 转写

ContextDelta 最少包含：

```text
goal_delta
source_scope_delta
selection_delta
coverage_delta
field_semantics_delta
output_delta
permission_delta
open_questions[]
confidence
source_turn_ids[]
inherited_revision
```

LLM 可以消除重复、绑定“这个/上次那些”等指代并引用已确认信息，不能新增未确认业务字段、
解释“全部”的具体范围、授权外发或删除历史。多个解释会影响结果或成本时只问一个最关键问题。

### 8.3 上下文窗口优先级

```text
1. 系统权限与安全边界
2. 冻结 GoalContract / TaskRevision
3. 已确认数据含义、交付和开放问题
4. 当前 Run 状态与重要 Observation
5. 冻结能力包和自动化方案摘要
6. 本次相关 RawUserTurn / ContextDelta
7. 按需 EvidenceRef 片段
```

不默认发送完整历史、完整 OCR、控制台日志、重复 SOP 和已被新 revision 取代的旧执行草案。
组成清单、引用和 token 统计可持久化；拼装后的敏感正文不需要复制成新的长期业务制品。

## 9. 结构化事件与渐进式披露

### 9.1 事件信封

```json
{
  "event_id": "evt_...",
  "sequence": 42,
  "task_id": "task_...",
  "revision": 2,
  "run_id": "run_...",
  "stage": "prepare_capabilities",
  "event_type": "capability.validation_started",
  "summary": "正在验证扫描 PDF 表格抽取能力",
  "progress": {"current": 1, "total": 3, "unit": "check"},
  "refs": {"pack_id": "pack_...", "pack_version": "1.2.0"},
  "action": null,
  "audience": "user",
  "created_at": "..."
}
```

`progress.total` 未知时必须为空。管理员技术细节使用独立受众投影或受保护引用，不把安装命令、
Token、宿主路径和原始日志放进普通事件。

### 9.2 顶层阶段

顶层保持少而稳定，细节使用子事件：

```text
理解目标 → 检查来源 → 准备能力 → 执行任务 → 验证结果 → 正式交付
```

任一时刻最多一个顶层阶段处于活动态。重规划是当前阶段内事件或显式回到某阶段，不额外制造
一条永不结束的“思考”阶段。

### 9.3 必需事件

- `context.rewrite_completed`
- `context.revision_proposed`
- `followup.answered_without_change`
- `capability.search_started`
- `capability.candidate_found`
- `capability.permission_required`
- `capability.download_progress`
- `capability.validation_passed | failed`
- `procedure.selected`
- `procedure.draft_created`
- `execution.replanned`
- `verification.started | passed | failed`
- `delivery.published`

## 10. UX 信息架构与文档线框

### 10.1 采用方案

采用“对话内持续更新紧凑卡片 + 原位展开事件流”。拒绝全量事件永久展开造成信息洪流，也
拒绝只放侧栏导致用户离开任务主线。

```text
┌──────────────────────────────────────────────────────┐
│ 正在处理报销审批文件                 2/6 阶段已完成 │
│ 当前：准备所需能力                                  │
│ 正在验证扫描 PDF 表格抽取能力                       │
│                                                      │
│ 查看详情    本次使用的方案    取消任务              │
└──────────────────────────────────────────────────────┘

展开后：
10:21 已理解任务      返回所有审批记录并生成 JSON
10:22 已检查来源      PDF 109 页，96 页为扫描内容
10:23 正在准备能力    平台方案 v1.3，最近验证通过
10:24 正在发现候选    已检查 42/109 页
```

输入框始终位于任务卡下方。发送追问后立即显示一种明确回执：

```text
已回答，不影响当前任务
已形成 V3 修改草案，等待确认
检测到独立目标，建议创建新任务
需要新的外部数据处理授权
```

### 10.2 自动化方案入口

自动化方案是生产资产，不建议继续塞入已经较重的“设置”。文档建议新增一级“自动化方案”
入口，同时在任务详情提供“本次使用的方案”快捷入口：

```text
自动化方案
├─ 我的方案
├─ 平台方案
├─ 草稿与待验证
└─ 审核队列（仅管理员/超级管理员）
```

设置页只保留能力来源策略、资源预算、缓存容量、管理员安全开关和 AC-07 最小能力治理入口。
普通用户不能看到其他用户的个人方案；管理员可看任务管理信息，查看个人原任务正文必须显式
进入审计查看、填写原因并留下读取对象记录。

### 10.3 新手引导

首次产生个人方案时只解释三件事：它解决什么、仅本人可用、下次会优先考虑。用户可跳过，
并在自动化方案页重新启动引导。平台发布审核使用独立管理员引导。

所有状态使用 Lucide 等统一图标和文字，不使用 Emoji；颜色不能作为唯一状态信号，深浅主题、
键盘操作、屏幕阅读器名称和 reduced-motion 均进入验收。

### 10.4 交互原型边界

本轮只冻结文档线框。可抛弃前端原型应在用户另行授权后，基于现有 `/data-prep` 路由制作
三个结构差异明显的 `?variant=` 版本，不接真实写接口、不进入生产构建默认路径。

## 11. 数据与权限模型

首期建议新增独立表或等价 Repository，不扩展旧模板文件：

```text
raw_user_turns
context_deltas
revision_proposals
compiled_context_manifests
capability_packs
capability_pack_versions
capability_components
capability_acquisition_runs
capability_validation_runs
automation_procedures
automation_procedure_versions
automation_procedure_validations
capability_selections
```

关键约束：

- 个人查询全部以 `owner_id + id` 过滤；
- 平台方案只有管理员发布动作可以创建，普通用户只读；
- Secret 只保存现有 SecretRef/ConnectionRef，不进入版本 JSON；
- TaskRevision 冻结 procedure/pack 版本和 digest；
- 个人转平台是新版本快照，不更新 scope 字段；
- 删除个人方案不得破坏历史任务引用，先弃用后按引用安全清理；
- 管理员可以治理平台元数据，但不能因此读取个人业务制品。

## 12. 产品 Interface 草案

### 12.1 对话

```text
POST /api/semantic-workspace/tasks/{task_id}/turns
GET  /api/semantic-workspace/tasks/{task_id}/turns
POST /api/semantic-workspace/tasks/{task_id}/revision-proposals/{id}/confirm
POST /api/semantic-workspace/tasks/{task_id}/revision-proposals/{id}/reject
```

确认请求必须显式选择：立即取消并切换、当前原子步骤结束后切换、创建独立任务。状态追问
返回 200 和即时回答，不创建 revision。

### 12.2 能力和方案

```text
GET  /api/capability-packs
GET  /api/capability-packs/{pack_id}/versions/{version}
POST /api/capability-acquisitions/{id}/cancel
GET  /api/automation-procedures?scope=personal|platform
GET  /api/automation-procedures/{id}/versions/{version}
POST /api/automation-procedures/{id}/versions/{version}/validate
POST /api/admin/automation-procedures/{id}/versions/{version}/publish
POST /api/admin/automation-procedures/{id}/versions/{version}/deprecate
```

Pi 内部的 resolve/acquire 不直接暴露为普通用户任意安装接口；产品 Interface 只允许查看、
取消、验证、选择和授权。所有读取继续进行 Owner/角色检查。

### 12.3 事件兼容

现有 `/tasks/{task_id}/events` 和 `/stream` 保持；新字段采用可忽略扩展。旧前端仍可显示
`summary`，新前端使用结构化 `progress/refs/action/audience`。不修改既有事件身份和顺序。

## 13. Legacy 兼容与迁移

- 现有内置 CapabilityManifest 通过只读 Adapter 映射为平台内置能力，不移动实现；
- `skills/*.md` 只作为平台 Legacy Skill 输入，脚本型内容重新验证后才可形成 CapabilityPack；
- `data/templates/*.md` 不自动转为个人方案；管理员可以显式导入为平台草稿，旧使用次数和
  质量分只作历史信息；
- `user_memory` 保持偏好语义，不用于保存 SOP 或任务级业务规则；
- 旧 TaskRevision、Run、Candidate、Delivery 和事件不迁移；
- 新表迁移只能前向、幂等、可空，不做不可逆物理删除。

## 14. 验收矩阵

### 14.1 能力获取

1. 任务缺少一个 Python 工具：获取环境中无用户来源和 Secret，验证后业务任务复用；
2. 第二次使用相同 digest：零下载、零重装；
3. 陌生 URL：必须进入确认，拒绝后零下载；
4. 达到时长/流量/磁盘/重试预算：停止并展示尝试记录；
5. 下载被篡改或 digest 不一致：失败关闭；
6. 恶意 Skill/MCP 请求宿主路径、Docker Socket、他人数据或未授权公网：全部拒绝；
7. 取消获取：网络 Lease、构建进程和临时目录回收，未生成可用版本；
8. Python、Node、CLI、本地 MCP 各有一条真实缓存与健康检查证据。

### 14.2 SOP 与权限

1. 用户 A 不能列出、读取、选择、修改或删除用户 B 的个人方案；
2. 管理员默认看到脱敏快照、引用和任务管理信息；查看原任务正文必须显式审计并填写原因；
3. 单次成功保持 draft；缺失败关闭测试不能 verified；
4. 发布平台版本生成新身份，个人版本修改不影响平台版本；
5. 平台更新不改写历史 TaskRevision；
6. 个人和平台方案同时匹配时，选择原因可见、可切换且硬权限门优先；
7. 失败学习生成并行候选，旧验证版本保持不变；
8. deprecated 历史任务可重放而新任务不可选；revoked 与 quarantined 均阻止重试和恢复；
9. 平台发布默认只进入管理员灰度，普通用户开放需要独立管理员动作；
10. High 限期例外到期自动隔离，Critical、Secret、签名失效和越权不允许例外。

### 14.3 追问与上下文

1. 运行中问进度：即时回答，Run ID 和 revision 不变；
2. 问选择依据：引用能力/方案证据，不展示原始思考；
3. 非专业同义表达：ContextDelta 等价且原话可追溯；
4. 修改字段、范围、权限或外发：只生成 revision/权限草案，未确认不生效；
5. 原子工具运行中确认修改：按用户选择的安全点切换，不产生半写结果；
6. 刷新、断线、服务恢复：追问、revision 草案和唯一活动阶段恢复；
7. 长对话：CompiledContext 不含无关全文，同时保留所有确认语义和开放问题；
8. 用户 A 的 RawUserTurn、ContextDelta 和个人记忆不进入用户 B 上下文。

### 14.4 UX

1. 默认卡片能回答“目标、当前位置、已完成、下一步”；
2. 展开事件显示真实序列，未知总量不伪造百分比；
3. 用户追问输入框在运行中可用；
4. 普通用户看不到安装命令、Token、宿主路径和原始日志；
5. 管理员能看到来源、版本、digest、权限和验证证据；
6. 页面无 Emoji，状态不只依赖颜色；
7. 深浅主题、1366 宽度、键盘、屏幕阅读器和 reduced-motion 通过；
8. 新手引导可跳过并可重新打开。

## 15. 实施顺序与完成边界

详细工单见
[Agentic 能力获取、SOP 与对话上下文任务拆分](2026-08-02-agentic-capability-sop-context-task-breakdown.md)。

AC-06 用户灰度验收已通过。AC-07 已形成规格 #32 和纵向实施票 #33～#44；#33 三轴治理投影
已完成工程验证、code-review 修复、用户验收、带备份生产迁移和 Issue 关闭；#34 可恢复验证运行
已完成工程实现、双轴审查和带备份生产迁移，等待用户灰度。后续按“验证/三轴治理 → 供应链 PoC →
管理员灰度平台快照 → AC-08 SOP 学习 → AC-09 UI → AC-10 端到端门”推进。vNext 正式 Publisher 仍是正式结果
闭环的独立 P0；本专项即使通过，也只能生成经验证 Candidate，不能用能力包或 SOP 绕过
ADR-0019。

除已授权完成的 #33 和 #34 工程实现外，以下事项必须再次由用户确认：

- 实现开工、真实依赖下载和可抛弃前端原型；
- 默认资源预算和允许来源清单的具体值；
- 使用真实外部 MCP/Provider 及其外发内容；
- 新顶级导航、数据库迁移、物理缓存清理；
- 其他 GitHub Issue 修改、后续提交/推送、默认切换、版本、标签和外部发布。
