"""独立、不可变的反馈正文访问证据。"""
from alembic import op

revision = 'webui_0013'
down_revision = 'webui_0012'
branch_labels = None
depends_on = None
operation_summary = ('新增不可变反馈正文访问证据，不复制正文、不回填历史访问',)

STATEMENTS = (
    """CREATE TABLE feedback_content_access (
        event_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL,
        actor_role TEXT NOT NULL CHECK(actor_role IN ('admin','super_admin')),
        idempotency_key TEXT NOT NULL, reason TEXT NOT NULL CHECK(length(reason) BETWEEN 5 AND 1000),
        action TEXT NOT NULL CHECK(action='feedback_content_read'),
        feedback_id INTEGER NOT NULL, message_id INTEGER,
        conv_id TEXT, owner_id TEXT,
        request_digest TEXT NOT NULL, response_digest TEXT NOT NULL,
        content_bytes INTEGER NOT NULL CHECK(content_bytes BETWEEN 0 AND 2097152),
        truncated INTEGER NOT NULL CHECK(truncated IN (0,1)),
        result TEXT NOT NULL CHECK(result IN ('success','failure')),
        failure_code TEXT, created_at TEXT NOT NULL,
        CHECK((result='success' AND failure_code IS NULL AND message_id IS NOT NULL AND conv_id IS NOT NULL AND owner_id IS NOT NULL)
           OR (result='failure' AND failure_code IS 'feedback_unavailable' AND message_id IS NULL AND conv_id IS NULL AND owner_id IS NULL)),
        UNIQUE(actor_id,idempotency_key)
    )""",
    """CREATE TRIGGER feedback_content_access_no_update BEFORE UPDATE ON feedback_content_access
       BEGIN SELECT RAISE(ABORT,'immutable audit'); END""",
    """CREATE TRIGGER feedback_content_access_no_delete BEFORE DELETE ON feedback_content_access
       BEGIN SELECT RAISE(ABORT,'immutable audit'); END""",
    # SQLite REPLACE 默认不触发删除触发器，必须在插入处阻止覆盖既有证据。
    """CREATE TRIGGER feedback_content_access_no_replace BEFORE INSERT ON feedback_content_access
       WHEN EXISTS(SELECT 1 FROM feedback_content_access WHERE event_id=NEW.event_id
         OR (actor_id=NEW.actor_id AND idempotency_key=NEW.idempotency_key))
       BEGIN SELECT RAISE(ABORT,'immutable audit'); END""",
)


def upgrade():
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade():
    raise RuntimeError('审计证据必须保留；请通过显式备份恢复降级')
