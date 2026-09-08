# #130 有依据的逐步澄清任务单

> 状态：接口与最小实施范围已冻结；实施及真实模型评测证据待验收。
> 权威：[GitHub #130](https://github.com/Eclipseic1848/Mangrove_ai/issues/130)，父票 #113，依赖 #127；承接已合并 #128、#129。
> 基线：`d9e90a06e505b08333db9547e268bbcd1e8b5041`。前端关联设计、实现、验证及独立审查使用 GPT-6-Astra high，执行档位由主会话留证。

## 目标

用户以口语、不同措辞或不完整要求提出同一业务意图时，系统结合获准来源的实际结构与有界样例展示当前理解、具体发现和建议。只有缺失条件会改变正确结果时，才逐次询问一个关键问题；充分信息直接进入现有任务链。用户能纠正理解，原话、已确认否定和范围不因改写或下一轮丢失。

任务对话、澄清与结果查看留在 `/data-prep` 同一任务主列。业务回答、普通追问、修改确认和外发授权是不同命令。澄清成功不代表结果正确；来源核验、独立 QA、Candidate 与正式 Delivery 的现有边界全部保留。

## 允许输入

- 工单 #130 正文，CONTEXT.md 中 RawUserTurn、ContextDelta、CompiledContext、GoalContract、TaskRevision、SourceSnapshot、RuntimeBinding 与 Delivery 的定义；ADR-0027、ADR-0035。
- 当前编译器、SourceInspectionReport/字段绑定、ConversationSteering 五类动作、问题发布与 `/answer`、工作台主列、运行恢复及现有固定评测脚本。预调查用于定位接缝，不作为功能已经实现的证据。
- Owner 有权读取且属于本次冻结来源集合的文件/快照、哈希匹配的 inspection/binding 事实、用户真实原话、已确认上下文与当前修订。客户端提供的字段名、样例、定位或来源身份都须由服务端重新核验。
- 合成六类语料、合成表格/文档、临时数据库、隔离 API 和 Runtime/HTTP 替身。实际模型评测的输入、端点与预算须另按本文执行门冻结。

只在精确允许列表内实施。保护基线清单中的用户 WIP；不修改 CoreMind 专属 checkout、冻结内核制品、既有独立泛化评测集或相邻生命周期整改。

## 要求输出

### 1. 真实发现与有界当前理解

复用现有 inspector 和 binder，在需要语言理解的接缝提供有界、已核身份的来源观察。编译器第一轮如使用字段或样例提出建议，实际请求必须已经包含对应 inspection 事实；不能由文件名、模型自报或空白假计划补造依据。可将现有 inspector 最窄扩展为按可信 artifact IDs 读取，不新增检查服务，不要求所有任务先完成 PDF/OCR 全量解析。

来源归属、TaskRevision、Artifact SHA、检查器版本和实际读取状态共同决定观察身份。来源变化、Owner 不匹配、哈希漂移、解压超限、加密、损坏或不支持时，沿既有失败关闭/明确未观察状态处理；不能回退活动文件、旧报告或另一修订。字段样例仅为已读取的有界样例，不把 sampled_rows/estimated_rows 冒充全量计数。

公开当前理解来自本轮已持久语义与可信观察。模型建议必须与已观察事实分开；没有证据的关联键、字段含义、计算口径和输出粒度只能作为待确认解释，不能成为“发现”。不公开系统 Prompt、宿主路径、原始工具日志、Secret 或任意模型生成元数据。

### 2. 解歧取决于语义，不取决于是否收到文本

修正 `compiler_graph._preserve_clarification_context` 无条件把当前 ambiguity 标记 resolved 的根因。回答“你决定吧”“还是不清楚”或答非所问时，如果不能确定会影响结果的条件，仍保留未解歧义并问一个更具体问题；不得删除新 draft 明确保留的同一歧义。确定性校验应基于结构化计划与解析结果，不新增中文关键词分流表。

真正消除歧义时，仅解决对应问题。原已确认范围、否定条件、字段含义、粒度、来源限制、输出契约、权限和未解问题必须保留；用户明确更正某条件时则用真实新回合解释该项变化，不能让旧值永久覆盖更正。已回答条件不得无因重复询问；未取得新信息时应说明仍缺什么，不通过无限自动重试制造“逐步”。

### 3. 五类 SteeringAction 内完成渐进理解

保留 `answer_only`、`normalized_no_material_change`、`revision_proposal`、`new_task_proposal`、`permission_request`，不增加第六种动作。

- 充分明确的状态/依据追问仍即时回答，不改变 Run；无实质变化的规范化显示“我理解为”并允许纠正。
- `ContextDelta.open_questions` 非空时，持久保存真实 delta 和一个当前问题；使用 `normalized_no_material_change`，`proposal_id=null`，反馈“仍需澄清，尚未形成可执行修改”。此状态不得自动应用业务增量、生成可确认提案或表示已经理解充分。权限/外发缺口仍由明确授权流程处理，不能混入普通业务问答。
- 普通追问产生的澄清关联原始 `origin_turn_id`。回答后，用原 RawUserTurn、原持久 delta、已确认上下文及本轮原话生成新的不可变 delta；`source_turn_ids` 按真实相关回合顺序记录，不能把拼接历史冒充一条用户原话。
- 运行中的 steering 澄清挂在理解投影下，不通过 `_set_needs_input` 暂停原 Run，也不覆盖其真实 Runtime/授权问题。当前 Runtime 问题优先展示；普通进度询问和明确更正入口继续可用。
- 澄清后若形成实质变化，仍生成既有 RevisionProposal，经“立即取消”“当前步骤后切换”或“新任务”确认；明确补充不是自动修改冻结 GoalContract 的授权。

### 4. 不可变问题、原子回答和真实消费

问题轮次由服务端持久生成，不能用文案、前端索引或可能复用的 `question_id` 代替。Pi 同一 Run 连续产生相同 `question_id` 时，两个 round_id 必须不同；刷新/重连看到同一未完成问题时 round_id 不变。

问题发布与其白名单历史快照同事务持久。回答必须在同一个事务中校验 Owner、活动修订、当前问题轮次、任务状态及取消代际，保存不可变 RawUserTurn、幂等回答收据与稳定事件，再推进允许的状态；不能串联三个独立 Repository 事务假装原子。原事件/问题 JSON 与回合表在同一数据库，可优先复用最小事务接缝；只有现场证明必需时才新增最窄显式迁移，不能挪用用户正文、Grant 或伪造 ContextDelta 存控制状态。

同幂等键、同规范请求重放返回原收据；换正文、轮次、修订或原回合身份返回 409。不同键竞争同一问题最多一个接收成功。失效修订/轮次、已取消或不支持消费的请求在模型/恢复调用前拒绝；失败不覆盖后来问题，不悄悄改投新一轮。

收到回答与执行已经消费回答是两件事。网络/Runtime 调用前持久单次发送占位；已完成时重放既有结果，有占位无确定完成结果时保持 unknown，不自动重发，不依赖占位到期或后台清理恢复发送资格。崩溃点、并发与进程重建都必须验证。显式可安全恢复的 Runtime 只按其真实持久消费协议恢复，不能把重复发送当作恢复。

### 5. 各 Runtime 的真实继续方式

| 路径 | 本票要求 | 不可声称 |
| --- | --- | --- |
| 运行前/Legacy 编译与字段绑定 | 回答通过现有 ClarificationResolution/绑定选择进入下一次实际编译或绑定；保持原 task；充分后进入原任务链 | 只更新 question.answer 或任务摘要就等于消费 |
| 已冻结原生 Pi / CoreMind 的真实业务补充 | 统一显式确认“按补充要求重新开始”，在同 task 创建新 revision、新 Run；新 start 实际消费确认后的合同 | 原 Run 恢复已消费追加回答、仅 start/resume 次数正确就证明新要求生效 |
| 固定 CoreMind 无真实 clarification 的 paused | 投影控制等待；用户仍可主动更正并进入同 task 新修订确认 | 普通 paused 必定缺业务条件、伪造通用业务问题 |

已核固定 CoreMind SDK 虽有 `resume_run(run_id, input=...)`，worker 会将其与原 initialPrompt/input receipt 比较，不同输入报 `resume_input_mismatch`；这是恢复一致性检查，不是追加回答协议。暂停控制协议也不能用“恢复后立即 steer”拼成可靠的持久回答消费链。不得修改冻结 worker、伪造 capability 或静默换回 Pi。

已冻结 Pi 家族的补充统一通过已有修改草案与确认路径生成新修订，本票不新增原生 Pi 单 Run 回答消费协议。确认前展示真实补充和将重新开始的事实；无真实问题时不合成通用“请补充范围”诱导回答。旧 Run 确认静止后，结合 cancel_generation/活动 revision CAS 冻结新修订并启动；清理仅达到 cancelling 时不得启动。连接、精确 model、Adapter/内核/协议与能力绑定漂移失败关闭，不按最新全局设置替代。新旧 Session、取消、历史与使用量仍按实际 Run 区分。

新的 typed 业务合同必须与确认的补充一致。仅映射现有可验证的 typed 字段，保留现有输出格式处理；`coverage_delta/selection_delta/field_semantics_delta` 等任意字典不能直接铺入 GoalContract。否定、数量等必须实际进入对应冻结字段；无法表达、未知键、无支持的新来源或字段含义继续澄清或明确拒绝，不能只写 normalized_text 掩盖旧 contract 冲突，也不新建通用动态 patch/合同框架。未改变的来源快照及业务限制保持。

复用现有 revision transaction hook，把提案/决策到唯一目标 revision 的关联与创建原子持久；建版本后尚未更新决策就崩溃，重放也只能返回同一版。已创建未 enqueue 依持久 queued 状态恢复，不能再创建下一版；原 Runtime 的 unknown/start 幂等门保持。新外发许可缺失应在停止旧 Run 前拒绝。
等待切换的最小恢复例外：已经选择 after_safe_point、决策仍为 WAITING_SAFE_POINT，随后同一基线 revision 进入 needs_input、paused、completed、candidate_ready、failed 或 cancelled，没有正在继续的原子步骤时，用户可显式改选 cancel_now 或 new_task。原选择、新选择及各自外发确认值须与决策 CAS 同事务留审计；事务核 Owner、同 revision、上述状态及取消代际，比较完整原决策 payload，最多一个选择生效。READY_TO_APPLY、NEW_TASK_REQUIRED、APPLIED 及其它状态不能借此换模式；迟到 worker 的 mark_safe_point 同样比较完整旧 payload，CAS 失败不得覆盖新选择。此例外不自动取消、不自动改选，也不将 needs_input/paused 当作 Runtime 已清理：实际创建新 revision 前仍必须确认旧 Run 已停止。已 APPLIED 的同请求优先返回原唯一目标，不能被当前状态门挡住。尚未存在等待决策、已无步骤的新 after_safe_point 请求在保存决策前拒绝，并提示明确选择另外两种方式。

### 6. 公开协议

本节已与后端、前端冻结；不得各自推断字段。所有新增输入对象拒绝未知字段，身份均由服务端核验，公开投影不含内部发送占位或原始检查报告。

问题保留现有 `kind/question_id/prompt/reason/affected_scope/options/allow_free_text`，新增：

```text
round_id: string                  # clarification_ + UUID hex，服务端一次持久生成
revision: integer >= 1
purpose: business | authorization | control
continuation: resume | steering | confirm_revision | unavailable
origin_turn_id: string | null      # steering 必须指向真实原回合；其他按真实来源填写
outbound_purpose: string | null    # 原 external purpose 用途说明迁至此，保留真实原文
```

`purpose` 决定是否普通业务交互；前端不能从 `kind=harness`、文字“同意”或 continuation 猜授权。旧 external 问题的 `purpose` 曾用于用途说明，新分类字段不得丢掉该说明：新发布、兼容回填和公共投影统一迁至 `outbound_purpose`，原外发 Dialog 的“用途”读取该字段，不能展示英文分类枚举代替用途。`continuation` 由服务端固定处理，客户端不传、不覆盖。无可消费协议时为 unavailable，不提供普通回答执行按钮；主动更正仍走 `/turns`。

```text
POST /api/semantic-workspace/tasks/{task_id}/answer
Idempotency-Key: string (1..200)
{
  answer: string (trim 后非空，最多 10000 字符),
  expected_revision: integer >= 1,
  question_round_id: string
}
```

所有新发布轮次（含 external）都要求这三个字段与幂等键；旧无 round 的问题须先经明确幂等升级写接缝补身份，再读取详情。旧 only-answer 请求不得静默选当前问题。external 的专门 confirm/local/cancel 值与免费取消控制保持，身份字段只用于防串轮。

答案继续遵守该轮 options/free-text/cancel-only 约束。服务端按持久轮次路由 Legacy resume、steering 或确认草案，不让答案自行选择命令；authorization/control 保持专门许可/恢复语义，不套用新业务执行方式。返回原 task 对象并附本次 `answer_receipt`：

```text
{round_id: string, revision: integer, turn_id: string, status: accepted | unknown}
```

accepted 仅表示回答已持久接收，不代表模型已消费或问题已解决。真实下一状态、提案及新问题从权威 task/steering 投影读取。unknown 不自动重发；客户端保留原稿与原幂等键并显示真实结果未知。

任务公开 `clarification_history`，按持久事件顺序返回：

```text
{
  round_id, revision,
  question: 当轮完整白名单问题快照,
  answer: string | null,
  turn_id: string | null,
  asked_at: ISO8601 | null,
  answered_at: ISO8601 | null
}[]
```

待答轮次也可出现；不得从摘要复原未保存的旧用户原话。兼容入口固定为 GET task detail 在公共投影前调用 `store.ensure_current_question_identity`：仅对当前活动 needs_input、真实已有且缺 round 的问题执行一次 BEGIN IMMEDIATE/CAS，回填 question_json 并保存身份快照。两个并发 GET 获得同一 round，后续只读；不接收回答、enqueue、外发、改变目标或执行状态。该元数据回填验证 Owner，不要求任务执行额度而阻塞已暂停账号读取。CoreMind 无真实 clarification 的旧泛化暂停先归为 control/unavailable，不能借回填改造成业务问题。旧 only-answer 请求拒绝后提示刷新，详情读取即提供真实新身份；不得客户端代填当前轮次。

asked_at 优先已有匹配 question_required 的真实时间，无可靠历史时间则 null，不能把兼容升级时间冒充提问时间。历史 revision 不回填、不改写；历史按所选 revision 读取，不能把 V1 问题提交给 V2。

`task.understanding` 由后端投影，含 `revision`、`summary`、`status: ready|needs_clarification|unavailable`、`findings` 和可空 `question`。steering 的当前问题同时随持久 `SteeringResult.clarification` 投影，并与 task 中对应 round 一致。没有可信理解时为 null 或 unavailable，不拿任务执行 summary 伪装业务理解。

`findings` 最多 20 条，精确白名单为：

```text
SourceFinding {
  artifact_id: string,
  source_sha256: 64 位小写十六进制,
  inspection_id: string,
  inspection_sha256: 64 位小写十六进制,
  inspector_version: string,
  status: ready | unsupported | corrupt | encrypted | over_limit | needs_user,
  summary: string (最多 500 字符),
  table_ref?: string,
  element_id?: string
}
```

source_sha256 必须等于冻结原件哈希，inspection_sha256 来自真实报告 canonical_hash；每条至多提供一个确有事实的 table_ref 或 element_id，不公开 physical_ref/metadata。summary 由报告确定性构造，表格最多取 12 个列名、每列最多 3 个真实 sample_values、每值最多 120 字符，再受 summary 总长度约束；截取样例不冒充全量。非 ready 项只说明报告实际不可读原因，不带表/元素定位或假发现。没有进行观察时 findings 为空，不伪造 inspection 身份或声称有来源发现；只有本轮理解依赖尚未观察事实时才 unavailable/needs_clarification 并说明缺口。状态追问或仅凭充分原话即可形成的理解可以 ready 且 findings=[]，不强制所有请求解析文件。解析器成功也不替代业务含义确认。

### 7. 主列交互和 #129 保护

普通业务问题与“当前理解”、真实发现放入主列，复用现有 textarea。一次只突出一个关键问题，选项可折行且自由补充仅在本轮允许时启用。明确输入用途为“回答这项问题”或“继续对话”，用户主动切换；不把既有普通草稿静默转交 `/answer`，不另造第二个文本输入或强制问卷。

external 外发、提案三决策、发布与删除沿既有专门确认，不能由普通业务答案授权。当前 Runtime 问题与 steering 问题同时存在时不互相覆盖；正文不能猜用途。许可等待仍可关闭/重新打开和取消，业务问题不再强制弹窗。

提交捕获 Owner/task/revision/round/origin/用途/原稿/幂等键。迟到结果只作用原上下文；不得关闭新问题、清新草稿、移除新结果引用或修改新任务 busy。失败保留原稿；新轮次、新稿使用新键，原提交重放复用原键。刷新从持久状态恢复真实问题与已答历史，不乐观伪造成功。

#129 的普通 `result_context` 四项身份、正式 output 门、来源 refs、网络前原话持久、单次 CAS 和 unknown 均须保留。普通引用追问不能因出现业务问题自动变回答；引用草稿留在其原用途。若 steering 续答关联原来的结果引用，服务端重核 Owner/活动 revision/正式输出/表示与真实引用，在新 answer RawUserTurn 上独立持久与占位；不可复用原 turn 已消耗的发送许可，不可借 `/answer` 绕过历史/候选/压缩限额。模型输入可使用原回合已核的有界 context，不能改写新用户原话或伪造新引用。外发确认、精确连接版本、计量 unknown 和公开回答白名单不放宽。

键盘沿用 Enter 发送、Shift+Enter 换行、IME 防误发；明确 label、错误关联与克制的 live 提示。长选项和短屏操作均可滚动到达。用户上翻或正在输入时不抢焦点/滚动；用户主动回答才聚焦。保持明暗主题对比度、既有“回最新”、来源/结果侧栏和真实任务动作。

## 验收

### A. 合同、状态和安全回归

1. 任意/无关回答不能强制 resolved；明确回答确实解决对应歧义；下一轮保留已确认否定/范围且允许真实更正。检查生成器实际入参，不能只看模拟输出状态。
2. 表格与文档各有真实合成 inspection 进入理解请求；错误 Owner、修订/哈希/版本漂移、不支持/超限资料不生成假发现。抽样不显示为全量统计。
3. open_questions 阻止业务自动应用与可确认提案；续答引用真实原回合+新回合，五类动作保持；普通状态询问不暂停 Run，权限请求不能变业务答案。
4. 同 question_id 两轮、并发两键、同键换稿、旧 revision、旧轮次、取消竞态、发布/接收中断、接收后发送前中断、发送后结果未知及进程重建均有真实事务回归。数据库原话/事件/状态一致，无重复发送。另核 WAITING_SAFE_POINT 后进入 needs_input 的两种显式改选、原新选择同事务审计、取消代际或决策 CAS 竞争、迟到 worker 不覆盖，以及 READY/APPLIED 不能改选。
5. Legacy 测试捕获回答实际进入编译/绑定；原生 Pi/CoreMind 测试捕获确认目标实际进入新 start，旧 resume 没有吞答案，普通 paused 不造问题。未确认零新 Run，确认后同 task 新 revision；旧 Run 未静止、再次取消代际、连接/Adapter 漂移均零新 Run。网页 typed 合同的否定/数量真实生效或有据拒绝；决策提交中断只认一个目标 revision。不得用空 resume 替身绿色代替消费证据。
6. #129 真实引用续问、origin 关联、两 Owner/两 revision、Candidate 禁止、超限解析前拒绝、CAS、SDK/Broker 重试与 unknown 回归保留；澄清历史和公开发现不泄漏内部数据。
7. 浏览器覆盖同主列完整旅程、明确用途切换、两轮相同 question_id、旧响应不清新稿、刷新历史、授权独立、取消/409/unknown、IME/换行、短屏长选项、明暗 axe、焦点与上翻滚动。完整后端合同驱动前端 fixture；fixture 通过只证明交互协议。

### B. 固定语义评测

新增本票合成语料，不修改既有独立泛化集，不按模型输出反向改预期。至少六组、每组三个案例，共 18 个案例；多轮更正组每例三轮，其他单轮，一次冻结运行共 24 次语言调用。固定原话、来源结构/有界样例、前置已确认事实和每轮预期约束。不同措辞至少共享三个相同意图族，并比较规范业务语义，不要求回答逐字相同。

| 组别 | 合成输入例及冻结预期 |
| --- | --- |
| 口语 | 对合成订单表说“把作废的去掉，按客户算实收”以及两条不同业务口语；明确状态筛选、分组粒度与金额列，有事实不问已知字段 |
| 同义改写 | 将上述三个意图分别正式改写；范围、否定、分组/输出含义与口语组等价，不能因措辞换任务模板 |
| 缺关键条件 | 两个金额字段都可能是“销售额”、两个日期字段都可能是统计期间、合成文档两种同名范围；每次问一个会改变结果的条件，不自选业务含义 |
| 含糊请求 | “帮我看看”表格/文档/多来源；先陈述实际结构或明确未读取，给有依据的方向并询问关键目标，不捏造完成任务 |
| 否定条件 | “别算退款”“不要跨文件合并”“不要姓名，只要编号”；各轮保留明确否定，不能在规范化中丢失或反向执行 |
| 多轮更正 | 三条三轮对话，分别更正金额口径、时间范围、输出粒度；不反复问已明确条件，不丢原否定，改变冻结业务语义仍进入确认门 |

这些是语料构造约束，不是产品关键词表。实施时把六组补足为可读取 fixture，并在运行前冻结内容哈希、每例期望、不允许新增的条件与等价组。结果至少逐例报告实际 delta/计划关键语义、问题数与问题相关性、未知条件/错误假设、是否正确触发差异门及明确 pass/fail 原因。

复用/最窄扩展 `scripts/evaluate_conversation_steering.py` 或其已有调用接缝，提供可执行的明确模式；现脚本只检查意图/action、部分关键词且默认配置模型，不能原样当本票语义验收。真实评测须调用实际理解实现，使用固定多轮上下文和真实合成结构事实；mock 只验证入参、守卫、持久与重放，不能获得“模型理解达标”结论。结构正确但提出无关问题、私设计算条件或漏否定仍失败。

全部冻结案例通过且无权限/范围/来源臆造，才可对本次固定模型/版本/语料声明通过。失败原样报告并修正实因；不能删除失败案例、降低标准或用重跑最好一次替代完整结果。意图等价仍不证明生成结果 QA、真实业务验收或其他模型能力。

### C. 真实模型调用执行门

本任务单只授权准备语料与可运行评测，不包含现在调用真实模型。执行前形成可审阅清单：合成 fixture/hash、准确模型与端点、Owner、现有连接 ID/版本或明确的本地模型配置、实际 API 路线、最大调用数/输入与输出界限、重试设置以及停止条件。不得选择“当前默认”或扫描并自动尝试可用供应商，不在命令行/报告中输出凭据。

2026-09-08用户将固定2048输出上限变更为默认采用模型最大支持能力。上下文窗口、回答输出与思考消耗必须分开：DeepSeek V4 Pro/Flash的官方集成值为上下文1000000、输出384000；百炼Qwen3.8系列为上下文1000000、回答输出131072。证据：[DeepSeek官方集成规则](https://github.com/deepseek-ai/awesome-deepseek-agent/blob/main/CONTRIBUTING.md)、[百炼Max](https://help.aliyun.com/en/model-studio/qwen3-8-max)、[Flash](https://help.aliyun.com/en/model-studio/qwen3-8-flash)、[27B](https://help.aliyun.com/en/model-studio/qwen3-8-27b)。两条澄清转写路径在已核OpenAI Chat协议使用同一模型目录上限；Responses等协议的总输出可能包含思维链，不能套用Chat回答上限，未核其总额时省略可选额度字段并明确只是服务端默认，不能声称已证实最大。本地/自定义模型未公开输出上限时，兼容协议不发送臆造的max_tokens，由部署端校验，不声称已知最大值；必须填写额度的未知Anthropic模型保留兼容值，不能宣称该值是最大。输入与输出合计仍受服务端真实上下文限制，超限必须报错，不能截断原话后继续。

冻结真实评测仍为一次完整18例/24次调用、并发1。单次总时限通过显式--timeout-seconds冻结（默认90秒，允许10到600秒）；针对已有90秒正文等待超时，新独立验收采用300秒，客户端与隔离进程的既有编译时限同步，退出后恢复，不修改生产配置。HTTP200响应头不等于完整答案，报告分别保留响应头与正文完成时间，正文未知仍立即停止本轮且不重发。该300秒验收不冒充生产默认120秒的时延资格。合成清单的序列化输入最多64KiB作为本次允许外发的数据边界，不代表模型token窗口；用户/来源身份、限定资料、Secret隔离均不放宽。每次真实发送核精确端点、模型与目录输出上限，报告记录实际额度策略。禁用SDK/框架隐式重试及自动修复额外调用；未知或不可用输出保留失败并停止，不覆盖或拼装多轮成功。费用使用实际可得计量，未知不填零。

模型选择与本次资源/外发授权由主会话核验用户已有授权；授权确已覆盖时不重复请求，未覆盖则先提交上述具体清单。只允许清单中合成内容，不读取真实用户任务、私有样例或生产库。真实推理评测不授权执行模型建议的业务工具、开服务、生产迁移或外部发布。

若本轮尚未运行真实模型，交付必须写“合同链已验证，六类语言能力待真实评测”，不能以 mock 替代 #130 语言验收。实施先最窄红绿，再受影响兄弟集/build、独立 Standards 与 Spec、最后同提交 Linux CI；各层证据分别留存，不把本机测试、运行中命令或旧 SHA CI 当最终门。

## 边界与交付

不新建服务、第二套对话主链、通用问卷引擎、任务关键词穷举或第六类 SteeringAction；不扩大来源、权限、外发、不可逆操作、正式交付或模型默认选择。不安装依赖，不升级/修改冻结 CoreMind，不把新 revision 称为原 Run resume。#131/#132 等后续产品范围保留。

前后端分别提交实现说明、精确改动清单和可定位红绿证据；正式任务单只维护本票合同与验收，不代替滚动状态台账。新迁移如经现场证明确需，只可在临时库验证，生产迁移仍是独立操作。Git/远端、发布与用户业务验收由主会话按已有授权处理，本规格不扩张授权。
