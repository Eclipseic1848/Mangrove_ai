# 工作台示例泛化评测与扩展

## 范围与证据

评测种子来自 `frontend/src/components/workspace/WorkspaceExamples.tsx`。示例有四大类、八个入口，
不是八个互斥的运行时 TaskType。采集、文件处理、调度和通知可以组合。

| 示例 | 可参数化内容 | 必须分开验证的阶段 |
|---|---|---|
| 用户口碑 | 主题、来源、数量、评分缺失、重复 | 来源获取、筛选、分析结论 |
| 站内检索 | 域名、主题、数量、时间范围 | 站点边界、内容体裁、处理产物 |
| 指定网页 | URL、正文范围、日期、输出格式 | 实际正文、首页导航与文章的区别 |
| 新闻汇总 | 主题、新闻条数、发布时间 | 新闻与榜单的区别、时效、摘要 |
| 报销单提取 | 单据范围、文件格式、字段、金额 | 文件读取、指定单据、算术与缺失 |
| 订单合并 | 主键、原字段、重复、冲突 | 合并、冲突保留、用户裁决、Excel |
| 周期性标讯 | 周期、时区、时间、生效范围 | 意图识别、调度落库、触发、实际采集 |
| 结果邮件 | 收件人、正文、附件、取消与更正 | 用户授权、正式结果范围、传输、重复发送 |

`scripts/workspace_example_cases.py` 生成 360 个不同的输入：每个示例的正常、边界、缺失、额外字段、
类型错误、约束、组合和对抗各 5 个；前四类再各加 10 个专项用例，分别检查口碑分句、URL 主机边界、
HTML 正文与导航分离、新闻体裁和跨时区日期。前四类各 50 个，后四类各 40 个。
合成来源和独立答案一起保存在评测目录，但只把来源文件
挂入 Pi 容器。答案不得通过提示词、能力包、期望哈希或挂载目录交给执行器。

结果分层记录：

- `not_run`：未执行；不能计作通过。
- `inflight_unknown` / `unknown_transport` / `unknown_timeout`：结果尚未确定；保留身份，不自动重发。
- `structured_passed`：产物字段、值、类型、格式通过独立核对。
- `needs_review`：结构正确，但报告正文尚无语义复核，不能计作整体成功。
- 初稿、系统核验、用户接受和正式发布是不同阶段。初稿通过本评测不产生正式 `output_id`。

采集类每类 50 个合成用例使用冻结快照，证明取得来源之后的能力；不能据此声称能实时采集任意站点。
自然语言定时/邮件初稿与隔离执行器测试也不能拼接冒充同一次端到端完成。

## 运行

使用项目已有 Python 环境与依赖。凭据 JSON 只保存本机，字段为 `username`、`password`，不要入库。
`<account.json>` 是操作者自己的既有测试账号文件。真实模式会调用该账号默认模型并启动 Pi 容器。

```powershell
# 只生成来源、答案及未运行报告；不调用模型。
py -3.13 -X utf8 -m scripts.evaluate_workspace_examples --output logs/workspace-eval

# 一键执行全量并持续生成 report.json / report.md。
py -3.13 -X utf8 -m scripts.evaluate_workspace_examples --output logs/workspace-eval --live --account-file <account.json>

# 独立模型提示复核已经生成的分析报告；仍需区分模型判断与人工金标准。
py -3.13 -X utf8 -m scripts.judge_workspace_reports --run logs/workspace-eval --account-file <account.json>

# 按冻结用例和原产物哈希重新判分，合并正文复核结果；不会再次调用模型。
py -3.13 -X utf8 -m scripts.evaluate_workspace_examples --output logs/workspace-final --merge-run logs/workspace-eval

# 真实 HTTP 入口。定时示例若创建测试计划，脚本立即暂停；邮件示例先询问地址。
py -3.13 -X utf8 -m scripts.evaluate_workbench_flows --output logs/workbench-flows --account-file <account.json>

# 本地回归：真实本机 TLS SMTP、隔离调度库、判分器反例与来源边界。
py -3.13 -X utf8 -m pytest tests/test_workspace_example_evaluation.py tests/test_workspace_example_delivery.py tests/test_scheduler_cron_generalization.py tests/test_search_source_scope.py -q
```

