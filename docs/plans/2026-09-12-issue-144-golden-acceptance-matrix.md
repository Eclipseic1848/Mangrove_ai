# Issue #144 黄金任务验收证据索引

状态：本地工程门通过；真实用户、真实站点、付费模型和目标服务器门未完成。

本索引只汇总可复跑证据，不把 Mock、合成素材或浏览器等效视口冒充真实外部验收。命令均从仓库根目录运行。

## 自动化与本地证据

| ID | 输入与确定断言 | 来源 identity | 结果 identity | 复跑命令 | 证据等级 |
| --- | --- | --- | --- | --- | --- |
| T01 表格清洗 | 固定生成的 16 行 CSV；筛选“谢超群”后必须为 11 行、2 个业务列、11 个独立证据行，验证状态为 `pass` | `artifact_id=upload_gate` + 输入 SHA-256 | 每行 `output_record_id` + 输出表结构 | `py -3.13 -m pytest -q tests/test_semantic_table_execution.py` | 合成确定性，已覆盖 |
| T02 六类表格格式 | 仓库内 CSV/TSV/XLSX/Parquet/JSON/JSONL；六类必须共享 DuckDB 语义并各得 11 行 | `fixture_<format>` + 文件 SHA-256 | ToolResult 状态、引擎摘要、输出行数 | `py -3.13 -m pytest -q tests/test_semantic_table_execution.py` | 固定 fixture，已覆盖 |
| D01 文档黄金集 | 24 份脱敏合成 PDF、120 页；合同/招标/发票分布必须为 12/7/5，全部标记 `CC0-1.0 synthetic` | `tests/fixtures/document_golden/expected.json` 中的文件名与许可证 | `document_count`、`page_count`、分布及逐文件页数 | `py -3.13 -m pytest -q tests/test_document_golden.py` | 固定 fixture，已覆盖 |
| D02 文档/OCR | 数字 PDF、扫描页、DOCX 及表格页；断言页码/位置、MinerU 原始结果、Paddle 表格增强、失败保留 `ocr_required` | 原文件路径 + ArtifactStore SHA-256 | 解析页/表格行 identity + 不可变缓存键 | `py -3.13 -m pytest -q tests/test_pdf_office_parsers.py` | 合成服务边界，已覆盖 |
| I01 图片 | PNG/JPEG/WebP 发票图；可信 OCR 必须发布可打开交付并保留原字节，低置信度/未知方向不得发布 | `upload_id` + 原图 SHA-256 | `output_id` + 下载 SHA-256 + QA `openable` | `py -3.13 -m pytest -q tests/test_image_file_task.py` | 合成 OCR，已覆盖 |
| W01 无 URL 公开搜索 | 固定查询“公开产品手册”，搜索快照返回 1 页；原 HTML 必须进入来源包，刷新生成 revision 2，同幂等键复用 attempt | `snapshot_id` + 原 HTML SHA-256 | `task_id` + revision + `attempt_id` + delivery | `py -3.13 -m pytest -q tests/test_public_search_delivery.py` | MockTransport，未冒充真实站点 |
| M01 混合来源 | 1 个 CSV + 2 个网页快照；三个原件必须进入 revision 1，仅刷新 B 后 revision 2 保留旧版，移除网页后 revision 3 只剩 CSV | `upload_id` + 两个 `snapshot_id` + 各原件 SHA-256 | `task_id` + revision 1/2/3 + `attempt_id` + delivery | `py -3.13 -m pytest -q tests/test_mixed_sources_delivery.py` | MockTransport，已覆盖持久接缝 |
| DB01 数据库 | MySQL 8 与 PostgreSQL 16 Testcontainers，各含两个 Owner、独立只读账号和 12 行表；断言 Owner 隔离、只读提取与来源快照 | `connection_id` + Owner + 表/列范围 + `source_id` | 每批 artifact/manifest SHA-256 与读取结果 | `py -3.13 -m pytest -q -m db_live tests/test_db_live_containers.py` | 本地真实数据库；Docker 不可用时会明确 skip |
| R01 正式交付与复用 | JSONL/XLSX/DOCX/PDF 及幂等发布；未验证、篡改、取消或跨 Owner 必须拒绝 | 冻结任务/修订 + candidate manifest digest | `output_id` + publication key + 文件 SHA-256 | `py -3.13 -m pytest -q tests/test_semantic_delivery.py tests/test_vnext_delivery_publisher.py` | 本地持久层，已覆盖 |
| C01 本地 MCP/能力宿主 | 本地 MCP 单会话复用、多包单 Sidecar、取消清理；远程 MCP 无任务授权必须拒绝 | pack/version/digest + task grant + Owner | session/sidecar cleanup identity + runtime evidence | `py -3.13 -m pytest -q tests/test_capability_adapters.py tests/test_capability_host.py tests/test_agentic_runtime.py` | 本地协议替身；远程 Registry 不在本票 |
| A01 旧资料复用 | 当前文件、历史来源和正式结果三类资料；精确重复只加入一次，失权、清理、换 Owner 或迟到响应不得复活旧正文 | `upload_id` + `snapshot_id` + 历史 `output_id` + Owner | 新 `task_id`/revision + 冻结派生来源 identity | `cd frontend; npm run test:e2e -- e2e/semantic-workspace.spec.ts --grep "#136" --workers=1` | 浏览器 Mock API，已覆盖 |
| P01 部分结果 | 来源不足、零证据、重试失败和严格数量缺口；只能展示真实部分结果，零证据不得提供接受动作 | task/revision + 来源集合 + completeness requirement | candidate/delivery `output_id` + 缺口说明 + QA 状态 | `cd frontend; npm run test:e2e -- e2e/semantic-workspace.spec.ts e2e/workspace-prototype.spec.ts --grep "部分|严格目标缺口|零条有证据" --workers=1` | 浏览器 Mock API，已覆盖 |
| RC01 运行控制 | 在途来源、停止结果未知、刷新、409、跨页迟到与任务清理失败；只允许按原身份查询或重试 | `task_id` + revision + `attempt_id` + idempotency key + Owner | 停止/清理 receipt + 最终状态 + delivery identity | `cd frontend; npm run test:e2e -- e2e/connector-source.spec.ts e2e/semantic-workspace.spec.ts --grep "停止|取消|结果未知|在途409" --workers=1` | 浏览器 Mock API，已覆盖 |
| SH01 分享 | 本人模板先预览，再确认生成通用副本；关闭、身份切换或失败不得串入旧内容或自动重试 | Owner + 私有 template/version identity | 共享副本 identity + 独立评分 | `cd frontend; npm run test:e2e -- e2e/library-sharing.spec.ts --workers=1` | 浏览器 fixture，已覆盖 |
| RD01 关联删除 | 删除前清单、共享保留默认、危险选择确认、未知结果查询、墓碑和独立正式结果保留 | Owner + source identity + cleanup plan/token + idempotency key | cleanup receipt + tombstone + 保留 `output_id` | `cd frontend; npm run test:e2e -- e2e/semantic-workspace.spec.ts --grep "#137" --workers=1` | 浏览器 Mock API，已覆盖 |
| K01 多模型/自带 Key | 8 类 Provider、本地/平台/个人连接、部分验证失败与云模型外发确认；切换 Provider 必须清 Key，不得重复计费请求 | Owner + `connection_id` + provider/model + SecretRef 配置键 | 验证 receipt + 默认模型 identity + task revision | `cd frontend; npm run test:e2e -- e2e/model-onboarding.spec.ts e2e/settings-role-access.spec.ts --workers=1` | 浏览器 Mock API；真实 Key/费用仍为 H05 |
| L01 计划、反馈与 Cookie 恢复 | 计划、反馈、结果未知、刷新、跨页迟到、Owner 切换及本人 Cookie 失效；只能查询原请求，Cookie 失效必须回本人设置恢复 | `task_id` + revision + Owner + idempotency key + Cookie 配置键 | run/feedback receipt + delivery `output_id` | `cd frontend; npm run test:e2e -- e2e/workspace-lifecycle.spec.ts --workers=1` | 浏览器 Mock API，已覆盖 |
| U01 三角色与前端边界 | user/admin/super_admin 的允许与拒绝、明暗主题、390px、键盘、IME、axe；另以 640×450/720×450 CSS 视口覆盖 200% 回流等效布局 | Owner + role + task/revision | 可见内容、焦点、请求次数和拒绝状态 | `cd frontend; npm run test:e2e -- --workers=1` | 浏览器自动化；不替代人工视觉与真实浏览器缩放 |
| S01 高危依赖 | Python collectors、前端与评测锁文件；Protego/Browserslist/Sharp/adm-zip 的受控版本及审计 High/Critical 必须为 0 | 上游包名、版本、lockfile integrity/digest | 安全脚本断言 + audit 严重度计数 | `gh workflow run ci-heavy.yml --repo Eclipseic1848/Mangrove_ai --ref codex/issue-144-independent -f gate=dependency-groups` | 独立 CI 安装与审计；记录 run ID，Moderate 需如实保留 |

