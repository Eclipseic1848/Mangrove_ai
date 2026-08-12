# Phase 4B 批次 8B-1 Docker Desktop 服务器就绪包任务拆分

> 日期：2026-07-28
>
> 状态：已暂停；2026-07-28 用户决定统一后置
>
> 当前阶段：非当前任务；待工程功能完成且目标服务器条件明确后重新拆分
>
> 上游规格：用户已确认
> [8B-1 Docker Desktop 服务器就绪包技术规格](2026-07-28-phase4b-batch8b1-docker-readiness-spec.md)
>
> 实施纪律：按 TDD 逐个纵向切片执行；8B-1a、8B-1b、8B-1c、8B-1d
> 分别展示证据并等待用户确认，不自动进入下一切片

> 调度修订：当前不执行 8B-1a～8B-1d。此前启动的 8B-1a 依赖构建已停止，未完成的
> Docker 草稿和路径迁移运行时代码已撤回，未改动用户数据或既有业务容器。后续不得从
> 本文直接恢复开工，必须先按最终服务器条件重新确认依赖、Compose、数据与验收范围。

> vNext 关系（2026-07-29）：Agentic Runtime 的任务级 Docker 功能沙箱属于独立工作包，
> 不得引用本文作为 8B-1a～8B-1d 的恢复授权，也不得改造整机一键启停。

## 1. 本文解决什么

本文把已确认的 8B-1 技术规格拆成可独立失败、实现、验证和验收的任务，不重新讨论
业务范围，也不在任务拆分阶段修改代码。

每个实现任务都明确：

- 目标文件；
- 已确认的测试 seam；
- 先出现的失败证据（RED）；
- 只让当前行为通过的最小实现（GREEN）；
- 回归命令和预期证据（VERIFY）；
- 必须停止或交给用户确认的边界。

## 2. 当前事实、推断和待验证项

### 2.1 已验证事实

- 当前应用入口是 FastAPI `8088` 和 Vite `5173`，旧根目录 Docker 文件仍描述
  Streamlit/WebSocket 架构，不能直接作为 8B-1 基线。
- 工作台的两个 worker 当前内嵌在 FastAPI 的 `SemanticWorkspaceManager` 中，重任务并发
  由进程内信号量限制为 1，不是独立 Compose 服务。
- 当前上传 sidecar 和部分语义交付数据库字段含 Windows 绝对路径；直接把现有数据目录
  挂载到 Linux 容器会破坏旧资料的预览或下载。
- 当前前端已有 Playwright 测试主要使用 `page.route()` 模拟工作台 API；它们能做 UI
  回归，但不能证明干净镜像内的真实闭环。
- Docker Desktop、Docker Engine、Compose 和 Phoenix 镜像在当前开发机可用。
- 现有 Python 依赖存在已知版本冲突，Linux 镜像必须用真实构建和 `pip check` 得出结论，
  不能把“当前 Windows 能运行”推断为“Python 3.13 Linux 可安装”。

### 2.2 基于代码的推断

- 上传路径兼容可在 `UploadStore.resolve()` 的公开 seam 内完成，不需要批量重写旧 sidecar。
- Harness 和 Delivery 路径兼容应集中在 `ManagedPathCodec`，并通过 `WebUIStore` 的公开
  读写方法接入，避免每条路由自行判断 Windows/Linux 路径。
- 日常开发热更新应继续使用 bind mount、Vite 和现有后端 reload 机制；不需要依赖已经
  验证不可用的 `docker compose up --watch`。
- 干净镜像验收可以使用独立 Compose project、独立数据目录和独立端口，不影响日常
  `mangrove-dev`。

### 2.3 尚未验证的建议

- `grafana/k6:2.1.0`、`shopify/toxiproxy:2.12.0` 和 `restic/restic:0.19.1` 的实际
  拉取、平台 manifest 和当前 Docker Desktop 兼容性仍要在各自 PoC 任务中验证。
- Python 3.13 Linux 的最小依赖调整范围必须由镜像构建结果决定。
- 10–20 VU 只用于本机并发正确性，不形成服务器吞吐量或生产 p95 结论。

## 3. 固定 seam 与 TDD 规则

### 3.1 已确认 seam

| 类型 | seam | 允许验证的行为 |
|---|---|---|
| Python | `UploadStore` | 上传持久化、owner 校验、旧 sidecar 兼容、当前根解析 |
| Python | `ManagedPathCodec` | 新路径编码、根迁移解码、旧绝对路径兼容、安全拒绝 |
| Python/API | `WorkspaceReadiness` | DB、worker、上传/执行/产物根是否可以安全接单 |
| PowerShell | `MangroveStack` | Start/Stop/Status、稳定退出码、资源身份和分级健康 |
| Python | `AcceptanceReport` | 结构化结果聚合、失败判定、Markdown/HTML 产出 |
| HTTP/页面 | 工作台公开 API 与页面 | 注册登录、上传预览、创建任务、事件、取消、下载、owner |
| Docker | Compose project 与 labels | 隔离、启动、停止、容器故障和数据保留 |
| 备份恢复 | 新目录恢复后的公开 API | 数据完整、owner 隔离、交付仍可下载 |

不为私有帮助函数、SQL 行数、DOM 实现细节或容器内部临时结构编写脆弱测试。

### 3.2 每项任务的固定循环

1. 只添加当前行为的一个失败测试或失败探针。
2. 运行最窄命令，保存“按预期失败”的输出。
3. 写使该行为通过的最小实现。
4. 重跑最窄命令并保存通过证据。
5. 跑相邻回归；失败则停留在当前任务修复，不提前写后续任务。
6. 当前切片全部通过后再跑切片门禁、形成执行报告并请用户验收。

禁止先横向写完一批测试再一次性实现；重构不混入 RED/GREEN，确需重构时放到当前切片
末尾的审查阶段并单独说明。

### 3.3 测试替身边界

- 优先使用真实 `tmp_path`、真实 SQLite、真实文件和真实 Docker project。
- 只允许替换真实外部边界，例如模型端点、解析服务、系统时间或不可控网络。
- 不 mock 自己正在验证的 `UploadStore`、`ManagedPathCodec`、`WebUIStore`、
  `WorkspaceReadiness`、`MangroveStack` 或 `AcceptanceReport`。
