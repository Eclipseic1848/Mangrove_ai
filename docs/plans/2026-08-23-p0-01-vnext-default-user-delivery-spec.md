# P0-01：vNext 默认链路与真实普通用户闭环规格

> 状态：approved，用户于 2026-08-23 明确确认
>
> 规格日期：2026-08-23
>
> 当前证据级别：SPEC_APPROVED
>
> 权威决策：ADR-0030
>
> 远端状态：未创建 GitHub Issue、分支、提交、PR 或发布

## Problem Statement

生产 Rollout 已经是 vnext_default，但主工作台创建任务时仍把 Legacy 作为前端初值并显式
提交。服务端会尊重显式 Legacy，因此普通用户从当前主界面创建的新任务不会按平台默认策略
进入 vNext。这造成运行策略、API 契约和产品体验三者不一致。

服务端已经能够在客户端省略运行时字段时按 Rollout、P0 门、历史绑定和用户显式意图冻结
RuntimeAssignment，但 API 模型仍向客户端公开 Legacy 默认值，主界面也无法表达“平台决定”。
任务详情虽然能收到最终运行时，当前页面没有把这个服务端事实明确展示给用户。

问题的用户影响是：

- 普通用户无需理解内部运行时，却会被前端悄悄固定到兼容链路；
- 用户无法区分平台默认、显式增强和显式兼容三种请求意图；
- 平台发生 P0 自动回退时，用户缺少最终实际执行方式的明确反馈；
- 外部模型连接、任务级外发确认、能力选择和运行时之间可能出现残留或误绑定；
- G3 的状态切换虽已验证，但尚无主工作台默认创建并形成正式 vNext Delivery 的真实闭环。

## Solution

主工作台把执行方式改为三个互斥选项：

1. **平台默认（推荐）**：不向 API 发送 runtime_version，由服务端 Rollout 决定；
2. **增强模式（Pi）**：显式请求 Pi；不合格、未开放或被 P0 阻断时失败关闭；
3. **兼容模式（Legacy）**：显式请求 Legacy，并继续受历史兼容契约保护。

选项使用浏览器原生 radio、fieldset、legend 和 label 实现互斥选择和键盘语义，复用现有
Tailwind、主题 Token、cn 工具和工作台视觉语言，不新增 npm 依赖。页面不进行视觉重做，
只把当前两项按钮选择器收敛成语义正确的三项控件。

平台默认与显式 Pi 都可能需要模型连接。界面继续展示连接身份、冻结模型和本任务外发范围，
并要求任务级确认。服务端只有在最终选择 Pi 时才冻结模型连接、外发确认和 Pi 专属能力；
若平台默认因 P0 回退或其他权威策略最终选择 Legacy，不得留下模型连接、能力包或虚假的
外发记录。

创建成功后，任务标题区域显示服务端冻结的实际执行方式：

- 实际执行：增强模式（Pi）；
- 实际执行：兼容模式（Legacy）。

最终显示只读取创建响应或任务查询投影，不能由前端选项推测。历史任务和历史修订继续显示
各自冻结的 RuntimeAssignment。

## User Stories

