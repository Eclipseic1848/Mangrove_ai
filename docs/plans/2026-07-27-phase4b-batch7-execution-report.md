# Phase 4B 批次 7：正式数据工作台执行报告

> 日期：2026-07-27
> 分支：`v0.0.6`
> 开发基线：`396586d`
> 功能提交：`48bf118`
> 二次 P0 纠偏提交：`554acd6`，已推送到 `platform/v0.0.6`
> 纠偏复审：2026-07-27，用户验收发现首版前端存在即时预览、进度状态和可用性问题；
> 原“仅凭 26 条基础 E2E 即验收通过”的结论已撤销并重新执行 UX 审计
> P0 复审：真实 Word 限定条款任务被错误执行为全文转换；已完成通用语义继承、
> 范围失败关闭和范围验证修复，证据见
> `2026-07-27-phase4b-batch7-p0-semantic-correction-report.md`
> 二次 P0 复审：第一次修复仍混同“扫描整份来源”和“输出整份来源”，且没有用用户
> 原始 Word 验证宽泛商务语义的完整召回。2026-07-28 已完成二次纠偏和真实源严格复验
>
> 当时结论：纠偏代码已完成，并达到真实交互回归、视觉自测和真实 Word 严格复验门禁，
> 等待用户确认；
> 内部自测不得再表述成用户验收通过。Phase 4B 仍需批次 8，不得封板或创建
> `v0.0.6` 标签
> 历史暂停点：用户于 2026-07-28 暂停工作；恢复后先确认批次 7，不自动开始批次 8
>
> 状态更新（2026-07-29）：上述暂停点已结束；批次 8A 后续完成并通过用户验收。
> 新发现的 PDF 文档表格转 CSV 架构问题另由 Agentic Runtime vNext 专项记录，不回写
> 本报告的历史验收范围。

## 1. 实际交付

### 1.1 后台编排

- 新增用户隔离的 `semantic_workspace_tasks`、`semantic_workspace_revisions` 和
  `semantic_workspace_events`；
- 创建任务立即返回 `202`，两个普通 worker 后台执行，重任务通过信号量串行；
- 服务重启扫描 queued/running 任务，并复用既有 Checkpointer 恢复；
- 工作台只编排 Compiler、Binder、Harness 和 Delivery，不复制业务执行器；
- 工作区事件先写 SQLite，再通过带 owner 校验的 SSE 输出；
- 排队、运行、待确认任务均可取消；取消后不发布新的正式 Delivery；
- 新修改创建不可变 V2/V3，旧 run、QA、Manifest、预览和下载继续可访问；
- 回收站按用户隔离，保留 30 天，启动时和每小时自动清理到期工作台记录；
- “永久删除”只删除工作台聚合记录，底层证据、上传原件和正式交付继续作为生产溯源保留。

### 1.2 产品体验

- `/data-prep` 默认进入统一“数据工作台”，原 Phase 4A 工作区保留在
  `/data-prep?legacy=1`；
- PC 三栏布局：任务与回收站、执行与结果、可调宽度原文件检查器；
- 上传完成后立即在右侧显示原文件预览；用户无需先创建并执行任务才能看到文件内容；
- 上传后进入聚焦态，收起示例和大段引导，只保留文件、目标、推荐输出和预览；
- 支持拖放、点击、粘贴上传、真实上传进度、失败重试、移除和键盘排序；
- 表格默认推荐 Excel，文档默认推荐 Word + PDF；低频格式和模型选择进入“更多”，
  不再把 11 种格式和模型同时铺在首屏；
- 支持任务级模型选择；外部 OpenAPI 在调用前显示服务、外发类型、用途、范围和风险；
- 真实事件以简洁事实摘要流式显示，不展示思维链、不做逐字假流式、不使用 Emoji；
- 工作区事件与 Harness 事件按阶段归并；最多一个阶段处于执行中，后续阶段完成时前序
  自动收口，Harness 回放早期节点不会把转圈状态倒退；
- 已完成任务默认折叠过程并优先展示结果，仍可展开查看完整执行阶段；
- STP、运行尝试和修复详情默认折叠；实质歧义一次只显示一个问题；
- 结构化结果使用 DuckDB 服务端计数、搜索、排序和分页；
- 文档结果按段落/结论展示 EvidenceRef；
- 原文件预览完整保留：PDF 页码、缩放和 bbox 高亮，DOCX 等结构元素定位，
  表格来源行和值回显；
