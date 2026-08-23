# -*- coding: utf-8 -*-
"""判断当前 Web 进程是否具备安全接单条件。"""
from __future__ import annotations

import os
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .semantic_workspace_runtime import SemanticWorkspaceManager
from .store import WebUIStore


@dataclass(frozen=True)
class ReadinessCheck:
    check_id: str
    status: Literal["passed", "failed"]
    summary: str


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    checks: tuple[ReadinessCheck, ...]


def _check_database(store: WebUIStore) -> ReadinessCheck:
    try:
        # 只读两个接单必需表，不返回用户或队列内容。
        store.count_users()
        store.list_pending_semantic_workspace_tasks()
    except Exception:
        return ReadinessCheck("CORE-DB-001", "failed", "database_unavailable")
    return ReadinessCheck("CORE-DB-001", "passed", "database_readable")


def _check_workers(manager: SemanticWorkspaceManager) -> ReadinessCheck:
    try:
        ready = manager.workers_ready()
    except Exception:
        ready = False
    return ReadinessCheck(
        "CORE-WORKER-001",
        "passed" if ready else "failed",
        "workspace_workers_running" if ready else "workspace_workers_unavailable",
    )


def _check_writable_root(
    check_id: str,
    root: Path,
    summary_prefix: str,
) -> ReadinessCheck:
    probe_path: Path | None = None
    try:
        if not root.is_dir():
            raise OSError("root_unavailable")
        probe_path = root / f".mangrove-readiness-{uuid.uuid4().hex}"
        descriptor = os.open(
            probe_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(b"mangrove-readiness")
            handle.flush()
            os.fsync(handle.fileno())
        probe_path.unlink()
    except Exception:
        if probe_path is not None:
            with suppress(OSError):
                probe_path.unlink()
        return ReadinessCheck(check_id, "failed", f"{summary_prefix}_unavailable")
    return ReadinessCheck(check_id, "passed", f"{summary_prefix}_writable")


def collect_workspace_readiness(
    *,
    store: WebUIStore,
    manager: SemanticWorkspaceManager,
    upload_root: Path,
    execution_root: Path,
    artifact_root: Path,
) -> ReadinessReport:
    """收集低敏核心就绪结果，不检查模型或运行真实任务。"""

    checks = (
        ReadinessCheck("CORE-API-001", "passed", "api_responding"),
        _check_database(store),
        _check_workers(manager),
        _check_writable_root("CORE-UPLOAD-001", upload_root, "upload_root"),
        _check_writable_root("CORE-EXEC-001", execution_root, "execution_root"),
        _check_writable_root("CORE-ARTIFACT-001", artifact_root, "artifact_root"),
    )
    return ReadinessReport(
        ready=all(check.status == "passed" for check in checks),
        checks=checks,
    )
