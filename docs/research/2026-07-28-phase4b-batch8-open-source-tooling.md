# Phase 4B 批次 8A 开源工具选型研究

> 日期：2026-07-28
>
> 范围：本机 PC 上的统一任务闭环、评测、可观测性与故障注入
>
> 资料口径：仅使用项目当前代码、官方文档、官方仓库和一手规范
> 决策状态：研究建议，尚未实施

## 1. 结论

批次 8A 不需要重建一套测试或观测平台。最小且效果最好的组合是：

1. **直接复用 `pytest + Hypothesis`**：负责契约、状态机不变量、权限、幂等、输出 QA 和确定性故障注入；
2. **直接复用现有 Playwright**：新增真实后端、真实模型、真实文件和真实下载的 PC 闭环；现有 Mock UI 测试继续保留；
3. **直接启用现有 OpenTelemetry 依赖**：仅给统一任务生命周期增加少量手工 span；
4. **新增 Phoenix，但只作为可选本地 Docker 观测后端**：不把 Phoenix Python 服务端安装进 Mangrove 主运行环境；
5. **Promptfoo 先做一个小型离线 PoC**：复用现有语义计划 Golden 和 Python 评分逻辑，证明异步任务与轨迹接入稳定后，才升级为正式门禁；
6. **Testcontainers 继续复用，但不扩张用途**：当前保留实库测试；只有需要真实网络故障时才用它启动 Toxiproxy；
7. **Toxiproxy 不作为批次 8A 的默认依赖**：普通模型截断、转换器崩溃、坏产物、重复投递和越权都应用确定性 test double；Toxiproxy 只补充真实 TCP 延迟、断连和复原测试。

最终推荐可概括为：

| 工具 | 决策 | 批次 8A 中的角色 |
|---|---|---|
| pytest | 直接复用，核心门禁 | 契约、集成、权限、幂等、故障与产物机械验收 |
| Hypothesis | 直接复用，定向扩展 | 任务状态机与格式协商属性测试 |
| Playwright | 直接复用，补真实闭环 | PC 用户路径、进度、取消、澄清、下载 |
| OpenTelemetry | 直接复用，增加最小埋点 | 统一任务阶段、耗时、错误与重试轨迹 |
| Phoenix | 新增可选 Docker 服务 | 本地 trace 查询和故障定位 |
| Promptfoo | 新增隔离 PoC，暂不替代 pytest | 自然语言计划矩阵及可选轨迹回归 |
| Testcontainers | 直接复用，保持可选 | 实库与可选容器化故障依赖 |
| Toxiproxy | 延后到确有网络故障门时再加 | TCP 延迟、timeout、reset、恢复 |

## 2. 当前仓库基础

### 2.1 已有依赖

`requirements.txt` 已声明：

- `pytest==9.1.1`、`pytest-asyncio==1.4.0`、`pytest-randomly==4.1.0`、`pytest-xdist==3.8.0`、`pytest-timeout==2.4.0`；
- `hypothesis==6.159.0`；
- `testcontainers[mysql,postgres]==4.13.3`；
- OpenTelemetry API、SDK、OTLP HTTP/gRPC exporter `1.39.1`；
- 多个 OpenTelemetry instrumentor 和 `traceloop-sdk==0.52.1`。

`frontend/package.json` 已声明：

- `@playwright/test`；
- `@axe-core/playwright`；
- `test:e2e` 执行 `playwright test`。

当前本机运行解释器 `E:\python3.13\python.exe` 的实际安装版本与声明存在漂移：

- OpenTelemetry API/SDK 为 `1.42.1`，不是 `requirements.txt` 的 `1.39.1`；
- `traceloop-sdk` 为 `0.61.0`，不是声明的 `0.52.1`；
- Promptfoo、Phoenix Python 包均未安装。

因此，任何批次 8A 工具接入前都应先做一次**依赖一致性门**：要么按锁定文件重装，要么经过回归后有意更新锁定版本；不能在两套版本事实之间做验收。

### 2.2 已有测试资产

仓库已经具备可复用基础：

