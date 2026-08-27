"""建立 webui 数据库的统一版本头。"""

from collections.abc import Sequence
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import uuid

from alembic import op


revision: str = "webui_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = "webui"
depends_on: str | Sequence[str] | None = None
operation_summary = (
    "创建或补齐 WebUI 核心表、索引与外键",
    "冻结回填 RBAC、消息元数据与文档任务单位",
)


def _column_names(table: str) -> set[str]:
    connection = op.get_bind()
    return {
        str(row[1])
        for row in connection.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    }


def _upgrade_legacy_users() -> None:
    connection = op.get_bind()
    columns = _column_names("users")
    if "role" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'"
        )
    if "disabled" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE users ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0"
        )
    if "pending" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE users ADD COLUMN pending INTEGER NOT NULL DEFAULT 0"
        )
    has_super = connection.exec_driver_sql(
        "SELECT 1 FROM users WHERE role='super_admin' LIMIT 1"
    ).fetchone()
    if has_super is None:
        first = connection.exec_driver_sql(
            "SELECT user_id FROM users ORDER BY created_at, rowid LIMIT 1"
        ).fetchone()
        if first is not None:
            connection.exec_driver_sql(
                "UPDATE users SET role='super_admin' WHERE user_id=?",
                (str(first[0]),),
            )


def _upgrade_legacy_messages() -> None:
    connection = op.get_bind()
    columns = _column_names("messages")
    if "task_id" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE messages ADD COLUMN task_id TEXT"
        )
    if "meta_json" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE messages ADD COLUMN meta_json TEXT"
        )


def _add_column_if_missing(table: str, column: str, declaration: str) -> bool:
    if column in _column_names(table):
        return False
    op.get_bind().exec_driver_sql(
        f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
    )
    return True


def _upgrade_legacy_webui_columns() -> None:
    connection = op.get_bind()
    _add_column_if_missing(
        "memory_hit_log", "hit", "INTEGER NOT NULL DEFAULT 1"
    )
    _add_column_if_missing(
        "library_dedup_scan_log", "details", "TEXT NOT NULL DEFAULT ''"
    )
    _add_column_if_missing(
        "message_feedback", "status", "TEXT NOT NULL DEFAULT 'pending'"
    )
    _add_column_if_missing("message_feedback", "admin_note", "TEXT")
    _add_column_if_missing("data_prep_tasks", "checkpoint_json", "TEXT")
    _add_column_if_missing("data_prep_tasks", "unit_id", "TEXT")
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS idx_dpt_unit ON data_prep_tasks(unit_id)"
    )
    checked_added = _add_column_if_missing(
        "document_workspaces",
        "checked_upload_ids_json",
        "TEXT NOT NULL DEFAULT '[]'",
    )
    if checked_added:
        connection.exec_driver_sql(
            "UPDATE document_workspaces "
            "SET checked_upload_ids_json=upload_ids_json"
        )
    _add_column_if_missing("document_workspaces", "active_unit_id", "TEXT")
    _add_column_if_missing(
        "semantic_harness_attempts",
        "artifact_paths_json",
        "TEXT NOT NULL DEFAULT '{}'",
    )
    _add_column_if_missing("semantic_workspace_tasks", "failure_json", "TEXT")
    _add_column_if_missing(
        "semantic_workspace_tasks",
        "source_refs_json",
        "TEXT NOT NULL DEFAULT '[]'",
    )
    _add_column_if_missing(
        "semantic_workspace_tasks",
        "table_output_contracts_json",
        "TEXT NOT NULL DEFAULT '[]'",
    )
    _add_column_if_missing(
        "semantic_workspace_revisions",
        "table_output_contracts_json",
        "TEXT NOT NULL DEFAULT '[]'",
    )
    _add_column_if_missing("document_task_units", "archived_at", "TEXT")


def _legacy_unit_id(user_id: str, kind: str, identity: str) -> str:
    """为历史迁移生成稳定 ID，保证迁移重放不会制造重复任务单位。"""
    value = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"mangrove-document-unit:{user_id}:{kind}:{identity}",
    )
    return f"du_{value.hex[:16]}"


