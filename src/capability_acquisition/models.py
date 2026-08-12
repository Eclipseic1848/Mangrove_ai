# -*- coding: utf-8 -*-
"""CapabilityAcquisition 的不可变调用契约。"""
from __future__ import annotations

from enum import StrEnum
import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.conversation_steering import AcquisitionBudget, AcquisitionStatus


class AcquisitionSourceKind(StrEnum):
    PYPI = "pypi"
    NPM = "npm"
    GITHUB_RELEASE = "github_release"
    OFFICIAL_GIT = "official_git"
    REGISTERED_MCP = "registered_mcp"
    REGISTERED_SKILL = "registered_skill"
    UNKNOWN_URL = "unknown_url"


class AcquisitionCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=120)
    kind: AcquisitionSourceKind
    source_uri: str = Field(min_length=1, max_length=1000)
    version: str = Field(min_length=1, max_length=120)
    expected_sha256: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    source_registration_id: str | None = Field(default=None, max_length=120)
    permission_grant_id: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_uri(self) -> "AcquisitionCandidate":
        parsed = urlsplit(self.source_uri)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("能力来源必须是精确 HTTPS URL")
        if parsed.username or parsed.password:
            raise ValueError("能力来源 URL 不得携带凭证")
        secret_query = re.compile(
            r"(?:api[_-]?key|token|signature|credential|password)=",
            re.IGNORECASE,
        )
        if secret_query.search(parsed.query):
            raise ValueError("能力来源 URL 不得携带 Secret 查询参数")
        if self.kind in {
            AcquisitionSourceKind.REGISTERED_MCP,
            AcquisitionSourceKind.REGISTERED_SKILL,
        } and not self.source_registration_id:
            raise ValueError("登记来源必须引用平台可信登记记录")
        if (
            self.kind is not AcquisitionSourceKind.UNKNOWN_URL
            and self.expected_sha256 is None
        ):
            raise ValueError("自动来源必须提供仓库公布的 SHA-256")
        return self


class AcquisitionRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    acquisition_id: str = Field(min_length=1, max_length=120)
    owner_id: str = Field(min_length=1, max_length=120)
    need_summary: str = Field(min_length=1, max_length=1000)
    candidates: tuple[AcquisitionCandidate, ...] = Field(min_length=1)
    budget: AcquisitionBudget

    @model_validator(mode="after")
    def validate_safe_request(self) -> "AcquisitionRequest":
        if len(self.candidates) > self.budget.max_candidates:
            raise ValueError("候选数量超过获取预算")
        if re.search(r"(?:[A-Za-z]:\\|/(?:home|Users|mnt|workspace)/)", self.need_summary):
            raise ValueError("获取请求不得包含宿主或用户来源路径")
        if re.search(
            r"(?:api[_-]?key\s*=|sk-[A-Za-z0-9_-]{6,}|provider[_-]?key)",
            self.need_summary,
            re.IGNORECASE,
        ):
            raise ValueError("获取请求不得包含 Provider Key")
        return self


class ResolvedCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate: AcquisitionCandidate
    final_uri: str
    redirect_chain: tuple[str, ...] = ()


class PreparedCapability(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pack_id: str
    version: str
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    oci_reference: str
    source_uri: str
    final_uri: str
    download_bytes: int = Field(ge=0)
    unpacked_bytes: int = Field(ge=0)
    reused: bool = False


class AcquisitionEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    acquisition_id: str
    owner_id: str
    sequence: int = Field(ge=1)
    status: AcquisitionStatus
    summary: str = Field(min_length=1, max_length=500)


class AcquisitionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    acquisition_id: str
    owner_id: str
    status: AcquisitionStatus
    pack_ref: str | None = None
    digest: str | None = None
    reused: bool = False
    failure_code: str | None = None
    message: str = ""


class AcquisitionRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request: AcquisitionRequest
    status: AcquisitionStatus
    events: tuple[AcquisitionEvent, ...] = ()
    result: AcquisitionResult | None = None
    cancel_requested: bool = False
