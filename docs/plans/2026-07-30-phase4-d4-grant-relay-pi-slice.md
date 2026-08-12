# Phase 4 D4：Grant/Relay 接入 Pi 纵切面

- 状态：`implemented_pending_user_acceptance`
- 日期：2026-07-30
- 上游：
  [D4 架构基线](2026-07-30-phase4-d4-provider-connection-and-controlled-egress-contract.md)、
  [首个连接纵切面](2026-07-30-phase4-d4-minimal-provider-connection-slice.md)、
  [ADR-0020](../adr/0020-provider-connection-broker-and-credential-isolation.md)

## 目标

让受平台总开关控制的标准 vNext 任务真实使用已验证的 `ModelConnection`，同时满足：

1. `PiRuntimeRequest`、运行台账、Docker 命令、Event 和工作区都不出现 Provider 原始 Key；
2. Pi 只持有绑定 Owner、Task、Revision、Run、Connection、Purpose 和到期时间的短期
   `AccessGrant`；
3. Relay 只代理该连接协议允许的固定推理路径，不做跨协议转换，不跨连接失败切换；
4. `start`、`resume`、`cancel`、失败和正常结束都关闭或轮换 Grant；
5. 记录 Provider 原生用量；Provider 未返回时记为 `unknown`，不计算价格。

## 实施前已验证事实

1. 当前 `PiRuntimeRequest` 直接包含 `base_url` 和 `api_key`；
2. `PiRuntime._write_runtime_files()` 会把 `request.api_key` 写入挂载给 Agent 的
   `/root/.pi/agent/models.json`；
3. 当前工作台 Pi 灰度只允许管理员/超级管理员和本地模型；
4. Pi 的 `start`、`resume`、`cancel` 已有任务级 Docker 网络、Smokescreen 生命周期和
   持久会话恢复；
5. 首个 D4 纵切面已经提供 Owner 隔离的密文连接、四种协议和精确 LAN 管理连接；
6. 当前独立 Candidate Verifier 仍使用 OpenAI Chat 兼容的本地模型。

## 基于代码的设计结论

### 深 Module Interface

`ConnectionBroker` 继续作为唯一模型连接 Interface，新增三项能力：

```text
issue_grant(owner, connection, connection_version, task, revision, run, purpose) -> AccessGrant
relay(grant, protocol_path, request) -> streamed provider response
revoke_grant(grant_id, reason)
```

实现隐藏连接解密、Provider 鉴权替换、固定路径检查、普通 HTTP Token 流转发、原生 Usage
提取和 Grant 状态；调用者不得取得 Provider Endpoint 或原始 Key。

### 冻结位置

- 工作台创建 Pi revision 时接收 `model_connection_id`；
- Owner 可用性、连接状态和外发确认在创建时失败关闭；
- `model_connection_id` 冻结在该 Runtime revision，不复用 `provider` 字段伪装；
- 普通用户在平台总灰度开关开启后，可以通过自己的个人连接或管理员发布的连接执行标准
  vNext 任务；产品界面不向普通用户暴露 Pi/Runtime 技术名称；
- 普通用户不得使用他人的个人连接、自定义 Endpoint、`extended` 或 `host_dev` 权限；
  管理员与超级管理员继续控制平台连接、本地 Pi 灰度和总灰度开关。

### Pi 生命周期

- `PiRuntimeRequest` 的外部连接模式只携带 `model_connection_id` 和不敏感的冻结版本引用，
  不携带 Provider `base_url` 或 `api_key`；
- `start` 生成 `run_id` 后即时签发 Agent Purpose Grant；
- `models.json` 只写 Relay Base URL、Pi 原生协议名、模型名和短期 Grant Token；
- `resume` 撤销旧 Grant、签发新 Grant并重写配置；
- `cancel`、失败和正常结束均撤销当前 Grant；
- 现有无秘密的本地模型路径继续保留，不自动切换到外部连接。

### Relay 路线

| 连接格式 | Pi 原生格式 | Relay 允许的 Provider 操作 |
|---|---|---|
| `openai_chat_completions` | `openai-completions` | Chat Completions |
| `openai_responses` | `openai-responses` | Responses |
| `anthropic_messages` | `anthropic-messages` | Messages |
| `gemini_generate_content` | `google-generative-ai` | generateContent/streamGenerateContent |

Relay 只接受 Pi 各协议产生的 Grant 鉴权头，移除 Grant 后再在 Broker 内注入真实 Provider
凭证。Gemini 固定使用 `x-goog-api-key`，不允许把 Grant 放入 URL 查询参数；请求体、响应正文
和 Token 不进入普通日志、Event 或 Delivery。

## 已确认的 TDD Seam

本轮只在以下三个公开 Seam 写行为测试：

1. **工作台 HTTP Interface**：
   `POST /api/semantic-workspace/tasks` 以 `model_connection_id` 创建 Pi revision；验证 Owner、
   管理员灰度、外发确认、幂等和不可见 Key；
2. **ConnectionBroker Interface**：
   验证签发、精确协议 Relay、流式透传、原生 Usage、跨 Owner 拒绝、到期和撤销；
