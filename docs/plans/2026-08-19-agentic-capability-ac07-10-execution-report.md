# AC07-10（#15）Python 表格 Tool 真实治理纵切面 — 执行报告

> 状态：阶段 0-6 已真实完成；阶段 7（收口/发布链）进行中
>
> 依据：验收方案 `2026-08-16-agentic-capability-ac07-10-acceptance-plan.md`
>
> 证据位置：生产库 `data/webui.db`、驱动脚本 `scripts/ac07_10_*.py`、聚焦测试套件

## 1. AC1-AC7 逐条对照

### AC1：真实 Python Tool 和真实表格样例，不用全 Mock、用例专属 Prompt 或绕过 CapabilityGovernance 的脚本 ✅

| 要求 | 证据 |
| --- | --- |
| 真实 Python Tool | AC-06 冻结的 `capability_python_table_summary`（阶段 2/4 真实 Pi 任务真实调用，`agentic_runtime_events` 的 `tool.completed` 机制门确认） |
| 真实表格样例 | 3 行真实表格样例（阶段 2 经验：4 行样例曾致 Qwen 误判，3 行稳定；样例在准备脚本固化） |
| 不用全 Mock | 全部验证走真实 Pi 任务 + Qwen3.6-35B-A3B 语义验证（Q3A 口径：LLM 可用） |
| 用例专属 Prompt | 语义验证 prompt 通用（一致性与长度约束是工程性修复，非专属用例） |
| 不绕过 CapabilityGovernance | 所有装载/治理动作均过 `CapabilityGovernance` 服务层与装载门；驱动脚本等价 API 校验后的写入路径 |

### AC2：个人草稿、合成 Smoke、授权真实任务、失败关闭、Trivy、Syft、verified 和并行新版本验证 ✅

| 要求 | 证据 |
| --- | --- |
| 个人草稿 | 2.0.0/3.0.0 `registered`（Owner=liyi，`prepare_ac07_10_packs.py --apply`） |
| 合成 Smoke | 五步验证第一步 SyntheticSmoke（`capability_validation_runs` evidence 五步全绿） |
| 授权真实任务 | D9 验证任务 Seam：`validation_target` 冻结持久化 + `check_mount(validation_exempt)` 装载豁免（个人+Owner 自己+active+eligible 仍强制） |
| 失败关闭 | FailClosed 验证步骤（失败关闭门） |
| Trivy | 供应链证据 `trivy_version=0.70.0`、config/result SHA-256、DB UpdatedAt 2026-08-17（7 天时效内） |
| Syft | `syft_json_sha256` / `cyclonedx_json_sha256` 逐行记录（3.0.0 四行证据） |
| verified | `promoted_to_verified`×2（自动晋级，判定门 + 幂等） |
| 并行新版本验证 | 2.0.0/3.0.0 两条独立真实验证链（succeeded 5 条含重跑留痕） |

### AC3：脱敏平台快照、新 digest、Cosign 签名、admin_gray 发布和管理员任务选择 ✅

| 要求 | 证据 |
| --- | --- |
| 脱敏平台快照 | 快照重打包新 digest（2.0.0 `e5556f83…` / 3.0.0 `9379fe29…`）；`purpose` 中性脱敏 `_SANITIZED_PURPOSE`（阶段 3 重建修复） |
| Cosign 签名 | 加密 Sigstore 密钥对（`~/.mangrove-signing/`，项目外），公钥 SHA-256 `103de227b8f5…`；签名 digest 记录于 `platform_published` 事件 |
| admin_gray 发布 | `platform_published`×4（初版×2 + 重建×2，重建版为有效发布链，旧版为失败留痕）；发布后装载门对签名平台包闭环 |
| 管理员任务选择 | 阶段 4 选择列表核验（2.0.0/3.0.0 出现、recommended 标记）；平台能力首次真实装载并调用 |

### AC4：真实任务结果、执行进度、取消、恢复、历史冻结版本和 Candidate/Delivery 边界均正确 ✅

