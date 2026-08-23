# ADR-0031：Provider 资格外发采用独立持久批次台账

- 状态：`accepted`，用户于 2026-08-23 明确确认
- 日期：2026-08-23
- 关联：[ADR-0020](0020-provider-connection-broker-and-credential-isolation.md)、[ADR-0023](0023-platform-model-governance-and-frozen-task-selection.md)

## 决策

正式 Provider 资格执行必须先创建 `ProviderQualificationBatch`，并显式提供批次 ID。唯一
权威 SQLite 台账固定为当前运行账号用户状态目录下的 `.mangrove/g4/qualification-ledger.sqlite3`；
正式命令不接受自定义台账路径，因此台账不会跟随工作树临时目录或连接数据库副本。它冻结
Provider 集合、Manifest、Git 提交、Owner、精确且无凭证的本机 Relay 路径、超时、授权人和
授权原因。连接数据库的 `app_settings` 只保存不含 Secret 的 Ledger ID、数据库路径身份摘要、
单调版本、状态摘要和首批次身份锚点；每次状态转换后必须同步，且外发前再次一致。数据库
副本路径、台账缺失、台账替换或同 Ledger ID 的旧快照必须失败关闭，不能静默创建或回退
历史。只有启用且已审批的超级管理员可以创建批次。

每个 Provider 的 Attempt 必须在外发前以事务写入台账。初始 Attempt 结束为
`passed | failed_after_egress | outcome_unknown`；系统不自动重试，`in_progress` 也不能并发
授权。只有终态失败或结果未知且用户明确确认可能产生重复请求和费用后，才允许同批次增加
一次恢复重试。本轮从旧台账导入的新批次必须登记恰好两份已耗尽报告；权威台账已有历史后
不得再建 initial，也不得重复导入旧报告重置计数。旧报告同时按规范化内容摘要和任务 ID
去重，改换 JSON 排版不能伪装成第二次 Attempt。持久父批次后继在形成独立规格前失败关闭。
幂等键防止重复提交，并发事务保证同一 Provider 集合只有一个活动批次。

如果 Attempt 已落台账但外发前锚点同步失败，系统必须在 Runtime 调用前撤回该 Attempt，恢复
原 Attempt 次数，并把上下文摘要写入恢复审计；不得把本地持久化失败计算成 Provider 请求。
若进程或磁盘异常让自动收口未完成，只允许启用且已审批的超级管理员在同一 Pi 执行锁空闲时
运行 `recover-anchor`：锚点只能前滚一版，或前滚到已审计的两版撤回结果。Provider 已返回后的
终态只前滚锚点，不改变 Attempt 结果，也不触发模型重试。所有正式 Pi 批次共用权威台账旁
的全局执行锁，避免不同 Manifest 并行时把其他批次的进行中 Attempt 误判为可恢复对象。显式
恢复永不删除 `in_progress`：进程已经退出时保留 Attempt 次数并收口为 `outcome_unknown`，之后
仍由用户决定是否承担重复请求和费用风险。只有同一进程明确捕获到外发前同步失败时，才允许
撤回未调用 Runtime 的 Attempt。

最终 G4 判定必须同时验证报告内容和台账的 `passed` 终态、Ledger ID、Batch ID、Manifest、
Provider 集合及 Git 提交。台账只保存脱敏身份摘要、结果和授权事实，不保存 Secret、宿主
绝对路径或原始工具日志。

## 取舍

该设计增加一个必须长期保留的本地 SQLite 文件和显式批次创建步骤，但能防止复制数据库、
切换工作目录、删除临时状态或伪造独立成功报告后重复外发。未采用“把状态放在连接数据库
旁边”，因为数据库副本和临时工作树会让重试计数分叉；也未采用 Runtime 自动重试，因为
超时或断线时无法证明 Provider 未处理请求，重试决定必须留给用户。
