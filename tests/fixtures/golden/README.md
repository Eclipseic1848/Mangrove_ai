# 黄金样例目录（plan 15.1 / Phase 0）

> 脱敏固定样例，用于契约测试、回放测试与格式矩阵回归。
> 相同 RawArtifact + Recipe 的输出哈希必须稳定（plan 15.2 黄金回放）。

## 目录约定

```
golden/
  csv/         # UTF-8/GBK、不同分隔符、引号换行、坏行
  json/        # 对象、数组、JSONL、嵌套、非法行
  excel/       # 多 Sheet、空表头、合并单元格、日期、公式
  pdf/         # 数字文本、表格、扫描件、混合页
  api/         # 页码/offset/cursor/Link Header 分页响应快照
  database/    # 无主键表、复合主键、大字段（SQL dump + 期望结果）
  media/       # 音频、带/不带字幕视频、无声视频
  web/         # 普通网页、JS 页面、验证页、样板噪声（HTML 快照）
  expected/    # 各样例的期望清洗结果与质量结论
```

## 样例规范

- 每个样例目录包含：`input.*`（原始输入）、`expected.json`（期望输出 + 记录账本）、`README.md`（来源说明与脱敏确认）。
- **必须脱敏**：不得包含真实个人数据、凭证、内部 URL。
- 命名：`<序号>-<特征>.<ext>`，如 `01-utf8-comma.csv`、`03-gbk-bad-row.csv`。

## v0.0.3 审计状态

- [x] 目录结构与规范
- [ ] 固定 CSV/Web/PDF/Office/ZIP/HTTP 等输入及 `expected.json` 尚未入库
- [x] 对应格式和安全矩阵已有 pytest 动态确定性样例覆盖

> 当前 v0.0.3 的 203 项常规测试与 2 项显式性能测试并不等于固定黄金语料库已齐全。后续补样例时必须脱敏，并把固定输入、期望记录账本、质量结论和来源说明一起提交；不要只放空目录或二进制输入。
