# AC07-10（#15）Python 表格 Tool 真实治理纵切面 — 验收方案

> 状态：待执行（每阶段逐项授权）
>
> 依据：需求复核 Q1-Q8（全部 A）、设计 D4、任务拆分 S7
>
> 总原则：每阶段先展示计划 → 用户授权 → 执行 → 记录证据；
> 生产库写动作前在线一致性备份；accept-s8-* 样本全程不动（Q8A）。

## 阶段 0：LLM 可用性探测（决定 Q3 口径）

- 探测本地 LLM 连接可用性（沿用 #34/#13 的探测方式）。
- 可用 → 阶段 4/5 走完整真实任务；不可用 → 复用 #34 冻结任务重放 +
  合成 Smoke，执行报告如实记录（同 #13 口径）。
- 授权点：无写动作，探测结果仅记录。

## 阶段 1：注册个人 draft 2.0.0（Q1A）

- `python scripts/prepare_ac07_10_packs.py` dry-run 展示计划（digest/版本/Owner）。
- 授权后 `--apply`：自动备份 `data/webui.db` → OCI push → 目录登记个人
  draft（Owner=liyi）→ 治理 registered 事件。
- 证据记录：备份路径、digest、OCI reference、目录行、registered 事件。

## 阶段 2：验证五步 + Trivy/Syft + 自动晋级 verified；注册 3.0.0 并行（Q2A）

- 2.0.0：发起 ValidationRun（合成 Smoke → 授权真实任务/冻结重放 →
  失败关闭 → 权限 → 清理）→ 供应链证据（Trivy/Syft）→ `maybe_promote`
  自动晋级 verified（事件 + 投影）。
- `--apply` 注册 3.0.0（同样路径）→ 并行验证 → verified。
- 证据记录：两条 ValidationRun、两条供应链证据（passed、库时间、SBOM
  hash）、两条 promoted_to_verified 事件、投影核对。

## 阶段 3：平台发布 2.0.0 与 3.0.0（Q1A/Q2A）

- 平台候选 → 脱敏快照（新 digest）→ Cosign 签名 → 平台六步验证 →
  `admin_gray` 发布（2.0.0 与 3.0.0 各自独立链）。
- 重复请求与重启幂等（AC6）：发布命令同幂等键重放 → already_applied；
  8088 重启后重放 → 仍 already_applied；不覆盖旧证据/旧版本。
- 证据记录：候选事件、快照 digest、签名 digest/公钥、六步证据、
  platform_published 事件、耗时。

## 阶段 4：管理员任务选择 + 回滚指针 + deprecated + 历史冻结恢复（AC4/AC5）

- 管理员新任务选择列表出现 2.0.0/3.0.0（recommended 标记/置顶）。
- 真实装载（按阶段 0 口径）：完整任务或冻结重放；结果/进度/Candidate/
  Delivery 边界正确。
- rollback 推荐指针 3.0.0 → 2.0.0（recommendation_changed 事件、列表
  标记切换）。
- deprecate 2.0.0 → 新任务不可选（列表过滤 + 冻结 409）→ 历史冻结
  任务恢复装载成功（#13 A5 路径）。
- 证据记录：选择列表快照、装载结果、指针切换、弃用事件、恢复装载结果。

## 阶段 5：revoked + 跨用户拒绝 + 篡改演示（AC5/Q5A/Q6A）

- revoke 2.0.0 → 历史恢复装载也被拒。
- 跨用户拒绝：另一真实普通用户对该能力 403/404（不新注册用户）。
- 篡改演示（blob 级备份安全原则）：
  1. 备份平台 Layout 目标 digest 的主体 manifest blob 到验收目录；
  2. 篡改一个字节 → 装载 409 fail-closed → 自动隔离事件（S1 钩子）；
  3. restore 命令（恢复复查链：Trivy 时效 + 签名 + 证据）解除隔离；
  4. 逐字节还原 blob → `verify_local` 复验通过 → 再次装载成功。
  全程不触碰发布事件证据；演示后主 Layout 与发布证据完全一致。
- 证据记录：拒绝矩阵、隔离事件（actor=system）、restore 事件、复验结果。

## 阶段 6：真实 risk_accept applied 链 + 惰性到期 + 零残留（Q4A/AC7）

- 人工隔离 3.0.0（证据 PASSED）→ risk_accept（finding_ref 实引本包
  验证运行，30 天）→ applied → 投影 eligible。
- 惰性到期验证：把接受事件到期时间改为过去（验收专用测试动作，记录后
  恢复原值）→ 投影重新 quarantined（零新事件）。
- 手动重扫演示（S2/S3）：`POST /supply-chain-rescan`（BLOCKED 夹具或
  真实重扫）→ 证据追加 + 自动隔离/不隔离按矩阵；恢复后重扫通过。
- 零残留核验：容器、网络、挂载、临时 Registry、Lease 全部清零。
- 证据记录：risk_accept 事件、到期投影、重扫事件/证据行、零残留清单、
  耗时、扫描数据库时间、SBOM hash、签名验证结果。

## 阶段 7：收口

- Issue AC1-AC7 逐条对照记录进执行报告。
- 文档同步：docs/status/current.md → handoff.md 精简。
- 发布链（逐项授权）：codex 分支 → 提交 → 推送 → PR → 合并。
- 明确不做的：accept-s8-* 样本处理、普通用户开放、AC-06 切换（#17）、
  定时重扫调度器。
