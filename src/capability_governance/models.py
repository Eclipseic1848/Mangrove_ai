# -*- coding: utf-8 -*-
"""能力治理的不可变事实与三轴投影契约。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.capability_catalog import CatalogActor
from src.conversation_steering import ProcedureScope


class CapabilityMaturity(StrEnum):
    DRAFT = "draft"
    VERIFIED = "verified"


class CapabilityLifecycle(StrEnum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    REVOKED = "revoked"


class CapabilityEligibility(StrEnum):
    ELIGIBLE = "eligible"
    QUARANTINED = "quarantined"


# ADR-0029 决策 4：Trivy 漏洞库有效期 7 天；按 DB UpdatedAt 计算，过期禁止新晋级与发布。
TRIVY_DATABASE_MAX_AGE = timedelta(days=7)


class CapabilityGovernanceTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_id: str | None = Field(default=None, min_length=1, max_length=120)
    scope: ProcedureScope
    pack_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_scope(self) -> "CapabilityGovernanceTarget":
        if self.scope is ProcedureScope.PERSONAL and not self.owner_id:
            raise ValueError("个人能力治理目标必须绑定 Owner")
        if self.scope is ProcedureScope.PLATFORM and self.owner_id is not None:
            raise ValueError("平台能力治理目标不得绑定个人 Owner")
        return self


_AC06_ADMIN_GRAY_VALIDATION_TARGETS = frozenset(
    {
        ("gray-python-table", "1.0.0"),
        ("gray-everything-mcp", "2026.7.4"),
    }
)


def is_ac06_admin_gray_validation_target(
    target: CapabilityGovernanceTarget,
) -> bool:
    """只识别 AC-06 已冻结的两项过渡灰度包，不扩大一般平台能力权限。"""

    return (
        target.scope is ProcedureScope.PLATFORM
        and (target.pack_id, target.version)
        in _AC06_ADMIN_GRAY_VALIDATION_TARGETS
    )


class PromotionGap(StrEnum):
    """能力晋级门的脱敏缺口字面量；不含路径、命令、Token 或原始日志。"""

    VALIDATION_INCOMPLETE = "validation_incomplete"
    EVIDENCE_REFERENCE_MISMATCH = "evidence_reference_mismatch"
    SUPPLY_CHAIN_EVIDENCE_MISSING = "supply_chain_evidence_missing"
    SECRET_DETECTED = "secret_detected"
    CRITICAL_VULNERABILITY = "critical_vulnerability"
    FIXABLE_HIGH_VULNERABILITY = "fixable_high_vulnerability"
    MISCONFIGURATION_FAILURE = "misconfiguration_failure"
    TRIVY_DATABASE_STALE = "trivy_database_stale"


class CapabilityGovernanceEvent(BaseModel):
    """只追加的治理事实；登记建立初始态，晋级由有界命令生成。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(
        default_factory=lambda: f"capgov_{uuid.uuid4().hex[:20]}"
    )
    idempotency_key: str = Field(min_length=1, max_length=200)
    target: CapabilityGovernanceTarget
    event_type: Literal["registered", "promoted_to_verified"] = "registered"
    maturity: CapabilityMaturity = CapabilityMaturity.DRAFT
    lifecycle: CapabilityLifecycle = CapabilityLifecycle.ACTIVE
    eligibility: CapabilityEligibility = CapabilityEligibility.ELIGIBLE
    actor_id: str = Field(min_length=1, max_length=120)
    actor_role: Literal["user", "admin", "superadmin"]
    source_validation_run_id: str | None = Field(
        default=None, min_length=1, max_length=120
    )
    source_supply_chain_evidence_id: str | None = Field(
        default=None, pattern=r"^supply_[0-9a-f]{20}$"
    )
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @model_validator(mode="after")
    def validate_event_state(self) -> "CapabilityGovernanceEvent":
        if self.event_type == "registered":
            if (
                self.maturity is not CapabilityMaturity.DRAFT
                or self.lifecycle is not CapabilityLifecycle.ACTIVE
                or self.eligibility is not CapabilityEligibility.ELIGIBLE
            ):
                # 后续晋级和隔离必须由各自有界命令产生，不能借“登记”绕过治理门。
                raise ValueError("能力登记事件只能建立 draft/active/eligible 初始态")
            if (
                self.source_validation_run_id is not None
                or self.source_supply_chain_evidence_id is not None
            ):
                raise ValueError("能力登记事件不得携带晋级证据引用")
            return self
        if self.event_type == "promoted_to_verified":
            if (
                self.maturity is not CapabilityMaturity.VERIFIED
                or self.lifecycle is not CapabilityLifecycle.ACTIVE
                or self.eligibility is not CapabilityEligibility.ELIGIBLE
            ):
                # 晋级只改变成熟度；生命周期与运行资格仍由 #13/#14 的独立命令治理。
                raise ValueError("能力晋级事件必须携带 verified/active/eligible 状态")
            if (
                self.source_validation_run_id is None
                or self.source_supply_chain_evidence_id is None
            ):
                raise ValueError("能力晋级事件必须引用验证运行与供应链证据")
            return self
        raise ValueError("未知能力治理事件类型")