- 完成结果在 1366 宽三栏状态下保持正常横排，文件名、QA、下载和来源跳转不再被挤成竖排；
- 正式输出逐文件显示 QA、大小和下载；下载全部 ZIP 包含 outputs、Manifest、QA 和 trace，
  原件默认不打包、可由用户显式选择；
- 帮助入口、三步引导和当前真实可执行的表格/文档示例已落地；
- 深色、浅色主题均沿用 Mangrove 现有视觉语言。

## 2. 成熟组件复用

本批没有自建通用表格、SSE、弹窗、拖拽或无障碍框架：

- `@tanstack/react-table`、`@tanstack/react-virtual`；
- `@microsoft/fetch-event-source`；
- Radix Dialog、AlertDialog、Tooltip、Collapsible；
- `@dnd-kit/core`、`@dnd-kit/sortable`；
- `@axe-core/playwright`；
- 继续复用现有 React、Vite、Tailwind、TanStack Query、react-dropzone、
  react-resizable-panels、react-pdf、sonner 和 lucide。

工作台通过动态 import 独立分包，生产构建中
`SemanticWorkspacePage` 约 244 KB（gzip 约 73 KB），没有把 PDF、虚拟表格和拖拽能力
全部加入基础首屏。

## 3. 关键修复

1. 重任务初版在取得信号量前已创建 `asyncio.Task`，实际会越过并发限制。现改为取得令牌后
   再启动，并新增并发峰值固定为 1 的回归测试。
2. 新建和修订任务均要求至少一种正式输出格式，避免执行成功却没有可交付文件。
3. 结果版本查询、预览和 ZIP 均显式绑定 revision，查看旧版本不会串到最新 Delivery。
4. 浅色主题的当前导航、新建任务按钮和底部版权文字对比度未达到 WCAG AA，已调深关键交互色；
   深浅主题 axe serious/critical 均为 0。
5. PostCSS 从受高危路径穿越告警影响的版本升级到 `8.5.23`，nanoid 升级到 `3.3.16`。
6. 首版新建页只显示“已上传”文件卡，创建任务前没有调用预览接口；现把上传态传给工作台，
   上传完成即打开表格/文档/PDF 原文件检查器。
7. 首版时间线直接逐条渲染底层事件，`stage_started` 永远转圈，即使同阶段或后续阶段已经
   完成；现按固定业务阶段聚合开始、完成、失败、等待和修复事件，并覆盖重复执行与乱序回放。
8. 首版只提供“回收站”入口，却没有“移入回收站”操作；现为终态任务补齐确认操作、
   恢复后的详情刷新、失败提示和存储统计刷新。
9. 待确认弹窗首版关闭后无法重新打开；现显示持续可见的“继续回答”入口，并保持取消任务可用。
10. 首版测试只证明页面渲染、上传卡出现和提交按钮可用，没有真正点击提交，也没有覆盖结果、
    来源、取消、版本和回收站；纠偏后这些均成为 Playwright 硬门。
11. 第二次真实验收发现多轮澄清会丢失上一版章节范围，Binder、执行器和 Verifier 又把
    零范围误当全文成功。现已建立 Compiler → Binder → PhysicalPlan → Executor → Verifier
    五层失败关闭，并新增 Word 限定条款 → TXT 的端到端正反断言。
12. 第一次 P0 修复仍错误地把回答“整份文档”解释为全文输出；宽泛语义又先被模糊召回
    或 rerank top-k 截断。现已分离“全源搜索”和“全文输出”，`content_query` 扫描全部
    文档目标；非宽泛查询使用 Instructor 结构化本地分类且强制 ID 完整覆盖，宽泛合同/
    商务查询使用章节上下文和有界结构规则。分类缺失、重复、越界、未知或不确定均失败关闭。
13. TXT 原先为每个 passage 重复写查询标题；现按相同语义标签聚合，标题只输出一次，
    原文仍按来源顺序保留。

## 4. 验收证据

### 4.1 后端

```text
E:\python3.13\python.exe -X utf8 -m pytest -q \
  --basetemp=.pytest-tmp\corrective-full

934 passed, 4 skipped, 0 failed, 4 warnings in 307.33s
```

四项 skip 为需显式开启的大规模性能测试和真实 MySQL/PostgreSQL 容器测试，不是失败。

批次 7 工作台专项：

```text
6 passed
```

覆盖后台完成、正式预览/ZIP、外部服务确认、取消、owner 隔离、回收站、30 天清理、
不可变 revision/历史 Delivery 和重任务串行。

### 4.2 前端

```text
npm.cmd run test:e2e
36 passed

npm.cmd run build
passed
```

