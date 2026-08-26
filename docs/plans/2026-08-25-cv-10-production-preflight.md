# CV-10 生产迁移、真实同 Run 重验与 Owner 验收预检

> 状态：`GATE_A_COMPLETED`
>
> 现场核验：2026-08-25
>
> 范围：只读预检；未迁移生产数据库、未调用 Provider、未创建重验 Attempt、未发布 Delivery

> 后续：Gate A 已按用户明确授权执行，生产证据见
> `docs/plans/2026-08-25-cv-10-gate-a-production-migration-report.md`；本文保留为迁移前快照。

## 1. 已验证事实

### 1.1 代码与运行实例

- `HEAD` 与 `origin/main` 均为
  `7efaf2fd78a8f1df0b86929b19e27cf0a7b5ca03`。
- 当前提交时间为 2026-08-25 00:38:51（本机时区）。
- 8088 后端进程启动于 2026-08-25 00:16:26，早于当前提交，因而不能证明其加载了当前
  CV-09 安全基线。
- 当前 Ruleset 只读解析成功：
  - `verifier_ruleset_hash`：
    `891b0a5874681f14839d3b322a62f10602486a799db92a688973f811550fa88d`
  - `verifier_source_hash`：
    `931b27f93d85e7718e1778e663c3414cea4c98193103b34ab7ea1c8df78ca79f`
  - `verifier_execution_identity_hash`：
    `e4a6b0ea005c982d2012d5451b52f1ba90e1d5ef61e9df33441451dba24e574b`

### 1.2 生产数据库

- 数据库：`data/webui.db`；现场大小 29,016,064 字节；SHA-256：
  `76d231765599a3dc73184b5ce60bcfd1837f39113f762ae6cb7a700e0c64e6bf`。
- 71 张业务表、10,313 行；`integrity_check=ok`，外键违规为 0。
- 在线观察的 3 秒窗口内数据库 SHA-256 和修改时间发生变化；8088 的常驻 worker 仍在写库，
  生产迁移不能与当前服务并行。
- CandidateVerification `0001/0002` 尚未登记或安装。
- 现有 35 条 legacy VerificationReport 将导入为不可变 Attempt：27 passed、4 failed、
  4 inconclusive。
- CV-07 的 `request_idempotency_hash` 空列与唯一索引已经存在，非空记录为 0；显式迁移将
  接管该 Schema 偏差，不回滚已有列或索引。
- `0001` SQL SHA-256 为
  `72a6ec05bd581a4b9f97d5b56923af34a03c4d49eb89bc4bbb0b1021989ed6f3`。
- 已对当前在线库的一致性 SQLite 快照执行临时克隆迁移演练：
  - 快照仍为 71 张原表、10,313 行，迁移前完整性 `ok`、外键违规 0；
  - `0001/0002` 均成功登记，35 条 legacy Attempt 状态分布与生产只读盘点一致；
  - 原 71 张表的逐表行数和逻辑内容指纹均未变化；
  - 两条迁移记录都绑定同一个恢复点 SHA-256，迁移重放返回同一恢复点；
  - 迁移后完整性 `ok`、外键违规 0，发布幂等列和索引完整；
  - 临时克隆与恢复点已清理，没有写入生产数据库或留下临时目录。

### 1.3 Gate 与目标 Candidate

- 当前 rollout 为 `vnext_default`、`p0_blocked=false`；活动 GateSnapshot 为
  `e366bbfc3999917861b2464a3ae2b63886165aa5747074b26c956b3291cfd3c8`，7 项检查均通过。
- Owner：`liyi111`（普通用户）。
- Task：`workspace_8363695f133645ac`；revision 1；Run：
  `pi_run_c033ae394ae94cf4`。
- CandidateSet：
  `2539e5676ba7ae5963d2dc43acc92cb1672a87477f8f07e283bc0e4dfa98a087`。
- 两个 Candidate 原件仍存在并逐字节匹配冻结哈希：
  - `result.csv`，48 字节，SHA-256
    `e4e061e8f234616983afd205b64cb8e4ad1b833779d5901544f60d67cfdb6292`；
  - `result.json`，93 字节，SHA-256
    `10e6722eff9a43e4d73728eb51b6659faa6c5e953172ff507fbb7ee6fecc5e65`。
- 旧报告为 `failed`：`artifact_set` 通过，`artifact_count` 失败；没有 Delivery、发布意图、
  活动重验 Attempt 或活动 Grant。
- Runtime 中另有 2 条 2026-07-30/31 的 legacy `queued` 孤儿记录：均无 `run_id`，对应
  SemanticTask 已不存在；它们不是当前可执行业务任务，但迁移前仍按历史数据保留，不人工改写。
