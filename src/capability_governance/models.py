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


# #17（AC07-12）兼容切换：AC-06 过渡灰度包白名单已退役。真实发布链
# （#15/#16：个人 draft → 验证 → 晋级 → 快照 → 签名 → admin_gray 发布）
# 已满足门禁，管理员不再能直接验证白名单平台包。


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


AuditSubjectType = Literal["task_prompt", "task_sources", "task_output"]
CapabilityAudience = Literal["admin_gray", "users"]


class CapabilityGovernanceEvent(BaseModel):
    """只追加的治理事实；登记建立初始态，晋级由有界命令生成，审计查看独立留痕。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(
        default_factory=lambda: f"capgov_{uuid.uuid4().hex[:20]}"
    )
    idempotency_key: str = Field(min_length=1, max_length=200)
    target: CapabilityGovernanceTarget
    event_type: Literal[
        "registered",
        "promoted_to_verified",
        "audit_viewed",
        "platform_candidate",
        "platform_published",
        "audience_changed",
        "lifecycle_changed",
        "eligibility_changed",
        "risk_accepted",
        "recommendation_changed",
        "rescan_completed",
    ] = "registered"
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
    # 审计查看专用字段；其余事件类型必须为 None，保证事件身份不串味。
    reason: str | None = Field(default=None, min_length=1, max_length=1000)
    subject_type: AuditSubjectType | None = None
    subject_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    result: Literal["succeeded", "failed"] | None = None
    # 审计查看必须精确记录"查看了哪个任务的哪次 revision"（AC3 任务字段）。
    task_id: str | None = Field(default=None, min_length=1, max_length=160)
    revision: int | None = Field(default=None, ge=1)
    # 失败读取的类型化原因（task_not_found 等）；不含正文，审计可区分失败类型。
    failure_reason: str | None = Field(default=None, min_length=1, max_length=120)
    # 平台发布专用字段；其余事件类型必须为 None。
    source_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    platform_digest: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    audience: CapabilityAudience | None = None
    platform_validation_run_id: str | None = Field(
        default=None, min_length=1, max_length=120
    )
    signing_signature_digest: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    signing_public_key_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    # 生命周期/隔离/风险接受/推荐指针专用字段；其余事件类型必须为 None。
    expires_at: datetime | None = None
    recommended_version: str | None = Field(
        default=None, min_length=1, max_length=120
    )
    finding_ref: str | None = Field(
        default=None, min_length=1, max_length=200
    )
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @model_validator(mode="after")
    def validate_event_state(self) -> "CapabilityGovernanceEvent":
        audit_fields = (
            self.subject_type,
            self.subject_sha256,
            self.result,
            self.task_id,
            self.revision,
            self.failure_reason,
        )
        lifecycle_fields = (
            self.expires_at,
            self.recommended_version,
            self.finding_ref,
        )
        if self.event_type not in {
            "lifecycle_changed",
            "eligibility_changed",
            "risk_accepted",
            "recommendation_changed",
        } and any(field is not None for field in lifecycle_fields):
            raise ValueError("生命周期治理字段只允许出现在对应治理事件中")
        platform_fields = (
            self.source_digest,
            self.platform_digest,
            self.audience,
            self.platform_validation_run_id,
            self.signing_signature_digest,
            self.signing_public_key_sha256,
        )
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
            if self.reason is not None or any(
                field is not None for field in audit_fields
            ):
                raise ValueError("能力登记事件不得携带审计查看字段")
            if any(field is not None for field in platform_fields):
                raise ValueError("能力登记事件不得携带平台发布字段")
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
            if self.reason is not None or any(
                field is not None for field in audit_fields
            ):
                raise ValueError("能力晋级事件不得携带审计查看字段")
            if any(field is not None for field in platform_fields):
                raise ValueError("能力晋级事件不得携带平台发布字段")
            return self
        if self.event_type == "audit_viewed":
            # 审计查看不改变三轴状态；若被写入投影，治理事实会被一次查看污染。
            if (
                self.maturity is not CapabilityMaturity.DRAFT
                or self.lifecycle is not CapabilityLifecycle.ACTIVE
                or self.eligibility is not CapabilityEligibility.ELIGIBLE
            ):
                raise ValueError("审计查看事件不得改变三轴状态")
            if (
                self.source_validation_run_id is not None
                or self.source_supply_chain_evidence_id is not None
            ):
                raise ValueError("审计查看事件不得携带晋级证据引用")
            if not self.reason:
                raise ValueError("审计查看事件必须携带非空原因")
            if self.subject_type is None or self.result is None:
                raise ValueError("审计查看事件必须携带查看对象与结果")
            if not self.task_id or self.revision is None:
                # AC3：审计记录必须能回答"查看了哪个任务的哪次 revision"。
                raise ValueError("审计查看事件必须携带任务身份与 revision")
            if self.result == "succeeded":
                if not self.subject_sha256:
                    raise ValueError("成功的审计查看必须携带内容 hash")
                if self.failure_reason is not None:
                    raise ValueError("成功的审计查看不得携带失败原因")
            else:
                if self.subject_sha256 is not None:
                    raise ValueError("失败的审计查看不得携带内容 hash")
                if self.failure_reason is None:
                    raise ValueError("失败的审计查看必须携带类型化失败原因")
            if any(field is not None for field in platform_fields):
                raise ValueError("审计查看事件不得携带平台发布字段")
            return self
        if self.event_type == "platform_candidate":
            # 候选只把已验证个人能力复制为平台快照，不改变任何现有投影。
            if (
                self.maturity is not CapabilityMaturity.VERIFIED
                or self.lifecycle is not CapabilityLifecycle.ACTIVE
                or self.eligibility is not CapabilityEligibility.ELIGIBLE
            ):
                raise ValueError("平台候选事件必须携带 verified/active/eligible 状态")
            if self.target.scope is not ProcedureScope.PLATFORM:
                raise ValueError("平台候选事件只能针对平台目标")
            if not self.reason:
                raise ValueError("平台候选事件必须携带非空原因")
            if self.source_digest is None or self.platform_digest is None:
                raise ValueError("平台候选事件必须携带来源与平台 digest")
            if (
                self.audience is not None
                or self.platform_validation_run_id is not None
                or self.signing_signature_digest is not None
                or self.signing_public_key_sha256 is not None
            ):
                raise ValueError("平台候选事件不得携带受众或验证/签名引用")
            return self
        if self.event_type == "platform_published":
            # 发布是候选全绿后的生效动作；#12 阶段受众固定 admin_gray。
            if (
                self.maturity is not CapabilityMaturity.VERIFIED
                or self.lifecycle is not CapabilityLifecycle.ACTIVE
                or self.eligibility is not CapabilityEligibility.ELIGIBLE
            ):
                raise ValueError("平台发布事件必须携带 verified/active/eligible 状态")
            if self.target.scope is not ProcedureScope.PLATFORM:
                raise ValueError("平台发布事件只能针对平台目标")
            if not self.reason:
                raise ValueError("平台发布事件必须携带非空原因")
            if self.source_digest is None or self.platform_digest is None:
                raise ValueError("平台发布事件必须携带来源与平台 digest")
            if self.audience != "admin_gray":
                # 普通用户受众必须由独立的受众变更命令产生，不能借发布扩大权限。
                raise ValueError("平台发布事件受众固定为 admin_gray")
            if (
                self.platform_validation_run_id is None
                or self.signing_signature_digest is None
                or self.signing_public_key_sha256 is None
            ):
                raise ValueError("平台发布事件必须引用平台验证与签名证据")
            return self
        if self.event_type == "audience_changed":
            # 受众变更不改变三轴状态，只改变已发布平台能力的可见范围。
            if (
                self.maturity is not CapabilityMaturity.VERIFIED
                or self.lifecycle is not CapabilityLifecycle.ACTIVE
                or self.eligibility is not CapabilityEligibility.ELIGIBLE
            ):
                raise ValueError("受众变更事件必须携带 verified/active/eligible 状态")
            if self.target.scope is not ProcedureScope.PLATFORM:
                raise ValueError("受众变更事件只能针对平台目标")
            if not self.reason:
                raise ValueError("受众变更事件必须携带非空原因")
            if self.audience is None:
                raise ValueError("受众变更事件必须携带新受众")
            if (
                self.source_digest is not None
                or self.platform_digest is not None
                or self.platform_validation_run_id is not None
                or self.signing_signature_digest is not None
                or self.signing_public_key_sha256 is not None
            ):
                raise ValueError("受众变更事件不得携带候选/验证/签名字段")
            return self
        if self.event_type == "lifecycle_changed":
            # 弃用/撤销/恢复只改变生命周期轴；成熟度与运行资格不借道变化。
            if self.maturity is not CapabilityMaturity.VERIFIED:
                raise ValueError("生命周期变更事件必须携带 verified 成熟度")
            if self.eligibility not in {
                CapabilityEligibility.ELIGIBLE,
                CapabilityEligibility.QUARANTINED,
            }:
                # 携带当前运行资格快照：隔离中的包被弃用/撤销时，事件快照
                # 必须与当时投影一致，不得冒充 eligible（AC7 预期状态真实性）。
                raise ValueError("生命周期变更事件必须携带当前运行资格快照")
            if not self.reason:
                raise ValueError("生命周期变更事件必须携带非空原因")
            if (
                self.source_validation_run_id is not None
                or self.source_supply_chain_evidence_id is not None
            ):
                raise ValueError("生命周期变更事件不得携带晋级证据引用")
            if any(field is not None for field in audit_fields):
                raise ValueError("生命周期变更事件不得携带审计查看字段")
            if any(field is not None for field in platform_fields):
                raise ValueError("生命周期变更事件不得携带平台发布字段")
            if any(field is not None for field in lifecycle_fields):
                raise ValueError("生命周期变更事件不得携带其他治理字段")
            return self
        if self.event_type == "eligibility_changed":
            # 隔离/解除隔离只改变运行资格轴；已撤销的能力不适用隔离语义。
            if self.maturity is not CapabilityMaturity.VERIFIED:
                raise ValueError("运行资格变更事件必须携带 verified 成熟度")
            if self.lifecycle not in {
                CapabilityLifecycle.ACTIVE,
                CapabilityLifecycle.DEPRECATED,
            }:
                raise ValueError(
                    "运行资格变更事件的生命周期必须为 active 或 deprecated"
                )
            if not self.reason:
                raise ValueError("运行资格变更事件必须携带非空原因")
            if (
                self.source_validation_run_id is not None
                or self.source_supply_chain_evidence_id is not None
            ):
                raise ValueError("运行资格变更事件不得携带晋级证据引用")
            if any(field is not None for field in audit_fields):
                raise ValueError("运行资格变更事件不得携带审计查看字段")
            if any(field is not None for field in platform_fields):
                raise ValueError("运行资格变更事件不得携带平台发布字段")
            if any(field is not None for field in lifecycle_fields):
                raise ValueError("运行资格变更事件不得携带其他治理字段")
            return self
        if self.event_type == "risk_accepted":
            # 限期风险接受：隔离之后恢复到 eligible 的有界事实。
            if (
                self.maturity is not CapabilityMaturity.VERIFIED
                or self.lifecycle
                not in {
                    CapabilityLifecycle.ACTIVE,
                    CapabilityLifecycle.DEPRECATED,
                }
                or self.eligibility is not CapabilityEligibility.ELIGIBLE
            ):
                raise ValueError(
                    "风险接受事件必须携带 verified/(active|deprecated)/eligible 状态"
                )
            if self.expires_at is None:
                raise ValueError("风险接受事件必须携带到期时间")
            if self.finding_ref is None:
                raise ValueError("风险接受事件必须引用验证运行证据")
            if self.recommended_version is not None:
                raise ValueError("风险接受事件不得携带推荐版本")
            if not self.reason:
                raise ValueError("风险接受事件必须携带非空原因")
            if (
                self.source_validation_run_id is not None
                or self.source_supply_chain_evidence_id is not None
            ):
                raise ValueError("风险接受事件不得携带晋级证据引用")
            if any(field is not None for field in audit_fields):
                raise ValueError("风险接受事件不得携带审计查看字段")
            if any(field is not None for field in platform_fields):
                raise ValueError("风险接受事件不得携带平台发布字段")
            return self
        if self.event_type == "recommendation_changed":
            # 回滚只改变新任务推荐指针，不改变任何三轴事实。
            if self.target.scope is not ProcedureScope.PLATFORM:
                raise ValueError("推荐指针变更事件只能针对平台目标")
            if (
                self.maturity is not CapabilityMaturity.VERIFIED
                or self.lifecycle is not CapabilityLifecycle.ACTIVE
                or self.eligibility is not CapabilityEligibility.ELIGIBLE
            ):
                raise ValueError(
                    "推荐指针变更事件必须携带 verified/active/eligible 状态"
                )
            if self.recommended_version is None:
                raise ValueError("推荐指针变更事件必须携带推荐版本")
            if self.expires_at is not None or self.finding_ref is not None:
                raise ValueError("推荐指针变更事件不得携带风险接受字段")
            if not self.reason:
                raise ValueError("推荐指针变更事件必须携带非空原因")
            if (
                self.source_validation_run_id is not None
                or self.source_supply_chain_evidence_id is not None
            ):
                raise ValueError("推荐指针变更事件不得携带晋级证据引用")
            if any(field is not None for field in audit_fields):
                raise ValueError("推荐指针变更事件不得携带审计查看字段")
            if any(field is not None for field in platform_fields):
                raise ValueError("推荐指针变更事件不得携带平台发布字段")
            return self
        if self.event_type == "rescan_completed":
            # 手动重扫只追加供应链证据；三轴快照 = 写入时刻投影，不借道变化
            # （触发隔离时快照携带 quarantined，与隔离事件写入后的投影一致）。
            if self.target.scope is not ProcedureScope.PLATFORM:
                raise ValueError("重扫事件只能针对平台目标")
            if self.maturity is not CapabilityMaturity.VERIFIED:
                raise ValueError("重扫事件必须携带 verified 成熟度")
            if not self.reason:
                raise ValueError("重扫事件必须携带非空原因")
            if self.source_supply_chain_evidence_id is None:
                raise ValueError("重扫事件必须引用新供应链证据")
            if self.source_validation_run_id is not None:
                raise ValueError("重扫事件不得携带验证运行引用")
            if any(field is not None for field in audit_fields):
                raise ValueError("重扫事件不得携带审计查看字段")
            if any(field is not None for field in platform_fields):
                raise ValueError("重扫事件不得携带平台发布字段")
            if any(field is not None for field in lifecycle_fields):
                raise ValueError("重扫事件不得携带其他治理字段")
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


class GovernanceCommandOutcome(BaseModel):
    """治理状态命令的显式结果；rejected 的 gaps 给出脱敏字面量原因。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["applied", "already_applied", "rejected"]
    gaps: tuple[str, ...] = ()
    event: CapabilityGovernanceEvent | None = None


