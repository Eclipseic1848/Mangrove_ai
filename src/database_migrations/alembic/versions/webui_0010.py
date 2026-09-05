"""将来源取消请求与实际读取静默分开记录。"""
from __future__ import annotations

from alembic import op

revision = "webui_0010"
down_revision = "webui_0009"
branch_labels = None
depends_on = None
operation_summary = (
    "为来源 Attempt 增加持久化取消请求与刷新任务关联，停止确认后才进入终态",
    "为任务增加取消代数，拒绝停止前发起的迟到修订",
)


def upgrade() -> None:
    connection = op.get_bind().connection.driver_connection
    columns = {row[1] for row in connection.execute("PRAGMA table_info(source_acquisition_attempts)")}
    if "cancel_requested_at" not in columns:
        op.execute("ALTER TABLE source_acquisition_attempts ADD COLUMN cancel_requested_at TEXT")
    if "request_context" not in columns:
        op.execute("ALTER TABLE source_acquisition_attempts ADD COLUMN request_context TEXT NOT NULL DEFAULT ''")
    task_columns = {row[1] for row in connection.execute("PRAGMA table_info(semantic_workspace_tasks)")}
    if "cancel_generation" not in task_columns:
        op.execute("ALTER TABLE semantic_workspace_tasks ADD COLUMN cancel_generation INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    raise RuntimeError("取消事实须保留；请通过显式备份恢复降级")
