"""将真实结果选择与用户原话在网络调用前原子冻结。"""
from alembic import op

revision = "webui_0014"
down_revision = "webui_0013"
branch_labels = None
depends_on = None
operation_summary = ("原始追问新增可空结果上下文和持久单次发送占位，旧回合保持空值和原话",)


def upgrade():
    op.execute("ALTER TABLE conversation_raw_turns ADD COLUMN result_context_json TEXT")
    op.execute("ALTER TABLE conversation_raw_turns ADD COLUMN result_context_claimed INTEGER NOT NULL DEFAULT 0 CHECK(result_context_claimed IN (0,1))")


def downgrade():
    raise RuntimeError("追问证据必须保留；请通过显式备份恢复降级")
