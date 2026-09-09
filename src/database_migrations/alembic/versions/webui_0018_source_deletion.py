"""显式关联清理日志和不含正文的身份墓碑。"""
from alembic import op

revision='webui_0018'
down_revision='webui_0017'
branch_labels=None
depends_on=None
operation_summary=('新增关联删除日志、最小身份墓碑和受控原件删除门',)

TABLES={
 'source_deletion_operations': 'operation_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, task_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, plan_json TEXT NOT NULL, state TEXT NOT NULL, error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL',
 'source_deletions': 'owner_id TEXT NOT NULL, source_key TEXT NOT NULL, operation_id TEXT NOT NULL, sha256 TEXT NOT NULL, snapshot_id TEXT, state TEXT NOT NULL, deleted_at TEXT, PRIMARY KEY(owner_id,source_key)',
}


def upgrade():
    connection=op.get_bind()
    for name,columns in TABLES.items():
        existing=connection.exec_driver_sql('PRAGMA table_info('+name+')').fetchall()
        if existing:
            # 临时同形表提供SQLite自己的规范化结果，不能静默接受错列。
            connection.exec_driver_sql('CREATE TEMP TABLE expected_deletion_shape ('+columns+')')
            expected=connection.exec_driver_sql('PRAGMA table_info(expected_deletion_shape)').fetchall()
            connection.exec_driver_sql('DROP TABLE expected_deletion_shape')
            if [tuple(row)[1:] for row in existing]!=[tuple(row)[1:] for row in expected]:
                raise RuntimeError(name+' 已有形状不兼容')
        else:
            op.execute('CREATE TABLE '+name+' ('+columns+')')
    op.execute('CREATE UNIQUE INDEX IF NOT EXISTS ix_deletion_owner_key ON source_deletion_operations(owner_id,task_id,idempotency_key)')
    # 仅持久删除意图能授权物理删除；正常来源仍不可改写。
    op.execute('DROP TRIGGER IF EXISTS source_artifacts_no_delete')
    op.execute("CREATE TRIGGER source_artifacts_no_delete BEFORE DELETE ON source_artifacts WHEN NOT EXISTS (SELECT 1 FROM source_deletions d JOIN source_deletion_operations o ON o.operation_id=d.operation_id AND o.owner_id=d.owner_id WHERE d.owner_id=OLD.owner_id AND d.source_key='web_artifact:'||OLD.artifact_id AND d.state='deleting' AND o.state='cleaning') BEGIN SELECT RAISE(ABORT,'SourceArtifact 未经确认不可删除'); END")
    # snapshot身份仍被保留任务外键引用，永远不能物理删除。
    op.execute('DROP TRIGGER IF EXISTS source_snapshots_no_delete')
    op.execute("CREATE TRIGGER source_snapshots_no_delete BEFORE DELETE ON source_snapshots BEGIN SELECT RAISE(ABORT,'SourceSnapshot 不可删除'); END")

    for table in ('task_revision_contexts','web_task_contracts'):
        if connection.exec_driver_sql("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
            op.execute('DROP TRIGGER IF EXISTS '+table+'_no_delete')
            op.execute("CREATE TRIGGER "+table+"_no_delete BEFORE DELETE ON "+table+" WHEN NOT EXISTS (SELECT 1 FROM source_deletion_operations o WHERE o.owner_id=OLD.owner_id AND o.task_id=OLD.task_id AND o.state='cleaning') BEGIN SELECT RAISE(ABORT,'任务合同未经清理确认不可删除'); END")
    op.execute('DROP TRIGGER IF EXISTS source_snapshots_no_update')
    op.execute("CREATE TRIGGER source_snapshots_no_update BEFORE UPDATE ON source_snapshots WHEN NOT (NEW.snapshot_id=OLD.snapshot_id AND NEW.owner_id=OLD.owner_id AND NEW.attempt_id=OLD.attempt_id AND NEW.created_at=OLD.created_at AND NEW.allowed_scope_json='{}' AND NEW.coverage_json='{}' AND NEW.valid_page_count=0 AND NEW.failed_page_count=0 AND NOT EXISTS (SELECT 1 FROM source_artifacts a WHERE a.owner_id=OLD.owner_id AND a.snapshot_id=OLD.snapshot_id) AND EXISTS (SELECT 1 FROM source_deletions d JOIN source_deletion_operations o ON o.operation_id=d.operation_id AND o.owner_id=d.owner_id WHERE d.owner_id=OLD.owner_id AND d.snapshot_id=OLD.snapshot_id AND o.state='cleaning')) BEGIN SELECT RAISE(ABORT,'SourceSnapshot 不可改写'); END")

    if connection.exec_driver_sql("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_page_failures'").fetchone():
        op.execute('DROP TRIGGER IF EXISTS source_page_failures_no_delete')
        op.execute("CREATE TRIGGER source_page_failures_no_delete BEFORE DELETE ON source_page_failures WHEN NOT (NOT EXISTS (SELECT 1 FROM source_artifacts a WHERE a.owner_id=OLD.owner_id AND a.snapshot_id=OLD.snapshot_id) AND EXISTS (SELECT 1 FROM source_deletions d JOIN source_deletion_operations o ON o.operation_id=d.operation_id AND o.owner_id=d.owner_id WHERE d.owner_id=OLD.owner_id AND d.snapshot_id=OLD.snapshot_id AND o.state='cleaning')) BEGIN SELECT RAISE(ABORT,'页面失败记录未经清理确认不可删除'); END")


def downgrade():
    raise RuntimeError('删除事实不能丢失，请通过显式备份恢复')
