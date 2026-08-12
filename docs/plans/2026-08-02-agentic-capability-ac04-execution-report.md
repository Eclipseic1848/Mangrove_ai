# AC-04 CapabilityCatalog 执行报告

> 日期：2026-08-02
> 状态：`ac04_engineering_verified_pending_user_acceptance`
> 分支：`v0.0.8`
> 边界：本报告只证明 AC-04；后续 AC-05、AC-06 与 AC-07 #33 状态分别见对应执行报告。
> AC-06 目录迁移和 AC-07 #33 治理迁移均已在后续独立授权下完成；平台发布或弃用仍未实施

## 1. 已完成产物

- 新增 `src/capability_catalog/` 深 Module，调用者通过 `CapabilityCatalog` 查询和冻结选择，
  不感知内存/SQLite 表、Legacy Adapter 或 ORAS 命令；
- 建立 Pack、Component、Procedure、Validation 与 TaskRevision Selection 的不可变 Repository；
- 个人版本强制 Owner 隔离。普通用户、管理员和超级管理员都不能借治理角色读取其他 Owner
  的个人目录；平台目录只读展示已由后续发布流程产生的快照；
- 新登记 Pack/Procedure 只能是个人 `draft`。目录登记不能把个人记录直接标为 `verified`，
  也不能直接写平台记录；验证晋级、脱敏发布、签名、弃用和回滚仍属于 AC-07；
- TaskRevision 同时冻结 Pack 与 Procedure 的 ID、version、digest；任一引用变化都拒绝覆盖；
- 内置 `CapabilityManifest` 通过只读 Adapter 映射为平台内置版本，不写 Legacy 数据；
- Legacy Skill/模板只有显式导入动作，产物是 Owner 隔离个人草稿，不自动迁移或公开；
- 单机不可变制品复用 ORAS OCI Image Layout。数据库只保存 Owner、scope、maturity、产品元数据
  和冻结引用，不自行实现第二套内容寻址协议；
- ORAS 写入前按同一版本的 layer digest 失败关闭：相同内容直接复用既有 manifest，不再
  push；不同内容拒绝覆盖。进程内同一 Layout 写入串行，跨进程 Lease 留给 AC-05；
- 新增前向 SQL 迁移草案。SQLite Adapter 与迁移文件共用同一 DDL 来源，避免双份漂移。

## 2. 验证证据

### 2.1 自动化

- `pytest tests/test_capability_catalog.py tests/test_conversation_steering.py -q`：25 passed；
- 覆盖 Owner/管理员/超级管理员矩阵、跨 Owner 拒绝、并发幂等、不可变写、Pack/Procedure
  digest 冻结、Legacy 零改写、显式导入和 ORAS 命令适配；
- `python -m compileall -q src/capability_catalog src/conversation_steering`：通过；
- `git diff --check`：通过。

### 2.2 真实 ORAS PoC

- 本机安装 ORAS 1.3.0（WinGet 稳定包）；
- 在临时目录真实执行 `oras push --oci-layout-path ...`，随后读取 manifest descriptor；
- artifact type：`application/vnd.mangrove.capability.v1`；
- 冻结 digest：`sha256:7ea41407a362f0cc08062282bf623ec34e435b8856fac4e25bb812f2aad62c81`；
- 同一文件同一版本连续导入返回同一 descriptor，第二次不 push；产品引用为逻辑
  `oci-layout://local/...@sha256:...`，不包含宿主绝对路径；
- 测试只使用临时 OCI Layout，没有写入项目目录或生产能力仓库。

## 3. 审查纠偏

双轴审查发现并已修复：

1. Selection 幂等判断曾只比较 Pack，可能静默吞掉 Procedure 变化；现两类引用同时比较；
2. 原型曾允许管理员直接登记平台版本、个人直接登记 `verified`；现目录入口失败关闭；
3. 原型把 AC-07 的发布/弃用提前塞入 AC-04，且平台快照复用个人 digest；已移除该越界入口；
4. Component 的 scope 字符串与领域枚举重复；现统一使用 `ProcedureScope`；
5. SQLite DDL 曾与迁移 SQL 双份维护；现只读取迁移文件。
6. OCI 引用曾包含本机 Layout 绝对路径，平台 Procedure/Component 也缺发布态过滤；现改为
   逻辑引用，并对未发布平台记录隐藏且不可选。

## 4. 边界与未决问题

### 已验证事实

- AC-04 的本机目录、Owner 隔离、不可变元数据、TaskRevision 冻结和 ORAS Layout PoC 已通过；
- AC-04 执行当时未迁移生产库；后续 AC-06 目录迁移和 AC-07 #33 治理迁移均带备份完成，
  既有 Legacy Skill、模板、任务和目录数据经摘要复核未改写；
- AC-05 独立获取状态机、缓存 Lease、取消清理和真实依赖下载已在后续工单完成；
- AC-07 #33 三轴治理投影已完成；验证晋级、管理员脱敏发布、Trivy/Syft/Cosign、弃用和回滚
  仍属于后续工单。

### 基于代码的推断

- 以 ORAS CLI 为制品 Adapter、SQLite 为产品元数据 Adapter，可以让后续 AC-05 替换 Registry
  或缓存实现而不改变 `CapabilityCatalog` 调用方；
- 平台治理角色不自动越过 Owner 过滤，可避免审核能力演化成读取个人业务制品的后门。

### 尚未验证的建议

- 后续生产迁移均已按独立授权执行备份、幂等迁移和旧数据零改写复核；
- AC-05 已按冻结的 Lease/取消契约完成；本报告中的单文件 ORAS PoC 仍只属于 AC-04 证据，
  完整获取证据必须引用 AC-05 报告。
