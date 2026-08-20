# Mangrove 零上下文交接

> 文档用途：写给完全没有历史对话的新会话
>
> 最后现场核验：2026-08-19
>
> 当前分支：`main`；HEAD = `95872a01`（#15 收口，PR #30 合并）；**工作树干净**
>
> 公开远端：`origin` → `https://github.com/Eclipseic1848/Mangrove_ai.git`
>
> 当前阶段：**#17 已完成（AC-06 兼容切换 + AC-07 综合验收门）**；AC-07 主线
> 可收口；后续方向（#18+ / Phase 4 剩余门）停在授权门

## 0. 一句话结论

Mangrove 正在把「能运行的个人能力」推进为「证据完整、可审计、可失败关闭、以后可以由管理员
发布的平台能力」。AC-07 #9～#14（签名、自动晋级、审核、平台快照、运行时门、生命周期治理）
已全部关闭；**#15 已真实走通「个人 draft → 验证五步 → 供应链 → 晋级 verified → 脱敏快照
→ Cosign 签名 → 六步 → admin_gray 发布 → 管理员真实装载 → 治理动作链（回滚/deprecated/
revoked/跨用户拒绝/篡改→自动隔离→restore→真实 risk_accept applied 链→惰性到期→手动重扫）」
全链**；**#15 已收口**（PR #30 `95872a01`、Issue #15 CLOSED）；**#16 Everything MCP 纵切面
已真实完成**（阶段 0-6：协议型能力三类验证、单 Sidecar 双能力、篡改/跨 Owner/并行版本治理链、
协议纵深超时取消进程异常、零残留；AC1-AC7 全部 ✅，执行报告
`docs/plans/2026-08-19-agentic-capability-ac07-11-execution-report.md`）。
生产库现有三条 verified 个人能力、四条已发布平台能力（python-table 2.0.0/3.0.0 +
everything-mcp 2026.7.4 active/2026.8.19 revoked 牺牲版本）、完整治理事件流。
**#17 AC-06 兼容切换与 AC-07 综合验收门已真实完成**（AC1-AC7 对照 ✅，执行报告
`docs/plans/2026-08-20-agentic-capability-ac07-12-execution-report.md`）；
AC-07 主线 #9-#17 全部关闭，普通用户开放与 AC-08/AC-09/8B 等边界未完成（见报告 §AC7）。

不要把 #15 完成表述成能力已向普通用户开放、Phase 4 已完成或任何版本已发布。
平台发布受众固定 admin_gray，普通用户开放与版本发布均未发生。

## 1. 新会话必须先做什么

本文件是第一个入口。打开本文件后按以下顺序继续读取，不要直接改代码：

1. `AGENTS.md`：仓库工程规则、稳定业务边界和 Git/发布权限。
2. `docs/status/current.md`：当前能力与路线状态的唯一滚动台账。
3. `CONTEXT.md`：领域词汇和长期语义。
4. `docs/agents/`：Issue、标签和领域文档约定。
5. 当前工单 #15、AC-07 规格、ADR-0029，以及 #15 的
   `docs/plans/2026-08-16-agentic-capability-ac07-10-{requirements-review,design,task-breakdown,acceptance-plan}.md`。
   （注：本次需求复核未单独成文，Q1-Q8 决策记录在设计文档；#15 阶段执行证据在
   `scripts/ac07_10_*.py` 与生产库。）

现场执行：

```powershell
git status --short --branch --untracked-files=all
git rev-parse HEAD
git rev-parse origin/main
gh issue view 15 --repo Eclipseic1848/Mangrove_ai --comments
```

预期现场状态是：

- 本地 `main` 在 `95872a01`（= origin/main，#15 PR #30 合并）；工作树干净；
- 新仓库 #9～#14 为 `CLOSED`；#15/#16/#17 为开放；
- 生产库 `data/webui.db`：
  - `gray-python-table@2.0.0`（personal digest `59076f40…`）/ `3.0.0`（`0ca80afd…`）
    为 personal verified；同版本另有 platform 行（2.0.0 平台 digest `e5556f83…`，
    3.0.0 平台 digest `9379fe29…`，admin_gray，`platform_published` 事件各 1 条）；
  - 1.0.0 仍是 legacy 平台包；everything-mcp、accept-s8-* 样本未动；
  - 治理事件：`platform_published`×4、`platform_candidate`×4、`promoted_to_verified`×2、
    `recommendation_changed`×2、`lifecycle_changed`×2（deprecated+revoked 2.0.0）、
    `eligibility_changed`×9（含篡改演示自动隔离 actor=system、阶段 6 隔离与恢复）、
    `risk_accepted`×1（阶段 6 applied 链）、`rescan_completed`×1（阶段 6 真实重扫）、
    `audit_viewed`×1、`registered`×6（含重建留痕）；共 32 条；
  - 平台验证运行 4 条（两条初版 + 两条重建后）；Lease 0；3.0.0 供应链证据 4 行（追加不覆盖）；
- 平台签名密钥在 `~/.mangrove-signing/`（加密 Sigstore，项目外），`.env` 已配置
  `CAPABILITY_PLATFORM_SIGNING_PRIVATE_KEY/PUBLIC_KEY`（gitignored）；
