"""扩展反馈目标至工作台结果，保留旧反馈和不可变访问证据。"""
from alembic import op

revision = "webui_0021"
down_revision = "webui_0020"
branch_labels = None
depends_on = None
operation_summary = ("扩展反馈的任务版本关联；逐行保留旧反馈和访问证据",)

STATEMENTS = (
    """CREATE TABLE message_feedback_next (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id INTEGER, conv_id TEXT, user_id TEXT NOT NULL,
        rating TEXT NOT NULL, reasons TEXT, comment TEXT, created_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', admin_note TEXT,
        task_id TEXT, revision INTEGER, result_id TEXT,
        CHECK((task_id IS NULL AND revision IS NULL AND result_id IS NULL AND message_id IS NOT NULL AND conv_id IS NOT NULL)
           OR (task_id IS NOT NULL AND revision IS NOT NULL AND revision >= 1 AND result_id IS NOT NULL AND message_id IS NULL AND conv_id IS NULL)),
        UNIQUE(message_id,user_id), UNIQUE(task_id,revision,result_id,user_id)
    )""",
    """INSERT INTO message_feedback_next
        (id,message_id,conv_id,user_id,rating,reasons,comment,created_at,status,admin_note)
        SELECT id,message_id,conv_id,user_id,rating,reasons,comment,created_at,status,admin_note FROM message_feedback""",
    """UPDATE sqlite_sequence SET seq=MAX(seq,COALESCE((SELECT seq FROM sqlite_sequence WHERE name='message_feedback'),0))
        WHERE name='message_feedback_next'""",
    "DROP TABLE message_feedback",
    "ALTER TABLE message_feedback_next RENAME TO message_feedback",
    """CREATE TABLE feedback_content_access_next (
        event_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL,
        actor_role TEXT NOT NULL CHECK(actor_role IN ('admin','super_admin')),
        idempotency_key TEXT NOT NULL, reason TEXT NOT NULL CHECK(length(reason) BETWEEN 5 AND 1000),
        action TEXT NOT NULL CHECK(action='feedback_content_read'),
        feedback_id INTEGER NOT NULL, message_id INTEGER, conv_id TEXT, owner_id TEXT,
        request_digest TEXT NOT NULL, response_digest TEXT NOT NULL,
        content_bytes INTEGER NOT NULL CHECK(content_bytes BETWEEN 0 AND 2097152),
        truncated INTEGER NOT NULL CHECK(truncated IN (0,1)),
        result TEXT NOT NULL CHECK(result IN ('success','failure')),
        failure_code TEXT, created_at TEXT NOT NULL,
        task_id TEXT, revision INTEGER, result_id TEXT,
        CHECK((result='success' AND failure_code IS NULL AND owner_id IS NOT NULL AND
            ((message_id IS NOT NULL AND conv_id IS NOT NULL AND task_id IS NULL AND revision IS NULL AND result_id IS NULL)
             OR (message_id IS NULL AND conv_id IS NULL AND task_id IS NOT NULL AND revision IS NOT NULL AND revision >= 1 AND result_id IS NOT NULL)))
           OR (result='failure' AND failure_code IS 'feedback_unavailable' AND message_id IS NULL AND conv_id IS NULL AND owner_id IS NULL
               AND task_id IS NULL AND revision IS NULL AND result_id IS NULL)),
        UNIQUE(actor_id,idempotency_key)
    )""",
    """INSERT INTO feedback_content_access_next
        (event_id,actor_id,actor_role,idempotency_key,reason,action,feedback_id,message_id,conv_id,owner_id,
         request_digest,response_digest,content_bytes,truncated,result,failure_code,created_at)
        SELECT event_id,actor_id,actor_role,idempotency_key,reason,action,feedback_id,message_id,conv_id,owner_id,
         request_digest,response_digest,content_bytes,truncated,result,failure_code,created_at FROM feedback_content_access""",
    "DROP TABLE feedback_content_access",
    "ALTER TABLE feedback_content_access_next RENAME TO feedback_content_access",
    """CREATE TRIGGER feedback_content_access_no_update BEFORE UPDATE ON feedback_content_access
       BEGIN SELECT RAISE(ABORT,'immutable audit'); END""",
    """CREATE TRIGGER feedback_content_access_no_delete BEFORE DELETE ON feedback_content_access
       BEGIN SELECT RAISE(ABORT,'immutable audit'); END""",
    """CREATE TRIGGER feedback_content_access_no_replace BEFORE INSERT ON feedback_content_access
       WHEN EXISTS(SELECT 1 FROM feedback_content_access WHERE event_id=NEW.event_id
         OR (actor_id=NEW.actor_id AND idempotency_key=NEW.idempotency_key))
       BEGIN SELECT RAISE(ABORT,'immutable audit'); END""",
)


def upgrade():
    # 重建只放宽目标关联，不清理旧业务记录；由迁移事务及外部备份保护。
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade():
    raise RuntimeError("反馈和审计必须保留；请从已验证备份恢复")
