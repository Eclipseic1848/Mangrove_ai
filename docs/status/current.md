# Mangrove 当前状态台账

> status: active
>
> last_verified: 2026-08-23
>
> branch: `main`
>
> baseline: 以现场 `git rev-parse HEAD` 和 `git rev-parse origin/main` 核验为准

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
| AC-05 隔离能力获取 | 带备份生产迁移与用户验收通过 | 不得与业务来源同时联网；平台发布和普通用户开放仍是独立门 |
| AC-06 本地 Adapter + Sidecar | 用户灰度验收通过，默认关闭 | 远程 MCP、Registry 发现和普通用户开放 |
| AC-07 旧 #33 三轴治理投影 | 完成并关闭 | 历史工单保留在旧仓库 |
| AC-07 旧 #34 可恢复 ValidationRun | 工程实现、双轴审查、生产迁移、两项真实能力灰度、取消后重新发起和 8088 用户验收完成；Capability Host 代理修复经 PR #7 合并，旧工单已关闭 | 无 |
| AC-07 旧 #35 Trivy/Syft 证据 | 工程实现、双轴复审、带备份生产 `0003` 迁移与 8088 用户验收完成；PR #6 已合并到 `main`，旧工单已关闭 | 无 |
| AC-07 新 #8～#17 | **#9-#17 全部完成并关闭**（#15/#16 两条真实纵切面 + #17 AC-06 兼容切换与综合验收门；PR #30/#33/#34 合并） | 未开放普通用户；AC-08/AC-09/8B 未完成 |

AC-06 两项历史 `admin_gray_only` 包是迁移兼容例外：管理员/超管只能使用自己拥有的
TaskRevision 发起验证；普通用户、其他平台包和跨 Owner 任务仍失败关闭。

## 4. 明确未完成的生产门

- G1 独立泛化门已取得一次合格正式运行：v3 独立盲集共 36 题（31 功能 + 5 安全），
  DeepSeek V4 Flash 正式结果为功能 30/31（96.8%）、安全 5/5（100%），runner 判定
  `qualified=true`。31 个功能候选均经真实 Publisher、持久化 Delivery/output_id、独立 QA、
  文件完整性与来源血缘门；独立 oracle 额外拒绝 G103-F27（业务值或行序错误）。该结果只关闭
  G1 工程资格缺口，不代表 Phase 4、生产发布、Provider 认证或用户验收完成。
- G2 PG-05 已完成：Word/Excel 连续 3/3；AC-05 带备份生产迁移、恢复副本状态机、真实
  Docker 探针及验收均通过。PR #44 已合并，Issue #38 已关闭。该结论不代表 Phase 4、平台
  发布或普通用户开放完成。
- G4 两条真实 Provider Pi 链和传输安全已通过，但最终生产资格仍缺 Vault 轮换证据：地址解析、
  关闭 SDK 盲重试、显式确认、多轮 Usage 汇总和未知 Runtime 收口已完成。DeepSeek V4 Flash
  真实合成链形成 1 个候选并通过独立验证，6 次调用全部记录 Usage，合计 19,843 tokens。
  PR #47（`a0560852`）按 ADR-0031 增加固定持久台账、生产数据库单调锚点、全局运行锁、旧证据
  去重及进程退出后的 `outcome_unknown` 恢复规则。新的百炼后继批次保留旧两次失败历史后只
  执行 Attempt 1：Qwen `qwen3.7-max-2026-06-08` 形成 1 个候选并通过独立验证，11 次调用
  Usage 全部为 `recorded`，合计 36,744 tokens；批次、Provider 与 Attempt 均为 `passed`，
  两个 Grant 已撤销，无重试、恢复事件或 Docker 残留。台账 revision 3 与生产锚点身份、版本和
  状态哈希一致。绑定 `a0560852` 的传输安全矩阵 6/6 通过。生产 Vault Key 未轮换，无法生成
  同一身份的轮换报告，最终三证据汇总仍被阻断，G4 不得表述为完整合格。
- G3 Rollout P0 GateSnapshot、累计硬门、P0 自动回退与分阶段默认切换已完成工程实现、生产
  迁移和生产 Gate/Approval 初始化；运行态核验曾因旧 8088 进程触发 P0 回退，后端现已重启
  加载 G3、取得新合格快照并恢复到 `admin_gray`。工程代码已通过 PR #44 合入；生产默认切换
  与完整默认切换验收未执行。
- 远程 MCP/Secret、Registry 自动发现、平台能力普通用户开放。
- G5 本机前置包已通过真实运行：干净 Linux 镜像、Compose 接单就绪、非 root/只读根、
  真实浏览器闭环、20 用户 Owner 隔离、40 次并发重复抽取拒绝、进程重启、模型超时单次请求、
  SQLite 在线备份恢复和本次 Docker 资源零残留均通过。上传、Harness 与 Legacy/vNext
  Delivery 的路径可移植性代码门和本机跨根回归也已完成；新记录使用受管相对路径，旧
  Windows/POSIX 记录按冻结锚点映射，Owner、哈希和越界失败关闭。该结论只证明目标服务器
  验收前的本机前置条件，不等于完整 8B-1 或 G5 合格。
- G5 目标服务器 8B-2 尚未执行：目标 Linux/GPU/驱动/CUDA、生产并发与容量、长期运行、
  RAID/灾难恢复和另一台可信 LAN PC 人工验收仍缺真实环境。

上述任一项都不能因局部测试通过被表述为整个 Phase 4 完成。

