# Phase 4B 批次 1 执行报告

> 日期：2026-07-26
>
> 分支：`v0.0.6`（开发分支，未封板、无同名标签）
>
> 范围：后端 STP 语义编译、测试 API 与审计存储；不含正式前端、Binder 和执行器

## 1. 结论

批次 1 已完成，可以进入批次 2 的 Source Inspector + Binder。

本批把用户自然语言先编译成强类型 Semantic Task Plan，再允许后续阶段绑定真实字段和执行。
模型不能生成来源 ID、权限、风险边界或修复预算；逻辑冲突最多修复两次，关键业务歧义只返回
一个最高价值问题，不能猜测后伪装为可执行。

本地 `Qwen3.6-35B-A3B` 在 5 项公开脱敏意图 Golden 上为 **5/5 通过**，覆盖：

- 表格筛选、指定列和单表交付；
- 商务条款逐字摘录并输出 DOCX；
- 多合同证据化比较并输出 DOCX/PDF；
- 表格筛选、排序、去重和 CSV 交付；
- 聚合含义不清时停止并询问用户。

## 2. 已交付

1. 复用成熟组件：Instructor 负责 Pydantic 结构化输出，LangGraph 负责显式有界修复，
   Pydantic/JSON Schema 负责契约和静态校验；没有新增生产依赖，也没有自造平行框架。
2. `CompileRequest` 将可信任务/来源范围与模型语义分离；模型只生成
   `PlanSemanticsDraft`，服务端组装最终 STP。
3. 过滤、投影、结果粒度、合并、操作、内容政策、证据政策、输出和语义后置条件独立表达。
4. 静态门禁覆盖投影与可见列一致、筛选与谓词后置条件一致、单表约束、比较操作、
   聚合/粒度歧义和外部调用确认。
5. 摘要由确定性代码生成，不让模型二次改写计划。
6. SQLite 新增只追加的 `semantic_plan_revisions`；保存用户、revision、模型、
   提示词版本/hash、计划 hash、诊断和澄清，禁止原地覆盖且按用户隔离。
7. 新增后端测试 API：编译、列出 revision、读取指定 revision、根据一次用户补充创建下一 revision。
8. 外部 Provider 未确认时不解析连接配置、不调用模型；默认本地/LAN。
9. Phase 4A `ExtractionSpec` 通过适配器进入 STP；旧契约没有保存的筛选、粒度或聚合语义
   会显式进入歧义，不伪造。
10. 导出 `CompileRequest`、`PlanSemanticsDraft`、`CompileResult` 三份 JSON Schema。

## 3. 验证证据

- 本地模型 Golden：**5/5 通过**；版本化结果：
  `phase4b-batch1-results/model-eval.json`。
- 批次 1 定向回归：编译器、适配器、API、Schema 均通过。
- Phase 4B 与数据任务相关回归：**65 passed**。
- 最终仓库级后端回归：**868 passed、4 skipped、0 failed**。
- Python 编译检查和 `git diff --check` 通过。
- `pip check` 仍为开发前已登记的 3 项冲突，本批没有新增冲突：
  缺少 `types-pytz`、`crawl4ai` 与 `lxml` 版本约束不符、`spider-dcd` 与 `httpx`
  版本约束不符。

## 4. 明确边界

批次 1 只证明系统能够生成、校验、审计逻辑计划，不代表已经操作真实数据。

以下仍未实现，必须继续保留为待办：

- 批次 2：来源格式、真实 Schema、表/列/章节检查，以及业务概念到物理字段的 Binder；
- 批次 3：Capability Registry、Physical Plan 和确定性表格执行器；
- 批次 4：文档摘录、比较、核查和多格式编排执行；
- 批次 5：真正的 execute → verify → bounded repair Loop；
- 批次 6–8：输出器矩阵、正式前端、性能/安全封板。

因此，当前不能宣称 Phase 4B 已完成，也不能把“计划 ready”解释为“结果已经正确生成”。

## 5. 下一步

从批次 2 开始：先检查实际来源并产生候选绑定证据，只有高置信、可验证的绑定才能进入
Bound Plan；绑定失败时继续沿用“一次只问一个最高价值问题”的交互约束。
