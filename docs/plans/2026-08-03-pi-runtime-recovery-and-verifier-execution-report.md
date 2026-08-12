# Pi Runtime 恢复与候选验证纠偏执行报告

> 2026-08-06 后续修订：覆盖契约已支持“第 N 个对象”的 `ordinal + result_ordinal`，
> Broker 语义验证空/无效响应会有界重试，且工作台可只重新验证 `inconclusive` Candidate，
> 无需重跑 OCR、检索或候选生成。最新边界与验证证据见
> `2026-08-06-v008-runtime-reliability-and-candidate-retry-closeout.md`。下文保留 2026-08-03
> 当时的真实恢复证据，不再用其中“仍只是 Candidate”的表述判断当前 Publisher 能力。

> 日期：2026-08-03
> 状态：工程验证通过；真实任务形成已验证候选，未发布正式 Delivery
> 真实任务：`workspace_9356efae7a064330`，revision `1`，Run
> `pi_run_89d85aa3c8314541`

## 1. 本轮目标

修复真实 Pi 文档任务在运行进度、服务热恢复、候选证据清单和独立语义验证上的连续故障，
恢复同一 Run 并形成可下载、可重开、来源可核对的候选。不得绕过覆盖门、独立 Verifier 或
候选/正式交付边界。

## 2. 已验证事实

1. 运行中界面曾把通用 `agent.started` 和工具事件错误投影为“处理数据”，而“理解要求”等
   前序阶段仍显示尚未开始；可恢复的单次工具失败也会永久污染阶段状态。
2. 开发热更新中断 Run 后，旧 Smokescreen 容器和确定性内部网络仍存在；恢复同一 Run
   再次创建相同网络，Docker 返回 `network ... already exists`。
3. 覆盖账本的停止提议只声明第 1 页，候选 Manifest 也引用第 1 页，但 Manifest 使用系统
   Prompt 要求的规范 `upload_id`；完成门只接受原文件名，错误地把证据页集合解析为空。
4. 原独立 Verifier 对 PDF 只重读文本层。109 页扫描 PDF 没有可靠文本层，即使当前 Run
   已通过 MinerU 权威读取，Verifier 仍会误报“来源中找不到声明的证据”。
5. DeepSeek V4 Flash（0731）曾把 `missing_requirements` 的单条内容返回为字符串，随后又
   出现一次空 JSON 响应。原验证器不做结构规范化且 `max_retries=0`，把 Provider 格式波动
   错误转交给 Pi 修改候选。
6. 修复后，同一真实任务进入 `candidate_ready`：候选 JSON 1 个、1750 字节、SHA-256
   `b97f569cbe2ce45e004f77580a99fba9f7d40006a331a461c4779c48871e11c0`。
   独立验证报告为 `passed`，文件集合、数量、19 条原件证据和目标语义四项均通过。
7. 任务终态后，任务级 Pi 容器、Smokescreen 代理容器和内部网络均已清理。

## 3. 实施改动

### 3.1 进度投影

- Pi 启动先发出 `goal_interpretation`；冻结覆盖前的来源观察仍归入理解阶段。
- 通用 Runtime/工具事件只作为动作进度或可恢复警告，不再擅自完成或失败整个业务阶段。
- 同一阶段后续成功会覆盖之前的可恢复失败，保证最多一个活动阶段。

### 3.2 同 Run 基础设施恢复

- Smokescreen 资源身份继续由 Owner、Task、revision、Run 和 phase 确定性生成。
- 仅 `resume` 可使用 `replace_existing=True`，先撤销同一确定性身份的旧代理和网络，再创建
  新资源；普通新启动仍保持“资源已存在即失败关闭”。
- Runtime 恢复时清除同一 Run 的陈旧 `failure_json`；恢复必须由 Web 服务进程接管，使
  Runtime 与内部文档 Relay 共享同一进程内 Grant 域。

### 3.3 Manifest 与扫描 PDF 独立验证

- PDF Manifest 同时识别不可变 `upload_id` 和兼容旧清单的原文件名，统一映射到同一
  ContentUnit 身份。
- 独立 Verifier 先重开原件文本层；文本层无法逐字核对时，只能在当前 Owner/Task/
  revision/Run Grant 内重读覆盖账本已经权威读取的页面。
- 权威重读必须返回唯一、`trusted` 的 EvidenceReadSet；不得扩展到未读页、任务外来源或
  非法 locator，也不信任 Pi 自述。

### 3.4 Provider 结构化响应

- `missing_requirements` 的单个字符串规范化为单元素 tuple，保留失败含义，不吞掉缺口。
- 独立语义判断允许 1 次 Instructor 结构化重试；正常响应不增加调用，第二次仍失败则继续
  失败关闭。

## 4. 验证证据

- 相关后端回归：`87 passed`。
- 前端进度专项 Playwright：`2 passed`。
- 真实任务：`candidate_ready`，`failure_json=null`。
- 候选 QA：`non_empty`、`reopened`。
- 独立 Verifier：
  - `artifact_set=passed`
  - `artifact_count=passed`
  - `source_grounding=passed`，重新确认 19 条证据
  - `semantic_goal=passed`
- 资源清理：真实任务 ID 对应的 Docker 容器和网络查询结果为空。

## 5. 基于代码的推断

本次超长耗时主要不是 109 页全部执行高质量 OCR，而是同一 Pi 持久会话已经累计大量上下文，
多轮错误修正和 DeepSeek 结构化响应波动放大了每轮等待时间。修复减少了无效候选修正轮次，
但没有改变 Provider 本身的首 token 延迟或超长会话成本。

## 6. 尚未验证的建议与边界

- 建议后续单独设计“验证器短上下文”：只传 GoalContract、候选预览和已核验证据，不复用
  Pi 执行会话；本轮没有扩大到该性能重构。
- 当前结果仍是 Candidate，不是正式 Delivery；`formal_delivery_eligible=false`，不得对外
  表述为 Phase 4 正式发布闭环完成。
- 本轮没有执行版本、标签、提交、推送、生产数据库迁移或外部发布。
