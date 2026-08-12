# ADR-0005：多格式输出，默认 JSONL + Parquet

- 状态：已采纳
- 日期：2026-07-19
- 决策来源：plan.md 第 6.5 节；产品决策 7C

## 背景

只输出 `data.json` 无法满足程序消费的多样性：API/日志适合 JSONL，分析/数据湖适合 Parquet，人工查看/传统交换适合 CSV/XLSX。

## 决策

- 首版支持格式（7C 全开）：JSONL、Parquet、CSV、TSV、JSON、XLSX、SQLite。
- **默认输出 JSONL + Parquet**；CSV/TSV/JSON/XLSX/SQLite 按用户选择生成。
- 同一份干净数据可一次生成多种格式；格式转换不重新执行清洗。
- 自动选型规则：
  - 未指定 -> JSONL + Parquet；小于阈值时额外生成 JSON 预览。
  - 用户要求 Excel -> 同时保留 JSONL/Parquet 权威数据，XLSX 作查看副本。
  - 含嵌套字段 -> 优先 JSONL/Parquet；CSV 必须明确展开策略。
  - 超大数据 -> 禁止单数组 JSON/XLSX，改用分片 JSONL/Parquet，Manifest 列出所有 part。
- 时间/Decimal/二进制/空值按 Schema 序列化；CSV/XLSX 类型损失必须在质量报告中告警。
- 强制生成：manifest.json、schema.json、quality_report.json、lineage/records.jsonl；存在异常时生成 rejects/*.jsonl。
- pyarrow 缺失时 Parquet 导出优雅降级（告警跳过，仍出 JSONL），不硬阻断任务。
- 实施状态（v0.0.3）：JSONL、Parquet、CSV、TSV、JSON、XLSX 已实现；SQLite 仍是已采纳的后续项，当前导出器不会生成 SQLite 文件。

## 后果

- 正面：覆盖 API/分析/人工/交换四类消费场景。
- 负面：实现复杂度高于单一格式；需维护多序列化器与类型保真策略。
- 依赖：pyarrow（Parquet）、openpyxl（XLSX）、pandas（CSV/TSV/JSON）。

## 相关

- [[adr-0001-data-prep-mode]]
