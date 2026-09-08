"""结果追问上下文只在合成旧库显式迁移，并验证备份可恢复。"""
import hashlib
import shutil
import sqlite3

import pytest
from sqlalchemy import create_engine

import src.database_migrations as migrations
from src.conversation_steering.repository import SqliteSteeringRepository


def test_result_context_migration_preserves_original_turn_and_restore(tmp_path):
    database = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    try:
        with engine.begin() as connection:
            config = migrations._alembic_config(connection)
            config.attributes["backup_sha256"] = hashlib.sha256(b"").hexdigest()
            migrations.command.upgrade(config, "webui_0013")
    finally:
        engine.dispose()
    with sqlite3.connect(database) as conn:
        conn.execute("INSERT INTO conversation_raw_turns VALUES ('turn-old','owner','task',1,'原始追问','old-key','2026-01-01')")
    before = database.read_bytes()
    with pytest.raises(migrations.SchemaNotCurrentError):
        SqliteSteeringRepository(str(database))
    assert database.read_bytes() == before
    target = migrations.DatabaseTarget("webui", database)
    receipt = migrations.apply_migrations(target, tmp_path / "backup.db", expected_source_sha256=hashlib.sha256(before).hexdigest())
    assert receipt.applied_revisions == ("webui_0014",)
    turn = SqliteSteeringRepository(str(database)).get_turn("owner", "turn-old")
    assert turn.text == "原始追问"
    assert turn.result_context is None
    after = database.read_bytes()
    assert migrations.apply_migrations(target, tmp_path / "replay.db").applied_revisions == ()
    assert database.read_bytes() == after
    restored = tmp_path / "restored.db"
    shutil.copyfile(receipt.backup_path, restored)
    assert migrations.verify_restored_copy(receipt.receipt_path, restored).integrity_check == "ok"
    assert restored.read_bytes() == receipt.backup_path.read_bytes()
    with sqlite3.connect(restored) as conn:
        assert conn.execute("SELECT text FROM conversation_raw_turns").fetchone() == ("原始追问",)
    with pytest.raises(migrations.SchemaNotCurrentError):
        SqliteSteeringRepository(str(restored))
