"""32MiB合成上传的有界读取、原件校验和取消；不使用真实用户文件。"""
import asyncio
import hashlib
import json
from pathlib import Path
import time
import tracemalloc

import pytest
from src.services.upload_store import UploadStore as store_type


def test_large_stream_preserves_original_and_cancel_leaves_no_partial(tmp_path):
    block = b"item,value\n" + b"a,1\n" * 262141 + b"\n"
    assert len(block) == 1024 * 1024
    count = 32
    store = store_type(str(tmp_path), max_bytes=len(block) * count)
    requested = []

    class Stream:
        def __init__(self, cancel=False):
            self.reads = 0
            self.cancel = cancel

        async def read(self, size):
            requested.append(size)
            await asyncio.sleep(0)
            self.reads += 1
            if self.cancel and self.reads == 4:
                raise asyncio.CancelledError()
            return block if self.reads <= count else b""

    tracemalloc.start()
    started = time.perf_counter()
    try:
        item = asyncio.run(store.save_upload("owner-a", "synthetic-large.csv", Stream(), verify_magic=True))
        elapsed = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    expected = hashlib.sha256()
    for _ in range(count):
        expected.update(block)
    assert item.size_bytes == 32 * 1024 * 1024
    assert item.sha256 == expected.hexdigest()
    assert store.resolve("owner-a", item.upload_id).sha256 == expected.hexdigest()
    with pytest.raises(PermissionError):
        store.resolve("owner-b", item.upload_id)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(store.save_upload("owner-a", "cancelled.csv", Stream(cancel=True), verify_magic=True))
    assert not list(tmp_path.rglob("staging/*"))
    assert store.resolve("owner-a", item.upload_id).sha256 == expected.hexdigest()
    assert requested and max(requested) == 1024 * 1024
    report = {"scope": "UploadStore async stream only, not HTTP multipart or full-process RSS", "size_bytes": item.size_bytes,
              "seconds": elapsed, "tracemalloc_peak_bytes": peak, "max_read_bytes": max(requested),
              "sha256": item.sha256, "cancelled_staging_empty": True, "external_calls": 0}
    (tmp_path / "large-upload.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert peak < 8 * 1024 * 1024, report
