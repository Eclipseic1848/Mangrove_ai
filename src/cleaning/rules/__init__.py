"""清洗规则包（plan.md 第 8.2 节）。

规则与引擎解耦：每条规则实现 Rule 接口，引擎按 stage 顺序调度。
- base.py：Rule 抽象基类
- web_rules.py：从 src/conductor/nodes/clean.py 迁移的网页清洗规则（行为兼容）
"""
from __future__ import annotations

from .base import Rule  # noqa: F401  重导出

__all__ = ["Rule"]
