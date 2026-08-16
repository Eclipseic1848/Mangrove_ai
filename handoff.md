# Mangrove 零上下文交接

> 文档用途：写给完全没有历史对话的新会话
>
> 最后现场核验：2026-08-16
>
> 当前分支：`main`
>
> 当前提交：`5ad3a472`（#14 PR #25 合并）；工作树干净
>
> 公开远端：`origin` → `https://github.com/Eclipseic1848/Mangrove_ai.git`
>
> 当前阶段：AC-07 能力信任与发布治理；#9～#14 已关闭，下一工单为 #15（未开工）

## 0. 一句话结论

Mangrove 正在把“能运行的个人能力”推进为“证据完整、可审计、可失败关闭、以后可以由管理员
发布的平台能力”。AC-07 的验证运行、供应链扫描、本地标准 OCI 签名、个人能力自动晋级机制、
管理员审核/审计查看、平台快照发布机制、运行时装载治理门与生命周期治理命令（弃用/回滚/
隔离/撤销/限期风险接受）已经完成；下一步是新仓库 #15“Python 表格 Tool 真实治理纵切面”。

不要把 #9～#14 完成、测试通过、Capability 可运行或管理员灰度可用表述成能力已经自动晋级、
平台已经发布、普通用户已经开放、Phase 4 已完成或 `v0.0.8` 已发布。库内能力没有被晋级，
真实灰度包晋级与真实平台发布纵切面留待 #15/#16。

## 1. 新会话必须先做什么

本文件是第一个入口。打开本文件后，按以下顺序继续读取，不要直接改代码：

1. `AGENTS.md`：仓库工程规则、稳定业务边界和 Git/发布权限。
2. `docs/status/current.md`：当前能力与路线状态的唯一滚动台账。
3. `CONTEXT.md`：领域词汇和长期语义。
4. `docs/agents/`：Issue、标签和领域文档约定。
5. 当前工单 #15、AC-07 规格、ADR-0029，以及旧
   `Eclipseic1848/Mangrove_platform#34/#35`、新 `Eclipseic1848/Mangrove_ai#9～#14`
   的需求复核/设计文档与执行报告。

现场执行：

```powershell
git status --short --branch --untracked-files=all
git branch --show-current
git rev-parse HEAD
git rev-parse origin/main
git remote -v
gh issue view 15 --repo Eclipseic1848/Mangrove_ai --comments
```

预期现场状态是：

- 本地 `main`、`HEAD` 与 `origin/main` 均为 #14 PR #25 的合并提交（SHA 以现场为准）；
- 工作树应干净；
- 新仓库 #9、#10、#11、#12、#13、#14 为 `CLOSED / COMPLETED`；
- 新仓库 #15～#17 为开放工单；
- 只有现场命令能证明当前状态，本段 SHA 只是 2026-08-16 的交接快照。

如果现场不同，先解释漂移并更新 `docs/status/current.md`，不得套用本文快照继续执行。

## 2. 我们在做什么工程

Mangrove 是统一数据任务平台。用户用自然语言描述目标，平台负责来源获取、任务规划、受控能力
调用、数据处理、证据绑定、独立验证和正式交付。当前核心链路是：

```text
自然语言目标
  → GoalContract / 不可变 TaskRevision
  → Agent 规划并选择 Tool、MCP、Skill 或 Procedure
  → 任务级受控 Runtime / Capability Host
  → Candidate + 来源证据
  → 独立 Verifier
  → Delivery Publisher
  → 正式 Delivery
```

当前主线 AC-07 解决最后一段“能力如何建立信任并被治理”：

```text
个人 draft 能力
  → 精确 digest 的 ValidationRun
  → 真实任务、失败关闭、权限和清理证据
  → Trivy / Syft / 来源锁
  → verified
  → 管理员审计
  → 独立脱敏平台快照 + Cosign 签名
  → admin_gray
  → 运行时签名/受众/三轴治理门
  → 弃用/回滚/隔离/撤销/限期风险接受（管理员命令）
  → 另行确认是否向普通用户开放
```

三轴治理语义已经冻结：

- 成熟度：`draft | verified`
- 生命周期：`active | deprecated | revoked`
- 运行资格：`eligible | quarantined`

一次任务成功、供应链扫描通过或签名成功都只是不可变证据，不会自动改变这三个投影。

## 3. 已经完成了什么

