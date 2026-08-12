# Phase 3：数据库安全只读取数实施计划

> 文档状态：已完成并验收（2026-07-22）；本文保留原任务拆分供审计
> 上游文档：`handoff.md` 第 7.2 节、`plan.md` Phase 3、`docs/task-driven-data-workflows.md`  
> 最终证据：定向测试 139 passed；SQLite 10 万行 1.21 秒；Testcontainers MySQL 8/PostgreSQL 16 为 2 passed；前端构建与 Playwright E2E 通过。后续仓库级测试治理已将全仓清零为 749 passed、4 skipped、0 failed，4 个默认跳过标记显式运行后 5 passed，详见 `handoff.md`。

## Goal

在不破坏 v0.0.3 既有链路的前提下，交付 SQLite/MySQL/PostgreSQL 安全只读取数：命名连接 + 服务端凭证注入、连接测试与 Schema 发现、表级批量抽取落 RawArtifact、受控单 SELECT（后端能力、UI 默认关闭）、主键/时间水位线增量与断点恢复。首版前端不提供任意 SQL 文本框，不把 Mangrove 变成任意 SQL 执行器。

## Architecture

沿用 LangGraph 控制面，`acquire_node` 新增 `database` 分支；新 `DatabaseConnector` 复用 `SourceConnector` 契约（`src/connectors/base.py`）。每批 keyset 分页 SELECT 的行集归一化为 JSONL 字节，立即落不可变 RawArtifact（`application/x-ndjson`），被现有 `JsonXmlParser`（已注册 `jsonl` 扩展与 `application/x-ndjson` 媒体类型）零改动消费，复用 parse→profile→clean→validate→export 全链。凭证仅在服务端解析点明文存在，经构造函数注入连接器，不进 spec/options/state/日志/Manifest。

## Tech Stack

Python 3.13、FastAPI、Pydantic v2、SQLAlchemy 2.0 Core（方言/quoting/introspection）、SQLGlot（受控 SQL AST 与表白名单校验）、PyMySQL/psycopg2/sqlite3（同步驱动 + `asyncio.to_thread`）、cryptography Fernet（连接密码静态加密）、React/TS/Vite、pytest、Hypothesis、Testcontainers、pytest-timeout、Playwright。

## 最终实现摘要

- 自研 token/正则式 SQL 判断被 SQLGlot AST 替代；仅允许单条 SELECT，拒绝 DDL/DML、多语句、危险函数、系统 Schema 和越权表。
- 表模式使用 SQLAlchemy Core 反射与 quoting；单主键/复合主键使用正确 keyset，无主键使用有界 OFFSET 降级并告警。
- SQLite 以 URI `mode=ro` 打开；MySQL/PostgreSQL 强制只读会话，使用 `NullPool` 并在批次结束释放连接。
- 支持字段、过滤、时间范围、增量水位线、checkpoint、最大行数/字节/时间、重试、大字段截断与二进制 base64。
- API、Graph、预览、任务复跑和前端连接/Schema/表列选择已闭环；数据库密码、DSN 和底层错误不进入公共响应或产物。
- Testcontainers 默认镜像为 `mysql:8.0`、`postgres:16`，可通过 `PHASE3_MYSQL_TEST_IMAGE`、`PHASE3_POSTGRES_TEST_IMAGE` 覆盖。

## 基线事实（已探明）

- `src/data_prep/models.py`：`SourceType.DATABASE`、`credential_ref`、`IncrementalSpec`、`SourceLimits`、`to_public_dict()` 均已就绪，**Phase 3 不改 models.py**（options 为自由 dict，键约定见 §3）。
- `src/data_prep/graph.py` `acquire_node`（约 178–242 行）：if/elif 三分支；`parse_node` 只消费 artifacts；`DataPrepState` 有 `checkpoint` 字段但 acquire 未使用。
- `src/api/routes/data_tasks.py`：`Literal["upload_file","http_api"]` 白名单 + `_source_spec()` 服务端构造，客户端无法注入 headers/凭证——database 源沿用此形态。
- `src/api/store.py`：`WebUIStore`（标准库 sqlite3 + 每操作自带连接 + 进程锁），`runtime_config` 表已有“scope=user_id 存用户凭证”先例。
- `src/connectors/http_security.py`：`default_resolver`/`_hard_blacklist_category`/云元数据 IP 集合可被 DB 场景直接 import 复用。
- 原计划评估过 sqlparse，但最终采用 SQLGlot 的完整 AST 遍历，避免 token/正则规则漏判嵌套 DML、表引用和方言结构。
- MySQL/PostgreSQL 真库不并入常驻 Compose；验收使用 Testcontainers 临时容器并由 `--run-db-live` 显式开启。

---

## 1. 威胁模型

攻击者画像：已登录的普通用户（多租户 Web UI），能提交任务/API 请求；以及被动威胁（日志/Manifest 泄露、服务器响应恶意数据）。防护目标：DB 源只能被读、只能读被授权的表列、凭证不外泄、资源不失控。

