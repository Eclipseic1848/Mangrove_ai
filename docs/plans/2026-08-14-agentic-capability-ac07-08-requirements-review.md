# AC-07-08 需求/规格复核：CapabilityMountResolver 运行时治理门

> 工单：[Eclipseic1848/Mangrove_ai#13](https://github.com/Eclipseic1848/Mangrove_ai/issues/13)
> 日期：2026-08-14
> 状态：已复核，Q1–Q7 用户确认（2026-08-14「同意」）
> 依据：Issue #13 Acceptance Criteria、AC-07 规格 §8、ADR-0029、现有代码实况

## 1. Issue AC 逐条对照

| # | Issue AC | 规格/代码对照结论 |
|---|---|---|
| 1 | 个人 Pack 必须属于当前 Owner，且 verified、active、eligible；平台 Pack 还必须满足当前受众和签名验证 | 与规格 §8 一致。现有 `resolve_for_owner` 已做 Owner 可见性与 digest 精确匹配；三轴投影、受众、签名检查缺失，本单补齐 |
| 2 | draft、quarantined、revoked、签名失败、受众不符、跨 Owner 和 digest 失配全部拒绝，不降级到旧灰度路径或其他版本 | 与 §8 一致。拒绝 = 失败关闭（异常传播 → 任务失败），不写治理事件（Q3 已确认零 DDL），不静默换版本（现有代码已不换版本） |
| 3 | deprecated 不进入新任务选择，但已冻结该 digest 的历史任务可继续恢复；推荐指针变化不修改 TaskRevision | 与 §8/§2 一致。新任务选择 Seam = `GET /capabilities` 路由 + `freeze_selection`；历史恢复 Seam = `resolve_selection` 只读冻结选择。推荐指针（#14）不改 TaskRevision，本单不实现回滚 |
| 4 | 创建、重试、恢复及每次原生能力调用均经过同一公开 Interface，不能由 API 或 Pi Runtime 绕过 | 创建/重试/恢复已收敛在 `resolve_for_owner`（`pi_runtime.py` start/resume 两处调用）。每次原生调用链路 = Pi 容器 TS 扩展 → 容器网络直连 Host Sidecar `:8765/invoke`，后端无法逐调用拦截；等价实现 = 运行期投影监督 + Sidecar 停止（Q1 已确认） |
| 5 | 运行中隔离或撤销后，在规定安全边界停止后续能力调用；违反硬门时取消并禁止发布 Candidate/Delivery | 与 §8 一致。实现 = 运行期周期检查投影，命中 quarantined/revoked 即停 Sidecar 并取消任务（现有 `cancel` 路径已保证不发布） |
| 6 | 无能力任务不创建治理运行、扫描器或 Sidecar；现有正常任务的启动和结果回归通过 | 现有结构已满足：无 selection → 返回空挂载 → 不启动 Sidecar。本单保证门逻辑不改变该短路顺序（无 selection 不触碰治理投影） |
| 7 | 拒绝、取消和异常后的容器、网络、临时挂载及 Lease 清理通过真实验证 | 现有 `CapabilityHost.stop`、`PiRuntime.cancel`、Egress Lease 清理已工程验证（#34/#35）。8088 验收用 AC-06 历史包做真实装载与取消清理验证（Q7 已确认） |

## 2. 关键事实核验（代码实况）

- **装载 Seam**：`CapabilityMountResolver.resolve_for_owner`（`src/capability_catalog/mount_resolver.py`）是创建（`pi_runtime.py:831`）与恢复（`pi_runtime.py:1091`）唯一挂载点。
- **投影数据源**：`CapabilityGovernance._projection_for_pack`（`service.py:278`）已返回 maturity/lifecycle/eligibility/audience 与 legacy 兼容映射；本单将其提升为公开只读入口。
- **签名验证**：`OciSigningRuntime.verify_local`（`oci_signing.py:662`）可独立重验；#12 发布事件携带 `platform_validation_run_id`/`signing_signature_digest`/`signing_public_key_sha256`，签名 Layout 位于 `平台Layout/signed/<run_id>`（`platform_validation.py:272`）。
- **物化缺口**：#12 平台快照 push 到 `capability_platform_oci_layout_path`，而 `DefaultCapabilityMounts` 的 MountResolver 只装配个人 `capability_oci_layout_path`——平台 Pack 装载物化需要按 scope 路由双 Layout（进入设计 D7）。
- **依赖方向**：`capability_governance.service` 顶层 import `capability_catalog`；`capability_catalog/__init__` 顶层导出 mount_resolver——MountResolver 不能顶层反向依赖 governance，须依赖倒置（进入设计 D1）。
- **新任务选择 Seam**：`GET /capabilities`（`src/api/routes/semantic_workspace.py:403`，`_require_capability_gray` + `list_visible_packs` + maturity 过滤）+ 冻结入口 `freeze_selection`（`semantic_workspace.py:978`）。
- **运行期等待点**：`_run_pi_task`（`semantic_workspace_runtime.py:868`）在 `await self._pi_runtime.start/resume` 期间无治理检查点；现有 `_maintenance_loop` 仅每小时清理回收站，不能作为运行门节奏（进入设计 D9）。

## 3. 需求复核确认记录（Q1–Q7，用户 2026-08-14「同意」）

| # | 决策 | 结论 |
|---|---|---|
| Q1 | 每次原生调用的拦截点 | 运行期 worker 周期检查投影，命中隔离/撤销即停 Sidecar（语义等价「当前原子调用完成后停止后续调用」），违反硬门时取消并禁止发布。不做 Host server 逐调用回查后端（新增容器间接口改动大） |
| Q2 | AC-06 兼容路径 | 无签名证据的平台 Pack（legacy 兼容投影）在 #13 继续旧路径放行；个人 Pack 一律过三轴门；真实平台 Pack（有发布事件与签名证据）过完整门。切换留 #17 |
| Q3 | 运行时拒绝是否写治理审计事件 | 零 DDL：拒绝以失败关闭呈现（异常 + RuntimeEvent），不写治理事件 |
| Q4 | deprecated 过滤落点 | 两层：新任务选择列表过滤（`/capabilities` 路由）+ 冻结时拦截（`freeze_selection`）；历史恢复路径放行 |
| Q5 | 平台签名验证时机 | 每次装载平台 Pack 前跑 `verify_local` 重验（仅平台 Pack 有此成本，数秒级），失败关闭 |
| Q6 | 隔离/撤销检测节奏 | 运行期并发监督（不新增常驻轮询器），节奏在设计中细化为常量 |
| Q7 | 真实清理验证 | 生产库无 verified 个人能力/已发布平台包，用 AC-06 历史包做装载拒绝与取消清理的 8088 级验证；真实治理纵切面留 #15/#16 |

## 4. 偏差与边界标注

1. **「触发隔离」语义**：规格 §8 称签名缺失/校验失败「阻止装载并触发隔离」。本单只实现阻止装载（失败关闭）；自动写 quarantined 投影属于 #14（弃用/回滚/隔离/撤销），Q3 已确认零 DDL。
2. **「当前原子调用完成后停止」**：后端无法观测容器内原子调用边界，实现为检测到投影变化即停 Sidecar（保守失败关闭）；与 Q1 用户确认的语义等价一致。
3. **推荐指针回滚**：规格 §8 提及回滚不修改 TaskRevision；回滚命令属于 #14，本单仅保证历史冻结装载路径不受未来指针变化影响。
4. **真实纵切面**：Python 表格 Tool / Everything MCP 的完整治理装载纵切面留 #15/#16；本单装载验证用冻结夹具 + AC-06 历史包。

## 5. 结论

需求/规格复核完成，与 Issue #13、规格 §8、ADR-0029 一致；Q1–Q7 已获用户确认。进入设计。
