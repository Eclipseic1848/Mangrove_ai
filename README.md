<h1 align="center">Mangrove（红树林）</h1>

<p align="center"><strong>用自然语言完成数据采集、整理、分析与交付。</strong></p>

<p align="center">
  <a href="https://github.com/Eclipseic1848/Mangrove_ai/releases/tag/v1.0.0"><img alt="v1.0.0" src="https://img.shields.io/badge/release-v1.0.0-2563EB"></a>
  <img alt="Python 3.13" src="https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white">
  <img alt="React and Vite" src="https://img.shields.io/badge/React%20%2B%20Vite-646CFF?logo=vite&logoColor=white">
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/license-MIT-22C55E"></a>
</p>

<p align="center">
  <a href="#功能概览">功能概览</a> ·
  <a href="#linux-服务器部署">Linux 部署</a> ·
  <a href="#本地快速开始windows">本地启动</a> ·
  <a href="#开发与文档">开发文档</a>
</p>

Mangrove 将数据任务集中在一个工作台中。描述目标、选择资料或网址后，平台获取来源、
处理数据并生成初稿；你可以查看结果和已发现的问题，选择接受初稿或继续核对，再下载交付文件。
任务保留来源、版本和执行记录，方便追溯与后续复用。

## 功能概览

| 功能 | 说明 |
| --- | --- |
| 数据工作台 | 自然语言创建任务，管理来源、版本、多轮追问、取消、历史记录与回收站 |
| 文件处理 | 读取 PDF、Word、Excel、CSV 等文件，提取、整理、分析并生成结果 |
| 网页采集 | 获取公开网页和搜索结果；部分渠道需要额外部署采集组件或配置登录态 |
| 初稿与交付 | 展示检查结果，由用户决定接受或继续核对；保留交付文件与来源证据 |
| 结果预览 | 支持表格、文档等格式，Word/PPT 可按原版式翻页、缩放和阅读 |
| 模型连接 | 管理个人与平台模型连接，支持云端及内网模型 |
| 自动化任务 | 创建执行计划、绑定模型、查看运行记录与结果 |
| 记忆与复用 | 保存个人偏好、任务模板和处理经验，供后续任务参考 |
| 运营审计 | 查看历史 Token 用量、筛选与导出；使用趋势支持悬停数据和小时至年度粒度 |
| 账号与权限 | 普通用户、管理员、超级管理员分级管理，业务数据按所有者隔离 |

初稿中的检查问题会保留在结果记录中。接受初稿表示用户采纳结果；系统核验状态单独显示。
正式交付仍需通过文件完整性与质量检查。

企业 API、对象存储、远程 MCP 等接入尚未完整开放；平台能力治理当前仅供管理员灰度使用。
各能力的验证范围和已知限制见[当前状态](docs/status/current.md)。

## Linux 服务器部署

