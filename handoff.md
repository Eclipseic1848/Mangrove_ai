# Mangrove 零上下文交接

> 文档用途：写给完全没有历史对话的新会话
>
> 最后现场核验：2026-08-22（G3 生产库带备份迁移后）
>
> 当前分支：`codex/g1-independent-evaluation`；正式评测绑定的代码基线 = `83fe3f70`；
> 当前 G2 Office 代码提交：`31729495`；AC-05 生产迁移代码提交：`235459a3`；
> 当前 HEAD 仍以现场 `git rev-parse HEAD` 为准
>
> 公开远端：`origin` → `https://github.com/Eclipseic1848/Mangrove_ai.git`
>
> 当前阶段：**#40 G1 本地工程门已达到资格阈值**；v3 独立正式运行结果为功能 30/31
>（96.8%）、安全 5/5（100%）、`qualified=true`。尚未推送、建 PR、更新或关闭 Issue；
> Phase 4、生产发布、Provider 认证与用户验收均未因此完成（见 §4.1）。

## 0. 一句话结论

Mangrove 正在把「能运行的个人能力」推进为「证据完整、可审计、可失败关闭的正式平台」。
AC-07 主线 #9-#17 已全部真实完成并关闭（两条纵切面 + 兼容切换，PR #30/#33/#34）。
**当前任务：Phase 4 剩余门**——G1 v3 本地正式运行已取得合格资格；G2 Office 已完成真实
Word/Excel 各连续 3/3，AC-05 带备份生产迁移、恢复副本、真实 Docker 探针与用户验收已通过；
G3 GateSnapshot、累计硬门、P0 自动回退和分阶段 Rollout 已完成工程实现、双轴终审、本地
提交、带备份生产迁移及生产 Gate/Approval 初始化；旧 8088 进程触发 P0 回退后已完成重启和
六项门重跑，新快照合格并已按独立授权恢复 `admin_gray`，技术烟测通过；生产默认未切换、
用户已确认本轮恢复验收通过，但完整默认切换验收未完成。
G4 的地址解析、盲重试和用户确认修复已提交为 `9ba95985`，绑定提交的传输安全矩阵 6/6 通过。
隔离 Relay 的真实 DeepSeek 合成运行已正确生成候选并通过验证，数据库实际记录 8 条
`recorded` Usage；资格脚本因旧的单条 Usage 假设误报 `missing`。多轮 Usage 汇总修复及
169 项相关后端回归已通过，但尚未提交或资格重跑；百炼未开始。生产 Vault 两阶段轮换按用户
决定不执行，最终三证据汇总因此不具备完整资格；G5（8B/Linux 服务器）仍未就绪。
**G1 确定结论：DeepSeek V4 Flash 在 36 题 v3 独立集上功能 30/31、安全 5/5，资格 PASS。**
31 个功能候选均形成正式 Delivery 并通过 QA；独立 oracle 拒绝 G103-F27「业务值或行序错误」。
Qwen3.8-27B 在 12 个功能 Delivery 后由用户中止，其证据单独隔离，不参与 DeepSeek 计分。

不要把 G1 部分通过表述成 Phase 4 完成；平台发布受众仍固定 admin_gray，普通用户开放、
版本发布均未发生。

## 1. 新会话必须先做什么

本文件是第一个入口。打开本文件后按以下顺序继续读取，不要直接改代码：

1. `AGENTS.md`：仓库工程规则、稳定业务边界和 Git/发布权限。
2. `docs/status/current.md`：当前能力与路线状态的唯一滚动台账。
3. `CONTEXT.md`：领域词汇和长期语义。
4. `docs/agents/`：Issue、标签和领域文档约定。
5. `docs/plans/2026-08-20-phase4-remaining-gates-plan.md`：Phase 4 剩余门计划与状态记录。
6. `docs/plans/2026-08-20-g1-generalization-30-fixtures.md`：G1 夹具与逐项结果。
7. `docs/plans/2026-08-20-g1-generalization-execution-report.md`：G1 执行结论与限制。
8. G1 评测资产：`evals/generalization-g1/`（fixtures.json / assertions.py / run_g1.py / runs/）。
9. AC-07 规格（Issue #8，已关闭）、ADR-0029，三条纵切面执行报告：
   `docs/plans/2026-08-19-agentic-capability-ac07-10-execution-report.md`（#15）、
   `docs/plans/2026-08-19-agentic-capability-ac07-11-execution-report.md`（#16）、
   `docs/plans/2026-08-20-agentic-capability-ac07-12-execution-report.md`（#17）。

现场执行：

```powershell
git status --short --branch --untracked-files=all
git rev-parse HEAD
git rev-parse origin/main
gh issue list --repo Eclipseic1848/Mangrove_ai --state open
E:/python3.13/python.exe -X utf8 evals/generalization-g1/run_g1.py --dry-run
```

预期现场状态是：

- 本地分支为 `codex/g1-independent-evaluation`，正式评测代码基线为 `83fe3f70`；G1 代码与
  v3 盲集已本地提交，工作树仅保留 10 个 G1 post-commit 冻结元数据（§4.2）；
- GitHub：Issue #36（父：Phase 4 剩余门）、#37（G1）、#38（G2）、#39（G3）为 OPEN；
  旧工单 #9-#17 全部 CLOSED；
- 生产库 `data/webui.db`：治理事件 32 条（#15 阶段 6 收口状态，详见旧 handoff 快照），
  三条 verified 个人能力 + 四条平台能力 + everything-mcp 牺牲版本；
