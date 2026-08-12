# Phase 4B 批次 8B-1 Docker Desktop 服务器就绪包技术规格

> 日期：2026-07-28
>
> 状态：规格历史上已确认；2026-07-28 用户决定后置，当前不实施
>
> 当前阶段：暂停；待工程功能完成且目标服务器条件明确后重新评估
>
> 实施环境：Windows 开发机 + Docker Desktop + Linux containers
>
> 依据：
> [批次 8 决策记录](2026-07-28-phase4b-batch8-framework-decisions.md)、
> [8B-1 技术调研](../research/2026-07-28-phase4b-batch8b1-docker-readiness-research.md)、
> [ADR-0016](../adr/0016-docker-desktop-unified-development-and-clean-image-gate.md)

> 调度修订：本规格不能替代最终服务器配置与实机验收。用户明确当前不继续干净镜像、
> Linux 依赖、Compose、并发或故障验证；曾启动的依赖构建已停止，未完成代码已撤回。
> 本文仅保留为后续重新设计时的输入，不构成开工授权。

> vNext 关系（2026-07-29）：Agentic Runtime 后续所需的任务级 Docker 功能沙箱只隔离
> 临时代码，不激活本规格的整机 Docker Desktop、干净应用镜像或服务器就绪范围。两者
> 必须分开设计、验收和授权。

## 1. 目标

在不提前实施 Phase 5B、不依赖尚未准备的目标服务器、不改写既有生产资料的前提下，
交付一套能在当前开发机真实运行和验收的 Phase 4B 服务器就绪包：

1. 日常使用 Docker Desktop 一键启动当前 React、FastAPI、内嵌工作台 worker、Phoenix
   和现有辅助容器；
2. 保留前后端热更新；
3. 现有用户、任务、上传、结果和配置能在 Linux 容器中继续使用；
4. 自动构建不挂载宿主源码的干净镜像并完成真实工作台闭环；
5. 在隔离环境完成 10–20 VU 并发正确性、可恢复故障和备份恢复；
6. 生成普通用户可理解、管理员可溯源的中文 Markdown/HTML 报告；
7. 明确保留所有待 8B-2 实机验收项，不把本机结果冒充服务器结论。

## 2. 非目标

本批次不实施：

- PostgreSQL、RabbitMQ、Celery、Redis 或多实例分布式 worker；
- 从 FastAPI 中拆出独立工作台 worker；
- GPU 租约、调度、吞吐或容量管理；
- 最终 Ubuntu、NVIDIA 驱动、CUDA、RAID、TLS、公网或防火墙配置；
- 最终全工程 Compose、离线镜像归档或正式发布包；
- 聊天、全网采集、MediaCrawler、SearXNG、Firecrawl、RSSHub 的业务验收；
- 移动端或多浏览器验收；
- 修复与本规格无关的历史业务问题；
- 创建版本、标签、外部发布或推送。

SearXNG、Firecrawl、RSSHub 只随日常一键入口启停，不进入本批业务通过条件。

## 3. 已冻结的约束

### 3.1 数据与权限

- 现有数据库、上传、任务、交付、Manifest 和配置是生产资料；
- 首次切换到 Docker 前必须完成只读检查和可恢复备份；
- 不原地迁移、改写或删除现有路径记录；
- 验收数据必须使用公开脱敏夹具和独立测试用户；
- 测试栈不得读取或挂载日常用户资料；
- 所有任务、事件、诊断、预览、恢复和下载继续按 `user_id` 隔离；
- 回收站与管理员治理语义不在本批修改。

### 3.2 运行

- `start_all.bat` 是日常唯一启动入口；
- `stop_all.bat` 是日常唯一停止入口；
- 不提供需要用户手工选择的宿主机/Docker 双模式；
- Docker Desktop 未运行时自动尝试启动并限时等待；
- 停止服务始终保留数据库、上传、结果和持久卷；
- 禁止按端口杀进程、`down -v`、全局 `prune` 和模糊清理。

### 3.3 验收

- 本机 10–20 VU 只验证并发正确性，不形成生产性能结论；
- 本机 p95、吞吐、CPU 和内存只记录基线；
- 模型、解析器或核心数据能力不可达时，完整 8B-1 验收失败；
- 辅助服务不可达时允许日常 Mangrove 启动，但必须显示部分能力不可用；
- skip、Mock 或未执行不能记为通过；
- GPU、生产并发、长期运行、RAID 和灾难恢复统一标记为待 8B-2。

## 4. 总体设计

