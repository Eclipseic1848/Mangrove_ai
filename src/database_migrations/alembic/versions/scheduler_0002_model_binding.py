"""保留定时计划创建时选定的模型连接，不改写历史计划。"""
from alembic import op

revision = "scheduler_0002"
down_revision = "scheduler_0001"
branch_labels = None
depends_on = None
operation_summary = ("为定时计划补充可空的冻结模型连接及版本",)


def upgrade():
    op.get_bind().exec_driver_sql("ALTER TABLE scheduled_tasks ADD COLUMN model_connection_id TEXT")
    op.get_bind().exec_driver_sql("ALTER TABLE scheduled_tasks ADD COLUMN model_connection_version INTEGER")


def downgrade():
    raise RuntimeError("请显式恢复经验证的迁移前备份，禁止隐式删除计划信息")