- 平台签名密钥在 `~/.mangrove-signing/`（项目外，加密 Sigstore），`.env` 已配置（gitignored）；
- G1 v3 正式结果在 `evals/generalization-g1/runs/independent-v3/`（gitignored，不提交）；
  功能 30/31、安全 5/5、资格 PASS。旧 `runs/g1-*.json` 只保留为诊断历史。
- G2 Office 代码提交为 `31729495`；真实 Word/Excel 各 3/3 PASS。G3 已按明确文件白名单
  完成本地提交；工作树仍只保留 10 个 G1 post-commit 冻结元数据文件，未混入 G3 提交。
- AC-05 生产迁移代码提交为 `235459a3`；恢复点为
  `data/backups/webui-before-ac05-20260822-072340.db`，生产任务记录为 0，8088 健康。
- G3 生产迁移恢复点为 `data/backups/webui-before-g3-20260822-133901.db`；迁移后保持
  `admin_gray`，未切换默认入口，8088 `/api/health` 返回 200。
- 首次生产 GateSnapshot 为 `abfc951ee4a99f282484f46c8ffe0c48f36e4c924fd2d765cacc3dd7de992b38`；
  独立 `explicit_opt_in` Approval 已记录但未执行模式切换。运行态核验随后追加修正快照
  `39c168fb4009478fcd731dbe1f5f10d05d8685b5721cbe7bc5302eddc1ab9fa8`，当前 P0 已阻断。
- 8088 已重启为加载 G3 的 PID `5136`；新活动快照
  `a936510e53eebc2abb04ce984e1fb72821730d0dc1ce9d37760d2c85beec3571` 累计有效合格，
  `admin_gray` 恢复 Approval 已独立记录并执行，技术烟测通过；未扩大普通用户权限。

如果现场不同，先解释漂移并更新 `docs/status/current.md`，不得套用本文快照继续执行。

## 2. 我们在做什么工程

Mangrove 是统一数据任务平台。用户用自然语言描述目标，平台负责来源获取、任务规划、受控
能力调用、数据处理、证据绑定、独立验证和正式交付。核心链路：

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

AC-07（已完成）解决「能力如何建立信任并被治理」：个人 draft → 精确 digest 的 ValidationRun
（五步）→ Trivy/Syft 供应链证据 → verified → 管理员审核 → 脱敏快照 + Cosign 签名 →
admin_gray 发布 → 运行时三轴治理门 → 弃用/回滚/隔离/撤销/限期风险接受。
三轴投影：成熟度 `draft|verified`、生命周期 `active|deprecated|revoked`、运行资格
`eligible|quarantined`。

**当前主线 Phase 4 剩余门**（用户 2026-08-20 选定，性价比排序第一）解决「生产资格」：

- **G1**：30 项泛化集（evaluation-spec §5）——正式交付正确率 ≥90%、安全/失败 100%；
- **G2**：PG-05 收口——Word/Excel 连续 3/3 + AC-05 依赖获取状态机生产迁移与验收；
- **G3**：本地工程门已通过；生产迁移、生产 Rollout 切换和 8088 验收仍是独立授权门；
- **G4 工程门已实现、生产资格未完成；G5 挂起**：G4 已获准使用 DeepSeek/Qwen 纯合成
  数据；DeepSeek 真实合成运行已形成单个候选并通过验证，运行时现场核验到 8 条
  `recorded` Usage，但旧资格脚本误报 `missing`。多轮 Usage 修复尚未提交或资格重跑，百炼
  未开始；用户明确不执行生产 Vault 轮换；8B/Linux 目标服务器未就绪（不得表述为已完成）。

## 3. 已经完成了什么

### 3.1 仓库与公开开发基线

- 权威公开仓库 `Eclipseic1848/Mangrove_ai`，默认开发分支为 `main`；当前本地 G1 工作在
  `codex/g1-independent-evaluation`，尚未推送。
- 旧仓库保留为 `legacy-origin`/`legacy-platform`（历史证据）。`v0.0.4` 是唯一稳定封板标签。
- 本机 Agent 配置、数据库、日志、任务制品、浏览器登录态、本地审计不进入 Git。
- 2026-08-19 仓库卫生：移除第三方完整副本（external/chrome-devtools-howso）、品牌 Logo、
  商务条款摘录（PR #32）；用户明确本机也不要这些文件。

### 3.2 产品与交付主链

- Conductor 公域采集、`/data-prep` 统一正式工作台、11 种交付预览、vNext Delivery Publisher、
  覆盖感知文档检索（DR-00~07 工程实现）、对话转向/上下文编译、多模型连接均已实现或验证。
- 交付语义：只有 `delivery_published` 且完整性/QA 通过的 `output_id` 是正式交付。

### 3.3 AC-07（全部完成并关闭，2026-08-19/20）

| 工单 | 结果 |
| --- | --- |
| 旧 #33/#34/#35 | 三轴投影、ValidationRun、Trivy/Syft——完成并关闭（旧仓库历史） |
| 新 #9-#14 | 签名 PoC、自动晋级、审核、平台快照发布、运行时门、生命周期治理——完成并关闭 |
| 新 #15 | Python 表格 Tool 真实治理纵切面——完成并关闭（PR #30 `95872a01`） |
| 新 #16 | Everything MCP 真实治理纵切面——完成并关闭（PR #33） |
| 新 #17 | AC-06 兼容切换与 AC-07 综合验收门——完成并关闭（PR #34） |
| 父 #8 | AC-07 规格工单——已关闭（2026-08-20） |

