# P0-05 显式数据库迁移体系规格

> status: ENGINEERING_AND_PRODUCTION_COPY_VERIFIED
>
> issue: GitHub #56
>
> baseline: `4e8e5f9c878002d9781dca622bafe7cd035ddb66`
>
> written_at: 2026-08-26

## 1. 目标

建立一条权威、显式、可审计的数据库迁移路径。应用和业务 Repository 不再在构造、启动或
首次请求时静默建表、`ALTER`、创建索引或回填数据；它们只检查所需 Schema 是否处于兼容版本，
不兼容时失败关闭并给出可执行的迁移命令。

本规格只冻结工程实现与验证边界，不授权读取或迁移生产数据库，不授权停机、恢复覆盖、提交、
推送或 GitHub 写入。

## 2. 已验证事实

1. #56 当前为 OPEN / `ready-for-agent`，前置 #55 已关闭。
2. `requirements.txt` 已锁定 `alembic==1.18.3` 与 `filelock==3.20.2`，无需为了迁移体系引入新的
   第三方包。
3. 维护者启动脚本指定的 Python 3.13 解释器安装了 Alembic 1.18.4 和 filelock 3.20.0，与仓库
   锁定版本存在漂移；当前命令行默认 Python 也不是项目锁定环境且没有 Alembic。实现验证不能
   复用这些漂移环境。
4. `webui.db` 被 WebUI、Agentic Runtime、Delivery、模型连接、能力目录/治理、运行时路由、
   对话转向和 Candidate Verification 等多个模块共享。
5. 代码搜索在至少 12 个源码文件中发现建表、索引、`ALTER` 或回填入口；其中
   `src/api/store.py`、`src/agentic_runtime/repository.py`、`src/model_connections/storage.py`、
   `src/scheduler/store.py` 等仍在 Repository 初始化时执行 DDL 或数据变更。
6. Candidate Verification、Runtime Routing、Capability Acquisition 和 Capability Governance
   已各自实现部分“先备份再迁移”逻辑，但迁移表、锁、校验强度和失败行为不统一。
7. `checkpoints.sqlite` 的 Schema 由 LangGraph `AsyncSqliteSaver` 所有；用户数据连接器只读访问
   用户指定数据库。这两类 Schema 不由 Mangrove 迁移体系接管。

## 3. 基于代码的推断

1. 继续给每个 Repository 增加独立迁移函数，会复制备份、SHA、锁、完整性检查和错误处理，
   无法满足“后续结构变更只有一条权威路径”。
2. 直接把当前所有 DDL 复制到一个巨型启动函数，只是移动代码，仍会在应用生命周期内写库，
   也不能提供可审计版本链。
3. 单独使用 Alembic 能提供版本图、确定性顺序和成熟的 SQLite/SQLAlchemy 执行机制，但不能独自
   证明备份 SHA、恢复可读、历史业务数据零改写或跨进程迁移锁；这些必须由 Mangrove 的深模块
   在 Alembic 外层统一保证。
4. 当前共享 `webui.db` 是最高风险与最高收益切面，应先完成；`scheduler.db`、Legacy `app.db`
   和资格账本随后通过同一 Interface 注册为独立 Profile，而不是再建新迁移框架。

## 4. 推荐架构

### 4.1 深模块与 Seam

新增 `src/database_migrations/` 深模块。外部 Seam 只暴露三个行为：

```python
status = inspect_database(target)
receipt = apply_migrations(target, backup_path, expected_source_sha256=None)
result = verify_restored_copy(receipt, restored_path)
```

- `inspect_database` 只读，返回当前 revision、目标 revision、待执行 revision、Schema 缺口和
  可执行提示。应用通过 `status.require_current()` 失败关闭，不另设一套校验语义。
- `apply_migrations` 是唯一写入口：锁定、校验源库、创建一致性备份、计算 SHA、执行 Alembic
  revision、验证完整性和外键、记录结构化 Receipt。
- `verify_restored_copy` 只验证恢复副本，不覆盖源库；真实恢复覆盖永远是独立人工操作。

