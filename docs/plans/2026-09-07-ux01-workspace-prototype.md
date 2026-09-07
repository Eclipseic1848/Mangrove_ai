# UX01：任务案头交互原型与设计修订提案

> 工单：#124。状态：本地工程验证通过，待最终CI及用户视觉验收。
> 基线：2322e6b686fd35875190077df13001a75a7f216c。原型不是生产能力，不连接真实服务。

## 目标与访问

中文业务用户从一句自然语言要求开始，在同一个任务内补资料、澄清、执行、核对结果、继续修改、复用或删除。
原型位于 `frontend/ux01-prototype.html`，由现有 Vite 开发服务器提供 `/ux01-prototype.html`。
它不注册到产品 `App` 路由，也不替代 `/data-prep`。首次视觉验收前不修改共享运行时 Token。

所有人物、文件、统计、来源、用量和结果为虚构演示。扫码只展示示意和模拟操作，不提供可登录的真实二维码。
下载只生成演示内容；删除只改变页面内存。刷新可以重置演示，不代表真实业务支持撤销或从缓存复活已删除数据。

## DESIGN 修订提案

现有 DESIGN 的“资料接收单”、来源类型选择和双侧栏属于旧布局。#124要求的任务案头替换这些布局约束；
本提案先供用户操作后确认，UX02实施时与共享组件/Token一起同步 DESIGN，不将候选样式冒充已批准的运行时规范。

| 项目 | 拟采用的规则 | 保持的边界 |
|---|---|---|
| 主布局 | 一层可收起任务导航、连续对话、按需打开的资料/结果画布 | 不增加第二种执行模式或独立聊天后端 |
| 窄屏 | 导航抽屉，对话/资料/结果切视图；输入与当前待办可达 | 不以隐藏溢出代替可用性 |
| 视觉识别 | 保留 howso/Mangrove 标识和红树林青；来源定位将结论与证据连起来 | 不使用卡片墙、装饰渐变或固定三步流程 |
| 色彩 | 使用现有 HSL 的 background/foreground/primary/accent/border/destructive；保持明暗两套 | DESIGN中的青色、近白和冷黑是品牌依据，不复制页面色表 |
| 字体 | 本地中文系统 sans 作正文，系统 mono 仅用于数据/技术身份；最多两种角色 | 不加载外部字体，不增加依赖 |
| 阅读 | 对话以连续段落呈现，标题、证据与工作记录有清楚层级；表格在自己的画布内滚动 | 不把长回答嵌进多层气泡或嵌套卡片 |
| 操作 | 同一区域一个主动作；状态、原因和下一步用中文说明 | 颜色不是唯一反馈；未知不显示成功 |
| 动效 | 仅状态/焦点等必要反馈，尊重 reduced-motion | 不用入场动画替代进度事实 |

Token仍由 `frontend/src/index.css` 和 Tailwind 消费关系持有。原型可局部验证布局、间距和字号，
不得修改共享颜色、主题或基础控件以提前启动UX02。共享迁移时统一映射，不能维护永久第二套设计系统。

## 跨页行为和组件归属

