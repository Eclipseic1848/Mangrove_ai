# AC-07-08 任务拆分：CapabilityMountResolver 运行时治理门

> 工单：[Eclipseic1848/Mangrove_ai#13](https://github.com/Eclipseic1848/Mangrove_ai/issues/13)
> 日期：2026-08-14
> 前置：[需求复核](2026-08-14-agentic-capability-ac07-08-requirements-review.md)（Q1–Q7 确认）、
> [设计](2026-08-14-agentic-capability-ac07-08-design.md)（D1–D12 确认）
> 流程：TDD 红绿循环，逐切片聚焦回归；结束后 Standards/Spec 双轴审查

## 切片

### S1 门契约与投影公开入口（模型层）

- `capability_catalog` 定义 `RuntimeGateContract` Protocol 与 `CapabilityMountGateRejected`
  异常归属；`CapabilityGovernance` 公开 `runtime_projection_for_pack`（由
  `_projection_for_pack` 提升，语义不变）。
- 验证门：`pytest tests/test_capability_runtime_gate.py`（投影折叠语义回归：个人/平台/
  legacy 三类 + 事件驱动覆盖）绿灯。

### S2 运行时门实现（governance/runtime_gate.py）

- `CapabilityGovernanceRuntimeGate.check_mount`：
  - 个人 Pack：owner 匹配 + verified/active/eligible 三轴矩阵；
  - 平台 Pack：legacy 兼容放行（无发布事件）；有发布事件 → 受众匹配 + 签名证据三比对
    （subject_digest / signature_digest / public_key_sha256 对齐发布事件）；
  - 违反 → `CapabilityMountGateRejected`，携带 pack 身份与原因。
- `PlatformSignatureVerifier` Protocol + 替身；真实实现直用 `OciSigningRuntime.verify_local`
  构造对齐 #12（`signed/<run_id>`、`output_reference=digest`、公钥配置）。
- 验证门：门矩阵单测（scope × 三轴 × 受众 × 签名证据 × legacy）红绿。

### S3 MountResolver 注入门与双 store 物化路由（catalog 层）

- `CapabilityMountResolver` 注入 `RuntimeGateContract`；`resolve_for_owner` 每 ref 在
  物化前调 `check_mount`；按 scope 路由物化 store（个人/平台 Layout）。
- 验证门：`tests/test_mount_resolver_gate.py`（拒绝时零物化零残留；平台 Pack 从平台
  store 物化；无 selection 短路不触门；digest 失配仍拒绝）绿灯。

### S4 生产装配接线（default_mounts + api runtime）

- `DefaultCapabilityMounts`：双 Layout + gate 延迟装配（`_get_resolver` 内 import，
  规避循环）；`semantic_workspace_runtime` 不改变装配签名。
- 验证门：装配冒烟测试（import 链 + resolve 冒烟 + schema 不存在时零写入零挂载不变）。

### S5 新任务选择过滤与冻结拦截（API 路由）

- `GET /capabilities`：现有 maturity 过滤之上加治理投影过滤（deprecated/revoked/
  quarantined/draft 与受众不符不进入列表）。
- `freeze_selection` 调用处：冻结前对每 ref 执行门检查（拒绝 → 409/400 失败关闭）。
- 验证门：HTTP 层过滤矩阵 + 冻结拒绝测试（管理员/普通用户 × 各状态）绿灯。

### S6 运行期监督（semantic_workspace_runtime）

- `_run_pi_task`：`await start/resume` 期间并发监督任务（常量
  `RUNTIME_GATE_POLL_SECONDS = 30`）周期读投影；命中 quarantined/revoked/签名证据失效 →
  `CapabilityHost.stop` + `pi_runtime.cancel` + 任务取消（复用现有取消路径，不发布）。
- 无能力任务不启动监督；监督只读不写（零 DDL）。
- 验证门：监督序列测试（投影变化 → stop/cancel 调用顺序；正常完成不受扰；无能力任务
  零监督）绿灯。

### S7 聚焦回归与双轴审查

- Capability 相关集合（catalog/governance/mount/workspace 路由/pi_runtime）+ 后端全量
  pytest + 前端构建 + Playwright（设置页 + 工作台回归）。
- Standards/Spec 双轴审查 → 修复 → 复核 PASS。

### S8 8088 验收与文档同步

- 8088：AC-06 历史包真实装载（legacy 放行）+ 装载拒绝场景 + 取消/异常后容器、网络、
  临时挂载、Lease 零残留（Q7 范围）。
- 同步 `docs/status/current.md`、`handoff.md`、`CONTEXT.md`、`AGENTS.md`、`README.md`；
  发布动作（分支/提交/推送/PR/Issue 关闭）逐项授权。

## 依赖顺序

S1 → S2 → S3 → S4 → S5/S6（可并行）→ S7 → S8

## 明确不做（跨切片边界）

- 弃用/回滚/撤销/隔离命令与自动 quarantined 投影（#14）
- 真实治理纵切面（#15/#16）
- AC-06 兼容切换（#17）
- 新数据库表/迁移、新前端页面、Host Sidecar 协议变更