def _backfill_document_task_uploads(connection: sqlite3.Connection) -> None:
    """从冻结任务规格恢复旧任务与上传文件的查询关联。"""
    old_tasks = connection.execute(
        "SELECT task_id, spec_json FROM data_prep_tasks "
        "WHERE task_id NOT IN "
        "(SELECT DISTINCT task_id FROM data_prep_task_uploads)"
    ).fetchall()
    for old_task in old_tasks:
        try:
            spec = json.loads(old_task["spec_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if spec.get("task_type") != "document_extraction":
            continue
        for ordinal, upload_id in enumerate(spec.get("upload_ids") or []):
            connection.execute(
                "INSERT OR IGNORE INTO data_prep_task_uploads "
                "(task_id, upload_id, ordinal) VALUES (?, ?, ?)",
                (old_task["task_id"], str(upload_id), ordinal),
            )


def _migrate_document_task_units(connection: sqlite3.Connection) -> None:
    """把旧任务无损归入单文件单位或历史批次。"""
    rows = connection.execute(
        "SELECT task_id, user_id, unit_id, spec_json, created_at, updated_at "
        "FROM data_prep_tasks ORDER BY user_id, created_at, rowid"
    ).fetchall()
    seen_uploads: dict[str, set[str]] = {}
    task_units: dict[str, str] = {}
    for row in rows:
        try:
            spec = json.loads(row["spec_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if spec.get("task_type") != "document_extraction":
            continue
        user_id = str(row["user_id"])
        upload_ids = list(
            dict.fromkeys(str(item) for item in (spec.get("upload_ids") or []))
        )
        if not upload_ids:
            continue
        unit_id = row["unit_id"] or spec.get("unit_id")
        parent_id = str(spec.get("parent_task_id") or "")
        if not unit_id and parent_id:
            unit_id = task_units.get(parent_id)
        known = seen_uploads.setdefault(user_id, set())
        new_uploads = [item for item in upload_ids if item not in known]
        if not unit_id and len(new_uploads) == 1:
            primary_upload = new_uploads[0]
            unit_id = _legacy_unit_id(user_id, "single", primary_upload)
            unit_type = "single_file"
            members = [primary_upload]
            name = primary_upload
        elif not unit_id and len(upload_ids) == 1:
            primary_upload = upload_ids[0]
            unit_id = _legacy_unit_id(user_id, "single", primary_upload)
            unit_type = "single_file"
            members = [primary_upload]
            name = primary_upload
        elif not unit_id:
            unit_id = _legacy_unit_id(
                user_id,
                "batch",
                parent_id or str(row["task_id"]),
            )
            unit_type = "file_set"
            members = upload_ids
            intents = spec.get("intent_messages") or []
            suffix = str(intents[-1] if intents else row["task_id"])[:60]
            name = f"历史批次 · {suffix}"
        else:
            existing = connection.execute(
                "SELECT unit_type, name FROM document_task_units "
                "WHERE unit_id=?",
                (unit_id,),
            ).fetchone()
            if existing:
                unit_type = str(existing["unit_type"])
                name = str(existing["name"])
                members = [
                    str(item["upload_id"])
                    for item in connection.execute(
                        "SELECT upload_id FROM document_task_unit_members "
                        "WHERE unit_id=? ORDER BY ordinal",
                        (unit_id,),
                    ).fetchall()
                ]
            else:
                unit_type = "file_set" if len(upload_ids) > 1 else "single_file"
                members = upload_ids
                name = upload_ids[0] if unit_type == "single_file" else "历史文件集"
        now = str(
            row["updated_at"]
            or row["created_at"]
            or datetime.now().isoformat(timespec="seconds")
        )
        connection.execute(
            "INSERT OR IGNORE INTO document_task_units "
            "(unit_id, user_id, unit_type, name, business_type, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, '', ?, ?)",
            (
                unit_id,
                user_id,
                unit_type,
                name,
                str(row["created_at"] or now),
                now,
            ),
        )
        for ordinal, upload_id in enumerate(members):
            connection.execute(
                "INSERT OR IGNORE INTO document_task_unit_members "
                "(unit_id, upload_id, ordinal, added_at) VALUES (?, ?, ?, ?)",
                (unit_id, upload_id, ordinal, now),
            )
        spec["unit_id"] = unit_id
        connection.execute(
            "UPDATE data_prep_tasks SET unit_id=?, spec_json=? WHERE task_id=?",
            (
                unit_id,
                json.dumps(spec, ensure_ascii=False),
                row["task_id"],
            ),
        )
        task_units[str(row["task_id"])] = str(unit_id)
        known.update(upload_ids)

    # 工作区中还没有任务的文件也必须作为独立任务单位出现。
    for row in connection.execute(
        "SELECT user_id, upload_ids_json, updated_at FROM document_workspaces"
    ).fetchall():
        user_id = str(row["user_id"])
        try:
            upload_ids = json.loads(row["upload_ids_json"] or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            upload_ids = []
        for upload_id in upload_ids:
            upload_id = str(upload_id)
            exists = connection.execute(
                "SELECT 1 FROM document_task_unit_members m "
                "JOIN document_task_units u ON u.unit_id=m.unit_id "
                "WHERE u.user_id=? AND u.unit_type='single_file' "
                "AND m.upload_id=? LIMIT 1",
                (user_id, upload_id),
            ).fetchone()
            if exists:
                continue
            unit_id = _legacy_unit_id(user_id, "single", upload_id)
            now = str(
                row["updated_at"] or datetime.now().isoformat(timespec="seconds")
            )
            connection.execute(
                "INSERT OR IGNORE INTO document_task_units "
                "(unit_id, user_id, unit_type, name, business_type, created_at, updated_at) "
                "VALUES (?, ?, 'single_file', ?, '', ?, ?)",
                (unit_id, user_id, upload_id, now, now),
            )
            connection.execute(
                "INSERT OR IGNORE INTO document_task_unit_members "
                "(unit_id, upload_id, ordinal, added_at) VALUES (?, ?, 0, ?)",
                (unit_id, upload_id, now),
            )


def _upgrade_legacy_document_units() -> None:
    """在 Alembic 事务内执行原先藏在应用启动中的数据回填。"""
    raw_connection = op.get_bind().connection.driver_connection
    previous_row_factory = raw_connection.row_factory
    raw_connection.row_factory = sqlite3.Row
    try:
        _backfill_document_task_uploads(raw_connection)
        _migrate_document_task_units(raw_connection)
    finally:
        raw_connection.row_factory = previous_row_factory


def upgrade() -> None:
    """从冻结 SQL 建立当前 webui Schema。"""

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
        raise RuntimeError("webui_0001.sql 包含不完整 SQL")
    _upgrade_legacy_users()
    _upgrade_legacy_messages()
    _upgrade_legacy_webui_columns()
    _upgrade_legacy_document_units()


def downgrade() -> None:
    raise RuntimeError("Mangrove 迁移不支持隐式降级；请验证并显式恢复备份")
