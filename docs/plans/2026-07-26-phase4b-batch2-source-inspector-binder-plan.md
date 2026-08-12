# Phase 4B 批次 2：Source Inspector + Binder 专项实施方案

> 文档状态：**已实施并通过本批公开 Golden 与全仓回归；不代表 Phase 4B 完成**
>
> 日期：2026-07-26
>
> 当前分支：`v0.0.6`（开发分支，未封板、无同名标签）
>
> 上游基线：Phase 4B 批次 -1/0/1 已完成，批次 1 提交为 `be2ab2d`
>
> 本批范围：后端 Inspector、Binder、不可变 Bound Plan、测试 API 和评测；不做正式前端
>
> 实施证据：
> [`2026-07-26-phase4b-batch2-execution-report.md`](2026-07-26-phase4b-batch2-execution-report.md)

## 1. 结论

本批要解决的核心问题不是“让模型猜哪个字段最像”，而是：

> 先读取真实来源结构，生成有证据的候选，再以确定性规则、语义模型和全局约束共同完成绑定；
> 证据不足就停止并一次只问一个问题，错误绑定绝不能进入后续执行器。

批次 2 完成后，系统应能把批次 1 的来源无关 STP：

```text
业务概念：姓名、核销工作量天数、付款条款
```

绑定为真实且不可变的物理目标：

```text
artifact/sheet/table/column
artifact/page/section/paragraph/table-cell
```

本批仍不执行筛选、合并、聚合、文档摘录或文件生成；这些属于批次 3、4、5。

## 2. 已确认的产品决策

1. 首批只覆盖：
   - 表格文件：CSV、TSV、XLSX、Parquet、JSON/JSONL；
   - 已解析文档：PDF、DOCX、PNG/JPG/JPEG/WEBP 进入 Phase 4A 后形成的
     `DocumentElement + EvidenceRef`。
2. 数据库、网页和 HTTP API 的物理字段绑定本批不做，但契约不得封死后续扩展。
3. 本批只做后端和测试 API，不做正式前端。
4. 高置信且可验证的候选才自动绑定；其余必须停止，一次只问一个最高价值问题。
5. 默认使用本地/LAN；外部 OpenAPI 未经确认不得收到 Schema、样本值或文档片段。
6. 表格和文档两条主线必须同时验收，自动错误绑定数必须为 0。
7. 成熟工具可新增，但冲突工具必须隔离；只有真实 Golden A/B 胜出后才能进入默认链。

## 3. 本批交付与不交付

### 3.1 必须交付

- `SourceInspectionReport`：来源真实性、可用性、结构、样本和诊断的不可变报告；
- 表格 inventory：工作表、表、表头、列、类型、空值、唯一性、样本和重复表头；
- 文档 inventory：章节、段落、表格、阅读顺序、跨页关系和 EvidenceRef 完整性；
- 语义字段/章节到真实目标的候选集合、分项分数、反证和来源证据；
- 全局绑定决策，不允许多个业务概念被贪心地错误绑定到同一物理列；
- `BoundPlan` 不可变 revision、输入哈希、Inspector/Binder 版本和绑定证据；
- 缺失/歧义时只返回一个问题，用户回答后创建下一 binding revision；
- 用户隔离的测试 API、Schema、Golden、A/B 报告和执行报告。

### 3.2 明确不交付

- 不执行 Polars/DuckDB 表格变换；
- 不执行文档摘录、比较或核查；
- 不生成 XLSX、DOCX、PDF 等最终交付文件；
- 不实现完整 execute → verify → repair Loop；
- 不做正式前端弹框、候选选择器或证据定位 UI；
- 不重新实现 OCR，不更改 MinerU/Paddle 当前默认路由；
- 不接数据库、网页或 HTTP API Binder；
- 不创建新版本分支或标签。

## 4. 开发前置条件

### 4.1 代码与数据前置

