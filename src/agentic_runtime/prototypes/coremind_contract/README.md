# PROTOTYPE — CoreMind AgentKernel 合同探针

> 可抛弃原型，不是生产实现，不接数据库、真实 Provider 或正式任务。

## 要回答的问题

CoreMind 能否在不接管 Mangrove 的 TaskRevision、模型选择、ProviderUsage、Verifier 和
Delivery 的前提下，通过一个稳定 AgentKernel Adapter 表达同一 Run 的启动、运行中引导、
暂停、恢复、取消、事件查询、Checkpoint、工具副作用和 Usage 语义？

这个原型只验证状态和接口形状。CoreMind 能力依据只来自相邻的本机源码仓库，不读取官方稳定
包、PyPI 或全局安装包。源码投影不会证明进程、容器、取消收敛或恢复已经通过；这些结论必须
由绑定精确 commit 的确定性探针给出。

## 运行

在仓库根目录执行：

```powershell
python -X utf8 src/agentic_runtime/prototypes/coremind_contract/tui.py
```

原型不持久化任何状态。退出后所有 Run、事件和 Usage 都会消失。

## 观察重点

- 缺少必需能力时，`start` 是否在创建 Run 前失败关闭；
- `pause → tick → resume` 是否保持同一 Run，并区分工作与等待时间；
- 已知与未知 Usage 是否分别累计；
- 工具失败恢复后是否保留历史事实；
- `cancel` 后是否禁止继续写入终态；
- Runtime 成功是否只形成 Candidate，而不会形成 Delivery。

## 快捷键

- `1`：完整合同模拟器；
- `2`：相邻本机 CoreMind 源码的 Protocol v2 只读能力投影；
- `s`：启动；`t`：工作一个时间单位；
- `o`：工具成功；`f`：工具失败；`h`：恢复失败；
- `p`：暂停；`r`：恢复；`g`：运行中引导；
- `k`：记录已知 Usage；`u`：记录 Usage 未知；
- `e`：注入未知 Runtime 事件；`x`：结束并产生 Candidate；
- `c`：取消；`0`：重置；`q`：退出。

## 退出条件

原型回答问题后，结论进入 P1-01 实施规格或工单；这个终端壳不进入生产主链。按当前仓库授权
边界，不创建 throwaway 分支、提交或 GitHub 指针，除非用户另行批准。

## 当前源码投影的严格口径

- CoreMind Runtime 内部已经有 Checkpoint、EffectReceipt、Replay 和 Child Run 等原语；
- Protocol v2 已有 RunHandle、resume、cursor events、query、cancel、steering 和 Usage 事件；
- 但 Python SDK 的 Checkpoint diff/restore 仍只允许 v1，v2 也明确不开放 Python callable 注册；
- 因此 profile 2 不把 `checkpoint` 和 `tool_effect` 宣称为现成 v2 Adapter 能力，只显示对应的
  Runtime/事件原语。它回答的是“是否可直接接入”，不是“CoreMind 内部有没有这些代码”。
