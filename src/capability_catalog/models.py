# -*- coding: utf-8 -*-
"""CapabilityCatalog 的调用身份契约。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.conversation_steering import ProcedureScope


class CatalogActor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_id: str = Field(min_length=1, max_length=120)
    role: Literal["user", "admin", "superadmin"]

    @property
    def is_admin(self) -> bool:
        return self.role in {"admin", "superadmin"}


class CapabilityPackRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pack_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class AutomationProcedureRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    procedure_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class CapabilityValidation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    validation_id: str = Field(min_length=1, max_length=120)
    owner_id: str | None = Field(default=None, min_length=1, max_length=120)
    target_kind: Literal["pack", "procedure"]
    target_id: str = Field(min_length=1, max_length=120)
    target_version: str = Field(min_length=1, max_length=80)
    target_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: Literal["passed", "failed"]
    evidence_refs: tuple[str, ...] = ()


class CapabilityComponent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    component_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    scope: ProcedureScope
    owner_id: str | None = Field(default=None, min_length=1, max_length=120)
    kind: Literal[
        "tool",
        "mcp_local",
        "mcp_remote",
        "skill",
        "dependency_bundle",
    ]
    oci_reference: str = Field(min_length=1, max_length=500)
    source_provenance: tuple[str, ...] = ()
    permission_requirements: tuple[str, ...] = ()
    resource_requirements: tuple[tuple[str, str], ...] = ()
    entrypoint: str | None = Field(default=None, max_length=500)
    healthcheck: str | None = Field(default=None, max_length=500)
    published: bool = False

    @model_validator(mode="after")
    def validate_scope_and_reference(self) -> "CapabilityComponent":
        if self.scope is ProcedureScope.PERSONAL and not self.owner_id:
            raise ValueError("个人能力组件必须绑定 Owner")
        if self.scope is ProcedureScope.PLATFORM and self.owner_id is not None:
            raise ValueError("平台能力组件不得绑定个人 Owner")
        if self.scope is ProcedureScope.PERSONAL and self.published:
            raise ValueError("个人能力组件不能标记为平台已发布")
        if not self.oci_reference.endswith(self.digest):
            raise ValueError("OCI 引用必须冻结确切 digest")
        return self


class PublicCapabilityDescriptor(BaseModel):
    """允许进入普通用户进度视图的最小能力身份，不携带执行配置。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    kind: Literal[
        "tool",
        "mcp_local",
        "mcp_remote",
        "skill",
        "dependency_bundle",
        "capability_pack",
    ]
    version: str = Field(min_length=1, max_length=80)
    purpose: str = Field(min_length=1, max_length=300)


class CapabilitySelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    selection_id: str = Field(
        default_factory=lambda: f"selection_{uuid.uuid4().hex[:16]}"
    )
    owner_id: str = Field(min_length=1, max_length=120)
    task_id: str = Field(min_length=1, max_length=120)
    revision: int = Field(ge=1)
    pack_refs: tuple[CapabilityPackRef, ...] = ()
    procedure_refs: tuple[AutomationProcedureRef, ...] = ()
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @model_validator(mode="after")
    def validate_nonempty(self) -> "CapabilitySelection":
        if not self.pack_refs and not self.procedure_refs:
            raise ValueError("能力选择至少冻结一个能力包或自动化方案")
        return self