## 人工与外部门

| ID | 验收输入 | 通过条件 | 当前状态 |
| --- | --- | --- | --- |
| H01 真实用户视觉 | 用户本人在统一入口完成新建、刷新恢复、停止、交付下载与再次新建 | 无旧任务残留、无永久“执行中”、操作含义可理解；用户明确确认 | 待用户验收 |
| H02 真实显示环境 | Chrome 实际 200% 缩放、390px 设备或等效真机、明暗主题、键盘与中文输入法 | 关键动作可达，无横向遮挡、焦点丢失或输入法误提交 | 待用户验收；自动化只有等效 CSS 回流证据 |
| H03 认证来源 | 用户本人提供测试账号或 Cookie，通过 #186 手填 Cookie 路线执行一次失效、更新、恢复 | 只读采集成功；失效后不绕门，更新后同 Owner 恢复 | 待人工凭据；#138 扫码路线继续暂停 |
| H04 真实站点与业务数据 | 用户批准的公开站点及脱敏业务样本 | 原文、来源范围、缺口、结果 identity 和重跑记录均可核验 | 待人工授权与素材 |
| H05 真实模型 | 用户批准的自带 Key 或付费模型、预算上限和外发范围 | 成功与拒绝都留痕；模型、用量、成本和输出绑定同一任务修订 | 待人工 Key/预算 |
| H06 目标服务器 | #143 的 Linux/GPU 主机、TLS、备份恢复、监控与性能场景 | #143 现场门完成且证据绑定最终部署版本 | 阻塞：目标服务器未到位 |

## 判定边界

- 自动化全绿只能完成本地工程门，不能关闭 H01-H06。
- #138 保持暂停；#144 只验收 #186 的管理员/用户手填 Cookie 稳定路径。
- 远程 MCP/Registry 和普通用户平台能力开放不属于 #144，不用 Mock 补写为已完成。
- #144 只有在本表自动化项、真实人工项、同提交 CI、Standards + Spec 审查全部完成后才能关闭。
