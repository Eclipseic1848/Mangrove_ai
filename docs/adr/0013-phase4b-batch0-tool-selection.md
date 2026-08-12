# ADR-0013：Phase 4B 批次 0 工具赛马结论

- 状态：建议已落实到批次 3–6；生产能力、Harness 和正式输出链已实现
- 日期：2026-07-26
- 适用范围：Phase 4B 批次 0 工具选择依据；生产执行已在后续批次接入，Phase 4A 默认路由仍未替换

## 背景

批次 -1 已冻结 `SemanticTaskPlan → BoundPlan → ToolResult → VerificationReport`
控制面契约，但尚不能仅凭工具知名度决定默认链。批次 0 使用公开脱敏 Golden、固定后置条件
和同题 A/B，验证表格、文档解析、常用输出、模型规划及失败边界。

机器报告位于：

- `docs/plans/phase4b-batch0-results/local-benchmark.json`
- `docs/plans/phase4b-batch0-results/docling-sidecar-probe.json`
- `docs/plans/phase4b-batch0-results/docling-sidecar-probe-warm.json`
- `docs/plans/phase4b-batch0-results/model-probe.json`
- `docs/plans/phase4b-batch0-parser-hyper-pilot.json`
- `docs/plans/phase4a-parser-ab-results.json`
- `docs/plans/2026-07-26-pre-phase4b-batch1-readiness-review.md`

## 结论

### 表格执行

三候选均通过筛选/投影、合并、聚合、账本守恒、缺失列拒绝和非唯一连接键拒绝。
100 万行同一 Parquet 的过滤与聚合结果完全一致，本机单次耗时为：

| 候选 | 10 万行 | 100 万行 | 结论 |
|---|---:|---:|---|
| Polars 1.43.0 | 1 ms | 3 ms | 单表列式转换首选 |
| Pandas 2.3.3 | 2 ms | 9 ms | 格式兼容和小表降级 |
| DuckDB 1.5.5 | 18 ms | 21 ms | 多文件关系运算、SQL 推下和低常驻内存备选 |

本轮只记录进程 RSS 前后差值，不冒充采样峰值。生产资源预算仍需在独立进程或容器中采样。

### 文档解析与转换

- MarkItDown 0.1.6 对 PDF、DOCX、PPTX、HTML、Markdown、TXT、XML、XLSX 为
  8/8；热运行数字 PDF 约 18 ms。采用为常见数字文档的轻量文本 fallback。
- Docling 2.115.0 在隔离 Python 环境中对 PDF、DOCX、PPTX、HTML、Markdown、XLSX
  为 6/6；热运行 PDF 约 16.27 s。批次 0 当时的共享解释器存在外部 editable 包依赖冲突；
  该包已在 2026-07-27 卸载，但 Docling 仍因依赖重量、资源和隔离边界只允许作为
  sidecar/增强链，不加入主 Python 依赖。
- Phase 4A 的 17 份、85 页同集结果仍支持 MinerU pipeline 为扫描/混合 PDF 默认，
  Paddle 为表格增强与缺页回退：加权分分别为 0.987554 和 0.960267。
- MinerU Hyper medium/high 本轮 6/6 在服务端立即失败，统一错误为
  `Device string must not be empty`。OpenAPI 没有客户端 `device` 参数，因此这是
  `192.168.1.21:8000` 的服务端配置待办；修复并重跑前不得进入默认链。
- 本机无 Java、LibreOffice 和 Pandoc。Pandoc 官方镜像首次拉取 300 秒未完成且未落镜像；
  Tika/LibreOffice/Pandoc 不进入本机默认链，保留服务器 sidecar PoC，不伪造实测成功。

### 输出与失败门

- DOCX、PDF、PPTX、XLSX 四类 Golden 均可重新打开且包含预期内容。
- PDF/DOCX 商务条款逐字抽取、DOCX/PDF 生成、三项合同差异定位全部通过。
- 损坏 PDF、加密 PDF、未知二进制格式和转换超时均被明确阻断，没有静默替代。
- 本批保留现有 `openpyxl/python-docx/python-pptx/reportlab` 为确定性基线；
  WeasyPrint、PptxGenJS、LibreOffice 的视觉和复杂版式 A/B 留在输出能力正式接入批次。

### 模型

同一公开脱敏复合任务含 12 项确定性断言：

| 模型 | 结果 | 耗时 | Token | 结论 |
|---|---:|---:|---:|---|
| 本地 Qwen3.6-35B-A3B | 12/12 | 15.30 s | 2120 | 默认本地候选 |
| DeepSeek V4 Pro | 12/12 | 13.99 s | 721 | 用户确认外发后的云备选 |
| 百炼 Qwen3.7 Plus | 5/12 | 9.56 s | 338 | 表格计划正确但条款数组为空，不可作复合任务默认 |

该结果只代表本批小型 Golden，不代表模型总体能力排名。它证明“合法 JSON”和“调用成功”
都不能作为任务完成条件；每个子目标必须由 `VerificationReport` 独立验收。

## 后续解析服务复测（2026-07-26）

批次 0 原始 Hyper 试点使用本地 `hybrid-engine`。后续发现服务还提供
`hybrid-http-client`，可把 Paddle VLM `18080/v1` 作为远程视觉端点：

- medium 3/3 跑通，15/15 页、字段召回 100%，但行召回 60%、表格行召回 0%，
  综合分 0.740017；
- high 3/3 返回完成，但 15 页全部为空，综合分 0；
- 同组 Paddle `/layout-parsing` 综合分 0.950895、表格行召回 100%；
- 本地 `hybrid-engine` 的 device 错误仍未解除。

这只把 Hyper medium 调整为实验候选，不改变当前生产解析建议；high 必须按空结果失败，
不能信任服务端“完成”状态。

## 建议采用的路由

1. 单表筛选、投影、排序、去重和常规聚合：Polars。
2. 多文件 Join、复杂关系查询和 SQL 推下：DuckDB；Pandas 仅作小表/格式兼容降级。
3. 数字 PDF/Office/标记文本轻量转换：MarkItDown；结构增强：隔离 Docling。
4. 扫描/混合 PDF：MinerU pipeline；表格增强或缺页：Paddle Pipeline。
5. 语义规划默认本地 Qwen；只有用户确认风险弹框后才能调用 DeepSeek。百炼 Qwen
   暂不处理同一轮多目标复合任务。

## 当前仍未解除的门禁

- 表格/文档生产能力包和后端 Harness 已实现，但 Phase 4A 正式流程尚未切换；
  批次 6/7 的正式输出下载和前端验收完成前不改运行时默认。
- 修复 MinerU Hyper high 空结果和本地 device，并补齐客户端空结果门与独立配置后，
  重跑至少 pilot，再决定是否扩到 17 份全集。
- Tika、LibreOffice、Pandoc、WeasyPrint、PptxGenJS 在服务器受控 sidecar 中补实测。
- 生产资源门需要记录独立进程峰值 RAM、临时磁盘峰值、缓存命中和并发吞吐。
- 批次 1 才实现 STP 语义编译器；本 ADR 的评测 Graph 不能冒充生产 Harness。
