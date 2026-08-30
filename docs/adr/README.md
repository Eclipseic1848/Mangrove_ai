# 架构决策记录（ADR）索引

> 每条 ADR 记录一个不可逆或影响深远的架构选择。后续改变原决策时新增 ADR 或追加明确的
> “状态变更”段，不用滚动实现日志重写原决策。
>
> 当前产品和工程进度只以 [`docs/status/current.md`](../status/current.md) 为准；本索引中的
> “状态”表示决策是否已采纳、取代或后置，不表示某个版本已经通过用户验收或生产门。

| 编号 | 标题 | 状态 | 日期 |
|---|---|---|---|
| [0001](0001-data-prep-mode.md) | 数据准备模式为默认主链路 | 已采纳 | 2026-07-19 |
| [0002](0002-raw-artifact-immutable.md) | 原始制品不可变，按任务隔离保存 | 已采纳 | 2026-07-19 |
| [0003](0003-llm-boundary.md) | LLM 只产 Recipe 草稿，确定性代码执行 | 已采纳；任务级临时代码边界被 0017 部分取代 | 2026-07-19 |
| [0004](0004-read-only-connectors.md) | 连接器默认只读，禁止写入/DDL/删除 | 已采纳 | 2026-07-19 |
| [0005](0005-output-formats.md) | 多格式输出，默认 JSONL + Parquet | 已采纳 | 2026-07-19 |
| [0006](0006-python-version-and-utf8.md) | 目标 Python 3.13，统一 UTF-8 基线 | 已采纳并落地 | 2026-07-19 |
| [0007](0007-evidence-bound-semantic-extraction.md) | 语义抽取必须绑定原始证据 | 已采纳，字段/记录/表格抽取闭环已实现 | 2026-07-21 |
| [0008](0008-safe-authenticated-source-discovery.md) | 认证来源发现采用隔离、只读和分阶段确认 | 已采纳，待 Phase 5A 实现 | 2026-07-21 |
| [0009](0009-document-parser-and-ocr-priority.md) | 文档解析按页分流，坐标证据优先于视觉模型候选 | 已采纳，基础层已实现 | 2026-07-22 |
| [0010](0010-document-task-units-and-file-sets.md) | 文档任务以独立文件或文件集为稳定边界 | 已采纳并落地 | 2026-07-23 |
| [0011](0011-mineru-paddle-parser-routing.md) | MinerU 主解析、Paddle 表格增强与失败回退 | 已采纳并落地 | 2026-07-23 |
| [0012](0012-semantic-task-plan-and-bounded-tool-loop.md) | 语义任务采用强类型计划与有界工具 Loop | 已采纳；固定前置计划被 0017 部分取代 | 2026-07-24 |
| [0013](0013-phase4b-batch0-tool-selection.md) | Phase 4B 批次 0 工具赛马结论 | 建议已落实到批次 3–6 | 2026-07-26 |
| [0014](0014-phase4b-batch4-evidence-document-engine.md) | Phase 4B 批次 4 证据约束文档执行引擎 | 已采纳并落地 | 2026-07-27 |
| [0015](0015-workspace-audit-tombstone.md) | 工作台删除后保留精简审计记录 | 已采纳并在批次 8A 落地 | 2026-07-28 |
| [0016](0016-docker-desktop-unified-development-and-clean-image-gate.md) | Docker Desktop 统一开发环境与干净镜像验收门 | 已采纳但整体后置 | 2026-07-28 |
| [0017](0017-agentic-runtime-vnext.md) | 数据工作台引入来源驱动的 Agentic Runtime | 已采纳，PG-05 恢复与安全纵切面已实现 | 2026-07-29 |
| [0018](0018-unified-task-domain-contract.md) | 统一任务域采用正交五轴模型与独立发布权 | 已采纳 | 2026-07-30 |
| [0019](0019-vnext-delivery-and-default-cutover-state-machine.md) | vNext 分离执行、正式发布与默认路由状态机 | 已采纳；三段 Rollout 顺序由 0030 部分取代 | 2026-07-30 |
| [0020](0020-provider-connection-broker-and-credential-isolation.md) | 模型连接采用凭证隔离代理与原生协议透传 | 已采纳；完整安全加固后置 | 2026-07-30 |
| [0021](0021-named-personal-model-connections-and-compatibility-slot.md) | 个人模型连接采用命名多实例与旧接口兼容槽 | 已实现并验收 | 2026-07-30 |
| [0022](0022-connection-model-validation-and-default-selection.md) | 模型连接采用逐模型验证与显式默认模型 | 已实现，等待用户验收 | 2026-07-30 |
| [0023](0023-platform-model-governance-and-frozen-task-selection.md) | 平台模型连接采用多实例治理，任务冻结用户选择 | 已实现，等待用户验收 | 2026-07-30 |
| [0024](0024-legacy-model-import-and-custom-protocol-discovery.md) | 旧模型配置采用待验证导入，自定义连接采用协议探测加人工覆盖 | 已实现，等待用户验收 | 2026-07-30 |
| [0025](0025-coverage-aware-document-retrieval.md) | 文档读取采用 Pi 自主检索与确定性覆盖完成门 | 已采纳并完成工程实现；序数增量已由用户复核通过 | 2026-07-31 |
| [0026](0026-agentic-capability-acquisition-and-procedure-governance.md) | Pi 能力获取采用隔离能力包与个人/平台方案治理 | 已采纳；成熟度定义由 0029 修正 | 2026-08-02 |
| [0027](0027-conversation-steering-and-context-compilation.md) | 运行中追问采用语义差异门与有界上下文编译 | 已采纳；AC-00～AC-03 工程验证完成 | 2026-08-02 |
| [0028](0028-task-level-capability-host-sidecar.md) | 原生任务能力使用单一 Capability Host Sidecar | 已采纳；工程门与用户灰度验收通过，默认关闭 | 2026-08-05 |
| [0029](0029-capability-validation-lifecycle-and-platform-publication.md) | 能力验证、生命周期与平台发布采用三轴治理 | 已采纳；新仓库 #9-#17 全部完成并关闭（两条真实纵切面 + 兼容切换，PR #30/#33/#34） | 2026-08-06 |
| [0030](0030-direct-vnext-default-cutover.md) | 合格后直接切换全用户 vNext 默认 | 已采纳 | 2026-08-23 |
| [0031](0031-durable-provider-qualification-batches.md) | Provider 资格外发采用独立持久批次台账 | 已采纳 | 2026-08-23 |
| [0035](0035-unified-data-workbench-and-coremind-runtime-adapter.md) | 统一数据工作台复用共享产品能力与 CoreMind Runtime | 已采纳 | 2026-08-27 |
| [0036](0036-single-workspace-with-verified-runtime-inheritance.md) | 单一工作台继承已验证 Runtime 与 Harness 能力 | 已采纳；部分取代 0018/0019/0035 的 Legacy 兼容要求 | 2026-08-30 |
| [0037](0037-private-multi-user-auth-observability-and-slo.md) | 私有多人部署的认证、可观测性与 P1 服务目标 | 已采纳 | 2026-08-30 |
| [0038](0038-confirmed-open-source-tool-installation-and-reuse.md) | 开源工具采用确认安装与持久复用 | 已采纳；产品层简化 0026/0029 的能力晋级呈现 | 2026-08-30 |

