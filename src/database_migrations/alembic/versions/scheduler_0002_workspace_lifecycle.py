"""显式工作台生命周期迁移；已有对象先完整校验，不重写未知结构。"""
from alembic import op
import sqlite3
revision='scheduler_0002'
down_revision='scheduler_0001'
branch_labels=None
depends_on=None
operation_summary=('新增冻结计划/正式反馈工作台持久结构',)
SQL="\nCREATE TABLE scheduled_workspace_bindings (\n schedule_id TEXT PRIMARY KEY REFERENCES scheduled_tasks(task_id), owner_id TEXT NOT NULL,\n source_task_id TEXT NOT NULL, source_revision INTEGER NOT NULL, payload_json TEXT NOT NULL,\n contract_json TEXT NOT NULL, request_key TEXT NOT NULL, request_hash TEXT NOT NULL,\n timezone TEXT NOT NULL, UNIQUE(owner_id,request_key)\n);\nCREATE TABLE scheduled_workspace_occurrences (\n occurrence_id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL REFERENCES scheduled_tasks(task_id),\n owner_id TEXT NOT NULL, config_hash TEXT NOT NULL, due_at TEXT NOT NULL, manual INTEGER NOT NULL,\n request_key TEXT NOT NULL, state TEXT NOT NULL, workspace_task_id TEXT,\n workspace_revision INTEGER, runtime_run_id TEXT, output_ids_json TEXT NOT NULL DEFAULT '[]',\n error_code TEXT, generation INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,\n UNIQUE(owner_id,request_key)\n);\n"

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
