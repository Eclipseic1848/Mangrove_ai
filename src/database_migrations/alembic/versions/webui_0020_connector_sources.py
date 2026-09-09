"""通用连接原件采用独立canonical key；不修改历史0018。"""
from alembic import op
import sqlalchemy as sa
revision='webui_0020'
down_revision='webui_0019'
branch_labels=None
depends_on=None
operation_summary=("新增冻结连接分页检查点并扩展精确连接制品删除身份",)


def upgrade():
    connection=op.get_bind()
    columns={row[1]:row for row in connection.exec_driver_sql('PRAGMA table_info(source_acquisition_attempts)')}
    if 'connector_progress_json' in columns and (columns['connector_progress_json'][2].upper()!='TEXT' or columns['connector_progress_json'][3]):
        raise RuntimeError('连接检查点列形状不匹配')
    row=connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='source_artifacts_no_delete'").fetchone()
    old="d.source_key='web_artifact:'||OLD.artifact_id"
    new="d.source_key IN ('web_artifact:'||OLD.artifact_id,'connector_artifact:'||OLD.artifact_id)"
    if row is None or (old not in row[0] and new not in row[0]):
        raise RuntimeError('原件删除触发器形状不匹配')
    if 'connector_progress_json' not in columns:
        op.add_column('source_acquisition_attempts',sa.Column('connector_progress_json',sa.Text(),nullable=True))
    if new in row[0]:
        return
    statement=row[0].replace(old,new)
    op.execute('DROP TRIGGER source_artifacts_no_delete')
    op.execute(statement)


def downgrade():
    raise RuntimeError('禁止自动回退来源删除身份约束')
