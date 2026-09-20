"""学习回执使用临时库和临时文件，不访问真实服务。"""
import sqlite3
from contextlib import closing

import pytest

from src.memory.learning_receipts import LearningReceipts
from tests.database_migration_helpers import migrated_webui_database


def test_three_frozen_lessons_have_independent_replayable_effects(tmp_path):
    database = migrated_webui_database(tmp_path / "webui.db")
    root = tmp_path / "lessons"
    root.mkdir()
    receipts = LearningReceipts(database, root)
    for index in range(1, 4):
        key = dict(owner_id="owner-a", task_id="task-a", revision=1, run_id="run-a", kind=f"lesson_use_{index}")
        receipts.enqueue(**key)
        receipts.prepare(**key, slug=f"lesson-{index}", before=None, after="有效次数：1")
        assert receipts.apply(**key) == "applied"
        assert LearningReceipts(database, root).apply(**key) == "applied"
    assert len(list(root.glob("*.md"))) == 3


def test_generation_claim_is_exclusive_and_unknown_is_not_automatically_retried(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    database = migrated_webui_database(tmp_path / "webui.db")
    receipts = LearningReceipts(database, tmp_path / "methods")
    key = dict(owner_id="owner-a", task_id="task-a", revision=2, run_id="run-a", kind="template_new")
    receipts.enqueue(**key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = list(pool.map(lambda _: receipts.claim_generation(**key), range(2)))
    assert sorted(attempts) == [False, True]
    reopened = LearningReceipts(database, tmp_path / "methods")
    reopened.enqueue(**key)
    assert reopened.claim_generation(**key) is False
    assert reopened.get(**key)["state"] == "generating"
    reopened.prepare(**key, slug="method", before=None, after="草稿建议", generated=True)
    assert reopened.apply(**key) == "applied"
    assert reopened.claim_generation(**key) is False


def test_queued_learning_survives_reopen_before_effect_preparation(tmp_path):
    database = migrated_webui_database(tmp_path / "webui.db")
    root = tmp_path / "templates"
    root.mkdir()
    key = dict(owner_id="owner-a", task_id="task-a", revision=1, run_id="run-a", kind="template_use")
    receipts = LearningReceipts(database, root)
    receipts.enqueue(**key)
    receipts = LearningReceipts(database, root)
    assert receipts.get(**key)["state"] == "queued"
    receipts.prepare(**key, slug="method", before=None, after="使用次数：1")
    assert receipts.apply(**key) == "applied"
    receipts.enqueue(**key)
    assert receipts.get(**key)["state"] == "applied"


def test_pending_scan_does_not_starve_later_owners(tmp_path):
    from src.memory.workspace_learning import pending_template_uses
    database = migrated_webui_database(tmp_path / "webui.db")
    receipts = LearningReceipts(database, tmp_path)
    for index in range(101):
        receipts.enqueue(owner_id=f"owner-{index}", task_id=f"task-{index}", revision=1,
                         run_id=f"run-{index}", kind="template_use")
    with closing(sqlite3.connect(database)) as connection, connection:
        for index in range(101):
            connection.execute(
                "INSERT INTO formal_delivery_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"delivery-{index}", f"publication-{index}", f"run-{index}", f"owner-{index}", f"task-{index}",
                 1, "a" * 64, "verification", "b" * 64, "c" * 64, "succeeded", "{}", "unused", "2026-09-18"),
            )
    first = pending_template_uses(database)
    second = pending_template_uses(database)
    assert len(first) <= 100 and len(second) <= 100
    assert {row["owner_id"] for row in first + second} == {f"owner-{index}" for index in range(101)}


def test_file_effect_replays_after_write_without_duplicate_change(tmp_path, monkeypatch):
    database = migrated_webui_database(tmp_path / "webui.db")
    root = tmp_path / "templates"
    root.mkdir()
    target = root / "method.md"
    target.write_text("原统计", encoding="utf-8")
    receipts = LearningReceipts(database, root)
    key = dict(owner_id="owner-a", task_id="task-a", revision=1, run_id="run-a", kind="template_use")
    receipts.prepare(**key, slug="method", before="原统计", after="使用次数：1")
    # 文件已经替换、回执尚未提交时中断，重放不再覆盖文件。
    from src.memory import learning_receipts
    write = learning_receipts.atomic_write

    def interrupted(path, content):
        write(path, content)
        raise OSError("模拟替换后进程中断")

    with monkeypatch.context() as patch:
        patch.setattr(learning_receipts, "atomic_write", interrupted)
        with pytest.raises(OSError):
            receipts.apply(**key)
    assert receipts.get(**key)["state"] == "pending"
    assert target.read_text(encoding="utf-8") == "使用次数：1"
    assert receipts.apply(**key) == "applied"
    assert receipts.apply(**key) == "applied"
    target.unlink()
    assert receipts.apply(**key) == "applied"
    assert not target.exists()
    assert receipts.get(**{**key, "owner_id": "owner-b"}) is None


@pytest.mark.parametrize("kind", ["template_new", "lesson_new"])
@pytest.mark.parametrize("deleted", [False, True])
def test_interrupted_new_file_recovery_respects_deletion(tmp_path, monkeypatch, kind, deleted):
    from src.memory import learning_receipts
    database = migrated_webui_database(tmp_path / "webui.db")
    root = tmp_path / "library"
    root.mkdir()
    target = root / "method.md"
    receipts = LearningReceipts(database, root)
    key = dict(owner_id="owner-a", task_id="task-a", revision=1, run_id="run-a", kind=kind)
    receipts.prepare(**key, slug="method", before=None, after="本人草稿")
    write = learning_receipts.atomic_write

    def interrupted(path, content):
        write(path, content)
        if path == target:
            raise OSError("文件写入后中断")

    with monkeypatch.context() as patch:
        patch.setattr(learning_receipts, "atomic_write", interrupted)
        with pytest.raises(OSError):
            receipts.apply(**key)
    assert target.exists()
    if deleted:
        target.unlink()
    reopened = LearningReceipts(database, root)
    assert reopened.apply(**key) == ("conflict" if deleted else "applied")
    assert target.exists() is not deleted


def test_file_effect_does_not_overwrite_concurrent_edit(tmp_path):
    database = migrated_webui_database(tmp_path / "webui.db")
    root = tmp_path / "templates"
    root.mkdir()
    target = root / "method.md"
    target.write_text("原统计", encoding="utf-8")
    receipts = LearningReceipts(database, root)
    key = dict(owner_id="owner-a", task_id="task-a", revision=1, run_id="run-a", kind="template_use")
    receipts.prepare(**key, slug="method", before="原统计", after="使用次数：1")
    target.write_text("用户已编辑", encoding="utf-8")
    assert receipts.apply(**key) == "conflict"
    assert target.read_text(encoding="utf-8") == "用户已编辑"
    with pytest.raises(ValueError):
        receipts.prepare(**key, slug="other", before=None, after="不同效果")


@pytest.mark.parametrize("changed", [False, True])
def test_pending_lesson_requires_same_failure_report_at_apply(tmp_path, changed):
    from src.agentic_runtime.models import RuntimeTaskConfig, VerificationReport, VerificationStatus
    from src.agentic_runtime.repository import AgenticRuntimeRepository
    from src.delivery_publishing.models import canonical_hash

    database = migrated_webui_database(tmp_path / "webui.db")
    repository = AgenticRuntimeRepository(database)
    repository.register(RuntimeTaskConfig(user_id="owner-a", task_id="task-a", revision=1, run_id="run-a"))
    failed = VerificationReport(status=VerificationStatus.FAILED, summary="缺少指定字段", evidence_count=1, checks=())
    repository.update("owner-a", "task-a", 1, verification=failed)
    receipts = LearningReceipts(database, tmp_path / "lessons")
    key = dict(owner_id="owner-a", task_id="task-a", revision=1, run_id="run-a", kind="lesson_new")
    receipts.prepare(**key, slug="lesson", before=None, after="失败教训草稿")
    if changed:
        repository.update("owner-a", "task-a", 1, verification=VerificationReport(
            status=VerificationStatus.PASSED, summary="重新核验通过", evidence_count=1, checks=()))
    result = receipts.apply(**key, expected_verification_hash=canonical_hash(failed.model_dump(mode="json")))
    assert result == ("conflict" if changed else "applied")
    assert (tmp_path / "lessons" / "lesson.md").exists() is not changed


def test_upgrade_preserves_old_rows_and_backup_can_restore(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL
    from src import database_migrations as migrations

    database = tmp_path / "old.db"
    engine = create_engine(URL.create("sqlite", database=str(database)))
    with engine.begin() as connection:
        config = migrations._alembic_config(connection)
        config.attributes["backup_sha256"] = "a" * 64
        migrations.command.upgrade(config, "webui_0021")
    engine.dispose()
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("INSERT INTO user_memory(user_id,text,created_at) VALUES ('owner-a','原有偏好','2026-09-18T12:00:00+08:00')")
        before = connection.execute("SELECT * FROM user_memory").fetchall()
    target = migrations.DatabaseTarget("webui", database)
    receipt = migrations.apply_migrations(target, tmp_path / "before.db")
    assert receipt.applied_revisions == ("webui_0022",)
    assert migrations.inspect_database(target).state == "current"
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT * FROM user_memory").fetchall() == before
    migrations.apply_migrations(target, tmp_path / "replay.db")
    import shutil
    restored = tmp_path / "restored.db"
    shutil.copy2(receipt.backup_path, restored)
    verified = migrations.verify_restored_copy(receipt.receipt_path, restored)
    assert verified.integrity_check == "ok"
    with closing(sqlite3.connect(restored)) as connection:
        assert connection.execute("SELECT * FROM user_memory").fetchall() == before