### 4.1 日常开发

```text
start_all.bat
    └─ scripts/ops/mangrove_stack.ps1 -Action Start
          ├─ Docker Desktop 就绪
          ├─ 只读数据预检 + 首次备份门
          ├─ mangrove-dev/frontend-dev
          ├─ mangrove-dev/api-dev
          │       └─ FastAPI + 2 个内嵌 workspace workers
          ├─ Phoenix
          ├─ 现有 SearXNG Compose
          ├─ 现有 Firecrawl Compose
          └─ 现有 RSSHub 容器
```

开发态：

- Vite 5173 对可信局域网开放；
- FastAPI 8088 对可信局域网开放；
- Phoenix 6006/4317/4318 只绑定 `127.0.0.1`；
- 源码通过精确 bind mount 进入开发容器；
- Vite 自己负责 HMR；
- `scripts/dev_reload.py` 继续负责后端进程外重载；
- 数据、下载、日志和配置单独受控挂载，不复制进镜像。

### 4.2 干净镜像验收

```text
Node 构建阶段
    └─ npm ci → npm run build
                     │
                     ▼
Python 运行阶段
    ├─ 锁定 requirements
    ├─ 当前运行资产
    ├─ frontend/dist
    └─ FastAPI 8088
          ├─ 同源托管前端
          └─ 内嵌 workspace workers
```

验收态：

- 不挂载 `src`、`frontend/src` 或整个仓库；
- 使用动态但可追踪的独立 Compose 项目名；
- 默认只绑定 `127.0.0.1:18088`；
- 使用隔离数据库、上传、执行、交付和报告目录；
- 公开夹具以只读方式挂载；
- 镜像内不得存在 `.env`、真实 `data`、真实 `downloads`、`.git` 或测试报告；
- 真实完成登录、上传、预览、任务、结果和正式下载。

### 4.3 工作台 worker

工作台 worker 继续由 `SemanticWorkspaceManager` 在 FastAPI lifespan 内创建：

- 两个普通 worker；
- 一个重任务信号量，容量为 1；
- 任务状态和事件继续落当前 SQLite；
- 进程重启后继续从当前待处理任务恢复。

“worker 就绪”是 FastAPI readiness 的一个检查项，不是独立 Compose service。

## 5. Module 设计

本规格只新增有实际复杂度和多个调用方的深 Module。测试通过同一 Interface 验证行为，
不为了测试暴露内部 seam。

### 5.1 `UploadStore` 深化

现有 `UploadStore` 已经是上传资料的正确 Module。路径可移植性继续隐藏在该 Module 内，
不让路由、Inspector、Connector 或前端理解 sidecar 的物理路径格式。

#### Interface

保持现有调用 Interface：

```python
UploadStore(root: str, *, max_bytes: int)

save_bytes(...) -> UploadItem
save_upload(...) -> UploadItem
resolve(user_id: str, upload_id: str) -> UploadItem
delete(user_id: str, upload_id: str) -> None
```

#### Interface 不变量

- `resolve()` 返回的 `UploadItem.storage_path` 永远是当前 `root` 下的规范绝对路径；
- 调用方可以直接读取返回路径，不需要知道记录来自 Windows 还是 Linux；
- `resolve()` 只根据已校验的 `user_id + upload_id` 构造对象路径；
- sidecar 中的 `storage_path` 不能把读取引向当前用户根以外；
- 新 sidecar 只持久化 `objects/<upload_id>` 形式的相对引用；
- 旧 sidecar 保持原样，读取时忽略其中的 Windows 物理前缀；
- 文件缺失、owner 不匹配和非法 ID 继续失败关闭；
- 已登记的大小和 SHA-256 不改变，现有消费与交付完整性校验不得减弱。

#### 实现约束

- 不批量重写 96 个既有 `.meta`；
- 返回给调用方的运行时对象和写入 sidecar 的持久化对象允许不同；
- 原始文件名、MIME、大小和 SHA-256 沿用 sidecar；
- `user_id` 和 `upload_id` 必须与请求和目录身份一致；
- 不增加新的数据库表或迁移。

### 5.2 `ManagedPathCodec`

语义执行、Harness attempt 和正式 Delivery 当前把物理绝对路径写入 SQLite。新增一个
`ManagedPathCodec` Module，把新路径编码和旧路径兼容集中在单一 seam。

建议文件：

`src/services/managed_paths.py`

#### Interface

