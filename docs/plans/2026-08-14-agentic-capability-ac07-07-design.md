# AC-07-07 独立平台快照、签名与 admin_gray 发布 领域/接口设计

> 日期：2026-08-14
>
> 对应工单：GitHub Issue #12（`Eclipseic1848/Mangrove_ai`，`[AC07-07]`）
>
> 状态：`design_confirmed`（用户于 2026-08-14 确认）
>
> 前置：`2026-08-14-agentic-capability-ac07-07-requirements-review.md`（需求复核，已确认，
> 含 Q1–Q7 推荐答案）
>
> 边界：本文只记录 #12 的设计决策，不授权实现、迁移、提交或发布；这些动作按后续阶段
> 分别取得授权。

## Problem Statement

管理员需要把 `verified/active/eligible` 个人能力复制为不依赖 Owner 的脱敏平台快照，
重新 digest、重新验证、Cosign 签名后发布到 `admin_gray`。现状：个人能力的 OCI 制品是
单 payload 归档（manifest + 代码），catalog 拒绝 PLATFORM 登记（"平台能力包只能由发布
治理流程写入"），签名事务（#9）与供应链扫描（#35）可复用，但平台验证证据结构、脱敏
重打包、发布命令与受众投影全部不存在。

## Solution

发布 = 一条有界治理命令链，全部挂在 `CapabilityGovernance` 主 Seam 内：

```text
管理员 publish_platform_candidate(actor, pack_ref, reason, idempotency_key)
  ① 候选门：verified/active/eligible + PERSONAL
  ② 脱敏重打包：materialize 个人 payload → 白名单重写 manifest → 新 tar
  ③ push 平台 Layout → 平台新 digest（platform:{pack_id}:{version}:{sanitized-digest}）
  ④ 平台验证六步（Smoke/fail_closed/Trivy/Syft/mount_probe/independent_verifier）
  ⑤ OciSigningTransaction.execute（平台 Layout）→ 签名证据 + 公钥重验
  ⑥ 发布命令 publish_platform(actor, candidate, reason, 幂等键, 预期状态)
     → platform_published 事件 → 投影 audience=admin_gray
     → 发布 Adapter 写 catalog PLATFORM pack（唯一写入口）
```

候选提交与发布是两个命令（提交=准备事实，发布=生效）；发布要求候选全绿（六步+签名）。

## Implementation Decisions

### D1 事件与投影：`platform_candidate` / `platform_published` / `audience_changed`

- `CapabilityGovernanceEvent.event_type` 扩展
  `"registered" | "promoted_to_verified" | "audit_viewed" | "platform_candidate" | "platform_published" | "audience_changed"`。
- 新增字段（发布类事件专用，其余类型必须为 None，validator 分支校验）：
  - `source_digest: str | None`（个人来源 digest，pattern sha256:）
  - `platform_digest: str | None`（平台快照 digest）
  - `audience: Literal["admin_gray", "users"] | None`
- `platform_candidate`：maturity=verified、lifecycle=active、eligibility=eligible、
  `source_digest`/`platform_digest` 必填、audience 必须 None、原因必填（reason 复用）。
- `platform_published`：同上 + `audience="admin_gray"` 固定（#12 唯一合法值）、
  必填签名引用（复用 source_validation_run_id 存平台验证运行 id、
  source_supply_chain_evidence_id 存签名证据 id？→ 改为专用字段 `platform_validation_run_id` 与
  `signing_evidence_ref`，不混用 #10 语义）。简化：`platform_validation_run_id` 必填。
- `audience_changed`：新 audience 必填、原因必填；#12 只实现命令与投影，产品无入口。
- 投影：platform pack 的治理投影扩展 `audience`（事件流 events[-1] 计算；
  legacy_compat 来源默认 `admin_gray`——与 AC-06 历史灰度包语义一致）。

### D2 脱敏重打包（平台快照生成器）

新建 `src/capability_governance/platform_snapshot.py`：

- `PlatformSnapshotGenerator`：
  - `generate(source_pack, source_layout, platform_layout) -> PlatformSnapshot`
    1. `OrasOciLayoutStore.materialize` 按个人 digest 拉取 payload（复用安全解包）；
    2. 读 manifest.json（`CapabilityRuntimeManifest` 校验通过后白名单重写）：
       保留 `schema_version/name/version/kind/entrypoint/healthcheck/skill_path/permissions`；
       删除 `purpose/connection_ref/secret_ref`；其余文件保持字节不变；
    3. 重新打包 `mangrove-capability.tar`（成员名规范化，时间戳归一，保证同源重打包
       deterministic）；打包后新 payload push 平台 Layout → `PlatformSnapshot`
       （pack_id/version/source_digest/platform_digest/manifest 摘要）。
