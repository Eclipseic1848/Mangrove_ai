"""建立 scheduler 数据库的统一版本头。"""

from collections.abc import Sequence
from pathlib import Path
import re
import sqlite3

from alembic import op


revision: str = "scheduler_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = "scheduler"
depends_on: str | Sequence[str] | None = None
operation_summary = ("创建或补齐调度任务与运行记录 Schema",)

_REPORT_RE = re.compile(r"report=([^;]+)")
_JSON_RE = re.compile(r"json=([^;]+)")


def _column_names(table: str) -> set[str]:
    return {
        str(row[1])
        for row in op.get_bind().exec_driver_sql(
            f"PRAGMA table_info({table})"
        ).fetchall()
    }


def _add_column_if_missing(column: str, declaration: str) -> None:
    if column not in _column_names("scheduled_tasks"):
        op.get_bind().exec_driver_sql(
            f"ALTER TABLE scheduled_tasks ADD COLUMN {column} {declaration}"
        )


def _backfill_legacy_runs() -> None:
    raw_connection = op.get_bind().connection.driver_connection
    previous_row_factory = raw_connection.row_factory
    raw_connection.row_factory = sqlite3.Row
    try:
        rows = raw_connection.execute(
            "SELECT task_id, last_run_at, last_success, last_result, last_error "
            "FROM scheduled_tasks WHERE last_run_at IS NOT NULL "
            "AND task_id NOT IN "
            "(SELECT DISTINCT task_id FROM scheduled_task_runs)"
        ).fetchall()
        for row in rows:
            result = row["last_result"] or ""
            report = _REPORT_RE.search(result)
            json_path = _JSON_RE.search(result)
            raw_connection.execute(
                "INSERT INTO scheduled_task_runs "
                "(task_id, run_at, success, summary, report_path, json_path) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    row["task_id"],
                    row["last_run_at"],
                    row["last_success"] or 0,
                    result or (row["last_error"] or ""),
                    report.group(1).strip() if report else "",
                    json_path.group(1).strip() if json_path else "",
                ),
            )
    finally:
        raw_connection.row_factory = previous_row_factory


def upgrade() -> None:
    script = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    buffer: list[str] = []
    connection = op.get_bind()
    for line in script.splitlines(keepends=True):
        buffer.append(line)
        statement = "".join(buffer).strip()
        if statement and sqlite3.complete_statement(statement):
            connection.exec_driver_sql(statement)
            buffer.clear()
    if "".join(buffer).strip():
        raise RuntimeError("scheduler_0001.sql 包含不完整 SQL")
    _add_column_if_missing("owner_user_id", "TEXT")
    _add_column_if_missing("name", "TEXT")
    _add_column_if_missing("source", "TEXT NOT NULL DEFAULT 'auto'")
    _add_column_if_missing("interval_seconds", "INTEGER")
    _add_column_if_missing("start_date", "TEXT")
    _add_column_if_missing("end_date", "TEXT")
    _backfill_legacy_runs()


def downgrade() -> None:
    raise RuntimeError("Mangrove 迁移不支持隐式降级；请验证并显式恢复备份")
