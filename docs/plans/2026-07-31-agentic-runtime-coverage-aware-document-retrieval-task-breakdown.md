# Agentic Runtime vNext 覆盖感知文档检索任务拆分

> 日期：2026-07-31
> 阶段：工程实现与验证已完成
> 状态：`engineering_verified_pending_user_acceptance`
> 上游规格：[覆盖感知文档检索规格](2026-07-31-agentic-runtime-coverage-aware-document-retrieval-spec.md)
> 架构决策：[ADR-0025](../adr/0025-coverage-aware-document-retrieval.md)
> 执行证据：[DR-00–DR-07 执行报告](2026-07-31-agentic-runtime-coverage-aware-document-retrieval-execution-report.md)
> 本轮仍未授权 GitHub Issue 创建、提交、推送、版本、标签或外部发布。

## 0. 工单完成状态

| 工单 | 工程状态 | 核心证据 |
|---|---|---|
| DR-00 | 完成 | 固定 Pi 0.80.10 生产 Extension 探针通过 |
| DR-01 | 完成 | 指定页真实回放只权威读取 1 页 |
| DR-02 | 完成 | 首个对象早停、跨页和证据门回归通过 |
| DR-03 | 完成 | 109/109 发现、9/9 候选精读、故障失败关闭 |
| DR-04 | 完成 | 16 条语义语料连续 3×16/16；真实 Pi 3×3/3 |
| DR-05 | 完成 | 恢复、幂等、取消零写入、Owner/Grant/Egress 门通过 |
| DR-06 | 完成 | 进度、刷新恢复、主题、axe 和完整 Playwright 通过 |
| DR-07 | 工程完成 | 全仓门通过；用户实际操作验收待下次完成 |

## 1. 当前事实、推断与未验证建议

### 1.1 实施前已验证事实（历史输入）

- 当前工作区的试验实现由 `SemanticWorkspaceManager` 在 Pi 启动前调用
  `PiSourcePreparer.prepare()`，扫描 PDF 会先生成整份 `.mangrove-ocr.jsonl`；
- `PiRuntime` 已加载自有 TypeScript Extension，并通过 JSONL RPC 运行固定的
  `pi-coding-agent 0.80.10`；
- Agentic Runtime 已有 Run、Event、Candidate、VerificationReport 的 SQLite 持久化和
  独立候选 Verifier；
- 当前前端时间线只识别 `interpret/inspect/execute/verify` 等既有阶段，后端试验事件使用
  `stage=observe`，因此可能显示任务未开始；
- Pi 官方主线文档说明 Extension 可以通过 `pi.registerTool()` 注册模型可调用的自定义工具，
  并支持 TypeBox 参数 Schema、动态启停和工具结果 `details`。来源：
  [Pi Extensions 官方文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md)。

### 1.2 实施前基于代码的推断

- 复用现有 Pi Extension 比另建一套 Agent 框架或先引入 MCP 更贴近当前实现；
- Extension 位于任务容器内，而 MinerU/Paddle 与权威缓存位于 Mangrove/LAN 一侧，因此
  需要一个只允许当前 Run 调用的内部文档工具 Relay；
- 可复用现有模型 Relay/AccessGrant 的绑定方式，但文档工具 Grant 必须独立用途、独立 Token，
  不能复用 Provider 凭证；
- 当前 CandidateVerifier 可以承接覆盖完成门，但不能只在既有报告里增加一句摘要，必须让它
  重开覆盖契约、覆盖账本和证据读取集。

### 1.3 实施前尚未验证的建议

- 当前固定的 Pi `0.80.10` 是否与主线文档中的全部 `registerTool()` 行为一致；
- 内部文档工具 Relay 在现有 Docker/Egress 网络中的真实延迟、取消和恢复行为；
- 低成本全页发现对用户 109 页扫描 PDF 的召回率与耗时；
- Pi 对不同中文问法生成等价覆盖契约的稳定性；
- 新路线相对整份高质量 OCR 的真实加速比例。

