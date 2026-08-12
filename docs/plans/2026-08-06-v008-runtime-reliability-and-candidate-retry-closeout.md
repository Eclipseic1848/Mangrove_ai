# v0.0.8 Runtime 可靠性与候选重验收口报告

> status: historical
>
> last_verified: 2026-08-11
>
> 该报告标题区的“待用户最终复核”已由用户后续验收通过；当前状态只以
> [`docs/status/current.md`](../status/current.md) 为准。

> 日期：2026-08-06
>
> 分支：`v0.0.8`
>
> 状态：`engineering_verified_pending_final_user_recheck`
>
> 范围：序数对象检索、任务有界执行、Windows 后端监督、产品名称投影、候选语义重验。
>
> 工程提交：`fb0b809f`，已推送到 `platform/v0.0.8`；未创建标签或 Release。

## 1. 本轮解决的问题

本轮不是新增业务范围，而是修复真实 PDF 数据任务暴露出的运行可靠性问题：

1. 用户要求“第 2 个报销审批单”时，覆盖契约曾把对象内部人员/行数误当成顶层结果数量，
   造成不必要的全源遍历和完成门误判。
2. Pi Shell 命令没有默认超时，且大工具输出直接进入会话；Sidecar 接入后，单次无界搜索或
   大输出可能持续占用 1800 秒任务预算。
3. `dev_reload.py` 或其子后端异常退出后，启动窗口仍可能存在，但 8088 服务已经消失。
4. 用户界面仍暴露内部执行器名称 `Pi`，与产品级 `Mangrove` 表述不一致。
5. 外部 Provider 的候选语义验证收到空响应时，Broker 路径没有结构化重试，直接把 Pydantic
   错误显示给用户；已有候选只能创建新版本或整项重跑。

## 2. 已完成实现

### 2.1 序数对象覆盖契约

- 新增 `ResultCardinality.ORDINAL` 与 `result_ordinal`。
- `first`、`ordinal`、`count`、`all` 现在分别表示首个对象、第 N 个对象、返回 N 个对象和
  全部对象；对象内部行数、人员数或字段数不改变顶层基数。
- 第 N 个对象只要求证明其前序对象与目标对象的稳定顺序；目标之后的低质量页面不会阻断
  合法早停。
- 结果页本身仍必须完成权威读取；低质量结果页不能冒充可信结果。

### 2.2 有界工具执行与上下文恢复

- Pi Bash 未显式提供超时时，默认限制为 300 秒，避免单条命令吞掉整个任务预算。
- 禁止从文件系统根目录执行无界 `grep/find/rg`；必须限定在任务工作区。
- 禁止任务 Shell 读取运行时模型、文档工具和 Capability Host 凭证配置。
- 大工具结果会以 0600 权限保存到 `/workspace/work/tool-results`，会话只保留头尾和明确的
  分页恢复路径；保存失败时要求缩小查询范围，不伪造完整输出。

### 2.3 Windows 后端双层监督

- `start_all.bat` 仍以 8088 为统一产品入口，不改变端口。
- 外层 `run_backend_supervisor.bat` 负责在 `dev_reload.py` 整体退出后自动恢复。
- 内层 `dev_reload.py` 每秒检查 Uvicorn 子进程；子进程意外退出时自动重启，并把证据写入
  `logs/dev_reload.log`。
- 文件监听异常只重建监听器，不带走仍健康的后端。
- 停止脚本验证父子进程创建时间，避免 Windows PID 复用把无关进程误接到 Mangrove 进程树。

### 2.4 产品名称投影

- 工作台普通用户可见的执行进度、行动记录、失败说明、候选卡、外发确认和 API 错误统一把
  内部 `Pi` 名称投影为 `Mangrove`。
- 内部类名、协议名、数据库字段与审计数据不做破坏性迁移。

### 2.5 候选语义重验与正式发布

- Broker 外部语义验证收到空内容或无效结构化 JSON 时，自动进行一次有界重试。
- 技术异常只写服务日志；公共界面显示“语义验证服务暂时不可用，请稍后重新验证候选”。
- 历史已持久化的 Pydantic 错误也在公共投影层脱敏，无需修改审计原记录。
- 只有 `inconclusive` 且文件集合、数量和来源证据门已经通过的 Candidate 才显示
  “重新验证候选”。
- 重验会重新核对候选路径、大小、SHA-256、Manifest 和证据数量，只重跑语义 Verifier；
  不重新执行 Pi、OCR、来源发现、证据读取或候选生成，也不创建新 revision。
- 语义重验通过后复用既有 Publisher，继续执行独立重开 QA 和正式 Delivery 发布。
- 候选发生变化、来源门未通过或原结论不是 `inconclusive` 时失败关闭。

## 3. 当前验证证据

2026-08-06 在当前工作树执行：

```text
后端相关回归：110 passed
前端 TypeScript + Vite 生产构建：passed
完整 Playwright：53 passed
git diff --check：passed
```

后端集合覆盖 Runtime、文档 Tool Relay、Windows 监督器、候选 Verifier、Pi 工作台 API 和
Legacy/新工作台 API。工作台纵切面明确断言：重验前后 Pi `start_calls == 1`，因此没有重跑
主任务；重验通过后形成正式 Delivery。

## 4. 尚未验证或仍未完成

- 用户已在原任务 `第2个报销审批单.json` 上完成最终人工复核并确认通过；该结论不替代其他
  Phase 4 生产门。
- 本轮没有执行全仓所有后端测试；当前证据是与变更风险对应的 110 项聚焦回归。
- 真实 DeepSeek Provider 的空响应重试没有主动消耗用户 Key 做自动化 Smoke；由原任务的
  人工“重新验证候选”完成最小真实复核。
- AC-06 Capability Host Sidecar 仍默认关闭；用户已确认管理员工作台灰度验收通过。
- 远程 MCP、AC-07 后续验证晋级/SBOM/签名/个人到平台发布、30 项泛化集、完整 PG-05、真实
  Provider 端到端安全门和 8B 服务器验证仍未完成。

## 5. 下一步

1. 原 Candidate 重验和 AC-06 用户灰度验收均已由用户确认通过，保留原 Candidate、Delivery
   与 TaskRevision，不回填或改写历史记录。
2. AC-07 #33 三轴治理投影已完成；后续工单仍需单独授权，不得自动默认开启 Sidecar、发布平台
   能力或扩大普通用户权限。
