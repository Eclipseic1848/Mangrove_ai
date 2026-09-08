# #129 统一来源与结果画布任务单

> 状态：实现完成；独立审查通过，最终同提交 CI 待核验。
> 权威：[GitHub #129](https://github.com/Eclipseic1848/Mangrove_ai/issues/129)，依赖 #127、#119；延续 #128 已完成的公开回答与 Session 边界。
> 本票全部前端关联设计、实现、测试与审查使用 GPT-6-Astra high，实际档位由主会话留证。

## 目标

在现有统一工作台内核对来源、原件、解析表示、结果与交付，不新建第二个工作区。用户能从实际结果项追问并查看本回合所引用的结果/来源，按真实页、行或元素定位；预览、QA、引用和下载始终对应同一任务修订与输出身份。同身份往返保留阅读状态，切身份不带入旧正文或操作状态。

## 允许输入

- GitHub #129 正文、CONTEXT.md 的 TaskRevision、SourceSnapshot、Artifact、ContentUnit、EvidenceRef、Candidate 与 Delivery 语义；ADR-0027/0035。
- #119 身份隔离规格和现有回归、#127 同任务侧栏、#128 不可变回答/冻结连接/计量接缝；已认可的 #124 原型仅供视觉方向，不覆盖真实合同。
- 当前 ResultPreview、SourcePreviewPanel、任务预览/下载路由、上传读取、冻结 inspection/binding、Runtime request、DeliveryManifest、ToolResult/ArtifactRef、结果行 lineage 与文档 item.evidence_refs。
- 已安装的 React PDF、TanStack Table/Virtual、ReactMarkdown、Radix、DuckDB 及当前表格/文档读取实现；仅用合成文件、临时数据库、隔离 API 和受控模型替身验证。

开工先核精确允许列表与调用者，保留用户和其他任务改动。调查结论不是接口已经实现或真实服务已验收的证据。

## 要求输出

### 1. 一个画布，明确内容身份

复用现有 inspector：桌面在对话旁按需展开和专注；窄屏在同一任务内切换并能返回。可切换来源、原件/解析预览和结果，不隐藏待办、停止、候选缺口、重验及发布确认。原件、解析表示、Candidate、正式结果分别标识，显示有事实依据的名称、版本和读取/生成时间；没有事实则明确未提供。

统一呈现现有回答、表格、图表/报告和文件产物；已登记支持格式才提供对应下载，不临时生成新格式。未实现的视觉预览显示真实 MIME/大小与受控下载，不以空白视图表示成功。部分结果与具体缺口同时可见，9/10 必须来自既有覆盖事实，不能按文件数推算；只有正式 Delivery 的已登记 output_id 才作为正式交付。

### 2. 结果预览绑定真实输出与表示

扩展现有 `GET /api/semantic-workspace/tasks/{task_id}/preview`，增加可选 `output_id`，沿用 revision、offset、limit、search、sort_by、sort_direction。默认不传时保留当前首个支持输出的选择规则；前端选定后固定该身份，不随轮询跳换输出。

正式结果响应至少在原内容旁提供：

| 字段 | 语义 |
| --- | --- |
| task_id、revision、run_id、delivery_id、output_id | 服务端验证并回显的真实归属；不能只证明 output 属于同 Owner |
| representation.kind | `output` 表示正在查看登记文件；`derived_result` 表示同次发布的结构化权威结果 |
| representation.sha256、media_type | 当前实际读取表示的哈希与类型，不能填正式 Excel 的哈希代表 Parquet |
| representation.associated_output_id、lineage_available | 与本次选定正式输出的可证明关联，以及有无经核实的逐项来源 |

派生预览只能在 result 的 SHA256 匹配 manifest.provenance.authoritative_result_sha256 时与该交付关联；有 lineage 还须验证同一 attempt 的 ArtifactRef/hash。最新 attempt 不匹配时从已有持久 attempts 查精确输入，找不到则明确无法取得对应派生表示或退到已校验正式文件，不能用最新文件充数。保留已有可靠 lineage，不为 output_id 简化而删除。

未发布的 Legacy 中间表示保留既有合法查看能力，但 delivery_id/output_id 可空并明确未正式发布；不能造正式身份。选择其它 Task/Revision/Delivery 的 output_id 拒绝，文件缺失、大小/hash不符或路径越界失败关闭。预览与单文件下载复用一致的登记完整性边界。

### 3. 来源按冻结 membership 授权，表格用全量窗口定位

新增一条任务级只读接缝，统一由后端验证 Owner、TaskRevision、来源成员关系与原件哈希，不能凭同 Owner 任意上传文件或客户端 refs 进入本修订画布。优先在既有任务路由提供：

`GET /api/semantic-workspace/tasks/{task_id}/sources/{artifact_id}/preview`

参数复用 `revision/offset/limit/search/sort_by/sort_direction`，表格增加 `table_ref`；引用定位可用明确 `row_number` 请求包含该物理行的窗口。只建这一处任务权威，不并行新增另一套上传预览协议。现有 PDF/content、DOCX/document-preview 可继续承载文件读取，但本票入口与引用必须先经任务成员关系校验。

响应回显 task_id、revision、artifact_id、upload_id或null、原件sha256与表示身份/解析版本；表格同时返回 tables（table_ref、table_index、name、header_row）、selected_table_ref、columns、rows、offset、limit、total、is_complete。每行包含稳定物理 row_number 与 values，行号不能随搜索、排序和分页重编号。

- 来源集合取所选修订冻结 binding reports、Runtime request.sources、SourceSnapshot/Revision.source_refs 或 Delivery.source_artifact_hashes 中可核对的事实；不得回退活动 task.upload_ids。缺乏足够冻结事实时返回具体不可用原因。
- table_ref/sheet/index/header_row 必须沿用冻结 inspection 与执行器语义。Excel 非首行表头、空行、公式缓存和 CSV 引号换行遵循原解释，不能用总取首行表头的通用 parser 替代后声称精确。
- 全量搜索排序先作用于整个所选表，再返回窗口；total 仅在完整读取后表示过滤结果数。既有读取限额内实现，超限明确拒绝或说明不完整，不把 sample/estimated_records 当全量。CSV 的“物理行”沿执行器记录定义，不以文本换行个数猜测。
- 同值重复行靠 table_ref/row_number 区分。当前筛选挡住目标时显式进入定位窗口或提示被筛选，不静默选择另一个同值行。
- PDF 页号、DOCX element_id/解析版本、bbox坐标空间等使用真实 EvidenceRef；等待对应页/元素加载并确认命中后才显示“已定位”。缺页、原件缺失、版本不匹配、无可靠坐标分别显示无法定位，仍允许合法来源浏览；不回退第一页并伪称命中。
- 公开字段采用最小白名单；不返回 raw_result_ref、宿主路径、任意 metadata 或日志。网页现有 text_preview 标明摘要/截断边界，无法证明是否完整时明确完整性未确认，不自动加载 content_blob 或重新联网。

### 4. 回答引用采用显式选定结果项的真实绑定

此项是 #129 完成门，不能仅增加“未附结构化引用”文案后略过。

用户只从当前活动修订、已登记正式 output 的结果项/表格行显式选择“围绕此结果追问”。Candidate 保留既有查看、重验和发布能力，本票不增加候选引用身份协议。前端只提交下述 result_context 选择身份，不提交正文或证据列表。后端重新读取对应结果，验证 Owner、修订、交付/表示哈希和项目存在，再解析该项现有 lineage/evidence_refs，核其来源成员关系。结果文档可复用既有 passage_id/finding_id 等；表格须由已有输出记录身份生成稳定不透明 item_ref，不能用当前页下标或值相等反查。

服务端将实际选定的有界内容作为这次追问输入，经既有 ConversationSteering/rewriter、冻结模型连接和外发确认处理；不新增生成器，不让模型创造 refs。在现有持久 JSON 边界中保存本回合与选定结果/来源的关联，扩展公开回答可选引用字段；GET turns、GET task/messages 与 SSE 回放必须返回同一持久关系。对已有无该字段的结果采用空关系，不回写历史回答。

引用控件统一标为“本次追问引用的结果/来源”，表示该次输入关联，不宣称回答每句话都经过独立事实验证。用户可选全部真实 refs 中的一条定位，不能永远只取首条。正常自由问答没有结构化绑定时仍明确“未附结构化引用”；普通 Markdown 链接和同任务来源都不能冒充已绑定证据。

同幂等回合必须绑定同一个规范化结果选择；相同键换 item/output/表示须拒绝，不能返回旧回答配新引用。并发及未知结果仍沿 #128 网络前持久占位，禁止增加重复付费调用。在网络前使用现有持久接缝冻结选择及摘要，回答完成时保存关联；不能只在前端内存或模型返回后临时附 refs。现场核实 RawUserTurn 仅有显式 SQL 列、没有 JSON 附加存储，零 DDL 的初始假设不成立。主会话已批准最窄显式迁移：conversation_raw_turns 新增可空 result_context_json，保存服务端核验后的规范身份、有界选定正文和 refs；旧回合为空。保存原话与该上下文须在同一事务内完成，相同幂等键比较规范上下文，任何字段变化拒绝。SteeringResult 沿用现有 payload_json 扩展公开关联；不挪用用户正文、Grant 或 ContextDelta 存放选择。迁移仅在临时库做前向/重放/兼容验证，不执行生产迁移。

单次发送占位覆盖新引用链的各现有路径，包括本地 Instructor 和冻结连接 Broker；不能仅依赖 Broker Grant 而让本地引用追问重复调用。主会话批准在尚未提交的同一 webui_0014 迁移中增加内部 result_context_claimed 整数列，默认 0、仅允许 0/1，不进入公开模型。先比较规范上下文并返回已有结果；无结果时核当前修订，再以 Owner/turn_id、上下文非空和 claimed=0 为条件原子更新至 1，提交事务后才进入 rewriter。占位已有而结果未存时返回 409、明确结果未知，取消、异常、进程重建或时间经过均不得清除占位重发。既有 Broker Grant 继续保留，rewriter 实际网络前仍复核活动修订；无 result_context 的普通追问不增加此路径。验收以两种 rewriter 路径的并发及未知结果重建证明单次调用。

历史修订仍可查看、定位与下载；新的结果引用追问按钮必须禁用并明确提示返回最新版本。既有自由追问基于最新修订的明示行为不变，不能把历史 V1 结果附入最新 V2。后端在接入请求和实际网络调用前分别重读活动 revision；选择不再属于当前活动修订返回 409，不发模型请求，也不改绑或静默重试。


### 4A. 前后端已协商冻结的引用 schema

本节与后端/前端所有者已确认，是唯一字段协议。可选结果上下文以缺省或 null 表示没有关联；客户端必须把服务端返回的 item_ref 作为不透明值使用。

```typescript
type ResultSelection = {
  revision: number;                 // 大于等于1；必须是当前活动修订
  output_id: string;                // 既有登记ID规则；必须属于该修订正式Delivery
  representation_sha256: string;  // 64位小写十六进制
  item_ref: string;                // 正则 ^item_[0-9a-f]{64}$
};
type SourceLocation =
  | { kind: "docx_paragraph"; paragraph: number }
  | { kind: "docx_table_row"; table: number; row: number }
  | { kind: "text_line"; line: number };
type ResultSourceRef = {
  artifact_id: string;
  source_sha256: string;           // 必须来自已验证冻结映射，64位小写十六进制
  snapshot_id?: string;
  table_ref?: string;
  row_number?: number;             // 原表物理行，1起点，不是分页下标
  element_id?: string;
  page?: number;                   // 1起点
  bbox?: {
    x0: number; y0: number; x1: number; y1: number;
    coordinate_space: "pdf_points" | "image_pixels" | "normalized_1000";
  };
  extractor?: string;
  extractor_version?: string;
  location?: SourceLocation;
  read_at?: string;                // ISO时间；仅已有冻结读取事实才返回
};
type PublicResultContext = ResultSelection & {
  label: string;                   // 服务端从真实结果项生成的安全显示标签
  source_refs: ResultSourceRef[];
};
// POST /tasks/{task_id}/turns：保留原text/确认字段，新增下列可选字段。
// Idempotency-Key仍沿原请求头；不得由客户端传source_refs/label/选定正文。
type TurnResultContextField = { result_context?: ResultSelection | null };
// SteeringResult与WorkspaceMessage使用相同公开投影字段。
type AnswerResultContextField = { result_context?: PublicResultContext | null };
// preview原rows/items业务内容保持不变，新增平行数组。
type PreviewItemRefsField = { item_refs: Array<string | null> };
```

`item_refs` 与当前响应 rows 或 items 必须严格等长同序；只以数组位置配对，不把位置当身份。后端在过滤/排序/分页之前建立稳定 ID：`item_` 加 SHA256（紧凑规范 JSON 元组 `[representation_sha256, kind, stable_internal_id]` 的 UTF-8 字节）。表格优先使用已有 __mg_output_record_id，公开前再次摘要并继续移除原内部列；文档使用带条目type的持久 passage/diff/finding/content ID。纯文件解析项使用不可变表示内预过滤原始序号，XLSX使用原sheet索引与物理行号组合。无法重建稳定选择或没有正式输出关联时对应item_ref为null，前端不启用引用追问；不得按页面序号、值匹配或当前排序发明身份。

source_refs 由后端读该项现有 refs/lineage 并核验后产生，禁止客户端或模型扩充。source_sha256缺失或不能通过冻结成员/hash校验时拒绝该选择，不把空hash当已验证。无原始 refs 的稳定结果项可以明确返回空source_refs，但不能伪造来源；有 refs 而无法核验不能偷偷删成空集合绕过检查。上述可选字段无事实时省略，不补空字符串或猜值；quote、raw_result_ref、任意metadata、values和选中正文均不进入公开引用对象。

location仅公开三种已核实现有结构及其整数字段，保留解析器原索引语义：DOCX paragraph/table是现有1起点计数，DOCX row沿解析器的原始0起点行索引（含表头），text_line沿解析器原行号；不可统一改成表格row_number。其它location.kind不透传，可继续用element_id定位。bbox保留显式坐标空间与合法有限数值、x1>x0/y1>y0，缺可靠尺寸时不绘制猜测高亮。InspectionReport.generated_at不是read_at，不能替换。

错误语义：未授权Owner/任务/输出按既有404处理；过期revision、表示hash不符、item不存在/不可重建、来源冻结证据不符和同幂等键换上下文返回409且不调用模型。每个请求在持久化前完成选择核验，网络前再查活动修订；公开GET/SSE只回放保存的PublicResultContext，不重新请求模型或借当前结果重建旧引用。

### 5. 同身份往返保留状态，换身份仍重置

页面现有 Owner/Task/Revision/Delivery 边界上保存少量 UI 状态：选择的输出/表示、来源、sheet、页码、搜索排序、缩放、横纵滚动和定位状态。再加入 output/表示哈希或来源artifact/hash/解析版本区分资源；同身份来源→结果、收起→展开、专注→返回、窄屏返回恢复原位置。

跨 Owner/Task/Revision/Delivery 切换遵守 #119：加载首帧不显示旧正文，筛选、排序、分页和定位重置。不能为了返回位置保留把 V1 状态套到 V2；本票不扩大为跨修订浏览器持久缓存。所有迟到响应在落地和渲染前核身份，取消或卸载释放请求/对象 URL；不把整份文件或全部 PDF 实例常驻。

### 6. 历史 ZIP 归属与完整性

现有 bundle 路由沿选定 revision/run 的正式 manifest 打包，不重生成格式。trace 使用所选修订及白名单任务字段、安全结构化事件；不能包含活动修订目标、其它修订对话、原始 attempts/日志、Prompt或宿主路径。

include_sources 根据该交付和修订的冻结来源清单恢复原件，逐项核 Owner、身份、hash、大小及路径；不能读取不存在的 selected_revision.upload_ids 或回退活动列表。冻结清单不全时明确拒绝 include_sources，不能生成看似完整的包；普通输出下载仍按自身资格工作。网页快照引用单独按真实编码和关系解析，不按冒号猜上传ID。

输出逐项核对所属 Run/Delivery、登记大小/hash和根目录。所有条目验证后再生成包；失败清理仅本次临时 ZIP。相同文件名使用稳定资源ID区分，manifest/归档映射与QA对应，禁止覆盖或混淆。用真实合成文件解包验证，不用“请求带revision”的浏览器断言替代内容证据。

## 验收标准

1. **完整同身份旅程**：长表全量筛选/排序/第二页→非首引用→来源非首sheet/第31行后或PDF后页→缩放/滚动→返回，状态保留；关开/专注/窄屏返回可用。切修订仍通过 #119 原重置断言。
2. **精确结果身份**：同任务两修订、多output、两Owner和两任务使用不同正文/QA/来源/下载；缓存首帧及迟到响应不混用。后续attempt不能替换旧Delivery权威派生结果；错误output归属、篡改和缺失明确拒绝。
3. **来源窗口与定位**：多sheet、非首行表头、空行、重复值、CSV含换行、公式无缓存、超限、筛选后定位、页/行越界、DOCX解析版本变化。以执行器物理行事实核窗口和高亮，不以30条sample作全量证据；无命中不得“已定位”。
4. **真实回答关联**：从当前活动修订至少一个正式output的有可信refs结果项/行显式追问，替身捕获的模型输入包含实际所选内容；服务端拒绝伪造refs、错output/hash/item和跨Owner/修订；持久回答与相同回合refs可重连/重建恢复。改默认模型不换冻结连接；重复/并发同回合仅一次调用，换绑定拒绝；自由问答和无证据项不制造引用；历史引用按钮禁用可回最新，提交与网络前切revision均409且零模型调用，Candidate不新增引用追问协议。旧turn空上下文可正常读取，显式迁移重放与带上下文幂等冲突均用临时库验证。
5. **部分与正式**：保留9/10与缺口、零成果、Candidate下载拒绝、重验通过未发布、正式多格式下载状态；回答引用不提升结果资格。网页摘要/截断与不支持预览格式准确披露。
6. **ZIP真实内容**：两修订不同来源和同名文件，解包核manifest、QA、输出与原件hash及归档映射；历史包无新修订内容。输出/来源篡改、缺失冻结清单、跨Owner/任务拒绝，失败无本次临时包残留。
7. **安全与可访问性**：恶意HTML/危险链接/远程图片不自动执行或请求；长表、PDF、长文和超大/损坏/404/409/取消后的失败状态可见，无未处理pageerror。桌面/窄短屏、明暗、键盘切换分页缩放与滚动区、焦点返回、减少动画与短读屏播报有E2E，弹窗前后都检查axe，不由遮罩隐藏背景缺口。
8. **完成门**：先最小红绿，再受影响API/来源读取/交付和兄弟前端回归；保留 #119身份/#128对话和用量/既有文档独有能力。Standards与Spec独立复核、同提交minimum-ci和受影响后端及完整浏览器门通过。合成文件、替身、真实服务、用户视觉与发布证据分别记录，失败记录不改写成通过。

## 边界与实现前必须核定事项

- 零新增依赖、零真实模型/来源外发；只允许上述 result_context_json 和 result_context_claimed 的必要窄显式迁移实现与临时库验证，不执行生产迁移；不修改CoreMind专属checkout、生产数据/秘密，不创建worktree或重启现有产品服务。规格本身不授权Git、发布或生产迁移。
- #131保留图片/扫描件统一接入、OCR/视觉提取和新证据生产；#132保留完整原始资料产品化、网页全文及全格式原件能力。本票复用当前可用原件和预览，显式说明缺失能力，不提前搬迁或删除旧文档/图像独有路径。
- 后端实施前落实三点并向前端交接：①Legacy发布输入/lineage精确查找与正式output/表示响应；②冻结来源membership和执行器一致的窗口/物理行实现及既有限额；③按已冻结schema实现item_ref、可信选定内容输入、原话和result_context_json原子保存及结果JSON引用的幂等接缝。前述均为要求，不代表已经实现；发现超出已批准窄迁移或现有执行边界时先报告具体缺口，不降级成伪引用或静默删需求。
- 前端可按上述身份/视图/引用语义拆分页面、ResultPreview、SourcePreviewPanel及类型/API；接口字段与后端单一所有者确认后接入。未知来源/时间/表示/定位一律诚实显示未知，禁止为演示生成身份或证据。
