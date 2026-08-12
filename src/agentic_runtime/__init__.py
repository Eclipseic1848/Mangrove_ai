# -*- coding: utf-8 -*-
"""Mangrove Agentic Runtime vNext 公共接口。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import (
    CandidateArtifact,
    PermissionProfile,
    PiRuntimeRequest,
    PiRuntimeResult,
    RuntimeEvent,
    RuntimeStatus,
    RuntimeTaskConfig,
    RuntimeVersion,
    SourceInput,
)
if TYPE_CHECKING:
    from .pi_runtime import PiRuntime
    from .repository import AgenticRuntimeRepository


def __getattr__(name: str) -> Any:
    """避免读取 Egress 等轻量子 Module 时加载全部可选 Runtime 依赖。"""

    if name == "PiRuntime":
        from .pi_runtime import PiRuntime

        return PiRuntime
    if name == "AgenticRuntimeRepository":
        from .repository import AgenticRuntimeRepository

        return AgenticRuntimeRepository
    raise AttributeError(name)

__all__ = [
    "AgenticRuntimeRepository",
    "CandidateArtifact",
    "PermissionProfile",
    "PiRuntime",
    "PiRuntimeRequest",
    "PiRuntimeResult",
    "RuntimeEvent",
    "RuntimeStatus",
    "RuntimeTaskConfig",
    "RuntimeVersion",
    "SourceInput",
]