class AuditViewOutcome(BaseModel):
    """审计查看命令的显式结果；失败也返回记录，保证“看过”不可抵赖。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["succeeded", "failed"]
    content: str | None = None
    truncated: bool = False
    failure_reason: str | None = None
    event: CapabilityGovernanceEvent

    @model_validator(mode="after")
    def validate_outcome(self) -> "AuditViewOutcome":
        if self.event.result != self.status:
            raise ValueError("审计查看结果必须与审计记录一致")
        if self.status == "succeeded":
            if self.content is None:
                raise ValueError("成功的审计查看必须携带正文")
            if self.failure_reason is not None:
                raise ValueError("成功的审计查看不得携带失败原因")
        else:
            if self.content is not None:
                raise ValueError("失败的审计查看不得携带正文")
            if self.failure_reason is None:
                raise ValueError("失败的审计查看必须携带类型化失败原因")
            if self.failure_reason != self.event.failure_reason:
                raise ValueError("审计查看失败原因必须与审计记录一致")
        return self


class BusinessContent(BaseModel):
    """审计查看的正文对象；只含内容与受控元数据，失败时给出类型化原因。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["succeeded", "failed"]
    subject_type: AuditSubjectType
    content: str = ""
    content_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    size_bytes: int = Field(default=0, ge=0)
    truncated: bool = False
    failure_reason: str | None = None

    @model_validator(mode="after")
    def validate_content(self) -> "BusinessContent":
        if self.status == "succeeded":
            if not self.content_sha256:
                raise ValueError("成功读取的正文必须携带内容 hash")
            if self.failure_reason is not None:
                raise ValueError("成功读取的正文不得携带失败原因")
        else:
            if self.failure_reason is None:
                raise ValueError("失败读取的正文必须携带类型化原因")
            if self.content_sha256 is not None:
                raise ValueError("失败读取的正文不得携带内容 hash")
            if self.content:
                raise ValueError("失败读取的正文不得携带内容")
        return self