AC-07 完整证据链：三份执行报告（ac07-10/ac07-11/ac07-12）+ 文档同步 PR #35（`37cda72a`，
AGENTS/CONTEXT/README/current/handoff/ADR-README 六份活跃文档迭代为完成状态）。

### 3.4 Phase 4 剩余门（当前任务）已完成部分

- **计划与 Issue（2026-08-20）**：`docs/plans/2026-08-20-phase4-remaining-gates-plan.md`
  （G1-G5 定义、G4 生产门与 G5 挂起条件）；Issue #36 父 + #37/#38/#39 子（ready-for-agent）。
- **G1 评测资产与外部连接路由就绪**：`evals/generalization-g1/` 三个文件——
  - `fixtures.json`：31 项夹具冻结清单（真实语料引用 + sha256 全部从 meta 真实读取并校验），
    当前冻结 fixture、具体 GoalContract、CandidateVerifier、断言、Runtime、驱动与 Git commit；
  - `assertions.py`：31 条诊断断言 + 语料解析；其中六条已增加源推导正反例，其余只有
    结构/长度/行数强度的历史规则被正式模式明确拒绝，仍不是正式验收断言；
  - `run_g1.py`：批量评测驱动（冻结断言 → PiRuntime 真实运行 → Verifier + 断言 → 重试机制
    最多 3 次 → 报告落盘 runs/g1-*.json；支持本地路由与冻结的 `--connection-id/--model-id`，
    报告不写 Key）。
- **G1 旧诊断结论（2026-08-20）**：31 项中 **19 候选预检 PASS / 6 FAIL /
  6 NOT_RUN**；通过项为 C1-C5、D1-D6、X1-X4、M1-M3、F2；失败项为 P1、P8、P9、P10、
  F1、S1。旧驱动未执行正式 Delivery Publisher/完整性/QA，不能把 19/25 写成正式正确率。
- **#40 正式评测机械链与结果**：当前驱动只有在真实 `DeliveryPublisher.publish` 成功、隔离
  Repository 可回读 `DeliveryManifest` 与 `output_id`、独立 QA、文件大小和 SHA-256 均一致时
  才记 PASS；D2/D4/X2/X3/F1/F2 已用源推导业务值与正反例加强。诊断清单标记
  `diagnostic_only`，默认正式模式拒绝运行，只有 `--diagnostic` 可做开发回归。v3 正式结果为
  功能 30/31（96.8%）、安全 5/5（100%）、`qualified=true`。
- **外部模型 P1 对照**：平台配置的 Qwen Max 与 DeepSeek Pro 均各跑 3 次；每次都在
  900 秒预算耗尽，未形成候选。该结果只补充 G1 P1 诊断，不能冒充 G4 安全端到端。
- **G1 证据文档**：逐项结果见 `2026-08-20-g1-generalization-30-fixtures.md`；门禁判定、
  失败归因和证据限制见 `2026-08-20-g1-generalization-execution-report.md`。
- **G1 过程中修复的生产代码缺陷（未提交，见 §4.2）**：
  1. `candidate_verifier.py` xlsx locator 正则贪婪匹配（`sheet:大师场景规划 row:2` 把
     「 row:2」吞进 sheet 名 → 证据回放 KeyError）；
  2. `candidate_verifier.py` `_quote_is_grounded` 多行匹配跳过 Pi 工具表格说明行
     （「Table N: … (R rows × C cols)」非来源事实）；
  3. `document_tools.py` `_propose_completion` 空 results 语义修正：只有显式
     JSON 布尔 `result_empty_confirmed=true` 才允许空数组继续进入覆盖校验；字符串或数字失败关闭。
  聚焦回归 `tests/test_candidate_verifier.py + test_candidate_manifest_tool.py +
  test_document_tool_relay.py` 为 35 passed；审查前全仓后端 `1655 passed, 5 skipped`，前端构建
  通过，最终失败关闭修复由聚焦集覆盖。

## 4. 当前卡在哪里

### 4.1 G1 诊断结论与待决策项

**4.1.1 评测环境缺口（已修复）**：PiRuntime 不内置 document relay server，默认指向
`http://127.0.0.1:8088/internal/document-tools`（产品主链 8088 进程自洽）。外部驱动进程
必须自己挂等价 relay：`run_g1.py` 的 `ensure_document_relay()` 已实现（复用 8088 的
document_tools 路由 + 共享 DocumentToolBroker + 动态端口 + document_relay_base_url 注入）。

**4.1.2 宿主工具契约缺口（已修复）**：propose_completion 的 `results` 允许空数组，Pi 可
用未确认的空数组提交停止提议。现在仅当 `result_empty_confirmed=true` 时允许空数组继续进入
覆盖校验；否则拒绝并报「结果对象必须至少 1 项」。这保留了「全部候选经证据排除」的合法
空结果语义，同时阻止未确认空结果绕过覆盖门。
注意：曾尝试改 `assets/mangrove-document-tools.ts` 加 `minItems: 1`，**Pi 扩展解析器对该
文件敏感，直接 ParseError 加载失败**（已回滚，git diff 该文件应为空）——以后不要改这个
扩展文件，宿主侧校验是安全路径。

**4.1.3 PDF 覆盖完成链（核心失败，未解决）**：P8 没有形成有效停止提议/结果对象；P9、
P10、S1 没有完成覆盖契约；本地 P1 完成更多链路后仍因低质量内容单元和必需字段证据不足
而失败。平台 Qwen Max、DeepSeek Pro 的 P1 对照均 3 次执行超出 900 秒且没有候选。
因此当前证据只支持「这些真实路径未能通过」，不能把失败统一简化成某一个模型不会构造
五字段，也不能把外部对照冒充完整 G4。

