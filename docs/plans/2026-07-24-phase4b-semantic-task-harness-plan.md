# Phase 4B：语义任务 Harness 与通用能力包专项执行计划

> 文档状态：**Legacy 权威实施计划；批次 -1 至 8A 完成且 8A 通过用户验收，8B 后置**
>
> 编制日期：2026-07-24
>
> 实施基线：`v0.0.5` / `6762c11`
>
> 历史专项开发分支：`feature/phase4b-semantic-harness`
>
> 当前开发版本分支：`v0.0.6`，由用户明确授权从 `b43c948` 建立；
> 尚未封板且没有同名标签
>
> 历史收口：批次 7 在用户否决首版 UX、首轮真实 Word 限定提取和第一次不完整修复后，
> 已完成 UX 与文档语义错配二次 P0 纠偏。用户原始 Word 本地严格复验扫描 501 个目标，
> 选择 83 个商务相关元素，六组漏项和业务章节前误选均为 0，TXT QA 通过。
> 二次纠偏提交 `554acd6` 已推送到 `platform/v0.0.6`。随后真实任务仍出现语义计划编译
> 输出截断、静态校验失败、错误反馈失真和输出格式错配，均在批次 8A 修复。
> 用户批准批次 8A 后，主流程框架、错误契约、四类真实文件后端闭环、低敏观测和故障门禁
> 已完成；真实本地模型编译已在 8.35 秒内收敛到限定范围和 TXT，用户已确认验收通过。
> 2026-07-29 的 PDF 文档表格转 CSV 失败已进入 Agentic Runtime vNext 专项；本计划保留
> 为 Legacy 历史依据，不再扩张新的 TaskFamily 或场景分支。
> 延期问题见
> [`2026-07-28-phase4b-batch7-deferred-issues.md`](2026-07-28-phase4b-batch7-deferred-issues.md)
>
> 开发前问题评审：
> [`2026-07-26-pre-phase4b-batch1-readiness-review.md`](2026-07-26-pre-phase4b-batch1-readiness-review.md)
>
> 适用范围：Mangrove 数据准备工作台、后续图片/音视频、认证来源发现和工程化任务
>
> 上位文档：[`plan.md`](../../plan.md)、[`docs/task-driven-data-workflows.md`](../task-driven-data-workflows.md)

## 0. 本计划的结论

Phase 4A 已解决“文件能否被解析、结果能否携带证据、是否可以复核和下载”的基础问题，但尚未建立一个能把用户自然语言完整编译为可执行任务的通用运行时。当前真实故障证明，仅让模型生成字段和 `ResultContract`，再按结果形态选择一条执行分支，不足以表达和执行以下语义：

- 在哪些来源、页面、表格、行或段落内处理；
- 哪些条件必须过滤；
- 每一行代表什么；
- 只保留哪些列；
- 是否合并、分组、聚合、排序或去重；
- 是原文摘录、重组，还是总结、改写、分析；
- 最终必须满足哪些可机械验证的条件；
- 失败后允许如何修复，何时必须停下来询问用户。

因此，本阶段不继续给现有提示词打补丁，而是在现有 LangGraph、Phase 4A 证据链和数据准备底座上新增：

> **Semantic Task Harness = 语义任务计划 + 来源检查/绑定 + 能力工具注册表 + 有界执行 Loop + 语义后置条件 + 可审计交付。**

它是一个通用能力，不是“表格专用 Agent”或“合同专用 Agent”。表格筛选、Word 商务条款摘录、跨文档比较、合规核查、报告编排和未来音视频转写，都通过同一份计划契约和不同能力包组合完成。

本计划与“Harness/Loop”的关系如下：

- **Harness** 是完整运行时边界：契约、工具、权限、状态、预算、追踪、验证和交付；
- **Loop** 是 Harness 内部的受控执行机制：检查 → 绑定 → 执行 → 验证 → 有界修复；
- 不能只做无限 ReAct Loop，也不能只做一份静态计划后盲目执行。

## 1. 已确认的产品决策

以下决策已经由用户确认，实施时不得重新解释为其他含义：

1. 本阶段先编制完整方案，再开始开发；本计划提交不包含功能实现。
2. 高置信度且可验证的任务自动执行；只有范围、行粒度、聚合、合并、外部数据风险等实质歧义才询问用户。
3. 文档内容任务默认采用**原文摘录或原文重组**；只有用户明确要求时才总结、改写、翻译或分析。
4. 默认使用本地模型和本地/LAN 工具。切换到外部 OpenAPI 时必须弹窗说明数据外发风险，并由用户确认。
5. 允许自动进行内部格式转换，但必须保留：
   - 原始制品；
   - 转换后制品；
   - 转换工具及版本；
   - 输入/输出哈希；
   - 完整 lineage。
6. 用户请求的输出格式暂不支持时：
   - 先尝试已登记的成熟转换器；
   - 相同格式仍无法生成时，明确说明原因并请用户选择替代格式；
   - 禁止静默改成别的格式交付。
7. 支持当代常见文本、表格、文档和媒体格式，不承诺覆盖所有冷门或损坏的历史格式。
8. 成熟开源工具优先，允许使用 Docker、Node、Java、LibreOffice、Pandoc、FFmpeg 等非 Python 组件；最终工具选择必须由真实样本 A/B 决定。
9. 真实业务文件可用于本地评测，但不得提交 Git；仓库只提交脱敏、可公开的小型 golden fixtures。
10. 开发环境不投入历史任务兼容迁移。未来如需重置任务、工作区和下载数据，必须先备份、列出范围并再次确认；本计划阶段不删除任何数据。
11. 最终用户隔离规则为：普通用户只能访问自己的文件、任务和结果；管理员拥有有审计记录的运维访问权；本阶段不做多人协作编辑。
12. 默认结果保留 90 天，用户可主动删除，重要任务可固定保留；临时文件和模型缓存使用独立生命周期。

## 2. 当前真实故障与根因

### 2.1 失败任务

用户要求：

> 帮我抽取谢超群相关的数据，并且我只需要看核销工作量天数和工作量费用两列，然后输出一个整表给我。

实际任务 `doc_785f33d5d467` 的计划被解释为：

- 两个字段；
- `shape=tables`；
- `cardinality=all`；
- `merge_tables=false`。

执行器进入 `tables` 分支后，直接复制全部原表。字段列表和“谢超群”过滤条件没有参与执行，最终输出 18 张表、154 行，其中只有 11 行属于目标人员。现有质量门只检查“表格结果是否非空”，因此错误结果仍被判定为完成。

### 2.2 根因不是 OCR

本故障发生在语义计划和执行绑定层，而不是 MinerU/PaddleOCR：

1. `IntentSpecDraft` 只能表达字段和结果形态，不能表达谓词、投影、行粒度和语义后置条件。
2. `_result_contract_from_intent()` 通过“表格”等词把任务强制路由到原表复制分支。
3. `ResultShape.TABLES` 分支绕过候选字段提取，也没有确定性行过滤和列投影。
4. `QualityGate` 校验的是“有表、有行”，不是“所有行都满足谢超群、只有两列、只有一张表”。
5. 系统缺少执行前的来源检查和字段绑定，模型不知道真实列名、跨页表头和同义词。
6. 系统缺少执行后的语义验证与有界修复 Loop。