因此 DR-00 是阻塞性真实 PoC；未通过时停止并回到规格，不得未经确认改用 MCP、另一 Pi
版本、另一 Agent 框架或外部 OCR。

## 2. 预先确认的测试 Seam

TDD 只在以下外部可观察 Seam 上写测试，不测试私有函数或内部 Provider 调用顺序：

| Seam | Interface | 观察结果 |
|---|---|---|
| S1 Pi 能力工具 | `freeze_coverage / inspect_source / discover_content / read_evidence` | Pi 能否自主选择并收到结构化 Observation |
| S2 文档检索 Module | `inspect / discover / read` | 来源地图、候选发现、权威证据与质量状态 |
| S3 覆盖完成门 | `verify_coverage(contract, ledger, evidence)` | 通过、结构化缺口或失败关闭 |
| S4 工作台产品 Interface | 既有任务详情与 SSE | 冻结理解、真实阶段、覆盖进度、恢复状态 |
| S5 浏览器体验 | `/data-prep` 用户流程 | 用户知道系统理解了什么、正在做什么、为何停止或失败 |

内部 OCR Adapter、缓存文件布局、页批次和提示词措辞不是测试 Seam。它们可以在不改变上述
行为的情况下重构。

## 3. 依赖图

```text
DR-00 精确版本工具桥 PoC
  ↓
DR-01 指定范围纵向切片
  ↓
DR-02 首个完整对象纵向切片
  ↓
DR-03 全部匹配纵向切片
  ├────────→ DR-04 Pi 语义与工具自主性评测 ──┐
  ├────────→ DR-05 恢复、取消与安全加固 ─────┤
  └────────→ DR-06 工作台进度与解释 UX ──────┤
                                                ↓
                                     DR-07 试验路径替换与真实验收
```

DR-00 未通过则其余工单全部阻塞。DR-04、DR-05、DR-06 可以在 DR-03 后独立实施，但
DR-07 必须等待三者全部通过。

## 4. 工单总览

| 工单 | 独立价值 | 完成证据 | 人工控制点 |
|---|---|---|---|
| DR-00 | 证明固定 Pi 版本能安全调用 Mangrove 工具 | 真实 Docker + Pi 0.80.10 工具调用、拒绝越权 Grant | 失败后更换接入路线需确认 |
| DR-01 | 指定页任务不再整份 OCR | 数字/扫描指定页候选与覆盖验证通过 | Coverage 字段语义变更需确认 |
| DR-02 | “第一个完整对象”可智能扩页并早停 | 跨页对象完整、后续高质量 OCR 为零 | 业务边界不清时只问一个问题 |
| DR-03 | “全部匹配”不漏散落页面 | 109/109 发现、候选精读、漏页门失败关闭 | 不允许降级为 best effort |
| DR-04 | 不同问法仍由 Pi 理解而非关键词路由 | 本地 Qwen 冻结语料、多轮重复结果 | 修改业务默认需确认 |
| DR-05 | 任务可恢复、取消且不串用户 | 恢复/取消/幂等/Owner/Egress 测试 | 新权限、外发另行确认 |
| DR-06 | 用户始终知道任务位置与覆盖含义 | 刷新/断线/失败 Playwright 与可访问性 | 新确认步骤需 UX 确认 |
| DR-07 | 新路线替代错误前置路径并通过真实验收 | 用户 109 页文件 A/B、全仓门禁、用户验收 | 删除缓存、提交、推送均另行确认 |

## 5. 详细工单

### DR-00：Pi 0.80.10 自定义工具桥真实 PoC

目标：只用合成来源证明当前固定镜像中的 Pi 可以通过 Extension 调用任务绑定的 Mangrove
能力工具，并形成可取消、可审计的 Observation。

Red：

1. 精确 `pi-coding-agent 0.80.10` 容器中没有 Mangrove 工具时，真实任务无法调用
   `inspect_source`；
2. 错误 Run、Owner、revision、purpose 或过期 Token 调用内部 Relay 必须失败；
3. 取消信号必须中止正在执行的合成工具调用。

Green：

- 在现有 Extension 目录新增最小 `inspect_source` 自定义工具，使用官方
  `pi.registerTool()` 与 TypeBox；