```python
class ManagedPathCodec:
    def __init__(self, root: Path, *, legacy_anchor: tuple[str, ...]) -> None: ...

    def encode(self, path: Path | str) -> str: ...

    def decode(self, stored: str) -> Path: ...
```

#### 持久格式

新记录使用：

```text
managed:v1/<root 下的 POSIX 相对路径>
```

示例：

```text
managed:v1/0f3a.../plan_.../run_.../delivery/delivery_.../result.txt
```

#### `encode()` 不变量

- 输入必须解析到配置 `root` 内；
- 拒绝 `..`、根目录外路径、设备路径和无法解析的符号链接；
- 输出不得包含盘符、反斜杠、宿主绝对路径或用户秘密；
- 相同文件在 Windows 和 Linux 得到相同持久化值。

#### `decode()` 支持

1. `managed:v1/...` 新格式；
2. 当前根下的受控普通相对路径；
3. 旧 Windows 绝对路径；
4. 旧 POSIX 绝对路径。

旧绝对路径只允许按 `legacy_anchor` 截取根之后的相对子路径。例如
`("data", "semantic-executions")`。不得通过“文件名相同”在全盘搜索。

#### `decode()` 不变量

- 最终路径必须位于当前 `root`；
- 不存在的文件可以由上层根据操作语义决定 404/409，但不能回退到任意宿主路径；
- 路径穿越、根不匹配、锚点缺失和符号链接逃逸必须失败关闭；
- 错误只暴露稳定错误码，不向普通用户返回物理路径。

#### 接入点

`WebUIStore` 构造 Interface 增加可选参数：

```python
WebUIStore(
    db_path: str = "data/webui.db",
    *,
    semantic_paths: ManagedPathCodec | None = None,
)
```

生产 `get_store()` 必须传入基于
`settings.semantic_execution_root` 的 codec。测试可以传 `tmp_path` codec。
`semantic_paths=None` 只保留给不触及语义物理路径的既有测试；生产启动和任何语义路径
测试不得使用 `None`。

以下私有字段在写入时 `encode()`、读取给服务端内部时 `decode()`：

- `semantic_harness_attempts.artifact_paths_json`；
- `semantic_delivery_runs.output_dir`；
- `semantic_delivery_outputs.file_path`。

公开序列化继续禁止暴露物理路径。Manifest 中的下载 URL 和 `output_id` 不变。

### 5.3 `WorkspaceReadiness`

新增一个进程内 `WorkspaceReadiness` Module，集中回答“当前 API 是否能安全接单”，避免
Docker healthcheck、启动脚本和路由分别复制判断逻辑。

建议文件：

`src/api/readiness.py`

#### Interface

```python
@dataclass(frozen=True)
class ReadinessCheck:
    check_id: str
    status: Literal["passed", "failed"]
    summary: str


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    checks: tuple[ReadinessCheck, ...]


def collect_workspace_readiness(
    *,
    store: WebUIStore,
    manager: SemanticWorkspaceManager,
    upload_root: Path,
    execution_root: Path,
    artifact_root: Path,
) -> ReadinessReport: ...
```

#### 核心检查

- `CORE-API-001`：FastAPI 进程可响应；
- `CORE-DB-001`：SQLite 可打开并读取必要表；
- `CORE-WORKER-001`：manager 已启动且两个 worker 存活；
- `CORE-UPLOAD-001`：上传根存在并可执行受控临时写删；
- `CORE-EXEC-001`：执行根存在并可执行受控临时写删；
- `CORE-ARTIFACT-001`：交付根存在并可执行受控临时写删。

#### 路由

- 保留 `GET /api/health` 作为纯 liveness；
- 新增 `GET /api/readiness` 返回低敏 `ReadinessReport`；
- readiness 不检查真实模型、不运行真实任务、不返回队列内容、用户名、文件名或物理路径；
- Docker healthcheck 只使用 `/api/readiness`；
- 模型、解析器、Phoenix 和辅助服务由操作脚本执行能力检查。

### 5.4 `MangroveStack` 操作 Module

复杂 Docker 命令不直接堆在 `.bat`。使用一个 PowerShell Module 脚本隐藏项目身份、启停
顺序、健康等待、降级和退出码。

建议文件：

`scripts/ops/mangrove_stack.ps1`

#### Interface

```powershell
scripts/ops/mangrove_stack.ps1 `
  -Action Start|Stop|Status `
  [-TimeoutSeconds 180] `
  [-JsonOutput <path>]