从 [v1.0.0 Release](https://github.com/Eclipseic1848/Mangrove_ai/releases/tag/v1.0.0)
下载 Linux 部署包，按发行页说明取得 `mangrove-v1.0.0-linux-amd64.zip`。
校验并解压后阅读 `00-先读我.md`，再按[完整部署手册](docs/deployment/linux-migration-it-guide.md)操作。

支持单台 Ubuntu/Debian **x86_64 / linux/amd64** 服务器。主服务由 systemd 管理，
Nginx 提供 HTTPS，Docker 运行任务及文档预览容器。部署包包含固定提交的 Git 源码、
前端构建、部署手册、校验清单和以下镜像：

| 镜像 | 用途 |
| --- | --- |
| `mangrove/pi-coding-agent:0.87.1-v1.0.0` | Pi 0.87.1 任务运行时与能力宿主 |
| `mangrove/smokescreen:da4840c9` | 任务出站代理 |
| `mangrove/office-preview:local` | Office 文档预览 |

Python 和系统依赖需要联网安装。Firecrawl、SearXNG、MediaCrawler 等可选服务需单独准备；
核心包不含 GPU 模型服务。账号、业务数据、Cookie 和密钥通过受限渠道单独迁移。

> [!WARNING]
> 旧开发版 `main`（`ec4cf05`）与本版存在同号不同内容的数据库迁移，**不能直接覆盖升级**。
> 应先设计并演练数据转换，不得改写迁移账本或覆盖原库。迁移其他已有实例前，同样需要核对
> 迁移摘要并备份数据和配套密钥。绑定旧 Pi 版本的未完成任务不保证原地续跑。

GitHub 自动生成的 Source code 压缩包不含 `.git`，不能替代专用部署包中的 Git 仓库包。
正式核验依赖提交身份；自行构建时请通过 `git clone` 检出 `v1.0.0`。

## 本地快速开始（Windows）

准备 Python 3.13、Node.js、Git 和 Docker Desktop（Linux 容器模式），进入克隆后的项目目录。
Linux 安装与配置请使用上面的部署手册。

### 1. 准备环境

```powershell
Copy-Item .env.example .env
py -3.13 -X utf8 -m pip install `
  -r requirements.txt `
  -r requirements-collectors.txt

Set-Location frontend
npm ci
npm run build
Set-Location ..
```

按需在 `.env` 中配置模型与采集账号。真实凭据、Cookie、数据库、日志和任务制品不得提交。
运行 Pi 任务前须安装 Docker，并从发行包导入核心镜像：`docker image load -i runtime-images.tar`。
镜像位置和逐项核对步骤见部署手册第 7.4 节；Windows 使用 Docker Desktop 的 Linux 容器模式。
`JWT_SECRET` 必须显式配置为至少 32 字节的随机值；缺失、过短或使用默认/示例值时网关拒绝启动。
已有实例不要自动更换密钥；若数据库连接凭据依赖 JWT 密钥派生，须先规划兼容与受控轮换。

### 2. 显式初始化数据库

Mangrove 自有 Schema 只通过中央迁移命令变更。首次启动和升级既有数据库前，都先查看状态并
使用唯一备份名显式迁移：

```powershell
New-Item -ItemType Directory -Force data/backups | Out-Null
$migrationStamp = Get-Date -Format yyyyMMdd-HHmmss

py -3.13 -X utf8 -m src.database_migrations status `
  --profile webui --database data/webui.db
py -3.13 -X utf8 -m src.database_migrations apply `
  --profile webui --database data/webui.db `
  --backup "data/backups/webui-before-$migrationStamp.db"

py -3.13 -X utf8 -m src.database_migrations status `
  --profile scheduler --database data/scheduler.db
py -3.13 -X utf8 -m src.database_migrations apply `
  --profile scheduler --database data/scheduler.db `
  --backup "data/backups/scheduler-before-$migrationStamp.db"
```

`apply` 会生成备份、SHA 和 Receipt；服务启动只验证 Schema，不会静默迁移。不要覆盖既有备份。
已有业务实例的迁移与恢复请按部署手册安排维护窗口，并先验证备份可恢复。

### 3. 启动统一入口

新实例在数据库迁移完成后，由维护者在交互终端显式初始化管理员：

```powershell
py -3.13 -X utf8 -m src.api.bootstrap_admin --database data/webui.db --username maintainer
```

命令隐藏输入并确认至少 12 位密码，不接收命令行密码，不迁移 Schema；并发初始化只能成功一次。
已有超级管理员时拒绝重建，不修改已有账号。已初始化实例直接跳过此步骤。
公开注册默认关闭；管理员开启后，自助注册也只创建待审批普通用户。

随后启动网关，使用初始化的管理员账号登录：

```powershell
py -3.13 -X utf8 scripts/dev_reload.py
```

打开 <http://localhost:8088>，按 `Ctrl+C` 停止服务。启动失败时先检查
`logs/dev_reload.log`。

平台登录使用 HttpOnly Cookie：访问权限最长 30 分钟，可轮换续期，一次登录绝对期限为 7 天。
“我的设置 → 登录与密码”可修改密码或退出所有设备；退出不取消后台任务。非环回访问须使用
HTTPS；浏览器需支持 Web Locks，以协调同一浏览器多个标签页的刷新。刷新结果未知时重新登录。
旧 Bearer 凭证不再接受；升级到 `webui_0011` 后需重新登录，已有实例迁移仍须独立维护窗口和备份。
脚本请求须保留 Cookie，并为写请求提供同源 `Origin` 与 `X-Mangrove-CSRF: 1`；登录响应不返回凭证。

> [!TIP]
> `http://localhost:5173` 只用于前端热更新，不是统一产品入口。需要前端开发服务时，在第二个
> 终端进入 `frontend/` 后运行 `npm run dev -- --host 0.0.0.0`。

### 4. 按需准备采集组件

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts/setup_external_dependencies.ps1
```

该脚本会获取固定上游提交并应用仓库内审核过的补丁。未准备可选组件时，相应网页或社媒采集
能力不可用，但不影响本地文件处理主链。

## 开发与文档

后端使用 FastAPI、SQLite，前端使用 React、TypeScript 和 Vite。统一产品入口为 `8088`，
主工作台路径为 `/data-prep`；`5173` 用于前端开发。

| 文档 | 内容 |
| --- | --- |
| [部署手册](docs/deployment/linux-migration-it-guide.md) | Linux 安装、迁移、HTTPS、验收、备份与回退 |
| [更新记录](CHANGELOG.md) | 版本功能、修复与升级注意事项 |
| [当前状态](docs/status/current.md) | 能力验证结果、限制与待办 |
| [贡献指南](CONTRIBUTING.md) | 开发环境、测试、CI 和 Pull Request |
| [领域与架构](CONTEXT.md) · [架构决策](docs/adr/README.md) | 任务、来源、验证和交付模型 |
| [安全策略](SECURITY.md) | 安全报告、依赖告警与部署安全 |

## 可选第三方组件

| 组件 | 用途 | 许可证 |
| --- | --- | --- |
| [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) | 社媒采集 | 仅限其许可证允许的非商业学习与研究 |
| [Firecrawl](https://github.com/firecrawl/firecrawl) | 网页采集 | AGPL-3.0 |

固定版本、补丁和完整许可说明见 [external/README.md](external/README.md) 与
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。部署前请核对所需组件的使用授权。

## 数据与安全

- 个人业务数据按所有者隔离；管理员读取业务正文须说明原因并记录审计。
- 登录使用 HttpOnly Cookie；非环回地址访问必须使用 HTTPS。
- 配置凭据通过 SecretRef 引用，由 Vault 加密保存。
- 数据库结构通过显式迁移命令更新，启动时只验证版本。
- `.env`、数据库、用户文件、Cookie、密钥和运行日志不得提交到公共仓库。

发现漏洞请按[安全策略](SECURITY.md)私密报告。

## 参与和许可

欢迎通过 [Issues](https://github.com/Eclipseic1848/Mangrove_ai/issues) 反馈问题，
或按[贡献指南](CONTRIBUTING.md)提交改进。参与者须遵守[行为准则](CODE_OF_CONDUCT.md)。
Mangrove 自有代码采用 [MIT License](LICENSE)，第三方组件遵循各自许可证。
