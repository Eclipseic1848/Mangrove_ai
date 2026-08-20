# AC07-11（#16）Everything MCP 真实治理纵切面 — 执行报告

> 状态：阶段 0-6 已真实完成；阶段 7（收口/发布链）进行中
>
> 依据：`Eclipseic1848/Mangrove_ai#16`（AC07-11）；复用 #15 机制与踩坑经验
>
> 证据位置：生产库 `data/webui.db`、驱动脚本 `scripts/ac07_11_*.py`、聚焦测试套件

## 1. AC1-AC7 逐条对照

### AC1：真实 Everything MCP 与本地真实检索任务，不用全 Mock 或绕过 Capability Host ✅

| 要求 | 证据 |
| --- | --- |
| 真实 Everything MCP | `@modelcontextprotocol/server-everything@2026.7.4`（13 工具，stdio）真实运行于 Sidecar；协议时代探测 legacy+modern 双支持 |
| 真实任务 | 真实 Pi 任务（阶段 2/4/5 并行版本共 3 条）真实调用 MCP echo 工具；`agentic_runtime_events` 的 `tool.completed` 机制门证明 |
| 不绕过 Capability Host | 全部调用走 Host `/invoke`（docker exec + node fetch → Sidecar 内真实 MCP 调用） |

### AC2：个人草稿、三类验证、Trivy、Syft、verified、独立平台快照、签名和 admin_gray 发布 ✅

| 要求 | 证据 |
| --- | --- |
| 个人草稿 | 2026.7.4（registered 05:50）+ 2026.8.19 并行版本（registered 09:49） |
| 三类验证 | ① 合成 Smoke：Smoke 步骤真实 MCP echo 调用（执行器增量）；② 真实任务重放：真实 Pi 任务 owner_task_replay；③ 协议生命周期：握手/健康/调用（Smoke 隐式）+ 超时/取消/进程异常（阶段 6 纵深） |
| Trivy/Syft | 供应链证据 5 行全 passed（trivy 0.70.0、DB 2026-08-17、syft/cyclonedx SHA-256 记录） |
| verified | `promoted_to_verified`×2（2026.7.4 @ 06:30、2026.8.19 @ 09:53） |
| 独立平台快照 | 2026.7.4 → `87741d37f6c…`；2026.8.19 → `c2fcb0cd37…`（新 digest） |
| 签名 | Cosign（公钥 `103de227…`）签名事务×2；独立 Layout 复验 PASS |
| admin_gray 发布 | `platform_published`×2 |

### AC3：legacy/modern stdio 协议、握手、健康检查、调用、超时、取消、恢复和进程异常失败关闭 ✅

| 要求 | 证据 |
| --- | --- |
| legacy/modern | 协议时代探测双支持；Pi 侧生产路径 legacy（Python SDK）+ Node Sidecar 内 legacy+modern（`versionNegotiation: auto`） |
| 握手/健康检查 | Host 启动探活（`/health` + token）+ initialize/list_tools 握手（Smoke 通过隐含） |
| 调用 | 真实 echo 调用返回 `Echo: …`（阶段 2/4/5） |
| **超时** | trigger-long-running-operation(duration=120) → 32.2s ≈ timeout_seconds=30 → 失败关闭（阶段 6） |
| **取消** | 客户端中断 → 调用未挂起（会话失败关闭，阶段 6） |
| **进程异常** | kill MCP 子进程 → 后续调用 rc=1 fail-closed（阶段 6） |
| **恢复** | 篡改演示后 restore 复查链 + 逐字节还原 + 复验 + 再次装载成功（阶段 5） |

### AC4：一个任务的多个能力仍共用单 Capability Host Sidecar，不新增每工具独立容器 ✅

| 要求 | 证据 |
| --- | --- |
| 单 Sidecar 多能力 | 阶段 4：单任务冻结 python-table@3.0.0 + everything-mcp@2026.7.4 → 单 Sidecar 装载双能力 → 双工具真实成功调用（`capability_python_table_summary` + `capability_everything_mcp`）→ 任务结束容器 0 残留 |

### AC5：并行版本、历史冻结、回滚、弃用、隔离、撤销、签名/digest 篡改和跨 Owner 拒绝 ✅

| 要求 | 证据 |
| --- | --- |
| 并行版本 | 2026.8.19 构造（基线 2026.7.4 + version 字段，新归档 `331792a0…`）与 2026.7.4 并行 |
| 历史冻结/回滚 | #15 已真实覆盖（python-table 2.0.0↔3.0.0 推荐指针、deprecated 冻结恢复装载）；本票不重复 |
| 弃用 | `lifecycle_changed→deprecated` 2026.8.19（牺牲版本） |
| 隔离 | `eligibility_changed` actor=system（篡改自动隔离） |
| 撤销 | `lifecycle_changed→revoked` 2026.8.19 |
| 签名/digest 篡改 | 篡改主体 blob 1 字节 → 409 fail-closed + 自动隔离 → restore → 还原 → 复验 → 装载成功 |
| 跨 Owner 拒绝 | liyi111（真实普通用户）对 everything-mcp admin_gray 装载被拒 |

### AC6：取消、超时、宿主服务异常和服务重启后不残留 MCP 子进程、容器、网络、挂载或 Lease ✅

