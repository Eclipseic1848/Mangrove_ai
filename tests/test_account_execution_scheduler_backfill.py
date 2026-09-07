"""调度历史回填仅接受明确的升级前冻结快照。"""
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from src.database_migrations import DatabaseTarget, _alembic_config, apply_migrations
from tests.database_migration_helpers import migrated_profile_database


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def legacy(tmp_path):
    old_web = tmp_path / "legacy-web.db"
    sqlite3.connect(old_web).close()
    shutil.copyfile(old_web, tmp_path / "legacy-empty-backup.db")
    engine = create_engine(URL.create("sqlite", database=str(old_web)))
    with engine.begin() as conn:
        config = _alembic_config(conn)
        config.attributes["backup_sha256"] = digest(tmp_path / "legacy-empty-backup.db")
        command.upgrade(config, "webui_0011")
    engine.dispose()
    with sqlite3.connect(old_web) as conn:
        conn.execute("INSERT INTO users(user_id,username,password_hash,display_name,role,created_at) VALUES ('synthetic-owner','fixture','unused','fixture','user','2026-09-01')")
    old_sched = migrated_profile_database(tmp_path / "legacy-scheduler.db", profile="scheduler")
    with sqlite3.connect(old_sched) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("INSERT INTO scheduled_tasks(task_id,user_input,owner_user_id,trigger_type,next_run_at,status,created_at) VALUES ('old-task','虚构正文','synthetic-owner','once','2999-01-01','active','2026-09-01')")
        row = dict(conn.execute("SELECT * FROM scheduled_tasks").fetchone())
    manifest = tmp_path / "legacy.json"
    data = {"version": 1, "captured_at": "2026-09-05T00:00:00Z", "quiescent": True,
            "quiescence_evidence": "synthetic-maintenance-gate",
            "webui_snapshot": old_web.name, "webui_sha256": digest(old_web),
            "scheduler_snapshot": old_sched.name, "scheduler_sha256": digest(old_sched),
            "tasks": [{"task_id": "old-task", "owner_user_id": "synthetic-owner", "row_sha256": hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()}]}
    manifest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    web, scheduler = tmp_path / "web.db", tmp_path / "scheduler.db"
    shutil.copyfile(old_web, web)
    shutil.copyfile(old_sched, scheduler)
    apply_migrations(DatabaseTarget("webui", web), tmp_path / "web-before.db")
    return web, scheduler, manifest, tmp_path / "backfill"


def run(legacy, *, apply=True):
    from src.database_migrations.scheduler_backfill import backfill_schedules
    web, scheduler, manifest, backup = legacy
    return backfill_schedules(webui=web, scheduler=scheduler, manifest=manifest,
                              expected_webui_sha256=digest(web), expected_scheduler_sha256=digest(scheduler),
                              expected_manifest_sha256=digest(manifest), backup_dir=backup, apply=apply)


def test_verified_legacy_is_preserved_and_replay_is_idempotent(legacy):
    web, scheduler, manifest, backup = legacy
    before = (digest(web), digest(scheduler))
    assert run(legacy, apply=False)["eligible_count"] == 1
    assert (digest(web), digest(scheduler)) == before and not backup.exists()
    assert run(legacy)["outcome"] == "succeeded"
    with sqlite3.connect(web) as conn:
        assert conn.execute("SELECT generation,state FROM account_execution_bindings").fetchone() == (0, "idle")
    with sqlite3.connect(scheduler) as conn:
        assert conn.execute("SELECT status FROM scheduled_tasks").fetchone() == ("active",)
    after = (digest(web), digest(scheduler))
    assert run(legacy)["outcome"] == "already_applied"
    assert (digest(web), digest(scheduler)) == after
    text = (backup / "receipt.json").read_text(encoding="utf-8")
    assert "虚构正文" not in text and "synthetic-owner" not in text


def test_changed_generation_stays_old_and_new_orphan_is_excluded(legacy):
    web, scheduler, manifest, backup = legacy
    with sqlite3.connect(web) as conn:
        conn.execute("UPDATE users SET execution_generation=2")
    with sqlite3.connect(scheduler) as conn:
        conn.execute("INSERT INTO scheduled_tasks(task_id,user_input,owner_user_id,trigger_type,status,created_at) VALUES ('new-orphan','虚构新正文','synthetic-owner','once','active','2026-09-06')")
    run(legacy)
    with sqlite3.connect(web) as conn:
        assert conn.execute("SELECT resource_id,generation,state FROM account_execution_bindings").fetchall() == [("old-task", 0, "paused")]
    with sqlite3.connect(scheduler) as conn:
        assert dict(conn.execute("SELECT task_id,status FROM scheduled_tasks")) == {"old-task": "paused", "new-orphan": "active"}


