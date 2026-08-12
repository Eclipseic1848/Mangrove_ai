# ADR-0002：原始制品不可变，按任务隔离保存

- 状态：已采纳
- 日期：2026-07-19
- 决策来源：plan.md 第 2.2/6.2/12 节；产品决策 4B

## 背景

清洗、转换、隔离、去重都必须可审计、可复跑。若原始数据可被覆盖，则任何清洗结果都无法追溯到原始证据，"正确性"无从验证。

## 决策

- 每次获取都登记 `RawArtifact`（artifact_id/source_id/task_id/uri/sha256/storage_path 等），写入后**不可变**。
- 原始制品按任务目录隔离：`downloads/<task_id>/raw/<artifact_id>.*`。
- 保留策略（4B）：原始 30 天、中间 7 天、输出 90 天，均可配置。
- 任何清洗结果可通过 `RecordEnvelope.meta.artifact_id` + `position` 追溯到原始制品与位置。
- 派生关系（解压/下载/转码）用 `parent_artifact_id` 链接。
- Manifest、日志、trace 不得包含 Cookie/Token/密码/完整授权头。

## 后果

- 正面：100% 血缘覆盖、确定性复跑、复查有据。
- 负面：存储成本随任务与保留期增长；2C 规模（GB/千万行）下需关注磁盘配额与清理策略。
- 实现：`ArtifactStore` 负责落盘、哈希、Manifest 生成；state 只存引用不存数据。

## 相关

- [[adr-0001-data-prep-mode]]
- [[adr-0004-read-only-connectors]]
