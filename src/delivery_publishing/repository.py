# -*- coding: utf-8 -*-
"""PublishIntent 与通用正式 Delivery 的 SQLite 仓库。"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any

from src.semantic_harness.delivery.models import DeliveryManifest
from src.database_migrations import DatabaseTarget, inspect_database
from src.services.managed_paths import ManagedPathCodec

from .models import PublishCommand


_LOCK = RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeliveryPublishingRepository:
    """历史 Delivery 保持只读；新表直接引用 vNext 身份。"""

    def __init__(
        self,
        db_path: str | Path,
        *,
        semantic_paths: ManagedPathCodec | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.semantic_paths = semantic_paths
        inspect_database(
            DatabaseTarget(profile="webui", path=self.db_path)
        ).require_current()

    def _persist_output_path(self, path: Path) -> str:
        if self.semantic_paths is None:
            # 仅兼容独立评测和旧单测；生产发布必须从调用方注入 execution root codec。
            return str(path.resolve())
        return self.semantic_paths.encode(path)

    def _resolve_output_path(self, value: str) -> str:
        if self.semantic_paths is None:
            return value
        # 旧绝对路径也只由 codec 按冻结锚点迁到当前根，禁止直接信任宿主路径。
        return str(self.semantic_paths.decode(value))

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn


    def claim_intent(
        self,
        command: PublishCommand,
        *,
        staging_dir: Path,
        final_dir: Path,
    ) -> dict[str, Any]:
        now = _now()
        command_hash = command.frozen_hash()
        with _LOCK, self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO delivery_publish_intents (
                    publication_key, command_hash, request_idempotency_hash,
                    owner_id, task_id,
                    task_revision, run_id, status, staging_dir, final_dir,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'staging', ?, ?, ?, ?)
                """,
                (
                    command.publication_key,
                    command_hash,
                    command.request_idempotency_hash,
                    command.owner_id,
                    command.task_id,
                    command.task_revision,
                    command.run_id,
                    str(staging_dir.resolve()),
                    str(final_dir.resolve()),
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM delivery_publish_intents WHERE publication_key=?",
                (command.publication_key,),
            ).fetchone()
            if row is None and command.request_idempotency_hash is not None:
                bound = conn.execute(
                    "SELECT publication_key FROM delivery_publish_intents "
                    "WHERE owner_id=? AND request_idempotency_hash=?",
                    (
                        command.owner_id,
                        command.request_idempotency_hash,
                    ),
                ).fetchone()
                if bound is not None:
                    raise ValueError("幂等键已绑定其他发布请求")
        if row is None:
            raise RuntimeError("发布意图创建失败")
        if row["command_hash"] != command_hash:
            raise ValueError("发布幂等键已用于不同冻结输入")
        return dict(row)

    def get_intent(self, publication_key: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM delivery_publish_intents WHERE publication_key=?",
                (publication_key,),
            ).fetchone()
        return dict(row) if row is not None else None

    def set_intent_status(
        self,
        publication_key: str,
        status: str,
        *,
        commit_token: str | None = None,
        manifest: DeliveryManifest | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        with _LOCK, self._conn() as conn:
            cursor = conn.execute(
                """
                UPDATE delivery_publish_intents
                SET status=?, commit_token=COALESCE(?, commit_token),
                    manifest_json=COALESCE(?, manifest_json), error_json=?,
                    updated_at=?
                WHERE publication_key=?
                """,
                (
                    status,
                    commit_token,
                    manifest.model_dump_json() if manifest else None,
                    json.dumps(error, ensure_ascii=False) if error else None,
                    _now(),
                    publication_key,
                ),
            )
        if cursor.rowcount != 1:
            raise KeyError("发布意图不存在")

    def begin_commit(
        self,
        command: PublishCommand,
        *,
        commit_token: str,
        manifest: DeliveryManifest,
    ) -> None:
        """在同一数据库写事务内冻结显式发布的最后业务 CAS。"""

        with _LOCK, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if command.verification_attempt_id is not None:
                task = conn.execute(
                    "SELECT active_revision, cancel_requested "
                    "FROM semantic_workspace_tasks "
                    "WHERE user_id=? AND task_id=?",
                    (command.owner_id, command.task_id),
                ).fetchone()
                if (
                    task is None
                    or int(task["active_revision"]) != command.task_revision
                ):
                    raise ValueError("活动版本已变化")
                if bool(task["cancel_requested"]):
                    raise ValueError("任务已取消")

                routing_table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='runtime_rollout_state'"
                ).fetchone()
                if routing_table is not None:
                    routing = conn.execute(
                        "SELECT p0_blocked FROM runtime_rollout_state "
                        "WHERE state_id=1"
                    ).fetchone()
                    if routing is None or bool(routing["p0_blocked"]):
                        raise ValueError("P0 发布门已阻断")

                attempt = conn.execute(
                    "SELECT status, report_hash, candidate_set_hash "
                    "FROM candidate_verification_attempts "
                    "WHERE owner_id=? AND attempt_id=? AND task_id=? "
                    "AND revision=? AND run_id=?",
                    (
                        command.owner_id,
                        command.verification_attempt_id,
                        command.task_id,
                        command.task_revision,
                        command.run_id,
                    ),
                ).fetchone()
                if (
                    attempt is None
                    or attempt["status"] != "passed"
                    or attempt["report_hash"]
                    != command.verification_report_hash
                    or attempt["candidate_set_hash"]
                    != command.candidate_set_hash
                ):
                    raise ValueError("候选验证 Attempt 冻结身份已变化")
                latest = conn.execute(
                    "SELECT attempt_id FROM candidate_verification_attempts "
                    "WHERE owner_id=? AND task_id=? AND revision=? "
                    "AND run_id=? AND candidate_set_hash=? "
                    "ORDER BY created_at DESC, attempt_id DESC LIMIT 1",
                    (
                        command.owner_id,
                        command.task_id,
                        command.task_revision,
                        command.run_id,
                        command.candidate_set_hash,
                    ),
                ).fetchone()
                if (
                    latest is None
                    or latest["attempt_id"]
                    != command.verification_attempt_id
                ):
                    raise ValueError("候选验证 Attempt 已不是当前精确结果")

            updated = conn.execute(
                "UPDATE delivery_publish_intents "
                "SET status='committing', commit_token=?, manifest_json=?, "
                "error_json=NULL, updated_at=? "
                "WHERE publication_key=? AND command_hash=? "
                "AND status!='published'",
                (
                    commit_token,
                    manifest.model_dump_json(),
                    _now(),
                    command.publication_key,
                    command.frozen_hash(),
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("发布意图已变化，无法进入提交点")
            conn.commit()

    def commit_delivery(
        self,
        command: PublishCommand,
        manifest: DeliveryManifest,
        output_dir: Path,
    ) -> DeliveryManifest:
        payload = manifest.model_dump(mode="json")
        now = _now()
        with _LOCK, self._conn() as conn:
            existing = conn.execute(
                "SELECT manifest_json FROM formal_delivery_runs "
                "WHERE publication_key=?",
                (command.publication_key,),
            ).fetchone()
            if existing is not None:
                return DeliveryManifest.model_validate_json(existing["manifest_json"])
            conn.execute(
                """
                INSERT INTO formal_delivery_runs (
                    delivery_id, publication_key, run_id, owner_id, task_id,
                    task_revision, candidate_set_hash, verification_report_id,
                    verification_report_hash, delivery_spec_hash, status,
                    manifest_json, output_dir, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    manifest.delivery_id,
                    command.publication_key,
                    command.run_id,
                    command.owner_id,
                    command.task_id,
                    command.task_revision,
                    command.candidate_set_hash,
                    command.verification_report_id,
                    command.verification_report_hash,
                    command.delivery_spec_hash,
                    manifest.status.value,
                    manifest.model_dump_json(),
                    self._persist_output_path(output_dir),
                    now,
                ),
            )
            for output in manifest.outputs:
                path = (output_dir / output.filename).resolve()
                conn.execute(
                    """
                    INSERT INTO formal_delivery_outputs (
                        output_id, delivery_id, run_id, owner_id, format,
                        filename, media_type, sha256, size_bytes, file_path,
                        qa_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        output.output_id,
                        manifest.delivery_id,
                        command.run_id,
                        command.owner_id,
                        output.format.value,
                        output.filename,
                        output.media_type,
                        output.sha256,
                        output.size_bytes,
                        self._persist_output_path(path),
                        output.qa.model_dump_json(),
                        now,
                    ),
                )
            conn.execute(
                """
                UPDATE delivery_publish_intents
                SET status='published', delivery_id=?, manifest_json=?,
                    updated_at=? WHERE publication_key=?
                """,
                (
                    manifest.delivery_id,
                    manifest.model_dump_json(),
                    now,
                    command.publication_key,
                ),
            )
        return manifest

    def get_delivery(
        self,
        owner_id: str,
        delivery_id: str,
    ) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT manifest_json FROM formal_delivery_runs "
                "WHERE owner_id=? AND delivery_id=? AND status='succeeded'",
                (owner_id, delivery_id),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["manifest_json"])
        payload.pop("user_id", None)
        return payload

    def latest_delivery(
        self,
        owner_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT manifest_json FROM formal_delivery_runs "
                "WHERE owner_id=? AND run_id=? AND status='succeeded' "
                "ORDER BY created_at DESC LIMIT 1",
                (owner_id, run_id),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["manifest_json"])
        payload.pop("user_id", None)
        return payload

    def get_output(
        self,
        owner_id: str,
        output_id: str,
    ) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM formal_delivery_outputs "
                "WHERE owner_id=? AND output_id=?",
                (owner_id, output_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "output_id": row["output_id"],
            "delivery_id": row["delivery_id"],
            "run_id": row["run_id"],
            "format": row["format"],
            "filename": row["filename"],
            "media_type": row["media_type"],
            "sha256": row["sha256"],
            "size_bytes": row["size_bytes"],
            "file_path": self._resolve_output_path(row["file_path"]),
            "qa": json.loads(row["qa_json"]),
            "created_at": row["created_at"],
        }