### 2.3 首个不可绕过的黄金门禁

同一脱敏样例必须编译为类似以下逻辑计划：

```yaml
task_family: tabular_transform
source_scope:
  artifact_ids: ["<fixture-artifact>"]
  table_scope: all_detected_tables
selection:
  - op: eq
    field: 姓名
    value: 谢超群
projection:
  - 核销工作量天数
  - 工作量费用
record_grain: source_detail_row
aggregation: none
combine:
  mode: one_table
content_policy: verbatim
delivery:
  formats: [xlsx]
postconditions:
  table_count: 1
  exact_visible_columns: [核销工作量天数, 工作量费用]
  all_source_rows_match:
    field: 姓名
    value: 谢超群
  expected_row_count_for_fixture: 11
  evidence_coverage: 1.0
```

阶段验收必须同时满足：

- 正好 11 行；
- 正好 2 个可见业务列；
- 正好 1 张结果表；
- 每个结果行的来源记录都满足“姓名 = 谢超群”；
- 每个结果行可回溯到原文件、原表、页码和源行；
- 任一条件不满足时任务不得标记 `COMPLETED`，不得登记权威 XLSX。

## 3. 范围与重新编号

### 3.1 新路线

| 阶段 | 目标 | 本计划中的状态 |
|---|---|---|
| Phase 4A | 文档解析、EvidenceRef、复核和五种结果形态底座 | 已封板 |
| **Phase 4B** | **语义任务 Harness、通用能力包、验证/修复 Loop、常用输出编排** | 本专项实施范围 |
| Phase 4C | 图片、音频、视频解析，接入同一 Harness | 后续阶段，接口在 4B 预留 |
| Phase 5A | 认证网站、只读 API 和企业来源发现 | 后续阶段，复用 4B 计划/工具协议 |
| Phase 5B | Recipe、模板、队列、配额、生命周期、质量运营和生产化 | 后续阶段，复用 4B 运行记录 |

原路线中的“Phase 4B 图片、音频与视频”顺延为 Phase 4C。不是取消多媒体，而是先补齐所有来源都依赖的语义执行内核。

### 3.2 Phase 4B 必须交付

- 版本化 Semantic Task Plan（STP）；
- 来源检查、Schema/结构绑定和歧义判定；
- 能力包注册表和受控工具协议；
- 表格筛选、投影、排序、合并、聚合和质量核对；
- 文档原文摘录、重组、比较、核查和 DOCX/PDF 编排；
- 执行前预览和高风险确认；
- 有界执行/验证/修复 Loop；
- 输出格式路由、转换链和禁止静默替代；
- 每用户任务/制品隔离；
- golden fixtures、轨迹评测和可观测性基础；
- Windows 本机开发方案和 Linux 服务器部署方案。

### 3.3 Phase 4B 不交付

- 任意网站登录和写操作；
- 绕过 MFA、CAPTCHA 或组织访问控制；
- 无界自主 Agent；
- 对任意格式、任意损坏文件、任意版式的“100% 完美转换”承诺；
- 用模型猜测缺失事实；
- 多人实时协作编辑；
- Kubernetes；
- 历史开发任务迁移；
- 外部 Label Studio 人工标注门禁；
- 完整媒体生产链；媒体在 Phase 4C 实施。

## 4. 目标架构

### 4.1 总链路

```text
用户自然语言
  ↓
Intent Interpreter（只理解目标，不接触原始大数据）
  ↓
Semantic Task Plan / Logical Plan
  ↓
Source Inspector（格式、结构、Schema、样本、能力）
  ↓
Binder + Ambiguity Gate
  ├─ 实质歧义 → 一次只问一个关键问题 → 新 Plan Revision
  └─ 可验证且高置信 → 自动继续
  ↓
Bound Plan + Physical Plan
  ↓
Capability Tools（确定性工具优先，LLM 只处理语义步骤）
  ↓
Preview / Execute
  ↓
Postcondition Validator
  ├─ 通过 → Renderer / Converter → Artifact QA → 交付
  ├─ 可安全修复 → 最多两次 Repair Loop
  └─ 仍失败/需改变用户语义 → 明确失败或询问用户
```

### 4.2 控制面与数据面

LangGraph state 只保存：

- `task_id`、`plan_revision`、`plan_hash`；
- 制品、批次、工具调用和结果引用；
- 计数、状态、错误分类、预算和摘要；
- 验证报告和下一步路由。

大表、长文、媒体帧和完整模型响应继续保存在 ArtifactStore 或批次文件中，不进入 LangGraph state。

### 4.3 三层计划

1. **Logical Plan**
   - 由用户目标生成；
   - 使用业务语义字段；
   - 不绑定具体文件列、页码或工具。
2. **Bound Plan**
   - 将业务语义绑定到真实列、表、章节、元素和来源；
   - 保存匹配证据、置信度和未解决歧义；
   - 一旦确认即不可变。
3. **Physical Plan**
   - 选择具体能力包、工具版本、批大小、并发、临时格式和输出器；
   - 可因环境变化重建，但不得改变 Logical Plan 的用户语义。

三层计划、输入哈希和能力包版本共同形成可复跑身份。任何会改变过滤、行粒度、聚合或内容政策的修复，必须创建新计划 revision，不能在后台静默改写。

## 5. Semantic Task Plan 契约

STP v1 至少包含以下强类型字段：

| 字段 | 作用 |
|---|---|
| `task_family` | `extract / tabular_transform / compare / audit / compose / summarize / translate / convert / transcribe / discover` |
| `objective` | 用户原始目标和规范化目标 |
| `source_scope` | 文件、文件集、表、页、章节、时间片、URL/API/数据库范围 |
| `input_contract` | 期望来源类型、必需结构、损坏/加密策略 |
| `selection` | 过滤谓词、语义检索条件、包含/排除范围 |
| `projection` | 要保留的字段、段落属性或媒体轨道 |
| `record_grain` | 每个结果对象或每一行代表什么 |
| `operations` | 合并、连接、分组、聚合、排序、去重、标准化、比较等有序算子 |
| `content_policy` | `verbatim / normalized / summarized / rewritten / translated / analyzed` |
| `evidence_policy` | 每个结果需要的来源、位置、原文、哈希和置信度 |
| `delivery` | 输出格式、文件数量、布局、模板和命名 |
| `postconditions` | 执行后必须机械验证的结构、内容、数量、证据和格式条件 |
| `risk_policy` | 本地/外部处理、敏感信息、人工确认和工具权限 |
| `budgets` | 最大字节、行、页、时长、工具调用、重试、CPU/GPU 和总耗时 |
| `ambiguities` | 未解决问题、候选解释、影响和置信度 |

### 5.1 内容政策

默认值必须是 `verbatim`：

- 允许裁剪到用户指定范围；
- 允许按用户要求重新排序和编排；
- 允许确定性的字符编码、换行、日期/数字类型规范化；
- 不允许改变原意、补写事实或压缩为摘要。

只有出现明确动词时才切换：

| 用户要求 | `content_policy` |
|---|---|
| 提取、摘录、整理、汇编 | `verbatim` 或 `normalized` |
| 总结、概括、提炼 | `summarized` |
| 改写、润色、重写 | `rewritten` |
| 翻译 | `translated` |
| 分析、评价、给出结论 | `analyzed` |