@pytest.mark.parametrize("damage", ["quiescence", "snapshot", "row", "owner"])
def test_missing_or_changed_evidence_refuses_all_writes(legacy, damage):
    web, scheduler, manifest, backup = legacy
    if damage == "quiescence":
        data = json.loads(manifest.read_text(encoding="utf-8")); data["quiescent"] = False
        manifest.write_text(json.dumps(data), encoding="utf-8")
    elif damage == "snapshot":
        (manifest.parent / "legacy-web.db").write_bytes(b"not a snapshot")
    else:
        with sqlite3.connect(scheduler) as conn:
            column = "user_input" if damage == "row" else "owner_user_id"
            conn.execute(f"UPDATE scheduled_tasks SET {column}='changed'")
    before = (digest(web), digest(scheduler))
    with pytest.raises((ValueError, RuntimeError)):
        run(legacy)
    assert (digest(web), digest(scheduler)) == before


@pytest.mark.parametrize("case", ["source_hash", "old_version", "backup_tamper"])
def test_digest_and_version_boundaries(legacy, case):
    from src.database_migrations.scheduler_backfill import backfill_schedules
    web, scheduler, manifest, backup = legacy
    if case == "source_hash":
        before = digest(web)
        with pytest.raises(ValueError):
            backfill_schedules(webui=web, scheduler=scheduler, manifest=manifest,
                               expected_webui_sha256="0" * 64, expected_scheduler_sha256=digest(scheduler),
                               expected_manifest_sha256=digest(manifest), backup_dir=backup, apply=True)
        assert digest(web) == before and not backup.exists()
    elif case == "old_version":
        data = json.loads(manifest.read_text(encoding="utf-8"))
        old_web = manifest.parent / data["webui_snapshot"]
        apply_migrations(DatabaseTarget("webui", old_web), manifest.parent / "changed-old-backup.db")
        data["webui_sha256"] = digest(old_web)
        manifest.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError, match="0011"):
            run(legacy)
    else:
        run(legacy)
        (backup / "webui.before.db").write_bytes(b"changed")
        with pytest.raises(ValueError):
            run(legacy)


def test_original_manual_pause_is_preserved_with_idle_binding(legacy):
    web, scheduler, manifest, backup = legacy
    data = json.loads(manifest.read_text(encoding="utf-8"))
    old_scheduler = manifest.parent / data["scheduler_snapshot"]
    for path in (old_scheduler, scheduler):
        with sqlite3.connect(path) as conn:
            conn.execute("UPDATE scheduled_tasks SET status='paused'")
    with sqlite3.connect(old_scheduler) as conn:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM scheduled_tasks").fetchone())
    data["scheduler_sha256"] = digest(old_scheduler)
    data["tasks"][0]["row_sha256"] = hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    manifest.write_text(json.dumps(data), encoding="utf-8")
    run(legacy)
    with sqlite3.connect(web) as conn:
        assert conn.execute("SELECT generation,state FROM account_execution_bindings").fetchone() == (0, "idle")
    with sqlite3.connect(scheduler) as conn:
        assert conn.execute("SELECT status FROM scheduled_tasks").fetchone() == ("paused",)


def test_half_commit_retries_only_original_frozen_rows(legacy, monkeypatch):
    from src.database_migrations import scheduler_backfill
    web, scheduler, manifest, backup = legacy
    with sqlite3.connect(web) as conn:
        conn.execute("UPDATE users SET execution_generation=1")
    connect = sqlite3.connect

    class FailWebCommit(sqlite3.Connection):
        def commit(self):
            raise RuntimeError("synthetic-web-commit-failure")

    def interrupted(path, *args, **kwargs):
        if path == web:
            kwargs["factory"] = FailWebCommit
        return connect(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(scheduler_backfill.sqlite3, "connect", interrupted)
        with pytest.raises(RuntimeError, match="synthetic-web-commit"):
            run(legacy)
    with sqlite3.connect(web) as conn:
        assert conn.execute("SELECT count(*) FROM account_execution_bindings").fetchone() == (0,)
    with sqlite3.connect(scheduler) as conn:
        assert conn.execute("SELECT status FROM scheduled_tasks").fetchone() == ("paused",)
    assert json.loads((backup / "receipt.json").read_text(encoding="utf-8"))["outcome"] == "failed"
    run((web, scheduler, manifest, backup.with_name("retry-backup")))
    with sqlite3.connect(web) as conn:
        assert conn.execute("SELECT resource_id,generation,state FROM account_execution_bindings").fetchall() == [("old-task", 0, "paused")]
