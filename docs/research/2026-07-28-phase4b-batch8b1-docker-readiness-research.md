# Phase 4B 批次 8B-1 Docker Desktop 服务器就绪包技术调研

> 日期：2026-07-28
>
> 阶段：历史技术调研；8B 已后置，当前不实施
>
> 范围：当前 Windows 开发机 + Docker Desktop；仅覆盖 Phase 4B 统一任务闭环及当前必需依赖
>
> 资料口径：项目当前代码、当前本机只读探测、官方文档和官方仓库

> 调度说明（2026-07-29）：本文只作未来服务器阶段的历史输入。Agentic Runtime vNext
> 的任务级 Docker 功能沙箱是独立安全边界，不激活本文的整机容器化、干净应用镜像、
> 并发或服务器就绪工作。

## 1. 结论

批次 8B-1 可以在当前开发机落地，但不能只把旧 Docker 配置换一个启动命令。当前存在一个
必须先解决的 P0 兼容问题：既有上传元数据和正式交付记录保存了 Windows 绝对路径，Linux
容器无法直接读取这些路径。

推荐方案如下：

1. 新增一套面向 React、FastAPI 和 Phase 4B 工作台的 Docker 配置，不覆盖旧
   Streamlit/WebSocket 历史配置；
2. 日常开发使用 Vite 容器和 FastAPI 容器；工作台的两个异步 worker 继续内嵌在
   FastAPI 进程，不在 8B-1 越界拆成独立队列服务；
3. 干净镜像验收使用一个多阶段应用镜像：Node 阶段构建前端，Python 阶段运行 FastAPI，
   FastAPI 同源托管 `frontend/dist`，不挂载宿主源码；
4. 对旧 Windows 绝对路径增加只读兼容解析；旧元数据和数据库不迁移、不改写，新记录改为
   保存受控根目录下的可移植相对路径；
5. `start_all.bat` 作为唯一日常启动入口，自动启动并等待 Docker Desktop，定向启动
   Mangrove、Phoenix、SearXNG、Firecrawl 和 RSSHub；`stop_all.bat` 只定向停止同组资源，
   始终保留数据；
6. 复用 Playwright 完成一个真实 PC 闭环；新增 Docker 化 k6 做 10–20 VU 的并发正确性门；
7. 使用 Toxiproxy 注入模型/解析服务的 TCP 延迟、超时和断连；容器崩溃直接使用带固定
   Compose 项目名的 Docker 命令；磁盘不足只在受限测试 `tmpfs` 中模拟；
8. 备份采用“SQLite Online Backup API 生成一致快照，再由 restic 备份全部暂存资料”；
   恢复必须写入新目录，并重新验证用户归属、任务历史、证据关联、哈希和授权下载；
9. 一键验收生成中文 Markdown 和 HTML 报告，状态严格分为“通过”“失败”“待 8B-2
   实机验证”。

该方案不引入 PostgreSQL、RabbitMQ、Celery、Redis、GPU 调度、最终服务器防火墙或离线镜像
包。上述内容继续留在整个工程完成后的 8B-2 实机验收。

## 2. 事实、推断和建议的标记

本文使用以下标记，避免把方案当成已实现事实：

- **已验证事实**：由当前代码、本机命令或现有数据只读检查直接确认；
- **基于代码的推断**：由当前实现关系推导，尚未通过新 Docker 环境实跑；
- **尚未验证的建议**：推荐进入下一阶段规格，但尚未实现或执行。

## 3. 当前仓库与本机事实

### 3.1 当前 Docker 入口已经过时

**已验证事实：**

- 根目录 `docker-compose.yml` 的主应用仍执行
  `streamlit run streamlit_app.py --server.port 8501`；
- 根目录 `Dockerfile` 的默认命令仍是
  `python demo_agent_mcp_v1/websocket_server.py`；
- 当前真实应用入口是 `src/api/main.py`，运行 FastAPI 8088；
- 开发前端是 React/Vite 5173；
- 生产模式由 FastAPI 同源托管已构建的 `frontend/dist`；
- `frontend/vite.config.ts` 把 `/api` 固定代理到
  `http://localhost:8088`，在前后端分离容器中无法解析为 API 容器；