**4.1.4 正式规格缺口**：F1 虽有一次候选和业务断言通过，但 CandidateManifest 证据为空、
制品无效，Verifier 正确失败。更关键的是：当前仅 5 项 `paraphrase`、7 项 `similar` + 1 项
`conflict`，均不足至少 11 项；没有独立跨 Owner/权限/隔离夹具；驱动不进入正式 Delivery；
D2/D4/X2/X3/F1/F2 等断言只有结构/非空/长度/行数强度；集合还在运行中参与了生产缺陷修复，
已不再是盲保留集。因此旧诊断自身不能用于 Issue #37 验收。
这是旧诊断时点的缺口快照；#40 v3 已用新独立集、强断言和正式运行关闭该资格缺口。

**4.1.5 冻结修复（只保护未来运行）**：旧报告只冻结 fixture/objective/assertions/HEAD，无法
识别未提交 Runtime/Verifier WIP。驱动现已冻结具体 GoalContract、CandidateVerifier、断言、
Runtime、驱动与 Git commit，并拒绝跨快照 `--verify-only`。旧报告与新快照不一致，保留为
诊断历史；重新冻结同一已看过的集合不会恢复盲测资格。

### 4.2 当前本地提交与工作树

```text
 M evals/generalization-g1-independent/freeze.json
 M evals/generalization-g1-independent/heldout_manifest.json
 M evals/generalization-g1-independent/self-check-report.json
 M evals/generalization-g1-independent-v2/freeze.json
 M evals/generalization-g1-independent-v2/heldout_manifest.json
 M evals/generalization-g1-independent-v2/self-check-report.json
 M evals/generalization-g1-independent-v3/freeze.json
 M evals/generalization-g1-independent-v3/heldout_manifest.json
 M evals/generalization-g1-independent-v3/self-check-report.json
 M evals/generalization-g1/fixtures.json
```

G1 生产代码与 v3 盲集已本地提交：`55ca58aa`、`e576a67d`、`8538481a`、`83fe3f70`。
上述未提交 JSON 是 G1 运行前后冻结/自检元数据；提交会再次改变 Git 身份，因此正式 runner
明确只允许这些路径在 post-commit 重绑定后保持 dirty。G3 已使用明确路径白名单完成本地
提交，10 个 G1 JSON 未被暂存；本轮三份状态文档也已按独立白名单完成本地提交。`runs/` 为
gitignored 正式证据目录。尚未推送、建 PR 或更新 Issue；扩大 Rollout 与远端动作仍需用户
分别授权。

### 4.3 其他开放事实

- 8088 开发后端进程运行中（logs/dev_reload.log 热更新监督）；
- Trivy DB 时效：过期后用 `trivy image --download-db-only --cache-dir
  data/platform-tools/supply-chain/cache/trivy` 更新（mirror.gcr.io 约 4 分钟）；
- 语义验证依赖真实 LLM，结果有随机性；G1 用最多 3 次完整重跑和冻结断言控制口径；
- G1 v3 正式结果为功能 30/31（96.8%）、安全 5/5（100%）；该结论只代表冻结的本地 G1
  工程资格，不代表 Phase 4、远端工单、生产发布或 Provider 认证完成。

## 5. 下一步计划（G4 生产安全门）

### G1 修复（#40 本地工程门已达标）

1. 已完成旧逐项诊断、失败归因、冻结机制修复、v3 独立集和正式 G1；
2. #40 已实现真实 Delivery 计分接缝、强断言/反例、清单资格校验和安全失败阶段执行语义；
   36 题独立盲集已通过来源重算、错误值反例和冻结闭环自检，Standards/Spec 最终双轴复审均 PASS。
3. 如需进入公开仓库：**先展示提交、正式结果和本地未提交冻结元数据，再由用户逐项授权**
   推送、PR、Issue 更新；合并另行确认；
4. 不修改 G1 判定标准，不把未运行 P2-P7 标记为失败，也不把外部 P1 对照写成 G4 完成。

### G2 PG-05 收口（已通过）

- `31729495` 已提交 Office 验证 CLI 的 Owner、空值、并发、超时和异常失败关闭加固；18 项
  定向测试通过，双轴审查无实现问题；
- 真实 Word/Excel 已各连续 3/3 通过候选格式、内容断言和独立 Verifier；PDF 基线脚本已存在，
  本轮没有扩展到 PDF 重跑；
- AC-05 已完成显式带备份生产迁移、同路径幂等重放、63 张既有表零改写、恢复副本状态机、
  真实隔离获取 → 冻结依赖 → 离线构建 → 清理/恢复；容器、网络与临时目录残留均为 0；
- 用户于 2026-08-22 明确确认验收通过；G2 已完成。Issue #38 仍为 OPEN，推送、更新或关闭
  远端工单需单独授权；该结论不代表 Phase 4 完成。

### G3 GateSnapshot + 默认入口切换

- 已实现不可变 GateSnapshot、累计硬门有效资格、独立 Approval、对比审计和
  `admin_gray → explicit_opt_in → vnext_default` 状态机；P0 自动进入 `legacy_rollback`；
- 新 Task/Revision 的 semantic 记录、活动指针、Runtime assignment 与 RuntimeTaskConfig
  同一 SQLite 事务提交；422、重复、并发、锁超时和故障注入均失败关闭；
