# -*- coding: utf-8 -*-
"""从既有工作台事实重建并复核真实任务重放身份。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable, Protocol

from src.capability_adapters import load_runtime_manifests
from src.capability_catalog import CapabilitySelection, CatalogActor
from src.agentic_runtime.models import PiRuntimeRequest
from src.config.settings import settings
from src.services.upload_store import UploadStore

from .models import (
    CapabilityGovernanceTarget,
    ValidationTaskOption,
    ValidationTaskRef,
    is_ac06_admin_gray_validation_target,
)


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ValidationTaskResolver(Protocol):
    def list_options(
        self,
        actor: CatalogActor,
        target: CapabilityGovernanceTarget,
    ) -> tuple[ValidationTaskOption, ...]: ...

    def resolve(
        self,
        actor: CatalogActor,
        target: CapabilityGovernanceTarget,
        *,
        task_id: str,
        revision: int,
    ) -> ValidationTaskRef: ...

    def verify(
        self,
        actor: CatalogActor,
        target: CapabilityGovernanceTarget,
        task_ref: ValidationTaskRef,
    ) -> ValidationTaskRef: ...

    def verify_independent_verifier(
        self,
        actor: CatalogActor,
        target: CapabilityGovernanceTarget,
        task_ref: ValidationTaskRef,
    ) -> str: ...

    def load_replay_request(
        self,
        actor: CatalogActor,
        target: CapabilityGovernanceTarget,
        task_ref: ValidationTaskRef,
    ) -> PiRuntimeRequest: ...


class SqliteValidationTaskResolver:
    """只读取 Owner 自己的冻结任务、选择、输入摘要和正式输出。"""

    def __init__(
        self,
        db_path: str | Path,
        *,
        upload_root: str | Path | None = None,
        execution_root: str | Path | None = None,
        capability_mounts: Callable[[str, str, int], tuple[Path, ...]] | None = None,
    ) -> None:
        self._db_path = str(db_path)
        self._upload_root = Path(
            upload_root or settings.data_prep_upload_root
        ).expanduser().resolve()
        self._execution_root = Path(
            execution_root or settings.semantic_execution_root
        ).expanduser().resolve()
        self._capability_mounts = capability_mounts
        self._uploads = UploadStore(
            root=str(self._upload_root),
            max_bytes=settings.data_prep_max_upload_bytes,
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone() is not None

    def _expected_target_tools(
        self,
        owner_id: str,
        task_id: str,
        revision: int,
        target: CapabilityGovernanceTarget,
    ) -> set[str]:
        if self._capability_mounts is None:
            return set()
        target_mounts: list[Path] = []
        for raw_path in self._capability_mounts(owner_id, task_id, revision):
            path = Path(raw_path).resolve()
            marker = path / ".mangrove-capability-digest"
            if (
                marker.is_file()
                and marker.read_text(encoding="utf-8").strip() == target.digest
            ):
                target_mounts.append(path)
        if len(target_mounts) != 1:
            raise ValueError("无法解析验证目标的精确能力挂载")
        return {
            "capability_"
            + re.sub(r"[^a-z0-9_]+", "_", item.manifest.name.lower())[:53]
            for item in load_runtime_manifests(tuple(target_mounts))
        }

    @staticmethod
    def _successful_tools_for_run(
        connection: sqlite3.Connection,
        *,
        owner_id: str,
        task_id: str,
        revision: int,
        run_id: str,
    ) -> set[str]:
        if not SqliteValidationTaskResolver._table_exists(
            connection,
            "agentic_runtime_events",
        ):
            return set()
        current_run_id = ""
        completed: set[str] = set()
        rows = connection.execute(
            "SELECT event_type, details_json FROM agentic_runtime_events "
            "WHERE user_id=? AND task_id=? AND revision=? ORDER BY sequence",
            (owner_id, task_id, revision),
        ).fetchall()
        for row in rows:
            try:
                details = json.loads(row["details_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                details = {}
            if row["event_type"] in {"runtime.preparing", "runtime.resuming"}:
                current_run_id = str(details.get("run_id") or "")
                continue
            if row["event_type"] == "tool.completed" and current_run_id == run_id:
                tool = str(details.get("tool") or "")
                if tool and not bool(details.get("failed")):
                    completed.add(tool)
        return completed

    def resolve(
        self,
        actor: CatalogActor,
        target: CapabilityGovernanceTarget,
        *,
        task_id: str,
        revision: int,
    ) -> ValidationTaskRef:
        if target.owner_id != actor.owner_id and not (
            actor.is_admin and is_ac06_admin_gray_validation_target(target)
        ):
            raise PermissionError("不能使用其他用户的任务作为验证证据")
        with self._connect() as connection:
            task = connection.execute(
                "SELECT upload_ids_json, source_refs_json FROM "
                "semantic_workspace_tasks WHERE user_id=? AND task_id=?",
                (actor.owner_id, task_id),
            ).fetchone()
            frozen = connection.execute(
                "SELECT status, run_id FROM semantic_workspace_revisions "
                "WHERE user_id=? AND task_id=? AND revision=?",
                (actor.owner_id, task_id, revision),
            ).fetchone()
            selection_row = connection.execute(
                "SELECT payload_json FROM capability_selections "
                "WHERE owner_id=? AND task_id=? AND revision=?",
                (actor.owner_id, task_id, revision),
            ).fetchone()
            if task is None or frozen is None or selection_row is None:
                raise PermissionError("任务、Revision 或冻结能力授权不存在")
            if frozen["status"] != "completed" or not frozen["run_id"]:
                raise ValueError("真实任务尚未形成可重放的完成版本")
            selection = CapabilitySelection.model_validate_json(
                selection_row["payload_json"]
            )
            if not any(
                item.pack_id == target.pack_id
                and item.version == target.version
                and item.digest == target.digest
                for item in selection.pack_refs
            ):
                raise PermissionError("TaskRevision 未授权目标能力 digest")
            expected_tools = self._expected_target_tools(
                actor.owner_id,
                task_id,
                revision,
                target,
            )
            if expected_tools and not expected_tools.intersection(
                self._successful_tools_for_run(
                    connection,
                    owner_id=actor.owner_id,
                    task_id=task_id,
                    revision=revision,
                    run_id=str(frozen["run_id"]),
                )
            ):
                # 只冻结未调用的能力不能证明真实业务可用，提前拒绝可避免无效重放和 Token 消耗。
                raise ValueError("真实任务未成功调用目标能力")
            attempt = connection.execute(
                "SELECT input_hash FROM semantic_harness_attempts "
                "WHERE user_id=? AND run_id=? AND status='succeeded' "
                "ORDER BY attempt_number DESC, created_at DESC LIMIT 1",
                (actor.owner_id, frozen["run_id"]),
            ).fetchone()
            input_sha256 = (
                str(attempt["input_hash"]).removeprefix("sha256:")
                if attempt is not None and attempt["input_hash"]
                else ""
            )
            if self._table_exists(connection, "agentic_runtime_runs"):
                runtime = connection.execute(
                    "SELECT request_json, external_api_confirmed "
                    "FROM agentic_runtime_runs "
                    "WHERE user_id=? AND task_id=? AND revision=? AND run_id=?",
                    (
                        actor.owner_id,
                        task_id,
                        revision,
                        frozen["run_id"],
                    ),
                ).fetchone()
                if runtime is not None and runtime["request_json"]:
                    runtime_request = json.loads(runtime["request_json"])
                    if (
                        runtime_request.get("model_connection_id")
                        and not bool(runtime["external_api_confirmed"])
                    ):
                        raise PermissionError(
                            "原 TaskRevision 未确认模型连接数据外发"
                        )
                    if not input_sha256:
                        input_sha256 = _canonical_hash(runtime_request)
            if not input_sha256:
                raise ValueError("真实任务缺少可复核的输入 hash")
            outputs: list[tuple[str, str, int, str]] = []
            # vNext 正式 Delivery 是权威输出；只有不存在正式输出时才兼容 Legacy，不能把
            # 两套历史制品混成一个并不存在的输出集合。
            for table, owner_column in (
                ("formal_delivery_outputs", "owner_id"),
                ("semantic_delivery_outputs", "user_id"),
            ):
                if not self._table_exists(connection, table):
                    continue
                found = [
                    (
                        str(row["output_id"]),
                        str(row["sha256"]),
                        int(row["size_bytes"]),
                        str(row["file_path"]),
                    )
                    for row in connection.execute(
                        f"SELECT output_id, sha256, size_bytes, file_path FROM {table} "
                        f"WHERE {owner_column}=? AND run_id=? ORDER BY output_id",
                        (actor.owner_id, frozen["run_id"]),
                    ).fetchall()
                ]
                if found:
                    outputs = found
                    break
        if not outputs:
            raise ValueError("真实任务缺少可复核的正式输出 hash")
        upload_ids = json.loads(task["upload_ids_json"] or "[]")
        if not upload_ids:
            raise ValueError("真实任务缺少可重新打开的 Owner 来源")
        source_objects: list[tuple[str, str, int]] = []
        safe_owner = "".join(
            character
            for character in actor.owner_id
            if character.isalnum() or character in "-_"
        )
        owner_objects = (self._upload_root / safe_owner / "objects").resolve()
        for upload_id in upload_ids:
            item = self._uploads.resolve(actor.owner_id, str(upload_id))
            source_path = Path(item.storage_path)
            resolved_source = source_path.expanduser().resolve()
            if (
                owner_objects not in resolved_source.parents
                or not resolved_source.is_file()
                or source_path.is_symlink()
            ):
                raise ValueError("真实任务来源已缺失或不是普通文件")
            actual = _file_sha256(resolved_source)
            if actual != item.sha256 or resolved_source.stat().st_size != item.size_bytes:
                raise ValueError("真实任务来源内容 hash 或大小已变化")
            source_objects.append((str(upload_id), actual, item.size_bytes))
        reopened_outputs: list[tuple[str, str]] = []
        for output_id, expected_hash, expected_size, file_path in outputs:
            output_path = Path(file_path).expanduser().resolve()
            if (
                self._execution_root not in output_path.parents
                or not output_path.is_file()
                or Path(file_path).is_symlink()
            ):
                raise ValueError("真实任务正式输出已缺失或不是普通文件")
            actual = _file_sha256(output_path)
            if actual != expected_hash or output_path.stat().st_size != expected_size:
                raise ValueError("真实任务正式输出内容 hash 或大小已变化")
            reopened_outputs.append((output_id, actual))
        source_snapshot_sha256 = _canonical_hash(
            {
                "uploads": source_objects,
                "source_refs": json.loads(task["source_refs_json"] or "[]"),
            }
        )
        return ValidationTaskRef(
            task_id=task_id,
            revision=revision,
            source_snapshot_sha256=source_snapshot_sha256,
            input_sha256=input_sha256,
            output_sha256=_canonical_hash(sorted(set(reopened_outputs))),
            capability_digest=target.digest,
            authorization_id=selection.selection_id,
        )

    def list_options(
        self,
        actor: CatalogActor,
        target: CapabilityGovernanceTarget,
    ) -> tuple[ValidationTaskOption, ...]:
        if target.owner_id != actor.owner_id and not (
            actor.is_admin and is_ac06_admin_gray_validation_target(target)
        ):
            return ()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT s.task_id, s.revision, s.payload_json, t.title, "
                "r.updated_at FROM capability_selections s "
                "JOIN semantic_workspace_tasks t ON t.task_id=s.task_id "
                "AND t.user_id=s.owner_id "
                "JOIN semantic_workspace_revisions r ON r.task_id=s.task_id "
                "AND r.user_id=s.owner_id AND r.revision=s.revision "
                "WHERE s.owner_id=? AND r.status='completed' "
                "ORDER BY r.updated_at DESC",
                (actor.owner_id,),
            ).fetchall()
        options: list[ValidationTaskOption] = []
        for row in rows:
            selection = CapabilitySelection.model_validate_json(row["payload_json"])
            if not any(
                item.pack_id == target.pack_id
                and item.version == target.version
                and item.digest == target.digest
                for item in selection.pack_refs
            ):
                continue
            try:
                self.resolve(
                    actor,
                    target,
                    task_id=row["task_id"],
                    revision=row["revision"],
                )
            except (PermissionError, ValueError):
                continue
            options.append(
                ValidationTaskOption(
                    task_id=row["task_id"],
                    revision=row["revision"],
                    title=row["title"],
                    updated_at=row["updated_at"],
                )
            )
        return tuple(options)

    def verify(
        self,
        actor: CatalogActor,
        target: CapabilityGovernanceTarget,
        task_ref: ValidationTaskRef,
    ) -> ValidationTaskRef:
        current = self.resolve(
            actor,
            target,
            task_id=task_ref.task_id,
            revision=task_ref.revision,
        )
        if current != task_ref:
            raise ValueError("真实任务来源、输入、输出或授权已变化")
        return current

    def verify_independent_verifier(
        self,
        actor: CatalogActor,
        target: CapabilityGovernanceTarget,
        task_ref: ValidationTaskRef,
    ) -> str:
        current = self.verify(actor, target, task_ref)
        with self._connect() as connection:
            frozen = connection.execute(
                "SELECT run_id FROM semantic_workspace_revisions "
                "WHERE user_id=? AND task_id=? AND revision=?",
                (actor.owner_id, current.task_id, current.revision),
            ).fetchone()
            verification = (
                connection.execute(
                    "SELECT run_id, verification_json FROM agentic_runtime_runs "
                    "WHERE user_id=? AND task_id=? AND revision=? AND run_id=?",
                    (
                        actor.owner_id,
                        current.task_id,
                        current.revision,
                        frozen["run_id"] if frozen is not None else "",
                    ),
                ).fetchone()
                if self._table_exists(connection, "agentic_runtime_runs")
                else None
            )
        if (
            frozen is None
            or verification is None
            or verification["run_id"] != frozen["run_id"]
            or not verification["verification_json"]
        ):
            raise ValueError("真实任务缺少独立 Verifier 结果")
        payload = json.loads(verification["verification_json"])
        if payload.get("status") != "passed":
            raise ValueError("真实任务独立 Verifier 未通过")
        checks = payload.get("checks") or []
        if not checks or any(not item.get("passed") for item in checks):
            raise ValueError("真实任务独立 Verifier 证据不完整")
        return _canonical_hash(
            {
                "run_id": frozen["run_id"],
                "status": payload["status"],
                "checks": [item.get("code") for item in checks],
                "evidence_count": payload.get("evidence_count"),
            }
        )

    def load_replay_request(
        self,
        actor: CatalogActor,
        target: CapabilityGovernanceTarget,
        task_ref: ValidationTaskRef,
    ) -> PiRuntimeRequest:
        """只在内存中恢复已冻结请求；ValidationRun 永不复制正文或连接 Secret。"""

        current = self.verify(actor, target, task_ref)
        with self._connect() as connection:
            frozen = connection.execute(
                "SELECT run_id FROM semantic_workspace_revisions "
                "WHERE user_id=? AND task_id=? AND revision=?",
                (actor.owner_id, current.task_id, current.revision),
            ).fetchone()
            runtime = (
                connection.execute(
                    "SELECT run_id, request_json, external_api_confirmed "
                    "FROM agentic_runtime_runs "
                    "WHERE user_id=? AND task_id=? AND revision=?",
                    (actor.owner_id, current.task_id, current.revision),
                ).fetchone()
                if self._table_exists(connection, "agentic_runtime_runs")
                else None
            )
        if (
            frozen is None
            or runtime is None
            or runtime["run_id"] != frozen["run_id"]
            or not runtime["request_json"]
        ):
            raise ValueError("真实任务缺少可恢复的冻结 Runtime 请求")
        values = json.loads(runtime["request_json"])
        if _canonical_hash(values) != current.input_sha256:
            raise ValueError("真实任务冻结 Runtime 请求 hash 已变化")
        if not values.get("model_connection_id"):
            # 本地连接的固定占位 Key 从未落库；重放时恢复非秘密占位值。
            values["api_key"] = "local-runtime"
        elif not bool(runtime["external_api_confirmed"]):
            raise PermissionError("原 TaskRevision 未确认模型连接数据外发")
        request = PiRuntimeRequest.model_validate(values)
        if (
            request.user_id != actor.owner_id
            or request.task_id != current.task_id
            or request.revision != current.revision
        ):
            raise PermissionError("冻结 Runtime 请求与 Owner TaskRef 不一致")
        return request