| # | 攻击面 | 威胁 | 缓解（分层） | 验证 |
|---|--------|------|--------------|------|
| T1 | 受控 SQL 文本 | 多语句 `; DROP/DELETE`、注释包裹、编码混淆 | SQLGlot 解析后**语句数必须恰为 1**；不对 SQL 做字符串拼接（只整体执行） | `test_sql_guard.py::TestMultiStatement` |
| T2 | 同上 | DDL/DML 直写（INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/TRUNCATE/REPLACE/GRANT/CALL/SET/USE/ATTACH/LOAD/BEGIN…） | 语句 `get_type()` 必须为 `SELECT`（PRAGMA/SET 实测返回 UNKNOWN/其他类型即被拒）+ 递归 token 关键字黑名单（含注释剥离后）；驱动层默认不启用多语句客户端标志 | `TestStatementType` |
| T3 | 同上 | **CTE 包裹 DML 绕过**（PG：`WITH t AS (DELETE ... RETURNING) SELECT * FROM t`） | SQLGlot AST 拒绝 WITH、DML 子树及 `FOR UPDATE/FOR SHARE/LOCK IN SHARE MODE` | `TestCteRejected`（含 pg 写 CTE 用例） |
| T4 | 同上 | 写文件/读文件函数与子句：MySQL `INTO OUTFILE/DUMPFILE`、`LOAD_FILE()`；PG `pg_read_file/lo_import/pg_ls_dir`；SQLite `ATTACH` | `INTO` 关键字黑名单 + 危险函数名黑名单（递归扫描 Function 标识符）；sqlite 拒 `ATTACH/DETACH/PRAGMA` | `TestDangerousFunctions` |
| T5 | 越权表列 | 访问连接库内其他 schema/表（如 `mysql.user`、`pg_authid`）、信息_schema 探测、UNION 拼别表 | 递归提取全部 FROM/JOIN 表引用（含子查询嵌套），**逐一**对服务端白名单（默认=该连接当前 introspection 到的全量表，API 可再收窄为 `allowed_tables`）；系统 schema（mysql/pg_catalog/information_schema/sys/performance_schema/sqlite_master）默认从白名单剔除；找不到 FROM（`SELECT 1` 探测型）直接拒 | `TestTableWhitelist` |
| T6 | 连接串/凭证泄露 | 连接串进 spec_json（`data_prep_tasks.spec_json` 落库）、state、日志、Manifest、LLM 上下文、API 响应 | 契约：`SourceSpec.credential_ref="dbconn:<id>"` 仅为不透明引用；明文只在 `resolve_credential()` 返回值与连接器构造函数参数中存在；`to_public_dict()` 已脱敏；artifact `request_snapshot` 只存方言/host:port/库/表/字段名/操作符/过滤值 sha256 摘要（**不存过滤值字面量**）；API 响应模型不含 password 字段；`_sanitize_error()` 剔除异常串中的 password 片段；Manifest 扫描测试 | `test_db_credential_hygiene.py`（grep 断言 artifacts/manifest/store/日志无密码） |
| T7 | SSRF 到私网/云元数据 | 用户把连接指向云元数据 IP 或非预期内网服务 | 云元数据三 IP（169.254.169.254、fd00:ec2::254、100.100.100.200）**硬黑名单不可放行**；`data_prep_db_allowed_ports` 默认 "3306,5432" 限制端口；`data_prep_db_host_allowlist` 非空时仅放行清单（生产加固开关）；DNS 全 A/AAAA 逐条校验（复用 http_security） | `test_db_security.py` |
| T8 | 平台自用库被当源读 | 用 sqlite 方言连接 `data/webui.db`（含密码哈希+Cookie 凭证）/`app.db`/`scheduler.db` 抽成数据集下载 | sqlite 连接**只允许** `data_prep_db_sqlite_root`（默认 `data/db_sources`）内相对路径，拒绝对路径与 `..` 穿越；平台自用库文件名硬黑名单（webui.db/app.db/scheduler.db/checkpoints.sqlite）无论路径一律拒 | `test_db_security.py::TestSqlitePath` |
| T9 | 凭证静态泄露 | webui.db 文件被拷走 → 全部 DB 密码泄露 | 密码 Fernet 加密落库（`data_prep_db_secret_key`，空则派生自 jwt_secret 并在日志警告）；列表/详情 API 永不返回密码字段 | `test_db_connection_store.py::TestEncryption` |
| T10 | 大结果集 OOM | 无 LIMIT 全表拉取进内存 / LangGraph state 膨胀 | keyset 分页（`WHERE key > :last ORDER BY key LIMIT :batch`），state 只存 artifact 引用与 checkpoint 游标；`SourceLimits.max_records/max_bytes/max_seconds` 三重上限，超限截断 + warning + is_final | `TestLimits` + 性能测试 |
| T11 | 恶意/异常服务器响应 | 超大 LOB 单元格、恶意类型对象、错误包泄露服务器内部信息 | 单元格字节上限 `data_prep_db_max_cell_bytes`（默认 1MB）截断+warning；类型白名单归一化（§3.4），未知类型 `str()`；API 只回分类错误消息不回原始栈 | `TestCellNormalization`（大字段用例） |
| T12 | 写事务/只读绕过 | 任何环节漏出写语句 | 四层：①文档建议只读 DB 账户；②**会话级只读**（PG `default_transaction_read_only=on`；MySQL `SET SESSION TRANSACTION READ ONLY`；SQLite URI `mode=ro`）；③语句白名单/构造器只产 SELECT；④只读拒绝被驱动抛错时映射为 fatal 并告警（说明防御生效） | `TestReadonlyEnforced`（尝试写被拒，三方言） |
| T13 | 超时/资源耗尽 | 慢查询挂死任务、长事务持锁 | 每语句超时（MySQL `max_execution_time`、PG `statement_timeout`、sqlite `set_progress_handler` 截止回调）；连接超时 `data_prep_db_connect_timeout_seconds`；`SourceLimits.max_seconds` 总截止 | `TestTimeouts` |
| T14 | 并发连接泄漏 | 任务异常退出后连接悬挂、线程池耗尽 | NullPool + 每批连接上下文管理器 + `close()` 兜底 `engine.dispose()`；信号量上限 `data_prep_db_max_connections`（默认 4）；`to_thread` 内不做跨批持有 | `TestConnectionCleanup`（异常注入后断言无泄漏） |
| T15 | 断连重复读 | 网络闪断重连后从头重读导致重复 | 连接器内 retryable 断连（MySQL 2006/2013/2055、PG OperationalError）重连 ≤3 次，**从 last_key 续读**；checkpoint 持久化支持跨运行续跑 | `TestResumeNoDuplicate` |
| T16 | 跨用户越权 | 引用他人 connection_id | 所有连接 CRUD/test/schema/任务构造经 `user_id` 归属校验，跨用户返回 404（沿用 uploads 先例） | API 测试 |

---

## 2. 关键架构决策

### 2a. 驱动选型：同步驱动 + `asyncio.to_thread`（不引入异步驱动）

**pymysql / psycopg2 / sqlite3 + SQLAlchemy 2.0 sync engine（NullPool），每批查询在 `asyncio.to_thread` 中执行。**

理由：