- 正式 Playwright 闭环禁止 `page.route()`、直接数据库写入和伪造 Delivery。
- 不得静默从本地模型切换外部模型；真实模型不可用时该项失败并停止。

### 3.4 证据约定

每次验收创建：

```text
runtime/acceptance/phase4b-8b1/<run_id>/
```

至少包含：

- `environment.json`：脱敏版本和能力；
- `checks/*.json`：统一结构的机器结果；
- `logs/`：必要的工具日志；
- `downloads/`：真实下载及 SHA-256；
- `report.md` 和 `report.html`；
- `commands.md`：实际命令、退出码和开始/结束时间。

`runtime/` 保持 Git 忽略。最终只把脱敏结论、必要摘要和门禁结果写入执行报告，不提交
真实上传、数据库、Cookie、令牌、模型请求正文或 restic 密码。

---

## 4. 8B-1a：数据可移植性和最小干净镜像

### 8B1-A0：冻结基线与非破坏性盘点

**目的**

先证明要保护什么，避免路径兼容和镜像 PoC 改写用户当前资料。

**目标文件**

- 新增执行期证据，不修改业务代码；
- 切片完成后新增
  `docs/plans/2026-07-28-phase4b-batch8b1a-execution-report.md`。

**执行**

- 记录 Git 分支、HEAD、工作树已有改动，只为本批次建立显式 allowlist；
- 记录上传 sidecar、三类语义路径字段的总数和旧绝对路径数量，只保存计数和脱敏样例；
- 记录 `data/`、配置文件和现有 Docker volume 的存在性及摘要哈希；
- 运行当前相关测试作为“修改前基线”。

**验证命令**

```powershell
python -m pytest `
  tests/test_file_upload_security.py `
  tests/test_semantic_harness_loop.py `
  tests/test_semantic_workspace_api.py `
  --basetemp .pytest-tmp/phase4b-8b1a-baseline -q
```

**预期证据**

- 明确区分“修改前已失败”和“本批次引入回归”；
- 基线盘点前后旧 sidecar、SQLite 文件和用户交付文件哈希不变。

**停止条件**

- 数据根或 DB 位置与规格中的当前配置不一致；
- 发现待操作路径不在当前工作区或 Docker Desktop 受控 volume 内；
- 需要清理、迁移或覆盖用户资料。

### 8B1-A1：旧上传 sidecar 在新根下可解析

**seam**

`UploadStore.resolve(user_id, upload_id)`。

**目标文件**

- `tests/test_file_upload_security.py`
- `src/services/upload_store.py`

**RED**

用 `tmp_path` 创建合法上传，把 sidecar 中的 `storage_path` 改成脱敏的旧 Windows 绝对路径，
再用同一对象目录模拟新 Linux/容器根。断言 `resolve()`：

- 返回当前 root 下的规范绝对路径；
- 文件内容、大小和 SHA-256 不变；
- 没有改写旧 sidecar。

当前实现应因为信任旧 `storage_path` 而失败。

**GREEN**

只让 `resolve()` 根据已校验的 `user_id + upload_id` 构造当前对象路径；原始文件名、
MIME、大小和 SHA-256 仍取 sidecar，owner、ID 和完整性校验不放宽。

**VERIFY**

```powershell
python -m pytest `
  tests/test_file_upload_security.py `
  -k "legacy and current_root" `
  --basetemp .pytest-tmp/8b1-a1 -q
```

预期得到目标测试通过，且 sidecar 修改前后哈希相同。

### 8B1-A2：新上传只持久化受控相对引用

**seam**

`UploadStore.save(...)` 后的公开 `resolve()` 行为及持久化 sidecar 契约。

**目标文件**

- `tests/test_file_upload_security.py`
- `src/services/upload_store.py`

**RED**

新增一个上传，断言 sidecar 的 `storage_path` 为 `objects/<upload_id>`，不含盘符、
反斜杠、宿主根或用户名；同时断言 `save()`/`resolve()` 返回的运行时路径仍是当前 root
下的绝对路径。当前实现应因写入绝对路径而失败。

**GREEN**

区分“持久化对象”和“返回给调用方的运行时对象”，只改变新 sidecar 的路径表示。

**VERIFY**

```powershell
python -m pytest `
  tests/test_file_upload_security.py `
  --basetemp .pytest-tmp/8b1-a2 -q
```

### 8B1-A3：`ManagedPathCodec` 新格式可跨根迁移

**seam**

`ManagedPathCodec.encode()` / `decode()`。

**目标文件**

- 新增 `tests/test_managed_paths.py`
- 新增 `src/services/managed_paths.py`

**RED**

先只测试一个纵向行为：

1. root A 下文件编码为 `managed:v1/<POSIX relative>`；
2. 同一相对结构复制到 root B；
3. root B 的 codec 解码到 root B，而不是 root A。

未实现类时测试按预期失败。

**GREEN**

实现最小构造器、`encode()` 和新格式 `decode()`，只接受当前 root 内路径。

**VERIFY**

```powershell
python -m pytest `
  tests/test_managed_paths.py `
  -k "managed_v1 and relocated_root" `
  --basetemp .pytest-tmp/8b1-a3 -q
```

### 8B1-A4：`ManagedPathCodec` 兼容旧绝对路径并失败关闭

**seam**

仍只使用 `ManagedPathCodec.decode()`。

**目标文件**

- `tests/test_managed_paths.py`
- `src/services/managed_paths.py`

**TDD 子循环**

按下列顺序逐个 RED/GREEN，不一次写完：

1. 旧 Windows 绝对路径通过稳定 `legacy_anchor` 映射到当前 root；
2. 旧 POSIX 绝对路径通过相同 anchor 映射；
3. 当前根下普通相对路径可读；
4. `..` 路径穿越被拒绝；
5. 锚点缺失或锚点不匹配被拒绝；
6. Windows 设备路径、UNC 越界或其他根被拒绝；
7. 已存在符号链接逃逸时被拒绝。

**最小实现**

只做路径语法归一、anchor 截取、当前 root 拼接和最终 containment 校验；不搜索磁盘、
不按文件名猜测、不修改旧数据库值。

