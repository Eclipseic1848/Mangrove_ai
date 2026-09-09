"""只在隔离库验证来源读取事实显式迁移。"""
import importlib.util
from pathlib import Path
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.migration import MigrationContext


def test_read_uses_explicit_migration_replays_without_losing_usage():
    path=Path("src/database_migrations/alembic/versions/webui_0017_source_read_uses.py")
    assert path.exists()
    assert b"\r" not in path.read_bytes()
    spec=importlib.util.spec_from_file_location("uses_migration",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    engine=sa.create_engine("sqlite://")
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
            conn.exec_driver_sql("INSERT INTO source_read_uses VALUES ('use','owner',NULL,NULL,'export','[]','active','now',NULL)")
            module.upgrade()
        assert conn.exec_driver_sql("SELECT state FROM source_read_uses").scalar()=="active"
    engine.dispose()


def test_read_uses_migration_rejects_existing_wrong_shape_and_preserves_other_data():
    import pytest
    path=Path("src/database_migrations/alembic/versions/webui_0017_source_read_uses.py")
    spec=importlib.util.spec_from_file_location("uses_migration_bad",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    engine=sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE retained (body TEXT)")
        conn.exec_driver_sql("INSERT INTO retained VALUES ('原始资料')")
        conn.exec_driver_sql("CREATE TABLE source_read_uses (use_id INTEGER)")
        with Operations.context(MigrationContext.configure(conn)):
            with pytest.raises(RuntimeError,match="形状不兼容"): module.upgrade()
        assert conn.exec_driver_sql("SELECT body FROM retained").scalar()=="原始资料"
        assert conn.exec_driver_sql("PRAGMA table_info(source_read_uses)").first()[2]=="INTEGER"
    engine.dispose()
