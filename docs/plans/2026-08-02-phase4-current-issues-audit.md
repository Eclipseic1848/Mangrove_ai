# Phase 4 当前问题与优化审计

> **2026-08-06 更新：本文正文是历史审查快照，不再作为当前任务排序依据。** P0-3
> “vNext 没有正式 Delivery”已经完成；AC-06 本地 Adapter 与任务级 Sidecar 也已完成工程
> 实现但默认关闭。当前状态、最新可靠性修复与下一步以
> `docs/plans/2026-08-06-v008-runtime-reliability-and-candidate-retry-closeout.md` 和
> `handoff.md` 为准。本文保留用于解释当时为何作出 Publisher/AC-06 决策；其他未决项不因后续实现
> 自动完成。

> 审查日期：2026-08-02
> 当前分支：`v0.0.7`
> 审查基线：`HEAD 496f66540c52d385791acbb3a5450db94e0f07b1` 与当前未提交工作树
> 当前阶段：收口审查；不进入下一阶段实现
> 本轮范围：只读审查、验证和 Markdown 状态同步；不修改实现、不提交、不推送、不改 Issue

> **2026-08-03 状态修订**：本文保留 2026-08-02 审查快照。此后 AC-04 能力目录和 AC-05
> 独立获取状态机已完成工程实现并等待用户验收，因此下文“没有独立依赖获取状态机”以及
> “AC-04 以后均未实现”已经过时。当前能力专项缺口缩小为 AC-06 真实 Python/Node/CLI/
> MCP/Skill Adapter 以后；生产数据库迁移、真实四类 Adapter、验证晋级、SOP 学习和平台
> 发布仍未开始。vNext Candidate → 正式 Delivery、工作树收口、真实 Provider/完整 PG-05
> 仍然有效。2026-08-04 在制工作树已由70文件白名单收口，提交 `10d5fdd3` 已推送到
> `platform/v0.0.8`。当前推荐顺序更新为：**实现 vNext 正式 Publisher →
> 再由用户确认是否进入 AC-06**。最新交接以 `handoff.md` 和
> `docs/plans/2026-08-03-customer-demo-product-positioning-report.md` 为准。

## 1. 结论

当前没有发现覆盖感知文档检索核心的自动化回归：聚焦后端、全仓后端、完整 Playwright、
前端生产构建和 `git diff --check` 均通过。但是，当前工作树还不能被当作下一阶段的干净
实施基线，原因有四类：

1. 覆盖感知文档检索和 D4 模型连接仍缺用户代表任务验收；
2. vNext 仍停在 `candidate_ready`，尚未接入统一正式 Publisher；AC-05 独立获取状态机已完成
   工程实现，但仍待用户验收；
3. 真实外部 Provider、完整 PG-05 和生产安全门仍未完成；
4. 本地工作树、GitHub Issue 和部分总览文档的状态已经漂移，必须先收口再继续扩展。

因此推荐顺序是：**先验收并封存当前在制成果 → 补 vNext 正式 Delivery → 完成必要的真实
Provider/泛化门 → 再由用户选择 AC-06、D6 多媒体正式规格或其他新阶段。**

## 2. 本轮重新验证的事实

### 2.1 当前自动化

```text
E:\python3.13\python.exe -m pytest
  tests/test_document_tool_relay.py
  tests/test_document_retrieval.py
  tests/test_agentic_runtime.py
  tests/test_pi_runtime_workspace_api.py
  -q --basetemp=.pytest-tmp\audit-current
=> 57 passed, 2 warnings

E:\python3.13\python.exe -m pytest tests -q
  --basetemp=.pytest-tmp\audit-full
=> 672 passed, 4 skipped, 4 warnings

cd frontend && npm run test:e2e
=> 51 passed

cd frontend && npm run build
=> 通过；保留两个大于 500 kB 的 chunk 警告

git diff --check HEAD
=> 通过
```

4 个跳过项仍是需要显式参数的真实数据库和大规模性能门，不是本轮测试失败。

### 2.2 已确认实现边界

- 覆盖感知文档检索 DR-00–DR-07 已完成工程实现，仍是
  `engineering_verified_pending_user_acceptance`。
