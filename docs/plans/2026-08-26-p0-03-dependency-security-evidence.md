# P0-03 依赖拆分与安全收口证据

> 状态：`ENGINEERING_REVIEWED`
>
> 核验日期：2026-08-26
>
> 对应 Issue：#58

## 1. 范围

本轮处理 Python 依赖安装边界、Windows/Python 3.13 可安装性、三个受跟踪 Node 工作区和
已公开漏洞。生产运行时、采集、开发、评测和 GPU overlay 分别验证；Node 前端、隔离
Promptfoo PoC 与 Agentic Runtime 赛马目录分别审计。容器操作系统漏洞和外部发布不在本报告内。

## 2. 初始事实

- 五组首次 clean install 均因 `uvloop==0.22.1` 在 Windows 上无条件生效而失败；修正为
  `uvloop==0.22.1; sys_platform != "win32"` 后，五组可独立解析和安装。
- 初始真实安装环境经 `pip-audit==2.10.1` 扫描，每组均有 157 条记录、36 个受影响包。
- 按 OSV/GitHub Advisory 去重为 129 项：Critical 0、High 64、Moderate 47、Low 18。
  其中 Click 公告的 GHSA 查询返回 404，使用其 CVE-2026-7246 的 NVD CVSS 3.1 记录补足为
  High 7.2；没有把未知严重度静默降级。

## 3. 收口动作

### HTTP、认证与加密簇

升级 aiohttp、Click、cryptography、FastAPI/Starlette、filelock、idna、PyJWT、
python-multipart、requests 和 urllib3。该簇 clean resolution、`pip check` 与 runtime import
smoke 通过，漏洞记录由 157 降至 92。

### 文档、数据与协议簇

升级 json-repair、Mako、MCP SDK、Pillow、protobuf、PyArrow、pyasn1、
pydantic-settings、pypdf、python-dotenv、setuptools 和 Werkzeug。该簇验证通过，漏洞记录
由 92 降至 52。

### Agent 框架兼容簇

使用满足公告修复和相互版本约束的最小一致组合升级 LangChain、LangGraph、checkpoint、
SDK、LangSmith 与 OpenAI SDK。clean resolution、`pip check`、runtime import smoke 通过；
Agent Runtime、对话转向、运行时选择和 LLM Provider 代表性回归为 71 passed。漏洞记录由
52 降至 31。

### 孤儿依赖清理

GitPython、sqlparse、tornado 和 wheel 在 tracked `src/` 中没有直接 import，且在干净环境
中没有其他包依赖；删除其直接 pin，并一并删除只为 GitPython 保留的 gitdb/smmap 和未被
运行时消费的 setuptools pin。重新解析后这些包不再出现在生产环境，漏洞记录由 31 降至 0。

## 4. 最终独立验证

全部环境均使用 `uv 0.11.19`、CPython 3.13.7 从空 venv 安装：

| 组 | 已安装包 | 生效的精确 pin | install | pip check | import smoke | pip-audit |
| --- | ---: | ---: | --- | --- | --- | --- |
| runtime | 263 | 233 | 通过 | 通过 | 通过 | 0 个已知漏洞 |
| collectors | 302 | 249 | 通过 | 通过 | 通过 | 0 个已知漏洞 |
| dev | 274 | 240 | 通过 | 通过 | 通过 | 0 个已知漏洞 |
| evaluation | 270 | 236 | 通过 | 通过 | 通过 | 0 个已知漏洞 |
| gpu | 263 | 233 | 通过 | 通过 | 通过 | 0 个已知漏洞 |

collectors smoke 还实际加载了 `ScraplingCollector`、`Fetcher` 和 `StealthyFetcher`，没有只做
顶层包导入。五次 `pip-audit==2.10.1` 均以退出码 0 返回
`No known vulnerabilities found`，因此本轮没有需要限期接受的 Python Critical/High 风险。

机器可核验证据位于被 Git 忽略的
`.artifacts/p0-07-clean-env-audit-20260826-03/`：

- `pip-audit-installed-*.json`：五组最终真实环境报告；
- `dependency-vulnerability-classification.json`：36 个初始受影响包的直接/传递属性、
  tracked 分组、修复版本、tracked `src/` import 证据和最终动作；
- `dependency-vulnerability-classification.sha256`：分类报告 SHA-256，当前值为
  `1118b1a622b9e901bd03518d3497eb76d704748184aab7b5f02e84f687cdfbdc`。

### 4.1 最终 Linux 生产镜像

最终镜像 `mangrove:p0-03-phase4b-final7-20260826-201848` 的 ID 为
`sha256:c91d78272fcca5a262f8c0405fef54f4b6203956c6407e774f256f3152485db5`。
镜像以 `10001:10001` 运行，正式 `ENTRYPOINT` 为
`/app/docker/phase4b/entrypoint.sh`，默认命令为 `python -m src.api.main`。冻结的 10 项构建输入
在构建前后没有漂移。

