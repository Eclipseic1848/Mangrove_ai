# Mangrove v1.0.0 Linux 部署与迁移手册

> 面向对象：第一次接触 Mangrove 的公司 IT 人员。
> 更新日期：2026-09-27。适用：v1.0.0；Ubuntu/Debian；单台 x86_64 服务器；迁移现有账号、配置、历史任务与文件；内网、公网使用 IP 访问。
> 重要：本手册是操作说明，不含真实密码、模型密钥、客户文件或可直接投入使用的业务数据包。

## 阅读顺序

第一次部署，请按第 1—15 章执行，不要只复制最后的启动命令。

- 第 1—3 章：弄清需要什么、谁负责、交付什么。
- 第 4 章：由开发人员准备完整版本和迁移包。
- 第 5—8 章：由 IT 安装服务器环境和依赖，暂不启动业务。
- 第 9—11 章：在约定维护窗口迁移数据、检查配置并启动。
- 第 12—13 章：配置容器内部网络、公网 HTTPS 和公司内网访问。
- 第 14—15 章：逐项验收、正式切换。
- 第 16—18 章：日常运维、备份、升级与回退。
- 第 19—21 章：问题排查、验收记录、代码及官方资料依据。

每一步都包含“在哪里做、做什么、怎样判断成功”。遇到写明“停止”的条件，应保留错误信息联系开发，不要通过删除数据库、重置账号、关闭权限检查来继续。

---

## 1. 交付内容

交付物分为版本化源码、前端构建、业务数据、密钥和 Docker 镜像。开发人员按清单整理交付目录，IT 校验后导入服务器。

### 1.1 文件分类

把本次应上线的源码、前端、配置模板、数据库迁移、依赖清单、测试和必要第三方补丁，整理成一个确定的 Git 提交。这个提交能重新取出完全相同的源文件。

这不等于把运行目录里的所有文件都提交进 Git：

| 内容 | 交付方式 | 原因 |
|---|---|---|
| `src/`、`frontend/` 源码、`config/`、`skills/`、必要 `scripts/`、迁移与依赖锁文件 | 代码包 | 决定平台功能 |
| Git 提交信息和该提交对应的 Git 对象 | 只含当前提交的浅层 Git 仓库包，导入后 clone | 保留原提交号和完整源码，不带已删除的历史业务样本；普通“源码 ZIP”不能替代 |
| 编译好的 `frontend/dist/` | 前端构建包 | IT 可直接使用，不需要在服务器安装 Node.js |
| 账号、任务、Token 用量、模板、记忆、上传和产物 | 一致性数据包 | 通常未被 Git 跟踪，提交代码并不会带走这些数据 |
| 原 JWT、数据库凭据加密密钥、Vault key、模型凭据等 | 单独的受限密钥包 | 不能进入公共仓库或普通共享群 |
| Pi 0.87.1、出站代理、Office 预览 | Docker 镜像包 | Pi 版本为 0.87.1；按清单校验镜像内容身份 |
| Windows `.venv`、`node_modules`、`__pycache__` | 不交付为 Linux 运行环境 | Windows 二进制不能直接搬到 Linux 使用 |
| 临时截图、测试输出、开发缓存、无关日志 | 通常不交付 | 由开发检查是否有业务证据引用，不能按目录名一概删除 |
| Windows `start_all.bat` / `stop_all.bat` | 留在原机 | Linux 使用 systemd，不能运行这些本机编排脚本 |

**目录可以通过 SFTP 传输，但传的是开发整理好的“交付目录”，不是未经筛选的整个工作目录。**

### 1.2 版本与升级兼容性