1. `read()` 的异步契约只需要“批次之间让出事件循环”——每次 `to_thread(fetch_batch)` 天然满足；keyset 分页下每批是独立 LIMIT 查询，无跨 await 的游标持有，异步驱动流式游标优势用不到。
2. 依赖最小化：三件套中两件已装且 sqlite3 是标准库，只需把 psycopg2 声明进 requirements；引入 aiomysql（维护停滞、MySQL 8 认证兼容性差）/asyncpg（C 扩展，Windows wheel 版本约束）/aiosqlite（纯新增）是三个新风险点。
3. Windows 与 Python 3.13 兼容性以同步驱动最稳。
4. SQLAlchemy sync engine + NullPool 配合“每批 to_thread 内开连接—执行—取数—关闭”模式，规避 sqlite3 连接线程绑定（`check_same_thread`）与 psycopg2 连接跨线程的坑——**连接永不跨线程迁移**。

代价与缓解：每批一次 TCP+auth 握手（MySQL/PG），在批大小 5000 行下占比可忽略；线程池阻塞由语句超时（T13）兜底；连接泄漏由 T14 分层兜底。文档注明：若 Phase 5 队列化后需要高并发 DB 源，可平移到 asyncpg/aiomysql，连接器契约不变。

### 2b. SQL 校验：双模式

- **表级模式（`options.mode="table"`，v1 唯一对用户开放的路径）**：服务端用 SQLAlchemy Core（`MetaData` + `table()`/`column()` + `select()` + 参数绑定 + `LIMIT`）构造查询，表名/列名来自服务端 introspection 白名单校验后的结构化 options，`IdentifierPreparer` 负责方言 quoting（保留字/特殊字符/大小写安全）。**注入面为零**——不存在 SQL 文本输入。
- **受控 SQL 模式（`options.mode="sql"`）**：`src/connectors/sql_guard.py` 使用 SQLGlot 解析完整 AST。规则：①语句数恰为 1；②根节点必须为 SELECT；③拒绝 WITH、DML/DDL 子树、锁子句、PRAGMA/ATTACH/SET/USE/CALL；④拒绝危险函数；⑤递归提取 FROM/JOIN 表引用并逐一对白名单；⑥拒绝无 FROM 的探测型 SELECT。与会话只读、超时/行数限制共同形成纵深防御。
- **首版门禁**：`settings.data_prep_db_custom_sql_enabled` 默认 **False**，API 层拒绝 `mode="sql"`；后端能力、校验器与测试随本阶段交付，但部署者显式开启前不暴露。前端不实现 SQL 文本框。

### 2c. 记录路径：产 RawArtifact（每批 JSONL 落盘），不直接产 RecordEnvelope

理由：

1. **原始不可变**：DB 响应字节立即落 RawArtifact，复查/复跑/血缘有据；直接产 Envelope 会跳过 raw 层，违背 plan 6.2。
2. **零管线改动**：`parse_node` 只消费 artifacts；`JsonXmlParser` 已注册 `jsonl`/`application/x-ndjson`，DB 批次 artifact（`media_type="application/x-ndjson"`、扩展名 `.jsonl`）被零改动解析，parse→clean→quality→export→Manifest 全链与账本（`record_counts`）语义和文件/HTTP 源完全一致。
3. **改动面最小**：改 `parse_node` 消费 records 需要为 database 加特例分支并重做账本，且与“state 只存批次引用”原则冲突。

artifact 命名 `db/{table}/part-{part_no:05d}.jsonl`；`uri` 用脱敏定位符 `{dialect}://{host}:{port}/{database}/{schema}.{table}#part-{n}`（含 host 供血缘，**永不**含 user/password）；`request_snapshot` 见 T6。

### 2d. credential_ref 闭环：webui.db 新表 `db_connections` + Fernet 静态加密 + “先建命名连接再引用”

- **存储**：`src/api/store.py` `WebUIStore` 加 `db_connections` 表（`connection_id/user_id/name/dialect/host/port/database_name/username/password_enc/sqlite_relpath/created_at/updated_at`）与 CRUD 方法。选 webui.db 而非 runtime_config 机制：连接是**结构化多字段实体**（host+port+db+user+password+名称），runtime_config 是扁平键值白名单，硬套会污染 REGISTRY；webui.db 已有用户隔离存储先例（Cookie、data_prep_tasks）。
- **加密**：新模块 `src/services/db_connections.py`：`encrypt_password/decrypt_password`（Fernet，key 来自 `settings.data_prep_db_secret_key`，空则 `SHA256(jwt_secret)` 派生并启动告警）；`to_public_dict()` 永不输出密码；`resolve_credential(credential_ref, user_id) -> DbCredentials` 是**唯一明文出口**。
- **注入**：`DatabaseConnector(credentials=... 或 credential_resolver=...)`，graph 路径下由连接器用 `credential_ref` + `options["user_id"]`（API 层注入，沿用 upload_file 先例）延迟解析，明文不落 spec/state。
- **API 形态**：`POST/GET/DELETE /api/data-sources/connections`、`POST /api/data-sources/connections/test`（支持已存连接 `{connection_id}` 与未保存草案内联测试）、`GET /api/data-sources/connections/{id}/schema?schema=`。
- **前端**：连接管理对话框（新建→测试→保存）、连接下拉、schema/表/列级联候选。
- **首版最小闭环** = 上述全部，无管理员共享连接、无 LLM 接触凭证。

### 2e. 私网/SSRF：默认允许私网与 loopback，云元数据硬黑名单 + 可选主机白名单 + 端口白名单

与 HTTP 源的差异：DB 连接是用户**显式配置的命名连接**，无重定向、无攻击者控制的 URL 抓取，主用例就是连 localhost/私网里的自有库——照搬 HTTP 的“私网默认拒绝”会让产品不可用。默认策略：

- 默认**允许**私网与 loopback；
- 云元数据 IP 硬黑名单**不可放行**（T7，这些地址上没有合法 DB 场景）；
- `data_prep_db_host_allowlist`（逗号分隔主机/IP，默认空）：非空时仅放行清单——生产/多租户部署的加固开关；
- `data_prep_db_allowed_ports`（默认 "3306,5432"）：防把驱动当内网端口探测工具；
- 新模块 `src/connectors/db_security.py`，import 复用 `http_security.default_resolver`/`_hard_blacklist_category`，DNS 全 A/AAAA 逐条校验；
- sqlite 路径策略见 T8（root 限定 + 平台库黑名单），这是 DB 场景独有的“loopback 等价物”。

### 2f. 只读强制：四层纵深

