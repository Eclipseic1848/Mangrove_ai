# P0-01 运行策略选择器开源适配研究

> 检索日期：2026-08-23
>
> 范围：普通用户新任务的三态运行策略选择（平台默认 / 显式 Pi / 显式 Legacy）、默认时省略
> `runtime_version`、任务创建后展示最终运行时
>
> 资料边界：本地仓库实况、WHATWG HTML Standard、W3C/WAI、Radix 官方文档、Radix 官方
> GitHub 仓库和 npm 官方 Registry
>
> 本文状态：只读研究结论；未安装依赖、未修改实现、未运行产品验收

## 1. 结论

**不存在一个 GitHub/npm UI 工具能够端到端完美适配 P0-01。**控件库最多解决“互斥选择、
键盘操作、可访问性语义和样式接缝”，不能替代 Mangrove 自己的请求省略语义、运行时路由、
Owner 隔离、外发确认、幂等指纹、失败关闭和最终运行时回显。

本任务的最小推荐组合是：

1. **REUSE：浏览器原生 `input[type="radio"]` + `fieldset` + `legend` + `label`。**三项互斥
   选择正是原生 radio 的语义；无需新增 npm 依赖、无需自己实现 roving tabindex，也不改锁文件。
2. **REUSE：仓库现有 Tailwind、`cn` 和按钮视觉语言。**只复用视觉样式，不复用当前两个
   `aria-pressed` 按钮的语义。
3. **REJECT（本次不新增）：`@radix-ui/react-radio-group`。**它成熟、MIT、React 18 兼容，且
   完整实现 Radio Group 键盘模式；但仓库尚未安装它，为三个静态选项新增包和传递依赖没有带来
   足以抵消锁文件与供应链增量的收益。只有前端设计明确要求脱离原生 radio 的自定义复合控件行为
   时，才把它作为后备方案提交人工确认。
4. **REJECT：`@radix-ui/react-toggle-group`。**`type="single"` 仍是可按下/取消的 Toggle 语义，
   官方示例需要额外受控逻辑才能保证始终有值；它不如 Radio Group 准确表达“三选一且始终有一项”。
5. **REJECT：`radix-ui` 聚合包。**仓库当前采用逐个安装 Radix 原语的方式；聚合包声明全部
   Primitives 为依赖，即使可 tree-shake，也会显著扩大依赖图和锁文件范围。

因此，本次没有“已有成熟 npm 工具完美适配，所以必须引入”的结论。推荐复用成熟 Web 平台原语，
只实现 Mangrove 特有的薄映射。

## 2. 判断口径

- **已验证事实**：由当前代码、锁文件、官方文档、官方仓库或正式规范直接支持。
- **基于代码的推断**：把规范和组件能力映射到 P0-01 当前接缝后得到的实现约束。
- **尚未验证的建议**：需要后续前端设计、依赖安装预演、构建或浏览器/辅助技术验收确认。

## 3. 当前仓库实况

### 3.1 已验证事实

1. `frontend/package.json` 已使用 React `^18.3.1`、Tailwind `^3.4.17`、`clsx`、
   `tailwind-merge`，并逐个安装了 Radix Alert Dialog、Collapsible、Dialog、Tooltip；没有
   `@radix-ui/react-radio-group`、`@radix-ui/react-toggle-group` 或 `radix-ui`。
2. `frontend/package-lock.json` 是 lockfile v3。当前根依赖和锁文件都没有 Radio Group / Toggle
   Group。Radio Group 最新包所需的 `@radix-ui/react-direction`、
   `@radix-ui/react-roving-focus` 当前也不存在；部分共享依赖虽已存在，但版本不全相同。
3. `TaskComposer.tsx:261-277` 把提交值声明为 `runtimeVersion: "legacy" | "pi"`；
   `TaskComposer.tsx:294-295` 默认固定为 `legacy`。
4. `TaskComposer.tsx:741-777` 当前用两个带 `aria-pressed` 的普通按钮表示“稳定模式 / Mangrove
   增强灰度”，没有“平台默认”第三项，也没有 `radiogroup` / 原生 radio 语义。