下表列名供静态工具识别，行为说明使用中文。

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| 导航 | 产品 Layout/App；原型独立入口 | 本文及既有UX契约 | 单层任务导航；辅助入口可回当前任务；窄屏抽屉可键盘开关 | 原型浏览器与对应后续票 |
| Button/Input | `components/ui/button.tsx`、`input.tsx`，简单字段保留原生语义 | 本文及既有UX契约 | 可访问名称、焦点、禁用/等待状态；空输入说明原因 | 原型浏览器与对应后续票 |
| Form | 现有字段与业务校验；本票不建立通用表单框架 | 本文及既有UX契约 | 保留输入，错误关联字段；Enter/Shift+Enter和中文IME分别处理 | 原型浏览器与对应后续票 |
| Select/Listbox | 原生 select，接受系统弹层 | 本文及既有UX契约 | 有标签，键盘可选择，长选项不挤掉核心操作 | 原型浏览器与对应后续票 |
| Table Selection | 本票无批量表格选择 | 本文及既有UX契约 | 不引入选择框或全量选择概念 | 原型浏览器与对应后续票 |
| Date | 本票无日期编辑 | 本文及既有UX契约 | 时间仅为明确标注的演示值，不实现新日历 | 原型浏览器与对应后续票 |
| Dialog/确认 | 已安装 Radix Dialog/AlertDialog；现有 Modal 可按实际能力复用 | 本文及既有UX契约 | 标题/说明、焦点约束、Escape、关闭后恢复焦点、长内容内部滚动 | 原型浏览器与对应后续票 |
| Scrollbar | `index.css`与HSL变量 | 本文及既有UX契约 | 画布/对话各自滚动；主题切换不改变业务状态 | 原型浏览器与对应后续票 |
| Table/Preview | 生产 ResultPreview、TanStack、既有文档预览 | 本文及既有UX契约 | 原型虚构小表使用语义table；生产复用原分页/身份契约 | 原型浏览器与对应后续票 |
| Toast | 生产 Sonner；原型持久就地状态 | 本文及既有UX契约 | 重要错误/部分结果/待办不能只靠短暂Toast；live region适量播报 | 原型浏览器与对应后续票 |
| 工作记录 | 生产WorkTrace；原型只显示已推进的模拟事件 | 本文及既有UX契约 | 默认折叠，授权/扫码待办独立显示，不展示逐字思维链 | 原型浏览器与对应后续票 |
| CRUD | 生产由服务端生命周期持有；原型只展示确认过程 | 本文及既有UX契约 | 展示关联任务、影响与共享资料选择；不乐观假称生产删除成功 | 原型浏览器与对应后续票 |

普通用户、管理员与超级管理员权限继续由后端执行。原型的管理演示不是角色授权，也不读取他人正文。
个人记忆默认本人；报告模板/经验的通用副本按ADR-0040预览确认后共享，不能把质量晋级当共享许可。

## 必须能操作的演示旅程

| 旅程 | 需要看见和完成的动作 | 本票不能声称的能力 |
|---|---|---|
| 文件分析 | 加入虚构文件、预览、自然语言要求、澄清、执行、核对来源/结果、下载示例、继续修订 | 未上传/解析真实用户文件 |
| 无URL检索 | 输入主题、确认有界要求、展示取得的虚构来源和结果 | 未真实搜索，发现链接不等于取得正文 |
| 混合资料 | 文件与网页来源同任务使用，查看各自资料和缺口 | 未实接混合Runtime |
| 部分结果 | 可用结果和缺口同时出现，重试/保留可用结果，停止/未知结果有后续路径 | 部分结果不能冒充完整正式交付 |
| 扫码恢复 | 待办可见、示意失效、模拟重新登录、回同任务继续 | 未登录来源，不收集Cookie，不提供真实二维码 |
| 关联删除 | 查看引用；保留共享资料或明确删除共享资料；取消/确认；依赖先暂停，其他任务独立结果保留 | 未真实删除；不能承诺数据已删后仍可完整重验 |

## 功能保留矩阵

下表基于frontend当前源码和测试源码。已有接线表示能追踪到客户端API；fake验证源码不代表真实服务；真实测试源码也不代表本次执行。
页面路径简称相对frontend/src，测试路径相对仓库根。矩阵核查未读取生产数据；后续票按公开父票#113。

