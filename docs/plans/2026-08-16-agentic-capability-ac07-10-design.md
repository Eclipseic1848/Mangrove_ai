# AC07-10（#15）Python 表格 Tool 真实治理纵切面 — 设计文档

> 状态：已确认（双轴审查后按实现口径修订）
>
> 依据：Issue `Eclipseic1848/Mangrove_ai#15`、AC-07 规格 §10、需求复核 Q1-Q8（全部 A）
>
> 定位：纵向纵切面票。代码增量极小（自动隔离触发接线 + 手动重扫命令），
> 主体是「真实证据链首跑」——#10/#12/#14 已建机制在真实数据上的首次贯通。

## D1 目标与非目标

目标：

1. 首条真实纵切面：个人 draft `gray-python-table@2.0.0` → 验证五步 → Trivy/Syft →
   自动晋级 verified → 平台候选 → 脱敏快照（新 digest）→ Cosign 签名 → 平台六步验证 →
   `admin_gray` 发布 → 管理员任务选择与真实装载。
2. 并行新版本：`3.0.0`（真实微调）并行验证/发布，多版本并存；推荐指针真实切换
   （rollback 2.0.0 ↔ 3.0.0）。
3. 治理动作链：deprecated（含历史冻结任务恢复装载）→ revoked → 跨用户拒绝 →
   签名/digest 篡改拒绝（fail-closed → 自动隔离 → restore 恢复）。
4. 自动隔离触发接线（本票唯一实质新代码，Q4A）：手动重扫触发 + 装载门验签失败触发。
5. 真实 risk_accept applied 链（人工隔离 + 非 BLOCKED 证据 → 接受 → 惰性到期 → 重隔离）。
6. 重复请求/服务重启不重复发布、不覆盖旧证据旧版本；耗时/扫描库时间/SBOM hash/
   签名验证/容器/网络/挂载/临时 Registry 零残留证据记录。

非目标（D8）：定时重扫调度器、普通用户开放、AC-06 兼容切换（#17）、MCP 纵切面（#16）、
新 DDL、新前端页面、accept-s8-* 验收样本处理。

## D2 版本与注册策略（Q1A/Q2A）

- 三个版本内容各异、digest 各异（OCI 归档 mtime=0 确定性打包）：
  - `1.0.0`（legacy 平台包）：不动，直至 #17 切换；
  - `2.0.0`（个人 draft → 发布）：源码真实微调——输出增加 `tool_version` 字段，功能等价；
  - `3.0.0`（个人 draft → 发布）：源码真实微调——新增可配置 `ignore_empty_rows`。
- 注册路径：最小脚本（真实源码 → 真实归档 → 真实 OCI push → 真实 digest →
  `save_pack` 个人 draft），与 `scripts/prepare_ac06_gray_capabilities.py` 同路径；
  不 Mock、不绕过治理。治理登记复用 `CapabilityGovernance.register_pack`
  （个人 registered 事件）。
- Owner = 真实用户 `liyi`（`u_9505fd620899`）；跨用户拒绝演示用另一真实普通用户。
- 生产库写动作：执行前在线一致性备份 + 逐项授权（Q8A）。

## D3 自动隔离触发接线（本票核心新代码，Q4A）

### D3.1 手动重扫触发

- 新服务方法 `CapabilityGovernance.rescan_supply_chain(actor, pack_ref, reason, idempotency_key)`：
  1. 仅管理员；目标必须是**已发布平台包**（存在 `platform_published` 事件），否则 409；
  2. 复用 `_platform_probe_parts` 共享装配（capability_governance_runtime.py，
     与平台六步验证同一物化/采集实现）按冻结 digest 物化；
  3. 复用 `CapabilitySupplyChainEvidenceService.collect` 采集新证据；
  4. `save_supply_chain_evidence` 保存（**追加新行，不覆盖旧证据**）；
  5. 触发判定：新证据 `status == BLOCKED` 且投影 `eligibility == ELIGIBLE` →
     自动写 `eligibility_changed(QUARANTINED)` 事件（事件快照 = 写入时刻投影 +
     quarantined，reason 引用新证据 findings）。BLOCKED 已涵盖全部 finding
     计数语义（secret/critical/可修复 High/误配置/库过期均为 blocker 组件，
     不存在「计数新增但非 BLOCKED」的形态）；
  6. 投影已 quarantined → 只更新证据，不重复写隔离事件（幂等）；
  7. 全链路幂等：新增 `rescan_completed` 事件类型（快照 = 写入时刻投影，
     引用新证据行）作为重扫留痕与 PASSED 路径幂等锚；隔离事件与重扫事件
     共用同一幂等键，双类型命中任一即返回，且**崩溃窗口补写**（隔离已写
     而重扫缺失时按投影补写，不吞掉缺失的一半，#14 多事件教训）；
  8. 返回 `GovernanceCommandOutcome`（applied/already_applied/rejected）。
