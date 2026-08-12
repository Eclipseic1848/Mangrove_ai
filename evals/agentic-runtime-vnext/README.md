# Agentic Runtime vNext 阶段 1 逻辑原型

> 这是可抛弃的赛马原型，不接 Mangrove 默认入口，也不写生产数据库。

> 阶段 1 结论：Pi、OpenCode、Deep Agents 分别通过 16/18、16/18、12/18，均未通过
> 连续三次 P0 硬门，没有胜出候选。详见
> [`阶段 1 执行报告`](../../docs/plans/2026-07-29-agentic-runtime-vnext-stage1-execution-report.md)。

## 要回答的问题

在相同的不可变 `GoalContract`、本地 Qwen、四项通用领域工具和独立 Verifier 下，
Deep Agents、OpenCode headless、Pi Agent Core 中哪一个能更稳定地执行
“观察来源 → 定向读取 → 生成候选 → 失败后重规划”，同时保留可转换为 Mangrove
事件的完整状态？本原型不评测 PDF/OCR 解析器质量，来源工具返回的是冻结后的结构化观察；
解析器赛马属于阶段 3。

## 运行

首次运行前安装隔离依赖：

```powershell
.\evals\agentic-runtime-vnext\setup.ps1
```

安装脚本已为慢速网络设置较长超时和有限重试。OpenCode 的 Windows 原生包约
174 MB，下载期间长时间没有终端输出不代表失败；不要重复启动安装或手动中止。

启动交互式原型：

```powershell
.\evals\agentic-runtime-vnext\run.ps1
```

终端每次操作都会重新显示完整 `RunState`。运行记录只写入已忽略的 `runs/`，不会进入
Git，也不会发布正式 Delivery。

## 公平边界

- 三个候选只能调用 `observe_sources`、`read_source`、`request_clarification`、
  `submit_candidate`。
- 候选不能直接访问原始文件、宿主 Shell、网络或正式交付接口。
- `submit_candidate` 只产生候选；原型控制器在 Agent 结束后独立执行 Verifier。
- 三个 Adapter 使用同一系统指令、模型参数、轮数预算和工具宿主。
- 框架原生事件与统一事件同时保留；不保存或展示隐藏思维链。

## 证据边界

本原型验证的是 AgentKernel 的逻辑循环，不评测真实 PDF/OCR 解析器。候选已经在已执行
小集中重复违反硬门，因此停止继续扩大三框架的昂贵测试；长上下文、重启幂等、完整复合
来源、30 项泛化集和生产所有权仍未验证。

可提交的去敏摘要在
[`stage1-evidence-summary.json`](stage1-evidence-summary.json)；本机原始模型输出保存在
已忽略的 `runs/`，不得作为正式 Delivery。
