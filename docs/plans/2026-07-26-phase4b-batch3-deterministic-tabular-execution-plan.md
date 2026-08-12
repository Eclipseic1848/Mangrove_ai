# Phase 4B 批次 3：确定性表格执行实施方案

> 日期：2026-07-26
>
> 实施分支：`v0.0.6`（开发分支，未封板、无同名标签）
>
> 上游前置：批次 1 `SemanticTaskPlan`、批次 2 `BoundPlan` 均已确认且哈希一致

## 1. 本批目标

把已经确认的自然语言语义和真实列绑定，编译成不可变 `PhysicalPlan`，再由一个确定性业务引擎
执行过滤、投影、重命名、排序、合并、连接、去重和分组聚合。模型只参与批次 1 的语义理解；
进入本批后不再调用模型猜列、生成自由 SQL或修改计划。

首个不可绕过门禁：

- 结果只能有 1 张表；
- 精确输出“核销工作量天数、工作量费用”两个可见列；
- 精确输出 11 行；
- 每条来源证据的姓名都等于“谢超群”；
- 输出记录血缘覆盖率 100%。

## 2. 范围

### 2.1 本批支持

- 输入：CSV、TSV、XLSX、Parquet、JSON、JSONL；
- 操作：filter、project、rename、sort、union、join、deduplicate、
  group/aggregate；
- 输出：内部 Arrow 表、结果 Parquet、逐输出记录 lineage Parquet；
- 后端测试 API：冻结 PhysicalPlan、执行、查询执行记录；
- 两套资源档：Windows 本机开发档和未来服务器档。

### 2.2 本批不做

- 不做数据库、网页和 HTTP API 的物理绑定；
- 不做 DOCX/PDF 文档提取和编排，它属于批次 4；
- 不做自动修复/重规划 Loop，它属于批次 5；
- 不做正式 XLSX/CSV 下载和前端交付，它属于批次 6/7；
- 不创建新版本分支或标签。

## 3. 成熟工具选型

| 职责 | 工具 | 约束 |
|---|---|---|
| 唯一业务执行引擎 | DuckDB 1.5.5 | 失败即停，不切 Polars/Pandas 继续算 |
| SQL AST 安全检查 | SQLGlot 28.6.0 | 只允许生成的 SELECT AST，禁止 DDL、路径读取和网络 |
| 批次传输/血缘 | PyArrow 22.0.0 | 结果和 sidecar 使用 Parquet + Zstandard |
| 格式适配 | Polars 1.43.0 | 只负责 Parquet/JSON/JSONL 读取，不承担业务兜底 |
| XLSX | openpyxl 3.1.5 | read-only、禁外链；公式只读缓存值，无缓存则停下 |
| 后置 Schema | Pandera 0.32.1 | strict + ordered，拒绝多列、漏列和错序 |
| 控制面 | Pydantic + LangGraph | 强类型、不可变、compile→execute→verify |

## 4. 安全和正确性设计

1. 客户端只能提交计划 ID 和运行档位，不能提交 SQL、文件路径、DSN 或 URL。
2. PhysicalPlan 只包含判别联合定义的白名单步骤；额外字段一律拒绝。
3. 所有来源由服务端按当前用户的 upload ID 解析，并在执行前重算 SHA-256。
4. DuckDB 只注册内存 Arrow 表，不在 SQL 中调用 `read_parquet/read_csv`；
   关闭 external access、扩展自动安装/加载和社区扩展，然后锁定配置。
5. SQL 值使用参数绑定，列名使用 SQLGlot 标识符引用。
6. union 列集合、顺序或类型不同即停；join 按声明基数检查唯一性，禁止未经确认的多对多。
7. 不自动补空列、不自动转换冲突类型、不计算 XLSX 公式、不执行宏。
8. 每个来源行 ID 由“制品哈希 + table_ref + 物理行号”计算；连接和聚合合并来源 ID，
   结果与 lineage 分开落盘。
9. 验证独立重开结果和 lineage，检查可见列、行数、非空、谓词、血缘、Schema 和行账本。
10. 只有 `VerificationReport.status=pass` 才允许被后续批次登记为权威输出。

## 5. 资源档

| 参数 | Windows 本机 | 未来服务器 |
|---|---:|---:|
| 单任务 DuckDB threads | 4 | 8 |
| 单任务内存上限 | 4 GB | 32 GB |
| Arrow 批次行数 | 65,536 | 262,144 |
| 超时 | 120 秒 | 600 秒 |
| 临时空间预算 | 10 GB | 100 GB |

服务器档是按 64 核、512 GB 内存配置的保守起点，不代表已在未来服务器做过压力验收。
4×L20 不参与本批确定性表格计算。

## 6. 实施顺序与退出门

1. 冻结 PhysicalPlan 和能力注册表；
2. 实现六种格式到 Arrow 的只读适配；
3. 实现 DuckDB + SQLGlot 安全执行；
4. 实现稳定行 ID、连接/聚合血缘和数值/行数账本；
5. 实现 Pandera 与独立验证器；
6. 实现 append-only 存储和用户隔离测试 API；
7. 运行六格式 Golden、操作正反门、API 回归和全仓回归；
8. 更新权威计划、AGENTS、handoff 和执行报告。

退出要求：核心“谢超群”门禁、六格式一致性、安全负例、用户隔离和全仓测试全部通过；
零行未知期望、错列、类型冲突、非唯一连接、无缓存公式值均不得产出权威结果。
