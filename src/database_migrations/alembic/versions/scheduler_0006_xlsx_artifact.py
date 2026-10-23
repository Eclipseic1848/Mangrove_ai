"""记录本轮调度工作簿；旧运行不猜测、不回填产物。"""
from alembic import op

revision = 'scheduler_0006'
down_revision = 'scheduler_0005'
branch_labels = None
depends_on = None
operation_summary = ('追加可空的运行工作簿路径，不改写旧运行或通知事实',)


def upgrade():
    op.get_bind().exec_driver_sql('ALTER TABLE scheduled_task_runs ADD COLUMN xlsx_path TEXT')


def downgrade():
    raise RuntimeError('请保全新增记录后显式恢复经验证的迁移前备份')