从 [v1.0.0 Release](https://github.com/Eclipseic1848/Mangrove_ai/releases/tag/v1.0.0) 按发行页说明下载 `mangrove-v1.0.0-linux-amd64.zip`。源码提交号和文件摘要以同包的 `RELEASE-MANIFEST.md`、`SHA256SUMS` 为准；不要混用不同批次的清单与文件。

旧开发版 `main`（`ec4cf05`）与本版存在同号、不同内容的迁移文件，其数据库不能直接升级至本版，须另行设计并演练转换。其他已有实例迁移前也须核对迁移内容摘要。不要改迁移历史、hash 或版本号绕过检查。

现成 `docker/phase4b/compose.acceptance.yaml` 是隔离验收配置，包含验收用密钥与端口设置，也没有完整接好当前任务容器宿主环境。**此文件不能直接用于生产部署。**

---

## 2. 这套系统如何运行

可以把服务器理解为一台一直开机的工作电脑：

```text
公司内网 / 公网浏览器
          │ HTTPS，443 端口
          ▼
        Nginx
          │ 本机 HTTP
          ▼
 Mangrove 主程序：127.0.0.1:8088
          │
          ├── 账号、任务、审计：SQLite 数据库
          ├── 上传、历史文件、生成结果：服务器磁盘
          └── Docker：Pi 执行、出站代理、能力宿主、Office 预览
                         │
                         └── 内部 Nginx 入口 → 模型 / 文档授权接口
```

- **Nginx**：负责 HTTPS 和转发网页请求。不能把它的网页根目录指向整个工程目录。
- **Python 主程序**：提供网页、登录、任务管理、调度等功能；按单进程部署。
- **Docker**：隔离运行任务与文件转换，配合宿主机上的 Python 主程序工作。
- **systemd**：Linux 的服务管理器，负责开机启动、停止和故障后重启。
- **SQLite**：数据库主要是磁盘上的文件，但运行时不能随意复制一个 `.db` 就认为备份完整。
- **模型服务**：可使用已有云端或内网模型。本手册不包含在本机部署 GPU 大模型。

公司内网访问与公网访问使用同一套账号和数据，不要另开一套互不相通的平台。

### 2.1 推荐服务器起点

小规模试运行可按 **8 核 CPU、32 GB 内存、200 GB 以上 SSD** 申请，再按实际数据和并发调整。这是部署规划起点，不是已验证的容量承诺。当前单个 Pi 容器默认上限是 4 CPU / 8 GB 内存，还需给主程序、预览和采集服务留资源。

磁盘至少覆盖：代码和依赖、Docker 镜像、现有数据、未来增长，以及导入临时副本和备份。不要只按压缩包大小申请磁盘。文件很多时，解压后的占用会明显增加。

前期限制并发，先验证一条完整任务链。不同用户同时执行大量任务的容量，需要上线前实测。

### 2.2 分工

| 人员 | 必须完成 |
|---|---|
| 项目负责人 | 确认部署范围、使用者、停用窗口、外网开放和上线验收 |
| 开发人员 | 整理完整代码提交、打包、数据与密钥清单、镜像身份、Linux 配置、历史兼容和技术排障 |
| 公司 IT | 服务器、网络、安装、证书、备份设施、按手册操作与记录 |
| 业务验收人员 | 确认原账号、历史结果及代表业务任务实际可用 |

IT 不需要理解 Agent 内部实现，也不应自行编辑冻结任务、迁移账本或权限数据。

---

## 3. 开始前填写这张交接表

下表中“待确认”的项目不要猜。软件准备可以先做，涉及的后续步骤须等该项明确。

| 项目 | 本次填写 |
|---|---|
| Linux 发行版和版本 | 待 IT 提供；主线命令面向 Ubuntu 24.04 / Debian 12、13 |
| CPU 架构 | 待确认；本文要求 `x86_64 / amd64` |
| CPU / 内存 / 可用磁盘 | 待确认 |
| SSH 地址 / 端口 / IT 用户名 | 由 IT 私下提供；不把密码写进文档 |
| 公司内网 IP | 待确认 |
| 公网 IP 是否固定 | 由网络管理员填写地址与固定性 |
| 公网 TCP 80 / 443 是否能到达服务器 | 待网络管理员确认 |
| 内网能否访问同一个公网 IP | 待确认 |
| 公司是否有内部证书服务 | 仅当必须使用内网 IP 时确认 |
| sudo 权限、是否已有其他业务 | 待确认；已有业务时不得套用安装和端口修改命令 |
| 迁移方式 | 完整迁移已有账号、配置、历史任务和文件 |
| 停用窗口 | 尚未确认；正式停止原平台前约定 |
| 正式部署 Git 分支 / 40 位提交 SHA | 按 `RELEASE-MANIFEST.md` 填写 |
| 数据备份时间 / 文件数 / 总字节数 | 由开发填 |
| 原数据库版本与迁移内容核对结果 | 本版要求 WebUI `webui_0023`、Scheduler `scheduler_0004`；导出前检查实际版本与摘要 |
| Pi 镜像标签及 Image ID | 服务器仅 `0.87.1`；清点旧冻结引用并确定未结束任务的处置，不要求安装旧版 |
| 模型访问方式、出站网络与预算负责人 | 由项目负责人/开发填写，不在本表放 Key |
| 必须启用的采集服务 | 逐项填写 MediaCrawler、Firecrawl、SearXNG、RSSHub 等实际使用项 |
| 商业使用与第三方组件授权 | 由公司负责人核实，不由 IT 推定 |
| 故障联系人 / 回退决策人 | 填姓名及公司内部联系方式 |

### 3.1 最少需要收到的交付物

开发准备一个目录，建议如下。示例名字用于统一操作，不代表文件已经制作完成：

```text
mangrove-handover/
├── Mangrove-Linux部署与迁移手册.md
├── RELEASE-MANIFEST.md            # 版本、数据、目录映射、组件与已知限制
├── SHA256SUMS                    # 所有交付文件的 SHA-256
├── app-git.tar.gz                # 顶层为 app.git/，保留当前提交的 Git 对象
├── frontend-dist.tar.gz          # 顶层为 dist/
├── runtime-data.tar             # 顶层为 data/、downloads/、memory/ 等已列明目录
├── secrets.tar                  # 单独受限传输；不得公开
├── runtime-images.tar           # Docker save 导出的镜像
└── extras/                      # 按启用功能提供
    ├── auxiliary-services/      # 已审查、镜像固定、端口受限的配套服务配置
    ├── mediacrawler-source/     # 经授权的精确源码/补丁及 Linux 依赖清单
    └── linux-tools/             # 与 Linux 锁文件匹配的工具和哈希
```

`secrets.tar` 的内部格式约定：

```text
server.env                       # 开发为 Linux 整理的配置；保留原加密身份
vault/
└── webui.db.model-connections.key
signing/                         # 如实际使用平台签名，放配套公私钥
```

若实际 WebUI 数据库文件名不是 `webui.db`，Vault key 文件名也随之变化。以开发清单为准，不要改名后“试试看”。

`RELEASE-MANIFEST.md` 至少写清：提交 SHA、代码分支、前端构建对应 SHA、Python 版本、数据根目录、额外引用文件、密钥位置、镜像 ID、外部组件版本/补丁、服务端口、数据库状态、历史缺失项、备份时间与恢复验证结果。

**交付清单不齐、代码版本不确定、密钥缺失、镜像身份不一致时，IT 停在接收环节，让开发补齐。**

---

## 4. 开发人员：怎样制作交付包

本章由开发执行。收到完整交付包的 IT 人员可跳到第 5 章。实际交付内容以 `RELEASE-MANIFEST.md` 为准；最终业务数据需在停写窗口单独导出。

### 4.1 固定一个完整代码版本

1. 审查当前跟踪和未跟踪文件；将属于本版本的源码、测试、配置模板和迁移文件一并纳入。
2. 扫描凭据、Cookie、客户正文、本机绝对路径；确认这些不进入 Git。
3. 使用明确文件清单提交，不使用 `git add .`。
4. 完成与当前变更相关的测试和前端构建检查，记录精确提交 SHA。
5. 按项目发布流程审批并发布该版本。

完整提交不是稳定生产验收，仍需在 Linux 验证安装、恢复、网络和业务链路。

### 4.2 导出代码和前端

**位置：Windows 开发机，PowerShell；在项目根目录执行。**

先选择一个新的、受限的导出目录。示例会创建在工程的同级目录，避免包递归包含自己：

```powershell
$repoRoot = (Get-Location).Path
$exportName = 'mangrove-handover-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$exportRoot = Join-Path (Split-Path -Parent $repoRoot) $exportName
New-Item -ItemType Directory -Path $exportRoot -ErrorAction Stop | Out-Null

git status --short
git rev-parse HEAD
git branch --show-current
```

确认显示的代码状态符合交付清单。若仍有应上线的改动未提交，先处理，不继续导出。

```powershell
$deployBranch = (git branch --show-current).Trim()
if (-not $deployBranch) { throw '当前不是命名分支，请由开发确认导出引用。' }

$deployCommit = (git rev-parse HEAD).Trim()
$sourceUri = ([System.Uri]::new($repoRoot + [IO.Path]::DirectorySeparatorChar)).AbsoluteUri
$bareRoot = Join-Path $exportRoot 'app.git'
git clone --bare --depth 1 --no-local --single-branch --branch $deployBranch $sourceUri $bareRoot
if ($LASTEXITCODE -ne 0) { throw 'Git 当前提交导出失败。' }
git --git-dir=$bareRoot remote remove origin
if ($LASTEXITCODE -ne 0) { throw '本机来源路径清理失败。' }
git --git-dir=$bareRoot fsck --full
if ($LASTEXITCODE -ne 0) { throw 'Git 对象校验失败。' }
if ((git --git-dir=$bareRoot rev-list --all --count).Trim() -ne '1') { throw '导出包不应包含旧历史。' }
tar -czf (Join-Path $exportRoot 'app-git.tar.gz') -C $exportRoot app.git
if ($LASTEXITCODE -ne 0) { throw 'Git 仓库包压缩失败。' }

$buildRoot = Join-Path $exportRoot 'build-checkout'
git -c core.autocrlf=false clone --no-local --branch $deployBranch $bareRoot $buildRoot
if ($LASTEXITCODE -ne 0) { throw '独立源码检出失败。' }
if ((git -C $buildRoot rev-parse HEAD).Trim() -ne $deployCommit) { throw '构建版本不符。' }
Push-Location (Join-Path $buildRoot 'frontend')
npm ci
if ($LASTEXITCODE -ne 0) { Pop-Location; throw '前端依赖安装失败。' }
npm run build
if ($LASTEXITCODE -ne 0) { Pop-Location; throw '前端构建失败。' }
Pop-Location

tar -czf (Join-Path $exportRoot 'frontend-dist.tar.gz') -C (Join-Path $buildRoot 'frontend') dist
if ($LASTEXITCODE -ne 0) { throw '前端构建包导出失败。' }
```

此方法不复制原仓库的本机配置，保留真实提交号与全部当前源码，只截断历史可达范围，不改写原仓库历史。移除临时 clone 的 origin 后，交付包不含开发机来源地址。不要改为导出完整历史的 bundle，也不要用缺少 Git 对象的 `git archive` 或 GitHub “Download ZIP”替代。参考：[Git clone 的 depth 和 bare 选项](https://git-scm.com/docs/git-clone)。

从压缩包在另一个新目录解包，再 clone，核对提交、迁移摘要、源码清洁状态与规则身份。交付目录只传清单中的包，不传临时 `app.git` 和 `build-checkout` 目录。不能仅以“压缩成功”判断。

### 4.3 清点业务数据与秘密

必须核对“实际生效配置”，不是只看默认路径或 `.env.example`：

| 范围 | 默认/典型位置 | 需要保留的内容 |
|---|---|---|
| WebUI 数据 | `data/webui.db` | 用户、任务、模型连接、审计、Token 记录等 |
| 自动任务 | `data/scheduler.db` | 计划与历史；迁移期间避免两台机器重复触发 |
| 检查点 | `data/checkpoints.sqlite`，以及实际启用的其他库 | 已保存的执行上下文 |
| 其他业务库/本地来源库 | 如 `data/app.db`、`data/db_sources/`，以实际存在为准 | 不能只备份前两个库 |
| 上传 | `data/uploads/` | 原始附件、来源材料 |
| 执行资料 | `data/semantic-executions/` | 来源快照、运行记录、候选、引用文件 |
| 正式下载与旧交付 | `downloads/` 及实际配置的产物根 | 已交付文件 |
| 模板与经验 | `data/templates/`、`data/lessons/` | 包括用户学习数据 |
| 用户记忆 | `memory/` 及数据库中的相关记录 | 长期偏好、记忆 |
| 能力与治理 | `data/capabilities/`、`data/capability-governance/` | OCI 内容、证据、状态绑定 |
| 授权凭据 | 数据库密文 + 对应 Vault key + 原配置中的派生密钥 | 只复制数据库会导致无法解密 |
| 平台签名材料 | 可能在工程外 | 私钥、公钥、相关解密凭据；单独受控传输 |
| 外部服务持久数据 | Docker volumes / 外部组件目录 | 逐项清点；普通项目目录并不包含 Docker volume |
| 额外被引用的原件或证据 | 可能在上述目录之外 | 开发列出并安排对应落点 |

不要重新生成 `JWT_SECRET` 或 `DATA_PREP_DB_SECRET_KEY` 来代替旧值。某些历史数据库凭据可能由原 JWT 派生；替换后会解密失败。

`data/webui.db.model-connections.key` 是当前共享 Vault 的关键文件。原值可能是单密钥或 keyring，两者都按原文件保存。不要打开后手抄，也不要在工单、聊天或日志中输出。

默认的数据目录名称不能证明覆盖全部实际运行数据。开发应列出实际目录映射和外部卷清单，核对数据库引用是否都能在目标机解析。已有受管路径编码支持一定的跨宿主迁移，但不能保证所有历史外部绝对路径都自动适配；不要批量 SQL 替换 Windows 盘符。

能力目录的冻结完整性摘要还包含文件权限 mode。开发须登记原 OCI 制品、目录权限和摘要，解包后逐项核对；不要对能力缓存全量 chmod 或重算摘要来消除报警。Windows 与 Linux 的权限表示不一致时，由开发在保留原数据和资格记录的前提下受控重新物化，不让 IT 自行删缓存绕过检查。

### 4.4 取得一致性备份：安排维护窗口

推荐顺序：

1. 提前装好 Linux 环境、代码、依赖和镜像，但不启动迁移数据的第二个业务实例。
2. 通知用户停止提交新任务，等待运行中任务正常结束；同时清点“等待用户输入/接受初稿”等所有未结束会话，未知状态由开发核实，不自动重发。现有 Pi 会话冻结了 Windows 工作目录，迁到 Linux 后不能保证原 Run 续跑；开发须先列出受影响会话并与用户确定处置。历史数据保留不等于旧会话原地恢复，不批量改路径或绕过 Owner 校验。
3. 记录并暂停自动任务、Cookie 巡检、知识库巡检及其他可能自动执行的工作。必须核对数据库保存的运行时开关。
4. 经负责人确认后，停止原平台和相关写入程序。使用原机经过项目身份检查的停止方法，不按端口批量杀其他程序。
5. 对原文件和数据库取得同一停写时点的副本；保存备份时间。
6. 完成包与哈希后，原平台保持停用，直到切换完成或明确回退。

**SQLite 不能热拷贝单个主文件。** 活跃写入可能还在 WAL 中。停写后，也建议用 SQLite 的 backup API 生成独立数据库快照，再放入数据暂存目录。数据库快照与附件应来自同一停写窗口。

单个 SQLite 快照的示例。先把代码保存成临时的 `snapshot_sqlite.py`，然后对清单中的每个 SQLite 库分别运行：

```python
# 只读取来源库，生成一个不存在的新备份；不修改原库。
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

source = Path(sys.argv[1]).resolve(strict=True)
target = Path(sys.argv[2]).resolve()
if source == target or target.exists():
    raise SystemExit("目标必须是新的备份文件，不能覆盖原库或既有备份")
target.parent.mkdir(parents=True, exist_ok=True)
with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
    with closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as check:
    result = [row[0] for row in check.execute("PRAGMA integrity_check")]
if result != ["ok"]:
    raise SystemExit("备份完整性检查失败，保留文件供排查")
print("SQLite backup: OK")
```

不要把 SQLite backup API 用在 DuckDB 或其他数据库格式上。不要把来源库的 `-wal` / `-shm` 混入已经生成的独立快照旁。保留原文件用于恢复，不能把本机原库替换为备份。

数据包暂存目录应由开发准备成：

```text
runtime-payload/
├── data/
│   ├── webui.db                 # 一致性快照
│   ├── scheduler.db
│   ├── checkpoints.sqlite      # 若实际存在
│   ├── uploads/ ...
│   └── ...                     # 清单中的其他完整数据
├── downloads/
├── memory/
└── ...                         # 清单指定的其他相对目录
```

密钥材料从数据暂存目录分离到 `secrets-payload/`，只在副本上整理；不删除原机任何 key。如数据文件本身就是敏感文件，数据包仍按敏感资料管理，不能因为密钥另存就公开分享。

**位置：Windows 开发机，PowerShell；`runtime-payload` 和 `secrets-payload` 已由开发清点并准备。**

```powershell
tar -cf (Join-Path $exportRoot 'runtime-data.tar') -C runtime-payload .
if ($LASTEXITCODE -ne 0) { throw '数据包导出失败。' }
tar -cf (Join-Path $exportRoot 'secrets.tar') -C secrets-payload .
if ($LASTEXITCODE -ne 0) { throw '密钥包导出失败。' }
```

打包后在独立目录解包，检查数据库完整性、行数基线、文件数量/哈希、引用文件可读性及密钥可解密性。检查只能输出结论或计数，不能输出真实密钥。

### 4.5 导出 Docker 镜像：Pi 只部署 0.87.1

本版标签 `0.87.1-v1.0.0` 中，0.87.1 是 Pi 版本，v1.0.0 表示 Mangrove 镜像构建批次。该镜像更新了 PDF 依赖，不覆盖原机的旧镜像身份。

当前主要镜像：

- `mangrove/pi-coding-agent:0.87.1-v1.0.0`：服务器唯一的 Pi 运行版本；
- `mangrove/smokescreen:da4840c9`：任务出站代理；
- `mangrove/office-preview:local`：Office 隔离预览；

**位置：Windows 开发机，PowerShell。以下三项存在且身份已核对后执行：**

```powershell
docker info --format '{{.OSType}}/{{.Architecture}}'
docker image inspect --format '{{.Id}}' mangrove/pi-coding-agent:0.87.1-v1.0.0
docker image inspect --format '{{.Id}}' mangrove/smokescreen:da4840c9
docker image inspect --format '{{.Id}}' mangrove/office-preview:local

docker image save --output (Join-Path $exportRoot 'runtime-images.tar') `
  mangrove/pi-coding-agent:0.87.1-v1.0.0 `
  mangrove/smokescreen:da4840c9 `
  mangrove/office-preview:local
if ($LASTEXITCODE -ne 0) { throw '镜像导出失败。' }
```

本版仅分发 Pi 0.87.1，不包含 Pi 0.80.10。历史记录与已发布文件继续按数据清单迁移；绑定旧版本的未结束任务不保证原地恢复，不把旧镜像标签改指向新镜像来伪造兼容。其他可选采集服务按第 19 章单独确认启用范围。

记录每个镜像的 `Image ID`。目标 Linux 必须是匹配架构；x86_64 镜像不能直接按原身份迁到 ARM。

Docker 镜像不包含持久卷数据。Firecrawl 的 PostgreSQL 等若仍有需保留的数据，须另外安排数据库级备份或停写卷快照。迁移时不能让原机和目标机同时消费同一批未完成任务。

### 4.6 生成校验清单与传输

在已经整理好的导出目录生成哈希。只散列准备交付的文件，不扫描整个源工作区：

```powershell
$sumLines = Get-ChildItem -LiteralPath $exportRoot -File -Recurse |
  Where-Object { $_.Name -ne 'SHA256SUMS' } |
  ForEach-Object {
    $relative = $_.FullName.Substring($exportRoot.Length + 1).Replace('\', '/')
    $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $relative"
  }
[IO.File]::WriteAllLines(
  (Join-Path $exportRoot 'SHA256SUMS'),
  [string[]]$sumLines,
  [Text.UTF8Encoding]::new($false)
)
```

通过公司批准的 SFTP / 文件传输渠道交付；密钥包限定人员可见。SHA256SUMS 也应通过可确认来源的渠道交付，避免“文件和校验表一起被替换”。不把密钥包上传 GitHub、公开网盘或群聊。

---

## 5. IT：登录服务器并做只读检查

### 5.1 如何连接

由网络管理员提供服务器地址、SSH 端口和具有 sudo 权限的个人运维账号。Windows 可在终端执行：

```powershell
ssh -p 22 ituser@服务器地址
```

把 `ituser`、`服务器地址`、`22` 改成 IT 分配的值。首次连接时通过公司渠道核对 SSH 主机指纹；不要对不明指纹一路输入 yes。输入密码时终端不显示字符属于正常现象。

进入 Linux 后，看到的是服务器的终端。后续标注“Linux”的命令在这里执行，不在 Windows PowerShell 中执行。

如果使用文件传输软件：协议选 SFTP，主机/端口/账号与 SSH 一致，上传到指定接收目录。不要把业务包放到 Nginx 网页目录。

### 5.2 复制以下检查命令

**位置：Linux，普通 IT 账号。只读。**

```bash
cat /etc/os-release
uname -m
getconf _NPROCESSORS_ONLN
free -h
df -h /
df -i /
timedatectl
sudo -v
sudo ss -lntp
command -v docker || true
command -v python3.13 || true
```

判断：

- `uname -m` 应为 `x86_64`。ARM 或其他架构先停止，交开发准备对应镜像和工具。
- 记录 Ubuntu/Debian 的具体版本。其他发行版或过旧版本不照抄本手册。
- 磁盘与 inode 不能接近满载；确认 Docker 和业务目录所在磁盘的实际容量。
- 系统时间应正确并有时间同步。不要为了显示时间随意更改公司的时区策略。
- 检查 80、443、8088、3002、8080 是否已有服务。不能杀掉不认识的占用进程。
- 如果已有 Docker、Nginx 或其他业务，先让 IT 核实升级、端口和配置影响，本手册默认独立新服务器。
- 本次不需要复制 Windows 的 Python、Node 或浏览器安装目录。

### 5.3 网络人员需要确认什么

- 公网 IP 是否长期固定；如果会变，IP 证书与用户地址也需相应调整。
- 公网 TCP 80 能否转发到服务器 TCP 80：用于本手册的证书验证与续期。
- 公网 TCP 443 能否转发到服务器 TCP 443：用于用户 HTTPS 访问。
- 公司内网能否访问相同的公网 IP：涉及路由、NAT 回流或公司网络策略。
- 出站是否允许访问 Docker/依赖下载站、证书服务，以及批准使用的模型与采集来源。
- 公网不能直接访问 8088、数据库、Docker API、Redis、采集服务管理口。
- SSH 只向运维来源开放；不要为了部署关闭全部防火墙。
- Docker 网络地址不能与公司网段冲突；若冲突，先由网络管理员设计地址池，不让应用脚本擅改全机网络。

公网 80/443 尚不明确时，可以准备第 6—11 章，通过 SSH 隧道验收。第 13 章公网证书与开放步骤暂缓，不能把浏览器证书报错当成正常上线方式。

---

## 6. IT：安装系统环境

以下主线使用新的 Ubuntu 24.04 或 Debian 12/13 x86_64 服务器。安装会修改服务器，执行前按公司变更流程确认。

### 6.1 基础软件和系统库

**位置：Linux，IT 账号。**

```bash
sudo apt-get update
sudo apt-get install -y \
  ca-certificates curl git tar xz-utils unzip rsync nano \
  nginx sqlite3 openssl build-essential pkg-config \
  libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
  libffi-dev liblzma-dev libncurses-dev libgdbm-dev uuid-dev \
  ffmpeg libgl1 libgomp1 libmagic1 poppler-utils
```

图形基础库的包名按发行版选择，只执行对应一行：

```bash
# Ubuntu 24.04 或 Debian 13
sudo apt-get install -y libglib2.0-0t64
```

```bash
# Debian 12
sudo apt-get install -y libglib2.0-0
```

如果 apt 提示锁被占用，先确认是否有系统更新在运行。不要删除 apt 锁文件或强杀未知进程。下载失败先检查公司代理/镜像源，不关闭 TLS 校验。

### 6.2 安装 Docker Engine 和 Compose

先确认没有现有 Docker 工作负载，也没有冲突的旧安装。已有安装时不要照抄卸载命令，让 IT 先检查。本文不提供一键卸载已有容器环境的命令。

下面的代码根据 `/etc/os-release` 只接受 Ubuntu / Debian，为官方 apt 源安装。公司使用批准的内部镜像时，由 IT 替换源，仍保持版本与来源可追溯。

```bash
(
  set -eu
  . /etc/os-release
  case "$ID" in
    ubuntu|debian) ;;
    *) echo "本步骤只支持 Ubuntu/Debian"; exit 1 ;;
  esac
  test "$(dpkg --print-architecture)" = "amd64"
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL "https://download.docker.com/linux/$ID/gpg" \
    -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  printf 'deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' \
    "$ID" "$VERSION_CODENAME" |
    sudo tee /etc/apt/sources.list.d/mangrove-docker.list >/dev/null
  sudo apt-get update
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin
)
sudo systemctl enable --now docker
sudo docker version
sudo docker compose version
sudo docker run --rm hello-world
```

成功标准：Docker 客户端/服务端正常、Compose 显示版本、hello-world 正常退出。记录实际安装版本。正式运行后不做无人值守的 Docker 大版本升级。

上述操作基于 Docker 官方 [Ubuntu](https://docs.docker.com/engine/install/ubuntu/) / [Debian](https://docs.docker.com/engine/install/debian/) 安装方法。Docker 发布的容器端口可能绕过普通 UFW 规则；应限制端口绑定并从外部实测，不能仅看 UFW 状态。

### 6.3 Python 3.13

先看是否已有公司批准的 Python 3.13：

```bash
python3.13 --version
```

若不存在，使用以下独立安装方式，不替换系统的 `/usr/bin/python3`。示例固定 Python 3.13.15，来自官方发布页；公司若统一提供其他 3.13 补丁版本，由开发核对并记录，不能无记录换成 3.14。

**位置：Linux，IT 账号；本节不在应用虚拟环境中执行。**

```bash
mkdir -p "$HOME/mangrove-build"
cd "$HOME/mangrove-build"
curl -fLO https://www.python.org/ftp/python/3.13.15/Python-3.13.15.tar.xz
printf '%s  %s\n' \
  '1e66a7945a48390ee4c2a4268a0e4185884059a13c4aab6d148aa208deea4a76' \
  'Python-3.13.15.tar.xz' | sha256sum -c -
```

只有校验显示 `OK` 才继续。第一次构建使用一个新的构建目录；已有同名解压目录先检查来源，不覆盖。

```bash
tar -xJf Python-3.13.15.tar.xz
cd Python-3.13.15
./configure --prefix=/opt/python3.13 --with-ensurepip=install
make -j2
sudo make altinstall
/opt/python3.13/bin/python3.13 --version
/opt/python3.13/bin/python3.13 -c \
  "import ssl, sqlite3, bz2, lzma, ctypes; print('Python modules: OK')"
```

`make -j2` 控制编译并行，减少挤占小机器资源。构建失败保留日志，不跳过所缺模块。成功后本手册使用 `/opt/python3.13/bin/python3.13`；若采用已有 Python，由开发/IT 统一替换解释器路径。

参考：[Python 3.13.15 发布和校验值](https://www.python.org/downloads/release/python-31315/)、[Python 的 Unix 安装说明](https://docs.python.org/3.13/using/unix.html)。

### 6.4 创建服务账号与目录

**位置：Linux，IT 账号。新服务器只执行一次。**

```bash
sudo useradd --system --create-home --home-dir /var/lib/mangrove \
  --shell /usr/sbin/nologin mangrove
sudo usermod -aG docker mangrove
sudo install -d -o mangrove -g mangrove -m 0750 /opt/mangrove
sudo install -d -o mangrove -g mangrove -m 0700 /opt/mangrove/incoming
sudo install -d -o mangrove -g mangrove -m 0700 /opt/mangrove/backups
sudo install -d -o mangrove -g mangrove -m 0700 /opt/mangrove/staging
sudo install -d -o root -g mangrove -m 0750 /etc/mangrove
sudo -u mangrove -H docker version
```

若账号或目录已存在，先核对归属，不盲目重建或递归更改已有业务目录的权限。

Docker 访问权限属于宿主机高权限能力；`mangrove` 只用于受信任的平台服务，不提供给普通用户交互登录。不要用 `chmod 666 /var/run/docker.sock` 解决权限错误，也不要公开 Docker TCP 管理端口。依据：[Docker 安全说明](https://docs.docker.com/engine/security/)。

---

## 7. IT：接收并还原代码、前端和镜像

本章可以在正式停用窗口前完成。暂不还原旧数据并启动第二个业务实例。

### 7.1 传输与校验

先在普通 IT 账号下建立私有接收目录：

```bash
mkdir -p "$HOME/mangrove-receive"
chmod 0700 "$HOME/mangrove-receive"
printf '%s\n' "$HOME/mangrove-receive"
```

通过 SFTP 将本次交付目录的文件上传到刚显示的路径。每个包都按敏感资料管理，不上传到 `/var/www`，不与上一批文件混放。上传结束后，在同一个 IT 终端复制文件并设定权限：

```bash
sudo cp -an -- "$HOME/mangrove-receive/." /opt/mangrove/incoming/
sudo chown -R mangrove:mangrove /opt/mangrove/incoming
sudo find /opt/mangrove/incoming -type d -exec chmod 0700 {} +
sudo find /opt/mangrove/incoming -type f -exec chmod 0600 {} +
```

这里的 `-n` 不覆盖同名文件；如是更新批次，应使用新的私有接收目录，并由开发确认旧包去向后再操作，不直接覆盖已有数据包。

**第 7—19 章的 Linux 运维命令统一在 root 运维终端执行：输入下面命令，确认 `id -u` 输出 `0` 后继续。应用本身仍通过 `sudo -u mangrove` 和 systemd 的 `User=mangrove` 运行。** 这样可以进入受限目录，且不用放宽数据或密钥权限。每次重连服务器都需重新进入该终端；完成操作输入 `exit` 退出，不在里面运行来源不明的命令。

```bash
sudo -i
id -u
```

文件放好后，在上述运维终端校验：

```bash
cd /opt/mangrove/incoming
sudo -u mangrove sha256sum -c SHA256SUMS
```

每一项都必须是 `OK`。缺文件或校验失败先重传，不解压、不启动。新旧不同批次的数据包、密钥包和清单不能混用。

### 7.2 从 Git 仓库包还原代码

从 `RELEASE-MANIFEST.md` 填入实际分支与 SHA：

```bash
export MANGROVE_DEPLOY_BRANCH='由开发填写的分支名'
export MANGROVE_DEPLOY_COMMIT='由开发填写的40位提交SHA'
```

**只在 `/opt/mangrove/app` 尚不存在时执行：**

```bash
sudo -u mangrove tar -xzf /opt/mangrove/incoming/app-git.tar.gz \
  -C /opt/mangrove/incoming --no-same-owner
sudo -u mangrove git -c core.autocrlf=false clone --no-local --branch "$MANGROVE_DEPLOY_BRANCH" \
  /opt/mangrove/incoming/app.git /opt/mangrove/app
sudo -u mangrove git -C /opt/mangrove/app rev-parse HEAD
sudo -u mangrove git -C /opt/mangrove/app status --short
```

核对 HEAD 与清单完全一致；源码应没有意外改动。如果 app 目录已存在，不删除，不在其中反复 clone；先确认是本次未完成安装还是已有业务。

禁止从 GitHub 直接 clone 默认 `main` 替代交付包，也不要编辑 `.git` 或删除 Git 目录。

### 7.3 还原前端构建

包中顶层必须是 `dist/`：

```bash
sudo -u mangrove tar -tzf /opt/mangrove/incoming/frontend-dist.tar.gz | head -n 15
sudo -u mangrove tar -xzf /opt/mangrove/incoming/frontend-dist.tar.gz \
  -C /opt/mangrove/app/frontend --no-same-owner
sudo -u mangrove test -f /opt/mangrove/app/frontend/dist/index.html
```

成功标准：最后一条退出码为 0，文件存在。不要修改构建中的 JS 来硬编码公网 IP；前后端通过同一个访问入口通信。

服务器无需安装 Node.js。开发已用源码对应的 lockfile 构建前端；如果交付缺少前端包，优先补交，不用猜版本重新拼装。

### 7.4 导入镜像并核对身份

```bash
sudo -u mangrove docker image load -i /opt/mangrove/incoming/runtime-images.tar
sudo -u mangrove docker image inspect --format '{{.Id}}' mangrove/pi-coding-agent:0.87.1-v1.0.0
sudo -u mangrove docker image inspect --format '{{.Id}}' mangrove/smokescreen:da4840c9
sudo -u mangrove docker image inspect --format '{{.Id}}' mangrove/office-preview:local
```

逐项与清单对照，不只对照标签。三项分别是 Pi 0.87.1、任务出站代理、Office 预览，并非三个 Pi 版本。镜像缺失或内容身份不同，联系开发补交。

---

## 8. IT：安装 Mangrove 的 Python 依赖

**位置：Linux；此时已有代码，尚未启动应用。**

```bash
sudo -u mangrove -H /opt/python3.13/bin/python3.13 \
  -m venv /opt/mangrove/venv

sudo -u mangrove -H /opt/mangrove/venv/bin/python -m pip install \
  -r /opt/mangrove/app/requirements.txt \
  -r /opt/mangrove/app/requirements-collectors.txt

sudo -u mangrove -H /opt/mangrove/venv/bin/python -m pip check
```

成功标准：`pip check` 报告无依赖冲突。失败时保留错误交开发，不随意删除版本约束、全局安装或使用 `--no-deps` 绕过去。

只安装运行依赖和采集依赖；不要把开发、评测、GPU 依赖全部安装进服务环境。当前 Windows 共享 Python 环境不是可复制的 Linux 运行环境。

### 8.1 浏览器组件

使用固定的服务浏览器缓存路径，避免“root 安装了，服务账号却找不到”：

```bash
sudo /opt/mangrove/venv/bin/python -m playwright install-deps chromium
sudo install -d -o mangrove -g mangrove -m 0750 /var/lib/mangrove/browsers

sudo -u mangrove -H env PLAYWRIGHT_BROWSERS_PATH=/var/lib/mangrove/browsers \
  /opt/mangrove/venv/bin/python -m playwright install chromium

sudo -u mangrove -H env PLAYWRIGHT_BROWSERS_PATH=/var/lib/mangrove/browsers \
  /opt/mangrove/venv/bin/python -m patchright install chromium
```

Playwright 与 Patchright 对应不同采集实现，不要假设安装其中一个就覆盖全部浏览器。

浏览器安装目录和后续 systemd 中的 `PLAYWRIGHT_BROWSERS_PATH` 必须一致。不要复制 Windows 的浏览器缓存。

### 8.2 确认正式核验规则可以解析

**位置：Linux；在源代码根目录执行。不会调用模型。**

```bash
cd /opt/mangrove/app
sudo -u mangrove /opt/mangrove/venv/bin/python -c \
  "from src.candidate_verification.ruleset import CurrentVerifierRulesetResolver; r=CurrentVerifierRulesetResolver('.').resolve_target(); print(r.verifier_ruleset_hash)"
```

应输出规则 hash。如果提示缺 Git、未提交语义变化、依赖不匹配，停止并联系开发重新准备正确代码/依赖。不注释检查，也不在服务器临时提交文件来伪造身份。

---

## 9. IT 与开发：导入最终数据和密钥

**执行条件：维护窗口已经批准，原平台已停止写入，收到同一批最终数据包及校验表。**

第 7 章的代码、前端、镜像可以提前传。第 9 章使用停写后最终导出的数据；不能拿几天前的演练副本当正式数据。

### 9.1 解包到新的暂存目录

**位置：Linux。**

```bash
export MANGROVE_IMPORT_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
export MANGROVE_DATA_STAGE="/opt/mangrove/staging/data-$MANGROVE_IMPORT_STAMP"
export MANGROVE_SECRET_STAGE="/opt/mangrove/staging/secrets-$MANGROVE_IMPORT_STAMP"

sudo install -d -o mangrove -g mangrove -m 0700 "$MANGROVE_DATA_STAGE"
sudo install -d -o root -g root -m 0700 "$MANGROVE_SECRET_STAGE"

sudo -u mangrove tar -tf /opt/mangrove/incoming/runtime-data.tar | head -n 25
sudo -u mangrove tar -xf /opt/mangrove/incoming/runtime-data.tar \
  -C "$MANGROVE_DATA_STAGE" --no-same-owner

sudo tar -xf /opt/mangrove/incoming/secrets.tar \
  -C "$MANGROVE_SECRET_STAGE" --no-same-owner
```

只处理来自已核验交付方、哈希匹配的包。清单中的归档成员必须是相对路径，无 `../`、绝对路径或指向目录外的链接。发现异常停止。

### 9.2 在首次启动前核对数据

默认 WebUI、Scheduler 应存在，且结果应为 `ok`：

```bash
sudo -u mangrove sqlite3 -readonly "$MANGROVE_DATA_STAGE/data/webui.db" \
  'PRAGMA integrity_check;'
sudo -u mangrove sqlite3 -readonly "$MANGROVE_DATA_STAGE/data/scheduler.db" \
  'PRAGMA integrity_check;'
```

其他 SQLite 快照逐项检查；不是 SQLite 的文件不使用这个命令。开发提供的逐文件哈希、库表行数与额外路径映射也要核对。只统计、不导出用户正文或凭据。

迁入前 app 目录中不应存在先前运行产生的业务数据库。若 `/opt/mangrove/app/data/webui.db` 已经存在，**停止，不能覆盖**；让开发判断是否误启动、已经产生新业务数据或属于上次安装。

新安装确认无既有业务数据后，先看复制预览：

```bash
sudo -u mangrove rsync -avn "$MANGROVE_DATA_STAGE/" /opt/mangrove/app/
```

确认只涉及清单中的业务目录，再复制：

```bash
sudo -u mangrove rsync -av "$MANGROVE_DATA_STAGE/" /opt/mangrove/app/
```

不用 `--delete`。不要把解压目录整个移动到已有 app 上，也不要复制未核对的源 Windows 工作区覆盖代码。

### 9.3 安装密钥与配置

默认数据文件名下：

```bash
sudo install -o mangrove -g mangrove -m 0600 \
  "$MANGROVE_SECRET_STAGE/vault/webui.db.model-connections.key" \
  /opt/mangrove/app/data/webui.db.model-connections.key

sudo install -o mangrove -g mangrove -m 0600 \
  "$MANGROVE_SECRET_STAGE/server.env" /etc/mangrove/mangrove.env

sudo -u mangrove ln -s /etc/mangrove/mangrove.env /opt/mangrove/app/.env
```

以上是首次安装。目标文件或 `.env` 已存在时，不追加 `-f` 强行覆盖，先核对来源。平台签名密钥按清单放入 `/etc/mangrove/keys/` 等受限目录，并在 Linux 配置中指向正确路径；完整保留公私钥及其配套身份。

**这次迁移保留原账号。不要执行初始化管理员，不要重新注册“第一个用户”，也不要生成新 JWT 替代旧值。**

### 9.4 只检查数据库版本，先不迁移

```bash
cd /opt/mangrove/app
sudo -u mangrove /opt/mangrove/venv/bin/python -m src.database_migrations status \
  --profile webui --database data/webui.db
sudo -u mangrove /opt/mangrove/venv/bin/python -m src.database_migrations status \
  --profile scheduler --database data/scheduler.db
```

应与交付清单匹配且无迁移内容漂移。相同版本代码与已匹配数据库迁移通常不需要重新 apply。

若提示版本落后或 hash 不匹配：停在这里，由开发判断。只有计划已审查、目标库确认且已独立备份，才执行显式迁移；不能套用“新实例初始化”的命令。未识别数据库不得执行升级。

---

## 10. 配置检查：哪些必须调整，哪些必须保留

### 10.1 由开发提供 server.env

IT 用 `sudo nano /etc/mangrove/mangrove.env` 编辑。nano 中 `Ctrl+O` 保存、回车确认文件名、`Ctrl+X` 退出。不要把真实文件内容截图或复制到聊天。

下面只是非秘密配置示例，不是完整 server.env，**不能整段覆盖交付文件**：

```dotenv
API_HOST=127.0.0.1
API_PORT=8088
API_DEBUG=False
WEBUI_DB_PATH=data/webui.db
SCHEDULER_DB_PATH=data/scheduler.db
CHECKPOINT_DB_PATH=data/checkpoints.sqlite
DATA_PREP_UPLOAD_ROOT=data/uploads
SEMANTIC_EXECUTION_ROOT=data/semantic-executions
DATA_PREP_ARTIFACT_ROOT=downloads
WEBUI_ALLOW_REGISTER=False

AGENT_KERNEL_PRIMARY_ADAPTER=pi-runtime
PI_RUNTIME_IMAGE=mangrove/pi-coding-agent:0.87.1-v1.0.0
PI_CAPABILITY_HOST_IMAGE=mangrove/pi-coding-agent:0.87.1-v1.0.0
PI_RUNTIME_RESUME_IMAGES=[]
PI_RUNTIME_EGRESS_IMAGE=mangrove/smokescreen:da4840c9
COREMIND_RUNTIME_ENABLED=False

CAPABILITY_SUPPLY_CHAIN_TOOL_ROOT=/opt/mangrove/linux-tools
CAPABILITY_SUPPLY_CHAIN_LOCK_PATH=config/supply-chain-tools.linux-amd64.lock.json

SCHEDULER_ENABLED=False
COOKIE_HEALTH_SCAN_ENABLED=False
LIBRARY_DEDUP_SCAN_ENABLED=False
```

本版仅安装 Pi 0.87.1，旧版本恢复列表保持为空；历史任务记录保留，绑定旧版本的未完成任务不保证续跑。Capability Host 是否启用、模型及其他业务开关沿用已核对的原配置，不因迁移随意改变。

`JWT_SECRET`、数据库凭据加密身份和 Vault key 由开发安全迁入。模型 Key 可能保存在 Vault 密文中，不必也不能随意从数据库手工导出明文重填。

`WEBUI_CORS_ORIGINS` 填实际允许使用的 HTTPS 入口，例如 `https://实际公网IP`，若启用内部独立 HTTPS 入口则增加其精确 origin。不要设置 `*` 规避登录问题。

### 10.2 数据库保存的配置会覆盖 .env

这是本工程的真实行为：启动时先读环境，再套用数据库中的管理员设置。因此只在 env 写“关闭巡检”不一定生效。

由开发在备份前记录并暂停需要暂停的配置；迁入后检查实际生效值。下面只输出非秘密开关，不输出密钥：

```bash
cd /opt/mangrove/app
sudo -u mangrove /opt/mangrove/venv/bin/python - <<'PY'
from src.api.auth import get_store
from src.config.runtime_config import apply_global_overrides
from src.config.settings import settings

apply_global_overrides(get_store())
for name in (
    "scheduler_enabled",
    "cookie_health_scan_enabled",
    "library_dedup_scan_enabled",
    "agent_kernel_primary_adapter",
    "pi_runtime_image",
    "pi_capability_host_image",
    "pi_runtime_resume_images",
):
    print(name, "=", getattr(settings, name))
PY
```

若与迁移期要求不符，由开发通过既有配置接口处理。IT 不要直接 SQL 修改 runtime_config 或把密文替换为明文。

服务器的实际生效值必须满足：Pi 与 Capability Host 镜像均为 `mangrove/pi-coding-agent:0.87.1-v1.0.0`，`pi_runtime_resume_images` 为空列表。不能只修改 env，却保留数据库里的旧镜像恢复配置。

需要重点检查的迁移差异：Windows 解释器路径、采集仓库路径、模型内网地址、旧机器 localhost 端口、浏览器缓存、数据库白名单、平台签名材料位置。`localhost` 在 Linux 上表示 Linux 自己，不是原 Windows 电脑。

### 10.3 模型与外部来源

- 模型仍在另一台公司服务器：Linux 必须能访问那台服务器对应地址与端口。
- 使用云模型：保留原连接身份；确认服务器出站、TLS 信任与公司授权。
- Cookie 搬迁后可能因出口 IP/设备变化需要重新验证；不能把网络失败一律当成 Cookie 失效。
- 出站私网白名单不能改成全部放行。由开发按实际批准的模型/数据源配置。
- 首次验收使用无敏感测试文件和已批准模型预算，不自动重跑全部历史任务。

---

## 11. 创建 systemd 服务并检查本机启动

**执行条件：原平台已停写、数据和密钥校验完成、自动任务与巡检状态已核对。**

创建文件：

```bash
sudo nano /etc/systemd/system/mangrove.service
```

完整内容如下：

```ini
[Unit]
Description=Mangrove application
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=simple
User=mangrove
Group=mangrove
SupplementaryGroups=docker
WorkingDirectory=/opt/mangrove/app
Environment=PYTHONUTF8=1
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PLAYWRIGHT_BROWSERS_PATH=/var/lib/mangrove/browsers
Environment=FORWARDED_ALLOW_IPS=127.0.0.1
ExecStart=/opt/mangrove/venv/bin/python -m src.api.main
Restart=on-failure
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=120
UMask=0027

[Install]
WantedBy=multi-user.target
```

这里让程序在工作目录中读取 `.env` 链接；没有再用另一套 dotenv 解析器重复加载秘密。Nginx 只从本机转发，应用只信任本机转发头。

执行：

```bash
sudo systemctl daemon-reload
sudo systemctl start mangrove
sudo systemctl status mangrove --no-pager
sudo journalctl -u mangrove -n 100 --no-pager
curl --fail http://127.0.0.1:8088/api/health
curl --fail http://127.0.0.1:8088/api/readiness
sudo ss -lntp
```

成功标准：服务 active；两个健康请求成功；应用监听 `127.0.0.1:8088`。日志中没有 schema、JWT、Vault、导入依赖等错误。health/readiness 通过只代表基础就绪，不代表模型、容器与旧任务全部可用。

若反复自动重启，先 `sudo systemctl stop mangrove`，保留日志解决原因，不让错误循环持续刷盘。

### 11.1 暂时没有公网证书时怎么查看页面

在自己的 Windows 终端建立 SSH 隧道；该终端保持打开：

```powershell
ssh -N -L 18088:127.0.0.1:8088 -p 22 ituser@服务器地址
```

在同一台电脑浏览器访问 `http://localhost:18088/data-prep`，使用原平台账号登录。这里只用于受控运维验证，不能让全公司用普通远程 HTTP 绕过 HTTPS。

这一步能检查登录、页面和历史列表。任务容器要等第 12 章配置完成后再试。

---

## 12. 让 Docker 任务容器能够访问主程序

如果省略本章，常见现象是“页面能打开，点执行就报模型或文档连接错误”。

主程序绑定 `127.0.0.1` 保护后端。Docker 容器里的 `127.0.0.1` 是容器自己，所以另外让 Nginx 在 **Docker 的私网网关地址** 上提供只允许内部接口的入口。

### 12.1 查 Docker 网关

```bash
sudo docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}'
```

常见结果是 `172.17.0.1`，**必须使用实际结果**。地址需由开发/IT 确认为容器可达私网 IPv4，不能使用公网 IP，也不能盲目照抄示例。

本节示例以 `172.17.0.1` 说明。若实际不同，将本节 Nginx 和 env 中对应地址一起替换。

### 12.2 增加内部 Nginx 配置

```bash
sudo nano /etc/nginx/conf.d/mangrove-relay.conf
```

```nginx
server {
    listen 172.17.0.1:8088;
    server_name _;
    client_max_body_size 520m;

    location ^~ /internal/ {
        proxy_pass http://127.0.0.1:8088;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    location / {
        return 404;
    }
}
```

该配置只监听 Docker 私网地址。`127.0.0.1:8088` 仍属于主程序，两者不会抢同一个监听地址。不要把这里改为 `listen 8088`、`0.0.0.0:8088` 或公网 IP。

在 `/etc/mangrove/mangrove.env` 中填写：

```dotenv
PI_RUNTIME_RELAY_BASE_URL=http://172.17.0.1:8088/internal/model-relay
PI_RUNTIME_DOCUMENT_RELAY_BASE_URL=http://172.17.0.1:8088/internal/document-tools
```

让 Nginx 在 Docker 创建网关后再启动。此配置适用于本手册的专用服务器；共享 Nginx 先由 IT 评估其他站点影响：

```bash
sudo mkdir -p /etc/systemd/system/nginx.service.d
sudo nano /etc/systemd/system/nginx.service.d/mangrove-docker.conf
```

```ini
[Unit]
Requires=docker.service
After=docker.service
```

检查并加载：

```bash
sudo systemctl daemon-reload
sudo nginx -t
sudo systemctl reload-or-restart nginx
sudo systemctl restart mangrove
curl -s -o /dev/null -w '%{http_code}\n' http://172.17.0.1:8088/
```

最后一条应为 `404`：内部入口不提供网页。`nginx -t` 失败时不要 reload，先修正配置。

### 12.3 从容器检查内部连接

下面仅检查 TCP 与 HTTP 响应，不发送文件、密钥或模型请求：

```bash
sudo -u mangrove docker run --rm --network bridge \
  mangrove/pi-coding-agent:0.87.1-v1.0.0 \
  node -e "fetch('http://172.17.0.1:8088/').then(r=>{console.log(r.status);process.exit(r.status===404?0:1)}).catch(()=>process.exit(1))"
```

预期 `404`，而不是连接失败。实际任务使用专用网络及出站代理，仍需第 14 章的一次完整任务验证。

连接失败时检查 Docker 路由、主机 INPUT 防火墙和监听地址。网络人员只放行已批准 Docker 网段到这个私网地址的 8088；不向公网放开 8088，不关闭全部防火墙。

重启服务器后要重新验证本章，防止 Docker 网关变化或 Nginx 启动顺序导致任务失效。

## 13. 公网 HTTPS 与公司内网访问

### 13.1 IP 地址也可以配置 HTTPS

没有域名不等于只能使用 HTTP。Let's Encrypt 已提供公网 IP 证书，要求使用短期证书，约 160 小时有效；必须自动续期。本章采用 Certbot 的 webroot 方式，要求公网 80 能访问验证目录，公网 443 用于业务访问。

**停止条件：公网 IP 或 80/443 转发尚未确认。** 此时保留 SSH 隧道验证方式，不把 HTTP 服务直接开放给全公司。

本文的命令模板按固定公网 IPv4 编写。若只有 IPv6、动态公网 IP、运营商不允许 80 或前方有公司统一网关，由网络管理员与开发调整入口方案，不照抄。

依据：[Let's Encrypt 的 IP 证书说明](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability)、[Certbot 的 IP 证书操作说明](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)。

### 13.2 配置 HTTP 验证目录

**位置：Linux。先创建目录：**

```bash
sudo install -d -o root -g root -m 0755 /var/www/mangrove-acme
sudo nano /etc/nginx/conf.d/mangrove-public.conf
```

将 `PUBLIC_IP` 替换为 IT 确认的实际公网 IP；这是文字占位符，不是 Nginx 变量：

```nginx
server {
    listen 80;
    server_name PUBLIC_IP;

    location ^~ /.well-known/acme-challenge/ {
        root /var/www/mangrove-acme;
        default_type text/plain;
    }

    location / {
        return 503;
    }
}
```

`503` 表示还在部署，暂不提供业务。不要让根目录变成工程目录。

```bash
sudo nginx -t
sudo systemctl reload-or-restart nginx
sudo install -d -m 0755 /var/www/mangrove-acme/.well-known/acme-challenge
printf 'mangrove-acme-ready\n' |
  sudo tee /var/www/mangrove-acme/.well-known/acme-challenge/probe.txt >/dev/null
```

在**公司外部网络**用浏览器访问：

```text
http://实际公网IP/.well-known/acme-challenge/probe.txt
```

应看到 `mangrove-acme-ready`。只在服务器本机访问成功不够；外部失败时先找网络管理员，不反复申请证书。

### 13.3 安装 Certbot

使用独立环境，不污染 Mangrove 的 Python 依赖。示例使用 Certbot 5.8.0；支持 IP webroot 的客户端至少需要 5.4。

```bash
sudo /opt/python3.13/bin/python3.13 -m venv /opt/mangrove-certbot
sudo /opt/mangrove-certbot/bin/python -m pip install 'certbot==5.8.0'
sudo /opt/mangrove-certbot/bin/certbot --version
```

预期显示该版本。不要混用 apt、snap、pip 多套 Certbot 而不知道哪套负责续期。如果公司已有统一证书系统，可由 IT 提供包含该 IP 的可信证书和续期责任，本章 Certbot 步骤不重复部署。

### 13.4 先试申请，再正式申请

先填两个变量。邮箱使用公司的运维收件地址：

```bash
export MANGROVE_PUBLIC_IP='填写实际固定公网IPv4'
export MANGROVE_CERT_EMAIL='填写公司运维邮箱'
```

执行下面任一申请前，先由公司确认同意证书服务条款；`--agree-tos` 表示接受条款。试申请不会保存生产证书：

```bash
sudo /opt/mangrove-certbot/bin/certbot certonly \
  --dry-run --webroot --webroot-path /var/www/mangrove-acme \
  --ip-address "$MANGROVE_PUBLIC_IP" \
  --preferred-profile shortlived --cert-name mangrove-ip \
  --email "$MANGROVE_CERT_EMAIL" --agree-tos --non-interactive
```

试申请成功后，再正式申请：

```bash
sudo /opt/mangrove-certbot/bin/certbot certonly \
  --webroot --webroot-path /var/www/mangrove-acme \
  --ip-address "$MANGROVE_PUBLIC_IP" \
  --preferred-profile shortlived --cert-name mangrove-ip \
  --email "$MANGROVE_CERT_EMAIL" --agree-tos --non-interactive
```

成功后应有：

```text
/etc/letsencrypt/live/mangrove-ip/fullchain.pem
/etc/letsencrypt/live/mangrove-ip/privkey.pem
```

不要给普通用户读取私钥的权限。失败时查看 Certbot 错误，不反复强制申请以触发限流。

### 13.5 配置正式 HTTPS 转发

先创建 Nginx 可读的维护标记目录，再建通用业务转发片段。此目录不存放密钥：

```bash
sudo install -d -o root -g root -m 0755 /var/lib/nginx/mangrove
sudo nano /etc/nginx/snippets/mangrove-app.conf
```

```nginx
client_max_body_size 520m;

location = /internal {
    return 404;
}
location ^~ /internal/ {
    return 404;
}
location ~ /\. {
    return 404;
}
location / {
    if (-f /var/lib/nginx/mangrove/maintenance) {
        return 503;
    }

    proxy_pass http://127.0.0.1:8088;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}
```

再编辑 `/etc/nginx/conf.d/mangrove-public.conf`，用以下完整配置替换第 13.2 节临时配置。两处 `PUBLIC_IP` 都换成真实 IP：

```nginx
server {
    listen 80;
    server_name PUBLIC_IP;

    location ^~ /.well-known/acme-challenge/ {
        root /var/www/mangrove-acme;
        default_type text/plain;
    }

    location / {
        return 301 https://PUBLIC_IP$request_uri;
    }
}

server {
    listen 443 ssl;
    server_name PUBLIC_IP;
    ssl_certificate /etc/letsencrypt/live/mangrove-ip/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mangrove-ip/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    include /etc/nginx/snippets/mangrove-app.conf;
}
```

说明：

- `proxy_buffering off` 让执行进度及时显示；否则可能等很久才一次出现。
- `X-Forwarded-Proto` 让后端正确识别 HTTPS，避免“登录需要安全连接”。
- 公网 `/internal/` 明确禁止访问；Docker 通过另一份私网配置访问它。
- Nginx 上传上限略大于平台的业务上限，业务端继续做实际字节和权限检查。
- 维护标记只控制用户入口，不用它当“数据库已停写”的证明；后台和已建立连接可能仍在运行。

加载：

```bash
sudo nginx -t
sudo systemctl reload nginx
sudo systemctl enable nginx
```

首次开放前先由网络管理员只允许验收人员访问 443，完成第 14 章后再放开约定范围。

### 13.6 配置自动续期

创建续期成功后重新加载证书的脚本：

```bash
sudo install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
sudo nano /etc/letsencrypt/renewal-hooks/deploy/mangrove-nginx.sh
```

```sh
#!/bin/sh
set -eu
/usr/sbin/nginx -t
/bin/systemctl reload nginx
```

```bash
sudo chmod 0750 /etc/letsencrypt/renewal-hooks/deploy/mangrove-nginx.sh
sudo nano /etc/systemd/system/mangrove-cert-renew.service
```

```ini
[Unit]
Description=Renew Mangrove HTTPS certificate
After=network-online.target nginx.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/opt/mangrove-certbot/bin/certbot renew --quiet
```

再创建定时器：

```bash
sudo nano /etc/systemd/system/mangrove-cert-renew.timer
```

```ini
[Unit]
Description=Check Mangrove certificate renewal four times a day

[Timer]
OnCalendar=*-*-* 00,06,12,18:00:00
RandomizedDelaySec=30m
Persistent=true

[Install]
WantedBy=timers.target
```

启用并测试：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mangrove-cert-renew.timer
sudo systemctl list-timers mangrove-cert-renew.timer --all
sudo /opt/mangrove-certbot/bin/certbot renew --dry-run --run-deploy-hooks
sudo journalctl -u mangrove-cert-renew.service -n 60 --no-pager
sudo openssl x509 -in /etc/letsencrypt/live/mangrove-ip/fullchain.pem \
  -noout -dates -ext subjectAltName
```

预期：定时器有下一次执行时间；续期演练成功；证书包含实际 IP；部署 hook 能通过 Nginx 检查并 reload。必须安排证书过期监控：约六天有效的证书不能只靠“申请时成功”就不再检查。

### 13.7 公司内网怎样访问

**优先方案：内网和公网都使用 `https://同一个公网IP`。** 前提是公司网络支持访问该地址；由网络管理员配置路由/NAT 回流。该方案只维护一套证书和入口，浏览器会话也不会因地址不同而变成两套。

如果公司要求内网使用 `https://内网IP`：

1. 由公司证书服务签发包含该内网 IP 的证书。
2. 由 IT 给所有使用设备分发并信任公司的根证书；不能要求用户每次忽略证书错误。
3. Nginx 增加内网 IP 对应的 HTTPS server，使用该证书，复用 `mangrove-app.conf`。
4. 在允许 origin 中加入该精确 HTTPS 地址。
5. 内网和公网仍转发到同一个 Mangrove 进程和数据库。
6. 两个地址的浏览器登录状态独立是正常现象，业务账号和数据仍相同。

公网 IP 的证书不能拿来匹配另一个内网 IP。仅拥有一个内网 IP，也不能因此跳过公网路由和证书验证。

### 13.8 从外部检查

将地址改为实际公网 IP，使用可信证书检查，不加 `-k`：

```bash
curl --fail https://实际公网IP/api/health
curl -s -o /dev/null -w '%{http_code}\n' https://实际公网IP/internal/
curl -s -o /dev/null -w '%{http_code}\n' https://实际公网IP/.env
```

预期：健康成功；后两条是 `404`。另由 IT 从外网确认 8088、Docker API、数据库和辅助服务端口不可访问。

---

## 14. 完整功能验收

部署成功不是“首页能打开”。每一项记录时间、操作者和结果；真实任务会调用模型或采集来源，只用批准的测试材料和费用额度。

### 14.1 数据与账号

- [ ] 原管理员账号能登录，未初始化新的第一管理员。
- [ ] 一个已有普通用户能登录，角色与可见菜单正确。
- [ ] 各用户只能读取自己的业务内容；管理员审计读取仍走原有说明原因和留痕流程。
- [ ] 原用户数、任务数、计划数等计数与停写快照基线一致；迁移后新产生的审计记录单独解释。
- [ ] 抽查至少三条不同日期历史任务，包括较早任务，正文和附件能打开。
- [ ] 抽查旧正式结果可下载，文件 hash 与打包前一致。
- [ ] 模板、记忆与已配置模型连接可见。
- [ ] Token 用量能查看历史日期；未知用量不能因迁移被补成零。
- [ ] 跨宿主路径检查通过；缺失文件逐项列出，不能静默忽略。

### 14.2 新任务主链路

准备一份不含秘密的小 CSV，例如：

```csv
部门,月份,金额
甲部门,2026-01,100
甲部门,2026-01,200
乙部门,2026-01,50
```

由测试用户上传，要求“按部门汇总金额，输出 Excel”。预期能看到甲部门 300、乙部门 50，并完成平台正常的初稿/用户决策/正式交付流程。

- [ ] 来源上传成功，错误文件有明确提示。
- [ ] 执行进度实时出现，不长时间只显示静止加载。
- [ ] 容器能通过授权接口访问模型与文档，无连接失败。
- [ ] 结果能预览和下载；业务数据对得上。
- [ ] 初稿存在问题时能提示，用户仍可按原规则决定接受或继续。
- [ ] 取消一个无敏感测试任务后，工作状态和本任务临时容器能正确收尾。
- [ ] 重复点击不会意外产生多份相同操作；未知状态不直接重发。
- [ ] Word/PPT 预览可用，乱码或字体替换问题如实记录。

### 14.3 历史运行时与可选功能

- [ ] Pi 只部署 0.87.1，Pi 与 Capability Host 均指向该镜像，旧版本恢复列表为空；三项支撑镜像的 ID 与清单一致。
- [ ] 已清点所有未结束会话（含等待用户输入/接受初稿）。现有 Windows Pi 工作目录不能在 Linux 原地恢复，开发已与用户确定受影响会话的处置；不拿已结束任务强行重跑，不通过修改冻结路径伪造恢复。
- [ ] 每一个本次承诺启用的网页/社媒/搜索采集渠道各做一次授权的小规模验证。
- [ ] Cookie 健康验证与实际采集分开记录；绿标不是所有来源通用可用的证明。
- [ ] 数据库来源连接只开放清单中的主机/端口，跨 Owner 访问被拒绝。
- [ ] 能力治理、工具装载、签名/扫描功能按启用范围检查。
- [ ] 一个测试自动任务在最终恢复调度后仅执行一次；原机不能同时启动。
- [ ] 内网、公网各用一台真实终端测试登录、上传、执行、下载。

### 14.4 重启与恢复

在确认没有运行中业务后，安排一次应用服务重启。若要重启服务器，应另确认维护窗口：

```bash
sudo systemctl restart mangrove
sudo systemctl status mangrove --no-pager
curl --fail http://127.0.0.1:8088/api/readiness
```

之后再次核对历史记录、附件、Token 用量和一个新任务。服务器级重启后还需确认 Docker、Nginx、应用、证书定时器和辅助服务都能恢复。

备份恢复演练见第 17 章。没有实际恢复验证时，验收表填写“未验证”，不能写“已完成备份恢复”。

---

## 15. 正式切换与开机启动

满足条件：数据校验通过、核心业务验收通过、所承诺的辅助功能通过、可信 HTTPS 与续期通过、原平台已停止、负责人同意开放。

顺序：

1. 记录原机最后停写时间及服务器验收时间。
2. 在新平台恢复原先记录的自动任务与巡检设置；只恢复经确认需要的项。
3. 确认原机器不会因开机/登录自动启动旧平台。
4. 开启应用开机启动：
   ```bash
   sudo systemctl enable mangrove
   sudo systemctl is-enabled mangrove docker nginx
   sudo systemctl list-timers mangrove-cert-renew.timer --all
   ```
5. 若启用了维护标记，确认可以开放后移除这个指定文件：
   ```bash
   sudo rm /var/lib/nginx/mangrove/maintenance
   ```
   若从未创建该文件，则跳过本条。不能使用递归删除或通配符。
6. 网络管理员开放约定的内网/公网访问范围，仍只暴露 80/443 和受限 SSH。
7. 通知用户新的访问地址、账号沿用、首次需重新登录，以及问题反馈方式。
8. 观察首批任务、错误率、磁盘、内存和证书状态；观察期长度由负责人约定。

原 Windows 平台和最终备份先保留，不删除。只保留用于回退，不再接受新业务写入。

---

## 16. IT 日常运维速查

### 16.1 启停与日志

```bash
# 查看状态，不修改服务
sudo systemctl status mangrove --no-pager
sudo systemctl status nginx --no-pager
sudo systemctl status docker --no-pager

# 最近日志；对外反馈前脱敏
sudo journalctl -u mangrove -n 100 --no-pager
sudo journalctl -u nginx -n 60 --no-pager

# 持续观察，按 Ctrl+C 退出观察，不会停止服务
sudo journalctl -u mangrove -f
```

下列命令影响业务，仅在维护或排障批准后执行：

```bash
sudo systemctl stop mangrove
sudo systemctl start mangrove
sudo systemctl restart mangrove
```

不要使用 `killall python`、按端口杀未知进程或 `docker system prune -a --volumes`。删除旧镜像可能让历史任务失去恢复能力。

### 16.2 每日/定期检查

```bash
df -h / /opt/mangrove
df -i / /opt/mangrove
free -h
sudo docker system df
sudo systemctl --failed
sudo systemctl list-timers mangrove-cert-renew.timer --all
sudo openssl x509 -in /etc/letsencrypt/live/mangrove-ip/fullchain.pem -noout -enddate
```

建议由公司监控平台告警：服务不健康、磁盘或 inode 不足、频繁重启、证书续期失败、证书临近到期、备份失败。阈值由 IT 按实际容量填写，不能只有脚本无人查看。

Docker 日志和 journal 需要按公司策略限制保留量。不要直接改整台服务器日志策略影响其他业务；专用服务器可由 IT 设置容量上限。平台原始证据或业务日志是否可以清理，由开发和业务负责人确认。

---

## 17. 备份与恢复

### 17.1 要备份什么

一份可恢复备份至少包含：

- 与数据配套的代码 Git 仓库包/提交、前端构建、依赖清单；
- 全部实际业务数据根、数据库快照、附件、结果和记忆；
- 与数据库配套的 Vault key、原 JWT/凭据加密身份、签名材料；
- Nginx、systemd、应用配置、证书与自动续期配置；
- 被历史任务冻结的镜像及 Image ID；
- 辅助服务持久数据；
- 备份时间、校验和、目录映射和恢复记录。

密钥丢失时，数据库文件存在也不等于可以恢复。

至少一份备份存到另一台设备或公司备份系统。不要只在同一块磁盘下建 `backups/` 就认为具备灾难恢复能力。保留周期和加密存储按公司策略确定，不自动删除旧备份。

### 17.2 新手建议采用短暂停写备份

先安排维护窗口，停止新任务并等待已有任务收尾。创建维护标记后，**还需停止应用和其他实际写入方**；标记本身不停止后台任务。

```bash
sudo install -m 0644 /dev/null /var/lib/nginx/mangrove/maintenance
sudo systemctl stop mangrove
sudo systemctl is-active mangrove
```

预期应用 inactive。确认没有相关工作容器或辅助服务在写清单中的文件，由开发逐项判断，不执行“停止所有 Docker 容器”。

示例业务目录备份；目录清单须与正式部署映射相同，缺少项不要静默忽略：

```bash
export MANGROVE_BACKUP_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
sudo tar -czf "/opt/mangrove/backups/data-$MANGROVE_BACKUP_STAMP.tar.gz" \
  -C /opt/mangrove/app data downloads memory
sudo sha256sum "/opt/mangrove/backups/data-$MANGROVE_BACKUP_STAMP.tar.gz"
```

备份包包含数据库旁的 key 时，按秘密资料管理并加密存放。配置、签名材料与辅助卷另按清单备份。需要独立 SQLite 快照时复用第 4.4 节方法。

备份和校验成功、确认服务可以恢复后：

```bash
sudo systemctl start mangrove
curl --fail http://127.0.0.1:8088/api/readiness
sudo rm /var/lib/nginx/mangrove/maintenance
```

任何中间步骤失败，都先保留当前状态和已生成文件，联系开发决定恢复服务或排障，不继续清理或覆盖。

### 17.3 恢复演练

先在独立目录或隔离服务器演练，不覆盖正在使用的数据。

1. 校验备份 SHA-256。
2. 还原与备份匹配的代码和镜像。
3. 还原数据与配套密钥。
4. 检查数据库完整性、迁移版本、规则身份和文件 hash。
5. 确认演练实例没有启用自动任务、通知、巡检或对外模型调用。
6. 运行受控验收，核对账号、历史结果和附件。
7. 记录恢复时间、成功项、失败项以及未验证项。
8. 经确认后清理演练资源；保留报告和原备份。

若同一账号/源服务仍在线，演练实例不能恢复自动执行。需要业务级演练的外部调用应单独安排。

---

## 18. 升级和回退

### 18.1 升级不是直接 git pull

收到新的已审核完整交付包后：

1. 阅读版本说明，确认是否有数据库变更和新依赖。
2. 先在副本恢复演练；评估旧任务的镜像兼容。
3. 安排停用窗口，停止新任务，备份现行代码、数据、密钥和镜像身份。
4. 原应用停止后，部署新代码/前端；不删除业务目录。
5. 只有明确要求且审查过的数据库迁移才执行 apply。
6. 完成健康、历史读取、新任务、权限和预览检查。
7. 负责人确认后开放并观察。

更新依赖也会改变执行环境身份；不能擅自全量 `pip install -U`、`npm update` 或拉取 `latest` 镜像。

### 18.2 回退的关键区别

| 情况 | 处理 |
|---|---|
| 新版本未写入业务，数据库也未变更 | 可按已验证方案恢复旧代码、前端和对应环境，再复验 |
| 新版本已经产生业务数据 | 先停写并保全新数据，由开发制定兼容回退；不能直接覆盖旧备份丢掉新记录 |
| 已执行不兼容数据库迁移 | 需匹配的代码、数据库和密钥整套恢复，并处理迁移后的新记录 |
| 首次 Windows → Linux 迁移失败、Linux 尚无新业务 | 经负责人决定，可恢复原机作为唯一写入方 |
| Linux 已开始承接真实业务后再切回 Windows | 不能直接启动旧库；先处理 Linux 新增数据和任务状态 |

本手册不提供“复制旧数据库覆盖当前库”的一键回退命令。回退要保留数据，且原机、新机只能有一个正式写入方。

---

## 19. 配套功能与常见故障

### 19.1 配套功能不要漏交

完整迁移“保存的数据”与“所有外部服务已经可用”是两件需要分别验收的事。

| 功能 | 交付与安装要求 |
|---|---|
| Office 预览 | 导入 `mangrove/office-preview:local`，镜像内含 LibreOffice/字体；检查一份真实 Word/PPT 的显示效果 |
| SearXNG | 提供固定镜像及配置；仓库现有 compose 使用 latest 且端口绑定较宽，开发须交受限版本 |
| Firecrawl | 保留当前补丁/固定基线，交付已审查 Compose、各镜像和必要持久数据；API 管理口不直接上公网 |
| MediaCrawler | 独立依赖环境、精确源码和补丁、Node/浏览器运行条件、数据目录与授权 Cookie；不能使用 Windows venv |
| RSSHub 等其他来源 | 按实际启用清单配置，未启用的不强行安装 |
| 能力扫描与签名 | 使用 `config/supply-chain-tools.linux-amd64.lock.json` 对应工具与 hash；Windows 工具不能使用 |
| CoreMind | 可选运行时；本手册使用默认 Pi 运行时 |

**MediaCrawler 的现有第三方许可证标注为非商业学习用途。** 公司启用该组件前，应由负责人确认已有适当授权或提供批准的替代来源；本手册不替公司推定授权。依据为工程随附的 `external/MediaCrawler/README.md` 和组件 LICENSE。

当前外部组件准备脚本是 PowerShell，不能在 Linux 上直接执行。开发应交付可在目标 Linux 安装的精确组件与独立依赖清单。尤其 MediaCrawler 的依赖与 Mangrove 主环境不同，不应把它的 requirements 安装进主 venv。

若本次必须使用社媒采集，而 Linux 组件依赖尚未演练，**此项应在交付清单写“待开发完成”，不能让新手 IT 自行挑版本解决，也不能把整个平台标为完整验收。**

### 19.2 用现有 Dockerfile 取得 Linux 治理工具

可复用现有工具构建阶段，不启动验收应用。它只构建工具阶段，不包含业务数据：

```bash
cd /opt/mangrove/app
sudo -u mangrove docker build --target toolchain-build \
  -f docker/phase4b/Dockerfile -t mangrove/linux-tools:deployment .
```

创建一个仅供导出文件的临时容器，不运行它：

```bash
export MANGROVE_TOOL_CONTAINER="mangrove-tools-export-$(date +%s)"
sudo -u mangrove docker create --name "$MANGROVE_TOOL_CONTAINER" \
  mangrove/linux-tools:deployment
sudo install -d -o mangrove -g mangrove -m 0750 /opt/mangrove/linux-tools
sudo -u mangrove docker cp \
  "$MANGROVE_TOOL_CONTAINER:/opt/mangrove-tools/." /opt/mangrove/linux-tools/
sudo -u mangrove docker rm "$MANGROVE_TOOL_CONTAINER"
```

逐个核对 `executable_sha256`，再测试版本：

```bash
cd /opt/mangrove/app
sudo -u mangrove /opt/mangrove/venv/bin/python - <<'PY'
import hashlib
import json
from pathlib import Path

lock = json.loads(Path("config/supply-chain-tools.linux-amd64.lock.json").read_text(encoding="utf-8"))
for name in ("trivy", "syft", "cosign", "oras"):
    entry = lock[name]
    path = Path("/opt/mangrove/linux-tools") / entry["executable"]
    with path.open("rb") as handle:
        actual = hashlib.file_digest(handle, "sha256").hexdigest()
    if actual != entry["executable_sha256"]:
        raise SystemExit(name + ": hash mismatch")
    print(name + ": OK")
PY
/opt/mangrove/linux-tools/trivy --version
/opt/mangrove/linux-tools/syft version
/opt/mangrove/linux-tools/cosign version
/opt/mangrove/linux-tools/oras version
```

扫描还需要合适时效的漏洞数据库和原签名身份。工具启动成功不代表能力已经通过治理或允许扩大用户权限。

### 19.3 故障速查表

| 现象 | 优先检查 | 不要做 |
|---|---|---|
| SSH 连不上 | 地址、SSH 端口、VPN、防火墙、账号权限 | 不直接关闭所有防火墙 |
| apt/pip 下载失败 | DNS、公司代理、批准的镜像源、TLS 信任、磁盘 | 不加全局“忽略证书验证” |
| 安装报依赖冲突 | Python 版本、独立 venv、精确 requirements | 不随机升级/删除版本约束 |
| `schema outdated / drift` | 代码和数据是否同一批，迁移文件内容/hash | 不删除库、不改迁移账本 |
| Vault 或凭据解密失败 | 数据库配套 key、原 JWT/数据库密钥、权限 | 不生成新 key 代替原 key |
| 网页 502 | systemd 状态、8088 本机监听、Nginx error.log | 不盲目重装全平台 |
| “登录需要安全连接” | HTTPS 证书、转发协议头、127.0.0.1 信任代理 | 不关闭安全 Cookie |
| 登录成功后频繁退出 | 系统时间、HTTPS、混用两个 IP、Cookie、浏览器 Web Locks | 不直接延长无限会话 |
| “请求来源校验失败” | 实际浏览器 origin 与允许入口、代理 Host | 不设 wildcard 或跳过 CSRF |
| 页面能开，任务连不上 | 第 12 章的 Docker 网关、Relay URL、Nginx 私网监听、出站代理 | 不把内部接口公开 |
| 镜像不存在/摘要不同 | Docker load、精确标签与 Image ID、旧镜像清单 | 不用新镜像冒充旧镜像 |
| 旧任务文件不存在 | 实际目录映射、上传/执行/产物是否齐全、受管路径解析 | 不按文件名在全机猜测、不批量改冻结正文 |
| Office 预览失败 | 预览镜像、文件大小/加密状态、Docker 权限、字体 | 不重生成旧报告代替排障 |
| 浏览器采集提示找不到 executable | Playwright/Patchright 版本、缓存目录、服务账号 | 不复制 Windows Chromium |
| 小红书等采集失败 | 组件 Linux 依赖、Cookie 归属与健康、出口网络、来源返回 | 不自动切换他人账号 |
| 页面进度很久才一起出现 | Nginx buffering、代理超时、前置网关配置 | 不把模型超时一律加到无限 |
| 上传 413 | Nginx 和平台各自限额、实际文件大小 | 不无条件放开无限上传 |
| 自动任务执行两次 | 原机仍在线、两个应用进程、两套调度 | 不删除执行记录掩盖重复 |
| Token 只有新数据 | 是否迁入原 WebUI 库、筛选日期、历史未知状态 | 不把旧日志临时统计数当正式原生用量 |
| IP 证书申请失败 | 固定公网 IP、外部 80 可达、挑战目录、客户端版本 | 不反复 force-renew、不交付 staging 证书 |
| 几天后证书过期 | 定时器、80 持续可达、续期日志、hook、系统时间 | 不让用户忽略证书警告 |
| 磁盘持续增长 | Docker 日志/镜像、任务产物、备份保留、inode | 不直接 prune 全部镜像/卷 |

反馈开发时提供：发生时间、所处步骤、脱敏错误、服务状态、版本 SHA、相关任务 ID、是否已发送外部请求。不要提交密码、Cookie、Key 或用户原件全文。

---

## 20. 验收与交接记录模板

请复制这张表填写实际结果，不提前勾选：

| 项目 | 结果/证据位置 | 操作人 | 时间 |
|---|---|---|---|
| 服务器系统、架构、资源已记录 | | | |
| 交付文件 SHA-256 全部匹配 | | | |
| 部署提交与前端构建一致 | | | |
| 数据与密钥属于同一快照 | | | |
| 原平台停止且不会自动启动 | | | |
| 数据库 integrity_check 通过 | | | |
| 迁移状态与清单匹配，无 hash 漂移 | | | |
| 文件数量、内容 hash、引用路径通过 | | | |
| Pi 0.87.1 与两项支撑镜像 Image ID 全部匹配 | | | |
| 规则身份能够解析 | | | |
| 原管理员/普通用户登录与权限通过 | | | |
| 历史任务、附件、下载、Token 用量通过 | | | |
| 一条完整文件任务通过 | | | |
| Office 预览通过 | | | |
| 所有承诺启用的采集功能通过 | | | |
| 容器 Relay 与主机端口隔离通过 | | | |
| 公网可信 HTTPS、内部接口不可公开访问 | | | |
| 公司内网实际访问通过 | | | |
| 证书续期演练通过、监控负责人明确 | | | |
| 自动任务恢复后只执行一次 | | | |
| 重启恢复通过 | | | |
| 备份与恢复演练通过 | | | |
| 未解决问题、缺失资料和业务限制已披露 | | | |
| 项目负责人同意开放 | | | |

交付后由 IT 保管：本手册、最终清单、服务配置、备份位置、受限密钥交接记录、排障联系人。私钥和模型 Key 不附在普通验收报告里。

---

## 21. 本手册的依据与使用边界

### 21.1 当前代码依据

以下均为工程相对路径，便于开发核对：

| 事实 | 代码/文档 |
|---|---|
| 单进程入口与后台服务启动 | `src/api/main.py`、`src/api/services.py` |
| 远程登录 HTTPS、同源校验 | `src/api/auth.py` 的 `secure_cookie` / `require_csrf` |
| 前端由后端统一提供 | `src/api/main.py` 的 `_FRONTEND_DIST` 和静态路由 |
| 数据库运行时配置覆盖 env | `src/config/runtime_config.py` 的 `apply_global_overrides` |
| Vault 与 key 路径 | `src/config/secret_refs.py`、`src/model_connections/vault.py` |
| Git/源码/依赖参与核验身份 | `src/candidate_verification/ruleset.py`、ADR-0033 |
| Pi 容器、Relay 与镜像内容身份 | `src/agentic_runtime/pi_runtime.py` |
| 受管路径与兼容锚点 | `src/services/managed_paths.py` |
| Office 隔离预览 | `src/services/office_preview.py`、`docker/preview/Dockerfile` |
| Linux 工具链 | `docker/phase4b/Dockerfile`、`config/supply-chain-tools.linux-amd64.lock.json` |
| 主运行依赖与采集依赖分离 | `requirements.txt`、`requirements-collectors.txt` |
| 外部组件基线与补丁 | `scripts/setup_external_dependencies.ps1`、`external/README.md` |
| 当前能力和未验收边界 | `docs/status/current.md` |

部署时以交付目录中的 `RELEASE-MANIFEST.md` 和实际 Git 提交 SHA 核对源码身份。

### 21.2 官方资料

- [Docker Ubuntu 安装](https://docs.docker.com/engine/install/ubuntu/)
- [Docker Debian 安装](https://docs.docker.com/engine/install/debian/)
- [Docker 安全边界](https://docs.docker.com/engine/security/)
- [Python 3.13.15 发布文件和校验值](https://www.python.org/downloads/release/python-31315/)
- [Python Unix 安装](https://docs.python.org/3.13/using/unix.html)
- [IP 短期证书可用性](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability)
- [Certbot 申请 IP 证书](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)
- [Certbot 使用与续期](https://eff-certbot.readthedocs.io/en/stable/using.html)
- [Nginx HTTP 代理配置](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)

### 21.3 验收范围

上线前须完成目标服务器检查、网络连通、正式数据快照与密钥交接、第三方授权、代表业务和备份恢复验证。逐项结果记录在第 20 章验收表中。

本手册采用完整迁移流程，不能与“创建空库并初始化首个管理员”的新实例流程混用。所有实际未完成项在最终交付清单明确标注。