**VERIFY**

```powershell
python -m pytest `
  tests/test_managed_paths.py `
  --basetemp .pytest-tmp/8b1-a4 -q
```

证据需列出每类允许/拒绝样例的稳定错误码；普通用户错误不得包含物理路径。

### 8B1-A5：Harness artifact 路径通过 codec 持久化

**seam**

`WebUIStore` 的 Harness attempt 公开写入/读取方法。

**目标文件**

- `tests/test_semantic_harness_loop.py` 或新增聚焦测试
  `tests/test_semantic_path_portability.py`
- `src/api/store.py`
- `src/api/auth.py`

**RED**

在 root A 通过公开 store 方法写入 artifact 路径，模拟数据根迁移至 root B 后重新打开
同一 SQLite，断言读取结果解析到 root B；直接查询仅用于证据，断言数据库新值以
`managed:v1/` 开头且不含 root A。

**GREEN**

- `WebUIStore.__init__()` 增加已确认的可选 `semantic_paths`；
- Harness artifact 写入调用 `encode()`，服务端内部读取调用 `decode()`；
- 生产 `get_store()` 始终传入基于 `settings.semantic_execution_root` 的 codec。

**VERIFY**

```powershell
python -m pytest `
  tests/test_semantic_path_portability.py `
  -k "harness_artifacts" `
  --basetemp .pytest-tmp/8b1-a5 -q
```

若复用既有测试文件，则命令使用对应 test node id。

### 8B1-A6：Delivery 路径迁移后仍按 owner 下载

**seam**

Delivery 公开 store 方法及下载 HTTP API。

**目标文件**

- `tests/test_semantic_path_portability.py`
- `tests/test_semantic_harness_loop.py`
- `src/api/store.py`
- `src/api/routes/semantic_deliveries.py`

**TDD 子循环**

1. `output_dir` 新写入为 managed 引用；
2. `file_path` 新写入为 managed 引用；
3. root A 的 SQLite 和文件复制到 root B 后，owner 下载成功；
4. 其他用户仍得到既有隔离响应；
5. 文件内容被篡改后仍由完整性校验拒绝；
6. 旧 Windows/POSIX 绝对路径记录读取成功，但数据库原值不被重写。

**GREEN**

只在 `WebUIStore` 持久化边界编解码；下载路由继续消费服务端已解码路径，不改变
Manifest、`output_id`、QA、owner 或错误语义。

**VERIFY**

```powershell
python -m pytest `
  tests/test_semantic_path_portability.py `
  tests/test_semantic_harness_loop.py `
  --basetemp .pytest-tmp/8b1-a6 -q
```

### 8B1-A7：真实 DOCX 旧资料回归

**seam**

工作台公开 HTTP API 和真实文件系统，不直接调用私有执行函数。

**目标文件**

- `tests/test_semantic_workspace_api.py`
- 必要时新增脱敏夹具
  `tests/fixtures/semantic_harness/public/batch8b1/`

**RED**

用公开脱敏旧路径记录和既有 `contract.docx` 夹具，验证：

- 上传预览可读；
- 限定提取任务完成；
- TXT Delivery 下载；
- TXT 包含夹具中预先定义的商务条款；
- 不包含已定义的非商务前序内容；
- 旧记录未被改写。

该测试首先应在“迁移后的数据根”情形失败。

**GREEN**

只补齐 A1–A6 暴露出的生产接线遗漏；不得为测试添加专用路由、绕过模型/解析器或降低
批次 7 的 Compiler、Binder、Executor、Verifier 失败关闭规则。

**VERIFY**

```powershell
python -m pytest `
  tests/test_semantic_workspace_api.py `
  -k "docx and legacy_path and delivery" `
  --basetemp .pytest-tmp/8b1-a7 -q
```

### 8B1-A8：Python 3.13 Linux 依赖 PoC

**seam**

干净容器内的安装、导入、`pip check` 和最小应用启动。

**目标文件**

- `requirements.txt`，仅当证据证明需要最小调整；
- 新增 `docker/mangrove/Dockerfile` 的依赖阶段；
- 新增 `scripts/acceptance/check_linux_dependencies.py`。

**RED**

在 Python 3.13 Linux build stage 中：

1. 从锁定/声明依赖安装；
2. 执行 `pip check`；
3. 导入 FastAPI、语义编译、文档解析和 Delivery 的生产模块；
4. 输出结构化结果。

先保存真实失败包、约束链和平台错误，不凭印象修改版本。

**GREEN**

只修复阻断当前应用启动和 8B-1 正式闭环的依赖；保留已验证的第三方工具，禁止顺手全量
升级。如果必须大范围升级或会改变批次 1–7 行为，立即停止并交用户确认。

**VERIFY**

```powershell
docker build `
  --target dependency-check `
  -f docker/mangrove/Dockerfile `
  -t mangrove-8b1-dependency-check:local .
```

预期产出 `DEP-LINUX-001` 结构化结果，并记录镜像 digest。

### 8B1-A9：干净镜像和 Compose 契约

**seam**

Dockerfile/Compose 的实际构建结果和 Docker labels，不通过字符串快照冒充运行验证。

**目标文件**

- `docker/mangrove/Dockerfile`
- `docker/mangrove/compose.acceptance.yml`
- 新增 `docker/mangrove/Dockerfile.dockerignore`，或对根 `.dockerignore` 做最小必要修改
- `docker/mangrove/README.md`
- 新增 `scripts/acceptance/check_container_contract.py`

**RED**

先运行容器契约探针，预期因文件或镜像不存在失败。探针检查：

- Compose 可渲染为 JSON；
- 无固定 `container_name`；
- project、service labels 可识别；
- app 服务没有宿主源码 bind mount；
- Dockerfile 没有把 `.env`、`data/`、`runtime/`、Git 元数据或测试结果复制进镜像；
- 前端是构建产物，运行镜像不携带 Node 构建工具。

**GREEN**

使用 Node 构建阶段生成 `frontend/dist`，Python 3.13 运行阶段只显式复制生产所需源码、
静态文件和运行入口；验收 Compose 使用独立 project、数据根和端口。

**VERIFY**

