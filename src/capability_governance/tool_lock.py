# -*- coding: utf-8 -*-
"""外部治理工具的共同版本、来源和内容锁校验。"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    """流式计算文件 SHA-256。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_locked_executable(
    *,
    lock: dict[str, Any],
    name: str,
    expected_version: str,
    tool_root: Path,
    expected_method: str | None = None,
    expected_fingerprint: str | None = None,
) -> Path:
    """解析并校验一个必须位于受控根目录内的冻结可执行文件。"""
    entry = lock.get(name)
    if not isinstance(entry, dict) or entry.get("version") != expected_version:
        raise ValueError(f"{name} 版本锁不存在或不匹配")
    verification = entry.get("source_verification")
    if not isinstance(verification, dict) or verification.get("verified") is not True:
        raise ValueError(f"{name} 上游来源尚未验证")
    if expected_method is not None and verification.get("method") != expected_method:
        raise ValueError(f"{name} 上游来源验证方式不符合冻结要求")
    if (
        expected_fingerprint is not None
        and verification.get("fingerprint") != expected_fingerprint
    ):
        raise ValueError(f"{name} 上游签名身份不符合冻结要求")
    executable = (tool_root / str(entry.get("executable", ""))).resolve()
    if not executable.is_relative_to(tool_root) or not executable.is_file():
        raise ValueError(f"{name} 可执行文件不在受控目录")
    if sha256_file(executable) != entry.get("executable_sha256"):
        raise ValueError(f"{name} 可执行文件 digest 校验失败")
    return executable
