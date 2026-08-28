"""增加部分候选覆盖结论与 Owner 缺口动作。"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "webui_0008"
down_revision: str | None = "webui_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
operation_summary = (
    "冻结 Candidate 的逐项证据与覆盖三态结论",
    "记录 TaskOwner 对缺口的独立幂等动作",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_coverage_assessments (
            owner_id          TEXT NOT NULL,
            task_id           TEXT NOT NULL,
            revision          INTEGER NOT NULL CHECK (revision >= 1),
            run_id            TEXT NOT NULL,
            candidate_set_hash TEXT NOT NULL,
            assessment_json   TEXT NOT NULL,
            created_at        TEXT NOT NULL,
            PRIMARY KEY (owner_id, task_id, revision, candidate_set_hash)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_candidate_coverage_owner_run "
        "ON candidate_coverage_assessments(owner_id, run_id, created_at)"
    )
    op.execute(
        "CREATE TRIGGER IF NOT EXISTS candidate_coverage_no_update "
        "BEFORE UPDATE ON candidate_coverage_assessments BEGIN "
        "SELECT RAISE(ABORT, 'Candidate 覆盖结论不可改写'); END"
    )
    op.execute(
        "CREATE TRIGGER IF NOT EXISTS candidate_coverage_no_delete "
        "BEFORE DELETE ON candidate_coverage_assessments BEGIN "
        "SELECT RAISE(ABORT, 'Candidate 覆盖结论不可删除'); END"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_gap_actions (
            owner_id           TEXT NOT NULL,
            task_id            TEXT NOT NULL,
            idempotency_key    TEXT NOT NULL,
            request_hash       TEXT NOT NULL,
            source_revision    INTEGER NOT NULL CHECK (source_revision >= 1),
            candidate_set_hash TEXT NOT NULL,
            action             TEXT NOT NULL,
            status             TEXT NOT NULL,
            target_revision    INTEGER,
            created_at         TEXT NOT NULL,
            updated_at         TEXT NOT NULL,
            PRIMARY KEY (owner_id, task_id, idempotency_key),
            CHECK (action IN ('accept_gap', 'reject_gap', 'supplement_source', 'refresh_source')),
            CHECK (status IN ('pending', 'completed', 'rejected'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_candidate_gap_owner_revision "
        "ON candidate_gap_actions(owner_id, task_id, source_revision, created_at)"
    )


def downgrade() -> None:
    raise RuntimeError("部分候选结论和 Owner 缺口动作不可变；请通过显式备份恢复降级")