- 任务冻结于旧 GateSnapshot
  `54cae0d7eb3267b2051697e24f0bd963aec99b7d0d990cd925c12c561d119969`；当前 Gate 是修复
  多输出 Verifier 后的新快照。这不会改写 TaskRevision 或 CandidateSet。

### 1.4 Provider 外发包

- 冻结连接：`bf02618a-95a9-4749-8e52-e896b4a06078`，版本
  `d86f365d4241f29c7f16c88c6b684018e02c92a45c9bb652534d72d30715438f`。
- 模型：`Qwen3.6-35B-A3B`；API 格式：`openai_chat_completions`；连接状态：verified；
  locality：`managed_private`。
- 端点为维护者局域网内的受管私有服务；具体地址和 Secret 不写入仓库。
- 重验可能外发的类别：任务目标与交付契约、一个上传来源的验证所需内容、两个 Candidate
  内容及其 Manifest/哈希、Verifier 语义裁判提示与必要证据片段。
- 不外发：Secret 明文、宿主绝对路径、其他 Owner 数据、旧工具原始日志、无关任务内容。
- 当前连接没有价格台账，潜在费用无法从生产库可靠量化；每次 Provider Attempt 仍必须按
  “可能产生费用”处理。
- 若调用超时、响应丢失或持久化结果不确定，Attempt 收口为 `outcome_unknown`，不得自动
  重发；再次调用必须重新确认重复费用并创建后继 Attempt。

## 2. 基于代码的推断

- 后端进程早于当前 HEAD，因此最安全的执行顺序是先受控停止 8088，再对静止数据库创建
  一致性备份并迁移，随后按当前 HEAD 重启。仅凭进程命令行无法反推出其实际加载提交。
- 目标旧报告绑定的 GateSnapshot 早于多输出修复；当前 Ruleset 与旧 legacy 规则身份不同，
  符合 `ruleset_changed` 重验原因，但必须由正式迁移导入旧 Attempt 后再由产品服务判定。
- 已有 CV-07 空列/索引最可能来自旧 Repository 的静默 DDL；目前没有审计证据可以确认具体
  写入进程和时间，因此不把该原因表述为已验证事实。

## 3. Gate A：待确认的生产迁移包

确认后才执行以下连续动作：

1. 使用仓库 `scripts/stop_dev_processes.ps1` 的项目路径与 8088 端口限定，受控停止后端监督器
   及其子进程；不停止 5173，不处理无法验证为本项目的进程。随后确认 8088 无监听、没有活动
   Attempt/Grant/运行任务，并以连续只读快照证明数据库不再变化。
2. 以当时静止数据库创建唯一恢复点：
   `data/backups/webui-before-cv10-20260825-004543.db`；若执行时间变化，使用新的秒级时间戳，
   不复用或覆盖既有文件。
3. 记录源库与备份 SHA-256、完整性、外键、表/行指纹；只有一致才执行正式
   `migrate_candidate_verification`。
4. 验证 `0001/0002` 迁移记录、完整 Schema、35 条 legacy Attempt、原 71 张表逻辑数据零改写、
   发布幂等 Schema 接管、重放幂等、完整性与外键。
5. 使用仓库 `scripts/run_backend_supervisor.bat` 与项目 Python 3.13 重新启动双层监督器，
   再以 `scripts/check_dev_services.ps1` 核对 8088 API、同源前端和局域网监听，并核对新进程
   启动时间、Gate 与任务投影。
6. 恢复触发条件：迁移或重启复核任一关键项失败。恢复时保持 8088 停止，先保留失败库作为
   审计副本，再从本次唯一恢复点恢复，复核 SHA-256/完整性/外键后重启；不覆盖本次恢复点。

## 4. 尚未执行的独立门

- **Gate B：真实重验。** Gate A 完成并展示迁移证据后，Owner 再对本文件 1.3/1.4 的精确
  Candidate、连接、模型、外发类别和未知结果策略作逐 Attempt 确认。确认后仅创建一次
  `ruleset_changed` Attempt，不重跑 Pi、不生成 Candidate、不创建 revision、不发布。
- **Gate C：正式发布。** 只有新 Attempt 确实为 `passed`，并展示报告、QA、Candidate/Attempt
  哈希后，Owner 才能单独确认发布。提前授权或工程测试绿色都不能替代该确认。

## 5. 当前结论

本预检当时只达到 `READY_FOR_GATE_A_CONFIRMATION`；随后 Gate A 已完成，权威结果见生产迁移
报告。当前仍未达到 `LIVE_REVERIFIED`、`LIVE_ACCEPTED`、Provider 资格或生产发布结论。