### 3.1 仓库与公开开发基线

- 权威公开仓库是 `Eclipseic1848/Mangrove_ai`，默认开发分支是 `main`。
- 旧仓库保留为 `legacy-origin` / `legacy-platform`，只用于历史证据；没有删除或重写。
- 首次公开快照不继承旧私有 Git 历史，避免本机 Agent 配置、数据库和运行数据进入公开历史。
- README、MIT License、贡献、安全、行为准则、Issue/PR 模板和第三方声明已齐备。
- 本机 `start_all.bat`、`stop_all.bat`、`.env`、数据库、日志、任务制品、浏览器登录态和本地审计
  不进入 Git。
- #9 经 PR #19 合并到 `main`，merge commit 为 `5587043c...`，Issue #9 已关闭。

### 3.2 产品与交付主链

- Conductor 公域采集主链可用。
- `/data-prep` 是统一正式工作台，支持不可变 revision、取消、恢复、版本、来源、结果预览、
  回收站和正式交付。
- PDF、Word、Excel、CSV 等本地文件代表主链可用。
- CSV、JSONL、Parquet、XLSX、JSON、DOCX、PDF、HTML、Markdown、TXT、PPTX 共 11 种交付
  预览已工程验证；TSV 不是界面正式交付格式。
- vNext Delivery Publisher 已实现：只有独立验证、完整性和 QA 通过的 Candidate 才能形成
  正式 Delivery。
- 覆盖感知文档检索、对话转向/上下文编译、多模型连接和 TaskRevision 冻结已工程实现或通过
  代表任务验证。

必须继续使用的交付语义：只有 `delivery_published` 且完整性/QA 通过的 `output_id` 是正式
交付。Candidate、`eligible_for_delivery`、中间 AST、Parquet、工具成功或验证通过状态都不能
冒充正式交付。

### 3.3 Agentic Capability 基础

| 能力 | 当前事实 | 仍缺 |
| --- | --- | --- |
| AC-04 能力目录 | Pack/Component/Procedure/Validation/Selection、Owner 隔离、digest 冻结与 OCI Layout Adapter 已实现 | 完整用户代表验收按后续票推进 |
| AC-05 隔离能力获取 | 来源、Grant、获取网络、离线构建、缓存、预算、取消、恢复和清理门已工程验证 | 生产迁移与用户验收 |
| AC-06 Adapter + Sidecar | Python Tool、stdio MCP、任务级 Capability Host、工作台选择和冻结 revision 已通过管理员灰度验收；默认关闭 | 远程 MCP、Registry 发现、普通用户开放 |
| `Eclipseic1848/Mangrove_platform#33` | 三轴治理投影、兼容读取、迁移和用户验收完成 | 无；保留历史记录 |
| `Eclipseic1848/Mangrove_platform#34` | 精确 digest 的可恢复 ValidationRun、两项真实能力灰度、取消后重新发起、生产迁移和 8088 验收完成 | 无；保留历史记录 |
| `Eclipseic1848/Mangrove_platform#35` | Trivy/Syft 供应链证据、真实双包扫描、双轴复审、生产迁移和 8088 验收完成 | 无；保留历史记录 |
| 新仓库 #9 | 标准 OCI image signature 本地 PoC、双轴复审、真实验收和发布闭环完成 | 无；Issue 已关闭 |
| 新仓库 #10 | 个人能力自动晋级 `verified`：判定门、幂等/并发、缺口投影、worker 双触发、Python/MCP 双夹具、双轴复审、生产迁移 0004、8088 页面验收与 PR #20 合并完成 | 无；Issue 已关闭；真实灰度包晋级留待 #15/#16 |
| 新仓库 #11 | 管理员审核与业务内容审计查看：跨 Owner 审核聚合、三组分组、渐进披露、审计查看命令（原因必填、任务与验证证据绑定、失败留痕、2MiB 按块截断）、零 DDL、双轴审查修复复核、8088 用户验收完成 | 无；Issue 已关闭 |
| 新仓库 #12 | 独立平台快照、签名与 admin_gray 发布机制：候选门、脱敏快照（白名单重写/确定性重打包）、平台六步验证（目录级探针）、#9 签名直用、发布/受众变更命令、生产接线、Lease、四轮双轴复核、生产迁移 0005、8088 用户验收完成 | 真实装载执行探针与真实发布纵切面留待 #15/#16；Issue 已关闭 |
| 新仓库 #13 | CapabilityMountResolver 运行时装载治理门：唯一装载 Seam 的个人三轴（Owner/verified/{active,deprecated}/eligible）与平台受众/签名门、冻结三轴可选谓词、30s 只读投影运行期监督（隔离/撤销 → 停 Sidecar + 取消 + 禁发布）、legacy 平台包旧路径放行、无能力任务零负担、验证重放前置门；61 项新测试、双轴审查两轮（A1-A5/B1-B6 全部修复）、8088 验收（真实物化/422/404/409×2/列表过滤/取消零残留）完成 | 完整任务执行受本地 LLM 环境限制未达成（非治理门问题）；PR #24 已合并，Issue 已关闭 |
| 新仓库 #14 | 弃用/回滚/隔离/撤销与限期风险接受：四类治理事件与 validator（事件快照=写入时刻投影）、六个管理员命令 + change_audience 路由（幂等键/预期状态/409 失败关闭）、投影逐轴折叠 + 风险接受惰性到期（零调度零 DDL）+ 推荐指针置顶、发布/受众/恢复三门补 7 天漏洞库时效复查、风险接受硬约束（admin_gray/blocker 全拒绝/finding_ref 实引/1-90 天）、零 DDL；68 项新测试、双轴审查两轮复核「无新问题，可合入」、8088 验收（9 命令演练 + 两轴投影 + 零残留）；PR #25 合并，Issue 已关闭 | 真实 risk_accept applied 链与自动隔离触发接线留待 #15/#16 |

