# Mangrove 当前状态台账

> status: active
>
> last_verified: 2026-08-12
>
> branch: `main`
>
> baseline: 首次公开发布快照（以 `git rev-parse HEAD` 为准）

本文件是当前产品能力、工程状态和后续路线的唯一滚动台账。历史规格、ADR 和执行报告只提供
设计与验证证据，不应重复维护“当前状态”。

## 1. 产品定位

Mangrove 统一处理在线/离线、公域/私域、结构化/非结构化数据。当前已支持互联网公域采集和
PDF、Word、Excel、CSV 文件主链；数据库具备连接与测试基础。企业 API、业务系统、本地路径、
对象存储和统一生产 Adapter 仍是规划，不得在演示或文档中表述为已完成。

## 2. 当前稳定可用能力

| 能力 | 状态 | 说明 |
|---|---|---|
| Conductor 公域采集 | 可用 | 支持自然语言理解、规划、采集、清洗、分析和结果输出 |
| 正式数据工作台 | 可用 | `/data-prep`，支持不可变 revision、取消、版本、回收站、来源与结果预览 |
| 11 种交付预览 | 已工程验证 | CSV/JSONL/Parquet/XLSX 表格与 JSON/DOCX/PDF/HTML/Markdown/TXT/PPTX 文档预览 |
| vNext Delivery Publisher | 工程验证通过 | 只有独立验证与 QA 通过的 Candidate 才能形成正式 Delivery |
| 覆盖感知文档检索 | 已代表任务验证 | Pi 按目标发现/精读，Verifier 约束覆盖、证据和稳定顺序 |
| 多模型连接 | 工程实现 | 个人/平台多连接、Preset、自定义/LAN、Key 隔离和 TaskRevision 冻结 |
| 对话转向与上下文编译 | 工程验证通过 | 状态追问不改 Run，实质变化形成待确认 revision 草案 |
| Windows 本机一键启停 | 已实现（本机私有） | 8088 统一入口、5173 开发服务、双层后端监督和安全进程清理；脚本不公开发布 |

## 3. Agentic Capability

| 项目 | 当前状态 | 尚缺 |
|---|---|---|
| AC-04 能力目录 | 工程验证通过 | 用户代表验收/完整生产门按后续票推进 |
| AC-05 隔离能力获取 | 工程验证通过 | 生产迁移与用户验收；不得与业务来源同时联网 |
| AC-06 本地 Adapter + Sidecar | 用户灰度验收通过，默认关闭 | 远程 MCP、Registry 发现和普通用户开放 |
| AC-07 #33 三轴治理投影 | 完成并关闭 | 无 |
| AC-07 #34 可恢复 ValidationRun | 工程实现、双轴审查、生产迁移完成 | 真实能力灰度闭环与最终用户确认 |
| AC-07 #35 Trivy/Syft 证据 | 工程实现、真实双包扫描完成 | code-review、生产 `0003` 迁移、用户验收 |
| AC-07 #36～#44 | 未开始 | 签名、晋级、平台快照、审计、清理和后续发布门 |

AC-06 两项历史 `admin_gray_only` 包是迁移兼容例外：管理员/超管只能使用自己拥有的
TaskRevision 发起验证；普通用户、其他平台包和跨 Owner 任务仍失败关闭。

## 4. 明确未完成的生产门

- 30 项泛化集。
- Word/Excel 连续生产门与完整 PG-05。
- 真实外部 Provider 的 Pi→Relay→Provider 安全端到端验证。
- Rollout P0 GateSnapshot 和默认入口切换。
- 远程 MCP/Secret、Registry 自动发现、平台能力普通用户开放。
- 8B Linux/Compose/并发/故障与目标服务器验证。

上述任一项都不能因局部测试通过被表述为整个 Phase 4 完成。

## 5. 公开发布边界

`main` 从 2026-08-12 的已验证工作树生成干净公开快照，不继承旧私有仓库历史。公开版本包含
#34/#35 当前工程实现与清理结果，但不因此升级其验收、迁移或生产资格。

以下内容只保存在本机或受控运行存储，不进入 Git：Secret、数据库、上传/下载、日志、任务制品、
浏览器登录态、运行学习库、个人偏好、Agent 本机配置、虚拟环境和本地审计。MediaCrawler 与 Firecrawl
按固定上游提交和仓内补丁重建，不发布本机第三方工作副本。

## 6. 工程清理验证

2026-08-11 清理已完成自动化和真实启停验证：

- 后端聚焦 48 passed；全仓 1249 passed / 4 skipped。
- 前端生产构建通过；完整 Playwright 单 worker 54/54 passed。
- 维护者本机私有 `start_all.bat --no-pause` 启动后，8088 API、8088 HTML 与 5173 HTML
  均返回 200；`stop_all.bat --no-pause` 后端口、Pi 容器和网络无残留。两个脚本均由
  `.gitignore` 排除，不进入公开仓库。

首轮 4-worker Playwright 为 53/54，唯一文件集用例在等待按钮时超时；单测 1/1 和随后完整
单 worker 54/54 均通过，因此没有通过修改业务代码或放宽超时掩盖并发时序波动。

## 7. 当前优先顺序

1. 对 #35 做 Standards/Spec 双轴 code-review。
2. 用户授权后执行 #35 带备份生产迁移与 8088 验收。
3. 再由用户决定进入 #36，或处理剩余外部依赖与生产门工程债。

## 8. 权威证据

- AC-07 规格：`docs/plans/2026-08-06-agentic-capability-ac07-spec.md`
- AC-07 ADR：`docs/adr/0029-capability-validation-lifecycle-and-platform-publication.md`
- #34 报告：`docs/plans/2026-08-07-agentic-capability-ac07-02-execution-report.md`
- #35 报告：`docs/plans/2026-08-07-agentic-capability-ac07-03-execution-report.md`
- vNext Publisher：`docs/plans/2026-08-04-vnext-delivery-publisher-execution-report.md`
- Runtime 可靠性：`docs/plans/2026-08-06-v008-runtime-reliability-and-candidate-retry-closeout.md`

状态改变时先更新本文件，再更新精简 `handoff.md`；不要把滚动状态复制回 README、CONTEXT 或 ADR。
