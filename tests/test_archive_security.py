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


def _write_archive(store, members, *, compression=zipfile.ZIP_STORED):
    data = _make_zip(members, compression=compression)
    artifact = store.write_raw(
        "task-1", "file-1", data, uri="bundle.zip",
        media_type="application/zip", ext="zip",
    )
    return artifact, data


def test_declared_oversize_member_is_not_opened_or_written(tmp_path, monkeypatch):
    store = ArtifactStore(str(tmp_path))
    artifact, data = _write_archive(store, {"large.bin": b"x" * 12})
    opened = []
    original_open = zipfile.ZipFile.open

    def tracked_open(self, member, *args, **kwargs):
        opened.append(member.filename)
        return original_open(self, member, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", tracked_open)
    result = ArchiveParser(max_total_bytes=8).extract(
        artifact, data, task_id="task-1", store=store,
    )

    assert opened == []
    assert result.children == []
    assert result.rejects[0]["reason"] == "zip_total_exceeded"
    assert list(store.task_dir("task-1").joinpath("raw").iterdir()) == [store.resolve_path(artifact.storage_path)]


def test_stream_overrun_stops_reading_and_does_not_write_member(tmp_path, monkeypatch):
    store = ArtifactStore(str(tmp_path))
    artifact, data = _write_archive(store, {"forged.bin": b"x", "later.bin": b"y"})
    read_sizes = []
    opened = []

    class Member(io.BytesIO):
        def read(self, size=-1):
            read_sizes.append(size)
            return super().read(min(size, 3) if size >= 0 else size)

    def open_member(self, info, *args, **kwargs):
        opened.append(info.filename)
        return Member(b"abcdefg")

    monkeypatch.setattr(zipfile.ZipFile, "open", open_member)
    result = ArchiveParser(max_total_bytes=5).extract(
        artifact, data, task_id="task-1", store=store,
    )

    assert read_sizes == [6, 3]
    assert opened == ["forged.bin"]
    assert result.children == []
    assert result.rejects[0]["reason"] == "zip_total_exceeded"


def test_failed_member_keeps_consumed_budget(tmp_path, monkeypatch):
    store = ArtifactStore(str(tmp_path))
    artifact, data = _write_archive(store, {"bad.bin": b"abcd", "later.bin": b"xyz"})
    original_open = zipfile.ZipFile.open
    opened = []

    class BadMember(io.BytesIO):
        def read(self, size=-1):
            if self.tell():
                raise zipfile.BadZipFile("合成 CRC 失败")
            return super().read(size)

    def open_member(self, info, *args, **kwargs):
        opened.append(info.filename)
        if info.filename == "bad.bin":
            return BadMember(b"abcd")
        return original_open(self, info, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", open_member)
    result = ArchiveParser(max_total_bytes=6).extract(
        artifact, data, task_id="task-1", store=store,
    )

    assert result.children == []
    assert opened == ["bad.bin"]
    assert result.rejects[0]["reason"].startswith("zip_read_failed:")
    assert result.rejects[1]["reason"] == "zip_total_exceeded"


@pytest.mark.parametrize("compression", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_unbounded_compression_is_rejected_and_original_retained(tmp_path, monkeypatch, compression):
    store = ArtifactStore(str(tmp_path))
    artifact, data = _write_archive(store, {"small.txt": b"small"}, compression=compression)
    opened = []
    original_open = zipfile.ZipFile.open

    def tracked_open(self, member, *args, **kwargs):
        opened.append(member.filename)
        return original_open(self, member, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", tracked_open)
    result = ArchiveParser().extract(artifact, data, task_id="task-1", store=store)

    assert result.children == []
    assert opened == []
    assert result.rejects[0]["reason"] == "zip_compression_unsupported"
    assert store.read_raw_bytes("task-1", artifact.storage_path) == data


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_exact_expansion_budget_accepts_supported_members(tmp_path, compression):
    store = ArtifactStore(str(tmp_path))
    artifact, data = _write_archive(store, {"one.txt": b"abc", "two.txt": b"def"}, compression=compression)
    result = ArchiveParser(max_total_bytes=6).extract(
        artifact, data, task_id="task-1", store=store,
    )

    assert result.rejects == []
    assert [store.read_raw_bytes("task-1", child.storage_path) for child in result.children] == [b"abc", b"def"]


def test_large_accepted_member_uses_bounded_reads(tmp_path, monkeypatch):
    store = ArtifactStore(str(tmp_path))
    payload = b"x" * 150_000
    artifact, data = _write_archive(store, {"large.txt": payload})
    read_sizes = []
    original_read = zipfile.ZipExtFile.read

    def tracked_read(self, size=-1):
        read_sizes.append(size)
        return original_read(self, size)

    monkeypatch.setattr(zipfile.ZipExtFile, "read", tracked_read)
    result = ArchiveParser(max_total_bytes=len(payload)).extract(
        artifact, data, task_id="task-1", store=store,
    )

    assert result.rejects == []
    assert len(read_sizes) > 1 and all(0 < size <= 64 * 1024 for size in read_sizes)
    assert store.read_raw_bytes("task-1", result.children[0].storage_path) == payload