真实 Linux 审计首先在 final6 发现构建进入镜像的 `pip 26.1.2` 命中
PYSEC-2026-3721 / CVE-2026-13346；随后在 python-build 阶段把 pip 精确锁定为 26.2 并重建
final7。临时派生审计容器使用 `pip-audit==2.10.1` 扫描 final7 实际
`/opt/venv/lib/python3.13/site-packages`：300 个发行包，包含 Linux 专属 `uvloop==0.22.1`，
退出码 0、已知漏洞 0。审计工具没有留在生产镜像。

final7 还通过 runtime/collectors 深导入、`pip check`、禁入包和 CUDA/Triton 缺失、非 root
Chromium `data:text/html` smoke。使用镜像默认 ENTRYPOINT、断网、只读 rootfs 和临时卷启动，
中央迁移应用 `webui_0001..0004` 与 `scheduler_0001`，health 返回 200，readiness 六项全部通过。
完整原始证据位于被 Git 忽略的
`.artifacts/p0-03-phase4b-final7-20260826-201848/`。

### 4.2 完整核心回归

最终依赖、SecretRef 与 Docker 契约冻结后，完整核心回归结果为：

```text
2165 passed, 7 skipped, 3 deselected, 8 warnings in 762.55s
```

三个显式 deselect 没有被静默当作通过：共享 `E:\python3.13` 的依赖漂移测试改在上述干净
dev 环境单独运行并 `1 passed`；VerifierRuleset 按设计拒绝当前未提交的 requirements 变化，须在
获得 Git 提交授权后复验；独立 G1 冻结集按规则保留，不因本轮代码和用户既有 fixture 变化而
重写。最新 Phase4B 验收文件另为 `26 passed`。

## 5. Node 工作区与残余风险

### 5.1 已验证事实

2026-08-26 使用 Node `24.16.0`、npm `11.13.0` 读取受跟踪 lockfile 并运行
`npm audit --json`：

| 工作区 | 生产关系 | Critical | High | Moderate | Low |
| --- | --- | ---: | ---: | ---: | ---: |
| `frontend/` | 前端构建产物进入生产镜像 | 0 | 0 | 2 | 0 |
| `evals/promptfoo-batch8a/` | 隔离评测 PoC，不进入生产镜像或最小 CI | 0 | 5 | 0 | 0 |
| `evals/agentic-runtime-vnext/` | 隔离赛马工具，不进入生产镜像或最小 CI | 0 | 0 | 0 | 0 |

三个 lockfile 均为 lockfile v3，根依赖与 `package.json` 一致，所有非根 package 节点均有
版本。Promptfoo lockfile 由 npm 正常解析生成，没有手改；使用 `npm@10.9.9 ci --dry-run
--ignore-scripts` 重放退出码为 0。SHA-256 分别为：

- `frontend/package-lock.json`：
  `45ec594345cfedfb5e070fed79f9da6044abb337fc753f8c4b27a3df7c98731a`；
- `evals/promptfoo-batch8a/package-lock.json`：
  `0fa4a04089e80b6383c41c767a667da760e0906526f371a3422f992480e479b5`；
- `evals/agentic-runtime-vnext/package-lock.json`：
  `aba4dc3616cfea4de72f91d37c2f0200295e5e63f8df8b320c58e35365772289`。

`npm view promptfoo version` 返回 `0.122.1`，与当前固定版本一致。随后又从当前受跟踪的
`package.json` 和 `package-lock.json` 在全新隔离目录执行了真实 `npm@10.9.9 ci`；安装退出码
为 0，共实际落盘 752 个包，不再只以 `--dry-run` 作为安装证据。当前 npm 公告仍把
`@huggingface/transformers`、`adm-zip`、`onnxruntime-node`、`promptfoo` 和 `sharp` 五个节点
判为 High；这说明升级到当前最新版仍不能消除其传递风险。`npm audit` 建议的
`promptfoo@0.120.14` 是降级，不作为无回归证据的自动修复。

### 5.2 基于代码的可达性判断

- 前端使用 `BrowserRouter`，没有 `createStaticRouter`、`StaticRouterProvider`、
  `hydrateRoot` 或 `deserializeErrors` 路径，因此 React Router 的 SSR hydration 构造器注入
  公告对当前构建不可达。受跟踪的 `NavLink`、`Navigate` 和 `navigate()` 目标均来自本地路由
  常量；没有发现用户或 API 输入直接成为跳转目标。该结论不等于依赖本身已修复，未来接入
  外部重定向参数会改变可达性。
- Promptfoo 六案例只把固定 `scenario_id` 传给本地 `provider.py`，明确关闭缓存和分享；当前
  路径没有 ZIP 解压、图像/libvips 处理、Transformers 或 ONNX 模型推理。因此五个 High 节点
  对冻结六案例没有已识别的执行路径。Promptfoo 仍是庞大的评测工具依赖树，不能把这项
  代码判断表述为通用安全保证。
