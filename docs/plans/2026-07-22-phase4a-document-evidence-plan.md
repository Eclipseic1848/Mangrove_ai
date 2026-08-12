# Phase 4A：文档智能与证据约束抽取实施计划

> 文档状态：已封板并在 v0.0.5 收口（批次 0–5 已完成；Paddle 同集 A/B、
> 图片链、不可变版本和自动表格 Recipe 已补齐）
> 前置条件：Phase 3 已完成；当前分支以 `handoff.md` 的验证证据为准
> 核心原则：优先集成成熟开源组件；先 PoC、再锁版；不为合同/发票/招投标复制行业专用主链

## 1. 目标

把 PDF、DOCX 和图片解析为统一的文档结构，在用户确认目标字段后进行证据约束抽取。每个非空字段必须携带来源制品、页码/段落/表格位置、证据片段或哈希、抽取器版本和置信度；未找到、冲突、低置信度必须显式表达，禁止模型猜测补值。

首个纵向验收使用合同交付条款，随后用招投标条件或发票字段复测同一套能力，证明实现没有硬编码合同主链。

## 2. 开源工具选型

| 能力 | 首选工具 | 使用方式 | 不自行实现的部分 |
|---|---|---|---|
| 多格式结构解析 | Docling | 先做隔离 PoC；通过后封装 `DocumentParserAdapter`，消费其统一文档结构 | PDF/DOCX/图片读取顺序、版面、表格结构和格式转换 |
| PDF 页渲染与坐标 | pypdfium2 + pdfplumber | 默认确定性基础层；输出 page image、text blocks、bbox | PDF 渲染、文本块坐标和图片提取 |
| 当前扫描 PDF 服务 | MinerU 3.4.4（pipeline） | 已接入 HTTP 服务；保留原始响应、坐标、置信度与版本，重复运行命中缓存 | 成熟文档解析/OCR 服务，避免在 Mangrove 内重造版面与 OCR 引擎 |
| 中文 OCR/版面/表格 | PaddleOCR PP-StructureV3 | 作为可插拔后端与黄金集基准；仅效果/环境收益明确时进入默认依赖 | 文字检测识别、版面分类、表格结构识别 |
| 复杂扫描页语义 | 现有 Qwen3.6-35B-A3B VL | 复用 `src/services/qwen_video/` 的 OpenAI 兼容客户端、配置回退和局域网直连 | 通用视觉模型调用与重试基础设施 |
| 文本意图/字段候选 | Instructor + 统一多模型 Provider | local、DeepSeek、百炼按任务显式选择；任务选择 → 用户默认 → 全局/.env | 结构化输出重试、供应商密钥/端点解析和用户隔离 |
| 标注与人工复核 | Label Studio | 先用于黄金集标注；产品期通过 REST/Webhook 适配任务与复核结果 | 标注工作台、用户协作、任务分配和导出 |
| 契约与校验 | Pydantic v2 + JSON Schema | 延续现有模型与 schema 导出方式 | 数据校验、序列化和版本化基础设施 |
| 测试与评测 | pytest + Hypothesis + jiwer | 单测/属性测试；OCR CER/WER；字段级 precision/recall/F1 | 随机边界生成和 OCR 指标实现 |
| 业务查看副本 | openpyxl | 从权威字段/记录 JSONL、表格 JSON、Evidence/Quality/Manifest 生成动态 XLSX | 工作簿、多工作表、筛选、冻结窗格、样式和公式安全 |
| 测试隔离诊断 | pytest-randomly + pytest-xdist | 随机顺序暴露全局状态泄漏；多进程验证测试独立性并缩短回归时间 | 测试顺序随机化和分布式执行器 |

选型约束：

1. Docling、PaddleOCR 不直接同时进入生产 requirements；PoC 以独立 extra/环境运行，依据精度、速度、内存、许可证和 Windows/Linux 可部署性决定最小组合。
2. `pypdfium2` 负责宽松许可的确定性页渲染，`pdfplumber` 负责当前数字文本 bbox；PyMuPDF 因 AGPL/商业双许可不进入默认链。
3. Qwen VL 只消费给定页面/区域并返回候选，不拥有跨文档搜索范围，也不能覆盖确定性证据位置。
4. Label Studio 是外部可选服务；Mangrove 只维护复核任务契约和适配器，不重造完整标注平台。
5. 本地模型超时不得静默切到云 API；选择云模型时会发送解析后的文档文本，必须由用户在工作区或设置页明确选择。原始图片像素只交给视觉模型。