- 建立内部文档工具 Relay 的最小 Interface 和测试内存 Adapter；
- 签发绑定 Owner/TaskRevision/Run/Purpose/TTL 的独立文档工具 Grant；
- 工具结果进入现有上下文门，Token、宿主路径和其他用户信息不进入事件或模型文本；
- Egress 只增加当前内部 Relay 的精确目标，不放宽公共网络。

完成证据：真实 Pi Docker 调用 1/1、取消 1/1、五类错误 Grant 全拒绝、无秘密扫描、残留
容器/网络为零。PoC 通过后保留为生产 tracer，不另写第二套桥。

### DR-01：指定范围读取纵向切片

用户场景：“只读取第 20 页的审批金额并输出 JSON。”

Red：

1. Pi 尚不能冻结结构化 `CoverageContract`；
2. 当前路径会在 Pi 运行前处理整份扫描 PDF；
3. 工作台无法展示冻结的范围、数量和完整性摘要；
4. 没有 `CoverageLedger` 证明只观察/读取了哪些内容单元。

Green：

- Pi 调用 `freeze_coverage` 提交自主理解的契约草案；确定性校验器只校验范围、权限和内部
  一致性，不使用关键词重写业务语义；
- 完成 `inspect/read` 的最小文档检索 Module Interface，生产 Adapter 复用现有数字 PDF、
  MinerU/Paddle 路由，测试使用内存 Adapter；
- 为 Run 持久化不可变 CoverageContract 和可追加 CoverageLedger；迁移幂等，不改写旧 Run；
- Pi 只权威读取指定页，生成带 EvidenceRef 的候选；Verifier 检查范围和证据；
- 工作台任务详情返回解释摘要和覆盖计数。

完成证据：数字 PDF、扫描 PDF、越界页、低质量页各一条 Module/HTTP Interface 测试；真实 Pi tracer
证明未生成整份高质量 OCR sidecar；旧 Legacy 路径保持不变。

### DR-02：首个完整对象纵向切片

用户场景：“提取第一个报销审批单，按人名组织 JSON。”

Red：

1. 关键词命中第一页但单据跨到下一页时，Pi 可能过早停止；
2. 缺少必需字段或边界证明时，Verifier 仍可能只按候选文件内容判断；
3. 后续页面存在第二个单据时，系统无法证明返回的是稳定顺序上的第一个。

Green：

- 增加 `discover` Interface，返回候选内容单元、质量和可继续读取的稳定引用；
- Pi 自主选择发现顺序、调用 `read` 精读候选和相邻内容、根据 Observation 扩页；
- CoverageLedger 记录顺序、对象边界、必需字段和 Pi 的停止提议；
- Verifier 不接受“发现关键词”作为完整对象，缺字段时返回结构化缺口供 Pi 有界重规划；
- 找到并验证首个完整对象后，不对后续无关页面做高质量 OCR。

完成证据：单页对象、跨页对象、第一页假阳性、缺字段、后续重复对象五个场景；真实 Pi
至少连续 3 次选择正确对象并通过独立验证。

### DR-03：全部匹配纵向切片

用户场景：“查找文件中 XXX 的全部数据”，目标散落在第 2、57、108 页。

Red：

1. Pi 在首个命中后提议结束时，当前 Verifier 无法证明仍有未知页面；
2. 某页发现超时或空返回时可能被误记为无匹配；
3. 全部页面高质量 OCR 虽完整但成本不可接受。

Green：

- Pi 可以自由安排发现优先级和批次，但严格全部匹配最终要求获准内容单元全部进入可信发现；
- 数字页优先原生文本，扫描页允许低成本发现；只对候选、盲区和低质量页做权威读取；
- SourceDiscoveryIndex 按 Owner、source SHA-256 和版本隔离缓存；发现索引不能直接成为证据；
- 未知或未解决低质量单元存在时，Verifier 拒绝完成并把缺口返回 Pi；
- 表格目标保留行列、页码和坐标，不退化为散乱文本。

