# -*- coding: utf-8 -*-
"""CandidateVerification 的冻结领域记录。"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


_HASH_PATTERN = r"^[0-9a-f]{64}$"


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AttemptReason(str, Enum):
    INITIAL = "initial"
    SEMANTIC_INCONCLUSIVE = "semantic_inconclusive"
    RULESET_CHANGED = "ruleset_changed"
    LEGACY_REBASELINE = "legacy_rebaseline"
    PROVIDER_OUTCOME_RECOVERY = "provider_outcome_recovery"


class AttemptStatus(str, Enum):
    REQUESTED = "requested"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    OUTCOME_UNKNOWN = "outcome_unknown"
    CANCELLED = "cancelled"


class RulesetIdentityStatus(str, Enum):
    VERSIONED = "versioned"
    LEGACY_UNVERSIONED = "legacy_unversioned"


class HistoricalReverificationPurpose(str, Enum):
    SEMANTIC_INCONCLUSIVE_REVERIFICATION = (
        "semantic_inconclusive_reverification"
    )


class HistoricalReverificationEvidence(FrozenModel):
    """只保存历史重验所需的身份摘要，不复制正文或 Secret。"""

    owner_id: str = Field(min_length=1, max_length=120)
    task_id: str = Field(min_length=1, max_length=120)
    revision: int = Field(ge=1)
    run_id: str = Field(min_length=1, max_length=160)
    purpose: HistoricalReverificationPurpose
    legacy_runtime_created_at: datetime
    runtime_routing_migration_id: Literal["0001_runtime_routing"]
    runtime_routing_applied_at: datetime
    runtime_routing_backup_sha256: str = Field(pattern=_HASH_PATTERN)
    runtime_request_hash: str = Field(pattern=_HASH_PATTERN)
    task_revision_hash: str = Field(pattern=_HASH_PATTERN)
    source_binding_hash: str = Field(pattern=_HASH_PATTERN)
    runtime_event_chain_hash: str = Field(pattern=_HASH_PATTERN)
    candidate_set_hash: str = Field(pattern=_HASH_PATTERN)
    candidate_manifest_hash: str = Field(pattern=_HASH_PATTERN)
    goal_contract_hash: str = Field(pattern=_HASH_PATTERN)
    delivery_spec_hash: str = Field(pattern=_HASH_PATTERN)
    previous_attempt_id: str = Field(min_length=1, max_length=160)
    previous_report_hash: str = Field(pattern=_HASH_PATTERN)
    connection_id: str = Field(min_length=1, max_length=120)
    connection_version: str = Field(min_length=1, max_length=120)
    model_id: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_historical_boundary(self) -> "HistoricalReverificationEvidence":
        if (
            self.legacy_runtime_created_at.tzinfo is None
            or self.runtime_routing_applied_at.tzinfo is None
        ):
            raise ValueError("历史重验时间必须包含时区")
        if self.legacy_runtime_created_at >= self.runtime_routing_applied_at:
            raise ValueError("只有 RuntimeRouting 上线前的任务可以恢复重验权威")
        return self


class HistoricalReverificationAuthority(FrozenModel):
    """Owner 当前追加的窄权威；它不是历史 RuntimeAssignment。"""

    authority_id: str = Field(
        pattern=r"^historical_authority_[0-9a-f]{64}$"
    )
    owner_id: str = Field(min_length=1, max_length=120)
    task_id: str = Field(min_length=1, max_length=120)
    revision: int = Field(ge=1)
    run_id: str = Field(min_length=1, max_length=160)
    purpose: HistoricalReverificationPurpose
    candidate_set_hash: str = Field(pattern=_HASH_PATTERN)
    evidence_manifest_json: str
    evidence_hash: str = Field(pattern=_HASH_PATTERN)
    actor_id: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(min_length=1, max_length=240)
    recorded_at: datetime

    @classmethod
    def build(
        cls,
        *,
        evidence: HistoricalReverificationEvidence,
        actor_id: str,
        idempotency_key: str,
        recorded_at: datetime,
    ) -> "HistoricalReverificationAuthority":
        manifest = json.dumps(
            evidence.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        evidence_hash = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
        return cls(
            authority_id="historical_authority_" + evidence_hash,
            owner_id=evidence.owner_id,
            task_id=evidence.task_id,
            revision=evidence.revision,
            run_id=evidence.run_id,
            purpose=evidence.purpose,
            candidate_set_hash=evidence.candidate_set_hash,
            evidence_manifest_json=manifest,
            evidence_hash=evidence_hash,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            recorded_at=recorded_at,
        )

    @model_validator(mode="after")
    def validate_authority_identity(self) -> "HistoricalReverificationAuthority":
        try:
            evidence = HistoricalReverificationEvidence.model_validate_json(
                self.evidence_manifest_json
            )
        except ValueError as exc:
            raise ValueError("历史重验 Evidence Manifest 无效") from exc
        canonical = json.dumps(
            evidence.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        evidence_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if canonical != self.evidence_manifest_json:
            raise ValueError("历史重验 Evidence Manifest 必须是规范化 JSON")
        if self.evidence_hash != evidence_hash:
            raise ValueError("历史重验 Evidence Manifest 摘要不一致")
        if self.authority_id != "historical_authority_" + evidence_hash:
            raise ValueError("历史重验 authority_id 与证据不一致")
        if (
            self.owner_id,
            self.task_id,
            self.revision,
            self.run_id,
            self.purpose,
            self.candidate_set_hash,
        ) != (
            evidence.owner_id,
            evidence.task_id,
            evidence.revision,
            evidence.run_id,
            evidence.purpose,
            evidence.candidate_set_hash,
        ):
            raise ValueError("历史重验权威与 Evidence Manifest 身份不一致")
        if self.actor_id != self.owner_id:
            raise ValueError("历史重验权威只能由 TaskOwner 记录")
        if self.recorded_at.tzinfo is None:
            raise ValueError("历史重验权威记录时间必须包含时区")
        return self


class HistoricalReverificationBinding(FrozenModel):
    """服务与权威 Adapter 之间的最小候选身份，不包含业务正文。"""

    candidate_set_hash: str = Field(pattern=_HASH_PATTERN)
    candidate_manifest_hash: str = Field(pattern=_HASH_PATTERN)
    goal_contract_hash: str = Field(pattern=_HASH_PATTERN)
    delivery_spec_hash: str = Field(pattern=_HASH_PATTERN)
    previous_attempt_id: str = Field(min_length=1, max_length=160)
    previous_report_hash: str = Field(pattern=_HASH_PATTERN)


class RebaselineAuthorizationEvidence(FrozenModel):
    """随 Legacy 再基线 Attempt 冻结的 TaskOwner 授权事实。"""

    authorization_text_version: Literal["legacy-rebaseline-v1"]
    owner_id: str = Field(min_length=1, max_length=120)
    task_id: str = Field(min_length=1, max_length=120)
    revision: int = Field(ge=1)
    run_id: str = Field(min_length=1, max_length=160)
    previous_attempt_id: str = Field(min_length=1, max_length=160)
    candidate_set_hash: str = Field(pattern=_HASH_PATTERN)
    target_ruleset_hash: str = Field(pattern=_HASH_PATTERN)
    actor_id: str = Field(min_length=1, max_length=120)
    legacy_ruleset_unknown_acknowledged: StrictBool
    external_api_confirmed: StrictBool
    authorized_at: datetime

    @model_validator(mode="after")
    def validate_owner_authorization(self) -> "RebaselineAuthorizationEvidence":
        if self.actor_id != self.owner_id:
            raise ValueError("Legacy 再基线只能由 TaskOwner 授权")
        if not self.legacy_ruleset_unknown_acknowledged:
            raise ValueError("Legacy 再基线必须确认旧规则身份未知")
        if self.authorized_at.tzinfo is None:
            raise ValueError("Legacy 再基线授权时间必须包含时区")
        return self

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class HistoricalAuthorityRecoveryOffer(FrozenModel):
    """Owner 在写命令前看到的精确恢复身份摘要。"""

    expected_evidence_hash: str = Field(pattern=_HASH_PATTERN)
    purpose: HistoricalReverificationPurpose
    owner_id: str = Field(min_length=1, max_length=120)
    task_id: str = Field(min_length=1, max_length=120)
    revision: int = Field(ge=1)
    run_id: str = Field(min_length=1, max_length=160)
    candidate_set_hash: str = Field(pattern=_HASH_PATTERN)
    explanation: str = Field(min_length=1, max_length=500)


class HistoricalAuthorityRecoveryConfirmation(FrozenModel):
    """请求内的窄确认；严格布尔值阻止字符串或整数被静默接受。"""

    expected_evidence_hash: str = Field(pattern=_HASH_PATTERN)
    acknowledge_no_historical_assignment: StrictBool
    acknowledge_reverification_only: StrictBool

    @model_validator(mode="after")
    def validate_acknowledgements(
        self,
    ) -> "HistoricalAuthorityRecoveryConfirmation":
        if not (
            self.acknowledge_no_historical_assignment
            and self.acknowledge_reverification_only
        ):
            raise ValueError("历史重验权威恢复必须明确确认两项边界")
        return self


class ReverificationBlocker(str, Enum):
    TASK_REVISION_DRIFT = "task_revision_drift"
    RUNTIME_ASSIGNMENT_DRIFT = "runtime_assignment_drift"
    HISTORICAL_AUTHORITY_RECOVERY_REQUIRED = (
        "historical_authority_recovery_required"
    )
    SOURCE_BINDING_DRIFT = "source_binding_drift"
    PROVIDER_BINDING_FORBIDDEN = "provider_binding_forbidden"
    PROVIDER_BINDING_UNAVAILABLE = "provider_binding_unavailable"
    AUTHORITY_UNAVAILABLE = "authority_unavailable"
    P0_BLOCKED = "p0_blocked"
    DELIVERY_EXISTS = "delivery_exists"
    CANDIDATE_DRIFT = "candidate_drift"
    SOURCE_DRIFT = "source_drift"
    ACTIVE_ATTEMPT = "active_attempt"
    OUTCOME_UNKNOWN = "outcome_unknown"
    LEGACY_UNVERSIONED = "legacy_unversioned"
    MANIFEST_DRIFT = "manifest_drift"
    GOAL_CONTRACT_DRIFT = "goal_contract_drift"
    DELIVERY_SPEC_DRIFT = "delivery_spec_drift"
    RULESET_UNAVAILABLE = "ruleset_unavailable"
    RULESET_UNCHANGED = "ruleset_unchanged"
    ALREADY_PASSED = "already_passed"
    PREVIOUS_CANCELLED = "previous_cancelled"
    SEMANTIC_RETRY_UNAVAILABLE = "semantic_retry_unavailable"


class ReverificationOffer(FrozenModel):
    """普通用户在任何重验写操作前可见的只读资格投影。"""

    eligible: bool
    reason: AttemptReason | None = None
    blockers: tuple[ReverificationBlocker, ...] = ()
    previous_attempt_id: str | None = None
    previous_status: AttemptStatus | None = None
    previous_reason: AttemptReason | None = None
    ruleset_identity_status: RulesetIdentityStatus | None = None
    ruleset_changed: bool | None = None
    ruleset_change_summary: str = Field(min_length=1, max_length=300)
    candidate_count: int = Field(ge=0)
    candidate_formats: tuple[str, ...] = ()
    candidate_set_hash: str = Field(pattern=_HASH_PATTERN)
    target_ruleset_hash: str | None = Field(default=None, pattern=_HASH_PATTERN)
    requires_provider: bool
    connection_id: str | None = None
    model_id: str | None = None
    egress_categories: tuple[str, ...] = ()
    egress_summary: str = Field(min_length=1, max_length=300)
    awaiting_publication: bool
    historical_authority_recovery: HistoricalAuthorityRecoveryOffer | None = None


class VerifierRulesetBinding(FrozenModel):
    """实际执行进程在验证前冻结的规则身份。"""

    verifier_ruleset_hash: str = Field(pattern=_HASH_PATTERN)
    verifier_code_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    verifier_source_hash: str = Field(pattern=_HASH_PATTERN)
    verifier_execution_identity_hash: str = Field(pattern=_HASH_PATTERN)
    verifier_ruleset_manifest_json: str

    @model_validator(mode="after")
    def validate_manifest(self) -> "VerifierRulesetBinding":
        try:
            manifest = json.loads(self.verifier_ruleset_manifest_json)
        except json.JSONDecodeError as exc:
            raise ValueError("VerifierRuleset Manifest 不是有效 JSON") from exc
        canonical = json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if canonical != self.verifier_ruleset_manifest_json:
            raise ValueError("VerifierRuleset Manifest 必须是规范化 JSON")
        if manifest.get("verifier_ruleset_hash") != self.verifier_ruleset_hash:
            raise ValueError("VerifierRuleset Manifest 与规则身份不一致")
        return self


class VerificationAttempt(FrozenModel):
    attempt_id: str = Field(min_length=1, max_length=160)
    owner_id: str = Field(min_length=1, max_length=120)
    task_id: str = Field(min_length=1, max_length=120)
    revision: int = Field(ge=1)
    run_id: str = Field(min_length=1, max_length=160)
    previous_attempt_id: str | None = Field(default=None, min_length=1)
    reason_code: AttemptReason
    candidate_set_hash: str = Field(pattern=_HASH_PATTERN)
    manifest_hash: str | None = Field(default=None, pattern=_HASH_PATTERN)
    goal_contract_hash: str | None = Field(default=None, pattern=_HASH_PATTERN)
    delivery_spec_hash: str | None = Field(default=None, pattern=_HASH_PATTERN)
    ruleset_identity_status: RulesetIdentityStatus = (
        RulesetIdentityStatus.VERSIONED
    )
    verifier_ruleset_hash: str | None = Field(
        default=None,
        pattern=_HASH_PATTERN,
    )
    verifier_code_commit: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{40}$",
    )
    verifier_source_hash: str | None = Field(
        default=None,
        pattern=_HASH_PATTERN,
    )
    verifier_execution_identity_hash: str | None = Field(
        default=None,
        pattern=_HASH_PATTERN,
    )
    verifier_ruleset_manifest_json: str | None = None
    actor_id: str = Field(min_length=1, max_length=120)
    connection_id: str | None = Field(default=None, min_length=1, max_length=120)
    connection_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
    )
    model_id: str | None = Field(default=None, min_length=1, max_length=240)
    egress_confirmed_at: datetime | None = None
    provider_attempt_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
    )
    idempotency_key: str = Field(min_length=1, max_length=240)
    request_hash: str = Field(pattern=_HASH_PATTERN)
    rebaseline_authorization_json: str | None = None
    rebaseline_authorization_hash: str | None = Field(
        default=None,
        pattern=_HASH_PATTERN,
    )
    status: AttemptStatus
    report_json: str | None = None
    report_hash: str | None = Field(default=None, pattern=_HASH_PATTERN)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def validate_ruleset_identity(self) -> "VerificationAttempt":
        versioned_values = (
            self.manifest_hash,
            self.goal_contract_hash,
            self.delivery_spec_hash,
            self.verifier_ruleset_hash,
            self.verifier_code_commit,
            self.verifier_source_hash,
            self.verifier_execution_identity_hash,
            self.verifier_ruleset_manifest_json,
        )
        if self.ruleset_identity_status is RulesetIdentityStatus.VERSIONED:
            if any(value is None for value in versioned_values):
                raise ValueError("versioned Attempt 必须冻结完整 Verifier 身份")
        elif any(
            value is not None
            for value in (
                self.verifier_ruleset_hash,
                self.verifier_code_commit,
                self.verifier_source_hash,
                self.verifier_execution_identity_hash,
                self.verifier_ruleset_manifest_json,
            )
        ):
            raise ValueError("legacy_unversioned Attempt 不得补猜 Verifier 身份")
        if self.verifier_ruleset_manifest_json is not None:
            try:
                manifest = json.loads(self.verifier_ruleset_manifest_json)
            except json.JSONDecodeError as exc:
                raise ValueError("VerifierRuleset Manifest 不是有效 JSON") from exc
            canonical = json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if canonical != self.verifier_ruleset_manifest_json:
                raise ValueError("VerifierRuleset Manifest 必须是规范化 JSON")
            if manifest.get("verifier_ruleset_hash") != self.verifier_ruleset_hash:
                raise ValueError("VerifierRuleset Manifest 与规则身份不一致")
        authorization_values = (
            self.rebaseline_authorization_json,
            self.rebaseline_authorization_hash,
        )
        if self.reason_code is AttemptReason.LEGACY_REBASELINE:
            if any(value is None for value in authorization_values):
                raise ValueError("Legacy 再基线 Attempt 必须冻结 Owner 授权证据")
            try:
                authorization = json.loads(
                    str(self.rebaseline_authorization_json)
                )
            except json.JSONDecodeError as exc:
                raise ValueError("Legacy 再基线授权证据不是有效 JSON") from exc
            canonical_authorization = json.dumps(
                authorization,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if canonical_authorization != self.rebaseline_authorization_json:
                raise ValueError("Legacy 再基线授权证据必须是规范化 JSON")
            if (
                hashlib.sha256(
                    canonical_authorization.encode("utf-8")
                ).hexdigest()
                != self.rebaseline_authorization_hash
            ):
                raise ValueError("Legacy 再基线授权证据哈希不一致")
        elif any(value is not None for value in authorization_values):
            raise ValueError("非 Legacy 再基线 Attempt 不得携带再基线授权证据")
        return self
