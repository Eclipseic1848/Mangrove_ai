"""仅临时库验证运行封存迁移、原始事实保留和不可变边界。"""
from contextlib import closing
import hashlib
import shutil
import sqlite3

from alembic import command
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from src import database_migrations as migrations


def _old_database(database):
    engine = create_engine(URL.create("sqlite", database=str(database)))
    try:
        with engine.begin() as conn:
            config = migrations._alembic_config(conn)
            config.attributes["backup_sha256"] = "a" * 64
            command.upgrade(config, "webui_0023")
    finally:
        engine.dispose()
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("""INSERT INTO users
            (user_id,username,password_hash,display_name,created_at,role)
            VALUES ('owner','synthetic-owner','unused','合成所有者','now','user')""")
        conn.execute("""INSERT INTO agentic_runtime_runs
            (user_id,task_id,revision,runtime_version,permission_profile,status,run_id,
             failure_json,candidates_json,request_json,created_at,updated_at)
            VALUES ('owner','task',1,'pi','standard','failed','original-run',
                '{ "error_code": "MODEL_OUTCOME_UNKNOWN", "detail": "原始未知" }',
                '[{"filename":"原始候选"}]','{"original":"原始请求"}','before','before')""")
        conn.execute("""INSERT INTO agentic_runtime_events
            (event_id,user_id,task_id,revision,event_type,summary,details_json,created_at)
            VALUES ('original-event','owner','task',1,'runtime.failed','原始未知事件',
                '{ "original": "原始细节" }','before')""")


def _history(conn):
    return {table: conn.execute(f"SELECT * FROM {table}").fetchall()
            for table in ("agentic_runtime_runs", "agentic_runtime_events")}


def _seal(conn, *, owner="owner", task="task", revision=1, actor="operator", key="original-key", replace=False):
    verb = "INSERT OR REPLACE" if replace else "INSERT"
    conn.execute(verb + """ INTO runtime_run_seals
        (owner_id,task_id,revision,run_id,runtime_identity_sha256,snapshot_json,stop_proof_json,
         actor_id,reason,approval_reference,idempotency_key,request_sha256,sealed_at)
        VALUES (?,?,?,'original-run',?,'{"snapshot":"合成快照"}','{"proof":"合成停止证明"}',
                ?,'合成封存原因','synthetic-approval',?,?,'2026-10-20T00:00:00+00:00')""",
        (owner, task, revision, "a" * 64, actor, key, "b" * 64))


def test_seal_migration_preserves_history_replays_and_restores(tmp_path):
    database = tmp_path / "old.db"
    _old_database(database)
    with closing(sqlite3.connect(database)) as conn:
        before = _history(conn)
        failure = conn.execute("SELECT CAST(failure_json AS BLOB) FROM agentic_runtime_runs").fetchone()[0]
        details = conn.execute("SELECT CAST(details_json AS BLOB) FROM agentic_runtime_events").fetchone()[0]
    source = database.read_bytes()
    target = migrations.DatabaseTarget("webui", database)
    assert migrations.inspect_database(target).pending_revisions == ("webui_0024", "webui_0025", "webui_0026")
    receipt = migrations.apply_migrations(target, tmp_path / "backup.db", expected_source_sha256=hashlib.sha256(source).hexdigest())
    assert receipt.applied_revisions == ("webui_0024", "webui_0025", "webui_0026")
    with closing(sqlite3.connect(database)) as conn, conn:
        assert _history(conn) == before
        assert conn.execute("SELECT CAST(failure_json AS BLOB) FROM agentic_runtime_runs").fetchone()[0] == failure
        assert conn.execute("SELECT CAST(details_json AS BLOB) FROM agentic_runtime_events").fetchone()[0] == details
        assert conn.execute("SELECT count(*) FROM runtime_run_seals").fetchone()[0] == 0
        _seal(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM runtime_run_seals")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE agentic_runtime_runs SET failure_json=NULL")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM agentic_runtime_events")
        assert _history(conn) == before
    current = database.read_bytes()
    assert migrations.apply_migrations(target, tmp_path / "replay.db").applied_revisions == ()
    assert database.read_bytes() == current
    restored = tmp_path / "restored.db"
    shutil.copyfile(receipt.backup_path, restored)
    assert migrations.verify_restored_copy(receipt.receipt_path, restored).integrity_check == "ok"
    assert restored.read_bytes() == receipt.backup_path.read_bytes()
    assert migrations.inspect_database(migrations.DatabaseTarget("webui", restored)).current_revision == "webui_0023"
    with closing(sqlite3.connect(restored)) as conn:
        assert _history(conn) == before


