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
| **G2** PG-05 收口 | ① Word/Excel 连续 3/3 真实任务（`scripts/verify_pi_runtime_pg05_office.py`/`_pdf.py` 已存在）；② AC-05 独立依赖获取状态机生产迁移 + 用户验收（audit P0-4：「生产数据库迁移未执行」） | 执行完成 | **PASS**：Office 3/3；AC-05 带备份生产迁移、恢复、真实 Docker 探针及用户验收通过 |
| **G3** P0 GateSnapshot + 默认入口切换 | Rollout GateSnapshot、P0 自动阻断、默认切换（失败即回 Legacy，不迁移/覆盖/删除旧任务与既有 Delivery）；切换动作需用户单独确认（`2026-07-30-phase4-d3-delivery-default-state-machine.md`、ADR-0019） | 执行（实现+验收本机可做；**切换本身需用户单独授权**） | **ADMIN_GRAY ACCEPTED**：恢复验收通过；产品目标改为 G4 合格后全用户默认，尚未切换 |
| **G4** 真实外部 Provider 端到端 | 对所有准备投入生产的外部 Provider 做 Pi→Grant→Relay→Provider→Usage Smoke + DNS rebinding/证书生命周期/备份擦除生产安全门（audit §7） | 批准范围为平台共享 DeepSeek/百炼连接和纯合成数据；其他 Provider 不在本轮资格范围 | 工程门已实现；真实请求/生产轮换/最终资格未执行 |
| **G5** 8B Linux/Compose/并发/故障与目标服务器 | 干净镜像、Linux/Compose 部署、服务器并发、目标服务器验收（audit §5 用户明确后置项） | **挂起**：用户目标服务器未就绪（2026-08-20 确认） | 挂起（不执行） |

## 1. 执行顺序

G1 v3 独立正式运行已达到资格阈值，但 Issue #37/#40 尚未更新。G2 的 Office、AC-05
生产迁移与用户验收均已通过。G3 默认切换仍是独立规格、实现与授权门；
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
  DeepSeek V4 Flash 正式运行功能 30/31、安全 5/5，G1 资格通过。
- G2：提交 `31729495` 完成 Office CLI 的 Owner/空值/并发/超时/异常失败关闭加固；18 项定向
  测试通过，真实 Word 与 Excel 各连续 3/3 通过候选格式、源数据内容断言和独立 Verifier。
  AC-05 提交 `235459a3` 完成显式带备份迁移、精确 Schema、并发写锁、备份 SHA 绑定和
  幂等重放；生产源库/恢复点完整、63 张既有表零改写、恢复副本状态机和真实 Docker 探针
  均通过；用户于 2026-08-22 明确确认 G2 验收通过。
- G3：提交 `21dbf11b` 完成 GateSnapshot、累计门、P0 回退、原子 RuntimeAssignment 与分阶段
  Rollout；生产带备份迁移完成。首次生产快照与独立 Approval 已记录，但运行态核验确认 8088
  进程早于 G3 提交启动；修正快照
  `39c168fb4009478fcd731dbe1f5f10d05d8685b5721cbe7bc5302eddc1ab9fa8` 已把运行态门标记
  为失败。8088 随后已重启加载 G3，新活动快照
  `a936510e53eebc2abb04ce984e1fb72821730d0dc1ce9d37760d2c85beec3571` 累计有效合格，
  当前快照的 `admin_gray` 恢复 Approval 已记录并按用户单独授权执行；当前为 `admin_gray`、
  `p0_blocked=false`。管理员 Pi、普通用户/默认 Legacy、跨 Owner 拒绝和 8088 三入口烟测通过；
  用户于 2026-08-22 明确确认本轮恢复验收通过，未进入 `explicit_opt_in` 或 vNext 默认。
- `explicit_opt_in` 切换前曾发现缺少“获准用户”资格门，并创建修复工单 #43。当前产品目标为
  “可用后所有用户默认使用”，因此不再实现 OptInGrant；#43 的原方案已被后续
  决策取代，远端工单状态尚未调整。生产继续保持 `admin_gray`，只有 G4 合格且默认切换再次
  单独授权后，才可进入 `vnext_default`。
- G4 执行范围限定为现有平台共享 DeepSeek 与百炼连接。允许外发范围仅为冻结的
  合成文本/表格，不含用户业务数据、文件路径、身份信息、Secret 或原始工具日志；本轮资格只
  覆盖这两个实际启用的 Provider，后续新增 Provider 必须独立通过同一安全矩阵。
- G4 工程门已实现：Broker/Relay 烟测只记录 `provider_chain_smoke_passed`，不再冒充正式资格；
  正式驱动必须由 Pi 以 standard 权限和普通合成 Owner 走完整 Grant/Relay/Usage 链，并绑定
  干净 Git 提交、冻结清单、1800 秒默认超时和不可覆盖报告。传输矩阵覆盖 DNS 单次解析/IP 固定、
  原 Host/SNI、禁止重定向、正确证书、错域名证书和过期证书；超时保留 `outcome_unknown`。
- Vault 轮换改为两阶段：先停服并启用新旧双代际、重加密，重启服务加载 keyring 后再次停服，再做最终
  重加密并销毁旧代际；最终阶段必须现场确认旧 8088 PID 已停止、Relay 端口不可达、限定目录
  不含旧 key/keyring 材料，且配置的 `data/backups` 目录内全部含 Provider Secret 的数据库
  备份都已证明会随旧代际销毁而失效。最终 G4 只由
  Pi、传输安全、Vault 三份同身份报告汇总产生。外发前按 Provider/连接版本持久化 attempt
  台账，未决或已外发失败状态拒绝自动重放；旧 key 备份使用跨分块边界的流式扫描。定向回归
  74 项通过；尚未真实外发、轮换生产
  密钥、提交或切换默认入口。

## 3. 边界声明

- G1-G3 的完成不自动等于 Phase 4 封板；G4 未完成、G5 挂起期间，不得在任何文档或沟通中把
  「真实外部 Provider 端到端」「8B Linux/服务器验证」表述为已完成。
- G4 已获准对 DeepSeek/百炼使用纯合成数据开工；现有连接状态和 G1 P1 对照仍不替代 G4
  安全矩阵结论。G5 需要用户目标服务器就绪并授权部署验证。
- 默认入口切换（G3 末段）是独立授权点，不得在 G3 前段实现中自动执行。
