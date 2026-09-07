"""设备会话迁移只在临时旧库前向验证，并以显式备份恢复。"""
import hashlib
import shutil
import sqlite3

import pytest
from sqlalchemy import create_engine

import src.database_migrations as migrations
from src.api.store import WebUIStore


def test_webui_0010_session_upgrade_preserves_users_tasks_and_backup(tmp_path):
    database = tmp_path / "synthetic-old.db"
    # 从冻结的历史 revision 建立真实旧 Schema，不伪造当前版本标记。
    empty_backup = tmp_path / "empty-before.db"
    empty_backup.touch()
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    try:
        with engine.begin() as connection:
            config = migrations._alembic_config(connection)
            config.attributes["backup_sha256"] = hashlib.sha256(empty_backup.read_bytes()).hexdigest()
            migrations.command.upgrade(config, "webui_0010")
    finally:
        engine.dispose()
    with sqlite3.connect(database) as conn:
        conn.execute("INSERT INTO users(user_id, username, password_hash, display_name, created_at, role, pending, disabled) VALUES ('synthetic-owner', 'synthetic-user', 'synthetic-hash', '虚构用户', '2026-01-01', 'user', 0, 0)")
        conn.execute("INSERT INTO data_prep_tasks(task_id, user_id, spec_json, status, created_at, updated_at) VALUES ('synthetic-task', 'synthetic-owner', '{}', 'COMPLETED', '2026-01-01', '2026-01-01')")
        user_columns = ",".join(row[1] for row in conn.execute("PRAGMA table_info(users)"))
        users = conn.execute(f"SELECT {user_columns} FROM users").fetchall()
        tasks = conn.execute("SELECT * FROM data_prep_tasks").fetchall()
    target = migrations.DatabaseTarget("webui", database)
    assert migrations.inspect_database(target).current_revision == "webui_0010"
    before = database.read_bytes()
    with pytest.raises(migrations.SchemaNotCurrentError):
        WebUIStore(str(database))
    assert database.read_bytes() == before
    receipt = migrations.apply_migrations(target, tmp_path / "before-sessions.db", expected_source_sha256=hashlib.sha256(before).hexdigest())
    assert receipt.applied_revisions == ("webui_0011", "webui_0012")
    assert migrations.inspect_database(target).state == "current"
    store = WebUIStore(str(database))
    assert store.get_user("synthetic-owner")["display_name"] == "虚构用户"
    with sqlite3.connect(database) as conn:
        assert conn.execute(f"SELECT {user_columns} FROM users").fetchall() == users
        assert conn.execute("SELECT * FROM data_prep_tasks").fetchall() == tasks
        assert conn.execute("SELECT COUNT(*) FROM platform_login_sessions").fetchone()[0] == 0
    current = database.read_bytes()
    replay = migrations.apply_migrations(target, tmp_path / "before-replay.db")
    assert replay.applied_revisions == ()
    assert database.read_bytes() == current
    restored = tmp_path / "restored-old.db"
    shutil.copyfile(receipt.backup_path, restored)
    verified = migrations.verify_restored_copy(receipt.receipt_path, restored)
    assert verified.integrity_check == "ok"
    assert verified.foreign_key_violations == 0
    assert restored.read_bytes() == receipt.backup_path.read_bytes()
    with sqlite3.connect(restored) as conn:
        assert conn.execute(f"SELECT {user_columns} FROM users").fetchall() == users
        assert conn.execute("SELECT * FROM data_prep_tasks").fetchall() == tasks
    assert migrations.inspect_database(migrations.DatabaseTarget("webui", restored)).current_revision == "webui_0010"
    with pytest.raises(migrations.SchemaNotCurrentError):
        WebUIStore(str(restored))
