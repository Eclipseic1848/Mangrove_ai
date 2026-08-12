"""记忆模块通用工装：原子写 + mtime 缓存。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, content: str) -> None:
    """原子写：临时文件 + os.replace，防止读时看到半截。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


class MtimeCache:
    """按 (path_str, mtime_ns) 失效的单槽缓存。

    读操作每次 stat 目录 mtime，变化则缓存 miss；写操作后主动 invalidate 兜底。
    不同目录的缓存互不干扰（用 path_str 区分）。
    """

    def __init__(self) -> None:
        self._key = None       # (path_str, mtime_ns)
        self._value = None

    def get(self, dir_path: Path):
        if not dir_path.exists():
            return None
        key = (str(dir_path), dir_path.stat().st_mtime_ns)
        if self._key == key and self._value is not None:
            return self._value
        return None

    def set(self, dir_path: Path, value):
        if dir_path.exists():
            self._key = (str(dir_path), dir_path.stat().st_mtime_ns)
        else:
            self._key = (str(dir_path), 0)  # 不存在时用 0 占位，下次 exists 检查返回 False
        self._value = value

    def invalidate(self) -> None:
        self._key = None
        self._value = None