class CapabilityTaskMetadata(BaseModel):
    """任务管理元数据（脱敏白名单）；不含标题、Prompt 或任何文件内容。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=1)
    owner_id: str = Field(min_length=1, max_length=120)
    task_status: str = Field(min_length=1, max_length=60)
    created_at: str
    updated_at: str
    input_count: int = Field(default=0, ge=0)
    input_types: tuple[str, ...] = ()
    output_count: int = Field(default=0, ge=0)
    output_formats: tuple[str, ...] = ()


class AdminReviewItem(BaseModel):
    """管理员审核聚合项；字段即脱敏白名单，业务正文永不进入此模型。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pack_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    scope: ProcedureScope
    maturity: CapabilityMaturity
    lifecycle: CapabilityLifecycle
    eligibility: CapabilityEligibility
    source: Literal["governance_event", "legacy_compat"]
    owner_id: str | None = None
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    promotion_gaps: tuple[PromotionGap, ...] = ()
    validation: CapabilityValidationRun | None = None
    supply_chain: CapabilitySupplyChainEvidence | None = None
    task_metadata: CapabilityTaskMetadata | None = None
    audit_history: tuple[CapabilityGovernanceEvent, ...] = ()


class CapabilityGovernanceProjection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target: CapabilityGovernanceTarget
    maturity: CapabilityMaturity
    lifecycle: CapabilityLifecycle
    eligibility: CapabilityEligibility
    source: Literal["governance_event", "legacy_compat"]
    # 平台能力受众；#12 发布固定 admin_gray，受众变更命令是唯一改变途径。
    audience: CapabilityAudience | None = None
    # 新任务推荐指针（#14 回滚命令折叠）；None 表示无显式指针。
    recommended_version: str | None = None


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
    audience: CapabilityAudience | None = None

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
            audience=projection.audience,
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