## 5. 公开发布边界

`main` 从 2026-08-12 的已验证工作树生成干净公开快照，不继承旧私有仓库历史。2026-08-13
现场核对：#34 Capability Host 代理绕过修复已通过 PR #7 合并，merge commit 为
`8ac9ee4d810db4782c0a848ddbef245bcb9a9fe7`；本地 `main`、`HEAD` 与 `origin/main` 一致。
AC-07 主工单与未完成子工单已从旧仓库原生迁移到当前公开仓库，映射为旧
`#32/#36～#44` → 新 `#8/#9～#17`；已完成的旧 `#33～#35` 留作历史记录。

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

2026-08-13，AC-07 #35 完成本地闭环：

- Standards/Spec 最终双轴复审均为 PASS；供应链与治理聚焦回归 `92 passed`，设置页角色与治理
  Playwright `13 passed`，TypeScript 与 Vite 生产构建通过；
- 固定 digest 的真实 Docker 五步验证全部通过，Capability Host 容器、网络和临时目录零残留；
- 生产 `data/webui.db` 在 SQLite 在线一致性备份后幂等执行 `0003_supply_chain_evidence.sql`，源库
  与备份 `quick_check`、`integrity_check` 均为 `ok`，8 张既有能力表零改写；
- `gray-python-table@1.0.0` 与 `gray-everything-mcp@2026.7.4` 形成两条不可变生产证据，均为
  `passed`，Secret、Critical、可修复 High 和严重误配置计数均为 0；
- 用户确认 8088 管理员摘要和普通用户隔离验收通过；该结论不代表能力晋级、签名、平台发布、
  普通用户受众开放或整个 AC-07/Phase 4 完成。
- PR #6 发布门现场复核：七个 Capability 测试文件 `118 passed`，设置页角色与治理
  Playwright `13 passed`，TypeScript 与 Vite 生产构建通过，8088 `/api/health` 返回 `200`，
  `git diff --check` 通过；PR 为 `CLEAN`、`MERGEABLE`。当前公开仓库没有 GitHub Actions、
  `main` 分支保护或 Ruleset，因此没有可替代上述本地门禁的远端 CI 结果。

2026-08-13，AC-07 #34 完成两项真实能力灰度与 8088 用户验收：

- 真实任务 `workspace_16e574e208e440e1` 成功调用 `gray-python-table@1.0.0`，
  `workspace_d759e1a3fcdf482b` 成功调用 `gray-everything-mcp@2026.7.4` 的 `echo`；
- Python ValidationRun `capval_e2eaa8c0938243aaa62b` 五步全部通过；MCP 首次运行
  `capval_3247730d04e94246bd1c` 按用户取消进入 `cancelled` 且清理通过，同一冻结任务以新幂等键
  重新发起的 `capval_54d7850d99404070bd4b` 五步全部通过；
- 两项供应链证据均精确绑定目标 digest 且为 `passed`；验证后 Lease、Capability Host
  容器、专用网络和运行目录零残留；
- 真实任务暴露的 Capability Host 内网请求被外发代理接管问题已使用窄范围
  `NO_PROXY=<当前任务 Host DNS>` 修复；Agentic Runtime 与 Capability Host 回归 `50 passed`；
- 用户确认 #34 的 8088 验收通过。该结论不代表能力自动晋级、签名、平台发布、
  普通用户开放或整个 AC-07/Phase 4 完成。

2026-08-14，AC-07 #10 完成本地闭环：

- 晋级判定门、晋级命令、幂等/并发（InMemory 锁 + SQLite 部分唯一索引）、缺口投影、
  worker 双触发点与 Python/MCP 双夹具双向验证共 30 项新测试通过；
- Capability 九文件全集合 `164 passed`（既有 133 零回退）；前端构建与设置页
  Playwright `14 passed`；
- Standards/Spec 最终双轴复审均为 PASS；
- 生产 `data/webui.db` 在 SQLite 在线一致性备份后幂等执行 `0004_promotion_gate.sql`，
  源库与备份 `integrity_check` 均为 `ok`，既有治理表零改写；
- 8088 用户验收通过（能力治理页面无回归）。该结论不代表任何能力已自动晋级、平台已发布、
  普通用户受众扩大或整个 AC-07/Phase 4 完成。

2026-08-14，AC-07 #11 完成本地闭环：

- 管理员审核聚合视图（跨 Owner 任务身份/状态/时间/输入输出类型数量/验证摘要）、三组分组
  （待验证/已晋级/已弃用·撤销）、渐进披露与审计查看命令（原因必填、任务与验证证据绑定、
  失败留痕、2MiB 按块截断、前端固定幂等键）全部实现；
- 新增测试 63 项（模型/Repository 双实现/投影过滤/任务解析器/服务层/HTTP/Playwright）；
  后端全量 `1381 passed`（2 项环境基线失败：DNS 解析与端口监听，修复前即存在）；
  前端构建通过；Playwright `57 passed`（1 项并发偶发，单独复验通过）；
- Standards/Spec 双轴审查首轮 FAIL（2 阻断：大文件整读 OOM 风险、审计记录缺任务字段），
  修复后复核双轴 PASS；
- 零 DDL：`audit_viewed` 事件复用 `capability_governance_events` 表（0004 起支持），
  生产库无需新迁移；
- 验收暴露并修复 #10 遗留缺陷：平台能力（无个人 Owner）的验证摘要与任务元数据查询
  按能力身份过滤而非 owner 列；
