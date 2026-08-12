# Phase 4B 批次 2：Source Inspector + Binder 执行报告

> 日期：2026-07-26
>
> 分支：`v0.0.6`（开发分支，未封板、无同名标签）
>
> 结论：**批次 2 后端范围已完成；Phase 4B 批次 3–8 尚未完成**

## 1. 本批交付

- 将 `Binding` 升级为结构化一对多 `BindingTarget[]`，`BoundPlan` 升级为
  `spec_version=2`，保存输入哈希、检查报告哈希、Binder/阈值版本和不可变 canonical hash。
- 新增 `SourceInspectionReport`、表/列 Profile、文档目标、候选分项评分、绑定结果和溯源契约，
  并导出 JSON Schema。
- 表格 Inspector 支持 CSV、TSV、XLSX、Parquet、JSON、JSONL：
  - CSV/TSV 流式读取并最多采样 200 行；
  - XLSX 使用 openpyxl `read_only=True`；
  - Parquet 使用 PyArrow metadata 与首批 RecordBatch；
  - 重复表头保留独立列位置，不通过自动改名掩盖歧义；
  - 电话、邮箱等样本在持久化前脱敏。
- 文档 Inspector 复用 Phase 4A `DocumentElement`，生成章节、段落、单元格的稳定目标；
  缺 bbox/结构位置或要求复核的目标不能自动绑定。
- Binder 使用 RapidFuzz 候选召回、现有本地/LAN rerank 和 SciPy Hungarian 全局分配：
  - 同一语义可跨多个文件、表或章节绑定多个真实目标；
  - 每张表都必须单独满足必需字段，不能用其他文件的成功掩盖缺列；
  - 重复/近似/弱证据目标停止并一次只问一个问题；
  - 用户只能选择服务端已生成的候选 physical ref；
  - 多表歧义使用“语义引用 + 表组”标识逐次解决，不覆盖已确认的其他表。
- 新增独立 LangGraph `inspect_sources → bind_sources`，本批不执行任何数据变换。
- 新增用户隔离、append-only 的 inspection/binding revision 存储和测试 API：
  - `POST /api/semantic-plans/{plan_id}/inspect-bind`
  - `GET /api/semantic-plans/{plan_id}/bound-revisions`
  - `GET /api/semantic-plans/{plan_id}/bound-revisions/{revision}`
  - `POST /api/semantic-plans/{plan_id}/bound-revisions`
- 同一用户、上传制品哈希和 Inspector 版本复用不可变检查缓存；revision 只能追加，不能覆盖。

## 2. 成熟工具采用结果

| 能力 | 采用结果 |
|---|---|
| 模糊字段召回 | RapidFuzz 3.14.5 |
| 全局最大权匹配 | SciPy `linear_sum_assignment(maximize=True)` |
| Parquet | PyArrow 22.0.0 |
| XLSX | openpyxl 3.1.5 read-only |
| 二进制类型 | filetype 1.2.0 |
| 文本/弱魔数类型 | Google Magika 0.6.3 作为补充 |
| 语义精排 | 复用现有本地/LAN rerank 客户端 |
| 编排 | LangGraph + Pydantic |

文件识别公开 A/B 为 4 个常见样例：filetype 单独正确 1/4，
filetype + Magika 正确 4/4。Magika 只补充内容类型识别；对应解析器能否安全只读打开仍是最终裁决，
不会让类型模型替代解析器。

原始结果：
`docs/plans/phase4b-batch2-results/filetype-ab.json`。

## 3. 验证结果

### 3.1 公开 Golden

`scripts/run_phase4b_batch2_eval.py`：

- 6/6 通过；
- 自动错误绑定：0；
- 表格：精确表头、多文件一对多、重复表头阻断、第二来源缺列阻断；
- 文档：多章节一对多、弱证据阻断。

原始结果：
`docs/plans/phase4b-batch2-results/golden-results.json`。

### 3.2 自动化测试

- Phase 4B 批次 -1/1/2 定向回归：**40 passed**；
- 全仓后端：**882 passed、4 skipped、0 failed**；
- `git diff --check`：通过。

跳过项仍是需要显式开关的性能/真库测试，不是本批新增失败。

### 3.3 安全门

- 只接受当前用户拥有的 upload ID，不接受客户端本地路径、URL 或自报哈希；
- 他人 plan、binding 和 artifact 返回 404；
- 用户选择必须命中候选白名单；
- 外部模型未进入默认路径；本批默认仅确定性规则及本地/LAN rerank；
- inspection 与 binding revision 不可原地覆盖；
- 解析异常只返回有界诊断，不返回 traceback。

## 4. 仍未完成

以下内容明确属于后续批次，不能因本报告而宣称已完成：

- 批次 3：DuckDB/PyArrow 表格 Physical Plan、执行、对账和 Pandera 验证；
- “谢超群”任务的最终 11 行、2 列、1 表交付验收；
- 批次 4：文档原文摘录、比较、核查和 DOCX/PDF 编排；
- 批次 5：完整 execute → verify → bounded repair Loop；
- 正式前端候选确认、证据定位和外部 OpenAPI 风险弹框；
- 数据库、网页和 HTTP API 的物理 Schema Binder；
- 私有真实业务集、更大文件和服务器 4×L20 压力基准。

公开 Golden 只有 6 个确定性门禁样例，足以证明本批关键控制逻辑，但不能替代未来私有真实业务集
对阈值和覆盖率的扩展评测。

## 5. 下一步

下一批应进入 **Phase 4B 批次 3：能力包注册表与确定性表格执行**。批次 3 必须消费本批
`BoundPlan`，不得重新根据自然语言猜列；任何 unresolved binding 都不能进入执行器。
