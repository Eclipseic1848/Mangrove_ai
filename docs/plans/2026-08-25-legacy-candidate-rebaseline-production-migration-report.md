# Legacy Candidate 再基线生产迁移报告

> 状态：`PRODUCTION_MIGRATION_COMPLETED`
>
> 执行日期：2026-08-25
>
> 范围：CandidateVerification `0004` 生产迁移与服务恢复；不含真实 Provider、
> VerificationAttempt 或 Delivery 发布

## 1. 授权与目标

- 用户明确回复“同意”，授权执行 LR-05 的生产 CandidateVerification `0004` 迁移；
- 目标数据库：`data/webui.db`；
- 迁移前记录为 `0001`～`0003`，活动 CandidateVerification Attempt 与活动模型 Grant 均为 0；
- 精确业务对象仍为 `liyi111 / workspace_8363695f133645ac / revision 1 /
  pi_run_c033ae394ae94cf4`，CandidateSet 为
  `2539e5676ba7ae5963d2dc43acc92cb1672a87477f8f07e283bc0e4dfa98a087`。

## 2. 停服与恢复点

- 项目停止脚本先关闭了 5173，但外层 Backend Supervisor 仍把 8088 拉起；现场按项目路径、
  `run_backend_supervisor.bat` 命令行和精确 PID 核验后终止该进程树，没有处理未知进程；
- 随后 8088/5173 均无监听，生产库连续 6 秒的 SHA-256、大小和修改时间完全一致，且无 WAL/SHM；
- 唯一恢复点：
  `data/backups/webui-before-cv10-rebaseline-20260825-235401.db`；
- 恢复点 SHA-256：
  `106bb38f50d523e383e36c0a549188fa389b53265a018152b55f905e9fe35a68`；
- 恢复点未覆盖既有备份，同恢复点重放后 SHA-256 与修改时间均未变化。

## 3. 迁移与数据不变量

- `0004_legacy_candidate_rebaseline` 已登记，DDL SHA-256 为
  `16c93187ba117d7db9fd68d5a6b8e14402b4ede0c8e15b7718508cbed8530e04`，与 UTF-8 SQL 原文一致；
- 迁移前既有 73 张非迁移业务表、10,197 行；按迁移前原列逐表重算的逻辑摘要在恢复点、
  迁移后和重放后三次均为
  `cab734a3f02021d462f93e3d19648ceb336a6bc84b1e2106679ef518474d65ec`；
- 三次逐表行数一致，`integrity_check=ok`，外键违规均为 0；
- `rebaseline_authorization_json` 与 `rebaseline_authorization_hash`、失败关闭 Trigger 和活动链唯一
  索引均已安装，临时旧表已删除；
- 迁移前 36 条旧 Attempt 的两个授权列仍全部为空，系统没有补造或倒填历史授权；
- 迁移后及服务恢复后，活动 CandidateVerification Attempt、活动模型 Grant、目标发布意图和
  正式 Delivery 均为 0。

## 4. 验收探针纠正

第一次迁移后探针错误地期待九个拆分授权列，因此在所有数据不变量已经通过时仍报告失败。
0004 的实际契约是把完整结构化证据冻结在 `rebaseline_authorization_json`，并以
`rebaseline_authorization_hash` 防篡改。按 SQL、领域模型和实际 Schema 建立最小复核后，正确
两列、DDL 摘要、Trigger、旧数据空授权和迁移重放全部通过；这不是生产迁移缺列，也没有执行恢复。

## 5. 服务恢复与当前现场

- 8088 Backend Supervisor 与 5173 Vite 已恢复；
- `scripts/check_dev_services.ps1` 通过：8088 API、同源前端和 `0.0.0.0` 局域网监听均就绪；
- `/api/health` 返回 `{"ok":true,"service":"mangrove-webui"}`；
- 目标仍只有旧
  `legacy_384796d3b628c58d89108c8a4eab586b752bd845b70f2ccd33a2f34e1f260086`：
  `failed + legacy_unversioned`，没有新 Attempt、授权证据、发布意图或 Delivery；
- Owner、Task、revision、Run、CandidateSet、连接版本和模型均未漂移。
- 服务恢复后的只读 Offer 为 `eligible=true`、`reason=legacy_rebaseline`、`blockers=[]`；当前目标
  Ruleset SHA-256 为
  `891b0a5874681f14839d3b322a62f10602486a799db92a688973f811550fa88d`。

## 6. 结论与下一门

生产 0004 迁移达到 `PRODUCTION_MIGRATION_COMPLETED`。本门没有调用 Provider、产生新的模型费用、
创建真实 Attempt 或发布 Delivery。

下一门是 LR-05B / Gate B：TaskOwner 对精确 `liyi111` CandidateSet、当前 Ruleset、冻结连接
`bf02618a-95a9-4749-8e52-e896b4a06078`、连接版本
`d86f365d4241f29c7f16c88c6b684018e02c92a45c9bb652534d72d30715438f`、模型
`Qwen3.6-35B-A3B` 和本次可能产生费用的 Provider 外发单独授权。只允许创建一个
`legacy_rebaseline` Attempt；不重跑 Pi、不修改 Candidate、不创建 revision、不自动发布。