1. As an 普通用户, I want 新任务默认采用平台当前策略, so that 我无需理解内部运行时也能获得平台推荐的执行方式。
2. As an 普通用户, I want “平台默认”成为初始选项, so that 前端不会再静默把我固定到 Legacy。
3. As an 普通用户, I want 平台默认请求真正省略运行时字段, so that 服务端 Rollout 保持唯一默认决策权威。
4. As an 普通用户, I want 显式选择增强模式, so that 我可以明确要求 Pi 而不是依赖平台默认。
5. As an 普通用户, I want 显式 Pi 被阻断时得到明确拒绝, so that 系统不会静默改用 Legacy 并伪装成功。
6. As an 普通用户, I want 显式选择兼容模式, so that 我在需要历史兼容行为时仍有安全入口。
7. As an 普通用户, I want 创建后看到实际执行方式, so that 我能区分自己的意图和服务端最终决策。
8. As an 普通用户, I want 刷新或重新打开任务后仍看到同一实际执行方式, so that 页面状态不会改写冻结事实。
9. As an 普通用户, I want 查看历史修订时看到该修订自己的运行时, so that 后续默认切换不会改写历史。
10. As an 普通用户, I want 平台发生 P0 回退时默认任务自动进入 Legacy, so that 回归风险被失败关闭。
11. As an 普通用户, I want P0 回退不影响已冻结的 Pi 任务, so that 历史任务和既有 Delivery 保持不可变。
12. As an 普通用户, I want 平台恢复必须经过人工授权, so that 默认链路不会因一次健康恢复自动扩大。
13. As an 普通用户, I want 使用外部模型前看到连接名称和冻结模型, so that 我知道请求会发往哪个连接。
14. As an 普通用户, I want 使用外部模型前看到本任务外发内容范围, so that 我的确认只覆盖当前 TaskRevision。
15. As an 普通用户, I want 从 Pi 切换到 Legacy 后不再提交 Pi 专属连接和能力, so that 兼容任务不会携带无效或误导状态。
16. As an 普通用户, I want 平台默认最终回退到 Legacy 时不留下外发记录, so that 审计事实与真实执行一致。
17. As an 普通用户, I want 缺少合格连接时得到可修正提示, so that 系统不会猜测或代用未经我选择的 Provider。
18. As an 普通用户, I want 连接失效、无权访问或模型不可用时失败关闭, so that 跨 Owner 或过期连接不能被使用。
19. As an 普通用户, I want 重复点击创建不会产生两个任务, so that 慢响应不会造成重复执行或费用。
20. As an 普通用户, I want 相同请求的幂等指纹和实际 JSON 完全一致, so that 省略字段与显式选择不会被错误合并。
21. As an 普通用户, I want 相同幂等键配合不同运行时意图时被拒绝, so that 重复请求不会改变冻结语义。
22. As an 普通用户, I want 请求超时且结果未知时由系统先核对状态, so that 不会自动重试可能已经外发的请求。
23. As an 普通用户, I want 创建失败后保留安全的表单输入和选择, so that 我可以修正连接或授权而不必重新填写目标。
24. As an 普通用户, I want 可以取消正在执行的 Pi 或 Legacy 任务, so that 长任务仍处于用户控制之下。
25. As an 普通用户, I want 正式结果只来自通过独立验证和 QA 的 Delivery, so that Candidate 不会冒充正式交付。
26. As an 普通用户, I want 在完成后预览和下载正式 Delivery, so that 默认 vNext 的价值通过真实产品入口闭环。
27. As an 键盘用户, I want 用方向键在三个执行方式间移动并选择, so that 不依赖鼠标也能创建任务。
28. As an 屏幕阅读器用户, I want 听到分组名称、每项标签和选中状态, so that 三种请求意图可以被准确理解。
29. As an 窄屏用户, I want 三个选项和说明自然换行且不遮挡提交按钮, so that 小视口仍可完成任务。
30. As an 管理员, I want 在允许本地 Pi 时继续使用本地模型, so that 本任务不强迫管理员产生外发。
31. As an 平台维护者, I want API 不再公开误导性的 Legacy 客户端默认值, so that 新客户端不会复制错误行为。
32. As an 平台维护者, I want 路由仓库不可用时显式失败关闭到 Legacy 或拒绝, so that 缺失权威状态不会扩大到 Pi。
33. As an 平台维护者, I want 并发创建只冻结一个 RuntimeAssignment, so that 同一 TaskRevision 不会出现不同运行时。
34. As an 平台维护者, I want 路由、运行配置和任务创建位于同一事务边界, so that 中途异常不会留下半绑定记录。
35. As an 平台维护者, I want 锁超时和 Rollout 并发变化返回可理解错误, so that 客户端不会把不确定状态当成功。
36. As an 平台维护者, I want Owner 隔离同时在 API 和存储层成立, so that 用户不能读取或冻结他人的连接、任务或分配。
37. As an 审计人员, I want 运行时、连接版本、外发确认和 Provider Usage 与同一 TaskRevision 对齐, so that 执行链可追溯。
38. As an 产品验收人员, I want 从主工作台完成一条纯合成数据的正式 Delivery, so that 工程实现与真实普通用户闭环被分别证明。

## Implementation Decisions

### 产品与交互

- 目标用户是普通数据任务用户；页面的单一任务是安全创建任务并进入正式 Delivery 主流程。
- 保留现有工作台布局、颜色、字体、密度和“更多”高级设置结构，不新增视觉体系或全局 Token。
- 执行方式使用原生单选组；组标题使用“执行方式”，默认项为“平台默认（推荐）”。
- 显式选项使用“增强模式（Pi）”和“兼容模式（Legacy）”，同时保留产品语言与技术身份。
- 平台默认说明必须明确“按平台当前策略选择；安全回退时可能使用兼容模式”。
- 显式 Pi 说明必须明确“被阻断时失败，不会静默改用兼容模式”。
- 最终运行时以静态状态标签呈现，不做成可点击控件，也不只依赖颜色传达。
- 状态标签与任务状态、结果版本位于同一信息层级，不引入弹窗或新的页面导航。
- 三项控件具备原生键盘语义、可见焦点、label 点击区域、禁用和窄屏换行行为。
- 本任务不创建 DESIGN.md 或 UX-CONTRACT.md；它是单一既有工作流的局部修正，不改变持久视觉身份或跨屏行为契约。