1. **账户层（文档）**：README/AGENTS 建议为 Mangrove 配 `GRANT SELECT` 只读账户——非代码强制，写明为部署建议。
2. **会话层（代码强制，每连接执行）**：PostgreSQL `SET SESSION default_transaction_read_only = on`（psycopg2 `set_session(readonly=True)` 等价封装）；MySQL `SET SESSION TRANSACTION READ ONLY`（5.6.5+，对会话后续全部事务含 autocommit 单语句生效；MariaDB 兼容）；SQLite `sqlite3.connect("file:...?mode=ro", uri=True)`（打开即只读，不产生 journal/WAL 写）。
3. **语句层**：表级模式只经 SQLAlchemy 构造 SELECT；受控 SQL 过 `sql_guard`；驱动层不启用多语句标志。
4. **资源层**：语句超时（方言级）、行/字节/总时长上限、连接数上限（T13/T14）。

---

## 3. 契约约定

### 3.1 SourceSpec 填充约定（database 源）

```text
locator        = "dbconn://<connection_id>"        # 不透明定位符，非真实连接串
credential_ref = "dbconn:<connection_id>"
options = {
  "user_id": "<服务端注入>",                        # 沿用 upload_file 先例
  "mode": "table" | "sql",                         # 默认 table；sql 受 flag 门控
  "schema": "shop",                                # 可选；sqlite 忽略
  "table": "orders",                               # table 模式必填（v1 单表）
  "fields": ["id", "amount"],                      # 空=全部列（introspection 校验）
  "filters": [{"field":"status","op":"eq","value":"paid"}],  # op ∈ eq/ne/gt/ge/lt/le/in/is_null/not_null/contains
  "time_range": {"field":"updated_at","start":"2026-01-01T00:00:00","end":"2026-02-01T00:00:00"},  # >= start AND < end
  "sql": "SELECT ...",                             # mode=sql 必填
  "allowed_tables": ["orders"],                    # mode=sql 收窄白名单；空=连接库全量（剔系统 schema）
}
incremental = {"strategy":"watermark","cursor_field":"updated_at","last_value":null}   # 复用 IncrementalSpec
limits      = SourceLimits(max_records=..., max_bytes=..., max_seconds=...)
```

`filters[].value` 一律参数绑定，不拼接；`is_null/not_null` 无 value。表级模式 keyset 排序键优先级：显式 `incremental.cursor_field` > 表主键（单列或复合） > 无主键且无水位线 → 全量单轮读取 + warning“不支持断点续跑”。

### 3.2 Checkpoint 编码（`Checkpoint.cursor` 为 JSON 字符串）

```json
{"mode":"table","table":"orders","key_cols":["id"],"last_key":[12345],
 "part_no":7,"rows_read":123456,"done":false}
```

`Checkpoint.watermark` 存水位线值（ISO 字符串/数字字符串化）；`next_part_no` 保证续跑 artifact 命名不覆盖。恢复语义：`WHERE (key) > :last_key ORDER BY key LIMIT :batch`，**不重复**已交付批次。跨运行：`data_prep_tasks` 新增 `checkpoint_json` 列（ALTER 迁移），rerun 时回注 `incremental.last_value`。

### 3.3 discover 公开摘要（不回传 host 以外任何连接参数）

```json
{"dialect":"mysql","server_version":"8.0.x","default_schema":"shop",
 "schemas":[{"name":"shop","table_count":12}],
 "tables":[{"schema":"shop","name":"orders","estimated_rows":12345,
   "columns":[{"name":"id","type":"BIGINT","nullable":false}],
   "primary_key":["id"]}]}
```

样本行仅在显式 `sample=true` 且经列白名单后返回，行数 ≤ `data_prep_db_max_sample_rows`（默认 20）。

### 3.4 DB→JSONL 类型归一化

| DB 值 | JSONL 表示 |
|---|---|
| int/float/str/bool/None | 原样 |
| Decimal | str（保精度） |
| datetime/date/time | ISO 8601 str |
| bytes/BLOB | base64 str（每批首见记 warning） |
| JSON/JSONB | 原生对象 |
| 单元格 > `data_prep_db_max_cell_bytes` | 截断 + warning |
| 其他未知类型 | `str()` |

### 3.5 错误分类（→ RecordBatch.fatal_error / retryable_error）

- **fatal**：认证失败（MySQL 1045 / PG 28P01）、权限不足（1142 / 42501）、表列不存在（校验期 ValueError）、只读冲突（1792 / 25006 / sqlite "readonly database"——防护生效）、sql_guard 拒绝、sqlite 路径越界。
- **retryable**：连接丢失（MySQL 2006/2013/2055、PG OperationalError/InterfaceError、超时）——连接器内重连 ≤ `data_prep_db_max_retries`（默认 3）次从 last_key 续读，耗尽后 fatal。

---

## 4. 任务分解（Task 0–12）

每 Task 含 TDD 步骤（先失败测试→实现→通过→提交）。所有 pytest 命令统一 `E:\python3.13\python.exe -X utf8 -m pytest ... -q`。

### Task 0：冻结 v0.0.3 基线 + 落计划文档

- **Files:** Create `docs/plans/2026-07-21-phase3-database-readonly-extraction.md`（本文档）；Modify `handoff.md`（Phase 3 开工状态一行）。
- **Steps:** ① `git status --short` 确认除 handoff 点名保留的本地改动与文档级改动外无新脏改；② 基线回归 `pytest tests -q` → 206 passed + 1 skipped；③ `npm --prefix frontend run build`。
- **测试：** 基线绿即门禁。
- **提交：** `docs: Phase 3 数据库安全只读取数实施计划与威胁模型`
- **退出门禁：** 计划文档入库、基线 206+1 复现。

### Task 1：settings / .env.example / requirements / pytest 标记

