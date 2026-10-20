"""清理任务时解除本人刷新记录的外键，保留其他任务和网页原件。"""
import sqlite3

import pytest

from src.account_execution import execution_context
from src.api.store import WebUIStore
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database
from tests.test_web_source_delivery_api import _seed_snapshot


@pytest.fixture
def refresh_history(tmp_path):
    database = migrated_webui_database(tmp_path / "webui.db")
    auth = seed_execution_owner(database, "user-a")
    store = WebUIStore(str(database))
    snapshot_id, content = _seed_snapshot(database)
    with execution_context(auth):
        for task_id in ("target", "retained"):
            store.create_semantic_workspace_task("user-a", task_id=task_id, title="合成任务",
                objective_text="合成删除验证", upload_ids=[], output_formats=["json"],
                provider="local", model="synthetic", external_api_confirmed=False)
            store.soft_delete_semantic_workspace_task("user-a", task_id)
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA foreign_keys=ON")
        for task_id in ("target", "retained"):
            db.execute("INSERT INTO source_refresh_intents(owner_id,task_id,idempotency_key,request_hash,"
                "expected_revision,attempt_id,snapshot_id,status,started_at,updated_at) "
                "VALUES ('user-a',?,'fixture-key',?,1,'source_attempt_web_delivery',?,'completed','now','now')",
                (task_id, "a" * 64, snapshot_id))
        db.execute("INSERT INTO source_deletion_operations VALUES "
            "('fixture-operation','user-a','target','fixture-key','hash','{}','cleaning',NULL,'now','now')")
    return store, database, content


def test_purge_removes_only_target_refresh_bindings(refresh_history):
    store, database, content = refresh_history
    assert store.purge_semantic_workspace_task("user-a", "target", deletion_operation_id="fixture-operation")
    assert store.get_semantic_workspace_task("user-a", "target") is None
    assert store.get_semantic_workspace_task("user-a", "retained") is not None
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT task_id FROM source_refresh_intents").fetchall() == [("retained",)]
        assert db.execute("SELECT content_blob FROM source_artifacts").fetchone()[0] == content
        assert db.execute("SELECT COUNT(*) FROM source_snapshots").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM source_acquisition_attempts").fetchone()[0] == 1
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("owner,state", [("other-owner", "cleaning"), ("user-a", "incomplete")])
def test_purge_rejection_preserves_refresh_bindings(refresh_history, owner, state):
    store, database, _ = refresh_history
    with sqlite3.connect(database) as db:
        db.execute("UPDATE source_deletion_operations SET state=?", (state,))
    with pytest.raises(ValueError, match="关联清理尚未完成"):
        store.purge_semantic_workspace_task(owner, "target", deletion_operation_id="fixture-operation")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM source_refresh_intents").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM semantic_workspace_tasks").fetchone()[0] == 2
