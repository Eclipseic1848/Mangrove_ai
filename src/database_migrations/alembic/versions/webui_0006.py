"""冻结网页任务的来源、目标、交付和首个运行绑定。"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "webui_0006"
down_revision: str | None = "webui_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
operation_summary = (
    "创建网页任务不可变合同",
    "冻结 SourceSnapshot、GoalContract、DeliverySpec 与首个 RuntimeBinding",
)


def upgrade() -> None:
    connection = op.get_bind().connection.driver_connection
    existing = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='web_task_contracts'"
    ).fetchone()
    if existing is not None:
        return
    op.execute(
        """
        CREATE TABLE web_task_contracts (
            owner_id            TEXT NOT NULL,
            task_id             TEXT NOT NULL,
            revision            INTEGER NOT NULL CHECK (revision >= 1),
            source_snapshot_id  TEXT NOT NULL,
            goal_contract_json  TEXT NOT NULL,
            delivery_spec_json  TEXT NOT NULL,
            runtime_binding_json TEXT NOT NULL,
            created_at          TEXT NOT NULL,
            PRIMARY KEY (owner_id, task_id, revision),
            FOREIGN KEY (owner_id, source_snapshot_id)
                REFERENCES source_snapshots(owner_id, snapshot_id)
                ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_web_task_contracts_snapshot "
        "ON web_task_contracts(owner_id, source_snapshot_id)"
    )
    op.execute(
        "CREATE TRIGGER web_task_contracts_no_update "
        "BEFORE UPDATE ON web_task_contracts BEGIN "
        "SELECT RAISE(ABORT, '网页任务合同不可改写'); END"
    )
    op.execute(
        "CREATE TRIGGER web_task_contracts_no_delete "
        "BEFORE DELETE ON web_task_contracts BEGIN "
        "SELECT RAISE(ABORT, '网页任务合同不可删除'); END"
    )


def downgrade() -> None:
    raise RuntimeError("网页任务合同是不可变执行证据；请通过显式备份恢复降级")
