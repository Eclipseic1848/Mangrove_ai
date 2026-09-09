"""官方备份/收据/恢复链必须带上已提交 WAL；全部为临时库。"""
from contextlib import closing
import hashlib
import shutil
import sqlite3

from src.database_migrations import DatabaseTarget, apply_migrations, verify_restored_copy
from tests.database_migration_helpers import migrated_webui_database


def test_official_backup_restores_committed_wal_without_changing_source(tmp_path):
    source = migrated_webui_database(tmp_path / "source.db")
    # 这里故意保留连接，确保备份时真实存在未 checkpoint 的已提交 WAL。
    with closing(sqlite3.connect(source)) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        connection.execute("CREATE TABLE synthetic_preserved (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO synthetic_preserved VALUES (1, 'committed-wal-value')")
        connection.commit()
        assert source.with_name(source.name + "-wal").stat().st_size > 0
        receipt = apply_migrations(DatabaseTarget("webui", source), tmp_path / "backup.db", expected_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
        restored = tmp_path / "restored.db"
        shutil.copyfile(receipt.backup_path, restored)
        verify_restored_copy(receipt.receipt_path, restored)
        with closing(sqlite3.connect(restored)) as recovered:
            assert recovered.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert recovered.execute("SELECT * FROM synthetic_preserved").fetchall() == [(1, "committed-wal-value")]
        assert connection.execute("SELECT * FROM synthetic_preserved").fetchall() == [(1, "committed-wal-value")]
