# G1 30 项泛化集夹具与候选预检结果索引

> status: diagnostic only; not a qualifying held-out evaluation
>
> frozen: 2026-08-20
>
> 机器可执行权威：`evals/generalization-g1/fixtures.json`、`assertions.py`、`run_g1.py`

## 1. 冻结身份

| 项目 | SHA-256 / identity |
|---|---|
| fixture | `0766fc026b94c7f7e9dbddea8bcab0bafccb3b3dbf981f0b4484d7c4de5fc003` |
| GoalContracts | `9c4a5b0e49856b2ca6a3ec792a61565b31334d41852a032da3b5cfaff909b3e0` |
| CandidateVerifier | `d75d9c531efcea0ee31d542fc215d948c99bfae822e0fc65caf8239dab3abca8` |
| assertions | `6a5f0a939681723065d5f363a748401efe98f10c6665ae4e02235bdb3f4557b7` |
| Agentic Runtime | `aeba9378805d5d9dd2486b5e50ed545a95ffc42a7151dd5dd45c6b2bd6aaa98d` |
| Delivery qualification | `9f3f01779616d8637cac07414446631a4616342e4b16adbd2eb7ecf6002fa289` |
| evaluation driver | `ee4df77e0dc37a40229e9c19314b41e2224094dcb774a85063df21e945e814f5` |
| Git commit | `37cda72a91750b575cbca0dd971c3ed89230a7c2` |
| code freeze | `b5e36db56ffd12c6481f612c891941f9561c8003c974692891542dcb4fd6b956` |

以上是 #40 当前 WIP 的冻结身份，能够检测未提交的 Runtime/Verifier/Publisher/QA/
清单资格校验代码变化。清单已显式标记 `diagnostic_only`；正式模式会拒绝运行，只有
`--diagnostic` 可以将它用于开发回归。旧运行报告
使用较弱的四项冻结快照，与当前身份不一致，驱动会拒绝跨快照重放；表中旧结果因此只能作为
诊断历史。来源对象仍按 `.meta` 的内容 SHA-256 命中并重算对象哈希。运行报告与候选制品保存
在 gitignored 的 `evals/generalization-g1/runs/`，不进入公开仓库。

## 2. 夹具与本地模型候选预检结果

状态语义：`PASS` 只表示至少一次完整 attempt 同时满足 Candidate Verifier 与当前断言；`FAIL`
表示三次完整 attempt 均未双过；`NOT_RUN` 表示停止的剩余批次。表中结果来自未调用 Delivery
Publisher 的旧驱动，部分断言也只有结构强度，因此任何旧 `PASS` 都不是正式 Delivery 通过。
当前驱动虽已接通 Publisher，但尚未对新的独立盲保留集运行。

| ID | 类别 | 陷阱 | 目标摘要 | 状态 |
|---|---|---|---|---|
| P1 | PDF | base | 工作量核算表明细行转 CSV | FAIL |
| P2 | PDF | paraphrase | 按人员汇总工作量 | NOT_RUN |
| P3 | PDF | conflict | 多来源中只取第二张表 | NOT_RUN |
| P4 | PDF | compound | 合并三份核算表 | NOT_RUN |
| P5 | PDF | paraphrase | 提取指定页表格 | NOT_RUN |
| P6 | PDF | similar | 提取第一张完整单据 | NOT_RUN |
| P7 | PDF | similar | 列出全部单据 | NOT_RUN |
| P8 | PDF | base | 功能规范模块清单 | FAIL |
| P9 | PDF | base | 规划事项及负责人 | FAIL |
| P10 | PDF | similar | 小程序题库练习步骤 | FAIL |
| D1 | DOCX | similar | 招标核心指标表 | PASS |
| D2 | DOCX | base | 平台功能需求模块 | PASS |
| D3 | DOCX | table | 官网区域元素说明 | PASS |
| D4 | DOCX | table | 智能体、场景数与价值 | PASS |
| D5 | DOCX | similar | 指定核心指标及目标值 | PASS |
| D6 | DOCX | paraphrase | 交付周期与验收要求 | PASS |
| X1 | XLSX | similar | 指定 Sheet、月份和两列 | PASS |
| X2 | XLSX | similar | 场景规划表应用场景 | PASS |
| X3 | XLSX | base | 2026 年新增场景 | PASS |
| X4 | XLSX | base | 指定牵头单位工作事项 | PASS |
| C1 | CSV | base | 按部门汇总金额 | PASS |
| C2 | CSV | paraphrase | 全表金额合计 | PASS |
| C3 | CSV | base | 金额阈值过滤 | PASS |
| C4 | CSV | garbled | 乱码表头归一与按列求和 | PASS |
| C5 | CSV | compound | 两表按 name 合并 | PASS |
| M1 | compound | compound, paraphrase | 两份金额数据总账 | PASS |
| M2 | compound | compound | 两份文档用途与模块 | PASS |
| M3 | compound | compound | 三张 CSV 全行全列合并 | PASS |
| F1 | fuzzy | fuzzy | 文档中钱与时间信息 | FAIL |
| F2 | fuzzy | fuzzy | 2026 年建设计划汇总 | PASS |
| S1 | security | forbidden | 模块清单且排除审签信息 | FAIL |

## 3. 汇总

- 已运行 25 项：19 个候选预检 PASS、6 个候选预检 FAIL，预检率 `76.0%`；
- 未运行 6 项：P2-P7；
- 当前 attempt 即使未运行项全部预检通过，上限也只有 `25/31 = 80.6%`；
- 正式交付正确率未测得；当前集合仅 5 项 `paraphrase`、7 项 `similar` + 1 项 `conflict`，
  也没有满足两个三分之一构成门；
- 该集合已参与缺陷发现和修复，不再具备盲保留集资格。G1 没有通过证据，Issue #37 未完成。

本清单只记录夹具和结果索引。失败原因、外部模型对照、代码修复与证据边界见
`2026-08-20-g1-generalization-execution-report.md`。
