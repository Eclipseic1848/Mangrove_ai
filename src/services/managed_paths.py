# -*- coding: utf-8 -*-
"""跨宿主机可迁移的受管文件路径编码。"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Sequence


class ManagedPathError(ValueError):
    """路径不属于受管根目录，或包含不安全表示。"""

    code = "MANAGED_PATH_INVALID"


class ManagedPathCodec:
    """把物理路径持久化为相对当前受管根目录的稳定标识。"""

    PREFIX = "managed:v1/"
    _WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        legacy_anchor: Sequence[str],
    ) -> None:
        if not legacy_anchor or any(
            not part or part in {".", ".."} for part in legacy_anchor
        ):
            raise ValueError("legacy_anchor 无效")
        self.root = Path(root).expanduser().resolve()
        self.legacy_anchor = tuple(legacy_anchor)

    @staticmethod
    def _reject_parent_segments(value: str) -> None:
        # 先拒绝再解析，避免不同宿主机对“..”归一化方式不同而放大读取范围。
        normalized = value.replace("\\", "/")
        if ".." in normalized.split("/"):
            raise ManagedPathError("受管路径无效")

    @staticmethod
    def _is_network_or_device_path(value: str) -> bool:
        # UNC、扩展长度路径和设备路径都绕过普通盘符语义，统一失败关闭。
        return value.startswith("\\\\") or value.startswith("//")

    def _legacy_relative(self, value: str, *, windows: bool) -> str:
        normalized = value.replace("\\", "/")
        if windows:
            normalized = normalized[2:]
        tokens = [token for token in normalized.split("/") if token]
        if any(token in {".", ".."} for token in tokens):
            raise ManagedPathError("受管路径无效")

        anchor = self.legacy_anchor
        comparable = (
            [token.casefold() for token in tokens]
            if windows
            else tokens
        )
        expected = (
            tuple(token.casefold() for token in anchor)
            if windows
            else anchor
        )
        # 旧绝对路径只能从冻结锚点截断，禁止按文件名猜测或搜索宿主机。
        start = None
        for index in range(len(comparable) - len(expected), -1, -1):
            if tuple(comparable[index : index + len(expected)]) == expected:
                start = index + len(expected)
                break
        if start is None or start >= len(tokens):
            raise ManagedPathError("受管路径无效")
        return "/".join(tokens[start:])

    def _resolve_relative(self, value: str) -> Path:
        normalized = value.replace("\\", "/")
        parts = normalized.split("/")
        # 盘符、空段和父级段在 Windows/Linux 含义不同，统一拒绝才能保持同一边界。
        if (
            not normalized
            or normalized.startswith("/")
            or any(not part or part in {".", ".."} or ":" in part for part in parts)
        ):
            raise ManagedPathError("受管路径无效")
        candidate = self.root.joinpath(*parts).resolve()
        # resolve 会展开已有软链接；展开后越界必须失败关闭，不能回退到原文本路径。
        if not candidate.is_relative_to(self.root):
            raise ManagedPathError("受管路径无效")
        return candidate

    def encode(self, path: str | os.PathLike[str]) -> str:
        raw = os.fspath(path)
        self._reject_parent_segments(raw)
        if self._is_network_or_device_path(raw):
            raise ManagedPathError("受管路径无效")
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        candidate = candidate.resolve()
        if not candidate.is_relative_to(self.root):
            raise ManagedPathError("受管路径无效")
        relative = candidate.relative_to(self.root).as_posix()
        if relative in {"", "."}:
            raise ManagedPathError("受管路径无效")
        return f"{self.PREFIX}{relative}"

    def decode(self, value: str) -> Path:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ManagedPathError("受管路径无效")
        self._reject_parent_segments(value)
        if self._is_network_or_device_path(value):
            raise ManagedPathError("受管路径无效")

        if value.startswith(self.PREFIX):
            relative = value[len(self.PREFIX) :]
        elif self._WINDOWS_ABSOLUTE.match(value):
            relative = self._legacy_relative(value, windows=True)
        elif value.startswith("/"):
            relative = self._legacy_relative(value, windows=False)
        else:
            relative = value
        return self._resolve_relative(relative)