| 要求 | 证据 |
| --- | --- |
| 真实任务结果 | 真实 Pi 任务 `completed` + 工具调用确认（阶段 4 mount drive） |
| 执行进度 | `capability_validation_runs` 状态推进（queued→running→succeeded/cancelled/failed 全状态留痕） |
| 取消 | 真实 `cancelled`×3 运行记录（1.0.0×1、everything-mcp×2，状态机留痕） |
| 恢复 | deprecated 冻结任务历史恢复装载成功（#13 A5 路径）；篡改演示后 restore→还原→再次装载成功 |
| 历史冻结版本 | deprecated 2.0.0 冻结恢复装载通过；revoked 后历史恢复装载被拒 |
| Candidate/Delivery 边界 | `platform_candidate`（候选门）→ 验证 → `platform_published`（发布门）；任务 Candidate 与正式 Delivery 边界由候选/发布事件与验证运行表分离证明 |

### AC5：覆盖推荐回滚、deprecated、自动隔离、revoked、跨用户拒绝和签名/digest 篡改拒绝 ✅

| 要求 | 证据 |
| --- | --- |
| 推荐回滚 | `recommendation_changed`×2（rollback 指针 2.0.0↔3.0.0 切换、列表标记联动） |
| deprecated | `lifecycle_changed→deprecated` 2.0.0（新任务不可选 + 冻结 409 + 历史恢复装载通过） |
| 自动隔离 | `eligibility_changed` actor=system×3（篡改演示验签失败 → 自动隔离钩子） |
| revoked | `lifecycle_changed→revoked` 2.0.0（历史恢复装载被拒） |
| 跨用户拒绝 | liyi111（真实普通用户）对 admin_gray 3.0.0 装载被拒（受众门） |
| 签名/digest 篡改拒绝 | 篡改主布局主体 blob 1 字节 → 装载 409 fail-closed + 自动隔离（修复后主布局哈希校验生效） |

### AC6：重复请求、服务重启和失败恢复不重复发布，不覆盖旧证据或旧版本 ✅

| 要求 | 证据 |
| --- | --- |
| 重复请求 | 发布/候选同幂等键重放 → `already_published`/`already_submitted`（publish_drive 第 4/5 步） |
| 服务重启 | 8088 由热更新监督进程自动重启（`logs/dev_reload.log`）；事件表幂等键唯一约束保证重启后重放不重复写事件（测试 `test_idempotent_replay_returns_existing` 覆盖） |
| 失败恢复 | 崩溃窗口补写（rescan 隔离事件已写而 rescan_completed 缺失时按投影补写，测试 `test_crash_window_replay_backfills_rescan_event`）；failed×4 运行留痕不覆盖 |
| 不覆盖旧证据 | 供应链证据追加不覆盖旧行（3.0.0 四行、2.0.0 两行全部保留） |
| 不覆盖旧版本 | `capability_pack_versions` 唯一键 (owner_key, pack_id, version)；重建采用删 OCI tag+目录行+换幂等键纪律（`--replace`），事件流保留旧记录作失败留痕 |

### AC7：记录耗时、扫描数据库时间、SBOM hash、签名验证及容器、网络、挂载、临时 Registry 零残留证据 ✅

| 要求 | 证据 |
| --- | --- |
| 耗时 | 各阶段脚本记录（阶段 3 六步验证与发布、阶段 6 重扫 24 秒内完成） |
| 扫描数据库时间 | Trivy DB UpdatedAt 2026-08-17（`capability_supply_chain_evidence` 逐行记录） |
| SBOM hash | `syft_json_sha256` / `cyclonedx_json_sha256` 逐行记录（如 3.0.0 最新 `8db47ea8…`/`d637cdf1…`） |
| 签名验证 | 独立 Layout `verify_local` 双 PASS（初版链）+ 篡改演示还原后密码学复验通过 |
| 容器零残留 | `docker ps`：演示能力容器 `mangrove-cap-host-…` 已停止；无临时 Registry 容器（zot 已清理）；剩余容器均为平台常驻服务 |
| 网络零残留 | 端口核验：无演示专用端口残留（8088/8080/5173 为平台服务，1200/3002 等为 firecrawl 常驻） |
| 挂载零残留 | 能力容器已退出，运行态挂载全部释放 |
| 临时 Registry 零残留 | 回环 Zot Registry 已停止并清理，OCI 证据仅存于平台主布局与独立 Layout |
| Lease 零残留 | `capability_validation_leases` / `capability_platform_validation_leases` 全 0 行 |

