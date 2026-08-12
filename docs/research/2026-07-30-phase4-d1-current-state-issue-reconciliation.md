# Phase 4 D1：当前状态与 Issue 对账研究

> 日期：2026-07-30
>
> 阶段：Wayfinder D1 / Research
>
> 对应任务：[GitHub #13](https://github.com/Eclipseic1848/Mangrove_platform/issues/13)
>
> 审计基线：本地分支 `v0.0.7`，提交 `45ee703c`
>
> 约束：本报告只做现状核验与处置建议，不授权修改代码、Issue、版本、标签或默认入口。

## 1. 研究问题与证据边界

本轮回答三个问题：

1. Phase 4A、Phase 4B Legacy、Agentic Runtime vNext、Phase 4C 分别做到什么程度；
2. GitHub #2–#11 的正文与当前事实是否一致；
3. 哪些内容可以直接归档，哪些必须保留、改写或等待后续决策。

证据优先级如下：

1. 当前代码和 Git 历史；
2. `AGENTS.md`、`handoff.md`、已采纳 ADR、阶段执行报告；
3. 当前总计划 `plan.md`；
4. GitHub Issue 正文；
5. 尚未实现的新 Wayfinder 决策。

本轮没有重新运行产品测试。文中的测试数字都是已有执行报告记录的历史证据，不是
2026-07-30 的重新执行结果。当前工作区原本已有与本报告无关的未提交文件，本轮未触碰。

## 2. 结论摘要

### 2.1 已验证事实

| 范围 | 当前事实状态 | 已有证据 | 不能据此宣称 |
|---|---|---|---|
| Phase 4A | 已按 `v0.0.5` 的约定范围收口 | 24 份/120 页黄金集；17 份 PDF/85 页/51 字段门禁；字段值和证据绑定双 100%；后端 834 passed、4 skipped、0 failed；性能门 3 passed；Playwright 21 passed | 不能宣称所有语言、全页 CER/WER、大语料和高并发 OCR 已完成；也不能把 `v0.0.5` 说成当前稳定封板标签 |
| Phase 4B Legacy | 批次 -1 至 8A 的本机工程主流程已实现，8A 已获用户验收；Legacy 仍是默认入口 | 批次 6 已有 11 种正式 Delivery、独立重开 QA、SHA 和用户隔离；批次 8A 记录 963 passed、4 skipped、0 failed | 不能宣称 Phase 4B 已封板；不能用 Legacy 的正式 Delivery 证明 vNext 已有正式 Delivery；不能掩盖 PDF 表格转 CSV 暴露的结构性缺陷 |
| Agentic Runtime vNext | 完成三路线 PoC 后，进入完整 Pi RPC + 任务级 Docker 的管理员灰度；已有候选、独立验证、恢复、取消和受控 Egress 的阶段性闭环 | Pi/OpenCode/Deep Agents 为 16/18、16/18、12/18；PDF 3/3、Word 回放、Excel 1/1；会话恢复、HTTP 幂等、跨用户隔离、真实取消、提示注入和 Egress 主链已有证据 | 不能宣称取得生产资格、完成 PG-05、创建正式 Delivery、通过 30 项泛化集或可以切换默认入口 |
| Phase 4C | 统一多媒体能力尚未实施 | `plan.md` 的 Phase 4C 条目仍全部未勾选 | 不能因为旧 Conductor 已有视频字幕/ASR/画面文字链，就宣称图片、音频、视频已接入统一 Harness |
| 8B / 服务器封板 | 已明确后置，当前没有继续实施授权 | `AGENTS.md`、`handoff.md` 和批次 8A 报告均明确未封板、服务器类验证后置 | 不能把历史 8B-1 规格当成当前实施授权，也不能自行创建版本或标签 |

主要证据：

- Phase 4A 的收口状态和验证数字：
  [`phase4a-document-evidence-plan.md`](../plans/2026-07-22-phase4a-document-evidence-plan.md)
  第 3、70、108–112、161 行；`plan.md` 第 842–881 行。
- 当前稳定标签与开发版本边界：`AGENTS.md` 第 15–25 行；
  `handoff.md` 第 22–33、98–100、107–137 行。
- Legacy 批次 6：
  [`phase4b-batch6-execution-report.md`](../plans/2026-07-27-phase4b-batch6-execution-report.md)
  第 16–63、156–178、226–242 行。
- 批次 8A：
  [`phase4b-batch8a-execution-report.md`](../plans/2026-07-28-phase4b-batch8a-execution-report.md)
  第 13、72–86、130、155–172 行。
- vNext 当前任务表：
  [`agentic-runtime-vnext-task-breakdown.md`](../plans/2026-07-29-agentic-runtime-vnext-task-breakdown.md)
  第 37–50 行。
- Pi 灰度完整边界：
  [`agentic-runtime-vnext-pi-full-capability-gray-plan.md`](../plans/2026-07-29-agentic-runtime-vnext-pi-full-capability-gray-plan.md)
  第 5–7、143–173、210–233 行。
- Phase 4C：`plan.md` 第 915–929 行。

### 2.2 基于代码的推断

以下结论来自当前代码静态检查，尚未通过本轮运行时测试：

1. vNext 已有 `RuntimeTaskConfig`、请求、Checkpoint、Candidate 和 Verification 等最小契约，
   Run/Event 按 `user_id + task_id + revision` 隔离；但是完整 `GoalContract` 只在
   `evals/agentic-runtime-vnext/state_model.py` PoC 中出现，生产目录
   `src/agentic_runtime/` 中没有同名稳定领域实体。参见
   `src/agentic_runtime/models.py:33–175`、`src/agentic_runtime/repository.py:5–93`。
2. `EgressPolicy` 已表达 `dependency_acquisition` 与 `business_execution` 两个阶段，
   但这是策略工厂，不等于“依赖获取状态机”已经接入生产编排。参见
   `src/agentic_runtime/egress_policy.py:47–139`，并与任务表第 44 行和 Pi 灰度计划
   第 151–152 行交叉核验。
3. 旧 Conductor 视频链确实会收集字幕、远程/本地 ASR 和画面文字，并在证据不足时失败关闭；
   但它仍是显式视频链接的专用节点，不是 Phase 4C 的统一来源、模态、任务、证据与交付模型。
   参见 `src/conductor/nodes/video_enrich.py:18–82` 和
   `src/services/video_evidence.py:126–161`。
4. 当前设置页已有按用户隔离的 DeepSeek/Qwen Key 自助配置与连通验证；但用户级白名单
   不允许自定义相应 endpoint/model，且存储路径直接把值写入 SQLite `TEXT` 字段。本轮静态
   检查没有发现该写入路径的应用层加密。因此，新确认的“任意兼容 OpenAPI 连接、Key 加密
   静态存储、管理员不可取回、失败不得转用平台 Key”不能算作现有能力。
   参见 `src/config/runtime_config.py:45–78`、
   `src/api/routes/config_routes.py:110–140,409–421`、
   `src/api/store.py:67–73,751–765`。

### 2.3 尚未验证的建议

1. 把整个 Phase 4 的权威计划入口固定为
   [#12 Wayfinder 总地图](https://github.com/Eclipseic1848/Mangrove_platform/issues/12)；
   #2 只保留为 Phase 4B vNext 实施总票，不再承担 Phase 4C 和 8B 总地图职责。
2. Phase 4 的功能完成与工程封板分开：
   - 4B vNext 与 4C 通过验收后可判定“功能范围完成”；
   - 8B-1/8B-2 和目标服务器验证通过后，才讨论“正式封板”。
3. 不立即批量重写 #4–#10。先让 D2–D10 固化领域、状态机、外部连接、多媒体、复合来源、
   UX、生命周期和生产门，再做一次稳定的 Issue 迁移，避免反复改票。

## 3. 四条能力线的详细对账

### 3.1 Phase 4A：范围已收口，但不是无限 OCR 能力

#### 已验证事实

- 权威计划明确写明“已封板并在 v0.0.5 收口，批次 0–5 已完成”。
- 当前稳定封板标签仍是 `v0.0.4`；`v0.0.5` 是 Phase 4A 收口实施基线。
- 数字 PDF、扫描/混合 PDF、DOCX、图片、安全 ZIP 子文档、EvidenceRef、人工复核和
  多形态输出已形成已验收闭环。
- `plan.md:853–857` 保留未勾选 OCR 条目，是因为完整多语言、全页 CER/WER、更大真实语料
  和高并发压力仍待完成。

#### 基于证据的判断

Phase 4A 的约定范围可以继续视为关闭；上述 OCR 扩展项是明确的后续覆盖，不应据此重新
打开整个 Phase 4A，也不能被误写成“所有 OCR 场景已完成”。

### 3.2 Phase 4B Legacy：已交付的正式链与尚未解决的结构缺陷并存

#### 已验证事实

- 批次 6 已实现用户确认的 11 种正式输出、独立读取器重开、QA、SHA、原子发布和用户隔离。
- 批次 8A 的本机主流程已通过用户验收，Legacy 继续默认。
- 8A 后的真实 PDF 表格转 CSV 任务仍暴露：
  “读取来源前锁定计划 + `TaskFamily` 独占路由 + 文档/表格执行器互斥”的结构缺陷。
- `plan.md:905–906` 仍把输出与转换写成未完成，但批次 6 已交付限定的 11 种格式。

#### 基于证据的判断

`plan.md:905–906` 是文档漂移与更宽泛目标混在一起：

- “当前 11 种格式的正式交付闭环”已经完成；
- “任意不支持格式自动寻找成熟转换器、复杂版式一比一复刻”并未完成。

后续规格必须拆开这两层，不能把复选框简单改成全完成，也不能反向否定批次 6 的已有交付。

### 3.3 vNext：真实灰度候选，不是生产 Delivery

#### 已验证事实

- 阶段 1 三路线赛马已完成，且没有候选直接通过生产硬门。
- 用户随后选择完整 `pi-coding-agent` JSONL RPC + 任务级 Docker；OpenCode 仅作后备，
  原 LangChain/LangGraph fallback 不再执行。
- VN-15、VN-20、VN-21、VN-32、VN-40、VN-41、VN-42 均已有不同比例的实现。
- VN-31、VN-50、VN-51 尚未开始，VN-60 不属于当前实施范围。
- 当前候选仍为 `candidate_ready`，不会创建正式 Delivery。

#### 尚未完成的硬缺口

- vNext 正式 Delivery Publisher 及其 QA/完整性/发布状态机；
- 独立依赖获取状态机；
- Word/Excel 连续 3/3、更多未知任务、至少 30 项冻结保留集；
- 完整 P0/PG-05、影子评测、回退演练和默认入口切换；
- 外部 OpenAPI 一等后端、用户自带 Key 的安全代理与原生用量记录；
- 图片、音频、视频及复合来源的统一契约与验收。

#### 基于证据的判断

vNext 当前最准确的名称是“管理员显式选择的生产灰度候选纵切面”。它比 PoC 更真实，
但还没有取得生产默认资格。Legacy 的正式 Delivery 不会自动让 vNext 候选变成正式交付。

### 3.4 Phase 4C：有可复用旧能力，但统一产品能力尚未开始

#### 已验证事实

- `plan.md:920–926` 的上传/URL、多媒体元数据、字幕/ASR/OCR、时间戳、坐标、切片和
  统一 STP/Harness 条目全部未勾选。
- 旧视频证据链能处理明确视频链接的字幕、ASR 和画面文字。

#### 基于证据的判断

旧视频链属于可复用资产，不是 Phase 4C 完成证据。当前没有证据表明图片、音频、视频已经
作为一等模态接入统一工作台、统一 Evidence、Verifier 和正式 Delivery。

#### 尚未验证的建议

Phase 4C 规格应沿用已经确认的五维模型：

`来源通道 × 内容模态 × 任务操作 × 证据策略 × 交付形式`

图片/音频/视频是内容模态，不应再形成与文本相互排斥的第二条总流水线。实时流、摄像头/
麦克风直播、HLS/DASH 直播、DRM 绕过和生物身份识别不进入本阶段。

## 4. GitHub #2–#11 对账

2026-07-30 读取时，#2–#11 均为 `OPEN`，均只有 `enhancement` 标签，评论数均为 0。
以下“处置”只是建议，尚未执行任何 GitHub 修改。

| Issue | 当前事实 | 建议处置 | 理由与后续依赖 |
|---|---|---|---|
| [#2 Agentic Runtime 专项整改总任务](https://github.com/Eclipseic1848/Mangrove_platform/issues/2) | 正文仍含“阶段 1 尚未开工”等过时状态；部分边界仍有效 | 保留并改写 | 缩为 Phase 4B vNext 实施总票；关联 #14/#15/#16/#22，不承载 Phase 4C 总地图 |
| [#3 三路线 AgentKernel 统一赛马](https://github.com/Eclipseic1848/Mangrove_platform/issues/3) | 赛马已完成，结果与选择路线已有正式报告 | 候选关闭为 `completed` | 关闭说明必须同时写清“无人直接取得生产资格”和“后续选择 Pi 灰度” |
| [#4 GoalContract 与 Agent Run 领域契约](https://github.com/Eclipseic1848/Mangrove_platform/issues/4) | 最小 Run/Event/Candidate/Checkpoint 已实现，完整领域实体未落地 | 保留，待 D2 后改写 | 对齐 #14 的统一领域模型；避免把 PoC `GoalContract` 当成生产契约 |
| [#5 Tool Catalog 与跨模态领域 Adapter](https://github.com/Eclipseic1848/Mangrove_platform/issues/5) | 最小候选清单和 PDF/DOCX/XLSX/CSV 来源复读已有；完整 Catalog/PDF A/B 未完成 | 保留，待 D2/D5/D7 后拆分或改写 | 确定性 Adapter、媒体工具赛马和复合来源语义不应继续塞进一个过宽 Issue |
| [#6 任务级 Docker 执行沙箱](https://github.com/Eclipseic1848/Mangrove_platform/issues/6) | 沙箱、真实取消、业务 Egress 主链已有；依赖状态机未完成 | 保留剩余安全工作 | 改成分阶段网络契约；补外部推理凭证代理、依赖状态机及逃逸/资源矩阵后再关 |
| [#7 动态 Agent Loop、预算与恢复](https://github.com/Eclipseic1848/Mangrove_platform/issues/7) | Verify→Replan、恢复、HTTP 幂等已有；`steer/inspect` 和完整强杀矩阵仍缺 | 保留并改写 | “预算”只指重试/轮次/时间等有界执行安全，不扩张成用户已否决的计费预算系统 |
| [#8 ContextManager 与 Skill Draft 治理](https://github.com/Eclipseic1848/Mangrove_platform/issues/8) | Context 门有首片；Skill Draft 未开始；“永久保留”与新生命周期方向冲突 | 拆分建议，需用户确认 | Context 完整性留在 Phase 4；Skill Draft 移到后续增强；保留周期由 #21 决定 |
| [#9 工作台事件、确认与候选结果 UX](https://github.com/Eclipseic1848/Mangrove_platform/issues/9) | 精简事件、候选区分和既有引导已有；多模态预览、可重播引导、完整失败操作未完成 | 待 #20 原型后改写 | 先做统一工作台原型和用户验证，不能用旧三步提示冒充新 UX 验收 |
| [#10 固定评测、影子运行与默认切换](https://github.com/Eclipseic1848/Mangrove_platform/issues/10) | 核心门仍有效，离线/影子评测和默认切换未开始 | 保持开放 | #22 固化全 Phase 4 门后，再决定 #10 是子门还是被新实施票替代 |
| [#11 Conductor 迁移专项](https://github.com/Eclipseic1848/Mangrove_platform/issues/11) | 未开始，Charter 明确首期不迁移 | 移出 Phase 4 活跃范围，保持未来专项 OPEN | 不做当前计划，不作为 Phase 4 完成阻塞项；是否开工以后另行确认 |

关键交叉证据：

- #3：三路线结果见
  [`stage1-execution-report.md`](../plans/2026-07-29-agentic-runtime-vnext-stage1-execution-report.md)
  第 43–49、121–129 行；路线选择见
  [`stage1-user-acceptance.md`](../plans/2026-07-29-agentic-runtime-vnext-stage1-user-acceptance.md)
  第 50–59 行。
- #4–#11：当前完成度见
  [`task-breakdown.md`](../plans/2026-07-29-agentic-runtime-vnext-task-breakdown.md)
  第 37–50 行。
- #6/#7：剩余安全和恢复边界见
  [`pi-full-capability-gray-plan.md`](../plans/2026-07-29-agentic-runtime-vnext-pi-full-capability-gray-plan.md)
  第 134–173、222–233 行。
- #10：生产门见 `docs/adr/0017-agentic-runtime-vnext.md:140–144`。
- #11：首期不迁移与后置条件见
  [`agentic-runtime-vnext-charter.md`](../plans/2026-07-29-agentic-runtime-vnext-charter.md)
  第 80–82、165–172 行。

## 5. 发现的文档与决策漂移

### 已验证事实

1. `plan.md:905–906` 的输出复选框与批次 6 的 11 格式交付报告没有同步。
2. #2 仍保留赛马未开工状态，而 VN-10 已完成。
3. #3 末尾保留的最小 LangChain/LangGraph Kernel 建议，已被后续完整 Pi RPC 决定取代。
4. #8 的“永久保留”与最新已确认的有期限保留、物理删除方向冲突。
5. 旧生产门强调本地 Qwen；最新方向要求本地与外部 OpenAPI 都是一等后端，但外部后端的
   数据外发、个人 Key 和验收矩阵尚未形成规格。

### 尚未验证的建议

- 不直接删除历史文字。应在 Issue/计划顶部增加“当前状态”和“被何项决定取代”，保留历史
  决策链。
- #4–#10 的稳定改写放在对应 D2–D10 决策完成后；现在只做最必要的状态提示。
- `plan.md` 的复选框治理放入后续规格/任务拆分，不在 D1 顺手修改。

## 6. D1 推荐结论与待人工确认

### 可作为下一阶段输入的结论

1. Phase 4A 不重开，只保留明确的扩展覆盖说明。
2. Legacy 已有正式交付能力，但 vNext 必须独立完成正式 Delivery 与生产门。
3. Phase 4C 从“可复用旧媒体资产”出发，仍需完整规格、工具赛马和统一工作台实现。
4. 8B/服务器验证继续后置；在明确目标服务器条件和重新授权前不实施。
5. #12 是 Phase 4 总地图；#2 收缩为 Phase 4B vNext 实施总票。

### 必须由用户确认的治理动作

1. 是否接受本报告作为 D1 冻结基线；
2. 是否授权把 #3 以 `completed` 关闭；
3. 是否授权把 #11 明确移出 Phase 4 活跃范围但保持 OPEN；
4. 是否同意暂不改写 #4–#10，等各 D2–D10 产物确认后一次性迁移；
5. 是否显式进入下一阶段 D2「统一能力/领域契约」。未经确认，不自动进入。

## 7. 本轮未执行事项

- 未修改产品代码或测试；
- 未运行测试；
- 未修改或关闭任何 GitHub Issue；
- 未创建分支、提交、PR、版本或标签；
- 未发布外部内容；
- 未触碰工作区原有未提交改动。