- 以 `v0.0.6` 当前批次 1 提交为唯一实施起点；
- 先复跑批次 1 的 26 项定向测试及全仓基线；
- 保持用户现有 `.claude/settings.local.json`、`data/lessons/`、
  `data/templates/`、`frontend/test-results/` 变动不进入提交；
- 验证数据库中是否存在已持久化 `BoundPlan`：
  - 若不存在，允许在尚未生产使用前修正一对多绑定契约；
  - 若已存在，必须新增 `spec_version=2` 和只读兼容适配器，禁止覆盖历史。

### 4.2 依赖前置

当前 `pip check` 的 3 项既有冲突仍需保持可见：

- `pandas-stubs` 缺少 `types-pytz`；
- `crawl4ai` 要求 `lxml~=5.3`，当前为 `lxml 6.1.1`；
- `spider-dcd` 要求 `httpx<0.28`，当前为 `httpx 0.28.1`。

批次 2 不以“顺手升级全仓依赖”解决这些冲突。新增候选工具先进入
`requirements-phase4b-eval.txt` 或独立 sidecar；验证无冲突且真实样本胜出后，再决定是否进入主依赖。

### 4.3 服务前置

- 本地 Qwen：继续作为最终语义裁决默认模型；
- embedding/rerank：优先复用现有 OpenAI-compatible `/embeddings` 和 Cohere-compatible
  `/rerank` 客户端，不复制一套 HTTP 实现；
- 未配置本地 embedding/rerank 时，系统必须退回确定性匹配并降低自动绑定覆盖率，
  不能改成低质量猜测；
- DeepSeek/百炼仅在用户明确选择并确认风险后可参与脱敏 A/B，不进入默认路径。

## 5. 总体架构

```text
SemanticTaskPlan revision
        │
        ▼
可信来源解析与权限校验
        │
        ▼
Source Inspector
  ├─ 文件身份/损坏/加密/大小/能力检查
  ├─ 表格结构与有界样本检查
  └─ 文档结构与 EvidenceRef 检查
        │
        ▼
Semantic Reference Normalizer
  └─ 合并 STP 中重复出现的同一业务概念
        │
        ▼
Candidate Generator
  ├─ 精确/规范化规则
  ├─ RapidFuzz 模糊召回
  └─ 本地 embedding 语义召回
        │
        ▼
Candidate Scorer
  ├─ 名称与语义
  ├─ 类型与样本值
  ├─ 文档位置与结构
  ├─ 反证/冲突
  └─ 本地 rerank/Qwen 有界裁决
        │
        ▼
Global Resolver
  └─ 全局最大权匹配 + 一对多目标规则
        │
        ├─ 高置信且可验证 ──► 不可变 BoundPlan
        │
        └─ 歧义/缺失 ──────► 一个澄清问题
                                      │
                                      └─ 用户回答后创建下一 binding revision
```

核心边界：

- Graph 状态只保存 ID、哈希、报告引用、候选摘要和决策，不保存整张表或整篇文档；
- Inspector 只读，Binder 不执行数据变换；
- 来源文本永远被视为数据，不能改变系统规则或扩大权限；
- STP 的用户语义不可被 Binder 原地改写。

## 6. 契约设计

### 6.1 `SourceInspectionReport`

建议包含：

- `inspection_id / inspector_version / generated_at`；
- `logical_plan_id / revision / hash`；
- `artifact_id / sha256 / size / declared_type / detected_type`；
- `status`：`ready / unsupported / corrupt / encrypted / over_limit / needs_user`；
- `capabilities`：可读取结构、可采样、可提取证据等；
- `tables` 或 `document` inventory；
- 有界诊断、告警和报告 canonical hash。

报告只追加，不覆盖；同一 `artifact_hash + inspector_version + limits` 可命中缓存。

### 6.2 表格 inventory

每张表必须使用稳定物理引用：

```text
artifact://<id>/sheet/<sheet-index>/table/<table-index>/column/<column-index>
```

