# Phase 4 剩余门执行计划与状态记录

> status: active
>
> created: 2026-08-20
>
> 权威入口：用户确认方向「Phase 4 剩余门」（性价比第一，2026-08-19/20 决策）；
> 本文件记录各门定义、现状、决策与执行进度。当前产品状态仍以
> [`docs/status/current.md`](../status/current.md) 为唯一滚动台账；本文件只记录
> 剩余门专项的推进状态与证据入口，不重复维护全局状态。

## 0. 门清单与决策

| 门 | 定义（规格出处） | 决策 | 状态 |
|---|---|---|---|
| **G1** 30 项泛化集 | ≥30 个未参与调优任务（文档/PDF/Excel/CSV/复合来源/模糊目标/多输出格式）；≥1/3 同义/口语/省略/顺序变化；≥1/3 相似表/章节/冲突来源；运行前冻结夹具哈希/GoalContract/Verifier；正式交付正确率 ≥90%，安全/权限/用户隔离/禁止项/失败不冒充成功 100%（`2026-07-29-agentic-runtime-vnext-evaluation-spec.md` §5） | 诊断尝试已收口，正式验收未完成 | **NOT QUALIFIED**：19/25 只是候选预检；保留集、构成、正式 Delivery 与安全矩阵均不合规 |
| **G2** PG-05 收口 | ① Word/Excel 连续 3/3 真实任务（`scripts/verify_pi_runtime_pg05_office.py`/`_pdf.py` 已存在）；② AC-05 独立依赖获取状态机生产迁移 + 用户验收（audit P0-4：「生产数据库迁移未执行」） | 执行（本机可真实完成） | 未开始 |
| **G3** P0 GateSnapshot + 默认入口切换 | Rollout GateSnapshot、P0 自动阻断、默认切换（失败即回 Legacy，不迁移/覆盖/删除旧任务与既有 Delivery）；切换动作需用户单独确认（`2026-07-30-phase4-d3-delivery-default-state-machine.md`、ADR-0019） | 执行（实现+验收本机可做；**切换本身需用户单独授权**） | 未开始 |
| **G4** 真实外部 Provider 端到端 | 真实 DeepSeek/Qwen/OpenAI/Anthropic/Gemini/Kimi/智谱 Key 做 Pi→Grant→Relay→Provider→Usage Smoke + DNS rebinding/证书生命周期/备份擦除生产安全门（audit §7） | 平台已有可用 Qwen/DeepSeek 连接；用户授权本地模型不足时用于 G1 正式集，但 G4 安全矩阵未授权开工 | 未开始 |
| **G5** 8B Linux/Compose/并发/故障与目标服务器 | 干净镜像、Linux/Compose 部署、服务器并发、目标服务器验收（audit §5 用户明确后置项） | **挂起**：用户目标服务器未就绪（2026-08-20 确认） | 挂起（不执行） |

## 1. 执行顺序

G1 的诊断尝试已收口，但 Issue #37 正式验收未完成。用户已创建并授权执行 #40；当前
已接通真实 Publisher/QA 计分，加强 D2/D4/X2/X3/F1/F2 源推导断言，并增加正式清单
资格失败关闭。独立评测方已提供 36 题盲保留集和五类安全矩阵并通过离线自检，尚待正式
模型全量运行。
G3 默认切换受 G1 未通过阻断。G2/G4/G5 与生产资格审计均不得自动进入。

## 2. 进度

- G1：31 项诊断夹具；本地路线运行 25 项，19 个候选预检 PASS、6 FAIL、6 NOT_RUN；驱动
  没有执行正式 Delivery Publisher/完整性/QA，正式交付正确率未测得。该集合已参与缺陷修复，
  且两个三分之一构成、安全/权限/隔离矩阵不足，因此不能作为合格保留集。执行报告：
  `2026-08-20-g1-generalization-execution-report.md`。
- G1 外部模型 P1 对照：Qwen 3.7 Max 与 DeepSeek V4 Pro 各 0/3（均超 900 秒）；该结果不
  等于 G4 Provider 安全端到端验收。
- #40 当前默认每次尝试超时 1800 秒，可用 `--timeout-seconds` 显式调整；历史 900 秒
  对照数据不因此改写。当前诊断清单只能用 `--diagnostic` 运行，不得计为 G1 正式正确率。
- #40 独立集：36 题（31 功能 + 5 安全）、41 个全新来源、CSV/JSON/XLSX 三种输出，
  transformation 23、similar/conflict 21；资格、来源哈希、源推导期望和强断言离线自检 PASS。
  该结论不代表正式模型运行或 G1 通过。
- G2/G3：未开始。

## 3. 边界声明

- G1-G3 的完成不自动等于 Phase 4 封板；G4/G5 挂起期间，不得在任何文档或沟通中把
  「真实外部 Provider 端到端」「8B Linux/服务器验证」表述为已完成。
- G4 开工仍需用户单独确认安全矩阵与允许外发的数据范围；现有连接和 G1 P1 对照不替代该
  授权。G5 需要用户目标服务器就绪并授权部署验证。
- 默认入口切换（G3 末段）是独立授权点，不得在 G3 前段实现中自动执行。