| 能力 | 当前入口与可核代码路径 | 已有测试路径与证据限制 | 目标入口 | 后续票 | 该项旧入口退役条件 |
|---|---|---|---|---|---|
| 自然语言启动、澄清 | /data-prep → pages/DataPrepPage.tsx → SemanticWorkspacePage.tsx → workspace/TaskComposer.tsx；lib/semanticWorkspaceApi.ts 的 createWorkspaceTask/answerWorkspaceTask；/chat → Chat.tsx + lib/api.ts streamChat | frontend/e2e/semantic-workspace.spec.ts 的文件目标提交、开始执行、待确认；chat-stream.spec.ts/chat-ordering.spec.ts 均替身验证源码；已有接线 | /data-prep 同一连续对话及输入，不选执行模式 | RT01 #125、UX02 #127 | 文件起步、澄清、失败保留输入、取消与恢复在统一入口通过；不能只替换空态 |
| 上传、进度、原件预览 | TaskComposer.tsx → lib/dataPrepApi.ts uploadFileWithProgress；SourcePreviewPanel.tsx → 获取原件、表格和文档预览 | semantic-workspace.spec.ts：Word自动预览、不支持格式；document-workspace.spec.ts：上传失败、DOCX迟到恢复；fake源码 | 同一输入附件、资料画布；每项可见进度及错误 | UX02 #127、UX04 #129 | 所有仍支持的格式、单项失败不丢其他资料、身份隔离及重新打开通过 |
| 文档与表格结果 | /data-prep ResultPreview.tsx 的 table/document 分支、分页排序筛选；/data-prep?legacy=1 的 DocumentWorkspacePage.tsx、StructuredDataPrepPage.tsx | semantic-workspace.spec.ts 的结果预览/版本隔离；document-workspace.spec.ts 的连续正文/多记录；fake源码。phase4b-8b1-real.spec.ts 是旧文档入口真实服务用例源码，本次未运行 | 结果画布：长文、宽表、专注结果视图 | UX04 #129 | 全部有效文档/表格能力、字段定位、长内容和下载一致性已验证；不能用一个表格替代文档能力 |
| PDF 与图片 | SourcePreviewPanel.tsx 用 react-pdf；旧 DocumentWorkspacePage.tsx 接 ImageEvidenceViewer.tsx（OpenSeadragon）；旧上传接受 png/jpg/jpeg/webp。新 TaskComposer.tsx DOCUMENT_EXTENSIONS 未包含图片 | document-workspace.spec.ts 有文档流程fake源码；未找到独立图片缩放/坐标全链成功证据。本次不声称新工作台已支持图片上传 | 资料画布 PDF翻页/定位、图片缩放及证据框 | FILE01 #131、UX04 #129 | 统一入口补齐图片实际上传/预览/证据定位，PDF与图片宽窄屏键盘验证后，旧图像唯一入口才可退役 |
| 多轮修订与旧版本 | SemanticWorkspacePage.tsx + semanticWorkspaceApi.ts revisions/revision-proposals/decision；TaskTimeline.tsx；旧 DocumentWorkspacePage.tsx 的 intent/extraction 路径 | semantic-workspace.spec.ts：运行中追问草案→确认新版本、严格缺口新版本；document-workspace.spec.ts：同任务修订；fake源码 | 同一输入区追加要求，显式版本切换 | UX03 #128、RT01 #125 | 确认生成不可变新修订、旧结果仍可查、并发冲突和迟到响应不串版本 |
| 模板及教训库 | /templates → Templates.tsx：预览、删除、评分信息、共享通用副本；/chat 的 confirm/template；WebSourceIntake.tsx 调 getTaskContextOptions/previewTaskContext 接入网页任务上下文 | library-sharing.spec.ts：Owner预览确认共享及失败/关闭竞态，fake源码；不能据此推断所有任务均接模板 | 当前任务调用共享模板Module，/templates 管理入口保留并可返回任务 | SHARE01 #140、UX05 #142、RT01 #125 | 模板选择/预览/确认、本人原件和独立平台副本边界、评分晋级淘汰信息均保留；非只迁移列表 |
| 个人记忆 | /memory → Memory.tsx /api/memory、/api/memory/self；WebSourceIntake.tsx 的 context-options/context-preview 可选择记忆 | 本次未找到独立 Memory CRUD E2E；网页上下文API客户端及调用者已核，不等于文件入口也已接通 | 当前任务使用共享记忆Module，/memory 可管理和返回任务 | SHARE01 #140、UX05 #142、RT01 #125 | 个人新增删除、Owner拒绝、应用预览/确认真实验证完成 |
| 调度/自动任务 | /tasks → Tasks.tsx：manual创建、patch编辑/启停、run_now、历史、报告/JSON；/chat 的 schedule 动作 POST /api/tasks | 本次未找到独立调度完整旅程E2E，已有前端API接线，真实调度执行未验证 | /tasks 辅助页与当前任务“设为自动任务”，沿用同一生命周期 | AUTO01 #141、SHARE01 #140、UX05 #142、RT01 #125 | 新建/编辑/暂停/执行/历史结果通过，生命周期和Owner一致；不能用静态菜单替代调度 |
| 用户反馈及审计处理 | /chat Chat.tsx /api/chat/feedback 赞踩、原因及评论；管理员 /feedback → Feedback.tsx 审计正文 | chat-ordering.spec.ts 反馈迟到隔离；feedback-audit.spec.ts 审计原因、失败、关闭、状态备注；fake源码 | 当前结果反馈动作；管理员辅助页受控查看 | UX03 #128、UX05 #142 | 统一结果能提交反馈；后台元数据、审计原因及正文清理仍通过 |
| 格式下载与正式交付 | TaskComposer.tsx 格式选项；ResultPreview.tsx Candidate下载和正式 output.download_url，downloadWorkspaceBundle；旧Tasks报告/JSON | semantic-workspace.spec.ts Candidate非正式、版本/QA/下载身份一致；phase4b-8b1-real.spec.ts 旧DOCX流程下载验证源码，本次未运行；格式选项不证明11种全部可生成 | 结果画布按实际输出显示格式和下载；Candidate显著标注 | UX04 #129 | 每种保留格式有实际非空重开/完整性与正确身份证据；正式交付门独立验证 |
| 原始数据下载及定位 | ResultPreview.tsx ZIP包含原始文件选项→downloadWorkspaceBundle；SourcePreviewPanel.tsx 原件/表格预览；旧DocumentWorkspacePage.tsx raw表与审计产物 | semantic-workspace.spec.ts 结果回原件定位fake源码；ZIP原件完整下载本次未验证 | 资料画布“原始资料”；结果明确派生身份和来源 | RAW01 #132、UX04 #129 | 原始/派生区分、下载授权及内容一致、证据定位均通过；不把搜索摘要当原始数据 |
| 模型选择与用量 | TaskComposer.tsx 模型；TaskTimeline.tsx workSession.usage/provider_usage 展示调用、原生Token、未知；Chat.tsx tokenUsage；settings/ModelConnectionsPanel.tsx 连接配置 | settings-role-access.spec.ts 连接及角色fake源码；semantic-workspace.spec.ts Provider确认fake源码；用量显示有接线，前置/Run去重和参考费用未完成核验 | 顶部模型可见；工作记录显示分会话用量，未知不得记0，参考费用带核验来源 | UX02 #127、UX03 #128、UX05 #142 | 连接隐私、外发确认、未知值、模型/会话归属与去重通过；费用不得仅凭假数据开放 |
| 管理与设置 | App.tsx AdminOnly保护/admin和/feedback；Admin.tsx、Settings.tsx、CapabilityGovernancePanel.tsx、ConfigCenter.tsx；/ 为Dashboard.tsx | admin-ordering.spec.ts、settings-role-access.spec.ts、capability-audit-lifecycle.spec.ts、account-execution.spec.ts 都有fake源码；权限前端不等于服务端鉴权 | 同一导航的角色受控辅助入口，保留返回任务 | UX05 #142 | 管理元数据、内容审计、账号治理、模型设置等逐项可用；保持后端独立拒绝，不扩大普通用户权限 |
| 公开URL来源、范围及刷新 | /data-prep → WebSourceIntake.tsx；semanticWorkspaceApi.ts source attempts/refresh；有模板与记忆上下文预览 | semantic-workspace.spec.ts URL范围、同站缺口、结果未知、停止fake源码 | 统一输入自动识别来源，在当前任务显示范围与取得状态 | UX02 #127、WEB01 #134 | URL已有取得、范围确认、部分结果、未知恢复、取消证据均保留；不得用假进度替代 |
| 无URL检索与文件/网络混合 | 当前 TaskComposer/WebSourceIntake 仍分别承担文件与网页入口；未找到无URL检索及真正混合任务连续UI | 尚规划；原型仅交互样例，不表示搜索/混合后端已通 | 同一输入写目标并附文件，可检索互联网后合并核对 | RT01 #125、MIX01 #135、UX02 #127、NL01 #130、WEB01 #134 | 完整检索/混合来源冻结、部分成功与失败、Owner范围及交付真实验证后才移除旧唯一能力 |
| 来源扫码登录 | 本次在 frontend/src 未找到来源扫码完整组件及扫码生命周期接线；平台Login.tsx是平台账号登录，不能混称来源登录 | 尚规划，未找到对应来源扫码E2E；platform-session系列仅平台会话 | 当前任务醒目待办：扫码/过期/完成后恢复同一任务 | AUTH01 #138、UX03 #128 | 真实受控来源扫码、过期/取消/失权/恢复通过；不存在旧扫码入口可直接退役 |
| 关联删除 | SemanticWorkspacePage.tsx 已有回收站/恢复/永久删除；当前文案为底层审计制品继续保留，未发现实际共享引用分支UI | semantic-workspace.spec.ts 回收站恢复fake源码；chat-ordering.spec.ts 删除迟到；ADR-0039关联删除为尚规划 | 任务删除弹层展示真实引用：独占、保留共享、删除共享 | DEL01 #137、UX05 #142 | 服务端真实引用、受影响任务停止、失败/未知恢复与正确后果通过；不能把现有回收站当关联清理完成 |
| 来源/结果跨任务复用 | 当前来源预览和同任务修订已接线；未找到本人来源/结果跨任务选择器与引用确认完整入口 | 尚规划，现有缓存/修订测试不证明复用已实现 | 统一输入附件菜单“使用已有资料/结果”，标原始/派生、获取时间 | REUSE01 #136、UX04 #129 | 新任务精确引用与权限复核、删除/失权拒绝、派生标识通过；不以复制文本冒充来源复用 |
| 工作记录、停止、平台会话恢复 | TaskTimeline.tsx、SemanticWorkspacePage.tsx、lib/api.ts/lib/auth.tsx；状态与取消API已接线 | semantic-workspace.spec.ts 停止/清理未知/重试；platform-session-recovery.spec.ts、account-execution-resume.spec.ts fake源码 | 连续对话内默认收起工作记录，停止及待办始终可达 | UX03 #128 | 断流不重跑、暂停/取消/清理分离、重新登录返回原任务、迟到事件隔离通过 |
| 数据库连接与表列选择 | /data-prep?legacy=1 → StructuredDataPrepPage.tsx + lib/dataPrepApi.ts | phase3-database.spec.ts route.fulfill验证源码；本次无真实数据库访问 | 统一来源选择中的已授权数据库资料入口；具体支持范围按来源票 | CONN01 #139、UX04 #129 | 唯一数据库连接/测试/表列选择能力被等价接入并验证，再退役旧结构化页 |

