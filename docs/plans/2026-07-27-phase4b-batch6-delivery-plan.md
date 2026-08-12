# Phase 4B 批次 6：输出、转换和下载闭环详细实施方案

> 状态：核心后端交付闭环已实施；原方案中的 sidecar、Linux 容器集成和视觉 Golden
> 未在本批落地，明确转入批次 8 扩展评测/封板门
>
> 日期：2026-07-27
>
> 当前分支：`v0.0.6`，未封板、无同名标签
>
> 前置批次：Phase 4B 批次 -1 至 5 已完成
>
> 本批边界：后端交付闭环；正式前端入口、运行轨迹和预览界面留到批次 7
>
> 发布边界：代码、测试和文档已完成当前范围验收；发布前必须保留本文记录的实施偏差，
> 不得把延期项写成已完成

## 0. 实施收口说明

用户批准方案后，实际实现选择了当前主环境中已经成熟、可在 Windows/Python 3.13 直接验证
的 PyArrow、XlsxWriter、python-docx、ReportLab、python-pptx 等组件，完成了 11 种格式、
逐文件重开 QA、Manifest、原子发布、用户隔离下载、防篡改和幂等恢复。

原方案同时设计了更大的扩展范围。以下内容**没有在本批实现，不能作为批次 6 已有能力引用**：

- Gotenberg、Pandoc、WeasyPrint、PptxGenJS、LibreOffice 生产 sidecar；
- Linux 容器内同 fixture 集成门；
- DOCX/PDF/PPTX 截图级视觉 Golden；
- 独立 `DeliveryRun` / `DeliveryQuestion` / `DeliveryDecision` Schema；
- Evidence/Lineage/Quality 独立下载侧车；
- 独立 A/B 脚本、benchmark JSON、字体镜像和镜像 digest；
- 大规模性能、真实数据库容器和目标服务器压力门。

这些项目不会被静默视为完成，统一保留到批次 8 扩展评测和封板审计。当前批次 6 的准确
完成证据以
[批次 6 执行报告](2026-07-27-phase4b-batch6-execution-report.md)为准。

## 1. 本批目标

把批次 5 的：

```text
VerificationReport.status=pass
+ eligible_for_delivery=true
```

升级为真实、可重新打开、可验证、可追溯且只有 owner 能下载的正式交付：

```text
已验证内部结果
→ Delivery Plan
→ Renderer / Converter
→ 暂存输出
→ 结构重开 / 渲染 QA
→ Evidence / Lineage / Quality
→ Manifest
→ 原子发布
→ owner 下载
```

本批必须覆盖以下 11 种正式输出格式：

- 结构化数据：XLSX、CSV、JSON、JSONL、Parquet；
- 文档：DOCX、PDF、HTML、Markdown、TXT；
- 演示：PPTX。

正式输出和证据侧车必须区分：

- 内部执行制品：批次 3 的结果 Parquet、批次 4 的 Document AST，不直接冒充用户交付；
- 权威结构化输出：能保留完整记录和类型语义的 JSON、JSONL、Parquet；
- 业务查看副本：CSV、XLSX；
- 正式文档输出：DOCX、PDF、HTML、Markdown、TXT、PPTX；
- 强制侧车：Evidence JSONL、Lineage JSONL、Quality JSON、Manifest JSON；
- 诊断产物：失败报告、渲染截图和转换日志，不进入正式 `outputs`。

## 2. 已确认产品决策

1. 本轮先提交本方案供确认，不修改生产代码。
2. 11 种格式全部纳入批次 6，按“核心格式 → 大数据/轻量格式 → PPTX”分步实现，但退出时
   必须全部通过。
3. 允许引入新的 Python/npm 依赖、系统工具和 Docker sidecar。
4. 成熟开源工具优先，不按许可证宽松程度筛选；效果、稳定性和可验证性优先。
5. Gotenberg、LibreOffice、Pandoc、WeasyPrint、PptxGenJS 等候选必须用相同真实/脱敏
   样本 A/B，未达到硬门不得因知名度进入默认链。
6. 同时兼顾 Windows 开发机和 Linux/Docker 部署：核心契约和主代码相同，重型转换器使用
   可移植 sidecar；批次 8 再做目标服务器完整压力与封板审计。
7. 批次 6 只做后端契约、生成、QA、Manifest 和安全下载 API；正式前端留到批次 7。
8. Phase 4A 正式流程不在本批被替换；本批扩展 Phase 4B 灰度 Harness。
9. 输出格式属于用户语义。所有同格式候选都失败后必须暂停询问替代格式，禁止静默替换、
   静默缺少某个请求格式或只发布部分成功格式。
10. 只有全部请求格式通过 QA 才能原子发布；失败或等待用户时，已生成文件只能处于暂存状态。
11. 本批文档和未来代码先保留在当前工作区；完成全部开发和测试后才按白名单提交并推送
    `platform/v0.0.6`。

## 3. 当前事实与需要补齐的断点

### 3.1 可直接复用

- `SemanticTaskPlan.delivery.formats` 已覆盖 11 种目标格式；
- 批次 5 已冻结逻辑计划、绑定、能力版本和循环策略；
- 表格执行已生成内部结果 Parquet 和逐记录 lineage Parquet；
- 文档执行已生成带逐元素 EvidenceRef 的 `document-result.json` / Document AST；
- `VerificationReport.authoritative_output_allowed` 已固定由确定性验证状态决定；
- `ArtifactStore` 已有任务目录、相对路径、防穿越、SHA-256 和 JSON/JSONL 写入能力；
- Phase 2/4A 已有 JSON(L)、CSV、Parquet、XLSX、Evidence、Lineage、Quality 和 Manifest
  的可复用实现；