### 请求与 API 契约

- 前端内部使用三态“平台默认 / Pi / Legacy”，API 仍只接受 Pi、Legacy 或字段省略。
- 平台默认的 JSON 中必须完全不存在 runtime_version；不发送 null、空字符串或前端猜测值。
- 显式 Pi 和显式 Legacy 精确发送对应值。
- 请求对象只构造一次，同一对象同时用于幂等 fingerprint 和实际 POST body。
- API 请求模型不再向 OpenAPI 暴露 Legacy 默认值。
- 服务端必须保留“字段省略”和“显式值”的区别，再把请求意图交给 RuntimeRouting。
- 路由仓库存在时，Rollout、P0 门、显式 Legacy、历史绑定和 Owner 是最终选择权威。
- 路由仓库不可用时不得猜测 Pi：平台默认和显式 Legacy 选择 Legacy；显式 Pi 返回服务不可用，
  且不创建任务、RuntimeAssignment、RuntimeConfig、连接冻结或外发记录。
- 创建响应、任务列表和任务详情都返回服务端冻结的 runtime_version。
- 前端最终显示只读取服务端字段，不读取本地选项推断结果。

### 模型连接、外发与能力

- 平台默认和显式 Pi 可以预选现有已验证连接；普通用户不得使用未验证、失效或无权访问的连接。
- 可能使用外部 Provider 时，界面继续展示连接、模型、外发类别和任务级确认。
- 只有最终 RuntimeAssignment 为 Pi 时，服务端才冻结连接版本、模型和 external_api_confirmed。
- 平台默认最终选择 Legacy 时，模型连接、连接版本、Pi 能力选择和外发确认不得落入 RuntimeAssignment 或任务审计事实。
- 显式 Legacy 不提交 Pi 专属能力；从 Pi 或平台默认切换到 Legacy 时清理不适用的前端选择。
- Capability Pack 仍只能冻结到通过现有治理门的 Pi TaskRevision；本任务不扩大普通用户能力受众。
- 不静默替换用户选定的连接、模型或 Provider。
- Provider 已收到请求而结果未知时，不自动重试；先持久化 Attempt，并把重复请求与费用风险交给用户决定。

### 路由、事务与历史

- RuntimeAssignment 继续按 Owner、Task、revision 不可变冻结。
- 默认切换只影响新 TaskRevision，不迁移或改写历史任务、历史 Delivery 或审计事件。
- 预览、RuntimeAssignment、RuntimeConfig 和任务创建保持现有原子提交边界。
- Rollout 在预览与提交之间变化时，创建失败并提示重试，不冻结半成品。
- 并发请求只允许一个等价 RuntimeAssignment；不同请求意图不能改写已冻结分配。
- P0 阻断时平台默认选择 Legacy，显式 Pi 返回明确冲突，显式 Legacy继续工作。
- 人工恢复沿用现有 Rollout 授权门，本任务不增加自动恢复。

### 开源与依赖

- 采用 Web 平台原生 radio、fieldset、legend 和 label。
- 复用仓库现有 Tailwind、主题 Token、cn 和现有按钮视觉语言。
- 不新增 Radix Radio Group、Toggle Group、聚合 Radix 包或表单库。
- Radix Radio Group 只作为未来确有跨屏自定义复合控件需求时的条件后备；届时必须独立展示依赖、锁文件、安全和包体差异。
- UI 控件库不承担请求省略、路由、Owner 隔离、外发确认、幂等或最终运行时回显等领域逻辑。

### 变更面约束

- 预计只触及任务创建器、工作台请求协调、任务运行时状态展示、API 输入模型与创建协调、现有路由/API 测试和现有浏览器测试。
- 不新增数据库表或迁移，不改变 RuntimeAssignment 数据结构。
- 不重构整个 Semantic Workspace，不拆分大型页面，不清理相邻历史代码。
- 精确文件允许列表在实施工单阶段根据本规格冻结；禁止覆盖或提交用户持有的 G1 评测文件。

## Testing Decisions