### 3.4 #9 本地标准 OCI 签名闭环

#9 固定并验证：

- Cosign `3.0.6`
- ORAS `1.3.2`
- Zot `2.1.20`
- 官方来源方法、可执行文件 SHA-256、镜像版本/tag/digest 和 Zot release commit

真实路径：

```text
冻结 OCI Layout
  → 仅监听 127.0.0.1 的短期 Zot Registry
  → Cosign 按主体 digest 生成标准 OCI image signature
  → ORAS 递归复制主体和 Referrers
  → 新的独立 OCI Layout
  → 重新上传并由 Cosign 使用公钥验证
```

已验证成功与失败路径：

- Python 表格 Tool 与 Everything MCP 两个冻结 digest 均签名成功；
- 首次写 `passed` 前重开独立 Layout，验证主体 digest、签名、Referrers 和公钥身份；
- 错误公钥和主体 manifest blob 篡改被拒绝；
- 预启动取消、ORAS 重验期间取消、取消回调自身异常、真实进程崩溃和重复执行均失败关闭；
- Windows 只读 OCI blob 可以在本事务失败时安全清理；
- 非法 transaction ID 不能借递归清理逃出专用 work root；
- 临时 Registry、容器、运行存储和命名网络零残留；
- 私钥使用加密 Sigstore 格式，位于项目、数据库和任务目录之外；口令不进入 argv、日志或证据；
- `sign-blob` 没有被当作标准 OCI image signature 的替代方案。

最终工程证据：八个 Capability 测试文件 `133 passed`，Standards 与 Spec 最终复审均为 PASS，
用户验收通过。详见：

- `docs/plans/2026-08-13-agentic-capability-ac07-04-execution-report.md`
- `src/capability_governance/oci_signing.py`
- `src/capability_governance/tool_lock.py`
- `scripts/verify_capability_signing_ac07.py`
- `tests/test_capability_signing.py`

### 3.5 近期较大范围验证快照

这些数字是历史验证证据，接手后不得当成当前未经复跑仍成立的结果：

- 后端全仓：`1249 passed, 4 skipped`
- 首次公开快照 Capability 聚焦：`75 passed`
- 前端 TypeScript 与 Vite 生产构建：通过
- Playwright 单 worker：`54 passed`
- 本机启停专项：`3 passed`
- #35 最终供应链/治理回归：`92 passed`
- #9 合并前八个 Capability 文件：`133 passed`
- #13 合并前：Capability 九文件 `118 passed`、后端全量 `1092 passed`（1 项 DNS 环境基线失败）、
  Playwright settings + semantic-workspace `38 passed`
- #14 合并前：治理/运行时门回归 `256 passed`、后端全量 `1166 passed`（1 项 DNS 环境基线失败）、
  前端构建通过、Playwright `38 passed`