5. `TaskComposer.tsx:526-555` 把运行时选择连同模型连接、外发确认和能力包选择一起提交；这些
   字段之间已有 Mangrove 特有的约束，不能交给通用控件库解释。
6. `SemanticWorkspacePage.tsx:466-496` 的提交类型同样只有 `legacy | pi`，并且构造请求时总是写入
   `runtime_version`。所以当前 UI 无法表达“让平台决定”。
7. `semanticWorkspaceApi.ts:50-65` 已把请求字段声明为可选
   `runtime_version?: "legacy" | "pi"`；前端 API 接缝本身允许省略。
8. 后端 `semantic_workspace.py:250-268` 的 Pydantic 模型仍以 Legacy 作为字段默认值，但
   `semantic_workspace.py:964-985` 通过 `model_fields_set` 区分“客户端显式传入”和“字段省略”，
   省略时把 `requested_runtime=None` 交给 `RuntimeRouting`。
9. `runtime_routing/service.py:152-178` 在 `vnext_default` 下仍尊重显式 Legacy，并让没有 Legacy
   历史绑定的新任务进入 Pi；历史 Legacy 任务修订继续冻结为 Legacy。
10. 创建接口在 `semantic_workspace.py:1343-1353` 返回解析后的 `runtime_version`；读取任务时
    `semantic_workspace.py:934-941` 也以冻结的 Agentic Runtime 覆盖任务投影。前端类型
    `semanticWorkspace.ts:246-248` 已接收该字段，但当前页面没有把它呈现为“最终运行时”。
11. `tests/test_pi_runtime_workspace_api.py:490-596` 已有后端证据：省略 `runtime_version` 时，
    `vnext_default` 可返回 `pi`；P0 阻断后同样的省略请求返回 `legacy`；已创建 Pi 任务不会被改写。

### 3.2 基于代码的推断

“平台默认”不是第三种后端 RuntimeVersion，而是**一种请求意图**。前端本地选择状态应区分：

| UI 选择 | 请求行为 | 最终显示事实源 |
|---|---|---|
| 平台默认 | 完全省略 `runtime_version` | 创建/读取接口返回的 `runtime_version` |
| 显式 Pi | 发送 `runtime_version: "pi"` | 接口返回的 `runtime_version`，且拒绝/阻断状态不得伪装成功 |
| 显式 Legacy | 发送 `runtime_version: "legacy"` | 接口返回的 `runtime_version` |

本地可用单独的 UI 类型（例如 `"platform_default" | "pi" | "legacy"`）表达三态，但 API 类型仍应
保持 `"legacy" | "pi" | undefined`。请求对象、幂等 fingerprint 和实际 POST body 必须由同一份
条件映射产生，避免“指纹包含默认字段、网络请求却省略”或相反的漂移。

最终运行时必须读取服务端回包/任务读取投影，不能把用户点击的“平台默认”直接显示成 Pi；
Rollout 门、P0 阻断和历史绑定都可能使平台最终解析为 Legacy。

## 4. 候选对比

| 候选 | 功能契合 | 可访问性 | 依赖/锁文件 | 维护/许可证 | 包体/供应链 | 卸载恢复 | 决策 |
|---|---|---|---|---|---|---|---|
| 原生 radio + fieldset/legend | 三项互斥、始终有默认项，直接匹配 | 浏览器原生语义；可用 label、fieldset、legend | 零新增 | Web 标准；无第三方许可证 | 零 npm 包、最小供应链面 | 删除局部 JSX/CSS 即可 | **REUSE** |
| 当前两个 `aria-pressed` 按钮 | 只有两项，且是独立按压按钮语义 | 不形成 radio group；需自行补角色、状态、焦点规则 | 零新增 | 仓库自有 | 零新增，但维护键盘语义成本高 | 容易 | **语义 REJECT，样式 REUSE** |
| Radix Radio Group `1.4.7` | 单选、受控/非受控、横纵方向，UI 层匹配 | 官方声明遵循 WAI-ARIA Radio Group，并实现 roving tabindex | 新增直接包和传递依赖；精确 diff 未预演 | 活跃官方仓库，MIT，React 18 兼容 | npm unpacked size 117,298 B；9 项直接运行依赖 | 移除 import、依赖并重锁即可 | **本次 REJECT；条件后备** |
| Radix Toggle Group `1.1.19` | 支持 single，但允许取消为无值 | Toggle/pressed 语义，不是三选一 radio 语义 | 新增直接包、Toggle 及共享依赖 | 同仓库，MIT，React 18 兼容 | npm unpacked size 56,319 B；7 项直接运行依赖 | 可移除，但仍需重锁 | **REJECT** |
| `radix-ui` 聚合包 `1.6.7` | 包含 Radio/Toggle 及全部原语 | 各原语能力不变 | 声明全部 Radix Primitives 为依赖，与当前逐包风格不一致 | 同仓库，MIT | npm 元数据列出大范围依赖；虽可 tree-shake，锁文件仍扩张 | 移除聚合包并恢复逐包 import，成本较高 | **REJECT** |

