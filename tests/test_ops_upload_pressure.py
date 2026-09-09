"""上传配额在落盘前拒绝；合成磁盘错误不留下半提交原件。"""
import asyncio
import errno
import io
from pathlib import Path

import pytest
from src.services import upload_store as original


def store_type():
    return original.UploadStore


class Stream:
    def __init__(self, data):
        self.buffer = io.BytesIO(data)

    async def read(self, size):
        return self.buffer.read(size)


def save(store, streamed, data):
    if streamed:
        return asyncio.run(store.save_upload("owner-a", "synthetic.csv", Stream(data), verify_magic=False))
    return store.save_bytes("owner-a", "synthetic.csv", data)


@pytest.mark.parametrize("streamed", [False, True])
def test_over_quota_chunk_is_rejected_before_writing(tmp_path, monkeypatch, streamed):
    store = store_type()(str(tmp_path), max_bytes=32)
    written = []
    real_open = Path.open

    class File:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.handle.close()
        def write(self, data):
            written.append(len(data))
            return self.handle.write(data)

    def opened(path, mode="r", *args, **kwargs):
        handle = real_open(path, mode, *args, **kwargs)
        return File(handle) if mode == "xb" else handle

    monkeypatch.setattr(Path, "open", opened)
    with pytest.raises(ValueError, match="超过上限"):
        save(store, streamed, b"x" * 128)
    assert written == []
    assert not list(tmp_path.rglob("staging/*"))


@pytest.mark.parametrize("streamed", [False, True])
def test_metadata_disk_full_preserves_old_upload_and_cleans_new_object(tmp_path, monkeypatch, streamed):
    store = store_type()(str(tmp_path), max_bytes=1024)
    existing = save(store, False, b"existing")
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    def full(directory, item):
        (directory / f"{item.upload_id}.meta").write_text("partial", encoding="utf-8")
        raise OSError(errno.ENOSPC, "synthetic full")

    monkeypatch.setattr(store, "_write_sidecar", full)
    with pytest.raises(OSError) as error:
        save(store, streamed, b"new object")
    assert error.value.errno == errno.ENOSPC
    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before
    assert store.resolve("owner-a", existing.upload_id).sha256 == existing.sha256


def test_upload_api_reports_storage_full_without_paths(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api.auth import get_current_user
    from src.api.routes import data_sources as route
    store = store_type()(str(tmp_path), max_bytes=1024)

    def full(*_args):
        raise OSError(errno.ENOSPC, "synthetic-private-host-path")

    monkeypatch.setattr(store, "_write_sidecar", full)
    monkeypatch.setattr(route, "get_upload_store", lambda: store)
    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "owner-a"}
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/data-sources/uploads", files={"file": ("synthetic.csv", b"a,b\n1,2\n", "text/csv")})
    assert response.status_code == 507
    assert "synthetic-private-host-path" not in response.text
    assert not list(tmp_path.rglob("objects/*"))