Playwright 覆盖：

- 1366×768 浅色、1920×1080 深色；
- 新工作台即时原文件预览、推荐输出、默认模型和真实任务提交；
- 完成结果、正式交付、服务端预览和原文件证据定位；
- 运行中最多一个活动阶段、完成态无遗留转圈、失败停在真实失败阶段；
- Harness 乱序回放、重复事件归并和完成过程默认折叠；
- 待确认问题收起/重开、随时取消、状态刷新；
- 不可变新版本、历史版本只读、移入回收站和恢复；
- 不支持格式明确提示，不静默忽略；
- 深浅主题 axe serious/critical 为 0；
- 原 Phase 4A 文档工作台全部旅程；
- Phase 3 数据库连接和表列选择兼容入口。

统一工作台新增 DOCX 上传完成即结构化预览硬门。人工复审了上传即时预览、完成进度折叠和
结果 + 原文件证据三栏截图；首次复审发现并修复了窄栏文字竖排，修复后文件名、
下载按钮、搜索、结果表和证据高亮在 1366×768 下保持可读。

完整前端回归首次运行出现 1 条旧 DOCX 结构化预览 5 秒超时；该用例单独复跑
1 passed，随后当时完整 34 条再次运行为 34 passed。补充“后序完成事件收口前序
遗留开始态”用例后为 35 passed；本次再补 DOCX 即时预览，最终完整 36 passed。
首次抖动记录继续保留，
没有从报告中删除。

### 4.4 P0 语义纠偏后的最终回归

```text
E:\python3.13\python.exe -X utf8 -m pytest -q \
  --basetemp=.pytest-tmp\p0-repository-full

943 passed, 4 skipped, 0 failed, 4 warnings in 224.74s

npm.cmd run build
passed

npm.cmd run test:e2e
35 passed
```

该回归新增覆盖多轮范围继承、无范围失败关闭、显式全文、页码选择、限定条款排他性和
全文混入拒绝。内部验证通过仍不等于用户验收通过。

### 4.5 二次 P0 纠偏与用户真实 Word 严格复验

用户原始 Word 仅在本地运行期目录参与复验，不提交文件名、正文、用户标识或验收脚本：

```text
source_target_count = 501
selected_count = 83
pre_business_false_positive_count = 0
cost/service/acceptance/confidentiality/intellectual_property/penalty misses = 0
TXT = 19,689 bytes（错误全文版为 121,202 bytes）
selection_heading_count = 1
delivery_qa_passed = true
```

定向后端测试为 48 passed，前端生产构建通过，完整 Playwright 为 36 passed。全仓后端
最终代码门禁为：

```text
953 passed, 4 skipped, 0 failed, 4 warnings in 267.60s
```

四项 skip 仍为需显式开启的大规模性能测试和真实 MySQL/PostgreSQL 容器测试。该证据证明
当前真实场景不再全文复制，但仍不等于用户已确认，也不替代批次 8 的跨场景扩展评测。

### 4.3 依赖安全

`npm audit --package-lock-only --audit-level=high` 当前为 0 high、0 critical、
2 moderate。剩余两项来自 React Router 6；
上游修复要求升级到 React Router 7.18.1，属于跨大版本迁移。当前应用无 SSR，所有
`Link/useNavigate` 目标由项目代码构造，未接收不可信重定向目标；该迁移登记为 B7-D11，
不得使用 `npm audit fix` 在本批盲升。

最终复核时，npm 即将退役的 quick audit 端点曾对完整依赖树返回一次
`400 Invalid package tree`；`npm ls --all --json` 随后返回 0 且无 tree problems，
改用锁文件审计成功得到上述结果。未为规避审计端点错误而重装或改写依赖锁。

## 5. 明确保留的边界

以下均未伪装为已完成：

- 同一任务联合处理表格和文档；
- 管理员跨用户任务、存储和回收站治理；
- 示例/引导在线编辑；
- 逐字段版本差异和交互式合并；
- 移动端、多媒体、分布式队列；
- DOCX/PDF/PPTX 原版式一比一复刻；
- 外部 sidecar/线程的进程级强制终止；
- 任务级模型目录漂移的执行前校验与重新选择；历史任务中的 `qwen3.7-max` 当前返回
  404，现有边界是不静默切换服务或外发文档；
- React Router 7 迁移；
- 大语料、故障注入、完整观测、压力门和封板审计；
- 删除 Phase 4A 回退代码；
- 物理删除底层审计、交付和上传原件。

完整延期台账见批次 7 实施方案第 8 节和 `handoff.md`。
