# -*- coding: utf-8 -*-
"""构造固定 CoreMind Worker 的最小环境。"""
from __future__ import annotations

from pathlib import Path


def sanitized_worker_environment(
    source: dict[str, str],
    *,
    grant_env_name: str,
    runtime_root: str | Path,
    node_path: str | Path,
) -> dict[str, str]:
    grant = {key.casefold(): value for key, value in source.items()}.get(
        grant_env_name.casefold()
    )
    if not grant:
        raise RuntimeError("CoreMind Worker 缺少本次 Run 的模型 Grant")
    root = str(Path(runtime_root).resolve())
    environment = {
        key: source[key]
        for key in ("SYSTEMROOT", "WINDIR", "COMSPEC")
        if key in source
    }
    environment.update(
        PATH=str(Path(node_path).resolve().parent),
        HOME=root,
        USERPROFILE=root,
        TEMP=root,
        TMP=root,
        TMPDIR=root,
        APPDATA=root,
        LOCALAPPDATA=root,
        PYTHONIOENCODING="utf-8",
        MANGROVE_COREMIND_MODEL_GRANT=grant,
    )
    return environment
