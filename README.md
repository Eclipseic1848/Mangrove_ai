# Mangrove（红树林）

Mangrove 是面向在线/离线、公域/私域、结构化/非结构化数据的智能数据任务平台。用户用自然
语言描述目标，系统负责来源获取、清洗、证据绑定、任务执行、独立验证和正式交付，让数据工程师
与业务开发者把精力集中在数据应用和价值创造上。

## 当前可用能力

- 公域互联网采集与多轮任务对话。
- PDF、Word、Excel、CSV 等本地文件处理。
- 不可变任务修订、来源证据、候选验证与正式 Delivery。
- 个人/平台模型连接和 OpenAI、Anthropic 等协议接入。
- 管理员灰度的 Python Tool、stdio MCP 与任务级 Capability Host。
- 能力三轴治理、可恢复 ValidationRun，以及 Trivy/Syft 供应链证据纵切面。

企业 API、业务系统、本地路径、对象存储、远程 MCP、普通用户平台能力开放、完整 PG-05 和
Linux/多人生产门仍在规划或后续验证中。详细状态只以
[`docs/status/current.md`](docs/status/current.md) 为准。

## 启动

1. 复制 `.env.example` 为 `.env`，按需配置模型与采集账号。
2. 如需网页或社媒采集，运行 `scripts/setup_external_dependencies.ps1` 准备固定版本的可选组件。
3. 安装依赖并构建前端，随后启动后端监督器：

```powershell
py -3.13 -X utf8 -m pip install -r requirements.txt
Set-Location frontend
npm install
npm run build
Set-Location ..
py -3.13 -X utf8 scripts/dev_reload.py
```

4. 打开统一入口：<http://localhost:8088>。按 `Ctrl+C` 停止服务。

`http://localhost:5173` 仅用于前端开发，不是统一产品入口；需要热更新时可在第二个终端运行
`npm run dev -- --host 0.0.0.0`。维护者本机的 Windows 一键启停脚本包含本地解释器、局域网
和服务编排配置，因此不随公开仓库发布。启动失败时先查看 `logs/dev_reload.log`。

## 开发环境

- Python 3.13
- FastAPI
- React + Vite + TypeScript
- SQLite（本地开发与审计状态）
- Docker Desktop（Pi Runtime、Capability Host 与隔离验证）

安装 Python 依赖：

```powershell
py -3.13 -X utf8 -m pip install -r requirements.txt
py -3.13 -X utf8 -m playwright install chromium
```

安装前端依赖并构建：

```powershell
Set-Location frontend
npm install
npm run build
```

## 验证

```powershell
py -3.13 -X utf8 -m pytest
Set-Location frontend
npm run build
npm run test:e2e
```

完整测试可能调用 Docker、OCR、本地模型或真实样例；应按当前工单风险选择聚焦门禁，并保留
可重复的测试源码。生成物可用 `scripts/clean_generated_artifacts.ps1` 清理。

## 可选第三方组件

Firecrawl 和 MediaCrawler 不作为 Mangrove 源码副本提交。执行以下命令会克隆固定上游提交并
应用仓库内经过审核的 Mangrove 补丁：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts/setup_external_dependencies.ps1
```

详见 [`external/README.md`](external/README.md) 和
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。MediaCrawler 仅限非商业学习与研究；
Firecrawl 遵循 AGPL-3.0。没有准备这些可选组件时，相应采集能力不可用，但不影响本地文件处理主链。

## 文档入口

- 工程规则：[`AGENTS.md`](AGENTS.md)
- 当前交接：[`handoff.md`](handoff.md)
- 当前状态：[`docs/status/current.md`](docs/status/current.md)
- 领域词汇：[`CONTEXT.md`](CONTEXT.md)
- ADR：[`docs/adr/README.md`](docs/adr/README.md)
- Agent 协作说明：[`docs/agents/`](docs/agents/)

## 数据与安全

- `.env`、`.claude/settings*.json`、本地审计、运行数据库、上传文件、浏览器登录态和任务制品不得提交。
- `data/lessons/`、`data/templates/` 和 `memory/` 中的运行学习结果或个人偏好默认只保存在本机；仓库仅发布机制说明。
- 不要整体删除 `data/`、`downloads/` 或外部采集器的浏览器登录态。
- 任何外部发布、数据外发、权限扩大、凭据处理和不可逆删除都需要人工确认。

## 参与和许可

- 贡献指南：[`CONTRIBUTING.md`](CONTRIBUTING.md)
- 行为准则：[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)
- 安全策略：[`SECURITY.md`](SECURITY.md)
- Mangrove 自有代码采用 [`MIT License`](LICENSE)；第三方组件遵循各自许可证。
