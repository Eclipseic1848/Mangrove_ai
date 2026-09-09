"""相同DDL优化后，版本、证据和结构变化仍须现场拒绝。"""
from contextlib import closing
import sqlite3

import pytest

import src.database_migrations as migrations
from tests.database_migration_helpers import migrated_webui_database


def change_fixture_evidence(connection, table, statement):
    # 只为临时损坏副本布置相同DDL/版本但不同证据；产品触发器原样恢复。
    version = connection.execute("PRAGMA schema_version").fetchone()[0]
    triggers = connection.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)).fetchall()
    for name, _ in triggers:
        connection.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')
    connection.execute(statement)
    for _, sql in triggers:
        connection.execute(sql)
    connection.execute(f"PRAGMA schema_version={version}")


@pytest.mark.parametrize("change, gap", [
    ("UPDATE candidate_verification_migrations SET backup_sha256='invalid'", "evidence:candidate_verification_migrations"),
    ("DROP INDEX idx_dpt_unit", "object:idx_dpt_unit"),
    ("ALTER TABLE memory_hit_log DROP COLUMN hit", "column:memory_hit_log.hit"),
])
def test_warmed_schema_does_not_hide_later_database_changes(tmp_path, change, gap):
    database = migrated_webui_database(tmp_path / "schema.db")
    target = migrations.DatabaseTarget(profile="webui", path=database)
    migrations.inspect_database(target).require_current()
    migrations.inspect_database(target).require_current()
    with closing(sqlite3.connect(database)) as connection:
        if change.startswith("UPDATE candidate_verification_migrations"):
            change_fixture_evidence(connection, "candidate_verification_migrations", change)
        else:
            connection.execute(change)
        connection.commit()
    before = database.read_bytes()
    status = migrations.inspect_database(target)
    assert status.state == "drift"
    assert gap in status.gaps
    assert database.read_bytes() == before


def test_warmed_schema_does_not_hide_revision_or_other_database_evidence(tmp_path):
    first = migrated_webui_database(tmp_path / "first.db")
    second = migrated_webui_database(tmp_path / "second.db")
    migrations.inspect_database(migrations.DatabaseTarget(profile="webui", path=first)).require_current()
    with closing(sqlite3.connect(second)) as connection:
        change_fixture_evidence(connection, "runtime_routing_migrations", "DELETE FROM runtime_routing_migrations")
        connection.commit()
    result = migrations.inspect_database(migrations.DatabaseTarget(profile="webui", path=second))
    assert result.state == "drift"
    assert "evidence:runtime_routing_migrations" in result.gaps
    with closing(sqlite3.connect(first)) as connection:
        connection.execute("UPDATE alembic_version SET version_num='unknown-future'")
        connection.commit()
    result = migrations.inspect_database(migrations.DatabaseTarget(profile="webui", path=first))
    assert result.state == "unknown"
