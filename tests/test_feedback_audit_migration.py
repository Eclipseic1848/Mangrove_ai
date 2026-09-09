"""反馈审计仅在临时旧库验证前向、重放和备份恢复。"""
import hashlib
import shutil
import sqlite3
from contextlib import closing

from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from src import database_migrations as migrations


def test_feedback_audit_upgrade_preserves_history_and_restore(tmp_path):
    database = tmp_path / 'old.db'
    engine = create_engine(URL.create('sqlite', database=str(database)))
    try:
        with engine.begin() as conn:
            config = migrations._alembic_config(conn)
            config.attributes['backup_sha256'] = 'a' * 64
            migrations.command.upgrade(config, 'webui_0012')
    finally:
        engine.dispose()
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("INSERT INTO message_feedback(message_id,conv_id,user_id,rating,reasons,comment,created_at) VALUES (999,'broken','missing','down','[\"历史私密理由\"]','旧正文','now')")
        conn.commit()
        history = conn.execute('SELECT * FROM message_feedback').fetchall()
    before = database.read_bytes()
    target = migrations.DatabaseTarget('webui', database)
    assert migrations.inspect_database(target).pending_revisions == ('webui_0013', 'webui_0014', 'webui_0015', 'webui_0016')
    receipt = migrations.apply_migrations(target, tmp_path / 'backup.db', expected_source_sha256=hashlib.sha256(before).hexdigest())
    assert receipt.applied_revisions == ('webui_0013', 'webui_0014', 'webui_0015', 'webui_0016')
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute('SELECT * FROM message_feedback').fetchall() == history
        assert conn.execute('SELECT count(*) FROM feedback_content_access').fetchone()[0] == 0
    current = database.read_bytes()
    assert migrations.apply_migrations(target, tmp_path / 'replay.db').applied_revisions == ()
    assert database.read_bytes() == current
    restored = tmp_path / 'restored.db'
    shutil.copyfile(receipt.backup_path, restored)
    assert migrations.verify_restored_copy(receipt.receipt_path, restored).integrity_check == 'ok'
    assert restored.read_bytes() == receipt.backup_path.read_bytes()
    assert migrations.inspect_database(migrations.DatabaseTarget('webui', restored)).current_revision == 'webui_0012'
