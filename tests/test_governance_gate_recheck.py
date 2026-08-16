# -*- coding: utf-8 -*-
"""AC07-09 S4：发布门与受众变更门的漏洞库时效复查。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.capability_catalog import (
    CapabilityCatalog,
    CapabilityPackRef,
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
    CapabilitySupplyChainEvidence,
    InMemoryCapabilityGovernanceRepository,
    PlatformSnapshot,
    PlatformValidationEvidence,
    PlatformValidationRun,
    PlatformValidationStep,
    SupplyChainEvidenceStatus,
    TrivyDatabaseMetadata,
    ValidationRunStatus,
    ValidationStepStatus,
)
from src.conversation_steering import (
    CapabilityMaturity as LegacyCapabilityMaturity,
    CapabilityPack,
    ProcedureScope,
)


def _admin() -> CatalogActor:
    return CatalogActor(owner_id="admin-a", role="admin")


def _personal_target() -> CapabilityGovernanceTarget:
    return CapabilityGovernanceTarget(
        owner_id="owner-a",
        scope=ProcedureScope.PERSONAL,
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
    )


def _platform_target() -> CapabilityGovernanceTarget:
    return CapabilityGovernanceTarget(
        owner_id=None,
        scope=ProcedureScope.PLATFORM,
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + "b" * 64,
    )


class _StubGenerator:
    def generate(self, pack):
        return PlatformSnapshot(
            pack_id=pack.pack_id,
            version=pack.version,
            source_digest=pack.digest,
            platform_digest="sha256:" + "b" * 64,
            manifest_summary=("schema_version", "name"),
        )


class _StubPublisher:
    def __init__(self):
        self.calls: list = []

    def save_pack(self, pack):
        self.calls.append(pack)
        return pack


def _fixture() -> tuple[
    CapabilityGovernance,
    InMemoryCapabilityGovernanceRepository,
    CapabilityGovernanceTarget,
]:
    """个人 verified pack + 平台候选 + 六步全绿运行 + 签名证据。"""
    repository = InMemoryCapabilityGovernanceRepository()
    catalog_repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(catalog_repository)
    governance = CapabilityGovernance(
        catalog,
        repository,
        platform_snapshot_generator=_StubGenerator(),
        platform_publisher=_StubPublisher(),
    )
    owner = CatalogActor(owner_id="owner-a", role="user")
    personal = _personal_target()
    catalog.register_pack(
        owner,
        CapabilityPack(
            pack_id=personal.pack_id,
            version=personal.version,
            digest=personal.digest,
            scope=ProcedureScope.PERSONAL,
            maturity=LegacyCapabilityMaturity.DRAFT,
            owner_id="owner-a",
        ),
    )
    repository.save_promotion_event(
        CapabilityGovernanceEvent(
            event_type="promoted_to_verified",
            idempotency_key="promotion:run-a",
            target=personal,
            maturity=CapabilityMaturity.VERIFIED,
            actor_id="owner-a",
            actor_role="user",
            source_validation_run_id="capval_a1b2c3d4e5f6a1b2c3d4",
            source_supply_chain_evidence_id="supply_" + "a" * 20,
        )
    )
    platform = _platform_target()
    repository.save_platform_event(
        CapabilityGovernanceEvent(
            event_type="platform_candidate",
            idempotency_key="candidate:one",
            target=platform,
            maturity=CapabilityMaturity.VERIFIED,
            actor_id="admin-a",
            actor_role="admin",
            reason="候选",
            source_digest=personal.digest,
            platform_digest=platform.digest,
        )
    )
    repository.create_platform_validation_run(
        PlatformValidationRun(
            run_id="pfval_" + "a" * 20,
            actor_id="admin-a",
            actor_role="admin",
            idempotency_key="pfval-one",
            target=platform,
            status=ValidationRunStatus.QUEUED,
        )
    )
    repository.save_platform_validation_run(
        PlatformValidationRun(
            run_id="pfval_" + "a" * 20,
            actor_id="admin-a",
            actor_role="admin",
            idempotency_key="pfval-one",
            target=platform,
            status=ValidationRunStatus.SUCCEEDED,
            evidence=tuple(
                PlatformValidationEvidence(
                    step=step,
                    status=ValidationStepStatus.PASSED,
                    evidence_ref=f"evidence://platform/run/{step.value}",
                    evidence_sha256="e" * 64,
                    summary="平台验证步骤已通过",
                )
                for step in PlatformValidationStep
            ),
            signing_signature_digest="sha256:" + "c" * 64,
            signing_public_key_sha256="d" * 64,
        )
    )
    return governance, repository, platform


def _evidence(
    repository: InMemoryCapabilityGovernanceRepository,
    target: CapabilityGovernanceTarget,
    *,
    updated_at_days_ago: int = 0,
    evidence_char: str = "b",
) -> None:
    repository.save_supply_chain_evidence(
        CapabilitySupplyChainEvidence(
            evidence_id="supply_" + evidence_char * 20,
            target=target,
            subject_digest=target.digest,
            status=SupplyChainEvidenceStatus.PASSED,
            blockers=(),
            secret_count=0,
            critical_count=0,
            fixable_high_count=0,
            misconfiguration_failure_count=0,
            trivy_version="0.70.0",
            trivy_config_sha256="c" * 64,
            trivy_result_sha256="d" * 64,
            trivy_database=TrivyDatabaseMetadata(
                version=2,
                updated_at=datetime.now(timezone.utc)
                - timedelta(days=updated_at_days_ago),
            ),
            syft_version="1.50.0",
            syft_json_sha256="e" * 64,
            cyclonedx_json_sha256="f" * 64,
            cyclonedx_spec_version="1.6",
        )
    )


def _ref(target: CapabilityGovernanceTarget) -> CapabilityPackRef:
    return CapabilityPackRef(
        pack_id=target.pack_id,
        version=target.version,
        digest=target.digest,
    )


class TestPublishGateTrivyRecheck:
    def test_fresh_evidence_publishes(self) -> None:
        governance, repository, platform = _fixture()
        _evidence(repository, platform)
        outcome = governance.publish_platform(
            _admin(),
            pack_ref=_ref(platform),
            reason="发布",
            idempotency_key="publish:one",
        )
        assert outcome.status == "published"

    def test_stale_trivy_db_rejects_publish(self) -> None:
        """Issue AC6：漏洞库过期阻止新发布（判定按内容 UpdatedAt）。"""
        governance, repository, platform = _fixture()
        _evidence(repository, platform, updated_at_days_ago=8)
        outcome = governance.publish_platform(
            _admin(),
            pack_ref=_ref(platform),
            reason="发布",
            idempotency_key="publish:stale",
        )
        assert outcome.status == "not_ready"
        assert "trivy_database_stale" in outcome.gaps

    def test_missing_evidence_rejects_publish(self) -> None:
        governance, _, platform = _fixture()
        outcome = governance.publish_platform(
            _admin(),
            pack_ref=_ref(platform),
            reason="发布",
            idempotency_key="publish:no-evidence",
        )
        assert outcome.status == "not_ready"
        assert "supply_chain_evidence_missing" in outcome.gaps


class TestAudienceGateTrivyRecheck:
    def _published_fixture(self):
        governance, repository, platform = _fixture()
        _evidence(repository, platform)
        governance.publish_platform(
            _admin(),
            pack_ref=_ref(platform),
            reason="发布",
            idempotency_key="publish:one",
        )
        return governance, repository, platform

    def test_fresh_evidence_changes_audience(self) -> None:
        governance, _, platform = self._published_fixture()
        outcome = governance.change_audience(
            _admin(),
            pack_ref=_ref(platform),
            audience="users",
            reason="开放普通用户",
            idempotency_key="audience:one",
        )
        assert outcome.status == "changed"

    def test_stale_trivy_db_rejects_audience_change(self) -> None:
        governance, repository, platform = self._published_fixture()
        # 受众变更时刻漏洞库已过期：必须重查当前事实（AC7）。
        _evidence(
            repository,
            platform,
            updated_at_days_ago=8,
            evidence_char="c",
        )
        with pytest.raises(ValueError, match="漏洞库"):
            governance.change_audience(
                _admin(),
                pack_ref=_ref(platform),
                audience="users",
                reason="开放普通用户",
                idempotency_key="audience:stale",
            )