SQLite 是本地可替代依赖，测试直接使用临时真实 SQLite 文件，不为它制造 Repository port 或
内存 mock。Alembic、文件锁、SQLite backup、hash 和 revision 发现均藏在模块内部。

### 4.2 CLI

统一入口为：

```text
python -m src.database_migrations status --profile webui --database <path>
python -m src.database_migrations plan --profile webui --database <path>
python -m src.database_migrations apply --profile webui --database <path> --backup <path>
python -m src.database_migrations verify-restore --receipt <json> --restored <path>
```

`status` 和 `plan` 保证零写入；`apply` 必须显式提供数据库与备份路径，不接受隐式生产默认值。
输出默认是脱敏 JSON：允许 revision、SHA、计数和相对逻辑名，不输出 Secret、业务正文或本机
绝对路径到可提交证据。

### 4.3 Database Profile

| Profile | 所有者 | 本工单处理方式 |
|---|---|---|
| `webui` | Mangrove 产品状态 | 首个完整 revision 链；收敛所有共享仓储 DDL/回填 |
| `scheduler` | Mangrove 调度器 | 独立 revision 链；构造器改为只验 Schema |
| `legacy_app` | Legacy Conductor SQLite 输出 | 显式 bootstrap/校验；写入路径不再自动建表 |
| `qualification_ledger` | Provider 资格证据 | 注册独立 revision 链；Ledger 初始化只验 Schema |
| LangGraph checkpoint | 第三方库 | 排除，不伪装为 Mangrove 自有 Schema |
| 用户连接器数据库 | 用户/外部系统 | 排除，只读；不得迁移 |
| Legacy Conductor MySQL | 外部部署数据库 | 不自动 DDL；本工单仅提供缺 Schema 的失败关闭提示，不执行远程迁移 |

### 4.4 Revision 与现有历史

1. 每个 Profile 使用线性、不可变、带内容 SHA 的编号 revision；禁止修改已执行 revision。
2. Alembic version table 是当前版本权威来源；Mangrove Receipt 记录执行证据，不另造第二套当前
   版本投影。
3. 首个 `webui` revision 同时支持：
   - 空库：创建当前完整 Schema；
   - 已知 Legacy 库：逐个验证所拥有的表/列/索引，只补明确缺口并执行冻结回填；
   - 未知漂移：不猜测、不删除、不覆盖，失败关闭并报告对象级缺口。
4. 现有 `candidate_verification_migrations`、`runtime_routing_migrations` 等表保留为历史证据；旧公开
   迁移函数暂时成为兼容 Adapter，内部委托统一模块，后续调用方迁完再删除，不维护双写版本。
5. 额外但不属于当前 Profile 的表允许存在；迁移前后对非本 revision 所有的表做逻辑指纹，任何
   意外改写均视为失败。

### 4.5 备份、锁和失败语义

执行顺序固定为：

1. 规范化并检查源库/备份路径，禁止相同路径、目录或覆盖既有恢复点；
2. 获取同库跨进程文件锁，并以 SQLite 写事务阻止并发迁移；
3. `quick_check`/`integrity_check`、`foreign_key_check` 和当前 revision 校验；
4. 使用 SQLite backup API 生成临时备份，验证后原子改名；
5. 冻结源库迁移前逻辑指纹与备份 SHA-256；
6. 通过共享连接执行 Alembic 待执行 revisions；
7. 再次执行完整性、外键、目标 Schema 与非目标数据指纹检查；
8. 原子写入 Receipt 后释放锁。

中途失败时禁止自动覆盖源库：回滚可回滚事务，保留原始恢复点和失败 Receipt，应用继续失败
关闭。是否把备份恢复到生产路径必须另行展示目标、停机状态和校验结果后由用户授权。

### 4.6 PRAGMA 决策

- `foreign_keys=ON`：所有产品连接统一设置，并以 `foreign_key_check` 验证；不能只在迁移器开启。
- `busy_timeout`：候选值 5000 ms，先用并发读写/迁移锁测试验证，再决定是否作为统一连接策略。
- WAL：本工单不预设启用。必须先证明备份包含一致状态、失败恢复无 `-wal/-shm` 残留、Windows
  并发与服务停止流程可靠；证据不足则保持当前 journal mode。