- 现有下载路由已有 owner 检查、404 隐藏和路径穿越防护经验；
- 当前主环境已有 openpyxl、XlsxWriter、PyArrow、python-docx、docxtpl、ReportLab、
  pypdf、pdfplumber、pypdfium2、python-pptx、Jinja2、Markdown、markdown-it-py。

### 3.2 当前真实缺口

1. 批次 5 的 `HarnessAdapterOutcome` 没有把内部结果路径持久化到 Harness attempt。
   `ToolResult.ArtifactRef` 故意不暴露路径，批次 6 不能靠文件名或目录约定反推。
2. `eligible_for_delivery` 只有布尔值，没有 DeliveryRun、输出 revision、QA 状态和
   正式 Manifest 引用。
3. 现有 `DatasetManifest` 不能完整表达：
   - 输出角色；
   - MIME、大小、生成器和转换链；
   - QA 报告；
   - 计划/绑定/运行/尝试身份；
   - 可下载状态、释放和过期；
   - 同格式候选失败记录。
4. 现有 `/api/downloads/{task_id}/{path}` 依赖旧任务归属和磁盘相对路径，不适合直接承载
   Phase 4B Harness；Phase 4B 必须按不透明 `output_id` 下载。
5. 现有输出器主要验证“写出”，没有形成统一的格式重开、渲染、内容对账和原子发布门。
6. LibreOffice、Gotenberg、WeasyPrint 和 PptxGenJS 尚未进入本项目生产依赖；
   Pandoc 主机 CLI 不存在，但本机已有 `pandoc/core:3.10.0.0-alpine` 镜像。
7. 当前 Gotenberg 镜像尚未安装，主机也没有 LibreOffice/Pandoc 命令；不能把候选能力写成
   已验证能力。

### 3.3 2026-07-27 候选工具探针

- Docker Client/Server：`29.6.1 / 29.6.1`；
- Node：`22.22.1`；
- npm：`10.9.4`；
- PptxGenJS npm 当前稳定版：`4.0.1`，项目尚未安装；
- Gotenberg GitHub 当前稳定版：`v8.34.0`，本机尚无镜像；
- WeasyPrint GitHub 当前稳定版：`v69.0`，主环境尚未安装；
- XlsxWriter：`3.2.9`，已安装；
- docxtpl：`0.20.2`，已安装；
- ReportLab：`5.0.0`，已安装；
- LibreOffice 主机命令：不存在；
- Pandoc 主机命令：不存在；
- Pandoc Docker 镜像：`pandoc/core:3.10.0.0-alpine` 已存在。

这些只是方案时点快照。实施前仍须重新探测，并在 A/B 报告中记录真实版本和镜像 digest。

## 4. 目标架构

### 4.1 状态流

```text
Harness verify
  ├─ 非 pass → 禁止进入交付
  └─ pass → eligible_for_delivery=true
                 ↓
          ensure DeliveryRun
                 ↓
          冻结 Delivery Plan
                 ↓
       按请求格式逐项选择候选
          ├─ Renderer
          └─ Converter
                 ↓
          staging/<delivery_id>
                 ↓
          每格式独立 Artifact QA
          ├─ pass → staged output
          ├─ 同格式备选存在 → 尝试下一候选
          └─ 同格式候选耗尽 → needs_user
                 ↓
        全部请求格式均为 qa_pass
                 ↓
     写 Evidence / Lineage / Quality / Manifest
                 ↓
        原子状态切换为 released
                 ↓
        仅 owner 可按 output_id 下载
```

### 4.2 不把大对象放进 LangGraph

LangGraph state 只增加：

- `delivery_id`；
- `delivery_status`；
- `delivery_question` 或其引用；
- `manifest_output_id`；
- 请求格式和已完成格式摘要。

Arrow Table、Document AST、渲染图片、DOCX/PPTX/PDF 字节、sidecar 原始响应都保存在
服务端制品目录，不进入 checkpoint。

### 4.3 交付目录

统一使用可配置 `semantic_delivery_root`，数据库只保存相对于该根的 `storage_key`：

```text
data/semantic-deliveries/
  <safe_user_id>/
    <run_id>/
      <delivery_id>/
        staging/
        outputs/
        sidecars/
          evidence.jsonl
          lineage.jsonl
          quality.json
        qa/
          <output_id>.json
          renders/
        diagnostics/
        manifest.json
```

约束：

- 不保存客户端传入的绝对路径；
- 输出扩展名由服务端格式注册表决定；
- staging 与正式 outputs 同一文件系统，QA 全通过后使用原子 rename/move 发布；
- sidecar 输入只读，renderer 只能写当前 delivery 的 staging；
- 数据库保存 owner、storage_key、哈希和状态，不保存大文件正文。

## 5. 强类型契约

新增契约并导出 Draft 2020-12 JSON Schema。

### 5.1 DeliveryRun

至少包含：

- `delivery_id/run_id/user_id/revision`；
- `logical_plan_id/revision/hash`；
- `binding_revision/hash`；
- `source_attempt_id`；
- `source_artifact_ids/source_hashes`；
- 冻结的 `requested_formats` 和 `output_name`；
- `idempotency_key`；
- 状态：
  `pending/rendering/qa/needs_user/succeeded/failed/expired`；
- `available_at/expires_at/completed_at`；
- `manifest_output_id`；
- 当前问题和明确失败原因。

### 5.2 DeliveryCapabilityManifest

Renderer/Converter 使用同一登记契约：

