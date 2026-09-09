"""保留公开查询发现与正文获取的区分报告。"""
from alembic import op
revision = 'webui_0015'
down_revision = 'webui_0014'
branch_labels = None
depends_on = None
operation_summary = ('来源获取新增可空公开搜索报告，历史URL记录保持空值',)


def upgrade():
    # 旧库可能已有此列；只有精确兼容形状才允许重放。
    columns = op.get_bind().exec_driver_sql('PRAGMA table_info(source_acquisition_attempts)').fetchall()
    existing = next((row for row in columns if row[1] == 'search_report_json'), None)
    if existing is not None:
        if str(existing[2]).upper() != 'TEXT' or existing[3] or existing[4] is not None or existing[5]:
            raise RuntimeError('search_report_json 已有列形状不兼容，停止迁移')
        return
    op.execute('ALTER TABLE source_acquisition_attempts ADD COLUMN search_report_json TEXT')


def downgrade():
    raise RuntimeError('来源证据必须保留；请通过显式备份恢复降级')
