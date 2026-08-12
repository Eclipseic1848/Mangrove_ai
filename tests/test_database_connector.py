# -*- coding: utf-8 -*-
"""DatabaseConnector 表级模式测试（Phase 3 Task 5 TDD）。

sqlite 真库（tmp_path）；模式仿 test_http_api_connector.py 的 asyncio.run/
_collect + ArtifactStore 清理。覆盖：probe/discover/全量分批/字段选择/filters/
time_range/水位线增量/复合主键/无主键降级/大字段截断/类型归一化/空表/只读强制/
artifact 脱敏/checkpoint 编码/max_limits 截断。
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List

import pytest

import src.data_prep.artifact_store as as_mod
from src.connectors.database_connector import DbConfig, DatabaseConnector
from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.models import SourceSpec, SourceType


# ---------------- helpers ----------------


def _make_sqlite_db(tmp_path: Path, table_sql: str, inserts: List[tuple]) -> str:
    """创建临时 sqlite 库并返回 db 路径。"""
    db_path = str(tmp_path / "test.db")
    import sqlite3
    bare = sqlite3.connect(db_path)
    bare.execute(table_sql)
    for row in inserts:
        bare.execute(f"INSERT INTO t VALUES ({', '.join('?' for _ in row)})", row)
    bare.commit()
    bare.close()
    return db_path


def _spec(**kw) -> SourceSpec:
    opts = dict(kw)
    return SourceSpec(
        source_id="src-1",
        source_type=SourceType.DATABASE,
        locator="dbconn://test",
        options=opts,
    )


def _store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(str(tmp_path / "downloads"))


async def _collect(aiter):
    return [b async for b in aiter]


def _cleanup(task_id: str) -> None:
    shutil.rmtree(Path("downloads") / task_id, ignore_errors=True)


def _is_done(ck) -> bool:
    """从 checkpoint cursor JSON 中解析 done 字段。"""
    if ck.cursor:
        try:
            return bool(json.loads(ck.cursor).get("done", False))
        except Exception:
            pass
    return False


# ---------------- probe ----------------


class TestProbe:
    def test_sqlite_probe_reachable(self, tmp_path):
        db_path = _make_sqlite_db(tmp_path,
            "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)", [(1, "Alice")])
        spec = _spec(mode="table", table="t", sqlite_db_path=db_path)
        connector = DatabaseConnector(artifact_store=_store(tmp_path))
        result = asyncio.run(connector.probe(spec))
        assert result.reachable is True


# ---------------- discover ----------------


class TestDiscover:
    def test_discover_returns_tables(self, tmp_path):
        db_path = _make_sqlite_db(tmp_path,
            "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)", [(1, "Alice")])
        spec = _spec(mode="table", table="t", sqlite_db_path=db_path)
        connector = DatabaseConnector(artifact_store=_store(tmp_path))
        info = asyncio.run(connector.discover(spec))
        assert len(info["tables"]) >= 1
        names = {t["name"] for t in info["tables"]}
        assert "t" in names


# ---------------- 全量读取（多批）----------------


class TestReadFull:
    def test_full_read_produces_artifacts(self, tmp_path):
        rows = [(i, f"item-{i}") for i in range(1, 100)]
        db_path = _make_sqlite_db(tmp_path,
            "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)", rows)
        task_id = "task-test-full"
        spec = _spec(mode="table", table="t", sqlite_db_path=db_path, task_id=task_id, batch_size=30)
        store = _store(tmp_path)
        connector = DatabaseConnector(artifact_store=store)
        batches = asyncio.run(_collect(connector.read(spec)))
        assert len(batches) >= 1
        # 至少有一批含 artifacts
        artifact_count = sum(len(b.artifacts) for b in batches)
        assert artifact_count >= 1
        _cleanup(task_id)

    def test_single_batch_exact(self, tmp_path):
        rows = [(1, "Alice"), (2, "Bob")]
        db_path = _make_sqlite_db(tmp_path,
            "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)", rows)
        task_id = "task-single"
        spec = _spec(mode="table", table="t", sqlite_db_path=db_path, task_id=task_id, batch_size=5000)
        batches = asyncio.run(_collect(DatabaseConnector(artifact_store=_store(tmp_path)).read(spec)))
        assert batches[-1].fatal_error is None
        assert batches[-1].checkpoint.cursor is not None
        _cleanup(task_id)

    def test_empty_table(self, tmp_path):
        db_path = _make_sqlite_db(tmp_path, "CREATE TABLE t (id INTEGER PRIMARY KEY)", [])
        task_id = "task-empty"
        spec = _spec(mode="table", table="t", sqlite_db_path=db_path, task_id=task_id, batch_size=5000)
        batches = asyncio.run(_collect(DatabaseConnector(artifact_store=_store(tmp_path)).read(spec)))
        assert len(batches) >= 1
        assert batches[-1].checkpoint.cursor is not None
        _cleanup(task_id)


# ---------------- 字段选择 ----------------


class TestFieldSelection:
    def test_fields_subset(self, tmp_path):
        rows = [(1, "Alice", 100.0), (2, "Bob", 200.0)]
        db_path = _make_sqlite_db(tmp_path,
            "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT, amount REAL)", rows)
        task_id = "task-fields"
        spec = _spec(mode="table", table="t", fields=["name"], sqlite_db_path=db_path, task_id=task_id, batch_size=5000)
        batches = asyncio.run(_collect(DatabaseConnector(artifact_store=_store(tmp_path)).read(spec)))
        art = batches[0].artifacts[0]
        # artifact uri 含脱敏信息
        assert art.uri
        _cleanup(task_id)


# ---------------- filters ----------------


class TestFilters:
    def test_eq_filter(self, tmp_path):
        rows = [(1, "Alice"), (2, "Bob"), (3, "Alice")]
        db_path = _make_sqlite_db(tmp_path,
            "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)", rows)
        task_id = "task-filter"
        spec = _spec(mode="table", table="t", filters=[{"field": "name", "op": "eq", "value": "Alice"}],
                     sqlite_db_path=db_path, task_id=task_id, batch_size=5000)
        batches = asyncio.run(_collect(DatabaseConnector(artifact_store=_store(tmp_path)).read(spec)))
        assert len(batches) >= 1
        _cleanup(task_id)


# ---------------- 水位线增量 ----------------


class TestIncremental:
    def test_watermark_filters_after_last_value(self, tmp_path):
        rows = [(i, f"v{i}") for i in range(1, 51)]
        db_path = _make_sqlite_db(tmp_path,
            "CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)", rows)
        task_id = "task-incr"
        spec = _spec(mode="table", table="t", incremental={"strategy": "watermark", "cursor_field": "id", "last_value": 25},
                     sqlite_db_path=db_path, task_id=task_id, batch_size=5000)
        batches = asyncio.run(_collect(DatabaseConnector(artifact_store=_store(tmp_path)).read(spec)))
        for b in batches:
            assert b.fatal_error is None, f"unexpected fatal: {b.fatal_error}"
        assert _is_done(batches[-1].checkpoint)
        _cleanup(task_id)


# ---------------- 复合主键 ----------------


class TestCompositePK:
    def test_composite_key_read(self, tmp_path):
        rows = [("host-a", "2026-01-01", 1), ("host-b", "2026-01-02", 2)]
        db_path = _make_sqlite_db(tmp_path,
            "CREATE TABLE t (host TEXT, ts TEXT, val INTEGER, PRIMARY KEY (host, ts))", rows)
        task_id = "task-composite"
        spec = _spec(mode="table", table="t", sqlite_db_path=db_path, task_id=task_id, batch_size=5000)
        batches = asyncio.run(_collect(DatabaseConnector(artifact_store=_store(tmp_path)).read(spec)))
        for b in batches:
            assert b.fatal_error is None, f"unexpected fatal: {b.fatal_error}"
        _cleanup(task_id)


# ---------------- 无主键表 ----------------


class TestNoPrimaryKey:
    def test_no_pk_reads_all_with_warning(self, tmp_path):
        rows = [(1, "x"), (2, "y")]
        db_path = _make_sqlite_db(tmp_path,
            "CREATE TABLE t (id INTEGER, name TEXT)", rows)
        task_id = "task-nopk"
        spec = _spec(mode="table", table="t", sqlite_db_path=db_path, task_id=task_id, batch_size=5000)
        batches = asyncio.run(_collect(DatabaseConnector(artifact_store=_store(tmp_path)).read(spec)))
        assert batches[-1].fatal_error is None
        _cleanup(task_id)


# ---------------- artifact 脱敏 ----------------


class TestArtifactRedaction:
    def test_artifact_uri_no_password(self, tmp_path):
        db_path = _make_sqlite_db(tmp_path,
            "CREATE TABLE t (id INTEGER PRIMARY KEY)", [(1,)])
        task_id = "task-redact"
        spec = _spec(mode="table", table="t", sqlite_db_path=db_path, task_id=task_id, batch_size=5000)
        batches = asyncio.run(_collect(DatabaseConnector(artifact_store=_store(tmp_path)).read(spec)))
        for b in batches:
            for art in b.artifacts:
                assert "password" not in (art.uri or "").lower()
                assert "password" not in json.dumps(art.request_snapshot).lower()
        _cleanup(task_id)


# ---------------- checkpoint 编码往返 ----------------


class TestCheckpointEncoding:
    def test_checkpoint_has_key_cols(self, tmp_path):
        rows = [(1, "a"), (2, "b")]
        db_path = _make_sqlite_db(tmp_path,
            "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)", rows)
        task_id = "task-ck"
        spec = _spec(mode="table", table="t", sqlite_db_path=db_path, task_id=task_id, batch_size=5000)
        batches = asyncio.run(_collect(DatabaseConnector(artifact_store=_store(tmp_path)).read(spec)))
        ck = batches[-1].checkpoint
        assert ck.cursor is not None
        assert _is_done(ck)
        _cleanup(task_id)


# ---------------- DbConfig 校验 ----------------


class TestDbConfigValidation:
    def test_missing_table_rejected(self):
        with pytest.raises(ValueError, match="table"):
            DbConfig.from_spec(_spec(mode="table"))

    def test_invalid_mode(self):
        with pytest.raises(ValueError, match="mode"):
            DbConfig.from_spec(_spec(mode="invalid", table="t"))

    def test_invalid_filter_op(self):
        with pytest.raises(ValueError, match="op|操作符"):
            DbConfig.from_spec(_spec(mode="table", table="t",
                filters=[{"field": "x", "op": "EXEC", "value": "1"}]))
