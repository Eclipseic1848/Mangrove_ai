# P0-05 显式数据库迁移工具调研

> 状态：research-complete
>
> 日期：2026-08-26
>
> 对应 Issue：[#56](https://github.com/Eclipseic1848/Mangrove_ai/issues/56)
>
> 证据边界：只核验仓库代码、官方文档、官方包元数据和官方源码；未安装新依赖，
> 未运行候选工具，未读取或修改真实数据库。因此本文不是兼容性 PoC、生产迁移资格或用户验收。

## 1. 结论

**建议复用仓库已经固定的 Alembic 1.18.3，限定它只负责迁移图、确定顺序、版本头和
离线 SQL 计划；由一个最小的 Mangrove 显式执行外壳负责备份路径/SHA-256、迁移互斥、
`integrity_check` + `foreign_key_check`、幂等重放和恢复副本验证。不要引入 Yoyo 或
Dbmate，也不要从零重写完整迁移引擎。**

这不是因为 Alembic 原生满足了 #56。三种工具都不原生提供完整的备份证据、双完整性检查、
恢复验证和“应用启动只读验 Schema”契约。Alembic 的优势是：

1. 仓库已固定 `alembic==1.18.3` 和 `SQLAlchemy==2.0.45`，无需增加供应链或安装门；
2. 1.18.3 官方元数据明确支持 Python 3.13；
3. 它原生提供版本图、确定性升级路径、SQLite batch 模式、离线 SQL 输出和只读版本头检查；
4. 应用业务代码可以继续使用原生 `sqlite3`，SQLAlchemy 只出现在显式迁移 CLI 中。

Yoyo 是最接近“原生 DB-API + 多目录现有 SQL”的候选，但官方元数据没有声明
`Requires-Python`，官方变更记录只明确到 Python 3.11，且官方命令/API文档没有 dry-run。
在未获准安装并完成 Python 3.13 PoC 前，不能把它当成已验证适配。Dbmate 是成熟的单文件
二进制和纯 SQL 工具，但官方 CLI 没有 dry-run，当前主线也没有迁移互斥能力，现有 SQL 还要
加入 Dbmate 专用区段标记；它没有抵消新增外部工具链的成本。

## 2. 仓库约束

### 2.1 已验证事实

- `requirements.txt` 已固定 `alembic==1.18.3`、`SQLAlchemy==2.0.45`；项目并非缺少
  Alembic 依赖。
- 业务仓库主要用 Python 标准库 `sqlite3` 直接访问数据库；Alembic 当前未在项目 Python
  源码中被调用。
- `settings.webui_db_path` 默认指向 `data/webui.db`。多个模块共享该文件，但各自拥有初始化
  或迁移逻辑。
- 目前有 11 个受 Git 跟踪的编号 `.sql`，分散于 5 个模块目录；不同模块重复使用 `0001`。
  Candidate Verification 的 `0002_delivery_publication_idempotency` 还以内联 Python 逻辑
  存在，并无对应 `.sql` 文件。
- `src/api/store.py` 构造时会执行集中 `_DDL`，随后按列存在性静默执行多条 `ALTER TABLE`；
  Capability Catalog、Conversation Steering 等仓库构造时也会执行建表 SQL。
- Candidate Verification、Capability Acquisition、Runtime Routing 已分别实现自己的迁移表、
  `FileLock`、SQLite Backup API、备份 SHA 和部分恢复/幂等校验，但命名、记录表和证据范围不统一。

### 2.2 基于代码的推断

- 现状不是“没有迁移能力”，而是多个垂直切面各自实现迁移，应用启动与显式迁移并存，缺少
  单一权威注册表和统一版本头。
- 由于多个目录中的 `0001` 并不全局唯一，无论使用 Alembic 还是 Yoyo，都必须先建立稳定的
  全局迁移 ID；不能直接把现有文件名当作全局 revision。
- 迁移工具的内部版本表不能替代现有 `backup_sha256` / `ddl_sha256` 证据。#56 需要单独的、
  不可静默改写的 Mangrove 执行记录。

## 3. 候选工具事实核验

### 3.1 Alembic 1.18.3

#### 已验证事实

- PyPI 1.18.3 元数据要求 Python `>=3.10`，并明确列出 Python 3.13 classifier；该版本也是
  当前仓库固定版本。[PyPI 1.18.3](https://pypi.org/project/alembic/1.18.3/)
- Alembic 是建立在 SQLAlchemy 之上的迁移工具，revision 形成可分支、合并的有向无环图，
  并以版本表记录当前 head。[官方说明](https://alembic.sqlalchemy.org/en/latest/)
- `--sql` offline mode 会把迁移生成 SQL，而不连接目标数据库；但依赖数据库内 SELECT
  结果的操作不适用于这种模式。
  [Offline Mode](https://alembic.sqlalchemy.org/en/latest/offline.html)
- SQLite batch 模式支持“建新表—复制—替换”的迁移；需要反射现有表时通常必须 online。
  离线生成 SQLite batch SQL 必须显式提供完整 `Table` 给 `copy_from`，官方称这种写法繁琐。
  [SQLite Batch Migrations](https://alembic.sqlalchemy.org/en/latest/batch.html)
- `alembic current --check-heads` 能检查数据库是否位于全部 head，不一致时失败；这适合作为
  启动只读兼容门的底层能力。
  [官方 Cookbook](https://alembic.sqlalchemy.org/en/latest/cookbook.html#test-current-database-revision-is-at-head-s)
- Alembic 没有跨数据库通用的内建并发迁移锁；官方维护者说明锁与隔离需要在 `env.py`
  按数据库自行配置。
  [官方 Issue #633](https://github.com/sqlalchemy/alembic/issues/633#issuecomment-562081980)

#### 基于代码约束的适配推断

- **Python 3.13：适配。** 有当前固定版本的官方元数据证据。
- **原生 `sqlite3` 业务代码：可适配。** 只让显式迁移 CLI 经 SQLAlchemy 连接，不要求业务
  Repository 改成 ORM。
- **现有 `.sql`：部分适配。** Alembic revision 是 Python 文件；需要用薄 revision wrapper
  引用冻结 SQL，并给每个 SQL 建立全局 ID/依赖。不能把 11 个文件原样丢进 versions 目录。
- **dry-run：部分适配。** `--sql` 能产生确定性计划，但依赖 schema 反射或数据查询的迁移需
  另做“在临时备份副本实跑但不触碰源库”的计划验证。
- **迁移锁、备份 SHA、完整性和恢复验证：不原生适配。** 必须由 Mangrove 外壳实现。
- **启动只验 Schema：适配。** 可复用 revision/head 解析，但应使用只读连接或直接读统一版本表；
  不应让默认 `alembic current` 在空库上隐式创建版本表。

### 3.2 Yoyo Migrations 9.0.0

#### 已验证事实

- Yoyo 官方说明支持 SQLite，迁移可直接写成 SQL 或 Python；SQL 文件名本身是唯一 ID，
  多来源中的 ID 必须全局唯一，依赖优先、文件名次序执行。
  [官方文档](https://ollycope.com/software/yoyo/latest/#migration-files)
- 官方文档支持 glob 多迁移源，正好能覆盖 `src/*/migrations` 这种布局。
  [Migration Sources](https://ollycope.com/software/yoyo/latest/#migration-sources)
- 每个迁移独立事务，步骤用 savepoint；Yoyo 创建 `_yoyo_migration`、`_yoyo_log`、
  `_yoyo_version` 和 `yoyo_lock` 表，并提供 `backend.lock()`，用于阻止多个 Yoyo 进程冲突。
  [Transactions 与 Python API](https://ollycope.com/software/yoyo/latest/#transactions)
- 官方 CLI 提供 `list`、`apply`、`rollback`、`reapply`、`mark`、`unmark`，文档中未发现
  dry-run 或仅渲染待执行 SQL 的命令。
  [CLI 文档](https://ollycope.com/software/yoyo/latest/#command-line-usage)
- PyPI 最新稳定版是 9.0.0（2024-08-10），元数据未声明 `Requires-Python`，只列
  `Python :: 3`；官方变更记录最近一次明确的解释器支持是 8.2.0 的 Python 3.11。
  [PyPI 9.0.0](https://pypi.org/project/yoyo-migrations/)

#### 基于代码约束的适配推断

- **原生 `sqlite3` 和现有 SQL：高度适配。** 它基于 DB-API，能读取多个 SQL 目录；但现有
  重复 `0001` 必须先改为稳定的全局 ID。
- **Python 3.13：未充分验证。** 没有官方元数据或测试矩阵证据，本文又未获准安装验证。
- **dry-run：不适配。** `list` 只能展示待执行 revision，不能给出 #56 所需的 SQL/影响计划。
- **迁移锁：部分适配。** 有工具锁和 `break-lock`，但仍需在 Windows、多进程、崩溃遗留锁和
  单个共享 `webui.db` 上做真实并发/恢复测试，不能由文档直接推定验收通过。
- **备份 SHA、SQLite 双检查、恢复启动：不原生适配。** 仍需 Mangrove 外壳。

### 3.3 Dbmate 2.34.1

#### 已验证事实

- Dbmate 是跨语言的单文件 CLI，支持 SQLite 和纯 SQL；migration 以数字版本排序，默认在
  单个事务中执行，并用 `schema_migrations` 记录已应用版本。
  [官方 README](https://github.com/amacneil/dbmate/blob/v2.34.1/README.md)
- 当前 SQL 格式必须包含 `-- migrate:up` 与 `-- migrate:down` 标记；Dbmate 只保存版本号，
  不保存迁移内容摘要。
  [Migration Files](https://github.com/amacneil/dbmate/blob/v2.34.1/README.md#migration-files)
- `--strict` 能拒绝乱序迁移，`status --exit-code` 可用于版本状态门；官方 CLI 选项中没有
  dry-run。[Commands](https://github.com/amacneil/dbmate/blob/v2.34.1/README.md#commands)
- 当前公开 `DB` API/CLI 没有迁移锁字段；为 PostgreSQL 增加锁的官方 PR 仍处于 open，且其
  问题描述就是并发启动迁移产生 race condition。
  [官方 PR #596](https://github.com/amacneil/dbmate/pull/596)
- v2.34.1 是官方 Releases 页面标记的最新版本。
  [官方 Releases](https://github.com/amacneil/dbmate/releases/tag/v2.34.1)

#### 基于代码约束的适配推断

- **Python 3.13：不相关且可共存。** 独立二进制不受 Python ABI 影响，但会新增 Windows/Linux
  工具分发、校验和版本管理。
- **现有 SQL：部分适配。** 每个文件都要加入 Dbmate 区段标记；重复数字版本仍需全局化。
- **dry-run、并发锁、备份/恢复证据：不适配。** 外壳工作量不低于 Alembic，并额外引入
  工具供应链，因此不推荐。

## 4. 能力矩阵

| #56 条件 | Alembic 1.18.3 | Yoyo 9.0.0 | Dbmate 2.34.1 |
|---|---|---|---|
| Python 3.13 官方证据 | 是 | 未充分验证 | 独立二进制，不适用 |
| 仓库已存在 | 是 | 否 | 否 |
| 原生读取现有 SQL | 需薄 revision wrapper | 是 | 需 up/down 标记 |
| 多目录/确定顺序 | version graph / locations | glob + dependency + filename | 数字排序 |
| 当前版本门 | head/version table | 内部版本/日志表 | `schema_migrations`/status |
| dry-run / SQL 计划 | `--sql`，SQLite 反射有局限 | 官方文档未提供 | 官方 CLI 未提供 |
| 并发迁移锁 | 需自定义 | 内建锁，仍需项目 PoC | 当前无 |
| 备份路径 + SHA | 需自定义 | 需自定义 | 需自定义 |
| `integrity_check` + `foreign_key_check` | 需自定义 | 需自定义 | 需自定义 |
| 幂等重放 | 版本头可阻止重复 revision；证据需自定义 | 已应用 revision 不重复；证据需自定义 | 已应用版本不重复；仅保存版本号 |
| 恢复副本验证 | 需自定义 | 需自定义 | 需自定义 |
| 启动只验 Schema | 可基于 head 自定义只读门 | 可基于元数据表自定义 | 可基于状态表自定义 |
| 综合建议 | **推荐作为内核** | 条件性备选 | 不推荐 |

## 5. SQLite 安全外壳为什么不可省

以下是数据库自身的约束，而不是迁移工具可自动替代的验收：

- SQLite Online Backup API 能从运行中的数据库创建一致快照；Python 3.13 `sqlite3.Connection`
  直接暴露 `backup()`。应继续使用该 API，而不是在数据库可能活跃时做普通文件复制。
  [SQLite Backup API](https://www.sqlite.org/backup.html)；
  [Python 3.13 sqlite3](https://docs.python.org/3.13/library/sqlite3.html#sqlite3.Connection.backup)
- `PRAGMA integrity_check` 不检查外键错误；官方要求另跑 `PRAGMA foreign_key_check`。
  两者必须分别判定并记录原始结果。
  [SQLite PRAGMA](https://www.sqlite.org/pragma.html#pragma_integrity_check)
- SQLite 同时只允许一个 write transaction；`BEGIN IMMEDIATE` 会立即申请写事务，已有写者时
  返回 `SQLITE_BUSY`。这可以作为数据库内第二层互斥，但不能替代可诊断、可超时的项目级锁。
  [SQLite Transactions](https://sqlite.org/lang_transaction.html#deferred_immediate_and_exclusive_transactions)
- `foreign_keys` 是连接级设置，SQLite 官方明确要求应用显式设置而不要依赖默认值；在事务中
  修改该 PRAGMA 是 no-op。因此是否启用必须单独通过现有数据、并发和恢复测试，不能夹在迁移
  事务中静默开启。[SQLite PRAGMA foreign_keys](https://www.sqlite.org/pragma.html#pragma_foreign_keys)

## 6. 推荐实施边界

### 6.1 建议采用

1. 建一个中央 Alembic environment 和单一线性 head；为既有跨模块 SQL 分配带域前缀的全局
   revision，例如 `cv_0001`、`cg_0001`，不得仅用重复的 `0001`。
2. 用薄 Python revision wrapper 读取 UTF-8 SQL，并在注册时冻结 SQL SHA-256；不把现有
   业务 DDL 重写成 ORM 模型，也不启用 autogenerate 作为权威来源。
3. 显式 CLI 分成 `plan`、`apply`、`verify`、`restore-verify`：
   - `plan`：解析当前 head/目标 head、列出有序 revision、校验 SQL hash，并尽可能生成
     offline SQL；对依赖反射/数据的迁移标记为必须在临时副本验证；
   - `apply`：先拿项目级文件锁，再 `BEGIN IMMEDIATE` 验证数据库写锁，创建 Online Backup，
     记录规范化备份路径和 SHA-256，然后执行 Alembic upgrade；
   - `verify`：对迁移后源库分别跑 `integrity_check` 和 `foreign_key_check`，核对目标 head、
     冻结 schema 契约和关键历史数据指纹；
   - `restore-verify`：从备份创建独立恢复副本，执行同样检查并用只读模式启动 Repository/应用
     schema probe，不覆盖源库。
4. 应用启动仅以只读方式比较当前 head、迁移 SQL hash 和必要 schema contract；不创建
   `alembic_version`，不执行 DDL、不回填数据。缺表、未知 head、hash 漂移或目标版本不兼容时
   失败关闭并打印唯一显式迁移命令。
5. 空库 bootstrap、当前生产库副本、重复执行、中途失败、两个进程并发、恢复副本启动均要有
   独立测试证据。WAL、`foreign_keys`、`busy_timeout` 作为单独实验变量，不随框架引入自动开启。

### 6.2 不建议采用

- 不引入 Yoyo/Dbmate 只为“直接执行 SQL”；这会形成第二套版本语义，却仍补不齐安全外壳。
- 不在应用启动中调用 `alembic upgrade head`、Yoyo `apply` 或 Dbmate `up/migrate`。
- 不把 Alembic autogenerate 输出当作已审查迁移；官方也把它定义为需要开发者继续编辑的
  candidate migration。
- 不以工具版本表替代迁移文件 hash、备份 SHA、检查结果和恢复证据。
- 不用简单文件复制替代 SQLite Online Backup API，也不在未测试时启用 WAL/外键。

## 7. 尚未验证的建议与后续决策

### 7.1 尚未验证

- Alembic 1.18.3 在本仓库 Python 3.13 环境下对现有 11 个 SQL 文件的真实执行兼容性；
  官方元数据只证明支持声明，不等于本仓库 PoC。
- 现有 SQL 中哪些可以完整 offline render，哪些依赖运行期 schema/data。
- Windows 上 `FileLock` + `BEGIN IMMEDIATE` 的公平性、超时、进程崩溃恢复和双进程行为。
- 当前真实 `webui.db` 是否存在外键违规、历史漂移、WAL sidecar 或非统一迁移记录。
- Alembic 1.18.3 相对当前更新版的安全/缺陷差异；#56 不应顺手升级依赖，除非另行批准并验证。

### 7.2 必须由用户确认

1. 是否接受“复用已固定 Alembic + 最小 Mangrove 安全外壳”作为 #56 实现方向；
2. 现有分域 migration ID 映射和统一 baseline 的业务含义；不能静默把历史已存在 schema
   误记为刚刚执行；
3. 真实生产数据库副本、备份目录、停写窗口和恢复演练均属于后续独立授权；
4. WAL、`foreign_keys`、`busy_timeout` 的启用策略必须在专项实验后单独确认。

## 8. 证据等级

- **已验证事实**：仓库静态代码/依赖与一手官方文档、PyPI 元数据、官方源码相互核对。
- **基于代码的适配推断**：说明了工具能力如何映射当前结构，但未执行候选工具。
- **尚未验证的建议**：实施结构与测试门，必须经 TDD、隔离数据库 PoC 和双轴审查后才能升级
  为工程结论。
