# AC-06 本地真实 Adapter 执行报告

> 日期：2026-08-04，2026-08-05 完成 Capability Host Sidecar 纠偏，2026-08-06 完成管理员灰度选择闭环并通过用户验收
> 状态：`ac06_user_accepted`
> 边界：完成本地 Python、Node、CLI、无脚本 Skill 与 stdio MCP 的真实 Adapter；
> 未启用远程 MCP、外部 Secret、Registry 自动发现或面向普通用户的平台发布

## 1. 本轮结论

AC-06 的包准备、格式冻结、健康检查、调用、取消和清理已使用成熟开源真实样例验证；
没有重新实现包管理器或 MCP 协议。Windows Docker Desktop 的 bind mount 无法靠容器内
降 UID 隔离，因此用户确认采用一个任务级 Capability Host Sidecar：同一任务的多个原生
Python、Node、CLI 与本地 MCP 能力进入一个 Sidecar，Pi 只持有短期 Relay URL/Token。
Sidecar 不挂载业务来源、模型配置或 Docker Socket；无原生能力的任务不创建 Sidecar。

2026-08-06 已补齐管理员灰度选择纵切面：生产库执行纯新增 CapabilityCatalog 迁移并保留
迁移前备份；Python 表格汇总 Tool 与官方 Everything MCP 2026.7.4 以不可变 OCI digest
登记为仅管理员可选择的预验证灰度能力。工作台可以列出和选择能力，创建任务时冻结到
TaskRevision；后续 V2 或独立任务只继承原 digest，不静默升级。OCI 单归档安全展开会拒绝
路径穿越、链接、设备文件和超预算内容；重新物化后的两个包已在同一个真实 Sidecar 中调用。

2026-08-06 用户已确认从 8088 工作台完成 AC-06 灰度验收并通过。该确认解除 AC-07 的真实
任务前置门，但不会把两项预验证包自动晋级为 AC-07 `verified`；具体 TaskRef、结果和清理证据
将在 AC-07 ValidationRun 中按 digest 重新绑定。`pi_capability_host_enabled` 的代码默认值保持
关闭，无选择的现有任务路径保持不变；AC-05 获取状态机自动产出并选择 TaskRevision 尚未贯通。
本机未提交的 `.env` 只开启管理员灰度，普通用户权限未扩大。

## 2. 已实现

- `CapabilityRuntimeManifest`：冻结 kind、版本、直接 argv、相对路径、健康检查、超时和最小权限；
  禁止 Shell 解释器、路径越界、Secret 环境变量和未知权限。
- Python：要求 `pyproject.toml`、`uv.lock`、精确 patch `.python-version`，执行
  `uv lock --check` 与 `uv sync --frozen --no-dev --no-editable`。
- Node：要求 `package-lock.json`，执行 `npm ci --ignore-scripts`。
- CLI：冻结官方 Release 引用、平台、架构、资产 SHA-256 与解包入口 SHA-256；拒绝符号链接。
- Skill：生产门校验 Agent Skills frontmatter、名称、目录边界、文件数量和大小；真实样例
  另由固定提交的 `skills-ref validate` 复核。含脚本的 Skill
  必须拆成独立可执行能力，不能借 Markdown 外壳绕过安全门。
- 本地 MCP：复用官方 Python MCP SDK 的 legacy stdio session，覆盖 initialize、ping、
  tools/list、调用、超时、取消和关闭；modern 协议由官方 Node v2 客户端/服务端独立探针验证，
  尚未接入同一个生产 Adapter。
- 命令 Runtime：只继承 PATH、语言和临时目录等最小环境；实时限制 stdout/stderr 各 1 MiB，
  超时或取消后先 TERM、三秒后 KILL。
- Capability Host：一个任务/Run 只创建一个 Sidecar；能力包只读挂载，容器只加入既有任务
  内部网络，使用只读根文件系统、`cap-drop ALL`、`no-new-privileges`、PID/内存/CPU 上限和
  `noexec` 临时目录；恢复只替换同一确定性 Host。
- Pi Bridge：按冻结能力注册 Tool，命令使用直接 argv，本地 MCP 同时支持 legacy 与 modern
  官方 SDK；Token 不进入 Prompt、事件或 Docker argv。Pi 容器不再挂载原生能力目录。
- 生命周期：所有 Docker 操作有界超时；取消先停止/强制删除 Host，再回收任务网络；删除失败
  时失败关闭并保留运行目录证据。命令断连会回收进程组，MCP 超时/断连会关闭 session，响应
  统一限制为 1 MiB。