```

`.bat` 仅做 UTF-8 控制台设置、定位 `%~dp0`、调用该 Interface 并展示结果。

#### 稳定退出码

| 退出码 | 含义 |
|---:|---|
| 0 | 核心和辅助能力均就绪 |
| 10 | 核心就绪，至少一个辅助能力降级 |
| 20 | 核心服务启动或停止失败 |
| 21 | Docker Desktop 缺失、启动失败或超时 |
| 22 | 数据预检或首次备份失败 |
| 23 | 端口被身份不明的进程占用 |
| 24 | 配置或镜像校验失败 |

#### 资源身份

Mangrove 应用使用固定项目名 `mangrove-dev`。不得写固定 `container_name`。

验收项目使用：

```text
mangrove-8b1-<run_id>
```

停止或故障前必须同时验证：

- `com.docker.compose.project`；
- `com.docker.compose.service`；
- 当前命令允许的项目名；
- 当前命令允许的服务名。

标签不匹配时停止操作并返回错误，不按端口或模糊名称兜底。

### 5.5 `AcceptanceReport`

新增确定性报告聚合 Module，只消费工具结构化输出，不替代 pytest、Playwright、k6、
Toxiproxy、SQLite 或 restic。

建议文件：

`scripts/acceptance/report_phase4b_8b1.py`

#### Interface

```python
build_report(
    result_files: Sequence[Path],
    *,
    output_markdown: Path,
    output_html: Path,
) -> AcceptanceSummary
```

#### 统一结果结构

```json
{
  "schema_version": "phase4b-8b1-check/v1",
  "run_id": "8b1-...",
  "checks": [
    {
      "check_id": "CONC-OWNER-001",
      "status": "passed",
      "summary": "跨用户读取成功数为 0",
      "evidence": ["k6-summary.json"],
      "remediation": null
    }
  ]
}
```

单项状态允许：

- `passed`；
- `failed`；
- `not_run`；
- `pending_8b2`。

总状态只有：

- `passed`：所有 8B-1 必须项通过；
- `failed`：任一必须项 failed 或 not_run；
- `pending_8b2` 只作为报告附加状态，不能把失败的 8B-1 项转成待实机。

HTML 使用仓库已锁定的 `markdown-it-py` 从最终 Markdown 渲染，并附带项目内静态 CSS。
不调用外部云服务，不加载远程资源。

## 6. Docker 文件规格

### 6.1 建议文件

```text
docker/mangrove/
├─ Dockerfile
├─ compose.dev.yml
├─ compose.acceptance.yml
├─ compose.fault.yml
└─ README.md

scripts/ops/
├─ mangrove_stack.ps1
├─ backup_phase4b_8b1.py
└─ restore_phase4b_8b1.py

scripts/acceptance/
├─ run_phase4b_8b1.ps1
├─ report_phase4b_8b1.py
├─ k6/
└─ toxiproxy/
```

根目录旧 `Dockerfile` 和 `docker-compose.yml` 保留为历史入口，不在本批覆盖。

### 6.2 镜像

`Dockerfile` 至少包含：

1. Node 22 LTS 前端构建阶段；
2. Python 3.13 slim 依赖构建阶段；
3. Python 3.13 slim 运行阶段；
4. 非 root 运行用户；
5. `init: true` 或等价 PID 1 信号处理；
6. FastAPI 8088；
7. 内置 `/api/health` 与 `/api/readiness` healthcheck。

镜像版本规则：

- 不使用 `latest`；
- 首次 PoC 确定精确 tag；
- manifest 网络可用后固定 multi-arch digest；
- 验收报告记录基础镜像、最终镜像 ID 和 RepoDigest；
- Phoenix 保持已验收的 `19.10.0`，除非单独确认升级。

### 6.3 构建上下文

`.dockerignore` 必须排除：

- `.git`、`.claude`；
- `.env` 和所有本地秘密；
- `data` 中的运行资料；
- `downloads`、`logs`、截图、缓存；
- `.pytest-tmp`、Playwright 报告和测试结果；
- 本地备份、restic repository 和验收报告；
- 编辑器与 Python/Node 缓存。

运行所需源码和静态资产必须有显式清单。不得通过 `COPY .` 再依赖遗漏的 ignore 规则保护
秘密。

### 6.4 开发 Compose

`compose.dev.yml` 至少包含：

- `frontend-dev`；
- `api-dev`；
- `phoenix`。

要求：

- `frontend-dev` 把 `VITE_API_PROXY_TARGET` 设置为 `http://api-dev:8088`；
- `api-dev` 把 `WORKSPACE_OTLP_ENDPOINT` 设置为
  `http://phoenix:6006/v1/traces`；
