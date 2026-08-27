"""建立 Provider 资格台账数据库版本头。"""

from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid

from alembic import op


revision: str = "qualification_ledger_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = "qualification_ledger"
depends_on: str | Sequence[str] | None = None
operation_summary = ("创建 Provider 资格批次、尝试、重试与恢复证据 Schema",)

_SCHEMA_VERSION = "g4-qualification-ledger-v1"


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
        raise RuntimeError("qualification_ledger_0001.sql 包含不完整 SQL")
    metadata = connection.exec_driver_sql(
        "SELECT schema_version FROM qualification_ledger_metadata "
        "WHERE singleton=1"
    ).fetchone()
    if metadata is None:
        connection.exec_driver_sql(
            "INSERT INTO qualification_ledger_metadata "
            "(singleton, schema_version, ledger_id, revision, created_at) "
            "VALUES (1, ?, ?, 0, ?)",
            (
                _SCHEMA_VERSION,
                f"g4ledger_{uuid.uuid4().hex}",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    elif str(metadata[0]) != _SCHEMA_VERSION:
        raise RuntimeError("资格批次台账版本不受支持")


def downgrade() -> None:
    raise RuntimeError("Mangrove 迁移不支持隐式降级；请验证并显式恢复备份")
