"""持久化 Cookie 失效阻塞收据；恢复依赖凭据身份变化。"""

import sqlite3

from alembic import op


revision = "scheduler_0003"
down_revision = "scheduler_0002"
branch_labels = None
depends_on = None
operation_summary = ("新增 Owner Cookie 失效后的计划恢复收据",)

SQL = """
CREATE TABLE scheduled_credential_blocks (
 task_id TEXT PRIMARY KEY REFERENCES scheduled_tasks(task_id),
 owner_user_id TEXT NOT NULL,
 credential_key TEXT NOT NULL,
 credential_identity TEXT NOT NULL,
 execution_task_id TEXT NOT NULL,
 generation INTEGER NOT NULL,
 manual INTEGER NOT NULL CHECK (manual IN (0, 1)),
 resume_requested INTEGER NOT NULL DEFAULT 0 CHECK (resume_requested IN (0, 1)),
 created_at TEXT NOT NULL
);
"""


def upgrade():
    connection = op.get_bind()
    expected = sqlite3.connect(":memory:")
    try:
        expected.executescript(SQL)
        expected_sql = expected.execute(
            "SELECT sql FROM sqlite_master WHERE name='scheduled_credential_blocks'"
        ).fetchone()[0]
        actual = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name='scheduled_credential_blocks'"
        ).fetchone()
        if actual and " ".join(actual[0].split()) != " ".join(expected_sql.split()):
            raise RuntimeError("已有 Cookie 恢复结构不兼容")
        if actual is None:
            connection.exec_driver_sql(SQL.strip())
    finally:
        expected.close()


def downgrade():
    raise RuntimeError("请使用经核验的迁移备份恢复，不能删除持久恢复收据")