## 4. 当前卡在哪里

没有已知代码级硬阻塞，也没有在运行的实现任务。当前停在“进入 #15 前的人控阶段门”：

1. #15 Issue 已存在，但新会话仍要核对其规格与当前代码是否漂移。
2. 用户尚未授权 #15 开工；不要把“下一任务是 #15”理解为已授权实现。
3. #15 是真实治理纵切面：涉及真实验证证据、晋级、平台发布与治理命令 applied 链，
   对生产灰度包（gray-python-table）执行任何治理动作都必须单独授权。

另有开放环境事实（现场核验）：

- 8088 上运行着一个开发后端进程（受 logs/dev_reload.log 记录的热更新监督进程自动重启）；
  生产库 `data/webui.db` 中保留两个 #13/#14 验收样本
  （`accept-s8-draft-sample` 个人 draft、`accept-s8-deprecated-sample` 平台
  deprecated+quarantined，幂等键 `accept:q1`），是 #15/#16 夹具；复原或删除需用户授权。

另有不属于 #10 的开放风险：

- 30 项泛化集未完成；
- Word/Excel 连续生产门与完整 PG-05 未完成；
- 真实外部 Provider 的 Pi→Relay→Provider 安全端到端未完成；
- Rollout P0 GateSnapshot 与默认入口切换未完成；
- 远程 MCP/Secret、Registry 自动发现和平台能力普通用户开放未完成；
- 8B Linux/Compose/并发/故障注入与目标服务器验证未完成；
- GitHub 最近一次 push 提示默认分支存在大量 Dependabot 告警；数量和严重度是时效信息，后续
  安全工作必须从 GitHub 现场重新查询，不要引用旧数字直接决策。

上述任一项都不能因 #9 或局部回归通过被表述为 Phase 4、生产门或稳定版本完成。

## 5. 下一步计划：新仓库 #15

工单：`Eclipseic1848/Mangrove_ai#15`，Python 表格 Tool 真实治理纵切面。

### 5.1 前置状态

#14 发布链已收口（PR #25 合并 `5ad3a472`，Issue #14 已关闭，本地 main 与 origin/main
一致，工作树干净）。#15 开工前按历史 PR 流程走 `codex/ac07-10-*` 分支。

### 5.2 #15 目标（开工前仍需现场复核 Issue 与规格）

- gray-python-table 的真实治理纵切面：真实验证证据、晋级、平台发布、治理命令与
  #14 机制的真实 applied 链（含 risk_accept、自动隔离触发接线）。
- #14 预留的自动隔离触发点（Q1A 留 #15/#16）接入真实证据链。

### 5.3 参考流程

沿用 #10～#14 的纵向流程：需求/规格复核 → 领域/接口设计 → 任务拆分 → TDD 实现 →
冻结夹具验证 → Standards/Spec 双轴审查 → 用户验收 → 发布动作逐项授权。

## 6. AC-07 工单 Roadmap

权威父工单是新仓库 #8。本文 AC-07 的历史 #33～#35 均指
`Eclipseic1848/Mangrove_platform`，不能静默解释成新仓库同号 Issue，也不能误投到
`Eclipseic1848/mangrove`。

| 顺序 | 工单 | 目标 | 依赖与现场状态 |
| ---: | --- | --- | --- |
| 1 | `Eclipseic1848/Mangrove_platform#33` | 三轴治理投影与兼容读取 | 已完成、迁移、验收并关闭 |
| 2 | `Eclipseic1848/Mangrove_platform#34` | 精确 digest 的可恢复 ValidationRun | 已完成、生产迁移、真实灰度、验收并关闭 |
| 3 | `Eclipseic1848/Mangrove_platform#35` | Trivy/Syft 供应链证据闭环 | 已完成、生产迁移、验收并关闭 |
| 4 | 新仓库 #9 | Cosign 本地 OCI image signature PoC | 已完成；PR #19 合并，Issue 已关闭 |
| 5 | 新仓库 #10 | 个人能力自动晋级 `verified` | 已完成；PR #20 合并，Issue 已关闭；真实晋级留待 #15/#16 |
| 6 | 新仓库 #11 | 管理员审核与业务内容审计查看 | 已完成；Issue 已关闭 |
| 7 | 新仓库 #12 | 独立平台快照、签名与 `admin_gray` 发布 | 已完成；Issue 已关闭；真实探针与真实发布纵切面留待 #15/#16 |
| 8 | 新仓库 #13 | CapabilityMountResolver 运行时治理门 | 已完成；PR #24 合并，Issue 已关闭 |
| 9 | 新仓库 #14 | 弃用、回滚、隔离、撤销与限期风险接受 | 已完成；PR #25 合并，Issue 已关闭 |
| 10 | 新仓库 #15 | Python 表格 Tool 真实治理纵切面 | **下一工单**；依赖 #14 |
| 10 | 新仓库 #15 | Python 表格 Tool 真实治理纵切面 | 开放；依赖 #14 |
| 11 | 新仓库 #16 | Everything MCP 真实治理纵切面 | 开放；依赖 #14，可与 #15 分别验收 |
| 12 | 新仓库 #17 | AC-06 兼容切换与 AC-07 综合验收门 | 开放；依赖 #15、#16，完成前不能称 AC-07 收口 |