```powershell
docker compose `
  -p mangrove-8b1-contract `
  -f docker/mangrove/compose.acceptance.yml `
  config --format json

python scripts/acceptance/check_container_contract.py `
  --compose-file docker/mangrove/compose.acceptance.yml `
  --project mangrove-8b1-contract
```

### 8B1-A10：不挂载源码的真实启动与路径迁移

**seam**

干净 app 镜像的 `/api/health`、`/api/readiness` 前置能力、静态前端和授权下载。

**目标文件**

- A8–A9 文件；
- 新增 `scripts/acceptance/run_phase4b_8b1a.ps1`。

**RED**

用隔离的脱敏旧路径夹具启动干净镜像，预期在生产接线未闭合时健康、预览或下载至少一项
失败。不得挂载仓库源码补救。

**GREEN**

只补 Compose 环境变量、生产启动命令和必要目录权限；不加入测试专用后门。

**VERIFY**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts/acceptance/run_phase4b_8b1a.ps1 `
  -RunId local-a
```

必须机械证明：

- app 容器使用刚构建的本地镜像；
- 容器没有仓库源码 bind mount；
- `/api/health` 可响应；
- 静态前端可打开；
- 迁移后的旧资料可预览和按 owner 下载；
- 停止 project 后隔离数据仍存在。

### 8B1-A-GATE：切片门禁与用户确认

**回归**

```powershell
python -m pytest `
  tests/test_file_upload_security.py `
  tests/test_managed_paths.py `
  tests/test_semantic_path_portability.py `
  tests/test_semantic_harness_loop.py `
  tests/test_semantic_workspace_api.py `
  --basetemp .pytest-tmp/8b1-a-gate -q

git diff --check
```

再执行一次 A10 的干净镜像验收。

**退出证据**

- 旧路径夹具在容器内可预览和下载；
- 新记录不含宿主绝对路径；
- 原有旧记录及用户资料未改写；
- 干净镜像不挂载源码且健康；
- Linux 依赖结论明确；
- 执行报告区分已通过、失败、未运行和待 8B-2。

**用户确认点**

展示 8B-1a 执行报告、变更 allowlist、测试结果和剩余风险。只有用户明确确认后，才进入
8B-1b；未确认时停在此处。

---

## 5. 8B-1b：日常 Docker 与一键启停

### 8B1-B1：`WorkspaceReadiness` 进程内检查

**seam**

`collect_workspace_readiness(...) -> ReadinessReport`。

**目标文件**

- 新增 `tests/test_workspace_readiness.py`
- 新增 `src/api/readiness.py`

**TDD 子循环**

逐项做一个 RED/GREEN：

1. 真实临时 SQLite 和必要表可读时 `CORE-DB-001=passed`；
2. manager 未启动或任一 worker 退出时 `CORE-WORKER-001=failed`；
3. 两个 worker 存活时该项通过；
4. 上传、执行、产物根分别完成受控临时写删；
5. 任一核心检查失败时 `ready=false`；
6. 返回摘要不包含用户名、队列正文、文件名或物理路径。

**GREEN**

实现已确认的数据类和聚合函数；不调用真实模型、不运行任务、不探测外部服务。

**VERIFY**

```powershell
python -m pytest `
  tests/test_workspace_readiness.py `
  --basetemp .pytest-tmp/8b1-b1 -q
```

### 8B1-B2：`/api/readiness` 低敏路由

**seam**

公开 `GET /api/readiness`；保留 `GET /api/health` 纯 liveness。

**目标文件**

- `tests/test_workspace_readiness.py`
- `src/api/main.py`
- `src/api/readiness.py`

**RED**

用真实 TestClient 和已启动/未启动 manager 分别请求路由，断言 HTTP 状态、`ready`、
稳定 `check_id` 和低敏摘要。当前路由不存在，应先失败。

**GREEN**

接入单一路由；Docker healthcheck 后续只消费该公开接口。

**VERIFY**

```powershell
python -m pytest `
  tests/test_workspace_readiness.py `
  -k "api_readiness" `
  --basetemp .pytest-tmp/8b1-b2 -q
```

### 8B1-B3：Vite 代理目标可配置

**seam**

浏览器访问 Vite 的 `/api/health`，由开发代理转发到 Compose 内 API。

**目标文件**

- `frontend/vite.config.ts`
- `docker/mangrove/compose.dev.yml`
- 新增 `scripts/acceptance/check_dev_proxy.ps1`

**RED**

在容器网络中以 `api-dev` 为目标启动 Vite，真实请求 `http://localhost:<dev-port>/api/health`；
当前硬编码宿主目标时应失败。

**GREEN**

代理目标读取 `VITE_API_PROXY_TARGET`，未设置时保持当前本机默认值，不改变前端公开 URL。

**VERIFY**

```powershell
npm.cmd --prefix frontend run build
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts/acceptance/check_dev_proxy.ps1
```

### 8B1-B4：日常开发 Compose 契约

**seam**

`docker compose config --format json` 与实际 Compose project。

**目标文件**

- `docker/mangrove/compose.dev.yml`
- `docker/mangrove/README.md`
- `scripts/acceptance/check_container_contract.py`

**RED**

新增开发栈契约探针，预期当前文件不存在或结构不符。断言：

- project 固定为 `mangrove-dev`；
- `frontend-dev`、`api-dev`、Phoenix 身份明确；
- worker 不被伪装成独立服务；
- 前后端使用受控 bind mount，`node_modules` 使用 named volume；
- 数据目录和配置按规格挂载；
- Phoenix 只绑定本机；
- 不使用固定 `container_name`；
- 日常栈与验收栈的数据根、端口和 project 不重叠。

**GREEN**

只增加已确认的开发服务、卷、网络和健康依赖，不把 SearXNG、Firecrawl、RSSHub 等现有
项目粗暴并入一个巨型 Compose。

**VERIFY**

```powershell
docker compose `
  -p mangrove-dev `
  -f docker/mangrove/compose.dev.yml `
  config --format json
