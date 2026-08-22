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
| **G1** 30 项泛化集 | ≥30 个未参与调优任务（文档/PDF/Excel/CSV/复合来源/模糊目标/多输出格式）；≥1/3 同义/口语/省略/顺序变化；≥1/3 相似表/章节/冲突来源；运行前冻结夹具哈希/GoalContract/Verifier；正式交付正确率 ≥90%，安全/权限/用户隔离/禁止项/失败不冒充成功 100%（`2026-07-29-agentic-runtime-vnext-evaluation-spec.md` §5） | 本地正式运行完成 | **QUALIFIED**：功能 30/31（96.8%）、安全 5/5（100%）；尚未更新远端工单 |
| **G2** PG-05 收口 | ① Word/Excel 连续 3/3 真实任务（`scripts/verify_pi_runtime_pg05_office.py`/`_pdf.py` 已存在）；② AC-05 独立依赖获取状态机生产迁移 + 用户验收（audit P0-4：「生产数据库迁移未执行」） | 执行（本机可真实完成） | **工程门通过，待用户验收结论**：Office 3/3；AC-05 带备份生产迁移、恢复与真实 Docker 探针通过 |
| **G3** P0 GateSnapshot + 默认入口切换 | Rollout GateSnapshot、P0 自动阻断、默认切换（失败即回 Legacy，不迁移/覆盖/删除旧任务与既有 Delivery）；切换动作需用户单独确认（`2026-07-30-phase4-d3-delivery-default-state-machine.md`、ADR-0019） | 执行（实现+验收本机可做；**切换本身需用户单独授权**） | 未开始 |
| **G4** 真实外部 Provider 端到端 | 真实 DeepSeek/Qwen/OpenAI/Anthropic/Gemini/Kimi/智谱 Key 做 Pi→Grant→Relay→Provider→Usage Smoke + DNS rebinding/证书生命周期/备份擦除生产安全门（audit §7） | 平台已有可用 Qwen/DeepSeek 连接；用户授权本地模型不足时用于 G1 正式集，但 G4 安全矩阵未授权开工 | 未开始 |
| **G5** 8B Linux/Compose/并发/故障与目标服务器 | 干净镜像、Linux/Compose 部署、服务器并发、目标服务器验收（audit §5 用户明确后置项） | **挂起**：用户目标服务器未就绪（2026-08-20 确认） | 挂起（不执行） |

## 1. 执行顺序

G1 v3 独立正式运行已达到资格阈值，但 Issue #37/#40 尚未更新。G2 的 Office 与 AC-05
工程门均已通过，等待用户给出最终验收结论。G3 默认切换仍是独立规格、实现与授权门；
G4/G5 与生产资格审计不得自动进入。

## 2. 进度

- G1：v3 独立正式运行 36 题，DeepSeek V4 Flash 功能 30/31、安全 5/5，
  `qualified=true`；旧 31 项诊断集仍只作为历史诊断证据。执行报告：
  `2026-08-20-g1-generalization-execution-report.md`。
- G1 外部模型 P1 对照：Qwen 3.7 Max 与 DeepSeek V4 Pro 各 0/3（均超 900 秒）；该结果不
  等于 G4 Provider 安全端到端验收。
- #40 当前默认每次尝试超时 1800 秒，可用 `--timeout-seconds` 显式调整；历史 900 秒
  对照数据不因此改写。当前诊断清单只能用 `--diagnostic` 运行，不得计为 G1 正式正确率。
- #40 独立集：36 题（31 功能 + 5 安全）、41 个全新来源、CSV/JSON/XLSX 三种输出，
  transformation 23、similar/conflict 21；资格、来源哈希、源推导期望和强断言离线自检 PASS。
  该结论不代表正式模型运行或 G1 通过。
- G2：提交 `31729495` 完成 Office CLI 的 Owner/空值/并发/超时/异常失败关闭加固；18 项定向
  测试通过，真实 Word 与 Excel 各连续 3/3 通过候选格式、源数据内容断言和独立 Verifier。
  AC-05 提交 `235459a3` 完成显式带备份迁移、精确 Schema、并发写锁、备份 SHA 绑定和
  幂等重放；生产源库/恢复点完整、63 张既有表零改写、恢复副本状态机和真实 Docker 探针
  均通过。G2 等待用户最终验收结论。
- G3：未开始。

## 3. 边界声明

- G1-G3 的完成不自动等于 Phase 4 封板；G4/G5 挂起期间，不得在任何文档或沟通中把
  「真实外部 Provider 端到端」「8B Linux/服务器验证」表述为已完成。
- G4 开工仍需用户单独确认安全矩阵与允许外发的数据范围；现有连接和 G1 P1 对照不替代该
  授权。G5 需要用户目标服务器就绪并授权部署验证。
- 默认入口切换（G3 末段）是独立授权点，不得在 G3 前段实现中自动执行。