#14 依赖（旧 #35、新 #13）已具备前置事实；不要自行并行扩大范围。

## 7. 整个工程 Roadmap

以下分为“当前权威主线”和“历史规格中的方向性后续”。方向性后续不是已授权排期。

### 7.1 当前权威主线

1. **完成 AC-07 #10～#17**：建立个人验证、管理员审核、平台快照、签名、运行门、生命周期、
   两条真实纵切面和 AC-06 兼容切换。
2. **补齐 Phase 4 未完成门**：30 项泛化、完整 PG-05、真实外部 Provider 安全端到端、P0
   GateSnapshot、默认入口切换、8B Linux/Compose/并发/故障与目标服务器验证。
3. **完成生产资格审计**：全仓回归、真实数据/任务、权限与安全、备份恢复、可观测性、资源清理、
   文档一致性和用户验收。

### 7.2 已落地但不能称整体封板的 Phase 4 基础

- Phase 4A 的文档解析、EvidenceRef、复核和交付基础已进入当前产品主链。
- Phase 4B 的语义任务 Harness、能力包、有界修复 Loop、输出/下载和正式工作台已有大量实现。
- Agentic Runtime vNext、统一任务域、Delivery Publisher、Provider 连接、受控外发、覆盖感知检索
  和 Agentic Capability 已形成当前主架构。
- 8B、完整 PG-05、默认切换和综合生产门仍未完成，所以不能宣布 Phase 4 封板。

### 7.3 方向性后续（必须重新规格化和授权）

| 阶段 | 方向 | 当前边界 |
| --- | --- | --- |
| Phase 4C | 图片、音频、视频解析接入同一 Harness | 只有部分原型/接口证据；未形成完整生产链 |
| Phase 5A | 认证网站、只读 API、企业来源发现 | ADR 已采纳；安全隔离、确认和数据外发仍需专项实现 |
| Phase 5B | Recipe、模板、增量、队列、配额、生命周期、质量运营和生产工程化 | 尚未进入当前实施主线；不得提前引入分布式复杂度 |

企业 API、业务系统、本地路径、对象存储、远程 MCP、OAuth、通用 Registry 自动发现、
多租户团队权限和大规模分布式执行都不能从现有本地灰度能力中推断为已完成。

## 8. 版本计划

### 8.1 已验证版本事实

- 当前仓库唯一现场可见的稳定封板标签是 `v0.0.4`，不得移动或回写。
- 公开 `main` 承接原 `v0.0.8` 开发能力，但 `v0.0.8` **没有同名标签、没有封板，也不是稳定
  生产版本**。
- 当前 `main` 提交为 `5587043c...`；它是公开开发基线，不是 Release 声明。
- `SECURITY.md` 将当前状态描述为 `v0.0.8` 开发阶段，安全修复优先进入 `main`。

### 8.2 尚未冻结的版本决策

- 没有已确认的下一个版本号、发布日期、RC 日期或稳定发布日。
- 没有授权创建 `v0.0.8`、`v0.0.9` 或其他标签/Release。
- AC-07 完成不自动等于 `v0.0.8` 可以发布；Phase 4 未完成门仍需独立评估。

### 8.3 建议的下一次版本决策门（建议，不是已确认计划）

在讨论新的 tag/Release 前，至少应重新确认：

