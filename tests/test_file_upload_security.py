# -*- coding: utf-8 -*-
"""安全文件上传存储测试（Phase 2 Task 3）。"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
from pathlib import Path

import pytest

from src.services.upload_store import UploadItem, UploadStore


def test_save_bytes_never_uses_client_filename(tmp_path: Path):
    """存储路径用服务端生成的 upload_id，不含客户端提供的文件名。"""
    store = UploadStore(root=str(tmp_path), max_bytes=1024)
    item = store.save_bytes("user-a", "../../secret.csv", b"id,name\n1,A\n")

    assert isinstance(item, UploadItem)
    assert "secret" not in item.storage_path
    assert ".." not in item.storage_path
    assert Path(item.storage_path).resolve().is_relative_to(tmp_path.resolve())


def test_save_bytes_isolates_users(tmp_path: Path):
    """user-a 的上传 user-b 无法解析。"""
    store = UploadStore(root=str(tmp_path), max_bytes=1024)
    item = store.save_bytes("user-a", "data.csv", b"id\n1\n")

    with pytest.raises(PermissionError):
        store.resolve("user-b", item.upload_id)


def test_save_bytes_computes_sha256_and_size(tmp_path: Path):
    import hashlib

    store = UploadStore(root=str(tmp_path), max_bytes=1024)
    data = b"id,name\n1,A\n"
    item = store.save_bytes("user-a", "data.csv", data)

    assert item.sha256 == hashlib.sha256(data).hexdigest()
    assert item.size_bytes == len(data)


def test_save_bytes_rejects_oversized(tmp_path: Path):
    """超过 max_bytes 的上传被拒绝且不留 staging 残留。"""
    store = UploadStore(root=str(tmp_path), max_bytes=4)
    with pytest.raises(ValueError, match="超过上限"):
        store.save_bytes("user-a", "big.csv", b"overflow-data")

    staging = tmp_path / "user-a" / "staging"
    assert not staging.exists() or not any(staging.iterdir())


def test_save_bytes_sanitizes_original_name(tmp_path: Path):
    """原始文件名只保留 basename，去除路径成分。"""
    store = UploadStore(root=str(tmp_path), max_bytes=1024)
    item = store.save_bytes("user-a", "../../etc/passwd", b"x\n")

    assert item.original_name == "passwd"
    assert "/" not in item.original_name
    assert ".." not in item.original_name


def test_resolve_returns_item_for_owner(tmp_path: Path):
    store = UploadStore(root=str(tmp_path), max_bytes=1024)
    item = store.save_bytes("user-a", "data.csv", b"id\n1\n")
    resolved = store.resolve("user-a", item.upload_id)

    assert resolved.upload_id == item.upload_id
    assert resolved.user_id == "user-a"


def test_sidecar_stores_managed_relative_path(tmp_path: Path):
    """新 sidecar 不固化宿主机绝对路径。"""
    store = UploadStore(root=str(tmp_path), max_bytes=1024)
    item = store.save_bytes("user-a", "data.csv", b"id\n1\n")

    meta_path = tmp_path / "user-a" / "objects" / f"{item.upload_id}.meta"
    payload = json.loads(meta_path.read_text(encoding="utf-8"))

    assert payload["storage_path"] == f"objects/{item.upload_id}"
    assert Path(item.storage_path).is_absolute()


def test_resolve_legacy_sidecar_uses_current_root_without_rewrite(
    tmp_path: Path,
) -> None:
    """旧 Windows 路径仅作历史字段，读取时映射到当前上传根。"""
    store = UploadStore(root=str(tmp_path), max_bytes=1024)
    item = store.save_bytes("user-a", "data.csv", b"id\n1\n")
    meta_path = tmp_path / "user-a" / "objects" / f"{item.upload_id}.meta"
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    payload["storage_path"] = (
        rf"D:\old-host\data\uploads\user-a\objects\{item.upload_id}"
    )
    legacy_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    meta_path.write_bytes(legacy_bytes)

    resolved = store.resolve("user-a", item.upload_id)

    assert Path(resolved.storage_path) == (
        tmp_path / "user-a" / "objects" / item.upload_id
    ).resolve()
    assert meta_path.read_bytes() == legacy_bytes


def test_resolve_rejects_object_tampering(tmp_path: Path) -> None:
    """读取时继续按登记的大小和哈希失败关闭。"""
    store = UploadStore(root=str(tmp_path), max_bytes=1024)
    item = store.save_bytes("user-a", "data.csv", b"id\n1\n")
    Path(item.storage_path).write_bytes(b"tampered")

    with pytest.raises(PermissionError, match="完整性"):
        store.resolve("user-a", item.upload_id)


def test_user_id_normalization_cannot_collide_or_delete_owner_file(
    tmp_path: Path,
) -> None:
    """不同原始 Owner 标识不能归一到同一目录。"""
    store = UploadStore(root=str(tmp_path), max_bytes=1024)
    item = store.save_bytes("usera", "data.csv", b"id\n1\n")

    with pytest.raises((ValueError, PermissionError)):
        store.delete("user/a", item.upload_id)

    assert Path(item.storage_path).is_file()


def test_delete_rejects_sidecar_owner_mismatch(tmp_path: Path) -> None:
    """删除前必须复核 sidecar Owner，不能只相信目录位置。"""
    store = UploadStore(root=str(tmp_path), max_bytes=1024)
    item = store.save_bytes("user-a", "data.csv", b"id\n1\n")
    meta_path = tmp_path / "user-a" / "objects" / f"{item.upload_id}.meta"
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    payload["user_id"] = "user-b"
    meta_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PermissionError):
        store.delete("user-a", item.upload_id)

    assert Path(item.storage_path).is_file()


def test_resolve_rejects_object_symlink_escape(tmp_path: Path) -> None:
    """对象软链接即使内容哈希相同，也不能把读取带出 Owner 根。"""
    store = UploadStore(root=str(tmp_path / "uploads"), max_bytes=1024)
    data = b"id\n1\n"
    item = store.save_bytes("user-a", "data.csv", data)
    object_path = Path(item.storage_path)
    outside = tmp_path / "outside.csv"
    outside.write_bytes(data)
    object_path.unlink()
    try:
        object_path.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"当前环境不能创建文件软链接：{exc}")

    with pytest.raises(PermissionError, match="无权访问"):
        store.resolve("user-a", item.upload_id)


def test_save_upload_streams_large_file_without_full_buffer(tmp_path: Path):
    """流式上传：分块读写，不为整个文件分配完整字节缓冲。"""
    store = UploadStore(root=str(tmp_path), max_bytes=10 * 1024 * 1024)
    payload = b"x" * (5 * 1024 * 1024)  # 5 MB
    item = asyncio.run(store.save_upload("user-a", "big.csv", _AsyncStream(payload)))

    assert item.size_bytes == len(payload)
    assert item.sha256 == hashlib.sha256(payload).hexdigest()


def test_save_upload_rejects_oversized_stream(tmp_path: Path):
    """流式上传超限时立即停止并清理 staging。"""
    store = UploadStore(root=str(tmp_path), max_bytes=64)
    with pytest.raises(ValueError, match="超过上限"):
        asyncio.run(store.save_upload("user-a", "big.csv", _AsyncStream(b"x" * 1024)))

    staging = tmp_path / "user-a" / "staging"
    assert not staging.exists() or not any(staging.iterdir())


class _AsyncStream:
    """测试用 async stream 包装（模拟 FastAPI UploadFile 的 async read）。"""

    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)

    async def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)


def test_save_upload_detects_mismatched_magic(tmp_path: Path):
    """魔数与扩展名不一致时拒绝（filetype 检测）。"""
    png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32  # PNG 魔数，扩展名伪造成 .csv
    store = UploadStore(root=str(tmp_path), max_bytes=1024)
    with pytest.raises(ValueError, match="魔数"):
        store.save_bytes("user-a", "fake.csv", png_header, verify_magic=True)
