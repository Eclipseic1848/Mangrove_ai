# 更新记录

## 未发布

### Improvements

- 将本机登录助手扩展至现有10个采集渠道，统一安装并按所选渠道授权；沿用个人/超管平台双入口及三步引导，保持原小红书包兼容。（[#295](https://github.com/Eclipseic1848/Mangrove_ai/issues/295)）
- 新增小红书本机 Chrome 登录助手与个人/超管平台双入口，沿三步引导显式读取和复制，再手动导入原配置；不接收密码、不自动上传、验证或恢复任务。（[#293](https://github.com/Eclipseic1848/Mangrove_ai/issues/293)）

## v1.1.0

版本变更如下；正式发布状态及对应提交以 [GitHub Releases](https://github.com/Eclipseic1848/Mangrove_ai/releases) 为准。

### Bug Fixes

- ZIP 与嵌套包共享实际展开额度，声明超限先拒绝，仅接受 STORED / DEFLATED 并保留被拒原包。（[#240](https://github.com/Eclipseic1848/Mangrove_ai/issues/240)、[PR #258](https://github.com/Eclipseic1848/Mangrove_ai/pull/258)）
- 文档 Relay 在缓冲前落实 1MiB 实收字节上限，越界返回 413 并停止后续接收和调用。（[#241](https://github.com/Eclipseic1848/Mangrove_ai/issues/241)、[PR #259](https://github.com/Eclipseic1848/Mangrove_ai/pull/259)）
- Markdown 默认不自动加载外部图片，保留说明、主动打开链接及原 URL 清洗。（[#243](https://github.com/Eclipseic1848/Mangrove_ai/issues/243)、[PR #261](https://github.com/Eclipseic1848/Mangrove_ai/pull/261)）
- MCP stdio 丢弃未消费的 stderr，避免管道背压；不缓存或转发原始日志。（[#245](https://github.com/Eclipseic1848/Mangrove_ai/issues/245)、[PR #265](https://github.com/Eclipseic1848/Mangrove_ai/pull/265)）
- Run 完成或取消时按冻结身份撤销文档 Grant、Token 并等待真实检索收尾，保留覆盖记录及审计。（[#246](https://github.com/Eclipseic1848/Mangrove_ai/issues/246)、[PR #266](https://github.com/Eclipseic1848/Mangrove_ai/pull/266)）
- 补回概览及模型设置使用的17张品牌 PNG 原件；生产构建必须核对图片原件与产物，防止缺图被兜底图标掩盖。（[#267](https://github.com/Eclipseic1848/Mangrove_ai/issues/267)、[PR #268](https://github.com/Eclipseic1848/Mangrove_ai/pull/268)）
- 复杂 cron 保持原字符串进入高级编辑，简单编辑只接受无损识别；仅改名称不改写时间语义。（[#247](https://github.com/Eclipseic1848/Mangrove_ai/issues/247)、[PR #269](https://github.com/Eclipseic1848/Mangrove_ai/pull/269)）
- PATCH 日期字段区分省略、null 与合法值，表单清空发送 null，非法日期在写入前返回 422。（[#248](https://github.com/Eclipseic1848/Mangrove_ai/issues/248)、[PR #270](https://github.com/Eclipseic1848/Mangrove_ai/pull/270)）
- 本轮明确生成的 XLSX 产物进入通知、运行记录和 Owner 受控下载；不扫描目录猜测历史工作簿。（[#249](https://github.com/Eclipseic1848/Mangrove_ai/issues/249)、[PR #271](https://github.com/Eclipseic1848/Mangrove_ai/pull/271)）
- 报告读取只接收最新请求代次；关闭、切换任务、历史或筛选后，旧成功及错误回调失效。（[#250](https://github.com/Eclipseic1848/Mangrove_ai/issues/250)、[PR #272](https://github.com/Eclipseic1848/Mangrove_ai/pull/272)）
- Capability Host 使用启动时冻结的所选能力执行预算；inspect、stop、rm 保留短运维预算。（[#251](https://github.com/Eclipseic1848/Mangrove_ai/issues/251)、[PR #273](https://github.com/Eclipseic1848/Mangrove_ai/pull/273)）
- 数据库连接创建和内联测试共享严格草案校验，非法方言、端口或缺失必填字段在保存及探测前拒绝。（[#252](https://github.com/Eclipseic1848/Mangrove_ai/issues/252)、[PR #274](https://github.com/Eclipseic1848/Mangrove_ai/pull/274)）

### Improvements

- readiness 对接单必需表使用 LIMIT 1，数据库及可写根检查单槽缓存五秒，Worker 退出仍实时反映。（[#242](https://github.com/Eclipseic1848/Mangrove_ai/issues/242)、[PR #260](https://github.com/Eclipseic1848/Mangrove_ai/pull/260)）
- 列表每次请求只读取一次 Owner 与计划绑定快照；缺项保持拒绝投影，跨请求重新刷新。（[#253](https://github.com/Eclipseic1848/Mangrove_ai/issues/253)、[PR #278](https://github.com/Eclipseic1848/Mangrove_ai/pull/278)）
- 自然到期计划共享两个并发槽，同任务不重入；停止禁新领取并等待自然及手动后台真实收尾，重复取消不提前释放。（[#254](https://github.com/Eclipseic1848/Mangrove_ai/issues/254)、[PR #279](https://github.com/Eclipseic1848/Mangrove_ai/pull/279)）

### Release Foundation

- 审核纳入十月现役源码和永久回归，保留已发布安全修补、默认 Pi 及可选 DeepSeek Search/Fetch、项目发现导出、预览、模型生命周期和来源历史能力。（[#239](https://github.com/Eclipseic1848/Mangrove_ai/issues/239)、[PR #257](https://github.com/Eclipseic1848/Mangrove_ai/pull/257)）
- 建立单实例暂停领取、自然排空、封口、进程身份与源码/前端清单核对后的受控重载；完整备份、独立恢复及异机副本作为切换前门。（[#244](https://github.com/Eclipseic1848/Mangrove_ai/issues/244)、[PR #262](https://github.com/Eclipseic1848/Mangrove_ai/pull/262)）

### Docs

- 后端、API 和前端统一产品版本 1.1.0；发行清单须绑定同一提交、构建摘要及版本，更新 CHANGELOG、公共入口和接手说明。（[#255](https://github.com/Eclipseic1848/Mangrove_ai/issues/255)、[PR #280](https://github.com/Eclipseic1848/Mangrove_ai/pull/280)）

### 升级与验证边界

- 当前 Schema 为 WebUI `webui_0025`、Scheduler `scheduler_0006`。首次维护显式迁移0023→0025，XLSX 迁移0005→0006；126条旧运行的新增 `xlsx_path` 保持 NULL。已有实例必须先核迁移链、备份数据及配套密钥，startup 不自动迁库。
- 默认 Runtime 仍为 Pi 0.87.1，镜像仍为 `mangrove/pi-coding-agent:0.87.1-v1.0.0`；平台版本更新不表示依赖镜像或冻结 Runtime 协议重新发布。CoreMind仍为独立候选，未默认切换。
- 34个历史目标、53份原件保持未知并隔离禁重发，隔离不等于逐 Run 停止证明。首次切换的风险接受、#249及#254停服超时后的单次例外、失败原件与完整维护时长均保留；不承诺零中断。
- 客户 Owner 旧 Chat 恢复拒绝和独立管理员角色两项真实验收按用户决定未执行；合成浏览器、图片HTTP和匿名拒绝检查不替代客户业务旅程。网络数据库、真实模型/能力/采集、多用户容量仍须按具体环境另验。
- 各票回归存在重叠，不将通过数相加冒称全仓覆盖。#254首批600秒无最终汇总的运行排除通过分母，后续同文件分组验证单列。既有依赖告警和历史业务评测限制见[安全策略](SECURITY.md)及[当前状态](docs/status/current.md)。

## v1.0.0 — 2026-09-27

### 功能

- 统一数据工作台：自然语言目标、来源管理、多轮追问、文件处理、结果预览及可追溯交付。
- 初稿显示已知问题，支持接受或继续核对；用户采纳状态与系统核验状态分别记录。
- 模型连接、自动化计划、个人记忆、模板、处理经验与按角色的新手引导。
- 默认任务运行时为 Pi 0.87.1。
- 提供 linux/amd64 部署包，包含 Git 源码、前端构建、三项核心镜像、校验清单和中文部署手册。

### 修复与改进

- 修复历史 Token 用量统计；趋势图增加悬停提示，支持小时、天、周、月、年粒度，默认按天。
- 改进来源约束、参数传递、隔离登录态、同账号凭据恢复及图片/OCR 预算。
- 改进 Word/PPT 原版式预览、历史分页、错误恢复、删除确认、表单和键盘操作。
- 修复上传鉴权与限额、数据库访问白名单、账号状态事务复核和资源释放。
- Linux 任务容器与宿主服务账号使用一致的 UID/GID，保留数据库迁移的冻结字节。
- 更新 AnyIO 至 4.14.2、pypdf 至 6.16.1、Protego 至 0.6.2；Pi 镜像同步 PDF 安全补丁。

### 部署与升级

- 核心镜像为 Pi 0.87.1、Smokescreen 和 Office Preview。Pi 镜像标签为 `0.87.1-v1.0.0`。
- 旧开发版 `main`（`ec4cf05`）的迁移链与本版不同，**不支持直接覆盖升级其数据库**。
- 新实例须显式初始化数据库；迁移已有实例须核对迁移摘要，并恢复同批次数据与密钥备份。
- 业务数据、账号凭据、Cookie 和密钥不随公开部署包分发。
- 不分发 Pi 0.80.10；绑定旧版本的未完成任务不保证原地续跑。
- Firecrawl、SearXNG、MediaCrawler 等可选服务需另行准备和验证。

### 已知限制

- 完整示例评测通过 285/360；后续定向复验通过 59/64。历史重放为 9 项一致、6 项差异、11 项缺输入。
- 平台能力治理仅面向管理员灰度，远程 MCP/Registry 和普通用户能力市场尚未开放。
- 构建、离线评测及 React Router 仍有依赖告警，详见[安全策略](SECURITY.md)。
- 目标服务器的网络、并发容量、备份恢复和实际业务需要按部署手册验收。

详细验证范围见[当前状态](docs/status/current.md)，安装步骤见[部署手册](docs/deployment/linux-migration-it-guide.md)。