完成证据：合成 109 页基准精确命中 2/57/108；发现覆盖 109/109；结果 3/3；候选页全部
权威读取；非候选页不做高质量 OCR；模拟第 40 页失败时任务失败关闭而非少报成功。

### DR-04：Pi 语义理解与工具自主性评测

目标：证明智能来自 Pi 的语义理解和 Observation 循环，而不是换了一套问法规则。

Red：

- 同一目标换同义表达后契约不稳定；
- 同一关键词处于“找一个示例”和“列出全部记录”上下文时产生相同路线；
- Pi 不读取 SourceMap 就机械执行固定工具顺序；
- 低置信度但实质影响结果/成本的歧义没有进入单问题澄清。

Green：

- 冻结不少于 16 条覆盖语义语料，覆盖首个、指定数量、全部、指定范围、整源、歧义和多轮
  继承；不为语料增加专属 Prompt 分支；
- 使用真实本地 Qwen 运行 Pi，记录结构化契约、能力调用轨迹、停止提议和最终验证；
- 等价问法得到等价业务契约；不同上下文得到不同契约；明确任务不增加确认点击；
- P0 场景连续 3/3，通过标准看业务行为，不要求工具调用序列完全相同。

完成证据：冻结语料与评分报告、真实本地模型轨迹、代码审查确认没有问法关键词路由。模型
能力不足时报告真实失败，不添加场景特判掩盖。

### DR-05：恢复、取消、幂等和安全加固

目标：工具循环跨故障仍保持同一契约、同一覆盖事实和同一 Owner。

Red：

- Pi 会话恢复后重复精读或丢失 CoverageLedger；
- 取消后 Relay/OCR 仍继续工作或产生候选；
- 同一请求重放产生第二份契约/账本；
- 另一用户可以猜测 Grant、索引或 EvidenceRef；
- 来源中的提示注入改变覆盖范围或诱导外发。

Green：

- CoverageContract、CoverageLedger 和工具幂等键随 Run 恢复，重复调用返回同一事实身份；
- 取消传播到 Extension、Relay、解析 Adapter 和 Pi 容器，取消后零新候选；
- Owner/TaskRevision/Run/Purpose/TTL 全部在 Relay 重新验证；
- 缓存路径不暴露 user_id，跨 Owner 即使 SHA-256 相同也不可读取；
- Egress 保持精确内部目标；外部 OCR/模型仍需单独确认且本工单不启用；
- 来源内容继续经过不可信数据上下文门，不能调用 `freeze_coverage` 改写用户目标。

完成证据：进程恢复、重复请求、运行中取消、跨用户访问、过期 Grant、提示注入和资源清理
回归；安全与所有权必须 100%，不能以加权分抵消。

### DR-06：工作台进度、解释与可恢复 UX

目标：用户能看到 Pi 如何理解覆盖范围、当前真实位置以及为什么继续、停止或失败，但不展示
隐藏思维链。

Red：

- 后端 `observe` 事件在前端时间线中显示 `0/8 尚未开始`；
- 用户不知道任务会找一个还是全部；
- 刷新/断线后发现和精读进度丢失；
- OCR 失败只显示笼统错误或没有真实操作。

Green：

- 后端和前端统一事件映射：目标理解、来源识别、候选发现、证据精读、Agent 处理、覆盖验证；
- 高置信度任务显示冻结摘要但不增加确认点击，实质歧义复用现有单问题交互；
- 显示已发现页/总页、候选精读数、缓存命中和未覆盖数，不展示模型思维链；
- 刷新和 SSE 重连从持久化事件恢复唯一活动阶段；
- 失败显示已完成范围、未覆盖范围和真实可执行的重试/修改目标/停止动作。

完成证据：指定页、首个、全部、低质量失败、取消、刷新、断线重连的 Playwright；普通用户、
管理员和超级管理员权限一致；深浅主题和 axe 门通过。

### DR-07：替换试验路径、真实 109 页验收与收口

目标：在前述工单全部通过后，用新工具循环替代 Pi 运行前整份 OCR，同时保留可回滚机制。

Red：