“整理成 Word/PDF”只表示更换容器和版式，不自动等于总结或改写。

### 5.2 行粒度、过滤和投影是独立概念

禁止继续用 `shape=tables` 代替完整语义：

- **行粒度**决定一行是什么；
- **过滤**决定哪些源记录进入结果；
- **投影**决定显示哪些列；
- **合并**决定多来源结果放在一个表还是多个表；
- **聚合**决定是否把多行变成统计值；
- **输出格式**只决定交付容器。

任何一个维度都不能通过关键词启发式覆盖其他维度。

### 5.3 后置条件

每个计划必须自动生成最小可执行后置条件：

- 范围覆盖：所有输入都已成功、失败或显式隔离；
- 谓词满足：结果行 100% 满足过滤条件；
- 精确 Schema：无缺列、无多余可见列、顺序符合要求；
- 粒度守恒：结果行与源记录/聚合组可解释对应；
- 数量与基数：单个、多个、全部、一表或多表符合约定；
- 聚合对账：分组数、行数、金额/数量求和可回算；
- 证据覆盖：所有非空语义结果都绑定有效 EvidenceRef；
- 内容忠实：原文模式下的文本可在来源证据中定位；
- 格式有效：输出可被对应解析器重新打开，MIME、扩展名和内部结构一致；
- lineage 完整：输入、转换、工具、计划和输出哈希可串联；
- 风险合规：没有未经确认的外部数据传输。

## 6. 澄清闸门

### 6.1 自动执行

满足以下全部条件时不打断用户：

- 只有一个高置信解释；
- 来源检查能绑定到唯一字段/章节；
- 行粒度、是否聚合和输出形式明确；
- 操作是只读、可回滚、无外部数据传输；
- 后置条件可机械验证；
- 工具资源在预算内。

例如，“只保留姓名为谢超群的行，并输出 A、B 两列到一个 Excel”应自动执行。

### 6.2 必须询问

仅在答案会实质改变结果时询问：

- “每个客户一行”还是“每笔订单一行”；
- “费用”是源明细、求和、平均值还是最新值；
- 多个同名列或多个候选章节无法可靠绑定；
- 用户要求“整理”但同时出现可能意味着改写的措辞；
- 需要把文本、图片、音频或视频发送到外部 API；
- 请求格式在全部同格式转换器失败后需要改成其他格式；
- 清洗会删除大量行、覆盖原值或不可逆匿名化；
- 工具执行会产生外部副作用。

前端一次只显示一个最关键问题。回答后生成新 revision 并重新绑定，不继续沿用旧的模糊计划。

## 7. 能力包协议

每个能力包提供 `CapabilityManifest`：

```yaml
id: table.query.duckdb
version: 1
accepts: [arrow_table, parquet, csv, xlsx_table]
produces: [arrow_table, parquet]
operations: [filter, project, sort, join, group, aggregate, union, deduplicate]
deterministic: true
evidence_preserving: true
side_effect: none
network: none
resource_class: cpu_medium
limits:
  max_rows: "<configured>"
  timeout_seconds: "<configured>"
healthcheck: "<callable>"
```

统一工具调用返回：

- `status`；
- 输入/输出 ArtifactRef；
- 行/页/字符/时长账本；
- lineage 事件；
- warnings/rejects；
- 可重试分类；
- 工具版本和配置摘要；
- 资源使用；
- 可供验证器读取的事实，不返回只面向人类的模糊成功文本。

LLM 不能直接执行任意 Python、Shell、SQL 或文件操作。它只能选择注册过的工具和符合 JSON Schema 的参数；Physical Planner 再将其编译为安全算子。

## 8. 成熟开源工具选型

### 8.1 选型原则

每项能力保留“首选、备选、降级”三层，但运行时不盲目全跑。批次 0 使用同一真实/脱敏语料比较：

- 准确率、召回率、结构/版式保真度；
- 表格行列、阅读顺序和 EvidenceRef 保留；
- 首次/缓存延迟、吞吐；
- 峰值 RAM、显存和临时磁盘；
- 崩溃、挂死、损坏/加密文件处理；
- Windows 本机、Linux 容器和 Python 版本兼容；
- 安装维护、健康检查和可观测性；
- 不可信输入隔离能力。

未达到门槛的工具不因为知名度高而进入默认链。

### 8.2 工具矩阵

| 能力 | 首选 | 备选/补充 | 采用方式 |
|---|---|---|---|
| Harness 编排 | 现有 LangGraph | 无需另引 CrewAI/AutoGen | 保留现有控制面，新增强类型节点和有界边 |
| 结构化模型输出 | Pydantic + Instructor | Provider 原生 structured output | 沿用现有 Provider，不让模型输出自由文本计划 |
| 表格执行 | DuckDB | Polars/Pandas | DuckDB 负责过滤、投影、连接、分组和聚合；真实性能不达标再比较 Polars |
| 安全 SQL/表达式 | 现有 SQLGlot | DuckDB Relation API | 只接受白名单 AST，不接受模型生成的任意 SQL 直跑 |
| 批次/列式交换 | 现有 PyArrow/Parquet | Pandas | 大数据继续批次化，不把整表放入图状态 |
| 结果 Schema 校验 | Pandera | 现有 QualityGate/Pydantic | 用 strict/ordered/unique/checks 验证精确列和行级谓词 |
| 文档结构主链 | Phase 4A 现有解析器 + MinerU/Paddle | Docling sidecar | 证据主链不推倒；Docling 用于扩展格式和统一中间表示 PoC |
| 轻量文本转换 | MarkItDown | Apache Tika | MarkItDown 作常见格式轻量 fallback；Tika 作广格式探测/文本兜底，不替代版面证据 |
| 旧 Office/ODF | LibreOffice headless | Apache Tika | 独立容器转换为 OOXML/PDF，保留原件和转换 lineage |
| 标记文本互转 | Pandoc | Docling/MarkItDown | Markdown/HTML/EPUB/LaTeX/RTF 等按白名单转换，禁用不可信过滤器 |
| 结构化差异 | DeepDiff | 自带 `difflib` 用于纯文本 | 先结构/段落对齐，再由模型解释有证据的语义差异 |
| PII 检测/脱敏 | Presidio | 规则 + 可插拔 NER/GLiNER PoC | 作为可选能力包；中文必须有自建 golden，不能宣称零漏检 |
| XLSX | 现有 openpyxl + PyArrow | XlsxWriter（复杂图表 PoC） | 输出后重新打开并验证 sheet、列、行和公式注入防护 |
| DOCX | docxtpl + 现有 python-docx | LibreOffice 转换 | 模板负责版式，python-docx 负责结构和最终校验 |
| PDF | HTML/CSS + WeasyPrint | LibreOffice/Pandoc 路径 | 只渲染受控模板；禁止任意本地/网络 URL 读取 |
| PPTX | PptxGenJS | python-pptx | Node sidecar 生成，LibreOffice/OOXML 解包做基本 QA |
| 图片/音视频 | Phase 4C：FFmpeg/ffprobe、PySceneDetect、现有 OCR/VLM | faster-whisper、FunASR/Qwen3-ASR、pyannote | 4B 只冻结接口，4C 按真实中文语料 A/B 后选择 |
| 追踪 | OpenTelemetry + Phoenix | 现有日志 | 自托管，记录计划/工具/验证轨迹，敏感正文默认不进 trace |
| LLM 回归 | pytest + Promptfoo | Phoenix datasets/experiments | 确定性断言优先，模型评审只能作补充指标 |
| 后台队列 | Phase 5B：Celery + 现有 RabbitMQ | Redis broker | CPU/转换/GPU 队列隔离；任务必须幂等 |