- 8088 用户验收通过（分组、任务管理元数据、审计查看弹窗、审计记录、普通用户无入口）；
  验收期间产生 1 条真实审计记录（gray-everything-mcp 关联任务的 Prompt 正文）。
  该结论不代表平台已发布、普通用户受众扩大或整个 AC-07/Phase 4 完成。

2026-08-14，AC-07 #12 完成本地闭环：

- 平台发布机制链：候选门（verified/active/eligible 个人精确 digest）、脱敏快照生成
  （白名单重写 purpose/connection_ref/secret_ref、清空 environment、确定性重打包同源同
  digest）、平台六步验证（Smoke/失败关闭/Trivy/Syft/装载结构探针/独立验证）、#9 签名
  事务直用、发布命令（预期状态/幂等键服务端派生/受众固定 admin_gray）与受众变更命令
  （约束检查、无产品入口）全部实现；
- 生产接线完整：双 Layout 生成器、目录级六步执行器、签名事务与发布 Adapter 装配，
  平台 worker 挂入 lifespan；digest 标记与完整性记录补齐（供应链扫描两段式身份复核可过）；
- 平台验证 digest Lease（0005 表）与 FAILED 候选重试路径；
- 新增测试 75 项（模型/Repository/快照/执行器/服务层/worker/HTTP/前端 e2e）；后端全量
  `1448 passed`（2 项环境基线失败：端口监听与 DNS 白名单，修复前即存在）；
  前端构建通过；Playwright `59 passed`；
- Standards/Spec 双轴审查首轮 FAIL（3 阻断：生产接线缺失、真实探针未实现、AC7 约束缺失），
  四轮复核修复后双轴 PASS；
- 生产迁移 `0005_platform_publication.sql`：一致性备份
  `data/backups/webui-before-ac07-07-20260814-200229.db`，源库与备份 quick/integrity 均 ok；
- 8088 用户验收通过（页面无回归、无 verified 个人能力时候选区空态不占位、普通用户无
  入口）。真实装载执行探针与真实签名冻结夹具已在 #15/#16 纵切面完成（真实装载调用、
  独立 Layout 复验）。
  该结论不代表平台已发布、普通用户受众扩大或整个 AC-07/Phase 4 完成。

2026-08-15，AC-07 #13 完成本地闭环：

- 运行时装载治理门：`CapabilityMountResolver.resolve_for_owner` 收敛为唯一装载 Seam，
  装载前执行个人三轴（Owner/verified/{active,deprecated}/eligible）与平台受众/签名证据
  门；legacy 平台 Pack（无发布事件）维持 AC-06 旧路径放行（Q2，直至 #17）；
- 冻结与监督：创建/冻结走同一 `check_mount` 接口，追加三轴可选谓词（deprecated 不进入
  新任务选择）；`_run_pi_task` 以 30s 节奏只读投影监督（零 DDL），命中隔离/撤销即停
  Sidecar + 取消执行 + 禁止发布 Candidate/Delivery；无能力任务零负担；
- 新增测试 61 项（门矩阵/装载集成/装配/选择与冻结 HTTP/运行期监督/验证重放门）；后端全量
  `1092 passed`（1 项环境基线失败：DNS 白名单解析，修复前即存在）；前端构建通过；
  Playwright settings + semantic-workspace `38 passed`；
- Standards/Spec 双轴审查首轮 FAIL（2+1 阻断：监督竞态、digest 失配 422 回归、DEPRECATED
  历史恢复缺失），修复后复审发现并修复平台三轴缺查与冻结层 DEPRECATED 拦截（A1-A5/B1-B6
  全部落地）；复审后无残留阻断；
- 8088 验收：AC-06 历史包真实物化装载（legacy 放行）、digest 失配 422、未知包 404、
  draft 冻结 409、deprecated 冻结 409、列表过滤、运行中取消后容器/网络/Lease/临时目录
  零残留；完整任务执行受本地 LLM 环境限制未达成（非治理门问题，可复跑）。
  该结论不代表平台已发布、普通用户受众扩大或整个 AC-07/Phase 4 完成。

2026-08-16，AC-07 #14 完成本地闭环：

- 治理命令与事件：lifecycle_changed（弃用/撤销/恢复）、eligibility_changed（隔离/解除）、
  risk_accepted（限期接受）、recommendation_changed（回滚指针）四类事件与 validator；
  六个管理员命令（deprecate/revoke/quarantine/restore/risk_accept/rollback）+ 补
  change_audience HTTP 路由；全部 actor/原因/幂等键/预期状态，事件快照与写入时刻投影一致；
- 投影折叠升级：三轴逐轴取最后事实；风险接受惰性到期（读取时过期即按 quarantined 投影，
  零调度零 DDL）；推荐指针折叠并置顶标记选择列表；
- 门检查强化：发布门/受众变更门/恢复复查链补 Trivy 漏洞库 7 天时效复查（按内容 UpdatedAt）；
  恢复完整复查链（发布/签名证据 + 供应链证据 + 验证运行）；
- 风险接受约束：仅平台 admin_gray 受众、隔离中、任何 blocker（Secret/Critical/可修复
  High/误配置/库过期）不可接受、finding_ref 实引本包验证运行、1-90 天（默认 30）；
- 零 DDL：全部事件复用既有治理事件表，无新迁移；
- 新增测试 68 项（事件/折叠/命令/门/HTTP/推荐指针）；后端全量
  `1166 passed`（1 项环境基线失败：DNS 白名单解析，修复前即存在）；前端构建通过；
  Playwright settings + semantic-workspace `38 passed`；