- 测试库显式迁移/回放、真实 Publisher P0 零改写保护、Steering/Safe Point 回归已通过；
  Standards/Spec 终审均无仍可复现问题；排除 G1 冻结身份漂移与测试域名 DNS 两项既有
  基线后，全仓后端 `1762 passed, 5 skipped, 2 deselected`；
- 生产 `data/webui.db` 已完成带备份显式迁移；恢复点为
  `data/backups/webui-before-g3-20260822-133901.db`，SHA-256 为
  `b552af5ac08bec3ba4421462cd51f73cf4b783520646ad285489ed9dd413bd7f`；生产库和恢复点
  `integrity_check=ok`，旧业务逻辑指纹一致，16 个 G3 对象、Schema digest、备份绑定及幂等
  回放均通过；初始状态保持 `admin_gray`、`p0_blocked=0`；
- 首次生产 GateSnapshot `abfc951ee4a99f282484f46c8ffe0c48f36e4c924fd2d765cacc3dd7de992b38`
  及独立 Approval `approval-explicit-opt-in-abfc951ee4a99f28` 已追加保存；随后发现 8088 进程
  早于 G3 提交启动，系统追加修正快照
  `39c168fb4009478fcd731dbe1f5f10d05d8685b5721cbe7bc5302eddc1ab9fa8`，当前为
  `legacy_rollback`、`p0_blocked=true`。历史 Approval 不绑定当前快照，不可复用；
- 8088 已重启并加载 G3，新活动快照
  `a936510e53eebc2abb04ce984e1fb72821730d0dc1ce9d37760d2c85beec3571` 的六项硬门累计
  有效合格；恢复 Approval `approval-admin-gray-recovery-a936510e53eebc2a` 已绑定当前快照；
- 用户单独授权后已原子恢复到 `admin_gray`、`p0_blocked=false`；旧业务逻辑指纹、既有
  Delivery 和 Runtime assignment 均未改变。管理员 Pi、普通用户 Legacy、默认 Legacy、跨
  Owner 拒绝及 8088 三入口技术烟测通过；用户于 2026-08-22 明确确认本轮恢复验收通过；
- **尚未执行**：`explicit_opt_in`、vNext 默认切换、完整默认切换用户验收、推送、Issue #39
  更新。Runtime assignment 为 0；后续动作必须再次单独授权。
- `explicit_opt_in` 切换前曾发现缺少“获准用户”资格门并创建 #43。当前产品目标为
  “可用后所有用户默认使用”，因此不再实现 OptInGrant；#43 原方案已被后续决策取代，远端
  状态尚未调整。生产继续保持 `admin_gray`；G4 合格和默认切换单独授权前不得扩大。

### 生产资格审计（G1-G3 后收口）

- 全仓回归、真实数据/任务、权限与安全、备份恢复、可观测性、资源清理、文档一致性、用户验收。

### G4/G5

- G4：执行范围限定为平台共享 DeepSeek/百炼连接，外发仅限冻结的纯合成数据；
  本轮资格范围不包含其他 Provider，新增 Provider 必须独立通过同一矩阵。工程实现和 74 项
  定向回归已通过。当前顺序为：提交多轮 Usage 汇总修复并绑定新 SHA；重跑传输矩阵；以独立
  冻结执行身份重跑 DeepSeek 资格链，明确通过后再运行百炼。用户已明确决定不更换生产 Vault
  Key，因此不执行两阶段
  轮换，也不能生成 Pi、传输安全、Vault 三证据齐全的最终 G4 合格结论；默认切换继续阻断；
- G4 首次 Pi 尝试的 Egress 只连接不可达的旧内部 8088 地址，Provider Usage=0 且 Grant 已撤销；
  当前修复改为 Docker 网络内解析、关闭 Pi SDK 自动重试，结果不确定时由用户确认并创建新
  Revision，确认只对用户刚看到的失败版本有效。旧 `in_progress` 台账不会再靠事后日志自动
  判定为“肯定未外发”，且因缺少原始 Owner/任务/Revision 绑定而不允许恢复；修复后的新台账
  会先冻结完整身份，只有用户明确确认重复调用和费用风险后才保留旧记录并最多放行一次新
  执行。多轮 Usage 修复提交和资格重跑仍待后续步骤；
- G5：用户目标服务器就绪并授权部署验证。

## 6. 工单 Roadmap

权威父工单（AC-07）是旧 #8（已关闭）；Phase 4 剩余门父工单是新 #36。

| 工单 | 目标 | 状态 |
| ---: | --- | --- |
| 新 #36 | Phase 4 剩余门（父工单） | OPEN（2026-08-20 创建） |
| 新 #37 | G1：30 项泛化集 | OPEN（本地正式资格 PASS；尚未更新远端工单） |
| 新 #40 | G1 修复：盲保留集与正式 Delivery 验收链 | OPEN（本地提交与正式资格 PASS；尚未推送/更新工单） |
| 新 #38 | G2：PG-05 收口 | OPEN（本地实现、生产迁移和用户验收 PASS；待远端更新授权） |
| 新 #39 | G3：GateSnapshot + 默认入口切换 | OPEN（admin_gray 验收 PASS；目标改为 G4 后全用户默认，未扩大/推送） |
| 新 #43 | G3 修复：explicit_opt_in 获准用户权限契约 | OPEN（原方案已被后续产品决策取代；远端状态待授权调整） |

AC-07 历史（全部 CLOSED）：新 #9-#17 + 父 #8；旧仓库 #33-#35（历史）。

## 7. 整个工程 Roadmap