- 当前 `start_all.bat` 硬编码了仓库盘符和 Python 路径，并在 Windows 宿主机启动前后端；
- 当前 `stop_all.bat` 按 8088/5173 端口结束任意监听进程，无法证明被结束进程属于
  Mangrove。

因此，旧根 Docker 入口不能作为 8B-1 的实施基线。

### 3.2 工作台 worker 不是独立服务

**已验证事实：**

- `src/api/main.py` 的 FastAPI lifespan 调用
  `get_semantic_workspace_manager().start()`；
- `SemanticWorkspaceManager` 在同一个 Python 进程中创建两个 `asyncio` worker；
- 重任务通过进程内 `asyncio.Semaphore(1)` 限制并发；
- 当前没有外部消息队列、独立 worker 进程或 worker 容器协议。

**基于代码的推断：**

如果 8B-1 把 worker 拆成独立容器，就必须重新设计队列、租约、进程间唤醒、并发锁和恢复
边界。这实质上是后续 Phase 5B 的分布式执行改造，而不是 Docker 包装。

**尚未验证的建议：**

8B-1 保持 worker 内嵌 FastAPI。健康报告把“工作台 worker”列为核心能力检查项，但不虚构
一个不存在的独立服务。

### 3.3 当前持久化资料

2026-07-28 的只读盘点结果：

| 路径或数据库 | 当前规模 |
|---|---:|
| `data/webui.db` | 约 18.2 MB |
| `data/checkpoints.sqlite` | 约 123 MB |
| `data/uploads` | 192 个文件，约 156 MB |
| `data/semantic-executions` | 20 个文件，约 68 MB |
| `downloads` | 1,074 个文件，约 228 MB |

主要默认路径来自 `src/config/settings.py`：

- `WEBUI_DB_PATH=data/webui.db`；
- `CHECKPOINT_DB_PATH=data/checkpoints.sqlite`；
- `DATABASE_URL=sqlite:///./data/app.db`；
- `DATA_PREP_UPLOAD_ROOT=data/uploads`；
- `SEMANTIC_EXECUTION_ROOT=data/semantic-executions`。

`ArtifactStore` 的默认根仍硬编码为相对路径 `downloads`。因此容器必须使用稳定工作目录，
并把 `data`、`downloads`、`logs` 等持久资料显式挂载到固定容器路径。

### 3.4 P0：既有资料含 Windows 绝对路径

**已验证事实：**

- `data/uploads` 中有 96 个 `.meta`；
- 96 个 `.meta` 的 `storage_path` 全部是 Windows 盘符绝对路径；
- `UploadStore.resolve()` 当前原样返回 sidecar 中的 `storage_path`；
- `FileConnector` 随后直接执行 `Path(item.storage_path).read_bytes()`；
- `semantic_delivery_outputs.file_path` 的 7/7 条记录包含 Windows 绝对路径；
- `semantic_delivery_runs.output_dir` 的 6/6 条记录包含 Windows 绝对路径；
- `semantic_harness_attempts.artifact_paths_json` 的 6/12 条记录包含 Windows 绝对路径；
- 下载接口直接对数据库中的 `file_path` 执行 `Path(...).is_file()` 和哈希检查。

**影响：**

即使把现有 `data` 和 `downloads` 正确挂载到 Linux 容器，旧任务的原文件预览、再次执行和
正式下载仍会因为 `F:\...` 路径在 Linux 中无效而失败。仅写 Compose 无法解决该问题。

**尚未验证的建议：**

实现一个集中、可测试的“受控存储路径解析器”：

1. 解析上传时，以已校验的 `user_id + upload_id` 在当前 `UploadStore.root` 下重新构造对象
   路径，不信任旧 sidecar 的绝对路径；
2. 解析交付和执行产物时，只允许把旧绝对路径映射到已知
   `semantic_execution_root`/`downloads` 下的相对子路径；
3. 映射结果必须经过 `resolve()`、根目录包含关系、文件存在、大小和 SHA-256 校验；
4. 不原地改写旧 `.meta` 或 `webui.db`；
5. 新记录只保存相对受控根目录的 POSIX 风格路径；
6. 同一组测试必须证明 Windows 宿主直接运行和 Linux 容器运行都能读取新旧记录。

