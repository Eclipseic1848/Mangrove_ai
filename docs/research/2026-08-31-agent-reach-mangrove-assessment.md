# Agent-Reach 对 Mangrove 的能力适配评估

> 日期：2026-08-31
>
> 阶段：调研与架构适配判断，不是实现
>
> 上游基线：`Panniantong/Agent-Reach` `main@06c202b03400a7d31886bf4399213706da1a0324`
>
> 最新正式版本：`v1.5.0`，2026-06-11 发布
>
> 资料边界：Agent-Reach 官方仓库、其直接引用的上游官方仓库，以及 Mangrove 当前代码、领域文档和 ADR
>
> 授权边界：本文不安装依赖、不调用真实平台、不处理 Cookie/Secret、不修改产品代码、不构成接入授权

## 1. 结论

Agent-Reach 对 Mangrove **有明显帮助，但不适合整包替换 Mangrove 的信息获取系统**。

它真正擅长的是：

1. 维护“平台 → 首选后端 → 备选后端”的渠道知识；
2. 检查上游命令、浏览器桥和登录前置条件是否真实可用；
3. 给智能体提供按平台组织的 Skill、命令和故障修复说明；
4. 随平台变化替换底层工具，而不要求调用者重新理解每个平台。

它不负责：

- 实际抓取结果的统一 API；
- 多用户 Owner 隔离；
- TaskRevision、来源范围和外发确认；
- SourceSnapshot、原始制品哈希、证据引用和完整性；
- Cookie Vault、扫码登录编排和同 Run 恢复；
- 沙箱、能力 digest、供应链门、Verifier 和正式 Delivery；
- Agent Loop、Session、Checkpoint 或 CoreMind Runtime。

因此推荐方向是：

> **吸收 Agent-Reach 的渠道目录、后端路由、只读体检和修复处方；底层工具按精确版本逐个进入 Mangrove 现有 CapabilityCatalog/SourceAcquisition 边界。不要在生产 Runtime 里直接执行 Agent-Reach 的全局安装流程，也不要让模型直接消费上游工具原始输出。**

最有价值的具体候选是：

- 用它的真体检模式升级 Mangrove 采集器的 `is_available()`；
- 用有序后端列表替代部分平台散落的硬编码兜底；
- 把 Agent-Reach 维护的上游选择当作 Capability 发现线索；
- 优先验证 `xiaohongshu-mcp`、OpenCLI、bili-cli、Exa 等 Mangrove 目前缺少或薄弱的来源路线；
- 对 YouTube、RSS、GitHub、V2EX 等已有能力，只吸收诊断和路由知识，不重复接一层包装。

## 2. 判断口径

本文区分三类结论：

- **已验证事实**：由当前代码或官方资料直接支持；
- **基于代码的推断**：依据 Agent-Reach 与 Mangrove 的实际边界作出的适配判断；
- **尚未验证的建议**：适合后续 PoC，但尚未在本项目环境和真实账号中验证。

不能把以下证据混为一谈：

- Agent-Reach CI 通过，不等于其所有上游渠道当前可用；
- `doctor` 报告命令存在，不等于登录态和目标内容可用；
- 某个上游工具能返回内容，不等于结果已形成 Mangrove SourceSnapshot；
- 开源许可证允许使用，不等于平台条款、账号权限和数据获取方式允许当前任务；
- 一个真实账号成功，不等于多 Owner 隔离、恢复和生产资格通过。

## 3. Agent-Reach 实际是什么

### 3.1 已验证事实

Agent-Reach 当前是一个 Python CLI、Skill 和健康检查器。其核心类只提供 `doctor()` 和
`doctor_report()`；项目自己的 MCP Server 也只暴露 `get_status`。实际读取或搜索由智能体直接
调用 Jina Reader、yt-dlp、gh、OpenCLI、mcporter、xiaohongshu-mcp 等上游工具完成。

官方说明同样把它定义为“selector、installer、health checker、router，never a wrapper”。

来源：

