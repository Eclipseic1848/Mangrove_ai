# vNext Candidate → 正式 Delivery Publisher 实施报告

> 日期：2026-08-04
> 分支：`v0.0.8`
> 状态：`representative_flow_accepted_followup_retry_pending_user_recheck`
> 边界：本报告只证明 Publisher 工程纵切面；不代表整个 Phase 4、AC-06、完整 PG-05、
> 真实外部 Provider 或 8B 已完成。

## 1. 结论

vNext 已不再把“独立验证通过的 Candidate”作为用户终态。新任务的 Pi Candidate 只有在
VerificationReport 为 `passed`、候选集合与冻结 DeliverySpec 完全一致、候选哈希未变化、
Owner/Task/revision/Run/来源快照一致且取消/P0 门未阻断时，才自动进入统一 Publisher。

Publisher 会把候选复制到同卷 staging，使用既有 Delivery QA 能力独立重开每个格式，冻结
Manifest 和 SHA-256，在 `committing` 提交点后原子改名并用一次 SQLite 事务登记正式
Delivery 与不透明 `output_id`。工作台只在 `published` 后显示“已完成”和正式下载。

## 2. 实施范围

### 2.1 新增深 Module

- `src/delivery_publishing/models.py`：冻结 `PublishCommand`、`DeliverySpec`、候选引用和发布门；
- `src/delivery_publishing/pi_adapter.py`：只从 Owner 隔离的 Runtime、TaskRevision 和 UploadStore
  构造命令，不接收 Agent 提供的宿主路径、命令、密钥或 Renderer 地址；
- `src/delivery_publishing/service.py`：实现 staging → QA → committing → published 和恢复对账；
- `src/delivery_publishing/repository.py`：持久化 PublishIntent、通用正式 Delivery 和 outputs。

### 2.2 兼容策略

- 历史 `semantic_delivery_runs` 不迁移、不回填、不覆盖；
- vNext 使用不依赖 `semantic_harness_runs` 外键的新通用表；
- 现有 `/api/semantic-deliveries/{delivery_id}`、`/outputs/{output_id}`、工作台 `delivery` 字段和
  ZIP/存储统计继续读取统一公共契约；
- Legacy Renderer 错误仍是 `DELIVERY_RENDER_FAILED`，vNext Publisher 错误独立为
  `DELIVERY_PUBLISH_FAILED`。

### 2.3 幂等和恢复

`publication_key` 由 Owner、TaskRevision 哈希、CandidateSet 哈希、VerificationReport 哈希和
DeliverySpec 哈希确定：

- 同 key、同冻结输入返回同一 Delivery；
- 同 key、不同冻结输入失败关闭；
- staging/QA 阶段取消不登记正式输出；
- final 已改名但数据库提交前中断时，从冻结 Manifest 重开并完成提交；
- 已发布文件缺失或哈希变化时失败关闭，不覆盖历史 Delivery。

## 3. 验证证据

### 3.1 TDD 红灯

首次运行 `tests/test_vnext_delivery_publisher.py` 因模块尚不存在而收集失败：
`ModuleNotFoundError: No module named 'src.delivery_publishing'`。

### 3.2 聚焦门禁

命令：

```powershell
E:\python3.13\python.exe -X utf8 -m pytest -q `
  tests/test_vnext_delivery_publisher.py `
  tests/test_semantic_delivery.py `
  tests/test_semantic_workspace_api.py `
  tests/test_pi_runtime_workspace_api.py `
  tests/test_agentic_runtime.py `
  --basetemp=.pytest-tmp\publisher-gate2
```

结果：`71 passed`。

覆盖：未通过验证、候选篡改、格式/数量错配、提交前取消、Owner 隔离、幂等冲突、提交中断
恢复、发布后篡改、Legacy Renderer 兼容、Pi 自动发布、正式下载和跨用户 404。

补充检查：`compileall` 与 `git diff --check` 通过。当前 Python 环境未安装 Ruff，因此没有把
“Ruff 命令不可用”冒充静态检查通过。

全仓后端门禁：`754 passed, 4 skipped`；跳过项仅为需要显式参数的真实 MySQL/PostgreSQL
容器和大规模性能测试。前端 `npm run build` 通过，完整 Playwright `51 passed`。
最终安全审查把 Task/Run 路径改为服务端哈希目录、禁止覆盖未知 final 目录，并按当前
publication key 精确读取既有 Delivery；修改后 Publisher + Pi API 聚焦回归 `19 passed`。

