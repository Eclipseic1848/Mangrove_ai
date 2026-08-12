# -*- coding: utf-8 -*-
"""CapabilityHost 的最小公开 Interface。"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class CapabilityHostRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    user_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    run_id: str = Field(min_length=1)
    network_name: str = Field(min_length=1)
    capability_dirs: tuple[Path, ...]


class CapabilityHostLease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    container_name: str
    relay_url: str
    relay_token: str = Field(repr=False)
    capability_names: tuple[str, ...]
    capability_kinds: tuple[tuple[str, str], ...]
    runtime_dir: Path
