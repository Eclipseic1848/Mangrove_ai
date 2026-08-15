# AC-07-07 独立平台快照、签名与 admin_gray 发布 需求复核

> 日期：2026-08-14
>
> 对应工单：GitHub Issue #12（`Eclipseic1848/Mangrove_ai`，`[AC07-07]`）
>
> 状态：`requirements_confirmed`（七项未决问题已于 2026-08-14 由用户按推荐逐项确认；
> 确认答案见第 9 节推荐列）
>
> 边界：本文是 #12 的需求复核记录，不是设计或实现授权。范围、权限、发布语义与已确认
> 决策以用户确认后的本文为准；实现与发布仍需后续各阶段单独授权。

## 1. 目标

管理员把 `verified/active/eligible` 的个人能力复制为**不再依赖 Owner 的脱敏平台快照**：
删除 Owner、个人 TaskRef、原文件名、业务字段值、宿主路径、Token、连接和个人配置，形成
独立 OCI 内容与新 digest，重新执行平台侧验证（不含个人任务重放），用已验证的 Cosign
标准 OCI image signature 路径签名后发布到 `admin_gray`。发布不扩大普通用户权限；
普通用户受众变更作为独立治理命令实现，但实际执行留待后续人工授权。

## 2. 范围

对照 Issue #12 的七条验收标准：

| # | 验收标准 | 本工单要做 |
| --- | --- | --- |
| AC1 | 只有 verified/active/eligible 个人精确 digest 可提交平台候选 | 候选提交门（三轴 + scope=PERSONAL 校验），拒绝其它组合 |
| AC2 | 快照删除 Owner/个人 TaskRef/原文件名/业务字段值/宿主路径/Token/连接/个人配置，形成独立 OCI 内容与新 digest | 脱敏快照生成命令：从个人 OCI 内容重建平台 OCI 内容（manifest/config 层白名单重写） |
| AC3 | 脱敏快照重新执行合成 Smoke、失败关闭、Trivy、Syft、装载探针和独立验证，不复用个人签名 | 平台验证证据集（六项，无 owner_task_replay），与个人五步验证分离 |
| AC4 | 平台 digest 用已验证 Cosign 标准路径签名；装载前可用公钥重验 | 复用 #9 的 `OciSigningTransaction` + 工具锁；签名引用与公钥身份入证据 |
| AC5 | 发布命令要求管理员、原因、幂等键、预期状态，写不可变审计事件；重复调用不产生重复平台版本 | 发布命令（幂等/预期状态/409 冲突），事件流加平台候选与发布事件 |
| AC6 | 受众固定 admin_gray，不自动推荐、不实际开放普通用户 | 发布后投影受众=admin_gray；推荐指针机制不在本工单 |
| AC7 | 受众变更作为独立治理命令实现并受签名/扫描/三轴约束；实际执行留待后续人工授权 | 实现受众变更命令（写事件+投影），但产品上不提供开放入口（#12 不实际执行） |

## 3. 非目标

- CapabilityMountResolver 运行时强制门（#13）：#12 的签名/受众投影在 #13 前无运行时
  强制效果，如实标注。
- 弃用、回滚、撤销和限期风险接受（#14）；推荐指针机制（随 #14）。
- 两条真实治理纵切面（#15/#16）；AC-06 兼容切换（#17）。
- 普通用户受众的**实际开放**（AC7 只实现命令，不执行）。
- 把两项 AC-06 历史灰度包重新发布为平台快照（它们在 #15/#16 各自发生时处理）。
- 私钥生成/轮换/吊销（#9 已固定本地加密私钥路径；本工单沿用）。

## 4. 现状映射（已有落点，可复用）

| Issue 要求 | 现有落点 | 缺口 |
| --- | --- | --- |
| 个人能力 OCI 内容 | `OrasOciLayoutStore`（capability_catalog/oci_store.py，push_file/lookup_file/materialize，AC-04/05 已在用） | 平台快照的独立 Layout 落点与"业务字段值"的白名单重写未定义 |
| Cosign 标准签名 | `OciSigningTransaction.execute`（oci_signing.py，#9 已验证：ORAS→短期 Zot→Cosign→重验→独立 Layout）+ `LockedOciSigningToolchain`（Cosign 3.0.6/ORAS 1.3.2/Zot 2.1.20 工具锁） | 直接复用；只需新的事务请求 |
| 三轴投影 | `CapabilityGovernanceEvent` + `events[-1]` 投影（#33/#10） | platform pack 的候选/发布事件类型与受众字段不存在 |
| 五步验证 | `CapabilityValidationManager`（#34） | 平台验证是六项新组合（Smoke/失败关闭/Trivy/Syft/装载探针/独立验证），无 owner_task_replay，不能直接复用五步运行结构 |
| Trivy/Syft | `CapabilitySupplyChainEvidenceService` + 工具锁（#35） | 对快照目录重扫是同一入口换目标，复用度待设计确认 |
| 管理员命令模式 | `maybe_promote`/`audit_view_business_content`（#10/#11：幂等键+预期状态+审计事件） | 发布命令复用该模式 |
| 目录登记拒绝 PLATFORM | `register_pack` 抛"平台能力包只能由发布治理流程登记" | 发布 Adapter 是缺口本身，#12 要补上这个"发布治理流程" |
| 装载探针 | **不存在**（全库无实现） | 新概念：待设计定义（见 Q4） |
| 独立验证（verifier） | 五步中的 `verifier` 步骤（#34） | 平台验证的"独立验证"证据语义待设计 |

