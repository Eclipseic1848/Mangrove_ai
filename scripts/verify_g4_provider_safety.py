# -*- coding: utf-8 -*-
"""冻结 G4 真实 Provider 验收清单。"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
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
from src.model_connections.vault import FernetCredentialVault


SCHEMA_VERSION = "g4-provider-manifest-v1"


class QualificationError(RuntimeError):
    """G4 验收前置条件不满足。"""


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
            with sqlite3.connect(uri, uri=True) as connection:
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
        with sqlite3.connect(uri, uri=True) as connection:
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
    relay_base_url = _validate_relay_base_url(relay_base_url)
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
) -> dict[str, object]:
    """串行化同一正式 Pi 证据目标，避免重复外发。"""

    relay_base_url = _validate_relay_base_url(relay_base_url)
    lock_path, ledger_path = _qualification_state_paths(
        db_path=db_path,
        manifest_path=manifest_path,
        action="pi-provider",
    )
    with _exclusive_file_lock(lock_path, "已有相同 G4 Pi 链路正在执行"):
        manifest = _load_manifest(manifest_path)
        prior_checks, before_provider, after_provider = _attempt_ledger_callbacks(
            ledger_path=ledger_path,
            action="pi-provider",
            manifest=manifest,
            run_context={
                **_git_identity(),
                "relay_base_url": relay_base_url,
                "timeout_seconds": timeout_seconds,
                "owner_user_id": owner_user_id,
                "expected_commit": expected_commit,
            },
        )
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
    manifest_path: Path,
    pi_report_path: Path,
    transport_report_path: Path,
    rotation_report_path: Path,
    output_path: Path,
    expected_commit: str,
    expected_manifest_sha256: str,
) -> dict[str, object]:
    """只在三组独立证据身份一致时形成 G4 最终资格。"""

    if output_path.exists():
        raise QualificationError("G4 最终报告已存在，拒绝覆盖")
    manifest = _load_manifest(manifest_path)
    if manifest.get("manifest_sha256") != expected_manifest_sha256:
        raise QualificationError("G4 冻结清单摘要不匹配")
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
    pi_report = _load_evidence(
        pi_report_path,
        "g4-pi-provider-report-v1",
    )
    transport_report = _load_evidence(
        transport_report_path,
        "g4-transport-safety-report-v1",
    )
    rotation_report = _load_evidence(
        rotation_report_path,
        "g4-vault-rotation-report-v2",
    )
    blockers: list[str] = []
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
        pi_report.get("git_commit") != expected_commit
        or pi_report.get("git_dirty") is not False
        or pi_report.get("manifest_sha256") != expected_manifest_sha256
        or pi_report.get("synthetic_egress_only") is not True
        or pi_report.get("code_identity_stable") is not True
        or pi_report.get("pi_provider_chain_passed") is not True
        or pi_provider_set != manifest_providers
        or len(pi_providers or []) != len(manifest_providers)
        or not pi_provider_details_valid
    ):
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
    if (
        rotation_report.get("phase") != "finalized"
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
        "qualification_blockers": [],
        "evidence_sha256": {
            "manifest": _file_sha256(manifest_path),
            "pi_provider": _file_sha256(pi_report_path),
            "transport_safety": _file_sha256(transport_report_path),
            "vault_rotation": _file_sha256(rotation_report_path),
        },
    }
    _write_json_atomic(output_path, report)
    return report


def _validate_relay_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        # Grant Token 只能发送给本机内部 Relay，不能被参数改成外部接收方。
        raise QualificationError("模型 Relay 必须是本机地址")
    return value.rstrip("/")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="冻结并执行 G4 真实 Provider 安全验收",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze = subparsers.add_parser("freeze", help="冻结脱敏 Provider 清单")
    freeze.add_argument("--db-path", required=True, type=Path)
    freeze.add_argument("--preset", action="append", required=True)
    freeze.add_argument("--output", required=True, type=Path)
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
    run_pi.add_argument("--confirm-synthetic-egress", action="store_true")
    run_pi.add_argument("--timeout-seconds", type=int, default=1800)
    authorize_retry = subparsers.add_parser(
        "authorize-ambiguous-retry",
        help="由用户确认重复请求与费用风险后授权一次新尝试",
    )
    authorize_retry.add_argument("--db-path", required=True, type=Path)
    authorize_retry.add_argument("--manifest", required=True, type=Path)
    authorize_retry.add_argument("--owner-user-id", required=True)
    authorize_retry.add_argument("--connection-id", required=True)
    authorize_retry.add_argument(
        "--relay-base-url",
        default="http://127.0.0.1:8088/internal/model-relay",
    )
    authorize_retry.add_argument("--timeout-seconds", type=int, default=1800)
    authorize_retry.add_argument("--expected-commit", required=True)
    authorize_retry.add_argument(
        "--confirm-duplicate-request-and-cost",
        action="store_true",
    )
    safety = subparsers.add_parser(
        "transport-safety",
        help="执行并冻结传输安全矩阵",
    )
    safety.add_argument("--expected-commit", required=True)
    safety.add_argument("--output", required=True, type=Path)
    assess = subparsers.add_parser(
        "assess",
        help="汇总 Pi、传输安全与密钥轮换证据",
    )
    assess.add_argument("--pi-report", required=True, type=Path)
    assess.add_argument("--manifest", required=True, type=Path)
    assess.add_argument("--transport-report", required=True, type=Path)
    assess.add_argument("--rotation-report", required=True, type=Path)
    assess.add_argument("--expected-commit", required=True)
    assess.add_argument("--expected-manifest-sha256", required=True)
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
            report = authorize_ambiguous_retry(
                db_path=args.db_path,
                manifest_path=args.manifest,
                owner_user_id=args.owner_user_id,
                connection_id=args.connection_id,
                relay_base_url=args.relay_base_url,
                timeout_seconds=args.timeout_seconds,
                expected_commit=args.expected_commit,
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
        if args.command == "assess":
            report = assess_g4_evidence(
                manifest_path=args.manifest,
                pi_report_path=args.pi_report,
                transport_report_path=args.transport_report,
                rotation_report_path=args.rotation_report,
                output_path=args.output,
                expected_commit=args.expected_commit,
                expected_manifest_sha256=args.expected_manifest_sha256,
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
