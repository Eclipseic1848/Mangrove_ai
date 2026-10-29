"""显式维护窗内单独固定两条旧 Chat；原结果未知，首次隔离保持。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.quarantine_first_cutover import _connect, _database_identity, _maintenance_check, _read_manifest
from src.database_migrations import DatabaseTarget, inspect_database
from src.deployment_quarantine import (
    EMPTY_MANIFEST, load_chat_quarantine, load_quarantine, register_chat_quarantine,
    verify_chat_quarantine_records, verify_quarantine_records,
)


def _verify(connection, scheduler, manifest):
    verify_quarantine_records(connection, load_quarantine(connection), scheduler_connection=scheduler)
    verify_chat_quarantine_records(connection, manifest)
    actor = connection.execute("SELECT role,disabled,pending FROM users WHERE user_id=?", (manifest['actor_id'],)).fetchone()
    if actor is None or tuple(actor) != ('super_admin', 0, 0):
        raise PermissionError('须记录有效超级管理员审批身份')
    previous = load_chat_quarantine(connection)
    if previous not in (EMPTY_MANIFEST, manifest):
        raise ValueError('补充 Chat 隔离已固定，候选冲突')
    return 'empty' if previous == EMPTY_MANIFEST else 'fixed'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'apply'))
    for name in ('database', 'scheduler-database', 'manifest'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--service')
    parser.add_argument('--runtime-root', type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = _read_manifest(args.manifest, args.manifest_sha256)
        database, scheduler = args.database.absolute(), args.scheduler_database.absolute()
        for profile, path in (('webui', database), ('scheduler', scheduler)):
            inspect_database(DatabaseTarget(profile, path)).require_current()
        if args.command == 'plan':
            if args.service is not None or args.runtime_root is not None:
                raise ValueError('plan 不接收维护执行参数')
            with ExitStack() as stack:
                connection = _connect(stack, database, readonly=True)
                scheduler_connection = _connect(stack, scheduler, readonly=True)
                connection.execute('BEGIN')
                scheduler_connection.execute('BEGIN')
                pin_state = _verify(connection, scheduler_connection, manifest)
        else:
            if (sys.platform != 'linux' or not hasattr(os, 'geteuid') or os.geteuid() == 0
                    or args.service != 'mangrove.service' or args.runtime_root is None):
                raise PermissionError('apply 仅允许原 Linux 维护账号及原服务')
            uid, runtime = os.geteuid(), args.runtime_root.absolute()
            if (not runtime.is_dir() or runtime.resolve(strict=True) != runtime
                    or database.parent != scheduler.parent
                    or database.name != 'webui.db' or scheduler.name != 'scheduler.db'):
                raise ValueError('须指定原现役根与同一数据根的精确双库')
            identities = tuple(_database_identity(path, uid) for path in (database, scheduler))
            _maintenance_check(database, scheduler, runtime, uid)
            with ExitStack() as stack:
                connection = _connect(stack, database, readonly=False)
                scheduler_connection = _connect(stack, scheduler, readonly=False)
                try:
                    # 双库锁及提交前复核沿用首次维护工具，不把风险接受写成停止证明。
                    connection.execute('BEGIN IMMEDIATE')
                    scheduler_connection.execute('BEGIN IMMEDIATE')
                    _verify(connection, scheduler_connection, manifest)
                    register_chat_quarantine(connection, manifest)
                    _maintenance_check(database, scheduler, runtime, uid)
                    if tuple(_database_identity(path, uid) for path in (database, scheduler)) != identities:
                        raise ValueError('维护期间原数据库身份变化')
                    connection.commit()
                    pin_state = 'fixed'
                finally:
                    connection.rollback()
                    scheduler_connection.rollback()
        result = {'ok': True, 'command': args.command, 'manifest_sha256': args.manifest_sha256,
                  'targets_count': len(manifest['targets']), 'records_count': len(manifest['records']),
                  'pin_state': pin_state, 'stop_confirmed': False}
    except Exception as error:
        result = {'ok': False, 'error_type': type(error).__name__, 'detail': '补充 Chat 隔离未通过；保留原件与未知结果'}
        print(json.dumps(result, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