1. 计划纳入版本的工单范围与非目标；
2. AC-07 是否要求 #17 综合门全部通过；
3. 完整后端、前端构建、Playwright、Docker/Linux 和代表真实任务证据；
4. 权限、供应链、签名、Dependabot/Secret、安全外发和残留资源审计；
5. 数据库迁移备份、重复迁移、恢复演练和旧数据零改写；
6. 8088 用户验收、升级/回滚说明、部署文档和已知限制；
7. 用户分别授权版本号、tag、Release、Push 和任何包/镜像发布。

## 9. 稳定业务与安全边界

- `8088` 是统一产品入口；`5173` 只用于前端开发。
- `/data-prep` 是主工作台；迁移完成前不得删除历史任务兼容入口或 Legacy Delivery 读取。
- TaskRevision、来源快照、连接版本、外发确认、能力 digest 和 Owner 隔离必须冻结且失败关闭。
- AC-06 两项历史 `admin_gray_only` 包只是迁移兼容例外，不扩大普通用户权限。
- 普通用户、管理员、超级管理员是产品角色；“高级用户”不是权限角色。
- 管理员可以查看跨 Owner 的任务管理元数据；读取个人业务正文必须填写原因并产生不可变审计。
- 无能力任务不能创建治理运行、扫描器或 Sidecar，也不能增加启动负担。
- 外部模型、采集器、下载源、Registry、镜像和代理变化都可能改变数据外发与安全语义，必须确认。
- 用户控制业务范围、数据含义、权限、生产迁移、能力晋级、平台发布、受众开放和不可逆操作。

## 10. 绝对不要再踩的坑

### 10.1 状态与范围

- **不要把测试、Code Review 或一次真实任务当成用户验收。** 用户验收必须由用户明确确认；生产
  资格和版本发布仍是另一道门。
- **不要把 Candidate、验证通过、`eligible_for_delivery` 或中间文件称为正式交付。** 只认
  `delivery_published` 且完整性/QA 通过的 `output_id`。
- **不要把局部审计、PoC 或 AC 工单完成称为 Phase 4 完成。** 报告必须区分已验证事实、代码
  推断和尚未验证的建议。
- **不要自动进入下一 Skill 或阶段。** 需求、规格、拆票、实现、审查、迁移、验收和发布之间都
  要展示产物与未决问题，等待用户确认。
- **不要顺手重构、扩大权限或合并工单。** 每一行改动都应能追溯到当前工单。

### 10.2 仓库、Issue 与文档

- **不要用错仓库或 Issue 编号。** 新工单只认 `Eclipseic1848/Mangrove_ai`；AC-07 旧 #33～#35
  只认 `Eclipseic1848/Mangrove_platform`。`origin` 是公开开发远端，`legacy-*` 只是历史远端。
- **不要只看 `git diff <base>...HEAD` 审查 WIP。** HEAD 可能和基线相同，而关键文件仍是
  untracked；必须同时看 `git status --short --untracked-files=all` 并逐一审查新增文件。
- **不要相信交接中的旧 SHA、分支、测试数、端点或 Issue 状态。** 这些都是时效信息，开工先现场
  核验。
- **不要让多个 Markdown 同时维护滚动状态。** `docs/status/current.md` 是唯一状态台账；
  `handoff.md` 只做接手快照与下一门禁；ADR/规格/报告完成后保持历史证据身份。
- **不要删除历史计划来“清理过期内容”。** 标记 historical/superseded 并指向当前权威文档，
  除非用户明确授权删除且引用扫描证明安全。

### 10.3 Git 与发布

- **禁止 `git add .`、`git add -A`、强推、`git reset --hard` 和 `git clean`。** 混合工作树只能用
  明确文件允许列表。
- **不要直接在默认分支偷偷提交。** 需要发布时按授权创建 `codex/` 功能分支、提交、推送、PR、
  合并；每个外部动作都要有授权。
- **「同意全部」不覆盖 PR 合并步。** #13/#14 两次都被权限分类器拦截：用户对「提交/推送/PR/
  关闭」清单回复「同意全部」后，合并仍需用户单独指名「合并」再执行；Issue 关闭由 PR 正文
  `Closes #N` 自动完成，无需手动 close。
- **不要提交本机路径、`.env`、Secret、数据库、日志、任务数据、签名私钥、浏览器状态、Agent
  设置或本地审计。** 私钥从 Git 历史删除也不等于已撤销，误泄漏必须轮换。
