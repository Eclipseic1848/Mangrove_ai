"""混合来源合同显式迁移；仅使用临时 SQLite。"""
from pathlib import Path
import importlib.util
import sqlalchemy as sa
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_source_contract_migration_preserves_rows_and_replays():
    path = Path('src/database_migrations/alembic/versions/webui_0016_mixed_source_contract.py')
    assert path.exists()
    assert b'\r' not in path.read_bytes()
    spec = importlib.util.spec_from_file_location('mixed_migration',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine('sqlite://')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE semantic_workspace_revisions (revision INTEGER, source_refs_json TEXT)')
        connection.exec_driver_sql("INSERT INTO semantic_workspace_revisions VALUES (1, '[{\"upload_id\":\"A\"}]')")
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            module.upgrade()
        assert connection.exec_driver_sql('SELECT * FROM semantic_workspace_revisions').fetchone() == (1,'[{"upload_id":"A"}]',None)
    engine.dispose()

@pytest.mark.parametrize('declaration',['INTEGER','TEXT NOT NULL','TEXT DEFAULT "wrong"'])
def test_source_contract_migration_rejects_wrong_existing_shape(declaration):
    spec=importlib.util.spec_from_file_location('mixed_migration','src/database_migrations/alembic/versions/webui_0016_mixed_source_contract.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    engine=sa.create_engine('sqlite://')
    with engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE TABLE semantic_workspace_revisions (source_contract_json {declaration})')
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError,match='source_contract_json'):
                module.upgrade()
    engine.dispose()
