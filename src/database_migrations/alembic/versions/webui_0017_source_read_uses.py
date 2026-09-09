"""保存不含正文的来源读取使用事实，供关联删除读取。"""
from alembic import op
revision = "webui_0017"
down_revision = "webui_0016"
branch_labels = None
depends_on = None
operation_summary = ("新增来源读取使用事实，不级联任务删除",)

SQL = "CREATE TABLE source_read_uses (use_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, task_id TEXT, revision INTEGER, operation TEXT NOT NULL, source_refs_json TEXT NOT NULL, state TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT)"


def upgrade():
    connection = op.get_bind()
    columns = connection.exec_driver_sql("PRAGMA table_info(source_read_uses)").fetchall()
    if columns:
        expected = [("use_id","TEXT",0,1),("owner_id","TEXT",1,0),("task_id","TEXT",0,0),("revision","INTEGER",0,0),("operation","TEXT",1,0),("source_refs_json","TEXT",1,0),("state","TEXT",1,0),("started_at","TEXT",1,0),("finished_at","TEXT",0,0)]
        # 已存在的错误形状不能被IF NOT EXISTS掩盖。
        if [(r[1],str(r[2]).upper(),r[3],r[5]) for r in columns] != expected or any(r[4] is not None for r in columns):
            raise RuntimeError("source_read_uses 已有形状不兼容")
    else:
        op.execute(SQL)
    op.execute("CREATE INDEX IF NOT EXISTS ix_source_read_uses_owner_state ON source_read_uses(owner_id,state)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_source_read_uses_owner_task ON source_read_uses(owner_id,task_id)")


def downgrade():
    raise RuntimeError("使用事实必须保留，请通过显式备份恢复降级")