相关上游能力依据：

- DuckDB Relation API 支持针对 DataFrame、CSV、Parquet 的惰性 filter/project/group/join/union；
- Pandera 支持严格列集合、列顺序、唯一性和 DataFrame checks；
- Docling 支持 PDF、Office、ODF、HTML、Markdown、CSV、图片及部分音视频，并能输出统一 JSON/HTML/Markdown/Text；
- MarkItDown 可作为 PDF/Office/HTML/文本/ZIP 等轻量 Markdown 转换备选；
- Pandoc 通过 reader/writer 和中间 AST 完成标记格式互转，并提供 sandbox 模式；
- LibreOffice 支持 headless 转换；
- WeasyPrint 适合从受控 HTML/CSS 生成 PDF，但不可信 HTML/CSS 必须限制 URL、内存和超时；
- PptxGenJS 可在 Node 中生成 OOXML PPTX；
- faster-whisper 支持 CPU/GPU 量化、批量、时间戳和 VAD；
- PySceneDetect 提供镜头检测并可配合 FFmpeg；
- Presidio 支持文本、图片和结构化数据的 PII 检测/匿名化，但上游也明确提示不能保证检出所有敏感信息；
- Phoenix 基于 OpenTelemetry/OpenInference 提供自托管追踪、评测和数据集实验；
- Celery 支持按任务路由到独立队列，重任务必须按幂等和确认语义设计。

### 8.3 不采用的做法

- 不再为“表格”“Word”“合同”各写一套关键词路由；
- 不把 Docling、Tika、MarkItDown、LibreOffice 同时嵌入主 Python 3.13 进程；
- 不让 LLM 直接编辑 DataFrame 或最终文件；
- 不用 Label Studio 作为默认人工流程；
- 不因单机需要 S3 API 就强制引入 MinIO；单节点对象服务不会自动获得跨主机高可用；
- 不在 Phase 4B 先引入 Kubernetes。

## 9. 常见场景覆盖

| 场景 | 计划类型 | 主要能力包 | 计划阶段 |
|---|---|---|---|
| 表格按条件筛选、保留指定列 | `tabular_transform` | inspect/bind + DuckDB + Pandera + XLSX | Phase 4B |
| 多表合并、分组、汇总、对账 | `tabular_transform` | DuckDB + reconciliation validator | Phase 4B |
| 文档提取商务/付款/交付条款 | `extract` + `compose` | Phase 4A evidence + passage selector + DOCX/PDF | Phase 4B |
| 多份合同条款横向比较 | `compare` | paragraph alignment + DeepDiff + evidence | Phase 4B |
| 合规检查、缺项清单 | `audit` | rules + evidence-bound semantic classifier | Phase 4B |
| 从多文件汇编一份报告 | `compose` | source selection + verbatim composition + renderer | Phase 4B |
| 显式总结、改写、翻译 | `summarize/translate` | LLM capability + faithfulness validator | Phase 4B |
| 常见格式互转 | `convert` | LibreOffice/Pandoc/Docling/MarkItDown/renderer | Phase 4B |
| 图片表单、截图文字 | `extract` | 现有 Paddle/MinerU/VLM + same STP | Phase 4C |
| 客服录音转写、说话人和要点 | `transcribe/extract` | FFmpeg + ASR + diarization + evidence time range | Phase 4C |
| 视频字幕、语音、关键帧信息 | `transcribe/extract` | FFmpeg + PySceneDetect + ASR/VLM | Phase 4C |
| 登录企业网站找报表 | `discover` | isolated browser + API handoff + same STP | Phase 5A |
| 保存可复用任务模板、批量排队 | STP/Bound Plan template | Recipe + Celery + lifecycle | Phase 5B |

## 10. 常见格式策略

### 10.1 输入

Phase 4B 的目标输入集合：

- 表格：CSV、TSV、XLSX、XLS、JSON、JSONL、Parquet、HTML/XML 表；
- 文档：PDF、DOCX、DOC、PPTX、PPT、ODT/ODS/ODP、HTML、Markdown、TXT、RTF、EPUB；
- 图片：PNG、JPEG/JPG、WEBP、TIFF（复用 4A/4C 解析）；
- 容器：安全 ZIP；
- 音视频：WAV、MP3、M4A、AAC、FLAC、OGG、MP4、MOV、AVI（Phase 4C）。

每种格式必须在格式能力表中标记：

- `native`：保留结构和证据；
- `converted`：先转为规范中间格式；
- `text_fallback`：只保证文本，不保证版面；
- `unsupported`：明确拒绝和原因。

### 10.2 输出

首批权威/查看输出：

- 表格/数据：JSON、JSONL、CSV、XLSX、Parquet；
- 文档：DOCX、PDF、HTML、Markdown、TXT；
- 演示：PPTX；
- 证据侧车：Evidence JSONL、Lineage JSONL、Quality JSON、Manifest JSON。

输出 QA 不是“文件存在”：

- XLSX 用 openpyxl 重开并核对 sheet/行/列；
- DOCX 解包检查 OOXML，并用 python-docx 重开；
- PDF 用 PDF 解析器核对页数、文本和关键标题，必要时渲染抽样页；
- PPTX 解包检查 OOXML、幻灯片数和必要元素；
- JSON/JSONL/CSV/Parquet 重新读取并核对 Schema、行数和哈希。

## 11. 本机开发方案

### 11.1 已验证环境快照（2026-07-24）

- Windows 11 Pro 64 位；
- Intel Core Ultra 9 285H，16 个逻辑处理器；
- 31.5 GB RAM；
- Intel Arc 140T，无 NVIDIA/CUDA；
- 项目 Python：`E:\python3.13\python.exe` 3.13.7；
- `torch 2.9.1+cpu`，`cuda_available=False`；
- Docker Desktop：16 CPU，约 16.5 GB 内存；
- Node 22.22.1、npm 10.9.4、Git 2.55、FFmpeg 8.1.2；
- MinerU `192.168.1.21:8000/health`：HTTP 200；
- Paddle Pipeline `192.168.1.21:18081/health`：HTTP 200；
- Paddle VLM `192.168.1.21:18080/v1/models`：HTTP 200；
- Qwen `192.168.1.20:6012/v1/models`：HTTP 200；
- LangChain/LangGraph 栈已对齐 `requirements.txt`：`langchain 1.2.2`、
  `langgraph 1.0.5`、`langgraph-checkpoint 3.0.1`、`pydantic 2.12.5`；
- 主环境当前已安装 DuckDB 1.5.5、Polars 1.43.0、Pandera 0.32.1 和
  MarkItDown 0.1.6；Docling 只在隔离环境验证。LibreOffice、Pandoc 和 Java/Tika
  仍未安装。
- 当前 `pip check` 有 2 条已知冲突：缺少 `types-pytz`，以及 crawl4ai 与当前 lxml
  主版本不符。先前外部 editable `spider-dcd` 已从共享解释器卸载；新增依赖前仍必须
  锁定或隔离并回归受影响采集器。