```

### 8B1-B5：`MangroveStack Status` 与稳定退出码

**seam**

`scripts/ops/mangrove_stack.ps1 -Action Status [-JsonOutput]`。

**目标文件**

- 新增 `scripts/ops/mangrove_stack.ps1`
- 新增 `scripts/acceptance/check_mangrove_stack.ps1`

**TDD 子循环**

使用隔离 project 的真实 Docker 状态逐个验证：

1. Docker Desktop 不可访问时返回 21；
2. 未启动核心服务时返回 20；
3. 核心就绪、辅助降级时返回 10；
4. 核心和辅助均就绪时返回 0；
5. 未知进程占用受管端口时返回 23；
6. JSON 输出包含稳定检查 ID，不包含密钥或物理数据路径。

只允许 mock Docker CLI 这个系统边界来覆盖“Docker 不可访问”分支；项目/标签/健康判断
必须至少有一次真实 Docker 验证。

**GREEN**

实现最小的 Status 分支和统一结果结构，再由后续任务增加 Start/Stop。

### 8B1-B6：幂等 Start、首次迁移预检与分级健康

**seam**

`MangroveStack -Action Start`。

**目标文件**

- `scripts/ops/mangrove_stack.ps1`
- `docker/mangrove/compose.dev.yml`
- `scripts/acceptance/check_mangrove_stack.ps1`

**TDD 子循环**

1. 第一次切换前生成 `runtime/state/docker-preflight-v1.json`；
2. 预检或首次备份失败时不启动并返回 22；
3. 冷启动达到核心 readiness；
4. 第二次 Start 幂等，不创建第二组资源、不重置数据；
5. 辅助服务失败时核心可用但返回 10；
6. 未知端口占用时不终止未知进程。

**GREEN**

按规格编排“预检/备份 → 核心启动 → 健康等待 → 辅助能力探测 → 分级摘要”；不修改
Docker Desktop 全局设置。

**VERIFY**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts/ops/mangrove_stack.ps1 `
  -Action Start `
  -TimeoutSeconds 180 `
  -JsonOutput runtime/acceptance/phase4b-8b1/dev-start.json
```

连续运行两次并比较 Compose project、容器和 volume 身份。

### 8B1-B7：标签安全的 Stop 与数据保留

**seam**

`MangroveStack -Action Stop`。

**目标文件**

- `scripts/ops/mangrove_stack.ps1`
- `scripts/acceptance/check_mangrove_stack.ps1`

**TDD 子循环**

1. 标签不匹配时拒绝停止；
2. 只停止允许的 `mangrove-dev` 服务；
3. 同端口的未知进程不被终止；
4. Stop 不带 `--volumes`，上传、DB、交付和 named volume 保留；
5. 重启后原测试任务和文件仍可读取。

**GREEN**

停止前同时验证 Compose project、service 和允许名单；不按端口、容器名包含关系或模糊
字符串兜底。

### 8B1-B8：`.bat` 薄包装与一处操作入口

**seam**

用户双击 `start_all.bat` / `stop_all.bat`。

**目标文件**

- `start_all.bat`
- `stop_all.bat`
- `scripts/ops/mangrove_stack.ps1`

**RED**

从包含空格的工作区路径调用两个 `.bat`，断言它们：

- 能定位仓库根；
- 正确传播 PowerShell 稳定退出码；
- 不复制 Compose 编排逻辑；
- 输出核心状态、辅助降级和 LAN 访问地址。

**GREEN**

`.bat` 只设置 UTF-8、定位 `%~dp0`、调用 `MangroveStack` 并保留返回码。

**VERIFY**

```powershell
cmd.exe /d /c start_all.bat
cmd.exe /d /c stop_all.bat
```

### 8B1-B9：前后端热更新实证

**seam**

运行中的 Vite 页面、Vite 日志、FastAPI PID 和公开健康接口。

**目标文件**

- `docker/mangrove/compose.dev.yml`
- 必要时 `scripts/dev_reload.py`
- `scripts/acceptance/check_hot_reload.ps1`

**RED**

在 dev Compose 中只改变受版本控制文件的 mtime，不改变内容：

- 触发后端 watcher，观察 API 进程 PID 更新且 readiness 恢复；
- 触发前端 watcher，观察 HMR 更新事件且页面仍可请求；
- 容器和 volume 身份不变。

如果当前 watcher 不监听容器路径，该探针应先失败。

**GREEN**

只校准 bind mount 路径、polling 环境或已存在 watcher 参数；不新建第二套 reload 框架。

**VERIFY**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts/acceptance/check_hot_reload.ps1
```

### 8B1-B-GATE：切片门禁与用户确认

**验证**

- `tests/test_workspace_readiness.py` 全过；
- 前端构建通过；
- Compose 配置校验通过；
- 一键冷启动、第二次幂等启动、分级降级、定向停止、重启数据保留通过；
- 前后端热更新通过；
- LAN 地址只作为当前局域网可访问提示，不冒充服务器验收。

**用户确认点**

展示 8B-1b 执行报告和用户操作说明，请用户实际运行一次 `start_all.bat`、
`stop_all.bat`。用户确认后才进入 8B-1c。

---

## 6. 8B-1c：真实闭环、并发与可恢复故障

### 8B1-C1：干净验收环境和公开用户引导

**seam**

干净镜像的公开注册、登录和管理员 API。

**目标文件**

- `docker/mangrove/compose.acceptance.yml`
- 新增 `scripts/acceptance/bootstrap_phase4b_8b1.py`
- 新增 `scripts/acceptance/run_phase4b_8b1.ps1`

**RED**

在全新独立 data root 中只通过 HTTP：

1. 注册首个管理员；
2. 登录；
3. 注册普通测试用户；
4. 通过公开管理员接口完成已有的用户审批流程；
5. 获取后续 Playwright/k6 所需的短期会话。

任何依赖直接写 SQLite 的步骤都视为失败。

**GREEN**

实现确定性引导脚本，只使用公开 API；凭证在运行时生成、只存验收目录并从报告中脱敏。

### 8B1-C2：无 API mock 的 Playwright 真实闭环

**seam**

真实浏览器页面和干净镜像 HTTP。

**目标文件**

- 新增 `frontend/e2e/semantic-workspace-live.spec.ts`
- 新增 `frontend/playwright.live.config.ts`
- `scripts/acceptance/run_phase4b_8b1.ps1`