- 新端点：`POST /api/capability-governance/admin/supply-chain-rescan`
  （admin_router，Idempotency-Key 头，同 #14 命令约定）。

### D3.2 装载门验签失败触发

- `CapabilityGovernanceRuntimeGate` 增加可选注入
  `auto_quarantine: Callable[[CapabilityPack, str], None] | None = None`
  （默认 None = 保持 #13 只读行为，测试与装配零回归）。
- 仅在四个**真实验签失败**分支触发：verify 抛异常、主体 digest 不一致、
  签名 digest 不一致、公钥不一致；「签名验证器未配置」不触发（配置问题非篡改证据）。
- 回调实现（装配层注入）：投影复查（已 quarantined 跳过）→ 写
  `eligibility_changed(QUARANTINED)` 事件（快照纪律同 D3.1）。
- 覆盖装载 Seam 与冻结拦截两个入口（两者共用同一 `check_mount` 实现）。

### D3.3 事件快照纪律

自动隔离事件走 `eligibility_changed` 既有 validator（#14 已放宽：lifecycle ∈
{active, deprecated} 允许 quarantined 快照），不新增事件类型、不改校验矩阵。

### D3.4 risk_accept 真实链的模型对齐（设计修正说明）

证据模型无「不可修复 High」字段（只有 `fixable_high_count` 等，BLOCKED 判定不含
无修复 High）。Q4 措辞中的「无修复 High 隔离 → 接受」改为语义等价的真实链：

人工隔离（管理员判定新版本不可用，证据仍 PASSED）→ `risk_accept`
（finding_ref 实引本包验证运行）→ applied → 惰性到期 → 重新 quarantined。

不改证据模型（零 DDL）；blocker 隔离拒绝接受（只能修复后解除），两者真实演示。

## D4 治理动作链与篡改演示（Q6A）

验收操作序列（分阶段逐项授权执行）：

1. 发布 2.0.0 后：管理员任务选择 → 真实装载（或冻结任务重放，按 Q3A LLM 探测结果）；
2. 发布 3.0.0 后：rollback 推荐指针 3.0.0 → 2.0.0 真实切换（选择列表标记/置顶）；
3. deprecate 2.0.0 → 新任务不可选（列表过滤 + 冻结 409）→ 历史冻结任务恢复装载成功；
4. revoke 2.0.0 → 历史恢复装载也被拒；
5. 跨用户拒绝：另一普通用户对该能力 403/404；
6. 篡改演示安全原则：篡改前**blob 级备份**到验收目录 → 篡改平台 Layout 主体 manifest
   blob 一个字节 → 装载 409 fail-closed → 自动隔离事件（D3.2）→ restore 命令 →
   逐字节还原 blob → `verify_local` 复验通过。全程不触碰发布事件证据
   （签名 digest 不变），主 Layout 演示后与发布证据完全一致。
7. 重复请求/重启不重复：发布命令同幂等键重放 already_applied；8088 重启后重放仍
   already_applied；失败恢复不覆盖旧证据/旧版本。

## D5 API 层

- 新增：`POST /api/capability-governance/admin/supply-chain-rescan`（D3.1）。
- 其余全部复用既有端点：验证（router 63-190）、平台候选/发布、六治理命令、
  change-audience（admin_router 298-517）、装载/冻结/选择（semantic_workspace）。

## D6 数据库与迁移

- 零 DDL：重扫证据追加 `capability_supply_chain_evidence` 新行；隔离事件复用
  `capability_governance_events`；无新表、无新迁移。

## D7 测试策略

TDD 单元（新代码全部先红后绿）：

- 重扫：BLOCKED→隔离 / PASSED→不隔离 / 已隔离→不重复写事件 / 幂等键重放 /
  非 admin 403 / 未发布 409 / 个人包 409 / 证据追加不覆盖旧行。
