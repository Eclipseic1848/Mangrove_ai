"""删除日志必须由显式迁移创建，既有错误形状失败关闭。"""
import importlib.util
from pathlib import Path
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.migration import MigrationContext


def test_deletion_migration_replay_preserves_operation():
    path=Path('src/database_migrations/alembic/versions/webui_0018_source_deletion.py')
    assert path.exists()
    assert b'\r' not in path.read_bytes()
    spec=importlib.util.spec_from_file_location('deletion_migration',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    engine=sa.create_engine('sqlite://')
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE source_artifacts (owner_id TEXT,artifact_id TEXT,snapshot_id TEXT)')
        conn.exec_driver_sql('CREATE TABLE source_snapshots (owner_id TEXT,snapshot_id TEXT)')
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
            conn.exec_driver_sql("INSERT INTO source_deletion_operations VALUES ('op','owner','task','key','hash','{}','planned',NULL,'now','now')")
            module.upgrade()
        assert conn.exec_driver_sql('SELECT operation_id FROM source_deletion_operations').scalar()=='op'
    engine.dispose()


def test_migration_keeps_context_immutable_except_confirmed_task_cleanup():
    path=Path('src/database_migrations/alembic/versions/webui_0018_source_deletion.py')
    spec=importlib.util.spec_from_file_location('deletion_context_migration',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    engine=sa.create_engine('sqlite://')
    with engine.begin() as conn:
        for sql in ('CREATE TABLE source_artifacts (owner_id TEXT,artifact_id TEXT,snapshot_id TEXT)', 'CREATE TABLE source_snapshots (owner_id TEXT,snapshot_id TEXT)', 'CREATE TABLE task_revision_contexts (owner_id TEXT,task_id TEXT)', 'CREATE TABLE web_task_contracts (owner_id TEXT,task_id TEXT)'):
            conn.exec_driver_sql(sql)
        with Operations.context(MigrationContext.configure(conn)): module.upgrade()
        triggers={row[0] for row in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert 'task_revision_contexts_no_delete' in triggers
        assert 'source_snapshots_no_update' in triggers
    engine.dispose()


def test_real_schema_tombstone_preserves_foreign_keys_and_rejects_identity_rewrite(tmp_path):
    import sqlite3,pytest
    from tests.database_migration_helpers import migrated_webui_database
    database=migrated_webui_database(tmp_path/'deletion.db')
    with sqlite3.connect(database) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute("INSERT INTO source_acquisition_attempts(attempt_id,owner_id,idempotency_key,request_hash,request_url,normalized_url,allowed_scope_json,purpose,status,started_at) VALUES ('attempt','owner','key','hash','https://example.org','https://example.org','{}','测试','succeeded','now')")
        connection.execute("INSERT INTO source_snapshots(snapshot_id,owner_id,attempt_id,allowed_scope_json,valid_page_count,failed_page_count,created_at) VALUES ('snapshot','owner','attempt','{}',1,0,'now')")
        connection.execute("INSERT INTO source_artifacts VALUES ('artifact','owner','snapshot','url','url','now','hash','text/html',4,'标题','正文',X'01020304')")
        connection.execute("INSERT INTO web_task_contracts VALUES ('owner','retained',1,'snapshot','{}','{}','{}','now')")
        with pytest.raises(sqlite3.IntegrityError): connection.execute("DELETE FROM source_artifacts")
        connection.execute("INSERT INTO source_deletion_operations VALUES ('op','owner','target','key','hash','{}','cleaning',NULL,'now','now')")
        connection.execute("INSERT INTO source_deletions VALUES ('owner','web_artifact:artifact','op','hash','snapshot','deleting',NULL)")
        connection.execute("DELETE FROM source_artifacts WHERE artifact_id='artifact'")
        connection.execute("UPDATE source_snapshots SET allowed_scope_json='{}',coverage_json='{}',valid_page_count=0,failed_page_count=0 WHERE snapshot_id='snapshot'")
        with pytest.raises(sqlite3.IntegrityError): connection.execute("UPDATE source_snapshots SET owner_id='other'")
        with pytest.raises(sqlite3.IntegrityError): connection.execute('DELETE FROM source_snapshots')
        assert connection.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert connection.execute('SELECT snapshot_id,owner_id,attempt_id FROM source_snapshots').fetchone()==('snapshot','owner','attempt')


def test_deletion_migration_rejects_existing_wrong_shape():
    import pytest
    path=Path('src/database_migrations/alembic/versions/webui_0018_source_deletion.py')
    spec=importlib.util.spec_from_file_location('wrong_deletion_shape',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    engine=sa.create_engine('sqlite://')
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE source_deletion_operations (operation_id INTEGER PRIMARY KEY)')
        with Operations.context(MigrationContext.configure(conn)):
            with pytest.raises(RuntimeError,match='已有形状不兼容'):module.upgrade()
    engine.dispose()