这是一项技术兼容修复，不改变任务含义、用户归属、证据或交付内容。

### 3.5 容器网络地址需要显式覆盖

**已验证事实：**

- `.env.example` 中 Phoenix OTLP 地址默认是
  `http://127.0.0.1:6006/v1/traces`；
- Vite API 代理当前固定为 `http://localhost:8088`；
- 容器内的 `127.0.0.1` 只指向当前容器，不指向 Phoenix、API 或 Windows 主机；
- 当前本地模型和解析服务包含局域网地址。

**尚未验证的建议：**

- 容器间地址使用 Compose 服务名，例如 `http://phoenix:6006/v1/traces` 和
  `http://api:8088`；
- Windows 主机上的服务使用 Docker Desktop 官方提供的
  `host.docker.internal`；
- 独立 LAN 主机继续使用实际局域网 IP；
- 启动前置检查根据地址类型分别探测，禁止把容器内 `localhost` 误判为宿主服务。

### 3.6 当前 Docker Desktop 能力

本机实测：

| 项目 | 结果 |
|---|---|
| Docker Desktop 状态 | running |
| Docker Client | 29.6.1 |
| Docker Server | 29.6.1 |
| Docker Compose | 5.1.4 |
| Docker Desktop CLI 插件 | 0.4.1 |
| Phoenix 镜像 | `arizephoenix/phoenix:19.10.0` 已存在，Linux/amd64 |

当前版本支持：

- `docker desktop start/status`；
- Compose 固定项目名；
- `depends_on.condition: service_healthy`；
- `docker compose up --wait --wait-timeout`；
- Compose Watch；
- 多阶段镜像构建；
- bind mount、命名卷和 `tmpfs`。

本机还验证了 `docker compose up -d --watch` 会明确报错：`--detach` 不能与 `--watch`
组合。因此，日常“一键后台启动 + 持续热更新”不应假设 Compose Watch 可以脱离客户端
进程后台运行。

## 4. 推荐 Docker 架构

### 4.1 日常开发拓扑

**尚未验证的建议：**

```text
可信局域网 PC
    │
    ├─ 5173 → frontend-dev（Vite HMR）
    └─ 8088 → api-dev（FastAPI + 内嵌 workspace workers）
                    │
                    ├─ data / uploads / executions / downloads
                    ├─ Phoenix（辅助）
                    ├─ LAN 模型与文档解析服务
                    └─ SearXNG / Firecrawl / RSSHub（随启停，不计 8B-1 业务验收）
```

开发态使用两个应用容器：

- `frontend-dev`：只运行 Vite，保留 HMR；
- `api-dev`：运行现有 `scripts/dev_reload.py`，保留当前进程外重载行为和内嵌 worker。

热更新优先使用精确 bind mount 和应用已有 watcher。原因不是 Compose Watch 不成熟，而是
Compose Watch 需要一个持续运行的宿主 CLI 进程；当前一键后台启停若再维护该进程，会增加
PID 管理和误杀风险。依赖变更仍通过显式重新构建镜像处理。

### 4.2 干净镜像验收拓扑

**尚未验证的建议：**

```text
Node 构建阶段
    └─ npm ci + npm run build
              │
              ▼
Python 运行阶段
    ├─ 安装锁定 Python 依赖
    ├─ COPY src、scripts、必要运行资产
    ├─ COPY --from=frontend-builder frontend/dist
    └─ FastAPI 8088 同源托管前端 + 内嵌 worker
```

干净镜像门必须满足：

- 不挂载 `src`、`frontend/src` 或整个宿主仓库；
- 使用隔离 Compose 项目名、端口和测试目录；
- 只挂载公开脱敏夹具、隔离数据和验收证据目录；
- 真实完成登录、上传、任务、正式交付和下载；
- 容器重建后任务状态和结果仍可恢复；
- 镜像构建成功不等于业务闭环通过。

### 4.3 为什么不做三个生产应用容器

前端构建产物已经由 FastAPI 同源托管，worker 也已经内嵌 FastAPI。为了“看起来像微服务”
而拆出 frontend-prod 和 worker，会增加反向代理、静态资源、队列和生命周期边界，却不能
提升 8B-1 的验证价值。

