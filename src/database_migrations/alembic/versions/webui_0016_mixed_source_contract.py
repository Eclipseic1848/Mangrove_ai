"""冻结每个任务修订的混合来源目标与范围合同。"""
from alembic import op
revision = 'webui_0016'
down_revision = 'webui_0015'
branch_labels = None
depends_on = None
operation_summary = ('修订新增可空来源合同，旧修订保持空值并兼容旧网页合同',)


def upgrade():
    # 旧库可能已有此列；只有精确兼容形状才允许重放。
    columns = op.get_bind().exec_driver_sql('PRAGMA table_info(semantic_workspace_revisions)').fetchall()
    existing = next((row for row in columns if row[1] == 'source_contract_json'), None)
    if existing is not None:
        if str(existing[2]).upper() != 'TEXT' or existing[3] or existing[4] is not None or existing[5]:
            raise RuntimeError('source_contract_json 已有列形状不兼容，停止迁移')
        return
    op.execute('ALTER TABLE semantic_workspace_revisions ADD COLUMN source_contract_json TEXT')


def downgrade():
    raise RuntimeError('来源证据必须保留；请通过显式备份恢复降级')