本机开发数据库已执行幂等建表并复核：`delivery_publish_intents`、`formal_delivery_runs`、
`formal_delivery_outputs` 均存在；没有迁移、覆盖或回填历史 Delivery/Candidate。8088 API、
同源前端和局域网监听健康门通过。

同日用户验收发现正式 JSON 下载正常但预览误报“预览制品不存在”。根因是工作台预览仍只
读取 Legacy Harness 中间制品。现已补齐正式 Delivery output 的 Owner 隔离读取，预览前复核
路径、大小和 SHA-256；Legacy 任务仍优先使用携带逐行来源的内部 Parquet，避免正式 Excel
预览让来源定位退化，只有 vNext/Pi 没有 Legacy 制品时才读取正式 Delivery。

随后对界面全部可选交付格式做了矩阵盘点并收口：CSV、JSONL、Parquet 使用 DuckDB 表格
预览，XLSX 使用 openpyxl 表格预览，均支持分页、搜索和排序；JSON 以及 DOCX、PDF、HTML、
Markdown、TXT、PPTX 统一转换为分段文档预览。通用 JSON 按稳定根键转换为可读项目，不执行
HTML 或文件内脚本。11 种格式逐一生成、重开和预览为 `11 passed`；Pi CSV 公共接口先复现
404 后转绿，真实用户 CSV 正式交付回放为 `table / 2 行 / 18 列`；Pi + Legacy 工作台回归
`36 passed`，原 Parquet 来源定位、JSON 预览均通过。底层 `DeliveryFormat.TSV` 仍只是枚举
声明，界面和正式 Renderer 均未提供，不计入当前支持格式。

同日进度验收发现固定六阶段会让没有能力事件的已完成任务显示“准备能力尚未开始”和
`5/6`。现已把能力准备改为按需阶段：没有选择、挂载或获取额外能力时不展示；实际挂载冻结
能力时由 Runtime 产生 `capability.completed`。用户后续确认专业能力身份应当可见，因此
Runtime 会从冻结目录读取真实 Tool/MCP/Skill/能力包名称、类型、版本和用途，主时间线摘要
展示名称，展开后显示能力卡片。普通用户引用投影只允许上述四个字段，digest、宿主路径、
调用参数、Secret 和网络地址继续失败关闭。历史与新任务的 Pi 工具事件均在投影层转换为
“已确认任务范围”“已完成候选内容定位”“已完成来源
证据读取”“已完成结果完整性检查”等业务语言，原始技术事件只保留给管理员诊断。真实旧任务
无需重跑即从 `5/6` 回放为 `5/5`，内部工具名为 0；带能力事件的公共 API 用例显示第六阶段
且状态为完成。能力身份增量相关后端 `81 passed`，最终核心纵切面 `98 passed`、全仓后端
`1187 passed/4 skipped`、前端生产构建和完整 Playwright `52 passed`。

2026-08-04，用户确认上述能力身份展示与安全投影验收通过。该确认只覆盖本次进度 UX 增量，
不替代下方 Publisher 代表任务验收，也不表示 AC-05、AC-06 或整个 Phase 4 已验收。

## 4. 尚未完成与验收建议

> 2026-08-06 修订：用户已确认代表任务总体功能可用，Publisher、正式下载与在线预览不再是
> “尚未实现”。随后真实 DeepSeek 验证出现空响应，系统保留了来源已验证的 Candidate，
> 但缺少只重验语义门的入口。后续已补充一次有界空/无效响应重试和“重新验证候选”，并由用户
> 完成原真实 Candidate 复核；该结论只关闭该代表任务，不替代其他生产门。

- 原真实 `inconclusive` Candidate 已由用户完成“重新验证候选”并确认通过；
- 2026-08-03 已形成的历史 Candidate 不自动回填，符合 D3“不回填历史 Candidate”约定；
- Rollout/GateSnapshot 的持久化 P0 自动阻断尚未实施；当前 Publisher 保留显式 P0 门位，
  工作台接入值固定由服务端控制，Agent 无法传入；
- AC-06 本地 Adapter 与任务级 Sidecar 已通过用户灰度验收但默认关闭；AC-07 #33 已完成。
  30 项泛化集、真实外部 Provider 端到端、完整 PG-05、AC-07 后续票、8B、默认入口切换和
  版本标签仍未完成。

代表任务和 AC-06 灰度均已完成用户复核。候选仍只作为诊断证据，正式 Delivery 才是完成结果；
后续生产门继续按各自授权推进。
