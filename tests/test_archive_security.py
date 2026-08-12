# -*- coding: utf-8 -*-
"""安全 ZIP 展开测试（Phase 2 Task 7）。"""
from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path

import pytest

from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.models import RawArtifact
from src.parsers.archive import ArchiveParser


def _make_zip(members: dict, *, compression=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_zip_extracts_members_as_children(tmp_path: Path):
    store = ArtifactStore(root=str(tmp_path))
    data = _make_zip({"a.csv": b"id,name\n1,A\n", "b.json": b'{"id":1}'})
    art = store.write_raw(
        task_id="task-1", source_id="file-1", data=data,
        uri="upload://data.zip", media_type="application/zip", ext="zip",
    )

    parser = ArchiveParser()
    result = parser.extract(art, data, task_id="task-1", store=store)

    assert len(result.children) == 2
    assert result.rejects == []
    for child in result.children:
        assert child.parent_artifact_id == art.artifact_id


def test_zip_slip_member_is_rejected(tmp_path: Path):
    store = ArtifactStore(root=str(tmp_path))
    data = _make_zip({"../../evil.txt": b"bad", "good.txt": b"ok"})
    art = store.write_raw(
        task_id="task-1", source_id="file-1", data=data,
        uri="upload://data.zip", media_type="application/zip", ext="zip",
    )

    parser = ArchiveParser()
    result = parser.extract(art, data, task_id="task-1", store=store)

    assert len(result.children) == 1  # 只有 good.txt 保留
    assert result.children[0].uri.endswith("good.txt")
    assert len(result.rejects) == 1
    assert result.rejects[0]["reason"] == "zip_path_escape"
    assert "../../evil.txt" in result.rejects[0]["member"]


def test_zip_bomb_ratio_is_rejected(tmp_path: Path):
    store = ArtifactStore(root=str(tmp_path))
    payload = b"0" * (512 * 1024)  # 512KB 高度可压缩
    data = _make_zip({"big.txt": payload})
    art = store.write_raw(
        task_id="task-1", source_id="file-1", data=data,
        uri="upload://data.zip", media_type="application/zip", ext="zip",
    )

    parser = ArchiveParser(max_ratio=20)
    result = parser.extract(art, data, task_id="task-1", store=store)

    assert result.children == []
    assert any(r["reason"] == "zip_ratio_exceeded" for r in result.rejects)


def test_zip_exceeds_max_files(tmp_path: Path):
    store = ArtifactStore(root=str(tmp_path))
    members = {f"f{i}.txt": b"x" for i in range(10)}
    data = _make_zip(members)
    art = store.write_raw(
        task_id="task-1", source_id="file-1", data=data,
        uri="upload://data.zip", media_type="application/zip", ext="zip",
    )

    parser = ArchiveParser(max_files=5)
    result = parser.extract(art, data, task_id="task-1", store=store)

    assert result.children == []
    assert any(r["reason"] == "zip_too_many_files" for r in result.rejects)
