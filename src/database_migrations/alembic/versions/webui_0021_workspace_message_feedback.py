"""扩展工作台反馈目标；保留既有正式输出反馈。"""

from alembic import op

revision = "webui_0021"
down_revision = "webui_0020"
branch_labels = None
depends_on = None
operation_summary = ("扩展工作台反馈以绑定消息结果与运行身份",)

OLD_FEEDBACK_COLUMNS = (
    "id", "user_id", "task_id", "revision", "output_id", "output_sha256",
    "rating", "reasons", "comment", "created_at", "status", "admin_note",
    "version", "request_key", "request_hash", "deleted_at",
)
OLD_RECEIPT_COLUMNS = (
    "user_id", "request_key", "request_hash", "task_id", "revision",
    "output_id", "feedback_id", "version", "created_at", "result",
    "failure_code",
)


def _columns(connection, table):
    return tuple(row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})"))


def upgrade():
    connection = op.get_bind()
    if _columns(connection, "workspace_feedback") != OLD_FEEDBACK_COLUMNS:
        raise RuntimeError("原工作台反馈表形状不匹配")
    if _columns(connection, "workspace_feedback_receipts") != OLD_RECEIPT_COLUMNS:
        raise RuntimeError("原工作台反馈收据表形状不匹配")

    # SQLite 不能安全地原位移除 NOT NULL；在同一事务内复制后替换，旧行明确归类为 output。
    connection.exec_driver_sql("DROP VIEW feedback_management")
    connection.exec_driver_sql("""CREATE TABLE workspace_feedback_v21 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,task_id TEXT NOT NULL,
        revision INTEGER NOT NULL,target_kind TEXT NOT NULL CHECK(target_kind IN ('output','message')),
        output_id TEXT,output_sha256 TEXT,result_id TEXT,turn_id TEXT,run_id TEXT,
        rating TEXT NOT NULL CHECK(rating IN ('up','down')),reasons TEXT NOT NULL,
        comment TEXT NOT NULL,created_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
        admin_note TEXT,version INTEGER NOT NULL,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,
        deleted_at TEXT,UNIQUE(user_id,request_key),
        CHECK((target_kind='output' AND output_id IS NOT NULL AND output_sha256 IS NOT NULL AND result_id IS NULL AND turn_id IS NULL AND run_id IS NULL)
           OR (target_kind='message' AND output_id IS NULL AND output_sha256 IS NULL AND result_id IS NOT NULL AND turn_id IS NOT NULL))
    )""")
    connection.exec_driver_sql("""INSERT INTO workspace_feedback_v21
        (id,user_id,task_id,revision,target_kind,output_id,output_sha256,result_id,turn_id,run_id,
         rating,reasons,comment,created_at,status,admin_note,version,request_key,request_hash,deleted_at)
        SELECT id,user_id,task_id,revision,'output',output_id,output_sha256,NULL,NULL,NULL,
               rating,reasons,comment,created_at,status,admin_note,version,request_key,request_hash,deleted_at
        FROM workspace_feedback""")
    connection.exec_driver_sql("""CREATE TABLE workspace_feedback_receipts_v21 (
        user_id TEXT NOT NULL,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,
        task_id TEXT NOT NULL,revision INTEGER NOT NULL,target_kind TEXT NOT NULL CHECK(target_kind IN ('output','message')),
        output_id TEXT,result_id TEXT,turn_id TEXT,run_id TEXT,
        feedback_id INTEGER NOT NULL,version INTEGER NOT NULL,created_at TEXT NOT NULL,
        result TEXT NOT NULL CHECK(result IN ('saved','rejected')),failure_code TEXT,
        PRIMARY KEY(user_id,request_key),
        CHECK((target_kind='output' AND output_id IS NOT NULL AND result_id IS NULL AND turn_id IS NULL AND run_id IS NULL)
           OR (target_kind='message' AND output_id IS NULL AND result_id IS NOT NULL)),
        CHECK((result='saved' AND feedback_id>0 AND version>0 AND failure_code IS NULL)
           OR (result='rejected' AND feedback_id=0 AND version=0 AND failure_code IS NOT NULL)),
        CHECK(result='rejected' OR target_kind='output' OR turn_id IS NOT NULL)
    )""")
    connection.exec_driver_sql("""INSERT INTO workspace_feedback_receipts_v21
        (user_id,request_key,request_hash,task_id,revision,target_kind,output_id,result_id,turn_id,run_id,
         feedback_id,version,created_at,result,failure_code)
        SELECT user_id,request_key,request_hash,task_id,revision,'output',output_id,NULL,NULL,NULL,
               feedback_id,version,created_at,result,failure_code
        FROM workspace_feedback_receipts""")
    connection.exec_driver_sql("DROP TABLE workspace_feedback_receipts")
    connection.exec_driver_sql("DROP TABLE workspace_feedback")
    connection.exec_driver_sql("ALTER TABLE workspace_feedback_v21 RENAME TO workspace_feedback")
    connection.exec_driver_sql("ALTER TABLE workspace_feedback_receipts_v21 RENAME TO workspace_feedback_receipts")
    connection.exec_driver_sql("CREATE UNIQUE INDEX uq_workspace_feedback_output ON workspace_feedback(user_id,task_id,revision,output_id) WHERE target_kind='output'")
    connection.exec_driver_sql("CREATE UNIQUE INDEX uq_workspace_feedback_message ON workspace_feedback(user_id,task_id,revision,result_id) WHERE target_kind='message'")
    connection.exec_driver_sql("""CREATE VIEW feedback_management AS
        SELECT id,message_id,conv_id,user_id,rating,reasons,comment,created_at,status,admin_note,
               'message' AS source_kind,NULL AS task_id,NULL AS revision,NULL AS target_kind,
               NULL AS output_id,NULL AS output_sha256,NULL AS result_id,NULL AS turn_id,NULL AS run_id
        FROM message_feedback
        UNION ALL
        SELECT -id,NULL,NULL,user_id,rating,reasons,comment,created_at,status,admin_note,
               'workspace',task_id,revision,target_kind,output_id,output_sha256,result_id,turn_id,run_id
        FROM workspace_feedback WHERE deleted_at IS NULL""")


def downgrade():
    raise RuntimeError("请使用经核验的迁移备份恢复，不能丢弃消息反馈身份")