1. **AC-07 已完成**：#9-#17 全部真实完成并关闭（PR #30/#33/#34 + #35 文档同步）。
2. **Phase 4 剩余门（当前）**：#40 G1 本地工程门资格 PASS；G2 Office 3/3、AC-05 生产
   迁移和用户验收 PASS；G3 本地工程门 PASS，但生产迁移、默认切换、8088 验收与提交发布
   均未执行；G4 工程门已实现但真实生产门未执行，G5 挂起。
3. **生产资格审计**：G1-G3 后收口（回归/权限/备份/可观测性/文档一致性/用户验收）。
4. **明确未完成/后置**：普通用户平台能力开放、远程 MCP/Secret、Registry 自动发现、
   AC-08/AC-09、Phase 4C（图片/音频/视频）、Phase 5A/B、多租户、大规模分布式执行。
   —— 任何一项都不能因局部测试通过被表述为 Phase 4 完成。

## 8. 版本计划

- 当前仓库唯一稳定封板标签 `v0.0.4`（不得移动或回写）；公开 `main` 承接原 `v0.0.8`
  开发能力但无该标签、未封板。
- 无已确认的下一个版本号、发布日期、RC；没有授权创建任何 tag/Release；AC-07 完成与
  Phase 4 剩余门推进不自动等于版本可发布。
- 版本决策门需重新确认：工单范围、生产门是否全过、权限/供应链/签名/外发/残留审计、
  数据库迁移备份恢复演练、8088 用户验收、以及用户分别授权版本号/tag/Release/Push。

## 9. 稳定业务与安全边界

- `8088` 是统一产品入口；`5173` 只用于前端开发。
- `/data-prep` 是主工作台；迁移完成前不得删除历史任务兼容入口或 Legacy Delivery 读取。
- TaskRevision、来源快照、连接版本、外发确认、能力 digest 和 Owner 隔离必须冻结且失败关闭。
- 普通用户、管理员、超级管理员是产品角色；「高级用户」不是权限角色。
- 管理员可查看跨 Owner 任务管理元数据；读取个人业务正文必须填写原因并产生不可变审计。
- 无能力任务不能创建治理运行、扫描器或 Sidecar。
- 外部模型、采集器、下载源、Registry、镜像和代理变化都可能改变数据外发与安全语义，必须确认。
- 用户控制业务范围、数据含义、权限、生产迁移、能力晋级、平台发布、受众开放和不可逆操作。
- #15 边界：验证任务豁免五条件（个人+Owner+active+eligible+冻结标记）同时成立才生效；
  平台包永不豁免；draft 能力不进入新任务选择列表。
- #15 阶段 5 边界：平台装载门签名验证绑定主布局主体内容；平台 restore 复查链从平台
  验证运行表取证。
- G1 新增：评测驱动只读 `data/uploads/` 对象；`runs/` 与 `.pytest-tmp/` 不进入 Git；
  运行前必须冻结 fixture、具体 GoalContract、CandidateVerifier、断言、Runtime、驱动和 Git
  commit；跨快照重放失败关闭。冻结一致不等于集合仍有盲保留资格。

## 10. 绝对不要再踩的坑

### 10.1 状态与范围

- **不要把测试、Code Review 或一次真实任务当成用户验收。** 用户验收必须由用户明确确认。
- **不要把 Candidate、验证通过、`eligible_for_delivery` 或中间文件称为正式交付。**
- **不要把局部审计、PoC 或 AC 工单完成称为 Phase 4 完成。** 报告必须区分已验证事实、
  代码推断和尚未验证的建议。
- **不要自动进入下一 Skill 或阶段。** 每个阶段结束展示产物与未决问题，等待用户确认。
- **不要顺手重构、扩大权限或合并工单。** 每一行改动都应能追溯到当前工单。

### 10.2 仓库、Issue 与文档

- **不要用错仓库或 Issue 编号。** 新工单只认 `Eclipseic1848/Mangrove_ai`；AC-07 旧 #33~#35
  只认 `Eclipseic1848/Mangrove_platform`。
- **不要只看 `git diff <base>...HEAD` 审查 WIP。** 必须同时看 `git status --short
  --untracked-files=all` 并逐一审查新增文件。
- **不要相信交接中的旧 SHA、分支、测试数、端点或 Issue 状态。** 开工先现场核验。
- **不要让多个 Markdown 同时维护滚动状态。** `docs/status/current.md` 是唯一状态台账；
  `handoff.md` 只做接手快照与下一门禁。
- **不要删除历史计划来「清理过期内容」。** 标记 historical/superseded 并指向当前权威文档。
- **不要删除或恢复 logos/、商务条款、第三方副本**（用户明确本机也不要）。

### 10.3 Git 与发布

- **禁止 `git add .`、`git add -A`、强推、`git reset --hard` 和 `git clean`。**
- **不要直接在默认分支偷偷提交。** 需要发布时按授权创建 `codex/` 分支、提交、推送、PR、合并。
- **「同意全部」不覆盖 PR 合并步。** 合并仍需用户单独指名「合并」再执行。
- **不要提交本机路径、`.env`、Secret、数据库、日志、任务数据、签名私钥、浏览器状态、
  Agent 设置或本地审计。**
- **不要因为下载慢就更换工具、版本、镜像、镜像源、URL、安装方式或实现路线。** 只能做
  语义不变的重试；替代方案先解释差异与风险并取得批准。

### 10.4 AC-06 / AC-07 与签名

