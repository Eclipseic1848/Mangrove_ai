"""追加不可变运行封存证据，阻止封存身份的迟到写入。"""
from alembic import op

revision = "webui_0024"
down_revision = "webui_0023"
branch_labels = None
depends_on = None
operation_summary = ("新增不可变运行封存及审计证据；保留原运行、失败及事件",)


def _sealed(reference):
    return f"""EXISTS (SELECT 1 FROM runtime_run_seals s
        WHERE s.owner_id={reference}.user_id AND s.task_id={reference}.task_id
          AND s.revision={reference}.revision)"""


def upgrade():
    op.execute("""CREATE TABLE runtime_run_seals (
        owner_id TEXT NOT NULL, task_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision > 0), run_id TEXT NOT NULL,
        runtime_identity_sha256 TEXT NOT NULL, snapshot_json TEXT NOT NULL,
        stop_proof_json TEXT NOT NULL, actor_id TEXT NOT NULL, reason TEXT NOT NULL,
        approval_reference TEXT NOT NULL, idempotency_key TEXT NOT NULL,
        request_sha256 TEXT NOT NULL, sealed_at TEXT NOT NULL,
        PRIMARY KEY(owner_id,task_id,revision), UNIQUE(actor_id,idempotency_key)
    )""")
    for operation in ("UPDATE", "DELETE"):
        op.execute(f"""CREATE TRIGGER runtime_run_seals_no_{operation.lower()}
            BEFORE {operation} ON runtime_run_seals
            BEGIN SELECT RAISE(ABORT,'immutable runtime seal'); END""")
    # SQLite REPLACE 默认不触发被替换行的删除触发器，两个唯一身份都须在插入前保护。
    op.execute("""CREATE TRIGGER runtime_run_seals_no_replace BEFORE INSERT ON runtime_run_seals
        WHEN EXISTS (SELECT 1 FROM runtime_run_seals s
            WHERE (s.owner_id=NEW.owner_id AND s.task_id=NEW.task_id AND s.revision=NEW.revision)
               OR (s.actor_id=NEW.actor_id AND s.idempotency_key=NEW.idempotency_key))
        BEGIN SELECT RAISE(ABORT,'immutable runtime seal'); END""")
    for table in ("agentic_runtime_runs", "agentic_runtime_events"):
        for operation, references in (("INSERT", ("NEW",)), ("UPDATE", ("OLD", "NEW")), ("DELETE", ("OLD",))):
            conditions = [_sealed(reference) for reference in references]
            if table == "agentic_runtime_events" and operation != "DELETE":
                # 唯一键冲突也可能借 REPLACE 删除另一个身份的封存事件。
                conditions.append("""EXISTS (
                    SELECT 1 FROM agentic_runtime_events e JOIN runtime_run_seals s
                      ON s.owner_id=e.user_id AND s.task_id=e.task_id AND s.revision=e.revision
                    WHERE e.event_id=NEW.event_id OR e.sequence=NEW.sequence)""")
            op.execute(f"""CREATE TRIGGER {table}_sealed_no_{operation.lower()}
                BEFORE {operation} ON {table} WHEN {" OR ".join(conditions)}
                BEGIN SELECT RAISE(ABORT,'sealed runtime history'); END""")


def downgrade():
    raise RuntimeError("运行封存及原始审计必须保留；请通过已验证备份恢复")