此机器可以完整开发和验证 Harness、表格算子、文档编排与 API/UI，但不适合在本机运行重型 OCR、VLM、ASR 或视频模型。LAN 服务已补足主要推理能力。

### 11.2 本机拓扑

```text
Windows 主进程（Python 3.13）
  ├─ FastAPI / LangGraph / STP / Binder / Validator
  ├─ DuckDB / PyArrow / Pandera / openpyxl
  ├─ Node 侧车：PptxGenJS
  ├─ Docker profile：Docling/MarkItDown（Python 3.11/3.12）
  ├─ Docker profile：LibreOffice headless
  ├─ 可选 Docker profile：Apache Tika（Java）
  └─ LAN：Qwen / MinerU / Paddle Pipeline / Paddle VLM
```

原则：

- 核心进程保持 Python 3.13，重依赖放入独立 sidecar，避免依赖污染；
- Docker 侧车按 Compose profile 按需启动，重转换并发默认 1；
- 保留至少 4–6 GB Docker 余量，不要求所有候选工具同时常驻；
- 本机 CPU fallback 只用于小样本和故障隔离，不作为效果基线；
- 制品、临时文件和模型缓存放在 F 盘的项目专用目录，不使用只剩约 10 GB 的 G 盘；
- 远程 OCR 并发不得超过服务健康信息和本项目配置上限；
- 每次实施前重新探测端点、版本和剩余磁盘，不能把本快照永久当成事实。

### 11.3 本机前置条件

1. 统一项目环境和锁文件，先解决 LangGraph 运行版本漂移。
2. 新增 `compose.semantic-tools.yml` 或等价 profile，不修改现有 SearXNG/RSSHub/Firecrawl 服务。
3. 安装/容器化 DuckDB、Pandera、Docling、MarkItDown、LibreOffice、Pandoc；Tika 仅在 PoC 证明有增益后常驻。
4. 所有 sidecar 提供版本、health、timeout、输入大小和网络策略。
5. 真实业务评测目录加入 `.gitignore`，仓库仅提交脱敏 fixture。

## 12. 最终服务器方案

### 12.1 已知硬件

- CPU：2 × Intel Xeon Gold 6530，合计 64 物理核；
- 内存：512 GB DDR5；
- GPU：4 × NVIDIA L20 48 GB；
- 磁盘：960 GB SSD ×2，1.92 TB SSD ×2；
- 目标在线用户：10–20；
- 无 NAS、无外部对象存储。

### 12.2 推荐基线

- 单机 Linux + Docker Compose；
- 首选候选 Ubuntu Server 24.04 LTS；
- 正式安装前使用目标 NVIDIA 驱动、CUDA、Container Toolkit、vLLM、Paddle/MinerU 实机矩阵做 PoC；
- 若 24.04 与关键模型/驱动组合不稳定，退回经供应商矩阵验证的 22.04 LTS；
- 暂不使用 Kubernetes，但 ArtifactStore、队列和模型端点必须保持可迁移接口。

### 12.3 存储

建议初始布局：

- 960 GB ×2：RAID1，系统、Docker、数据库、模型配置和关键日志；
- 1.92 TB ×2：RAID1，上传原件、转换件、任务中间产物和交付结果；
- 文件系统 ArtifactStore 作为首选，不强制单机 MinIO；
- 数据库只保存元数据、所有权、状态、哈希和制品引用，不把大文件塞进数据库。

RAID1 只缓解单盘故障，不是备份。没有异机或离线备份时，服务器不得被宣称为完整生产级容灾。正式生产前必须增加至少一种异机、离线或远端加密备份，并完成恢复演练。

生命周期：

- 临时分片/渲染缓存：默认 24 小时；
- 可诊断失败中间件：默认 7 天；
- 普通任务原件和结果：默认 90 天；
- 固定任务：用户解除固定前保留；
- 用户主动删除：生成可审计删除任务并清理所有派生物；
- 模型权重和公共工具缓存不按用户任务 90 天策略删除。

### 12.4 服务拓扑

```text
Reverse Proxy
  └─ Frontend + FastAPI
       ├─ PostgreSQL（任务/计划/权限/审计）
       ├─ RabbitMQ（Phase 5B）
       ├─ Redis（锁、租约、短状态；不作权威任务库）
       ├─ CPU workers（检查、绑定、DuckDB、验证、导出）
       ├─ Converter workers（LibreOffice/Pandoc/Docling/Tika）
       ├─ GPU lease manager
       │    ├─ 文本规划/抽取模型
       │    ├─ OCR/文档 VLM
       │    └─ ASR/视频 VLM（Phase 4C）
       ├─ ArtifactStore（数据 RAID1）
       └─ OpenTelemetry Collector + Phoenix
```

### 12.5 GPU 和并发

初始目标：

- 最多 4 个重型 GPU 作业同时获得租约；
- 普通 API、表格和轻文档任务不占 GPU 租约；
- CPU worker、转换 worker、OCR、ASR、文本/VLM 使用不同队列和并发门；
- 不在计划中硬编码“一张 GPU 永久只跑一个模型”，通过 Compose profile 和租约配置做两种 PoC：
  - 文档/表格优先：文本模型副本或 TP + OCR/VLM + 一张弹性 GPU；
  - 媒体批处理：文本服务保持最低副本，更多 GPU 临时给 ASR/视频。

最终模型、量化、TP/副本数和每类并发由真实 10–20 用户压测决定。GPU OOM、排队时间、首 token、任务完成时间和质量必须一起比较，不能只看吞吐。

### 12.6 用户隔离

- 每个任务、计划、制品和下载记录必须带 `owner_user_id`；
- API 查询默认附加所有者条件，下载不能接受任意服务端路径；
- PostgreSQL 迁移后增加 Row-Level Security 作为第二道防线；
- ArtifactStore 使用不可猜测 ID 和服务端授权读取；
- 管理员运维读取必须记录人、时间、目的和制品；
- trace 默认只记录哈希、计数和脱敏摘要，不记录完整业务正文；
- 当前阶段不支持用户之间共享或共同编辑。

## 13. 分批实施计划

### 批次 -1：实施前闸门

目标：保证不会在错误依赖和不可复现基线上开发。

- [x] 重新探测 Git、Python、Node、Docker、磁盘和四个 LAN 端点；
- [x] 解决 `requirements.txt` 与实际 LangGraph 版本漂移；
- [x] 冻结 STP v1、Bound Plan、CapabilityManifest、ToolResult、VerificationReport JSON Schema；
- [x] 编写 ADR：语义计划与有界工具 Loop；
- [x] 建立不可信转换 sidecar 的网络、CPU、内存、超时和只读挂载策略；
- [x] 建立真实业务样本本地目录和脱敏 fixture 生成流程；
- [x] 不迁移历史任务；如需重置，另行备份并再次询问用户。

退出标准：依赖可复现；Schema 通过 round-trip；没有删除或提交运行库存。

完成证据（2026-07-24）：