只有替代能力与实际回归通过后，才由QA01 #144/CLOSE01 #145退役旧入口；本票不删除旧实现。

## 验证与验收

验收覆盖1440/390px、明暗主题、六旅程、键盘/焦点、中文IME、错误/停止/未知恢复、短视口、长内容、200%布局、reduced-motion。
浏览器应记录网络请求，确认无真实API/外部资源访问；下载应核对虚构文件内容。静态审查不能替代可操作浏览器验证。
Standards/Spec及同提交CI分别记录。原型构建通过不代表生产体验或用户视觉认可。

**用户查看可交互原型后明确确认视觉方向，才能合并关闭#124。** 当前还未取得该确认。

### 已取得的本地工程证据

- 30条浏览器旅程通过，另1条原生选择器键盘打开/选择通过；六场景覆盖1440/390与明暗主题，四组合axe无A/AA发现。
- 登录取消恢复、混合来源分别核对、部分重试失败、版本1/2及三种下载一致性、IME、停止/未知恢复、弹层焦点和长输入验证通过。
- 390×568短视口可滚动操作；720×450为1440×900在200%下的等效CSS视口回流检查，不冒称全部浏览器原生缩放或软键盘设备已验。
- TypeScript与生产构建通过；现有大bundle警告保持。原型范围Premium strict静态审计零发现。
- Standards焦点问题、Spec四项旅程问题均修复并复核无遗留；测试源码保留。全部数据虚构，网络拦截未见业务API或外部资源请求。
- 最终同SHA CI另在PR记录；用户尚未确认视觉方向，不能据测试绿色关闭#124。
