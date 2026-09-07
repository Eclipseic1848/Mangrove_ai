"""新增可撤销平台登录会话、共享限流和低敏安全审计。"""
from alembic import op

revision = "webui_0011"
down_revision = "webui_0010"
branch_labels = None
depends_on = None
operation_summary = (
    "新增设备登录会话与已消费刷新摘要，不改写现有用户或任务",
    "新增跨实例限流状态与低敏平台安全审计",
)

STATEMENTS = (
    """CREATE TABLE platform_login_sessions (
        session_id TEXT PRIMARY KEY,
        owner_user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        created_at REAL NOT NULL,
        absolute_expires_at REAL NOT NULL,
        access_expires_at REAL NOT NULL,
        refresh_digest TEXT NOT NULL,
        revoked_at REAL,
        revocation_reason TEXT
    )""",
    "CREATE INDEX idx_platform_sessions_owner ON platform_login_sessions(owner_user_id)",
    """CREATE TABLE platform_spent_refresh (
        session_id TEXT NOT NULL REFERENCES platform_login_sessions(session_id) ON DELETE CASCADE,
        refresh_digest TEXT NOT NULL,
        consumed_at REAL NOT NULL,
        PRIMARY KEY(session_id, refresh_digest)
    )""",
    "CREATE TABLE platform_rate_events (bucket TEXT, subject_digest TEXT, occurred_at REAL)",
    "CREATE INDEX idx_platform_rate_subject ON platform_rate_events(bucket, subject_digest, occurred_at)",
    "CREATE INDEX idx_platform_rate_time ON platform_rate_events(occurred_at)",
    """CREATE TABLE platform_rate_blocks (
        bucket TEXT, subject_digest TEXT, blocked_until REAL,
        PRIMARY KEY(bucket, subject_digest)
    )""",
    """CREATE TABLE platform_security_audit (
        event_id TEXT PRIMARY KEY, actor_user_id TEXT, action TEXT,
        subject_digest TEXT, reason TEXT, result TEXT, occurred_at REAL
    )""",
    "CREATE INDEX idx_platform_security_time ON platform_security_audit(occurred_at)",
)


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("会话撤销与审计事实须保留；请通过显式备份恢复降级")