3. **PiRuntime Interface**：
   验证 `start/resume/cancel` 的 Grant 生命周期，以及请求、配置、命令、台账和事件中均没有
   Provider 原始 Key。

SQLite、真实 `ConnectionBroker`、真实 `PiRuntime` 文件生成逻辑不 Mock；只在系统边界替换
外部 Provider HTTP 和 Docker CLI。本轮不调用真实 Provider。

## 实施结果

### 已实现

1. 工作台创建和修改外部连接任务时，按 Owner 校验连接、要求该修订单独确认数据外发，并在
   `RuntimeAssignment` 中不可变保存 `model_connection_id`、连接版本和确认记录；
2. 普通用户在总开关开启后可使用自己的个人连接或管理员发布的连接运行 `standard` 任务；
   本地 Pi 入口仍仅由管理员/超级管理员控制，`extended` 和 `host_dev` 未开放；
3. `ConnectionBroker` 已实现绑定 Owner、Task、Revision、Run、Purpose、Connection Version
   和 TTL 的哈希 Grant、撤销、四协议固定路径 Relay 及原生 Usage 台账；
4. Provider Secret 只在 Broker 内解密和注入；外部 `PiRuntimeRequest`、运行台账、Event、
   Docker argv 和候选均不含 Provider Secret；
5. Pi `start`、`resume`、无会话重开、`cancel`、失败和正常终态均撤销或轮换 Grant；
   外部模式不会再传会覆盖 `models.json` Grant 的 CLI `--api-key`；
6. Candidate Verifier 使用同一冻结连接的独立 `candidate_verify` Grant；失败为
   `inconclusive`，不切换其他连接；
7. Relay 已覆盖 Chat Completions、Responses、Anthropic Messages、Gemini
   generateContent/streamGenerateContent；Gemini 流固定补 `alt=sse`，压缩响应先解码再转发；
8. Provider 发送失败或大响应无法安全提取 Usage 时写 `unknown`；请求体和 Usage 观察缓冲
   均有上限，不保存完整请求或响应。

### 审查纠偏

双轴代码审查发现并已修复：

- Pi 0.80.10 的 CLI Key 会覆盖 `models.json`；
- 同一 Runtime revision 可静默换连接；
- 任务创建到签发 Grant 之间连接可能轮换；
- 无会话恢复遗漏撤销旧 Grant；
- Gemini 查询参数会把 Grant 写入访问日志，且原生 SSE 缺少 `alt=sse`；
- 压缩响应、JSON `Content-Type`、Provider 发送失败 Usage 和 Relay 内存上限缺口。

### 验证证据

- D4 聚焦测试：`58 passed`；
- 全仓后端：`1023 passed, 4 skipped, 4 warnings`，耗时 `271.76s`；
- `python -m compileall -q src tests`：通过；
- `git diff --check`：通过，仅有既有 Windows CRLF 提示；
- 固定镜像 `mangrove/pi-coding-agent:0.80.10` 只读源码核对：CLI `--api-key` 会设置
  Runtime override；Gemini SDK 使用 `x-goog-api-key`。当前外部 argv 不含 `--api-key`。

### 尚未验证

- 未使用真实用户 Key、真实外部 Provider 或真实业务数据；
- 未执行真实 Pi 容器 → Relay → 外部 Provider 的端到端 Smoke；
- 因而本纵切面是“实现完成、等待用户验收”，不是 D4、默认切换或 Phase 4 完成。

## 已确认的业务决策

### D4-G1：独立 Verifier 是否使用同一外部连接

**推荐：使用，但单独签发 `candidate_verify` Purpose Grant。**

外发内容限定为：

- 用户目标；
- 候选文件的有界预览；
- 已经由确定性代码重开的来源证据片段。

影响：

- 使用用户自己的连接和 Key，会产生额外 Token；
- Usage 与 Agent 推理分开记录；
- 验证失败返回 `inconclusive`，不切换到平台或其他用户连接；
- 不把 Grant Token 或 Provider Key写入验证报告。

不采用该方案时，外部 Agent 任务仍依赖本地 Verifier；本地模型不可用时只能形成
`inconclusive`，与用户希望降低本地建设成本的目标存在冲突。

用户已确认采用推荐方案：Verifier 使用同一个已选外部连接，但使用独立
`candidate_verify` Grant；失败返回 `inconclusive`，不切换连接。

### D4-G2：普通用户是否可以使用 vNext

用户已确认：

1. 普通用户可以在平台总灰度开关开启后，通过自己的个人连接或管理员发布的连接运行标准
   vNext 任务；
2. 普通用户不需要理解或选择 Pi/Runtime，产品层只展示业务化的增强处理入口；
3. 普通用户不能配置自定义 Endpoint、不能使用他人的个人连接，也不能取得扩展目录或
   宿主机权限；
4. 用户选择连接并确认本任务外发构成显式试用；管理员仍控制总灰度开关和平台连接。

## 本纵切面明确不做

- 不实现最终设置页、连接选择器、新手引导或重新引导；
- 不迁移旧 `runtime_config` Key；
- 不实现价格、预算、钱包、自动 Failover 或跨协议转换；
- 不调用真实 Provider，不创建提交、版本、标签，不修改远端 Issue；
- 不宣称完成 D4、默认切换或整个 Phase 4。