- **Files:** Modify `src/config/settings.py`、`.env.example`、`requirements.txt`、`pytest.ini`、`tests/conftest.py`。
- **settings 新增键：**
  - `data_prep_db_batch_size: int = 5000`（单批行数）
  - `data_prep_db_query_timeout_seconds: int = 60`、`data_prep_db_connect_timeout_seconds: int = 10`
  - `data_prep_db_max_connections: int = 4`、`data_prep_db_max_retries: int = 3`
  - `data_prep_db_max_cell_bytes: int = 1048576`、`data_prep_db_max_sample_rows: int = 20`
  - `data_prep_db_host_allowlist: str = ""`、`data_prep_db_allowed_ports: str = "3306,5432"`
  - `data_prep_db_sqlite_root: str = "data/db_sources"`
  - `data_prep_db_secret_key: str = ""`（空则派生自 jwt_secret + 告警）
  - `data_prep_db_custom_sql_enabled: bool = False`（受控 SQL 总开关）
  - `data_prep_db_max_discovery_tables: int = 500`（发现摘要上限）
- **requirements.txt**：加 `psycopg2-binary==2.9.11`（Windows 免编译；Linux 部署文档注明可换源码版 psycopg2）；核对 PyMySQL/SQLAlchemy 锁版与环境对齐。
- **pytest.ini** markers 加 `db_live`；`tests/conftest.py` 以独立 `--run-db-live` 开关启用 Testcontainers 真库测试，常规回归默认跳过。
- **测试：** `pytest tests -q` 基线不回归；`python -c "from src.config.settings import settings; assert settings.data_prep_db_batch_size==5000"` 冒烟。
- **提交：** `feat: Phase 3 数据库取数配置项与依赖声明`
- **退出门禁：** 206+1 绿；新配置键全部有 .env.example 注释。

### Task 2：连接存储与凭证解析（凭证闭环服务端）

- **Files:** Modify `src/api/store.py`（DDL + CRUD：`create_db_connection/list_db_connections/get_db_connection/delete_db_connection`，全部按 user_id 归属；`data_prep_tasks` 加 `checkpoint_json` 列的 try/except ALTER 迁移）；Create `src/services/db_connections.py`（`DbConnectionIn`/`DbConnectionPublic`/`DbCredentials` dataclass、`encrypt_password/decrypt_password`、`resolve_credential(credential_ref, user_id)`、`to_public_dict()`）；Test `tests/test_db_connection_store.py`。
- **要点：** `credential_ref` 格式 `dbconn:<uuid>` 严格正则解析，拒绝其他 scheme；跨用户 get 返回 None（API 映射 404）；Fernet key 派生逻辑集中一处；删除连接不级联删历史任务（spec 里引用变死引用，重跑时报 fatal“连接不存在”，可接受）。
- **测试（约 12 个）：** 加密往返/错误密钥拒解/CRUD/跨用户不可见/脱敏输出无 password 字段/坏 credential_ref 拒解/`checkpoint_json` 迁移幂等。
- **提交：** `feat: 数据库命名连接存储与 Fernet 凭证加密`
- **退出门禁：** 新测试全绿 + 基线绿；`WebUIStore` 既有测试不回归。

### Task 3：db_security 与 sql_guard（安全校验双模块，纯函数先行）

- **Files:** Create `src/connectors/db_security.py`（`validate_db_host(host, port, *, allowlist, allowed_ports, resolver)`、`validate_sqlite_path(relpath) -> Path`）；Create `src/connectors/sql_guard.py`（`SqlGuardError`、`validate_select(sql, *, allowed_tables) -> ValidatedQuery`）；Test `tests/test_db_security.py`、`tests/test_sql_guard.py`。
- **要点：** db_security 复用 `http_security.default_resolver`/`_hard_blacklist_category`；sqlite 平台库黑名单（webui/app/scheduler/checkpoints 库文件名）；sql_guard 按 §1 T1–T5 规则实现，表引用递归提取覆盖 FROM/JOIN/子查询嵌套。
- **测试（约 30 个，class 组织仿 test_http_security.py）：** 多语句、DDL/DML 全族、PRAGMA/SET/USE/CALL/ATTACH、**CTE 含 PG 写 CTE**、`FOR UPDATE`、`INTO OUTFILE`、`LOAD_FILE/pg_read_file`、子查询合法放行、UNION 合法但表全部过白名单、`SELECT 1` 无 FROM 拒绝、越权表/系统 schema 拒绝、注释混淆（`SEL/**/ECT`、行注释藏分号）、大小写混淆；db_security：loopback 默认放行、云元数据三 IP 硬拒、端口白名单、主机白名单生效、sqlite 穿越/绝对路径/平台库拒绝。
- **提交：** `feat: 数据库主机校验与单 SELECT 受控 SQL 校验器`
- **退出门禁：** 全部安全用例绿；sql_guard 对 50+ 攻击样例零漏判（用例表入测试文件 docstring）。

### Task 4：方言适配层 db_dialects

- **Files:** Create `src/connectors/db_dialects.py`；Test `tests/test_db_dialects.py`。
- **要点（统一抽象 `DbDialect` dataclass + 三方言实例）：**
  - `make_engine(creds) -> Engine`：`create_engine(url, poolclass=NullPool, connect_args={connect_timeout...})`；sqlite 用 `sqlite:///file:<abs>?mode=ro&uri=true`。
  - `apply_readonly_session(conn)` / `apply_statement_timeout(conn, seconds)`：三方言 SQL 见 §2f / T13。
  - `introspect(engine, schema) -> SchemaInfo`：用 `sqlalchemy.inspect()`（`get_schema_names/get_table_names/get_columns/get_pk_constraint`），剔除系统 schema。
  - `build_table_query(meta, table, fields, filters, time_range, key_cols, last_key, limit)`：SQLAlchemy Core 构造，复合主键用 row-value 元组比较（`tuple_(*cols) > bindparam`，sqlite≥3.15/MySQL/PG 均支持）；filters 操作符白名单映射 + 参数绑定。
  - `normalize_value(v)`：按 §3.4 表。
  - `classify_error(exc) -> "fatal"|"retryable"`：按 §3.5 错误码表。
- **测试（约 15 个）：** sqlite 真库验证 introspect/只读会话（写尝试被 mode=ro 拒）/keyset 查询构造（复合主键、filters 参数绑定、time_range）/类型归一化全表/错误分类；mysql/pg 的 SQL 构造断言用 `str(query.compile(compile_kwargs={"literal_binds":...}))` 离线比对（不需真库）。
- **提交：** `feat: 三方言只读会话、introspection 与 keyset 查询构造层`
- **退出门禁：** sqlite 真库用例绿；mysql/pg 离线构造断言绿。