- 确定性要求：同一来源重复生成得到同一平台 digest（tar 成员排序 + 固定时间戳），
  这是 AC5"重复调用不产生重复平台版本"的第一道保证。
- 平台 Layout 新配置：`capability_platform_oci_layout_path`
  （默认 `data/capabilities/oci-platform`）。

### D3 平台验证运行（六步）

新建 `PlatformValidationRun`（models.py）：

- `run_id/actor_id/actor_role/idempotency_key/snapshot(target: platform CapabilityGovernanceTarget)/status/evidence/created_at/updated_at`。
- 六步枚举 `PlatformValidationStep`：
  `synthetic_smoke / fail_closed / trivy / syft / mount_probe / independent_verifier`。
- 执行器协议 `PlatformValidationExecutor`（协议 + 真实实现
  `LockedPlatformValidationExecutor`）：
  - `synthetic_smoke`：快照目录内执行 manifest healthcheck/合成调用（复用 #34 Smoke 执行器）。
  - `fail_closed`：权限/失败关闭复核（复用 #34 的 fail_closed 检查器）。
  - `trivy`/`syft`：`CapabilitySupplyChainEvidenceService.collect(target=平台目标, subject_root=快照目录)`，
    证据绑定平台 digest（Q7）。
  - `mount_probe`：Capability Host 内装载快照并执行一次确定性调用（Q4）：
    Protocol 抽象 + #12 实现目录级装载结构探针（`platform_executors.py`，
    物化可解包/入口结构完整/确定性 hash）；真实 Capability Host 执行探针留待
    #15/#16 纵切面（需求复核 Q4 已标注确认）。
  - `independent_verifier`：对 Smoke/mount_probe 输出 hash 与声明一致性的独立复核
    （不调用业务模型）。
- `PlatformValidationRun` 全部六步 `passed` 才满足发布前置。

### D4 签名复用（#9 事务直用）

- `OciSigningTransaction` 零改动。发布流程构造
  `OciSigningRequest(source_layout=平台 Layout, source_reference=平台 digest, ...)`，
  私钥/公钥路径沿用 #9 的工具锁与配置。签名证据 `OciSigningEvidence`
  （subject_digest/signature_digest/public_key_sha256/referrer_digests）入发布事件。

### D5 服务层命令（CapabilityGovernance 主 Seam）

- `submit_platform_candidate(actor, pack_ref, *, reason, idempotency_key) -> PlatformCandidateOutcome`：
  - 管理员门；候选门（三轴 verified/active/eligible + scope=PERSONAL + 个人治理投影）；
  - 幂等（同键返回同一候选）；生成快照（D2）→ 平台验证（D3 全绿）→ 签名（D4）；
  - 写 `platform_candidate` 事件；候选不改变个人能力与平台目录。
- `publish_platform(actor, pack_ref, *, reason, idempotency_key) -> PublishOutcome`：
  - 管理员门；预期状态（存在全绿候选）；幂等键 `publish:{platform_digest}`；
  - 写 `platform_published` 事件（audience=admin_gray 固定）→ 投影生效；
  - 发布 Adapter 写 catalog：`SqliteCapabilityCatalogRepository.save_pack(PLATFORM pack)`
    （唯一写入口，digest=平台 digest，maturity=VERIFIED，audience 不入 catalog 由治理投影权威）。
- `change_audience(actor, pack_ref, *, audience, reason, idempotency_key) -> AudienceOutcome`：
  - 管理员门；约束检查（签名有效、扫描无硬门、三轴 verified/active/eligible）；
  - 写 `audience_changed` 事件；**无 HTTP 端点、无前端入口**（Q5），
    测试证明服务层命令存在但产品 Interface 不暴露。
- 三轴投影过滤扩展：`audit_viewed` 之外的发布类事件按 events[-1] 参与投影
  （platform_published 携带 verified/active/eligible + audience）。

### D6 Repository（InMemory + SQLite 双实现）