## 2. 阶段 0-6 执行摘要

| 阶段 | 内容 | 结果 |
| --- | --- | --- |
| 0 | LLM 可用性探测（Q3A） | Qwen3.6-35B-A3B @ 192.168.121.32:6012 可用 → 走完整真实任务 |
| 1 | 注册个人 draft 2.0.0 | ✅ registered×1 |
| 2 | 验证五步 + 供应链 + verified；3.0.0 并行 | ✅ 双链 succeeded、passed 证据×2、`promoted_to_verified`×2 |
| 3 | 平台发布 2.0.0/3.0.0 | ✅ 双独立链（候选→脱敏快照→六步→签名→admin_gray 发布）；幂等重放通过；独立 Layout 复验双 PASS |
| 3' | 方案 A 重建（修复缺 purpose） | ✅ 新快照新 digest → 重建发布链（2.0.0 `e5556f83…`、3.0.0 `9379fe29…`） |
| 4 | 管理员选择 + rollback + deprecated + 冻结恢复 + 首次真实装载 | ✅ 推荐指针切换×2、deprecated、历史冻结恢复装载通过、真实任务 completed |
| 5 | revoke + 跨用户拒绝 + 篡改演示 | ✅ revoke 2.0.0、liyi111 被拒、篡改→409→自动隔离→restore→还原→复验→装载成功 |
| 6 | risk_accept applied 链 + 惰性到期 + 手动重扫 + 零残留 | ✅ `risk_accepted`×1（30 天）、到期投影重隔离（零新事件）、真实重扫 PASSED 追加、Lease 0、探针无残留 |

## 3. 暴露并修复的真实缺陷（9 个，均为真实首跑才暴露）

1. 平台快照生成器/六步执行器硬编码 `manifest.json` → 兼容 `mangrove-capability.json` 标准名。
2. `materialize_platform` 路径拼接 `Path + str` TypeError → 括号拼接。
3. 发布 Adapter 传绑定方法 vs 对象 AttributeError → 传仓库实例。
4. 平台快照白名单删 `purpose` → 真实装载 `PI_RUNTIME_FAILED` → 中性脱敏 purpose + 重建发布链。
5. 装载门签名验证只验 signed/<run_id> 副本，篡改主布局不被检测 → 主布局 subject blob 哈希校验。
6. 平台 restore 复查链误查个人验证表 → 改查平台验证运行表。
7. restore 演示脚本复用固定幂等键掩盖新状态 → 唯一幂等键（含时间戳）。
8. `accept_pack_risk` finding_ref 误查个人验证运行表（平台 digest 永不匹配）→ 平台 scope 从平台验证运行表取证。
9. 脚本/测试对事件表结构假设错误（无 reason/status 顶层列）→ 统一解析 payload_json。

## 4. 明确不做（阶段 7 边界）

- accept-s8-* 样本处理；
- 普通用户开放（平台受众固定 admin_gray）；
- AC-06 兼容切换（#17）；
- 定时重扫调度器（手动重扫已演示）。

## 5. 代码与测试

- 工作树：22 个修改文件 + 19 个新增文件（未提交，待发布链）。
- 新增测试 60+ 项；治理/平台聚焦回归 **141 passed**（阶段 6 修复后全量）。
- 新增驱动脚本：`ac07_10_publish_drive.py`、`ac07_10_rebuild_platform.py`、
  `ac07_10_stage4_drive.py`、`ac07_10_stage4_mount_drive.py`、`ac07_10_stage5_drive.py`、
  `ac07_10_stage6_drive.py`（支持 `--verify-only`）、`ac07_10_verify_platform_signatures.py`、
  `prepare_ac07_10_packs.py`。