- `.env` 作为运行环境变量来源，不进入镜像；
- `data`、`downloads`、`logs` 使用显式 bind mount；
- 源码 mount 与数据 mount 分开声明；
- Node `node_modules` 使用 Linux 命名卷，禁止与 Windows 宿主共享；
- Phoenix 数据使用命名卷；
- API 和前端使用 healthcheck；
- Phoenix healthcheck 失败只影响辅助状态，不阻止核心页面运行。

### 6.5 验收 Compose

`compose.acceptance.yml` 至少包含：

- `app`：干净 Mangrove 镜像；
- `phoenix`：低敏 trace；
- `toxiproxy`：只在网络故障场景启用；
- `backup`：只在备份恢复场景运行。

要求：

- 测试数据库和资料目录全部位于 `runtime/acceptance/<run_id>`；
- `app` 不挂载源码；
- fixtures 只读；
- 报告目录可写；
- Toxiproxy 管理端口只在验收网络内开放；
- backup 工具不随日常 `up` 常驻；
- 所有 profile 都有真实可执行场景，不创建空 profile；
- 清理只针对本次项目名和 `runtime/acceptance/<run_id>`；
- 自动清理不得删除报告和失败证据。

## 7. 网络与端口

### 7.1 日常

| 能力 | 宿主绑定 | 访问范围 |
|---|---|---|
| Vite | `0.0.0.0:5173` | 可信 LAN |
| FastAPI | `0.0.0.0:8088` | 可信 LAN |
| Phoenix UI | `127.0.0.1:6006` | 当前 PC |
| OTLP gRPC | `127.0.0.1:4317` | 当前 PC/容器 |
| OTLP HTTP | `127.0.0.1:4318` | 当前 PC/容器 |

不在 8B-1 自动修改 Windows 防火墙、路由器或目标服务器规则。LAN PC 无法连接时，报告
给出待用户检查项，不自动扩大网络权限。

### 7.2 容器地址

- Compose 内服务使用服务名；
- Windows 主机服务使用 `host.docker.internal`；
- 独立 LAN 服务使用明确 IP；
- 容器配置中的 `localhost` 只允许指向自身；
- 所有能力探测必须记录实际目标类型，但报告不得输出密钥或完整带凭据 URL。

### 7.3 端口冲突

启动前检查 5173/8088：

- 已由 `mangrove-dev` 对应服务占用：视为幂等重入；
- 被其他容器或进程占用：返回 `23`，列出进程/容器身份摘要；
- 不自动结束任何未知进程。

## 8. 首次切换与日常数据

### 8.1 首次切换标记

使用 Git 忽略的：

```text
runtime/state/docker-preflight-v1.json
```

只在以下全部成功后写入：

- 只读目录和数据库盘点；
- 路径兼容扫描；
- SQLite 一致快照；
- 文件清单和 SHA-256；
- restic snapshot；
- restic check；
- 隔离恢复抽检。

标记只记录版本、时间、snapshot ID、清单哈希和检查结果，不含秘密。

### 8.2 只读预检

预检不得构造会执行 DDL 的真实 `WebUIStore`。使用 SQLite URI `mode=ro` 读取：

- 数据库可打开；
- 必要表存在；
- `quick_check` 通过；
- 记录数量；
- 旧绝对路径数量；
- 所需目录存在和规模；
- 可用磁盘空间。

预检不写数据库、不触发 schema migration、不改 sidecar。

### 8.3 挂载

日常 Docker 继续挂载当前：

- `data`；
- `downloads`；
- `logs`；
- 必要配置文件；
- 运行时资料目录。

挂载前不得把宿主目录复制进镜像。挂载目标路径固定，容器 `WORKDIR` 固定为 `/app`。

## 9. 备份与恢复规格

### 9.1 `BackupBundle`

每次备份生成：

```text
runtime/backups/staging/<backup_id>/
├─ databases/
├─ data/
├─ downloads/
├─ config/
└─ manifest.json
```

`manifest.json` 至少包含：

- schema version；
- backup ID 和时间；
- Git commit 和开发分支；
- 应用/镜像版本；
- 文件相对路径、大小和 SHA-256；
- SQLite `quick_check`；
- 总文件数和总字节数；
- 明确的排除项；
- restic snapshot ID。

### 9.2 SQLite

对每个当前 SQLite：