- `capability_id/version/kind`；
- `accepts/produces`；
- 支持的平台和运行方式；
- 是否确定性、是否保留 Evidence/Lineage；
- 网络、副作用和资源等级；
- 输入大小、行数、页数、超时和并发上限；
- 健康检查、工具版本探针；
- 参数 JSON Schema；
- 优先级只由服务端配置和 A/B 结论决定。

LLM 和客户端都不能提交命令、路径、镜像名、Provider URL 或任意转换参数。

### 5.3 DeliveryOutput

- `output_id/delivery_id`；
- `format/role`；
- `filename/mime_type`；
- `storage_key/sha256/size_bytes`；
- `record_count/page_count/slide_count`；
- `renderer_id/version`；
- `source_artifact_ids`；
- `qa_report_id/qa_status`；
- 状态：`staged/qa_passed/released/qa_failed/expired`；
- 创建、释放和过期时间。

`role` 固定为：

- `authoritative_data`；
- `view_copy`；
- `document_output`；
- `evidence_sidecar`；
- `lineage_sidecar`；
- `quality_sidecar`；
- `manifest`；
- `diagnostic`。

### 5.4 ArtifactQAReport

- 输出 ID、格式和输出哈希；
- 重开工具及版本；
- 结构、内容、证据、视觉和安全检查；
- 期望值、实际值和错误码；
- 渲染页/幻灯片引用；
- `pass/fail`；
- 失败指纹和是否允许尝试同格式备选；
- 生成时间。

QA 只能读取已落盘文件和冻结期望，不接受 Renderer 的“成功”文本作为通过依据。

### 5.5 DeliveryManifest

Manifest 至少记录：

- STP、Bound Plan、Harness run、成功 attempt 和 VerificationReport 身份；
- 请求格式与实际同格式生成器；
- 每个正式输出的格式、角色、MIME、大小、SHA-256、记录/页/幻灯片数；
- Evidence、Lineage、Quality 和 QA 引用；
- Renderer/Converter 版本、镜像 digest、模板版本和字体包版本；
- 输入/输出哈希和完整转换链；
- 内容政策以及是否为派生内容；
- 发布时间、过期时间和保留策略；
- 所有失败格式候选的脱敏摘要；
- 明确的 `all_requested_formats_satisfied=true`。

质量失败或等待用户的 Manifest 可以存在，但其 `outputs` 必须为空，且不能取得正式下载状态。

### 5.6 DeliveryQuestion / DeliveryDecision

同格式候选全部失败时：

- 一次只问一个最高价值问题；
- 明确失败格式、原因和已尝试工具；
- 提供 2–3 个已登记替代格式；
- 带 `delivery_id/question_id/resume_token`；
- 用户回答后生成 append-only `DeliveryDecision`；
- 不覆盖原 STP，不伪造原格式成功；
- 新的交付 revision 记录用户批准的替代格式。

## 6. 数据库与批次 5 接缝

### 6.1 持久化内部执行路径

对 `semantic_harness_attempts` 增加仅供服务端读取的 `artifact_paths_json`，并在
`HarnessAdapterOutcome` 增加内部 `artifact_paths`：

- 表格：结果 Parquet、lineage Parquet；
- 文档：`document-result.json`；
- 保存前逐个核对路径位于 `semantic_execution_root`、哈希与 `ArtifactRef` 一致；
- 普通 attempts API 不返回该字段；
- DeliveryService 通过 owner + run + 成功 attempt 的内部查询读取；
- 不为历史开发任务推断或迁移路径，批次 6 新 run 使用新字段。

### 6.2 新增交付表

最小新增：

1. `semantic_delivery_runs`
   - 一次交付一行；
   - `idempotency_key` 唯一；
   - 保存冻结输入、状态、问题、Manifest 引用和生命周期。
2. `semantic_delivery_attempts`
   - 每格式、每 Renderer/Converter 尝试一行；
   - append-only；
   - 保存失败分类、输入/输出哈希、工具版本、QA 摘要和耗时；
   - 不保存业务正文。
3. `semantic_delivery_outputs`
   - 每个暂存/正式文件一行；
   - owner、storage_key、哈希、大小、MIME、角色、QA 和状态；
   - API 只返回无路径公共视图。

Harness run 增加 `delivery_id/delivery_status` 公共摘要。`eligible_for_delivery` 继续表示
执行验证已通过，不能代替 `delivery_status=succeeded`。

## 7. Renderer / Converter 注册表

### 7.1 预设候选链

| 输出 | 预设首选候选 | 同格式备选 | 独立 QA |
|---|---|---|---|
| JSON | Python 流式 JSON Writer | PyArrow/Python 分批 Writer | 标准 JSON 重读 + Schema/账本 |
| JSONL | Python 流式 JSONL Writer | PyArrow 分批转换 | 逐行 JSON 重读 + Schema/账本 |
| CSV | Python `csv` 流式 Writer | PyArrow CSV Writer | 独立 CSV reader + 行列/内容账本 |
| Parquet | PyArrow ParquetWriter | DuckDB COPY（仅 A/B 达标后） | PyArrow 重读 + Schema/统计 |
| XLSX | XlsxWriter `constant_memory` | openpyxl write-only | openpyxl read-only 重开 + LibreOffice/Gotenberg |
| DOCX | docxtpl + python-docx | Pandoc 白名单转换 | OOXML 解包 + python-docx + Gotenberg 渲染 |
| HTML | Jinja2 受控模板 | Pandoc 白名单转换 | HTML parser + 离线资源/内容检查 |
| Markdown | 确定性 Markdown Renderer | Pandoc | markdown-it 重读 + 结构检查 |
| TXT | UTF-8 确定性 Renderer | 无；失败即询问 | UTF-8 严格解码 + 内容检查 |
| PDF | Gotenberg Chromium HTML→PDF | WeasyPrint → ReportLab | pypdf + pypdfium2 全文/渲染 QA |
| PPTX | PptxGenJS Node sidecar | python-pptx | OOXML + python-pptx + Gotenberg 渲染 |