- 只有现场命令能证明当前状态，本段 SHA/digest 只是 2026-08-18 的交接快照。

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

当前主线 AC-07 解决最后一段「能力如何建立信任并被治理」：

```text
个人 draft 能力
  → 精确 digest 的 ValidationRun（五步：Smoke/真实重放/失败关闭/Verifier/清理）
  → Trivy / Syft 供应链证据（7 天漏洞库时效是硬门）
  → verified（自动晋级，判定门 + 幂等）
  → 管理员审核（#11）
  → 独立脱敏平台快照 + Cosign 签名（#12）
  → admin_gray 发布
  → 运行时签名/受众/三轴治理门（#13）
  → 弃用/回滚/隔离/撤销/限期风险接受（#14）
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
- 旧仓库保留为 `legacy-origin` / `legacy-platform`，只用于历史证据。
- 首次公开快照不继承旧私有 Git 历史；本机 Agent 配置、数据库和运行数据不入库。
- README、License、贡献/安全/行为准则、Issue/PR 模板齐备；`v0.0.4` 是唯一稳定封板标签。
- 本机 `start_all.bat`/`stop_all.bat`、`.env`、数据库、日志、任务制品、浏览器登录态、本地审计
  不进入 Git。

### 3.2 产品与交付主链

- Conductor 公域采集主链可用；`/data-prep` 统一正式工作台（不可变 revision、取消、恢复、
  版本、来源、结果预览、回收站、正式交付）。
- 11 种交付预览已工程验证；vNext Delivery Publisher：只有独立验证、完整性和 QA 通过的
  Candidate 才能形成正式 Delivery。
- 覆盖感知文档检索、对话转向/上下文编译、多模型连接和 TaskRevision 冻结已实现或通过代表任务验证。

必须继续使用的交付语义：只有 `delivery_published` 且完整性/QA 通过的 `output_id` 是正式交付。

### 3.3 Agentic Capability 各工单状态

| 工单 | 当前事实 |
| --- | --- |
| AC-04 能力目录 | 工程验证通过；完整用户代表验收按后续票推进 |
| AC-05 隔离能力获取 | 工程验证通过；生产迁移与用户验收 |
| AC-06 Adapter + Sidecar | 管理员灰度验收通过，默认关闭；远程 MCP/Registry/普通用户开放未做 |
| 旧 #33/#34/#35 | 三轴投影、可恢复 ValidationRun、Trivy/Syft 证据——完成并关闭（历史） |
| 新 #9 | Cosign 本地 OCI image signature PoC——完成并关闭 |
| 新 #10 | 个人能力自动晋级 verified——完成并关闭；真实晋级在 #15 已发生 |
| 新 #11 | 管理员审核与业务内容审计查看——完成并关闭 |
| 新 #12 | 独立平台快照、签名与 admin_gray 发布机制——完成并关闭；**真实发布链在 #15 阶段 3 已首次执行** |
| 新 #13 | CapabilityMountResolver 运行时装载治理门——完成并关闭 |
| 新 #14 | 弃用/回滚/隔离/撤销与限期风险接受——完成并关闭 |
| 新 #15 | **进行中**：见 §3.4 |

### 3.4 #15（AC07-10）Python 表格 Tool 真实治理纵切面——进行中（阶段 0-6 完成）

已完成：

1. **需求复核 Q1-Q8**（全部 A）与设计 D1-D9、任务拆分 S1-S7、验收方案——已确认入库。
   阶段 3-5 真实执行又暴露若干设计未覆盖缺陷，均已修复（见下）。
2. **代码实现（S1-S5 + D9）**：
   - 装载门自动隔离钩子（`auto_quarantine` 可选注入，四验签失败分支触发；默认 None 保持 #13 只读）；
   - 手动重扫命令（`rescan_supply_chain` 服务方法 + `POST /admin/supply-chain-rescan` 端点；
     证据追加不覆盖、BLOCKED 自动隔离、崩溃窗口补写、`rescan_completed` 新事件类型）；
   - **D9 验证任务 Seam**（本票核心新增）：`WorkspaceTaskCreateIn.validation_target` 标记 →
     `CapabilitySelection.validation_target` 冻结持久化 → `check_mount(validation_exempt)` 装载豁免
     （个人+Owner 自己+active+eligible 仍强制；平台包永不豁免）→ `copy_selection_for_owner` 丢弃标记。
     这是「个人 draft 永远无法进入真实任务」断链的补齐。
   - 真实注册脚本 `scripts/prepare_ac07_10_packs.py`、驱动脚本
     `scripts/ac07_10_validation_drive.py`（阶段 2）、`scripts/ac07_10_publish_drive.py`
     （阶段 3）、`scripts/ac07_10_rebuild_platform.py`（方案 A 重建）、
     `scripts/ac07_10_stage4_drive.py`/`ac07_10_stage4_mount_drive.py`（阶段 4）、
     `scripts/ac07_10_stage5_drive.py`（阶段 5）、`scripts/ac07_10_verify_platform_signatures.py`
     （独立 Layout 复验）。
   - 新增测试 60+ 项（钩子/重扫/HTTP/脚本/D9/快照/执行器/签名验证器/restore 复查链），
     聚焦回归全部通过。
3. **阶段 0-2 真实纵切面**：LLM（Qwen3.6-35B-A3B @ 192.168.121.32:6012）与 Docker 可用；
   注册个人 draft 2.0.0/3.0.0（Owner=liyi）；两条真实验证链完成（真实 Pi 任务真实调用
   `capability_python_table_summary`、五步全 passed、供应链 passed、`promoted_to_verified`、
   投影 verified/active/eligible）。
4. **阶段 3 真实平台发布**：平台签名密钥就绪（`~/.mangrove-signing/` 加密 Sigstore 密钥对 +
   口令文件注入，`.env` 配置）；2.0.0/3.0.0 各自独立链（候选→脱敏快照新 digest→六步全绿→
   Cosign 签名同一公钥 `103de227…`→admin_gray 发布）；生产库首次 `platform_published` ×2；
   幂等重放通过；独立 Layout 复验双 PASS；装载门对签名平台包闭环通过。
5. **阶段 4 治理动作链 + 真实装载**：管理员选择列表核验；rollback 推荐指针切 2.0.0↔3.0.0
   （`recommendation_changed`）；deprecate 2.0.0（新任务不可选 + 冻结被拒 + 历史冻结恢复装载
   通过）；**平台能力首次真实装载并调用**（真实 Pi 任务 completed + `capability_python_table_summary`
   工具调用确认）。
6. **阶段 5 revoke/跨用户/篡改**：revoke 2.0.0（历史恢复装载被拒）；跨用户拒绝（liyi111
   普通用户对 admin_gray 被拒）；**篡改演示**（备份→篡改 1 字节→装载 409 fail-closed→自动隔离
   actor=system→restore 复查链→逐字节还原→密码学复验→装载成功）。
7. **阶段 6 真实 risk_accept + 重扫 + 零残留**（2026-08-19 完成）：人工隔离 3.0.0 →
   `accept_pack_risk`（finding_ref 实引平台验证运行 `pfval_2d816c74…`，30 天）→ applied →
   投影 eligible → **惰性到期演示**（验收专用改写 expires_at 为过去，改前记录、改后恢复）→
   投影重新 quarantined（零新事件）→ restore（复查链全绿）→ eligible → **手动重扫**
   （真实采集器：物化 + Trivy/Syft，PASSED 追加证据行 `supply_3402df2c…`，不覆盖旧行）→
   `rescan_completed` → 零残留核验（Lease 全 0、探针无残留、事件计数、投影复验）。
   驱动脚本 `scripts/ac07_10_stage6_drive.py`（支持 `--verify-only` 重跑核验）。

**阶段 3-6 暴露并修复的真实缺陷**（都是真实首跑才暴露，绝对不要再踩，详见 §10.5）：

- 阶段 3：平台快照生成器/六步执行器硬编码 `manifest.json`（真实归档是
  `mangrove-capability.json`）→ 兼容标准名；`materialize_platform` 路径拼接 `Path+str`
  TypeError → 修正括号；发布 Adapter 传绑定方法 vs 对象 AttributeError → 改传实例。
- 阶段 3 重建（方案 A）：平台快照白名单删 `purpose` 导致真实装载 `PI_RUNTIME_FAILED`
  → 快照写中性脱敏 purpose（`_SANITIZED_PURPOSE`），重建发布链（删旧 tag+目录行→新快照
  新 digest→六步→签名→发布）。
- 阶段 5：装载门签名验证只验 signed/<run_id> 副本，篡改主布局 blob 不被检测、自动隔离
  不触发 → `OciPlatformSignatureVerifier.verify` 先校验主布局 subject blob 内容哈希；
  平台 restore 复查链误查个人验证表（validation_incomplete）→ 改查平台验证运行表。
- 阶段 6：`accept_pack_risk` 的 finding_ref 校验误查个人验证运行表（`get_validation_run`
  只查个人表，平台 digest 永不匹配 → 任何平台包 risk_accept 必被拒 finding_ref_unknown）
  → 平台 scope 改从平台验证运行表取证（digest 匹配 + SUCCEEDED，与 restore 复查链同源）；
  回归测试 `test_platform_pack_requires_platform_run_ref` + `TestRiskAcceptCommand`/
  `TestRestoreCommand` 取证表全部同步为平台表（141 项通过）。

未完成（停在授权门）：阶段 7（收口：Issue AC1-AC7 逐条对照 → 执行报告 → 文档同步 → 发布链）。

## 4. 当前卡在哪里

**没有代码级硬阻塞**。AC-07 主线 #9-#17 已全部真实完成并收口。当前停在
「AC-07 之后的方向」授权门：

1. #15/#16/#17 全部交付（执行报告 3 份、PR #30/#33/#34 合并、Issue #9-#17 关闭）；
2. 后续方向待用户定夺：Phase 4 剩余门（30 项泛化、完整 PG-05、真实外部 Provider
   安全端到端、P0 GateSnapshot、默认入口切换、8B Linux/Compose/并发/故障与目标
   服务器验证、生产资格审计）或 AC-08/AC-09；
3. 未开放普通用户、未完成 AC-08/AC-09/8B（#17 报告 §AC7 边界声明）。

开放环境事实（现场核验）：

- 8088 上运行着一个开发后端进程（受 logs/dev_reload.log 记录的热更新监督进程自动重启）；
  本会话修改的 src/*.py 已由热更新加载；
- 生产库 `data/webui.db`：两条 verified 个人能力（2.0.0/3.0.0）、两条已发布平台能力
  （2.0.0 平台 digest `e5556f83…` / 3.0.0 平台 digest `9379fe29…`，admin_gray）、
  两条 legacy 平台包（1.0.0/everything-mcp）与两个 #13/#14 验收样本（accept-s8-*，未动）；
  2.0.0 平台投影 `verified/revoked/eligible`（阶段 5 revoke 已 applied）、
  3.0.0 平台投影 `verified/active/eligible`（篡改演示后已 restore）；
- 平台签名密钥：`~/.mangrove-signing/mangrove-platform-signing.{key,pub}`（加密 Sigstore，
  项目外），口令文件 `COSIGN_PASSWORD.txt` 权限收紧，`.env` 已配置（gitignored）；
  公钥 SHA-256 `103de227b8f5…`；
- 本机 Trivy DB 已更新（UpdatedAt 2026-08-17，7 天时效内）；再次过期后用
  `trivy image --download-db-only --cache-dir data/platform-tools/supply-chain/cache/trivy` 更新
  （mirror.gcr.io 约 4 分钟）；
- 语义验证依赖真实 LLM（Qwen3.6-35B-A3B @ 192.168.121.32:6012），结果有随机性；
  已验证的稳定口径见 §10.5；
- 8088 签名运行时 `get_platform_signing_runtime` 装配了 `password_provider`
  （`_platform_signing_password` 从私钥同目录 `COSIGN_PASSWORD.txt` 读取，失败回退环境变量）。

另有不属于 #15 的开放风险（#10 起保留）：30 项泛化集、完整 PG-05、真实外部 Provider
安全端到端、P0 GateSnapshot 与默认入口切换、远程 MCP/Secret、Registry 自动发现、
平台能力普通用户开放、8B Linux/Compose/并发/故障与目标服务器验证、GitHub Dependabot
告警（时效信息需现场重查）。

## 5. 下一步计划：AC-07 之后（待授权）

### #15/#16/#17 全部完成（2026-08-19/20）

AC-07 主线 #9-#17 全部真实完成并关闭（执行报告：ac07-10/ac07-11/ac07-12 三份）。

### 后续方向（用户定夺，停在授权门）

- Phase 4 剩余门：30 项泛化、完整 PG-05、真实外部 Provider 安全端到端、P0 GateSnapshot、
  默认入口切换、8B Linux/Compose/并发/故障与目标服务器验证、生产资格审计；
- AC-08（普通用户开放决策）、AC-09（远程 MCP/Registry 自动发现）；
- 未开放普通用户、未完成 AC-08/AC-09/8B 是当前明确边界。

## 6. AC-07 工单 Roadmap

权威父工单是新仓库 #8。历史 #33～#35 均指 `Eclipseic1848/Mangrove_platform`，不能混淆。

| 顺序 | 工单 | 目标 | 状态 |
| ---: | --- | --- | --- |
| 1-3 | 旧 #33/#34/#35 | 三轴投影、ValidationRun、供应链证据 | 完成并关闭（旧仓库历史） |
| 4 | 新 #9 | Cosign 本地 OCI image signature PoC | 完成；PR #19 |
| 5 | 新 #10 | 个人能力自动晋级 verified | 完成；PR #20；真实晋级已在 #15 发生 |
| 6 | 新 #11 | 管理员审核与业务内容审计查看 | 完成 |
| 7 | 新 #12 | 独立平台快照、签名与 admin_gray 发布 | 完成；**真实发布链在 #15 阶段 3 已首次执行** |
| 8 | 新 #13 | CapabilityMountResolver 运行时治理门 | 完成；PR #24 |
| 9 | 新 #14 | 弃用、回滚、隔离、撤销与限期风险接受 | 完成；PR #25 |
| 10 | 新 #15 | Python 表格 Tool 真实治理纵切面 | **完成并关闭**（PR #30 合并，Issue #15 CLOSED，2026-08-19） |
| 11 | 新 #16 | Everything MCP 真实治理纵切面 | **完成**（阶段 0-6 真实走通，2026-08-19；发布链收口中） |
| 12 | 新 #17 | AC-06 兼容切换与 AC-07 综合验收门 | **完成**（AC-06 白名单退役 + 迁移演练 + 完整回归 + 浏览器验收，2026-08-20） |

## 7. 整个工程 Roadmap

### 7.1 当前权威主线

1. **完成 AC-07 #15/#16/#17**：#15 进行中；#16 复用 #15 机制；#17 收口。
2. **补齐 Phase 4 未完成门**：30 项泛化、完整 PG-05、真实外部 Provider 安全端到端、P0
   GateSnapshot、默认入口切换、8B Linux/Compose/并发/故障与目标服务器验证。
3. **完成生产资格审计**：全仓回归、真实数据/任务、权限与安全、备份恢复、可观测性、
   资源清理、文档一致性和用户验收。

### 7.2 已落地但不能称整体封板的 Phase 4 基础

Phase 4A 文档解析/EvidenceRef/复核/交付基础已进入产品主链；Phase 4B 语义任务 Harness/
能力包/有界修复 Loop/正式工作台已有大量实现；vNext Runtime/统一任务域/Delivery Publisher/
Provider 连接/受控外发/覆盖感知检索/Agentic Capability 已形成主架构。8B、完整 PG-05、
默认切换和综合生产门仍未完成，所以不能宣布 Phase 4 封板。

### 7.3 方向性后续（必须重新规格化和授权）

Phase 4C（图片/音频/视频解析）、Phase 5A（认证网站/只读 API/企业来源发现）、
Phase 5B（Recipe/模板/增量/队列/配额/生命周期/质量运营）——都未进入实施主线。
企业 API、业务系统、本地路径、对象存储、远程 MCP、OAuth、通用 Registry 自动发现、
多租户团队权限和大规模分布式执行不能从现有本地灰度能力推断为已完成。

## 8. 版本计划

### 8.1 已验证版本事实

- 当前仓库唯一现场可见的稳定封板标签是 `v0.0.4`，不得移动或回写。
- 公开 `main` 承接原 `v0.0.8` 开发能力，但 `v0.0.8` 没有同名标签、没有封板。
- `SECURITY.md` 将当前状态描述为 `v0.0.8` 开发阶段，安全修复优先进入 `main`。

### 8.2 尚未冻结的版本决策

没有已确认的下一个版本号、发布日期、RC 或稳定发布日；没有授权创建任何 tag/Release；
AC-07 完成不自动等于版本可发布；Phase 4 未完成门仍需独立评估。

### 8.3 建议的下一次版本决策门

重新确认：工单范围与非目标、#17 综合门是否要求全过、完整后端/前端/Playwright/Docker 证据、
权限/供应链/签名/Dependabot/外发/残留资源审计、数据库迁移备份/恢复演练/旧数据零改写、
8088 用户验收/升级回滚/部署文档/已知限制、以及用户分别授权版本号/tag/Release/Push。

## 9. 稳定业务与安全边界

- `8088` 是统一产品入口；`5173` 只用于前端开发。
- `/data-prep` 是主工作台；迁移完成前不得删除历史任务兼容入口或 Legacy Delivery 读取。
- TaskRevision、来源快照、连接版本、外发确认、能力 digest 和 Owner 隔离必须冻结且失败关闭。
- AC-06 两项历史 `admin_gray_only` 包只是迁移兼容例外，不扩大普通用户权限。
- 普通用户、管理员、超级管理员是产品角色；「高级用户」不是权限角色。
- 管理员可查看跨 Owner 任务管理元数据；读取个人业务正文必须填写原因并产生不可变审计。
- 无能力任务不能创建治理运行、扫描器或 Sidecar，也不能增加启动负担。
- 外部模型、采集器、下载源、Registry、镜像和代理变化都可能改变数据外发与安全语义，必须确认。
- 用户控制业务范围、数据含义、权限、生产迁移、能力晋级、平台发布、受众开放和不可逆操作。
- #15 新增边界：验证任务豁免（`validation_target`）五条件同时成立才生效
  （个人 + Owner 自己 + active + eligible + 冻结 selection 标记）；平台包永不豁免；
  豁免不随 revision 继承；draft 能力不进入 `/capabilities` 新任务选择列表。
- #15 阶段 5 修复边界：平台装载门签名验证必须绑定主布局主体内容（篡改即拒 + 自动隔离）；
  平台 restore 复查链从平台验证运行表取证（六步全绿 + 签名齐备）。

## 10. 绝对不要再踩的坑

### 10.1 状态与范围

- **不要把测试、Code Review 或一次真实任务当成用户验收。** 用户验收必须由用户明确确认。
- **不要把 Candidate、验证通过、`eligible_for_delivery` 或中间文件称为正式交付。**
  只认 `delivery_published` 且完整性/QA 通过的 `output_id`。
- **不要把局部审计、PoC 或 AC 工单完成称为 Phase 4 完成。** 报告必须区分已验证事实、
  代码推断和尚未验证的建议。
- **不要自动进入下一 Skill 或阶段。** 需求、规格、拆票、实现、审查、迁移、验收和发布之间
  都要展示产物与未决问题，等待用户确认。
- **不要顺手重构、扩大权限或合并工单。** 每一行改动都应能追溯到当前工单。

### 10.2 仓库、Issue 与文档

- **不要用错仓库或 Issue 编号。** 新工单只认 `Eclipseic1848/Mangrove_ai`；AC-07 旧 #33～#35
  只认 `Eclipseic1848/Mangrove_platform`。
- **不要只看 `git diff <base>...HEAD` 审查 WIP。** HEAD 可能和基线相同，而关键文件仍是
  untracked；必须同时看 `git status --short --untracked-files=all` 并逐一审查新增文件。
- **不要相信交接中的旧 SHA、分支、测试数、端点或 Issue 状态。** 这些都是时效信息，开工先现场核验。
- **不要让多个 Markdown 同时维护滚动状态。** `docs/status/current.md` 是唯一状态台账；
  `handoff.md` 只做接手快照与下一门禁。
- **不要删除历史计划来「清理过期内容」。** 标记 historical/superseded 并指向当前权威文档。

### 10.3 Git 与发布

- **禁止 `git add .`、`git add -A`、强推、`git reset --hard` 和 `git clean`。** 混合工作树只能用
  明确文件允许列表。
- **不要直接在默认分支偷偷提交。** 需要发布时按授权创建 `codex/` 分支、提交、推送、PR、合并。
- **「同意全部」不覆盖 PR 合并步。** #13/#14 两次都被权限分类器拦截：用户对「提交/推送/PR/
  关闭」清单回复「同意全部」后，合并仍需用户单独指名「合并」再执行。
- **不要提交本机路径、`.env`、Secret、数据库、日志、任务数据、签名私钥、浏览器状态、Agent
  设置或本地审计。** 私钥从 Git 历史删除也不等于已撤销，误泄漏必须轮换。
- **不要因为下载慢就更换工具、版本、镜像、镜像源、URL、安装方式或实现路线。** 只能做语义
  不变的重试；替代方案先解释差异与风险并取得批准。

### 10.4 AC-06 / AC-07 与签名

- **Everything MCP 灰度样本是 MCP 协议测试服务器，不是 Voidtools Everything 文件搜索。**
- **不要用 `cosign sign-blob` 冒充标准 OCI image signature。** #9 已证明的路径是短期回环
  Registry + digest 签名 + OCI Referrers + 独立 Layout。
- **签名工具锁不能只检查 `verified=true`。** 必须绑定版本、来源方法、身份/commit、可执行文件
  digest，以及 Zot 镜像 tag 与 digest。
- **签名密钥不能只检查「文件存在」。** 私钥必须是加密 Sigstore 格式，位于项目、数据库和任务
  根之外；口令只进受控子进程环境，不进 argv、日志、Prompt、事件或证据。
  本机口令读取：`get_platform_signing_runtime` 用 `_platform_signing_password`
  （私钥同目录 `COSIGN_PASSWORD.txt`，失败回退 `COSIGN_PASSWORD` 环境变量）。
- **递归清理前必须验证 transaction ID 和解析后的绝对路径。** Windows 只读 OCI blob 需要显式
  变为可写后重试（`chmod(S_IWUSR)` → 写 → 恢复原 mode）。
- **治理命令的幂等检查必须先于预期状态检查。** 同幂等键重试必须返回既有事件（already_applied）。
- **多事件命令的非原子部分应用不能被幂等吞掉。** restore 写两条事件：任一幂等键命中都不能
  无条件返回，必须按投影补写缺失的另一条；自动隔离的崩溃窗口同理（重扫事件缺失时补写）。
- **restore 幂等键不能复用掩盖新状态。** restore 成功后又发生新自动隔离时，旧幂等键命中会
  不补写隔离解除 → 投影卡在 quarantined。演示/重放脚本必须用唯一幂等键（含时间戳/序号）。
- **治理事件快照必须与写入时刻投影一致，不冒充他态。** 隔离中的弃用/撤销事件必须携带
  quarantined 资格快照。
- **两轴写序视状态选择。** revoked+quarantined 恢复必须先写生命周期（携带 quarantined 快照）
  再解除隔离；反序会撞 validator。

### 10.5 #15 纵切面专属（新踩的坑，绝对不要再踩）

- **能力归档必须用固定名 `mangrove-capability.tar`（或 .tar.gz/.tgz）。** `_expand_capability_archive`
  只认这三个固定名；带版本后缀不会被物化解压 → mount 目录只有 tar → `load_runtime_manifests` 空
  → Sidecar 不启动 → 能力工具不可用。这是阶段 2 调试最深的根因。
- **能力包 manifest 的标准文件名是 `mangrove-capability.json`，不是 `manifest.json`。**
  mount_resolver 物化展开校验前者；快照生成器与平台六步执行器都必须兼容标准名
  （`_resolve_manifest` 优先标准名、兼容测试旧名），且快照重打包必须保留源 manifest 文件名
  （否则平台快照物化展开失败）。
- **平台快照 manifest 的 `purpose` 必须保留为中性脱敏文案**（`_SANITIZED_PURPOSE`）——
  运行时 `CapabilityRuntimeManifest` 要求 purpose 必填，删除字段会导致真实装载
  `PI_RUNTIME_FAILED`；业务 purpose 不得进入平台内容。
- **平台装载门签名验证必须绑定主布局主体内容。** `verify_local` 只验 signed/<run_id> 副本；
  `OciPlatformSignatureVerifier.verify` 必须先校验主布局 subject blob 内容哈希
  （sha256 == digest），否则篡改主布局不被检测、自动隔离不触发（靠物化兜底）。
- **平台 restore 复查链从平台验证运行表取证。** `get_latest_succeeded_validation_run`
  查个人表，平台能力会误报 `validation_incomplete`；平台目标改用
  `list_platform_validation_runs`（六步全绿 + 签名齐备）。
- **`materialize_platform` 路径拼接不要用 `Path + str`**（运算符优先级会先算 `/` 再 `+`，
  TypeError）；用括号整体拼接 `Path / dir / (f"{a}-{b}" + c)`。
- **发布 Adapter 装配必须传仓库实例（对象），不是绑定方法。** `PlatformPublisherContract`
  期望带 `save_pack` 方法的对象；`get_platform_publication_dependencies` 传
  `SqliteCapabilityCatalogRepository(...).save_pack` 会 AttributeError。
- **instructor v2 strict 下模型字段不能用 tuple 接收 LLM 输出的 JSON 数组。**
  `SemanticDecision.missing_requirements` 已从 tuple 改 list（含 validator 字符串规范化）；
  以后新增 instructor 响应模型一律用 list。
- **语义验证 prompt 必须强制 reason 与 passed 自一致**（「发现候选正确后不要沿用先前的不通过
  结论」「reason 不得超过 400 字符」）；`VerificationCheck.summary` 上限 500，LLM 长 reason
  必须防御性截断（`decision.reason[-500:]`）；`max_tokens` 给足（语义验证 4000）。
- **上传必须写入 `settings.data_prep_upload_root`。** Pi 运行时按该路径解析上传；写临时目录会
  「上传不存在或无权访问」。
- **`source_refs` 的 sha256 必须是上传对象的真实 hash**，不能填空串（Pi 校验失败）。
- **Pi 任务的语义判断输入不含工具调用轨迹**，不要靠 objective 里的「必须调用工具」证明工具
  调用；工具调用的真实发生由冻结 selection + `_expected_target_tools`/
  `_successful_tools_for_run` 机制门（agentic_runtime_events 的 `tool.completed`）证明。
- **验证任务样例越简单越好。** 4 行数据曾让 Qwen 误判 row_count 与源行数不一致，3 行样例稳定通过。
- **`SemanticWorkspaceManager` 是内存队列**：8088 的 worker 只执行 8088 进程内 enqueue 的任务；
  外部驱动脚本必须自己构造 manager 并 start+enqueue；验证/平台 worker 是 DB 轮询
  （8088 与外部进程可竞争，Lease 串行化）。
- **平台验证 worker 的 `_run` 无 try/except**：执行器抛异常会让 worker task 崩溃且静默停止。
  真实验证卡住时先查运行记录 evidence 是否推进、Lease 是否持有，再决定重试。
- **Trivy DB 7 天时效是真实环境门**：过期后 `trivy image --download-db-only --cache-dir
  data/platform-tools/supply-chain/cache/trivy` 更新（mirror.gcr.io 约 4 分钟）。
- **`capability_pack_versions` 唯一键 (owner_key, pack_id, version)，同版本不可覆盖。**
  重建平台版本（如快照修复）需删平台 OCI tag + 删目录行 + 换幂等键（`--replace` 纪律），
  事件流保留旧记录作失败留痕；不要直接对旧行 UPDATE。
- **语义验证有真实 LLM 随机性**：同输入可能输出自相矛盾结论（reason 全对却 passed:false）；
  prompt 一致性 + 简单样例 + 重试是当前稳定口径；任何「卡在 candidate_ready」先看
  `verification_json` 的 semantic_goal 结论再决定重试/重跑，不要盲目重跑。
- **Windows 只读 OCI blob 写入前必须 chmod(S_IWUSR)**，写后恢复原 mode；还原 blob 用
  signed/<run_id> 副本的正确内容（哈希与 digest 匹配），不要用可能被污染的备份文件。
- **`accept_pack_risk` 的 finding_ref 校验必须区分取证表**：个人 scope 用
  `get_validation_run`（个人表）；平台 scope 必须用平台验证运行表（digest 匹配 + SUCCEEDED，
  与 restore 复查链同源）。`get_validation_run` 只查个人表，平台 digest 永不匹配——
  误用会导致任何平台包 risk_accept 必被拒 `finding_ref_unknown`（阶段 6 首跑暴露）。
  测试同理：平台场景的证据运行必须存平台验证运行表（`create_platform_validation_run`），
  不能像修复前那样塞个人表掩盖问题。
- **治理事件表 `capability_governance_events` 没有 reason/status 顶层列**，内容全在
  `payload_json`；直接 SELECT reason 会 OperationalError。查询事件理由/快照先解析 payload。
  （供应链证据表 `capability_supply_chain_evidence` 顶层有 status 列，两个表结构不同。）
- **惰性到期是投影层判定，不是事件**：risk_accepted 到期（now >= expires_at）时投影回
  QUARANTINED 但零新事件。验收演示「到期」改 payload 的 expires_at 即可触发，改前记录原值、
  演示后恢复；不要靠写新事件模拟到期（会破坏零新事件断言与审计语义）。

### 10.6 Runtime、网络与本机运维

- **Capability Host 内网请求不能被业务外发代理接管。** `NO_PROXY` 只加入当前任务的确定性 Host
  DNS。
- **不要把辅助 Docker 容器失败直接当成 8088 服务失败。** 先检查后端日志、端口和 `/api/health`。
- **FastAPI 启动阶段不要直接运行同步 embedding/rerank 网络调用。**
- **Windows PowerShell 可能拦截 `npm.ps1`。** 需要时使用 `npm.cmd`。
- **所有中文文本都显式使用 UTF-8。** 出现乱码先修编码；PowerShell 管道打印中文加 `PYTHONUTF8=1`
  或先 `chcp 65001`。
- **停止脚本只能清理经项目路径、标记或祖先进程验证的进程树。** 未知端口占用只能报警。
- **8088 后端受热更新监督进程托管。** `logs/dev_reload.log` 记录自动重启：修改 src/*.py 会
  自动重启网关，杀掉的进程会被自动恢复——排查「幽灵进程/端口占用」先看这个日志。
- **权限分类器（auto 模式）偶发不可用。** 报错 `classifier temporarily unavailable` 是分类器
  服务瞬时故障（fail-closed 默认拒绝），重试通常成功；若持续可用，用户可切换权限模式后继续。
- **全量 pytest 偶发卡在 IO 等待（曾 33 分钟无输出）。** 用 `-o faulthandler_timeout=120` 重跑。

## 11. 权威资料索引

### 当前状态与规则

- `AGENTS.md`、`docs/status/current.md`、`CONTEXT.md`、`docs/agents/`
- `docs/agents/issue-tracker.md`、`docs/agents/triage-labels.md`、`docs/agents/domain.md`

### AC-07

- 规格：`docs/plans/2026-08-06-agentic-capability-ac07-spec.md`
- ADR：`docs/adr/0029-capability-validation-lifecycle-and-platform-publication.md`
- #34/#35 报告：`docs/plans/2026-08-07-agentic-capability-ac07-{02,03}-execution-report.md`
- #9 报告：`docs/plans/2026-08-13-agentic-capability-ac07-04-execution-report.md`
- #10 需求/设计：`docs/plans/2026-08-14-agentic-capability-ac07-05-{requirements-review,design}.md`
- #11 需求/设计/拆票：`docs/plans/2026-08-14-agentic-capability-ac07-06-*.md`
- #12 需求/设计/拆票：`docs/plans/2026-08-14-agentic-capability-ac07-07-*.md`
- #13 需求/设计/拆票：`docs/plans/2026-08-14-agentic-capability-ac07-08-*.md`
- #14 需求/设计/验收：`docs/plans/2026-08-16-agentic-capability-ac07-09-*.md`
- **#15 设计/拆票/验收方案：`docs/plans/2026-08-16-agentic-capability-ac07-10-{design,task-breakdown,acceptance-plan}.md`**
- #15 驱动脚本：`scripts/ac07_10_{validation_drive,publish_drive,rebuild_platform,stage4_drive,stage4_mount_drive,stage5_drive,stage6_drive,verify_platform_signatures}.py`
- #15 注册脚本：`scripts/prepare_ac07_10_packs.py`

### Phase 4 与长期方向

- Phase 4B Harness：`docs/plans/2026-07-24-phase4b-semantic-task-harness-plan.md`
- Phase 4 当前问题审计：`docs/plans/2026-08-02-phase4-current-issues-audit.md`
- Agentic Runtime vNext：`docs/adr/0017-agentic-runtime-vnext.md`
- Delivery 状态机：`docs/adr/0019-vnext-delivery-and-default-cutover-state-machine.md`
- Provider/外发：`docs/adr/0020-provider-connection-broker-and-credential-isolation.md`

## 12. 新会话的第一轮输出应该是什么

读取上述资料与当前工作树后，先给用户一份只读阶段判断，不要立即实现。至少说明：

1. 当前阶段：#17（AC-06 兼容切换与 AC-07 综合验收门）授权门；#15/#16 已真实完成。
2. 已验证事实：#15/#16 全链真实走通并收口（#15：PR #30 `95872a01`、Issue CLOSED；
   #16：阶段 0-6 真实完成，AC1-AC7 对照 ✅）、生产库状态（四条平台能力 + 三条 verified
   个人能力 + everything-mcp 2026.8.19 牺牲版本 revoked）、平台签名密钥就绪、
   当前 Git 实况（#16 发布链收口中）、8088/LLM/Docker 可用。
3. 基于代码的推断：#17 的执行路径（AC-06 兼容切换：legacy Adapter 过渡到平台发布链；
   AC-07 综合验收门：两条纵切面证据汇总 + 验收）。
4. 尚未验证的建议：#17 的最小切片、是否需要数据库变化。
5. 必须由用户确认：#17 计划展示与逐阶段授权；Commit/Push/PR/Issue 写入。
6. 根据开发计划，#17 完成后 AC-07 可收口；但不得自动进入。

如果用户只说「继续」，优先完成当前已确认阶段（#17 计划展示），不要把
#17 拆成没有价值的微步骤，也不要越过用户控制点。
