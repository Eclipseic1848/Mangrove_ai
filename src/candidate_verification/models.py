# -*- coding: utf-8 -*-
"""CandidateVerification 的冻结领域记录。"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
import json

from pydantic import BaseModel, ConfigDict, Field, model_validator


_HASH_PATTERN = r"^[0-9a-f]{64}$"


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AttemptReason(str, Enum):
    INITIAL = "initial"
    SEMANTIC_INCONCLUSIVE = "semantic_inconclusive"
    RULESET_CHANGED = "ruleset_changed"


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


class ReverificationBlocker(str, Enum):
    TASK_REVISION_DRIFT = "task_revision_drift"
    RUNTIME_ASSIGNMENT_DRIFT = "runtime_assignment_drift"
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
    requires_provider: bool
    connection_id: str | None = None
    model_id: str | None = None
    egress_categories: tuple[str, ...] = ()
    egress_summary: str = Field(min_length=1, max_length=300)
    awaiting_publication: bool


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
        return self