- Standards/Spec 双轴审查首轮发现 4 项（签名受众检查、restore 幂等、事件快照冒充他态、
  风险接受约束），两轮修复复核后双轴「无新问题，可合入」；
- 8088 验收：9 个真实命令演练（applied/幂等/409 拒绝矩阵/403）全部符合预期，投影
  （deprecated+quarantined 两轴独立）与选择列表生效，事件留痕可审计，容器/网络零残留；
  真实 risk_accept applied 链与自动隔离触发接线已在 #15/#16 纵切面真实执行（#15 阶段 6
  惰性到期零新事件；#15/#16 篡改演示自动隔离 actor=system）。
  该结论不代表平台已发布、普通用户受众扩大或整个 AC-07/Phase 4 完成。

> 以下为 #15/#16 逐阶段历史执行时间线（全部阶段已完成，记录保留作证据；「下一门」
> 行仅表示当时状态）。

2026-08-17，AC-07 #15 阶段 0-2 完成本地闭环（真实纵切面）：

- 代码实现（S1-S5 + D9）：装载门自动隔离钩子（可选注入、四验签失败分支触发、默认 None
  保持 #13 只读）；手动重扫命令（rescan_supply_chain 服务方法 + /admin/supply-chain-rescan
  端点；证据追加不覆盖、BLOCKED 自动隔离、崩溃窗口补写、rescan_completed 事件）；
  D9 验证任务 Seam（validation_target 标记：冻结 selection 持久化、check_mount 豁免
  仅个人+Owner+active+eligible、平台包永不豁免、revision 不继承）；
  注册/驱动脚本与 52 项新测试；双轴审查两轮无残留阻断；
- 阶段 0：LLM（Qwen3.6-35B-A3B）与 Docker 可用；
- 阶段 1：注册个人 draft gray-python-table@2.0.0（digest 59076f40…）与 3.0.0
  （0ca80afd…），Owner=liyi（归档名修正后 --replace 重建）；
- 阶段 2：两条真实验证链全部完成——真实 Pi 任务真实调用 capability_python_table_summary
  工具、验证五步全 passed、供应链 passed（Trivy DB 2026-08-17 更新）、promoted_to_verified
  事件、投影 verified/active/eligible；**生产库首次出现 verified 个人能力**；
- 阶段 2 暴露并修复 6 个真实缺陷：instructor v2 strict 下 tuple 不可解析（missing_requirements
  改 list）、语义验证 prompt 自一致 + reason 截断 500 + max_tokens 4000、验证 worker 装配
  catalog NameError、能力归档固定名（_expand_capability_archive 只认固定名，带版本号不展开
  导致 Sidecar 不启动）、Trivy DB 7 天过期更新；
- 该结论不代表平台已发布、普通用户受众扩大或整个 AC-07/Phase 4 完成。
- 下一门：阶段 3（平台发布 2.0.0/3.0.0，候选→快照→签名→六步→admin_gray）待用户授权。

2026-08-17，AC-07 #15 阶段 3 完成本地闭环（真实平台发布，进行中）：

- **平台签名密钥就绪**：`~/.mangrove-signing/` 生成加密 Sigstore 密钥对（Cosign 3.0.6
  锁定工具），口令文件权限收紧，`get_platform_signing_runtime` 注入 password_provider
  从项目外口令文件读取（不进 argv/日志/事件）；.env 配置密钥路径（gitignored）；
- **阶段 3 双版本发布链全部真实走通**（2.0.0 与 3.0.0 各自独立链）：
  - 提交平台候选（候选门 verified/active/eligible）→ 脱敏快照新 digest
    （2.0.0=`5326dfae…`，3.0.0=`b462e577…`）→ platform_candidate 事件；
  - 平台六步验证全绿（SyntheticSmoke/失败关闭/Trivy/Syft/装载结构探针/独立验证，
    供应链证据 scope=platform 各 2 条）；Cosign 签名（回环 Zot Registry + OCI
    Referrers）写回运行记录；**生产库首次 platform_published 事件 ×2**；
  - admin_gray 发布 + 平台 OCI Layout 首个签名快照（signed/<run_id> 输出目录 +
    signing.json）；幂等重放同幂等键 already_published/already_submitted（AC6）；
  - 独立 Layout 密码学复验双版本 PASS（主体/签名 digest、公钥 `103de227…` 身份一致）；
  - 装载门闭环：admin 对两个签名平台包 check_mount 通过（签名验证成功、未隔离）；
  - 零残留：无 Zot/签名容器、无临时 Registry 监听、signing-runtime 已清理；
    阶段 2 遗留空网络 mangrove-capval-* 已清理；
- **阶段 3 暴露并修复 3 个真实缺陷**（都是 #12 发布机制真实首跑才暴露）：
  1. 平台快照生成器硬编码读/写 `manifest.json`，真实归档是 `mangrove-capability.json`
     → 兼容标准名（读优先标准名、写保留源名），快照 tar 保留标准名供物化展开；
  2. 平台六步目录级执行器（SyntheticSmoke/FailClosed/MountProbe/独立验证）硬编码
     `manifest.json` → 统一 `_resolve_manifest` 兼容标准名（worker 不再崩）；
  3. `materialize_platform` 路径拼接 `Path + str`（运算符优先级错误 TypeError）
     → 修正为括号拼接；`get_platform_publication_dependencies` 发布 Adapter 传绑定
     方法而非对象（service `.save_pack` 调用 AttributeError）→ 改传仓库实例；