- 实现提交：`81de441b65d9c7f7d9fe0c9ae3b8609a2f6e775b`；
- 环境快照：`docs/plans/phase4b-batch-minus1-environment.json`；
- 契约实现：`src/semantic_harness/models.py`；
- ADR：`docs/adr/0012-semantic-task-plan-and-bounded-tool-loop.md`；
- 私有样本流程：`tests/fixtures/semantic_harness/README.md`、
  `scripts/prepare_semantic_fixture.py`；
- 定向门禁：25 passed；全仓后端 848 passed、4 skipped；前端生产构建通过。
  原 checkpoint/graph 对齐前后均保持通过。

边界：本批只冻结控制面契约和安全边界；尚未实现自然语言编译、Source Binder、Physical Plan、
表格执行器、Harness 图或前端。

历史休会说明：本批已完成且无需重做；批次 0、批次 1 也已完成。当前直接进入批次 2，
不重复批次 -1/0/1。

### 批次 0：基线与开源工具 A/B

- [x] 把“谢超群”任务做成脱敏 golden fixture；
- [x] 增加表格筛选/投影/合并/聚合的最小正反样例；
- [x] 增加商务条款原文摘录 → DOCX/PDF；
- [x] 增加两版合同结构/语义差异；
- [x] 增加不支持格式、损坏文件、加密文件和转换超时；
- [x] 对 DuckDB/Polars、Docling/MarkItDown 及 DOCX/PDF/PPTX 输出链做实测；
  Tika/LibreOffice/Pandoc 因本机依赖或镜像准备门未通过，已形成“不进入本机默认链，
  转服务器 sidecar PoC”的明确结论；
- [x] 记录质量、延迟、RSS 前后差值、输入/输出字节、失败率和证据保留；
  独立进程峰值 RAM/临时磁盘峰值仍是生产预算待办，不把 RSS 差值冒充峰值。

退出标准：每项默认/备选工具有数据支撑的采用或拒绝结论；结果写入版本化 JSON 和 ADR。

执行证据：`docs/plans/2026-07-26-phase4b-batch0-execution-report.md`、
`docs/plans/phase4b-batch0-results/` 和 ADR-0013。按既定决策，建议路由已形成，
生产能力包尚未实现，当前运行时默认保持不变。MinerU Hyper 后续复测证明：
`hybrid-http-client + medium` 可运行但表格质量未达标，high 返回空结果，本地
`hybrid-engine` 仍报 device 为空；均不阻塞批次 1 的 STP 编译器开发，详见开发前评审。

### 批次 1：STP 与语义编译器

- [x] 扩展 `src/semantic_harness/`，不继续膨胀 `ResultContract`；
- [x] 自然语言生成强类型 Logical Plan；
- [x] 分离过滤、投影、行粒度、合并、聚合、内容政策和输出；
- [x] 计划静态校验：不允许互相冲突或无法验证的组合；
- [x] 生成用户可读计划摘要；
- [x] 保存不可变 plan revision、模型、提示词版本和 plan hash；
- [x] 旧 Phase 4A `ExtractionSpec` 通过适配器进入 STP，不复制执行主链。

实施前约束：

- 保持 MinerU pipeline + Paddle fallback 当前生产路由；
- 不把批次 0 的固定夹具评测 Graph 接成生产执行器；
- 新增依赖前先处理或隔离开发前评审登记的依赖冲突；其中外部 editable 包冲突已在
  2026-07-27 清除，当前仍剩 `types-pytz` 缺失和 crawl4ai/lxml 版本约束两条；
- 只在当前主体工作区开发，已登记的额外 worktree 未确认前不使用也不删除。

退出标准：golden 意图全部生成正确逻辑计划；关键歧义能被识别而不是猜测。

执行证据：`docs/plans/2026-07-26-phase4b-batch1-execution-report.md` 和
`docs/plans/phase4b-batch1-results/model-eval.json`。本批只交付后端逻辑计划编译、
测试 API 和审计存储；物理字段绑定、数据执行、验证/修复执行 Loop 与正式前端属于后续批次，
不得据此宣称完整 Phase 4B 已完成。

### 批次 2：来源检查与绑定

详细实施方案：
`docs/plans/2026-07-26-phase4b-batch2-source-inspector-binder-plan.md`。
执行报告：
`docs/plans/2026-07-26-phase4b-batch2-execution-report.md`。

- [x] 格式、MIME、加密、损坏、大小和能力检查；
- [x] 表/列/类型/样本值/重复表头检查；
- [x] 文档章节、段落、表格和结构位置证据检查；
- [x] 语义字段到实际字段/章节的候选绑定及证据；
- [x] 自动绑定仅在高置信且可验证时生效；
- [x] 绑定失败生成一个最高价值澄清问题；
- [x] Bound Plan 确认后不可变。

退出标准：模型不再在看不到真实 Schema 时决定物理字段；错误绑定不会进入执行。
公开 Golden 6/6、自动错误绑定 0；全仓后端 882 passed、4 skipped、0 failed。

### 批次 3：能力包注册表与确定性表格执行

- [x] CapabilityManifest 注册、版本、健康检查和资源类；
- [x] DuckDB 安全物理计划：filter/project/sort/union/join/group/aggregate/deduplicate；
- [x] SQLGlot AST 白名单和参数绑定；
- [x] PyArrow 批次输入/输出及 lineage；
- [x] Pandera strict/ordered/row checks；
- [x] 结果行到源行的稳定 ID 映射；
- [x] 所有表格操作生成前后行数和金额/数量对账。

退出标准：“谢超群”门禁精确通过；错误过滤、漏列、多列、错聚合和零行假成功全部被阻断。

详细实施方案：
`docs/plans/2026-07-26-phase4b-batch3-deterministic-tabular-execution-plan.md`。
执行报告：
`docs/plans/2026-07-26-phase4b-batch3-execution-report.md`。
公开门禁覆盖六种格式和常用确定性操作；全仓后端 895 passed、4 skipped、0 failed。

### 批次 4：文档提取、比较、核查与编排

- [x] 复用 Phase 4A 元素/EvidenceRef，不重新实现 OCR；
- [x] `verbatim` passage selector；
- [x] 多文档章节对齐和结构化差异；
- [x] 审核规则分成确定性规则和有证据语义判断；
- [x] 总结/改写/翻译仅在内容政策明确时调用模型；
- [x] 文档编排生成中间 Document AST；
- [x] 每个段落、表格和结论保留来源引用。

退出标准：商务条款可从 PDF/DOCX 等首批七种格式提取为可验证的中间 Document AST；
原文模式无无来源改写；比较结果每项差异有双方证据。正式 DOCX/PDF 渲染、重开 QA 和下载
按本计划批次 6 交付，不在批次 4 冒充完成。

执行报告：
`docs/plans/2026-07-27-phase4b-batch4-execution-report.md`。公开脱敏合同本地语义门 3/3，
证据覆盖 100%；全仓后端 915 passed、4 skipped、0 failed。

### 批次 5：有界 Harness Loop

> 实施决策（2026-07-27）：批次 5 先交付真实可执行的后端灰度入口，通过独立 API 和
> 自动化门禁运行；本批不替换 Phase 4A 当前正式执行入口。待批次 6 正式输出/下载和
> 批次 7 前端状态展示完成并验收后，再决定正式切换。
>
> 详细实施方案：
> `docs/plans/2026-07-27-phase4b-batch5-bounded-harness-plan.md`。

