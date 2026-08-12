# -*- coding: utf-8 -*-
"""正式交付的稳定契约。"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..models import DeliveryFormat


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DeliveryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DeliveryStatus(str, Enum):
    STAGING = "staging"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ArtifactQAReport(DeliveryModel):
    format: DeliveryFormat
    openable: bool
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    row_count: int | None = Field(default=None, ge=0)
    sheet_count: int | None = Field(default=None, ge=0)
    page_count: int | None = Field(default=None, ge=0)
    slide_count: int | None = Field(default=None, ge=0)
    checks: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class DeliveryOutput(DeliveryModel):
    output_id: str = Field(min_length=1)
    format: DeliveryFormat
    filename: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    qa: ArtifactQAReport
    download_url: str = Field(min_length=1)


class DeliveryManifest(DeliveryModel):
    schema_version: str = "1"
    delivery_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    status: DeliveryStatus
    source_artifact_hashes: dict[str, str]
    requested_formats: tuple[DeliveryFormat, ...]
    outputs: tuple[DeliveryOutput, ...]
    renderer_versions: dict[str, str]
    provenance: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