- **Everything MCP 灰度样本是 MCP 协议测试服务器，不是 Voidtools Everything 文件搜索。**
- **不要用 `cosign sign-blob` 冒充标准 OCI image signature。** 已证明路径是回环 Registry +
  digest 签名 + OCI Referrers + 独立 Layout。
- **签名工具锁不能只检查 `verified=true`。** 必须绑定版本、来源、身份/commit、可执行文件
  digest、镜像 tag 与 digest。
- **签名密钥不能只检查「文件存在」。** 私钥是加密 Sigstore 格式、项目外；口令只进受控子进程
  环境，不进 argv/日志/事件/证据。
- **递归清理前必须验证 transaction ID 和绝对路径；Windows 只读 OCI blob 先 chmod(S_IWUSR)。**
- **治理命令幂等检查先于预期状态检查；多事件命令按投影补写缺失事件；restore 幂等键
  不能复用掩盖新状态（用唯一幂等键含时间戳/序号）。**
- **治理事件快照与写入时刻投影一致；两轴写序视状态选择（revoked+quarantined 先生命周期
  再解除隔离）。**

### 10.5 AC-07 纵切面（#15/#16 专属）

- **能力归档固定名 `mangrove-capability.tar`（或 .tar.gz/.tgz）**；manifest 标准名
  `mangrove-capability.json`（不是 manifest.json）；平台快照 purpose 必须保留中性脱敏文案。
- **平台装载门签名验证绑定主布局主体内容**；restore 复查链从平台验证运行表取证。
- **`materialize_platform` 路径拼接用括号整体拼**；发布 Adapter 传仓库实例（对象）。
- **instructor v2 strict 下响应模型字段用 list 不用 tuple**。
- **语义验证 prompt 强制 reason 与 passed 自一致；summary 上限 500 截断；max_tokens 4000。**
- **上传写入 `settings.data_prep_upload_root`；source_refs sha256 是真实 hash。**
- **工具调用由机制门证明**（冻结 selection + `tool.completed` 事件），不靠 objective 措辞。
- **验证任务样例越简单越好**（3 行稳定，4 行曾误判）。
- **SemanticWorkspaceManager 是内存队列**：外部驱动脚本必须自己构造 manager；验证/平台
  worker 是 DB 轮询（8088 与外部进程可竞争，Lease 串行化）。
- **Trivy DB 7 天时效是真实环境门**；`capability_pack_versions` 唯一键
  (owner_key, pack_id, version)，重建平台版本按 `--replace` 纪律。
- **`accept_pack_risk` 平台 scope 必须从平台验证运行表取证**（误查个人表必拒
  finding_ref_unknown）。
- **治理事件表没有 reason/status 顶层列**（在 payload_json）；供应链证据表顶层有 status。
- **惰性到期是投影层判定不是事件**（改 expires_at 演示，不要写新事件）。
- **Windows docker.exe 吞 `\"`**：JS 脚本 base64 + process.argv 传参。
- **Pi 任务 sources 至少 1 项**；`os.makedirs` 与 materialize 内部 mkdir 冲突（不预创建）。

### 10.6 G1 泛化集专属（本会话新踩的坑）

- **fixtures.json 的 sha256 必须从 meta 真实读取**（编造哈希 = 数据完整性事故；已用脚本
  按 original_name 重映射 + 全校验修复）。
- **冻结自指**：fixture_sha256 剔除 frozen_inputs 字段计算（否则冻结写回改变文件哈希）。
- **上传对象文件名是 upload_id（32 hex），不是内容 sha256**；匹配须读 meta 后按内容哈希
  匹配并复验对象哈希。
- **openpyxl 按扩展名判定格式**：无扩展名路径打不开（临时副本 + `.xlsx` 后缀）；
  read_only workbook 没有 `.items()`（用 `sheetnames`）。
- **NFKC 规范化把全角括号变半角**：表头列匹配用关键词模糊匹配，不用精确名
  （「应用场景（三级）」陷阱）。
- **断言必须跳过候选表头行**（C5/M3/D1/D5/X4 反复踩：`_nonempty_rows(rows[1:])`）。
- **Verifier 语义判定波动**（reason 自相矛盾 / 相同输出判定翻转 / 结构错误被接受）：
  run_g1 重试机制（最多 3 次完整重跑）是稳定口径；断言失败与 Verifier 失败分开归因。
- **objective 歧义导致 Pi 输出偏差**（M3「重复字段算一列」、D5「核心指标表」范围、X1
  「应用场景」二级/三级）：明确化 objective 是合法任务表述修正，但**改后必须重新冻结**。
- **生产验证器缺陷（G1 发现并修复）**：xlsx locator 贪婪匹配吞「 row:2」（已修：非贪婪 +
  row 后缀剥离）；Pi 工具表格说明行「Table N: … (R rows × C cols)」被复制进 quote 导致
  grounding 失败（已修：多行匹配跳过工具说明行，正文仍逐字命中，不放松验证）。
- **PiRuntime 不内置 document relay**：外部驱动进程必须挂等价 relay（复用 8088 的
  document_tools 路由 + 共享 broker + 动态端口 + `document_relay_base_url` 注入）；
  relay 与 PiRuntime 必须共享同一个 DocumentToolBroker 实例（grant 在 broker 内）。
- **不要改 `assets/mangrove-document-tools.ts`**：Pi 扩展解析器对该文件敏感，任何
  结构改动都可能 ParseError 导致扩展加载失败；用宿主侧（document_tools.py）强校验。
