# ADR-0028：原生任务能力使用单一 Capability Host Sidecar

- 状态：`accepted`；工程门与用户灰度验收通过，代码默认关闭
- 日期：2026-08-05
- 上游：[ADR-0017](0017-agentic-runtime-vnext.md)、[ADR-0026](0026-agentic-capability-acquisition-and-procedure-governance.md)
- 证据：[AC-06 本地真实 Adapter 执行报告](../plans/2026-08-04-agentic-capability-ac06-local-adapters-execution-report.md)

## 背景

AC-06 真实样例证明 Python、Node、CLI 与本地 MCP 可以冻结和复现，但 Windows Docker
Desktop 会把宿主 bind mount 暴露为容器内可读路径。固定 Pi 镜像中降低 UID/GID 仍能读取
任务来源和模型配置，现有权限又不允许用 `unshare` 建立可信文件命名空间。因此把第三方原生
代码直接装入 Pi 容器不能满足来源和模型凭证隔离。

## 决策

1. 一个 TaskRevision/Run 的全部本地原生能力共用一个短生命周期 Capability Host Sidecar，
   不为每个普通工具创建容器；无原生能力时不创建 Sidecar。
2. Sidecar 只读装载冻结能力包及自身配置，不挂载业务来源、Pi 工作目录、模型配置、Provider
   Secret 或 Docker Socket。
3. Pi 只得到任务级短期 Relay URL/Token，并通过一个 Bridge 扩展调用冻结能力；原生能力目录
   不再挂载到 Pi。Token 不进入 Prompt、事件、审计正文或 Docker argv。
4. Sidecar 加入既有任务内部网络，沿用 Egress Controller 的精确出口边界；不新增公网直连、
   模型访问或业务 Secret。
5. 容器使用只读根文件系统、能力包只读挂载、`cap-drop ALL`、`no-new-privileges`、PID/CPU/
   内存上限和 `noexec` 临时目录。命令使用直接 argv，禁止 Shell 解释器。
6. `start(request) → lease`、`stop(lease)` 与 `cancel(lease)` 是唯一生命周期 Interface。取消先
   终止并删除 Sidecar，再删除任务网络；Docker 操作超时或删除失败必须失败关闭并保留证据。
7. MCP 使用能力包内冻结的官方 SDK，支持 legacy 与 modern 协议；会话在任务内复用，超时、
   请求断开或取消时关闭客户端与传输。请求和响应均有 1 MiB 上限。
8. `pi_capability_host_enabled` 默认 `false`。工程门通过不自动开启灰度；真实任务验收和默认
   切换仍是人工控制点。

## 后果

- 隔离依赖明确的“不挂载”，不再依赖 Windows bind mount 的容器内权限表现。
- 一个原生能力任务会多启动一个容器，增加有限冷启动与资源成本；同任务多能力和 MCP session
  复用限制了该成本。
- 无原生能力任务和无脚本 Skill 保持既有路径，默认配置不会改变当前正常使用。
- 远程 MCP、AC-07 验证晋级、SBOM/签名、个人到平台发布和默认开启均不由本 ADR 授权。

## 已验证边界

真实 Docker 已验证 Pi Bridge 命令调用、Everything MCP legacy/modern 工具发现与调用、恢复、
取消顺序、Docker 超时、删除失败关闭及零残留。最终审查修复后组合回归 103 passed；此前
同一工作树全仓后端 1210 passed、4 skipped。尚未完成真实用户业务
任务灰度验收和 AC-05 获取到 TaskRevision/Sidecar 的完整端到端闭环。
