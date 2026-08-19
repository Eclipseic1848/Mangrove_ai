# AC07-10（#15）Python 表格 Tool 真实治理纵切面 — 任务拆分

> 状态：已确认
>
> 依据：需求复核 Q1-Q8（全部 A）+ 设计 D1-D8（已确认）
>
> 原则：新代码全部 TDD 先红后绿；既有 #13/#14 机制只复用不重写；生产库写动作
> 逐项授权（Q8A）；每阶段完成后同步 md 文档。

## 任务总览与依赖

```text
S1 装载门自动隔离钩子（代码）
  └→ S2 重扫命令服务方法（代码）
       └→ S3 重扫 admin 端点（代码）
            └→ S4 事件快照与投影一致性（代码/测试）
                 └→ S5 真实注册脚本与冻结夹具（脚本+夹具）
                      └→ S6 双轴审查与回归
                           └→ S7 8088 真实纵切面验收（分阶段授权）
```

S1-S3 相互独立可并行实现，S4 依赖 S1/S2；S5 起为真实链阶段。

## S1 装载门自动隔离钩子（D3.2）

- [src/capability_governance/runtime_gate.py](src/capability_governance/runtime_gate.py)：
  `CapabilityGovernanceRuntimeGate` 增加可选注入
  `auto_quarantine: Callable[[CapabilityPack, str], None] | None = None`；
  四个真实验签失败分支（verify 异常 / 主体 digest / 签名 digest / 公钥不一致）调用；
  「验证器未配置」不调用；默认 None 行为与 #13 完全一致。
- [src/capability_governance/service.py](src/capability_governance/service.py)：
  新方法 `auto_quarantine_for_signature_failure(pack, reason)`——投影复查（已
  quarantined 跳过）→ 写 `eligibility_changed(QUARANTINED)` 事件（快照 = 写入时刻
  投影 + quarantined；幂等键服务端派生 `auto-quarantine:{digest}:{代次}`，代次 =
  该 target 既有隔离事件数 + 1，restore 后再次失败仍可写）。
- [src/api/capability_governance_runtime.py](src/api/capability_governance_runtime.py)：
  `get_runtime_gate` 装配注入真实回调。
- 测试：新文件 `tests/test_auto_quarantine_hook.py`
  （四分支触发 / 配置缺失不触发 / 已隔离跳过 / 默认 None 回归 / 代次幂等键）。
- 验证：新测试红→绿；#13 装载门与冻结拦截既有测试零回退。

## S2 重扫命令服务方法（D3.1）

- [src/capability_governance/service.py](src/capability_governance/service.py)：
  `rescan_supply_chain(actor, pack_ref, reason, idempotency_key)`——仅管理员；目标必须已
  发布平台包（存在 `platform_published` 事件）否则 rejected；物化/采集器经构造
  可选参数注入（默认 None → `rescan_not_configured` 拒绝，不静默跳过）；
  物化 → collect → `save_supply_chain_evidence` 追加新行 → 触发判定
  （新证据 BLOCKED 且投影 ELIGIBLE → 自动隔离事件；BLOCKED 已涵盖全部
  finding 计数语义）；
  返回 `GovernanceCommandOutcome`；幂等键命中先于一切检查（#14 教训）。
- [src/api/capability_governance_runtime.py](src/api/capability_governance_runtime.py)：
  装配共享 `_platform_probe_parts()`（物化 + 采集器绑定 collect 方法，
  与平台六步验证同一实现）+ 单例缓存。
- 测试：新文件 `tests/test_governance_rescan.py`
  （BLOCKED→隔离 / PASSED→不隔离 / 已隔离→只更新证据 / 幂等重放 /
  非 admin / 未发布 409 / 个人包 409 / 未装配拒绝 / 证据追加不覆盖旧行 /
  崩溃窗口补写 / 绑定 collect 方法契约）。
- 验证：新测试红→绿；既有服务层测试零回退。

## S3 重扫 admin 端点（D3.1/D5）

- [src/api/routes/capability_governance.py](src/api/routes/capability_governance.py)：
  `POST /api/capability-governance/admin/supply-chain-rescan`——
  `GovernancePackRequest` 复用、`Idempotency-Key` 头、rejected → 409、
  非 admin → 403（同 #14 端点约定）。