- `docker/phase4b/Dockerfile` 和最小 `.github/workflows/ci.yml` 均不引用两个 `evals/` Node
  工作区；生产镜像只执行 `frontend/` 的 `npm ci && npm run build` 并复制静态产物。

### 5.3 待审查的限期风险记录

| 风险 | 当前缓解 | Owner | 最晚复查 | 提前触发条件 |
| --- | --- | --- | --- | --- |
| React Router 6.30.6 的 2 个 Moderate；公告修复要求升至 7.18.2 大版本 | 继续只允许受跟踪的内部路由常量，不接受外部 URL/反斜杠目标；保留 BrowserRouter CSR 边界 | 前端依赖维护者 | 2026-09-25 | React Router 6 发布兼容修复；开始 SSR/hydration；新增用户可控 redirect/returnTo/to 参数 |
| Promptfoo 0.122.1 的 5 个 High 传递节点 | 仅在隔离评测目录按固定六案例运行；`--no-cache --no-share`；不处理不可信 ZIP、图像或模型文件；不进入生产镜像和最小 CI | 评测工具维护者 | 2026-09-25 | Promptfoo 或相关传递包发布修复；PoC 升为正式门禁；输入扩展到 ZIP/图像/模型；目录被生产或 CI 最小路径引用 |

到期前必须重新运行三个工作区的 `npm audit --json`，并对 React Router 7 做前端构建与完整
Playwright 回归、对 Promptfoo 做 lockfile clean regenerate、`npm ci` 与冻结六案例回归。
若提前触发条件出现，应先停止扩大可达范围，再完成升级或由维护者重新审查风险；本报告不构成
无限期风险接受。

### 5.4 Promptfoo 0.122.1 真实安装与冻结六案例

2026-08-26 在被 Git 忽略的全新隔离目录中，仅复制当前受跟踪的 `package.json`、
`package-lock.json`、`promptfooconfig.yaml` 和 `provider.py`，执行：

```powershell
npx.cmd -y npm@10.9.9 ci --no-audit --no-fund
$env:CI = "true"
$env:PROMPTFOO_DISABLE_UPDATE = "true"
$env:PROMPTFOO_DISABLE_TELEMETRY = "true"
$env:PROMPTFOO_DISABLE_SHARING = "true"
$env:PROMPTFOO_DISABLE_REMOTE_GENERATION = "true"
$env:PROMPTFOO_DISABLE_REDTEAM_REMOTE_GENERATION = "true"
.\node_modules\.bin\promptfoo.cmd --version
npx.cmd -y npm@10.9.9 run eval
```

可复核结果：

- 环境：Node `v24.16.0`、npm `10.9.9`、Python `3.14.6`；
- 真实 `npm ci`：退出码 0，实际安装 752 个包；
- `promptfoo --version`：`0.122.1`，退出码 0；
- 冻结六案例：6 passed、0 failed、0 errors，12/12 断言通过，退出码 0；
- 对真实安装树再次执行 `npm@10.9.9 audit --json`：退出码 1，仍为 5 High、0 Critical、
  0 Moderate、0 Low，节点与 5.1 节一致；原始报告 SHA-256 为
  `4b29e108559f7537a926ed555766102c1c2dc25edf27bef0af2166fc7baa5157`；
- 评测用量：6 次本地 Provider 请求、0 token；结果 SHA-256 为
  `63007dc45bdfed79c11941a4c5ee56d24ecaf1d26a08ae48bef1751a536d1298`；
- 本次使用的 lockfile SHA-256 仍为
  `0fa4a04089e80b6383c41c767a667da760e0906526f371a3422f992480e479b5`。

`provider.py` 只按固定 `scenario_id` 查询进程内静态字典并返回 JSON；六案例没有读取文件正文、
环境 Secret 或调用模型/HTTP。评测命令还显式关闭版本检查、遥测、分享及远程生成。`npm ci`
按授权从 npm 下载依赖包，这与评测数据或模型外发不同。上述结论来自配置、Provider 代码、命令
退出码和结果文件；本次没有做数据包级网络抓取，因此不把它扩写成 Promptfoo 通用零网络保证。

该目录的 `.gitignore` 明确排除 `results.json`，既有规范也把它定义为本地运行证据，所以本次
不提交生成结果、不手改历史结果；公共可复现结论记录在本节，本机原始结果位于
`.artifacts/p0-07-promptfoo-real-20260826-201115/results.json`。

## 6. 证据边界与剩余维护项

- `pip-audit` 与 `npm audit` 只能证明核验时公告数据库中的命中，不能证明未来没有新公告，
  也不替代容器 OS、动态恶意行为或生产验收。
- Firecrawl import 仍产生两个 Pydantic 字段遮蔽 warning；Starlette TestClient 提示未来迁移
  `httpx2`。二者没有造成解析、运行或漏洞门失败，不作为安全风险接受；依赖维护者应在下一次
  常规升级窗口复查。
- 原始漏洞 JSON 和本机 venv 属于本地审计证据，不提交 Git；公共结论以本报告、CI 分组门和
  requirements 精确 pin 为准。