- `SemanticWorkspaceManager` 仍在 Pi 启动前调用 `PiSourcePreparer.prepare()`；
- 系统 Prompt 仍要求优先读取整份 `.mangrove-ocr.jsonl`；
- 试验测试仍把 `prepare()` 调用顺序当成产品行为；
- 真实任务只证明“跑完”，没有完整性和耗时 A/B。

Green：

- 移除 Pi 主链的强制前置整份 sidecar；保留可复用的页型判断、按页解析、版本化缓存和 Owner
  隔离实现，收进文档检索 Module；
- 用 S1–S5 的 Interface 级测试替换实现耦合测试；只删除本轮产生的孤儿代码，不删除历史
  缓存或用户任务；
- 以新 revision 回放用户 109 页扫描 PDF：指定范围、首个完整对象、散落全部匹配、单页
  发现失败四类任务；原任务和原候选保持不可变；
- 记录墙钟时间、首次可用 Observation、发现页数、高质量 OCR 页数、缓存命中、结果召回、
  EvidenceRef 和 Verifier 结论；不在测量前虚构秒级门槛；
- 运行 Agentic Runtime、工作台、文档解析、权限安全聚焦门，再运行全仓后端、完整 Playwright
  和前端生产构建；
- 回滚方式是关闭 vNext 文档工具并失败关闭或返回仍为默认的 Legacy，不静默恢复昂贵试验路径。

完成证据：真实文件 A/B 报告、命令与日志、自动化通过数、残留资源检查和用户实际操作验收。
工程验证通过不等于用户验收通过。

## 6. 每个工单的共同 TDD 规则

1. 一次只做一个纵向行为：先在确认的 Seam 写一个会失败的测试，再写最小实现使其通过；
2. 不先批量写完所有测试，也不模拟私有函数或断言 OCR Provider 调用顺序；
3. 预期值来自冻结夹具、原文件或人工核验真值，不用实现代码重新计算自己验证自己；
4. 每个工单完成后运行本工单聚焦测试和所有已完成切片回归；
5. 重构只限本轮新增代码造成的明显重复，正式结构审查留到 `code-review` 阶段；
6. 失败不得用问法特判、自动外发、换模型、换 Pi 版本或放宽完整性掩盖；
7. 每个工单展示差异、验证证据和未决问题后再继续；本任务图获实施授权后，可以按用户此前
   “执行完所有子工单再来找我”的指令连续执行，但遇到下列人工控制点必须暂停。

## 7. 人工控制点

必须暂停并取得用户确认：

- 改变业务范围、结果基数、完整性含义、对象边界或默认解释；
- 新增普通用户/管理员权限，扩大来源、目录、网络或宿主机访问；
- 把业务正文发送到新的外部 OCR、模型或其他服务；
- DR-00 失败后改用 MCP、升级/降级 Pi 或更换 Agent 框架；
- 不可逆数据库迁移、物理删除缓存或历史任务；
- 创建/关闭 GitHub Issue、提交、推送、版本、标签、默认入口切换或外部发布。

不需要逐项确认：

- 在已确认 Seam 内添加测试、最小实现和失败关闭；
- 复用现有本地 MinerU/Paddle/pdfplumber/PDFium 与 Pi Extension；
- 修复本任务引入的编译、类型、Lint 和测试问题；
- 更新本任务的执行报告和本地 Markdown 状态。

## 8. 当前阶段完成门

工程完成门结果：

- DR-00–DR-07 每项都有独立价值、Red、Green、证据和阻塞关系；
- 测试 Seam 已按 Interface、Relay、Runtime、真实文件和 UI 分层落地；
- 已验证事实、代码推断和未验证建议分开；
- 没有把 Pi 自主性重新写成关键词或 TaskFamily 路由；
- 没有外发、默认切换、版本、标签或 GitHub 变更；
- Markdown UTF-8、链接和 `git diff --check` 通过。

DR-00–DR-07 已完成工程实现和工程验证。用户下一次只需进行真实工作台操作验收，并决定
是否授权提交/推送；工程通过仍不等于用户验收通过。