- 测试：新文件 `tests/test_governance_rescan_api.py`（HTTP 矩阵 + 幂等头）。
- 验证：新测试红→绿；#14 HTTP 层测试零回退。

## S4 事件快照与投影一致性（D3.3/D3.4）

- 确认自动隔离事件过 `eligibility_changed` validator（lifecycle ∈
  {active, deprecated} + quarantined 快照）；deprecated 中验签失败 → 隔离事件
  携带 deprecated+quarantined 快照。
- risk_accept 惰性到期 → 重隔离投影（不写事件）→ 再次 restore 的恢复复查链
  （Trivy 7 天时效 + 签名 + 证据）真实可用。
- 测试：并入 `tests/test_auto_quarantine_hook.py` / `tests/test_governance_rescan.py`
  的快照纪律用例。
- 验证：新测试红→绿；#14 治理回归零回退。

## S5 真实注册脚本与冻结夹具（D2）

- 新脚本 `scripts/prepare_ac07_10_packs.py`（模式同 prepare_ac06）：
  真实 Tool 源码两版本（2.0.0 `tool_version` 字段 / 3.0.0 `ignore_empty_rows`）→
  真实样例校验 → 确定性归档 → 真实 OCI push → `save_pack` 个人 draft
  （Owner=`liyi`）→ 治理 registered 事件；**必须显式 `--apply`** 才写本机 OCI
  目录与生产库，执行前自动备份 `data/webui.db`；dry-run 输出完整计划。
- 测试：`tests/test_ac07_10_prepare_script.py`（源码内容确定性 / 样例校验 /
  digest 与版本一一对应 / dry-run 零写 / 归档 mtime=0）。
- 验证：测试红→绿；dry-run 输出人工核验后等用户授权 `--apply`。

## S6 双轴审查与回归

- 后端回归：#14 治理回归 + #13 装载门回归 + 新增测试全绿；
  前端构建（无前端改动，仅确认零回归）；Playwright settings +
  semantic-workspace。
- Standards/Spec 双轴审查（固定点 = #14 合并提交 `5ad3a472`），首轮阻断
  全部修复并复核。
- 验证：审查结论「可合入」；回归数字记录进执行报告。

## S7 8088 真实纵切面验收（D4 1-7，分阶段授权）

- 验收方案单独成文（`docs/plans/2026-08-16-agentic-capability-ac07-10-acceptance-plan.md`）。
- 阶段 0：LLM 可用性探测（决定真实任务 or 冻结重放口径，Q3A）；
- 阶段 1：注册 2.0.0 个人 draft（S5 脚本 `--apply`，备份先行）；
- 阶段 2：验证五步 + Trivy/Syft + 自动晋级 verified + 注册 3.0.0 并行验证；
- 阶段 3：平台候选 → 快照 → 签名 → 六步验证 → admin_gray 发布（2.0.0/3.0.0）；
- 阶段 4：管理员任务选择/真实装载 + 回滚指针切换 + deprecated + 历史冻结恢复；
- 阶段 5：revoked + 跨用户拒绝 + 篡改演示（备份→篡改→409→自动隔离→restore→还原）；
- 阶段 6：risk_accept applied 链 + 惰性到期 + 重启幂等 + 零残留证据；
- 每阶段：展示计划 → 用户授权 → 执行 → 记录证据（耗时/库时间/SBOM hash/签名验证/
  零残留）。
- 验证：Issue AC 1-7 逐条对照记录；执行报告 + 文档同步 + 发布链（逐项授权）。

## 产出文件清单

| 文件 | 任务 |
|---|---|
| src/capability_governance/runtime_gate.py（改） | S1 |
| src/capability_governance/service.py（改） | S1/S2 |
| src/api/capability_governance_runtime.py（改） | S1/S2 |
| src/api/routes/capability_governance.py（改） | S3 |
| tests/test_auto_quarantine_hook.py（新） | S1/S4 |
| tests/test_governance_rescan.py（新） | S2/S4 |
| tests/test_governance_rescan_api.py（新） | S3 |
| scripts/prepare_ac07_10_packs.py（新） | S5 |
| tests/test_ac07_10_prepare_script.py（新） | S5 |
| docs/plans/…-ac07-10-acceptance-plan.md（新） | S7 |
| docs/plans/…-ac07-10-execution-report.md（新） | S7 |
