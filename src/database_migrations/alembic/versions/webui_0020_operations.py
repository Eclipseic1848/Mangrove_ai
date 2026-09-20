"""运营事件与请求回执分开追加，保留既有业务和安全审计数据。"""
from alembic import op

revision = "webui_0020"
down_revision = "webui_0019"
branch_labels = None
depends_on = None
operation_summary = ("新增低敏运营日志、请求结果、前台会话与个人视图；不回填历史行为",)

STATEMENTS = (
    """CREATE TABLE operations_policy (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        retention_days INTEGER NOT NULL DEFAULT 180 CHECK(retention_days IN (90,180)),
        started_at REAL NOT NULL, last_pruned_at REAL NOT NULL DEFAULT 0,
        version INTEGER NOT NULL DEFAULT 1
    )""",
    "INSERT INTO operations_policy(singleton,started_at) VALUES (1,CAST(strftime('%s','now') AS REAL))",
    """CREATE TABLE operations_events (
        event_id TEXT PRIMARY KEY, occurred_at REAL NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('login','visit','action','access')),
        actor_id TEXT, actor_role TEXT,
        module TEXT NOT NULL, action TEXT NOT NULL, object_ref TEXT NOT NULL DEFAULT '',
        ip_mask TEXT NOT NULL DEFAULT '', device TEXT NOT NULL DEFAULT '未知',
        source TEXT NOT NULL DEFAULT 'unknown', session_ref TEXT NOT NULL DEFAULT '',
        client_id TEXT, UNIQUE(actor_id,client_id)
    )""",
    "CREATE INDEX idx_operations_time ON operations_events(occurred_at,event_id)",
    "CREATE INDEX idx_operations_actor ON operations_events(actor_id,kind,occurred_at)",
    """CREATE TABLE operations_outcomes (
        event_id TEXT PRIMARY KEY REFERENCES operations_events(event_id) ON DELETE CASCADE,
        result TEXT NOT NULL CHECK(result IN ('success','failure','unknown')),
        actor_id TEXT, actor_role TEXT, subject_id TEXT, subject_role TEXT,
        status_code INTEGER, duration_ms INTEGER,
        changes_json TEXT NOT NULL DEFAULT '[]', completed_at REAL NOT NULL
    )""",
    """CREATE TABLE operations_presence (
        actor_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        actor_role TEXT NOT NULL,
        session_ref TEXT NOT NULL, tab_id TEXT NOT NULL, started_at REAL NOT NULL,
        last_seen REAL NOT NULL, active_seconds REAL NOT NULL DEFAULT 0,
        PRIMARY KEY(actor_id,session_ref,tab_id)
    )""",
    """CREATE TABLE operations_views (
        owner_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        view_id TEXT NOT NULL, name TEXT NOT NULL, filters_json TEXT NOT NULL,
        PRIMARY KEY(owner_id,view_id)
    )""",
    """CREATE TRIGGER operations_events_no_update BEFORE UPDATE ON operations_events
        BEGIN SELECT RAISE(ABORT,'运营事件不可修改'); END""",
    """CREATE TRIGGER operations_outcomes_no_update BEFORE UPDATE ON operations_outcomes
        BEGIN SELECT RAISE(ABORT,'运营结果不可修改'); END""",
)


def upgrade():
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade():
    raise RuntimeError("不得丢失新日志；请使用经过验证的显式备份恢复")