推荐：

- 开发态：Vite + API 两个应用容器；
- 干净镜像验收态：一个 Mangrove 应用容器；
- Phoenix 和现有外部依赖保持独立容器或 LAN 服务。

### 4.4 一个入口不等于一个巨型 Compose

`start_all.bat` 可以一次完成所有操作，但不必把 Firecrawl、SearXNG、RSSHub 的既有
Compose 强行合并为一个巨型文件。推荐由脚本使用稳定项目名和显式 Compose 文件逐组启动，
保留各组件的现有边界，同时向用户提供一个启动摘要。

## 5. 一键启停与健康模型

### 5.1 `start_all.bat`

建议流程：

1. 用 `%~dp0` 解析仓库目录，禁止硬编码盘符；
2. 检查 Docker CLI 和 Docker Desktop CLI 是否存在；
3. Docker Engine 未就绪时执行 `docker desktop start`；
4. 在明确超时内轮询 `docker desktop status` 和 `docker info`；
5. 执行只读路径兼容预检和首次备份门；
6. 使用固定 Compose 项目名启动 Mangrove 核心服务；
7. 等待 API、前端、worker 状态、数据库和存储就绪；
8. 启动并检查 Phoenix、SearXNG、Firecrawl、RSSHub；
9. 检查本地/LAN 模型和解析服务；
10. 输出局域网访问地址、服务分级状态、失败检查编号和下一步。

核心服务失败时返回非零退出码。辅助服务失败时返回“部分能力不可用”，不得显示成全部正常。
模型或解析服务不可达时，页面可以启动，但完整 8B-1 验收必须失败。

### 5.2 `stop_all.bat`

建议流程：

- 只使用固定项目名、Compose 文件和服务名定位资源；
- 先请求正常停止，再在超时后报告未停止对象；
- 保留数据库、上传、交付、Phoenix 数据和所有持久卷；
- 禁止按端口结束身份不明的进程；
- 禁止 `down -v`、`prune` 和删除宿主数据目录；
- 不在正常停止入口内隐藏“重置环境”。

### 5.3 健康检查层级

| 层级 | 示例 | 失败影响 |
|---|---|---|
| L1 进程存活 | API HTTP、Vite/静态页、Phoenix HTTP | 对应服务不可用 |
| L2 核心就绪 | SQLite 可打开、worker 已启动、上传/执行/交付根可访问 | 整体启动失败 |
| L3 能力就绪 | 模型、文档解析、OTLP、外部辅助服务 | 分级降级；完整验收可能失败 |
| L4 业务探针 | 公开夹具完成上传到正式下载 | 8B-1 验收失败 |

当前 `/api/health` 只证明 FastAPI 进程能响应，不能证明 worker、数据库、存储、模型或解析器
可用。下一阶段规格应定义稳定检查编号和最小只读/临时写探针，但不能让健康接口执行重型
真实任务。

## 6. 并发正确性工具选型

### 6.1 选择 k6