- 灰度门：远程 MCP 继续失败关闭；开关默认关闭；无原生能力和只读无脚本 Skill 路径不变。
- 产品接缝：管理员专属能力目录接口、工作台多选、TaskRevision 冻结、Owner 校验和版本继承
  已接通；普通用户访问拒绝，开关关闭时管理员接口也返回 409。
- OCI 装载：灰度能力使用唯一 `mangrove-capability.tar` 负载，解包后必须存在运行清单；
  路径穿越、链接/设备、文件数和展开大小均失败关闭。

## 3. 真实样例证据

执行：`E:\python3.13\python.exe scripts/verify_ac06_real_adapters.py`

| 类型 | 冻结样例 | 结果 |
|---|---|---|
| Python | Pygments 2.19.2 / CPython 3.13.7 | 健康检查 2.19.2，确定性输出通过；热同步 287.47 ms |
| Node | Prettier 3.9.6 / Node 22.22.1 / npm 10.9.4 | `npm ci --ignore-scripts`，确定性输出通过；热安装 2486.16 ms |
| CLI | fd 10.4.2 Linux x86_64 | Release digest `def59805…3143c8` 与入口 digest 通过 |
| Skill | Agent Skills 最小样本 | `skills-ref` 固定提交 `217be548…a90af` 校验通过，无脚本 |
| MCP | Everything MCP 2026.7.4 | Sidecar 内 legacy/modern 通过；13 个工具、echo、session 复用/关闭通过 |

固定 Pi 镜像 ID 为 `sha256:a241e5e4…aaf4316`。隔离探针复现降 UID 后仍可读 bind mount，
并据此采用不挂载来源/模型配置的独立 Sidecar。真实 Pi Bridge 加载、命令调用、Everything
MCP 13 个工具发现、echo、session 复用/关闭以及 legacy/modern 协议均通过；验证临时目录、
Sidecar 容器和任务网络均已删除。

自动化门：

- 最终 Capability Host 专项：8 passed；
- 最终审查修复后，CapabilityCatalog、Adapter、Capability Host、Agent Runtime、工作台 API
  与进度组合回归 103 passed；此前同一工作树全仓后端 1210 passed、4 skipped；
- Node `--check`、Python `py_compile` 与相关 `git diff --check` 通过；
- `scripts/verify_ac06_real_adapters.py` 五类真实样本通过，修复后又以
  `--mcp-host-only` 重跑 Sidecar MCP 门通过。
- 2026-08-06 管理员灰度闭环：CapabilityCatalog/Adapter/Host/工作台 Pi API 聚焦集合
  `78 passed`；最终目录与 Pi API 回归 `37 passed`；前端生产构建通过；完整工作台文件
  Playwright `22 passed`。
- `scripts/prepare_ac06_gray_capabilities.py --apply` 连续执行可复现相同两项 OCI digest；
  从 OCI 重新物化后，单 Sidecar 内 Python Tool 与 Everything MCP 调用均通过，临时容器和
  网络清理完成。证据位于 `.artifacts/ac06-gray/prepared-capabilities.json`，不纳入 Git。

## 4. 已验证事实、推断与建议

已验证事实：五类本地真实样例、legacy/modern MCP、Pi Bridge、环境变量不泄漏、输出上限、
取消清理、Docker 超时、删除失败关闭和零残留均有本机证据；默认开关为关闭。

基于代码和运行环境的推断：Sidecar 通过“不挂载”而非容器内权限字符串隔离业务来源和
`models.json`，比在 Pi 容器内降权更可靠；用户灰度通过不替代 AC-07 按精确 digest 重放与
供应链验证。

尚未验证的建议：下一步按已确认的 AC-07 增量规格，把 Python 表格 Tool 与 Everything MCP
作为两条真实验证/平台快照纵切面；不应因 AC-06 验收通过而默认开启 Sidecar、发布平台能力或
扩大普通用户权限。

## 5. 未完成边界

- Sidecar 代码默认值仍关闭；用户已确认 8088 工作台灰度验收通过；
- 管理员预验证包已完成 OCI → TaskRevision → Sidecar 工程闭环；AC-05 Acquisition 自动产出
  并进入这一闭环尚未完成；
- 远程 MCP 的 ConnectionRef/SecretRef、逐任务外发确认与精确网络目标尚未实现；
- MCP Registry DiscoveryFeed、私有目录冻结和更宽泛的恶意/故障矩阵未完成；
- AC-07 决策与增量规格已确认，验证晋级、SBOM/签名、个人到平台发布尚未实现；
- 已执行纯新增生产目录迁移并备份原库，只登记两项管理员灰度包；未执行面向普通用户的
  平台能力发布、外部内容发布或不可逆历史迁移。
