# Phase 4 D4 最小模型连接纵切面

- 状态：`implemented_pending_user_confirmation`
- 日期：2026-07-30
- 上游：
  [D4 架构基线](2026-07-30-phase4-d4-provider-connection-and-controlled-egress-contract.md)、
  [ADR-0020](../adr/0020-provider-connection-broker-and-credential-isolation.md)
- 对应 Issue：GitHub #16；本轮不修改或关闭远端 Issue

## 目标

交付第一个可用且不会锁死后续架构的模型连接纵切面：

1. 登录用户能看到 DeepSeek、Qwen、OpenAI、Anthropic、Gemini 五个版本化
   `ProviderPreset`；
2. 普通用户选择 Preset 和模型后只填写自己的 API Key，平台用合成提示验证并保存为在线密文；
3. 管理员和超级管理员能精确登记一个公网 HTTPS 或 `192.168.*` 等 LAN 模型服务；
4. 普通用户不能创建自定义 Endpoint，不能读取或使用其他用户的个人连接；
5. 对外响应、日志和连接元数据不返回 API Key；Provider 请求只在
   `ConnectionBroker` 内短暂取得明文；
6. 验证失败即失败，不改用平台或其他用户的连接。

## 已确认的测试 Seam

本轮只在两个公开 Seam 上写行为测试：

1. **HTTP 产品 Interface**：`/api/model-connections`，验证用户能完成的动作、返回内容和角色
   权限；
2. **ConnectionBroker Interface**：验证 Preset、连接验证、Owner 隔离、精确 Endpoint 和
   失败关闭。

Provider HTTP 是系统外部边界，测试使用 `httpx.MockTransport`；SQLite 和真实
`ConnectionBroker` 不做内部 Mock。在线密文属于安全不变量，另做数据库字节扫描作为补充
证据，不替代 Interface 行为测试。

## 产品 Interface

| 方法 | 路径 | 权限 | 行为 |
|---|---|---|---|
| `GET` | `/api/model-connections/presets` | 已登录 | 返回五个卡片、友好模型目录和推荐模型，不返回 Base URL 或鉴权细节 |
| `GET` | `/api/model-connections` | 已登录 | 返回本人的个人连接与平台发布的管理连接 |
| `PUT` | `/api/model-connections/presets/{preset_id}` | 已登录 | 用用户提供的 Key 验证推荐/所选模型；成功后新增或替换本人的连接 |
| `POST` | `/api/model-connections/managed` | 管理员/超级管理员 | 验证并登记一个精确自定义公网或 LAN Endpoint，成功后平台发布 |
| `DELETE` | `/api/model-connections/{connection_id}` | Owner 或管理权限 | 个人连接只能本人删除；管理连接只能管理员/超级管理员删除 |

所有写接口都返回不含 `api_key`、`ciphertext` 和内部鉴权字段的公开连接摘要。

## 最小领域数据

首个纵切面只持久化：

- `ProviderPreset`：代码内版本化目录，不含秘密；
- `ModelConnection`：Owner scope、Preset/Endpoint、协议、模型、状态和 Secret 引用；
- `CredentialSecret`：与连接元数据分表的在线密文和尾部提示；
- 最近一次验证时间。

不在首个纵切面建立 Grant、Disclosure、价格、预算、自动轮换或备份擦除表。

## 网络边界

- Preset 使用平台冻结的官方 HTTPS Endpoint，普通用户不能编辑；
- 管理连接按完整 `scheme + host/IP + port + base path + api_format + model` 冻结；
- 公网自定义连接只允许 HTTPS；
- 管理员可登记明确的私网/LAN HTTP(S) 服务，包括 `192.168.*`；
- 不开放整段私网；云元数据、链路本地、保留地址和未登记 Endpoint 继续拒绝；
- 首个纵切面不声称已经完成 DNS rebinding、证书、自定义 CA 和所有重定向攻击矩阵。

## 验证规则

- 使用与连接 `api_format` 一致的最小真实生成请求；
- 合成输入不含任务、来源或用户业务数据；
- 只验证用户选择的模型，不扫描整张模型目录；
- 禁止自动重定向和环境代理；
- 验证失败不持久化新的活动连接，也不跨连接重试；
- 本仓库自动化只使用假 Provider，不调用真实外部模型。

## 本轮明确不做

- 不把连接接入 Pi 任务容器；Agent Grant/Relay 是紧随其后的第二个纵切面；
- 不删除或迁移 `runtime_config` 中的旧 DeepSeek/Qwen Key；
- 不实现最终设置页视觉、新手引导和管理员安全运营台；
- 不进入 D5，不实现多媒体、数据库、业务 HTTP API 或本地路径来源；
- 不创建提交、版本、标签，不修改 GitHub Issue。

## 完成证据

1. 五个 Preset 目录和公开字段测试；
2. 个人连接成功、验证失败、替换 Key 和跨用户隔离测试；
3. 普通用户自定义 Endpoint 被拒、管理员精确 LAN 成功、云元数据失败测试；
4. API 响应与 SQLite 数据库字节均不含测试明文 Key；
5. 定向 pytest、相关既有模型/HTTP 安全回归、`git diff --check` 和严格 UTF-8 检查通过。

## 实现结果（2026-07-30）

- 已新增 `src/model_connections/` 深 Module、独立连接/Secret 表和
  `/api/model-connections` 产品接口；
- 五类 Preset 验证路线分别覆盖 OpenAI Chat Completions、OpenAI Responses、
  Anthropic Messages 和 Gemini `generateContent`；
- 普通用户、管理员和超级管理员的连接治理视图及删除权限已按本规格失败关闭；
- 14 个新接口用例通过；与既有 Provider、模型 API、HTTP 安全用例合并回归为
  `60 passed, 4 warnings`；
- 新模块 `compileall`、15 个相关文件严格 UTF-8、尾随空白/Markdown fence 检查和限定范围
  `git diff --check` 均通过；
- 测试仅使用 `httpx.MockTransport`，没有调用真实 Provider、使用真实 Key 或发送业务数据。

本状态只表示第一个后端连接纵切面完成并等待用户确认，不表示 D4 或整个 Phase 4 完成。
