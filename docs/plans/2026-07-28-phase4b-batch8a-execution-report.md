# Phase 4B 批次 8A 执行报告

> 日期：2026-07-28
>
> 状态：**主流程框架代码、自动化工程门禁和用户验收均已完成**
>
> 分支：`v0.0.6`
>
> 基线：`554acd6`
>
> 功能与文档提交：`44d19faf`
>
> 版本边界：未创建或移动标签，Phase 4B 未封板

## 1. 本批结论

批次 8A 已把统一工作台的核心框架补成可安全失败、可诊断、可追溯的本机闭环：

```text
提交 → 编译 → 读取 → 绑定 → 执行 → 验证 → 正式交付
```

本批修复了两个真实主流程问题：

1. 结构化 Compiler 不再继承聊天任务的长思考预算，固定关闭思考，单次模型调用
   120 秒、输出 8192 tokens，Instructor 内部不重试，修复只由外层有界预算控制。
2. `requested_output_formats` 已与输入格式契约分离；UI 选择的 TXT/XLSX 等正式格式
   由服务端可信契约覆盖模型草稿，不再出现“要求 TXT，实际持久化 DOCX/PDF”。

同时，可信章节/页码范围会覆盖模型错误的 `whole_document=true`。这只消除模型对已明确
用户范围的扩张，不推断新的范围、字段或结果含义。

## 2. 已完成能力

### 2.1 普通用户失败说明

工作台持久化以下字段，并在任务详情中向任务所有者展示：

- 错误编号、失败阶段和原因；
- 尝试次数和耗时；
- 是否已读取来源；
- 是否生成中间结果；
- 是否发布正式交付；
- 可执行的下一步；
- 诊断引用。

语义编译失败、来源损坏、执行失败和正式文件转换失败不再统一显示成模糊的“运行失败”。
转换器崩溃时固定不发布下载。

### 2.2 生命周期与边界

- `needs_input` 与 `failed` 可机械区分；
- 文档与表格混合输入返回明确拒绝，不只处理一部分；
- Prompt 与页面输出格式冲突在提交前返回明确说明；
- 取消后不发布新 Delivery；
- 相同 run 重复请求复用同一 manifest；
- 跨用户任务、事件和下载继续按所有者隔离；
- 工作台聚合记录清理后保留不可恢复的审计墓碑。

审计墓碑只保存用户/任务标识、目标哈希、来源/结果哈希、格式、终态、错误编号和时间，
不保存正文、文件名或绝对路径。

### 2.3 表格与正式交付纠偏

四类真实文件闭环暴露并修复：

- 无扩展名上传文件导致 OpenPyXL 拒绝打开；
- 聚合派生列被错误绑定成源字段；
- CSV 数字字符串导致 DuckDB 对 `VARCHAR` 求和失败；
- 正式表格泄露内部 `__mg_` 记录列。

修复后 DOCX、PDF、XLSX、CSV 四条路径均由真实 HTTP API 和后台 Worker 执行，下载 ZIP
后分别使用 `python-docx`、`pypdf`、`openpyxl`、CSV/JSON 标准读取器重新打开并机械比对。

### 2.4 低敏观测

- 新增 `workspace.task` 根 span；
- 新增 compile、inspect、bind、execute、verify、publish 阶段 span；
- 只记录任务哈希、阶段、来源类型/数量、格式、模型、状态和错误编号；
- OTLP 配置失败时业务 fail-open；
- Phoenix 固定为 `arizephoenix/phoenix:19.10.0`，仅绑定本机端口；
- Phoenix 关闭自身遥测、外部资源和外部 AI Provider，默认保留 30 天。

实际本机证据：

- 镜像 ID：`sha256:3092f5543a3ddd35db7390cf971027c33be6be1f171274d57f3c8658c2193d67`；
- `http://127.0.0.1:6006/` 返回 HTTP 200；
- Mangrove 发送根/阶段 span 后，Phoenix 两次返回 `POST /v1/traces 200`。

Phoenix 19.10.0 启动时会记录可选 Monty 沙箱二进制缺失，但随后 Web UI、REST 和 OTLP
均正常启动；批次 8A 不使用 Monty 代码执行能力。

### 2.5 Promptfoo 小型 PoC