这张表是 A/B 起点，不是未经验证的最终默认链。

### 7.2 为什么优先复用这些工具

- Gotenberg 已把 Chromium、LibreOffice 和 PDF 引擎封装为稳定 HTTP API，可减少自建
  进程管理、转换命令和字体环境的代码；正式镜像必须固定版本和 digest。
- XlsxWriter 的 `constant_memory` 支持逐行写大表，生成与 openpyxl 独立重开形成 maker/checker
  分离。
- docxtpl 负责模板化 DOCX，python-docx 负责结构生成补充和独立重开。
- PptxGenJS 提供 Slide Master、表格自动分页和完整 TypeScript 类型，适合做 PPTX 效果候选；
  python-pptx 保留为不同实现的同格式备选。
- Pandoc 适合标记文本和 DOCX 的受控转换，但其 AST 不能保证复杂版式无损，因此只作为
  同格式备选或轻量互转，不作复杂 Office 保真主链。
- WeasyPrint 适合受控 HTML/CSS 到 PDF，但 Windows 原生依赖较多且不可信 HTML/CSS 有资源、
  网络和本地文件风险，故只放隔离 sidecar 参与 A/B。
- LibreOffice 直接命令不再由业务代码自行拼接；优先经 Gotenberg 使用。只有 Gotenberg
  A/B 或运行边界不达标时，才评估独立 LibreOffice sidecar。

### 7.3 不采用

- 不自己实现 DOCX/PPTX/OOXML 格式；
- 不自己实现 PDF 排版引擎；
- 不让 LLM 直接生成最终文件、HTML、脚本或转换命令；
- 不把 Gotenberg、LibreOffice、Pandoc、WeasyPrint 全部塞入主 Python 3.13 进程；
- 不使用用户提供的模板脚本、Pandoc filter、宏或外部资源 URL；
- 不因为某个格式失败就写一份同扩展名的其他格式文件；
- 不依赖文件扩展名判断 QA，通过 MIME、魔数、格式解析和内容账本共同判断。

## 8. 开源工具 A/B 计划

### 8.1 硬门

候选只有全部满足以下条件才参与评分：

- 输出可由独立工具重新打开；
- 请求内容、行数、列、顺序、证据和内容政策全部满足；
- 空文件、损坏文件、缺页、缺幻灯片和中文缺字为 0；
- 不读取未授权本地路径，不发起未授权网络访问；
- 超时、崩溃和坏输入返回结构化失败；
- Windows Docker Desktop 和 Linux 容器均能运行；
- 不静默替换格式或截断内容。

任何硬门失败即淘汰，不用性能分补偿正确性。

### 8.2 评分

硬门通过后按效果优先评分：

- 内容/结构/证据保真：40%；
- 版式、中文字体、表格分页和视觉：35%；
- 稳定性与失败可解释性：15%；
- 延迟、峰值内存和临时磁盘：10%。

许可证不作为本项目筛选分。

### 8.3 A/B 样本

仓库提交脱敏小型 fixture，真实业务样本只在本地目录：

1. 表格：
   - “谢超群”11 行 × 2 可见列；
   - 中文、日期、Decimal、空值、长文本、嵌套值；
   - 以 `= + - @` 开头的公式注入样本；
   - 多 Sheet、接近 Excel 行限制和超长单元格边界；
   - 零行结果和故意损坏输出。
2. 文档：
   - 中文商务条款 verbatim；
   - 两文档差异；
   - 合规核查；
   - 明确授权的总结/改写/翻译；
   - 标题层级、长表格、分页、页眉页脚、证据附录；
   - 50–100 页长文和多来源编排。
3. PPTX：
   - 长表自动分页；
   - 中文字体、标题、正文、脚注、来源和证据页；
   - 极长段落和无法容纳内容的显式失败样本。

### 8.4 A/B 产物

新增可复跑脚本和机器报告：

```text
scripts/eval_phase4b_batch6_outputs.py
docs/plans/phase4b-batch6-results/tool-probe.json
docs/plans/phase4b-batch6-results/format-benchmark.json
docs/plans/phase4b-batch6-results/visual-review.md
docs/adr/0015-phase4b-batch6-delivery-tool-selection.md
```

报告记录版本、镜像 digest、输入哈希、输出哈希、分项得分、峰值资源、截图和淘汰原因。

## 9. 各格式生成与 QA

### 9.1 共同规则

- Renderer 只消费已通过验证的内部结果；
- 文档 Renderer 只把 Document AST 确定性排版，不重新让 LLM 生成内容；
- `verbatim` 节点必须逐字进入输出，禁止自动润色；
- 派生内容必须标出动作、Provider、模型和 EvidenceRef；
- 所有文件先写 staging，禁止直接覆盖已发布输出；
- 所有文本使用 UTF-8；CSV 使用明确编码并在 Manifest 记录；
- 生成后计算 SHA-256、大小和魔数；
- QA 使用与生成器不同的解析路径；
- 每个请求格式均有 `qa_pass` 才允许发布。

### 9.2 JSON / JSONL