class PlatformValidationStep(StrEnum):
    """平台快照验证六步；不含个人任务重放，脱敏快照与个人证据语义分离。"""

    SYNTHETIC_SMOKE = "synthetic_smoke"
    FAIL_CLOSED = "fail_closed"
    TRIVY = "trivy"
    SYFT = "syft"
    MOUNT_PROBE = "mount_probe"
    INDEPENDENT_VERIFIER = "independent_verifier"


class PlatformValidationEvidence(BaseModel):
    """平台验证步骤的受控证据摘要；正文、Secret 和宿主路径不得进入此记录。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    step: PlatformValidationStep
    status: ValidationStepStatus
    evidence_ref: str = Field(
        pattern=r"^evidence://[A-Za-z0-9._/-]{1,500}$"
    )
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    summary: str = Field(min_length=1, max_length=300)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PlatformValidationRun(BaseModel):
    """绑定平台快照 digest 的不可变验证运行；六步全过 + 签名证据是发布前置。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(default_factory=lambda: f"pfval_{uuid.uuid4().hex[:20]}")
    actor_id: str = Field(min_length=1, max_length=120)
    actor_role: Literal["user", "admin", "superadmin"]
    idempotency_key: str = Field(min_length=1, max_length=200)
    target: CapabilityGovernanceTarget
    status: ValidationRunStatus = ValidationRunStatus.QUEUED
    evidence: tuple[PlatformValidationEvidence, ...] = ()
    # 签名证据随运行保存：发布命令从全绿运行读取，不复用个人签名。
    signing_signature_digest: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    signing_public_key_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def validate_platform_target(self) -> "PlatformValidationRun":
        if self.target.scope is not ProcedureScope.PLATFORM:
            raise ValueError("平台验证运行只能针对平台快照目标")
        return self


