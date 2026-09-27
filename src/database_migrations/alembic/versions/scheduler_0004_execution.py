"""计划时区与可追溯执行状态；旧值保持原样，旧计划需显式重建。"""
from alembic import op

revision = 'scheduler_0004'
down_revision = 'scheduler_0003'
branch_labels = None
depends_on = None
operation_summary = ('追加计划时区与单次执行元数据，不改写旧任务或结果',)


def upgrade():
    conn = op.get_bind()
    conn.exec_driver_sql('ALTER TABLE scheduled_tasks ADD COLUMN time_zone TEXT')
    for field in ('state', 'started_at', 'ended_at', 'provider', 'model', 'model_connection_id', 'model_connection_version', 'notification_json', 'usage_json'):
        conn.exec_driver_sql(f'ALTER TABLE scheduled_task_runs ADD COLUMN {field} TEXT')


def downgrade():
    raise RuntimeError('请保全新增记录后显式恢复经验证的迁移前备份')
