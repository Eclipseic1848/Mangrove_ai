# AC-06 真实 Adapter 官方来源复核

> 日期：2026-08-04
> 阶段：AC-06 实施前调研；不构成实现、外部下载、依赖安装、网络放行或生产发布授权
> 范围：Python、Node、CLI、本地 stdio MCP、Agent Skills 适配语义
> 来源边界：只使用上游官方文档、官方仓库和正式规范

## 1. 结论摘要

2026-08-02 的总体方向仍然成立：Python 使用 uv 锁定项目，Node 使用
`package-lock.json + npm ci`，CLI 固定官方 Release 资产与摘要，本地 MCP 使用 stdio，
Skill 遵循 Agent Skills 目录并执行 `skills-ref validate`。本次复核补充了以下关键约束：

1. **Python 冻结不能只运行 `uv sync --frozen`。** `--frozen` 只保证不更新 lock，且不会检查
   `pyproject.toml` 与 lock 是否一致；冻结门应先执行 `uv lock --check` 或
   `uv sync --locked`，业务阶段才可从已验证材料执行 frozen/offline 恢复。`uv.lock` 也不固定
   Python 解释器二进制，解释器版本、来源和 digest 必须单独冻结。
2. **Node 首次安装仍应默认 `npm ci --ignore-scripts`。** npm 11 已提供更细的
   `allowScripts`、`strict-allow-scripts`，但显式 `npm run <script>` 即使开启
   `ignore-scripts` 仍会运行目标脚本；脚本执行必须是单独的声明和权限门。
3. **CLI 不能把“GitHub Release URL”当成完整信任证据。** 应同时冻结 owner/repo、tag、平台、
   架构、资产名、最终 URL 和 SHA-256；若上游启用了 Immutable Releases 或 Artifact
   Attestations，再额外执行官方验证，不能用它替代本地 digest 比对。
4. **stdio MCP 必须兼容两代协议。** 截至本报告日期，当前协议已是 `2026-07-28`：取消
   session 级 `initialize`，改用每请求 `_meta` 和 `server/discover`。Adapter 应先做 modern
   探测并兼容 2025-era initialize fallback；健康不是“进程存在”，而是时代协商和声明能力的
   只读探针通过。stdio 请求取消仍使用 `notifications/cancelled`，随后回收完整进程树。
5. **`skills-ref validate` 只验证格式。** Agent Skills 规范确实推荐它，但官方仓库明确说明
   `skills-ref` 是演示性 reference library、并非生产组件。它可以做 conformance gate，不能
   替代脚本扫描、权限、来源、digest、Owner 隔离和真实验证。

## 2. 与 2026-08-02 调研的差异

| 原结论 | 本次判断 | 修订 |
|---|---|---|
| `uv sync --frozen` 可作为冻结同步 | 方向正确但表述不完整 | 获取/冻结门先 `uv lock --check` 或 `uv sync --locked`；`--frozen` 仅用于完整材料已被冻结后的恢复 |
| uv 固定解释器与独立环境 | 只固定依赖仍不够 | 追加精确 Python 版本、实现、平台/架构和解释器或基础镜像 digest |
| `npm ci` + 默认禁安装脚本 | 仍准确 | npm 11 可用 `allowScripts` + `strict-allow-scripts` 做显式白名单；第一门仍是 `--ignore-scripts` |
| 本地 stdio MCP 在任务期间启动一次、健康检查、取消回收 | 方向正确但协议基线已变化 | 当前 `2026-07-28` 使用 `server/discover` 与每请求 `_meta`；兼容 2025 initialize fallback，并明确取消与三级退出 |
| `skills-ref validate` 做 Skill 校验 | 规范层面准确 | 官方工具为 demo only，只能验证格式，不得描述为生产安全验证器 |

## 3. Python Adapter：uv 锁定与独立解释器

### 3.1 已验证事实

