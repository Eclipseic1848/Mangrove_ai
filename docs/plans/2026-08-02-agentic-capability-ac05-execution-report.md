# AC-05 独立能力获取状态机与共享缓存执行报告

> 日期：2026-08-02
> 状态：`ac05_production_migrated_pending_user_acceptance_conclusion`
> 当前生产迁移代码提交：`235459a3`
> 边界：本报告只证明 AC-05；后续 AC-06 已通过用户灰度验收，AC-07 #33 已完成并迁移。
> AC-05 获取链本身仍未发布平台能力或正式 Delivery

## 1. 已完成产物

- 新增 `src/capability_acquisition/` 深 Module，调用者只使用
  `CapabilityAcquisition.acquire/cancel`，请求不携带用户来源路径或 Provider Key；
- 状态覆盖发现、等待权限、获取、构建、验证、就绪、失败和取消，并持久化 Owner 隔离事件；
- 官方来源按平台清单自动授权；登记 MCP/Skill 必须引用平台可信登记记录，不能由请求自报域名；
  陌生精确 HTTPS URL 必须持有 Owner、Grant、URI 三者绑定的权限；重定向后再次校验；
- 下载环境使用 Smokescreen `dependency_acquisition` 策略，不挂载业务来源、不接收业务 Secret；
  经授权的登记或陌生域名会进入本次 Lease 的精确 ACL；
- 下载后用 BuildKit `--network none` 离线构建，并复用本机 BuildKit layer cache；制品通过
  ORAS 写入 OCI Image Layout，只以不可变 digest 返回；
- 同一候选使用进程内 single-flight，下载前再使用 `filelock` 跨实例/跨进程文件锁并在锁内
  二次 lookup；OCI Layout 读写也同时持有进程锁与文件锁。并发冷请求只下载/构建一次；
- 时间、下载、解包、候选、重试和并发预算失败关闭；候选按冻结顺序回退；
- 取消会写入跨实例确定性 marker；解析、下载和 BuildKit 命令轮询 marker 后终止，下载容器也
  按确定性名称删除；重试前重建干净短期上下文；
  非终态记录重启时先回收同一确定性 Lease 和工作目录；
- 同一 Owner + `acquisition_id` 先取得跨进程 execution claim 才能恢复或执行，避免第二实例
  清理第一实例的活动资源；进程退出后操作系统自动释放 claim；
- READY 只在网络 Lease、进程和临时目录清理成功后持久化；清理失败只能得到 FAILED；
  `request_cancel` 与 `finalize_ready` 分别在 SQLite `BEGIN IMMEDIATE` 内完成终态判断和写入，
  跨实例取消与 READY 不能用旧快照互相覆盖；
- 新增 `CapabilityMountResolver`：从当前 Owner 的 TaskRevision `CapabilitySelection` 解析
  pack/version/digest，复核目录可见性和 OCI manifest digest 后物化；Pi Runtime 生产命令只读
  挂载这些目录。业务 Egress 继续只允许冻结的本地/LAN 服务，不重新开放公共依赖站点。
  具体 Python/Node/CLI/MCP 调用语义属于 AC-06。

## 2. 验证证据

### 2.1 自动化与静态门

- `pytest tests/test_capability_acquisition.py tests/test_capability_catalog.py tests/test_agentic_runtime.py
  tests/test_semantic_workspace_api.py -q`：77 passed；
- 覆盖 Owner 取消隔离、权限等待后恢复、可信登记、最终 URL 复核、预算、候选回退、并发去重、
  SQLite 终态重开、非终态恢复、真实重试契约、清理失败不得 READY、只读能力挂载和业务网络门；
- `python -m compileall -q src/capability_acquisition src/capability_catalog
  src/agentic_runtime/egress_policy.py`：通过；
- `git diff --check`：通过。
- 2026-08-22 迁移加固后，`tests/test_capability_acquisition.py` 为 38 passed；相邻
  `test_capability_catalog.py + test_agentic_runtime.py` 为 65 passed；Standards/Spec 最终
  双轴复审无 P0/P1/P2。

### 2.2 真实 Docker、Smokescreen、BuildKit 与 ORAS 探针

执行 `python scripts/verify_capability_acquisition_ac05.py`，使用 PyPI 官方
`idna 3.10` wheel 和仓库公布的 SHA-256，验证结果：

- 两个独立 Module、Environment 和 ORAS Adapter 的并发冷 Run 均 READY，仅一个
  `reused=false`，另一个在跨实例锁内二次 lookup 后零下载复用；
- 重建 Module 与 ORAS Adapter 后的热 Run `reused=true`，三者 OCI digest 一致；
- 错误源 digest 失败关闭，不能覆盖同名版本；
- 另一 Module 实例在真实 downloader 容器已运行后触发取消，跨实例 marker 使运行实例得到
  CANCELLED、无 `pack_ref`，容器被终止；同一 marker 也由 BuildKit 命令轮询；