class PlatformSnapshot(BaseModel):
    """脱敏平台快照的冻结身份；manifest 摘要只列保留字段名，不含业务值。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pack_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    source_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    platform_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    manifest_summary: tuple[str, ...] = ()


class PlatformCandidateOutcome(BaseModel):
    """平台候选提交的显式结果；调用方必须区分新建、幂等命中与拒绝。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["created", "already_submitted", "rejected"]
    snapshot: PlatformSnapshot | None = None
    gaps: tuple[str, ...] = ()
    event: CapabilityGovernanceEvent | None = None

    @model_validator(mode="after")
    def validate_outcome_shape(self) -> "PlatformCandidateOutcome":
        if self.status == "rejected":
            if not self.gaps or self.snapshot is not None or self.event is not None:
                raise ValueError("拒绝的候选必须携带缺口且不携带快照或事件")
        else:
            if self.snapshot is None or self.event is None:
                raise ValueError("候选结果必须携带快照与事件")
        return self


class PublishOutcome(BaseModel):
    """平台发布的显式结果；发布只对候选全绿的平台版本生效。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["published", "already_published", "not_ready"]
    gaps: tuple[str, ...] = ()
    event: CapabilityGovernanceEvent | None = None

    @model_validator(mode="after")
    def validate_outcome_shape(self) -> "PublishOutcome":
        if self.status == "not_ready":
            if not self.gaps or self.event is not None:
                raise ValueError("未就绪的发布必须携带缺口且不携带事件")
        elif self.event is None:
            raise ValueError("发布结果必须携带事件")
        return self


class AudienceOutcome(BaseModel):
    """受众变更命令的显式结果；只改变平台能力可见范围。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["changed", "already"]
    event: CapabilityGovernanceEvent

    @model_validator(mode="after")
    def validate_outcome_shape(self) -> "AudienceOutcome":
        if self.event.event_type != "audience_changed":
            raise ValueError("受众变更结果必须携带受众变更事件")
        return self


class PlatformCandidateSummary(BaseModel):
    """管理员候选列表的脱敏摘要；含验证六步与签名状态，不含任何业务正文。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pack_id: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    source_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    platform_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    validation_status: str = Field(min_length=1, max_length=60)
    steps_passed: int = Field(default=0, ge=0)
    steps_total: int = Field(default=6, ge=0, le=6)
    signed: bool = False
    submitted_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    reason: str = Field(min_length=1, max_length=1000)
