# -*- coding: utf-8 -*-
"""Phase 3 收口回归：验证真实数据内容，而非仅验证“没有异常”。"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.auth as auth_mod
import src.data_prep.artifact_store as artifact_mod
from src.api.auth import get_current_user
from src.api.routes import data_sources, data_tasks, downloads
from src.config.settings import settings
from src.connectors.database_connector import DatabaseConnector
from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.models import SourceLimits, SourceSpec, SourceType


def _database(path: Path, ddl: str, rows: list[tuple]) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(ddl)
        if rows:
            placeholders = ",".join("?" for _ in rows[0])
            conn.executemany(f"INSERT INTO t VALUES ({placeholders})", rows)


def _spec(path: Path, task_id: str, **options) -> SourceSpec:
    return SourceSpec(
        source_id="db", source_type=SourceType.DATABASE, locator="dbconn://test",
        options={"mode": "table", "table": "t", "sqlite_db_path": str(path),
                 "task_id": task_id, **options},
    )


async def _read_rows(connector: DatabaseConnector, spec: SourceSpec):
    rows = []
    batches = []
    async for batch in connector.read(spec):
        batches.append(batch)
        for artifact in batch.artifacts:
            raw = connector.artifact_store.read_raw_bytes(spec.options["task_id"], artifact.storage_path)
            rows.extend(json.loads(line) for line in raw.decode("utf-8").splitlines() if line)
    return rows, batches


def test_field_projection_keeps_keyset_across_batches(tmp_path: Path):
    db = tmp_path / "field.db"
    _database(db, "CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT)",
              [(i, f"n{i}") for i in range(1, 9)])
    connector = DatabaseConnector(ArtifactStore(str(tmp_path / "out")))
    rows, _ = asyncio.run(_read_rows(connector, _spec(db, "field", fields=["name"], batch_size=3)))
    assert rows == [{"name": f"n{i}"} for i in range(1, 9)]


def test_incremental_last_value_is_applied(tmp_path: Path):
    db = tmp_path / "incremental.db"
    _database(db, "CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT)",
              [(i, f"n{i}") for i in range(1, 11)])
    connector = DatabaseConnector(ArtifactStore(str(tmp_path / "out")))
    spec = _spec(db, "incremental", batch_size=2,
                 incremental={"strategy": "watermark", "cursor_field": "id", "last_value": 5})
    rows, _ = asyncio.run(_read_rows(connector, spec))
    assert [row["id"] for row in rows] == [6, 7, 8, 9, 10]


def test_composite_keyset_has_no_gap_or_duplicate(tmp_path: Path):
    db = tmp_path / "composite.db"
    rows_in = [("a", 1, "a1"), ("a", 2, "a2"), ("a", 3, "a3"),
               ("b", 1, "b1"), ("b", 2, "b2")]
    _database(db, "CREATE TABLE t(host TEXT, seq INTEGER, value TEXT, PRIMARY KEY(host, seq))", rows_in)
    connector = DatabaseConnector(ArtifactStore(str(tmp_path / "out")))
    rows, _ = asyncio.run(_read_rows(connector, _spec(db, "composite", batch_size=2)))
    assert [(r["host"], r["seq"]) for r in rows] == [(r[0], r[1]) for r in rows_in]


def test_no_primary_key_uses_bounded_offset_batches(tmp_path: Path):
    db = tmp_path / "no-pk.db"
    _database(db, "CREATE TABLE t(id INTEGER, name TEXT)", [(i, f"n{i}") for i in range(7)])
    connector = DatabaseConnector(ArtifactStore(str(tmp_path / "out")))
    rows, batches = asyncio.run(_read_rows(connector, _spec(db, "no-pk", batch_size=3)))
    assert [row["id"] for row in rows] == list(range(7))
    assert any("无主键" in warning for batch in batches for warning in batch.warnings)


def test_source_limits_truncate_on_record_boundary(tmp_path: Path):
    db = tmp_path / "limits.db"
    _database(db, "CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT)",
              [(i, "x" * 20) for i in range(1, 11)])
    connector = DatabaseConnector(ArtifactStore(str(tmp_path / "out")))
    spec = _spec(db, "limits", batch_size=3)
    spec.limits = SourceLimits(max_records=4)
    rows, batches = asyncio.run(_read_rows(connector, spec))
    assert len(rows) == 4
    assert batches[-1].checkpoint.is_final is True


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    db_root = tmp_path / "db_sources"
    db_root.mkdir()
    monkeypatch.setattr(settings, "data_prep_db_sqlite_root", str(db_root))
    monkeypatch.setattr(settings, "webui_db_path", str(tmp_path / "webui.db"))
    monkeypatch.setattr(artifact_mod, "_DEFAULT_ROOT", str(tmp_path / "downloads"))
    auth_mod._store = None
    app = FastAPI()
    app.include_router(data_sources.router)
    app.include_router(data_tasks.router)
    app.include_router(downloads.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "phase3-user"}
    return TestClient(app)


def test_database_api_preview_pipeline_and_secret_hygiene(tmp_path: Path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    source_db = Path(settings.data_prep_db_sqlite_root) / "orders.db"
    _database(source_db, "CREATE TABLE t(id INTEGER PRIMARY KEY, name TEXT)",
              [(1, "Alice"), (2, "Bob"), (3, "Carol")])

    created = client.post("/api/data-sources/connections", json={
        "name": "orders", "dialect": "sqlite", "sqlite_relpath": "orders.db",
        "password": "phase3-canary-password",
    })
    assert created.status_code == 200, created.text
    connection_id = created.json()["connection_id"]
    assert "password" not in created.text.lower()

    tested = client.post("/api/data-sources/connections/test", json={"connection_id": connection_id})
    assert tested.status_code == 200 and tested.json()["reachable"] is True, tested.text
    schema = client.get(f"/api/data-sources/connections/{connection_id}/schema")
    assert schema.status_code == 200, schema.text
    assert schema.json()["tables"][0]["primary_key"] == ["id"]

    source = {"source_type": "database", "connection_id": connection_id,
              "table": "t", "fields": ["name"]}
    preview = client.post("/api/data-tasks/preview", json={"source": source, "sample_records": 2})
    assert preview.status_code == 200, preview.text
    assert [row["name"] for row in preview.json()["sample"]] == ["Alice", "Bob"]

    task = client.post("/api/data-tasks", json={"source": source, "outputs": ["jsonl"]})
    assert task.status_code == 200, task.text
    body = task.json()
    assert body["status"] in {"SUCCEEDED", "SUCCEEDED_WITH_WARNINGS"}, body
    manifest = client.get(f"/api/data-tasks/{body['task_id']}/manifest")
    assert manifest.status_code == 200
    all_bytes = b"".join(path.read_bytes() for path in (tmp_path / "downloads").rglob("*") if path.is_file())
    assert b"phase3-canary-password" not in all_bytes