可用 `--case 'receipt/*'` 筛选 Pi 用例，HTTP 脚本用 `--case receipt`。
已有完成记录不会重跑；用例内容变化时须使用新目录。原失败和未知证据保留，不通过反复运行覆盖失败。
报告语义复核按产物哈希绑定；模型没有确定回复时维持未知，不推测通过。
生成模式正常退出码为 0；执行/汇总模式有失败返回 1，只有未运行、未知或待复核返回 2，全量通过才返回 0。
多个批次可重复传入 `--merge-run`。同一个完整用例若出现多次执行则拒绝汇总，不挑最好的一次。
用例发生变化时只使用完全相同的新版本记录；旧执行仍保留为历史证据。失败运行、未知运行和缺产物
不能通过残留的正确文件变成成功；产物集合或哈希发生变化时停止汇总。

金额 CSV 的 `12` 与 `12.00` 按有限 Decimal 等价；cron 按时间集合等价，但必须保留接口约定的 `cron@`
前缀。口碑专项未限制优缺点的容器形式，因此单分句字符串与单元素列表等价，可去掉转折连词；错误事实、
额外分句和缺字段不能据此通过。订单待决清单的空白主键允许显式 `null` 或空字符串，缺字段或补造编号
仍失败；原需求未限定空值表示。其他字段仍严格检查类型、内容和字段集合。

邮件合成需求遇到无效地址只要求询问，未规定必须清空地址。仅在 `ask_recipient` 下，整段合成来源明确
给出无效地址且禁止猜测时，保留原串或单元素列表与空列表等价；缩短、补全、额外地址、直接确认发送、
缺字段和错误布尔值仍不通过。该规则仅用于评测，冻结语料及原始结果不改写，新旧批次统一重判。

正文逐份核对三点：事实受完整来源支持、覆盖选中内容、结论不越过样本范围。将“已排除”写成“未提供”、
虚构处理步骤或样本、没有量表却解释评分等级都判失败。仅输出 JSON 正确不代表报告正确。
人工或 Agent 复核须写明身份和范围；本轮 Codex 来源对照不是用户业务验收，也不是独立真人金标准。

## 当前抽象及新增类型的方法

工作台使用 `PiRuntimeRequest`、冻结来源、`TaskRevision`、任务上下文和 `AgentKernel` Adapter 合同。
旧 `SemanticTaskPlan.TaskFamily` 只覆盖旧 Harness，不能把新增枚举当作新工作台能力已经接通。
当前没有统一的“工作台 TaskType + Schema + Executor”注册表；注册点按职责分布。

1. **已有能力的新业务变体**：优先只增加示例/任务参数和独立答案。报销改为采购、汽车改为家电，
   不应新增执行器或按品牌名称判断。先验证现有任务上下文、来源范围和产物能表达需求。
2. **新采集通道**：实现 `BaseCollector.matches/is_available/collect`，通过 `collectors.registry.register`
   注册。参数进 `TaskSpec` 的共享转换/校验，补来源限定、空结果、超时、重复和错误体裁测试。
   后端降级必须保留域名约束；不能用站外数据补齐站内数量。
3. **新文件/工具能力**：接入现有文档检索或能力目录/宿主接缝，贯通输入格式、来源授权、调用 Schema、
   执行、错误分类和产物验证。新工具不是仅加一个 UI 名称；额外依赖或权限必须另行评估。
4. **新 Runtime**：实现既有 `AgentKernelRuntimeAdapter`，提供能力 manifest、固定制品身份以及
   `start/resume/cancel`，复用冻结模型连接、来源和候选验证服务。
5. **新增评测示例**：更新生成器的示例映射和独立 oracle，保证至少 30～50 个不同输入，给判分器加入
   “貌似正确但其实错误”的反例；再跑真实入口、真实执行和原有回归。不要仅校验 Schema 能否构造。

扩展成本取决于新增的是业务参数、来源通道、工具还是 Runtime，不能统一承诺“注册 Type + Executor 即可”。
邮件和定时属于交付/执行约束，宜沿用现有通知与调度服务，避免再造一套业务类型分派。

## 来源规划与质检契约

显式 URL 任务须在 `TaskSpec.urls` 保留原目标，不能通过清空专用场景却留下错误平台来绕过来源校验。
`evidence_collection` 和 `bank_benefits` 是关键词发现流程，不能替代显式 URL 的定向读取。
规划最多纠正一次；纠正调用失败、仍冲突或存在排除/示例链接歧义时停止，不默默删掉约束。
字面 URL 保全是保守检查，不是完整的自然语言授权解析器；涉及指代、否定或复杂混合来源时仍需澄清。

Conductor 的 Checker 接收冻结 URL、站点、时间范围、原件和报告。模型须返回每个 `source_id` 的
`usable` 布尔值及非空 `reason`。来源 ID 缺失、重复、非法，没有可用来源，越过指定站点，报告/原件
超过核对上下文限额，以及核对服务不可用，均不能判通过。是否属于目标正文、日期是否满足任务、结论
是否有依据仍需语义核对；这些不是仅靠字段校验即可证明的能力。增加来源记录字段时，应保留日期与原文，
不要只传标题或搜索摘要供评分。空数据不能通过跳过分析绕过失败状态。

