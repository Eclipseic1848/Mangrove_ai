"""增加 Owner 隔离的匿名网页来源获取事实。"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "webui_0005"
down_revision: str | None = "webui_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
operation_summary = (
    "创建 SourceAcquisitionAttempt 幂等与状态事实",
    "创建一个逻辑批次一个 SourceSnapshot 的冻结事实",
    "创建每个有效页面独立 SourceArtifact 的内容与证据事实",
)


def upgrade() -> None:
    connection = op.get_bind().connection.driver_connection
    expected_tables = {
        "source_acquisition_attempts",
        "source_snapshots",
        "source_artifacts",
    }
    existing_tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN (?, ?, ?)",
            tuple(sorted(expected_tables)),
        ).fetchall()
    }
    if existing_tables == expected_tables:
        # 中央迁移支持已知 legacy 数据库全链重放；完整存在时最终 Schema
        # 冻结门会继续验证，部分存在则必须失败关闭，不能猜测补建。
        return
    if existing_tables:
        raise RuntimeError("来源获取 Schema 仅部分存在，拒绝猜测修补")
    op.execute(
        """
        CREATE TABLE source_acquisition_attempts (
            attempt_id         TEXT PRIMARY KEY,
            owner_id           TEXT NOT NULL,
            idempotency_key    TEXT NOT NULL,
            request_hash       TEXT NOT NULL,
            request_url        TEXT NOT NULL,
            normalized_url     TEXT NOT NULL,
            allowed_scope_json TEXT NOT NULL,
            purpose            TEXT NOT NULL,
            status             TEXT NOT NULL CHECK (
                status IN ('acquiring', 'succeeded', 'failed', 'canceled')
            ),
            started_at         TEXT NOT NULL,
            finished_at        TEXT,
            snapshot_id        TEXT,
            error_code         TEXT,
            error_message      TEXT,
            UNIQUE (owner_id, idempotency_key),
            UNIQUE (owner_id, attempt_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE source_snapshots (
            snapshot_id        TEXT PRIMARY KEY,
            owner_id           TEXT NOT NULL,
            attempt_id         TEXT NOT NULL UNIQUE,
            allowed_scope_json TEXT NOT NULL,
            valid_page_count   INTEGER NOT NULL CHECK (valid_page_count >= 0),
            failed_page_count  INTEGER NOT NULL CHECK (failed_page_count >= 0),
            created_at         TEXT NOT NULL,
            UNIQUE (owner_id, snapshot_id),
            FOREIGN KEY (owner_id, attempt_id)
                REFERENCES source_acquisition_attempts(owner_id, attempt_id)
                ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE source_artifacts (
            artifact_id    TEXT PRIMARY KEY,
            owner_id       TEXT NOT NULL,
            snapshot_id    TEXT NOT NULL,
            request_url    TEXT NOT NULL,
            final_url      TEXT NOT NULL,
            read_at        TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            media_type     TEXT NOT NULL,
            size_bytes     INTEGER NOT NULL CHECK (size_bytes >= 0),
            title          TEXT NOT NULL,
            text_preview   TEXT NOT NULL,
            content_blob   BLOB NOT NULL,
            UNIQUE (owner_id, artifact_id),
            FOREIGN KEY (owner_id, snapshot_id)
                REFERENCES source_snapshots(owner_id, snapshot_id)
                ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_source_attempts_owner_status "
        "ON source_acquisition_attempts(owner_id, status, started_at)"
    )
    op.execute(
        "CREATE INDEX idx_source_artifacts_owner_snapshot "
        "ON source_artifacts(owner_id, snapshot_id, read_at)"
    )
    op.execute(
        "CREATE TRIGGER source_snapshots_no_update "
        "BEFORE UPDATE ON source_snapshots BEGIN "
        "SELECT RAISE(ABORT, 'SourceSnapshot 不可改写'); END"
    )
    op.execute(
        "CREATE TRIGGER source_snapshots_no_delete "
        "BEFORE DELETE ON source_snapshots BEGIN "
        "SELECT RAISE(ABORT, 'SourceSnapshot 不可删除'); END"
    )
    op.execute(
        "CREATE TRIGGER source_artifacts_no_update "
        "BEFORE UPDATE ON source_artifacts BEGIN "
        "SELECT RAISE(ABORT, 'SourceArtifact 不可改写'); END"
    )
    op.execute(
        "CREATE TRIGGER source_artifacts_no_delete "
        "BEFORE DELETE ON source_artifacts BEGIN "
        "SELECT RAISE(ABORT, 'SourceArtifact 不可删除'); END"
    )


def downgrade() -> None:
    raise RuntimeError("来源快照是不可变证据；请通过显式备份恢复降级")
