"""本机逐 Run 封存工具；生产执行须先批准具体目标、证据及停机窗口。"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.runtime_run_seals import _digest, seal_unknown_run, snapshot_unknown_run
from src.database_migrations import DatabaseTarget, inspect_database
from src.service_identity_window import ServiceIdentityWindow


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "apply"))
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--revision", type=int, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--actor-id")
    parser.add_argument("--approval-reference")
    parser.add_argument("--idempotency-key")
    parser.add_argument("--snapshot-sha256")
    parser.add_argument("--proof", type=Path)
    parser.add_argument("--service")
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--service-window-manifest", type=Path)
    parser.add_argument("--service-window-manifest-sha256")
    parser.add_argument("--service-window-nonce")
    args = parser.parse_args()
    try:
        window_values = (args.service_window_manifest, args.service_window_manifest_sha256, args.service_window_nonce)
        if any(value is not None for value in window_values) and (args.command == "plan" or any(value is None for value in window_values)):
            raise ValueError("维护窗口三个参数须完整且仅用于 apply")
        window_args = {"service_window": ServiceIdentityWindow(*window_values)} if all(value is not None for value in window_values) else {}
        inspect_database(DatabaseTarget(profile="webui", path=args.database)).require_current()
        if args.command == "plan":
            with closing(sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
                conn.execute("PRAGMA query_only=ON")
                conn.execute("BEGIN")
                snapshot = snapshot_unknown_run(conn, args.owner_id, args.task_id, args.revision)
            result = {"snapshot": snapshot, "snapshot_sha256": _digest(snapshot),
                      "runtime_identity_sha256": _digest(snapshot["identity"]), "stop_confirmed": False}
        else:
            if any(value is None for value in (args.run_id, args.actor_id, args.approval_reference, args.idempotency_key,
                    args.snapshot_sha256, args.proof, args.service, args.runtime_root)):
                raise ValueError("缺少具体封存、审批或维护窗口参数")
            result = seal_unknown_run(args.database, owner_id=args.owner_id, task_id=args.task_id, revision=args.revision,
                run_id=args.run_id, actor_id=args.actor_id, approval_reference=args.approval_reference,
                idempotency_key=args.idempotency_key, expected_snapshot_sha256=args.snapshot_sha256,
                proof_path=args.proof, service=args.service, runtime_root=args.runtime_root, **window_args)
    except Exception as error:
        print(json.dumps({"ok": False, "error_type": type(error).__name__, "detail": "封存核验未通过；保留历史未知及原记录"}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
