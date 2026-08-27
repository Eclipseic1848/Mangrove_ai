"""Mangrove 显式数据库迁移命令行入口。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from . import (
    DatabaseTarget,
    apply_migrations,
    inspect_database,
    plan_database,
    verify_restored_copy,
)


def _target(arguments: argparse.Namespace) -> DatabaseTarget:
    return DatabaseTarget(
        profile=str(arguments.profile),
        path=Path(arguments.database),
    )


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _status_payload(arguments: argparse.Namespace) -> dict[str, object]:
    status = inspect_database(_target(arguments))
    return {
        "profile": status.target.profile,
        "database_name": status.target.path.name,
        "state": status.state,
        "current_revision": status.current_revision,
        "target_revision": status.target_revision,
        "pending_revisions": list(status.pending_revisions),
        "gaps": list(status.gaps),
    }


def _plan_payload(arguments: argparse.Namespace) -> dict[str, object]:
    plan = plan_database(_target(arguments))
    return {
        "profile": plan.target.profile,
        "database_name": plan.target.path.name,
        "state": plan.state,
        "source_revision": plan.source_revision,
        "target_revision": plan.target_revision,
        "pending_revisions": list(plan.pending_revisions),
        "revisions": [
            {
                "revision": item.revision,
                "content_sha256": item.content_sha256,
                "requires_copy_validation": item.requires_copy_validation,
                "operations": list(item.operations),
            }
            for item in plan.revisions
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mangrove 显式数据库迁移")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "plan"):
        command = subparsers.add_parser(name)
        command.add_argument("--profile", required=True)
        command.add_argument("--database", required=True)
    apply = subparsers.add_parser("apply")
    apply.add_argument("--profile", required=True)
    apply.add_argument("--database", required=True)
    apply.add_argument("--backup", required=True)
    apply.add_argument("--expected-source-sha256")
    verify = subparsers.add_parser("verify-restore")
    verify.add_argument("--receipt", required=True)
    verify.add_argument("--restored", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "status":
        _print_json(_status_payload(arguments))
        return 0
    if arguments.command == "plan":
        _print_json(_plan_payload(arguments))
        return 0
    if arguments.command == "apply":
        receipt = apply_migrations(
            _target(arguments),
            arguments.backup,
            expected_source_sha256=arguments.expected_source_sha256,
        )
        _print_json(
            {
                "outcome": receipt.outcome,
                "profile": receipt.target.profile,
                "database_name": receipt.target.path.name,
                "source_revision": receipt.source_revision,
                "source_database_sha256": receipt.source_database_sha256,
                "target_revision": receipt.target_revision,
                "applied_revisions": list(receipt.applied_revisions),
                "backup_name": receipt.backup_path.name,
                "backup_sha256": receipt.backup_sha256,
                "receipt_name": receipt.receipt_path.name,
            }
        )
        return 0
    verification = verify_restored_copy(arguments.receipt, arguments.restored)
    _print_json(
        {
            "restored_name": verification.restored_path.name,
            "verification_receipt_name": (
                verification.verification_receipt_path.name
            ),
            "backup_sha256": verification.backup_sha256,
            "integrity_check": verification.integrity_check,
            "foreign_key_violations": verification.foreign_key_violations,
            "schema_state": verification.schema_state,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