- 新增测试 18 项（快照标准名 1 + 执行器 9 + 既有快照 8 回归）；平台发布/快照/执行器
  聚焦回归 67 passed；
- 该结论不代表普通用户受众扩大或整个 AC-07/Phase 4 完成。平台发布受众固定 admin_gray。
- 下一门：阶段 4（管理员任务选择/真实装载、回滚指针、deprecated + 历史冻结恢复）待授权。

2026-08-18，AC-07 #15 阶段 4-5 完成本地闭环（真实治理动作链 + 真实装载 + 篡改演示，进行中）：

- **阶段 3 暴露并修复快照缺 purpose 缺陷（方案 A 重建发布链）**：
  - 平台快照白名单删 `purpose` 导致真实装载 `PI_RUNTIME_FAILED`（运行时模型必填）；
  - 修复：快照写中性脱敏 purpose（`_SANITIZED_PURPOSE`），重建发布链——删旧平台 OCI
    tag + 目录行 → 新快照新 digest（2.0.0=`e5556f83…`，3.0.0=`9379fe29…`）→ 六步 →
    签名 → 发布；旧事件流保留作失败留痕；独立 Layout 复验 PASS、装载门闭环通过；
- **阶段 4 治理动作链 + 真实装载**：
  - 管理员选择列表：2.0.0/3.0.0 平台能力可见（verified/active/eligible）；
  - rollback 推荐指针切 2.0.0↔3.0.0（`recommendation_changed` 事件、幂等安全）；
  - deprecate 2.0.0（`lifecycle_changed→deprecated`）：新任务不可选、冻结被拒、
    历史冻结恢复装载通过（#13 A5）；
  - **平台能力首次真实装载并调用**：真实 Pi 任务 completed + `capability_python_table_summary`
    工具调用确认（机制门 `tool.completed`）；
- **阶段 5 revoke/跨用户/篡改演示**：
  - revoke 2.0.0（`lifecycle_changed→revoked`）：历史恢复装载被拒；
  - 跨用户拒绝：liyi111（真实普通用户）对 admin_gray 3.0.0 装载被拒（受众门）；
  - **篡改演示（blob 级备份安全原则）**：备份主体 manifest blob → 篡改 1 字节 → 装载
    409 fail-closed → 自动隔离（`eligibility_changed` actor=system）→ restore 复查链
    → 逐字节还原 → 独立 Layout 密码学复验通过 → 再次装载成功；演示后主 Layout 与
    发布证据完全一致；
- **阶段 4-5 暴露并修复 3 个真实缺陷**：
  1. 装载门签名验证只验 signed/<run_id> 副本，篡改主布局 blob 不被检测、自动隔离
     不触发 → `OciPlatformSignatureVerifier.verify` 先校验主布局 subject blob 内容
     哈希（sha256 == digest），失配即拒 + 触发自动隔离；
  2. 平台 restore 复查链误查个人验证表（validation_incomplete）→ 改查平台验证运行表
     （六步全绿 + 签名齐备）；
  3. 演示脚本复用固定幂等键会在「restore 后又自动隔离」时掩盖新状态 → 用唯一幂等键
     （含时间戳/序号），符合 #14「幂等键不能吞掉恢复后的新隔离」纪律；
- 新增测试 23 项（快照 purpose 回归 + 签名验证器 3 + restore 复查链 1 + 既有回归）；
  装载门/自动隔离/平台发布/签名验证器聚焦回归 92 passed；
- **阶段 6 真实 risk_accept + 重扫 + 零残留**（2026-08-19）：
  - 人工隔离 3.0.0 → `accept_pack_risk`（finding_ref 实引平台验证运行 `pfval_2d816c74…`，
    30 天）→ applied → 投影 eligible；
  - **惰性到期演示**：验收专用改写该事件 expires_at 为过去（改前记录、改后恢复）→
    投影重新 quarantined（零新事件，Q5A 惰性判定）；
  - restore（复查链全绿）→ eligible；expires_at 恢复原值；
  - **手动重扫（真实采集器：物化 + Trivy/Syft）**：PASSED 追加证据行 `supply_3402df2c…`
    （4 行，不覆盖旧行）→ `rescan_completed`；
  - **零残留核验**：Lease 表全 0、平台探针无残留、事件总数 32（risk_accepted=1、
    rescan_completed=1）、3.0.0 投影 verified/active/eligible；
  - 驱动脚本 `scripts/ac07_10_stage6_drive.py`（支持 `--verify-only` 重跑核验）；
- **阶段 6 暴露并修复 1 个真实缺陷**：`accept_pack_risk` 的 finding_ref 校验误查个人验证
  运行表（`get_validation_run` 只查个人表，平台 digest 永不匹配 → 任何平台包 risk_accept
  必被拒 finding_ref_unknown）→ 平台 scope 改从平台验证运行表取证（digest 匹配 + SUCCEEDED，
  与 restore 复查链同源）；回归测试 `test_platform_pack_requires_platform_run_ref` +
  `TestRiskAcceptCommand`/`TestRestoreCommand` 取证表同步为平台表；治理/平台聚焦回归
  141 passed；
- 该结论不代表普通用户受众扩大或整个 AC-07/Phase 4 完成。平台发布受众固定 admin_gray。
- 下一门：阶段 7（收口：Issue AC1-AC7 逐条对照 → 执行报告 → 文档同步 → 发布链）待授权。

