"""增加任务模板、带用途的个人记忆与冻结上下文。"""

from __future__ import annotations

from collections.abc import Sequence
from alembic import op


revision: str = "webui_0009"
down_revision: str | None = "webui_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
operation_summary = (
    "为个人记忆增加用途、来源与软删除语义",
    "建立 Owner 隔离的版本化任务模板目录",
    "冻结每个 TaskRevision 使用的模板、记忆与 CompiledContext",
)


def upgrade() -> None:
    connection = op.get_bind().connection.driver_connection
    memory_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(user_memory)").fetchall()
    }
    if "purpose" not in memory_columns:
        op.execute("ALTER TABLE user_memory ADD COLUMN purpose TEXT NOT NULL DEFAULT 'general'")
    if "source" not in memory_columns:
        op.execute("ALTER TABLE user_memory ADD COLUMN source TEXT NOT NULL DEFAULT 'user_entered'")
    if "deleted_at" not in memory_columns:
        op.execute("ALTER TABLE user_memory ADD COLUMN deleted_at TEXT")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS task_templates (
            owner_id TEXT NOT NULL,
            template_id TEXT NOT NULL,
            version INTEGER NOT NULL CHECK (version >= 1),
            title TEXT NOT NULL,
            source TEXT NOT NULL,
            purpose TEXT NOT NULL,
            goal_contract_draft TEXT NOT NULL,
            delivery_spec_json TEXT NOT NULL,
            method_draft TEXT NOT NULL,
            summary_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (owner_id, template_id, version),
            CHECK (status IN ('active', 'retired'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_task_templates_owner_purpose "
        "ON task_templates(owner_id, purpose, status, template_id, version)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS task_revision_contexts (
            owner_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK (revision >= 1),
            preview_sha256 TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            compiled_context_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (owner_id, task_id, revision),
            FOREIGN KEY (task_id, revision)
                REFERENCES semantic_workspace_revisions(task_id, revision)
                ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE TRIGGER IF NOT EXISTS task_revision_contexts_no_update "
        "BEFORE UPDATE ON task_revision_contexts BEGIN "
        "SELECT RAISE(ABORT, '任务上下文快照不可改写'); END"
    )
    op.execute(
        "CREATE TRIGGER IF NOT EXISTS task_revision_contexts_no_delete "
        "BEFORE DELETE ON task_revision_contexts BEGIN "
        "SELECT RAISE(ABORT, '任务上下文快照不可删除'); END"
    )


def downgrade() -> None:
    raise RuntimeError("任务上下文快照不可变；请通过显式备份恢复降级")