**RED**

先让新 live spec 对干净环境运行；在未接好真实 base URL、用户或任务前按预期失败。
测试中禁止 `page.route()`。

**TDD 子循环**

1. 登录并进入正式工作台；
2. 上传 `contract.docx` 后，提交前立即看到原文件预览；
3. 创建“汇总商务条款并输出 TXT”任务；
4. 如出现澄清，按公开 UI 回答后继续；
5. 进度中最多一个阶段活动，未开始阶段不得打勾；
6. 等待真实终态；
7. 结果区显示来源、QA 和可下载交付；
8. 下载 TXT，并记录浏览器下载事件和文件 SHA-256；
9. 刷新/重新进入后仍可查看已完成任务。

**GREEN**

只修正真实闭环暴露的 8B-1 框架接线问题。若问题属于已登记的批次 7 细节、模型理解质量
或新交互范围，记录并停止扩大修复。

**VERIFY**

```powershell
$env:PLAYWRIGHT_BASE_URL='http://127.0.0.1:<acceptance-port>'
npm.cmd --prefix frontend exec -- playwright test `
  --config playwright.live.config.ts `
  semantic-workspace-live.spec.ts
```

### 8B1-C3：下载内容机械验真

**seam**

公开 Delivery 下载文件，不读取执行器内部中间文件。

**目标文件**

- 新增 `scripts/acceptance/verify_contract_delivery.py`
- `tests/fixtures/semantic_harness/public/batch0/documents/contract.docx`
- 必要时新增公开的期望词项清单

**RED**

对“把 DOCX 原文原封不动转 TXT”、空文件、缺少商务条款、包含明确非目标前序内容、
Manifest/hash 不一致分别产生失败结果。

**GREEN**

实现确定性机械校验：

- 文件可按声明格式解码；
- 大小和 SHA-256 与交付清单一致；
- 已确认的商务条款目标全部命中；
- 已确认的非目标内容不命中；
- QA 和 `delivery_published` 一致。

它只判定结果，不生成或修复结果。

### 8B1-C4：k6 最小 smoke 和 owner 隔离

**seam**

公开 auth、workspace list/detail、events 和 download API。

**目标文件**

- 新增 `scripts/acceptance/k6/workspace-smoke.js`
- 新增 `scripts/acceptance/k6/lib/`
- `scripts/acceptance/run_phase4b_8b1.ps1`

**RED**

先以 2 VU 对两个真实用户运行，验证：

- 各自登录和读取自己的任务；
- 跨用户 task/detail/events/download 成功数为 0；
- 401/403/404 行为符合既有公开契约；
- 请求和断言结果输出 k6 JSON。

**GREEN**

只补验收脚本或真实权限漏洞的最小修复；不得为 k6 放宽用户审批和 owner 规则。

### 8B1-C5：10–20 VU 并发状态不变量

**seam**

公开任务创建、轮询/SSE、取消、版本和下载 API。

**目标文件**

- 新增 `scripts/acceptance/k6/workspace-concurrency.js`
- 新增 `scripts/acceptance/verify_concurrency_invariants.py`

**TDD 子循环**

先 2 VU，再 10 VU，最后最多 20 VU；逐项增加：

1. 同一请求幂等键不产生重复正式交付；
2. 每个任务状态只沿允许状态机变化；
3. 取消后的任务不发布新 Delivery；
4. 完成任务只有通过 QA/完整性校验的 `output_id` 可下载；
5. 用户之间任务、事件和文件不串线；
6. 重任务并发仍符合当前单进程信号量语义。

**停止条件**

- 2 VU smoke 尚未通过；
- 本地模型容量无法支撑正确性验证；
- 需要改变业务状态机、权限或交付语义；
- 测试开始形成对服务器吞吐量的错误承诺。

### 8B1-C6：Toxiproxy 工具 PoC 和代理边界

**seam**

验收 Compose 中可识别的 Toxiproxy service、代理 API 和被代理的真实外部连接。

**目标文件**

- `docker/mangrove/compose.fault.yml`
- 新增 `scripts/acceptance/toxiproxy/configure.py`
- 新增 `scripts/acceptance/toxiproxy/reset.py`

**RED**

先验证锁定镜像可拉取、manifest 支持当前平台、代理可创建/删除、toxic 可添加/清除，
并保存镜像 digest。任一不成立时停止，不临时换成不明镜像。

**GREEN**

把已确认需要故障注入的模型/解析服务连接显式指向验收代理；日常 `.env` 和外部端点不改写。

### 8B1-C7：网络超时、断连和恢复

**seam**

真实任务 API、Toxiproxy 和任务终态。

**目标文件**

- `scripts/acceptance/toxiproxy/`
- 新增 `scripts/acceptance/scenarios/network_faults.py`

**TDD 子循环**

逐项注入并在每项后 reset：

1. 延迟超过客户端超时；
2. 单向断连；
3. 短时不可用后恢复；
4. 重复同一故障指纹。

断言：

- 重试次数和有界 Harness 规则一致；
- 用户得到基本可理解的失败阶段和原因；
- 内部详细诊断只进入管理员/开发证据；
- 不发布坏 Delivery；
- 恢复后新任务能成功，旧失败任务不会被伪装为成功。

### 8B1-C8：API 精确重启和 Phoenix fail-open

**seam**

允许的验收 Compose project/service labels。

**目标文件**

- 新增 `scripts/acceptance/scenarios/container_restart.py`
- `docker/mangrove/compose.acceptance.yml`

**TDD 子循环**

1. 标签不匹配时拒绝 kill/restart；
2. 精确停止验收 API，SSE 断开但任务持久状态不损坏；
3. API 恢复后可重新登录和读取任务；
4. Phoenix 停止时核心任务继续，遥测标记降级；
5. Phoenix 恢复后新遥测可写入；
6. 日常 `mangrove-dev` 和其他 Docker 项目不受影响。

### 8B1-C9：受限磁盘/ENOSPC 故障

**seam**

验收专用 tmpfs/受限 volume 和公开任务结果。

**目标文件**

- `docker/mangrove/compose.fault.yml`
- 新增 `scripts/acceptance/scenarios/disk_full.py`

**RED**

