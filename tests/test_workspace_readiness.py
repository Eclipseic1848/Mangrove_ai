# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from src.api.readiness import collect_workspace_readiness
from src.api.semantic_workspace_runtime import SemanticWorkspaceManager
from src.api.store import WebUIStore
from tests.database_migration_helpers import migrated_webui_database


def _store(tmp_path: Path) -> WebUIStore:
    return WebUIStore(str(migrated_webui_database(tmp_path / "webui.db")))


def _manager_with_workers(*, exited_worker: bool = False) -> SemanticWorkspaceManager:
    manager = SemanticWorkspaceManager.__new__(SemanticWorkspaceManager)
    loop = asyncio.new_event_loop()
    workers: list[asyncio.Future[None]] = [loop.create_future(), loop.create_future()]
    if exited_worker:
        workers[1].set_result(None)
    manager._workers = workers  # type: ignore[attr-defined]
    manager._readiness_test_loop = loop  # type: ignore[attr-defined]
    return manager


def _close_manager(manager: SemanticWorkspaceManager) -> None:
    for worker in manager._workers:  # type: ignore[attr-defined]
        if not worker.done():
            worker.cancel()
    manager._readiness_test_loop.close()  # type: ignore[attr-defined]


def _check_map(report: object) -> dict[str, object]:
    return {check.check_id: check for check in report.checks}  # type: ignore[attr-defined]


def test_workspace_readiness_passes_with_real_sqlite_workers_and_roots(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    manager = _manager_with_workers()
    roots = [tmp_path / name for name in ("uploads", "executions", "artifacts")]
    for root in roots:
        root.mkdir()

    try:
        report = collect_workspace_readiness(
            store=store,
            manager=manager,
            upload_root=roots[0],
            execution_root=roots[1],
            artifact_root=roots[2],
        )
    finally:
        _close_manager(manager)

    assert report.ready is True
    assert [check.check_id for check in report.checks] == [
        "CORE-API-001",
        "CORE-DB-001",
        "CORE-WORKER-001",
        "CORE-UPLOAD-001",
        "CORE-EXEC-001",
        "CORE-ARTIFACT-001",
    ]
    assert {check.status for check in report.checks} == {"passed"}
    assert all(list(root.iterdir()) == [] for root in roots)


def test_workspace_readiness_fails_closed_when_worker_exits(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manager = _manager_with_workers(exited_worker=True)
    roots = [tmp_path / name for name in ("uploads", "executions", "artifacts")]
    for root in roots:
        root.mkdir()

    try:
        report = collect_workspace_readiness(
            store=store,
            manager=manager,
            upload_root=roots[0],
            execution_root=roots[1],
            artifact_root=roots[2],
        )
    finally:
        _close_manager(manager)

    checks = _check_map(report)
    assert report.ready is False
    assert checks["CORE-WORKER-001"].status == "failed"


def test_workspace_readiness_fails_without_creating_missing_root_or_leaking_path(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    manager = _manager_with_workers()
    upload_root = tmp_path / "private-user-name" / "uploads"
    execution_root = tmp_path / "executions"
    artifact_root = tmp_path / "artifacts"
    execution_root.mkdir()
    artifact_root.mkdir()

    try:
        report = collect_workspace_readiness(
            store=store,
            manager=manager,
            upload_root=upload_root,
            execution_root=execution_root,
            artifact_root=artifact_root,
        )
    finally:
        _close_manager(manager)

    checks = _check_map(report)
    summaries = " ".join(check.summary for check in report.checks)
    assert report.ready is False
    assert checks["CORE-UPLOAD-001"].status == "failed"
    assert not upload_root.exists()
    assert str(tmp_path) not in summaries
    assert "private-user-name" not in summaries


def test_api_readiness_returns_200_then_503_without_sensitive_details(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from src.api import auth, semantic_workspace_runtime
    from src.api.main import app
    from src.config.settings import settings

    store = _store(tmp_path)
    manager = _manager_with_workers()
    roots = [tmp_path / name for name in ("uploads", "executions", "artifacts")]
    for root in roots:
        root.mkdir()
    monkeypatch.setattr(auth, "_store", store)
    monkeypatch.setattr(semantic_workspace_runtime, "_manager", manager)
    monkeypatch.setattr(settings, "data_prep_upload_root", str(roots[0]))
    monkeypatch.setattr(settings, "semantic_execution_root", str(roots[1]))
    monkeypatch.setattr(
        settings,
        "data_prep_artifact_root",
        str(roots[2]),
        raising=False,
    )

    try:
        client = TestClient(app)
        ready_response = client.get("/api/readiness")
        manager._workers[1].set_result(None)  # type: ignore[attr-defined]
        failed_response = client.get("/api/readiness")
    finally:
        _close_manager(manager)

    assert ready_response.status_code == 200
    assert ready_response.json()["ready"] is True
    assert failed_response.status_code == 503
    payload = failed_response.json()
    assert payload["ready"] is False
    serialized = failed_response.text
    assert str(tmp_path) not in serialized
    assert "username" not in serialized
    assert "queue" not in serialized