2026-08-20，Phase 4 G1 30 项泛化集诊断尝试收口，正式验收未完成：

- 旧运行使用 fixture/objective/assertions/HEAD 四项弱冻结；双轴审查后驱动已改为冻结具体
  GoalContract、CandidateVerifier、断言、Runtime、驱动与 Git commit，并拒绝跨快照重放；
- 本地 Qwen 路线运行 25 项：CSV/XLSX/DOCX/复合/模糊共 19 个候选预检 PASS，PDF 与候选
  证据共 6 项 FAIL；P2-P7 为 NOT_RUN。该驱动未进入正式 Delivery 发布与 QA；
- P1 本地三次失败于低质量内容单元与必需字段证明缺口；外部 Qwen 3.7 Max、DeepSeek V4 Pro
  各 3 次 P1 对照均超过 900 秒，未形成可验证候选。该对照不等于 G4 安全端到端验收；
- 当前仅 5 项 `paraphrase`、7 项 `similar` + 1 项 `conflict`，不满足两个至少 11 项的构成门；
  集合已用于缺陷修复，不再是盲保留集；部分断言也只有结构/长度/行数强度；
- G1 发现并修复 XLSX locator、工具说明行 grounding、空结果完成提议三类契约缺陷；空结果
  确认严格只接受 JSON 布尔 `true`；
- #40 最终全仓后端回归 `1690 passed, 5 skipped`；前端生产构建、31/31 来源哈希 dry-run
  与 diff 检查通过；Standards/Spec 双轴复审均 PASS；
- 执行报告：`docs/plans/2026-08-20-g1-generalization-execution-report.md`。测试通过不等于
  G1、Phase 4、用户验收或生产资格通过。
- #40 本地机械链：新增隔离评测 Repository 下的正式 Delivery 资格读取；当前驱动要求
  `DeliveryPublisher.publish`、持久化 `DeliveryManifest`/`output_id`、独立 QA、文件大小与
  SHA-256 全部通过才记 PASS；外部 Provider 必须传递冻结的用户确认且缺失时失败关闭；
  尚未运行新盲保留集。

2026-08-21，#40 G1 v3 独立正式运行达到资格阈值：

- 提交 `8538481a`（v3 独立集）与 `83fe3f70`（二进制夹具字节规范）完成；正式运行绑定
  HEAD `83fe3f70122ad965c210196823536b12c3942932`、code-freeze
  `5e3737f69dbe8e57429e3cfeab72e79a69e1f49c7d37bc64482d7dbc0a4f70cf`；PR #41 已合并；
- Qwen3.8-27B（LAN 6013）运行至 12 个功能 Delivery 后中止：前 12 个均通过
  Verifier，但多次自修正，且 G103-F10 语义裁判连续发生 `max_tokens` 截断；证据已隔离，
  不与正式结果混算，也不能据此宣称该模型 G1 失败；
- 平台连接 `deepseek-v4-flash` 从第 1 题重新独立运行，预算为单次 2400 秒、每题最多 3 次
  重试；正式汇总为功能 30/31（96.8%）、安全 5/5（100%）、`qualified=true`；
- 唯一独立断言失败为 G103-F27「业务值或行序错误」；该候选已 `delivery_published` 且 QA
  通过，说明生产 Verifier 与独立业务 oracle 的双层门均真实生效；
- 正式结果 SHA-256 为
  `72cdd9ef6c142ddac541ab51b63865a9b8c0adb10ffc9ae93ee75e6ba9eab639`，独立断言报告
  SHA-256 为 `2e3af8eb833c71b31c20c2f0c511f7b167690f8fcccb3879ba136410c6b0d1a2`；
- DeepSeek 运行中出现过 Provider 临时连接失败、空/无效裁判和截断 JSON，均由失败关闭与
  重试恢复；这是稳定性证据，不影响本次冻结口径下的最终资格判定，也不等于 G4 完成。
- PR #41 已以 merge commit `936f1980` 合入 `main`；Issue #37、#40 由 PR 自动关闭。

2026-08-22，G2 Office、AC-05 生产迁移与用户验收通过：

- 提交 `31729495` 加固 Office 验证 CLI：Owner 与上传对象绑定、空值和不安全标识失败关闭、
  批次/任务/工作区隔离、1800 秒默认与 7200 秒硬上限、异常分类及宿主路径脱敏；新增 18 项
  定向测试，Standards/Spec 双轴审查未发现实现问题或范围扩张；
- 平台配置的本地 Qwen3.8-27B 对真实 Word 与 Excel 各连续运行三次，六次均形成单一约定格式
  候选，并通过源数据内容断言和独立 Verifier：Word 3/3、Excel 3/3；
- 全仓后端回归为 `1716 passed, 5 skipped, 2 failed`。两个失败分别是保留域名 DNS 无法解析
  和 G1 冻结快照拒绝当前 HEAD/元数据漂移，复跑稳定且不在 G2 两文件差异内；
- AC-05 提交 `235459a3` 新增唯一显式带备份迁移入口；Repository 缺失/畸形 Schema 失败关闭，
  SQLite 写锁覆盖备份与 DDL，迁移元数据绑定首次备份 SHA-256；38 项 AC-05 测试、65 项相邻
  回归和双轴复审通过；
- 生产恢复点 `data/backups/webui-before-ac05-20260822-072340.db` 完整且 SHA-256 为
  `669a13e24cbf6877e5c16bf41e532304a51b14e45742ef1162f7aa0957310b9e`；迁移前后与恢复点
  的 63 张既有表逻辑指纹一致，同路径重放不改写恢复点；