推荐以 Docker 化 [Grafana k6](https://grafana.com/docs/k6/latest/) 作为协议级并发工具。
截至本次调研，官方最新 release 为
[v2.1.0](https://github.com/grafana/k6/releases/tag/v2.1.0)，该版本说明没有新的 breaking
change；正式写入规格前仍需完成镜像拉取和脚本 PoC，并固定镜像 digest。

k6 适合当前任务的原因：

- 可在独立容器运行，不污染 Mangrove Python 环境；
- scenarios 能表达 10–20 VU、固定迭代、顺序和并行场景；
- checks 验证单请求，thresholds 使聚合门禁返回非零退出码；
- 自定义 metric 可以统计跨用户泄露、重复交付、错误终态和超限重试；
- `handleSummary()` 可以输出结构化 JSON，供最终中文报告聚合；
- 支持纯本地执行，不需要 Grafana Cloud。

8B-1 的 k6 门不设置生产 p95 指标。建议只设置以下硬门：

- 跨用户读取、修改、事件和下载成功数必须为 0；
- 一个 run 的正式 Delivery 数不得大于 1；
- 终态回退数必须为 0；
- 取消后发布数必须为 0；
- 无界重试/超时任务数必须为 0；
- API/worker 进程崩溃数必须为 0。

延迟、吞吐、CPU 和内存仅记录为开发机基线，不作为生产通过线。

### 6.2 k6 场景边界

k6 负责 API 并发，不承担浏览器 UI 验收或重型模型性能测试。推荐场景：

1. 10–20 个隔离测试用户并发登录和读取自己的任务；
2. 并发创建轻量公开夹具任务并轮询状态；
3. 交叉尝试读取其他 VU 的任务、事件和交付；
4. 对受控任务执行取消、恢复和重复查询；
5. 对预制成功结果并发下载并校验状态、大小和哈希；
6. 结束后通过管理侧只读核查统计重复 run/Delivery 和异常状态。

真实 DOCX 模型闭环继续串行，避免 20 个 VU 同时争抢当前单个重任务信号量和本地模型。

### 6.3 为什么不默认使用 Locust

[Locust](https://docs.locust.io/en/stable/) 也是成熟工具，截至本次调研官方最新 release 为
[2.46.2](https://github.com/locustio/locust/releases/tag/2.46.2)。它的 Python 用户模型
适合复杂自定义流程，但会引入另一组 Python 依赖和运行环境，且当前需求不需要其 Web UI
或分布式 worker。

因此：

- 默认采用 Docker 化 k6；
- 只有 k6 PoC 无法稳定处理当前认证、SSE/轮询或文件上传时，才回退 Locust；
- 不同时维护两套负载脚本。

### 6.4 继续复用 Playwright

现有 Playwright 继续负责一条真实 PC 用户路径：

- 登录；
- 上传公开脱敏文件；
- 第一时间看到原文件预览；
- 提交自然语言任务；
- 看到顺序正确的阶段状态；
- 完成后查看结果和来源；
- 下载正式交付并机械校验。

Playwright 自带 HTML/JSON/JUnit reporter，不新增 Cypress 或 Selenium。用户还需要从另一台
局域网 PC 手工完成一次登录、提交和下载，作为 LAN 可达性的唯一人工步骤。

## 7. 故障注入工具选型

### 7.1 网络故障：Toxiproxy

推荐固定
[Shopify Toxiproxy v2.12.0](https://github.com/Shopify/toxiproxy/releases/tag/v2.12.0)，
使用官方 GHCR 镜像候选 `ghcr.io/shopify/toxiproxy:2.12.0`。

Toxiproxy 提供 HTTP API，可确定性增加和移除：

- latency/jitter；
- timeout；
- reset_peer；
- bandwidth/limit_data；
- 整条代理 down。

适用对象只限验收栈到模型、解析服务或其他受控 TCP 上游。故障结束后调用 `/reset` 并再次
验证正常请求。日常生产链路不默认经过 Toxiproxy。

本次只确认了官方 release；由于容器仓库 TLS 握手超时，镜像 manifest 尚未在本机验证。
该项必须在规格后的 PoC 中完成，不能把 tag 存在等同于镜像已可用。

### 7.2 容器故障：直接使用 Docker Compose

API 容器终止和重启不需要再引入 chaos 框架。验收脚本可以使用固定项目名和服务名：

- 终止验收项目内的指定 API 容器；
- 确认任务没有重复正式发布；
- 重启同一服务；
- 确认持久状态恢复和错误可理解。

命令执行前必须机械确认目标容器同时具有预期
`com.docker.compose.project` 和 `com.docker.compose.service` 标签。

### 7.3 磁盘不足：受限 `tmpfs`

Docker Compose 官方支持带 `size` 的 `tmpfs`。建议只在专用故障场景把验收应用的某个输出
根挂载到小容量 `tmpfs`，使用公开夹具触发 `ENOSPC`：

- 不填充宿主磁盘；
- 不挂载日常 `data` 或 `downloads`；
- 验证错误信息、任务终态、临时文件清理和坏产物拒绝发布；
- 销毁专用容器即可恢复。

### 7.4 为什么不引入 Pumba

[Pumba](https://github.com/alexei-led/pumba) 能执行容器 kill、pause、netem 和资源压力，但
官方用法需要访问 `/var/run/docker.sock`，部分能力还需要 root、网络命名空间、`tc` 或
iptables。Docker 官方提醒，拥有 Docker daemon 控制凭据等价于获得主机高权限。

8B-1 的故障集合可由 Toxiproxy、受限 `tmpfs` 和宿主验收脚本安全覆盖，因此不应为了少量
场景向 sidecar 暴露 Docker Socket 或额外特权。

## 8. 备份与恢复工具选型

### 8.1 两阶段备份

推荐：

```text
运行中的 SQLite
    │
    └─ SQLite Online Backup API
          └─ 一致数据库快照
                  │
现有文件 + 数据库快照 + 配置白名单
                  │
                  └─ manifest + SHA-256
                          │
                          └─ restic 本地加密仓库
```

不能直接让普通文件复制工具读取正在写入的 SQLite 主文件。SQLite 官方
[Online Backup API](https://www.sqlite.org/backup.html) 可以在数据库在线时生成一致快照，
只在短暂读取期间加锁。

文件暂存完成后，推荐使用
[restic v0.19.1](https://github.com/restic/restic/releases/tag/v0.19.1) 写入本机独立加密
仓库。restic 提供快照、去重、校验和恢复能力；但它不替代 SQLite 一致快照。

### 8.2 备份范围

至少覆盖：

- `webui.db`；
- `scheduler.db`、`checkpoints.sqlite` 和实际存在的其他当前 SQLite；
- `data/uploads`；
- `data/semantic-executions`；
- `downloads` 中 Phase 4B 正式交付、Manifest 和证据；
- 恢复所需的非敏感配置白名单；
- 版本、时间、文件数量、大小和 SHA-256 清单。

`.env`、Cookie、Token 和密钥不能原文写入普通报告。若恢复确实依赖秘密配置，只记录
“秘密项存在/缺失”和受控外部秘密文件引用。

### 8.3 恢复门

恢复必须：

1. 创建全新的隔离目标目录；
2. 执行 `restic check`；
3. 恢复指定快照，禁止覆盖日常目录；
4. 对数据库执行 `quick_check` 和应用 schema 读取；
5. 对文件清单重新计算 SHA-256；
6. 启动隔离恢复栈；
7. 使用两个测试用户验证任务归属；
8. 验证历史结果、证据关联、正式 Delivery 和授权下载；
9. 验证跨用户访问仍被拒绝；
10. 报告快照 ID、检查结果和待 8B-2 项。

restic 的仓库级 `check` 只能证明备份仓库结构，不证明 Mangrove 任务语义正确，因此必须
保留上述应用级复验。

## 9. 一键验收和报告

### 9.1 推荐入口

提供一个 PowerShell 主入口，内部调用成熟工具：

- Docker Desktop CLI；
- Docker Compose；
- pytest；
- Playwright；
- k6；
- Toxiproxy；
- SQLite backup；
- restic。

主入口只做编排、检查编号映射和报告聚合，不重写这些工具已经提供的测试、负载、故障和
备份能力。

### 9.2 为什么报告聚合需要少量项目代码

现有工具分别产出 JSON、JUnit、HTML、日志和退出码，没有一个成熟工具能直接表达 Mangrove
特有的：

- 核心/辅助服务分级；
- 业务生命周期不变量；
- 正式 Delivery/QA/Manifest；
- “本机通过”与“待 8B-2 实机验证”；
- 中文用户操作说明和稳定检查编号。

因此建议写一个薄的确定性报告聚合器，只读取各工具的结构化输出，不自行实现测试框架。
Playwright 原生 HTML 报告作为附件保留，k6 用 `handleSummary()` 输出 JSON。

### 9.3 稳定检查编号

建议编号：

| 前缀 | 含义 |
|---|---|
| `ENV-*` | Docker Desktop、Compose、镜像和端口 |
| `CORE-*` | API、前端、worker、SQLite 和存储 |
| `CAP-*` | 模型、解析器、Phoenix 和辅助能力 |
| `FLOW-*` | 上传、任务、结果和正式下载 |
| `CONC-*` | 10–20 VU 并发正确性 |
| `FAULT-*` | 网络、容器、磁盘和恢复 |
| `BACKUP-*` | 快照、哈希、restic 和恢复 |
| `LAN-*` | 另一台 PC 的登录、提交和下载 |
| `SERVER-*` | 只能在 8B-2 验证的服务器项目 |

报告首页只展示结论和失败项，详细日志放在附件目录。失败必须给出下一步，不要求普通用户
阅读 Docker 原始日志。

## 10. 工具决策表

| 能力 | 推荐工具 | 决策 | 原因 |
|---|---|---|---|
| 容器编排 | Docker Desktop + Compose | 采用 | 当前环境已安装，支持固定项目名、健康等待和隔离 |
| 开发热更新 | Vite + `dev_reload.py` + 精确 bind mount | 采用 | 复用现有 watcher，符合后台一键启动 |
| Compose Watch | Docker Compose Watch | 暂不作为日常入口 | `--detach` 与 `--watch` 冲突，需要额外宿主常驻进程 |
| 干净镜像 | Docker 多阶段构建 | 采用 | 前端构建与 Python 运行层分离，不依赖宿主源码 |
| PC 用户闭环 | Playwright | 复用 | 现有框架和报告器已具备 |
| API 并发正确性 | k6 | 采用，先 PoC | Docker 隔离、场景/门禁/JSON 输出适配 |
| 备选并发工具 | Locust | 不并行引入 | 会增加第二套 Python 负载环境 |
| TCP 故障 | Toxiproxy | 采用，先验证镜像 | 可逆、确定性、HTTP API |
| 容器故障 | Docker Compose 精确服务操作 | 采用 | 无需额外框架 |
| 容器 chaos | Pumba | 不采用 | Docker Socket/特权范围超过本批需求 |
| 磁盘不足 | Compose 限额 `tmpfs` | 采用 | 不影响宿主和日常数据 |
| SQLite 一致快照 | SQLite Online Backup API | 采用 | 运行中数据库的一致备份 |
| 文件快照/校验/恢复 | restic | 采用，先 PoC | 加密、快照、校验、恢复成熟 |
| 中文总报告 | 薄聚合器 + 原生工具报告 | 采用 | 只补 Mangrove 特有状态语义 |

## 11. 实施难度和成本

估算前提：

- 一名熟悉当前代码的工程师；
- 当前 LAN 模型和解析服务可用；
- 不包含修复验收中发现的未知业务 Bug；
- 不包含 8B-2 服务器实机工作。

| 工作包 | 难度 | 估算 |
|---|---|---:|
| 路径可移植兼容层及新旧数据测试 | 高 | 2–3 天 |
| Linux Python 3.13 依赖构建和多阶段镜像 | 高 | 3–5 天 |
| 开发 Compose、热更新、持久挂载 | 中高 | 2–3 天 |
| `start_all.bat`/`stop_all.bat` 和分级健康 | 中 | 2–3 天 |
| 干净镜像真实闭环与 Playwright | 高 | 2–4 天 |
| k6 10–20 VU 并发正确性门 | 中高 | 2–3 天 |
| Toxiproxy、容器故障、受限磁盘故障 | 中高 | 2–3 天 |
| SQLite + restic 备份恢复闭环 | 高 | 2–4 天 |
| 中文 Markdown/HTML 报告和用户验收说明 | 中 | 1.5–2.5 天 |
| 回归和风险缓冲 | 高 | 2–4 天 |

总估算为 **20.5–34.5 工程日**。最可能区间约 **24–29 工程日**。

最大不确定性不是 Docker YAML，而是：

1. 全量 Python 3.13 依赖在 Linux 镜像中的 wheel、系统库和浏览器依赖；
2. Windows 绝对路径兼容层对历史上传、执行、交付和证据链的覆盖；
3. Docker 容器到当前 LAN 模型/解析服务的实际网络可达性；
4. 真实闭环可能重新暴露批次 7 尚未完成的产品缺陷。

建议下一阶段按四个可独立验收的实现切片推进：

1. **8B-1a：数据可移植性 + 最小干净应用镜像**；
2. **8B-1b：日常 Docker 开发环境 + 一键启停 + 分级健康**；
3. **8B-1c：真实闭环 + k6 并发 + 安全故障注入**；
4. **8B-1d：备份恢复 + 中文总报告 + 用户验收说明**。

每个切片完成后展示证据并等待确认，不自动进入下一切片。

## 12. 尚未验证项

下列项目不是业务范围未决，而是必须在规格后通过 PoC 消除的技术不确定性：

- Python 3.13 Linux 基础镜像和完整 requirements 能否一次构建成功；
- k6 v2.1.0 容器镜像的本机拉取、认证、上传和轮询脚本；
- `ghcr.io/shopify/toxiproxy:2.12.0` 的本机 manifest 和运行；
- restic v0.19.1 容器或本机二进制在 Windows 路径上的权限、中文路径和恢复行为；
- Docker 容器到 LAN 模型、MinerU/Paddle 等解析服务的路由；
- Vite 和后端 watcher 在 Docker Desktop bind mount 下的稳定热更新；
- 旧路径兼容层对所有历史交付类型的覆盖；
- 另一台 LAN PC 的真实登录、任务和正式下载。

这些项目若失败，报告必须显示“失败”，不能用 skip 记为通过。

## 13. 明确留给 8B-2

以下内容继续标记为“待 8B-2 实机验证”：

- 最终服务器操作系统、NVIDIA 驱动、CUDA 和 Container Toolkit；
- 真实 GPU 模型吞吐和调度；
- 10–20 个真实用户的生产并发与 p95；
- RAID、异机/离线备份和真实灾难恢复；
- 长期运行、容量上限和生产 SLO；
- 真实域名、TLS、公网、路由器和最终防火墙；
- 最终全工程 Compose、离线镜像包和发布制品。

8B-1 完成不等于服务器部署完成，也不等于 Phase 4B 封板。

## 14. 官方资料

### Docker

- [Docker Desktop CLI](https://docs.docker.com/desktop/features/desktop-cli/)
- [Compose 启动顺序与健康条件](https://docs.docker.com/compose/how-tos/startup-order/)
- [Compose 项目名和隔离](https://docs.docker.com/compose/how-tos/project-name/)
- [Compose Watch](https://docs.docker.com/compose/how-tos/file-watch/)
- [Docker 多阶段构建](https://docs.docker.com/build/building/multi-stage/)
- [Docker Desktop 容器访问宿主](https://docs.docker.com/desktop/features/networking/networking-how-tos/)
- [Bind mounts](https://docs.docker.com/engine/storage/bind-mounts/)
- [Compose 服务、标签和 tmpfs](https://docs.docker.com/reference/compose-file/services/)
- [保护 Docker daemon socket](https://docs.docker.com/engine/security/protect-access/)

### 并发与前端验收

- [k6 scenarios](https://grafana.com/docs/k6/latest/using-k6/scenarios/)
- [k6 checks 和 thresholds](https://grafana.com/docs/k6/latest/using-k6/thresholds/)
- [k6 JSON 输出](https://grafana.com/docs/k6/latest/results-output/real-time/json/)
- [k6 自定义 summary](https://grafana.com/docs/k6/latest/results-output/end-of-test/custom-summary/)
- [Playwright reporters](https://playwright.dev/docs/test-reporters)
- [Locust 官方文档](https://docs.locust.io/en/stable/)

### 故障、备份与恢复

- [Shopify Toxiproxy](https://github.com/Shopify/toxiproxy)
- [Pumba](https://github.com/alexei-led/pumba)
- [SQLite Online Backup API](https://www.sqlite.org/backup.html)
- [restic 备份](https://restic.readthedocs.io/en/v0.19.1/040_backup.html)
- [restic 恢复](https://restic.readthedocs.io/en/v0.19.1/050_restore.html)
- [restic 检查](https://restic.readthedocs.io/en/v0.19.1/045_working_with_repos.html)

## 15. 阶段出口

技术调研阶段已经形成可进入规格编写的明确建议，没有新增业务范围、数据含义、权限或外部
发布决策。

进入规格阶段前只需要用户确认：

1. 采用本文推荐架构与工具组合；
2. 允许把“旧路径只读兼容 + 新记录相对路径”写入 8B-1a 规格；
3. 按 8B-1a 至 8B-1d 分切片实施，每个切片单独展示证据并等待确认。
