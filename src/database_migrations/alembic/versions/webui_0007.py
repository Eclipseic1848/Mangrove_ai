"""增加同站批次覆盖事实和逐页失败记录。"""

from __future__ import annotations

from collections.abc import Sequence
from alembic import op


revision: str = "webui_0007"
down_revision: str | None = "webui_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
operation_summary = (
    "为 SourceSnapshot 增加冻结覆盖投影",
    "记录同站批次中每个失败或越界页面",
    "把来源引用冻结到每个 TaskRevision",
    "串行化 SourceRefresh 的幂等版本切换",
)


def upgrade() -> None:
    connection = op.get_bind().connection.driver_connection
    revision_columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(semantic_workspace_revisions)"
        ).fetchall()
    }
    if "source_refs_json" not in revision_columns:
        op.execute(
            "ALTER TABLE semantic_workspace_revisions "
            "ADD COLUMN source_refs_json TEXT NOT NULL DEFAULT '[]'"
        )
        op.execute(
            "UPDATE semantic_workspace_revisions SET source_refs_json="
            "COALESCE((SELECT source_refs_json FROM semantic_workspace_tasks "
            "WHERE semantic_workspace_tasks.task_id="
            "semantic_workspace_revisions.task_id), '[]')"
        )
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(source_snapshots)").fetchall()
    }
    if "coverage_json" not in columns:
        op.execute("ALTER TABLE source_snapshots ADD COLUMN coverage_json TEXT")
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='source_page_failures'"
    ).fetchone()
    if exists is None:
        op.execute(
            """
            CREATE TABLE source_page_failures (
            failure_id    TEXT PRIMARY KEY,
            owner_id      TEXT NOT NULL,
            attempt_id    TEXT NOT NULL,
            snapshot_id   TEXT,
            request_url   TEXT NOT NULL,
            final_url     TEXT,
            error_code    TEXT NOT NULL,
            error_message TEXT NOT NULL,
            failed_at     TEXT NOT NULL,
            UNIQUE (owner_id, failure_id),
            FOREIGN KEY (owner_id, attempt_id)
                REFERENCES source_acquisition_attempts(owner_id, attempt_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (owner_id, snapshot_id)
                REFERENCES source_snapshots(owner_id, snapshot_id)
                ON DELETE RESTRICT
            )
            """
        )
        op.execute(
            "CREATE INDEX idx_source_failures_owner_snapshot "
            "ON source_page_failures(owner_id, snapshot_id, failed_at)"
        )
        op.execute(
            "CREATE TRIGGER source_page_failures_no_update "
            "BEFORE UPDATE ON source_page_failures BEGIN "
            "SELECT RAISE(ABORT, '来源页面失败事实不可改写'); END"
        )
        op.execute(
            "CREATE TRIGGER source_page_failures_no_delete "
            "BEFORE DELETE ON source_page_failures BEGIN "
            "SELECT RAISE(ABORT, '来源页面失败事实不可删除'); END"
        )
    refresh_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='source_refresh_intents'"
    ).fetchone()
    if refresh_exists is None:
        op.execute(
            """
            CREATE TABLE source_refresh_intents (
                owner_id          TEXT NOT NULL,
                task_id           TEXT NOT NULL,
                idempotency_key   TEXT NOT NULL,
                request_hash      TEXT NOT NULL,
                expected_revision INTEGER NOT NULL,
                attempt_id        TEXT NOT NULL,
                snapshot_id       TEXT NOT NULL,
                status            TEXT NOT NULL,
                created_revision  INTEGER,
                started_at        TEXT NOT NULL,
                updated_at        TEXT NOT NULL,
                PRIMARY KEY (owner_id, task_id, idempotency_key),
                CHECK (status IN ('binding', 'completed', 'failed')),
                FOREIGN KEY (task_id)
                    REFERENCES semantic_workspace_tasks(task_id)
                    ON DELETE RESTRICT,
                FOREIGN KEY (owner_id, attempt_id)
                    REFERENCES source_acquisition_attempts(owner_id, attempt_id)
                    ON DELETE RESTRICT,
                FOREIGN KEY (owner_id, snapshot_id)
                    REFERENCES source_snapshots(owner_id, snapshot_id)
                    ON DELETE RESTRICT
            )
            """
        )
        op.execute(
            "CREATE INDEX idx_source_refresh_owner_status "
            "ON source_refresh_intents(owner_id, status, updated_at)"
        )


def downgrade() -> None:
    raise RuntimeError("来源覆盖与失败事实不可变；请通过显式备份恢复降级")
