"""健康记录绑定凭证及验证环境；旧记录保持未知直到重新验证。"""
from alembic import op

revision = "webui_0023"
down_revision = "webui_0022"
branch_labels = None
depends_on = None
operation_summary = ("为Cookie健康记录增加验证绑定摘要，不改写业务任务",)


def upgrade():
    op.execute("ALTER TABLE cookie_health ADD COLUMN verification_binding TEXT NOT NULL DEFAULT ''")


def downgrade():
    raise RuntimeError("回退请使用已验证备份，并保全迁移后的业务数据")
