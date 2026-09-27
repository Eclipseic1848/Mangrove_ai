"""保存学习文件效果回执，不改写既有任务及经验。"""
from alembic import op

revision = "webui_0022"
down_revision = "webui_0021"
branch_labels = None
depends_on = None
operation_summary = ("新增按 Owner、任务、版本及执行绑定的学习回执",)

STATEMENTS = (
    """CREATE TABLE library_learning_receipts (
        owner_id TEXT NOT NULL, task_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision >= 1), run_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('template_use','template_new','lesson_new','lesson_failure','lesson_use_1','lesson_use_2','lesson_use_3')),
        slug TEXT NOT NULL, before_sha256 TEXT, after_sha256 TEXT NOT NULL,
        after_content TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('queued','generating','pending','applied','conflict','skipped')),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(owner_id,task_id,revision,run_id,kind)
    )""",
)


def upgrade():
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade():
    raise RuntimeError("回退请使用已验证备份，并保全迁移后的新回执与业务数据")