### Task 5：DatabaseConnector 表级模式（核心）

- **Files:** Create `src/connectors/database_connector.py`；Modify `src/connectors/__init__.py`；Test `tests/test_database_connector.py`。
- **要点：**
  - `_DbConfig.from_spec(spec)`：仿 `_HttpApiConfig.from_spec` 严格校验（mode/table/fields/filters op 白名单/time_range ISO 解析），缺项抛 ValueError（→ fatal）。
  - `DatabaseConnector(artifact_store=None, credentials=None, credential_resolver=None, engine_factory=None)`：`engine_factory` 注入便于测试替换；凭证解析失败 = fatal。
  - `probe(spec)`：连接 + 只读会话 + 版本/默认 schema，返回 `ProbeResult(sample={dialect, server_version, default_schema, table_count})`。
  - `discover(spec)`：§3.3 摘要（表数超 `data_prep_db_max_discovery_tables` 截断 + warning）。
  - `read(spec, checkpoint)`：异步生成器——`to_thread` 内每批 fetch → 行归一化 → JSONL 序列化（`\n` 分隔 UTF-8 字节）→ `ArtifactStore.write_raw(..., media_type="application/x-ndjson", ext=".jsonl", uri=脱敏定位符, request_snapshot={mode,dialect,database,schema,table,fields,filter_fields,filters_digest,time_range,part_no})` → `yield RecordBatch(artifacts=[a], checkpoint=按 §3.2 编码, byte_count=len(bytes))`；末批 `is_final=True`；限制超限截断 + warning；断连 retryable 重连续读（§3.5）。
  - `capabilities()`：READ_ONLY/SUPPORTS_CHECKPOINT/STREAMING/INCREMENTAL/SCHEMA_PROBE/RANDOM_ACCESS。
  - `close()`：`engine.dispose()` 兜底。
- **测试（约 25 个，sqlite 真库 tmp_path + ArtifactStore 清理，模式仿 test_http_api_connector.py 的 asyncio.run/_collect）：** 配置校验全分支、probe/discover、全量分批读取（行数>batch_size 验证多 artifact）、字段选择、filters 全 op、time_range、水位线增量（last_value 过滤）、复合主键 keyset、无主键表警告路径、大字段截断、BLOB base64、Decimal/datetime 归一化、空表、只读强制（连接后尝试写被拒→fatal 分类）、artifact 脱敏（无密码/user）、checkpoint 编码往返、断连恢复（engine_factory 注入故障连接）、max_records/max_bytes/max_seconds 截断。
- **提交：** `feat: DatabaseConnector 表级只读抽取（keyset 分页 + RawArtifact 落盘）`
- **退出门禁：** sqlite 全量/增量/恢复用例绿；既有基线绿。

### Task 6：受控 SQL 模式接入（flag 门控）

- **Files:** Modify `src/connectors/database_connector.py`（read 分支 `mode="sql"`）；Test `tests/test_database_connector_sql.py`。
- **要点：** mode="sql" 时 `sql_guard.validate_select(sql, allowed_tables=options.allowed_tables 或 discover 全量)` → 原样执行（无改写、无包裹），行数由 fetch 上限 + 语句超时控制；flag 关闭时 `from_spec` 直接拒绝（fatal）；checkpoint 对 sql 模式仅在含 `incremental.cursor_field` 且用户 SQL 含可识别排序时支持——**首版 sql 模式不做断点续跑**（warning 声明），keyset 续跑是表级模式独占能力。
- **测试（约 10 个）：** flag 关闭拒绝、合法 JOIN/子查询/UNION 放行执行、攻击样例在连接器层被拦、行上限截断、系统表拒绝。
- **提交：** `feat: 受控单 SELECT 模式（sql_guard + 默认关闭开关）`
- **退出门禁：** 攻击样例零穿透；flag 默认 False 的 API 行为测试在 Task 9 补齐。

### Task 7：增量与 checkpoint 跨运行续跑

- **Files:** Modify `src/api/store.py`（`update_data_prep_task(..., checkpoint_json=)` setter/getter）；Modify `src/api/routes/data_tasks.py`（rerun 注入 `incremental.last_value`）；Test `tests/test_db_incremental_resume.py`。
- **要点：** 任务成功/中断时 acquire 产出的最终 checkpoint 落 `data_prep_tasks.checkpoint_json`；rerun 时若 `incremental.strategy=="watermark"` 且有 `last_key/watermark` → 注入 `incremental.last_value` 续跑（显式请求参数 `start_from` 优先）；rerun 现有“清空 task_dir”逻辑保留（part_no 从 checkpoint.next_part_no 续编号避免覆盖语义冲突——清空后从 0 重编也行，但 checkpoint 记录 truth，选后者并在 docstring 说明）。
- **测试（约 8 个）：** 首轮全量→追加数据→二轮增量只读新增、显式 start_from、无 checkpoint 时 rerun 全量重读、复合主键 last_key 恢复、中断模拟（读到一半 fatal 后重进）。
- **提交：** `feat: 水位线增量与跨运行 checkpoint 续跑`
- **退出门禁：** 不重复读取断言（行级集合比对）全绿。

### Task 8：graph 接线（acquire_node database 分支 + checkpoint 传递）

- **Files:** Modify `src/data_prep/graph.py`（acquire_node 加 `elif src.source_type.value == "database":` 分支：`DatabaseConnector(credential_resolver=默认实现)`，`adapter.read(src, checkpoint=state.get("checkpoint"))`，聚合末批 checkpoint 进返回 `{"checkpoint": last_ckpt}`；分支结构完全仿现有三分支）；Test `tests/test_db_pipeline_e2e.py`。
- **要点：** 不动既有三分支一行代码；`parse_node` 的 `checkpoint.processed_artifact_ids` 跳过重解析机制对 DB artifact 自然生效；acquire 摘要对 database 源加一行脱敏描述（表名+批次）。
- **测试（约 6 个）：** sqlite 端到端（spec→acquire→parse→clean→quality→manifest）JSONL+Parquet 产物真实生成、账本一致、Manifest 无密码/host 仅脱敏定位符、fatal 凭证错误→FAILED。
- **提交：** `feat: 数据准备图接入 database 源（acquire 分支 + checkpoint 透传）`
- **退出门禁：** E2E 绿 + 206 基线零回归。

