# ADR-0034：历史 Candidate 缺少 RuntimeAssignment 时追加窄重验权威

- 状态：`accepted`，用户于 2026-08-25 确认六项边界
- 日期：2026-08-25
- 关联：[ADR-0033](0033-candidate-reverification-and-verifier-ruleset.md)
- 规格：[历史候选重验权威恢复规格](../plans/2026-08-25-historical-reverification-authority-recovery-spec.md)

RuntimeRouting 上线前形成的 Candidate 可能合法缺少 `RuntimeAssignment`，但回填普通 Assignment
会把当前恢复决定伪装成任务创建时的路由事实，直接忽略缺口又会放行新故障或数据损坏。因此，
CandidateVerification 追加不可变 `HistoricalReverificationAuthority`：只有 TaskOwner 可以基于
迁移前时间边界和当前仍一致的 Task、Run、Candidate、前序报告及连接摘要，为精确
`semantic_inconclusive_reverification` 用途记录当前授权。

该权威只在 CandidateVerification 的 `inspect_reverification` / `request_reverification` 接缝解析；
它不是 RuntimeAssignment、Provider Grant、Pi resume、新 revision 或 Delivery 发布权。Offer
只读返回精确 Evidence hash；Owner 写命令必须双重确认“不补造历史”和“仅用于重验”。生产迁移、
真实 authority、Provider 调用和正式发布继续是相互独立的人工门。

未采用回填 RuntimeAssignment、缺记录直接放行、创建新 revision 或重跑 Pi。后果是新任务漏写
Assignment、证据漂移、跨 Owner 操作或用途不符仍失败关闭；历史恢复需要独立追加式 Schema、
Worker 二次复核和更严格的迁移/浏览器证据。
