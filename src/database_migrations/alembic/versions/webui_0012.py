"""固定八类执行绑定与持久账号停用操作。"""
import json

from alembic import op

revision = "webui_0012"
down_revision = "webui_0011"
branch_labels = None
depends_on = None
operation_summary = (
    "新增账号执行代数、固定执行绑定与持久停止证明",
    "回填可确定 Owner 的历史根执行，停用账号旧执行保持阻断",
)

STATEMENTS = (
    "ALTER TABLE users ADD COLUMN execution_generation INTEGER NOT NULL DEFAULT 0 CHECK(execution_generation>=0)",
    "ALTER TABLE delivery_publish_intents ADD COLUMN execution_generation INTEGER NOT NULL DEFAULT 0 CHECK(execution_generation>=0)",
    """CREATE TABLE account_execution_bindings (
        owner_user_id TEXT NOT NULL, resource_kind TEXT NOT NULL
            CHECK(resource_kind IN ('workspace','data','harness','source','validation','chat','candidate','schedule')),
        resource_id TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>=0),
        state TEXT NOT NULL CHECK(state IN ('active','idle','paused','cleanup_failed')),
        updated_at REAL NOT NULL,
        PRIMARY KEY(owner_user_id,resource_kind,resource_id)
    )""",
    "CREATE INDEX idx_account_execution_generation ON account_execution_bindings(owner_user_id,generation,state)",
    """CREATE TABLE account_execution_holds (
        operation_id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(generation>=1), actor_user_id TEXT NOT NULL,
        reason TEXT NOT NULL CHECK(reason IN ('disabled','pending')),
        status TEXT NOT NULL CHECK(status IN ('pending','completed','failed')),
        reconciliation_complete INTEGER NOT NULL DEFAULT 0 CHECK(reconciliation_complete IN (0,1)),
        error_code TEXT CHECK(error_code IN ('scheduler_unavailable','holder_unknown','cleanup_failed','reconciliation_failed')),
        created_at REAL NOT NULL, updated_at REAL NOT NULL,
        UNIQUE(owner_user_id,generation)
    )""",
)

# 仅已结束状态可证明没有待继续执行；取消及未知外部结果需运行时核对。
IDLE = frozenset({'succeeded', 'completed', 'passed', 'failed', 'inconclusive'})
ROOTS = (
    ('workspace', 'semantic_workspace_tasks', 'user_id', 'task_id'),
    ('data', 'data_prep_tasks', 'user_id', 'task_id'),
    ('harness', 'semantic_harness_runs', 'user_id', 'run_id'),
    ('source', 'source_acquisition_attempts', 'owner_id', 'attempt_id'),
    ('validation', 'capability_validation_runs', 'owner_id', 'run_id'),
    ('candidate', 'candidate_verification_attempts', 'owner_id', 'attempt_id'),
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
    conn = op.get_bind()
    conn.exec_driver_sql("UPDATE users SET execution_generation=1 WHERE disabled<>0 OR pending<>0")
    # 历史 Grant 没有执行代数；持久撤销防止重新启用后复活，保留既有撤销证据。
    conn.exec_driver_sql("""UPDATE model_connection_grants
        SET revoked_at=strftime('%Y-%m-%dT%H:%M:%f','now'),revoke_reason='account_execution_hold'
        WHERE revoked_at IS NULL AND owner_user_id IN
            (SELECT user_id FROM users WHERE disabled<>0 OR pending<>0)""")
    owners = dict(conn.exec_driver_sql("SELECT user_id,execution_generation FROM users").fetchall())

    def bind(owner, kind, resource_id, status):
        # 不猜测缺失 Owner，更不能在服务启动时补成最新授权。
        if owner not in owners:
            return
        state = 'idle' if str(status).lower() in IDLE else 'active'
        if owners[owner] and state == 'idle':
            state = 'paused'
        conn.exec_driver_sql(
            "INSERT INTO account_execution_bindings VALUES (?,?,?,0,?,0)",
            (owner, kind, resource_id, state),
        )

    for kind, table, owner_column, id_column in ROOTS:
        for owner, resource_id, status in conn.exec_driver_sql(
            f"SELECT {owner_column},{id_column},status FROM {table}"
        ).fetchall():
            bind(owner, kind, resource_id, status)
    for resource_id, status, payload in conn.exec_driver_sql(
        "SELECT run_id,status,payload_json FROM capability_platform_validation_runs"
    ).fetchall():
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and isinstance(data.get('actor_id'), str):
            bind(data['actor_id'], 'validation', 'platform:' + resource_id, status)
    # scheduler 与 Chat 的持有者需显式核对；空扫描不能提前宣告完成。
    conn.exec_driver_sql("""INSERT INTO account_execution_holds
        SELECT 'migration:' || user_id,user_id,1,'system:migration',
            CASE WHEN disabled<>0 THEN 'disabled' ELSE 'pending' END,
            'pending',0,NULL,0,0 FROM users WHERE execution_generation=1""")


def downgrade() -> None:
    raise RuntimeError("执行阻断与停止证明须保留；请通过显式备份恢复降级")