- 分批流式写，不因 JSON 数组较大就静默改成 JSONL；
- 日期、Decimal、null 和嵌套对象使用冻结的规范化规则；
- 重读后核对 Schema、记录数、字段顺序和规范化内容摘要；
- 无完整记录时失败，不生成空数组冒充完成。

### 9.3 CSV

- 流式写、固定换行和 quoting；
- 明确区分 canonical 数据与 CSV 查看副本的类型损失；
- 公式注入值按查看副本安全策略处理，原值仍保留在权威格式；
- QA 核对全部行列，不只读取前 N 行；
- Manifest 记录编码、delimiter、quote 和公式安全策略。

### 9.4 Parquet

- PyArrow 分批写，保留 null、日期、数值和嵌套类型；
- 重读 Schema、行组、记录数和统计；
- 核对可见列不包含内部 `__mg_*` 控制列；
- 逐记录 lineage 通过 sidecar 保留，不把内部 lineage 列暴露为业务列。

### 9.5 XLSX

- 优先 XlsxWriter `constant_memory`，字符串用 `write_string` 或关闭
  `strings_to_formulas`，禁止来源文本变成公式；
- 冻结标题行、筛选、列宽、换行、中文字体和 Evidence/Quality/Manifest 工作表策略；
- 超过 Excel 行限制时允许在同一 XLSX 内按确定性规则分 Sheet，必须完整保留所有行并在
  Manifest 记录；超过列限制或单元格长度限制时明确失败并询问替代格式；
- openpyxl `read_only/data_only` 重开，核对 Sheet、行、列、值、公式数和内部控制列；
- Gotenberg/LibreOffice 把工作簿转 PDF 做中文、列宽和分页抽样 QA；
- XLSX 始终标记为 `view_copy`，不能替代 Evidence、Lineage、Quality 和 Manifest。

### 9.6 HTML / Markdown / TXT

- 只使用项目受控模板；来源文本全部转义，不执行其中的 HTML/脚本；
- HTML 默认自包含，不加载外部脚本、样式、图片或字体 URL；
- Markdown 不允许来源文本注入原始 HTML 改变页面结构；
- TXT 使用 UTF-8，无隐藏格式替换；
- 各自用 HTML parser、markdown-it 和严格 UTF-8 解码重开；
- 核对标题层级、段落、表格、证据标记和 verbatim 内容。

### 9.7 DOCX

- docxtpl 使用版本化 reference DOCX；python-docx 补充结构和证据附录；
- OOXML 解包检查必要 parts、relationship 和媒体引用；
- python-docx 重开核对段落、表格、标题层级和关键原文；
- 经 Gotenberg/LibreOffice 转 PDF，再由 pypdfium2 渲染；
- 检查页数、空白页、中文缺字、表格跨页、标题孤行和证据附录；
- 不执行宏，不解析外部链接资源。

### 9.8 PDF

- 预设首选是受控 HTML/CSS 经 Gotenberg Chromium 输出；
- WeasyPrint 和 ReportLab 作为相同目标格式 A/B 备选；
- pypdf 核对页数、文本、标题、元数据和加密状态；
- pypdfium2 渲染全部小型 fixture；长文检查首页、末页、所有表格页和固定间隔抽样页；
- 自动检查空白页、近空页、页面尺寸、缺字占位符和异常超大页面；
- A/B 视觉报告必须人工或可复核图像检查通过，不能只有文本解析成功。

### 9.9 PPTX

- PptxGenJS 与 python-pptx 用相同 Slide Model 生成，避免两个实现各自解释用户语义；
- 使用版本化 16:9 master、中文字体、标题/正文/表格/来源/证据布局；
- 长表必须显式分页并重复表头；无法容纳内容时失败，禁止缩成不可读字号；
- OOXML 解包和 python-pptx 重开，核对幻灯片数、元素、文本、表格和关系；
- Gotenberg/LibreOffice 转 PDF，逐页渲染检查空页、溢出、裁切和字体缺失；
- PPTX 只有用户明确请求时生成，本批仍必须具备并通过完整 QA。

## 10. Manifest 与发布事务

### 10.1 Idempotency

交付幂等键由以下内容规范化后计算 SHA-256：

- run ID；
- 逻辑计划、绑定和成功 attempt 哈希；
- 内部结果制品哈希；
- 请求格式和 output_name；
- Renderer/Converter 版本；
- 模板和字体包版本；
- QA 规则版本。

相同键：

- 已成功：直接返回相同 delivery/output IDs，不重新生成；
- 正在执行：返回当前状态，不启动第二份；
- needs_user：返回同一问题，不生成新问题；
- 失败：只有显式 retry 且失败类型允许时创建新 attempt，不覆盖历史。

### 10.2 原子发布

1. 所有请求格式写 staging；
2. 每个格式完成独立 QA；
3. 生成 Evidence、Lineage、Quality；
4. 生成 Manifest；
5. 复核 Manifest 中所有 SHA-256、大小和引用；
6. 单事务把文件移入 outputs，并把数据库状态切成 `released/succeeded`；
7. 事务失败则不开放任何正式下载，重试复用已有 qa_pass staging。

“部分格式通过、部分格式失败”不得发布部分结果。用户确认替代格式后，以新 delivery revision
继续，不改写原失败记录。

## 11. 下载 API

批次 6 扩展 `/api/semantic-harness`，不复用带路径参数的旧下载 URL：

- `GET /runs/{run_id}/delivery`
  - 返回交付状态、请求格式、已完成格式和问题，不返回路径。
- `GET /runs/{run_id}/delivery/manifest`
  - 仅 owner 且 Manifest 已发布可读取。
