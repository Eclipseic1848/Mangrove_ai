"""保存自动化计划的选中上下文；旧计划不补选、不改写。"""
from alembic import op

revision = 'scheduler_0005'
down_revision = 'scheduler_0004'
branch_labels = None
depends_on = None
operation_summary = ('追加可空的计划上下文快照，不改写旧计划或运行历史',)


def upgrade():
    op.get_bind().exec_driver_sql('ALTER TABLE scheduled_tasks ADD COLUMN task_context_json TEXT')


def downgrade():
    raise RuntimeError('请保全新增记录后显式恢复经验证的迁移前备份')
