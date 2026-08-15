# AC-07-07 独立平台快照、签名与 admin_gray 发布 任务拆分

> 日期：2026-08-14
>
> 对应工单：GitHub Issue #12（`Eclipseic1848/Mangrove_ai`，`[AC07-07]`）
>
> 状态：`breakdown_review`（等待用户确认后开始 S1）
>
> 前置：`2026-08-14-agentic-capability-ac07-07-requirements-review.md`（已确认）、
> `2026-08-14-agentic-capability-ac07-07-design.md`（已确认）
>
> 边界：本文是 #12 的执行切片计划；每个切片"先红灯、再最小实现、切片聚焦回归通过"后才进入
> 下一片，完成全部切片后统一走双轴审查与用户验收。

## 切片总览

| 切片 | 内容 | 验证标准（聚焦回归） | 主要文件 |
| --- | --- | --- | --- |
| S1 | 模型：发布类事件/受众/平台验证运行 | 事件 validator 分支、旧 payload 兼容 | `models.py`、`tests/test_capability_platform_publish.py` |
| S2 | Repository 双实现 + 迁移 0005 | 事件专用入口/平台运行表/幂等/可重放 | `repository.py`、`sqlite_repository.py`、`migrations/0005_*.sql` |
| S3 | 快照生成器（脱敏重打包） | 白名单重写/确定性 digest/安全解包约束 | `platform_snapshot.py`、`tests/test_platform_snapshot.py` |
| S4 | 平台验证六步执行器 | 每步证据/失败关闭/trivy-syft 绑定平台 digest | `platform_validation.py`、同测试 |
| S5 | 服务层三命令 + 投影过滤 | 候选门/发布幂等/预期状态/受众固定/权限 | `service.py`、同测试 |
| S6 | 签名集成 + worker | OciSigningTransaction 直用/Lease/签名证据写回 | `platform_validation_runtime.py`、`validation_runtime.py` |
| S7 | HTTP 路由 + 前端候选分组 | 权限矩阵/脱敏/幂等键/无受众端点；Playwright | `routes/capability_governance.py`、`CapabilityGovernancePanel.tsx`、e2e |
| S8 | 收尾回归 | Capability 全量 + 前端 build + Playwright 零回退 | 无新增 |

## 切片详情

### S1 模型

- 目标：`CapabilityGovernanceEvent.event_type` 增加 `platform_candidate` /
  `platform_published` / `audience_changed` 分支与
  `source_digest/platform_digest/audience` 字段；新增 `PlatformValidationRun`、
  `PlatformValidationStep`、`PlatformSnapshot`、`PlatformCandidateOutcome`、
  `PublishOutcome`、`AudienceOutcome` 模型；投影扩展 `audience`。
- 红灯：三分支 validator（必填/互斥/audience=admin_gray 固定）；旧 payload 反序列化；
  `CapabilityGovernanceProjection` 带 audience。
- 绿灯：最小字段与分支校验实现。

### S2 Repository 双实现 + 迁移 0005

- 目标：`save_platform_event`（只接受三类发布事件）、`get_latest_platform_event`、
  `list_platform_events`、平台验证运行 CRUD；0005 纯新增表 + 幂等索引。
- 红灯：InMemory/SQLite 幂等、串类型拒绝、迁移重放（两备份路径）、旧数据零改写。
- 绿灯：双实现。

### S3 快照生成器

- 目标：`PlatformSnapshotGenerator.generate`：materialize → 白名单重写 manifest →
  确定性重打包 → push 平台 Layout → `PlatformSnapshot`。
- 红灯：同源两次生成同 platform_digest；purpose/connection_ref/secret_ref 不出现；
  tar 无链接/越界成员；个人 Layout 零改动。
- 绿灯：实现（复用 OrasOciLayoutStore 两实例：个人读 + 平台写）。

### S4 平台验证执行器

- 目标：`PlatformValidationExecutor` 协议 + `LockedPlatformValidationExecutor`；
  六步证据（synthetic_smoke/fail_closed/trivy/syft/mount_probe/independent_verifier）；
  trivy/syft 调 `CapabilitySupplyChainEvidenceService.collect`（平台目标 + 快照目录）。
- 红灯：六步全 passed 才全绿；任一步失败记录 failed 证据；mount_probe Protocol 替身
  可注入（TDD 不依赖 Docker）；trivy 主体 digest 与平台 digest 一致断言。
- 绿灯：实现；真实 Docker 探针测试在 S6 一并验证。

### S5 服务层命令