@pytest.fixture(scope="module")
def sealed_template(tmp_path_factory):
    root = tmp_path_factory.mktemp("runtime-seal-schema")
    database = root / "sealed.db"
    _old_database(database)
    migrations.apply_migrations(migrations.DatabaseTarget("webui", database), root / "before.db")
    with closing(sqlite3.connect(database)) as conn, conn:
        _seal(conn)
    return database


@pytest.fixture
def sealed_database(tmp_path, sealed_template):
    database = tmp_path / "sealed.db"
    shutil.copyfile(sealed_template, database)
    return database


@pytest.mark.parametrize("operation", ["update", "delete", "replace-primary", "replace-idempotency"])
def test_seal_audit_rejects_update_delete_and_both_replace_conflicts(sealed_database, operation):
    with closing(sqlite3.connect(sealed_database)) as conn, conn:
        original = conn.execute("SELECT * FROM runtime_run_seals").fetchall()
        with pytest.raises(sqlite3.IntegrityError, match="immutable runtime seal"):
            if operation == "update":
                conn.execute("UPDATE runtime_run_seals SET reason='替换原因'")
            elif operation == "delete":
                conn.execute("DELETE FROM runtime_run_seals")
            elif operation == "replace-primary":
                _seal(conn, actor="other-operator", key="other-key", replace=True)
            else:
                _seal(conn, owner="other-owner", task="other-task", replace=True)
        assert conn.execute("SELECT * FROM runtime_run_seals").fetchall() == original


@pytest.mark.parametrize("statement", [
    """INSERT INTO agentic_runtime_runs
        (user_id,task_id,revision,runtime_version,permission_profile,status,created_at,updated_at)
        VALUES ('owner','missing-task',1,'pi','standard','queued','now','now')""",
    "UPDATE agentic_runtime_runs SET failure_json=NULL WHERE task_id='task'",
    "UPDATE agentic_runtime_runs SET user_id='other-owner' WHERE task_id='task'",
    "DELETE FROM agentic_runtime_runs WHERE task_id='task'",
    "INSERT OR REPLACE INTO agentic_runtime_runs SELECT * FROM agentic_runtime_runs WHERE task_id='task'",
    "UPDATE agentic_runtime_runs SET task_id='missing-task' WHERE task_id='unsealed'",
    """INSERT INTO agentic_runtime_events
        (event_id,user_id,task_id,revision,event_type,summary,created_at)
        VALUES ('late-event','owner','task',1,'late','迟到事件','now')""",
    "UPDATE agentic_runtime_events SET summary='修改旧事件' WHERE event_id='original-event'",
    "UPDATE agentic_runtime_events SET user_id='other-owner' WHERE event_id='original-event'",
    "DELETE FROM agentic_runtime_events WHERE event_id='original-event'",
    "UPDATE agentic_runtime_events SET task_id='task' WHERE event_id='unsealed-event'",
    """INSERT OR REPLACE INTO agentic_runtime_events
        (event_id,user_id,task_id,revision,event_type,summary,created_at)
        VALUES ('original-event','other-owner','other-task',2,'replace','覆盖事件唯一键','now')""",
    """INSERT OR REPLACE INTO agentic_runtime_events
        (sequence,event_id,user_id,task_id,revision,event_type,summary,created_at)
        SELECT sequence,'replacement-event','other-owner','other-task',2,'replace','覆盖序列唯一键','now'
        FROM agentic_runtime_events WHERE event_id='original-event'""",
    "UPDATE OR REPLACE agentic_runtime_events SET event_id='original-event' WHERE event_id='unsealed-event'",
    """UPDATE OR REPLACE agentic_runtime_events SET sequence=(
        SELECT sequence FROM agentic_runtime_events WHERE event_id='original-event')
        WHERE event_id='unsealed-event'""",
], ids=["run-insert", "run-update", "run-move-out", "run-delete", "run-replace", "run-move-in",
        "event-insert", "event-update", "event-move-out", "event-delete", "event-move-in",
        "event-replace-id", "event-replace-sequence", "event-update-replace-id", "event-update-replace-sequence"])
