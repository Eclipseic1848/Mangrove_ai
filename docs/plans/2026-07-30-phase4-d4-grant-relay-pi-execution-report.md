# Phase 4 D4：Grant/Relay 接入 Pi 纵切面执行报告

- 状态：`implemented_pending_user_acceptance`
- 日期：2026-07-30
- 分支：`v0.0.7`
- 实施起点：`45ee703c7b8b853c01ddbffec087236f6598bd53`
- 规格：
  [Grant/Relay 接入 Pi 纵切面](2026-07-30-phase4-d4-grant-relay-pi-slice.md)
- 架构：
  [ADR-0020](../adr/0020-provider-connection-broker-and-credential-isolation.md)

## 1. 本轮完成范围

本轮完成 D4 的第二个后端价值纵切面，把首个纵切面的 Provider Preset、个人/平台连接和
在线密文接入 vNext 标准任务。没有新增 UI、调用真实外部 Provider、发布版本或进入下一
阶段。

### 1.1 TaskRevision 冻结

- 工作台接受 `model_connection_id`，按任务 Owner 校验个人或平台共享连接；
- 每个外部连接 revision 都要求单独确认数据外发；
- RuntimeAssignment 不可变保存连接 ID、当前不敏感版本引用和确认记录；
- 同一 revision 重复注册不同 Runtime、权限或连接会失败关闭；
- 用户换 Key、换模型或重配 Preset 后，旧 revision 不能静默签发新版本 Grant。

### 1.2 Grant 与 Relay

- Grant 绑定 Owner、Task、Revision、Run、Connection、Connection Version、Purpose 和 TTL；
- SQLite 只保存 Token 哈希，不保存可用 Grant Token；
- Relay 只允许所选协议的固定原生 POST 路径，禁止重定向和跨连接 Failover；
- Provider Secret 仅在 Broker 内瞬时解密并替换 Grant 鉴权；
- 支持 OpenAI Chat Completions、OpenAI Responses、Anthropic Messages 和 Gemini
  generateContent/streamGenerateContent；
- Gemini Grant 只走 `x-goog-api-key` Header，固定流式操作补 `alt=sse`，不接受可能进入
  access log 的 `?key=`；
- Provider 发送失败或无法可靠提取原生 Usage 时记录 `unknown`，不推算价格。

### 1.3 Pi 与 Verifier 生命周期

- 外部 `PiRuntimeRequest` 只携带连接 ID 和不敏感版本引用；
- `models.json` 只包含内部 Relay 地址、原生 Pi 协议名、模型名和短期 Grant；
- 外部模式 Docker argv 不传 `--api-key`，避免 Pi 0.80.10 Runtime override 覆盖 Grant；
- start、resume、无会话重开、cancel、异常和正常终态都会轮换或撤销 Grant；
- Smokescreen 只放行解析后的单一内部 Relay IP/端口，不开放整个 LAN；
- Verifier 使用同一连接的独立 `candidate_verify` Grant，失败返回 `inconclusive`，不切换
  到平台或其他用户连接。

### 1.4 Relay 资源与泄密边界

- Runtime 请求体有 `16 MiB` 上限；
- Provider 响应逐块转发，Usage 观察缓冲上限为 `2 MiB`；超过后继续转发但 Usage 为
  `unknown`；
- 压缩响应解码后再转发，不制造正文与 Header 不一致；
- 请求体、响应正文、Provider Secret 和 Grant 不写普通 Event、Delivery 或 Docker 命令
  Trace；
- Provider Endpoint 安全校验错误对 Runtime 只返回统一消息，不回显 Host/IP。

## 2. 审查结果

按 `code-review` Skill 分别执行 Standards 和 Spec 审查。

### Standards

初审发现 Pi CLI Key 覆盖、无会话恢复旧 Grant、同 revision 可变、Grant 查询参数日志、
压缩流、资源上限和深 Module 导出等问题。全部已修复；复审除文档尚未同步外未发现剩余
P0/P1 代码阻断，本文与 `AGENTS.md` 同步后该文档项关闭。

### Spec

初审发现连接版本未在 TaskRevision 冻结、Gemini SSE 查询缺失和 Provider 发送失败无
Usage 台账。全部已修复；最终实现保持普通用户仅 `standard`、独立 Verifier Grant、无
Failover、无 Provider Secret 外露。

未采用“顺手统一四协议字符串 switch”的重构建议：当前是可读的固定四协议边界，重构不
增加本纵切面业务价值，留到协议扩展确有重复成本时再处理。

## 3. 验证证据

### 已验证事实

1. D4 聚焦测试：

   ```text
   58 passed
   ```

2. 全仓后端回归：

   ```text
   1023 passed, 4 skipped, 4 warnings in 271.76s
   ```

   四项 skip 分别是需显式开启的大规模性能门和真实 MySQL/PostgreSQL 容器门；四项 warning
   来自既有第三方依赖。