- [Agent-Reach README](https://github.com/Panniantong/Agent-Reach/blob/06c202b03400a7d31886bf4399213706da1a0324/README.md)
- [核心类](https://github.com/Panniantong/Agent-Reach/blob/06c202b03400a7d31886bf4399213706da1a0324/agent_reach/core.py)
- [MCP Server](https://github.com/Panniantong/Agent-Reach/blob/06c202b03400a7d31886bf4399213706da1a0324/agent_reach/integrations/mcp_server.py)
- [安装说明](https://github.com/Panniantong/Agent-Reach/blob/06c202b03400a7d31886bf4399213706da1a0324/docs/install.md)

当前仓库定义了 15 个渠道，使用 `Channel` 抽象维护：

- `name`、`description`、`tier`；
- 有序 `backends`；
- URL 是否属于该渠道；
- 当前环境检查；
- `active_backend` 与修复说明。

多个后端时，配置可以把已知后端提到列表首位；未知配置不会隐藏其余可用后端。渠道应执行轻量、
无副作用命令，而不是只看可执行文件是否存在。

来源：

- [Channel 契约](https://github.com/Panniantong/Agent-Reach/blob/06c202b03400a7d31886bf4399213706da1a0324/agent_reach/channels/base.py)
- [真实命令探测](https://github.com/Panniantong/Agent-Reach/blob/06c202b03400a7d31886bf4399213706da1a0324/agent_reach/probe.py)
- [渠道契约测试](https://github.com/Panniantong/Agent-Reach/blob/06c202b03400a7d31886bf4399213706da1a0324/tests/test_channel_contracts.py)

### 3.2 工程成熟度事实

- 仓库声明 MIT，Python `>=3.10`，项目分类是 Beta；
- 当前 Python 依赖有范围约束，CI 使用 `constraints.txt` 固定测试组合；
- CI 覆盖 Python 3.10–3.13、Windows 3.12 和 Wheel 安装门；
- 当前基线提交的 CI 成功；
- 最新正式 Release 是 `v1.5.0`，但当前 `main` 在该 Release 之后仍有变化，项目版本字段仍是
  `1.5.0`。

来源：

- [pyproject.toml](https://github.com/Panniantong/Agent-Reach/blob/06c202b03400a7d31886bf4399213706da1a0324/pyproject.toml)
- [constraints.txt](https://github.com/Panniantong/Agent-Reach/blob/06c202b03400a7d31886bf4399213706da1a0324/constraints.txt)
- [CI workflow](https://github.com/Panniantong/Agent-Reach/blob/06c202b03400a7d31886bf4399213706da1a0324/.github/workflows/pytest.yml)
- [v1.5.0 Release](https://github.com/Panniantong/Agent-Reach/releases/tag/v1.5.0)
- [当前基线 CI](https://github.com/Panniantong/Agent-Reach/actions/runs/32813494976)

### 3.3 基于代码的推断

Agent-Reach 面向的是“一个本地用户给自己的命令行智能体装互联网工具”。它不是面向 Mangrove
这种多用户、持久任务、证据冻结和正式交付的平台服务。

判断依据：

- 配置默认保存在单一 `~/.agent-reach/config.yaml`；
- Cookie/Token 可以作为 YAML 明文值保存，主要依靠本机文件权限保护；
- 安装流程可以写全局 npm/pipx、Agent Skill 和 mcporter 配置；
- Skill 指示智能体直接运行上游命令；
- 实际内容没有统一结果 Schema、Owner、TaskRevision、来源范围或证据引用。

这些选择对本地个人 Agent 合理，但不能直接成为 Mangrove 的生产信任边界。

## 4. 值得吸收的能力

### 4.1 平台渠道目录与有序后端

**建议吸收。**

Agent-Reach 把同一平台的候选实现放入有序列表，并让健康检查决定当前后端。Mangrove 当前也有
Collector Registry、tier、近期成功率降权和反爬短路，因此不需要新增第二个注册表；应把有序
后端和健康状态合并进现有目录。

适合形成的 Mangrove 投影：

```text
SourceChannel
  platform
  ordered_provider_refs[]
  supported_operations[]
  auth_mode
  side_effect_class
  egress_scope
  current_health
  last_verified_at
  repair_action
```

这里的 `provider_ref` 必须指向 Mangrove 已冻结的 Capability 或内置 Connector，而不是一个
浮动命令名。

### 4.2 真体检和修复处方

**建议优先吸收。**

Agent-Reach 的 `probe_command()` 能区分：

- 未安装；
- 命令存在但解释器/venv 已损坏；
- 超时；
- 执行错误；
- 正常。

`doctor` 对每个渠道单独捕获异常，一个坏渠道不会拖垮整份报告，并在输出边界清理 URL 中的凭据。
登录相关检查刻意避免执行会刷新或读取浏览器 Cookie 的上游命令。

这可以替代或增强 Mangrove 中仅靠 `import`、文件存在和 `shutil.which()` 的可用性检查。例如：

- `YouTubeCollector.is_available()` 目前只验证 `yt_dlp` 能导入；
- `BrowserCollector.is_available()` 主要验证 Node 和构建文件存在；
- 社媒采集器在任务执行后才从长输出判断登录过期。

建议在 Mangrove 内形成 Owner/版本/能力包维度的 `SourceProviderHealth`，而不是直接展示
Agent-Reach 的本机报告。

### 4.3 Skill 的渐进式路由说明

**建议吸收内容组织方式，不吸收强制触发规则。**

Agent-Reach 的 Skill 先给平台路由表，再按搜索、社媒、开发、网页、视频、金融等分类加载
reference，能减少模型上下文；命令和失败链由同一资料维护。

但其“任何互联网任务都必须使用本 Skill”的规则不适合 Mangrove。Mangrove 必须先冻结来源、
权限、模型、外发和能力清单，不能由 Skill 覆盖 TaskRevision 或 PolicyGate。

### 4.4 只读健康检查的凭据边界

**建议吸收测试思想。**

Agent-Reach 对配置文件做原子写、符号链接拒绝、大小限制和敏感字段脱敏；其测试还验证 Doctor
不会自动提取、刷新或持久化 Twitter、Reddit、小红书 Cookie。这些适合作为 Mangrove
AuthenticatedSourceConnection 和 Connector Doctor 的负向测试参考。

但 Mangrove 不能复用其 YAML Secret 存储。Cookie/API Key 仍只能以 Owner 隔离的 SecretRef
进入共享 Vault 边界。

### 4.5 上游选择与失效知识

**建议作为候选发现线索。**

Agent-Reach 当前维护的路线包括 Jina Reader、Exa、yt-dlp、bili-cli、OpenCLI、twitter-cli、
rdt-cli、xiaohongshu-mcp、gh、feedparser 等。其价值是持续记录哪个工具对哪个平台更合适，
以及某条路线何时失效。

Mangrove 可以定期只读查看其 Release/渠道变化，但每个上游仍要独立固定版本、digest、许可证、
权限、网络和测试。Agent-Reach 的推荐不能直接成为安装许可或安全背书。

## 5. 可以替换或增强哪些现有能力

| Mangrove 当前能力 | Agent-Reach 可带来的价值 | 判断 | 原因 |
|---|---|---|---|
| `collectors.registry` 的 tier/健康降权 | 有序平台后端、`active_backend`、修复处方 | **增强，不替换** | Mangrove 已有任务匹配和历史健康；Agent-Reach 缺少 Owner、任务、证据语义 |
| 各 Collector 的 `is_available()` | 真运行、损坏/超时/缺失分类 | **部分替换** | 这是最直接、最小且高收益的复用点 |
| `search_collector` | Exa 作为额外语义搜索候选 | **补充，不替换** | 现有 AnySearch/SearXNG/DDG/Tavily/HTML 路由更完整，且负责结果归一和正文补采 |
| `simple_http` / `article` / 匿名网页 SourceAcquisition | Jina Reader 公开网页读取 | **仅作外部兜底** | Jina 会产生第三方外发；不能替代 SSRF、精确范围、重定向检查、SourceSnapshot 和哈希 |
| `youtube_collector` | yt-dlp 配置诊断、字幕命令与版本知识 | **增强，不替换** | 两边底层都是 yt-dlp；Mangrove Collector 还要输出统一 CollectedItem/SourceArtifact |
| `rss_collector` | feedparser 安装与诊断 | **不替换** | Mangrove 已有 feed/sitemap 发现、正文抓取、去重、限速和统一输出 |
| `v2ex_collector` | 同一公开 API 的命令说明 | **不替换** | Mangrove 已有更完整的任务适配与结果结构 |
| `social_media_collector` / MediaCrawler | OpenCLI、xiaohongshu-mcp、bili-cli、twitter-cli、rdt-cli 等平台专用候选 | **按平台逐步替换** | 当前 MediaCrawler 有非商业使用边界；但 Agent-Reach 覆盖平台不完全，且仍需逐个上游审查 |
| `browser_collector` / Chrome DevTools MCP | OpenCLI 复用真实 Chrome 登录态 | **不能整体替换** | OpenCLI 是单机浏览器桥；缺少任务级浏览器隔离、来源范围、证据冻结和服务器多 Owner 模型 |
| CapabilityAcquisition | 候选渠道和安装说明 | **只补发现输入** | Agent-Reach 默认装入宿主/用户 Home；Mangrove 必须冻结 CapabilityPack 并隔离获取 |
| CapabilityCatalog / CapabilityHost | 无 | **不能替换** | Agent-Reach 不提供 digest、Owner scope、供应链门、只读挂载、Sidecar 租约和治理状态 |
| AgentKernel / CoreMind Adapter | 无 | **不能替换** | Agent-Reach 没有 Agent Loop、Run、Session、Checkpoint、Replay 或 Runtime 协议 |
| TaskRevision / WorkSession / AgentWorkTrace | 渠道状态文案可成为进度事件素材 | **不能替换** | 它没有持久任务真相、时间戳、Token 用量和可恢复事件模型 |
| Verifier / Candidate / Delivery | 无 | **不能替换** | 上游命令成功不能证明内容完整、质量合格或可正式交付 |

## 6. 小红书与扫码登录的专门判断

### 6.1 已验证事实

Agent-Reach 当前小红书后端顺序为：

```text
OpenCLI → xiaohongshu-mcp → xhs-cli
```

但其当前代码和文档明确限定：

- Agent-Reach 不替用户执行小红书登录；
- 不自动读取浏览器 Cookie；
- OpenCLI 只使用用户已经存在且明确控制的 Chrome 会话；
- Doctor 不运行平台命令，因此即使 OpenCLI 浏览器桥已连接，也只标记“登录态未实时验证”；
- xiaohongshu-mcp 可达，也只说明服务和接入存在，不证明登录有效；
- 存量 xhs-cli 的 Cookie 超过 7 天时只给出过期提示，不自动刷新。

来源：

- [Agent-Reach 小红书渠道](https://github.com/Panniantong/Agent-Reach/blob/06c202b03400a7d31886bf4399213706da1a0324/agent_reach/channels/xiaohongshu.py)
- [OpenCLI 健康检查](https://github.com/Panniantong/Agent-Reach/blob/06c202b03400a7d31886bf4399213706da1a0324/agent_reach/backends/opencli.py)
- [Cookie/登录边界测试](https://github.com/Panniantong/Agent-Reach/blob/06c202b03400a7d31886bf4399213706da1a0324/tests/test_doctor_credential_boundaries.py)

Agent-Reach 选用的上游 `xiaohongshu-mcp` 官方仓库提供独立登录程序、登录状态检查、搜索、推荐、
帖子详情和评论读取，也同时提供发布、评论、点赞、收藏等写操作。

来源：[xiaohongshu-mcp 官方仓库](https://github.com/xpzouying/xiaohongshu-mcp)

### 6.2 基于代码的推断

Agent-Reach **不能直接完成** Mangrove 已确认的产品流程：

```text
任务识别需要小红书
→ 读取当前 Owner 的连接版本
→ 明确证据判定 Cookie 失效
→ 暂停同一 Run
→ 在 OwnerActionRequest 展示二维码
→ 当前 Owner 扫码
→ 验证新登录态
→ 加密保存为该 Owner 的新连接版本
→ 原 Run 继续
```

原因不是上游完全没有登录能力，而是 Agent-Reach 没有 Mangrove 的 Owner、Run、二维码动作请求、
SecretRef、版本化连接和恢复状态机。

OpenCLI 直接复用某台桌面的 Chrome Profile，也不满足服务器多用户的默认隔离。它只适合作为未来
“Owner 专属桌面伴随连接器”候选，不能作为共享平台默认浏览器。

`xiaohongshu-mcp` 更适合作为受控 Source Worker 候选，但不能原样开放：它同时包含发布、评论、
点赞和收藏工具，默认服务鉴权还可以关闭。Mangrove 必须只允许经过 Schema 冻结的只读工具，
用任务网络和短期凭证保护调用，并把持久 Cookie 放在 Owner Secret 边界，而不是通用能力包或日志。

### 6.3 尚未验证的建议

后续 PoC 可直接评估 `xiaohongshu-mcp` 的精确 Release，而不是先安装整个 Agent-Reach：

1. 固定上游 commit/Release、二进制 SHA-256、许可证和浏览器依赖；
2. 只暴露 `check_login`、`search`、`list`、`detail` 等读取能力；
3. 对发布、评论、点赞、收藏等写工具做负向拒绝测试；
4. 把登录程序适配为 OwnerActionRequest，验证能否提取可展示二维码和明确成功状态；
5. 新 Cookie 只写入扫码 Owner 的 AuthenticatedSourceConnection；
6. 注销、过期、超时、平台故障和反爬分别映射，不能全部误报为 Cookie 失效；
7. 扫码成功继续同一 Run，服务重启后保持暂停并生成新二维码；
8. 原始返回必须形成 SourceArtifact、哈希、读取时间、平台 URL 和证据引用。

“上游登录程序可以无损转成网页二维码”目前尚未验证，不能从 README 的登录演示直接推断。

## 7. 不应直接采用的部分

### 7.1 不在服务器执行 `agent-reach install --system`

该模式会安装系统/全局依赖、写 Home 目录、Skill 和 MCP 配置。它适合个人工作站，不符合
Mangrove 的隔离获取、不可变 CapabilityPack 和任务执行环境分离。

### 7.2 不使用浮动 `main` 或 `latest`

官方安装说明从 `main.zip` 安装，部分上游命令使用 `@latest`。Mangrove 必须绑定精确 commit、
包版本和 digest。Agent-Reach 当前 `main` 已在 `v1.5.0` 后变化但版本字段未改变，进一步说明
仅记录 `1.5.0` 不足以恢复行为。

### 7.3 不复用其 Secret 存储

`~/.agent-reach/config.yaml` 的 owner-only 文件权限对个人 CLI 有意义，但不等同于平台 Vault、
Owner 隔离、轮换、撤销和审计。Mangrove 只能保存 SecretRef。

### 7.4 不让 Skill 绕过产品路由

Agent-Reach Skill 中的直接 shell 命令不能绕过 SourceAcquisition、CapabilityHost、Egress、
TaskRevision 和 Tool Policy。Skill 只能提供方法说明；真正可调用能力来自冻结目录。

### 7.5 不把 Doctor 当成业务验收

Doctor 有意避免真实登录平台调用，因此 `ok/warn/off` 只能表示环境和前置条件状态。真实任务仍需
目标平台的只读探针、非空结果、证据冻结和失败分类。

### 7.6 不把 Agent-Reach 的 MIT 许可证扩散到上游

Agent-Reach 自身是 MIT，不代表 OpenCLI、xiaohongshu-mcp、各 CLI、外部 API 和内容平台都适用
同一许可证或商业条款。每个 Capability 必须单独审查。

## 8. 最小接入路线

### 阶段 A：只吸收目录和体检模式

产物：

- 在现有 CapabilityCatalog/Collector Registry 中定义最小渠道健康投影；
- 对 2–3 个已有 Collector 把“存在检查”升级为无副作用真实探针；
- 前端展示“可用 / 已装但损坏 / 登录态未知 / 需重新认证 / 不可用”和修复动作；
- 不安装新平台工具。

建议先选择 `youtube`、`browser` 和一个登录态平台，因为三类失败语义不同。

### 阶段 B：公开来源小纵切面

选择一个 Agent-Reach 带来新增价值、又不涉及账号的上游，例如 bili-cli 或 Exa。按 Mangrove
现有流程完成：

```text
候选发现
→ 用户确认来源/版本/外发
→ 隔离获取
→ 冻结 CapabilityPack
→ Source Adapter
→ SourceSnapshot
→ Candidate/Verifier
```

若只是 Jina、yt-dlp、feedparser 或 V2EX，现有能力已经覆盖，不应为了“接入 Agent-Reach”重复
实现。

### 阶段 C：小红书认证来源纵切面

以 `xiaohongshu-mcp` 精确版本做只读 Source Worker 候选，完成扫码、Owner 隔离、过期检测、同
Run 恢复、写工具拒绝和证据冻结。Agent-Reach 只提供后端选择与健康知识，不进入业务数据通道。

### 阶段 D：持续更新

不要自动跟随 Agent-Reach `main`。每次上游更新只做：

1. 读取 Release/渠道 diff；
2. 识别新增、换序、退役和安全边界变化；
3. 为受影响的 Mangrove Capability 建立新候选版本；
4. 重跑对应渠道契约和黄金任务；
5. 用户确认后只让新 Run 使用新版本。

无需 fork Agent-Reach，也无需把它的全部 Python 代码复制进 Mangrove。

## 9. PoC 完成门

### 9.1 公共来源

- 精确版本和 digest 可重放；
- 原任务范围外 URL 被拒绝；
- 第三方外发在 TaskRevision 中明确；
- 原始内容、读取时间、最终 URL 和 SHA-256 被保存；
- 后端失败会转到已批准备选，不能静默扩大权限或数据外发；
- 空结果、部分结果、网络故障和登录要求不被当作成功；
- 取消后工具进程和 Sidecar 零残留。

### 9.2 小红书

- 用户 A 扫码产生的连接，用户 B 绝对不可见、不可用；
- Cookie 有效时后续任务复用，不重复扫码；
- 有明确登录失败证据时暂停并展示二维码；
- 网络错误/平台故障只显示“登录状态无法确认”；
- 扫码成功后继续原 Run，不创建新 WorkSession；
- 服务重启后旧二维码作废，任务仍暂停；
- 所有写工具被硬拒绝；
- Cookie、二维码密钥、浏览器路径和原始工具日志不进入模型上下文或普通 Trace；
- 结果先形成 SourceSnapshot 与 Candidate，经独立 Verifier 后仍须通过既有 Publisher、完整性与
  QA 门；只有进入 `delivery_published` 的 `output_id` 才是正式 Delivery。

## 10. 需要用户确认的决策

本调研不要求立即确认接入。未来进入实现前，需要用户确认：

1. 是否采用“吸收目录/体检模式，不整包安装 Agent-Reach”的方向；
2. 第一个公开来源 PoC 选择哪个平台；
3. 小红书是否允许以 `xiaohongshu-mcp` 作为只读候选；
4. 小红书账号使用、平台条款、封号风险和访问范围；
5. 真实账号、Cookie/Secret、外部网络和数据外发授权；
6. 新第三方依赖的精确版本、许可证和供应链结果；
7. 任何真实外部发布或不可逆操作。

## 11. 最终建议

### 已验证事实

- Agent-Reach 是渠道选择、安装、体检和 Skill 层，不是统一抓取 Runtime；
- 其 MCP 只暴露状态，不暴露各平台读取能力；
- 它维护 15 个渠道、有序后端和真实命令探测；
- 当前小红书路线不替用户登录，不能直接满足 Mangrove 扫码恢复流程；
- Mangrove 已有 SourceAcquisition、Collector Registry、CapabilityAcquisition、Catalog、Host、
  Owner 隔离与 Delivery 边界。

### 基于代码的推断

- 直接安装整包会带来宿主污染、单用户配置、浮动上游和证据旁路；
- 将 Agent-Reach 当作“持续维护的候选知识源”，把具体上游逐个接入 Mangrove，收益最大、风险最小；
- 现有采集器最先受益的是健康检查和平台后端路由，而不是抓取实现重写；
- 小红书应直接验证 `xiaohongshu-mcp` 的只读 Worker 能力，Agent-Reach 只承担选型参考。

### 尚未验证的建议

- 先做阶段 A 的渠道健康投影，再做一个公共来源 PoC；
- CoreMind ready 后，再把认证来源纵切面接入统一 WorkSession/AgentWorkTrace；
- 在真实账号授权前，不运行小红书、Twitter、Reddit 等登录态平台测试；
- 不为同步 Agent-Reach 建新插件市场、自动更新器或第二套能力目录。