### Task 9：API（connections CRUD/test/schema + data_tasks database 源）

- **Files:** Modify `src/api/routes/data_sources.py`（新增 `DbConnectionIn`/`DbConnectionTestIn` Pydantic 模型；`POST/GET /api/data-sources/connections`、`GET/DELETE .../connections/{id}`、`POST .../connections/test`、`GET .../connections/{id}/schema`）；Modify `src/api/routes/data_tasks.py`（`PreviewSourceIn/TaskCreateSourceIn` Literal 加 `"database"` + `connection_id/table/fields/filters/time_range/incremental/start_from` 字段；`_source_spec()` 加 database 分支：归属校验→构造 SourceSpec（credential_ref 引用，**不解析明文**）；preview 分支：表级 LIMIT sample 读取→JsonXmlParser→ProfileAccumulator schema 推断，复用现有返回结构）；Test `tests/test_db_connection_api.py`、`tests/test_data_task_api.py` 追加 database 类。
- **要点：** 全部端点 `get_current_user` + 归属 404；`mode="sql"` 入参在 API 层按 flag 直接 400；测试沿用 monkeypatch settings 路径 + `dependency_overrides` 绕 JWT + TestClient 模式；连接测试的错误消息分类（认证失败/不可达/只读设置失败）但不回栈。
- **测试（约 20 个）：** CRUD/跨用户 404/脱敏响应/草案内联测试/schema 摘要/preview 样本+schema/创建任务 database 全参/flag 关闭拒 sql/非法 filters op 400/任务记录无密码。
- **提交：** `feat: 数据库连接管理与 database 任务 API`
- **退出门禁：** API 测试绿；手工 `uvicorn` 起服务 curl 一遍 connections/test。

### Task 10：前端（连接管理 + 表列候选 + 预览执行，无 SQL 文本框）

- **Files:** Modify `frontend/src/lib/dataPrepApi.ts`（`DataTaskSource` 联合加 `{source_type:"database"; connection_id; table; fields?; filters?; time_range?; incremental?}`；`testConnection` 改为按 source_type 分派的新签名；新增 `listDbConnections/createDbConnection/deleteDbConnection/testDbConnection/getDbSchema`）；Modify `frontend/src/types/dataPrep.ts`（`DbConnection/DbSchemaSummary` 类型）；Create `frontend/src/components/data-prep/DbSourceForm.tsx`；Modify `frontend/src/pages/DataPrepPage.tsx`（sourceType useState 联合加 `"database"` + 表单分支 + `currentSource()`）。
- **要点（DbSourceForm 状态机）：** 连接下拉（listDbConnections）+ “新建连接”对话框（dialect 选择→sqlite 显示路径输入、mysql/pg 显示 host/port/db/user/password）→ “测试连接”按钮（testDbConnection 内联草案）→ 保存 → 选中后 `getDbSchema` 加载 schema/表 → 选表 → 列多选（默认全选，显示类型与 PK 徽标）→ 水位线字段下拉（候选=PK 列+datetime 列）→ 可选时间范围 → 复用现有预览/执行/产物面板。**首版无 SQL 输入框**；页面文案注明“只读取数，不修改源库”。
- **测试：** `npm --prefix frontend run build`（TS 检查）；浏览器手工 E2E 清单见 §5。
- **提交：** `feat: 数据准备页数据库源表单与连接管理`
- **退出门禁：** build 绿；DataPrepPage 既有 upload_file/http_api 路径手工无回归。

### Task 11：三库测试矩阵补齐 + 安全专项 + 性能基线

- **实际 Files:** `tests/test_db_live_containers.py`（Testcontainers MySQL/PostgreSQL）；`tests/test_phase3_completion.py`（凭证卫生、API/流水线和分页边界）；`tests/test_phase3_db_performance.py`；`tests/test_sql_guard_properties.py`（Hypothesis 属性测试）。
- **要点：** sqlite 用例常驻（tmp_path 建库）；mysql/pg 用例镜像 sqlite 核心场景；性能：10 万行（行均约 1KB）全量读取+落盘 < 60s 且峰值 RSS 增量 < 300MB（仿 Phase 2 性能测试写法）。
- **提交：** `test: 三库测试矩阵、凭证卫生与读侧性能基线`
- **退出门禁：** Phase 3 定向套件绿；`--run-db-live` 在 Docker 环境显式跑通并记录输出到 handoff；性能用例通过。

### Task 12：文档收尾与发布门禁

- **Files:** Modify `AGENTS.md`/`README_AGENT.md`（数据库源能力、options 键约定、只读账户建议、`data_prep_db_*` 配置说明）、`plan.md`（Phase 3 状态）、`handoff.md`（完成证据与边界）；确认 `docs/schemas/SourceSpec.json` 与现状一致（models.py 未变，无需重导出）。
- **要点：** 明确写入发布边界：受控 SQL 默认关闭、多表/JOIN 仅经受控 SQL（UI 不开放）、sqlite 源限 `data_prep_db_sqlite_root`、mysql/pg 实测依赖外部 DSN。
- **提交：** `docs: Phase 3 完成状态与发布边界`
- **退出门禁：** §5 全部门禁项。

---

## 5. 测试矩阵（三方言 × 场景）

标注：S=sqlite（常驻 CI）、M=MySQL（db_live）、P=PostgreSQL（db_live）。“●”=必测，“○”=离线构造断言覆盖（Task 4 编译字符串比对，无需真库）。

