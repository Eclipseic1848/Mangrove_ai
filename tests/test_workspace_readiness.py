# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from src.api import readiness as readiness_module
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


@pytest.fixture
def readiness_clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(readiness_module, "time", SimpleNamespace(monotonic=lambda: now[0]), raising=False)
    monkeypatch.setattr(readiness_module, "_resource_cache", None, raising=False)
    return now


@pytest.fixture
def readiness_resources(tmp_path):
    store = _store(tmp_path)
    manager = _manager_with_workers()
    roots = [tmp_path / name for name in ("uploads", "executions", "artifacts")]
    for root in roots:
        root.mkdir()
    try:
        yield store, manager, roots
    finally:
        _close_manager(manager)


def _collect(store, manager, roots):
    return collect_workspace_readiness(
        store=store, manager=manager,
        upload_root=roots[0], execution_root=roots[1], artifact_root=roots[2],
    )


def test_readiness_concurrent_requests_share_probes_until_completed_ttl(tmp_path, monkeypatch, readiness_clock):
    store = _store(tmp_path)
    manager = _manager_with_workers()
    roots = [tmp_path / name for name in ("uploads", "executions", "artifacts")]
    for root in roots:
        root.mkdir()
    counts = {"database": 0, "fsync": 0}
    counter_lock = threading.Lock()
    check_database = readiness_module._check_database
    fsync = readiness_module.os.fsync

    def count_database(value):
        with counter_lock:
            counts["database"] += 1
            readiness_clock[0] += 2.0
        return check_database(value)

    def count_fsync(descriptor):
        fsync(descriptor)
        with counter_lock:
            counts["fsync"] += 1
            readiness_clock[0] += 1.0

    monkeypatch.setattr(readiness_module, "_check_database", count_database)
    monkeypatch.setattr(readiness_module.os, "fsync", count_fsync)

    def concurrent_round():
        barrier = threading.Barrier(8)

        def collect(_):
            barrier.wait(timeout=5)
            return _collect(store, manager, roots)

        with ThreadPoolExecutor(max_workers=8) as pool:
            return list(pool.map(collect, range(8)))

    try:
        assert all(report.ready for report in concurrent_round())
        assert counts == {"database": 1, "fsync": 3}
        # 探测耗时共五秒，TTL 从完成时刻起算，期间请求不能再写探针文件。
        readiness_clock[0] = 109.999
        assert _collect(store, manager, roots).ready
        assert counts == {"database": 1, "fsync": 3}
        readiness_clock[0] = 110.0
        assert all(report.ready for report in concurrent_round())
        assert counts == {"database": 2, "fsync": 6}
        assert all(list(root.iterdir()) == [] for root in roots)
    finally:
        _close_manager(manager)


def test_readiness_uses_one_connection_without_reading_sensitive_columns(tmp_path, monkeypatch, readiness_clock):
    store = _store(tmp_path)
    manager = _manager_with_workers()
    roots = [tmp_path / name for name in ("uploads", "executions", "artifacts")]
    for root in roots:
        root.mkdir()
    opened = []
    reads = []
    connection = store._conn

    def authorize(action, table, column, _database, _trigger):
        if action == sqlite3.SQLITE_READ:
            reads.append((table, column))
            if column:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    @contextmanager
    def scoped_connection():
        with connection() as conn:
            opened.append(conn)
            conn.set_authorizer(authorize)
            yield conn

    monkeypatch.setattr(store, "_conn", scoped_connection)
    try:
        report = _collect(store, manager, roots)
    finally:
        _close_manager(manager)

    assert report.ready
    assert len(opened) == 1
    assert set(reads) == {("users", ""), ("semantic_workspace_tasks", "")}


@pytest.mark.parametrize("table", ["users", "semantic_workspace_tasks"])
def test_readiness_fails_if_either_required_table_is_missing(readiness_resources, readiness_clock, table):
    store, manager, roots = readiness_resources
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(f'DROP TABLE "{table}"')

    report = _collect(store, manager, roots)

    assert not report.ready
    assert _check_map(report)["CORE-DB-001"].status == "failed"