- `tests/test_sql_guard_properties.py` 已使用 Hypothesis；
- `tests/test_db_live_containers.py` 已使用 Testcontainers 运行真实 MySQL/PostgreSQL；
- `scripts/run_phase4b_batch1_eval.py` 已能执行公开脱敏的语义计划 Golden；
- `tests/fixtures/semantic_harness/public/batch1/intents.json` 已包含计划、澄清和输出格式预期；
- DOCX、PDF、XLSX、CSV 等公开脱敏夹具已存在；
- `frontend/e2e/` 已有 3 个 Playwright 套件，当前以 API route mock 为主；
- Playwright 已配置 `trace: "retain-on-failure"`。

当前明显缺口：

- 未发现业务代码实际创建 OpenTelemetry span；
- 当前 Playwright 工作台测试大量拦截 API，不能证明真实后台任务、模型、交付和下载可用；
- 当前语义评测是 Python 脚本输出 JSON，缺少跨提示/模型矩阵、可视化和轨迹断言；
- 失败注入还没有形成统一的测试协议和固定验收命令。

## 3. 自然语言计划与轨迹回归：Promptfoo

### 3.1 能力与适配性

Promptfoo 提供声明式测试矩阵、确定性断言、自定义 Python/JavaScript 断言、模型评分、结果导出，以及基于 OpenTelemetry 的 trace/trajectory 断言。它可以：

- 对 STP JSON 做 schema、字段、枚举、包含关系和自定义 Python 检查；
- 比较不同模型、提示版本和案例；
- 检查错误 span、阶段数量、阶段耗时；
- 在 trace 规范后检查工具使用、参数和顺序；
- 导出 JSON、HTML、CSV、YAML 和 JUnit。

官方 Python provider 能直接包装现有 Python 代码，并支持异步函数；启用 tracing 后还能接收 W3C `traceparent`。这比另写一套 Node 业务适配器更适合当前仓库。

官方资料：