- 恢复副本上的幂等、请求身份、Owner 权限、取消和终态 CAS 全部通过；真实 Docker 获取、
  冻结挂载、取消、重试和崩溃恢复通过且残留为 0；8088 重启健康，G2 验收通过。PR #44 已
  合并，Issue #38 已关闭；Phase 4 仍未完成。

2026-08-22，G3 GateSnapshot 与 P0 回退完成本地工程门：

- 新增不可变 GateSnapshot、累计 Gate 有效资格、对比审计、独立 Approval 与 Rollout 状态机；
  删除历史硬门、P0 失败或快照漂移均失败关闭，合格快照不会自动解除回退；
- 新任务与所有 Revision 路径先预览、完成权限/契约/外发校验，再在同一 SQLite 事务冻结
  semantic task/revision、活动指针、Runtime assignment 与 RuntimeTaskConfig；重复、并发、
  锁超时和中途异常不会留下半状态；
- Delivery Publisher 在 G3 激活后读取中央 P0 状态，P0 下拒绝新正式发布，并已验证既有
  Delivery 数据库行和文件字节零改写；
- 显式迁移要求一致性备份、Schema/DDL digest、首次备份 SHA-256 与逻辑指纹，迁移前保持
  Legacy 路径，迁移后畸形对象失败关闭；相关 API/Publisher/Steering/Workspace 回归
  `88 passed`，Agentic Runtime 与原子故障聚焦回归 `79 passed`，Standards/Spec 终审均
  无仍可复现问题；排除已确认的 G1 冻结身份漂移与测试域名 DNS 两项基线后，全仓后端
  `1762 passed, 5 skipped, 2 deselected`；
- G3 工程代码已完成本地提交，生产 `data/webui.db` 已执行带备份显式迁移；恢复点为
  `data/backups/webui-before-g3-20260822-133901.db`，SHA-256 为
  `b552af5ac08bec3ba4421462cd51f73cf4b783520646ad285489ed9dd413bd7f`。生产库和恢复点
  `integrity_check=ok`，旧业务逻辑指纹一致，16 个 G3 对象、Schema digest、备份绑定与幂等
  回放通过；初始状态保持 `admin_gray`、`p0_blocked=0`，8088 `/api/health` 返回 200；
- 首次生产 GateSnapshot `abfc951ee4a99f282484f46c8ffe0c48f36e4c924fd2d765cacc3dd7de992b38`
  绑定 G3 提交 `21dbf11b3b6d72702fdaf95c4cd321299e19b8fe` 与环境摘要
  `06e11542ebaf1d4b49477cd32ad92f7c802223358c34dd8c2dd6189b2ccf43c3`；该快照最初记录的六项
  硬门如下，现只保留为不可变历史：

  | GateCheck | evidence_hash |
  |---|---|
  | `g1-formal-delivery-quality` | `cda84b4547404595f332a9f36c649751acbccf5f1b78e34a3e1adc7f3df61708` |
  | `g1-safety-permission-isolation` | `a99ef24b5b08c244dff2c5582b6fa5cb9918ae74c81a4a6e5c9624d1e01bd5da` |
  | `g2-office-pg05` | `d7735bafb5b69a0d1c1fbb489ce44eee36451aa5238ac14c9f11e9e48456da9f` |
  | `g2-ac05-production-readiness` | `42a9b1d7b45453b87c2ef16b8ea85e8b068526cabaddfca090dcb35416c3da50` |
  | `g3-runtime-routing-regression` | `7c7d812e678b5e6791a520af35fdd774951f949df12b2faf7639ff8e22437a00` |
  | `g3-production-migration` | `c18fd9afbff662525626a69fb4d6082852be5bd355b748c882a950f783471e7d` |

- 随后核验确认 8088 进程启动时间 `2026-08-22T10:52:02-07:00` 早于 G3 提交时间
  `2026-08-22T13:33:57-07:00`，不能证明运行进程已加载该提交。系统追加修正快照
  `39c168fb4009478fcd731dbe1f5f10d05d8685b5721cbe7bc5302eddc1ab9fa8`，把
  `g3-runtime-routing-regression` 标记为失败（证据
  `e8e530d373f4afd519ef41774ccb224f82acbb18bdae0fdd4fc9809d5133a778`），并自动进入
  `legacy_rollback`、`p0_blocked=true`；
- 独立 Approval `approval-explicit-opt-in-abfc951ee4a99f28` 已由超级管理员
  `u_9505fd620899` 本人身份记录，但它绑定历史快照 `abfc951e...`，不能用于当前活动快照；
- 后端在 G3 验收时已由标准 supervisor 重启，启动时间晚于 G3 提交，`src/` 与
  `21dbf11b` 一致、项目 supervisor 收敛为 1 个且 `/api/health` 返回 200。新活动快照
  `a936510e53eebc2abb04ce984e1fb72821730d0dc1ce9d37760d2c85beec3571` 的六项硬门均
  通过且累计有效合格；更新后的运行态证据为
  `1eb872e625ca495f07ac476cce8487aa23116b5d9dbf0b18aa20bbaf60b36316`，环境摘要为
  `a955b26cf8a23afcd14066c11e7254c53acd942a411d6fa9f5a5e03c16d0e882`；