- 验签钩子：四分支触发 / 配置缺失不触发 / 已隔离跳过 / 默认 None 行为不变（#13 回归）。
- 事件快照：自动隔离事件过 validator，快照与写入时刻投影一致。
- 冻结夹具：两版本真实 Tool 源码归档（内容、digest 与生产一致）。
- 回归门：#14 治理回归 + #13 装载门回归零回退。

8088 验收：真实操作序列（D4 1-7），逐项展示 → 授权 → 执行 → 记录证据；
耗时、扫描库时间、SBOM hash、签名验证与零残留按 Issue AC7 记录进执行报告。

## D8 范围边界（明确不做）

- 定时重扫调度器（Q4A：只做手动触发命令）；
- 证据模型新增「不可修复 High」字段（D3.4 已对齐，零 DDL）；
- accept-s8-* 样本恢复/删除（留 #16/#17 或专门授权）；
- 普通用户受众开放、AC-06 兼容切换、MCP 纵切面；
- 新前端页面（治理命令仍无产品 UI 入口，同 #14）；
- 新数据库表/迁移（D6）。

## D9 验证任务 Seam（阶段 1 后发现的断链补齐，用户已确认方案 A）

**断链事实**（#15 纵切面暴露的根因）：个人 draft 能力被 #13 冻结门
（`_check_freeze_gate` 要求 verified）与装载门（`check_mount` 个人分支拒
draft）双层拒绝，永远无法进入真实任务 -> 验证五步的 `owner_task_replay`
所需「授权任务 + 真实调用证据」不可达 -> 个人能力无法晋级 verified
（生产库从未有 verified 个人能力的根因）。#13 的 replay_guard 注释
「draft 验证目标允许」预留了消费端，但产生端（授权任务）从未接通。

**设计**：为「验证用途任务」加显式豁免标记 `validation_target`，
贯通 冻结 -> 装载 -> 重放 三段：

1. **API 入口**：`WorkspaceTaskCreateIn` 新增
   `validation_target: CapabilityPackRef | None = None`。语义：本任务是为
   验证该个人 draft 能力而创建的授权任务。创建时校验：
   - target 必须 ∈ `capability_pack_refs`（digest 精确一致）；
   - 必须是个人包且 Owner=当前用户（他人包/平台包 409）；
   - 冻结门豁免仅对该 pack：`check_mount(..., validation_exempt=True)`
     并跳过 `_selectable_for_task`（owner/active/eligible 仍强制）；
     同任务其他 pack 走正常门。
2. **冻结持久化**：`CapabilitySelection` 新增
   `validation_target: CapabilityPackRef | None = None`，随 selection
   payload 冻结落库（零 DDL，payload_json 原有字段扩展）；
   `freeze_selection` 透传标记（catalog 层对个人包本无 maturity 检查，
   无需豁免逻辑）。
3. **装载豁免**：`check_mount(actor, pack, *, validation_exempt=False)`
   --个人分支豁免时跳过 maturity 检查（owner 匹配、lifecycle、
   eligibility 仍强制）；平台分支**永不豁免**。
   `CapabilityMountResolver.resolve_for_owner` 读 selection 后，对与
   `selection.validation_target` 相等的 ref 传豁免。`RuntimeGateContract`
   协议签名同步（可选参数，既有实现零破坏）。
4. **不继承**：`copy_selection_for_owner` 复制时**丢弃**标记--新 revision
   是新业务上下文，验证豁免不继承；继承后的 draft 挂载失败关闭（正确行为）。
5. **验证五步衔接**（零改动）：replay_guard 已允许 draft 验证目标；
   `_expected_target_tools` 用挂载 digest 标记；`list_options`/`resolve`
   按 selection.pack_refs 匹配。

**安全边界**：
- 豁免仅限：个人 + Owner 自己 + active + eligible + 显式 API 标记 +
  冻结 selection 持久化，五条件同时成立；
- draft 能力仍不进入 `/capabilities` 新任务选择列表
  （`_selectable_for_task` 不变）；
- 平台包、他人包、隔离/撤销包一律不豁免；
- 验证任务仍是真实 Pi 任务（真实数据、真实 Token 消耗）；验证通过
  只产生证据，不自动发布、不自动晋级（晋级走 #10 maybe_promote 判定门）。
