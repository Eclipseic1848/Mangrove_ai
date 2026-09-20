"""冻结连接版本是摘要字符串，不能使用 SQLite 数值亲和性。"""
from alembic import op

revision = "scheduler_0003"
down_revision = "scheduler_0002"
branch_labels = None
depends_on = None
operation_summary = ("将冻结模型版本转为文本列，保留原有值和全部计划",)


def upgrade():
    connection = op.get_bind()
    connection.exec_driver_sql("ALTER TABLE scheduled_tasks RENAME COLUMN model_connection_version TO model_connection_version_0002")
    connection.exec_driver_sql("ALTER TABLE scheduled_tasks ADD COLUMN model_connection_version TEXT")
    connection.exec_driver_sql("UPDATE scheduled_tasks SET model_connection_version=CAST(model_connection_version_0002 AS TEXT)")
    connection.exec_driver_sql("ALTER TABLE scheduled_tasks DROP COLUMN model_connection_version_0002")


def downgrade():
    raise RuntimeError("请显式恢复经验证的迁移前备份")