### 测试原则

- 测试外部可观察行为，不断言组件内部 state、私有函数或具体 CSS 实现。
- 使用最高可用接缝：API + SQLite 是业务正确性的主要接缝；Playwright 是用户体验主要接缝；
  RuntimeRouting 单元测试只保护路由状态机和并发不变量。
- 先写能够证明当前问题的失败测试，再做最小实现。
- 自动测试只证明 ENGINEERING_VERIFIED，不替代真实 Provider、真实用户或 Release 资格。

### API 与存储接缝

- OpenAPI 中 runtime_version 可省略且没有 Legacy 默认值。
- 字段省略 + vnext_default：创建成功并冻结 Pi。
- 显式 Pi + vnext_default：创建成功并冻结 Pi。
- 显式 Legacy + vnext_default：创建成功并冻结 Legacy。
- 字段省略 + P0 阻断：创建成功并冻结 Legacy。
- 显式 Pi + P0 阻断：409，且零任务、零 RuntimeAssignment、零 RuntimeConfig 残留。
- 显式 Legacy + P0 阻断：继续成功。
- 路由仓库不可用：平台默认和显式 Legacy 创建 Legacy；显式 Pi 返回 503，且零 Pi 侧残留。
- 普通用户缺少合格连接、连接失效、模型不可用、连接跨 Owner：分别失败关闭。
- 平台默认最终为 Pi：连接版本、模型和任务级外发确认正确冻结。
- 平台默认最终为 Legacy：连接、Pi 能力和外发事实均不冻结。
- 请求中 runtime_version 为 null、空字符串、未知值或额外字段：422。
- 相同幂等键 + 相同省略请求：返回同一任务，不重复冻结。
- 相同幂等键 + 省略/显式 Pi/显式 Legacy 的不同意图：冲突，不改写既有任务。
- 并发相同创建：一个不可变分配，重复请求不产生双任务或双外发。
- Rollout 并发变化、SQLite 锁超时、中途事务异常：失败关闭且零半成品。
- 创建后取消：Pi 与 Legacy 都保持现有取消、清理和提交点语义。
- P0 回退和人工恢复只影响新修订；历史 Pi、Legacy 和 Delivery 零改写。
- Owner A 无法读取、改写或复用 Owner B 的任务、连接与 RuntimeAssignment。

### 浏览器接缝

- 主工作台初始选中“平台默认（推荐）”。
- 三项属于同一原生 radio 组，legend/label 可访问，方向键和 label 点击可切换。
- 平台默认实际 POST body 和 fingerprint 都省略 runtime_version。
- 显式 Pi/Legacy 的 POST body 精确包含对应字段。
- 不发送 null，不因对象展开意外带回默认值。
- 平台默认/显式 Pi 显示适用的连接与外发确认；Legacy 不提交 Pi 专属字段。
- 在选项间往返切换后不残留不适用连接、确认或能力包。
- 缺少连接、外发未确认、403、409、422、503 和网络失败均保留可修正输入并显示可理解错误。
- 创建按钮阻止重复提交，busy 状态不改变按钮尺寸；相同尝试保持一个幂等键。
- 创建响应为 Pi 或 Legacy 时，任务标题区域显示对应“实际执行”标签。
- 刷新、切换历史修订后，标签继续显示服务端冻结值。
- P0 回退场景中，平台默认显示最终 Legacy，而不是前端预期 Pi。
- Chromium 定向 E2E 覆盖成功、失败、键盘、窄屏、浅色/深色和 axe 基线。
- 与风险相称时补 Firefox/WebKit；真实浏览器验收至少覆盖当前维护者支持的主浏览器。

### 基线与回归门

- 当前已确认的路由/API 定向基线为 53 passed、exit 0。
- 实现后先运行新增失败测试，再运行相关路由/API测试。
- 运行前端 TypeScript 检查和正式 Vite build。
- 运行定向 Playwright 与 axe 检查。
- 运行与风险相称的完整后端回归；根命令必须核对进程退出码和测试端口。
- 运行 frontend-design-premium 静态审计和适用检查，但审计报告不替代浏览器证据。
- 搜索变更代码中的非语义点击、原生弹窗、重复请求构造和未处理的不确定状态。
- 运行 Standards + Spec 双轴 code-review，阻断问题清零后才进入真实验收。
- 检查 UTF-8、无乱码、差异仅包含文件允许列表，并运行 diff whitespace 检查。

### 真实普通用户闭环

真实闭环是独立授权门，不随本规格自动执行：