## 3. 统一数据契约

新增并导出 JSON Schema：

- `TaskGoal`：用户目标、对象、范围和成功标准。
- `DiscoverySpec`：允许搜索的制品、章节和页范围。
- `ExtractionSpec`：目标字段、类型、是否必填、证据规则和冲突策略。
- `ResultContract`：结果形态、基数、每行含义、渲染器、输出格式、是否全量，以及用户是否明确要求多表无损合并。
- `DocumentElement`：document/page/section/paragraph/table/cell/image，含稳定 ID、bbox 和 reading order。
- `EvidenceRef`：artifact_id、element_id、page、bbox、quote/hash、extractor/version、confidence。
- `ExtractedField`：value、status(found/not_found/conflict/low_confidence)、evidence_refs。
- `ExtractedRecord` / `ExtractedTable`：多行记录及确定性原表；分别保留字段证据和原始表/行/列账本。
- `ReviewPolicy` / `ReviewTask`：阈值、触发原因、候选、人工裁决和审计信息。

兼容要求：保持现有 data_prep v2 输入/输出可读取；新字段使用新 `spec_version`，禁止原地改变旧 JSON 含义。

## 4. 实施批次

### 批次 -1：仓库测试基线治理（Phase 4A 开工门禁，已完成）

治理前全仓为 734 passed、15 failed、4 skipped；治理后两轮全量串行和一次两进程并行均为 749 passed、4 skipped、0 failed。4 个默认跳过标记已单独显式运行为 5 passed，没有新增 skip/xfail 或删除测试。

1. 用 pytest fixture 隔离 `.env`/runtime settings、模板/教训目录、采集器 registry 和外部搜索后端；测试不得调用真实 AnySearch。
2. 更新已被生产流程演进取代的断言（如 `target_resolve` 路由），但不为让测试变绿而回退正确生产行为。
3. FakeStore 实现当前最小接口，或改用真实临时 WebUIStore；禁止生产代码为测试桩降级。
4. 引入 pytest-randomly，固定失败 seed；在隔离完成后用 pytest-xdist 验证多进程可运行性。性能和 Docker 真库测试继续单独串行。
5. 去重扫描器线程卸载改动经用户授权保留，并补齐事件循环不阻塞回归测试。

退出门禁（已满足）：常规全仓测试 0 failed；相关回归 5 个随机 seed 均为 99 passed；无外网、真实 Cookie 或本地学习库存依赖；Phase 3 定向/性能/真库/E2E 保持全绿。下一执行项为批次 0。

### 批次 0：固定黄金集与工具 PoC

首轮状态：已完成 24 份/120 页合成黄金集、确定性基线和 3 份 Qwen 扫描探针。Docling 2.114.0 与 PaddleOCR 3.7.0 均曾在隔离环境安装成功，但默认模型初始化/下载在 10 分钟内无首份结果。2026-07-23 后续使用 17 份/85 页扫描与混合 PDF 完成 MinerU/Paddle 同集评测：MinerU pipeline 字段 51/51、表格行 260/272、125.416 秒；Paddle 18081 字段 47/51、表格行 272/272、359.675 秒。默认链锁定 MinerU，Paddle 用于表格增强及失败/缺页回退。2026-07-26 复测显示 MinerU `hybrid-http-client + medium` 已可调用但表格行召回为 0，high 返回空结果；本地 `hybrid-engine` 仍返回 `Device string must not be empty`。详见 ADR-0011、开发前评审和 `phase4a-parser-ab-results.json`。

1. 提交脱敏固定样例：数字 PDF、扫描 PDF、混合 PDF、DOCX、图片、跨页表格、损坏页；同时提交 `expected.json` 和证据位置。
2. 使用同一黄金集比较 Docling、pypdfium2/pdfplumber、PaddleOCR 与 Qwen VL：文本覆盖、阅读顺序、表格结构、bbox、中文 OCR CER/WER、耗时和峰值内存。
3. 记录许可证、模型体积、离线部署、Windows/Python 3.13 与 Linux 兼容性。
4. 形成 ADR，锁定默认解析链与可选降级链后再进入生产编码。

退出门禁：至少 20 份文档/100 页；结果可复现；选型 ADR 获批；没有为了赶进度自行实现 OCR、表格识别或标注平台。

### 批次 1：契约和文档中间表示

