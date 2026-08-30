# P1 CoreMind AgentKernel 本机源码契约原型报告

> 状态：`PROTOTYPE_ACCEPTED`，用户于 2026-08-27 明确确认推荐方案
>
> 日期：2026-08-27
>
> CoreMind 候选：本机 `codex/issue-73-child-runs@7b7da43c66f594c0c43239d28439fd1cfa1d07b5`
>
> 证据边界：本机源码、Mock/localhost 确定性测试和可抛弃状态原型；没有真实 Provider、
> Mangrove 生产接线、容器验收、依赖安装、发布或用户验收

## 要回答的问题

本机 CoreMind 源码能否作为 Mangrove 的 AgentKernel 复用，同时保持 Mangrove 对
TaskRevision、来源与权限、用户选定模型、ProviderUsage、Candidate、Verifier 和 Delivery 的
产品权威？如果可以，当前是否能通过 Python SDK + Protocol v2 直接替换现有 PiRuntime？

## 结论

**CoreMind 作为 AgentKernel 内核可复用，但当前 Python SDK + Protocol v2 不是可直接替换的
Mangrove Adapter。**

已验证的内核与协议能力足以支持后续 Adapter：持久 Run、resume、steering、cancel、顺序事件、
游标恢复、Projection 查询、Usage 事件、Checkpoint、EffectReceipt、Replay 和工具生命周期。
当前直接接入仍有两个明确合同缺口：

1. Checkpoint diff/restore 只在 v1 Python 方法和 Runtime 内部存在，Protocol v2 没有对应操作；
2. Python callable 动态工具注册只在 v1 存在，v2 客户端明确拒绝混用该能力。

因此，原型的严格 v2 profile 在启动前报告缺少 `checkpoint`、`tool_effect` 并失败关闭；它同时
显示 `runtime_checkpoint`、`checkpoint_events`、`tool_effect_events`，避免把“协议没开放”
误述为“CoreMind 内部没有能力”。

当前证据可以判定为：

```text
KERNEL_REUSE_FEASIBLE
PROTOCOL_V2_ADAPTER_GAPS_CONFIRMED
RUNTIME_COMPATIBLE_NOT_YET
```

`RUNTIME_COMPATIBLE` 仍需精确 Adapter、Mock Mangrove Tool Relay、进程/取消/资源清理和黄金任务
通过同一契约套件后才能成立。

## 必需能力矩阵

| AgentKernel 能力 | 本机源码事实 | 当前结论 |
| --- | --- | --- |
| `start` | v2 `run/chat` 返回 RunHandle；Python SDK 支持 v2 | 可直接映射 |
| `resume` | v2 `resume`、持久 RunState 与恢复路径存在 | 可直接映射，但必须沿用 RuntimeBinding |
| `steer` | v2 持久 ControlInbox 支持 `steering`；PiAgentDriver 调用 `agent.steer` | 可直接映射 |
| `cancel` | v2 控制与 Runtime abort/清理路径存在 | 可映射；ControlReceipt ACK 不等于已静止，Adapter 必须继续等终态/Quiescent |
| `events` | v2 typed event、durable sequence、cursor expiry recovery 存在 | 可直接映射，先经过 Mangrove 脱敏投影 |
| `query` | v2 从同一 Fact 前缀生成 Projection | 可直接映射，Projection 不是 Mangrove 业务真相 |
| `usage` | `turn_end` 有 input/output/cache/total/cost 字段，RunMetrics 可汇总 | 可映射；Mangrove ProviderUsage/unknown 调用数仍是用户用量权威 |
| `checkpoint` | Runtime 与 v1 有 inspect/restore；v2 只有 Checkpoint 事件 | 当前 v2 缺口 |
| `tool_effect` | Runtime 有 Tool Capability、EffectReceipt、阶段事实；v1 可注册 Python callable | 当前 v2 动态工具桥缺口 |

## 可选能力与语义边界

- CoreMind Session 是跨 Run 的原始消息事实域；Mangrove WorkSession 仍严格等于一个 Run，二者
  不做一对一映射。
- ReplayKit 已在 Runtime 中存在，但尚不是本次 v2 AgentKernel 的公开操作；首片只冻结未来
  能力位，不自动开放产品入口。
- Child Run 内核存在，但 CoreMind 自身仍将其视为 Experimental，且 P1-01 不需要它。
- 一个 ProtocolHost Worker 同时只允许一个 Run。首片可以沿用 Mangrove 的任务级进程/容器，
  每个 Mangrove Run 绑定一个 Worker；不能让一个 Worker 承载整个平台并发。
- CoreMind manifest 标为 0.3.2，但本机 handoff 说明源码跨越 0.4/0.7 能力。RuntimeBinding 必须
  冻结源码 commit、实际 Artifact digest、协议 Schema fingerprint 和 Adapter 版本，不能只写
  `0.3.2`。
- v2 事件包含工具参数等原始载荷。CoreMind event 只能先进入 Adapter 内部，再转为脱敏的
  StructuredProgressEvent；不能直接发送给普通用户。

## 验证证据

### 原型烟测

运行：

```powershell
python -X utf8 src/agentic_runtime/prototypes/coremind_contract/tui.py
```

本机源码 profile 绑定 `source-7b7da43c66f5 (manifest 0.3.2)`，投影到：

- 直接 v2 能力：`start/resume/steer/cancel/events/query/usage`；
- 内核/事件原语：`runtime_checkpoint/checkpoint_events/tool_effect_events/runtime_replay`；
- 可选：`session/experimental_child_run`；
- 严格缺口：`checkpoint/tool_effect`；
- 启动结果：`incompatible`，未创建原型 Run。

