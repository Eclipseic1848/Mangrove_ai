"""独立脚本与 pytest 共用的临时调度 Owner，不接触真实用户库。"""
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from tests.database_migration_helpers import migrated_webui_database


@contextmanager
def scheduler_owner(database: Path):
    from src.api.store import WebUIStore

    store = WebUIStore(str(migrated_webui_database(database)))
    owner = store.create_user("scheduler-fixture", "synthetic-unused-hash")
    with patch("src.api.auth.get_store", return_value=store):
        yield owner