执行状态：`TaskGoal`、`DiscoverySpec`、`ExtractionSpec`、`ResultContract`、`DocumentElement`、
`EvidenceRef`、`ExtractedField`、`ExtractedRecord`、`ExtractedTable`、`ExtractedDocument`、
`ExtractedAggregate`、`ReviewPolicy`、`ReviewTask` 及 JSON Schema 已落地。`document` 按制品和阅读
顺序生成连续正文并保留逐元素证据，`aggregate` 保留字段状态、来源制品与复核状态；两者均有独立
权威 JSON、XLSX 工作表和前端视图。Instructor 复用统一多模型 Provider，把用户意图转换为可编辑
ExtractionSpec，用户确认后才执行真实元素约束抽取。伪造 element_id、越界文件、无原文支持或缺乏
可验证位置的候选不能进入 `found`。

1. 实现上述 Pydantic 契约、JSON Schema 和版本迁移测试。
2. 建立 `DocumentElement` 稳定 ID 与 ArtifactStore 路径约定。
3. 属性测试覆盖 bbox、页码、父子关系、跨文档隔离和序列化往返。

退出门禁：Schema 与模型一致；同输入产生稳定 element ID；跨任务/跨文档引用被拒绝。

### 批次 2：解析器适配与 OCR 路由

基础层状态：数字/扫描/混合页路由、稳定元素 ID、数字文本 bbox、`pypdfium2` 页渲染和统一多模型文档候选客户端已落地。DOCX 复用 `python-docx` 完成结构位置证据闭环；图片通过 Paddle 完整 Pipeline 解析为带 bbox 的统一元素，并由按需加载的 OpenSeadragon 预览像素/归一化证据框。MinerU pipeline 主解析、Paddle 表格增强与失败/缺页回退已经同集 A/B 验证。Docling 不进入当前默认依赖；MinerU Hyper medium 仅作为实验候选，high 空结果和本地 device 问题修复后需重新评测。

1. 封装 Docling/pypdfium2/坐标 OCR 适配器，不让第三方对象进入 LangGraph state；当前 MinerU 与 PaddleOCR-VL 均通过统一轻量 DTO 接入，Paddle 未配置时不实例化，Docling 仍为本机候选。
2. 数字文本优先确定性提取；文本覆盖不足才进入 OCR。
3. OCR 后端通过统一接口接 MinerU/PaddleOCR；Qwen VL 只作候选和复核。MinerU 的端点、后端、
   超时和缓存已可配置；整文主备解析结束后只对仍缺失的页执行有界重试，远程解析调用受可配置的
   进程级并发信号量保护，避免为正常页重复调用或无上限压垮服务。
4. 原始页图和第三方原始结果作为不可变制品保存，解析结果只存引用。

退出门禁：坏页进入 rejects；失败不拖垮整批；重复执行命中缓存；state 不承载大页面/图片字节。

当前验证（v0.0.5 收口）：全仓后端 **834 passed、4 skipped、0 failed**（169.21 秒）；
显式性能门 **3 passed**（51.80 秒）；前端生产构建通过；完整 Playwright **21 passed**
（19.9 秒）。新增回归覆盖 `document/aggregate` 独立执行与交付、缺页重试、跨线程并发上限、
安全 ZIP 子文档解析和 ZIP 工作台上传。local / `Qwen3.6-35B-A3B` 的 `phase4a-local-gate`
仍以 17 份 PDF / 85 页 / 51 字段的字段精确值和证据绑定双 100% 为解析链门禁。

### 批次 3：证据约束抽取

执行状态：已完成。候选只消费当前任务允许的 `DocumentElement`，所有 element_id、原文 quote、页码/bbox 和 artifact_id 均由确定性代码反向验证；多元素短语支持联合证据校验。数字合同+招投标 7 份/21 字段真实基线达到 21/21 值精确命中、21/21 证据完整、0 跨文档引用、0 无证据 found。

1. 用户自然语言目标先转换为可编辑 `ExtractionSpec`，高风险字段必须确认。
2. 候选检索只在当前任务和允许文档元素内执行。
3. Qwen 输出经 Pydantic 校验；每个非空值必须引用真实 `EvidenceRef`，并由确定性代码校验证据片段/位置。
4. 无证据、跨文档串值、冲突和低置信度不进入 found 状态。
5. `ResultContract` 在执行前声明结果形态、基数、每行含义、渲染器、是否全量和 `merge_tables`；明显的“所有/全部/每个/原表”意图有确定性兜底，只有明确的“合并成/为一张表”才启用无损多表合并。
6. records 遍历全部元素块并按 `record_id` 聚合，不把同字段多行误判为冲突；tables 直接保留解析器给出的全部表、行、列，不调用 LLM 选择行。数字 PDF 使用 pdfplumber 表格结构，扫描/混合页使用 MinerU HTML 表格；合并结果保留来源表和来源页。

