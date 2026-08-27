"""收敛共享 webui.db 中其余启动期 Schema 与历史回填。"""

from collections.abc import Sequence
from pathlib import Path
import sqlite3

from alembic import op


revision: str = "webui_0002"
down_revision: str | None = "webui_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
operation_summary = (
    "创建共享运行时、模型连接、对话转向、交付与能力目录 Schema",
    "冻结回填模型目录、运行验证字段与发布幂等字段",
)


def _column_names(table: str) -> set[str]:
    return {
        str(row[1])
        for row in op.get_bind().exec_driver_sql(
            f"PRAGMA table_info({table})"
        ).fetchall()
    }


def _upgrade_agentic_runtime() -> None:
    connection = op.get_bind()
    declarations = {
        "verification_json": "TEXT",
        "verified_candidate_set_hash": "TEXT",
        "model_connection_id": "TEXT",
        "model_connection_version": "TEXT",
        "model_connection_model": "TEXT",
        "external_api_confirmed": "INTEGER NOT NULL DEFAULT 0",
    }
    columns = _column_names("agentic_runtime_runs")
    for column, declaration in declarations.items():
        if column not in columns:
            connection.exec_driver_sql(
                f"ALTER TABLE agentic_runtime_runs ADD COLUMN {column} {declaration}"
            )


def _upgrade_model_connections() -> None:
    connection = op.get_bind()
    if "compatibility_slot" not in _column_names("model_connections"):
        connection.exec_driver_sql(
            "ALTER TABLE model_connections ADD COLUMN compatibility_slot TEXT"
        )
        connection.exec_driver_sql(
            "UPDATE model_connections "
            "SET compatibility_slot='personal_preset_v1' "
            "WHERE owner_scope='user_personal'"
        )
    connection.exec_driver_sql(
        "DROP INDEX IF EXISTS idx_model_connections_personal_preset"
    )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "idx_model_connections_personal_compatibility_slot "
        "ON model_connections(owner_user_id, preset_id, compatibility_slot) "
        "WHERE owner_scope='user_personal' AND compatibility_slot IS NOT NULL"
    )
    connection.exec_driver_sql(
        """
        INSERT INTO model_connection_models (
            connection_id, model_id, display_name, catalog_role,
            catalog_version, catalog_order, status, enabled,
            verified_at, error_code, usage_status, native_usage_json, updated_at
        )
        SELECT
            c.connection_id, c.model, c.model, 'legacy',
            COALESCE(c.preset_version, 'legacy'), 0,
            CASE WHEN c.status='verified' THEN 'available' ELSE 'pending_validation' END,
            CASE WHEN c.status='verified' THEN 1 ELSE 0 END,
            c.verified_at, NULL, 'unknown', '{}', c.updated_at
        FROM model_connections AS c
        WHERE NOT EXISTS (
            SELECT 1 FROM model_connection_models AS m
            WHERE m.connection_id=c.connection_id
        )
        """
    )


def _upgrade_delivery_publishing() -> None:
    connection = op.get_bind()
    if "request_idempotency_hash" not in _column_names(
        "delivery_publish_intents"
    ):
        connection.exec_driver_sql(
            "ALTER TABLE delivery_publish_intents "
            "ADD COLUMN request_idempotency_hash TEXT"
        )
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "idx_dpi_owner_request_idempotency "
        "ON delivery_publish_intents(owner_id, request_idempotency_hash) "
        "WHERE request_idempotency_hash IS NOT NULL"
    )


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
        raise RuntimeError("webui_0002.sql 包含不完整 SQL")
    _upgrade_agentic_runtime()
    _upgrade_model_connections()
    _upgrade_delivery_publishing()


def downgrade() -> None:
    raise RuntimeError("Mangrove 迁移不支持隐式降级；请验证并显式恢复备份")
