"""仅临时库验证执行绑定迁移、历史保留及备份恢复。"""
from contextlib import closing
import hashlib
import json
import shutil
import sqlite3

from alembic import command
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from src import database_migrations as migrations
from src import account_execution as execution


def upgrade_old(database):
    engine = create_engine(URL.create('sqlite', database=str(database)))
    try:
        with engine.connect() as conn:
            config = migrations._alembic_config(conn)
            config.attributes['backup_sha256'] = 'a' * 64
            command.upgrade(config, 'webui_0011')
            conn.commit()
    finally:
        engine.dispose()


def test_forward_backfills_without_rewriting_roots_and_restore_is_exact(tmp_path):
    database = tmp_path / 'synthetic.db'
    upgrade_old(database)
    with closing(sqlite3.connect(database)) as conn:
        for owner, disabled, pending in [('active', 0, 0), ('disabled', 1, 0), ('pending', 0, 1)]:
            conn.execute("INSERT INTO users(user_id,username,password_hash,display_name,created_at,role,disabled,pending) VALUES (?,?,'fake','虚构','2026-01-01','user',?,?)", (owner, owner, disabled, pending))
        for owner, status in [('active', 'running'), ('disabled', 'completed'), ('pending', 'outcome_unknown'), ('missing', 'completed')]:
            conn.execute("INSERT INTO semantic_workspace_tasks(task_id,user_id,title,objective_text,status,created_at,updated_at) VALUES (?,?, '历史任务','保留问题和正文',?,'now','now')", ('task-' + owner, owner, status))
        conn.execute("INSERT INTO capability_platform_validation_runs VALUES ('same-id','pack','1','digest','key','succeeded',?,'now','now')", (json.dumps({'actor_id': 'active'}),))
        conn.execute("INSERT INTO capability_platform_validation_runs VALUES ('unknown-owner','pack','1','digest','key-2','running','{}','now','now')")
        conn.execute("INSERT INTO delivery_publish_intents(publication_key,command_hash,owner_id,task_id,task_revision,run_id,status,staging_dir,final_dir,created_at,updated_at) VALUES ('synthetic-publication','synthetic-hash','active','task-active',1,'synthetic-run','committing','synthetic-staging','synthetic-final','now','now')")
        for grant, owner, revoked in [('active-grant', 'active', None), ('disabled-grant', 'disabled', None), ('pending-grant', 'pending', None), ('old-revoked', 'disabled', '2025-01-01')]:
            conn.execute("INSERT INTO model_connection_grants(grant_id,token_hash,owner_user_id,task_id,revision,run_id,connection_id,purpose,base_url,model,api_format,locality,expires_at,created_at,revoked_at,revoke_reason) VALUES (?,?,?,'task',1,'run','fake-connection','test','https://invalid.example','fake','fake','local','2099-01-01','now',?,?)", (grant, grant, owner, revoked, 'original' if revoked else None))
        original_grants = conn.execute('SELECT * FROM model_connection_grants ORDER BY grant_id').fetchall()
        publication_columns = [row[1] for row in conn.execute('PRAGMA table_info(delivery_publish_intents)')]
        original_columns = [row[1] for row in conn.execute('PRAGMA table_info(users)')]
        roots = {table: conn.execute(f'SELECT * FROM {table}').fetchall() for table in ('users', 'semantic_workspace_tasks', 'capability_platform_validation_runs', 'delivery_publish_intents')}
        conn.commit()
    before = database.read_bytes()
    target = migrations.DatabaseTarget('webui', database)
    assert migrations.inspect_database(target).pending_revisions == ('webui_0012', 'webui_0013', 'webui_0014', 'webui_0015', 'webui_0016', 'webui_0017', 'webui_0018', 'webui_0019', 'webui_0020', 'webui_0021')
    with pytest.raises(migrations.SchemaNotCurrentError):
        migrations.inspect_database(target).require_current()
    assert database.read_bytes() == before
    receipt = migrations.apply_migrations(target, tmp_path / 'backup.db', expected_source_sha256=hashlib.sha256(before).hexdigest())
    assert receipt.applied_revisions == ('webui_0012', 'webui_0013', 'webui_0014', 'webui_0015', 'webui_0016', 'webui_0017', 'webui_0018', 'webui_0019', 'webui_0020', 'webui_0021')
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute('SELECT ' + ','.join(original_columns) + ' FROM users').fetchall() == roots['users']
        assert conn.execute('SELECT ' + ','.join(publication_columns) + ' FROM delivery_publish_intents').fetchall() == roots['delivery_publish_intents']
        assert conn.execute('SELECT execution_generation,status FROM delivery_publish_intents').fetchone() == (0, 'committing')
        for table in ('semantic_workspace_tasks', 'capability_platform_validation_runs'):
            assert conn.execute(f'SELECT * FROM {table}').fetchall() == roots[table]
        assert dict(conn.execute('SELECT user_id,execution_generation FROM users')) == {'active': 0, 'disabled': 1, 'pending': 1}
        grants = {row[0]: row[1:] for row in conn.execute('SELECT grant_id,revoked_at,revoke_reason FROM model_connection_grants')}
        assert grants['active-grant'] == (None, None)
        assert grants['old-revoked'] == ('2025-01-01', 'original')
        for grant in ('disabled-grant', 'pending-grant'):
            assert grants[grant][0] is not None
            assert grants[grant][1] == 'account_execution_hold'
        bindings = {row[0]: row[1:] for row in conn.execute('SELECT resource_id,generation,state FROM account_execution_bindings')}
        assert bindings == {'task-active': (0, 'active'), 'task-disabled': (0, 'paused'), 'task-pending': (0, 'active'), 'platform:same-id': (0, 'idle')}
        assert [(op['owner_user_id'], op['status'], op['reconciliation_complete']) for owner in ('disabled', 'pending') for op in execution.list_hold_operations(conn, owner)] == [('disabled', 'pending', 0), ('pending', 'pending', 0)]
        conn.execute('BEGIN IMMEDIATE')
        auth = execution.capture_authorization(conn, 'active')
        execution.require_binding(conn, auth, 'workspace', 'task-active')
        with pytest.raises(execution.ExecutionDenied):
            execution.require_binding(conn, auth, 'chat', 'unknown-run')
        with pytest.raises(execution.ExecutionDenied):
            execution.capture_authorization(conn, 'pending')
        conn.rollback()
        conn.execute('BEGIN IMMEDIATE')
        execution.update_account_status(conn, 'disabled', disabled=False, pending=False, actor_user_id='active', now=10)
        assert {row[0]: row[1:] for row in conn.execute('SELECT grant_id,revoked_at,revoke_reason FROM model_connection_grants')} == grants
        conn.rollback()
    current = database.read_bytes()
    assert migrations.apply_migrations(target, tmp_path / 'unused-replay.db').applied_revisions == ()
    assert database.read_bytes() == current
    restored = tmp_path / 'restored.db'
    shutil.copyfile(receipt.backup_path, restored)
    assert migrations.verify_restored_copy(receipt.receipt_path, restored).integrity_check == 'ok'
    assert restored.read_bytes() == receipt.backup_path.read_bytes()
    assert migrations.inspect_database(migrations.DatabaseTarget('webui', restored)).current_revision == 'webui_0011'
    with closing(sqlite3.connect(restored)) as conn:
        assert conn.execute('SELECT * FROM model_connection_grants ORDER BY grant_id').fetchall() == original_grants
        for table, rows in roots.items():
            assert conn.execute(f'SELECT * FROM {table}').fetchall() == rows


def test_frozen_revision_manifest_is_complete():
    # 每个旧 revision 都按冻结摘要复核，新增版本不得改写历史迁移。
    for revision in migrations._revision_manifest():
        assert len(migrations._revision_content_sha256(revision)) == 64