```powershell
py -3.13 -X utf8 -m pytest tests/test_planner_source_routing.py tests/test_checker_source_evidence.py tests/test_collection_quality_gate.py -q
```

Pi 的初稿写作自查与用户批准后的独立验证是两个阶段。明确字段、字面前缀和原文支持应在写稿时自查，
但提示要求不构成确定性 Schema 保证；仍须用独立 oracle 检查实际文件，不能因出现 `draft.ready` 计作通过。

## 盲点

### 衍生输出检查与预算回归

JSON 初稿运行可从冻结用户原话提取 JSON Schema 检查项；CSV/XLSX 新初稿可提取表头要求，沿用当前冻结模型连接。
检查项保存在宿主 Run 状态，不修改 TaskRevision，不是用户已确认的新业务契约。
只使用明确原话支持的字段、类型、数量与字面前缀；本地检查差异通过现有初稿提示和接受 gaps 展示，
用户仍可接受或继续。提取失败、响应截断或不完整占位只提示未完成，未知请求不自动重发。
旧会话没有派生记录时不补发提取请求。CSV/XLSX 复用 `TableOutputContract` 校验列名和现有表头读取器，
但派生检查不写入正式表格契约。只检查明确的必需列；仅用户明确排他或排序时才检查多列或列顺序。
允许附加列时不隐含要求表头唯一。多工作表、单元格类型、公式、业务值与行数不在此表头检查范围；
不宣称已经自动解释所有格式。读取失败仅提示未核对，用户接受后保留原文件及提示。

```powershell
py -3.13 -X utf8 -m pytest tests/test_output_requirements.py tests/test_conductor_total_budget.py tests/test_draft_snapshot.py -q
py -3.13 -X utf8 -m scripts.evaluate_output_requirements --account-file <账号文件> --output <新证据目录>
py -3.13 -X utf8 -m scripts.evaluate_output_requirements --account-file <账号文件> --output <新证据目录> --suite structure --case '*'
py -3.13 -X utf8 -m scripts.evaluate_output_requirements --account-file <账号文件> --output <另一个新证据目录> --suite regression --case '*'
```

`structure` 生成明确前缀、紧凑格式前缀、条件格式前缀、CSV、XLSX 五组各 40 个需求，覆盖正常、边界、缺失、多余、
类型、约束、组合与对抗变体。用例和答案预存本地清单；用于判分的 JSON/CSV/XLSX 文件在模型回执后生成，
不发送标准答案或候选文件。表格正例包括
未指定顺序时换序、允许附加列时增加/重复列；反例包含缺列、明确不允许的多列和换序。
新增变体同时给出 `valid_outputs` 和 `mutations`，按原始需求写答案，不能从模型 Schema 反推预期。
`regression` 保留七个旧调度前缀漏检和两个来源/否定前缀对照，供后续提示变更复验。

JSON 提取在同一次模型请求中单列 `prefixes`（字段路径、字面前缀、原话）；宿主只向已声明字符串
节点合成安全 `pattern`。可空类型保持原状，不新增字段或覆盖冲突规则；非法 Schema 不修猜。
冻结记录继续保存原有 Schema 格式，旧记录不补发。前缀后的位数、复杂语法和条件语义仍须另行核对。

提取提示不要求模型直接生成 `pattern`、`format` 或其他接收端不支持的关键字。日期等复杂格式
由模型保留明确的字段名和类型，格式含义仍待核对。若仍返回非法 Schema，沿用原规则拒绝并提示未核对。
新失败记录区分 `error_stage=inference/validation`；只有本地固定拒绝文案才进入
`validation_reason`，第三方异常仍仅保存类型，不保存可能含业务原文或凭据的异常正文。
旧冻结记录不补写；该诊断不触发重试，也不改变用户接受初稿或继续核对的决定权。