- `CapabilityGovernanceRepository` 新增：
  - `save_platform_event(event)`（专用入口，只接受 platform_candidate/platform_published/audience_changed）；
  - `get_latest_platform_event(target, event_type)`；
  - `list_platform_events(target)`；
  - `create_platform_validation_run / get_platform_validation_run /
    list_platform_validation_runs / save_platform_validation_run`（复用验证运行的
    payload_json 模式，独立表）。
- 通用 `save_event` 保持"只接受 registered"。

### D7 迁移 0005

`src/capability_governance/migrations/0005_platform_publication.sql` 纯新增：

- `CREATE TABLE IF NOT EXISTS capability_platform_validation_runs (...)`
  （run_id/pack_id/version/digest/status/payload_json/created_at/updated_at +
  幂等索引）；
- `capability_governance_events` 零 ALTER（新字段全在 payload_json，沿用 #11 模式）。
- 沿用"先一致性备份、纯新增、可重放、旧数据零改写"迁移流程；生产执行单独授权。

### D8 HTTP 管理员路由组（挂 #11 的 admin_router）

- `POST /admin/platform-candidates`：提交候选（body: pack_id/version/digest/reason，
  Idempotency-Key；**异步执行**：返回 202 + run_id 或同步？→ 同步但重验证/签名耗时
  长——设计为同步命令返回 outcome，验证由 worker 触发，见 D9）。
- `GET /admin/platform-candidates`：候选列表（脱敏摘要：来源 pack、平台 digest、
  验证六步状态、签名状态）。
- `POST /admin/platform-publish`：发布（body: pack_id/version/platform_digest/reason，
  Idempotency-Key，预期状态=候选全绿）。
- 受众变更**无端点**（Q5）。
- 前端：审核视图补"平台候选"分组与发布按钮（#11 留下的分组缺口，Q6 管理员代提交）。

### D9 验证与签名的执行时点

- `submit_platform_candidate` 同步完成 D2（重打包）+ 写候选事件（状态=preparing）；
  六步验证与签名由既有 `CapabilityValidationManager` 风格的后台 worker 执行
  （新 `PlatformValidationManager`，run_once 轮询候选：未完成六步的继续执行，
  六步全绿后触发签名，签名证据写回候选事件/运行记录）。
- 幂等与 Lease 复用 #34 模式（platform run 的 digest Lease 防并发重复执行）。
- 发布命令只接受六步全绿 + 签名证据存在的候选（预期状态检查，否则 409/422）。

### D10 明确不改的

- `OciSigningTransaction`/`LockedOciSigningToolchain`/`OrasOciLayoutStore` 零改动。
- 个人能力验证、晋级门（#10）、审计查看（#11）零改动。
- CapabilityMountResolver 运行时门（#13）不实现；签名/受众在 #13 前无运行时强制。
- AC-06 历史灰度包不重新发布；真实纵切面（#15/#16）不执行。
- 私钥生成/轮换零改动（沿用 #9 配置）。

## Testing Decisions

- 服务层（InMemory 双实现）：候选门全矩阵（非 verified/非 personal/缺证据拒绝）；
  发布幂等（同键同平台版本、重复发布不产生第二个平台 pack）；预期状态冲突；
  受众固定 admin_gray；审计事件不可变；普通用户/非管理员拒绝。
- 快照生成器：同源重复生成同 digest（确定性）；白名单字段删除
  （purpose/connection_ref/secret_ref 不出现）；tar 安全约束（无链接/越界）。
- 平台验证：六步各自失败保持候选不发布；trivy/syft 证据绑定平台 digest；
  mount_probe 失败关闭零残留（真实 Docker 探针测试）。
- 签名：复用 #9 冻结夹具对平台 digest 走完整签名+公钥重验；错误公钥拒绝。
- HTTP：权限矩阵、脱敏断言、幂等键、404/409/422；受众变更无端点（404 断言）。
- 前端 Playwright：平台候选分组、发布按钮、原因必填、状态文本；普通用户无入口。
- 冻结夹具：Python Tool 与 MCP 双夹具（个人包 → 快照 → 验证 → 签名 → 发布）
  双向成功/失败覆盖；断言数据来自冻结夹具，不来自实现自身摘要。

## Out of Scope

- 运行时强制门（#13）；弃用/回滚/撤销/风险接受（#14）；真实纵切面（#15/#16）；
  AC-06 切换（#17）。
- 普通用户受众的实际开放执行（AC7 只实现命令）。
- 推荐指针机制、自动化方案库（AC-09）。
- 私钥生命周期管理。