- uv 项目命令默认会自动 lock 和 sync；`--locked` 要求现有 lock 与项目元数据一致，否则失败；
  `--frozen` 使用现有 lock、跳过新鲜度检查且不更新 lock。缺少 lock 时 `uv sync --frozen`
  会失败，但 `pyproject.toml` 后续新增且未进入 lock 的依赖不会自动出现在环境中：
  [uv Locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/)、
  [uv CLI reference](https://docs.astral.sh/uv/reference/cli/#uv-sync)。
- `uv sync` 默认执行 exact sync，会移除 lock 中不存在的额外包；默认仍会同步 `dev` group，
  项目通常以 editable 方式安装，因此生产能力环境需要显式决定 `--no-dev`、group/extras 和
  `--no-editable`：
  [uv Locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/)。
- `.python-version` 提供默认 Python 版本请求，`--managed-python` 可拒绝回退到系统 Python；
  uv 管理的 CPython 来自 Astral 使用的 `python-build-standalone` 分发，而不是 Python 官方发布
  的二进制：
  [uv Python versions](https://docs.astral.sh/uv/concepts/python-versions/)。
- `--offline` 会禁止网络，只使用本地缓存和已安装材料：
  [uv CLI reference](https://docs.astral.sh/uv/reference/cli/#uv-sync)。

### 3.2 基于项目的推断

Mangrove 第一版 Python Adapter 应分两段：

1. 获取/构建阶段：验证精确 `pyproject.toml`、`uv.lock`、`.python-version` 和 uv 版本；运行
   `uv lock --check`，在受控网络内构建非 editable 环境并记录全部输入 digest。
2. 业务阶段：优先直接挂载已经验证的只读环境；确需重建时，只允许固定 uv 和解释器执行
   `uv sync --frozen --offline --no-dev --no-editable`，且环境创建位置必须在任务 Lease 内。

`uv.lock` 固定的是依赖解析，不是解释器供应链。CapabilityPack 还应保存 Python 实现、精确
patch 版本、OS/arch，以及解释器归档或基础镜像 digest。仅保存 `requires-python = ">=3.11"`
不足以复现同一个运行环境。

### 3.3 本机真实样本建议

首个 Python 样本建议使用 **Pygments**：官方文档同时提供稳定的 Python API 和
`pygmentize` CLI，可用一段固定 Python 文本生成确定性 HTML，样本小、无需外部服务和 Secret，
适合验证冷/热安装、import、只读输入、固定输出和取消前后的环境清理：
[Pygments Quickstart](https://pygments.org/docs/quickstart/)。版本必须在实施时从官方索引解析后
写入 lock，本报告不建议浮动 `latest`。

## 4. Node Adapter：npm ci 与脚本门

### 4.1 已验证事实

- `npm ci` 要求已有 `package-lock.json`；manifest 与 lock 不一致时失败，不修改两者，并在安装
  前移除现有 `node_modules`：
  [npm ci](https://docs.npmjs.com/cli/v11/commands/npm-ci/)。
- `--ignore-scripts` 禁止运行 `package.json` 中的生命周期脚本，但显式执行 `npm run`、
  `npm test` 等目标脚本仍会运行该目标，只是不运行其 pre/post 脚本：
  [npm ci - ignore-scripts](https://docs.npmjs.com/cli/v11/commands/npm-ci/#ignore-scripts)。
- npm 11 支持 `allowScripts`、`strict-allow-scripts` 和
  `dangerously-allow-all-scripts`。项目级策略应写入 `package.json` 或 `.npmrc`；在项目级
  `npm ci` 命令行直接传 `--allow-scripts` 会报错：
  [npm ci configuration](https://docs.npmjs.com/cli/v11/commands/npm-ci/)。
- lock 中的 `resolved` 和 `integrity` 用于记录来源与 SRI 完整性，但它不表达 Mangrove 的
  Owner、网络、文件系统和业务数据权限：
  [package-lock.json](https://docs.npmjs.com/cli/v11/configuring-npm/package-lock-json/)。

### 4.2 基于项目的推断

- 默认路径必须是 `npm ci --ignore-scripts`；安装成功后从冻结环境调用本地
  `node_modules/.bin/<tool>`，不得在业务任务中执行裸 `npx -y`。
- 若包确实依赖安装脚本，应先在 `--ignore-scripts` 门识别差异，再由 CapabilityPack 显式声明
  被允许的依赖身份和用途，并在隔离构建阶段使用 `allowScripts` 与
  `strict-allow-scripts=true` 重建。禁止使用 `dangerously-allow-all-scripts`。
- 必须冻结 Node 和 npm 的精确版本；创建 lock 时使用的会影响依赖树的选项，应通过项目
  `.npmrc` 一并冻结，不能依赖宿主全局配置。

### 4.3 本机真实样本建议

首个 Node 样本建议使用 **Prettier**。官方明确要求本地安装精确版本，并警告未安装时裸
`npx` 会临时下载最新版本；可对固定 JSON/Markdown 执行 stdin/stdout 格式化，适合验证
lock、`npm ci --ignore-scripts`、只读输入、确定性输出和二次零下载：
[Prettier installation](https://prettier.io/docs/install/)、
[Prettier CLI](https://prettier.io/docs/cli/)。

## 5. CLI Adapter：官方 Release 与 digest

### 5.1 已验证事实

- GitHub Release 是基于 tag 的可分发版本；Release 资产与 GitHub 自动生成的 source archive
  不是同一种对象：
  [GitHub About releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)。
- 对启用了 Immutable Releases 的项目，GitHub CLI 可用 `gh release verify <tag>` 验证
  release 不可变，并用 `gh release verify-asset <tag> <path>` 验证本地文件与 Release 资产一致；
  自动生成的 source zip/tarball 不支持该资产验证：
  [GitHub Verifying release integrity](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/verify-release-integrity)。
- 若上游发布 Artifact Attestation，可用 `gh attestation verify <artifact> -R <owner/repo>`
  验证来源证明；attestation 说明构建来源，不保证软件本身安全：
  [GitHub Artifact attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations)。

### 5.2 基于项目的推断

CLI Adapter 必须保存并校验：`owner/repo`、精确 tag、资产名、平台、架构、原 URL、最终 URL、
字节数、SHA-256、解包后入口相对路径、入口 SHA-256 和 `--version` 探针结果。摘要必须从实际
下载字节重新计算；Release 页面展示的 digest、上游 checksum、Immutable Release 和
attestation 都是附加证据，不能替代 Mangrove 自己的 digest。

归档解包必须拒绝绝对路径、`..` 穿越、越界符号链接和额外可执行入口。业务阶段只加载被冻结
的只读文件，不再次访问 GitHub。

### 5.3 本机真实样本建议

首个 CLI 样本建议使用 **fd**：官方 GitHub Release 提供 Windows 和 Linux 的小型预编译归档，
Release 资产展示 SHA-256，运行无需 Secret。可在任务临时目录构造 5 个固定文件，用精确路径、
类型和 glob 查询验证输出，同时覆盖错误架构、摘要篡改、Zip Slip、超时和取消：
[fd official releases](https://github.com/sharkdp/fd/releases)。实施时只选择精确 tag 和资产，
不得使用 `latest/download`。

## 6. 本地 stdio MCP Adapter：双时代、健康和取消

### 6.1 已验证事实

- MCP 当前协议版本是 `2026-07-28`。该时代使用无状态、自包含请求和逐请求能力协商；每个请求
  的 `_meta` 声明协议版本，`server/discover` 是强制由 Server 提供、但 Client 可选择是否先调用
  的发现 RPC：
  [MCP Versioning](https://modelcontextprotocol.io/docs/2026-07-28/learn/versioning)、
  [MCP 2026-07-28 Specification](https://modelcontextprotocol.io/specification/2026-07-28)。
- 官方 TypeScript SDK 将 2024-10-07～2025-11-25 称为 legacy era，将 2026-07-28 称为
  modern era。modern 不再运行 `initialize`，先用 `server/discover`；`mode: auto` 可探测 modern
  并 fallback 到 legacy initialize：
  [MCP TypeScript SDK protocol versions](https://ts.sdk.modelcontextprotocol.io/v2/protocol-versions)。
- 在官方 SDK 的 stdio transport 上，auto probe 会用一个短生命周期 sibling process 探测，随后
  才启动会话进程；这避免旧 Server 因收到 initialize 前未知方法而破坏真正会话。对每次调用都
  spawn 的 CLI，官方不建议默认 auto；Mangrove 的任务级长驻进程可承担一次探测，但必须计入
  冷启动和进程回收证据：
  [MCP TypeScript SDK protocol versions](https://ts.sdk.modelcontextprotocol.io/v2/protocol-versions)。
- modern stdio Server 应使用 SDK v2 的 `serveStdio(factory)`；直接把 `McpServer` 连接到
  `StdioServerTransport` 只服务 2025 era。`serveStdio` 默认同时服务两代，也可显式拒绝 legacy：
  [Supporting protocol revision 2026-07-28](https://ts.sdk.modelcontextprotocol.io/v2/migration/support-2026-07-28)、
  [serveStdio API](https://ts.sdk.modelcontextprotocol.io/v2/api/%40modelcontextprotocol/server/server/serveStdio.html)。
- stdio 仍由 Client 启动 Server 子进程，通过 stdin/stdout 交换逐行 UTF-8 JSON-RPC；stdout 是
  协议通道，日志写 stderr。2025 Transport 规范明确了该 framing，v2 官方教程仍要求 stdout
  只放协议内容：
  [MCP 2025-11-25 Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#stdio)、
  [MCP SDK v2 stdio tutorial](https://ts.sdk.modelcontextprotocol.io/v2/get-started/first-server)。
- 对 stdio，两代协议在请求超时/中止时仍发送 `notifications/cancelled`；2026 Streamable HTTP
  才改为关闭该请求的 SSE 响应流，不能把 HTTP 取消方式套到 stdio：
  [Supporting protocol revision 2026-07-28](https://ts.sdk.modelcontextprotocol.io/v2/migration/support-2026-07-28)。
- legacy stdio 的兼容关闭顺序是 Client 先关闭子进程 stdin，合理时间内不退出再 TERM，最后
  KILL；modern 规范不再定义 session shutdown RPC，因此 Host 仍需拥有并回收底层子进程：
  [MCP 2025-11-25 Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle#stdio)。

### 6.2 基于项目的推断

本地 MCP 的最小状态机应为：

```text
starting → initializing → ready → draining → stopped
                    ↘ unhealthy → terminating → killed/stopped
```

- `ready` 的门是：modern `server/discover` 或 legacy `initialize → initialized` 成功，再对声明
  capability 执行至少一个只读探针（例如 `tools/list`）并验证响应结构。2025-era `ping` 可作为
  legacy 附加探针，但不能假设所有 modern Server 以它作为统一健康 RPC。
- 一个任务环境内同 digest 的 Server 只启动一次，多个 tool call 复用同一 session；不能为每个
  call 新建容器。不同 Owner/Run 不共享可变 session。
- 请求有 soft timeout 和 absolute timeout；soft timeout 发送 stdio 协议取消，absolute timeout 进入
  stdin close → terminate → kill。Windows 上实现可用等价的进程树终止语义，但产品状态仍按
  上述协议阶段记录。
- stderr 是诊断流，不能因为有 stderr 就判失败，也不能把 stderr 原文直接暴露给普通用户。
- Server schema、说明与返回内容均视为不可信输入；协议健康通过不等于业务验证、安全验证通过。

### 6.3 本机真实样本建议

legacy 样本建议使用官方 **Everything MCP Server** 的精确 npm 版本。官方明确说明它是为
MCP Client 开发者覆盖 prompts、resources、tools 等协议能力的测试 Server，不是生产工具；
这恰好适合作为 legacy Adapter conformance fixture：
[Everything Server](https://github.com/modelcontextprotocol/servers/blob/main/src/everything/README.md)。

必须先在获取阶段生成独立 lock 并用 `npm ci --ignore-scripts` 冻结，运行时直接调用本地入口，
不能照 README 使用浮动 `npx -y`。modern 样本使用官方 TypeScript SDK v2 的极小
`serveStdio(factory)` fixture，并固定 SDK/Node/npm lock。验证用例至少覆盖 modern discover、
legacy fallback、tools/list、一个无副作用 tool、请求取消、进程退出、强杀和 stdout 污染拒绝。

## 7. Agent Skills Adapter 与 skills-ref

### 7.1 已验证事实

- Agent Skills 最小目录含 `SKILL.md`；`scripts/`、`references/`、`assets/` 等为可选。规范允许
  额外文件和目录：
  [Agent Skills Specification](https://agentskills.io/specification#directory-structure)。
- `name` 与 `description` 必填，name 必须匹配父目录名；`allowed-tools` 是实验字段，不同客户端
  支持不同：
  [Agent Skills Frontmatter](https://agentskills.io/specification#frontmatter)。
- 官方规范推荐 `skills-ref validate ./my-skill`，它检查 frontmatter 和命名约定：
  [Agent Skills Validation](https://agentskills.io/specification#validation)。
- `skills-ref` 当前为 0.1.0 的 Python reference library，官方 README 明确标记为
  demonstration only、not meant for production：
  [skills-ref README](https://github.com/agentskills/agentskills/tree/main/skills-ref)、
  [skills-ref pyproject](https://github.com/agentskills/agentskills/blob/main/skills-ref/pyproject.toml)。

### 7.2 基于项目的推断

- Skill Adapter 可以把 `skills-ref validate` 作为第一道格式门，但要按精确官方 commit 和
  自己的 uv.lock 冻结，不能每次从 main 安装。
- 生产安全门仍需 Mangrove 执行：路径边界、文件数量/大小、UTF-8、符号链接、脚本和二进制
  inventory、Secret 扫描、网络/文件权限、Owner、digest、真实执行与失败关闭验证。
- `allowed-tools` 只能作为作者意图提示，不能扩大 CapabilityPack Grant；Mangrove 权限始终取
  平台编译结果与 Skill 声明的交集。
- Skill 内 `scripts/` 继续路由到 Python/Node/CLI Adapter；不能因为外层是 Markdown 而直接执行。

### 7.3 本机真实样本建议

先从官方规范示例生成一个最小、无脚本、只含 `SKILL.md + references/` 的“固定 JSON 字段说明”
Skill；同时准备 name 不匹配、非法 frontmatter、越界引用、超大文件、含脚本五类负例。
这个样本的目标是验证格式和 Mangrove 的附加安全门，不宣称它是第三方生产 Skill。

`skills-ref` 自身应按官方仓库精确 commit 获取并锁定为工具依赖。由于官方明确声明 demo only，
第一版不得让它成为运行时常驻依赖或唯一成功判据。

## 8. 推荐的 AC-06 最小真实验证矩阵

| Adapter | 样本 | 正向证据 | 必测失败 |
|---|---|---|---|
| Python | Pygments | locked/managed Python、冷/热同步、import、确定性 HTML、只读运行 | lock 过期、离线缺包、错误 Python、篡改环境 |
| Node | Prettier | exact package、`npm ci --ignore-scripts`、stdin/stdout、二次零下载 | lock 不一致、生命周期脚本、浮动 npx、超预算 |
| CLI | fd Release 资产 | tag/asset/platform/arch/SHA-256、`--version`、固定目录查询 | 摘要篡改、错架构、路径穿越、取消残留 |
| MCP | Everything legacy + SDK v2 modern fixture | discover/initialize 双时代、tools/list、复用、取消、三级退出 | 探测超时、stdout 污染、忽略取消、sibling/会话孤儿进程 |
| Skill | 规范最小 Skill + skills-ref | 格式通过、渐进读取、digest、Owner、无脚本执行 | name 错配、越界引用、超限、脚本未声明 |

统一采集以下指标：首次获取时长、二次缓存命中时长、能力包大小、运行 RSS/CPU、启动/首次调用
延迟、取消到进程树归零时间、网络目标清单和临时目录残留。所有样本只使用合成数据，不挂载用户
来源、不注入业务 Secret。

## 9. 实施前仍需用户确认的决策

1. 是否允许 AC-06 获取阶段访问 PyPI、npm Registry 和上述官方 GitHub Release 精确目标；
2. 是否接受 Pygments、Prettier、fd、Everything MCP 和最小本地 Skill 作为第一批真实样本；
3. 是否允许任何候选运行安装脚本；本调研建议第一批全部不允许，需脚本的候选另行审批；
4. Python 解释器采用固定任务镜像内版本，还是 uv-managed Python 精确分发；
5. 第一批只做本地 stdio MCP，远程 MCP、Secret 与业务外发继续后置。

这些决策会改变网络、可执行代码和供应链范围，不能由实现阶段自动推断。

## 10. 最终建议

AC-06 不需要自创包管理器、MCP 协议客户端或 Skill 格式。复用 uv、npm、官方 MCP SDK/规范、
GitHub Release 验证和 Agent Skills 规范；Mangrove 只实现统一 Adapter Interface、权限编译、
digest 冻结、进程生命周期、Owner 隔离和验证证据收集。

建议第一批范围严格限定为本地无 Secret 的五个样本。它足以证明 CapabilityPack Interface 能
隐藏 Python、Node、二进制、长驻协议进程和声明式 Skill 的不同运行机制，同时不提前引入远程
MCP、OAuth、企业数据源或平台发布。
