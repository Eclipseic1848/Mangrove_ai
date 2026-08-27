"""建立 Legacy Conductor SQLite 输出数据库版本头。"""

from collections.abc import Sequence

from alembic import op


revision: str = "legacy_app_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = "legacy_app"
depends_on: str | Sequence[str] | None = None
operation_summary = ("创建 Legacy Conductor SQLite 输出 Schema",)


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS collected_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT,
            source TEXT,
            url TEXT,
            title TEXT,
            content TEXT,
            metadata TEXT,
            created_at TEXT
        )
        """
    )


def downgrade() -> None:
    raise RuntimeError("Mangrove 迁移不支持隐式降级；请验证并显式恢复备份")
