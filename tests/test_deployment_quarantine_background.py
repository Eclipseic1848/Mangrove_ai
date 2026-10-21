"""首次维护隔离穿过后台收口与学习队列；仅临时迁移库。"""
import asyncio
from contextlib import closing
import hashlib
import json
import shutil
import sqlite3
from types import SimpleNamespace

import pytest

from src.api.account_execution_runtime import AccountExecutionManager
from src.api.store import WebUIStore
from src.memory.learning_receipts import LearningReceipts
from src.memory.workspace_learning import pending_lesson_failures, pending_template_uses
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture(scope="module")
def background_template(tmp_path_factory):
    return migrated_webui_database(tmp_path_factory.mktemp("quarantine-background") / "webui.db")


@pytest.fixture
def database(tmp_path, background_template):
    path = tmp_path / "webui.db"
    shutil.copyfile(background_template, path)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executemany(
            "INSERT INTO users(user_id,username,password_hash,created_at,execution_generation) "
            "VALUES (?,?,'synthetic-hash','before',1)",
            [("owner-a", "synthetic-a"), ("owner-b", "synthetic-b")],
        )
        conn.execute("INSERT INTO account_execution_bindings VALUES ('owner-a','workspace','legacy-task',0,'idle',1)")
    return path


def quarantine(database, targets, *, held_operation=None):
    # 本组验证清单消费接缝；真实生产注册与关系覆盖由注册器回归独立验证。
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.row_factory = sqlite3.Row
        if held_operation is None:
            table = "account_execution_bindings"
            key = {"owner_user_id": "owner-a", "resource_kind": "workspace", "resource_id": "legacy-task"}
            row = conn.execute("SELECT * FROM account_execution_bindings WHERE owner_user_id='owner-a' "
                               "AND resource_kind='workspace' AND resource_id='legacy-task'").fetchone()
        else:
            table = "account_execution_holds"
            key = {"operation_id": held_operation}
            row = conn.execute("SELECT * FROM account_execution_holds WHERE operation_id=?", (held_operation,)).fetchone()
        digest = hashlib.sha256(json.dumps(dict(row), ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":")).encode("utf-8")).hexdigest()
        manifest = {"version": 1, "approval_reference": "synthetic-cutover-approval", "actor_id": "synthetic-admin",
                    "accepted_risk": "first_cutover_unknown_interruption", "targets": targets,
                    "records": [{"profile": "webui", "table": table, "key": key, "sha256": digest}]}
        assert conn.execute("UPDATE app_settings SET value=? WHERE key=?",
                            (json.dumps(manifest, ensure_ascii=False),
                             "deployment.first_cutover_quarantine")).rowcount == 1


def target(kind, resource_id, owner="owner-a"):
    return {"owner_id": owner, "kind": kind, "resource_id": resource_id, "generation": 0}


def rows(database, table):
    with closing(sqlite3.connect(database)) as conn:
        return conn.execute('SELECT * FROM "' + table + '" ORDER BY rowid').fetchall()


def test_pending_failure_learning_never_materializes_or_rotates_quarantined_task(database):
    for task_id in ("legacy-task", "new-task"):
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.execute("INSERT INTO agentic_runtime_runs(user_id,task_id,revision,runtime_version,permission_profile,"
                         "status,run_id,created_at,updated_at) VALUES ('owner-a',?,1,'pi','restricted','failed',?,'before','before')",
                         (task_id, "run-" + task_id))
            conn.execute("INSERT INTO semantic_workspace_events VALUES (?,?, 'owner-a',1,'verify',"
                         "'candidate_verification_failed','synthetic',?,'before')",
                         ("event-" + task_id, task_id,
                          json.dumps({"revision": 1, "run_id": "run-" + task_id, "failure_learning_requested": True})))
    quarantine(database, [target("task", "legacy-task")])
    runs_before = rows(database, "agentic_runtime_runs")
    events_before = rows(database, "semantic_workspace_events")
    found = pending_lesson_failures(database)
    assert [(item["task_id"], item["run_id"]) for item in found] == [("new-task", "run-new-task")]
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("SELECT task_id FROM library_learning_receipts").fetchall() == [("new-task",)]
    assert rows(database, "agentic_runtime_runs") == runs_before
    assert rows(database, "semantic_workspace_events") == events_before


def test_quarantined_template_queue_does_not_mutate_old_rows_or_starve_new_task(database, tmp_path):
    receipts = LearningReceipts(database, tmp_path)
    legacy = []
    for index in range(101):
        task_id = "new-task" if index == 100 else f"legacy-{index:03}"
        if index < 100:
            legacy.append(target("task", task_id))
        receipts.enqueue(owner_id="owner-a", task_id=task_id, revision=1, run_id="run-" + task_id, kind="template_use")
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.execute("INSERT INTO formal_delivery_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         ("delivery-" + task_id, "publication-" + task_id, "run-" + task_id, "owner-a", task_id,
                          1, "a" * 64, "verification", "b" * 64, "c" * 64, "succeeded", "{}", "unused", "before"))
    quarantine(database, legacy)
    before = rows(database, "library_learning_receipts")[:100]
    assert [item["task_id"] for item in pending_template_uses(database)] == ["new-task"]
    assert rows(database, "library_learning_receipts")[:100] == before