## 5. 权限矩阵

| 角色 | 本工单之后能做什么 |
| --- | --- |
| 能力 Owner | 提交自己的 verified 个人能力为平台候选（或管理员代提交？见 Q6） |
| 管理员/超管 | 审核候选、执行脱敏快照生成、平台重验证、签名、发布到 admin_gray；受众变更命令（但产品无入口，实际执行留待授权） |
| 普通用户 | 不可见治理入口与平台候选/发布状态（现状保持，管理路由 403） |
| 发布动作 | 只由管理员命令触发；系统 worker 执行验证/签名子流程 |

## 6. 平台快照脱敏清单（AC2 的删除项落点，草案）

- Owner：快照 OCI 内容与治理目标 `owner_id=None`、`scope=PLATFORM`。
- 个人 TaskRef / 原文件名 / 业务字段值：快照 manifest/config 只保留能力运行所需的最小
  字段（能力 id、版本、入口、权限声明、运行时配置）。**"原文件名"语义（用户确认，
  2026-08-14）**：指业务来源文件名——快照中本就不存在业务文件；能力结构成员名
  （含 entrypoint 引用的入口脚本）是运行必需路径，予以保留，外层归档名统一为
  `mangrove-capability.tar`。
- 宿主路径 / Token / 连接 / 个人配置：任何绝对路径、凭证、模型连接引用、个人目录配置
  一律不出现在快照内容与证据中。
- 具体"业务字段值"清单（manifest 中哪些字段属于业务字段）待设计阶段按真实个人包
  manifest 结构逐字段核对（见 Q3）。

## 7. 事件与投影

- 事件类型扩展：`platform_candidate`（提交候选）、`platform_published`（发布）。
- 投影扩展：platform pack 的治理投影（scope=PLATFORM）在 #12 前只有 legacy_compat 来源；
  #12 后新增 governance_event 来源，携带受众字段（`audience: admin_gray`，固定）。
- `audit_viewed`/`registered`/`promoted_to_verified` 语义不变。

## 8. 验证策略

- 服务层：候选门拒绝非 verified/active/eligible；脱敏快照不含清单字段；平台验证六项
  证据齐全才可发布；发布幂等/预期状态/409；受众固定 admin_gray；审计事件不可变。
- 签名：复用 #9 的失败关闭/取消/重验路径测试（针对平台 digest 再走一遍冻结夹具）。
- HTTP：管理员路由权限矩阵；Owner 提交候选；普通用户 403。
- 前端：管理员审核视图补"平台候选"分组与发布动作（#11 留下的分组缺口）。
- 真实路径：本工单只建机制与冻结夹具验证；真实个人能力的平台快照发布属于
  #15/#16 纵切面（与 #10 同样的边界）。

## 9. 事实分级与未决问题

- **已验证事实**：#9 签名闭环、#10 晋级机制、#11 审核视图、AC-04/05 OCI 打包、#34/#35
  验证与扫描均已实现；`OciSigningTransaction` 与工具锁可直接复用；"装载探针"不存在。
- **基于代码的推断**：平台验证不能复用五步运行结构（个人任务重放与快照语义冲突）；
  发布 Adapter 需要新写（catalog 已预留拒绝 PLATFORM 的门）。
- **尚未验证的建议**：以下未决问题的推荐答案。

| # | 问题 | 推荐答案 |
| --- | --- | --- |
| Q1 | 平台验证证据落点 | 新建 `PlatformValidationRun`（六步：synthetic_smoke/fail_closed/trivy/syft/mount_probe/independent_verifier），不复用个人五步 ValidationRun |
| Q2 | 平台 OCI 内容落点与目录登记 | 平台专属 OCI Layout 目录（配置项）；发布后经发布 Adapter 写入 catalog 的 PLATFORM pack（唯一写入口），治理投影走事件流 |
| Q3 | "业务字段值"白名单怎么定 | 设计阶段按真实个人包 manifest 结构逐字段核对；快照 config 只保留 id/version/entry/permissions/runtime 五类字段，其余删除或置空 |
| Q4 | 装载探针定义 | Capability Host 内加载快照并执行一次确定性合成调用（复用 Smoke 的执行环境），输出可复核 hash；失败关闭不留残留。**实现偏差（已确认，2026-08-14）**：#12 实现目录级装载结构探针（物化可解包、manifest 入口结构完整、入口脚本存在、确定性 hash），真实 Capability Host 执行探针留待 #15/#16 纵切面（与"真实灰度包晋级留待纵切面"同一先例） |
| Q5 | 受众变更命令的产品呈现 | 只实现服务层命令 + 事件 + 投影（audience 可变为 users），HTTP 与前端**不提供入口**（实际执行留待后续人工授权），并有测试证明无入口 |
| Q6 | 谁提交平台候选 | 管理员提交（治理动作归管理员）；Owner 不新增提交按钮（规格故事 8 的"提交平台审核"由管理员在审核视图代执行） |
| Q7 | 供应链扫描复用度 | 平台验证的 trivy/syft 步骤直接对快照目录调用既有扫描服务（换目标参数），证据绑定平台新 digest，不复用个人证据 |

---

> 说明：本会话沿用 #10/#11 的纵向流程。用户确认本节后，进入领域/接口设计（产出设计
> 文档与任务拆分），之后每阶段产物等待用户确认，不自动进入实现。