上述 npm 版本、许可证、unpacked size、依赖和 peer dependencies 通过 npm 官方 Registry 的
`npm view` 于 2026-08-23 只读检索；unpacked size 是发布包解包尺寸，**不是** Vite 最终 gzip/brotli
产物大小。

## 5. 原生 radio：推荐复用

### 5.1 已验证事实

- WHATWG HTML Standard 规定，同一 form/tree 中、`name` 相同且非空的 radio 形成一组，同组只能
  有一个 checked；某项被选中时，其他项会被取消选中：
  [HTML Standard：Radio Button state](https://html.spec.whatwg.org/multipage/input.html#radio-button-state-(type=radio))
  （检索：2026-08-23）。
- W3C/WAI 建议用 `fieldset` 关联一组 radio、用 `legend` 标识组、用 `label` 标识各选项：
  [WAI Grouping Controls](https://www.w3.org/WAI/tutorials/forms/grouping/)
  （检索：2026-08-23）。
- WAI 的命名实践明确建议优先使用原生 HTML 技术；原生 label 还会把可点击区域扩展到标签：
  [WAI Providing Accessible Names and Descriptions](https://www.w3.org/WAI/ARIA/apg/practices/names-and-descriptions/)
  （检索：2026-08-23）。

### 5.2 对 P0-01 的适配结论

- 功能契合：三态只是一个有默认值的单选字段，原生 radio 已提供互斥和 change 事件。
- 可访问性：无需手写 `role="radiogroup"`、`role="radio"`、`aria-checked` 或 roving tabindex；
  仍需可见且简洁的 legend、每项 label 和清晰的 `:focus-visible`。
- 依赖/锁文件：不改 `package.json` / `package-lock.json`。
- 维护/许可证：行为来自 Web 标准和浏览器，不引入第三方许可证或升级节奏。
- 包体/供应链：没有新的 JavaScript 包、安装脚本或 Registry 获取物。
- 恢复性：回退仅涉及局部 JSX/样式和请求映射；不需要依赖卸载或重锁。

### 5.3 尚未验证的设计点

原生 input 可以视觉隐藏并由 label 呈现为三张分段卡片，但必须保留可聚焦控件和可见焦点环。
具体布局、文案、默认项强调方式及窄屏折行应由 frontend-design 产物决定，不能由本研究替用户
冻结产品文案。

## 6. Radix Radio Group：成熟但本次不新增

### 6.1 已验证事实

- 官方文档定义它为“最多一个被选中”的 Radio Group，支持完整键盘导航、横/纵方向、受控与
  非受控模式；在 form 内会渲染隐藏 input：
  [Radix Radio Group](https://www.radix-ui.com/primitives/docs/components/radio-group)
  （检索：2026-08-23）。
- 官方说明其遵循 WAI-ARIA Radio Group 模式并使用 roving tabindex。W3C 模式要求组有
  `radiogroup`、项有 `radio`、项维护 `aria-checked`，方向键移动并选择：
  [W3C Radio Group Pattern](https://www.w3.org/WAI/ARIA/apg/patterns/radio/)
  （检索：2026-08-23）。
- npm 官方 Registry 于 2026-08-23 返回稳定版 `1.4.7`、MIT、unpacked size 117,298 B、
  `sideEffects: false`、React/ReactDOM peer 范围覆盖 18，并列出 9 项直接运行依赖：
  [npm 包页](https://www.npmjs.com/package/@radix-ui/react-radio-group/v/1.4.7)、
  [npm Registry 元数据](https://registry.npmjs.org/@radix-ui%2freact-radio-group/latest)
  （检索：2026-08-23）。
- Radix 官方仓库未归档，由 WorkOS 维护，2026-08-08 仍有 push；仓库和包均为 MIT：
  [Radix Primitives 官方仓库](https://github.com/radix-ui/primitives)、
  [官方 LICENSE](https://github.com/radix-ui/primitives/blob/main/LICENSE)、
  [GitHub API 仓库元数据](https://api.github.com/repos/radix-ui/primitives)
  （检索：2026-08-23）。

### 6.2 适配判断

它能很好解决 UI 单选和键盘行为，但不能解决 P0-01 的关键差异：Radix 的 `value` 始终是字符串，
而“平台默认”需要在提交时映射为**字段不存在**；最终显示还必须读取后端解析结果。因此即使采用
Radix，Mangrove 仍需编写相同的领域映射和验收测试。

当前仓库没有 Radio Group、Direction、Roving Focus，部分共享 Radix 包版本也低于该包固定依赖。
新增它一定会改 `package.json` 和 lockfile；由于本轮未获依赖安装授权，没有生成精确锁文件 diff。
对于只出现一次、只有三项的选择器，这个增量不优于原生 radio。

若 frontend-design 后续证明必须采用自定义复合控件，建议只安装固定的
`@radix-ui/react-radio-group`，不要改用 `radix-ui` 聚合包；安装前单独展示 package/lock diff、
Registry integrity、`npm audit` 结果和构建体积差异，交由用户确认。

## 7. Radix Toggle Group：拒绝

### 7.1 已验证事实

- 官方把 Toggle Group 定义为一组可开/关的 two-state buttons；它支持 single/multiple，但
  `Space` / `Enter` 都会激活或取消当前项：
  [Radix Toggle Group](https://www.radix-ui.com/primitives/docs/components/toggle-group)
  （检索：2026-08-23）。
- 官方专门给出“Ensuring there is always a value”示例：`type="single"` 时仍需受控状态并忽略空值，
  才能保证始终有选择。这说明“始终三选一”不是它的默认语义：
  [Radix Toggle Group：Ensuring there is always a value](https://www.radix-ui.com/primitives/docs/components/toggle-group#ensuring-there-is-always-a-value)
  （检索：2026-08-23）。
- npm 官方 Registry 于 2026-08-23 返回 `1.1.19`、MIT、unpacked size 56,319 B、
  `sideEffects: false`、React/ReactDOM peer 范围覆盖 18，并列出 7 项直接运行依赖：
  [npm 包页](https://www.npmjs.com/package/@radix-ui/react-toggle-group/v/1.1.19)、
  [npm Registry 元数据](https://registry.npmjs.org/@radix-ui%2freact-toggle-group/latest)
  （检索：2026-08-23）。

### 7.2 拒绝理由

P0-01 是“从三种请求意图中选择且始终有一项”，不是“分别切换三个命令的按压状态”。使用
Toggle Group 会要求本地再实现“禁止清空”，同时给辅助技术暴露 pressed/toggle 语义。它既增加
依赖，又比 Radio Group 更不准确，因此拒绝。

## 8. UI 控件库不能替代的 Mangrove 领域逻辑

无论采用原生 radio 还是 Radix，以下逻辑必须留在 Mangrove 自己的 API/路由层并测试：

1. **请求省略语义**：平台默认必须让 JSON 中不存在 `runtime_version`，不能发送 `null`、空串或
   前端猜测的 `pi`。
2. **路由与冻结**：最终 runtime 由当前 Rollout、P0 门、显式 Legacy、历史任务绑定和 Owner
   共同决定；控件值不是路由事实。
3. **外发确认**：只有最终/显式 Pi 路径才可能关联模型连接和外发确认；当前 TaskComposer 已有
   连接、模型和 `external_api_confirmed` 约束，不能让切换控件绕过或残留错误状态。
4. **能力与权限**：Capability Pack 只允许冻结到合格的 vNext TaskRevision，权限和 digest 门必须
   由后端失败关闭。
5. **幂等**：省略或显式字段会改变请求语义，实际 POST body 与 idempotency fingerprint 必须一致。
6. **失败显示**：显式 Pi 可能返回 403/409/503；UI 必须显示拒绝原因，不能自动伪装为平台默认或
   Legacy 成功。
7. **最终运行时展示**：创建成功与刷新后都使用服务端 `runtime_version`；前端初始选择只用于解释
   用户意图。

## 9. 后续验证建议（尚未执行）

进入实现阶段后，最小证据矩阵应包含：

| 层级 | 必须验证 |
|---|---|
| 组件/可访问性 | 三个 radio 有同组 name、可见 legend/label、默认选中“平台默认”、键盘切换和焦点可见；axe 无新增违规 |
| 请求契约 | 平台默认的实际 JSON 与 fingerprint 都省略字段；显式 Pi/Legacy 精确发送对应值；不发送 `null` |
| 状态联动 | 从 Pi 切回平台默认/Legacy 后，不残留不适用的连接、外发确认或能力包提交 |
| API 集成 | `vnext_default` 省略请求返回 Pi；P0 阻断/回退返回 Legacy；显式 Legacy 始终可辨认；显式 Pi 拒绝如实显示 |
| 最终回显 | 创建回包、任务刷新和历史任务都显示服务端冻结的最终 runtime，而非 UI 预期值 |
| 工程门 | TypeScript、Vite build、定向 Playwright、相关后端路由测试、`git diff --check` |

这些自动化结果只证明工程回归，不代表真实普通用户验收、Provider 外发资格或生产发布。

## 10. 未验证事项

- 本研究子任务不负责 frontend-design；主流程已另行加载相关设计技能，但在测试接缝经用户确认前，
  仍未冻结三项文案、信息层级、窄屏布局、颜色和 Legacy 入口显著度。
- 未在 Chromium/Firefox/WebKit 及 NVDA/VoiceOver 上运行真实键盘与读屏验收。
- 未安装 Radix Radio/Toggle，也未生成精确 package-lock diff、`npm audit`、Vite chunk 或 gzip/brotli
  体积差异；npm unpacked size 不能替代这些证据。
- 本研究子任务未读取或变更真实生产 Rollout/数据库；主流程的只读现场核验另行记录，本文不把
  代码支持的 `vnext_default` 行为单独误述为生产验证。
- 本研究子任务未运行前端构建、Playwright 或后端测试；主流程另有定向基线测试证据，本文仍只
  是规格阶段的研究输入，不是实现证据。

## 11. 官方来源索引

所有来源检索日期均为 2026-08-23：

- [WHATWG HTML Standard：Radio Button state](https://html.spec.whatwg.org/multipage/input.html#radio-button-state-(type=radio))
- [W3C/WAI：Grouping Controls](https://www.w3.org/WAI/tutorials/forms/grouping/)
- [W3C/WAI：Providing Accessible Names and Descriptions](https://www.w3.org/WAI/ARIA/apg/practices/names-and-descriptions/)
- [W3C/WAI：Radio Group Pattern](https://www.w3.org/WAI/ARIA/apg/patterns/radio/)
- [Radix：Radio Group](https://www.radix-ui.com/primitives/docs/components/radio-group)
- [Radix：Toggle Group](https://www.radix-ui.com/primitives/docs/components/toggle-group)
- [Radix：Introduction / incremental adoption](https://www.radix-ui.com/primitives/docs/overview/introduction)
- [Radix 官方 GitHub 仓库](https://github.com/radix-ui/primitives)
- [Radix 官方 MIT LICENSE](https://github.com/radix-ui/primitives/blob/main/LICENSE)
- [npm：`@radix-ui/react-radio-group@1.4.7`](https://www.npmjs.com/package/@radix-ui/react-radio-group/v/1.4.7)
- [npm：`@radix-ui/react-toggle-group@1.1.19`](https://www.npmjs.com/package/@radix-ui/react-toggle-group/v/1.1.19)
- [npm：`radix-ui@1.6.7`](https://www.npmjs.com/package/radix-ui/v/1.6.7)