- **不要因为下载慢就更换工具、版本、镜像、镜像源、URL、安装方式或实现路线。** 只能做语义
  不变的重试；替代方案先解释差异与风险并取得批准。

### 10.4 AC-06 / AC-07 与签名

- **Everything MCP 灰度样本是 MCP 协议测试服务器，不是 Voidtools Everything 文件搜索。** 如需
  真实本地文件搜索，必须新建能力、冻结包与 digest、重新验证，不能静默替换或宣传错位。
- **不要用 `cosign sign-blob` 冒充标准 OCI image signature。** #9 已证明的路径是短期回环
  Registry + digest 签名 + OCI Referrers + 独立 Layout。
- **签名工具锁不能只检查 `verified=true`。** 必须绑定版本、来源方法、身份/commit、可执行文件
  digest，以及 Zot 镜像 tag 与 digest。
- **签名密钥不能只检查“文件存在”。** 私钥必须是加密 Sigstore 格式，位于项目、数据库和任务
  根之外；口令只进受控子进程环境，不进 argv、日志、Prompt、事件或证据。
- **递归清理前必须验证 transaction ID 和解析后的绝对路径。** 只能删除专用 work root 下本事务
  新建的精确目录；Windows 只读 OCI blob 需要显式变为可写后重试。
- **取消回调自身抛错时也要先 terminate/kill/communicate 回收子进程。** 否则诊断异常会留下失控
  ORAS/Cosign 进程和文件占用。
- **测试运行中取消时必须确认子进程已经真正启动。** 曾出现回调先于 PID 文件写入，导致测试时序
  波动；探针应等待可观察启动条件再触发异常或取消。
- **不要要求旧缓存立即具备新 sidecar 元数据而没有兼容/回填策略。** 新完整性门不能让升级前
  已物化的灰度包全部失效，也不能改写旧 digest、历史验证或冻结选择。
- **不要仅凭源码猜 Docker Inspect 字段。** `--tmpfs` 等配置可能位于 `HostConfig.Tmpfs` 而不是
  顶层 `Mounts`；提出安全 finding 前必须用真实 Docker 行为或官方资料核实。
- **治理命令的幂等检查必须先于预期状态检查。** 同幂等键重试必须返回既有事件（already_applied），
  不能在状态已变化后先被前置检查打成 rejected；#14 曾因此把幂等重试打成 409。
- **多事件命令的非原子部分应用不能被幂等吞掉。** restore 写两条事件（资格+生命周期）：任一
  幂等键命中都不能无条件返回，必须按投影补写缺失的另一条；#14 双向都踩过。
- **治理事件快照必须与写入时刻投影一致，不冒充他态。** 隔离中的弃用/撤销事件必须携带
  quarantined 资格快照（validator 相应放宽），否则审计事件流会伪造状态。
- **两轴写序视状态选择。** revoked+quarantined 恢复必须先写生命周期（携带 quarantined 快照）
  再解除隔离；反序会撞 validator（eligibility_changed 只允许 active/deprecated 生命周期）。

### 10.5 Runtime、网络与本机运维

- **Capability Host 内网请求不能被业务外发代理接管。** `NO_PROXY` 只加入当前任务的确定性 Host
  DNS，不能放宽为任意主机、网段、端口或外部目标。
- **不要把辅助 Docker 容器失败直接当成 8088 服务失败。** 先检查后端日志、端口和
  `/api/health`。
- **FastAPI 启动阶段不要直接运行同步 embedding/rerank 网络调用。** 不可达本地模型曾让服务卡在
  `Waiting for application startup`；阻塞工作应离开事件循环并有超时/降级边界。
- **Windows PowerShell 可能拦截 `npm.ps1`。** 需要时使用 `npm.cmd`，但不要因此改变依赖或构建
  语义。
- **所有中文文本都显式使用 UTF-8。** 出现乱码先修编码，不能把乱码写进规格、日志或数据库。
- **停止脚本只能清理经项目路径、标记或祖先进程验证的进程树。** 未知端口占用只能报警，不能
  广泛杀进程。
