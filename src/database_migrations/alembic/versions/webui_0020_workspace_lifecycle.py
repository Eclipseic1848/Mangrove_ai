"""显式工作台生命周期迁移；已有对象先完整校验，不重写未知结构。"""
from alembic import op
import sqlite3
revision='webui_0020'
down_revision='webui_0019'
branch_labels=None
depends_on=None
operation_summary=('新增冻结计划/正式反馈工作台持久结构',)
SQL="\nCREATE TABLE workspace_feedback (\n id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,task_id TEXT NOT NULL,\n revision INTEGER NOT NULL,output_id TEXT NOT NULL,output_sha256 TEXT NOT NULL,\n rating TEXT NOT NULL CHECK(rating IN ('up','down')),reasons TEXT NOT NULL,\n comment TEXT NOT NULL,created_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',\n admin_note TEXT,version INTEGER NOT NULL,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,\n deleted_at TEXT,UNIQUE(user_id,task_id,revision,output_id),UNIQUE(user_id,request_key)\n);\nCREATE TABLE workspace_feedback_receipts (\n user_id TEXT NOT NULL,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,\n task_id TEXT NOT NULL,revision INTEGER NOT NULL,output_id TEXT NOT NULL,\n feedback_id INTEGER NOT NULL,version INTEGER NOT NULL,created_at TEXT NOT NULL,\n result TEXT NOT NULL CHECK(result IN ('saved','rejected')),failure_code TEXT,PRIMARY KEY(user_id,request_key),\n CHECK((result='saved' AND feedback_id>0 AND version>0 AND failure_code IS NULL)\n OR (result='rejected' AND feedback_id=0 AND version=0 AND failure_code IS NOT NULL))\n);\nCREATE VIEW feedback_management AS\n SELECT id,message_id,conv_id,user_id,rating,reasons,comment,created_at,status,admin_note,\n 'message' AS source_kind,NULL AS task_id,NULL AS revision,NULL AS output_id,NULL AS output_sha256\n FROM message_feedback\n UNION ALL\n SELECT -id,NULL,NULL,user_id,rating,reasons,comment,created_at,status,admin_note,\n 'workspace',task_id,revision,output_id,output_sha256\n FROM workspace_feedback WHERE deleted_at IS NULL;\nCREATE TABLE workspace_feedback_content_access (\n event_id TEXT PRIMARY KEY,actor_id TEXT NOT NULL,actor_role TEXT NOT NULL CHECK(actor_role IN ('admin','super_admin')),\n idempotency_key TEXT NOT NULL,reason TEXT NOT NULL CHECK(length(reason) BETWEEN 5 AND 1000),\n action TEXT NOT NULL CHECK(action='feedback_content_read'),feedback_id INTEGER NOT NULL CHECK(feedback_id<0),\n message_id INTEGER CHECK(message_id IS NULL),conv_id TEXT CHECK(conv_id IS NULL),owner_id TEXT,\n request_digest TEXT NOT NULL,response_digest TEXT NOT NULL,\n content_bytes INTEGER NOT NULL CHECK(content_bytes BETWEEN 0 AND 2097152),\n truncated INTEGER NOT NULL CHECK(truncated IN (0,1)),result TEXT NOT NULL CHECK(result IN ('success','failure')),\n failure_code TEXT,created_at TEXT NOT NULL,source_identity_json TEXT,\n CHECK((result='success' AND owner_id IS NOT NULL AND source_identity_json IS NOT NULL AND failure_code IS NULL)\n OR (result='failure' AND owner_id IS NULL AND source_identity_json IS NULL AND failure_code IS 'feedback_unavailable')),\n UNIQUE(actor_id,idempotency_key)\n);\nCREATE TRIGGER workspace_feedback_audit_no_update BEFORE UPDATE ON workspace_feedback_content_access BEGIN SELECT RAISE(ABORT,'immutable audit'); END;\nCREATE TRIGGER workspace_feedback_audit_no_delete BEFORE DELETE ON workspace_feedback_content_access BEGIN SELECT RAISE(ABORT,'immutable audit'); END;\nCREATE TRIGGER workspace_feedback_audit_no_replace BEFORE INSERT ON workspace_feedback_content_access WHEN EXISTS(SELECT 1 FROM workspace_feedback_content_access WHERE event_id=NEW.event_id OR (actor_id=NEW.actor_id AND idempotency_key=NEW.idempotency_key)) BEGIN SELECT RAISE(ABORT,'immutable audit'); END;\n"

def statements():
    buffer=''
    for line in SQL.splitlines(True):
        buffer+=line
        if sqlite3.complete_statement(buffer):
            yield buffer.strip();buffer=''
    if buffer.strip():raise RuntimeError('迁移SQL未完整结束')

def upgrade():
    connection=op.get_bind()
    expected=sqlite3.connect(':memory:')
    try:
        expected.executescript(SQL)
        rows=expected.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'").fetchall()
        for kind,name,sql in rows:
            actual=connection.exec_driver_sql('SELECT type,sql FROM sqlite_master WHERE name=?',(name,)).fetchone()
            if actual and (actual[0]!=kind or ' '.join(actual[1].split())!=' '.join(sql.split())):
                raise RuntimeError('已有工作台结构不兼容: '+name)
        for statement in statements():
            # 按SQLite解析后的声明名定位；所有现存对象已在任何DDL前通过核验。
            words=statement.split()
            name=words[2]
            if connection.exec_driver_sql('SELECT 1 FROM sqlite_master WHERE name=?',(name,)).fetchone() is None:
                connection.exec_driver_sql(statement)
    finally:expected.close()

def downgrade():
    raise RuntimeError('请使用经核验的迁移备份恢复，不能删除持久工作台历史')
