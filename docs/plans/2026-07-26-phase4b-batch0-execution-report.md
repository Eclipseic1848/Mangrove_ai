# Phase 4B 批次 0 执行报告

## 状态

批次 0 的公开脱敏 Golden、确定性评分器、评测 Graph、候选工具/模型 A/B 和建议 ADR
已经落地。当前可进入批次 1；生产能力包尚未实现，运行时默认工具链保持 Phase 4A
当前配置。

## 已完成

- 32 个公开脱敏夹具，覆盖核心表格、文档、图片、压缩包和失败样例；
- “谢超群”映射样例固定为 11 行、2 列、1 表、谓词满足率和证据覆盖率 100%；
- Graph 状态只保存制品引用和摘要，候选结果、Manifest、ToolResult、VerificationReport
  分文件保存；
- DuckDB、Polars、Pandas 的筛选/投影/合并/聚合及两类负例；
- 10 万/100 万行正确性和本机耗时；
- MarkItDown 与隔离 Docling 的核心格式兼容性；
- PDF/DOCX 商务条款摘录、DOCX/PDF 输出、两版合同差异；
- 损坏、加密、不支持格式和超时门；
- 本地 Qwen、DeepSeek、百炼 Qwen 的同题确定性评测；
- MinerU/Paddle 三服务健康检查与 MinerU Hyper 失败复现。

## 关键事实

- 表格候选全部正确；100 万行本机耗时 Polars 3 ms、Pandas 9 ms、DuckDB 21 ms。
- MarkItDown 8/8；Docling 6/6，但 Docling 热 PDF 约 16.27 s 且必须隔离。
- 本地 Qwen 与 DeepSeek 均为 12/12；百炼 Qwen 为 5/12，静默漏掉条款子任务。
- 批次 0 原始试点使用本地 `hybrid-engine`，medium/high 共 6/6 报
  `Device string must not be empty`，属于服务端 device 配置问题。
- MinerU pipeline 与 Paddle 的既有 17 份/85 页全量 A/B 仍有效。

## 2026-07-26 后续复测

服务新增/恢复 `hybrid-http-client` 路径后，使用同一 3 份/15 页试点复测：

- Paddle `/layout-parsing`：3/3 成功，字段召回 88.9%、行召回 95.8%、
  表格行召回 100%、综合分 0.950895；
- MinerU `hybrid-http-client + medium`：3/3 成功，字段召回 100%、行召回 60%、
  表格行召回 0%、综合分 0.740017；
- MinerU `hybrid-http-client + high`：接口 3/3 返回完成，但 15 页均无正文、区块和表格，
  综合分 0；任务 `29ac7b7d-d659-4614-9c4a-a2039496db5a` 的服务原始
  `model_output` 为 5 个空数组；
- 本地 `hybrid-engine` 的 device 错误未解除。

因此只把 Hyper medium 从“不可调用”调整为“实验候选”，不改变 MinerU pipeline +
Paddle fallback 的当前默认路线。完整问题和下一步见
`2026-07-26-pre-phase4b-batch1-readiness-review.md`。

## 边界

本批产物是评测 Harness，不是面向用户的生产执行链。批次 1 才开始语义编译器、
Source Binder 和生产 STP。Tika、LibreOffice、Pandoc 以及复杂视觉输出链仍需服务器
sidecar PoC；本机未验证的能力不标记为支持。

详细建议见 `docs/adr/0013-phase4b-batch0-tool-selection.md`。

## 验证

- 批次 0 定向测试：4 passed；
- 全仓后端：852 passed、4 skipped、0 failed（164.03 秒）；
- 前端：TypeScript 检查与 Vite 生产构建通过；
- 连续两次生成 Golden 的 manifest SHA-256 完全一致；
- 生成的 DOCX/PDF 连续两次哈希一致且可重新打开。