- `GET /runs/{run_id}/delivery/outputs`
  - 只列出 `released` 输出的 ID、名称、格式、角色、MIME、大小和哈希。
- `GET /runs/{run_id}/delivery/outputs/{output_id}/download`
  - 仅 owner、QA pass、已发布、到达 `available_at` 且未过期时下载。
- `POST /runs/{run_id}/delivery/resume`
  - 回答替代格式问题；校验 question ID、resume token 和固定候选。
- `POST /runs/{run_id}/delivery/retry`
  - 仅对明确可重试的技术故障，保持用户语义不变。

下载响应：

- 无权访问、跨用户枚举和不存在统一 404；
- 质量未通过、未发布或尚未到释放时间返回 409；
- 已过期返回 410；
- 文件缺失或哈希不符立即阻断并把 delivery 标为损坏，不能继续下载；
- `Content-Type` 与注册格式一致；
- `Content-Disposition` 同时提供安全 ASCII fallback 和 UTF-8 `filename*`；
- 文件名由服务端清洗 Windows/Linux 非法字符、保留合理中文，不接受路径分隔符；
- 不返回绝对路径、storage key 或 sidecar 宿主目录。

## 12. Sidecar 与跨平台方案

### 12.1 Docker Compose

新增独立 `compose.semantic-delivery.yml`，不修改现有 SearXNG/RSSHub/Firecrawl 服务：

- `gotenberg`：Office/HTML/Markdown 转 PDF和渲染；
- `weasyprint`：仅 A/B 或同格式备选 profile；
- `pandoc`：仅白名单 reader/writer；
- `pptxgenjs`：最小 Node sidecar，只接收强类型 Slide Model；
- 直接 LibreOffice sidecar 仅在 Gotenberg A/B 不达标时保留。

### 12.2 安全基线

- 固定镜像版本和 digest，禁止生产使用 `latest`；
- 非 root、只读根文件系统、禁止 privileged 和 Docker Socket；
- 默认无外网；Gotenberg outbound deny，Pandoc 不允许 URL、filter、Lua 和任意参数；
- sidecar 只通过 API 接收任务字节，不挂载项目根、用户目录、`.env` 或凭证目录；
- 输出只写任务专属临时目录或由主服务接收字节后落盘；
- CPU、内存、PID、请求字节、超时、tmpfs 和并发上限全部配置；
- Gotenberg LibreOffice 单实例转换并发按 1 起步，Linux 通过多实例扩容而不是同实例并发；
- 字体镜像固定 Noto CJK 等中文字体，Manifest 记录字体包版本；
- stdout/stderr 和健康日志不记录正文。

### 12.3 Windows 与 Linux 验收

- Windows：主服务 Python 3.13 + Docker Desktop Linux containers，完整跑 11 格式；
- Linux/Docker：同一 Compose、镜像 digest、模板和 fixture 跑集成测试；
- 两端比较语义内容、结构和渲染，不要求 Office ZIP 元数据导致的逐字节相同；
- 同一 delivery 重试必须复用已发布字节，保证本次幂等 SHA-256 不变；
- 目标服务器 GPU、10–20 用户并发和完整故障注入仍属于批次 8，不提前冒充完成。

## 13. 实施顺序

### 步骤 0：开发前基线与 A/B 环境

- 重新核对 Git、解释器、`pip check`、Docker、磁盘和端口；
- 运行批次 1–5 定向回归；
- 创建公开 batch6 fixture 和私有样本目录说明；
- 拉取并记录候选镜像 digest，不改主环境重依赖；
- 运行工具探针和最小 A/B。

验收：

- 批次 1–5 基线 0 failed；
- 每个候选有版本、健康、超时、输入限制和明确可用/不可用结果；
- 不能运行的候选不会被写成默认能力。

### 步骤 1：冻结交付契约与 Schema

- 实现 DeliveryRun、Capability、Output、QA、Manifest、Question/Decision；
- 导出 JSON Schema；
- 增加非法状态、额外字段、路径、未知格式和哈希校验测试。

验收：

- 客户端不能提交路径、命令、镜像、工具参数或未登记格式；
- 非 pass Harness run 不能创建 DeliveryRun；
- 格式替代必须有结构化用户决定。

### 步骤 2：批次 5 结果引用与数据库

- 持久化成功 attempt 的内部 artifact paths；
- 新增三张 delivery 表和 store 方法；
- 公共 API 视图删除内部路径；
- 实现交付幂等键和状态迁移。

验收：

- 服务重启后仍能从成功 attempt 找到且核验内部结果；
- 跨用户读取内部结果为 0；
- 重复创建只产生一个 delivery。

### 步骤 3：Renderer/Converter 注册表

- 实现固定注册表、健康检查、优先级和同格式候选选择；
- 接入主进程轻量 Writer；
- 接入受控 sidecar clients；
- 所有错误映射为结构化失败分类。

验收：

- 未登记能力不能调用；
- 工具不健康时只切换同格式、同契约候选；
- 网络、超时、OOM、损坏输出和不支持格式均可区分。

### 步骤 4：结构化输出

- 依次完成 JSON/JSONL、CSV、Parquet、XLSX；
- 生成 Evidence、Lineage 和 schema sidecars；
- 完成全部结构 QA 和 Excel 视觉抽样。

验收：

- “谢超群”5 种格式均为 11 行、2 个可见业务列、1 张逻辑表；
- 谓词、证据和 lineage 覆盖率 100%；
- 内部 `__mg_*` 列不出现在业务输出；
- 公式注入执行数为 0；
- 无静默截断。