- 使用 Python `sqlite3.Connection.backup()`；
- 源连接只读；
- 目标写入 staging；
- 目标执行 `PRAGMA quick_check`；
- 不使用普通文件复制代替在线快照；
- 不执行 `VACUUM`。

### 9.3 restic

- 固定 restic 版本；
- repository 位于 `runtime/backups/restic-repo` 或用户显式配置的本机外部目录；
- password file 位于 `runtime/secrets/restic-password`；
- password file 和 repository 均由 Git 忽略；
- 首次缺失时生成高熵密码，并在报告中明确提示用户另行保存；
- password 不写 Markdown/HTML、日志或 manifest；
- 备份后执行 `restic check`；
- 不在本批自动 `forget`、`prune` 或删除旧 snapshot。

### 9.4 恢复

恢复目标必须是：

```text
runtime/restore-tests/<restore_id>
```

禁止恢复到当前 `data` 或 `downloads`。

恢复通过条件：

- restic check 通过；
- 清单全部还原且哈希一致；
- SQLite quick_check 通过；
- 隔离应用可启动；
- 两个测试用户 owner 不串；
- 历史任务、事件、证据和 Delivery 可读取；
- 授权下载通过；
- 跨用户下载返回拒绝；
- 旧路径兼容不修改恢复资料。

## 10. 并发正确性规格

### 10.1 工具

- k6 固定版本 Docker 镜像；
- 本地执行；
- 禁止 Cloud、share 和远程结果上传；
- `handleSummary()` 输出 JSON；
- 每个 VU 使用独立测试用户和 token；
- token 只在进程内或临时 secret 文件存在，不写报告。

### 10.2 场景

| 检查编号 | 场景 | 通过条件 |
|---|---|---|
| `CONC-OWNER-001` | 20 用户交叉读取任务 | 非 owner 成功数为 0 |
| `CONC-EVENT-001` | 20 用户交叉读取事件 | 非 owner 成功数为 0 |
| `CONC-DOWNLOAD-001` | 20 用户交叉下载 | 非 owner 成功数为 0 |
| `CONC-STATE-001` | 创建、轮询、取消 | 非法终态回退数为 0 |
| `CONC-DELIVERY-001` | 重复查询/恢复/发布边界 | 每个 run 正式 Delivery 不超过 1 |
| `CONC-RETRY-001` | 受控失败 | 超过 Harness 上限的重试数为 0 |
| `CONC-PROCESS-001` | 10–20 VU 轻量负载 | API/worker 崩溃数为 0 |

### 10.3 性能口径

记录但不判生产达标：

- 请求总数；
- p50/p95/p99；
- 错误率；
- 各场景完成时间；
- Docker CPU、内存和磁盘；
- 本地模型/解析器调用数。

报告必须注明“开发机基线，不代表生产容量”。

## 11. 故障注入规格

### 11.1 网络

Toxiproxy 场景：

| 检查编号 | 故障 | 必须验证 |
|---|---|---|
| `FAULT-NET-001` | 模型延迟超过超时 | 有界失败、用户可理解、无无限重试 |
| `FAULT-NET-002` | TCP reset | 正确分类、可恢复、无假结果 |
| `FAULT-NET-003` | 代理 down 后恢复 | 恢复后新任务可成功 |
| `FAULT-NET-004` | 响应截断/限流 | 失败关闭，不发布不完整交付 |

每个场景结束执行 Toxiproxy reset，并用正常探针验证恢复。

### 11.2 容器

| 检查编号 | 故障 | 必须验证 |
|---|---|---|
| `FAULT-PROC-001` | 终止验收 API 容器 | 无日常资源受影响 |
| `FAULT-PROC-002` | 重启 API | 待处理任务按当前契约恢复 |
| `FAULT-PROC-003` | 重放查询/恢复 | 不重复正式发布 |
| `FAULT-AUX-001` | 停止 Phoenix | 业务继续，观测明确降级 |

所有命令先校验 Compose labels。不得向容器挂载 Docker Socket。

### 11.3 磁盘

- 只使用验收项目的限额 `tmpfs`；
- 禁止向宿主磁盘写满数据；
- 验证 ENOSPC 后任务失败、临时文件清理和 Delivery 不发布；
- 删除专用容器即可恢复；
- 失败时保留必要的脱敏日志和检查结果。

## 12. 真实用户闭环

### 12.1 自动 Playwright

至少包含：

