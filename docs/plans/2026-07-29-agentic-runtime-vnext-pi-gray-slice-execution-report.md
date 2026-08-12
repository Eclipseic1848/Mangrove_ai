# Agentic Runtime vNext：Pi 全能力灰度首个纵切面执行报告

> 日期：2026-07-29
>
> 分支：`feature/agentic-runtime-vnext`
>
> 实现提交：`7bb388b7`
>
> 结论：PG-02 至 PG-04 的首个“真实任务 → Pi → 未验证候选”纵切面已实现并取得工程
> 验证证据；尚未取得正式交付资格，PG-05 未完成

## 1. 本轮交付

### 1.1 Runtime 与持久化

- 新增独立 `src/agentic_runtime/` 深模块，业务路由不直接处理 Pi JSONL RPC、Docker
  生命周期或候选文件发现；
- 新增 Runtime、权限、事件、来源、候选和结果契约；
- 在现有 Web UI SQLite 中建立独立 Run/Event 表，按
  `user_id + task_id + revision` 隔离；
- 持久化精简行动事件和候选身份，不持久化本地模型 API Key；
- 来源复制后使用流式 SHA-256 复核，输入目录只读挂载，工作区、输出、会话和配置目录
  分离；
- Docker 超时、任务取消或进程未正常稳定时会强制终止任务容器。

### 1.2 完整 Pi 任务环境

- 固定 `@earendil-works/pi-coding-agent@0.80.10`；
- 镜像：
  `mangrove/pi-coding-agent:0.80.10`；
- 本机镜像 ID：
  `sha256:50a74b0a663e6dd711e0a7964b343f586db2507d412cb7ed664c72a57f73b2b2`；
- 镜像大小：392,207,026 字节；
- 已验证 Pi 0.80.10、Node 22.23.0、npm 10.9.8、Python 3.13.14、
  Git 2.39.5、Bash 5.2.15 和 curl 7.88.1；
- Pi 以 JSONL RPC 运行，完整开放 `read/write/edit/bash`；没有挂载 Docker Socket、
  宿主 `.env`、业务数据库或其他任务目录；
- `start_all.bat` 在镜像缺失时自动构建，`stop_all.bat` 会终止并移除仍在运行的
  `mangrove-pi-*` 临时任务容器。

镜像构建没有从零重做 Agent。它复用本机已有且摘要固定的 Debian 12 Firecrawl Runtime
作为 Node/Git/curl/bash 底座，并从摘要固定的 Python 官方镜像复制 Python 运行时。这样
在当前慢网络和 Docker Desktop 环境中避免重复拉取大型基础层，同时保持 Pi 版本固定。

### 1.3 工作台灰度入口

- 现有创建、任务详情、列表、SSE、取消、版本和下载 API 保持兼容；
- Legacy 继续默认；只有管理员可以在“更多”中显式选择“Pi 增强灰度”；
- Pi 首期只允许本地模型和标准增强权限档位；
- Pi 可以在一个任务中观察文档与表格混合来源，不再受 Legacy 的 `TaskFamily` 互斥限制；
- 前端只展示读取、处理、生成和候选就绪等精简行动摘要，不展示隐藏思维链或完整命令参数；
- `candidate_ready` 是独立终态，位于“待确认”而不是“已完成”；
- 候选下载带 `X-Mangrove-Artifact-Status: unverified-candidate`，页面持续显示
  “未验证候选 / 不是正式交付”；
- 当前路径不会创建正式 Delivery，也不会把候选状态冒充 `completed`。

## 2. 验证证据

### 2.1 后端与契约

命令：

```powershell
$env:PYTHONIOENCODING='utf-8'
E:\python3.13\python.exe -X utf8 -m pytest `
  tests/test_agentic_runtime.py `
  tests/test_pi_runtime_workspace_api.py `
  tests/test_semantic_workspace_api.py `
  -q --basetemp=.pytest-tmp/pi-gray-regression
```

结果：`20 passed`。覆盖：

- Run/Event 持久化和跨用户拒绝；
- 候选格式、重开、空/损坏文件和目录逃逸门；
- 输入只读挂载、无 Docker Socket、完整 Pi 工具参数；
- Docker Desktop 宿主回环地址转换；
- 管理员灰度限制、本地模型、复合来源；
- 真实模型 Key 不落运行台账；
- 候选所有权下载、状态响应头和零正式 Delivery。

Python 编译检查与 `git diff --check` 通过。

### 2.2 前端

