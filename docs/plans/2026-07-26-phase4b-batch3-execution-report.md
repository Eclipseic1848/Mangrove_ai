# Phase 4B 批次 3：确定性表格执行报告

> 日期：2026-07-26
>
> 分支：`v0.0.6`（开发分支，未封板、无同名标签）
>
> 结论：**批次 3 后端范围已完成；Phase 4B 批次 4–8 尚未完成**

## 1. 本批交付

- 新增强类型、不可变 `PhysicalPlan`，操作使用 Pydantic 判别联合，不接收自由 SQL 或客户端路径。
- 新增显式 `CapabilityRegistry`，登记 `table.duckdb@1.0.0` 的格式、操作、资源、
  网络、副作用、确定性和证据能力。
- 新增 `compile_physical_plan()`：
  - 只消费已确认且哈希一致的 STP + BoundPlan + InspectionReport；
  - 按真实 `physical_ref/column_index` 编译，不重新猜列；
  - 不完整或冲突参数返回 `needs_user`。
- 新增 DuckDB 唯一业务执行器：
  - 支持 filter/project/rename/sort/union/join/deduplicate/group/aggregate；
  - SQLGlot 复核 SELECT AST，值参数绑定，标识符安全引用；
  - SQL 不直接读取文件；DuckDB 禁外部访问、禁扩展自动安装/加载并锁定配置；
  - DuckDB 失败不切换 Polars/Pandas 继续执行。
- 六种常见格式接入同一语义：
  - CSV/TSV 按检查报告的物理列序号读取；
  - XLSX 由 openpyxl read-only 读取，公式无缓存值时停止；
  - Parquet/JSON/JSONL 由 Polars 适配后进入 Arrow；
  - 输入哈希变化、跨来源类型冲突和 union Schema 冲突均停止。
- 新增稳定来源行 ID、输出记录 ID 和独立 lineage Parquet：
  - filter/project/sort/rename/union 保留来源；
  - join 合并左右证据；
  - deduplicate 保留同组全部来源证据；
  - aggregate 合并组内全部来源证据。
- `ToolResult.facts.reconciliation` 记录各来源行数、输出行数、输入/输出数值列合计和粒度变化；
  `ExecutionLedger` 记录输入、输出、过滤/去重和字节账本。
- 新增 Pandera strict/ordered Schema 和独立验证器：精确列序、期望行数、未知期望零行、
  table_count、来源谓词、血缘覆盖、行账本及 Parquet 重开均为权威输出门。
- 新增 append-only 存储和后端测试 API：
  - `POST /api/semantic-plans/{plan_id}/physical-plans`
  - `GET /api/semantic-plans/{plan_id}/physical-plans`
  - `POST /api/semantic-plans/{plan_id}/physical-plans/{physical_plan_id}/execute`
  - `GET /api/semantic-plans/{plan_id}/execution-runs/{run_id}`
- 服务端内部结果路径不通过 API 返回；执行时重新检查当前用户对上传制品的所有权。

## 2. 成熟工具采用结果

| 能力 | 实际采用 |
|---|---|
| 业务计算 | DuckDB 1.5.5 |
| 安全 SQL AST | SQLGlot 28.6.0 |
| 数据面与 Parquet | PyArrow 22.0.0 |
| Parquet/JSON/JSONL 格式适配 | Polars 1.43.0 |
| XLSX 只读 | openpyxl 3.1.5 |
| 严格结果 Schema | Pandera 0.32.1 |
| 编排和契约 | LangGraph + Pydantic |

DuckDB、Polars、Pandera 已从批次 0 评测依赖提升到主 `requirements.txt`；
未盲目升级现有包。

## 3. 验证结果

### 3.1 核心门禁和格式门

`tests/test_semantic_table_execution.py`：

- “谢超群”：精确 11 行、2 个可见列、1 表；
- 所有来源行满足姓名谓词；
- 11/11 输出记录都有来源证据，覆盖率 100%；
- CSV、TSV、XLSX、Parquet、JSON、JSONL 六种格式在同一 DuckDB 语义下全部通过；
- rename、sort、union、join、deduplicate、aggregate 路径有覆盖；
- 非唯一 many-to-one join 停止，`fallback_used=false`；
- DDL 和 SQL 直接文件读取被 AST 安全门拒绝。

### 3.2 API 和定向回归

- Phase 4B 批次 -1/0/1/2/3 定向：**56 passed**；
- 新增执行/API 集：**13 passed**；
- API 验证 PhysicalPlan 和执行记录不可变、服务端路径不泄漏。

### 3.3 全仓

- 后端：**895 passed、4 skipped、0 failed**（165.07 秒）；
- `git diff --check`：通过；
- `pip check` 未新增冲突，仍只有已登记的 3 项：
  `types-pytz` 缺失、crawl4ai/lxml 版本约束、spider-dcd/httpx 版本约束。

4 个跳过项仍是需要显式开关的真实 MySQL/PostgreSQL 和大规模性能测试，不是本批失败。

## 4. 两套部署边界

- Windows 本机档已由自动化功能门验证，默认 4 threads / 4 GB / 120 秒。
- 未来服务器档已固化为 8 threads / 32 GB / 600 秒的单 worker 起点；
  服务器 4 worker 并发和大文件压力仍需迁移后实测，不得把配置值冒充性能结论。
- GPU 不参与本批，不会因本机或服务器 GPU 差异改变确定性计算结果。

## 5. 尚未完成

- 批次 4：文档原文摘录、比较、核查和 DOCX/PDF 编排；
- 批次 5：完整有界 repair Loop；
- 批次 6：正式 XLSX/CSV/JSON 等交付、下载权限和输出 QA；
- 批次 7：正式前端计划确认、证据定位和外部 OpenAPI 风险弹框；
- 批次 8：私有真实业务集、未来服务器压力和 Phase 4B 封板。

本批内部 Parquet 结果不能冒充用户正式下载文件；只有验证通过才具备进入批次 6 交付链的资格。