只对验收 project 的可丢弃执行目录施加小容量 tmpfs，触发写满并断言：

- 任务失败在正确阶段；
- 不产生 `delivery_published`；
- 原上传和既有正式交付不被破坏；
- 清除故障并重启后新任务可成功。

不得对用户当前 `data/`、Docker Desktop 全局磁盘或日常 volume 注入故障。

### 8B1-C-GATE：切片门禁与用户确认

**必须通过**

- live Playwright 无 `page.route()`；
- 真实 DOCX → 商务条款 TXT 下载内容机械验真；
- 2 VU smoke 后的 10–20 VU 正确性；
- owner、幂等、状态、取消、QA 和下载不变量；
- 网络、API、Phoenix、磁盘故障可恢复；
- 坏结果发布数为 0；
- 所有故障资源已按标签确认并清理，持久数据保留。

**用户确认点**

展示真实下载样例的脱敏摘要、并发矩阵、故障矩阵和失败/恢复证据。用户确认后才进入
8B-1d。

---

## 7. 8B-1d：备份恢复、报告与用户说明

### 8B1-D1：SQLite 一致性 snapshot

**seam**

备份 CLI 对真实 SQLite 的输入和 staging 输出。

**目标文件**

- 新增 `tests/test_phase4b_8b1_backup.py`
- 新增 `scripts/ops/backup_phase4b_8b1.py`

**RED**

用仍有读写连接的临时 SQLite 建立已知用户、任务和 Delivery 数据，运行备份后断言：

- 使用 SQLite backup API 得到一致 snapshot；
- `PRAGMA quick_check` 通过；
- snapshot 包含备份开始前已提交的数据；
- 不复制 `-wal`/`-shm` 当作一致性方案；
- 源 DB 未被修改。

**GREEN**

实现只读源、staging snapshot、完整性检查和结构化 manifest；失败时不进入 restic。

**VERIFY**

```powershell
python -m pytest `
  tests/test_phase4b_8b1_backup.py `
  -k "sqlite_snapshot" `
  --basetemp .pytest-tmp/8b1-d1 -q
```

### 8B1-D2：备份清单、哈希与秘密排除

**seam**

备份 CLI 的结构化 manifest。

**目标文件**

- `tests/test_phase4b_8b1_backup.py`
- `scripts/ops/backup_phase4b_8b1.py`

**TDD 子循环**

1. 上传、语义执行、交付和必要 DB 被纳入；
2. 每个文件有相对路径、大小和 SHA-256；
3. `.env`、Cookie、token、`runtime/secrets`、测试会话和缓存被排除；
4. 路径逃逸和符号链接逃逸失败关闭；
5. manifest 不含宿主绝对路径。

### 8B1-D3：restic 本地仓库 PoC 和快照

**seam**

锁定 restic 镜像/CLI、验收 staging 和本地 backup repo。

**目标文件**

- `docker/mangrove/compose.acceptance.yml` 或独立受控工具调用
- `scripts/ops/backup_phase4b_8b1.py`
- `scripts/acceptance/check_restic.py`

**RED**

验证锁定镜像可拉取、平台兼容和 digest；初始化隔离 repo、备份 staging、执行 `restic check`
并输出 snapshot ID。未安装/未拉取/校验失败都必须显式失败。

**GREEN**

- 首次生成高熵密码到 `runtime/secrets/`；
- 控制台和报告只提示用户保存密码，不打印明文；
- 不自动 `forget`、`prune` 或删除历史 snapshot。

### 8B1-D4：只恢复到新目录并逐文件验真

**seam**

恢复 CLI 和新目录。

**目标文件**

- 新增 `scripts/ops/restore_phase4b_8b1.py`
- `tests/test_phase4b_8b1_backup.py`

**RED**

尝试恢复到非空目录、工作区根、当前数据根或无法确认的路径，必须拒绝；恢复到
`runtime/restore-tests/<run_id>` 后逐文件校验大小和 SHA-256。

**GREEN**

实现明确的目录 containment、空目录要求、restic restore、manifest 比对和 SQLite
`quick_check`；禁止原地覆盖当前数据。

### 8B1-D5：恢复后的业务语义复验

**seam**

以恢复目录启动的独立 Compose project 和公开 HTTP API。

**目标文件**

- 新增 `scripts/acceptance/scenarios/restore_semantics.py`
- `docker/mangrove/compose.acceptance.yml`

**RED**

从恢复目录启动新 project，断言：

- 原 owner 可登录并查看任务；
- 原 Delivery 可下载且 SHA-256 一致；
- 其他用户无法访问；
- 上传预览仍可读；
- 新建任务不覆盖旧版本和旧交付。

任何只验证“文件复制成功”而未验证 owner/下载语义的结果都不算通过。

### 8B1-D6：`AcceptanceReport` 确定性聚合

**seam**

`build_report(result_files, output_markdown, output_html)`。

**目标文件**

- 新增 `tests/test_phase4b_8b1_report.py`
- 新增 `scripts/acceptance/report_phase4b_8b1.py`
- 新增本地报告 CSS

**TDD 子循环**

1. 全部 `passed` 时总状态通过；
2. 任一 `failed` 时总状态失败；
3. 必需结果文件缺失时生成失败项，而不是跳过；
4. `not_run` 不能计为通过；
5. `pending_8b2` 单独展示且不冒充本机失败或通过；
6. Markdown 和 HTML 显示相同 check ID、状态、证据和修复建议；
7. 输入非法、重复 check ID 或 run_id 不一致时失败关闭。

**GREEN**

用 `markdown-it-py` 和本地 CSS 生成 HTML；不解析人类日志推断成功，不调用 LLM，
不依赖 CDN。

**VERIFY**

```powershell
python -m pytest `
  tests/test_phase4b_8b1_report.py `
  --basetemp .pytest-tmp/8b1-d6 -q