## 产品决策汇总（plan 第 3 节 + 本次确认）

| 项 | 决策 | ADR |
|---|---|---|
| 目标部署环境 | 开发期单机 Windows，架构面向 Linux 多容器（1AC） | 0001/0004 |
| 数据规模 | GB 级/千万行，契约按此设计，外部队列 Phase 5（2C） | 0002/0005 |
| 首发数据库 | SQLite + MySQL + PostgreSQL（3B） | 0004 |
| 原始保留 | 30 天可配置（4B） | 0002 |
| 自动脱敏 | 否，默认只检测告警（5A） | 0003 |
| 旧分析 | 旁路不删，待 e2e + 兼容期后按门禁删（6B） | 0001 |
| 输出格式 | 默认 JSONL+Parquet；CSV+TSV+JSON+XLSX 已实现；SQLite 为已采纳但未实现的后续项 | 0005 |
| LLM 边界 | 确定性清洗仍只接受 Recipe；后续语义抽取仅产有 EvidenceRef 的候选事实 | 0003/0007 |
| 场景复用 | 合同、营销等是纵向验收；核心任务/发现/证据/复核契约保持来源和行业无关 | 0001/0007/0008 |
| 认证来源 | 每用户/任务隔离、默认只读、候选范围先确认、MFA/CAPTCHA 人工接管 | 0004/0008 |
| 文件格式 | 含音视频，契约含 stub，实现分阶段（9C） | 0001 |
| API 范围 | REST/HTTP + 分页（10A） | 0004 |
| 音视频 | 复用现有字幕/ASR/Qwen（11A） | 0001 |
| Python | 目标 3.13（13B） | 0006 |
| 会话范围 | Phase 0 + Phase 1 内核（12B）；Phase 2 自 2026-07-20 起按独立计划继续实施 | - |
| 删除与溯源 | 工作台记录清理后保留不含正文、不可恢复的精简审计记录 | 0015 |
| 能力获取与复用 | 无来源挂载的独立获取阶段、不可变能力包、个人/平台方案正交隔离 | 0026 |
| 运行中追问 | 保留用户原话，LLM 生成 ContextDelta，确定性差异门决定只读回答或 Revision 草案 | 0027 |
| 原生能力隔离 | 一个任务共用一个无来源/模型配置挂载的 Capability Host Sidecar，Pi 仅持短期 Relay | 0028 |
| 能力验证与平台发布 | 成熟度、生命周期、运行资格三轴分离；平台快照独立签名并默认管理员灰度 | 0029 |
| vNext 默认切换 | 不建立普通用户显式试用资格；硬门合格并独立授权后从管理员灰度直接切换全用户默认 | 0030 |
| Provider 资格外发 | 独立持久批次台账先记 Attempt；每批只有一次初始执行和一次经用户确认的恢复重试 | 0031 |
| 统一工作台与 Runtime 复用 | 数据工作台是唯一主工作区；CoreMind 经 AgentKernel Adapter 复用，逐 Run 冻结版本与能力清单 | 0035 |
| 新平台收口 | 不迁移未上线历史资产；单一任务生命周期继承 CoreMind、Pi/coding-agent、Harness 的已验证能力 | 0036 |
| P1 远程多人运行 | 单组织私有部署；设备会话可撤销；最小内容日志；99.5% 月可用性与 2 秒交互目标 | 0037 |
| 开源工具获取与复用 | 缺失时发现、确认后安装；个人或平台范围持久复用；只统计模型 Token 与可选官方参考费用 | 0038 |
