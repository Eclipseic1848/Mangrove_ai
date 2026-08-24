# -*- coding: utf-8 -*-
"""冻结 G4 真实 Provider 验收清单。"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import uuid
from collections.abc import Callable

import httpx


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.model_connections.broker import ConnectionBroker, ConnectionError
from src.model_connections.storage import ModelConnectionRepository
from src.model_connections.qualification_ledger import (
    QualificationBatchLedger,
    QualificationLedgerError,
)
from src.model_connections.vault import FernetCredentialVault


SCHEMA_VERSION = "g4-provider-manifest-v1"
AUTHORITATIVE_QUALIFICATION_LEDGER_PATH = (
    Path.home() / ".mangrove" / "g4" / "qualification-ledger.sqlite3"
)
QUALIFICATION_LEDGER_ANCHOR_KEY = "g4_qualification_ledger_anchor_v3"


class QualificationError(RuntimeError):
    """G4 验收前置条件不满足。"""


def _require_authoritative_qualification_ledger(path: Path) -> Path:
    expected = AUTHORITATIVE_QUALIFICATION_LEDGER_PATH.resolve()
    if path.resolve() != expected:
        raise QualificationError(
            "正式 Provider 资格必须使用工作目录外的权威台账"
        )
    return expected


def _qualification_ledger_lock_path(ledger_path: Path) -> Path:
    return ledger_path.with_name(f"{ledger_path.name}.lock")


def _database_path_sha256(db_path: Path) -> str:
    normalized = os.path.normcase(str(db_path.resolve()))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _connection_version(secret_id: str, model: str) -> str:
    return hashlib.sha256(
        f"{secret_id}\0{model}".encode("utf-8")
    ).hexdigest()


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _qualification_state_paths(
    *,
    db_path: Path,
    manifest_path: Path,
    action: str,
) -> tuple[Path, Path]:
    manifest = _load_manifest(manifest_path)
    identity = _canonical_sha256(
        {
            "action": action,
            "db_path": str(db_path.resolve()),
            "manifest_sha256": manifest["manifest_sha256"],
        }
    )
    state_dir = db_path.resolve().parent / ".g4-run-state"
    return (
        state_dir / f"{action}-{identity}.lock",
        state_dir / f"{action}-{identity}.attempts.json",
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_identity() -> dict[str, object]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise QualificationError("无法读取 Git 运行身份") from exc
    return {"git_commit": commit, "git_dirty": dirty}


def _provider_runtime_compatibility(
    *,
    provider_evidence_commit: str,
    current_commit: str,
) -> dict[str, object]:
    """确认旧 Provider 报告覆盖的关键执行代码未发生变化。"""

    protected_paths = [
        "src/model_connections",
        "src/agentic_runtime",
        "src/api/routes/model_relay.py",
        "src/connectors/http_security.py",
    ]
    allowed_script_functions = {
        "_database_backups_with_provider_secrets",
        "_verify_database_backups_will_be_erased",
        "_file_contains_material",
        "_verify_no_retained_key_backups",
        "_verify_retained_database_backups",
        "_run_synthetic_vault_rotation_drill",
        "verify_vault_retention_safety",
        "_provider_runtime_compatibility",
        "assess_g4_evidence",
        "_parser",
        "main",
    }

    def run_git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    provider_revision = run_git("rev-parse", "--verify", f"{provider_evidence_commit}^{{commit}}")
    current_revision = run_git("rev-parse", "--verify", f"{current_commit}^{{commit}}")
    if provider_revision.returncode or current_revision.returncode:
        return {"compatible": False, "reason": "git_commit_unavailable"}
    provider_commit = provider_revision.stdout.strip()
    resolved_current_commit = current_revision.stdout.strip()
    ancestry = run_git("merge-base", "--is-ancestor", provider_commit, resolved_current_commit)
    if ancestry.returncode != 0:
        return {
            "compatible": False,
            "reason": "provider_commit_not_ancestor",
            "provider_evidence_commit": provider_commit,
            "current_commit": resolved_current_commit,
        }
    changed = run_git(
        "diff",
        "--name-only",
        f"{provider_commit}..{resolved_current_commit}",
        "--",
        *protected_paths,
    )
    if changed.returncode:
        return {
            "compatible": False,
            "reason": "git_diff_failed",
            "provider_evidence_commit": provider_commit,
            "current_commit": resolved_current_commit,
        }
    changed_paths = sorted(line for line in changed.stdout.splitlines() if line)

    ast = __import__("ast")

    def provider_runner_module_digest(revision: str) -> str | None:
        source = run_git("show", f"{revision}:scripts/verify_g4_provider_safety.py")
        if source.returncode:
            return None
        try:
            module = ast.parse(source.stdout)
        except SyntaxError:
            return None
        protected_body = [
            node
            for node in module.body
            if not (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and (
                    node.name in allowed_script_functions
                    or node.name.startswith("_retained_")
                )
            )
        ]
        protected_module = ast.Module(body=protected_body, type_ignores=[])
        payload = ast.dump(protected_module, include_attributes=False).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    provider_script_digest = provider_runner_module_digest(provider_commit)
    current_script_digest = provider_runner_module_digest(resolved_current_commit)
    script_compatible = (
        provider_script_digest is not None
        and provider_script_digest == current_script_digest
    )
    compatible = not changed_paths and script_compatible
    return {
        "compatible": compatible,
        "reason": "compatible" if compatible else "provider_runtime_changed",
        "provider_evidence_commit": provider_commit,
        "current_commit": resolved_current_commit,
        "protected_paths_changed": changed_paths,
        "provider_runner_module_compatible": script_compatible,
        "provider_runner_module_digest": current_script_digest,
    }


def _validate_base_url(base_url: str) -> None:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise QualificationError("Provider Base URL 必须是无凭证的 HTTPS 根地址")


def freeze_manifest(
    *,
    db_path: Path,
    presets: list[str],
) -> dict[str, object]:
    """从生产元数据冻结不含 Secret 的 Provider 清单。"""

    requested = sorted({item.strip() for item in presets if item.strip()})
    if not requested:
        raise QualificationError("至少指定一个 Provider Preset")
    if len(requested) != len(presets):
        raise QualificationError("Provider Preset 不能为空或重复")
    if not db_path.is_file():
        raise QualificationError("模型连接数据库不存在")

    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        providers: list[dict[str, str]] = []
        for preset_id in requested:
            rows = connection.execute(
                """
                SELECT
                    c.connection_id,
                    c.preset_id,
                    c.preset_version,
                    c.base_url,
                    c.model,
                    c.api_format,
                    c.secret_id
                FROM model_connections AS c
                JOIN model_connection_models AS m
                  ON m.connection_id = c.connection_id
                 AND m.model_id = c.model
                WHERE c.owner_scope = 'platform_shared'
                  AND c.locality = 'public_external'
                  AND c.status = 'verified'
                  AND c.secret_id IS NOT NULL
                  AND m.status = 'available'
                  AND m.enabled = 1
                  AND c.preset_id = ?
                """,
                (preset_id,),
            ).fetchall()
            if len(rows) != 1:
                raise QualificationError(
                    f"Provider {preset_id} 必须且只能有一条合格的平台共享连接"
                )
            row = rows[0]
            base_url = str(row["base_url"]).rstrip("/")
            _validate_base_url(base_url)
            model = str(row["model"])
            providers.append(
                {
                    "connection_id": str(row["connection_id"]),
                    "connection_version": _connection_version(
                        str(row["secret_id"]),
                        model,
                    ),
                    "preset_id": str(row["preset_id"]),
                    "preset_version": str(row["preset_version"]),
                    "base_url": base_url,
                    "model": model,
                    "api_format": str(row["api_format"]),
                }
            )

    unsigned: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "providers": providers,
    }
    return {
        **unsigned,
        "manifest_sha256": _canonical_sha256(unsigned),
    }


def _require_active_superadmin(*, db_path: Path, actor_user_id: str) -> None:
    """失败关闭地确认资格批次授权人拥有当前超级管理员权限。"""

    if not db_path.is_file():
        raise QualificationError("模型连接数据库不存在")
    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            row = connection.execute(
                """
                SELECT role, disabled, pending
                FROM users
                WHERE user_id = ?
                """,
                (actor_user_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise QualificationError(
            "无法确认资格批次授权人的超级管理员权限"
        ) from exc
    # 外发资格批次会消耗真实 Provider 配额，授权身份缺失或状态异常时必须拒绝。
    if row is None or row[0] != "super_admin" or bool(row[1]) or bool(row[2]):
        raise QualificationError(
            "资格批次授权人必须是启用且已审批的超级管理员"
        )


def _parse_qualification_ledger_anchor(raw_value: object) -> dict[str, object]:
    try:
        anchor = json.loads(str(raw_value))
    except (TypeError, ValueError) as exc:
        raise QualificationError("Provider 资格台账锚点无效") from exc
    if (
        not isinstance(anchor, dict)
        or set(anchor)
        != {
            "schema_version",
            "ledger_id",
            "bootstrap_batch_id",
            "initialized_at",
            "initialized_by",
            "ledger_revision",
            "ledger_state_sha256",
            "database_path_sha256",
        }
        or anchor.get("schema_version") != "g4-qualification-ledger-anchor-v3"
        or any(
            not isinstance(anchor.get(name), str)
            or not str(anchor.get(name)).strip()
            for name in (
                "ledger_id",
                "bootstrap_batch_id",
                "initialized_at",
                "initialized_by",
            )
        )
        or not isinstance(anchor.get("ledger_revision"), int)
        or isinstance(anchor.get("ledger_revision"), bool)
        or int(anchor.get("ledger_revision")) < 0
        or not isinstance(anchor.get("ledger_state_sha256"), str)
        or len(str(anchor.get("ledger_state_sha256"))) != 64
        or any(
            character not in "0123456789abcdef"
            for character in str(anchor.get("ledger_state_sha256"))
        )
        or not isinstance(anchor.get("database_path_sha256"), str)
        or len(str(anchor.get("database_path_sha256"))) != 64
        or any(
            character not in "0123456789abcdef"
            for character in str(anchor.get("database_path_sha256"))
        )
    ):
        raise QualificationError("Provider 资格台账锚点无效")
    return dict(anchor)


def _load_qualification_ledger_anchor(
    *,
    db_path: Path,
) -> dict[str, object] | None:
    if not db_path.is_file():
        raise QualificationError("模型连接数据库不存在")
    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                (QUALIFICATION_LEDGER_ANCHOR_KEY,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise QualificationError("无法读取 Provider 资格台账锚点") from exc
    if row is None:
        return None
    return _parse_qualification_ledger_anchor(row[0])


def _sync_qualification_ledger_anchor(
    *,
    db_path: Path,
    ledger: QualificationBatchLedger,
    bootstrap_batch_id: str | None = None,
    initialized_by: str | None = None,
    allowed_revision_advance: int = 1,
) -> dict[str, object]:
    if allowed_revision_advance not in {1, 2}:
        raise QualificationError("Provider 资格台账锚点推进范围无效")
    receipt = ledger.state_receipt()
    try:
        with closing(sqlite3.connect(db_path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                (QUALIFICATION_LEDGER_ANCHOR_KEY,),
            ).fetchone()
            if existing is None:
                if not all(
                    isinstance(value, str) and bool(value.strip())
                    for value in (bootstrap_batch_id, initialized_by)
                ):
                    raise QualificationError(
                        "首次 Provider 资格台账锚点缺少批次或授权人"
                    )
                anchor: dict[str, object] = {
                    "schema_version": "g4-qualification-ledger-anchor-v3",
                    "ledger_id": receipt["ledger_id"],
                    "bootstrap_batch_id": bootstrap_batch_id,
                    "initialized_at": datetime.now(timezone.utc).isoformat(),
                    "initialized_by": initialized_by,
                    "ledger_revision": receipt["ledger_revision"],
                    "ledger_state_sha256": receipt["ledger_state_sha256"],
                    "database_path_sha256": _database_path_sha256(db_path),
                }
                connection.execute(
                    "INSERT INTO app_settings (key, value) VALUES (?, ?)",
                    (
                        QUALIFICATION_LEDGER_ANCHOR_KEY,
                        json.dumps(anchor, ensure_ascii=False, sort_keys=True),
                    ),
                )
            else:
                current = _parse_qualification_ledger_anchor(existing[0])
                if current["ledger_id"] != receipt["ledger_id"]:
                    raise QualificationError(
                        "Provider 资格台账锚点已绑定其他 Ledger"
                    )
                if current["database_path_sha256"] != _database_path_sha256(
                    db_path
                ):
                    raise QualificationError(
                        "Provider 资格台账锚点绑定的数据库身份不一致"
                    )
                current_revision = int(current["ledger_revision"])
                ledger_revision = int(receipt["ledger_revision"])
                if ledger_revision < current_revision:
                    raise QualificationError(
                        "Provider 资格台账旧快照回滚，拒绝继续"
                    )
                if ledger_revision == current_revision:
                    if (
                        current["ledger_state_sha256"]
                        != receipt["ledger_state_sha256"]
                    ):
                        raise QualificationError(
                            "Provider 资格台账状态与外部锚点不一致"
                        )
                    anchor = current
                else:
                    if (
                        ledger_revision
                        != current_revision + allowed_revision_advance
                    ):
                        raise QualificationError(
                            "Provider 资格台账版本跳跃，拒绝继续"
                        )
                    anchor = {
                        **current,
                        "ledger_revision": ledger_revision,
                        "ledger_state_sha256": receipt[
                            "ledger_state_sha256"
                        ],
                    }
                    updated = connection.execute(
                        """
                        UPDATE app_settings
                        SET value = ?
                        WHERE key = ? AND value = ?
                        """,
                        (
                            json.dumps(
                                anchor,
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                            QUALIFICATION_LEDGER_ANCHOR_KEY,
                            existing[0],
                        ),
                    )
                    if updated.rowcount != 1:
                        raise QualificationError(
                            "Provider 资格台账锚点并发更新失败"
                        )
    except sqlite3.Error as exc:
        raise QualificationError("无法持久化 Provider 资格台账锚点") from exc
    return anchor


def _anchored_qualification_ledger(
    *,
    db_path: Path,
    ledger_path: Path,
) -> QualificationBatchLedger:
    anchor = _load_qualification_ledger_anchor(db_path=db_path)
    if anchor is None:
        raise QualificationError("Provider 资格台账锚点不存在，拒绝继续")
    if not ledger_path.is_file():
        raise QualificationError("权威 Provider 资格台账缺失，拒绝重建")
    try:
        ledger = QualificationBatchLedger(ledger_path)
        receipt = ledger.state_receipt()
    except (QualificationLedgerError, sqlite3.Error, OSError) as exc:
        raise QualificationError("权威 Provider 资格台账不可读取") from exc
    if receipt["ledger_id"] != anchor["ledger_id"]:
        raise QualificationError("Provider 资格台账身份与外部锚点不一致")
    if anchor["database_path_sha256"] != _database_path_sha256(db_path):
        raise QualificationError("Provider 资格台账锚点绑定的数据库身份不一致")
    if int(receipt["ledger_revision"]) < int(anchor["ledger_revision"]):
        raise QualificationError("Provider 资格台账旧快照回滚，拒绝继续")
    if (
        receipt["ledger_revision"] != anchor["ledger_revision"]
        or receipt["ledger_state_sha256"] != anchor["ledger_state_sha256"]
    ):
        raise QualificationError("Provider 资格台账状态与外部锚点不一致")
    return ledger


def _recover_qualification_ledger_anchor_unlocked(
    *,
    db_path: Path,
    ledger_path: Path,
    recovered_by: str,
    recovery_reason: str,
    allow_pre_egress_cancel: bool,
) -> dict[str, object]:
    anchor = _load_qualification_ledger_anchor(db_path=db_path)
    if anchor is None:
        raise QualificationError("Provider 资格台账锚点不存在，拒绝恢复")
    if not ledger_path.is_file():
        raise QualificationError("权威 Provider 资格台账缺失，拒绝恢复")
    try:
        ledger = QualificationBatchLedger(ledger_path)
        receipt = ledger.state_receipt()
        if receipt["ledger_id"] != anchor["ledger_id"]:
            raise QualificationError(
                "Provider 资格台账身份与外部锚点不一致"
            )
        if anchor["database_path_sha256"] != _database_path_sha256(db_path):
            raise QualificationError(
                "Provider 资格台账锚点绑定的数据库身份不一致"
            )
        recovery = ledger.recover_anchor_gap(
            anchor_revision=int(anchor["ledger_revision"]),
            recovered_by=recovered_by,
            recovery_reason=recovery_reason,
            allow_pre_egress_cancel=allow_pre_egress_cancel,
        )
        synchronized = _sync_qualification_ledger_anchor(
            db_path=db_path,
            ledger=ledger,
            allowed_revision_advance=int(
                recovery["anchor_revision_advance"]
            ),
        )
    except (QualificationLedgerError, sqlite3.Error, OSError) as exc:
        raise QualificationError(str(exc)) from exc
    return {
        "schema_version": "g4-qualification-ledger-recovery-v1",
        "ledger_id": synchronized["ledger_id"],
        "ledger_revision": synchronized["ledger_revision"],
        "pre_egress_attempt_cancelled": recovery[
            "pre_egress_attempt_cancelled"
        ],
        "stale_attempt_closed_outcome_unknown": recovery[
            "stale_attempt_closed_outcome_unknown"
        ],
        "recovered_by": recovered_by,
        "recovery_reason": recovery_reason,
    }


def recover_qualification_ledger_anchor(
    *,
    db_path: Path,
    manifest_path: Path,
    ledger_path: Path,
    recovered_by: str,
    recovery_reason: str,
) -> dict[str, object]:
    """在没有活动 Pi 进程时安全前滚一次未完成的锚点同步。"""

    ledger_path = _require_authoritative_qualification_ledger(ledger_path)
    if not all(
        isinstance(value, str) and bool(value.strip())
        for value in (recovered_by, recovery_reason)
    ):
        raise QualificationError("资格台账恢复身份和原因不能为空")
    _require_active_superadmin(
        db_path=db_path,
        actor_user_id=recovered_by,
    )
    verify_frozen_inventory(db_path=db_path, manifest_path=manifest_path)
    lock_path = _qualification_ledger_lock_path(ledger_path)
    with _exclusive_file_lock(
        lock_path,
        "已有 G4 Pi 链路正在执行，不能恢复资格台账",
    ):
        return _recover_qualification_ledger_anchor_unlocked(
            db_path=db_path,
            ledger_path=ledger_path,
            recovered_by=recovered_by,
            recovery_reason=recovery_reason,
            allow_pre_egress_cancel=False,
        )


def create_qualification_batch(
    *,
    db_path: Path,
    manifest_path: Path,
    ledger_path: Path,
    owner_user_id: str,
    relay_base_url: str,
    timeout_seconds: int,
    expected_commit: str,
    authorized_by: str,
    authorization_reason: str,
    idempotency_key: str,
    confirm_initial_batch: bool,
    confirm_new_batch_after_exhausted_history: bool,
    previous_report_paths: tuple[Path, ...],
    git_identity: dict[str, object] | None = None,
) -> dict[str, object]:
    """创建独立于工作树和连接数据库的正式资格批次。"""

    ledger_path = _require_authoritative_qualification_ledger(ledger_path)
    if confirm_initial_batch == confirm_new_batch_after_exhausted_history:
        raise QualificationError("必须且只能确认一种资格批次类型")
    if confirm_initial_batch and previous_report_paths:
        raise QualificationError("首个资格批次不得携带历史报告")
    if (
        confirm_new_batch_after_exhausted_history
        and len(previous_report_paths) != 2
    ):
        raise QualificationError("新资格批次必须登记两份已耗尽的历史报告")
    if not all(
        isinstance(value, str) and bool(value.strip())
        for value in (
            owner_user_id,
            expected_commit,
            authorized_by,
            authorization_reason,
            idempotency_key,
        )
    ):
        raise QualificationError("资格批次身份和授权原因不能为空")
    if not isinstance(relay_base_url, str) or not relay_base_url.strip():
        raise QualificationError("资格批次 Relay 地址不能为空")
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds <= 0
        or timeout_seconds > 7200
    ):
        raise QualificationError("资格批次超时必须在 1 到 7200 秒之间")
    _require_active_superadmin(db_path=db_path, actor_user_id=authorized_by)
    relay_base_url = _validate_pi_relay_base_url(relay_base_url)
    identity = dict(git_identity or _git_identity())
    if identity != {"git_commit": expected_commit, "git_dirty": False}:
        raise QualificationError("资格批次必须绑定预期的干净 Git 提交")
    verify_frozen_inventory(db_path=db_path, manifest_path=manifest_path)
    manifest = _load_manifest(manifest_path)
    previous_evidence: list[dict[str, object]] = []
    seen_report_sha256: set[str] = set()
    seen_attempt_identity_sha256: set[str] = set()
    seen_task_ids: set[str] = set()
    manifest_provider_set = {
        (
            str(provider["connection_id"]),
            str(provider["connection_version"]),
            str(provider["preset_id"]),
            str(provider["model"]),
            str(provider["api_format"]),
        )
        for provider in manifest["providers"]
        if isinstance(provider, dict)
    }
    for report_path in previous_report_paths:
        report = _load_evidence(report_path, "g4-pi-provider-report-v1")
        report_sha256 = _file_sha256(report_path)
        report_providers = report.get("providers")
        report_provider_set = {
            (
                str(provider.get("connection_id")),
                str(provider.get("connection_version")),
                str(provider.get("preset_id")),
                str(provider.get("model")),
                str(provider.get("api_format")),
            )
            for provider in report_providers
            if isinstance(provider, dict)
        } if isinstance(report_providers, list) else set()
        provider_outcomes = [
            str(provider.get("outcome"))
            for provider in (report_providers or [])
            if isinstance(provider, dict)
        ]
        report_task_ids = {
            str(provider.get("task_id"))
            for provider in (report_providers or [])
            if isinstance(provider, dict)
            and str(provider.get("task_id") or "").strip()
        }
        attempt_identity_sha256 = _canonical_sha256(report)
        if (
            report_sha256 in seen_report_sha256
            or attempt_identity_sha256 in seen_attempt_identity_sha256
            or bool(report_task_ids & seen_task_ids)
            or report.get("manifest_sha256") != manifest["manifest_sha256"]
            or report.get("synthetic_egress_only") is not True
            or report.get("pi_provider_chain_passed") is not False
            or report_provider_set != manifest_provider_set
            or len(report_providers or []) != len(manifest_provider_set)
            or len(report_task_ids) != len(manifest_provider_set)
            or not provider_outcomes
            or any(
                outcome not in {"failed", "outcome_unknown"}
                for outcome in provider_outcomes
            )
            or any(
                provider.get("permission_profile") != "standard"
                or provider.get("pi_provider_chain_passed") is not False
                or not str(provider.get("owner_user_id") or "").strip()
                for provider in (report_providers or [])
                if isinstance(provider, dict)
            )
        ):
            raise QualificationError("历史 Pi 报告与当前资格批次不一致")
        seen_report_sha256.add(report_sha256)
        seen_attempt_identity_sha256.add(attempt_identity_sha256)
        seen_task_ids.update(report_task_ids)
        aggregate_outcome = (
            "outcome_unknown"
            if "outcome_unknown" in provider_outcomes
            else "failed"
        )
        previous_evidence.append(
            {
                "sha256": report_sha256,
                "attempt_identity_sha256": attempt_identity_sha256,
                "schema_version": report["schema_version"],
                "manifest_sha256": report["manifest_sha256"],
                "git_commit": report.get("git_commit"),
                "generated_at": report.get("generated_at"),
                "outcome": aggregate_outcome,
                "provider_outcomes": provider_outcomes,
                "task_ids": sorted(report_task_ids),
            }
        )
    try:
        anchor = _load_qualification_ledger_anchor(db_path=db_path)
        if anchor is None:
            if not confirm_new_batch_after_exhausted_history:
                raise QualificationError(
                    "权威台账首次建立必须绑定两份已耗尽的旧报告"
                )
            if ledger_path.exists():
                raise QualificationError(
                    "发现没有外部锚点的资格台账，拒绝自动接管"
                )
            ledger = QualificationBatchLedger(ledger_path)
        else:
            ledger = _anchored_qualification_ledger(
                db_path=db_path,
                ledger_path=ledger_path,
            )
        batch = ledger.create_batch(
            manifest_sha256=str(manifest["manifest_sha256"]),
            providers=[dict(provider) for provider in manifest["providers"]],
            expected_commit=expected_commit,
            owner_user_id=owner_user_id,
            relay_base_url=relay_base_url,
            timeout_seconds=timeout_seconds,
            authorized_by=authorized_by,
            authorization_reason=authorization_reason,
            idempotency_key=idempotency_key,
            batch_kind=(
                "initial" if confirm_initial_batch else "successor"
            ),
            parent_batch_id=None,
            previous_evidence=previous_evidence,
        )
        _sync_qualification_ledger_anchor(
            db_path=db_path,
            ledger=ledger,
            bootstrap_batch_id=(str(batch["batch_id"]) if anchor is None else None),
            initialized_by=(authorized_by if anchor is None else None),
        )
        return batch
    except (QualificationLedgerError, sqlite3.Error, OSError) as exc:
        raise QualificationError(str(exc)) from exc


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _provider_attempt_key(provider: dict[str, object]) -> str:
    return f"{provider['connection_id']}:{provider['connection_version']}"


def _attempt_ledger_callbacks(
    *,
    ledger_path: Path,
    action: str,
    manifest: dict[str, object],
    run_context: dict[str, object],
) -> tuple[
    dict[str, dict[str, object]],
    Callable[[dict[str, object], dict[str, object] | None], None],
    Callable[[dict[str, object], dict[str, object]], None],
]:
    provider_keys = {
        _provider_attempt_key(dict(provider))
        for provider in manifest["providers"]
    }
    if ledger_path.exists():
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QualificationError("G4 Provider 外发台账不可读取，拒绝外发") from exc
        if (
            not isinstance(ledger, dict)
            or ledger.get("schema_version") != "g4-provider-attempt-ledger-v1"
            or ledger.get("action") != action
            or ledger.get("manifest_sha256") != manifest["manifest_sha256"]
            or ledger.get("run_context_sha256")
            != _canonical_sha256(run_context)
            or (
                ledger.get("run_context") is not None
                and ledger.get("run_context") != run_context
            )
            or not isinstance(ledger.get("providers"), dict)
            or not set(ledger["providers"]).issubset(provider_keys)
        ):
            raise QualificationError("G4 Provider 外发台账完整性无效，拒绝外发")
    else:
        ledger = {
            "schema_version": "g4-provider-attempt-ledger-v1",
            "action": action,
            "manifest_sha256": manifest["manifest_sha256"],
            "run_context_sha256": _canonical_sha256(run_context),
            "run_context": dict(run_context),
            "providers": {},
        }

    entries = ledger["providers"]
    prior_checks: dict[str, dict[str, object]] = {}
    for key, entry in entries.items():
        if not isinstance(entry, dict) or entry.get("state") not in {
            "in_progress",
            "passed",
            "outcome_unknown",
            "failed_after_egress",
            "retry_authorized",
        }:
            raise QualificationError("G4 Provider 外发台账状态无效，拒绝外发")
        if entry["state"] == "retry_authorized":
            authorization = entry.get("authorization")
            prior_attempt_context = entry.get("attempt_context")
            if (
                not isinstance(authorization, dict)
                or not isinstance(prior_attempt_context, dict)
                or authorization.get(
                    "user_confirmed_duplicate_request_and_cost"
                ) is not True
                or authorization.get("retry_number") != 1
                or authorization.get("owner_user_id")
                != prior_attempt_context.get("owner_user_id")
                or authorization.get("previous_attempt_context_sha256")
                != _canonical_sha256(prior_attempt_context)
            ):
                raise QualificationError("G4 Provider 重试授权证据无效，拒绝外发")
            continue
        if entry["state"] != "passed":
            # in_progress 可能已到达 Provider；没有幂等回执时必须按未知处理。
            raise QualificationError("存在未决或失败的 Provider 外发记录，拒绝重复外发")
        check = entry.get("check")
        if not isinstance(check, dict):
            raise QualificationError("G4 Provider 外发台账证据无效，拒绝外发")
        prior_checks[key] = dict(check)
    if provider_keys and set(prior_checks) == provider_keys:
        raise QualificationError("该冻结清单的 Provider 已全部执行，拒绝重复外发")

    def persist() -> None:
        ledger["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_json_atomic(ledger_path, ledger)

    def before_provider(
        provider: dict[str, object],
        attempt_context: dict[str, object] | None = None,
    ) -> None:
        key = _provider_attempt_key(provider)
        previous_attempts: list[dict[str, object]] = []
        if key in entries and entries[key].get("state") != "retry_authorized":
            raise QualificationError("Provider 已有外发记录，拒绝重复外发")
        if key in entries:
            authorization = entries[key].get("authorization")
            previous_attempts = list(entries[key].get("previous_attempts") or [])
            if previous_attempts:
                raise QualificationError("该 Provider 的一次恢复重试次数已用完")
            if (
                not isinstance(authorization, dict)
                or not isinstance(attempt_context, dict)
                or attempt_context.get("owner_user_id")
                != authorization.get("owner_user_id")
                or attempt_context.get("owner_user_id")
                != run_context.get("owner_user_id")
                or attempt_context.get("relay_base_url")
                != run_context.get("relay_base_url")
                or not str(attempt_context.get("task_id") or "").strip()
                or attempt_context.get("revision") != 1
                or not str(attempt_context.get("execution_root") or "").strip()
                or not str(attempt_context.get("source_sha256") or "").strip()
            ):
                raise QualificationError("Provider 恢复重试身份无效，拒绝外发")
            previous_attempts.append({
                name: value
                for name, value in entries[key].items()
                if name != "previous_attempts"
            })
        entries[key] = {
            "state": "in_progress",
            "connection_id": provider["connection_id"],
            "connection_version": provider["connection_version"],
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        if attempt_context is not None:
            entries[key]["attempt_context"] = dict(attempt_context)
        if previous_attempts:
            entries[key]["previous_attempts"] = previous_attempts
        persist()

    def after_provider(
        provider: dict[str, object],
        check: dict[str, object],
    ) -> None:
        key = _provider_attempt_key(provider)
        entry = entries.get(key)
        if not isinstance(entry, dict) or entry.get("state") != "in_progress":
            raise QualificationError("Provider 外发台账缺少进行中记录")
        outcome = check.get("outcome")
        entry["state"] = {
            "passed": "passed",
            "outcome_unknown": "outcome_unknown",
        }.get(str(outcome), "failed_after_egress")
        entry["completed_at"] = datetime.now(timezone.utc).isoformat()
        entry["check"] = dict(check)
        persist()

    return prior_checks, before_provider, after_provider


def authorize_qualification_batch_retry(
    *,
    db_path: Path,
    manifest_path: Path,
    ledger_path: Path,
    batch_id: str,
    connection_id: str,
    authorized_by: str,
    authorization_reason: str,
    confirm_duplicate_request_and_cost: bool,
    git_identity: dict[str, object] | None = None,
) -> dict[str, object]:
    """把用户决定的一次恢复重试写入正式资格批次台账。"""

    ledger_path = _require_authoritative_qualification_ledger(ledger_path)
    if not confirm_duplicate_request_and_cost:
        raise QualificationError("未确认重复 Provider 请求和费用风险")
    if not all(
        isinstance(value, str) and bool(value.strip())
        for value in (
            batch_id,
            connection_id,
            authorized_by,
            authorization_reason,
        )
    ):
        raise QualificationError("重试授权身份和原因不能为空")
    _require_active_superadmin(db_path=db_path, actor_user_id=authorized_by)
    verify_frozen_inventory(db_path=db_path, manifest_path=manifest_path)
    manifest = _load_manifest(manifest_path)
    providers = [
        dict(provider)
        for provider in manifest["providers"]
        if provider.get("connection_id") == connection_id
    ]
    if len(providers) != 1:
        raise QualificationError("重试授权连接不属于冻结清单")
    try:
        ledger = _anchored_qualification_ledger(
            db_path=db_path,
            ledger_path=ledger_path,
        )
        authorization = ledger.authorize_retry(
            batch_id=batch_id,
            provider=providers[0],
            manifest_sha256=str(manifest["manifest_sha256"]),
            git_identity=dict(git_identity or _git_identity()),
            authorized_by=authorized_by,
            authorization_reason=authorization_reason,
            user_confirmed_duplicate_request_and_cost=(
                confirm_duplicate_request_and_cost
            ),
        )
        _sync_qualification_ledger_anchor(
            db_path=db_path,
            ledger=ledger,
        )
        return authorization
    except (QualificationLedgerError, sqlite3.Error, OSError) as exc:
        raise QualificationError(str(exc)) from exc


def authorize_ambiguous_retry(
    *,
    db_path: Path,
    manifest_path: Path,
    connection_id: str,
    owner_user_id: str,
    relay_base_url: str,
    timeout_seconds: int,
    expected_commit: str,
    confirm_duplicate_request_and_cost: bool,
    git_identity: dict[str, object] | None = None,
) -> dict[str, object]:
    """由用户明确承担重复请求风险后，只放行一次新的正式尝试。"""

    if not confirm_duplicate_request_and_cost:
        raise QualificationError("未确认重复 Provider 请求和费用风险")
    if timeout_seconds <= 0:
        raise QualificationError("超时必须大于 0 秒")
    if not connection_id.strip() or not owner_user_id.strip():
        raise QualificationError("重试授权身份不能为空")
    manifest = _load_manifest(manifest_path)
    providers = [
        dict(provider)
        for provider in manifest["providers"]
        if provider.get("connection_id") == connection_id
    ]
    if len(providers) != 1:
        raise QualificationError("重试授权连接不属于冻结清单")
    provider = providers[0]
    current_git = dict(git_identity or _git_identity())
    if (
        current_git.get("git_commit") != expected_commit
        or current_git.get("git_dirty") is not False
    ):
        raise QualificationError("重试授权必须绑定预期的干净 Git 提交")
    new_run_context = {
        **current_git,
        "relay_base_url": _validate_relay_base_url(relay_base_url),
        "timeout_seconds": timeout_seconds,
        "owner_user_id": owner_user_id,
        "expected_commit": expected_commit,
    }
    lock_path, ledger_path = _qualification_state_paths(
        db_path=db_path,
        manifest_path=manifest_path,
        action="pi-provider",
    )
    with _exclusive_file_lock(lock_path, "已有相同 G4 Pi 链路正在执行"):
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QualificationError("G4 Provider 外发台账不可读取") from exc
        provider_entries = ledger.get("providers")
        previous_run_context = ledger.get("run_context")
        key = _provider_attempt_key(provider)
        entry = (
            provider_entries.get(key)
            if isinstance(provider_entries, dict)
            else None
        )
        manifest_keys = {
            _provider_attempt_key(dict(item))
            for item in manifest["providers"]
        }
        if (
            ledger.get("schema_version") != "g4-provider-attempt-ledger-v1"
            or ledger.get("action") != "pi-provider"
            or ledger.get("manifest_sha256") != manifest["manifest_sha256"]
            or not isinstance(previous_run_context, dict)
            or ledger.get("run_context_sha256")
            != _canonical_sha256(previous_run_context)
            or not isinstance(provider_entries, dict)
            or not set(provider_entries).issubset(manifest_keys)
            or not isinstance(entry, dict)
            or entry.get("state") not in {"in_progress", "outcome_unknown"}
        ):
            raise QualificationError("没有可由用户决定重试的未决 Pi 外发记录")
        previous_attempt_context = entry.get("attempt_context")
        if (
            not isinstance(previous_attempt_context, dict)
            or previous_run_context.get("owner_user_id") != owner_user_id
            or previous_attempt_context.get("owner_user_id") != owner_user_id
            or previous_attempt_context.get("relay_base_url")
            != previous_run_context.get("relay_base_url")
            or not str(previous_attempt_context.get("task_id") or "").strip()
            or previous_attempt_context.get("revision") != 1
            or not str(
                previous_attempt_context.get("execution_root") or ""
            ).strip()
            or not str(previous_attempt_context.get("source_sha256") or "").strip()
        ):
            raise QualificationError("未决 Pi 外发记录缺少原始身份，拒绝重绑")
        if entry.get("previous_attempts") or entry.get("authorization"):
            raise QualificationError("该 Provider 的一次恢复重试次数已用完")
        authorization = {
            "user_confirmed_duplicate_request_and_cost": True,
            "retry_number": 1,
            "owner_user_id": owner_user_id,
            "previous_state": entry["state"],
            "previous_run_context_sha256": ledger.get("run_context_sha256"),
            "previous_attempt_context_sha256": _canonical_sha256(
                previous_attempt_context
            ),
            "replacement_run_context_sha256": _canonical_sha256(
                new_run_context
            ),
            "authorized_at": datetime.now(timezone.utc).isoformat(),
        }
        report = {
            "schema_version": "g4-provider-ambiguous-retry-authorization-v1",
            "manifest_sha256": manifest["manifest_sha256"],
            "connection_id": connection_id,
            **authorization,
        }
        entry["state"] = "retry_authorized"
        entry["authorization"] = authorization
        ledger["run_context_sha256"] = _canonical_sha256(new_run_context)
        ledger["run_context"] = new_run_context
        ledger["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_json_atomic(ledger_path, ledger)
    return report




def _load_manifest(path: Path) -> dict[str, object]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualificationError("G4 Provider 清单不可读取") from exc
    if not isinstance(manifest, dict):
        raise QualificationError("G4 Provider 清单格式无效")
    if set(manifest) != {
        "schema_version",
        "providers",
        "manifest_sha256",
    }:
        raise QualificationError("G4 Provider 清单字段无效")
    unsigned = {
        "schema_version": manifest["schema_version"],
        "providers": manifest["providers"],
    }
    if (
        manifest["schema_version"] != SCHEMA_VERSION
        or not isinstance(manifest["providers"], list)
        or manifest["manifest_sha256"] != _canonical_sha256(unsigned)
    ):
        raise QualificationError("G4 Provider 清单完整性校验失败")
    return manifest


def verify_frozen_inventory(*, db_path: Path, manifest_path: Path) -> None:
    manifest = _load_manifest(manifest_path)
    providers = manifest["providers"]
    if not all(
        isinstance(item, dict) and isinstance(item.get("preset_id"), str)
        for item in providers
    ):
        raise QualificationError("G4 Provider 清单内容无效")
    current = freeze_manifest(
        db_path=db_path,
        presets=[str(item["preset_id"]) for item in providers],
    )
    if current != manifest:
        # 不输出发生变化的 Secret、Endpoint 或模型，避免错误信息扩大配置暴露面。
        raise QualificationError("Provider 连接在冻结后已变化")


def _relay_request(provider: dict[str, object]) -> tuple[str, dict[str, object]]:
    model = str(provider["model"])
    prompt = (
        "这是 Mangrove G4 合成连通性测试，不含用户数据。"
        "请只回复 G4_SYNTHETIC_OK。"
    )
    if provider["api_format"] == "openai_chat_completions":
        return "chat/completions", {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 32,
            "stream": False,
        }
    if provider["api_format"] == "openai_responses":
        return "responses", {
            "model": model,
            "input": prompt,
            "max_output_tokens": 32,
            "store": False,
        }
    raise QualificationError("G4 清单包含本轮未授权的 Provider 协议")


def _response_text(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    output_text = payload.get("output_text")
    if isinstance(output_text, str):
        return output_text
    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict) and isinstance(
                message.get("content"), str
            ):
                return str(message["content"])
    output = payload.get("output")
    if isinstance(output, list):
        texts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    texts.append(str(part["text"]))
        return "\n".join(texts)
    return ""


def _usage_summary(
    broker: ConnectionBroker,
    owner_user_id: str,
    task_id: str,
) -> tuple[str, dict[str, object] | None]:
    usage = broker.list_usage(
        owner_user_id,
        task_id=task_id,
        revision=1,
    )
    if not usage:
        return "missing", None

    def total_or_none(name: str, *, positive: bool = False) -> int | None:
        values = [item.get(name) for item in usage]
        if any(
            type(value) is not int
            or value < (1 if positive else 0)
            for value in values
        ):
            return None
        return sum(values)

    # Pi 会多轮调用同一 Provider；只要任一轮用量未知，整条链就保持未知。
    totals = {
        "input_tokens": total_or_none("input_tokens"),
        "output_tokens": total_or_none("output_tokens"),
        "total_tokens": total_or_none("total_tokens"),
        "request_count": total_or_none("request_count", positive=True),
    }
    status = (
        "recorded"
        if all(item.get("status") == "recorded" for item in usage)
        and all(value is not None for value in totals.values())
        else "unknown"
    )
    return status, totals


def _broker_for_database(db_path: Path) -> ConnectionBroker:
    key_path = db_path.with_name(f"{db_path.name}.model-connections.key")
    if not key_path.is_file():
        raise QualificationError("模型连接独立主密钥不存在")
    return ConnectionBroker(
        repository=ModelConnectionRepository(str(db_path)),
        vault=FernetCredentialVault.from_key_file(key_path),
    )


@contextmanager
def _exclusive_file_lock(lock_path: Path, busy_message: str):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    locked = False
    try:
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise QualificationError(busy_message) from exc
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


@contextmanager
def _vault_rotation_lock(key_path: Path):
    lock_path = key_path.with_name(f"{key_path.name}.rotation.lock")
    with _exclusive_file_lock(lock_path, "已有密钥轮换正在执行"):
        yield


def prepare_vault_rotation(
    *,
    db_path: Path,
    key_path: Path,
    backend_stopped_check: Callable[[], bool],
) -> int:
    """停服后启用双代际并重加密；旧代际保留到最终阶段。"""

    if not backend_stopped_check():
        raise QualificationError("8088 必须停服后才能准备密钥轮换")
    if not db_path.is_file() or not key_path.is_file():
        raise QualificationError("数据库或模型连接独立主密钥不存在")
    with _vault_rotation_lock(key_path):
        vault = FernetCredentialVault.from_key_file(key_path)
        vault.begin_rotation()
        repository = ModelConnectionRepository(str(db_path))
        count = repository.reencrypt_all_secrets(
            lambda ciphertext: vault.encrypt(vault.decrypt(ciphertext))
        )
        return count


def finalize_vault_rotation(
    *,
    db_path: Path,
    key_path: Path,
    backend_stopped_check: Callable[[], bool],
    key_backup_roots: list[Path],
    database_backup_paths: list[Path],
) -> tuple[
    int,
    list[dict[str, object]],
    list[dict[str, str]],
]:
    """停服后再次重加密并销毁旧代际。"""

    if not backend_stopped_check():
        raise QualificationError("8088 必须停服后才能销毁旧密钥代际")
    if not db_path.is_file() or not key_path.is_file():
        raise QualificationError("数据库或模型连接独立主密钥不存在")
    with _vault_rotation_lock(key_path):
        vault = FernetCredentialVault.from_key_file(key_path)
        if not vault.has_inactive_keys:
            raise QualificationError("密钥轮换尚未进入双代际准备阶段")
        authoritative_root = _authoritative_backup_root(db_path)
        if {item.resolve() for item in key_backup_roots} != {
            authoritative_root
        }:
            raise QualificationError("旧 key 检查范围必须是配置的 data/backups 目录")
        backup_scope = _verify_no_inactive_key_backups(
            vault=vault,
            key_path=key_path,
            roots=key_backup_roots,
        )
        discovered_backups = _database_backups_with_provider_secrets(
            authoritative_root
        )
        if not discovered_backups or {
            item.resolve() for item in database_backup_paths
        } != discovered_backups:
            raise QualificationError("必须完整列出配置目录内含 Provider Secret 的数据库备份")
        database_backup_evidence = _verify_database_backups_will_be_erased(
            backup_paths=database_backup_paths,
            transitional_vault=vault,
        )
        repository = ModelConnectionRepository(str(db_path))
        count = repository.reencrypt_all_secrets(
            lambda ciphertext: vault.encrypt(vault.decrypt(ciphertext))
        )
        vault.retire_inactive_keys()
        return count, backup_scope, database_backup_evidence


def _verify_no_inactive_key_backups(
    *,
    vault: FernetCredentialVault,
    key_path: Path,
    roots: list[Path],
) -> list[dict[str, object]]:
    if not roots:
        raise QualificationError("最终轮换必须显式限定旧 key 备份检查目录")
    live_key = key_path.resolve()
    evidence: list[dict[str, object]] = []
    for root in roots:
        if not root.is_dir():
            raise QualificationError("旧 key 备份检查目录不存在")
        resolved_root = root.resolve()
        file_count = 0
        byte_count = 0
        for candidate in resolved_root.rglob("*"):
            if not candidate.is_file() or candidate.resolve() == live_key:
                continue
            size = candidate.stat().st_size
            if vault.file_contains_inactive_key_material(candidate):
                raise QualificationError("检查目录仍含旧 key/keyring 备份")
            file_count += 1
            byte_count += size
        evidence.append(
            {
                "root_name": resolved_root.name,
                "file_count": file_count,
                "byte_count": byte_count,
            }
        )
    return evidence


def _authoritative_backup_root(db_path: Path) -> Path:
    return (db_path.resolve().parent / "backups").resolve()


def _database_backups_with_provider_secrets(root: Path) -> set[Path]:
    discovered: set[Path] = set()
    for candidate in root.rglob("*"):
        if not candidate.is_file():
            continue
        try:
            with candidate.open("rb") as handle:
                if handle.read(16) != b"SQLite format 3\0":
                    continue
        except OSError as exc:
            raise QualificationError("配置备份目录含不可读取文件") from exc
        try:
            uri = f"{candidate.resolve().as_uri()}?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='model_connection_secrets'"
                ).fetchone()
                if table is None:
                    continue
                has_secret = connection.execute(
                    "SELECT 1 FROM model_connection_secrets "
                    "WHERE ciphertext IS NOT NULL LIMIT 1"
                ).fetchone()
        except sqlite3.Error as exc:
            raise QualificationError("配置备份目录含不可验证的数据库文件") from exc
        if has_secret is not None:
            discovered.add(candidate.resolve())
    return discovered


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        raise QualificationError("停服 PID 必须大于 0")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _backend_stopped_check(*, expected_pid: int, relay_base_url: str) -> bool:
    if _pid_is_running(expected_pid):
        return False
    parsed = urlsplit(_validate_relay_base_url(relay_base_url))
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((str(parsed.hostname), port), timeout=1.0):
            return False
    except OSError:
        return True


def _verify_database_backups_will_be_erased(
    *,
    backup_paths: list[Path],
    transitional_vault: FernetCredentialVault,
) -> list[dict[str, str]]:
    if not backup_paths:
        raise QualificationError("最终轮换必须显式列出数据库备份")
    evidence: list[dict[str, str]] = []
    for backup_path in backup_paths:
        if not backup_path.is_file():
            raise QualificationError("列出的数据库备份不存在")
        uri = f"{backup_path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            rows = connection.execute(
                "SELECT ciphertext FROM model_connection_secrets "
                "WHERE ciphertext IS NOT NULL"
            ).fetchall()
        if not rows:
            raise QualificationError("数据库备份不含可验证的 Provider Secret")
        for row in rows:
            try:
                ciphertext = str(row[0])
                transitional_vault.decrypt(ciphertext)
            except ValueError:
                raise QualificationError("数据库备份不属于当前旧密钥代际")
            if transitional_vault.active_key_can_decrypt(ciphertext):
                raise QualificationError("数据库备份已属于新代际，销毁旧 key 不会使其失效")
        evidence.append(
            {
                "file_name": backup_path.name,
                "sha256": _file_sha256(backup_path),
            }
        )
    return evidence


def _retained_key_materials(key_path: Path) -> tuple[bytes, ...]:
    """只在当前进程内读取密钥材料，用于检查限定备份目录。"""

    raw = key_path.read_bytes().strip()
    if not raw:
        raise QualificationError("模型连接主密钥为空")
    if not raw.startswith(b"{"):
        return (raw,)
    try:
        payload = json.loads(raw.decode("utf-8"))
        active_key_id = str(payload["active_key_id"])
        keys = payload["keys"]
        if not isinstance(keys, dict) or set(keys) != {active_key_id}:
            raise ValueError
        material = str(keys[active_key_id]).encode("ascii")
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise QualificationError("保留密钥检查要求单一有效密钥代际") from exc
    return (material,)


def _file_contains_material(
    path: Path,
    materials: tuple[bytes, ...],
    *,
    chunk_size: int = 1024 * 1024,
) -> bool:
    if chunk_size <= 0:
        raise ValueError("分块大小必须大于 0")
    overlap_size = max(len(item) for item in materials) - 1
    overlap = b""
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            content = overlap + chunk
            if any(material in content for material in materials):
                return True
            overlap = content[-overlap_size:] if overlap_size else b""
    return False


def _verify_no_retained_key_backups(
    *,
    db_path: Path,
    key_path: Path,
    roots: list[Path],
) -> list[dict[str, object]]:
    authoritative_root = _authoritative_backup_root(db_path)
    if {item.resolve() for item in roots} != {authoritative_root}:
        raise QualificationError("保留密钥检查范围必须是配置的 data/backups 目录")
    materials = _retained_key_materials(key_path)
    live_key = key_path.resolve()
    evidence: list[dict[str, object]] = []
    for root in roots:
        if not root.is_dir():
            raise QualificationError("保留密钥检查目录不存在")
        resolved_root = root.resolve()
        file_count = 0
        byte_count = 0
        for candidate in resolved_root.rglob("*"):
            if not candidate.is_file() or candidate.resolve() == live_key:
                continue
            if _file_contains_material(candidate, materials):
                raise QualificationError("配置备份目录仍含当前生产密钥材料")
            file_count += 1
            byte_count += candidate.stat().st_size
        evidence.append(
            {
                "root_name": resolved_root.name,
                "file_count": file_count,
                "byte_count": byte_count,
            }
        )
    return evidence


def _verify_retained_database_backups(
    *,
    db_path: Path,
    vault: FernetCredentialVault,
    wrong_vault: FernetCredentialVault,
) -> list[dict[str, object]]:
    backup_root = _authoritative_backup_root(db_path)
    backups = sorted(_database_backups_with_provider_secrets(backup_root))
    if not backups:
        raise QualificationError("配置备份目录缺少含 Provider Secret 的数据库备份")
    evidence: list[dict[str, object]] = []
    for backup_path in backups:
        uri = f"{backup_path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            rows = connection.execute(
                "SELECT ciphertext FROM model_connection_secrets "
                "WHERE ciphertext IS NOT NULL"
            ).fetchall()
        if not rows:
            raise QualificationError("数据库备份不含可验证的 Provider Secret")
        for row in rows:
            ciphertext = str(row[0])
            try:
                vault.decrypt(ciphertext)
            except ValueError as exc:
                raise QualificationError("数据库备份无法由当前生产密钥恢复") from exc
            if wrong_vault.active_key_can_decrypt(ciphertext):
                raise QualificationError("错误密钥意外解开 Provider Secret")
        evidence.append(
            {
                "file_name": backup_path.name,
                "sha256": _file_sha256(backup_path),
                "secret_count": len(rows),
            }
        )
    return evidence


def _run_synthetic_vault_rotation_drill() -> bool:
    """用一次性数据库和密钥演练轮换，不接触生产密钥或 Provider。"""

    with tempfile.TemporaryDirectory(prefix="mangrove-g4-vault-drill-") as value:
        root = Path(value)
        database = root / "synthetic.db"
        key_path = root / "synthetic.key"
        backup_root = root / "backups"
        backup_root.mkdir()
        repository = ModelConnectionRepository(str(database))
        vault = FernetCredentialVault.from_key_file(key_path)
        repository.create_managed(
            created_by="synthetic-g4-drill",
            display_name="Synthetic",
            base_url="https://provider.invalid",
            model="synthetic-model",
            api_format="openai_chat_completions",
            locality="public_external",
            ciphertext=vault.encrypt("synthetic-secret"),
            key_hint="synthetic",
            verified_at="2026-08-23T00:00:00Z",
            preset_id="synthetic",
            preset_version="synthetic-v1",
        )
        backup = backup_root / "synthetic-before.db"
        with closing(sqlite3.connect(database)) as source, closing(
            sqlite3.connect(backup)
        ) as destination:
            source.backup(destination)
        if prepare_vault_rotation(
            db_path=database,
            key_path=key_path,
            backend_stopped_check=lambda: True,
        ) != 1:
            return False
        finalized, _, _ = finalize_vault_rotation(
            db_path=database,
            key_path=key_path,
            backend_stopped_check=lambda: True,
            key_backup_roots=[backup_root],
            database_backup_paths=[backup],
        )
        if finalized != 1:
            return False
        current_vault = FernetCredentialVault.from_key_file(key_path)
        with closing(sqlite3.connect(database)) as connection:
            live_ciphertext = str(
                connection.execute(
                    "SELECT ciphertext FROM model_connection_secrets"
                ).fetchone()[0]
            )
        with closing(sqlite3.connect(backup)) as connection:
            old_ciphertext = str(
                connection.execute(
                    "SELECT ciphertext FROM model_connection_secrets"
                ).fetchone()[0]
            )
        current_vault.decrypt(live_ciphertext)
        try:
            current_vault.decrypt(old_ciphertext)
        except ValueError:
            return True
        return False


def verify_vault_retention_safety(
    *,
    db_path: Path,
    key_path: Path,
    manifest_path: Path,
    output_path: Path,
    expected_commit: str,
    provider_evidence_commit: str | list[str],
    accepted_by: str,
    acceptance_reason: str,
    confirm_retain_production_key: bool,
    key_backup_roots: list[Path],
    git_identity: dict[str, object] | None = None,
    compatibility_checker: Callable[..., dict[str, object]] | None = None,
) -> dict[str, object]:
    """保留现有生产密钥时生成补偿控制证据，不改写密钥和数据库。"""

    if not confirm_retain_production_key:
        raise QualificationError("必须确认保留现有生产密钥及其剩余风险")
    provider_evidence_commits = (
        [provider_evidence_commit]
        if isinstance(provider_evidence_commit, str)
        else list(provider_evidence_commit)
    )
    if not all(
        isinstance(value, str) and bool(value.strip())
        for value in (accepted_by, acceptance_reason)
    ) or not provider_evidence_commits or not all(
        isinstance(value, str) and bool(value.strip())
        for value in provider_evidence_commits
    ):
        raise QualificationError("保留密钥的授权身份、原因和 Provider 证据提交不能为空")
    if len(set(provider_evidence_commits)) != len(provider_evidence_commits):
        raise QualificationError("Provider 证据提交不得重复")
    if not db_path.is_file() or not key_path.is_file():
        raise QualificationError("数据库或模型连接独立主密钥不存在")
    report_lock = output_path.with_name(f".{output_path.name}.lock")
    with _exclusive_file_lock(report_lock, "G4 保留密钥安全报告正在生成"):
        if output_path.exists():
            raise QualificationError("G4 保留密钥安全报告已存在，拒绝覆盖")
        with _vault_rotation_lock(key_path), closing(
            sqlite3.connect(db_path, timeout=0)
        ) as maintenance_connection:
            try:
                # 保留报告必须绑定一个阻止 Provider 配置并发写入的数据库快照。
                maintenance_connection.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                raise QualificationError("Provider 连接正在变更，拒绝生成保留密钥报告") from exc
            _require_active_superadmin(db_path=db_path, actor_user_id=accepted_by)
            identity = dict(git_identity or _git_identity())
            if identity != {"git_commit": expected_commit, "git_dirty": False}:
                raise QualificationError("保留密钥安全检查必须绑定预期的干净 Git 提交")
            verify_frozen_inventory(db_path=db_path, manifest_path=manifest_path)
            manifest = _load_manifest(manifest_path)
            checker = compatibility_checker or _provider_runtime_compatibility
            compatibilities = [
                checker(
                    provider_evidence_commit=commit,
                    current_commit=expected_commit,
                )
                for commit in provider_evidence_commits
            ]
            if not all(
                isinstance(item, dict) and item.get("compatible") is True
                for item in compatibilities
            ):
                raise QualificationError("既有 Provider 证据与当前运行时代码不兼容")
            normalized_provider_commits = [
                str(item.get("provider_evidence_commit") or commit)
                for item, commit in zip(
                    compatibilities,
                    provider_evidence_commits,
                    strict=True,
                )
            ]
            key_sha256_before = _file_sha256(key_path)
            database_sha256_before = _file_sha256(db_path)
            vault = FernetCredentialVault.from_key_file(key_path)
            if vault.has_inactive_keys:
                raise QualificationError("保留密钥检查不接受未完成的双代际轮换")
            rows = maintenance_connection.execute(
                "SELECT ciphertext FROM model_connection_secrets "
                "WHERE ciphertext IS NOT NULL"
            ).fetchall()
            if not rows:
                raise QualificationError("生产数据库不含可验证的 Provider Secret")
            wrong_vault = FernetCredentialVault.generate()
            for row in rows:
                ciphertext = str(row[0])
                try:
                    vault.decrypt(ciphertext)
                except ValueError as exc:
                    raise QualificationError("当前生产密钥无法解开全部 Provider Secret") from exc
                if wrong_vault.active_key_can_decrypt(ciphertext):
                    raise QualificationError("错误密钥意外解开 Provider Secret")
            key_backup_scope = _verify_no_retained_key_backups(
                db_path=db_path,
                key_path=key_path,
                roots=key_backup_roots,
            )
            backup_evidence = _verify_retained_database_backups(
                db_path=db_path,
                vault=vault,
                wrong_vault=wrong_vault,
            )
            if not _run_synthetic_vault_rotation_drill():
                raise QualificationError("一次性密钥轮换演练失败")
            verify_frozen_inventory(db_path=db_path, manifest_path=manifest_path)
            if (
                _file_sha256(key_path) != key_sha256_before
                or _file_sha256(db_path) != database_sha256_before
            ):
                raise QualificationError("保留密钥安全检查意外改写生产密钥或数据库")
            report: dict[str, object] = {
                "schema_version": "g4-vault-retention-report-v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                **identity,
                "manifest_sha256": manifest["manifest_sha256"],
                "database_path_sha256": _database_path_sha256(db_path),
                "key_sha256": key_sha256_before,
                "production_key_changed": False,
                "live_secret_count": len(rows),
                "live_secrets_decryptable": True,
                "wrong_key_rejected": True,
                "key_backup_scope_verified": True,
                "backup_scope_kind": "configured_data_backups_root",
                "key_backup_scope": key_backup_scope,
                "database_backup_recovery_verified": True,
                "database_backup_evidence": backup_evidence,
                "synthetic_rotation_drill_passed": True,
                "retention_risk_accepted": True,
                "accepted_by": accepted_by,
                "acceptance_reason": acceptance_reason.strip(),
                "provider_runtime_compatibility": (
                    compatibilities[0]
                    if len(compatibilities) == 1
                    else compatibilities
                ),
                "code_identity_stable": (
                    _git_identity() == identity if git_identity is None else True
                ),
            }
            if report["code_identity_stable"] is not True:
                raise QualificationError("保留密钥安全检查期间代码身份发生变化")
            if len(normalized_provider_commits) == 1:
                report["provider_evidence_commit"] = normalized_provider_commits[0]
            else:
                report["provider_evidence_commits"] = normalized_provider_commits
            _write_json_atomic(output_path, report)
            maintenance_connection.rollback()
            return report


def _execute_provider_smoke_unlocked(
    *,
    db_path: Path,
    manifest_path: Path,
    output_path: Path,
    relay_base_url: str,
    timeout_seconds: int,
    relay_post=None,
    broker: ConnectionBroker | None = None,
    prior_checks: dict[str, dict[str, object]] | None = None,
    before_provider: Callable[[dict[str, object]], None] | None = None,
    after_provider: Callable[
        [dict[str, object], dict[str, object]], None
    ] | None = None,
) -> dict[str, object]:
    """经 HTTP Relay 执行 Broker 烟测；该结果不能替代真实 Pi G4 验收。"""

    if output_path.exists():
        raise QualificationError("G4 烟测报告已存在，拒绝覆盖")
    verify_frozen_inventory(db_path=db_path, manifest_path=manifest_path)
    manifest = _load_manifest(manifest_path)
    active_broker = broker or _broker_for_database(db_path)
    post = relay_post or httpx.post
    checks: list[dict[str, object]] = []
    owner_user_id = "g4_provider_qualification"
    for provider_value in manifest["providers"]:
        provider = dict(provider_value)
        prior_check = (prior_checks or {}).get(_provider_attempt_key(provider))
        if prior_check is not None:
            checks.append(dict(prior_check))
            continue
        task_id = f"g4_provider_{uuid.uuid4().hex}"
        run_id = f"g4_run_{uuid.uuid4().hex}"
        check: dict[str, object] = {
            "preset_id": provider["preset_id"],
            "model": provider["model"],
            "task_id": task_id,
            "run_id": run_id,
            "response_status": None,
            "response_marker_ok": False,
            "usage_status": "missing",
            "outcome": "failed",
            "provider_chain_smoke_passed": False,
        }
        grant = None
        attempt_started = False
        try:
            binding = active_broker.freeze_connection(
                owner_user_id,
                str(provider["connection_id"]),
            )
            if binding.connection_version != provider["connection_version"]:
                raise QualificationError("Provider 连接在签发 Grant 前已变化")
            grant = active_broker.issue_grant(
                owner_user_id=owner_user_id,
                connection_id=binding.connection_id,
                connection_version=binding.connection_version,
                task_id=task_id,
                revision=1,
                run_id=run_id,
                purpose="agent_inference",
                model_id=binding.model,
                ttl_seconds=timeout_seconds + 60,
            )
            protocol_path, payload = _relay_request(provider)
            if before_provider is not None:
                before_provider(provider)
            attempt_started = True
            response = post(
                f"{relay_base_url.rstrip('/')}/internal/model-relay/{protocol_path}",
                headers={
                    "Authorization": f"Bearer {grant.token}",
                    "Content-Type": "application/json",
                },
                content=json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8"),
                timeout=timeout_seconds,
            )
            check["response_status"] = int(response.status_code)
            try:
                response_payload = response.json()
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                response_payload = None
            check["response_marker_ok"] = (
                "G4_SYNTHETIC_OK" in _response_text(response_payload)
            )
            usage_status, usage_summary = _usage_summary(
                active_broker,
                owner_user_id,
                task_id,
            )
            check["usage_status"] = usage_status
            if usage_summary is not None:
                check["usage"] = usage_summary
            check["provider_chain_smoke_passed"] = bool(
                check["response_status"] == 200
                and check["response_marker_ok"]
                and check["usage_status"] == "recorded"
            )
            check["outcome"] = (
                "passed" if check["provider_chain_smoke_passed"] else "failed"
            )
        except httpx.TimeoutException:
            # 请求可能已经到达 Provider；没有幂等回执时不能武断标记为未执行。
            check["outcome"] = "outcome_unknown"
            check["error_code"] = "relay_timeout"
        except ConnectionError:
            check["error_code"] = "grant_or_connection_failed"
        except (QualificationError, OSError, httpx.HTTPError):
            check["error_code"] = "relay_failed"
        finally:
            if grant is not None:
                try:
                    active_broker.revoke_grant(
                        grant.grant_id,
                        "g4_probe_complete",
                    )
                except (ConnectionError, OSError, sqlite3.Error):
                    # 撤销失败不能覆盖主错误，也不能让报告冒充成功。
                    check["provider_chain_smoke_passed"] = False
                    check["outcome"] = "failed"
                    check["grant_revoke_status"] = "failed"
                else:
                    check["grant_revoke_status"] = "revoked"
            if check["usage_status"] == "missing" and grant is not None:
                usage_status, usage_summary = _usage_summary(
                    active_broker,
                    owner_user_id,
                    task_id,
                )
                check["usage_status"] = usage_status
                if usage_summary is not None:
                    check["usage"] = usage_summary
            if (
                check["usage_status"] == "unknown"
                and not check["provider_chain_smoke_passed"]
            ):
                check["outcome"] = "outcome_unknown"
                check["error_code"] = "provider_outcome_unknown"
        if attempt_started and after_provider is not None:
            after_provider(provider, check)
        checks.append(check)

    smoke_passed = bool(checks) and all(
        bool(item["provider_chain_smoke_passed"]) for item in checks
    )
    report: dict[str, object] = {
        "schema_version": "g4-provider-smoke-report-v2",
        "manifest_sha256": manifest["manifest_sha256"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "synthetic_egress_only": True,
        "timeout_seconds": timeout_seconds,
        "provider_chain_smoke_passed": smoke_passed,
        "g4_qualified": False,
        "qualification_blockers": [
            "missing_real_pi_task_evidence",
            "missing_transport_safety_evidence",
            "missing_vault_rotation_evidence",
        ],
        "providers": checks,
    }
    _write_json_atomic(output_path, report)
    return report


def execute_qualification(
    *,
    db_path: Path,
    manifest_path: Path,
    output_path: Path,
    relay_base_url: str,
    timeout_seconds: int,
    relay_post=None,
    broker: ConnectionBroker | None = None,
) -> dict[str, object]:
    """串行化同一报告目标，避免重复 Provider 请求与证据覆盖。"""

    lock_path, ledger_path = _qualification_state_paths(
        db_path=db_path,
        manifest_path=manifest_path,
        action="provider-smoke",
    )
    with _exclusive_file_lock(lock_path, "已有相同 G4 烟测正在执行"):
        manifest = _load_manifest(manifest_path)
        prior_checks, before_provider, after_provider = _attempt_ledger_callbacks(
            ledger_path=ledger_path,
            action="provider-smoke",
            manifest=manifest,
            run_context={
                **_git_identity(),
                "relay_base_url": relay_base_url.rstrip("/"),
                "timeout_seconds": timeout_seconds,
                "owner_user_id": "g4_provider_qualification",
            },
        )
        report = _execute_provider_smoke_unlocked(
            db_path=db_path,
            manifest_path=manifest_path,
            output_path=output_path,
            relay_base_url=relay_base_url,
            timeout_seconds=timeout_seconds,
            relay_post=relay_post,
            broker=broker,
            prior_checks=prior_checks,
            before_provider=before_provider,
            after_provider=after_provider,
        )
        return report


async def _execute_pi_provider_chain_unlocked(
    *,
    db_path: Path,
    manifest_path: Path,
    output_path: Path,
    execution_root: Path,
    relay_base_url: str,
    timeout_seconds: int,
    owner_user_id: str,
    expected_commit: str | None = None,
    broker: ConnectionBroker | None = None,
    runtime_factory=None,
    prior_checks: dict[str, dict[str, object]] | None = None,
    before_provider: Callable[
        [dict[str, object], dict[str, object]], None
    ] | None = None,
    after_provider: Callable[
        [dict[str, object], dict[str, object]], None
    ] | None = None,
    qualification_batch: dict[str, object] | None = None,
) -> dict[str, object]:
    """执行真实 Pi→Grant→Relay→Provider→Usage 合成链路。"""

    from src.agentic_runtime.models import (
        PermissionProfile,
        PiRuntimeRequest,
        RuntimeStatus,
        SourceInput,
    )
    from src.agentic_runtime.pi_runtime import PiRuntime, PiRuntimeError

    runtime_class = runtime_factory or PiRuntime

    if output_path.exists():
        raise QualificationError("G4 Pi 报告已存在，拒绝覆盖")
    if not owner_user_id.strip():
        raise QualificationError("G4 合成 Owner 不能为空")
    if timeout_seconds <= 0:
        raise QualificationError("超时必须大于 0 秒")
    relay_base_url = _validate_pi_relay_base_url(relay_base_url)
    identity = _git_identity()
    if expected_commit is not None and (
        identity["git_commit"] != expected_commit or identity["git_dirty"]
    ):
        raise QualificationError("正式 Pi 链路必须绑定预期的干净 Git 提交")
    verify_frozen_inventory(db_path=db_path, manifest_path=manifest_path)
    manifest = _load_manifest(manifest_path)
    active_broker = broker or _broker_for_database(db_path)
    execution_root.mkdir(parents=True, exist_ok=True)
    checks: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="mangrove-g4-source-") as source_dir:
        source_path = Path(source_dir) / "synthetic-provider-check.csv"
        source_path.write_text(
            "item,value\nsynthetic_alpha,1\nsynthetic_beta,2\n",
            encoding="utf-8",
            newline="\n",
        )
        source_sha256 = _file_sha256(source_path)
        for provider_value in manifest["providers"]:
            provider = dict(provider_value)
            prior_check = (prior_checks or {}).get(
                _provider_attempt_key(provider)
            )
            if prior_check is not None:
                checks.append(dict(prior_check))
                continue
            task_id = f"g4_pi_{uuid.uuid4().hex[:16]}"
            check: dict[str, object] = {
                "connection_id": provider["connection_id"],
                "connection_version": provider["connection_version"],
                "preset_id": provider["preset_id"],
                "model": provider["model"],
                "api_format": provider["api_format"],
                "task_id": task_id,
                "owner_user_id": owner_user_id,
                "permission_profile": PermissionProfile.STANDARD.value,
                "outcome": "failed",
                "runtime_status": None,
                "usage_status": "missing",
                "pi_provider_chain_passed": False,
            }
            attempt_started = False
            try:
                binding = active_broker.freeze_connection(
                    owner_user_id,
                    str(provider["connection_id"]),
                )
                if binding.connection_version != provider["connection_version"]:
                    raise QualificationError("Provider 连接在 Pi 启动前已变化")
                request = PiRuntimeRequest(
                    user_id=owner_user_id,
                    task_id=task_id,
                    revision=1,
                    objective_text=(
                        "读取合成 CSV，输出 JSON 数组；每行只保留 item 和 value，"
                        "不得补充其他内容。"
                    ),
                    requested_output_formats=("json",),
                    sources=(
                        SourceInput(
                            upload_id="g4-synthetic-source",
                            original_name=source_path.name,
                            host_path=source_path,
                            sha256=source_sha256,
                            media_type="text/csv",
                        ),
                    ),
                    permission_profile=PermissionProfile.STANDARD,
                    external_api_confirmed=True,
                    model_connection_id=binding.connection_id,
                    model_connection_version=binding.connection_version,
                    model_connection_model=binding.model,
                )
                event_types: list[str] = []

                async def on_event(event) -> None:
                    event_types.append(event.event_type)

                runtime = runtime_class(
                    execution_root=execution_root,
                    timeout_seconds=timeout_seconds,
                    connection_broker=active_broker,
                    relay_base_url=relay_base_url,
                )
                if before_provider is not None:
                    before_provider(
                        provider,
                        {
                            "owner_user_id": owner_user_id,
                            "task_id": task_id,
                            "revision": 1,
                            "relay_base_url": relay_base_url,
                            "execution_root": str(execution_root.resolve()),
                            "source_sha256": source_sha256,
                        },
                    )
                attempt_started = True
                result = await asyncio.wait_for(
                    runtime.start(request, on_event=on_event),
                    timeout=timeout_seconds + 120,
                )
                check["runtime_status"] = result.status.value
                check["run_id"] = result.run_id
                check["event_types"] = event_types
                check["candidate_count"] = len(result.candidates)
                check["candidate_sha256"] = [
                    item.sha256 for item in result.candidates
                ]
                verification = getattr(result, "verification", None)
                check["verification_status"] = (
                    verification.status.value if verification is not None else None
                )
                usage_status, usage_summary = _usage_summary(
                    active_broker,
                    owner_user_id,
                    task_id,
                )
                check["usage_status"] = usage_status
                if usage_summary is not None:
                    check["usage"] = usage_summary
                check["pi_provider_chain_passed"] = bool(
                    result.status is RuntimeStatus.CANDIDATE_READY
                    and len(result.candidates) == 1
                    and check["verification_status"] == "passed"
                    and check["usage_status"] == "recorded"
                )
                check["outcome"] = (
                    "passed" if check["pi_provider_chain_passed"] else "failed"
                )
            except (TimeoutError, asyncio.TimeoutError):
                check["outcome"] = "outcome_unknown"
                check["error_code"] = "pi_timeout"
            except (QualificationError, ConnectionError, PiRuntimeError, OSError):
                check["error_code"] = "pi_chain_failed"
            except Exception as exc:
                # 运行器的未知异常也可能发生在请求发出之后；必须保留未知结果并收口台账，
                # 但不能把可能含路径、Token 或 Provider 正文的异常消息写入正式证据。
                check["outcome"] = "outcome_unknown"
                check["error_code"] = "pi_internal_error"
                check["error_type"] = type(exc).__name__
            if check["usage_status"] == "missing":
                usage_status, usage_summary = _usage_summary(
                    active_broker,
                    owner_user_id,
                    task_id,
                )
                check["usage_status"] = usage_status
                if usage_summary is not None:
                    check["usage"] = usage_summary
            if (
                check["usage_status"] == "unknown"
                and not check["pi_provider_chain_passed"]
            ):
                check["outcome"] = "outcome_unknown"
                check["error_code"] = "provider_outcome_unknown"
            if attempt_started and after_provider is not None:
                after_provider(provider, check)
            checks.append(check)

    identity_after = _git_identity()
    code_identity_stable = identity_after == identity
    pi_passed = code_identity_stable and bool(checks) and all(
        bool(item["pi_provider_chain_passed"]) for item in checks
    )
    blockers = [
        "missing_transport_safety_evidence",
        "missing_vault_rotation_evidence",
    ]
    if not code_identity_stable:
        blockers.append("code_identity_changed_during_run")
    report: dict[str, object] = {
        "schema_version": "g4-pi-provider-report-v1",
        "manifest_sha256": manifest["manifest_sha256"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **identity,
        "synthetic_egress_only": True,
        "source_sha256": source_sha256,
        "timeout_seconds": timeout_seconds,
        "code_identity_stable": code_identity_stable,
        "pi_provider_chain_passed": pi_passed,
        "g4_qualified": False,
        "qualification_blockers": blockers,
        "providers": checks,
    }
    if qualification_batch is not None:
        report["qualification_ledger_id"] = qualification_batch["ledger_id"]
        report["qualification_batch_id"] = qualification_batch["batch_id"]
    _write_json_atomic(output_path, report)
    return report


async def execute_pi_provider_chain(
    *,
    db_path: Path,
    manifest_path: Path,
    output_path: Path,
    execution_root: Path,
    relay_base_url: str,
    timeout_seconds: int,
    owner_user_id: str,
    expected_commit: str | None = None,
    broker: ConnectionBroker | None = None,
    runtime_factory=None,
    qualification_ledger_path: Path | None = None,
    qualification_batch_id: str | None = None,
) -> dict[str, object]:
    """串行化同一正式 Pi 证据目标，避免重复外发。"""

    relay_base_url = _validate_pi_relay_base_url(relay_base_url)
    if (qualification_ledger_path is None) != (qualification_batch_id is None):
        raise QualificationError("资格台账路径和批次 ID 必须同时提供")
    if qualification_ledger_path is None:
        raise QualificationError("正式 Pi 外发必须绑定持久资格批次")
    qualification_ledger_path = _require_authoritative_qualification_ledger(
        qualification_ledger_path
    )
    lock_path = _qualification_ledger_lock_path(
        qualification_ledger_path
    )
    with _exclusive_file_lock(lock_path, "已有相同 G4 Pi 链路正在执行"):
        manifest = _load_manifest(manifest_path)
        run_context = {
            **_git_identity(),
            "relay_base_url": relay_base_url,
            "timeout_seconds": timeout_seconds,
            "owner_user_id": owner_user_id,
            "expected_commit": expected_commit,
        }
        try:
            durable_ledger = _anchored_qualification_ledger(
                db_path=db_path,
                ledger_path=qualification_ledger_path,
            )
            qualification_batch, prior_checks = durable_ledger.prepare_run(
                batch_id=str(qualification_batch_id),
                manifest_sha256=str(manifest["manifest_sha256"]),
                providers=[
                    dict(provider) for provider in manifest["providers"]
                ],
                run_context=run_context,
            )

            def before_provider(
                provider: dict[str, object],
                attempt_context: dict[str, object],
            ) -> None:
                sanitized_context = {
                    name: value
                    for name, value in attempt_context.items()
                    if name != "execution_root"
                }
                sanitized_context["execution_root_sha256"] = hashlib.sha256(
                    str(attempt_context["execution_root"]).encode("utf-8")
                ).hexdigest()
                durable_ledger.begin_attempt(
                    batch_id=str(qualification_batch_id),
                    provider=provider,
                    attempt_context=sanitized_context,
                )
                # 外发前先把单调版本写入连接数据库；任一侧失败都不能继续请求 Provider。
                try:
                    _sync_qualification_ledger_anchor(
                        db_path=db_path,
                        ledger=durable_ledger,
                    )
                except QualificationError as sync_error:
                    try:
                        _recover_qualification_ledger_anchor_unlocked(
                            db_path=db_path,
                            ledger_path=qualification_ledger_path,
                            recovered_by="system",
                            recovery_reason=(
                                "外发前锚点同步失败，撤回未发送的 Attempt"
                            ),
                            allow_pre_egress_cancel=True,
                        )
                    except QualificationError as recovery_error:
                        raise QualificationError(
                            "外发前锚点同步失败且自动收口未完成；未发送 Provider 请求"
                        ) from recovery_error
                    raise QualificationError(
                        "外发前锚点同步失败，未发送 Provider 请求且 Attempt 已撤回"
                    ) from sync_error

            def after_provider(
                provider: dict[str, object],
                check: dict[str, object],
            ) -> None:
                durable_ledger.finish_attempt(
                    batch_id=str(qualification_batch_id),
                    provider=provider,
                    check=check,
                )
                _sync_qualification_ledger_anchor(
                    db_path=db_path,
                    ledger=durable_ledger,
                )
        except (QualificationLedgerError, sqlite3.Error, OSError) as exc:
            raise QualificationError(str(exc)) from exc
        report = await _execute_pi_provider_chain_unlocked(
            db_path=db_path,
            manifest_path=manifest_path,
            output_path=output_path,
            execution_root=execution_root,
            relay_base_url=relay_base_url,
            timeout_seconds=timeout_seconds,
            owner_user_id=owner_user_id,
            expected_commit=expected_commit,
            broker=broker,
            runtime_factory=runtime_factory,
            prior_checks=prior_checks,
            before_provider=before_provider,
            after_provider=after_provider,
            qualification_batch=qualification_batch,
        )
        return report


_TRANSPORT_SAFETY_TESTS = (
    "tests/test_g4_provider_safety_cli.py::test_provider_relay_pins_validated_ip_and_preserves_tls_identity",
    "tests/test_g4_provider_safety_cli.py::test_provider_relay_does_not_follow_redirect_or_repeat_dns",
    "tests/test_g4_provider_safety_cli.py::test_pinned_transport_enforces_original_tls_identity_and_lifetime[provider.test-False-True]",
    "tests/test_g4_provider_safety_cli.py::test_pinned_transport_enforces_original_tls_identity_and_lifetime[wrong-host.test-False-False]",
    "tests/test_g4_provider_safety_cli.py::test_pinned_transport_enforces_original_tls_identity_and_lifetime[provider.test-True-False]",
    "tests/test_g4_provider_safety_cli.py::test_keyring_atomic_replace_failure_removes_plaintext_temporary_file",
)


def execute_transport_safety(
    *,
    output_path: Path,
    expected_commit: str,
    git_identity: dict[str, object] | None = None,
    test_runner=None,
) -> dict[str, object]:
    """在干净提交上执行精确的传输/密钥临时文件安全矩阵。"""

    if output_path.exists():
        raise QualificationError("G4 传输安全报告已存在，拒绝覆盖")
    identity = git_identity or _git_identity()
    if identity.get("git_commit") != expected_commit or identity.get("git_dirty"):
        raise QualificationError("传输安全矩阵必须绑定预期的干净 Git 提交")
    command = [
        sys.executable,
        "-m",
        "pytest",
        *_TRANSPORT_SAFETY_TESTS,
        "-q",
    ]
    runner = test_runner or (
        lambda value: subprocess.run(
            value,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    )
    completed = runner(command)
    identity_after = git_identity or _git_identity()
    code_identity_stable = identity_after == identity
    report: dict[str, object] = {
        "schema_version": "g4-transport-safety-report-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **identity,
        "test_count": len(_TRANSPORT_SAFETY_TESTS),
        "test_ids": list(_TRANSPORT_SAFETY_TESTS),
        "pytest_returncode": int(completed.returncode),
        "code_identity_stable": code_identity_stable,
        "transport_safety_passed": (
            completed.returncode == 0 and code_identity_stable
        ),
    }
    _write_json_atomic(output_path, report)
    return report


def _load_evidence(path: Path, schema_version: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualificationError("G4 证据不可读取") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != schema_version:
        raise QualificationError("G4 证据 Schema 不匹配")
    return payload


def assess_g4_evidence(
    *,
    db_path: Path,
    manifest_path: Path,
    pi_report_path: Path | list[Path],
    transport_report_path: Path,
    rotation_report_path: Path | None = None,
    retention_report_path: Path | None = None,
    output_path: Path,
    expected_commit: str,
    expected_manifest_sha256: str,
    qualification_ledger_path: Path,
    qualification_batch_id: str | list[str],
) -> dict[str, object]:
    """只在三组独立证据身份一致时形成 G4 最终资格。"""

    qualification_ledger_path = _require_authoritative_qualification_ledger(
        qualification_ledger_path
    )
    if output_path.exists():
        raise QualificationError("G4 最终报告已存在，拒绝覆盖")
    if (rotation_report_path is None) == (retention_report_path is None):
        raise QualificationError("G4 密钥证据必须在轮换报告和保留报告中二选一")
    manifest = _load_manifest(manifest_path)
    if manifest.get("manifest_sha256") != expected_manifest_sha256:
        raise QualificationError("G4 冻结清单摘要不匹配")
    manifest_provider_values = [
        dict(item) for item in manifest["providers"] if isinstance(item, dict)
    ]
    manifest_providers = {
        (
            str(item["connection_id"]),
            str(item["connection_version"]),
            str(item["preset_id"]),
            str(item["model"]),
            str(item["api_format"]),
        )
        for item in manifest["providers"]
        if isinstance(item, dict)
    }
    pi_report_paths = (
        [pi_report_path] if isinstance(pi_report_path, Path) else list(pi_report_path)
    )
    qualification_batch_ids = (
        [qualification_batch_id]
        if isinstance(qualification_batch_id, str)
        else list(qualification_batch_id)
    )
    if (
        not pi_report_paths
        or len(pi_report_paths) != len(qualification_batch_ids)
        or len(set(pi_report_paths)) != len(pi_report_paths)
        or len(set(qualification_batch_ids)) != len(qualification_batch_ids)
    ):
        raise QualificationError("G4 Provider 报告与资格批次必须非空、一一对应且不得重复")
    pi_reports = [
        _load_evidence(path, "g4-pi-provider-report-v1")
        for path in pi_report_paths
    ]
    transport_report = _load_evidence(
        transport_report_path,
        "g4-transport-safety-report-v1",
    )
    rotation_report = (
        _load_evidence(rotation_report_path, "g4-vault-rotation-report-v2")
        if rotation_report_path is not None
        else None
    )
    retention_report = (
        _load_evidence(retention_report_path, "g4-vault-retention-report-v1")
        if retention_report_path is not None
        else None
    )
    blockers: list[str] = []
    provider_evidence_commits: list[str] = []
    qualification_ledger_ids: list[str] = []
    combined_pi_provider_set: set[tuple[str, str, str, str, str]] = set()
    pi_provider_chain_valid = True
    batch_ledger_valid = qualification_ledger_path.is_file()
    try:
        anchored_ledger = _anchored_qualification_ledger(
            db_path=db_path,
            ledger_path=qualification_ledger_path,
        )
    except (QualificationError, QualificationLedgerError, sqlite3.Error, OSError):
        anchored_ledger = None
        batch_ledger_valid = False
    for pi_report, batch_id in zip(
        pi_reports,
        qualification_batch_ids,
        strict=True,
    ):
        provider_evidence_commit = str(pi_report.get("git_commit") or "")
        provider_evidence_commits.append(provider_evidence_commit)
        ledger_id = str(pi_report.get("qualification_ledger_id") or "")
        qualification_ledger_ids.append(ledger_id)
        pi_providers = pi_report.get("providers")
        pi_provider_set = {
            (
                str(item.get("connection_id")),
                str(item.get("connection_version")),
                str(item.get("preset_id")),
                str(item.get("model")),
                str(item.get("api_format")),
            )
            for item in pi_providers
            if isinstance(item, dict)
        } if isinstance(pi_providers, list) else set()
        subset_providers = [
            item
            for item in manifest_provider_values
            if (
                str(item["connection_id"]),
                str(item["connection_version"]),
                str(item["preset_id"]),
                str(item["model"]),
                str(item["api_format"]),
            )
            in pi_provider_set
        ]
        expected_subset_manifest_sha256 = _canonical_sha256(
            {
                "schema_version": SCHEMA_VERSION,
                "providers": subset_providers,
            }
        )
        pi_provider_details_valid = bool(pi_provider_set) and all(
            isinstance(item, dict)
            and item.get("permission_profile") == "standard"
            and bool(str(item.get("owner_user_id") or "").strip())
            and item.get("outcome") == "passed"
            and item.get("runtime_status") == "candidate_ready"
            and item.get("usage_status") == "recorded"
            and item.get("candidate_count") == 1
            and item.get("verification_status") == "passed"
            and item.get("pi_provider_chain_passed") is True
            for item in (pi_providers or [])
        )
        if (
            not provider_evidence_commit
            or not ledger_id
            or pi_report.get("qualification_batch_id") != batch_id
            or pi_report.get("git_dirty") is not False
            or pi_report.get("manifest_sha256")
            != expected_subset_manifest_sha256
            or pi_report.get("synthetic_egress_only") is not True
            or pi_report.get("code_identity_stable") is not True
            or pi_report.get("pi_provider_chain_passed") is not True
            or not pi_provider_set.issubset(manifest_providers)
            or len(subset_providers) != len(pi_providers or [])
            or not pi_provider_details_valid
            or bool(combined_pi_provider_set & pi_provider_set)
        ):
            pi_provider_chain_valid = False
        combined_pi_provider_set.update(pi_provider_set)
        if anchored_ledger is None:
            batch_ledger_valid = False
            continue
        try:
            anchored_ledger.validate_passed_batch(
                batch_id=batch_id,
                manifest_sha256=expected_subset_manifest_sha256,
                providers=subset_providers,
                expected_commit=provider_evidence_commit,
                expected_ledger_id=ledger_id,
            )
        except (
            QualificationLedgerError,
            sqlite3.Error,
            OSError,
            ValueError,
            AttributeError,
        ):
            batch_ledger_valid = False
    if not batch_ledger_valid:
        blockers.append("qualification_batch_invalid")
    if not pi_provider_chain_valid or combined_pi_provider_set != manifest_providers:
        blockers.append("pi_provider_chain_invalid")
    if (
        transport_report.get("git_commit") != expected_commit
        or transport_report.get("git_dirty") is not False
        or transport_report.get("code_identity_stable") is not True
        or transport_report.get("transport_safety_passed") is not True
        or transport_report.get("pytest_returncode") != 0
        or transport_report.get("test_count") != len(_TRANSPORT_SAFETY_TESTS)
        or transport_report.get("test_ids") != list(_TRANSPORT_SAFETY_TESTS)
    ):
        blockers.append("transport_safety_invalid")
    vault_evidence_mode: str
    vault_evidence_key: str
    vault_evidence_path: Path
    if rotation_report is not None and rotation_report_path is not None:
        vault_evidence_mode = "rotated"
        vault_evidence_key = "vault_rotation"
        vault_evidence_path = rotation_report_path
        if (
            any(commit != expected_commit for commit in provider_evidence_commits)
            or rotation_report.get("phase") != "finalized"
            or rotation_report.get("git_commit") != expected_commit
            or rotation_report.get("git_dirty") is not False
            or rotation_report.get("code_identity_stable") is not True
            or rotation_report.get("old_key_generation_retained") is not False
            or rotation_report.get("key_backup_scope_verified") is not True
            or rotation_report.get("backup_scope_kind")
            != "configured_data_backups_root"
            or not rotation_report.get("key_backup_scope")
            or not rotation_report.get(
                "verified_database_only_backups_unreadable_with_current_key"
            )
        ):
            blockers.append("vault_rotation_invalid")
    elif retention_report is not None and retention_report_path is not None:
        vault_evidence_mode = "retained"
        vault_evidence_key = "vault_retention"
        vault_evidence_path = retention_report_path
        compatibility_value = retention_report.get("provider_runtime_compatibility")
        compatibilities = (
            [compatibility_value]
            if isinstance(compatibility_value, dict)
            else compatibility_value
        )
        reported_commits_value = retention_report.get(
            "provider_evidence_commits",
            [retention_report.get("provider_evidence_commit")],
        )
        accepted_by = retention_report.get("accepted_by")
        acceptance_reason = retention_report.get("acceptance_reason")
        authorization_valid = (
            isinstance(accepted_by, str)
            and bool(accepted_by.strip())
            and isinstance(acceptance_reason, str)
            and bool(acceptance_reason.strip())
        )
        if authorization_valid:
            try:
                _require_active_superadmin(
                    db_path=db_path,
                    actor_user_id=accepted_by,
                )
            except QualificationError:
                authorization_valid = False
        rechecked_compatibility = [
            _provider_runtime_compatibility(
                provider_evidence_commit=commit,
                current_commit=expected_commit,
            )
            for commit in provider_evidence_commits
        ]
        if (
            retention_report.get("git_commit") != expected_commit
            or retention_report.get("git_dirty") is not False
            or retention_report.get("code_identity_stable") is not True
            or retention_report.get("manifest_sha256")
            != expected_manifest_sha256
            or retention_report.get("database_path_sha256")
            != _database_path_sha256(db_path)
            or retention_report.get("production_key_changed") is not False
            or retention_report.get("live_secrets_decryptable") is not True
            or retention_report.get("wrong_key_rejected") is not True
            or retention_report.get("key_backup_scope_verified") is not True
            or retention_report.get("backup_scope_kind")
            != "configured_data_backups_root"
            or not retention_report.get("key_backup_scope")
            or retention_report.get("database_backup_recovery_verified") is not True
            or not retention_report.get("database_backup_evidence")
            or retention_report.get("synthetic_rotation_drill_passed") is not True
            or retention_report.get("retention_risk_accepted") is not True
            or not authorization_valid
            or reported_commits_value != provider_evidence_commits
            or not isinstance(compatibilities, list)
            or compatibilities != rechecked_compatibility
            or not all(
                isinstance(item, dict) and item.get("compatible") is True
                for item in rechecked_compatibility
            )
        ):
            blockers.append("vault_retention_invalid")
    else:
        raise AssertionError("缺少密钥证据")
    if blockers:
        raise QualificationError(
            "G4 证据不完整：" + ",".join(blockers)
        )
    report: dict[str, object] = {
        "schema_version": "g4-final-assessment-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": expected_commit,
        "manifest_sha256": expected_manifest_sha256,
        "g4_qualified": True,
        "vault_evidence_mode": vault_evidence_mode,
        "qualification_blockers": [],
        "qualification_batch_ids": qualification_batch_ids,
        "qualification_ledger_id": qualification_ledger_ids[0],
        "provider_evidence_commits": provider_evidence_commits,
        "evidence_sha256": {
            "manifest": _file_sha256(manifest_path),
            "pi_provider": (
                _file_sha256(pi_report_paths[0])
                if len(pi_report_paths) == 1
                else [_file_sha256(path) for path in pi_report_paths]
            ),
            "transport_safety": _file_sha256(transport_report_path),
            vault_evidence_key: _file_sha256(vault_evidence_path),
            "qualification_ledger": _file_sha256(qualification_ledger_path),
        },
    }
    if len(qualification_batch_ids) == 1:
        report["qualification_batch_id"] = qualification_batch_ids[0]
    _write_json_atomic(output_path, report)
    return report


def _validate_relay_base_url(value: str) -> str:
    if not isinstance(value, str):
        raise QualificationError("模型 Relay 必须是无凭证的本机地址")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.query)
        or bool(parsed.fragment)
    ):
        # Grant Token 只能发送给本机内部 Relay，不能被参数改成外部接收方。
        raise QualificationError("模型 Relay 必须是无凭证的本机地址")
    return value.rstrip("/")


def _validate_pi_relay_base_url(value: str) -> str:
    normalized = _validate_relay_base_url(value)
    if urlsplit(normalized).path != "/internal/model-relay":
        raise QualificationError("正式 Pi Relay 必须是精确的本机内部地址")
    return normalized


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="冻结并执行 G4 真实 Provider 安全验收",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze = subparsers.add_parser("freeze", help="冻结脱敏 Provider 清单")
    freeze.add_argument("--db-path", required=True, type=Path)
    freeze.add_argument("--preset", action="append", required=True)
    freeze.add_argument("--output", required=True, type=Path)
    start_batch = subparsers.add_parser(
        "start-batch",
        help="创建独立持久的 Provider 资格批次",
    )
    start_batch.add_argument("--db-path", required=True, type=Path)
    start_batch.add_argument("--manifest", required=True, type=Path)
    start_batch.add_argument("--owner-user-id", required=True)
    start_batch.add_argument(
        "--relay-base-url",
        default="http://127.0.0.1:8088/internal/model-relay",
    )
    start_batch.add_argument("--timeout-seconds", type=int, default=1800)
    start_batch.add_argument("--expected-commit", required=True)
    start_batch.add_argument("--authorized-by", required=True)
    start_batch.add_argument("--authorization-reason", required=True)
    start_batch.add_argument("--idempotency-key", required=True)
    batch_kind = start_batch.add_mutually_exclusive_group(required=True)
    batch_kind.add_argument("--confirm-initial-batch", action="store_true")
    batch_kind.add_argument(
        "--confirm-new-batch-after-exhausted-history",
        action="store_true",
    )
    start_batch.add_argument(
        "--previous-pi-report",
        action="append",
        type=Path,
        default=[],
    )
    run = subparsers.add_parser(
        "run",
        help="执行冻结清单的 Broker/Relay 烟测（不形成 G4 资格）",
    )
    run.add_argument("--db-path", required=True, type=Path)
    run.add_argument("--manifest", required=True, type=Path)
    run.add_argument("--output", required=True, type=Path)
    run.add_argument("--relay-base-url", default="http://127.0.0.1:8088")
    run.add_argument("--confirm-synthetic-egress", action="store_true")
    run.add_argument("--timeout-seconds", type=int, default=1800)
    run_pi = subparsers.add_parser(
        "run-pi",
        help="执行真实 Pi→Grant→Relay→Provider→Usage 合成链路",
    )
    run_pi.add_argument("--db-path", required=True, type=Path)
    run_pi.add_argument("--manifest", required=True, type=Path)
    run_pi.add_argument("--output", required=True, type=Path)
    run_pi.add_argument("--execution-root", required=True, type=Path)
    run_pi.add_argument(
        "--relay-base-url",
        default="http://127.0.0.1:8088/internal/model-relay",
    )
    run_pi.add_argument("--owner-user-id", required=True)
    run_pi.add_argument("--expected-commit", required=True)
    run_pi.add_argument("--qualification-batch-id", required=True)
    run_pi.add_argument("--confirm-synthetic-egress", action="store_true")
    run_pi.add_argument("--timeout-seconds", type=int, default=1800)
    authorize_retry = subparsers.add_parser(
        "authorize-ambiguous-retry",
        help="由用户确认重复请求与费用风险后授权一次新尝试",
    )
    authorize_retry.add_argument("--db-path", required=True, type=Path)
    authorize_retry.add_argument("--manifest", required=True, type=Path)
    authorize_retry.add_argument("--batch-id", required=True)
    authorize_retry.add_argument("--connection-id", required=True)
    authorize_retry.add_argument("--authorized-by", required=True)
    authorize_retry.add_argument("--authorization-reason", required=True)
    authorize_retry.add_argument(
        "--confirm-duplicate-request-and-cost",
        action="store_true",
    )
    recover_anchor = subparsers.add_parser(
        "recover-anchor",
        help="在无活动 Pi 进程时收口一次本地锚点同步失败",
    )
    recover_anchor.add_argument("--db-path", required=True, type=Path)
    recover_anchor.add_argument("--manifest", required=True, type=Path)
    recover_anchor.add_argument("--recovered-by", required=True)
    recover_anchor.add_argument("--recovery-reason", required=True)
    safety = subparsers.add_parser(
        "transport-safety",
        help="执行并冻结传输安全矩阵",
    )
    safety.add_argument("--expected-commit", required=True)
    safety.add_argument("--output", required=True, type=Path)
    retain = subparsers.add_parser(
        "verify-vault-retention",
        help="保留生产密钥并生成补偿控制证据",
    )
    retain.add_argument("--db-path", required=True, type=Path)
    retain.add_argument("--key-path", required=True, type=Path)
    retain.add_argument("--manifest", required=True, type=Path)
    retain.add_argument(
        "--provider-evidence-commit",
        action="append",
        required=True,
    )
    retain.add_argument("--expected-commit", required=True)
    retain.add_argument("--output", required=True, type=Path)
    retain.add_argument(
        "--key-backup-root",
        action="append",
        required=True,
        type=Path,
    )
    retain.add_argument("--accepted-by", required=True)
    retain.add_argument("--acceptance-reason", required=True)
    retain.add_argument("--confirm-retain-production-key", action="store_true")
    assess = subparsers.add_parser(
        "assess",
        help="汇总 Pi、传输安全与密钥轮换证据",
    )
    assess.add_argument("--db-path", required=True, type=Path)
    assess.add_argument("--pi-report", action="append", required=True, type=Path)
    assess.add_argument("--manifest", required=True, type=Path)
    assess.add_argument("--transport-report", required=True, type=Path)
    vault_evidence = assess.add_mutually_exclusive_group(required=True)
    vault_evidence.add_argument("--rotation-report", type=Path)
    vault_evidence.add_argument("--retention-report", type=Path)
    assess.add_argument("--expected-commit", required=True)
    assess.add_argument("--expected-manifest-sha256", required=True)
    assess.add_argument(
        "--qualification-batch-id",
        action="append",
        required=True,
    )
    assess.add_argument("--output", required=True, type=Path)
    rotate = subparsers.add_parser(
        "rotate-vault",
        help="两阶段重加密 Provider Secret",
    )
    rotate.add_argument("--phase", choices=("prepare", "finalize"), required=True)
    rotate.add_argument("--db-path", required=True, type=Path)
    rotate.add_argument("--key-path", required=True, type=Path)
    rotate.add_argument("--expected-key-sha256", required=True)
    rotate.add_argument("--expected-commit", required=True)
    rotate.add_argument("--output", required=True, type=Path)
    rotate.add_argument("--expected-stopped-pid", type=int)
    rotate.add_argument(
        "--relay-base-url",
        default="http://127.0.0.1:8088",
    )
    rotate.add_argument("--database-backup", action="append", type=Path)
    rotate.add_argument("--key-backup-root", action="append", type=Path)
    rotate.add_argument("--confirm-maintenance-window", action="store_true")
    rotate.add_argument(
        "--confirm-irreversible-backup-erasure",
        action="store_true",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "freeze":
            manifest = freeze_manifest(
                db_path=args.db_path,
                presets=args.preset,
            )
            _write_json_atomic(args.output, manifest)
            print(
                "G4_PROVIDER_MANIFEST_FROZEN "
                f"sha256={manifest['manifest_sha256']}",
                flush=True,
            )
            return 0
        if args.command == "start-batch":
            batch = create_qualification_batch(
                db_path=args.db_path,
                manifest_path=args.manifest,
                ledger_path=AUTHORITATIVE_QUALIFICATION_LEDGER_PATH,
                owner_user_id=args.owner_user_id,
                relay_base_url=args.relay_base_url,
                timeout_seconds=args.timeout_seconds,
                expected_commit=args.expected_commit,
                authorized_by=args.authorized_by,
                authorization_reason=args.authorization_reason,
                idempotency_key=args.idempotency_key,
                confirm_initial_batch=args.confirm_initial_batch,
                confirm_new_batch_after_exhausted_history=(
                    args.confirm_new_batch_after_exhausted_history
                ),
                previous_report_paths=tuple(args.previous_pi_report),
            )
            print(
                "G4_PROVIDER_QUALIFICATION_BATCH_CREATED "
                f"batch_id={batch['batch_id']} "
                f"ledger_id={batch['ledger_id']}",
                flush=True,
            )
            return 0
        if args.command == "run":
            if not args.confirm_synthetic_egress:
                raise QualificationError("未确认仅外发合成测试数据")
            if args.timeout_seconds <= 0:
                raise QualificationError("超时必须大于 0 秒")
            verify_frozen_inventory(
                db_path=args.db_path,
                manifest_path=args.manifest,
            )
            report = execute_qualification(
                db_path=args.db_path,
                manifest_path=args.manifest,
                output_path=args.output,
                relay_base_url=_validate_relay_base_url(args.relay_base_url),
                timeout_seconds=args.timeout_seconds,
            )
            print(
                "G4_PROVIDER_CHAIN_SMOKE_"
                f"{'PASS' if report['provider_chain_smoke_passed'] else 'FAIL'} "
                f"manifest_sha256={report['manifest_sha256']}",
                flush=True,
            )
            return 0 if report["provider_chain_smoke_passed"] else 2
        if args.command == "run-pi":
            if not args.confirm_synthetic_egress:
                raise QualificationError("未确认仅外发合成测试数据")
            report = asyncio.run(
                execute_pi_provider_chain(
                    db_path=args.db_path,
                    manifest_path=args.manifest,
                    output_path=args.output,
                    execution_root=args.execution_root,
                    relay_base_url=args.relay_base_url,
                    timeout_seconds=args.timeout_seconds,
                    owner_user_id=args.owner_user_id,
                    expected_commit=args.expected_commit,
                    qualification_ledger_path=(
                        AUTHORITATIVE_QUALIFICATION_LEDGER_PATH
                    ),
                    qualification_batch_id=args.qualification_batch_id,
                )
            )
            print(
                "G4_PI_PROVIDER_CHAIN_"
                f"{'PASS' if report['pi_provider_chain_passed'] else 'FAIL'} "
                f"manifest_sha256={report['manifest_sha256']}",
                flush=True,
            )
            return 0 if report["pi_provider_chain_passed"] else 2
        if args.command == "authorize-ambiguous-retry":
            report = authorize_qualification_batch_retry(
                db_path=args.db_path,
                manifest_path=args.manifest,
                ledger_path=AUTHORITATIVE_QUALIFICATION_LEDGER_PATH,
                batch_id=args.batch_id,
                connection_id=args.connection_id,
                authorized_by=args.authorized_by,
                authorization_reason=args.authorization_reason,
                confirm_duplicate_request_and_cost=(
                    args.confirm_duplicate_request_and_cost
                ),
            )
            print(
                "G4_PROVIDER_AMBIGUOUS_RETRY_AUTHORIZED "
                f"connection_id={report['connection_id']}",
                flush=True,
            )
            return 0
        if args.command == "recover-anchor":
            report = recover_qualification_ledger_anchor(
                db_path=args.db_path,
                manifest_path=args.manifest,
                ledger_path=AUTHORITATIVE_QUALIFICATION_LEDGER_PATH,
                recovered_by=args.recovered_by,
                recovery_reason=args.recovery_reason,
            )
            print(
                "G4_PROVIDER_QUALIFICATION_ANCHOR_RECOVERED "
                f"ledger_id={report['ledger_id']} "
                f"ledger_revision={report['ledger_revision']}",
                flush=True,
            )
            return 0
        if args.command == "transport-safety":
            report = execute_transport_safety(
                output_path=args.output,
                expected_commit=args.expected_commit,
            )
            print(
                "G4_TRANSPORT_SAFETY_"
                f"{'PASS' if report['transport_safety_passed'] else 'FAIL'}",
                flush=True,
            )
            return 0 if report["transport_safety_passed"] else 2
        if args.command == "verify-vault-retention":
            report = verify_vault_retention_safety(
                db_path=args.db_path,
                key_path=args.key_path,
                manifest_path=args.manifest,
                output_path=args.output,
                expected_commit=args.expected_commit,
                provider_evidence_commit=args.provider_evidence_commit,
                accepted_by=args.accepted_by,
                acceptance_reason=args.acceptance_reason,
                confirm_retain_production_key=(
                    args.confirm_retain_production_key
                ),
                key_backup_roots=args.key_backup_root,
            )
            print(
                "G4_VAULT_RETENTION_SAFETY_PASS "
                f"manifest_sha256={report['manifest_sha256']}",
                flush=True,
            )
            return 0
        if args.command == "assess":
            report = assess_g4_evidence(
                db_path=args.db_path,
                manifest_path=args.manifest,
                pi_report_path=args.pi_report,
                transport_report_path=args.transport_report,
                rotation_report_path=args.rotation_report,
                retention_report_path=args.retention_report,
                output_path=args.output,
                expected_commit=args.expected_commit,
                expected_manifest_sha256=args.expected_manifest_sha256,
                qualification_ledger_path=(
                    AUTHORITATIVE_QUALIFICATION_LEDGER_PATH
                ),
                qualification_batch_id=args.qualification_batch_id,
            )
            print(
                "G4_FINAL_ASSESSMENT_PASS "
                f"manifest_sha256={report['manifest_sha256']}",
                flush=True,
            )
            return 0
        if args.command == "rotate-vault":
            if not args.confirm_maintenance_window:
                raise QualificationError("必须确认 8088 停服维护窗口")
            if (
                args.phase == "finalize"
                and not args.confirm_irreversible_backup_erasure
            ):
                raise QualificationError(
                    "必须确认维护窗口与不可逆备份擦除"
                )
            if args.expected_stopped_pid is None:
                raise QualificationError("必须提供已停止的 8088 PID")
            rotation_identity = _git_identity()
            if (
                rotation_identity["git_commit"] != args.expected_commit
                or rotation_identity["git_dirty"]
            ):
                raise QualificationError("密钥轮换必须绑定预期的干净 Git 提交")
            key_sha256_before = _file_sha256(args.key_path)
            if key_sha256_before != args.expected_key_sha256:
                raise QualificationError("模型连接主密钥摘要不匹配")
            if args.phase == "prepare":
                count = prepare_vault_rotation(
                    db_path=args.db_path,
                    key_path=args.key_path,
                    backend_stopped_check=lambda: _backend_stopped_check(
                        expected_pid=args.expected_stopped_pid,
                        relay_base_url=args.relay_base_url,
                    ),
                )
                result = {
                    "schema_version": "g4-vault-rotation-report-v2",
                    **rotation_identity,
                    "phase": "prepared",
                    "rotated_secret_count": count,
                    "key_sha256_before": key_sha256_before,
                    "key_sha256_after": _file_sha256(args.key_path),
                    "old_key_generation_retained": True,
                    "requires_backend_restart_then_stop": True,
                }
            else:
                (
                    count,
                    key_backup_evidence,
                    backup_evidence,
                ) = finalize_vault_rotation(
                    db_path=args.db_path,
                    key_path=args.key_path,
                    backend_stopped_check=lambda: _backend_stopped_check(
                        expected_pid=args.expected_stopped_pid,
                        relay_base_url=args.relay_base_url,
                    ),
                    key_backup_roots=args.key_backup_root or [],
                    database_backup_paths=args.database_backup or [],
                )
                result = {
                    "schema_version": "g4-vault-rotation-report-v2",
                    **rotation_identity,
                    "phase": "finalized",
                    "rotated_secret_count": count,
                    "key_sha256_before": key_sha256_before,
                    "key_sha256_after": _file_sha256(args.key_path),
                    "old_key_generation_retained": False,
                    "verified_database_only_backups_unreadable_with_current_key": (
                        backup_evidence
                    ),
                    "backup_scope_kind": "configured_data_backups_root",
                    "unscoped_locations_not_claimed": True,
                    "key_backup_scope_verified": True,
                    "key_backup_scope": key_backup_evidence,
                }
            result["code_identity_stable"] = (
                _git_identity() == rotation_identity
            )
            _write_json_atomic(args.output, result)
            print(
                "G4_VAULT_ROTATION_OK "
                f"rotated_secret_count={count}",
                flush=True,
            )
            return 0
    except (QualificationError, sqlite3.Error, OSError) as exc:
        print(f"G4_PROVIDER_QUALIFICATION_FAILED: {exc}", file=sys.stderr)
        return 2
    except Exception:
        # CLI 边界不输出可能携带 Provider/路径上下文的意外异常正文。
        print(
            "G4_PROVIDER_QUALIFICATION_FAILED: internal_error",
            file=sys.stderr,
        )
        return 2
    raise AssertionError("未知命令")


if __name__ == "__main__":
    raise SystemExit(main())