@pytest.mark.parametrize("changed", ["store", "upload", "execution", "artifact"])
def test_readiness_cache_does_not_cross_store_or_roots(readiness_resources, readiness_clock, monkeypatch, changed):
    store, manager, roots = readiness_resources
    assert _collect(store, manager, roots).ready
    selected_store = store
    selected_roots = list(roots)
    check_id = "CORE-DB-001"
    if changed == "store":
        # 相同数据库路径的另一个 Store 仍须独立探测，不能复用旧实例成功结果。
        selected_store = WebUIStore(store.db_path)

        def unavailable():
            raise sqlite3.OperationalError("synthetic unavailable store")

        monkeypatch.setattr(selected_store, "_conn", unavailable)
    else:
        index = ["upload", "execution", "artifact"].index(changed)
        selected_roots[index] = roots[index] / "missing-root"
        check_id = ["CORE-UPLOAD-001", "CORE-EXEC-001", "CORE-ARTIFACT-001"][index]

    report = _collect(selected_store, manager, selected_roots)

    assert not report.ready
    assert _check_map(report)[check_id].status == "failed"
    assert _collect(store, manager, roots).ready


def test_api_readiness_expires_database_success_and_caches_failure_until_recovery(
    readiness_resources, readiness_clock, monkeypatch,
):
    from src.api import auth, semantic_workspace_runtime
    from src.api.main import app
    from src.config.settings import settings

    store, manager, roots = readiness_resources
    monkeypatch.setattr(auth, "_store", store)
    monkeypatch.setattr(semantic_workspace_runtime, "_manager", manager)
    monkeypatch.setattr(settings, "data_prep_upload_root", str(roots[0]))
    monkeypatch.setattr(settings, "semantic_execution_root", str(roots[1]))
    monkeypatch.setattr(settings, "data_prep_artifact_root", str(roots[2]))
    connection = store._conn
    unavailable = [False]
    opened = []

    @contextmanager
    def controlled_connection():
        opened.append(None)
        if unavailable[0]:
            raise sqlite3.OperationalError(str(roots[0] / "private-database"))
        with connection() as conn:
            yield conn

    monkeypatch.setattr(store, "_conn", controlled_connection)
    client = TestClient(app)
    try:
        assert client.get("/api/readiness").status_code == 200
        unavailable[0] = True
        readiness_clock[0] = 104.999
        assert client.get("/api/readiness").status_code == 200
        assert len(opened) == 1
        readiness_clock[0] = 105.0
        failed = client.get("/api/readiness")
        assert failed.status_code == 503
        assert failed.headers["cache-control"] == "no-store"
        assert {item["check_id"]: item["status"] for item in failed.json()["checks"]}["CORE-DB-001"] == "failed"
        assert "private-database" not in failed.text
        assert len(opened) == 2
        unavailable[0] = False
        readiness_clock[0] = 109.999
        assert client.get("/api/readiness").status_code == 503
        assert len(opened) == 2
        readiness_clock[0] = 110.0
        recovered = client.get("/api/readiness")
        assert recovered.status_code == 200
        assert recovered.headers["cache-control"] == "no-store"
        assert len(opened) == 3
        manager._workers[1].set_result(None)
        assert client.get("/api/readiness").status_code == 503
        assert len(opened) == 3
    finally:
        client.close()


def test_health_does_not_probe_database_or_disk(monkeypatch):
    from src.api import auth
    from src.api.main import app

    def unexpected(*args, **kwargs):
        raise AssertionError("存活探针不应访问数据库或磁盘就绪探针")

    monkeypatch.setattr(auth, "get_store", unexpected)
    monkeypatch.setattr(readiness_module, "collect_workspace_readiness", unexpected)
    monkeypatch.setattr(readiness_module.os, "fsync", unexpected)
    client = TestClient(app)
    try:
        response = client.get("/api/health")
    finally:
        client.close()

    assert response.status_code == 200
    assert response.json() == {"ok": True, "service": "mangrove-webui"}


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
