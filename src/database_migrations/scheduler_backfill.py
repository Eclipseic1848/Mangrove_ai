"""显式回填升级前调度绑定；不用于运行期修复缺失绑定。"""
from contextlib import ExitStack, closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from filelock import FileLock

from . import DatabaseTarget, _create_backup, _file_sha256, _write_receipt, inspect_database


def _hash(path, expected):
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError("必须提供冻结 SHA-256")
    # 主文件摘要不覆盖 WAL；本命令不替操作者执行 checkpoint 或停止服务。
    wal = Path(str(path) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("存在未冻结 WAL，拒绝回填")
    if not path.is_file() or _file_sha256(path) != expected:
        raise ValueError("冻结文件摘要不匹配")


def _read(path):
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _row_hash(row):
    return hashlib.sha256(json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _legacy(manifest, expected):
    _hash(manifest, expected)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("version") != 1 or data.get("quiescent") is not True or not str(data.get("quiescence_evidence") or "").strip():
        raise ValueError("缺少预升级静默证据")
    captured = datetime.fromisoformat(data.get("captured_at", "").replace("Z", "+00:00"))
    if captured.tzinfo is None:
        raise ValueError("冻结时间必须包含时区")
    snapshots = []
    for kind in ("webui", "scheduler"):
        name = data.get(kind + "_snapshot")
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError("快照必须位于清单同目录")
        path = (manifest.parent / name).resolve()
        _hash(path, data.get(kind + "_sha256"))
        snapshots.append(path)
    with closing(_read(snapshots[0])) as web, closing(_read(snapshots[1])) as scheduler:
        if web.execute("SELECT version_num FROM alembic_version").fetchall()[0][0] != "webui_0011" or any(row[1] == "execution_generation" for row in web.execute("PRAGMA table_info(users)")):
            raise ValueError("仅接受升级前 webui_0011 快照")
        if scheduler.execute("SELECT version_num FROM alembic_version").fetchall()[0][0] != "scheduler_0001":
            raise ValueError("调度快照版本不匹配")
        entries, seen = [], set()
        if not isinstance(data.get("tasks"), list):
            raise ValueError("缺少冻结任务清单")
        for item in data["tasks"]:
            task_id = item.get("task_id")
            if not isinstance(task_id, str) or not task_id or task_id in seen:
                raise ValueError("冻结任务编号缺失或重复")
            seen.add(task_id)
            row = scheduler.execute("SELECT * FROM scheduled_tasks WHERE task_id=?", (task_id,)).fetchone()
            owner = item.get("owner_user_id")
            user = web.execute("SELECT disabled,pending FROM users WHERE user_id=?", (owner,)).fetchone()
            if row is None or user is None or row["owner_user_id"] != owner or _row_hash(row) != item.get("row_sha256"):
                raise ValueError("冻结任务或 Owner 证据不一致")
            if row["status"] in {"active", "paused"}:
                entries.append((dict(row), bool(user["disabled"] or user["pending"])))
    return entries, snapshots


def _plan(web, scheduler, entries):
    actions = []
    for old, unavailable in entries:
        owner, task_id = old["owner_user_id"], old["task_id"]
        user = web.execute("SELECT execution_generation,disabled,pending FROM users WHERE user_id=?", (owner,)).fetchone()
        row = scheduler.execute("SELECT * FROM scheduled_tasks WHERE task_id=?", (task_id,)).fetchone()
        if user is None or row is None:
            raise ValueError("历史计划或账号已缺失")
        paused = unavailable or user["execution_generation"] != 0 or user["disabled"] or user["pending"]
        current = dict(row)
        # 仅允许本回填的暂停写在半提交重试中不同；其它字段变化必须拒绝。
        if paused and current["status"] == "paused":
            current["status"] = old["status"]
        if current != old:
            raise ValueError("历史计划已变化，拒绝回填")
        binding = web.execute("SELECT generation,state FROM account_execution_bindings WHERE owner_user_id=? AND resource_kind='schedule' AND resource_id=?", (owner, task_id)).fetchone()
        if binding is not None:
            # 从不覆盖已经存在的绑定（包括后来显式恢复到新代的绑定）。
            continue
        actions.append((owner, task_id, "paused" if paused else "idle"))
    return actions


def backfill_schedules(*, webui, scheduler, manifest, expected_webui_sha256,
                       expected_scheduler_sha256, expected_manifest_sha256, backup_dir, apply=False):
    webui, scheduler, manifest, backup_dir = (Path(value).resolve() for value in (webui, scheduler, manifest, backup_dir))
    entries, snapshots = _legacy(manifest, expected_manifest_sha256)
    if len({webui, scheduler, *snapshots}) != 4:
        raise ValueError("当前库与历史快照必须独立")
    for profile, path, expected in (("webui", webui, expected_webui_sha256), ("scheduler", scheduler, expected_scheduler_sha256)):
        _hash(path, expected)
        inspect_database(DatabaseTarget(profile, path)).require_current()
    receipt_path = backup_dir / "receipt.json"
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("outcome") == "succeeded" and receipt.get("manifest_sha256") == expected_manifest_sha256 and receipt.get("after_sha256") == [expected_webui_sha256, expected_scheduler_sha256]:
            for name, expected in zip(("webui.before.db", "scheduler.before.db"), receipt["backup_sha256"]):
                _hash(backup_dir / name, expected)
            return {"outcome": "already_applied", "eligible_count": receipt["eligible_count"]}
        raise FileExistsError("已有收据不匹配，拒绝覆盖")
    with closing(_read(webui)) as web, closing(_read(scheduler)) as schedules:
        actions = _plan(web, schedules, entries)
    if not apply:
        return {"outcome": "planned", "eligible_count": len(actions)}
    backup_dir.mkdir(parents=True, exist_ok=True)
    payload = {"kind": "scheduler-backfill-v1", "manifest_sha256": expected_manifest_sha256,
               "source_sha256": [expected_webui_sha256, expected_scheduler_sha256], "eligible_count": len(actions)}
    with ExitStack() as stack:
        for path in (backup_dir / "receipt.json", webui, scheduler):
            stack.enter_context(FileLock(str(path) + ".migration.lock", timeout=0))
        if receipt_path.exists():
            raise FileExistsError("迁移收据已存在")
        web = stack.enter_context(closing(sqlite3.connect(webui)))
        schedules = stack.enter_context(closing(sqlite3.connect(scheduler)))
        web.row_factory = schedules.row_factory = sqlite3.Row
        # 与业务代码一致：WebUI → scheduler 短 SQL 锁，不跨外部运行期调用。
        web.execute("BEGIN IMMEDIATE")
        schedules.execute("BEGIN IMMEDIATE")
        try:
            _hash(webui, expected_webui_sha256)
            _hash(scheduler, expected_scheduler_sha256)
            _legacy(manifest, expected_manifest_sha256)
            actions = _plan(web, schedules, entries)
            payload["backup_sha256"] = []
            for source, name in ((webui, "webui.before.db"), (scheduler, "scheduler.before.db")):
                payload["backup_sha256"].append(_create_backup(source, backup_dir / name))
            for owner, task_id, state in actions:
                web.execute("INSERT INTO account_execution_bindings(owner_user_id,resource_kind,resource_id,generation,state,updated_at) VALUES (?,'schedule',?,0,?,?)", (owner, task_id, state, time.time()))
                if state == "paused":
                    schedules.execute("UPDATE scheduled_tasks SET status='paused' WHERE task_id=? AND owner_user_id=? AND status='active'", (task_id, owner))
            # 先提交暂停，再提交绑定；半提交只会留下无绑定的已暂停行。
            schedules.commit()
            web.commit()
            payload.update(outcome="succeeded", eligible_count=len(actions), after_sha256=[_file_sha256(webui), _file_sha256(scheduler)])
        except Exception as exc:
            schedules.rollback()
            web.rollback()
            payload.update(outcome="failed", error_type=type(exc).__name__)
            _write_receipt(receipt_path, payload)
            raise
        _write_receipt(receipt_path, payload)
    return {"outcome": "succeeded", "eligible_count": len(actions)}