Promptfoo 固定为 `0.121.19`，位于 `evals/promptfoo-batch8a/`，使用本地 Python
Provider，运行参数包含 `--no-cache --no-share`。本次一次真实运行 6/6 通过：

- 四个成功场景契约；
- 本地模型截断失败关闭；
- 文档与表格混合来源拒绝。

它当前只是工具接线 PoC，不替代 pytest，也没有冒充真实模型/真实文件验收。连续三次
关闭缓存和分享的运行均为 6/6；由于 provider 只验证评测接线和场景契约，本批仍不把
Promptfoo 升级为核心强制门禁。

## 3. 真实本地模型证据

端点 `http://192.168.1.20:6012/v1` 返回模型 `Qwen3.6-35B-A3B`。

任务：

> 从整份 Word 中识别并汇总商务条款，只输出相关条款，不要输出全文，交付 TXT。

最终结果：

- 状态：`ready`；
- 耗时：8.35 秒；
- 外层修复：1 次；
- `whole_document=false`；
- `section_patterns=["商务条款"]`；
- `delivery.formats=["txt"]`。

这证明真实模型主编译路径已从先前约 15 分钟截断，收敛为有界调用并正确保留范围和格式。
它不等于用户已在页面完成整条真实任务。

## 4. 自动化验证

最终结果：

- 全仓后端：963 passed、4 skipped、0 failed、4 warnings；
- 前端 TypeScript 与 Vite 生产构建：通过；
- 完整 PC Playwright：37 passed；
- Promptfoo：连续三次均为 6 passed、0 failed、0 errors；
- Phoenix：Web HTTP 200，OTLP 两次 POST 200；
- 锁定 OTel 1.39.1 + Hypothesis 隔离门：3 passed；
- `py_compile`、`git diff --check`、UTF-8 与 Markdown 相对链接检查：通过。

4 个 skip 均为仓库既有的显式重型门：两个数据库 live 容器用例和两个大规模性能用例，
不是批次 8A 功能测试被跳过。

自动化 Playwright 使用可控 API 路由证明 PC 页面交互与状态渲染；真实文件内容正确性由
后端公开 API 闭环证明。二者本身不冒充“用户验收”；用户后续已按单独说明完成并明确
确认批次 8A 验收通过。

## 5. 未完成与已知限制

- 用户页面验收已完成；
- 批次 7 的非阻塞 UX 细节继续保留在延期台账；
- 文档+表格复合执行尚未开发，本批只做明确拒绝；
- 网页/API/数据库仅保留既有能力和契约，不计入四类闭环；
- Promptfoo 已完成三次稳定 PoC，但未升级为核心强制门禁；
- Phoenix Monty 可选能力不可用，不影响追踪；
- 主解释器 `pip check` 仍有两个既存问题：缺少 `types-pytz`，以及
  `crawl4ai 0.9.0` 与 `lxml 6.1.1` 约束不一致；
- 8B 的服务器部署、10–20 用户压力、更多故障注入和封板审计未获开工授权。

## 6. 用户验收结论

用户已按
[`批次 8A 用户验收说明`](2026-07-28-phase4b-batch8a-user-acceptance.md)
完成验收并明确确认通过。该结论只覆盖批次 8A 已声明的主流程范围，不把未知需求、8B、
服务器部署或 Phase 4B 封板自动纳入。

## 7. 后记：2026-07-29 新架构专项

后续真实“PDF 附件 2 表格只输出 CSV”任务暴露来源前置计划和文档/表格互斥路由的结构性
缺陷，并出现没有真实操作选项的确认弹窗。这不是对批次 8A 验收事实的改写，而是批次 8A
明确拒绝的复合能力边界在新场景中的实际失败。

用户已批准建立 Agentic Runtime vNext 双轨专项。阶段 1 三路线可抛弃赛马后，Pi、
OpenCode、Deep Agents 分别通过 16/18、16/18、12/18，均未直接通过生产硬门。用户随后
批准并授权完整 Pi RPC + 任务级 Docker 的全能力生产灰度纵切面；OpenCode 保留后备，
原 LangChain/LangGraph fallback 不再执行。当前尚未取得生产资格，且不创建
`v0.0.6` 标签。实施入口见
[`Pi 全能力生产灰度计划`](2026-07-29-agentic-runtime-vnext-pi-full-capability-gray-plan.md)。
