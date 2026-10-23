"""首次切换隔离工具；apply 仅限已批准、服务真实停止的维护窗。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.database_migrations import DatabaseTarget, inspect_database
from src.deployment_quarantine import (
    EMPTY_MANIFEST, MAX_MANIFEST_BYTES, SETTING_KEY,
    load_quarantine, register_quarantine, verify_quarantine_records,
)

CGROUP_ROOT = Path("/sys/fs/cgroup")
_PROPERTIES = ("MainPID", "LoadState", "ActiveState", "SubState", "User",
               "WorkingDirectory", "NeedDaemonReload", "ControlGroup")
_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("隔离清单包含重复字段")
        result[key] = value
    return result


def _file_identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _read_manifest(path, expected_sha256):
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise ValueError("须提供固定清单 SHA256")
    path = path.absolute()
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or path.resolve(strict=True) != path
            or not 0 < before.st_size <= MAX_MANIFEST_BYTES):
        raise ValueError("清单须为有界原文件，不得重定向")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as handle:
        opened = os.fstat(handle.fileno())
        # Windows 的路径与句柄 ctime 可不同；跨接口核原件，各接口仍须前后完整稳定。
        if _file_identity(opened)[:3] != _file_identity(before)[:3]:
            raise ValueError("清单文件身份已变化")
        raw = handle.read(MAX_MANIFEST_BYTES + 1)
        if (_file_identity(os.fstat(handle.fileno())) != _file_identity(opened)
                or _file_identity(path.lstat()) != _file_identity(before)):
            raise ValueError("清单读取期间已变化")
    if len(raw) > MAX_MANIFEST_BYTES or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("清单超限或摘要不匹配")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)


def _service_user_uid(user):
    import pwd
    return int(user) if user.isascii() and user.isdecimal() else pwd.getpwnam(user).pw_uid


def _database_identity(path, uid):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.resolve(strict=True) != path or info.st_uid != uid:
        raise PermissionError("数据库须为原维护账号持有的真实原件")
    return info.st_dev, info.st_ino


def _empty_cgroup(group):
    if group not in {"", "/system.slice/mangrove.service"} or not CGROUP_ROOT.is_dir():
        raise ValueError("原服务 cgroup 不可核对")
    directory = CGROUP_ROOT / "system.slice/mangrove.service"
    if not directory.exists():
        return
    # 子 cgroup 也可能仍持进程，不能只读取父级空列表。
    def unreadable(error):
        raise error
    for root, directories, files in os.walk(directory, followlinks=False, onerror=unreadable):
        current = Path(root)
        if current.is_symlink() or any((current / name).is_symlink() for name in directories):
            raise ValueError("原服务 cgroup 路径不明")
        with (current / "cgroup.procs").open("r", encoding="utf-8") as handle:
            if handle.read(65537):
                raise ValueError("原服务 cgroup 仍有进程或内容不明")


def _maintenance_check(database, scheduler, runtime, uid):
    result = subprocess.run(["/usr/bin/systemctl", "show", "--all", "--property=" + ",".join(_PROPERTIES), "--", "mangrove.service"],
                            capture_output=True, text=True, encoding="utf-8", timeout=10, env=_ENV)
    if result.returncode != 0 or result.stderr:
        raise ValueError("原服务状态不可读取")
    state = _unique_object(line.split("=", 1) for line in result.stdout.splitlines())
    if (set(state) != set(_PROPERTIES) or state["MainPID"] != "0" or state["LoadState"] != "loaded"
            or state["ActiveState"] != "inactive" or state["SubState"] != "dead"
            or state["NeedDaemonReload"] != "no" or not state["User"]
            or _service_user_uid(state["User"]) != uid or state["WorkingDirectory"] != str(runtime)):
        raise ValueError("原服务未停止或维护身份不匹配")
    _empty_cgroup(state["ControlGroup"])
    names = []
    for database_path in (database, scheduler):
        _database_identity(database_path, uid)
        names.append(str(database_path))
        for suffix in ("-wal", "-shm"):
            sidecar = database_path.with_name(database_path.name + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                _database_identity(sidecar, uid)
                names.append(str(sidecar))
    # 现役 lsof 不支持 -D；仅采 PID，任何告警或未知退出均不能当作无人持有。
    result = subprocess.run(["/usr/bin/sudo", "-n", "/usr/bin/lsof", "-nP", "-t", "--", *names],
                            capture_output=True, text=True, encoding="utf-8", timeout=10, env=_ENV)
    if result.stderr or result.returncode not in {0, 1}:
        raise ValueError("数据库持有者不可核对")
    if result.returncode == 1 and result.stdout == "":
        return
    lines = result.stdout.splitlines()
    if (result.returncode != 0 or not lines
            or any(re.fullmatch(r"[1-9][0-9]*", line) is None or int(line) != os.getpid() for line in lines)):
        raise ValueError("仍有其他数据库持有者或输出不明")


def _connect(stack, path, *, readonly):
    connection = stack.enter_context(closing(sqlite3.connect(path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"), uri=True, timeout=1)))
    deadline = time.monotonic() + 30
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    if readonly:
        connection.execute("PRAGMA query_only=ON")
    return connection


def _verify(conn, scheduler, manifest):
    verify_quarantine_records(conn, manifest, scheduler_connection=scheduler)
    actor = conn.execute("SELECT role,disabled,pending FROM users WHERE user_id=?", (manifest["actor_id"],)).fetchone()
    if actor is None or tuple(actor) != ("super_admin", 0, 0):
        raise PermissionError("须记录有效超级管理员审批身份")
    row = conn.execute("SELECT 1 FROM app_settings WHERE key=?", (SETTING_KEY,)).fetchone()
    if row is None:
        return "uninitialized"
    previous = load_quarantine(conn)
    if previous != EMPTY_MANIFEST and previous != manifest:
        raise ValueError("既有固定隔离清单与候选冲突")
    return "empty" if previous == EMPTY_MANIFEST else "fixed"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "apply"))
    for name in ("database", "scheduler-database", "manifest"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--service")
    parser.add_argument("--runtime-root", type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = _read_manifest(args.manifest, args.manifest_sha256)
        database, scheduler = args.database.absolute(), args.scheduler_database.absolute()
        if args.command == "plan":
            if args.service is not None or args.runtime_root is not None:
                raise ValueError("plan 不接收维护执行参数")
            with ExitStack() as stack:
                conn = _connect(stack, database, readonly=True)
                scheduler_conn = _connect(stack, scheduler, readonly=True)
                conn.execute("BEGIN")
                scheduler_conn.execute("BEGIN")
                pin_state = _verify(conn, scheduler_conn, manifest)
        else:
            if (sys.platform != "linux" or not hasattr(os, "geteuid") or os.geteuid() == 0
                    or args.service != "mangrove.service" or args.runtime_root is None):
                raise PermissionError("apply 仅允许原 Linux 维护账号与已批准原单元")
            uid = os.geteuid()
            runtime = args.runtime_root.absolute()
            if (not runtime.is_dir() or runtime.resolve(strict=True) != runtime
                    or database != runtime / "data/webui.db" or scheduler != runtime / "data/scheduler.db"):
                raise ValueError("必须登记原现役根下的精确双库")
            identities = tuple(_database_identity(path, uid) for path in (database, scheduler))
            _maintenance_check(database, scheduler, runtime, uid)
            for profile, path, revision in (("webui", database, "webui_0025"), ("scheduler", scheduler, "scheduler_0006")):
                status = inspect_database(DatabaseTarget(profile, path))
                status.require_current()
                if status.current_revision != revision:
                    raise ValueError("当前 Schema 与登记版本不匹配")
            with ExitStack() as stack:
                conn = _connect(stack, database, readonly=False)
                scheduler_conn = _connect(stack, scheduler, readonly=False)
                try:
                    # 两库保留锁只防并发写；Scheduler 无 DML，最终回滚释放，不声称跨库原子提交。
                    conn.execute("BEGIN IMMEDIATE")
                    scheduler_conn.execute("BEGIN IMMEDIATE")
                    _verify(conn, scheduler_conn, manifest)
                    register_quarantine(conn, manifest, scheduler_connection=scheduler_conn)
                    _maintenance_check(database, scheduler, runtime, uid)
                    if tuple(_database_identity(path, uid) for path in (database, scheduler)) != identities:
                        raise ValueError("维护期间数据库原件身份变化")
                    conn.commit()
                    pin_state = "fixed"
                finally:
                    conn.rollback()
                    scheduler_conn.rollback()
        result = {"ok": True, "command": args.command, "manifest_sha256": args.manifest_sha256,
                  "targets_count": len(manifest["targets"]), "records_count": len(manifest["records"]),
                  "pin_state": pin_state, "stop_confirmed": False, "scope_complete": False}
    except Exception as error:
        result = {"ok": False, "error_type": type(error).__name__, "detail": "首次切换隔离核验未通过；保留原记录与未知结果"}
        print(json.dumps(result, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