### 步骤 5：轻量与 Office 文档输出

- 完成 HTML、Markdown、TXT；
- 完成 DOCX；
- 完成 PDF 三候选 A/B并冻结默认/备选；
- 完成 Evidence/Lineage/Quality 附录和 sidecars。

验收：

- verbatim 原文逐字一致；
- 比较保留双方证据；
- 审查 pass/fail 结论均有证据；
- 派生内容明确标注；
- DOCX/PDF 可重开且渲染通过。

### 步骤 6：PPTX

- 冻结 Slide Model 和 16:9 master；
- PptxGenJS/python-pptx A/B；
- Gotenberg/LibreOffice 渲染 QA；
- 长表、长文和中文字体边界测试。

验收：

- PPTX 可由 python-pptx 和 LibreOffice 重开；
- 幻灯片数、必要元素和来源页齐全；
- 无空页、裁切、不可读缩字和字体缺失。

### 步骤 7：Manifest、原子发布与下载

- 写正式 Manifest；
- 实现全格式事务发布；
- 接入 Harness deliver 节点；
- 实现状态、Manifest、输出列表、下载、resume 和 retry API；
- 完成文件名、MIME、哈希、过期与延迟释放。

验收：

- 只有 `delivery_status=succeeded` 的 QA pass 输出可以下载；
- 某一格式失败时正式下载数为 0；
- 跨用户下载和枚举成功数为 0；
- 重复调用不会生成第二套正式文件。

### 步骤 8：回归和执行报告

- Phase 4B 批次 -1 至 6 定向；
- 全仓后端；
- 前端生产构建；
- Windows 完整格式门；
- Linux 容器集成门；
- Phase 4A 正式输出/下载回归；
- 生成 A/B、资源和视觉报告；
- 同步 ADR、权威总计划、handoff、AGENTS、README_AGENT 和执行报告。

验收：

- 全部门禁 0 failed；
- 既有 skip/xfail 没有为掩盖本批故障而增加；
- `git diff --check`、JSON Schema 和文档链接检查通过；
- 只按批次 6 文件白名单暂存。

## 14. 自动化验收矩阵

| 维度 | 场景 | 固定期望 |
|---|---|---|
| 前置门 | Verification fail/needs_user | DeliveryRun 不创建 |
| 前置门 | eligible=false | 409，不调用 Renderer |
| 引用 | attempt 无路径或哈希不符 | 明确失败，不猜路径 |
| JSON | 正常/空/嵌套/大数组 | 可重读；空假成功为 0 |
| JSONL | 多批次/最后无换行/坏行 | 全行可读；记录数精确 |
| CSV | 中文/逗号/换行/公式字符串 | 行列一致；公式执行为 0 |
| Parquet | null/日期/数值/嵌套 | Schema 和记录数一致 |
| XLSX | 大表/多 Sheet/长单元格 | 不截断；超限明确失败 |
| HTML | 来源含 script/外链/file URL | 不执行、不访问、不泄漏 |
| Markdown | 来源含原始 HTML/伪标题 | 内容与结构不越权 |
| TXT | 中文/换行/控制字符 | UTF-8 严格可读 |
| DOCX | 原文/表格/证据附录 | OOXML、重开、渲染全通过 |
| PDF | 长表/分页/中文/空白页 | 页数、文本、渲染全通过 |
| PPTX | 长表/长段/中文/来源页 | 无裁切、空页和字体缺失 |
| Evidence | 非空语义结果 | 覆盖率 100% |
| Lineage | 表格每条输出记录 | 覆盖率 100% |
| Verbatim | 原文任务 | 未授权改写为 0 |
| 派生内容 | 总结/改写/翻译 | Provider/模型/证据完整 |
| 格式失败 | 首选失败、备选成功 | 同格式备选可发布 |
| 格式耗尽 | 所有同格式候选失败 | needs_user；无正式下载 |
| 部分成功 | 10 格式 pass、1 格式 fail | 正式下载数为 0 |
| 替代格式 | 有效/无效/重复 resume | 只接受当前问题一次 |
| 幂等 | 同请求并发/重试/重启 | 同一 delivery/output IDs |
| 损坏 | QA 后文件被篡改 | 下载阻断并标记损坏 |
| 权限 | 跨用户 run/output/download | 全部 404 |
| 路径 | `../`、绝对路径、保留名 | 全部拒绝 |
| 生命周期 | 未释放/过期 | 409/410 |
| Sidecar | 超时/OOM/崩溃/断网 | 结构化失败、无假成功 |
| 跨平台 | Windows 与 Linux 同 fixture | 语义和结构一致 |
| 回归 | Phase 4A 下载 | 行为不退化 |
| 回归 | 批次 5 repair/resume | 全部原门禁保持 |

## 15. 批次 6 退出标准实测

