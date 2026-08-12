# ADR-0014：Phase 4B 批次 4 采用证据约束文档执行引擎

- 状态：已采纳并落地
- 日期：2026-07-27
- 适用范围：文档原文提取、比较、核查、总结、改写、翻译与编排的后端执行层

## 背景

Phase 4A 已能把 PDF、DOCX 和图片解析为 `DocumentElement`，并用
`EvidenceRef` 回到原文位置，但还不能可靠执行“只摘原文”“比较两份合同”“按规则核查”
或“明确要求后再总结”等自然语言任务。批次 4 需要补齐来源无关的文档执行能力，同时避免：

- 重新实现 OCR 或把模型输出冒充原文；
- 用 Prompt 写死行业场景；
- 模型直接生成无来源的结论；
- 为引入重型工具污染 Python 3.13 主环境。

## 决策

1. 继续复用 Phase 4A 的 `DocumentElement/EvidenceRef`，执行前重新读取不可变原始制品并校验
   SHA-256。
2. PDF、DOCX、PPTX、HTML、Markdown、TXT、XML 首批进入统一元素层。原生解析优先复用
   `python-docx`、`python-pptx`、BeautifulSoup/lxml 和 `markdown-it-py`。
3. 原文选择、章节扩展、规则核查和客观差异由确定性代码执行；多文档章节对齐使用
   RapidFuzz，结构化表格差异使用 DeepDiff，中文数值和日期分别复用 cn2an、dateparser。
4. 语义模型只承担无法由确定性规则完成的影响判断和用户明确授权的总结、改写、翻译或编排。
   默认只允许本地/LAN Provider；模型失败时保留客观差异或返回“无法判断”，不得编造成功。
5. 模型只能引用执行器提供的证据 ID。未知 ID、空证据或无来源派生内容一律拒绝。
6. 中间结果使用强类型 `DocumentPhysicalPlan → DocumentExecutionResult → DocumentAST`。
   AST 按“来源文档 → 原始标题/段落/列表/表格 → 比较/核查/派生结果”保存，每个带正文节点
   必须包含来源引用。
7. 批次 4 只交付可验证的中间 Document AST JSON 和测试 API。DOCX/PDF 等正式渲染、
   重开 QA、下载权限与前端交付属于批次 6/7。

## 成熟工具边界

- MarkItDown 作为轻量文本 fallback 候选，不作为权威结构或证据来源。
- Docling 和 Unstructured 继续放在隔离 sidecar 候选链；没有真实 A/B 增益前不加入主环境。
- LibreOffice、Pandoc、docxtpl、WeasyPrint 和 PptxGenJS 留给批次 6 的输出/转换层。

## 结果

- 同一套契约可覆盖条款摘录、合同比较、合规核查及显式派生内容，不需要为行业复制主链。
- 原文和模型派生内容在契约及 AST 中分离。
- 每项差异保留修改前、修改后双方证据；每项通过/失败核查都有来源证据。
- 代价是正式可下载文档尚未交付，长文档、并发和更多语义类别仍需批次 8 扩展评测。
