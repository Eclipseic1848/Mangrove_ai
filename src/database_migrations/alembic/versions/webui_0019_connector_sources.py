"""为冻结连接读取增加断点，并扩展精确原件删除身份。"""

import sqlalchemy as sa
from alembic import op

revision = "webui_0019"
down_revision = "webui_0018"
branch_labels = None
depends_on = None
operation_summary = ("新增冻结连接分页检查点并扩展精确连接制品删除身份",)


def upgrade():
    connection = op.get_bind()
    columns = {
        row[1]: row
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(source_acquisition_attempts)"
        )
    }
    checkpoint = columns.get("connector_progress_json")
    if checkpoint and (checkpoint[2].upper() != "TEXT" or checkpoint[3]):
        raise RuntimeError("连接检查点列形状不匹配")

    trigger = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_master "
        "WHERE type='trigger' AND name='source_artifacts_no_delete'"
    ).fetchone()
    old_identity = "d.source_key='web_artifact:'||OLD.artifact_id"
    new_identity = (
        "d.source_key IN "
        "('web_artifact:'||OLD.artifact_id,'connector_artifact:'||OLD.artifact_id)"
    )
    if trigger is None or (
        old_identity not in trigger[0] and new_identity not in trigger[0]
    ):
        raise RuntimeError("原件删除触发器形状不匹配")

    if checkpoint is None:
        op.add_column(
            "source_acquisition_attempts",
            sa.Column("connector_progress_json", sa.Text(), nullable=True),
        )
    if new_identity in trigger[0]:
        return

    # 删除仍须命中同一 Owner 的显式清理记录；这里只增加连接原件身份。
    op.execute("DROP TRIGGER source_artifacts_no_delete")
    op.execute(trigger[0].replace(old_identity, new_identity))


def downgrade():
    raise RuntimeError("禁止自动回退来源删除身份约束")