- [x] LangGraph 新增 interpret/inspect/bind/plan/execute/verify/repair/needs_user/deliver 节点；
- [x] 工具参数只接受 Schema 校验后的对象；
- [x] 失败分类：暂时性、资源耗尽、工具不兼容、计划错误、数据不足、需要用户和政策拒绝；
- [x] 暂时性错误按有界退避重试；
- [x] 兼容工具切换只允许同契约注册项；当前无第二个生产工具时明确失败，不伪造替代；
- [x] 确定性安全修复自动执行；
- [x] 语义 replan 预算最多两次，改变语义时必须新建 revision；
- [x] 同一后置条件连续两次失败立即停止；
- [x] 不允许工具或来源内容扩大任务权限。

退出标准：不存在无限循环；假成功为 0；每次修复都能解释“修改了什么、为何不改变用户语义”。

执行报告：
`docs/plans/2026-07-27-phase4b-batch5-execution-report.md`。Phase 4B 定向 78 passed；
全仓后端 925 passed、4 skipped、0 failed；前端生产构建通过。

### 批次 6：输出、转换和下载闭环

> 完成状态（2026-07-27）：已实现 11 种正式格式、逐文件重开 QA、SHA-256 Manifest、
> staging 原子发布、SQLite 交付登记、用户隔离 `output_id` 下载和篡改阻断。全仓
> 928 passed、4 skipped、0 failed，前端生产构建通过。证据见
> [批次 6 执行报告](2026-07-27-phase4b-batch6-execution-report.md)。

- [x] Renderer/Converter 注册表；
- [x] XLSX、CSV、JSON/JSONL、Parquet；
- [x] DOCX、PDF、HTML、Markdown、TXT；
- [x] PPTX；
- [x] 输出重开/解析/渲染 QA；
- [x] 权威数据与业务查看副本区分；
- [x] unsupported → 同格式备选 → 用户选择替代格式；
- [x] 下载权限、过期、延迟释放和 Manifest 账本沿用 v0.0.5 修复。

退出标准：没有“文件生成了但打不开”、空权威文件或无权限下载；没有静默格式替代。

### 批次 7：前端体验

> 完成状态（2026-07-27）：统一数据工作台、后台持久化编排、真实 SSE、取消、
> 单问题闸门、结果/原件/证据预览、正式下载、不可变结果版本和用户隔离回收站已实现。
> 首版 UX 验收被用户否决后，已补齐上传即时预览、阶段进度聚合、Harness 乱序回放、
> 结果优先、渐进披露、待确认重开、移入回收站和三栏窄宽布局。Playwright 完整
> 36 passed；生产构建和深浅主题 axe 门禁通过。
> 真实 Word 验收又发现限定条款任务退化为全文转换；第一次修复仍混淆全源搜索和全文
> 输出。二次纠偏已补结构化澄清继承、全源 `content_query`、完整 ID 分类覆盖、显式
> 全文契约、五层范围失败关闭和 `document_scope_respected` 校验。用户原始 Word
> 严格复验扫描 501 个目标、选择 83 个，六组漏项和前序误选均为 0；全仓后端
> 953 passed、4 skipped、0 failed；证据见
> [批次 7 执行报告](2026-07-27-phase4b-batch7-execution-report.md)和
> [P0 语义纠偏报告](2026-07-27-phase4b-batch7-p0-semantic-correction-report.md)。

- [x] 用自然语言摘要显示“系统理解为：范围、过滤、行粒度、列、操作、输出”；
- [x] 高级详情可查看 STP，但普通用户不需要理解 JSON；
- [x] 仅实质歧义时显示单问题弹窗；
- [x] 外部 OpenAPI 显示将外发的数据类型、目的和目标服务；
- [x] 显示工具链、进度、修复次数和最终验证；
- [x] 结果页显示“为什么这些行/段落被选中”；
- [x] 失败时给出可操作原因和可选下一步。

退出标准：已明确任务不被反复确认；歧义任务不被默默猜测；用户能看懂最终结果如何满足原要求。
代码门已通过，但用户复验尚未通过，因此批次 7 仍不能作为已验收阶段移交。

### 批次 8A：本机主流程与框架验收

- [x] pytest/Hypothesis 固化生命周期、权限、取消、幂等、格式协商和失败关闭；
- [x] OpenTelemetry 记录脱敏任务轨迹，Phoenix Docker 已完成 HTTP 200 和 OTLP 实发；
- [x] DOCX/PDF/XLSX/CSV 通过真实 HTTP/Worker/Delivery 和独立读取器重开；
- [x] 完整 PC Playwright 通过；真实页面后端联动仍由用户按验收清单确认；
- [x] 固定四类用户失败路径和四类后端故障注入；
- [x] Promptfoo 六案例隔离 PoC 6/6，通过但不升级为强制门禁；
- [x] 输出用户本人可直接操作的验收清单。

详细方案见
[`2026-07-28-phase4b-batch8a-framework-plan.md`](2026-07-28-phase4b-batch8a-framework-plan.md)。
执行证据见
[`2026-07-28-phase4b-batch8a-execution-report.md`](2026-07-28-phase4b-batch8a-execution-report.md)。

退出标准：只能声明“本机框架工程验证通过”；用户按清单明确确认后，才能声明
“用户验收通过”。

### 批次 8B：部署、压力与封板

- [ ] 服务器 Compose、GPU 租约和 10–20 用户压力门；
- [ ] 部署故障、容量、磁盘和长期运行验证；
- [ ] 对照第 15 节做完整封板审计；
- [ ] 更新部署说明和正式封板材料。

批次 8B 必须再次获得用户开工确认。达到第 15 节全部标准后，才可评估 Phase 4B 封板；
仍不得自行创建或移动版本标签。

## 14. 测试与评测矩阵

### 14.1 计划正确性

- 过滤、投影、粒度、合并、聚合、排序和输出格式分别评测；
- 同一句话的同义表达应生成语义等价计划；
- “整理”不能误触发总结；
- “输出整表”不能抹掉过滤和投影；
- 计划缺少可验证后置条件时禁止执行。

### 14.2 执行正确性

- 结果行集合与 golden 完全一致；
- 精确列集合和顺序；
- 过滤谓词满足率 100%；
- 聚合可回算；
- 源/结果账本守恒；
- 非空语义结果 EvidenceRef 覆盖率 100%；
- 跨文件、跨任务、跨用户串值为 0。

### 14.3 轨迹正确性

- 只调用完成任务所需工具；
- 不调用未授权网络或副作用工具；
- 修复次数不超过 2；
- 同一失败不得无参数变化地重复调用；
- 最终成功必须由 Validator 决定，不由模型自报。

### 14.4 格式和视觉

- 生成文件可重新打开；
- 中文字体、表格分页、标题层级、页码和换行正确；
- DOCX/PDF/PPTX 抽样渲染检查；
- 大表不会因 Excel 行/列或单元格限制静默截断；
- 证据侧车不因查看副本限制丢失。

### 14.5 性能

本机用于“能完整开发和跑通”，服务器用于“效果、吞吐和稳定性”。批次 0 先建立基线，再冻结以下预算：