| 要求 | 证据 |
| --- | --- |
| 取消/超时 | 阶段 6 演示后容器内 node 进程数不变（无 MCP 子进程残留） |
| 宿主服务异常 | kill 子进程 + Host 失败关闭后无残留 |
| 服务重启 | 8088 热更新重启（`logs/dev_reload.log`）；幂等键唯一约束保证不重复写 |
| 零残留 | `mangrove-cap-host-*` 容器 0、`capability_validation_leases`/`capability_platform_validation_leases` 全 0、演示网络已删、物化临时目录已清 |

### AC7：记录调用结果、进度、资源、扫描、SBOM、签名和治理审计的真实证据 ✅

| 要求 | 证据 |
| --- | --- |
| 调用结果/进度 | Pi 任务 completed×3、验证五步 evidence 全绿、`tool.completed` 事件 |
| 资源 | Sidecar 单容器（memory 2g / cpus 2 / pids-limit 128，host.py 配置）、演示耗时记录 |
| 扫描 | Trivy 0.70.0 / DB UpdatedAt 2026-08-17（5 行证据） |
| SBOM | syft_json_sha256 / cyclonedx_json_sha256 逐行记录 |
| 签名 | 独立 Layout 复验 PASS×2 + 篡改还原复验 PASS |
| 治理审计 | 14 条 everything-mcp 治理事件（registered×2、promoted_to_verified×2、platform_candidate/published×2、eligibility_changed×2、lifecycle_changed×2、audit_viewed×1） |

## 2. 阶段 0-6 执行摘要

| 阶段 | 内容 | 结果 |
| --- | --- | --- |
| 0 | 环境预检 + 协议探测 | node/docker/LLM 可用；legacy+modern 双协议；性能基线（lock 20s/cold 7.7s/hot 5.3s） |
| 1 | 登记个人 draft 2026.7.4 | ✅ registered（复用 OCI 归档，不重复 push） |
| 2 | 三类验证 + 供应链 + verified | ✅ 五步全绿（Smoke 真实 MCP echo）、`promoted_to_verified` |
| 3 | 平台发布 2026.7.4 | ✅ 快照 `87741d37f6c…`、六步 6/6、签名、发布、幂等重放、独立 Layout 复验 PASS |
| 4 | 单 Sidecar 双能力装载 | ✅ 双工具真实调用、单容器、0 残留 |
| 5 | 篡改 + 跨 Owner + 并行版本治理链 | ✅ 篡改→隔离→restore→还原→复验；liyi111 被拒；2026.8.19 并行（发布→deprecated→revoked 牺牲） |
| 6 | 协议纵深 + 零残留 | ✅ 超时 32.2s、取消、进程异常 fail-closed、Lease 0/容器 0/无 MCP 子进程残留 |

## 3. 暴露并修复的真实缺陷（#16 专属 5 个）

1. **同归档同 digest 的个人 draft 与平台 legacy 行歧义**（#16 缺陷 #1）：`list_visible_packs` 平台行排序在前 → `register_pack`/`request_validation`/`request_validation_for_task`/`list_validation_task_options` 解析歧义。修复：`resolve_pack` 新增 `prefer_owner` 参数 + `register_pack` 按 Owner 重查；驱动脚本显式按 Owner 解析。歧义窗口仅在发布前（同 digest 内容相同）；发布后平台行换新 digest 自然消失。
2. **Pi 运行时要求任务至少 1 个 source**（`PiRuntimeRequest.sources` 非空）→ 驱动加真实上传样例。
3. **Windows docker.exe 参数重拼接破坏内联 JS**（`\"` 反斜杠被吞 → node `[eval]:1` 语法错误）→ 修复：JS 脚本 base64 编码 + 参数经 `process.argv` 传递（容器侧原样还原）。
4. **everything-mcp 个人行与平台 legacy 行共用 OCI tag**：`--replace` 删 OCI tag 破坏候选物化（`ORAS 读取冻结制品失败`）→ 只删平台目录行、保留 OCI tag（#15 无此问题：平台行是独立新归档）。
5. **LLM 参数契约随机性**：`table_summary.py` 期望 `argv[1]` 为单个 JSON 字符串，双目标任务中 LLM 传多元素数组 → `json.loads` 失败 → objective 明确参数契约后双工具成功。

另有脚本层排障：CapabilityPack 导入位置（conversation_steering）、`os.makedirs` 预创建与 materialize 内部 mkdir 冲突、materialize 目标目录残留、断言逻辑（超时耗时≈timeout_seconds）。

## 4. 明确不做（边界）

- Python 侧 modern stdio Adapter（决策：不纳入本票，Node Sidecar 双支持已覆盖）；
- 远程 MCP / 普通用户开放 / AC-06 切换（#17）；
- 定时重扫调度器。

## 5. 代码与测试

- 新增脚本：`prepare_ac07_11_packs.py`、`ac07_11_validation_drive.py`、`ac07_11_publish_drive.py`、
  `ac07_11_verify_platform_signatures.py`、`ac07_11_stage4_drive.py`、`ac07_11_stage5_drive.py`、
  `ac07_11_stage5_parallel.py`、`ac07_11_stage6_drive.py`；
- 代码改动：`catalog.py`（prefer_owner）、`service.py`（register/validation 歧义修复）、
  `validation_runtime.py`（Smoke mcp_local 真实调用增量）；
- 回归测试 305 passed（治理/能力/平台全套）。