| 原定门禁 | 当前结果 | 证据或去向 |
|---|---|---|
| 11 种格式 Renderer、独立重开和下载 | 通过 | `tests/test_semantic_delivery.py` |
| 表格/文档只消费已验证权威结果 | 通过 | Harness 定向和全仓回归 |
| 空、损坏或 QA fail 不发布 | 通过 | Renderer 故障注入测试 |
| MIME、大小、SHA-256、生成器版本和 QA | 通过 | `DeliveryManifest` |
| 请求格式失败不发布其他格式 | 通过 | staging 全格式事务发布 |
| 不支持格式不静默替代 | 通过 | TSV 显式失败测试 |
| 跨用户下载成功次数为 0 | 通过 | owner 404 测试 |
| 文件篡改后仍可下载次数为 0 | 通过 | SHA-256/大小 409 测试 |
| 重复 run 不生成第二份正式结果 | 通过 | run 唯一索引与既有 Manifest 复用 |
| Phase 4A、批次 5 和全仓无回归 | 通过 | 928 passed、4 skipped、0 failed |
| 前端生产构建 | 通过 | `npm.cmd run build` |
| 截图级 DOCX/PDF/PPTX 视觉 Golden | 延期 | 批次 8 |
| Evidence/Lineage/Quality 独立下载侧车 | 延期 | 批次 8；当前保留输入哈希和结果 provenance |
| Linux 容器同 fixture 集成门 | 延期 | 批次 8 |
| sidecar A/B、镜像 digest、字体镜像 | 延期 | 批次 8 |
| 大规模性能与真实数据库容器门 | 延期 | 需显式性能/DB live 开关 |

因此这里的“批次 6 完成”只指当前已经验证的后端正式交付基线，不等于原方案全部扩展门、
Phase 4B 封板或生产服务器验收。批次 7 正式前端和批次 8 扩展评测/封板审计仍未完成。

## 16. 实际代码与文档范围

实际实现保持为最小可验证闭环：

```text
src/semantic_harness/delivery/
  models.py
  service.py

src/semantic_harness/harness_adapters.py
src/semantic_harness/harness_graph.py
src/api/store.py
src/api/routes/semantic_deliveries.py
src/api/main.py
requirements.txt

tests/test_semantic_delivery.py
tests/test_semantic_harness_loop.py

docs/plans/2026-07-27-phase4b-batch6-delivery-plan.md
docs/plans/2026-07-27-phase4b-batch6-execution-report.md
```

`reportlab` 和 `xlsxwriter` 是实际生产 Renderer，因此从原评测 requirements 提升为
`requirements.txt` 的固定运行时依赖。未采用的重型候选没有并入主环境。

## 17. 风险与处理

| 风险 | 处理 |
|---|---|
| 工具返回 success 但文件损坏 | 独立重开、内容账本和渲染 QA |
| 内部路径未持久化 | attempt 保存受根目录约束的私有 artifact mapping |
| 请求多格式只有部分成功 | staging + 全格式事务发布 |
| XLSX 大表截断 | 硬限制检查、确定性多 Sheet、无法表示时询问 |
| CSV/XLSX 公式注入 | 查看副本安全写入，权威格式保留原值并记录策略 |
| DOCX/PPTX 中文字体漂移 | 固定字体镜像、Gotenberg 渲染和视觉 Golden |
| Pandoc/LibreOffice 版式损失 | 只作候选；硬门失败即淘汰 |
| WeasyPrint/Chromium 读取本地或网络资源 | 受控模板、无网、输入隔离、URL 拒绝 |
| Gotenberg 单 LibreOffice 实例串行 | 并发 1 起步，Linux 多实例扩容 |
| Sidecar 卡死/OOM | 超时、资源限制、结构化失败、同格式备选 |
| 工具升级导致输出漂移 | 固定版本/digest/字体，Golden 回归后升级 |
| 下载路径穿越或跨用户 | 不透明 output ID、owner 404、根目录 resolve |
| 下载后文件被篡改 | 发布和下载完整性检查，损坏立即撤销 |
| Manifest 自己与文件不一致 | Manifest 最后生成，发布事务前重新核对全部引用 |
| 批次 6 提前混入前端 | 本批只提供 API；正式 UI 留到批次 7 |
| 共享 Python 环境依赖继续污染 | 重工具 sidecar；主依赖最小化并回归 `pip check` |

## 18. 实施前仍需实时确认但不阻塞方案冻结

以下内容在用户确认本方案并正式开工时用实时证据决定，不在本方案中伪装为已确定：

1. Gotenberg、WeasyPrint、PptxGenJS 和 Pandoc 的最终固定版本/digest；
2. A/B 后每个格式的最终默认和备选链；
3. 私有真实样本的本地目录和脱敏边界；
4. 字体包清单；
5. 各格式最大字节、页数、行数和并发预算；
6. 是否需要保留独立 LibreOffice sidecar；
7. A/B 未证明增益的候选是否从 Compose profile 中删除。

用户确认本方案后，先执行步骤 0 和 A/B，不直接一次性安装所有候选到主环境。

## 19. 上游依据

- Phase 4B 权威计划：
  `docs/plans/2026-07-24-phase4b-semantic-task-harness-plan.md`
- 批次 5 实施方案：
  `docs/plans/2026-07-27-phase4b-batch5-bounded-harness-plan.md`
- 批次 5 执行报告：
  `docs/plans/2026-07-27-phase4b-batch5-execution-report.md`
- ADR-0012：
  `docs/adr/0012-semantic-task-plan-and-bounded-tool-loop.md`
- ADR-0013：
  `docs/adr/0013-phase4b-batch0-tool-selection.md`
- Gotenberg：
  <https://gotenberg.dev/docs/getting-started/routes>
- LibreOffice headless 与转换过滤器：
  <https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html>
  <https://help.libreoffice.org/latest/en-US/text/shared/guide/convertfilters.html>
- Pandoc：
  <https://pandoc.org/MANUAL.html>
- WeasyPrint：
  <https://doc.courtbouillon.org/weasyprint/stable/first_steps.html>
- PptxGenJS：
  <https://gitbrent.github.io/PptxGenJS/docs/introduction/>
- XlsxWriter：
  <https://xlsxwriter.readthedocs.io/working_with_memory.html>
- docxtpl：
  <https://docxtpl.readthedocs.io/>