- 目标：`submit_platform_candidate`（管理员门 + 候选门 + 幂等 + 候选事件）、
  `publish_platform`（预期状态=候选全绿 + 幂等 + 发布事件 + 发布 Adapter 写 catalog）、
  `change_audience`（约束检查 + 事件 + 无产品入口）；投影过滤与 audience 计算。
- 红灯：候选门全矩阵（非 verified/非 personal/draft 拒绝）；发布幂等（同键同平台版本、
  重复发布不产生第二个 pack）；预期状态冲突 409 语义；普通用户拒绝；audience 固定
  admin_gray；legacy_compat 投影 audience=admin_gray。
- 绿灯：服务层实现。

### S6 签名集成 + worker

- 目标：候选全绿后 `OciSigningTransaction.execute`（平台 Layout）；签名证据写回；
  `PlatformValidationManager` worker（run_once 轮询 + digest Lease + 幂等续跑）。
- 红灯：冻结夹具走完整签名+公钥重验；错误公钥拒绝；取消/重复执行失败关闭；
  真实 Docker mount_probe 探针（Python Tool 与 MCP 双夹具）。
- 绿灯：实现（签名零改动直用，新增编排）。

### S7 HTTP + 前端

- 目标：`POST /admin/platform-candidates`（202 + 幂等键）、
  `GET /admin/platform-candidates`（脱敏摘要）、`POST /admin/platform-publish`；
  前端审核视图"平台候选"分组 + 提交/发布按钮 + 原因必填；受众变更**无端点**（404 断言）。
- 红灯：权限矩阵/脱敏断言/404/409/422；Playwright 候选分组与发布流程；
  普通用户无入口；build 通过。
- 绿灯：路由与面板实现。

### S8 收尾回归

- 目标：Capability 全家族、后端全仓、前端 build、全量 Playwright 零回退；统计报告。
- 无新增代码；发现回归按切片归属回修。

## 执行纪律

- 每个切片先写失败测试，再做最小实现；切片聚焦回归通过后才进入下一片。
- 夹具数据来自测试内构造的冻结事实（个人包归档、manifest、平台 Layout），
  不来自实现自身摘要；真实 Docker 探针只出现在 S6 冻结夹具验证。
- 发布类事件不参与三轴投影污染（S5 一并验证）。
- 完成后按批次规则同步 md 文档；双轴审查与发布动作另行授权。

## 审查修复附录（2026-08-14 双轴审查后）

双轴审查（Standards + Spec）结论：3 阻断 + 多项高/中/低，已全部修复或如实标注：

| 编号 | 问题 | 处理 |
| --- | --- | --- |
| B1 | 生产接线缺失（快照生成器/发布 Adapter/worker 无装配，真实环境死链路） | `capability_governance_runtime.py` 装配双 Layout 生成器、目录级六步执行器、签名事务、发布 Adapter；`main.py` lifespan 启动平台 worker；`_governance()` 注入平台依赖；settings 加平台 Layout 与签名密钥配置 |
| B2 | 发布 fail-open（publisher 缺失静默跳过，事件与目录孤儿） | publisher 缺失抛 RuntimeError；先写目录（幂等）再写不可变事件，目录失败事件不落库 |
| B3 | AC7 change_audience 无约束检查 | 命令内重查最新绿色+签名运行存在，否则拒绝；补拒绝分支测试 |
| 高 | 候选列表缺六步/签名状态 | `PlatformCandidateSummary` 模型 + 服务层/HTTP/前端展示"验证 n/6 步 · 已签名/未签名" |
| 高 | mount_probe 真实执行探针 | 按用户确认留待 #15/#16；#12 实现目录级装载结构探针（真实代码非测试替身），需求复核 Q4 与设计 D3 已标注 |
| 中 | 发布幂等键竞态 | 服务层派生固定键 `publish:{platform_digest}`，调用方键不再决定发布事实 |
| 中 | environment/working_directory 未清洗 | 快照统一清空 environment、归一 working_directory（凭证类环境变量在来源模型层已禁止） |
| 中 | "原文件名"未编号化 | 按用户确认语义为业务来源文件名（快照本不存在）；能力结构成员名保留，文档已标注 |
| 中 | 平台验证 Lease 缺失 | 0005 加 Lease 表 + 双实现 acquire/release/renew + manager 每运行租约保护 |
| 低 | Protocol 类型/孤儿代码/字典标注/前端随机幂等键/schema_exists 表集合/signing_only 收紧 | 全部修复 |