```

### 8B1-D7：一键验收总编排

**seam**

`scripts/acceptance/run_phase4b_8b1.ps1`。

**目标文件**

- `scripts/acceptance/run_phase4b_8b1.ps1`
- 8B-1a～8B-1d 所有验收工具

**TDD 子循环**

1. 前置条件失败时停止且报告失败原因；
2. 每个工具写独立结构化结果；
3. 单项失败后仍执行安全的诊断/报告步骤，但不执行依赖该项的破坏性场景；
4. Ctrl+C/异常时恢复 Toxiproxy、停止验收 project、保留日志和数据；
5. 最终退出码与报告总状态一致；
6. 不停止日常 `mangrove-dev` 或其他项目。

**GREEN**

总脚本只负责编排已验证工具，不重复实现各工具逻辑。

### 8B1-D8：普通用户、管理员和开发者操作说明

**目标文件**

- `docker/mangrove/README.md`
- 新增 `docs/guides/phase4b-batch8b1-docker-desktop.md`
- 新增 `docs/guides/phase4b-batch8b1-acceptance.md`

**内容**

- 普通用户：如何通过本机/LAN 地址访问、上传、预览、运行、取消、下载、理解基本失败；
- 管理员：一键启停、分级健康、用户审批、备份、恢复演练、辅助降级；
- 开发者：干净镜像、live Playwright、k6、Toxiproxy、ENOSPC、证据目录；
- 明确 Docker Desktop 需要启动；
- 明确服务器尚未 ready，8B-2 实机项暂不执行；
- 给用户一份逐步验收操作说明，不要求用户理解内部测试术语。

**验证**

所有命令从干净 PowerShell 会话逐条复制运行；路径含空格；所有链接和文件名存在；不得
写入当前工具实际不支持的参数。

### 8B1-D9：同步知识基座和未完成项

**目标文件**

- `AGENTS.md`
- `CLAUDE.md`，仅当操作约定确实变化；
- `CONTEXT.md`，仅当术语或接口确实变化；
- `handoff.md`
- `docs/adr/0016-docker-desktop-unified-development-and-clean-image-gate.md`，
  仅当决策变化；
- `docs/adr/README.md`
- 本规格、本文和 8B-1 执行报告。

**规则**

- 分别记录：代码已完成、内部验证、用户验收、未运行和待 8B-2；
- 8B-2 保留服务器安装、真实服务器容量、真实网络/TLS/域名、目标机备份介质和最终实机
  验收；
- 批次 7 已登记问题不因 8B-1 通过而自动关闭；
- 不创建版本、标签或发布外部内容；
- 不提交用户运行数据和无关工作树改动。

### 8B1-D-GATE：全量门禁、审查与用户确认

**代码门禁**

```powershell
python -m pytest `
  tests/test_file_upload_security.py `
  tests/test_managed_paths.py `
  tests/test_semantic_path_portability.py `
  tests/test_workspace_readiness.py `
  tests/test_phase4b_8b1_backup.py `
  tests/test_phase4b_8b1_report.py `
  tests/test_semantic_harness_loop.py `
  tests/test_semantic_workspace_api.py `
  --basetemp .pytest-tmp/8b1-final -q

npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run test:e2e
git diff --check
```

以上文件名如果在实施中按既有测试布局合并，以执行报告中的真实 node id 为准，不得用
“未收集到测试”冒充通过。

**真实门禁**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts/acceptance/run_phase4b_8b1.ps1 `
  -RunId final-local
```

**审查**

切片完成后进入独立审查阶段，按：

- Standards：是否符合仓库文档、UTF-8、最小改动和安全约定；
- Spec：是否逐项满足本规格与本文；
- Evidence：是否有与风险相称的真实验证；
- Scope：是否混入批次 7 细节、Phase 5B、版本或外部发布。

审查发现的问题回到对应任务修复并重跑证据，不在审查中顺手重构。

**用户确认点**

展示：

- 变更文件 allowlist；
- 各门禁真实命令、退出码和摘要；
- `report.md` / `report.html`；
- 用户手工验收步骤；
- 8B-2 待办；
- 已知限制和未解决问题。

只有用户明确确认 8B-1 后，才能把状态写为“用户验收通过”。GitHub 推送、创建 PR、
版本分支或标签均是独立外部操作，需要另行明确确认。

---

## 8. 依赖顺序

```text
A0
 └─ A1 → A2 → A3 → A4 → A5 → A6 → A7 → A8 → A9 → A10 → A-GATE
                                                               │
                                                               ▼ 用户确认
B1 → B2 → B3 → B4 → B5 → B6 → B7 → B8 → B9 → B-GATE
                                                               │
                                                               ▼ 用户确认
C1 → C2 → C3 → C4 → C5 → C6 → C7 → C8 → C9 → C-GATE
                                                               │
                                                               ▼ 用户确认
D1 → D2 → D3 → D4 → D5 → D6 → D7 → D8 → D9 → D-GATE
```

依赖不能倒置：

- 未证明路径可移植前，不用真实用户数据进入 Linux 容器；
- 未证明一键栈隔离前，不做容器 kill 或磁盘故障；
- 2 VU smoke 未通过前，不升到 10–20 VU；
- SQLite snapshot 未通过前，不把目录交给 restic；
- 恢复文件哈希未通过前，不做业务语义复验；
- 结构化检查未齐全前，不生成“通过”总报告。

## 9. 全局停止条件

出现任一情况，停止当前任务并展示证据：

- 要改变业务范围、字段含义、ResultContract、权限或正式交付语义；
- 只能通过批量改写旧资料完成兼容；
- 需要向未获确认的外部模型或服务发送用户内容；
- Linux 依赖要求大范围升级，可能影响批次 1–7；
- 需要修改 Docker Desktop 全局设置、提升宿主权限或停止未知进程；
- 故障目标无法用 Compose project/service labels 精确识别；
- 恢复目标不是经过机械确认的新目录；
- 需要物理删除、覆盖、版本、标签、PR、推送或其他外部发布；
- 内部测试通过但真实公开接口、真实下载或真实恢复尚未验证。

## 10. 本阶段完成标准

本任务拆分获得用户确认后，任务拆分阶段才算完成。下一步只允许开工
**8B-1a：数据可移植性和最小干净镜像**，从 `8B1-A0` 开始。

不得因为本文已写完而：

- 修改业务代码；
- 启动故障注入；
- 清理或迁移用户数据；
- 自动进入 8B-1b；
- 标记 8B-1 已完成；
- 推送 GitHub 或创建版本/标签。