- 第一次人为留下脏构建上下文后，真实 Adapter 在第二次尝试清空上下文并 READY；
- 人为留下真实下载容器、Egress 网络和工作目录后，重启恢复入口全部回收；
- 冻结 TaskRevision 经真实 ORAS 校验 manifest digest 并物化，随后形成只读业务挂载目录；
- BuildKit 本地 cache 生成；探针结束后目标容器、网络和 active 工作目录残留均为 0。

探针使用临时目录，结束后删除临时 OCI Layout 和 cache；没有写生产能力仓库或业务数据库。

### 2.3 生产迁移、恢复与重放门（2026-08-22）

- `migrate_capability_acquisition(db_path, backup_path)` 是唯一显式迁移入口；Repository 在缺失或
  畸形 Schema 时失败关闭，不再隐式执行 DDL；
- SQLite `BEGIN IMMEDIATE` 写锁覆盖恢复点一致性检查、原子备份发布和纯新增 DDL，业务写入
  不能进入备份与 DDL 之间；迁移元数据在同一事务内绑定首次备份 SHA-256；
- 生产恢复点为 `data/backups/webui-before-ac05-20260822-072340.db`，SHA-256 为
  `669a13e24cbf6877e5c16bf41e532304a51b14e45742ef1162f7aa0957310b9e`；源库与恢复点
  `integrity_check=ok`；
- 迁移前、迁移后及恢复点中 AC-05 之外的 63 张既有表逻辑指纹均为
  `901d133bbeaf0c34720f74e46ffc9fa211b408b1f0ed6950e2b4d07925df3242`，验证零改写；
- 同路径重放后恢复点 SHA-256 不变；生产新增两张表和一个索引，任务记录保持 0；
- 从恢复点建立的临时副本通过完整性、再次迁移、幂等创建、请求身份冲突、跨 Owner 取消拒绝、
  取消持久化及取消后 READY CAS 拒绝，随后副本与临时恢复点均删除；
- 真实 Docker 探针再次通过并独立确认目标容器、网络、工作目录残留均为 0；8088 重启后
  `/api/health` 返回 200。该证据完成生产迁移与工程验收，最终用户验收结论仍由用户确认。

## 3. 审查纠偏

双轴审查首轮发现并已修复：

1. READY 曾先于 cleanup 持久化，现严格改为 cleanup 成功后才 READY；
2. 登记来源域名曾可由请求自报，陌生/登记域名也未进入真实 ACL；现改为可信登记解析或
   Owner 绑定 Grant，并把授权域名注入本次 Smokescreen 策略；
3. 原实现只处理首个候选且并发会重复构建；现支持有界顺序回退、失败尝试事件和跨实例
   single-flight；
4. BuildKit 进程未进入取消路径，真实重试会撞上脏目录；现登记当前操作 Task，并在每次尝试
   前重建短期上下文；
5. OCI Layout 跨进程锁曾声明但未使用；现读写路径都同时持有进程锁与文件锁；
6. 服务重启测试曾只覆盖终态读取；现非终态重进会先回收确定性资源，再重新执行。
7. 跨实例曾缺少同 acquisition 执行所有权，取消也可能以旧记录倒写终态；现增加 OS FileLock
   execution claim 和事务化双向终态门。
8. READY 投影失败曾可能触发错误终态回写；现 Repository 终态单调，`_finish` 返回实际持久化
   结果，READY 后的事件投影失败只记日志并由刷新恢复。

## 4. 边界与未决问题

### 已验证事实

- AC-05 状态机、来源策略、隔离获取、缓存、取消、重试、恢复和资源清理已完成工程验证；
- AC-05 已于 2026-08-22 完成带备份生产迁移、幂等重放、零改写和恢复副本验收；最终用户
  验收结论待确认；
- AC-06 四类真实 Adapter 与用户灰度验收已完成，AC-07 #33 三轴治理投影已完成；AC-07
  验证晋级/管理员发布、AC-08 SOP 学习及后续 UI 仍未开始；
- 本次 READY 只是冻结能力制品，不是 `verified` 平台能力，也不是正式 Delivery。

### 基于代码的推断

- Smokescreen、BuildKit、ORAS 与 filelock 分别承担网络、离线构建、OCI 制品和 Windows
  跨进程串行职责，比在项目内重写代理、构建器或制品协议更可控；
- `acquire/cancel` Interface 可让 AC-06 增加不同 Adapter，而不把包管理器细节泄露给 Pi。

### 尚未验证的建议

- AC-06 本地样本与用户灰度已完成；任何外部 MCP/Secret 仍需单独确认；
- AC-07 #33 生产迁移已授权完成；物理 cache 清理、平台发布及后续供应链工具仍需单独授权；
- 目标服务器上的 Linux、Docker 并发和故障恢复仍应留到既定 8B/AC-10 环境验收。