3. 静态门：

   ```text
   python -m compileall -q src tests
   git diff --check
   ```

   均通过；后者仅报告 Windows 工作树既有 CRLF 转换提示。

4. 固定镜像只读核对：

   - `mangrove/pi-coding-agent:0.80.10` 的 CLI `--api-key` 会调用
     `setRuntimeApiKey()`，其优先级高于 `models.json`；
   - 当前外部模式生成的 Docker argv 已无 `--api-key`；
   - 镜像内 Gemini SDK 使用 `x-goog-api-key`。

5. 自动化行为证据覆盖：

   - 连接 Owner 隔离、版本轮换、Grant 到期、直接撤销和服务重启后撤销；
   - 四协议精确路径、Gemini SSE、压缩响应和 Provider 原生 Usage；
   - Provider 发送失败记 `unknown`；
   - 普通用户个人连接、每 revision 外发确认和 RuntimeAssignment 不可变；
   - Provider Secret 不进入请求契约、运行台账、Docker argv 或工作区普通文件。

### 尚未验证

- 没有真实外部 Provider Key，因此未执行真实外部模型调用；
- 没有执行真实 Pi 容器 → 内部 Relay → 外部 Provider 的端到端 Smoke；
- 设置页、连接选择器和新手引导已在后续 UI 纵切面实现并通过自动化门禁，但尚未取得
  用户体验验收结论。

## 4. 明确保留边界

- 不迁移或删除旧 `runtime_config` Key；
- 不开放普通用户自定义 Endpoint、`extended` 或 `host_dev`；
- 不实现价格、预算、钱包、账单或自动 Failover；
- 不完成 DNS rebinding、证书固定、外部 Vault/HSM、历史备份密码学擦除等完整加固；
- 不创建正式 Delivery，不切换默认入口；
- 不创建提交、分支、版本、标签，不推送，不修改 GitHub Issue。

## 5. 下一次接管

1. 先读 `handoff.md`、本报告和 D4 两份上游规格；
2. 当前停点是“D4 后端与角色化设置/任务连接选择纵切面实现完成，等待用户确认”；
3. 未经用户明确确认，不进入下一个显式 `code-review` Skill；
4. 若用户确认继续，按 Standards 与 Spec 双轴审查本轮未提交差异。

## 6. 后续 UI/权限纵切面补充（2026-07-30）

### 已实现

- `/settings` 按真实角色重组：普通用户只见“我的设置、模型与连接、采集账号”；管理员和
  超级管理员在保留个人能力的同时，增加“平台配置、运行与诊断”；
- 旧模型 Key、默认模型和文档抽取项不迁移、不删除，收纳到管理员可见的“旧流程兼容”；
- Provider Preset 配置、验证、删除接入真实 `/api/model-connections`，普通用户不能登记
  自定义 Endpoint，管理员可登记精确公网 HTTPS 或 LAN 连接；
- 新手引导使用 `react-joyride`，状态按用户持久化，支持完成、跳过和手动重新播放；
- `/api/settings/selfcheck` 后端限制为管理员，`/api/config/models` 不向普通用户返回
  `local_urls`；
- `/data-prep` 读取全局 Pi 开关和当前用户可用连接。普通用户只有在总开关开启且存在已验证
  的个人/平台共享连接时才看到 Pi；选择外部连接后冻结模型，并显示 Provider、模型、当前
  上传内容类别和仅限当前 revision 的外发范围，未逐任务勾选确认不能提交；
- 管理员仍可选择平台本地 Pi，不要求外发确认。

### 验证证据

```text
后端 D4/权限聚焦回归：91 passed, 4 warnings
完整 Playwright：45 passed
前端生产构建：tsc --noEmit + vite build 通过
外发披露组件 Axe：0 violations
git diff --check（本轮文件）：通过，仅有 CRLF 提示
```

### 仍未完成

- 真实外部 Provider Key Smoke；
- 真实 Pi 容器 → Relay → 外部 Provider 端到端 Smoke；
- 完整 DNS rebinding、证书和备份擦除加固；
- 旧 `runtime_config` Key 迁移或删除（本轮明确不做）；
- 用户体验验收与后续 `code-review`。

### 用户反馈修复

- 修复未知 `/api/*` 被前端静态 SPA 回退吞掉并返回 HTML 200 的问题；现在实机返回 JSON
  404，前端即使遇到代理 HTML 也显示错误说明与重新加载入口，不暴露 JSON 解析异常；
- 个人连接改为明确的 Provider、模型版本、必填 API Key 顺序，技术端点由平台 Preset
  维护；
- 新增管理员平台共享 Provider Preset 发布接口和表单，API Key 必填；
- 自定义/LAN 连接与 Provider Preset 分离：公网空 Key 后端失败关闭，精确 LAN/本地
  无鉴权服务保留空 Key 能力；
- 恢复运行中的 `dev_reload.py` 监听器，实机 `8088` 健康检查和新路由 OpenAPI 探针通过。
