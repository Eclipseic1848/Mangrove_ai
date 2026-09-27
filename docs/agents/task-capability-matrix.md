# 任务合同与执行能力矩阵

本表说明实现接线，不构成真实模型或全部格式验收。统一工作台与旧 semantic-harness 入口必须分别判断；不能把已有枚举数当作执行器数。

| 入口／任务家族 | 参数与来源合同 | 现有执行接缝 | 验证与拒绝边界 |
| --- | --- | --- | --- |
| semantic-harness：tabular_transform | SemanticTaskPlan、record_grain、SourceScope、绑定 | table.duckdb，物理表格计划 | 表格验证器、行级来源、谓词／列／数量后置条件 |
| semantic-harness：extract | 明确章节／页码／筛选，或用户明确全文 | document.evidence，文档物理计划 | 文档证据与范围验证；不把缺范围补成全文 |
| semantic-harness：compare／audit | 对应操作及其参数、绑定来源 | document.evidence | 文档验证及审查规则；声明操作不代表任意规则可执行 |
| semantic-harness：compose／summarize／translate | 内容政策、证据与交付合同 | document.evidence | 摘要／翻译要求对应内容政策；派生内容不能当逐字原文 |
| semantic-harness：convert／transcribe／discover | 枚举存在 | 此旧灰度入口未接执行器 | HTTP409拒绝；不能仅因合同解析成功便执行 |
| 统一工作台 | 任务修订、SourceSnapshot、模型连接版本及目标合同 | AgentKernel → 冻结Pi/CoreMind Adapter、能力工具 | CandidateVerification、交付QA与正式发布；不是TaskFamily关键词路由器 |
| 共享网页／社交采集 | TaskSpec、EvidenceCollectionScope／已有领域规格、冻结预算 | Conductor、Collector Registry | 来源状态、覆盖、证据抽取、宿主初稿；采到数据不等于正式交付 |
| 历史文档抽取 | task_type=document_extraction，ExtractionSpec | data_tasks既有提取链 | 文档证据与复核；不能套用DataPrepTaskSpec误判旧记录 |

## 现有注册机制

`src/semantic_harness/capabilities.py` 的 CapabilityRegistry 固定注册两种能力，包含manifest、executor、healthcheck；`src/api/routes/semantic_harness.py` 的入口映射选择它们，harness adapter负责物理计划和验证。新增类型不仅是加一个枚举，还需核对入口、绑定、规划与验证支持。

表格manifest声明csv/tsv/xlsx/parquet/json/jsonl；文档manifest声明pdf/docx/pptx/html/markdown/txt/xml及四类静态图片。它们是接受格式声明，不代表任意损坏、加密或所有格式组合都已验收。未知来源、能力或未解决歧义必须通过已有合同和绑定门拒绝／澄清。

统一工作台按 CONTEXT.md 的AgentKernel边界执行，不应复制一套按TaskFamily或业务关键词分派的框架。RuntimeBinding固定adapter、模型、内容身份及能力digest；Kernel缺必需能力时失败关闭。

## 追溯与失败统计

按Owner、task、revision和run关联以下证据，不把不同修订合并成一次成功：

1. 原始目标、冻结逻辑计划及参数、SourceSnapshot／绑定身份。
2. semantic-harness的capability_id与物理计划；工作台的kernel.binding.frozen及能力事件。
3. 工具完成／失败、独立Verifier结果、Candidate与正式Delivery身份。
4. failure.error_code、stage、elapsed_ms、source_read、intermediate_created、delivery_published。

Pi已明确识别的镜像不可用／摘要无效、模型协议不支持、来源完整性、文档范围拒绝与连续读取失败由抛错点提供稳定代码。其他异常保留通用PI_RUNTIME_FAILED，不根据业务主题编造原因；新增代码不表示底层故障已修好。未知模型结果优先保持needs_input，不能由OCR等文案规则降级为可重试失败。旧记录不会自动回填新分类。

合同生成器、历史清单和新增类型步骤见 [task-generalization.md](task-generalization.md)。真实模型、Cookie身份、引擎升级与全量历史重放各有独立验收，不能由本表推导通过。