- `npm.cmd run build`：通过，TypeScript 和 Vite 生产构建成功；
- `npm.cmd run test:e2e`：`39 passed`；
- 新增覆盖管理员选择 Pi、自动限定本地模型、创建请求以及候选与正式交付视觉区分；
- 原上传即时预览、取消、版本、回收站、结果/来源、进度时序和深浅主题用例继续通过。

Vite 仍报告既有大 Chunk 警告，不是本轮新增失败。

### 2.3 真实本地模型与工具循环

命令：

```powershell
$env:PYTHONIOENCODING='utf-8'
E:\python3.13\python.exe -X utf8 scripts/verify_pi_runtime_smoke.py
```

结果：通过。Pi 在真实任务容器内连接当前本地 Qwen，依次读取目标和真实 CSV、运行处理
命令、写入候选、再次读取并校验。最终：

- 只生成 1 个 CSV；
- 只保留姓名为“示例人员乙”的 2 条原始记录；
- 没有混入其他人员；
- 候选可按 UTF-8 CSV 重新打开。

早先一次临时 PowerShell 管道验证曾把中文目标和文件名转成问号，导致语义结果错误；该次
不计为通过。改用 UTF-8 脚本后连续两次真实运行均通过，本报告只引用最终脚本化证据。

## 3. 已验证事实、推断和未验证项

### 已验证事实

- 完整 Pi 可以在任务级 Docker 中使用本地 Qwen 完成真实 CSV 工具循环；
- 工作台能持久化 Pi Run、显示精简事件、隔离候选并按所有者下载；
- 候选不会进入现有正式 Delivery；
- Legacy 默认入口、现有旧任务和服务器部署状态没有改变。

### 基于代码的推断

- 深 `PiRuntime` Seam 已把高权限行为集中到单一模块，后续补恢复、转向和策略门时不需要
  把 Pi 协议散落到业务路由；
- 文档与表格混合来源不再被 Legacy 的独占路由提前拒绝，但具体任务正确性仍取决于后续
  解析依赖、语义验证和真实语料。

### 尚未验证或尚未实现

- 用户原始“附件 2 服务费用标准及明细 → 单一 CSV”尚未运行；
- 真实 Word 商务条款、真实 Excel、多 Sheet、扫描 PDF 和未知任务尚未完成 3/3；
- 当前只有候选文件完整性门，没有独立业务语义 Verifier 和正式 Delivery Publisher；
- `resume / steer / inspect`、进程重启恢复、重复请求幂等、上下文截断恢复尚未完成；
- 扩展访问和宿主机开发权限档位尚未开放；
- 当前镜像没有预装 ripgrep、Poppler、LibreOffice 和完整 Office/PDF 转换依赖，Pi 可在
  容器内按需安装，但固定镜像依赖集仍需根据真实 PDF/Word/Excel 赛马收口；
- 标准增强模式具备公共网络；目前通过管理员显式选择和规则告知约束用途，尚未建立能在
  Shell 层区分“下载公共依赖”和“外发业务正文”的强制 Egress PolicyGate；
- 长任务的正式恢复、30 项泛化集、提示注入、跨用户压力和服务器实机验收均未完成。

以上未验证项不得用本轮 `20 passed / 39 passed / CSV smoke passed` 替代。

## 4. 下一步与人工控制点

下一工作包是 PG-05 前半段：

1. 用用户原始 PDF、Word、Excel 做工具与依赖 A/B，固定首批解析依赖；
2. 建立独立语义 Verifier，只有目标、包含项、排除项、格式和文件数量全部通过才允许进入
   正式 Delivery；
3. 增加取消、重启恢复、幂等和上下文截断门；
4. 再执行核心任务本地 Qwen 连续 3/3 和未调优泛化集。

以下决策继续由用户控制：

- 是否授予额外目录、凭证、宿主机或更广网络权限；
- 是否把业务正文发送到任何外部服务；
- 是否允许候选升级为正式 Delivery；
- 是否切换默认入口、进入 Conductor 迁移、Phase 4C/5、版本/标签或服务器部署。

## 5. 后续状态

上述“尚未验证”是首片实施时的历史快照。后续已在 `v0.0.7` 形成真实
PDF/Word/Excel 候选独立验证、Pi 官方会话恢复、HTTP 幂等、跨用户隔离、提示注入、
运行中真实取消和业务 Egress 主链证据。仍未完成独立依赖获取状态机、30 项泛化集、
正式 Delivery、默认入口切换和服务器验收。