1. 干净镜像页面加载；
2. 测试用户登录；
3. 上传公开 DOCX；
4. 上传完成立即显示原文件预览；
5. 输入“提取商务条款，汇总并输出 TXT”；
6. 阶段顺序最多一个活动态；
7. 任务到达正确终态；
8. 完成任务默认优先展示结果；
9. 来源/证据入口可用；
10. 正式 TXT 下载；
11. 下载文件重新打开并机械校验；
12. Playwright trace、截图和 HTML 报告仅在隔离报告目录。

真实闭环不得使用 `page.route()` Mock 业务接口。

### 12.2 LAN 人工验收

提供一页中文操作说明，只要求用户：

1. 在另一台可信 LAN PC 打开 Mangrove；
2. 登录测试账号；
3. 上传公开夹具；
4. 提交固定任务；
5. 下载正式结果；
6. 在一键报告中勾选 `LAN-FLOW-001` 并记录结果。

不要求用户阅读 Docker 日志或手工执行诊断命令。

## 13. 一键验收流程

主入口：

```powershell
powershell -ExecutionPolicy Bypass `
  -File scripts/acceptance/run_phase4b_8b1.ps1
```

顺序：

1. `ENV-*`：Docker、Compose、镜像仓库和磁盘；
2. `CONFIG-*`：Compose `config --quiet`、端口和秘密排除；
3. `BUILD-*`：干净镜像构建、镜像内容扫描、依赖事实；
4. `CORE-*`：应用、worker、SQLite 和存储 readiness；
5. `CAP-*`：模型、解析器、Phoenix；
6. `FLOW-*`：真实 Playwright 闭环；
7. `CONC-*`：k6 并发正确性；
8. `FAULT-*`：网络、容器、Phoenix、磁盘；
9. `BACKUP-*`：SQLite、restic、恢复和业务复验；
10. `LAN-*`：生成用户操作页并等待人工结果录入；
11. `SERVER-*`：列出全部待 8B-2 项；
12. 生成 Markdown/HTML 总报告。

前置失败规则：

- Docker 不可用：后续标记 `not_run`，总状态 failed；
- 干净镜像失败：业务门标记 `not_run`，总状态 failed；
- 模型/解析器不可达：真实闭环 failed，不降为 pending 8B-2；
- 一个测试框架返回 0 但结构化结果缺失：对应检查 failed；
- 清理失败：报告 failed，并列出本次项目身份，禁止扩大清理。

## 14. 验收证据目录

```text
runtime/acceptance/<run_id>/
├─ inputs/
├─ data/
├─ downloads/
├─ restore/
├─ reports/
│  ├─ acceptance.md
│  ├─ acceptance.html
│  ├─ summary.json
│  ├─ playwright/
│  ├─ k6/
│  ├─ faults/
│  └─ backup/
└─ logs/
```

`runtime/` 全部 Git 忽略。正式执行报告只在用户确认后摘取低敏结论到 `docs/plans/`，不提交
原始用户数据、token、物理路径或完整 trace。

## 15. 测试与验证门

### 15.1 数据可移植

必须先写失败测试，再实现：

- 旧 Windows `.meta` 在 Linux 根下解析到当前对象；
- 旧 `.meta` 中指向其他用户/根的路径被忽略；
- 新 `.meta` 不含盘符和绝对路径；
- 旧 Delivery Windows 路径映射到当前 execution root；
- 新 Delivery 使用 `managed:v1/`；
- `..`、错误锚点、UNC、设备路径和符号链接逃逸全部拒绝；
- 移动整个测试根后历史记录仍可读；
- 旧记录未被改写；
- owner 和哈希校验不减弱。

### 15.2 Python

- 新 Module 单元/集成测试；
- 当前 UploadStore、Harness、Delivery、工作台路由回归；
- 全仓 pytest；
- `pip check` 输出记录；
- 若当前锁定依赖在 Linux 产生既有冲突，先形成精确冲突清单和最小修复方案，不静默升级
  大片依赖。

### 15.3 前端

- `npm ci`；
- TypeScript/Vite build；
- 现有完整 Playwright；
- 新增干净镜像真实闭环；
- PC Chrome；
- 失败保留 trace，成功不长期保留敏感附件。

### 15.4 Docker

- `docker compose config --quiet`；
- 多阶段镜像真实构建；
- 镜像中秘密/用户资料缺失；
- 开发前后端热更新各验证一次；
- `start_all.bat` 冷启动、幂等重入、辅助降级；
- `stop_all.bat` 定向停止和数据保留；
- 干净镜像无源码 mount；
- 容器重建后状态恢复；
- LAN PC 实际访问。

### 15.5 备份与故障

- SQLite 快照 quick_check；
- manifest 哈希；
- restic check；
- 全新目录恢复；
- 两用户语义复验；
- Toxiproxy reset 后恢复；
- API 容器重启；
- Phoenix fail-open；
- tmpfs ENOSPC；
- 日常数据和容器在前后哈希/身份检查中保持不受影响。

## 16. 安全要求

- 容器不挂载 Docker Socket；
- 不使用 privileged；
- 运行用户非 root，除非某个已核实系统依赖明确要求并单独记录；
- 密钥不进入镜像层、Compose 输出、报告或 trace；
- `docker compose config` 只使用 `--quiet` 做验收，不把展开后的秘密写证据；
- trace 不含文档正文、完整 Prompt、模型全文、Cookie、Token、原文件名和物理路径；
- 故障测试只作用于允许的验收项目名；
- 恢复和清理目标必须解析为 `runtime/acceptance` 或 `runtime/restore-tests` 的子目录；
- 目标无法机械确认时停止。

## 17. 文档交付

实现阶段必须同步：

- `AGENTS.md`：当前 Docker 入口、数据兼容和验收状态；
- `CLAUDE.md`：如新增操作约定；
- `CONTEXT.md`：如术语或接口改变；
- `handoff.md`：已完成、证据、阻塞和下一步；
- `docs/adr/0016-...`：只在决策发生变化时更新；
- `docs/adr/README.md`；
- 当前规格和执行报告；
- Docker/备份/验收用户操作说明；
- 所有未开发的 8B-2 项。

不得把规格、代码完成、内部测试通过或用户验收通过混写为同一状态。

## 18. 实施切片

本规格批准后才进入任务拆分。实施仍按以下四个切片，每个切片独立展示证据并等待用户确认：

### 8B-1a：数据可移植性和最小干净镜像

完成：

- `UploadStore` 深化；
- `ManagedPathCodec`；
- 新旧路径测试；
- Python 3.13 Linux 依赖 PoC；
- 最小干净镜像；
- 不挂载源码的 API/静态前端启动。

退出条件：

- 旧 Windows 路径格式的公开脱敏回归夹具在容器内可预览和下载；
- 旧资料未改写；
- 干净镜像健康；
- 依赖冲突有明确结论。

### 8B-1b：日常 Docker 和一键启停

完成：

- 开发 Compose；
- 前后端热更新；
- `WorkspaceReadiness`；
- `MangroveStack`；
- `start_all.bat`/`stop_all.bat`；
- 现有辅助容器统一启停；
- 分级健康和 LAN 地址。

退出条件：

- 一键冷启动、幂等重入和定向停止通过；
- 辅助降级不冒充全绿；
- 数据和卷保留。

### 8B-1c：真实闭环、并发和故障

完成：

- 干净镜像 Playwright；
- k6 10–20 VU；
- Toxiproxy；
- API/Phoenix 重启；
- 受限磁盘故障。

退出条件：

- 真实下载内容正确；
- 权限、幂等、状态和重试不变量通过；
- 故障可恢复且不发布坏结果。

### 8B-1d：备份恢复和总报告

完成：

- SQLite snapshot；
- restic；
- 新目录恢复；
- 业务语义复验；
- Markdown/HTML 报告；
- LAN 用户操作说明；
- 8B-2 待办清单。

退出条件：

- 一键验收生成完整报告；
- 备份和恢复闭环通过；
- 用户完成 LAN 步骤；
- 所有本机结果和待实机项严格分开。

## 19. 失败停止条件

遇到以下情况停止当前切片并展示证据，不扩大范围：

- 需要改变任务含义、ResultContract、用户权限或交付语义；
- 路径兼容只能通过批量改写现有数据实现；
- 需要自动切换到外部模型；
- Linux 依赖修复要求大范围升级并影响批次 1–7；
- Docker Desktop 必须修改全局设置或提升宿主权限；
- 故障测试无法与日常资源机械隔离；
- 备份无法证明一致或恢复无法验证 owner；
- 需要创建版本、标签、发布或不可逆清理。

## 20. 规格完成标准

本规格被用户确认后，规格阶段完成。下一阶段是任务拆分，不是直接实现。

任务拆分阶段必须为每个 8B-1a 至 8B-1d 条目给出：

- 目标文件；
- 先写的失败测试；
- 最小实现；
- 验证命令；
- 预期证据；
- 风险和停止条件；
- 用户确认点。
