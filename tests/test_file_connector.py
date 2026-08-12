# -*- coding: utf-8 -*-
"""FileConnector 测试（Phase 2 Task 3）。"""
from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

import pytest

from src.connectors.file_connector import FileConnector
from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.models import SourceSpec, SourceType
from src.services.upload_store import UploadStore


def _setup(tmp_path: Path, data: bytes = b"id,name\n1,A\n2,B\n"):
    upload_root = tmp_path / "uploads"
    store = UploadStore(root=str(upload_root), max_bytes=1024)
    item = store.save_bytes("user-a", "data.csv", data)
    return store, item


def test_file_connector_probe_reachable(tmp_path: Path):
    store, item = _setup(tmp_path)
    connector = FileConnector(upload_store=store)
    spec = SourceSpec(
        source_id="file-1", source_type=SourceType.UPLOAD_FILE,
        locator="",
        options={"upload_id": item.upload_id, "user_id": "user-a"},
    )
    probe = asyncio.run(connector.probe(spec))

    assert probe.reachable
    assert probe.sample["size_bytes"] == len(b"id,name\n1,A\n2,B\n")
    assert probe.sample["original_name"] == "data.csv"


def test_file_connector_capabilities_does_not_error(tmp_path: Path):
    from src.data_prep.models import ConnectorCapability

    store, _ = _setup(tmp_path)
    connector = FileConnector(upload_store=store)
    caps = connector.capabilities()

    assert ConnectorCapability.READ_ONLY in caps
    assert ConnectorCapability.SUPPORTS_CHECKPOINT in caps


def test_file_connector_probe_rejects_wrong_user(tmp_path: Path):
    store, item = _setup(tmp_path)
    connector = FileConnector(upload_store=store)
    spec = SourceSpec(
        source_id="file-1", source_type=SourceType.UPLOAD_FILE,
        locator="",
        options={"upload_id": item.upload_id, "user_id": "user-b"},
    )
    probe = asyncio.run(connector.probe(spec))

    assert not probe.reachable
    assert probe.message


def test_file_connector_read_produces_raw_artifact(tmp_path: Path):
    task_id = f"fileconn_{uuid.uuid4().hex[:8]}"
    try:
        store, item = _setup(tmp_path)
        connector = FileConnector(upload_store=store)
        spec = SourceSpec(
            source_id="file-1", source_type=SourceType.UPLOAD_FILE,
            locator="",
            options={"upload_id": item.upload_id, "user_id": "user-a",
                     "task_id": task_id, "original_name": "data.csv"},
        )
        batches = []
        for batch in asyncio.run(_collect(connector.read(spec))):
            batches.append(batch)

        assert len(batches) == 1
        assert len(batches[0].artifacts) == 1
        art = batches[0].artifacts[0]
        assert art.size_bytes == len(b"id,name\n1,A\n2,B\n")
        assert art.sha256 == item.sha256
        assert batches[0].checkpoint.is_final
    finally:
        task_dir = Path("downloads") / task_id
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)


async def _collect(aiter):
    out = []
    async for item in aiter:
        out.append(item)
    return out
