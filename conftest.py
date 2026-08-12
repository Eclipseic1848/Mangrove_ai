"""项目级 conftest.py -- 统一 UTF-8 测试基线（plan 4.3 / Phase 0）。

问题：Windows 控制台默认 cp936，测试打印中文（如 scripts/test_clean.py 最终汇总行）
会抛 UnicodeEncodeError，导致功能通过却被判失败。

本文件在 pytest 启动时把 stdout/stderr 重配为 UTF-8，并对子进程注入 PYTHONUTF8=1。
脚本式测试（python scripts/test_xxx.py）不经过 pytest，需在脚本顶部自行 reconfigure，
或用 `python -X utf8 scripts/test_xxx.py` / `set PYTHONUTF8=1` 运行（见 ADR-0006）。
"""
from __future__ import annotations

import os
import sys


def _reconfigure_utf8() -> None:
    """把当前进程标准流重配为 UTF-8（Python 3.7+ 支持 reconfigure）。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        encoding = getattr(stream, "encoding", "") or ""
        # 已经是 utf-8（含 -X utf8 模式）则不动，避免重复 reconfigure 报错
        if encoding.lower().replace("-", "") == "utf8":
            continue
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            # 某些自定义 stream 无 reconfigure 方法，忽略而非阻断测试
            pass


_reconfigure_utf8()

# 子进程也走 UTF-8（采集器/数据库驱动等常 spawn 子进程）
os.environ.setdefault("PYTHONUTF8", "1")