| 场景 | S | M | P | 所在测试文件 |
|---|---|---|---|---|
| 全量表级读取（多批次，行数 > batch_size） | ● | ● | ● | test_database_connector / test_db_live_* |
| 字段选择（含保留字列名 quoting） | ● | ○ | ○ | 同上 + test_db_dialects |
| filters 全操作符（eq/ne/gt/ge/lt/le/in/is_null/not_null/contains，参数绑定） | ● | ○ | ○ | 同上 |
| 时间范围（>= start AND < end） | ● | ● | ○ | 同上 |
| 单列 PK keyset 增量（last_value 续跑，零重复） | ● | ● | ● | test_db_incremental_resume |
| 复合主键 keyset（row-value 比较） | ● | ○ | ○ | test_database_connector |
| 无主键无水位线表（全量单轮 + 不可续跑 warning） | ● | ○ | ○ | 同上 |
| 时间水位线增量（追加数据后二轮只读新增） | ● | ● | ○ | test_db_incremental_resume |
| 断连恢复（注入连接丢失→重连→从 last_key 续读不重复） | ● | ● | ○ | test_database_connector（engine_factory 注入） |
| 大字段（TEXT>1MB 截断+warning；BLOB→base64） | ● | ○ | ○ | 同上 |
| Decimal/datetime/date/JSON 类型归一化 | ● | ○ | ○ | test_db_dialects |
| 空表 / 全表仅一行 / 恰好等于 batch_size | ● | — | — | test_database_connector |
| SQL 注入（T1–T4 全族：多语句/DDL/DML/PRAGMA/CTE-DML/FOR UPDATE/INTO OUTFILE/危险函数/注释混淆） | ●（guard 层，方言无关） | — | ●（PG 写 CTE 专项） | test_sql_guard |
| 越权表列（系统 schema/未在白名单表/子查询藏表/UNION 拼表） | ● | ● | ● | test_sql_guard + test_db_live_* |
| 只读拒绝（连接后尝试 INSERT/UPDATE/CREATE 被会话层拒 → fatal 分类） | ●（mode=ro） | ● | ● | test_db_live_* + test_database_connector |
| 凭证脱敏（spec_json/artifact/request_snapshot/Manifest/API 响应/日志无密码与完整 DSN） | ● | ○ | ○ | test_db_credential_hygiene |
| 跨用户连接越权（404） | ●（API 层） | — | — | test_db_connection_api |
| sqlite 路径越界/平台自用库拒绝 | ● | — | — | test_db_security |
| 云元数据 IP/端口白名单/主机白名单 | ●（resolver 注入） | — | — | test_db_security |
| 语句超时（progress_handler / max_execution_time / statement_timeout） | ● | ● | ○ | test_database_connector + test_db_live_mysql |
| 上限截断（max_records/max_bytes/max_seconds） | ● | — | — | test_database_connector |
| 端到端（database→parse→clean→quality→Manifest，JSONL+Parquet 真实产物） | ● | — | — | test_db_pipeline_e2e |
| 性能（10 万行读+落盘 <60s，RSS 增量 <300MB） | ● | — | — | test_phase3_db_performance（--run-performance） |

---

## 6. 验收门禁与性能基线

**Phase 3 完成定义（全部满足）：**

1. 威胁模型 §1 每条威胁有对应绿测试（T1–T16 映射到 §5 矩阵行）。
2. Phase 3 定向套件：**139 passed**；后续全仓实跑为 **749 passed、4 skipped、0 failed**，默认跳过的真库/性能标记显式运行后 **5 passed**。
3. `pytest tests/test_db_live_containers.py -q --run-db-live`：Testcontainers MySQL 8/PostgreSQL 16 用例全绿，结果记录进 handoff.md；无 Docker 环境时由测试明确 skip。
4. 性能：`pytest tests\test_phase3_db_performance.py -q --run-performance` → 10 万行 < 60s、RSS 增量 < 300MB。
5. 凭证卫生：测试断言 + 人工 grep 复核 `downloads/`、`data/webui.db` 的 `data_prep_tasks.spec_json`、Manifest 不含密码与完整 DSN。
6. 前端 `npm --prefix frontend run build` 通过；Playwright 数据库源表单 E2E 通过，覆盖创建/测试/保存连接、Schema/表列选择；后端 API E2E 覆盖预览与完整任务链。
7. `data_prep_db_custom_sql_enabled=False` 默认下，API 拒绝 mode=sql（400）；显式开启后受控 SQL 攻击样例零穿透。
8. 文档：AGENTS.md/README_AGENT.md/plan.md/handoff.md 更新完成，发布边界写明（见 §7）。

**与 206 基线共跑策略：** 新测试独立文件，不改动既有 fixture/conftest 行为（仅在 `tests/conftest.py` 追加 `--run-db-live` 选项，与 performance 同构）；graph.py 只加 elif 分支；data_tasks.py 只扩 Literal 与字段（旧请求体不受影响）；前端只加新分支。每个 Task 的退出门禁都包含“基线绿”。

---

## 7. 明确不做（本阶段边界）

1. **前端任意 SQL 文本框**（受控 SQL 仅后端能力 + flag 默认关闭；UI 开放留待后续阶段单独立项评审）。
2. 多表/JOIN 的表级模式（v1 单表；JOIN 仅经受控 SQL 后端模式）。
3. 写库输出/Sink（`conductor/db_writer.py` 的“入库”语义与本阶段“取数”严格分离；settings 的 `mysql_*` 键不复用为取数源）。
4. 媒体抽取、文档语义/OCR、认证浏览器、S3/对象存储（Phase 4/5）。
5. 管理员共享连接、跨用户连接引用、LLM 参与凭证处理或查询生成。
6. SSL/TLS 细粒度证书配置、SSH 隧道、Kerberos/云 IAM 认证（首版走驱动默认 TLS 选项，文档标注）。
7. 后台队列化执行与 500MB 级 DB 抽取实测（Phase 5 工程化）；本阶段性能门禁止于 10 万行读侧。
8. 连接池常驻/长连接复用（NullPool 每批连接，刻意取舍，文档记录）。
9. 存量 rerun 的 RawArtifact 复用（沿用 Phase 2 边界，Phase 5 统一增量模型）。
10. 不引入新异步驱动、不引入 ORM 会话模型、不改 `src/data_prep/models.py`、不重导出 docs/schemas（models 未变）。

---

## 执行备注

1. **SQL 方言/AST 版本漂移**：SQLGlot 已锁版；升级前必须复跑示例攻击集与 Hypothesis 属性测试。
2. **JsonXmlParser 的 record_id 生成细节**（`src/parsers/json_xml.py` JSONL 信封构造）在 Task 5 联调时确认其对 DB 行字典生成稳定 record_id（内容哈希即可满足账本与去重需求）。
3. 工作区有 handoff 点名保留的本地改动（`.claude/settings.local.json`、`scripts/test_library_dedup_scanner.py`、`src/api/library_dedup_scanner.py`），每个提交精确 `git add` 路径，不裹挟无关改动。