记录：

- 工作表名、工作表序号、表序号和候选表头行；
- 原始表头、规范化表头、列位置、重复表头组；
- 推断类型与混合类型比例；
- null 比率、unique 比率、最小/最大长度；
- 有界样本的脱敏预览、值指纹和筛选字面量命中统计；
- 多行表头、合并单元格、空列、隐藏列和公式列提示；
- 来源行/单元格位置证据。

禁止：

- 自动把重复列改成 `_1/_2` 后假装歧义消失；
- 只看前 100 行就宣称整列类型确定；
- 为了绑定把整份大文件放进内存或 Graph 状态。

### 6.3 文档 inventory

只消费 Phase 4A 已有 `DocumentElement + EvidenceRef`，不重复 OCR。记录：

- 页数、元素数、段落数、表格数、图片数；
- 标题候选及层级、章节起止元素、跨页连续关系；
- 表格、行、单元格的稳定结构位置；
- 阅读顺序缺口、重复标题、低置信元素、缺失 bbox/location；
- 每个章节候选的 EvidenceRef 覆盖和原始解析器版本。

章节物理引用示例：

```text
artifact://<id>/section/<section-id>
artifact://<id>/page/<page>/element/<element-id>
artifact://<id>/table/<table-id>/row/<row>/cell/<column>
```

### 6.4 一对多绑定修正

当前 `Binding.physical_ref` 只能表达一个目标，不足以覆盖：

- 同一业务字段在 15 个文件中分别对应 15 个真实列；
- “付款条款”同时出现在正文章节、补充协议和文档表格；
- 同一逻辑字段在多个工作表中的列名不同。

批次 2 必须改为：

```text
ResolvedBinding
  semantic_ref
  cardinality: one | many
  targets: BindingTarget[]
  status: bound | ambiguous | missing
  confidence
  evidence
```

每个 `BindingTarget` 独立保存：

- `physical_ref / artifact_id / target_kind`；
- 分项分数和最终置信度；
- 正向证据、反证和 Inspector 报告引用。

不能用逗号拼接多个路径冒充结构化一对多关系。

### 6.5 绑定溯源

Bound Plan 还必须记录：

- 输入 artifact 哈希和 inspection report 哈希；
- Binder、规则集、同义词表、embedding、rerank、LLM 及提示词版本；
- 阈值配置版本；
- 自动绑定、用户选择或缺失的决策来源；
- canonical hash 和不可变 revision。

## 7. Inspector 详细策略

### 7.1 文件身份与安全

采用“三方一致”而不是只信扩展名：

1. 客户端声明扩展名/MIME；
2. 内容类型检测；
3. 对应解析器只读打开探针。