- **propose_completion 空 results 数组**：未显式确认结果为空时宿主拒绝；只有
  `result_empty_confirmed=true` 才继续进入覆盖校验。不要用一刀切拒绝破坏「全部候选经证据
  排除」的合法空结果。
- **不要把 PDF 失败单因化**：P8 未形成有效停止提议；P9/P10/S1 未完成覆盖契约；本地 P1
  走得更深但仍有低质量单元/必需字段证据缺口；外部 Qwen Max/DeepSeek Pro 的 P1 均 3 次
  超过 900 秒且没有候选。各自保留原始归因，不能都写成「不会构造五字段」。
- **G1 外部 P1 对照不等于 G4**：它没有执行 G4 的 Key/Prompt/日志/审计/失败注入安全矩阵，
  也没有扩大后续外发授权。
- **候选预检不等于正式 G1 正确率**：必须进入 Delivery Publisher，并验证
  `delivery_published`、完整性和 QA；结构/长度/行数断言也不能冒充业务正确性。
- **保留集一旦用于修复就被污染**：同一集合重新冻结不能恢复盲测资格；正式重测要换成实现
  人员未查看期望步骤的新集合。当前 5 项 paraphrase、8 项 similar/conflict 均不足 11 项。
- **Pi 偶发不写 candidate-manifest.json**（D3/F1）：manifest 校验失败归为 error 类失败。
- **评测运行不能并发**（Pi 容器/relay 端口冲突）：分批顺序跑，前台单项目诊断、后台跑批。

### 10.7 Runtime、网络与本机运维

- **Capability Host 内网请求不能被业务外发代理接管**（NO_PROXY 只加当前任务 Host DNS）。
- **不要把辅助 Docker 容器失败直接当成 8088 服务失败**；先查日志/端口/`/api/health`。
- **FastAPI 启动阶段不要直接运行同步 embedding/rerank 网络调用。**
- **Windows PowerShell 可能拦截 `npm.ps1`**（用 `npm.cmd`）；中文文本显式 UTF-8。
- **停止脚本只能清理经路径/标记/祖先进程验证的进程树**；8088 受热更新监督（先看
  logs/dev_reload.log）。
- **权限分类器（auto 模式）偶发不可用**（fail-closed 默认拒绝，重试通常成功）。
- **全量 pytest 偶发卡 IO 等待**：用 `-o faulthandler_timeout=120`；`.pytest-tmp` 父目录
  不存在时先 `mkdir`（pytest basetemp 不建父目录）。

## 11. 权威资料索引

### 当前状态与规则

- `AGENTS.md`、`docs/status/current.md`、`CONTEXT.md`、`docs/agents/`
- `docs/agents/issue-tracker.md`、`docs/agents/triage-labels.md`、`docs/agents/domain.md`

### Phase 4 剩余门（当前主线）

- 计划与状态：`docs/plans/2026-08-20-phase4-remaining-gates-plan.md`
- 父工单/子工单：Issue #36/#37/#38/#39
- G1 评测资产：`evals/generalization-g1/fixtures.json`、`assertions.py`、`run_g1.py`
- G1 运行结果：`evals/generalization-g1/runs/g1-*.json`（gitignored）
- G1 逐项结果：`docs/plans/2026-08-20-g1-generalization-30-fixtures.md`
- G1 执行报告：`docs/plans/2026-08-20-g1-generalization-execution-report.md`
- G1 规格：`docs/plans/2026-07-29-agentic-runtime-vnext-evaluation-spec.md`（§5 泛化集）

### AC-07（已完成，历史证据）

- 规格：`docs/plans/2026-08-06-agentic-capability-ac07-spec.md`；ADR-0029
- 执行报告：`docs/plans/2026-08-19-agentic-capability-ac07-10-execution-report.md`（#15）、
  `2026-08-19-agentic-capability-ac07-11-execution-report.md`（#16）、
  `2026-08-20-agentic-capability-ac07-12-execution-report.md`（#17）
- #15 驱动脚本：`scripts/ac07_10_*.py`、`scripts/prepare_ac07_10_packs.py`

### Phase 4 与长期方向

- Phase 4B Harness：`docs/plans/2026-07-24-phase4b-semantic-task-harness-plan.md`
- Phase 4 问题审计：`docs/plans/2026-08-02-phase4-current-issues-audit.md`
- Agentic Runtime：ADR-0017/0018/0019/0020/0025/0026/0027/0028/0029
- PG-05 验证脚本：`scripts/verify_pi_runtime_pg05_{office,pdf}.py`
- vNext Publisher：`docs/plans/2026-08-04-vnext-delivery-publisher-execution-report.md`

## 12. 新会话的第一轮输出应该是什么

读取上述资料与当前工作树后，先给用户一份只读阶段判断，不要立即实现。至少说明：

1. 当前阶段：#40 G1 本地工程门资格 PASS；DeepSeek V4 Flash 正式结果功能 30/31、安全 5/5；
   G2 Office 3/3、AC-05 生产迁移与用户验收 PASS；G3 未开始；G4 安全矩阵与 G5 挂起。
2. 已验证事实：31 个功能候选均形成正式 Delivery 并通过 QA；独立 oracle 通过 30 个，安全
   5/5；唯一失败 G103-F27 为业务值或行序错误。
3. 尚未完成：远端 Issue/PR、G3、G4、G5、生产发布。
4. 必须由用户确认：推送、PR、Issue 更新/关闭、合并和默认入口切换均逐项授权。
5. 如果用户只说「继续」：可以收口本地状态文档，但不要自动推送、建 PR、更新 Issue、
   进入 G3 或执行生产切换。
