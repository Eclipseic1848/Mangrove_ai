# AC-07-08 设计：CapabilityMountResolver 运行时治理门

> 工单：[Eclipseic1848/Mangrove_ai#13](https://github.com/Eclipseic1848/Mangrove_ai/issues/13)
> 日期：2026-08-14
> 前置：[需求/规格复核](2026-08-14-agentic-capability-ac07-08-requirements-review.md)（Q1–Q7 已确认）
> 依据：AC-07 规格 §8、ADR-0029、Issue #13 AC

## 设计决策

### D1 门的位置与装配方向（依赖倒置）

`capability_governance.service` 顶层已依赖 `capability_catalog`；`capability_catalog/__init__`
顶层导出 `CapabilityMountResolver`，因此 MountResolver 不能顶层反向 import governance
（包级循环）。方案：

- `capability_catalog` 定义运行时门的最小 Protocol（`RuntimeGateContract`），MountResolver
  只依赖该 Protocol。
- 门实现放在新模块 `src/capability_governance/runtime_gate.py`（`CapabilityGovernanceRuntimeGate`），
  只依赖 governance 的 repository/models/oci_signing 与 catalog 的 models（方向合法）。
- 装配在 `DefaultCapabilityMounts._get_resolver()` 内完成（该方法本已是延迟构造，
  import 在运行时发生，无循环风险）。

### D2 门接口形态

```python
class RuntimeGateContract(Protocol):
    def check_mount(self, actor, pack) -> None: ...
```

- `check_mount` 在装载前检查三轴、受众与签名；不满足时抛
  `CapabilityMountGateRejected`（RuntimeError 子类，携带 pack 身份与原因），失败关闭。
- 个人 Pack：owner 匹配 + verified/active/eligible。
- 平台 Pack：受众匹配（admin_gray → 仅管理员/超管；users → 全部角色）
  + 发布事件签名证据存在 + `verify_local` 重验通过 + 公钥指纹与发布事件一致。
- 不返回部分结果、不降级、不换版本。

### D3 投影只读公开入口

将 `CapabilityGovernance._projection_for_pack` 提升为公开方法
`runtime_projection_for_pack(pack) -> CapabilityGovernanceProjection`，语义不变
（治理事件折叠 + legacy 兼容映射），供门与新任务选择过滤共用，避免第二份投影逻辑。

### D4 平台签名装载验证（#9 直用）

- 门内组合 `PlatformSignatureVerifier` Protocol，实现用 `OciSigningRuntime.verify_local`。
- 装载请求构造对齐 #12 签名侧（`platform_validation.py:272`）：
  - `output_layout = 平台Layout / "signed" / <发布事件的 platform_validation_run_id>`
  - `output_reference = subject_digest = pack.digest`
  - `public_key_path = settings.capability_platform_signing_public_key`
  - `transaction_id = "load-" + digest 前缀`（确定性）
- 重验结果须满足：`subject_digest == pack.digest`、`signature_digest == 发布事件的
  signing_signature_digest`、`public_key_sha256 == 发布事件的 signing_public_key_sha256`，
  任一不一致 → 拒绝装载。
- 公钥配置缺失或签名 Layout 缺失 → 拒绝装载（失败关闭）。

### D5 装载门检查顺序（每 ref，在 `resolve_for_owner` 内）

1. `resolve_selection` 读取冻结选择（无选择 → 返回空，不触碰治理投影，零回归）。
2. `resolve_pack` 可见性 + digest 精确匹配（现有，跨 Owner 天然拒绝）。
3. `check_mount`（新增）：三轴/受众/签名门。
4. 物化（现有，按 D7 路由到正确 Layout）。

任一步失败 → 异常传播 → PiRuntimeError → 任务失败关闭；不降级到旧灰度路径。

### D6 legacy 兼容判定（Q2）

- 平台 Pack 投影 `source == "legacy_compat"`（无 platform_published/audience_changed 事件）
  → 无签名证据 → 放行旧路径（AC-06 两项历史灰度包依赖此路径直至 #17 切换）。
- 有发布事件的平台 Pack 走完整门；`source == "legacy_compat"` 的个人 Pack 三轴门照常
  （legacy verified 映射为 verified/active/eligible，通过即装载）。

### D7 平台 Pack 物化来源（#12 接线缺口修复）

`DefaultCapabilityMounts` 当前只装配个人 `capability_oci_layout_path`；平台快照位于
`capability_platform_oci_layout_path`。MountResolver 装配改为按 scope 路由的双 store
（`artifact_store_for(scope)`），个人 Pack 从个人 Layout、平台 Pack 从平台 Layout 物化。
物化后的 digest marker、integrity 记录与缓存复用逻辑不变。

### D8 新任务选择过滤（deprecated 不可选）

- `GET /capabilities`（`semantic_workspace.py:403`）：在现有 maturity 过滤之上加治理
  投影过滤——deprecated/revoked/quarantined/draft 与受众不符的平台 Pack 不进入列表。
  路由通过注入的只读投影接口读取（与 MountResolver 同源）。
- `freeze_selection` 冻结入口（`semantic_workspace.py:978` 调用处）：冻结前对每个 ref
  执行同一门检查（防止绕过选择列表直接冻结）；历史 `resolve_selection` 路径不检查
  （deprecated 历史任务可继续恢复）。

### D9 运行期检测（运行中隔离/撤销）

- `_run_pi_task` 中 `await self._pi_runtime.start/resume` 期间改为并发监督：
  `asyncio.wait([执行任务, 监督任务])`，监督任务按固定节奏（常量
  `RUNTIME_GATE_POLL_SECONDS = 30`）读取当前 selection 中各 Pack 的投影。
- 命中 quarantined/revoked/签名证据失效（个人/平台）→ `CapabilityHost.stop`
  （阻断后续能力调用）+ `pi_runtime.cancel` + 任务标记取消——复用现有取消路径，
  Candidate/Delivery 不发布。
- 无能力任务（无 selection/无 Sidecar）不启动监督任务，零负担。
- 检测只读投影，不写事件、不写投影（Q3）。

### D10 无能力任务零回归

- 短路顺序保持：`resolve_selection is None → return ()`，之后才构造治理投影读取。
- 无原生能力任务不创建 Sidecar（现有 `has_native_capability` 判断不变）。
- 现有 AC-06 未切换路径（legacy 放行）不受影响。

### D11 测试策略（冻结夹具 + 回归）

- 新测试：门逻辑单测（三轴矩阵 × 个人/平台 × 受众 × 签名证据）、MountResolver 集成
  （拒绝时无物化无残留）、`/capabilities` 过滤、`freeze_selection` 拦截、运行期监督
  （投影变化 → Sidecar stop + cancel 调用序列）、双实现 Repository（InMemory/SQLite）。
- 签名验证用替身 Verifier（真实 `verify_local` 已在 #9 覆盖；本单覆盖请求构造与结果
  比对逻辑）。
- 回归：capability_catalog / governance / pi_runtime / workspace 路由既有集合；
  后端全量 + 前端构建 + Playwright 设置页与工作台回归。
- 8088 验收（Q7）：AC-06 历史包真实装载 + 拒绝场景 + 取消清理零残留。

### D12 范围边界

- 不实现：弃用/回滚/撤销/隔离命令与自动 quarantined 投影（#14）、真实治理纵切面
  （#15/#16）、AC-06 兼容切换（#17）。
- 无新数据库表、无新迁移、无新前端页面（Playwright 只做回归）。

## Seam 变更清单

| 文件 | 变更 |
|---|---|
| `src/capability_catalog/mount_resolver.py` | 注入 `RuntimeGateContract`；`resolve_for_owner` 每 ref 调 `check_mount`；按 scope 路由物化 store |
| `src/capability_catalog/default_mounts.py` | 双 Layout 装配 + gate 装配（延迟 import） |
| `src/capability_governance/runtime_gate.py` | 新增：`CapabilityGovernanceRuntimeGate` + `PlatformSignatureVerifier` + `CapabilityMountGateRejected` |
| `src/capability_governance/service.py` | `_projection_for_pack` 提升为公开 `runtime_projection_for_pack` |
| `src/api/routes/semantic_workspace.py` | `/capabilities` 加投影过滤；`freeze_selection` 调用处加冻结前门检查 |
| `src/api/semantic_workspace_runtime.py` | `_run_pi_task` 并发监督（运行期检测） |
| 不动 | 数据库、迁移、前端页面、Pi Runtime 容器侧、Host Sidecar 协议 |

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| `verify_local` 每装载一次约数秒（仅平台 Pack） | 规格字面要求「装载前验证」；真实成本在 #15/#16 纵切面度量，若不可接受再回用户决策 |
| 运行期检测 30s 窗口内可能发生一次调用 | Q1 已确认语义等价（原子调用完成后停止后续调用）；保守失败关闭 |
| 循环 import | D1 依赖倒置 + 延迟装配，实现切片时以 import 冒烟验证 |
| legacy 放行被误用扩大权限 | 判定键是「平台 Pack 且无发布事件」；#17 切换时收紧，不做普通用户开放 |

## 双轴审查后修正与已知限制（2026-08-15 更新）

双轴审查（Standards/Spec）首轮 FAIL 后按批准计划修复（A/B 类），C 类为文档标注：

### 审查后修正

| # | 修正 | 说明 |
|---|---|---|
| A1 | 装载门 lifecycle 放行 {ACTIVE, DEPRECATED} | AC3/Q4：deprecated 历史冻结任务可恢复装载；新任务由列表过滤 + 冻结拦截挡住 |
| A2 | `_await_with_gate_supervision` 监督优先 | 监督先完成即失败关闭（专用 `_GateViolationAbort`）；cancel 期间执行恰好完成的竞态不再返回结果 |
| A3 | `_check_freeze_gate` digest 失配 422 | 引用 digest 与目录 Pack 不一致时 422，不再落入 500 |
| B1 | 监督命中专用异常 | `_run_pi_task` 捕获 `_GateViolationAbort` 静默退出，状态保持 cancelled，不再被 `_mark_failed` 覆盖 |
| B2 | 监督读取异常继续轮询 | 投影读取异常不是确定性违反，跳过本轮；取消只发生在确定性违反时 |
| B3 | 装载侧门始终装配 | 治理表缺失时门仍构建；读路径降级到 legacy_compat 投影（个人 draft 拒绝 fail-closed），与冻结侧一致 |
| B4 | 监督零 DDL | `initialize_schema=False` + 只读表存在检查，30s 轮询不再触碰迁移 |
| B5 | cancel 清理失败留痕 | `gate_cancel_cleanup_failed` 工作台事件，不掩盖治理取消事实 |
| B6 | 验证重放前置投影检查 | `PiTaskReplayRunner` 注入 `replay_guard`：revoked/quarantined 拒绝重放；draft 验证目标允许 |

### 复审（第二轮）修正

复审确认首轮阻断项消除，另发现 2 个冻结/装载缺口并修复：

| # | 修正 | 说明 |
|---|---|---|
| A4 | 平台分支补三轴检查 | `check_mount` 平台分支补齐 maturity VERIFIED / lifecycle {ACTIVE, DEPRECATED} / eligibility ELIGIBLE，revoked/quarantined 平台 Pack 装载与冻结立即拒绝（不再只靠 30s 监督兜底） |
| A5 | 冻结层补可选谓词 | `_check_freeze_gate` 在装载门之后追加 `_selectable_for_task` 检查：冻结是「新任务」入口，deprecated 必须 409（装载门放行 DEPRECATED 仅服务于历史恢复路径） |

复审判断项（接受，不修）：门装配双份（B3 已记录防漂移约定）、投影谓词分散、
测试 DDL 重复（C5）、`_runtime_gate()` 单行转发、`_replay_guard` 裸 RuntimeError
（#14 落地时随事件形态一起专用化）。

### 已知限制（C 类标注）

- C1 D8 字面「受众不符不进列表」未逐字实现：`/capabilities` 路由已由
  `_require_capability_gray` 限定管理员，行为等价；投影过滤对受众不再重复。
- C2 D9 字面「签名证据失效」运行中未检测：Q1 已确认本议题范围只含
  隔离/撤销；签名证据失效检测留待 #15/#16 纵切面。
- C3 verifier/工具链单例在装配时降级后需进程重启恢复（无热修复路径）；
  降级时装载/冻结按 fail-closed 拒绝，B5 事件留痕提供可观测性。
- C4 前端 409 文案无需修改：治理拒绝发生在创建任务端点，前端直接显示
  API detail（可读文案）；Tasks.tsx 的「任务正在执行中」仅覆盖 run_now 端点。
- C5 测试夹具手工 DDL 建治理事件表（#14 才有合法撤销/隔离事件形态），
  #14 落地后可切换到真实迁移夹具。