- vNext Pi 成功链明确把验证通过结果保留为 `candidate_ready`；代码注释和状态投影均明确
  `Publisher 尚未接入`，不会冒充正式 Delivery。
- `EgressPolicy` 已区分 `dependency_acquisition` 与 `business_execution`，但
  `PiRuntime.start/resume` 当前只进入业务执行策略；独立依赖获取编排尚未实现。
- Phase 4B Legacy 已有正式发布闭环；缺失的是 vNext Candidate 到统一 Publisher 的适配，
  不能把两者混为“整个系统没有 Delivery”。
- GitHub #24–#31 仍为 `OPEN + ready-for-agent`，但本地执行报告已经记录工程实现完成；
  Issue 生命周期与本地事实不同步。
- `pip check` 当前仍有两项不一致：缺少 `types-pytz`，以及 `crawl4ai 0.9.0` 要求
  `lxml~=5.3`、当前环境为 `lxml 6.1.1`。
- 当前工作树同时包含 vNext 实现、文档、运行期教训/模板变化、测试临时目录和前端测试
  结果，不能直接整体暂存或提交。

## 3. 阻塞下一阶段的收口问题

### P0-1：当前成果尚未形成用户验收基线

**事实**：DR-00–DR-07 自动化和真实文件回放通过，但用户尚未在工作台完成指定页、首个、
全部和故障恢复的代表操作；D4 也只有局部纠偏反馈，没有完整产品验收结论。

**需要完成**：

1. 用真实工作台各运行一次指定范围、首个完整对象、全部匹配和故障重试；
2. 核对冻结理解、阶段进度、候选、证据定位和恢复动作；
3. 对 D4 至少完成个人连接、平台连接、自定义/LAN、导入重验和任务冻结的代表验收；
4. 分别记录 `UserAccepted` 或具体拒绝项，不能用自动化代替。

### P0-2：在制工作树尚未收口

**事实**：覆盖检索代码、测试、ADR 和执行报告仍未提交；工作树还混有运行时生成的教训、
模板、`.pytest-tmp/`、`frontend/test-results/` 和本机设置变化。

**风险**：直接进入下一阶段会把两个阶段的变更和运行期数据混在一起，后续无法可靠审查、
回滚或发布。

**需要完成**：先由用户确认哪些文件属于本轮成果，清理或排除临时产物，再按显式白名单
提交；提交、推送和 Issue 写操作仍需单独授权。

### P0-3：vNext 没有正式 Delivery

**事实**：Pi 候选即使通过独立 Verifier，也只进入 `candidate_ready`。ADR-0019 已冻结
`PublishIntent`、`PublicationKey`、提交点和默认切换语义，但精确 Schema、迁移、通用
Publisher Adapter、恢复对账和 UI 尚未实现。

**影响**：vNext 可以生成并下载候选，但不能作为正式、不可变、带 Manifest 和重开 QA 的
用户交付。任何新模态继续接入 vNext 都会继承这个缺口。

### P0-4：独立依赖获取已工程实现，真实 Adapter 与晋级闭环未完成

**当前事实（2026-08-03 修订）**：AC-04 已建立 Owner 隔离的不可变能力目录；AC-05 已实现
“不挂载用户来源 → 受治理获取并冻结依赖 → 离线构建 → 清理/恢复”的状态机，并等待用户
验收。生产数据库迁移未执行，AC-06 真实 Python/Node/CLI/MCP/Skill Adapter、验证晋级、
SOP 学习和平台发布仍未开始。

**影响**：基础治理框架已经存在，但在真实 Adapter 和晋级门完成前，不能宣称系统已经能
自主获取并生产使用任意 Tool/MCP/Skill；业务阶段仍不得联网安装依赖。

**实施证据**：对话分支见
[AC-00～AC-03 执行报告](2026-08-02-conversation-steering-ac00-ac03-execution-report.md)，
能力目录与获取状态机分别见
[AC-04 执行报告](2026-08-02-agentic-capability-ac04-execution-report.md) 和
[AC-05 执行报告](2026-08-02-agentic-capability-ac05-execution-report.md)。

## 4. 应尽快修复或优化的问题

### P1-1：D4 缺真实 Provider 端到端证据