1. 展示拟用 Provider、连接、冻结模型、纯合成输入、外发类别、潜在费用和结果未知处理；
2. 用户明确授权一条任务外发与生产任务写入；
3. 从 8088 主工作台选择平台默认并上传纯合成文件；
4. 验证 RuntimeAssignment 最终为 Pi，连接版本和外发确认正确冻结；
5. 完成 Pi → Candidate → 独立 Verifier → Delivery；
6. 从普通用户界面预览并下载正式 Delivery；
7. 核对 Provider Usage、取消/超时、数据库完整性、零异常和零历史改写；
8. 由用户明确给出 LIVE_ACCEPTED 或提出整改。

## Out of Scope

- 不删除 Legacy，不移除显式 Legacy 入口。
- 不迁移或改写历史任务、历史 RuntimeAssignment、既有 Delivery 或审计事件。
- 不扩大平台能力到普通用户，不改变 admin_gray 能力受众。
- 不新增 Provider、模型类型、远程 MCP、多媒体或数据来源。
- 不重构整个 Semantic Workspace、Store、Worker 或前端页面架构。
- 不新增数据库表、迁移或启动时 DDL。
- 不引入新的 UI、表单、状态管理或运行时路由依赖。
- 不修复与 P0-01 无直接关系的依赖告警、Secret、CI 或数据库迁移体系。
- 不运行真实业务数据，不读取、复制、轮换或持久化生产 Secret。
- 不自动重试结果未知的外部请求。
- 不创建 GitHub Issue、分支、提交、推送、PR、标签、Release 或部署。
- 不把测试绿色表述为用户验收、Provider 资格、生产服务器资格或发布完成。

## Further Notes

### 已验证事实

- 规格前现场核验时，main、HEAD 与 origin/main 一致，GitHub Open Issues 为 0。
- 8088 健康与就绪通过。
- 生产数据库只读核验为 vnext_default、p0_blocked=false、RuntimeAssignment 数量为 0，
  SQLite 完整性通过且无外键违规。
- 当前 OpenAPI 仍公开 runtime_version 默认值 Legacy。
- 当前前端仍以 Legacy 为初值并总是发送 runtime_version。
- 当前服务端已经能够区分字段省略并让 RuntimeRouting 决定最终运行时。
- 当前相关路由/API 测试在项目 Python 3.13 下为 53 passed、exit 0；两个现存依赖告警未阻断测试。

### 基于代码的推断

- P0-01 的主要实现量是移除前端显式 Legacy 默认、收窄 API 默认契约，并把服务端已有最终事实
  显示出来；不需要新路由框架或新数据库结构。
- 平台默认是一种请求意图，不是第三个 RuntimeVersion。
- 现有 API + SQLite 和 Playwright 测试已提供足够高的接缝，不需要为本任务引入组件测试框架。
- 原生 radio 比新增 Radix Radio Group 更符合本次单一三选一控件的收益/风险比例。

### 尚未验证的建议

- 三项最终文案、宽度和窄屏排列需要在实现后通过真实浏览器截图与键盘检查确认。
- frontend-design-premium 的严格静态审计可能暴露仓库既有问题；只修复本次变更直接造成或
  阻断本接缝的问题，既有无关问题单独报告。
- 全量回归范围和运行时间在实施工单阶段根据实际差异确定。
- 真实 Provider、模型、纯合成样例和可接受费用尚未选择。

### 后续人工决策与授权门

1. 用户确认本规格后，才能进入 to-tickets；默认先生成本地实施工单草案。
2. 是否创建 GitHub Issue 和使用 ready-for-agent 标签，需要独立授权。
3. 是否创建分支，需要独立授权。
4. 新增第三方依赖若后来变得必要，需要展示精确依赖与锁文件差异后确认。
5. 真实 Provider 外发、连接、模型、纯合成输入和费用需要独立确认。
6. 生产数据库写入和真实普通用户任务需要独立确认。
7. 提交、推送、PR、合并、状态更新、标签、Release 和部署分别需要明确授权。

### 完成定义

- **SPEC_APPROVED**：用户确认本规格和未决授权门。
- **IMPLEMENTED**：约定代码存在，未声称测试或真实验收。
- **ENGINEERING_VERIFIED**：定向与相称回归、构建、浏览器检查和双轴审查通过。
- **LIVE_ACCEPTED**：用户授权并完成一条纯合成普通用户正式 Delivery 闭环。
- **RELEASED**：经独立授权完成相应提交、合并、发布或部署；本规格不自动授权。
