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
    AuditSubjectType,
    BusinessContent,
    CapabilityGovernanceTarget,
    CapabilityTaskMetadata,
    ValidationTaskOption,
    ValidationTaskRef,
    is_ac06_admin_gray_validation_target,
)

# 审计查看单对象读取上限；超出只返回截断前段，hash 按实际返回内容计算。
_BUSINESS_CONTENT_MAX_BYTES = 2 * 1024 * 1024


def _read_limited(path: Path, limit: int) -> tuple[bytes, bool]:
    """按块读取至多 limit 字节；返回内容与是否被截断，避免大文件整读进内存。"""

    with path.open("rb") as stream:
        chunks: list[bytes] = []
        total = 0
        while total < limit:
            chunk = stream.read(min(1024 * 1024, limit - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        # 多读 1 字节判定是否还有剩余；审计查看从不把超限尾部读进内存。
        truncated = bool(stream.read(1))
        return b"".join(chunks), truncated


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

    def read_task_metadata(
        self,
        actor: CatalogActor,
        task_id: str,
        revision: int,
        *,
        task_owner_id: str,
    ) -> CapabilityTaskMetadata: ...

    def read_business_content(
        self,
        actor: CatalogActor,
        task_id: str,
        revision: int,
        subject_type: AuditSubjectType,
        *,
        task_owner_id: str,
    ) -> BusinessContent: ...

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

    @staticmethod
    def _failed_content(
        subject_type: AuditSubjectType,
        failure_reason: str,
    ) -> BusinessContent:
        return BusinessContent(
            status="failed",
            subject_type=subject_type,
            failure_reason=failure_reason,
        )

    @staticmethod
    def _content_result(
        subject_type: AuditSubjectType,
        content: str,
        *,
        truncated: bool = False,
    ) -> BusinessContent:
        # 大文件已在读取层按块截断；这里只负责 hash 与包装，不再整读任何内容。
        raw = content.encode("utf-8")
        return BusinessContent(
            status="succeeded",
            subject_type=subject_type,
            content=content,
            content_sha256=hashlib.sha256(raw).hexdigest(),
            size_bytes=len(raw),
            truncated=truncated,
        )

    @staticmethod
    def _assert_audit_reader(actor: CatalogActor, task_owner_id: str) -> None:
        if not actor.is_admin and actor.owner_id != task_owner_id:
            raise PermissionError("不能读取其他用户的任务管理信息")

    def _upload_extension_types(
        self,
        task_owner_id: str,
        upload_ids: list[str],
    ) -> tuple[str, ...]:
        # 管理元数据只呈现可解析的来源类型；单个来源缺失不阻断整行投影。
        types: list[str] = []
        for upload_id in upload_ids:
            try:
                item = self._uploads.resolve(task_owner_id, str(upload_id))
            except Exception:
                continue
            # upload_id 是无扩展名的随机标识，类型来自原始文件名；兜底取存储路径。
            name = item.original_name or item.storage_path
            extension = Path(name).suffix.lstrip(".").lower()
            if extension and extension not in types:
                types.append(extension)
        return tuple(types)

    @staticmethod
    def _delivery_output_rows(
        connection: sqlite3.Connection,
        owner_id: str,
        run_id: str,
    ) -> list[sqlite3.Row]:
        for table, owner_column in (
            ("formal_delivery_outputs", "owner_id"),
            ("semantic_delivery_outputs", "user_id"),
        ):
            if not SqliteValidationTaskResolver._table_exists(connection, table):
                continue
            rows = connection.execute(
                f"SELECT file_path FROM {table} "
                f"WHERE {owner_column}=? AND run_id=? ORDER BY output_id",
                (owner_id, run_id),
            ).fetchall()
            if rows:
                return rows
        return []

    @staticmethod
    def _count_delivery_outputs(
        connection: sqlite3.Connection,
        owner_id: str,
        run_id: str,
    ) -> int:
        for table, owner_column in (
            ("formal_delivery_outputs", "owner_id"),
            ("semantic_delivery_outputs", "user_id"),
        ):
            if not SqliteValidationTaskResolver._table_exists(connection, table):
                continue
            row = connection.execute(
                f"SELECT COUNT(*) AS count FROM {table} "
                f"WHERE {owner_column}=? AND run_id=?",
                (owner_id, run_id),
            ).fetchone()
            if row["count"] > 0:
                return int(row["count"])
        return 0

    def read_task_metadata(
        self,
        actor: CatalogActor,
        task_id: str,
        revision: int,
        *,
        task_owner_id: str,
    ) -> CapabilityTaskMetadata:
        self._assert_audit_reader(actor, task_owner_id)
        with self._connect() as connection:
            task = connection.execute(
                "SELECT user_id, status, created_at, updated_at, "
                "upload_ids_json, output_formats_json "
                "FROM semantic_workspace_tasks "
                "WHERE user_id=? AND task_id=?",
                (task_owner_id, task_id),
            ).fetchone()
            frozen = connection.execute(
                "SELECT status, run_id FROM semantic_workspace_revisions "
                "WHERE user_id=? AND task_id=? AND revision=?",
                (task_owner_id, task_id, revision),
            ).fetchone()
            if task is None or frozen is None:
                raise KeyError("任务管理信息不存在")
            upload_ids = json.loads(task["upload_ids_json"] or "[]")
            output_count = (
                self._count_delivery_outputs(
                    connection, task_owner_id, str(frozen["run_id"] or "")
                )
                if frozen["run_id"]
                else 0
            )
            return CapabilityTaskMetadata(
                task_id=task_id,
                revision=revision,
                owner_id=task_owner_id,
                task_status=str(frozen["status"]),
                created_at=str(task["created_at"]),
                updated_at=str(task["updated_at"]),
                input_count=len(upload_ids),
                input_types=self._upload_extension_types(
                    task_owner_id, upload_ids
                ),
                output_count=output_count,
                output_formats=tuple(
                    json.loads(task["output_formats_json"] or "[]")
                ),
            )

    def read_business_content(
        self,
        actor: CatalogActor,
        task_id: str,
        revision: int,
        subject_type: AuditSubjectType,
        *,
        task_owner_id: str,
    ) -> BusinessContent:
        self._assert_audit_reader(actor, task_owner_id)
        if subject_type == "task_prompt":
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT objective_text FROM semantic_workspace_revisions "
                    "WHERE user_id=? AND task_id=? AND revision=?",
                    (task_owner_id, task_id, revision),
                ).fetchone()
            if row is None:
                return self._failed_content(subject_type, "task_not_found")
            return self._content_result(
                subject_type, str(row["objective_text"] or "")
            )
        if subject_type == "task_sources":
            with self._connect() as connection:
                task = connection.execute(
                    "SELECT upload_ids_json FROM semantic_workspace_tasks "
                    "WHERE user_id=? AND task_id=?",
                    (task_owner_id, task_id),
                ).fetchone()
            if task is None:
                return self._failed_content(subject_type, "task_not_found")
            upload_ids = json.loads(task["upload_ids_json"] or "[]")
            if not upload_ids:
                return self._failed_content(subject_type, "source_missing")
            safe_owner = "".join(
                character
                for character in task_owner_id
                if character.isalnum() or character in "-_"
            )
            owner_objects = (self._upload_root / safe_owner / "objects").resolve()
            # 不经过 UploadStore.resolve：其无 sidecar 兜底会整读文件，违反流式截断。
            parts: list[bytes] = []
            remaining = _BUSINESS_CONTENT_MAX_BYTES
            truncated = False
            for index, upload_id in enumerate(upload_ids):
                safe_id = "".join(
                    character
                    for character in str(upload_id)
                    if character.isalnum() or character in "-_"
                )
                if not safe_id or safe_id != str(upload_id):
                    return self._failed_content(subject_type, "source_missing")
                raw_path = owner_objects / str(upload_id)
                source_path = raw_path.resolve()
                if (
                    owner_objects not in source_path.parents
                    or not source_path.is_file()
                    or raw_path.is_symlink()
                ):
                    return self._failed_content(subject_type, "source_invalid")
                try:
                    content, file_truncated = _read_limited(
                        source_path, remaining
                    )
                except OSError:
                    return self._failed_content(subject_type, "source_unreadable")
                parts.append(content)
                remaining -= len(content)
                if file_truncated:
                    truncated = True
                    break
                if remaining <= 0:
                    # 预算耗尽：只有后面还有文件未读才算截断。
                    truncated = index < len(upload_ids) - 1
                    break
            content_bytes = b"\n".join(parts)
            return self._content_result(
                subject_type,
                content_bytes.decode("utf-8", errors="replace"),
                truncated=truncated,
            )
        with self._connect() as connection:
            frozen = connection.execute(
                "SELECT run_id FROM semantic_workspace_revisions "
                "WHERE user_id=? AND task_id=? AND revision=?",
                (task_owner_id, task_id, revision),
            ).fetchone()
            if frozen is None:
                return self._failed_content(subject_type, "task_not_found")
            outputs = self._delivery_output_rows(
                connection, task_owner_id, str(frozen["run_id"] or "")
            )
        if not outputs:
            return self._failed_content(subject_type, "output_missing")
        parts: list[bytes] = []
        remaining = _BUSINESS_CONTENT_MAX_BYTES
        truncated = False
        for index, row in enumerate(outputs):
            file_path = str(row["file_path"])
            output_path = Path(file_path).expanduser().resolve()
            if (
                self._execution_root not in output_path.parents
                or not output_path.is_file()
                or Path(file_path).is_symlink()
            ):
                return self._failed_content(subject_type, "output_invalid")
            try:
                content, file_truncated = _read_limited(output_path, remaining)
            except OSError:
                return self._failed_content(subject_type, "output_unreadable")
            parts.append(content)
            remaining -= len(content)
            if file_truncated:
                truncated = True
                break
            if remaining <= 0:
                # 预算耗尽：只有后面还有文件未读才算截断。
                truncated = index < len(outputs) - 1
                break
        content_bytes = b"\n".join(parts)
        return self._content_result(
            subject_type,
            content_bytes.decode("utf-8", errors="replace"),
            truncated=truncated,
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