- 计划生成/绑定 p50/p95；
- 每 10 万/100 万行 filter/project/group 延迟与峰值内存；
- 100 页文档和多文件编排延迟；
- DOCX/PDF/PPTX 转换吞吐与峰值内存；
- 4 个重 GPU 作业 + 普通任务并发时的排队和完成时间；
- 90 天保留策略下的磁盘增长预测。

不得用“100 万行合成测试”冒充真实 500 MB 文件门禁；真实 500 MB 文件仍在 Phase 5B 独立验收。

## 15. Phase 4B 封板标准

只有同时满足以下条件才能封板：

1. “谢超群”黄金任务为 11 行、2 列、1 表、谓词满足率 100%、证据覆盖率 100%。
2. 至少 30 组自然语言变体的关键计划字段全对；高影响歧义不得自动猜测。
3. 表格 filter/project/merge/group/aggregate/sort/dedup 各有正反和账本测试。
4. 商务条款从至少 PDF、DOCX 两类来源提取并生成可打开的 DOCX/PDF。
5. 至少一组跨文档比较和一组合规核查，每条结论有证据。
6. `verbatim` 模式无无来源总结、改写或补写。
7. 全部输出经过格式重开和账本校验；空/损坏产物不登记为权威输出。
8. 不支持格式没有静默替代。
9. 外部 OpenAPI 未确认调用次数为 0。
10. 跨用户读取、下载和任务枚举成功次数为 0。
11. Repair Loop 最大 2 次，无无限循环，无相同失败空转。
12. 本机 Windows 方案完整跑通；服务器方案通过驱动/容器/GPU/磁盘 PoC。
13. deterministic tests、Promptfoo 轨迹门、前端 E2E、全仓回归全部通过。
14. 已知限制、工具版本、A/B 结果和运维手册入库。

## 16. 风险与处理

| 风险 | 处理 |
|---|---|
| 模型计划看似合理但遗漏语义 | 强类型 STP + Source Binder + deterministic postconditions |
| Agent 无限尝试 | 最多两次语义修复，同失败立即停 |
| LLM 直接改数据造成不可审计 | LLM 只产计划/候选，数据由白名单算子处理 |
| 格式转换丢版式或内容 | 原件不变、转换 lineage、真实语料 A/B、输出重开/渲染 |
| Python 3.13 依赖冲突 | 重工具放 Python 3.11/3.12 或 Java/Node sidecar |
| 本机无 CUDA | LAN 推理 + CPU 小样本降级，不把本机作为重模型效果基线 |
| Docker 内存不足 | 按 profile 启动，重转换并发 1，记录峰值 |
| 中文 PII 漏检 | Presidio 仅作可选能力，增加中文规则/模型和本地 golden，不承诺全检出 |
| 文档提示注入 | 来源内容始终是数据，不能改变系统目标、工具和权限 |
| 单机磁盘损坏/主机故障 | RAID1 + 校验 + 恢复流程；正式生产前必须异机/离线备份 |
| GPU 服务互相抢显存 | GPU 租约、独立队列、显存水位和模型 profile |
| 工具版本升级导致漂移 | 固定镜像 digest/包版本，记录工具版本，golden 回归后升级 |
| 管理员访问真实文件缺乏约束 | 管理访问审计、最小权限和敏感正文 trace 禁止 |
| MinerU 报完成但内容为空 | 客户端以页/块/后置条件判定成功；空结果显式失败并回退，不信服务状态文本 |
| 主环境依赖冲突继续扩大 | 批次 1 前锁定或隔离当前 3 项冲突，新增重依赖继续走 sidecar |
| 遗留 worktree 污染提交 | 不使用额外 worktree；先核对改动归属，清理必须另行确认；提交始终使用文件白名单 |

## 17. 实施时再确认的闸门

以下问题**现在不需要继续询问用户**，到对应实施动作前再确认：

1. 是否备份并重置开发环境中的历史任务、工作区和下载数据；
2. Ubuntu 24.04 还是 22.04，以 NVIDIA/模型实机 PoC 为准；
3. 两组 SSD 使用硬件 RAID、`mdadm` 还是其他受支持实现；
4. 最终文本、VLM、OCR 和 ASR 模型及其量化/并行配置；
5. 是否增加异机、离线或远端加密备份；
6. 是否让可选 Tika、Presidio、Phoenix 常驻；
7. 真实业务样本的本地存放位置和脱敏规则。

这些闸门不影响本计划冻结，也不得被误写为已经决定或已经实施。

## 18. 计划交付物和提交策略

计划基线提交只包含专项计划和四份权威上下文。进入开发后，每个批次必须只提交该批实现、
测试、生成 Schema、评测证据和必要文档，不得跨批混入运行库存。

批次 -1 的提交范围为：

- `src/semantic_harness/` 控制面契约；
- `docs/schemas/` 新增的五份 Schema 和索引；
- Schema 导出器、契约/私有样本测试和 fixture 准备工具；
- ADR-0012、环境快照、私有样本目录规范；
- `.gitignore` 及必要的计划、交接和知识基座状态同步。

明确排除：

- `.claude/settings.local.json`；
- `data/lessons/`；
- `data/templates/`；
- `frontend/test-results/`；
- 下载、数据库、缓存和真实业务文件。

提交时只能按文件白名单暂存，禁止 `git add .`。批次 -1 完成不代表 Phase 4B 功能完成；
后续实施继续逐批提交，每批均有独立回归和可回滚边界。

## 19. 上游资料

- [LangGraph workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)
- [LangChain tools / ToolNode](https://docs.langchain.com/oss/python/langchain/tools)
- [DuckDB Relational API](https://duckdb.org/docs/stable/clients/python/relational_api)
- [Pandera DataFrame schemas](https://pandera.readthedocs.io/en/latest/dataframe_schemas.html)
- [Docling supported formats](https://docling-project.github.io/docling/usage/supported_formats/)
- [Microsoft MarkItDown](https://github.com/microsoft/markitdown/blob/main/README.md)
- [Apache Tika supported formats](https://tika.apache.org/2.9.2/formats.html)
- [Pandoc User’s Guide](https://pandoc.org/MANUAL.html)
- [LibreOffice headless parameters](https://help.libreoffice.org/latest/ug/text/shared/guide/start_parameters.html)
- [WeasyPrint documentation](https://doc.courtbouillon.org/weasyprint/stable/)
- [PptxGenJS](https://gitbrent.github.io/PptxGenJS/docs/introduction/)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [FunASR](https://github.com/modelscope/FunASR)
- [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)
- [Qwen3-VL](https://github.com/QwenLM/Qwen3-VL)
- [PySceneDetect](https://www.scenedetect.com/docs/latest/)
- [pyannote.audio](https://github.com/pyannote/pyannote-audio)
- [Microsoft Presidio](https://github.com/microsoft/presidio)
- [DeepDiff](https://github.com/qlustered/deepdiff)
- [OpenTelemetry Python](https://opentelemetry.io/docs/languages/python/)
- [Arize Phoenix](https://arize.com/docs/phoenix/)
- [Promptfoo assertions and metrics](https://www.promptfoo.dev/docs/configuration/expected-outputs/)
- [Celery task routing](https://docs.celeryq.dev/en/stable/userguide/routing.html)
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- [PostgreSQL Row Security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
- [MinIO single-node reliability boundary](https://min.io/docs/minio/container/operations/install-deploy-manage/deploy-minio-single-node-single-drive.html)
