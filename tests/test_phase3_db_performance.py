# -*- coding: utf-8 -*-
"""Phase 3 SQLite 十万行读取性能门禁。"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

import pytest

from src.connectors.database_connector import DatabaseConnector
from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.models import SourceSpec, SourceType


@pytest.mark.performance
@pytest.mark.timeout(70)
def test_sqlite_100k_rows_under_60_seconds(tmp_path: Path):
    db = tmp_path / "performance.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, value TEXT)")
        conn.executemany("INSERT INTO t VALUES (?, ?)", ((i, f"v{i}") for i in range(100_000)))
    spec = SourceSpec(
        source_id="db", source_type=SourceType.DATABASE, locator="dbconn://performance",
        options={"mode": "table", "table": "t", "sqlite_db_path": str(db),
                 "task_id": "phase3-performance", "batch_size": 5_000},
    )
    connector = DatabaseConnector(ArtifactStore(str(tmp_path / "out")))

    async def run():
        count = 0
        async for batch in connector.read(spec):
            for artifact in batch.artifacts:
                count += connector.artifact_store.read_raw_bytes(
                    spec.options["task_id"], artifact.storage_path,
                ).count(b"\n")
        return count

    started = time.perf_counter()
    assert asyncio.run(run()) == 100_000
    assert time.perf_counter() - started < 60
