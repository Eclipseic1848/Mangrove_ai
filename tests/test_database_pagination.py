# -*- coding: utf-8 -*-
"""数据库并列水位与过滤内容回归；所有输入均为临时 SQLite。"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from src.connectors.database_connector import DatabaseConnector
from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.checkpoints import Checkpoint
from tests.test_phase3_completion import _database, _read_rows, _spec


def test_repeated_timestamp_keeps_composite_key_rows_and_projection(tmp_path: Path):
    db = tmp_path / "source.db"
    source = [("2026-09-01 12:00:00.000000", tenant, f"2026-09-0{day}", f"{tenant}{day}")
              for tenant in ("a", "b") for day in range(1, 5)]
    _database(db, "CREATE TABLE t(stamp TIMESTAMP, tenant TEXT, day DATE, body TEXT, "
              "PRIMARY KEY(tenant, day))", source)
    before = db.read_bytes()
    connector = DatabaseConnector(ArtifactStore(str(tmp_path / "artifacts")))
    spec = _spec(db, "ties", fields=["body"], batch_size=2,
                 incremental={"strategy": "watermark", "cursor_field": "stamp"})
    rows, batches = asyncio.run(_read_rows(connector, spec))
    assert db.read_bytes() == before
    assert not [batch.fatal_error for batch in batches if batch.fatal_error]
    assert rows == [{"body": row[3]} for row in source]
    assert sum(bool(batch.artifacts) for batch in batches) >= 3


def test_null_filter_preserves_sql_null_semantics(tmp_path: Path):
    db = tmp_path / "source.db"
    _database(db, "CREATE TABLE t(id INTEGER PRIMARY KEY, body TEXT)",
              [(1, None), (2, "内容"), (3, None)])
    before = db.read_bytes()
    connector = DatabaseConnector(ArtifactStore(str(tmp_path / "artifacts")))
    rows, batches = asyncio.run(_read_rows(connector, _spec(
        db, "null", filters=[{"field": "body", "op": "eq", "value": None}], batch_size=2,
    )))
    assert db.read_bytes() == before
    assert not [batch.fatal_error for batch in batches if batch.fatal_error]
    assert rows == [{"id": 1, "body": None}, {"id": 3, "body": None}]


def _batch_rows(connector, spec, batches):
    return [json.loads(line) for batch in batches for artifact in batch.artifacts
            for line in connector.artifact_store.read_raw_bytes(
                spec.options["task_id"], artifact.storage_path).decode("utf-8").splitlines() if line]


async def _resume(connector, spec, checkpoint):
    batches = [batch async for batch in connector.read(spec, checkpoint)]
    return _batch_rows(connector, spec, batches), batches


def test_json_checkpoint_restores_timestamp_and_date_composite_keys(tmp_path: Path):
    db = tmp_path / "source.db"
    source = [("2026-09-01 12:00:00.000000", tenant, f"2026-09-0{day}", f"{tenant}{day}")
              for tenant in ("a", "b") for day in range(1, 5)]
    _database(db, "CREATE TABLE t(stamp TIMESTAMP, tenant TEXT, day DATE, body TEXT, "
              "PRIMARY KEY(tenant, day))", source)
    before = db.read_bytes()
    store = ArtifactStore(str(tmp_path / "artifacts"))
    spec = _spec(db, "resume", fields=["body"], batch_size=2,
                 incremental={"strategy": "watermark", "cursor_field": "stamp"})

    async def first_page():
        connector = DatabaseConnector(store)
        stream = connector.read(spec)
        try:
            batch = await anext(stream)
            assert not batch.fatal_error
            return _batch_rows(connector, spec, [batch]), batch.checkpoint
        finally:
            await stream.aclose()
            await connector.close()

    first, checkpoint = asyncio.run(first_page())
    assert len(first) == 2 and not checkpoint.is_final
    restored = Checkpoint(**json.loads(json.dumps(checkpoint.to_dict(), ensure_ascii=False)))
    remainder, batches = asyncio.run(_resume(DatabaseConnector(store), spec, restored))
    assert not [batch.fatal_error for batch in batches if batch.fatal_error]
    assert first + remainder == [{"body": row[3]} for row in source]
    assert db.read_bytes() == before
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT * FROM t ORDER BY tenant, day").fetchall() == source


@pytest.mark.parametrize("field,op,value,expected", [
    ("score", "eq", None, [1]), ("score", "ne", None, [2, 3, 4, 5]),
    ("score", "is_null", None, [1]), ("score", "not_null", None, [2, 3, 4, 5]),
    ("score", "eq", 2, [3]), ("score", "ne", 2, [2, 4, 5]),
    ("score", "in", [0, 4], [2, 4]), ("body", "contains", "alpha", [2, 4]),
    ("score", "gt", 2, [4, 5]), ("score", "ge", 2, [3, 4, 5]),
    ("score", "lt", 2, [2]), ("score", "le", 2, [2, 3]),
    (None, None, None, [1, 2, 3, 4, 5]),
])
def test_filter_contents_and_nullable_body(tmp_path: Path, field, op, value, expected):
    db = tmp_path / "source.db"
    source = [(1, None, None), (2, 0, "alpha"), (3, 2, "beta"), (4, 4, "alphabet"), (5, 6, None)]
    _database(db, "CREATE TABLE t(id INTEGER PRIMARY KEY, score INTEGER, body TEXT)", source)
    before = db.read_bytes()
    filters = [{"field": field, "op": op, "value": value}] if field else []
    rows, batches = asyncio.run(_read_rows(
        DatabaseConnector(ArtifactStore(str(tmp_path / "artifacts"))),
        _spec(db, "filter", filters=filters, batch_size=2),
    ))
    assert not [batch.fatal_error for batch in batches if batch.fatal_error]
    assert rows == [dict(zip(("id", "score", "body"), row)) for row in source if row[0] in expected]
    assert db.read_bytes() == before


@pytest.mark.parametrize("duplicate", [False, True])
def test_legacy_single_watermark_requires_unique_values(tmp_path: Path, duplicate: bool):
    db = tmp_path / "source.db"
    _database(db, "CREATE TABLE t(id INTEGER PRIMARY KEY, stamp INTEGER, body TEXT)",
              [(1, 1, "first"), (2, 1 if duplicate else 2, "second"), (3, 3, "third")])
    before = db.read_bytes()
    spec = _spec(db, "legacy", batch_size=1,
                 incremental={"strategy": "watermark", "cursor_field": "stamp"})
    checkpoint = Checkpoint(cursor=json.dumps({
        "mode": "table", "table": "t", "key_cols": ["stamp"], "last_key": [1],
        "part_no": 1, "rows_read": 1, "bytes_read": 0, "done": False,
    }))
    connector = DatabaseConnector(ArtifactStore(str(tmp_path / "artifacts")))
    if duplicate:
        _assert_rejected(connector, spec, checkpoint)
    else:
        rows, batches = asyncio.run(_resume(connector, spec, checkpoint))
        assert not [batch.fatal_error for batch in batches if batch.fatal_error]
        assert [row["id"] for row in rows] == [2, 3]
    assert db.read_bytes() == before


def _assert_rejected(connector, spec, checkpoint=None):
    # API 与任务图消费 fatal batch；批前预检也不得裸抛或先输出部分正文。
    rows, batches = asyncio.run(_resume(connector, spec, checkpoint))
    assert not rows
    assert any(batch.fatal_error for batch in batches)


@pytest.mark.parametrize("values,rejected", [([1, 1, 2], True), ([None, 1, 2], True), ([1, 2, 3], False)])
def test_no_pk_watermark_checks_actual_values(tmp_path: Path, values, rejected):
    db = tmp_path / "source.db"
    _database(db, "CREATE TABLE t(stamp INTEGER, body TEXT)", [(value, f"n{i}") for i, value in enumerate(values)])
    before = db.read_bytes()
    connector = DatabaseConnector(ArtifactStore(str(tmp_path / "artifacts")))
    spec = _spec(db, "no-pk", batch_size=1, fields=["body"],
                 incremental={"strategy": "watermark", "cursor_field": "stamp"})
    if rejected:
        _assert_rejected(connector, spec)
    else:
        rows, batches = asyncio.run(_read_rows(connector, spec))
        assert not [batch.fatal_error for batch in batches if batch.fatal_error]
        assert rows == [{"body": f"n{i}"} for i in range(3)]
    assert db.read_bytes() == before


@pytest.mark.parametrize("scope", ["filters", "time_range", "last_value"])
def test_null_sort_key_outside_requested_range_does_not_block(tmp_path: Path, scope):
    db = tmp_path / "source.db"
    _database(db, "CREATE TABLE t(id INTEGER PRIMARY KEY, stamp INTEGER, tag TEXT, seen TEXT)",
              [(1, None, "drop", "2026-01-01"), (2, 2, "keep", "2026-01-02"), (3, 3, "keep", "2026-01-03")])
    before = db.read_bytes()
    options = {"incremental": {"strategy": "watermark", "cursor_field": "stamp"}}
    if scope == "filters":
        options["filters"] = [{"field": "tag", "op": "eq", "value": "keep"}]
    elif scope == "time_range":
        options["time_range"] = {"field": "seen", "start": "2026-01-02", "end": "2026-01-04"}
    else:
        options["incremental"]["last_value"] = 1
    rows, batches = asyncio.run(_read_rows(DatabaseConnector(ArtifactStore(str(tmp_path / "artifacts"))),
                                         _spec(db, "scoped", fields=["id"], batch_size=1, **options)))
    assert not [batch.fatal_error for batch in batches if batch.fatal_error]
    assert rows == [{"id": 2}, {"id": 3}]
    assert db.read_bytes() == before


@pytest.mark.parametrize("damage", ["json", "empty", "table", "keys", "fractional_integer"])
def test_invalid_done_checkpoint_is_rejected_before_completion(tmp_path: Path, damage):
    db = tmp_path / "source.db"
    _database(db, "CREATE TABLE t(id INTEGER PRIMARY KEY, body TEXT)", [(1, "one"), (2, "two")])
    saved = {"mode": "table", "table": "t", "key_cols": ["id"], "last_key": [1],
             "rows_read": 1, "part_no": 1, "bytes_read": 0, "done": True}
    if damage == "table":
        saved["table"] = "other"
    elif damage == "keys":
        saved["key_cols"] = ["body"]
    elif damage == "fractional_integer":
        saved["last_key"] = [1.9]
    cursor = "{" if damage == "json" else json.dumps({} if damage == "empty" else saved)
    _assert_rejected(DatabaseConnector(ArtifactStore(str(tmp_path / "artifacts"))),
                     _spec(db, "invalid"), Checkpoint(cursor=cursor, is_final=True))


def test_existing_single_primary_key_checkpoint_remains_compatible(tmp_path: Path):
    db = tmp_path / "source.db"
    _database(db, "CREATE TABLE t(id INTEGER PRIMARY KEY, body TEXT)", [(1, "one"), (2, "two"), (3, None)])
    checkpoint = Checkpoint(cursor=json.dumps({"mode": "table", "table": "t", "key_cols": ["id"],
        "last_key": [1], "rows_read": 1, "part_no": 1, "bytes_read": 0, "done": False}))
    rows, batches = asyncio.run(_resume(DatabaseConnector(ArtifactStore(str(tmp_path / "artifacts"))),
                                       _spec(db, "single", batch_size=1), checkpoint))
    assert not [batch.fatal_error for batch in batches if batch.fatal_error]
    assert rows == [{"id": 2, "body": "two"}, {"id": 3, "body": None}]


@pytest.mark.parametrize("sqltype,keys", [
    ("DECIMAL(10, 2)", ["1.25", "2.50", "3.75"]),
    ("TIME", ["09:00:00.000000", "09:00:00.000001", "10:00:00.000000"]),
    ("BLOB", [b"\x00a", b"\x00b", b"\xff"]),
])
def test_typed_primary_key_json_checkpoint_resume(tmp_path: Path, sqltype, keys):
    db = tmp_path / "source.db"
    _database(db, f"CREATE TABLE t(id {sqltype} PRIMARY KEY, body TEXT)",
              [(key, f"n{i}") for i, key in enumerate(keys)])
    before = db.read_bytes()
    store = ArtifactStore(str(tmp_path / "artifacts"))
    spec = _spec(db, "typed", batch_size=1, fields=["body"])

    async def run():
        first_connector = DatabaseConnector(store)
        stream = first_connector.read(spec)
        try:
            first = await anext(stream)
            assert not first.fatal_error
            first_rows = _batch_rows(first_connector, spec, [first])
            checkpoint = Checkpoint(**json.loads(json.dumps(first.checkpoint.to_dict())))
        finally:
            await stream.aclose()
            await first_connector.close()
        remainder, batches = await _resume(DatabaseConnector(store), spec, checkpoint)
        assert not [batch.fatal_error for batch in batches if batch.fatal_error]
        return first_rows + remainder

    assert asyncio.run(run()) == [{"body": f"n{i}"} for i in range(3)]
    assert db.read_bytes() == before