## 5. TDD 纵切面

1. **MIG-01：Interface 与只读状态**
   - 红：空库、旧库、当前库、未知漂移的 status 契约；status/plan 字节级零写入。
   - 绿：深模块骨架、Profile 注册表、Alembic revision 发现和结构化错误。
2. **MIG-02：`webui` 显式迁移**
   - 红：空库创建、Legacy fixture、重复执行、中途失败、非目标数据指纹。
   - 绿：当前完整 Schema revision 与冻结回填；共享 Repository 构造器改成只验 Schema。
3. **MIG-03：备份、锁与恢复**
   - 红：备份冲突、SHA 不符、两个进程并发、故障注入、恢复副本启动/读取。
   - 绿：原子备份、跨进程锁、Receipt 和恢复验证。
4. **MIG-04：其余 Profile 与兼容 Adapter**
   - 红：scheduler/app/ledger 构造或首次写入不得产生 DDL；旧迁移入口不得形成第二套版本。
   - 绿：注册 revisions、调用方切换和兼容委托。
5. **MIG-05：PRAGMA 与完整验收**
   - 红：外键违规、锁争用、WAL 候选备份/恢复、端口/进程/文件残留。
   - 绿：只采纳有证据的连接策略；生产副本演练、重复执行和恢复读取全部通过。

每个纵切面的测试保留为回归安全网，不以一次性探针替代。

## 6. 完成标准

- 空库、冻结 Legacy fixture、当前生产库副本、重复执行和故障注入均有通过证据。
- 生产副本迁移前后非目标表与历史业务数据逻辑指纹一致。
- 两个进程不能同时迁移同一数据库；失败不产生“版本已完成”的假记录。
- 备份路径、SHA、源/目标 revision、完整性和恢复验证进入结构化 Receipt。
- 应用以旧版、未来版、未知漂移 Schema 启动时均失败关闭并给出迁移命令。
- 代码搜索不再发现平台自有 Repository 构造/首次请求中的建表、`ALTER`、索引变更或回填。
- CI 至少执行空库、Legacy fixture、重放、失败注入和并发迁移快速门；真实生产副本与恢复是人工门。
- Standards/Spec 双轴审查无剩余 P1/P2。

## 7. 明确排除

- 不在本工单迁移、轮换或销毁 Secret；#57 使用本体系实施其数据变更。
- 不处理依赖漏洞或拆分 requirements；属于 #58。
- 不配置 required checks、分支保护或 Ruleset；属于 #59。
- 不迁移用户连接器数据库，不修改 LangGraph 管理的 checkpoint Schema。
- 不自动恢复覆盖生产库，不发布、部署、打标签或创建 Release。

## 8. 实施与验收结果

- 已采用 Alembic 作为内部 revision 引擎，由 `src/database_migrations/` 统一负责 Profile、冻结
  摘要、计划、备份、锁、Receipt、完整性和恢复验证；Repository 构造器只验证 Schema。
- 空库、冻结 Legacy、重放、故障注入、跨进程争用、恢复副本及未知漂移失败关闭均由保留的
  自动化测试覆盖。最终完整核心回归为 `2165 passed, 7 skipped, 3 deselected`；三个 deselect
  均有独立边界说明，不涉及迁移功能失败。
- 当前生产 `data/webui.db` 的只读副本演练先真实发现并修复遗漏的合法
  `runtime_rollout_state.mode='vnext_default'`；最终 `webui_0001..0003` 应用成功，74 张表、
  10216 行历史数据无意外改写，完整性和外键检查通过。
- 同一迁移副本重复执行为零 revision 且字节不变；备份和恢复副本 SHA-256 一致，恢复验证、
  WebUIStore 与 RuntimeRouting 只读打开均通过。旧 Schema 由产品 Repository 失败关闭。
- `gray`、`all` 和任意非法 rollout mode 的失败关闭已固化为参数化回归；授权的
  `vnext_default` 保持值、P0 状态和快照引用不变。
- 独立 Standards/Spec 终审均无剩余 P1/P2。生产原库未迁移，恢复覆盖、提交、推送和 GitHub
  写入仍不由本地工程验收自动授权。