- 尚未用真实 DeepSeek、Qwen、OpenAI、Anthropic、Gemini、Kimi 或智谱 Key 做 Smoke；
- 尚未完成真实 Pi → Grant → Relay → 外部 Provider → Usage 的端到端调用；
- DNS rebinding、证书生命周期和备份擦除属于生产安全门，当前不阻塞架构迭代，但默认
  切换或生产外发前必须完成。

### P1-2：完整 PG-05 和默认切换门未完成

- 30 项冻结泛化集未完成；
- Word/Excel 连续 `3/3` 尚未完成；
- D10 冻结验收语料和生产硬门仍是开放 Issue；
- Legacy 继续默认，不能据现有纵切面自动切换 vNext。

### P1-3：首次长扫描文档的发现性能仍高

109 页扫描 PDF 的冷发现缓存约需 483 秒；该数字来自缓存时间戳，原进程在汇总前被终止，
不是完整脚本墙钟输出。缓存回放 8.092 秒不能代表首次任务。后续应补冷启动可重复测量、
进度/取消体验和真实泛化样本，不能虚构统一秒级目标。

### P1-4：依赖环境不是干净基线

`pip check` 的两项冲突虽未阻断当前 672 个测试，但会影响类型工具链和 Crawl4AI 运行可信度。
应在独立依赖修复任务中选择兼容版本并回归采集器，不能只为让 `pip check` 变绿而随意降级。

### P1-5：开发启停反馈问题已修复

2026-08-03 已改为以脚本目录为根、强制 UTF-8、显式监听 `0.0.0.0:5173`，并等待后端
`/api/health`、前端 HTTP 和 LAN 监听全部就绪；停止脚本会清理本项目完整进程树和 Pi 临时
资源。2026-08-04 收口复核又补上端口监听归属门：未知项目进程只告警、不终止。证据见
[Windows 一键启停可靠性报告](2026-08-03-windows-start-stop-reliability-report.md)。

### P1-6：GitHub Issue 状态与实现漂移

#24–#31 已有工程执行报告但仍标记 `ready-for-agent`；#13、#16、#17 等总工单也未反映最新
纵切面。下一次获准写 GitHub 时，应按“工程完成 / 待用户验收 / 仍缺生产门”逐项评论或
调整标签，不能批量关闭所有 Issue。

## 5. 非阻塞优化与明确后置项

### P2 优化

- 前端生产构建通过，但 `TextLayer` 与主入口 chunk 超过 500 kB；可在体验性能专项中做
  路由级拆分，不应混入当前收口。
- 测试依赖仍产生 Starlette/httpx、pynvml 和 Firecrawl Pydantic 警告；应跟随独立依赖
  升级处理。
- SQLite 导出、完整 HTTP 高级配置、500 MB 真实文件、多语种/高并发仍是已知能力边界。

### 用户已经明确后置

- 8B 干净镜像、Linux/Compose、服务器并发和目标服务器验收；
- PaddleOCR/faster-whisper 完整本地模型卷及长媒体服务器门；
- 管理员授权的物理擦除和审计墓碑之外的不可逆删除；
- Conductor 全量迁移、默认入口切换、版本、标签和外部发布。

这些项目不应反向阻塞当前工程功能收口，也不得在未确认时自动开工。

## 6. 下一阶段前的推荐完成门

1. 用户完成覆盖检索和 D4 代表任务验收；
2. 对当前工作树做白名单归属确认并形成干净提交基线；
3. 实现并验证 vNext Candidate → 正式 Delivery；
4. 用户验收 AC-04/AC-05，并在正式 Publisher 完成后确认是否进入 AC-06 真实 Adapter；
5. 完成与实际下一阶段风险匹配的真实 Provider/PG-05 门；
6. 同步 GitHub Issue 状态；
7. 由用户决定下一业务阶段是 D6 多媒体正式证据规格、D7 复合来源，还是继续生产收口。

## 7. 必须由用户确认的决策

- 当前在制成果的提交与推送白名单；
- 是否把 vNext 正式 Delivery 作为下一实施包；
- 正式 Publisher 完成后，是否授权进入 AC-06 真实 Adapter；
- 外部 Provider 使用哪套真实 Key/账号做 Smoke，以及允许外发的合成数据范围；
- 是否进入 D6/D7、默认切换、服务器验证、物理删除、版本或外部发布。