提取评测的 `oracle_compatible` 仅证明规则没有拒绝正确答案，不证明规则完整，也不等于 Agent 完成任务。
`mutations` 单列识别与漏检；未知提取不能算检测到反例。脚本对误报、漏报、空规则或未核对返回非零，
已存在的输出目录拒绝重跑。新增提示试验须保留独立批次；覆盖率没有改善时撤回试验，不能挑最好结果覆盖旧失败。
评测另保存 `inference.json`、运行身份、连接版本、耗时和错误类型，便于区分 Provider 截断、未知回执与
Schema 不合规；这些包含需求原文的本地证据不进入版本控制。每份 JSON 只生成一个检查项，条件要求
不能丢弃前提后变成无条件限制。已配置的 DeepSeek 辅助提取使用非思考 JSON 请求，其余 Provider 与
主任务写作、语义核验沿用原设置；失败仍提示未完成，不放宽 Schema 校验，也不重发未知请求。
完整执行仍使用 `evaluate_workspace_examples` 与独立答案；`--local-auth` 只在主入口未运行时验证本机账号并调用隔离 Runtime，
不能称为 HTTP 或用户页面验收。报告语义复核可使用 `judge_workspace_reports --local-auth`，失败记录不得覆盖。

Conductor 顶层 `time_budget_seconds` 表示明确要求的总耗时；未设置时序列化不添加该键，保持旧进度摘要兼容。
规划返回后计入入口以来耗时，后续节点使用剩余时间。超时保留此前节点及已完成采集器返回的数据，
恢复沿用原起点；取消在途请求不保证 Provider 已停止，也不自动重发。自然语言时限在规划返回前尚未识别，
不能把此机制描述为任意请求都具有硬实时中断保证。

### 定时回执与候选原文核对

Conductor 定时回执通过 `normalize_schedule` 复用原调度解析器：cron 统一加 `cron@` 并整理空白，
once 保留显式时区，every 保留相同秒数；无效计划要求澄清。此规范化不改写 Pi 生成的任意 JSON，
不能用调度回归成绩替代 Agent 对用户所要求前缀的实际遵循率。

用户选择继续后的候选语义核验，在原有 20 项、单项 4,000 字符、总计 16,000 字符预算内，
补充全部冻结来源的可完整读取文本，核对 SHA256；任一来源缺失、变化、超限或不能证明文本完整，
均保留部分材料语义。文本、CSV、JSON 与网页正文可进入此上下文；PDF、DOCX、XLSX 解析不保证
覆盖图片、页眉、批注或公式，不能标为完整原文。完整冻结文本也不代表互联网检索覆盖完整。
引用只能证明引文内容，不能证明原文没有其他字段或记录；未选中不等于未提供，未知评分尺度不能猜测。
这属于系统核验的问题识别，不改变用户直接接受初稿的决定权。

XLSX 核对另提供有界单元格视图：列出整个工作簿的工作表名、实际读取的工作表/行号、JSON 基本值类型和
公式原文；不执行公式。每张表只有读完实际单元格才标记 `CELL_SCAN_COMPLETE=true`，可用于核对该表
的行数、空值、类型和值。扫描受 20 个工作表、500 行、100 列和既有字符预算约束，截断或特殊公式无法
读取时明确披露。此标记只覆盖单元格，不代表图片、批注、版式或整份文档已核验。来源 SHA256 不符时
不提供该来源的额外单元格视图。DOCX/PDF 仍只提供可核对正文及缺口，不冒充完整文档解析。
字符串、数字、布尔和空值保留类型；日期等特殊值转成字符串，不保证全部 Excel 类型信息。

```powershell
py -3.13 -X utf8 -m scripts.evaluate_candidate_content --output <新证据目录>
py -3.13 -X utf8 -m scripts.evaluate_candidate_content --live --account-file <账号文件> --output <另一个新证据目录>
py -3.13 -X utf8 -m pytest tests/test_candidate_content_context.py tests/test_candidate_source_context.py tests/test_candidate_verifier.py tests/test_workspace_draft_acceptance.py -q
```

内容评测生成 36 个独立预期：两套列名、CSV/XLSX 正例及行数/空值/类型/值/工作表/公式反例，另含
DOCX/PDF 有界声明和不实完整声明。标准答案不传给核验模型；结果 JSON、摘要、代码摘要和 Markdown
报告留在新目录。未知、不一致或未执行返回非零，不重发未知调用。它验证已有产物的内容检查能力，
不代表 Agent 初稿生成率，也不创建正式 VerificationAttempt。

```powershell
py -3.13 -X utf8 -m pytest tests/test_schedule_normalization.py tests/test_candidate_source_context.py tests/test_candidate_verifier.py -q
```

当前合成文件覆盖 DOCX、文本、Markdown、HTML、数字 PDF、CSV、XLSX；没有证明扫描件 OCR、
复杂合并单元格、大文件容量或所有社会化平台访问。单次真实模型测试不测随机波动，独立提示使用同一模型
仍有相关偏差。新闻日期、网站反爬及正文质量要用实时来源单独评测。历史输入缺失时，不能用猜出的文件
或新增答案把历史失败补成通过。
