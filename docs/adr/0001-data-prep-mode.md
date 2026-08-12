# ADR-0001：数据准备模式为默认主链路

- 状态：已采纳
- 日期：2026-07-19
- 决策来源：plan.md 第 2/3/10 节；产品决策 6B

## 背景

Mangrove v1.2.0 的中心是"采集后生成分析报告"（VOC/摘要/报告模板学习）。新定位是面向开发团队的"数据获取与准备 Agent"，主链路改为：理解需求 -> 数据源与清洗计划 -> 获取 -> 解析 -> 标准化 -> 剖析 -> 确定性清洗 -> 质量校验 -> 输出干净数据 + 隔离数据 + 血缘 + 质量报告。

## 决策

- 新任务**默认走 data_prep 模式**（`DataPrepTaskSpec.mode=data_prep`）。
- 旧分析能力（analyze/VOC/templates/checker 报告评分）从主路由**旁路**，不作为默认路径。
- 旧 `TaskSpec` v1 与旧 LangGraph 图**保留**，仅作 `mode=legacy_analysis` 显式兼容入口。
- 物理删除旧分析代码遵循 plan 13.8 门禁：Git 全仓引用为零、部署入口无引用、新链路 e2e 通过 + 兼容观察期结束后才执行。本专项不做提前删除。

## 后果

- 正面：主链路聚焦"数据怎么来、怎么治理"，不再被分析模块绑架。
- 负面：旧用户若依赖分析报告，需显式选择 legacy_analysis 或在 data_prep 成功后走可选后处理。
- 迁移：现有网页采集器通过 `WebCollectorAdapter` 输出新制品契约，第一阶段不重写其内部实现。

## 后续扩展（2026-07-21）

本 ADR 的 data_prep 默认主链决策保持不变。后续路线在其上增加任务理解、授权边界、数据发现、证据约束抽取和人工复核；合同、招投标、营销等场景必须组合通用能力，不能另建行业专用主图。该扩展尚未在 v0.0.3 实现，详见 [任务驱动数据工作流蓝图](../task-driven-data-workflows.md) 和 [ADR-0007](0007-evidence-bound-semantic-extraction.md)。

## 相关

- [[adr-0002-raw-artifact-immutable]]
- [[adr-0005-output-formats]]
