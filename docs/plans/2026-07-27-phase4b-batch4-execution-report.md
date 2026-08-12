# Phase 4B 批次 4 执行报告：文档提取、比较、核查与编排

> 日期：2026-07-27
>
> 分支：`v0.0.6`
>
> 功能提交：`0e5d897`，已推送至 `platform/v0.0.6`
>
> 状态：批次 4 后端能力已完成；完整 Phase 4B 未封板
>
> 下一批：批次 5 有界 Harness Loop

## 1. 本批交付

- 新增强类型 `DocumentPhysicalPlan`、审查规则、差异、核查发现、派生内容和
  `DocumentAST` 契约及 JSON Schema。
- 首批统一支持 PDF、DOCX、PPTX、HTML、Markdown、TXT、XML；复用 Phase 4A
  `DocumentElement/EvidenceRef`，不重复实现 OCR。
- 实现原文 passage selector、章节扩展、多文档章节对齐、客观文本/表格差异、
  确定性数值/日期/正则核查，以及有证据的可选语义判断。
- 总结、改写、翻译和编排只有在内容政策明确时才调用本地/LAN 模型；原文与派生内容分开保存。
- Document AST 逐来源保留标题、段落、列表和表格节点；比较、核查、派生结论继续引用原始元素。
- 新增文档计划、执行和运行结果测试 API，执行记录按用户隔离，响应不泄露内部文件路径。

## 2. 成熟工具复用

| 能力 | 采用工具 | 边界 |
|---|---|---|
| DOCX/PPTX | python-docx 1.2.0、python-pptx 1.0.2 | 保留结构顺序和稳定位置 |
| HTML/XML/Markdown | BeautifulSoup+lxml、lxml 安全解析、markdown-it-py 4.0.0 | XML 禁止外部实体和网络 DTD |
| 章节对齐/结构差异 | RapidFuzz 3.14.5、DeepDiff 9.1.0 | 客观差异不依赖模型 |
| 中文数值/日期 | cn2an 0.5.24、dateparser 1.4.1 | 规则结果必须绑定证据 |
| 重型统一解析 | Docling/Unstructured | 继续隔离 A/B，不污染主环境 |
| 轻量文本转换 | MarkItDown 0.1.6 | 仅 fallback 候选，不作权威结构 |

## 3. 实机语义门

使用 LAN 本地 `Qwen3.6-35B-A3B` 比较两份公开脱敏合同：

- 付款比例 60% → 70%；
- 交付日期 2026-09-30 → 2026-10-15；
- 每日违约比例 1‰ → 2‰。

3/3 变化判断正确，双方证据覆盖率 100%，无无来源结论，耗时 48.6 秒。机器可读结果见
`docs/plans/phase4b-batch4-results/local-semantic-eval.json`。该结果只代表小型公开 Golden，
不代表全部业务场景准确率。

## 4. 验证结果

- 文档执行/API 定向：14 passed；
- Python `compileall`：通过；
- JSON Schema 重导出：通过；
- 全仓后端：**915 passed、4 skipped、0 failed**，214.50 秒；
- 4 项跳过均为需显式参数开启的真实 MySQL/PostgreSQL 或大规模性能门；
- `git diff --check`：通过；
- `pip check`：仍有既存 3 项冲突：
  - `types-pytz` 未安装；
  - crawl4ai 期望 lxml 5.3，当前为 6.1.1；
  - spider-dcd 期望 httpx `<0.28`，当前为 0.28.1。

## 5. 明确边界

- 本批交付的是后端执行、验证、中间 AST JSON 和测试 API，不是正式产品 UI。
- DOCX/PDF/HTML/PPTX 等正式输出、重开/渲染 QA、下载权限和 Manifest 归批次 6。
- 完整 interpret/inspect/bind/plan/execute/verify/repair/deliver 有界 Loop 归批次 5。
- 长文档、并发、故障注入和服务器压力门归批次 8。
- MinerU Hyper high/device 既有待办不影响本批复用当前 Phase 4A 默认解析链。