完整内存合同 profile 仍能推动同一 Run 的暂停/恢复、steering、工具失败恢复、已知/未知 Usage、
取消和 Candidate 形成，并持续保持 `delivery_created=false`。

### CoreMind 本机确定性测试

以下命令使用仓库已有 `node_modules`，没有安装、构建或清理 `dist`：

```powershell
.\node_modules\.bin\vitest.cmd run `
  packages/coremind-protocol/src/protocol-v2.test.ts `
  packages/coremind-runtime/src/control-inbox.test.ts `
  packages/coremind-runtime/src/run-kernel.test.ts `
  packages/coremind-runtime/src/agent-driver.test.ts `
  packages/coremind-runtime/src/result.test.ts `
  packages/coremind-runtime/src/replay-kit.test.ts `
  packages/coremind-worker/src/protocol-host.test.ts `
  --maxWorkers=1
```

结果：`7 files passed / 47 tests passed`。

外部副作用恢复使用 localhost Fake Provider/Tool 和系统临时目录：

```powershell
.\node_modules\.bin\vitest.cmd run `
  packages/coremind-runtime/src/external-resume.acceptance.test.ts `
  --maxWorkers=1
```

结果：`1 file passed / 6 tests passed`。

Python SDK 测试显式把 `PYTHONPATH` 指向本机仓库 `python/src`，worker 为仓库内 fake worker，
没有加载全局旧包：

```powershell
$env:PYTHONPATH = (Resolve-Path 'python/src').Path
python -X utf8 -m unittest discover -s python/tests -p test_client.py -v
```

结果：`10 tests passed`。其中 v2 测试覆盖 RunHandle、events、query、control 和 cursor recovery；
Python callable 与 Checkpoint 操作通过的是 v1 路径，这正好印证当前 v2 缺口。

## 已验证事实

- 本机 CoreMind Runtime 源码文件没有工作树修改；现有改动只在领域文档和未跟踪 ADR，未被本
  原型触碰。
- Protocol v2 支持 RunHandle、resume、cursor events、Projection query 和持久 control；控制
  类型包含 cancel、approval、steering、follow-up。
- steering 最终进入 AgentDriver；CoreMind 的 Pi Adapter 调用 `agent.steer`。
- v2 Usage 事件包含输入、输出、缓存读写和总量字段；字段可以缺失。
- EffectReceipt、Tool Capability、Checkpoint 和 Replay 原语存在于 Runtime。
- Python SDK 明确拒绝 v2 与 Python callable 注册混用；Checkpoint diff/restore 明确要求 v1。
- ProtocolHost 同时只允许一个 Run。
- 定向本机测试全部通过，但它们不构成 Mangrove Adapter、容器或真实 Provider 验收。

## 基于代码的推断

- 最小复用路线不需要重构 Mangrove 的 TaskRevision、Verifier 或 Publisher；Adapter 可以在产品
  域边界外终止 CoreMind 类型。
- 当前 v2 的两个缺口是有限协议/宿主接缝，不要求复制或重写 CoreMind Runtime。
- Mangrove 已有任务级 PiRuntime 进程/容器边界，可以承接“一 Run 一 Worker”，无需为了首片
  先完成 CoreMind 的通用 Local Supervisor。
- CoreMind RunMetrics 可用于执行观测，但无法替代 Mangrove 对 Owner、Purpose 和 unknown 调用
  的 ProviderUsage 账本。

## 尚未验证的建议

推荐首个真实 Adapter 原型采用“每个 Mangrove Run 一个 CoreMind Worker/Sidecar”，并只补：

1. Mangrove Tool Catalog/CapabilityHost 到 CoreMind ToolDefinition/EffectReceipt 的受控桥；
2. v2 Checkpoint inspect/restore 的显式协议操作或等价的受限 Adapter Host；
3. ControlReceipt 后继续等待终态与 Quiescent 的取消合同；
4. 原始事件到 StructuredProgressEvent 的排序、脱敏和 unknown-event 失败关闭；
5. ProviderUsage 逐调用回传与 unknown 语义，不把 RunMetrics 当账单；
6. 精确模型连接和 AccessGrant 注入，禁止 Runtime 自主换模型或 Failover。

该形态还没有用 Mangrove Fake AgentKernel 契约套件验证，不能直接进入生产实现。Node Runtime
进程内直连是备选，但会扩大 Python 产品后端与 Node 生命周期耦合；在取消、Secret 和资源清理
对比探针前不建议提前选定。

## 阶段门结论

本 prototype 阶段已经回答“是否值得复用”：**值得，并且应复用内核而不是复制代码；当前 v2
需要两个 Adapter 接缝，不能直接替换。**

用户于 2026-08-27 明确确认：

1. 接受上述结论和“一 Run 一 Worker/Sidecar”作为 P1-01 的首选实现方向；
2. 允许后续阶段把本机 CoreMind 的精确源码候选作为开发依赖来源；
3. Checkpoint 操作和动态工具桥属于通用 Runtime 合同，优先在 CoreMind Protocol v2 补齐；
   Mangrove 只维护薄 Adapter，不建立长期私有协议分叉。

Prototype 阶段至此完成。该确认不自动授权安装依赖、修改 CoreMind、进入 `to-tickets`、实施
Mangrove 生产代码、调用真实 Provider、创建分支/提交/PR、发布或部署；下一 Skill 仍等待用户
显式调用或确认。