def test_sealed_runtime_and_events_reject_late_writes_and_replace_bypasses(sealed_database, statement):
    with closing(sqlite3.connect(sealed_database)) as conn, conn:
        _seal(conn, task="missing-task", key="missing-key")
        conn.execute("""INSERT INTO agentic_runtime_runs
            (user_id,task_id,revision,runtime_version,permission_profile,status,created_at,updated_at)
            VALUES ('owner','unsealed',1,'pi','standard','queued','now','now')""")
        conn.execute("""INSERT INTO agentic_runtime_events
            (event_id,user_id,task_id,revision,event_type,summary,created_at)
            VALUES ('unsealed-event','owner','unsealed',1,'original','未封存事件','now')""")
        before = _history(conn)
        with pytest.raises(sqlite3.IntegrityError, match="sealed runtime history"):
            conn.execute(statement)
        assert _history(conn) == before


@pytest.mark.parametrize("owner,task,revision", [
    ("another-owner", "task", 1), ("owner", "another-task", 1), ("owner", "task", 2),
])
def test_other_owner_task_and_revision_remain_writable(sealed_database, owner, task, revision):
    with closing(sqlite3.connect(sealed_database)) as conn, conn:
        before = _history(conn)
        conn.execute("""INSERT INTO agentic_runtime_runs
            (user_id,task_id,revision,runtime_version,permission_profile,status,created_at,updated_at)
            VALUES (?,?,?,'pi','standard','queued','now','now')""", (owner, task, revision))
        conn.execute("""INSERT INTO agentic_runtime_events
            (event_id,user_id,task_id,revision,event_type,summary,created_at)
            VALUES ('another-event',?,?,?,'original','未封存事件','now')""", (owner, task, revision))
        conn.execute("UPDATE agentic_runtime_runs SET status='failed' WHERE user_id=? AND task_id=? AND revision=?", (owner, task, revision))
        conn.execute("UPDATE agentic_runtime_events SET summary='更新未封存事件' WHERE event_id='another-event'")
        conn.execute("DELETE FROM agentic_runtime_runs WHERE user_id=? AND task_id=? AND revision=?", (owner, task, revision))
        conn.execute("DELETE FROM agentic_runtime_events WHERE event_id='another-event'")
        assert _history(conn) == before


def test_seal_columns_require_complete_identity_and_do_not_cascade(sealed_database):
    with closing(sqlite3.connect(sealed_database)) as conn, conn:
        fields = {row[1]: row[3] for row in conn.execute("PRAGMA table_info(runtime_run_seals)")}
        assert fields == {name: 1 for name in (
            "owner_id", "task_id", "revision", "run_id", "runtime_identity_sha256", "snapshot_json",
            "stop_proof_json", "actor_id", "reason", "approval_reference", "idempotency_key", "request_sha256", "sealed_at",
        )}
        for revision in (0, -1):
            with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
                _seal(conn, task="invalid-revision", revision=revision, key="invalid-key")
        assert conn.execute("PRAGMA foreign_key_list(runtime_run_seals)").fetchall() == []
        original = conn.execute("SELECT * FROM runtime_run_seals").fetchall()
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("DELETE FROM users WHERE user_id='owner'")
        assert conn.execute("SELECT * FROM runtime_run_seals").fetchall() == original


def test_rolled_back_seal_does_not_freeze_unsealed_history(sealed_database):
    with closing(sqlite3.connect(sealed_database)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        _seal(conn, task="not-committed", key="not-committed-key")
        conn.rollback()
        conn.execute("""INSERT INTO agentic_runtime_runs
            (user_id,task_id,revision,runtime_version,permission_profile,status,created_at,updated_at)
            VALUES ('owner','not-committed',1,'pi','standard','queued','now','now')""")
        assert conn.execute("SELECT count(*) FROM runtime_run_seals WHERE task_id='not-committed'").fetchone()[0] == 0