- [Promptfoo 简介](https://www.promptfoo.dev/docs/intro/)
- [Python 集成](https://www.promptfoo.dev/docs/integrations/python/)
- [Python provider](https://www.promptfoo.dev/docs/providers/python/)
- [Assertions and metrics](https://www.promptfoo.dev/docs/configuration/expected-outputs/)
- [Tracing and trajectory assertions](https://www.promptfoo.dev/docs/tracing/)
- [结果导出](https://www.promptfoo.dev/docs/configuration/outputs/)
- [缓存行为](https://www.promptfoo.dev/docs/configuration/caching/)
- [安装与 Node.js 要求](https://www.promptfoo.dev/docs/installation/)

### 3.2 最小接入方式

不要重写现有评测器。建议：

1. 继续以 `intents.json` 和现有 Python scorer 作为事实源；
2. 写一个很薄的 Python provider，调用真实 Compiler 或统一工作台 API；
3. Python assertion 复用确定性评分逻辑；
4. 首个 PoC 只覆盖：
   - 4 个固定成功场景；
   - 模型输出截断；
   - 复合来源拒绝；
5. 验收运行使用 `--no-cache --no-share`，确保真的调用当前模型且不上传结果；
6. 结果显式输出为仓库外运行目录中的 JSON/HTML，再把批准后的汇总证据纳入批次报告；
7. 轨迹断言只有在 OpenTelemetry 阶段 span 稳定后才启用。

推荐链路：

```text
Promptfoo
  └─ Python provider
      └─ Mangrove 统一任务闭环
          └─ OTel spans → Promptfoo 本次评测 receiver
                         └─ 可选转发 → 本地 Phoenix
```

Promptfoo 官方支持把收到的 trace 转发到外部 OTLP 后端，因此评测时不需要创建第二套业务埋点。

### 3.3 成本

估算前提：1 名熟悉当前代码的工程师；不含修复评测发现的业务 Bug。

| 项目 | 成本 |
|---|---:|
| 固定版本的 Promptfoo 工具目录与配置 | 0.5 天 |
| Python provider 与现有 scorer 适配 | 1–2 天 |
| 6 个 PoC 案例、JSON/HTML 证据与本地命令 | 1 天 |
| 轨迹断言接入 | 另需 1–2 天，依赖 OTel 埋点先稳定 |

总计：**不含轨迹约 2–3.5 天；包含稳定轨迹约 3–5.5 天**。

### 3.4 边界与风险

- Promptfoo 不是 pytest 的替代品；确定性 P0 契约仍由 Python 测试负责；
- LLM judge 有非确定性、时间和模型成本，只能作辅助指标；
- Mangrove 是 `202 + 后台执行 + SSE/轮询 + 下载`，不是一次普通聊天调用，必须有适配器；
- Promptfoo 默认缓存成功响应，正式模型回归必须使用 `--no-cache`；
- 部分结果导出会包含原始响应和配置，官方明确表示导出脱敏是 best effort；不得放真实用户文档内容或密钥；
- 批次 8A 不启用 red-team 远程生成、Cloud、share 或自动上传；
- 当前 Node.js `22.22.1` 满足 Promptfoo 当前最低要求，但仍应固定 Promptfoo 版本和 lockfile，不使用漂移的 `@latest` 作为验收基线。

### 3.5 决策

**建议引入，但先作为 6-case 隔离 PoC，不立即成为全仓强制门禁。**

PoC 的升级条件：

- 连续 3 次真实运行均能正确关联案例、阶段和结果；
- 失败不会被缓存伪装为通过；
- 轨迹中没有原文、密钥、本机绝对路径；
- 评测耗时和失败原因可稳定复现；
- 与现有 pytest 结果没有相互矛盾的判定。

## 4. 本地可观测性：OpenTelemetry + Phoenix

### 4.1 能力与适配性

OpenTelemetry Python 的 trace 与 metrics 已是稳定能力，OTLP exporter 可把同一套埋点发送到不同后端。Phoenix 是可自托管的 OTLP collector、SQL 存储和 Web UI，适合本地查看一次任务从理解到交付的完整轨迹。

官方资料：

- [OpenTelemetry Python](https://opentelemetry.io/docs/languages/python/)
- [OpenTelemetry Python exporters](https://opentelemetry.io/docs/languages/python/exporters/)
- [OTLP exporter 规范](https://opentelemetry.io/docs/specs/otel/protocol/exporter/)
- [OpenTelemetry trace semantic conventions](https://opentelemetry.io/docs/specs/semconv/general/trace/)
- [Phoenix 官方仓库](https://github.com/Arize-ai/phoenix)
- [Phoenix Docker 部署](https://arize.com/docs/phoenix/self-hosting/deployment-options/docker)
- [Phoenix 配置与端口](https://arize.com/docs/phoenix/self-hosting/configuration)
- [Phoenix 隐私说明](https://arize.com/docs/phoenix/self-hosting/security/privacy)
- [Phoenix 数据保留](https://arize.com/docs/phoenix/settings/data-retention)

### 4.2 最小埋点模型

只增加一个 root span 和七个业务阶段 child span：

```text
workspace.task
├─ compile
├─ inspect
├─ bind
├─ execute
├─ verify
├─ publish
└─ download
```

span 属性只记录低敏元数据：

- `task_id_hash`、`run_id_hash`、`revision`；
- `source_type`、`source_count`、`output_formats`；
- `provider`、`model`、`prompt_version`；
- `status`、`error_code`、`retry_count`；
- 输入/输出条数、字节数、耗时；
- QA、完整性和发布结果。

默认禁止进入 trace：

- 文档正文、完整用户 Prompt、完整模型回复；
- Cookie、Token、API Key、Authorization；
- 用户名、原始文件名和本机绝对路径；
- 未脱敏的工具参数、下载链接和证据文本。

### 4.3 Phoenix 部署方式

批次 8A 推荐：

- 使用**固定版本**的 Phoenix Docker 镜像；
- 作为可选 local/dev Compose profile；
- SQLite + 独立持久卷即可，不新增 PostgreSQL；
- 仅绑定本机访问；
- 设置 `PHOENIX_TELEMETRY_ENABLED=false`；
- 设置 `PHOENIX_ALLOW_EXTERNAL_RESOURCES=false`；
- 明确设置 retention，不能沿用默认无限保留；
- Phoenix 不可用时，业务任务必须继续运行，最多记录 exporter 警告。

不要把 `arize-phoenix` 服务端 Python 包安装到 Mangrove 主解释器。Phoenix 官方 Docker 已包含 collector、UI 和存储，主应用已有 OTLP exporter，继续往主环境塞服务端依赖没有收益。

运行时链路：

```text
Mangrove → 现有 OTel SDK/OTLP exporter → 本机 Phoenix
```

### 4.4 成本

| 项目 | 成本 |
|---|---:|
| OTel 初始化、环境开关和 fail-open | 0.5–1 天 |
| 七阶段 span、上下文传播和脱敏属性 | 1.5–2.5 天 |
| Phoenix 固定版本 Docker、SQLite 卷和隐私配置 | 0.5 天 |
| trace 集成测试与人工查看证据 | 0.5–1 天 |

总计：**约 3–5 天**。

### 4.5 边界

- 批次 8A 只做 traces，不同时扩展 metrics、logs、Phoenix datasets 和 evals；
- OpenTelemetry Python 的 logs 仍非稳定主线，本阶段不接；
- GenAI 语义约定仍在演进，Mangrove 自有属性必须加稳定命名空间和 schema version；
- 不同时启用多套全局 TracerProvider；现有 `traceloop-sdk` 不能和手工初始化重复注册；
- 自动 instrumentation 只能补 HTTP/LLM 调用，不能替代 `compile/inspect/bind/execute/verify/publish` 这些业务 span；
- 运行 trace 是诊断材料，不替代正式 QA、Manifest 和交付证据。

### 4.6 决策

**直接采用 OpenTelemetry；新增 Phoenix 作为可选本地后端。**

本阶段不新增 Phoenix Python SDK，也不引入 OpenTelemetry Collector；Phoenix 自身已经接收 OTLP，单机多一跳没有实际收益。到批次 8B 出现多服务路由、采样或多后端转发需求时，再评估独立 Collector。

## 5. PC 前端真实闭环：Playwright

### 5.1 当前基础与目标

Playwright 已经安装，Chrome channel、失败保留 trace、axe 可访问性和工作台 UI 测试均已存在。新增框架没有必要。

当前主要问题不是测试工具，而是测试层级：已有工作台 E2E 大量使用 `page.route()` 返回模拟数据，只能证明 UI 状态映射，不能证明真实上传、模型、worker、交付和下载。

官方资料：

- [Playwright fixtures](https://playwright.dev/docs/test-fixtures)
- [Web server](https://playwright.dev/docs/test-webserver)
- [Web-first assertions](https://playwright.dev/docs/test-assertions)
- [Trace modes](https://playwright.dev/docs/test-use-options)
- [Trace Viewer](https://playwright.dev/docs/trace-viewer)
- [CI 建议](https://playwright.dev/docs/ci)

### 5.2 最小扩展

保留当前快速 Mock 套件，再新增一组串行真实闭环：

1. 启动真实后端与真实前端；
2. 使用公开脱敏夹具；
3. 真实登录或创建隔离测试用户；
4. 通过页面上传文件、填写需求、提交任务；
5. 使用 web-first assertion 或 `expect.poll` 等待真实终态，禁止固定长 `sleep`；
6. 验证当前阶段、错误、取消、澄清和结果入口；
7. 捕获真实下载；
8. 把下载文件交给 Python 产物验收器重新打开并机械比对。

固定成功场景：

| 来源 | 任务 | 交付 |
|---|---|---|
| DOCX | 限定范围提取并汇总 | TXT |
| PDF | 证据绑定的条款提取或核查 | DOCX/PDF |
| XLSX | 筛选、保留指定列并排序 | XLSX |
| CSV | 去重或分组汇总 | CSV/JSON |

Playwright 负责证明“用户真的完成了操作并得到下载”，Python 验收器负责证明“下载内容真的正确”。两者不能互相替代。

### 5.3 成本

| 项目 | 成本 |
|---|---:|
| 真实前后端与测试用户 fixture | 1–1.5 天 |
| 四个真实成功场景 | 2–3 天 |
| 用户可见失败路径 | 1–2 天 |
| 下载附件、trace 和清理机制 | 0.5–1 天 |

总计：**约 4.5–7.5 天**。不含修复真实闭环发现的产品缺陷和本地模型推理时间。

### 5.4 边界

- 真实模型闭环应串行运行，不和快速 UI 测试并发争抢单个重 worker；
- CI 或本地门禁必须显式区分 Mock UI、真实后端、真实模型三层；
- “Docker/模型不可用而 skip”不能算正式验收通过；
- Playwright retry 不能用于掩盖稳定失败；
- trace、网络日志和附件可能包含用户数据，只允许公开脱敏夹具；
- 当前只验 PC Chrome；不扩展移动端和全浏览器矩阵。

### 5.5 决策

**继续使用现有 Playwright，不引入 Cypress、Selenium 或新的前端测试框架。**

## 6. Python 契约与属性测试：pytest + Hypothesis

### 6.1 适配性

pytest 是当前全仓主测试框架；Hypothesis 已用于 SQL guard。两者足够覆盖批次 8A 的生命周期和交付不变量。

官方资料：

- [pytest fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html)
- [pytest monkeypatch](https://docs.pytest.org/en/stable/how-to/monkeypatch.html)
- [pytest parametrization](https://docs.pytest.org/en/stable/how-to/parametrize.html)
- [Hypothesis 文档](https://hypothesis.readthedocs.io/en/latest/)
- [Hypothesis stateful testing](https://hypothesis.readthedocs.io/en/latest/stateful.html)

建议新增的属性：

1. 任意时刻最多一个业务阶段处于活动状态；
2. 已完成、失败、取消等终态不可回退到运行中；
3. 取消后永远不能 `delivery_published`；
4. 同一任务重复投递只能产生一个正式 Delivery；
5. 任何 owner 不匹配的读取、下载、修改和恢复都失败；
6. 自然语言与界面格式冲突必须在执行前暂停确认；
7. 复合来源在未支持时必须明确拒绝，不能只处理其中一个；
8. 同一事件序列重放得到相同任务摘要；
9. 坏产物永远不能通过 QA 或进入正式下载；
10. 失败重试达到上限后停止，不形成无限循环。

简单字段、格式和纯函数用普通 `@given`；任务的创建、补充、取消、执行、失败、恢复等动作序列用 `RuleBasedStateMachine`。

### 6.2 故障注入方式

大多数批次 8A 故障不需要容器：

| 故障 | 推荐做法 |
|---|---|
| 模型超时、截断、非法结构 | fake generator 或 HTTPX `MockTransport` |
| 转换器退出 | 注入受控 subprocess 返回码/异常 |
| 产物损坏 | 修改测试临时目录中的产物字节后执行真实 QA |
| 重复投递 | 并发或重复调用同一幂等入口 |
| 跨用户访问 | FastAPI/TestClient + 两个隔离 owner |
| 格式冲突 | 契约与 API 集成测试 |
| 用户取消 | worker 边界上的受控阻塞点 + cancel |

[HTTPX 官方 MockTransport](https://www.python-httpx.org/advanced/transports/) 已能提供确定性 HTTP 响应，不必为少量模型异常再引入 RESPX 或 `pytest-httpx`。

### 6.3 成本

| 项目 | 成本 |
|---|---:|
| 生命周期纯模型与 5–7 个核心 invariant | 1.5–2.5 天 |
| 格式、权限、幂等和坏产物参数化测试 | 1.5–2.5 天 |
| 固定失败样例和复现证据 | 0.5 天 |

总计：**约 3.5–5.5 天**。

### 6.4 边界

- 不用 Hypothesis 随机轰真实 LLM、浏览器或 LibreOffice；
- 属性测试针对纯状态 reducer、协议层或轻量 store；
- 重型转换和真实模型只跑少量固定集成案例；
- 记录并固定最小反例，但不能靠固定 seed 把其他随机案例永久屏蔽；
- `pytest-xdist` 不用于争抢同一个真实模型或重 worker 的场景；
- 新的测试分组要在当前 `--strict-markers` 下显式登记。

### 6.5 决策

**直接复用并扩展，是批次 8A 的第一优先级。**

## 7. Testcontainers 与 Toxiproxy

### 7.1 适用范围

Testcontainers 适合在测试期间启动和清理真实容器依赖，仓库已用于 MySQL/PostgreSQL。Toxiproxy 是 Shopify 的 TCP 代理，可动态注入：

- latency/jitter；
- timeout；
- connection reset；
- bandwidth/limit data；
- 整条代理 down；
- 移除故障后的恢复。

官方资料：

- [Testcontainers Python 官方仓库](https://github.com/testcontainers/testcontainers-python)
- [Testcontainers Python 入门](https://testcontainers.com/guides/getting-started-with-testcontainers-for-python/)
- [Shopify Toxiproxy 官方仓库及 HTTP API](https://github.com/Shopify/toxiproxy)

### 7.2 是否现在引入

批次 8A 已约定的异常中，只有“真实网络层超时/断连/恢复”需要 Toxiproxy。以下问题不适用：

- 本地转换进程崩溃；
- OOM 或磁盘不足；
- 输出文件损坏；
- worker 重复投递；
- 权限越权；
- 模型输出被 `max_tokens` 截断。

因此建议分两层：

1. 默认门：pytest + fake/MockTransport，稳定、快速、可重复；
2. 可选 `fault_network` 门：Testcontainers `GenericContainer` 启动固定版本 Toxiproxy，覆盖真实延迟、reset 和恢复。

不建议引入官方 README 链接的 `toxiproxy-python` 客户端。其仓库仍自称 “Work in Progress”，示例大量未完成。当前项目已有 `httpx`，直接调用 Toxiproxy 官方 HTTP API 的薄 fixture 更可靠，也避免新增低成熟度依赖。

### 7.3 成本

| 项目 | 成本 |
|---|---:|
| 复用现有 Testcontainers | 0 天新增依赖 |
| Toxiproxy GenericContainer + HTTP fixture | 1 天 |
| 延迟、reset、恢复 3 个测试 | 1–1.5 天 |
| Windows Docker Desktop/LAN 模型路由验证 | 0.5–1 天 |

总计：**约 2.5–3.5 天**。

### 7.4 边界

- Docker 不可用时可在日常开发套件中明确 skip，但正式故障验收必须把 skip 视为未完成；
- Toxiproxy 只能操作经过代理的 TCP 连接；
- 不把生产服务默认改为走 Toxiproxy；
- 必须固定镜像版本、动态分配端口并可靠清理 toxic；
- 局域网模型通过 Windows 主机访问 Docker 容器的路由需要先做 PoC；
- 本阶段如果确定性 timeout stub 已覆盖业务状态契约，可将 Toxiproxy整体延后到批次 8B。

### 7.5 决策

**复用 Testcontainers；Toxiproxy 保留为可选网络故障增强，不作为启动批次 8A 的前置条件。**

## 8. 验收场景与工具映射

| 验收场景 | pytest | Hypothesis | Playwright | OTel/Phoenix | Promptfoo | Toxiproxy |
|---|---:|---:|---:|---:|---:|---:|
| DOCX → 限定提取 → TXT | 产物机械比对 | — | 真实用户闭环 | 阶段/耗时 | 计划 PoC | — |
| PDF → 条款核查 → DOCX/PDF | 证据与产物 QA | — | 真实用户闭环 | 阶段/耗时 | 计划 PoC | — |
| XLSX → 筛选排序 → XLSX | 行列/顺序比对 | 可测算子性质 | 真实用户闭环 | 阶段/耗时 | 计划 PoC | — |
| CSV → 去重/汇总 → CSV/JSON | 精确记录比对 | 可测幂等性质 | 真实用户闭环 | 阶段/耗时 | 计划 PoC | — |
| 信息不足后补充继续 | API/状态契约 | 状态序列 | 用户路径 | pause/resume | 可选 | — |
| 用户取消 | worker/发布契约 | “取消后不发布” | 用户路径 | cancel span | 可选 | — |
| 复合来源拒绝 | 编译/API 契约 | “不丢源” | 用户提示 | error code | 计划 PoC | — |
| 输出格式冲突 | 协商契约 | 格式性质 | 暂停确认 | pause reason | 计划 PoC | — |
| 模型超时/截断 | fake/MockTransport | 重试上限 | 真实错误显示 | error/retry | 计划 PoC | 可选真实网络 |
| 转换器崩溃/坏产物 | fault stub + 真实 QA | “坏产物不发布” | 真实错误显示 | verify/publish | — | 不适用 |
| 重复投递 | 并发幂等测试 | 状态序列 | 结果只出现一次 | duplicate count | 可选 | 不适用 |
| 跨用户访问 | 两 owner API 测试 | 权限性质 | 访问被拒 | 仅错误码，禁内容 | — | 不适用 |

## 9. 推荐实施顺序

### 第 0 步：依赖事实一致

- 对齐 `requirements.txt` 与 `E:\python3.13` 实际包版本；
- 固定 Node、Promptfoo、Phoenix 镜像版本；
- 输出依赖清单和可重复安装命令。

退出条件：验收环境中的实际版本与记录一致。

### 第 1 步：pytest/Hypothesis 生命周期门

- 固化状态机、权限、格式协商、取消、幂等和发布不变量；
- 建立确定性故障 stub；
- 先让框架在没有真实模型时可完整回归。

退出条件：核心生命周期 invariant 全部通过，反例可复现。

### 第 2 步：OpenTelemetry + Phoenix

- 增加七阶段最小 span；
- 完成跨后台 worker 的上下文传播；
- 证明无敏感正文且 exporter fail-open；
- 本地 Phoenix 能按一次任务看到完整链路。

退出条件：成功、澄清、取消和失败任务均能定位实际停止阶段。

### 第 3 步：Playwright 真实闭环

- 保留 Mock UI 门；
- 新增四类真实成功任务和四类用户失败路径；
- 下载产物交给 Python 验收器重开比对。

退出条件：四个真实文件从上传到正式下载全部完成，Mock 不替代真实结果。

### 第 4 步：故障注入

- 先完成模型 timeout/截断、转换崩溃、坏产物、重复投递、越权；
- 只有需要验证真实 TCP 行为时，再增加 Toxiproxy 三案例。

退出条件：所有异常安全停止，不发布假结果，状态和错误可操作。

### 第 5 步：Promptfoo PoC

- 复用现有 Golden；
- 先跑 6 个案例和确定性断言；
- 稳定后再启用轨迹断言；
- 达到升级条件后才进入正式门禁。

退出条件：PoC 连续三次稳定、无敏感数据、与 pytest 判定一致。

## 10. 明确不推荐

批次 8A 不推荐：

- 用 Promptfoo 替代 pytest 或现有 Python scorer；
- 用 LLM judge 作为唯一 P0 判定；
- 在主 Python 环境安装 Phoenix 服务端；
- 为单机 Phoenix 先引入 PostgreSQL、Kubernetes 或独立 OTel Collector；
- 同时启用多个全局 TracerProvider 或重复自动 instrumentation；
- 把完整用户 Prompt、文档正文、模型回复或下载链接写入 trace；
- 用 Playwright route mock 冒充真实闭环；
- 用 Toxiproxy 模拟进程崩溃、坏产物、重复投递或权限问题；
- 引入仍标记 Work in Progress 的 `toxiproxy-python`；
- 为 HTTP 模型异常额外引入 RESPX/`pytest-httpx`，现有 HTTPX `MockTransport` 已足够；
- 因工具引入而重构批次 1–7 的业务架构。

## 11. 最终建议

当前最重要的不是增加更多框架，而是建立一条可重复证明的证据链：

```text
pytest/Hypothesis 固化系统不变量
        ↓
OTel/Phoenix 证明实际运行到哪一步
        ↓
Playwright 证明用户真的完成闭环
        ↓
Python 验收器证明交付内容真的正确
        ↓
Promptfoo 补充自然语言计划和轨迹回归
```

按此方案，批次 8A 只新增两个外部组件：

- **Phoenix 固定版本 Docker 镜像**；
- **Promptfoo 固定版本的隔离评测工具目录**。

其余能力全部从仓库现有依赖和夹具中复用。Toxiproxy是否引入，由确定性故障测试完成后剩余的真实网络风险决定。
