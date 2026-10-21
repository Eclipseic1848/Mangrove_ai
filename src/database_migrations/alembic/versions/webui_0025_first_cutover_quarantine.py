"""复用专用设置保存首次切换隔离，不改写历史结果或停止事实。"""
import json

from alembic import op

revision = "webui_0025"
down_revision = "webui_0024"
branch_labels = None
depends_on = None
operation_summary = ("初始化首次切换隔离权威；批准后不可删除、替换或解除",)

_KEY = "deployment.first_cutover_quarantine"
_EMPTY = json.dumps({"version": 1, "targets": [], "records": [], "approval_reference": None,
                     "actor_id": None, "accepted_risk": None}, sort_keys=True, separators=(",", ":"))

_RECORD_KEYS = {
    "account_execution_bindings": ("owner_user_id", "resource_kind", "resource_id"),
    "account_execution_holds": ("operation_id",), "source_read_uses": ("use_id",),
    "source_deletion_operations": ("operation_id",), "source_deletions": ("owner_id", "source_key"),
    "semantic_workspace_tasks": ("task_id",), "semantic_workspace_revisions": ("task_id", "revision"),
    "semantic_workspace_events": ("event_id",), "agentic_runtime_runs": ("user_id", "task_id", "revision"),
    "agentic_runtime_events": ("event_id",), "candidate_verification_attempts": ("attempt_id",),
    "candidate_gap_actions": ("owner_id", "task_id", "idempotency_key"),
    "capability_validation_runs": ("run_id",), "capability_platform_validation_runs": ("run_id",),
    "capability_validation_leases": ("digest",), "capability_platform_validation_leases": ("digest",),
    "source_acquisition_attempts": ("owner_id", "attempt_id"),
    "source_refresh_intents": ("owner_id", "task_id", "idempotency_key"),
    "delivery_publish_intents": ("publication_key",),
    "library_learning_receipts": ("owner_id", "task_id", "revision", "run_id", "kind"),
    "semantic_harness_runs": ("run_id",), "data_prep_tasks": ("task_id",),
}
_TASK_TABLES = {
    "semantic_workspace_tasks": "user_id", "semantic_workspace_revisions": "user_id",
    "semantic_workspace_events": "user_id", "agentic_runtime_runs": "user_id",
    "agentic_runtime_events": "user_id", "candidate_verification_attempts": "owner_id",
    "candidate_gap_actions": "owner_id", "source_refresh_intents": "owner_id",
    "library_learning_receipts": "owner_id", "data_prep_tasks": "user_id",
}


def _protected(table, reference):
    keys = " AND ".join(f"json_extract(q.value,'$.key.{key}')={reference}.{key}" for key in _RECORD_KEYS[table])
    conditions = [f"""EXISTS (SELECT 1 FROM app_settings s,json_each(s.value,'$.records') q
        WHERE s.key='{_KEY}' AND json_extract(q.value,'$.profile')='webui'
          AND json_extract(q.value,'$.table')='{table}' AND {keys})"""]
    if table in _TASK_TABLES:
        conditions.append(f"""EXISTS (SELECT 1 FROM app_settings s,json_each(s.value,'$.targets') q
            WHERE s.key='{_KEY}' AND json_extract(q.value,'$.kind')='task'
              AND json_extract(q.value,'$.owner_id')={reference}.{_TASK_TABLES[table]}
              AND json_extract(q.value,'$.resource_id')={reference}.task_id)""")
    return "(" + " OR ".join(conditions) + ")"


def upgrade():
    connection = op.get_bind()
    # 既有同名私有值不能被迁移静默覆盖。
    if connection.exec_driver_sql("SELECT 1 FROM app_settings WHERE key=?", (_KEY,)).fetchone():
        raise RuntimeError("首次切换隔离设置已经存在，须核对原件")
    connection.exec_driver_sql("INSERT INTO app_settings(key,value) VALUES (?,?)", (_KEY, _EMPTY))
    op.execute(f"""CREATE TRIGGER first_cutover_quarantine_no_delete BEFORE DELETE ON app_settings
        WHEN OLD.key='{_KEY}' BEGIN SELECT RAISE(ABORT,'persistent first cutover quarantine'); END""")
    # INSERT OR REPLACE 可绕过默认关闭的删除触发器，须在冲突处理前拦截。
    op.execute(f"""CREATE TRIGGER first_cutover_quarantine_no_replace BEFORE INSERT ON app_settings
        WHEN NEW.key='{_KEY}' AND EXISTS (SELECT 1 FROM app_settings WHERE key='{_KEY}')
        BEGIN SELECT RAISE(ABORT,'persistent first cutover quarantine'); END""")
    empty_literal = _EMPTY.replace("'", "''")
    connection.exec_driver_sql(f"""CREATE TRIGGER first_cutover_quarantine_no_reset BEFORE UPDATE ON app_settings
        WHEN (OLD.key='{_KEY}' AND (NEW.key IS NOT OLD.key OR OLD.value IS NOT '{empty_literal}'))
          OR (NEW.key='{_KEY}' AND OLD.key IS NOT '{_KEY}')
        BEGIN SELECT RAISE(ABORT,'persistent first cutover quarantine'); END""")
    # 原状态及原事件保留；迟到回调、直接 SQL 与 REPLACE 均不能重写隔离对象。
    # Grant撤销和真实用量仍正常记录，不把可变安全/计量权威冻结。
    for table in _RECORD_KEYS:
        primary_key = [row[1] for row in sorted(connection.exec_driver_sql(f'PRAGMA table_info("{table}")').fetchall(), key=lambda row: row[5]) if row[5]]
        unique_keys = [(primary_key, "1")] if primary_key else []
        for index in connection.exec_driver_sql(f'PRAGMA index_list("{table}")').fetchall():
            if not index[2]:
                continue
            columns = [row[2] for row in connection.exec_driver_sql(f'PRAGMA index_info("{index[1]}")').fetchall()]
            if not columns or any(column is None for column in columns):
                raise RuntimeError("隔离保护不支持未核实的表达式唯一索引")
            if columns == primary_key:
                continue
            predicate = "1"
            if index[4]:
                predicate = {
                    "uq_candidate_verification_active_candidate": "e.status IN ('requested','running') AND NEW.status IN ('requested','running')",
                    "idx_dpi_owner_request_idempotency": "e.request_idempotency_hash IS NOT NULL AND NEW.request_idempotency_hash IS NOT NULL",
                }.get(index[1])
                if predicate is None:
                    raise RuntimeError("隔离保护不支持未核实的部分唯一索引")
            unique_keys.append((columns, predicate))
        for operation, references in (("INSERT", ("NEW",)), ("UPDATE", ("OLD", "NEW")), ("DELETE", ("OLD",))):
            conditions = [_protected(table, reference) for reference in references]
            if operation != "DELETE":
                # 两种REPLACE都可经任意唯一键删除旧行；部分索引须同时满足新旧谓词，避免阻断独立历史。
                for columns, predicate in unique_keys:
                    existing_key = " AND ".join(f'e."{column}"=NEW."{column}"' for column in columns)
                    conditions.append(f'EXISTS (SELECT 1 FROM "{table}" e WHERE {existing_key} AND ({predicate}) AND ' + _protected(table, "e") + ")")
            op.execute(f"""CREATE TRIGGER first_cutover_{table}_no_{operation.lower()}
                BEFORE {operation} ON {table} WHEN {" OR ".join(conditions)}
                BEGIN SELECT RAISE(ABORT,'quarantined historical work'); END""")


def downgrade():
    raise RuntimeError("历史隔离不可移除；回退必须使用识别隔离的兼容代码并保留原件")