退出门禁：黄金集“无证据非空值”为 0；跨文档串值为 0；证据引用完整率 100%。

### 批次 4：质量、复核与输出

执行状态：低置信度/冲突 ReviewTask、三种人工裁决、字段/记录写回、用户/时间/前后值 JSONL
审计和重复裁决保护已完成；全量任务在所有元素块处理完后一次性展示待复核项。确定性质量门与
权威字段/记录 JSONL、表格 JSON、连续文档 JSON、证据汇总 JSON、Evidence、Lineage、Quality、
Manifest 已完成并可下载。XLSX 按形态增加 Records、逐原表、Documents 或 Aggregate 工作表。
Label Studio 官方 SDK 适配器保留为可选外部接口，产品内 ReviewTask 闭环是本阶段验收路径。

1. 增加文档覆盖、字段完整、证据覆盖、冲突率、低置信度率和跨文档隔离质量门。
2. 通过 Label Studio 适配器导出/回收复核任务；服务不可用时保留标准 ReviewTask JSON，不阻断安全输出。
3. 输出 Dataset + Evidence + Rejects + Lineage + Quality + Manifest；XLSX 只作为查看副本。

退出门禁：人工裁决有用户、时间和前后值审计；复核结果可重放；权威 JSONL/Parquet 与 XLSX 数量账本一致。

### 批次 5：产品闭环与跨场景验收

执行状态：`/data-prep` 三栏桌面工作台已覆盖多文件上传、用户命名文件集、PDF/DOCX/图片预览、字段编辑/确认、动态记录/多表结果、批量人工复核和产物下载。文件集成员范围变化会先提示并创建下一不可变 revision，旧任务和产物继续可查。显式合并表在无损原表之后自动执行确定性 Recipe：仅移除跨来源完全相同的重复表头，合计行保留并标记，同时保存 `extracted_tables_raw.json` 和 `table_recipe_audit.json` 供审计恢复。本地模型为默认；切换云 OpenAPI 前弹窗确认文档文本外发风险。

1. `/data-prep` 增加目标字段确认、文档/页范围、证据预览、低置信度复核和产物下载。
2. Playwright 覆盖上传→解析→字段确认→执行→证据定位→复核→下载。
3. 先完成合同交付条款，再不改主链迁移到招投标或发票。

退出门禁：两个领域共用同一契约与节点；新增场景只增加 Schema/配置/样例，不新增行业专用主图。

## 5. 最终验收指标

- 所有非空字段 EvidenceRef 覆盖率：100%。
- 跨任务/跨文档串值：0。
- 无证据填值：0。
- 黄金集数字合同+招投标原基线字段 F1/精确值/证据完整率均为 1.0（21/21）；当前 `phase4a-local-gate` 对 17 份扫描/混合合同、招投标和全部发票的 51 个字段要求精确值与证据绑定均为 1.0，实测通过。该结果不能替代更大真实语料、全页 OCR CER/WER 或 Paddle 同集 A/B。
- 坏页/损坏文档隔离率：100%，数量账本守恒。
- 同输入、同模型/解析器版本、同配置重复执行结果可追溯；非确定模型差异必须记录版本与原始响应引用。
- 前端构建、Playwright E2E、定向测试和全仓回归均记录真实结果；不得只报告提交成功。

封板验证（2026-07-23）：

- 全仓后端：827 passed、4 skipped、0 failed，214.71 秒。
- 显式性能门：3 passed，44.20 秒；包含 100 万行分批门禁，不冒充实际 500 MB 文件。
- 前端生产构建：通过；OpenSeadragon 独立按需 chunk 为 342.77 kB。
- 完整 Playwright：19 passed，24.9 秒。
- Paddle 图片实机：扫描发票页 PNG 返回 6 个带坐标内容块。

## 6. 明确不做

- 不绕过密码、MFA、CAPTCHA 或文档权限。
- 不让模型在没有证据时补全字段。
- 不在 Phase 4A 同时建设认证浏览器、音视频全链、分布式队列或行业专用工作流。
- 不复制 Docling/pypdfium2/PaddleOCR/Label Studio 已成熟提供的解析、OCR、表格、标注和协作基础能力。