- 恢复 Approval `approval-admin-gray-recovery-a936510e53eebc2a` 已由超级管理员
  `u_9505fd620899` 本人身份记录并绑定当前快照。按失败恢复规则，合格快照不会自动解除回退；
  经独立授权已从 `legacy_rollback` 原子恢复为 `admin_gray`、`p0_blocked=false`，审计事件
  绑定当前快照；旧业务逻辑指纹一致，24 条正式 Delivery、7 条 Legacy Delivery 和
  Runtime assignment 0 条均未改变；
- `admin_gray` 技术烟测通过：管理员显式 Pi 选择为 Pi，普通用户请求 Pi 和默认请求均为
  Legacy，跨 Owner 失败关闭；8088 `/`、`/api/health`、`/openapi.json` 均返回 200，烟测未
  创建任务或业务数据。`admin_gray` 恢复验收通过；该结论不等于 vNext 默认切换验收；
- G3 工程代码已通过 PR #44 合入，Issue #39 因默认切换尚未执行继续保持 OPEN。
- `explicit_opt_in` 切换前曾核验出缺少“获准用户”资格门，并创建修复工单 #43。ADR-0030
  已按“可用后所有用户默认使用”的产品决定取消该中间阶段：`admin_gray` 在累计硬门合格且
  获得独立授权后可直接进入 `vnext_default`；历史 `explicit_opt_in` 新任务失败关闭到 Legacy，
  且只能恢复 `admin_gray`。路由与 API 接缝测试已通过，PR #44 已合并，Issue #43 已关闭；
  生产仍保持 `admin_gray`，G4 合格前不得扩大受众。
- G4 执行范围限定为平台共享 DeepSeek 与百炼连接，外发仅限冻结的纯合成数据。生产库脱敏
  核验确认两条连接均为 `platform_shared`、已绑定 Secret 且状态 `verified`；该状态只证明连接
  已配置，不等于 G4 合格。

## 7. 当前优先顺序

1. **G1 已合入并关闭**：PR #41 已合入 `main`，Issue #37/#40 已关闭；v3 正式结果功能
   96.8%、安全 100%，资格 PASS。
2. **G2 已关闭、G3 `admin_gray` 恢复验收已通过**：PR #44 已合并并关闭 #38/#43；正式
   默认切换仍由 G4 完整合格门阻断，Issue #39 保持 OPEN，生产不得提前扩大。
3. **G4 部分通过、G5 本机前置通过**：传输矩阵与 DeepSeek/百炼 Pi 链均通过；Vault 轮换
   证据缺失，因此 G4 最终汇总和 G3 默认切换仍被阻断。G5 已通过干净 Linux 镜像、Compose、
   真实模型/浏览器、并发、故障、备份恢复与零残留本机前置包；路径可移植性代码门与本机
   跨根回归完成，但目标 Linux 跨系统复验和服务器 8B-2 未执行，#36 仍保持 OPEN。

## 8. 权威证据

- AC-07 规格：`docs/plans/2026-08-06-agentic-capability-ac07-spec.md`
- AC-07 ADR：`docs/adr/0029-capability-validation-lifecycle-and-platform-publication.md`
- #10 需求复核：`docs/plans/2026-08-14-agentic-capability-ac07-05-requirements-review.md`
- #10 设计：`docs/plans/2026-08-14-agentic-capability-ac07-05-design.md`
- #11 需求复核：`docs/plans/2026-08-14-agentic-capability-ac07-06-requirements-review.md`
- #11 设计：`docs/plans/2026-08-14-agentic-capability-ac07-06-design.md`
- #11 任务拆分：`docs/plans/2026-08-14-agentic-capability-ac07-06-task-breakdown.md`
- #12 需求复核：`docs/plans/2026-08-14-agentic-capability-ac07-07-requirements-review.md`
- #12 设计：`docs/plans/2026-08-14-agentic-capability-ac07-07-design.md`
- #12 任务拆分：`docs/plans/2026-08-14-agentic-capability-ac07-07-task-breakdown.md`
- #13 需求复核：`docs/plans/2026-08-14-agentic-capability-ac07-08-requirements-review.md`
- #13 设计：`docs/plans/2026-08-14-agentic-capability-ac07-08-design.md`
- #13 任务拆分：`docs/plans/2026-08-14-agentic-capability-ac07-08-task-breakdown.md`
- #14 需求复核：`docs/plans/2026-08-16-agentic-capability-ac07-09-requirements-review.md`
- #14 设计：`docs/plans/2026-08-16-agentic-capability-ac07-09-design.md`
- #14 验收方案：`docs/plans/2026-08-16-agentic-capability-ac07-09-acceptance-plan.md`
- #15 设计：`docs/plans/2026-08-16-agentic-capability-ac07-10-design.md`
- #15 任务拆分：`docs/plans/2026-08-16-agentic-capability-ac07-10-task-breakdown.md`
- #15 验收方案：`docs/plans/2026-08-16-agentic-capability-ac07-10-acceptance-plan.md`
- #34 报告：`docs/plans/2026-08-07-agentic-capability-ac07-02-execution-report.md`
- #35 报告：`docs/plans/2026-08-07-agentic-capability-ac07-03-execution-report.md`
- vNext Publisher：`docs/plans/2026-08-04-vnext-delivery-publisher-execution-report.md`
- Runtime 可靠性：`docs/plans/2026-08-06-v008-runtime-reliability-and-candidate-retry-closeout.md`
- G5 本机前置报告：`docs/plans/2026-08-23-g5-local-prerequisite-execution-report.md`

状态改变时先更新本文件，再更新精简 `handoff.md`；不要把滚动状态复制回 README、CONTEXT 或 ADR。