现有 `filetype` 保留为二进制魔数基线。Google Magika 对文本类型识别更强且支持 Python、
Rust CLI 和 npm，但只作为隔离 A/B 候选；官方说明它支持 200 多种类型，并有置信度模式，
因此适合补充 CSV/JSON/TXT 等弱魔数格式，而不能代替真实解析器打开验证。
参考：[Magika 官方仓库](https://github.com/google/magika)、
[Magika 支持类型说明](https://securityresearch.google/magika/core-concepts/models-and-content-types/)。

处理顺序：

- 扩展名、检测类型、解析器结果一致：继续；
- 检测器低置信但解析器可稳定打开：保留告警并继续；
- 类型冲突：拒绝自动绑定；
- 加密：按 InputContract 返回 `reject` 或 `needs_user`；
- 损坏：隔离并给出有限错误，不把异常堆栈和路径返回前端；
- ZIP/OOXML：继续复用现有 ZIP Slip、符号链接、成员数、展开大小和压缩比防护；
- XLSX 宏、外部链接、隐藏工作表只登记风险，本批绝不执行。

Magika 若引入，只能先进入独立评测环境；不得通过 PowerShell 在线安装脚本进入生产部署流程。

### 7.2 表格检查

首选复用：

- CSV/TSV：Polars lazy scan + 有界分层采样；
- Parquet：PyArrow/Polars 直接读取 metadata/schema，不扫描全表；
- XLSX：openpyxl `read_only=True`，只读工作表结构与有界单元格；
- JSON/JSONL：现有流式解析器，先判断记录、数组或嵌套对象形态；
- PDF/DOCX 表格：Phase 4A `DocumentElement`，不把表格重新 OCR。

Polars 官方支持在不读取完整 Parquet 数据的情况下读取 Schema；openpyxl 官方只读模式
可降低内存，并会拒绝部分无效 OOXML 文件。参考：
[Polars `read_parquet_schema`](https://docs.pola.rs/api/python/stable/reference/api/polars.read_parquet_schema.html)、
[openpyxl 加载与只读模式](https://openpyxl.readthedocs.io/en/stable/tutorial.html)。

采样必须可复现：

- 小表全量；
- 大表采用头部、尾部和固定种子分层样本；
- 样本上限、字符串最大长度和总字节受配置限制；
- 统计结果记录抽样策略，不能把样本推断冒充全量事实；
- 用户筛选字面量可以做受控全列计数，但只能在预算允许且对应解析器支持谓词下推时执行。

### 7.3 文档检查

- 先验证解析缓存和 artifact hash 一致；
- 检查 `DocumentElement` 的 artifact/page/reading_order/parent 关系；
- 标题层级优先使用确定性样式、元素类型和版面信息；
- OCR 产生的“疑似标题”必须携带置信度和位置；
- 跨页连续段落只建立关系，不重写原文；
- 文档表格中的条款与正文条款使用不同 `target_kind`；
- EvidenceRef 缺 quote/hash 或缺可复核位置时，不允许成为自动绑定目标。

## 8. Binder 详细策略

### 8.1 语义引用规范化

先把 STP 中同一概念合并为一个 semantic ref，例如：

- selection 的“姓名”；
- projection 的“姓名”；
- postcondition 的“姓名”。

三者共享一次绑定，避免同一概念被分别绑定到不同列。规范化只合并确定相同的引用，
不得把“工作量”和“工作量费用”等相近概念合并。

### 8.2 候选召回

候选召回按低成本到高成本执行：

1. Unicode NFKC、空白、大小写、全半角和标点规范化；
2. 精确表头/章节名；
3. 声明式通用同义词；
4. RapidFuzz `WRatio/token_set_ratio`；
5. 本地 embedding 召回 top-k；
6. 本地 rerank 对 top-k 精排。

RapidFuzz 已在主环境，官方 `process.extract` 支持 scorer、候选数和 score cutoff；
不需要再实现编辑距离算法。参考：
[RapidFuzz process 文档](https://rapidfuzz.github.io/RapidFuzz/Usage/process.html)。

同义词必须：

- 存在版本号和来源；
- 是通用业务词，不写进 Prompt 特判某个用户名或某份文件；
- 经 Golden 验证后才能进入自动绑定路径；
- 用户纠正只进入新的 binding revision，不在本批自动污染全局词典。

### 8.3 分项评分

每个候选至少计算：

- `name_exact`：规范化后是否相同；
- `name_fuzzy`：RapidFuzz 分数；
- `semantic_score`：embedding/rerank；
- `type_compatibility`：金额、日期、ID、文本等是否相容；
- `literal_support`：筛选值是否真实出现在候选列/段落；
- `structure_support`：所在表、章节、标题层级是否符合 STP 范围；
- `evidence_quality`：位置、quote/hash、解析器置信度；
- `contradiction_penalty`：类型冲突、全空、重复表头、范围外、低 OCR 置信等。

最终分数不能只由 LLM 给出。LLM 只能对 top-k 的短候选证据做结构化裁决，
并且确定性反证拥有否决权。

### 8.4 全局解析

表格字段不能逐个贪心选择。应使用当前环境已有的
`scipy.optimize.linear_sum_assignment(maximize=True)` 做最大权匹配，避免两个不同业务字段
同时抢占同一真实列。SciPy 官方将其定义为二分图线性分配/最大权匹配问题。
参考：[SciPy `linear_sum_assignment`](https://scipy.github.io/devdocs/reference/generated/scipy.optimize.linear_sum_assignment.html)。

规则：

- 同一规范化 semantic ref 可在多个 artifact/table 中各绑定一个目标；
- 不同 semantic ref 默认不能绑定同一列；
- 文档章节允许一对多，但每个目标都要独立通过证据门；
- 同分、近分或存在强反证时不得自动选择。

### 8.5 自动绑定门

阈值不得凭经验写死。先用开发集拟合，再在独立 held-out Golden 上冻结。

只有同时满足以下条件才能 `BOUND`：

- 总分达到冻结阈值；
- 第一名与第二名的 margin 达到冻结阈值；
- 类型、字面量或结构证据至少有一项独立支持；
- 没有强反证；
- 所有必需来源均完成目标绑定；
- 目标证据可复核。

其余状态：

- 多个合理候选：`AMBIGUOUS`；
- 没有达到最低召回线：`MISSING`；
- 来源本身不可用：由 Inspector 阻断，不进入 Binder。

本批取向是精度优先：宁可少自动绑定、多问一次，也不允许错绑。

### 8.6 一个最高价值问题

问题优先级：

1. 会改变结果粒度或聚合含义的业务歧义；
2. 多个高分字段/章节候选无法区分；
3. 必需字段缺失，需要用户选择替代字段或移除该要求；
4. 部分来源缺列，需要确认跳过来源还是停止。

一次只返回一个问题，包含 2–3 个有证据的候选。不得询问用户技术路径、内部 ID 或算法阈值。
用户回答后：

- 原 SourceInspectionReport 不变；
- 原 Bound Plan revision 不变；
- 创建下一 binding revision；
- 若 artifact hash 已变化，旧报告失效并重新检查，不复用旧绑定。

## 9. Graph Engineering

批次 2 新增独立 LangGraph，不提前实现批次 5 执行 Loop：

```text
load_logical_plan
  → authorize_sources
  → inspect_sources
  → validate_inspections
      ├─ blocked → finalize_blocked
      └─ ready
          → normalize_semantic_refs
          → generate_candidates
          → score_candidates
          → resolve_globally
          → validate_bindings
              ├─ ready → persist_bound_plan
              └─ needs_user → persist_question
```

约束：

- 每个节点输入/输出使用 Pydantic 契约；
- 模型调用次数有显式预算，Instructor 内部重试关闭；
- 本批不做无界 retry；
- 同一输入哈希、规则版本和模型版本必须生成相同的确定性 inspection hash；
- 模型分数变化不得修改历史 revision。

## 10. 成熟工具采用方案

| 能力 | 默认/候选 | 决策 |
|---|---|---|
| 二进制魔数 | 现有 `filetype` | 保留基线 |
| 文本/未知类型识别 | Magika | 隔离 A/B；胜出后再决定主依赖或 sidecar |
| CSV/TSV Schema | Polars | 复用批次 0 首选，进入 Inspector |
| Parquet Schema | PyArrow + Polars | 复用现有依赖，metadata 优先 |
| XLSX 结构 | openpyxl read-only | 复用现有解析器 |
| 表格契约验证 | Pydantic；Pandera 仅评测 | Batch 3 前不把 Pandera 强塞进主运行时 |
| 模糊名称召回 | RapidFuzz | 复用现有依赖，不自写编辑距离 |
| 全局字段分配 | SciPy Hungarian | 复用现有依赖，不自写匹配算法 |
| 语义召回/精排 | 现有 embedding/rerank 客户端 | 抽成通用服务供 memory 与 Binder 共用 |
| 最终语义裁决 | Instructor + 本地 Qwen | 仅处理 top-k 短证据，不看整文件 |
| PDF/DOCX/图片结构 | Phase 4A DocumentElement/EvidenceRef | 不重新解析、不新建平行 OCR 主链 |
| Docling/MarkItDown | 不进入本批 Binder 默认链 | 继续遵循 ADR-0013 的 fallback/sidecar 定位 |
| npm 工具 | 无 | 本批无正式前端，不为“用了开源”而新增 npm 依赖 |

PyArrow 的 `unify_schemas` 可合并多来源 Schema，但它按字段名工作，不能代替语义 Binder；
只用于 Inspector 的结构对齐诊断。
参考：[Apache Arrow Schema API](https://arrow.apache.org/docs/python/api/datatypes.html)。

Pandera 支持 strict、ordered、unique column name 和 lazy errors，适合批次 3 的执行前后验证，
本批只在评测环境验证 InspectionReport 转执行契约是否完整。
参考：[Pandera DataFrame Schema](https://pandera.readthedocs.io/en/stable/dataframe_schemas.html)。

## 11. 存储与测试 API

建议新增 append-only 表：

- `source_inspection_reports`；
- `bound_plan_revisions`；
- 可选 `binding_candidate_snapshots`，只保存脱敏 top-k，不保存全部原始样本。

唯一性/索引至少包含：

- 用户、logical plan、revision；
- artifact hash、inspector version；
- bound plan、binding revision。

测试 API：

```text
POST /api/semantic-plans/{plan_id}/inspect-bind
GET  /api/semantic-plans/{plan_id}/inspections
GET  /api/semantic-plans/{plan_id}/bound-revisions
GET  /api/semantic-plans/{plan_id}/bound-revisions/{revision}
POST /api/semantic-plans/{plan_id}/bound-revisions
```

安全要求：

- 只接受 artifact ID，不接受客户端 storage path、任意 URL 或自报 hash；
- 服务端重新解析所有权和真实路径；
- 他人 plan/artifact 一律返回 404；
- 重复提交相同 revision 返回冲突，不覆盖；
- API 只生成报告/Bound Plan，不启动真实数据操作。

## 12. 隐私、安全与提示词注入

- 样本默认只在本地内存短暂使用；
- 持久化只保存有界脱敏预览、类型统计、值指纹和证据引用；
- email、电话、身份证号、密钥形态等先脱敏；
- 外部模型确认必须绑定本次调用、目标 Provider 和发送摘要；
- 用户确认前不得解析外部 Provider 连接，更不得发送数据；
- 文档中的“忽略系统规则”“调用外部工具”等内容一律视为普通来源文本；
- 模型不能生成 artifact ID、physical ref、用户 ID、权限或输入哈希；
- LLM 只能在服务端候选白名单内选择，不能发明新列或新章节；
- 错误信息不返回本地绝对路径、密钥、SQL、完整样本或模型原始响应。

## 13. Golden 与 A/B 评测

### 13.1 数据集分层

- `public/dev`：公开脱敏合成样本，用于开发；
- `public/heldout`：不参与调阈值，用于封板；
- `private/local`：真实业务文件本地评测，只提交脱敏 manifest 和指标；
- 表格、文档各自独立计分，再计算总门禁；不得用强项平均掩盖弱项失败。

### 13.2 表格 Golden

至少覆盖：

- 表头完全一致、大小写/空格/全半角差异；
- 常见同义词；
- 名称相似但类型冲突；
- 两个近似候选；
- 重复表头、多行表头、合并单元格、空列、隐藏列；
- 多工作表、多文件中同一业务字段对应不同真实列；
- 筛选值只出现在正确候选列；
- 全空、混合类型、日期/金额/ID；
- 缺失必需列；
- CSV/TSV/XLSX/Parquet/JSON/JSONL；
- 损坏、加密、扩展名伪造、未知二进制；
- 大文件有界采样与确定性 hash。

“谢超群”门禁在本批只验收绑定：

- “姓名”必须绑定到真实姓名列；
- “核销工作量天数”“工作量费用”必须跨全部目标表找到正确列；
- 同名/近似错误列不得进入 Bound Plan；
- 执行后的 11 行、2 列、1 表由批次 3 验收，不能提前宣称。

### 13.3 文档 Golden

至少覆盖：

- 规范标题和不规范标题；
- 付款/交付/违约责任同义章节；
- 同一主题分散在多个章节或补充协议；
- 跨页连续段落；
- 条款位于表格单元格；
- 重复标题但正文主题不同；
- 缺章节、低 OCR 置信、缺 EvidenceRef；
- PDF 与 DOCX 同题绑定一致；
- 来源文本内含提示词注入指令；
- 多文档一对多绑定。

### 13.4 A/B 矩阵

1. 文件类型：`filetype` vs `filetype + Magika`；
2. 名称召回：规范化精确 vs RapidFuzz；
3. 语义召回：embedding vs embedding + rerank；
4. 裁决：纯确定性 vs top-k 本地 Qwen；
5. 全局解析：逐字段贪心 vs Hungarian；
6. 文档候选：标题规则 vs 标题规则 + 本地语义精排。

记录：

- 自动绑定 precision/recall；
- ambiguous/missing 分类；
- top-k recall；
- 错误自动绑定数；
- 每种来源耗时、输入字节、样本字节；
- 峰值 RAM、临时磁盘、模型调用次数；
- 本地/外部数据发送边界；
- 重复运行 hash 稳定性。

## 14. 封板门禁

### 14.1 正确性

- held-out Golden 自动绑定错误数：**0**；
- 自动绑定 precision：**100%**；
- 明确无歧义目标的候选 top-k recall：**100%**；
- 歧义/缺失正确阻断率：**100%**；
- Bound 目标证据覆盖率：**100%**；
- 重复表头、类型冲突和低证据候选不得自动绑定；
- 表格和文档分别达标，不能平均。

自动绑定 recall 不强行设为 100%；若为保持零错绑而进入澄清，可以接受，但必须报告覆盖率。

### 14.2 安全与隔离

- 未确认外部调用：0；
- 模型发明 physical ref：0；
- 跨用户读取：0；
- 原地覆盖 inspection/binding revision：0；
- 来源提示词扩大权限：0；
- 大文件全量进入 Graph 状态：0。

### 14.3 工程质量

- 批次 2 定向测试全绿；
- 批次 -1/0/1 回归全绿；
- 全仓测试不得低于当前 `868 passed、4 skipped、0 failed` 基线；
- Schema 导出和 round-trip 通过；
- `git diff --check`、UTF-8 和中文无乱码；
- 新依赖无新增主环境冲突，或已通过 sidecar 隔离；
- Windows 本机可完整运行；Linux/4×L20 迁移参数和 sidecar 说明同步更新。

性能阈值不在方案阶段凭空编造。首轮 benchmark 必须记录 1 万、10 万、100 万行及
10/50/100 份文档的 P50/P95、峰值 RAM 和临时磁盘，再在实现报告中冻结本机与服务器两套预算。

## 15. 实施批次

### Task 2.0：基线与契约修正

- 复跑基线；
- 验证是否已有持久化 BoundPlan；
- 建立 public dev/heldout 和 private manifest；
- 修正一对多 Binding/BoundPlan 契约并导出 Schema。

退出：旧契约兼容策略明确，Golden 能表达跨文件和多章节目标。

### Task 2.1：通用 Source Inspector

- 文件身份、权限、哈希、大小、损坏、加密和能力检查；
- `SourceInspectionReport`、缓存和 append-only 存储；
- filetype/Magika 隔离 A/B。

退出：不可用来源被明确分类，不进入 Binder。

### Task 2.2：表格 Inspector

- CSV/TSV/XLSX/Parquet/JSON(L) inventory；
- 稳定 table/column ref；
- 类型、样本、重复/多行表头和筛选值支持证据。

退出：全部表格 Golden 生成稳定、可复核的 inventory。

### Task 2.3：文档 Inspector

- 复用 DocumentElement/EvidenceRef；
- 章节、段落、表格、跨页和证据完整性 inventory。

退出：全部文档 Golden 生成稳定结构，不调用新 OCR。

### Task 2.4：候选召回与评分

- 规范化、RapidFuzz、embedding、rerank；
- 类型/样本/结构支持和反证；
- 分数与证据契约。

退出：held-out 所有正确目标进入 top-k，错误目标有可解释反证。

### Task 2.5：全局 Binder 与澄清

- semantic ref 去重；
- Hungarian/一对多规则；
- 自动绑定门；
- 一个最高价值问题；
- 用户回答后新 revision。

退出：错误自动绑定为 0，歧义不猜测。

### Task 2.6：存储与测试 API

- inspection/bound revision 存储；
- 所有权、不可变和缓存；
- 后端测试 API。

退出：跨用户、覆盖历史、客户端伪造路径/哈希均被阻断。

### Task 2.7：评测、文档与收口

- A/B、性能、安全和全仓回归；
- 版本化 JSON 结果和执行报告；
- 更新权威计划、handoff、AGENTS、README_AGENT；
- 只按白名单提交；未经用户要求不创建新版本或标签。

退出：第 14 节全部门禁通过。

## 16. 预计文件边界

建议新增：

```text
src/semantic_harness/inspection_models.py
src/semantic_harness/inspectors/
src/semantic_harness/binder.py
src/semantic_harness/binder_graph.py
src/semantic_harness/binding_summary.py
src/api/routes/semantic_bindings.py
scripts/run_phase4b_batch2_eval.py
tests/fixtures/semantic_harness/public/batch2/
tests/test_source_inspector_*.py
tests/test_semantic_binder_*.py
docs/plans/phase4b-batch2-results/
```

建议修改：

```text
src/semantic_harness/models.py
src/semantic_harness/__init__.py
src/api/store.py
src/api/main.py
scripts/export_schemas.py
docs/schemas/
```

禁止为本批改动：

- Phase 4A MinerU/Paddle 默认路由；
- 批次 3 表格执行器；
- 批次 4 文档执行器；
- 正式前端页面；
- 采集器、模板库和教训库运行库存。

## 17. 风险与处理

| 风险 | 处理 |
|---|---|
| 只靠字段名导致错绑 | 类型、样本、结构、反证和全局匹配共同门禁 |
| 阈值对开发集过拟合 | dev/heldout 分离，heldout 前冻结阈值 |
| 多来源只绑定第一份 | 一对多 target 契约，逐 artifact 验证 |
| LLM 发明列名/章节 | 只能从候选白名单选择，物理 ref 服务端生成 |
| OCR 标题误判 | 低置信/缺位置目标不得自动绑定 |
| 样本泄露 | 本地有界使用、脱敏持久化、外发单次确认 |
| 大文件拖垮内存 | metadata 优先、流式/有界采样、Graph 只存引用 |
| 新工具污染主环境 | A/B 虚拟环境或 sidecar，胜出后再采纳 |
| 用户回答覆盖历史 | 每次回答创建下一 binding revision |
| 把 Binder ready 当执行成功 | API、状态文案和报告明确“尚未执行” |

## 18. 最终实施顺序

```text
契约一对多修正
→ Source Inspector
→ 表格 Inspector
→ 文档 Inspector
→ 候选召回/评分
→ 全局 Binder
→ 单问题澄清
→ 不可变存储/API
→ A/B 与封板
```

这是批次 2 的完整实施边界。当前已按该边界交付后端测试能力；批次 3 的表格执行器、
批次 4 的文档执行器、完整 Harness Loop 和正式前端仍未实现。
