# -*- coding: utf-8 -*-
"""AC07-09 S2：投影折叠升级（逐轴事实、风险接受惰性到期、推荐指针）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.capability_catalog import (
    CapabilityCatalog,
    CatalogActor,
    InMemoryCapabilityCatalogRepository,
)
from src.capability_governance import (
    CapabilityEligibility,
    CapabilityGovernance,
    CapabilityGovernanceEvent,
    CapabilityGovernanceTarget,
    CapabilityLifecycle,
    CapabilityMaturity,
    InMemoryCapabilityGovernanceRepository,
)
from src.conversation_steering import (
    CapabilityMaturity as LegacyCapabilityMaturity,
    CapabilityPack,
    ProcedureScope,
)


def _platform_target(digest_char: str = "a") -> CapabilityGovernanceTarget:
    return CapabilityGovernanceTarget(
        owner_id=None,
        scope=ProcedureScope.PLATFORM,
        pack_id="gray-python-table",
        version="1.0.0",
        digest="sha256:" + digest_char * 64,
    )


def _governance_with_pack(
    repository: InMemoryCapabilityGovernanceRepository,
) -> tuple[CapabilityGovernance, CapabilityPack]:
    catalog_repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(catalog_repository)
    pack = CapabilityPack(
        pack_id="gray-python-table",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
        scope=ProcedureScope.PLATFORM,
        maturity=LegacyCapabilityMaturity.VERIFIED,
        owner_id=None,
    )
    catalog_repository.save_pack(pack)
    return CapabilityGovernance(catalog, repository), pack


_sequence = 0


def _event(
    repository: InMemoryCapabilityGovernanceRepository,
    target: CapabilityGovernanceTarget,
    event_type: str,
    *,
    lifecycle=CapabilityLifecycle.ACTIVE,
    eligibility=CapabilityEligibility.ELIGIBLE,
    expires_at: datetime | None = None,
    recommended_version: str | None = None,
    occurred_at: datetime | None = None,
) -> CapabilityGovernanceEvent:
    global _sequence
    _sequence += 1
    fields: dict = {
        "event_type": event_type,
        "idempotency_key": f"gov:{event_type}:{_sequence}",
        "target": target,
        "maturity": CapabilityMaturity.VERIFIED,
        "lifecycle": lifecycle,
        "eligibility": eligibility,
        "actor_id": "admin-a",
        "actor_role": "admin",
        "reason": f"测试事件 {event_type}",
    }
    if expires_at is not None:
        fields["expires_at"] = expires_at
    if event_type == "risk_accepted":
        fields["finding_ref"] = "capval_a1b2c3d4e5f6a1b2c3d4"
    if recommended_version is not None:
        fields["recommended_version"] = recommended_version
    if occurred_at is not None:
        fields["occurred_at"] = occurred_at
    event = CapabilityGovernanceEvent(**fields)
    repository.save_governance_event(event)
    return event


class TestAxisFolding:
    def test_lifecycle_then_eligibility_fold_independently(self) -> None:
        """逐轴事实：弃用后再隔离，两轴分别取各自最后事件值。"""
        repository = InMemoryCapabilityGovernanceRepository()
        governance, pack = _governance_with_pack(repository)
        target = _platform_target()
        _event(
            repository,
            target,
            "lifecycle_changed",
            lifecycle=CapabilityLifecycle.DEPRECATED,
        )
        _event(
            repository,
            target,
            "eligibility_changed",
            eligibility=CapabilityEligibility.QUARANTINED,
        )
        projection = governance.runtime_projection_for_pack(pack)
        assert projection.lifecycle is CapabilityLifecycle.DEPRECATED
        assert projection.eligibility is CapabilityEligibility.QUARANTINED
        assert projection.maturity is CapabilityMaturity.VERIFIED

    def test_restore_after_revoke_folds_active(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, pack = _governance_with_pack(repository)
        target = _platform_target()
        now = datetime.now(timezone.utc)
        _event(
            repository,
            target,
            "lifecycle_changed",
            lifecycle=CapabilityLifecycle.REVOKED,
            occurred_at=now - timedelta(days=2),
        )
        _event(
            repository,
            target,
            "lifecycle_changed",
            lifecycle=CapabilityLifecycle.ACTIVE,
            occurred_at=now - timedelta(days=1),
        )
        projection = governance.runtime_projection_for_pack(pack)
        assert projection.lifecycle is CapabilityLifecycle.ACTIVE

    def test_recommendation_folds_pointer(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, pack = _governance_with_pack(repository)
        target = _platform_target()
        now = datetime.now(timezone.utc)
        _event(
            repository,
            target,
            "recommendation_changed",
            recommended_version="1.0.0",
            occurred_at=now - timedelta(days=2),
        )
        _event(
            repository,
            target,
            "recommendation_changed",
            recommended_version="0.9.0",
            occurred_at=now - timedelta(days=1),
        )
        projection = governance.runtime_projection_for_pack(pack)
        assert projection.recommended_version == "0.9.0"

    def test_no_recommendation_event_means_no_pointer(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, pack = _governance_with_pack(repository)
        _event(repository, _platform_target(), "lifecycle_changed")
        projection = governance.runtime_projection_for_pack(pack)
        assert projection.recommended_version is None


class TestRiskAcceptanceLazyExpiry:
    def test_unexpired_risk_acceptance_projects_eligible(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, pack = _governance_with_pack(repository)
        target = _platform_target()
        now = datetime.now(timezone.utc)
        _event(
            repository,
            target,
            "eligibility_changed",
            eligibility=CapabilityEligibility.QUARANTINED,
            occurred_at=now - timedelta(days=2),
        )
        _event(
            repository,
            target,
            "risk_accepted",
            expires_at=now + timedelta(days=28),
            occurred_at=now - timedelta(days=1),
        )
        projection = governance.runtime_projection_for_pack(pack)
        assert projection.eligibility is CapabilityEligibility.ELIGIBLE

    def test_expired_risk_acceptance_projects_quarantined(self) -> None:
        """Q5A 惰性到期：读取时发现过期即按 quarantined 投影，不写事件。"""
        repository = InMemoryCapabilityGovernanceRepository()
        governance, pack = _governance_with_pack(repository)
        target = _platform_target()
        now = datetime.now(timezone.utc)
        _event(
            repository,
            target,
            "eligibility_changed",
            eligibility=CapabilityEligibility.QUARANTINED,
            occurred_at=now - timedelta(days=35),
        )
        _event(
            repository,
            target,
            "risk_accepted",
            expires_at=now - timedelta(days=5),
            occurred_at=now - timedelta(days=30),
        )
        projection = governance.runtime_projection_for_pack(pack)
        assert projection.eligibility is CapabilityEligibility.QUARANTINED
        # 到期是投影语义，不产生新事件（零写入）。
        assert len(repository.list_events(target)) == 2

    def test_risk_acceptance_only_affects_eligibility_axis(self) -> None:
        """接受不改变生命周期轴；到期后弃用状态保持。"""
        repository = InMemoryCapabilityGovernanceRepository()
        governance, pack = _governance_with_pack(repository)
        target = _platform_target()
        now = datetime.now(timezone.utc)
        _event(
            repository,
            target,
            "lifecycle_changed",
            lifecycle=CapabilityLifecycle.DEPRECATED,
            occurred_at=now - timedelta(days=40),
        )
        _event(
            repository,
            target,
            "risk_accepted",
            expires_at=now - timedelta(days=10),
            occurred_at=now - timedelta(days=40),
        )
        projection = governance.runtime_projection_for_pack(pack)
        assert projection.lifecycle is CapabilityLifecycle.DEPRECATED
        assert projection.eligibility is CapabilityEligibility.QUARANTINED