@pytest.mark.parametrize("operation", ["enqueue", "skip", "claim_generation", "prepare", "apply"])
def test_learning_mutations_cannot_rewrite_quarantined_task(database, tmp_path, operation):
    from src.deployment_quarantine import QuarantinedExecutionError

    directory = tmp_path / "methods"
    directory.mkdir()
    receipts = LearningReceipts(database, directory)
    key = dict(owner_id="owner-a", task_id="legacy-task", revision=1, run_id="legacy-run", kind="template_new")
    receipts.enqueue(**key)
    if operation == "apply":
        receipts.prepare(**key, slug="legacy-method", before=None, after="保留的未知学习内容")
    quarantine(database, [target("task", "legacy-task")])
    before = rows(database, "library_learning_receipts")
    with pytest.raises(QuarantinedExecutionError):
        if operation == "prepare":
            receipts.prepare(**key, slug="legacy-method", before=None, after="不应被写入")
        else:
            getattr(receipts, operation)(**key)
    assert rows(database, "library_learning_receipts") == before
    assert list(directory.iterdir()) == []
    independent = dict(owner_id="owner-a", task_id="new-task", revision=1, run_id="new-run", kind="template_new")
    receipts.enqueue(**independent)
    assert receipts.claim_generation(**independent) is True


@pytest.mark.parametrize("block_hold_record", [False, True])
def test_account_hold_preserves_quarantined_fact_and_continues_other_owner(database, block_hold_record):
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("INSERT INTO account_execution_bindings VALUES ('owner-b','chat','new-chat',0,'idle',1)")
        conn.executemany("INSERT INTO account_execution_holds VALUES (?,?,1,'synthetic-admin','disabled','pending',0,NULL,1,1)",
                         [("legacy-hold", "owner-a"), ("new-hold", "owner-b")])
    quarantine(database, [] if block_hold_record else [target("workspace", "legacy-task")], held_operation="legacy-hold")
    store = WebUIStore(str(database))
    called = []

    async def reconcile(owner, generation):
        called.append((owner, generation))
        return True

    scheduler = SimpleNamespace(reconcile_account_execution=reconcile)
    manager = AccountExecutionManager(store, SimpleNamespace(), scheduler, SimpleNamespace(), SimpleNamespace())
    before_binding = rows(database, "account_execution_bindings")[0]
    before_hold = rows(database, "account_execution_holds")[0]
    asyncio.run(manager.run_once())
    assert called == [("owner-b", 1)]
    assert rows(database, "account_execution_bindings")[0] == before_binding
    assert rows(database, "account_execution_holds")[0] == before_hold
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("SELECT status FROM account_execution_holds WHERE operation_id='new-hold'").fetchone() == ("completed",)


def test_new_hold_can_govern_new_work_without_confirming_quarantined_binding(database):
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("UPDATE users SET execution_generation=2 WHERE user_id='owner-a'")
        conn.execute("UPDATE account_execution_bindings SET state='cleanup_failed' WHERE resource_id='legacy-task'")
        conn.execute("INSERT INTO account_execution_bindings VALUES ('owner-a','chat','independent-chat',1,'idle',1)")
        conn.execute("INSERT INTO account_execution_holds VALUES ('new-hold','owner-a',2,'synthetic-admin','disabled','pending',0,NULL,2,2)")
    quarantine(database, [target("workspace", "legacy-task")])
    store = WebUIStore(str(database))
    called = []

    async def reconcile(owner, generation):
        called.append((owner, generation))
        return True

    manager = AccountExecutionManager(store, SimpleNamespace(), SimpleNamespace(reconcile_account_execution=reconcile),
                                      SimpleNamespace(), SimpleNamespace())
    with closing(sqlite3.connect(database)) as conn:
        before = conn.execute("SELECT * FROM account_execution_bindings WHERE resource_id='legacy-task'").fetchone()
    asyncio.run(manager.run_once())
    assert called == [("owner-a", 2)]
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("SELECT * FROM account_execution_bindings WHERE resource_id='legacy-task'").fetchone() == before
        assert conn.execute("SELECT state FROM account_execution_bindings WHERE resource_id='independent-chat'").fetchone() == ("paused",)
        assert conn.execute("SELECT status,error_code FROM account_execution_holds WHERE operation_id='new-hold'").fetchone() == ("completed", None)