- **8088 后端受热更新监督进程托管。** `logs/dev_reload.log` 记录自动重启：修改 src/*.py 会
  自动重启网关，杀掉的 `python -m src.api.main` 进程会被自动恢复——排查「幽灵进程/端口占用」
  先看这个日志，不要反复杀进程。
- **PowerShell 代码页影响 pytest 子进程。** `test_stop_helper_preserves_unrelated_port_listener`
  在 GBK 代码页下因 stdout 乱码失败；全量测试先 `chcp 65001` 再跑。
- **全量 pytest 偶发卡在 IO 等待（曾 33 分钟无输出）。** 用 `-o faulthandler_timeout=120` 重跑
  可定位并保护；重跑通常正常完成。

## 11. 权威资料索引

### 当前状态与规则

- `AGENTS.md`
- `docs/status/current.md`
- `CONTEXT.md`
- `docs/agents/issue-tracker.md`
- `docs/agents/triage-labels.md`
- `docs/agents/domain.md`

### AC-07

- 规格：`docs/plans/2026-08-06-agentic-capability-ac07-spec.md`
- ADR：`docs/adr/0029-capability-validation-lifecycle-and-platform-publication.md`
- #34 报告：`docs/plans/2026-08-07-agentic-capability-ac07-02-execution-report.md`
- #35 报告：`docs/plans/2026-08-07-agentic-capability-ac07-03-execution-report.md`
- #9 报告：`docs/plans/2026-08-13-agentic-capability-ac07-04-execution-report.md`
- #10 需求复核：`docs/plans/2026-08-14-agentic-capability-ac07-05-requirements-review.md`
- #10 设计：`docs/plans/2026-08-14-agentic-capability-ac07-05-design.md`
- #11 需求复核：`docs/plans/2026-08-14-agentic-capability-ac07-06-requirements-review.md`
- #11 设计：`docs/plans/2026-08-14-agentic-capability-ac07-06-design.md`
- #11 任务拆分：`docs/plans/2026-08-14-agentic-capability-ac07-06-task-breakdown.md`
- #12 需求复核：`docs/plans/2026-08-14-agentic-capability-ac07-07-requirements-review.md`
- #12 设计：`docs/plans/2026-08-14-agentic-capability-ac07-07-design.md`
- #12 任务拆分：`docs/plans/2026-08-14-agentic-capability-ac07-07-task-breakdown.md`
- #13 需求复核：`docs/plans/2026-08-14-agentic-capability-ac07-08-requirements-review.md`
- #13 设计：`docs/plans/2026-08-14-agentic-capability-ac07-08-design.md`
- #13 任务拆分：`docs/plans/2026-08-14-agentic-capability-ac07-08-task-breakdown.md`
- #14 需求复核：`docs/plans/2026-08-16-agentic-capability-ac07-09-requirements-review.md`
- #14 设计：`docs/plans/2026-08-16-agentic-capability-ac07-09-design.md`
- #14 验收方案：`docs/plans/2026-08-16-agentic-capability-ac07-09-acceptance-plan.md`

### Phase 4 与长期方向

- Phase 4B Harness：`docs/plans/2026-07-24-phase4b-semantic-task-harness-plan.md`
- Phase 4 当前问题审计：`docs/plans/2026-08-02-phase4-current-issues-audit.md`
- Agentic Runtime vNext：`docs/adr/0017-agentic-runtime-vnext.md`
- Delivery 状态机：`docs/adr/0019-vnext-delivery-and-default-cutover-state-machine.md`
- Provider/外发：`docs/adr/0020-provider-connection-broker-and-credential-isolation.md`

## 12. 新会话的第一轮输出应该是什么

读取上述资料和 Issue #15 后，先给用户一份只读阶段判断，不要立即实现。至少说明：

1. 当前阶段：#15 开工前的需求/规格复核（#9～#14 已收口）。
2. 已验证事实：#9～#14 已关闭、#15 开放、前置全部具备、当前 Git 实况。
3. 基于代码的推断：#14 治理命令/风险接受/惰性到期与 #15 真实证据链的衔接点（自动隔离
   触发接线、真实 risk_accept applied 链）。
4. 尚未验证的建议：#15 的最小纵向切片、测试策略和是否需要数据库变化。
5. 必须由用户确认：真实验证/晋级/发布的范围、治理命令对生产灰度包的执行、真实任务、
   Commit/Push/PR/Issue 写入。
6. 根据开发计划，#15 完成后的下一任务是 #16；但不得自动进入。

如果用户只说“继续”，优先完成当前已确认阶段，不要把整个 #15 拆成没有价值的微步骤，也不要
越过用户控制点。