class PromotionOutcome(BaseModel):
    """晋级命令的显式结果；调用方必须区分晋级、幂等命中与证据不足。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["promoted", "already_verified", "held"]
    gaps: tuple[PromotionGap, ...] = ()
    event: CapabilityGovernanceEvent | None = None

    @model_validator(mode="after")
    def validate_outcome_shape(self) -> "PromotionOutcome":
        if self.status == "held":
            if not self.gaps or self.event is not None:
                raise ValueError("保持草稿的结果必须携带缺口且不携带事件")
        elif self.gaps or self.event is None:
            raise ValueError("晋级结果必须携带事件且无缺口")
        return self


class CapabilityGovernanceProjection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target: CapabilityGovernanceTarget
    maturity: CapabilityMaturity
    lifecycle: CapabilityLifecycle
    eligibility: CapabilityEligibility
    source: Literal["governance_event", "legacy_compat"]


class CapabilityGovernanceView(BaseModel):
    """按 Actor 投影；普通用户不接收跨层治理身份。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pack_id: str
    version: str
    scope: ProcedureScope
    maturity: CapabilityMaturity
    lifecycle: CapabilityLifecycle
    eligibility: CapabilityEligibility
    source: Literal["governance_event", "legacy_compat"]
    owner_id: str | None = None
    digest: str | None = None
    can_validate: bool = False
    promotion_gaps: tuple[PromotionGap, ...] = ()

    @classmethod
    def from_projection(
        cls,
        projection: CapabilityGovernanceProjection,
        actor: CatalogActor,
    ) -> "CapabilityGovernanceView":
        target = projection.target
        return cls(
            pack_id=target.pack_id,
            version=target.version,
            scope=target.scope,
            maturity=projection.maturity,
            lifecycle=projection.lifecycle,
            eligibility=projection.eligibility,
            source=projection.source,
            owner_id=(
                target.owner_id
                if actor.is_admin or target.owner_id == actor.owner_id
                else None
            ),
            digest=(
                target.digest
                if actor.is_admin or target.owner_id == actor.owner_id
                else None
            ),
            can_validate=(
                target.scope is ProcedureScope.PERSONAL
                and target.owner_id == actor.owner_id
            ),
        )


class ValidationRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


class ValidationStep(StrEnum):
    SYNTHETIC_SMOKE = "synthetic_smoke"
    OWNER_TASK_REPLAY = "owner_task_replay"
    FAIL_CLOSED = "fail_closed"
    VERIFIER = "verifier"
    CLEANUP = "cleanup"


class ValidationStepStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ValidationEvidence(BaseModel):
    """可展示的受控证据摘要；正文、Secret 和宿主路径不得进入此记录。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    step: ValidationStep
    status: ValidationStepStatus
    evidence_ref: str = Field(
        pattern=r"^evidence://[A-Za-z0-9._/-]{1,500}$"
    )
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    summary: str = Field(min_length=1, max_length=300)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ValidationTaskRef(BaseModel):
    """真实任务重放的冻结身份；只保存 hash 和授权引用，不保存业务正文。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=1)
    source_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capability_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    authorization_id: str = Field(min_length=1, max_length=200)


class ValidationTaskOption(BaseModel):
    """能力卡片可选择的最小历史任务信息，不包含 Prompt 或业务正文。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=300)
    updated_at: str


class CapabilityValidationRun(BaseModel):
    """严格绑定精确 digest 的不可变验证运行身份。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(default_factory=lambda: f"capval_{uuid.uuid4().hex[:20]}")
    owner_id: str = Field(min_length=1, max_length=120)
    target: CapabilityGovernanceTarget
    actor_id: str = Field(min_length=1, max_length=120)
    actor_role: Literal["user", "admin", "superadmin"]
    idempotency_key: str = Field(min_length=1, max_length=200)
    task_ref: ValidationTaskRef
    status: ValidationRunStatus = ValidationRunStatus.QUEUED
    evidence: tuple[ValidationEvidence, ...] = ()
    cancel_requested: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SupplyChainEvidenceStatus(StrEnum):
    PASSED = "passed"
    BLOCKED = "blocked"


class TrivyDatabaseMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=1)
    updated_at: datetime
    next_update: datetime | None = None
    downloaded_at: datetime | None = None


class SupplyChainCollection(BaseModel):
    """供应链 CLI Adapter 的受控输出；不得携带路径、业务正文或原始日志。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    trivy_version: Literal["0.70.0"]
    trivy_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    trivy_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    trivy_database: TrivyDatabaseMetadata
    secret_count: int = Field(ge=0)
    critical_count: int = Field(ge=0)
    fixable_high_count: int = Field(ge=0)
    misconfiguration_failure_count: int = Field(ge=0)
    critical_misconfiguration_count: int = Field(default=0, ge=0)
    fixable_high_misconfiguration_count: int = Field(default=0, ge=0)
    syft_version: Literal["1.50.0"]
    syft_json_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cyclonedx_json_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cyclonedx_spec_version: Literal["1.6"]


class CapabilitySupplyChainEvidence(BaseModel):
    """按精确 digest 保存的不可变供应链结论与受控证据引用。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str = Field(pattern=r"^supply_[0-9a-f]{20}$")
    target: CapabilityGovernanceTarget
    subject_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: SupplyChainEvidenceStatus
    blockers: tuple[
        Literal[
            "secret_detected",
            "critical_vulnerability",
            "fixable_high_vulnerability",
            "misconfiguration_failure",
            "trivy_database_stale",
        ],
        ...,
    ] = ()
    secret_count: int = Field(ge=0)
    critical_count: int = Field(ge=0)
    fixable_high_count: int = Field(ge=0)
    misconfiguration_failure_count: int = Field(ge=0)
    critical_misconfiguration_count: int = Field(default=0, ge=0)
    fixable_high_misconfiguration_count: int = Field(default=0, ge=0)
    trivy_version: Literal["0.70.0"]
    trivy_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    trivy_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    trivy_database: TrivyDatabaseMetadata
    syft_version: Literal["1.50.0"]
    syft_json_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cyclonedx_json_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cyclonedx_spec_version: Literal["1.6"]
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
