# -*- coding: utf-8 -*-
"""JSON/JSONL 解析器测试（Phase 2 Task 5）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.models import RawArtifact
from src.parsers.json_xml import JsonXmlParser


def _make_artifact(store: ArtifactStore, task_id: str, data: bytes, ext: str, media_type: str) -> RawArtifact:
    return store.write_raw(
        task_id=task_id, source_id="file-1", data=data,
        uri=f"upload://data.{ext}", media_type=media_type, ext=ext,
    )


def test_jsonl_parser_parses_rows_with_line(tmp_path: Path):
    store = ArtifactStore(root=str(tmp_path))
    data = b'{"id":1,"name":"A"}\n{"id":2,"name":"B"}\n'
    art = _make_artifact(store, "task-1", data, "jsonl", "application/x-ndjson")

    parser = JsonXmlParser()
    records, rejects = parser.parse(art, data)

    assert len(records) == 2
    assert records[0].data["name"] == "A"
    assert records[0].meta["position"]["line"] == 1
    assert records[0].meta["parser"] == "jsonl"
    assert records[0].meta["artifact_id"] == art.artifact_id
    assert rejects == []


def test_jsonl_parser_isolates_bad_line(tmp_path: Path):
    store = ArtifactStore(root=str(tmp_path))
    data = b'{"id":1}\nBAD LINE\n{"id":2}\n'
    art = _make_artifact(store, "task-1", data, "jsonl", "application/x-ndjson")

    parser = JsonXmlParser()
    records, rejects = parser.parse(art, data)

    assert len(records) == 2
    assert len(rejects) == 1
    assert rejects[0]["reason"] == "invalid_json"
    assert rejects[0]["position"]["line"] == 2


def test_json_array_parser_parses_rows(tmp_path: Path):
    store = ArtifactStore(root=str(tmp_path))
    data = b'[{"id":1,"name":"A"},{"id":2,"name":"B"}]'
    art = _make_artifact(store, "task-1", data, "json", "application/json")

    parser = JsonXmlParser()
    records, rejects = parser.parse(art, data)

    assert len(records) == 2
    assert records[0].data["name"] == "A"
    assert records[0].meta["parser"] == "json_array"
    assert records[0].meta["position"]["index"] == 0
    assert rejects == []


def test_json_single_object_parser(tmp_path: Path):
    store = ArtifactStore(root=str(tmp_path))
    data = b'{"id":1,"name":"A"}'
    art = _make_artifact(store, "task-1", data, "json", "application/json")

    parser = JsonXmlParser()
    records, rejects = parser.parse(art, data)

    assert len(records) == 1
    assert records[0].data["name"] == "A"
    assert records[0].meta["parser"] == "json_object"


def test_large_json_array_rejected(tmp_path: Path):
    store = ArtifactStore(root=str(tmp_path))
    data = b'[{"id":' + b'1},{"id":'.join(str(i).encode() for i in range(2, 50)) + b'1}]'
    art = _make_artifact(store, "task-1", data, "json", "application/json")

    parser = JsonXmlParser(max_json_array_bytes=32)
    with pytest.raises(ValueError, match="JSONL"):
        parser.parse(art, data)


def test_jsonl_parse_stream_yields_batches(tmp_path: Path):
    store = ArtifactStore(root=str(tmp_path))
    lines = b"".join(f'{{"id":{i}}}\n'.encode() for i in range(1, 6))
    art = _make_artifact(store, "task-1", lines, "jsonl", "application/x-ndjson")

    parser = JsonXmlParser()
    batches = list(parser.parse_stream(art, lines, batch_size=2))

    assert len(batches) == 3  # 2 + 2 + 1
    all_records = [r for b in batches for r in b[0]]
    assert len(all_records) == 5
