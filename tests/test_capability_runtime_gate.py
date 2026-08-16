# -*- coding: utf-8 -*-
"""AC-07-08 S1：运行时门契约与投影公开入口（装载门前置模型层）。"""
from __future__ import annotations

from src.capability_catalog import (
    CapabilityCatalog,
    CapabilityMountGateRejected,
    InMemoryCapabilityCatalogRepository,
    RuntimeGateContract,
)
from src.capability_catalog.models import CatalogActor
from src.capability_governance import (
    CapabilityGovernance,
    CapabilityEligibility,
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


def _personal_target(digest_char: str = "a") -> CapabilityGovernanceTarget:
    return CapabilityGovernanceTarget(
        owner_id="owner-a",
        scope=ProcedureScope.PERSONAL,
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + digest_char * 64,
    )


def _platform_target(digest_char: str = "b") -> CapabilityGovernanceTarget:
    return CapabilityGovernanceTarget(
        owner_id=None,
        scope=ProcedureScope.PLATFORM,
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + digest_char * 64,
    )


def _personal_pack(target: CapabilityGovernanceTarget) -> CapabilityPack:
    return CapabilityPack(
        pack_id=target.pack_id,
        version=target.version,
        digest=target.digest,
        scope=ProcedureScope.PERSONAL,
        maturity=LegacyCapabilityMaturity.DRAFT,
        owner_id=target.owner_id,
    )


def _platform_pack(target: CapabilityGovernanceTarget) -> CapabilityPack:
    """AC-06 历史灰度平台包的形态：legacy VERIFIED maturity，无治理事件。"""
    return CapabilityPack(
        pack_id=target.pack_id,
        version=target.version,
        digest=target.digest,
        scope=ProcedureScope.PLATFORM,
        maturity=LegacyCapabilityMaturity.VERIFIED,
        owner_id=None,
    )


def _governance_with_catalog(
    repository: InMemoryCapabilityGovernanceRepository,
) -> tuple[CapabilityGovernance, CapabilityCatalog, InMemoryCapabilityCatalogRepository]:
    catalog_repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(catalog_repository)
    governance = CapabilityGovernance(catalog, repository)
    return governance, catalog, catalog_repository


def _save_personal_pack(
    catalog: CapabilityCatalog,
    pack: CapabilityPack,
) -> None:
    catalog.register_pack(CatalogActor(owner_id="owner-a", role="user"), pack)


def _save_platform_pack(
    catalog_repository: InMemoryCapabilityCatalogRepository,
    pack: CapabilityPack,
) -> None:
    # 平台 pack 只能由发布流程写入目录（与 #12 夹具一致）。
    catalog_repository.save_pack(pack)


class TestS1GateContract:
    """装载门最小契约与拒绝异常的公开形态。"""

    def test_runtime_gate_contract_is_importable(self) -> None:
        # Protocol 存在即契约成立；实现位于 capability_governance.runtime_gate。
        assert RuntimeGateContract is not None

    def test_rejection_is_runtime_error(self) -> None:
        assert issubclass(CapabilityMountGateRejected, RuntimeError)

    def test_rejection_carries_pack_identity_and_reason(self) -> None:
        digest = "sha256:" + "a" * 64
        error = CapabilityMountGateRejected(
            pack_id="python-table-summary",
            version="1.0.0",
            digest=digest,
            reason="成熟度未达到 verified",
        )
        assert error.pack_id == "python-table-summary"
        assert error.version == "1.0.0"
        assert error.digest == digest
        assert "python-table-summary" in str(error)
        assert "成熟度未达到 verified" in str(error)


class TestS1RuntimeProjectionEntry:
    """runtime_projection_for_pack 公开入口的折叠语义。"""

    def test_personal_pack_without_events_gets_legacy_draft_projection(
        self,
    ) -> None:
        governance, catalog, _ = _governance_with_catalog(
            InMemoryCapabilityGovernanceRepository()
        )
        target = _personal_target()
        _save_personal_pack(catalog, _personal_pack(target))
        pack = catalog.resolve_pack(
            CatalogActor(owner_id="owner-a", role="user"),
            target.pack_id,
            target.version,
        )
        assert pack is not None
        projection = governance.runtime_projection_for_pack(pack)
        assert projection.maturity is CapabilityMaturity.DRAFT
        assert projection.lifecycle is CapabilityLifecycle.ACTIVE
        assert projection.eligibility is CapabilityEligibility.ELIGIBLE
        assert projection.source == "legacy_compat"
        assert projection.audience is None

    def test_platform_pack_without_events_gets_admin_gray_legacy_projection(
        self,
    ) -> None:
        governance, _, catalog_repository = _governance_with_catalog(
            InMemoryCapabilityGovernanceRepository()
        )
        target = _platform_target()
        _save_platform_pack(catalog_repository, _platform_pack(target))
        pack = governance._catalog.resolve_pack(
            CatalogActor(owner_id="owner-a", role="user"),
            target.pack_id,
            target.version,
        )
        assert pack is not None
        projection = governance.runtime_projection_for_pack(pack)
        assert projection.maturity is CapabilityMaturity.VERIFIED
        assert projection.lifecycle is CapabilityLifecycle.ACTIVE
        assert projection.eligibility is CapabilityEligibility.ELIGIBLE
        assert projection.source == "legacy_compat"
        # AC-06 历史灰度包语义上只对管理员开放。
        assert projection.audience == "admin_gray"

    def test_promoted_event_drives_governance_projection(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, catalog, _ = _governance_with_catalog(repository)
        target = _personal_target()
        _save_personal_pack(catalog, _personal_pack(target))
        repository.save_promotion_event(
            CapabilityGovernanceEvent(
                event_type="promoted_to_verified",
                idempotency_key="promotion:run-a",
                target=target,
                maturity=CapabilityMaturity.VERIFIED,
                actor_id="owner-a",
                actor_role="user",
                source_validation_run_id="capval_a1b2c3d4e5f6a1b2c3d4",
                source_supply_chain_evidence_id="supply_" + "a" * 20,
            )
        )
        pack = catalog.resolve_pack(
            CatalogActor(owner_id="owner-a", role="user"),
            target.pack_id,
            target.version,
        )
        assert pack is not None
        projection = governance.runtime_projection_for_pack(pack)
        assert projection.maturity is CapabilityMaturity.VERIFIED
        assert projection.lifecycle is CapabilityLifecycle.ACTIVE
        assert projection.eligibility is CapabilityEligibility.ELIGIBLE
        assert projection.source == "governance_event"

    def test_platform_published_event_carries_audience(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, _, catalog_repository = _governance_with_catalog(repository)
        target = _platform_target()
        _save_platform_pack(catalog_repository, _platform_pack(target))
        repository.save_platform_event(
            CapabilityGovernanceEvent(
                event_type="platform_published",
                idempotency_key="publish:sha256-b",
                target=target,
                maturity=CapabilityMaturity.VERIFIED,
                actor_id="admin-a",
                actor_role="admin",
                reason="发布：六步验证与签名全部通过",
                source_digest="sha256:" + "a" * 64,
                platform_digest=target.digest,
                audience="admin_gray",
                platform_validation_run_id="pfval_" + "a" * 20,
                signing_signature_digest="sha256:" + "c" * 64,
                signing_public_key_sha256="d" * 64,
            )
        )
        pack = governance._catalog.resolve_pack(
            CatalogActor(owner_id="owner-a", role="user"),
            target.pack_id,
            target.version,
        )
        assert pack is not None
        projection = governance.runtime_projection_for_pack(pack)
        assert projection.source == "governance_event"
        assert projection.audience == "admin_gray"

    def test_audience_changed_follows_latest_event(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, _, catalog_repository = _governance_with_catalog(repository)
        target = _platform_target()
        _save_platform_pack(catalog_repository, _platform_pack(target))
        repository.save_platform_event(
            CapabilityGovernanceEvent(
                event_type="platform_published",
                idempotency_key="publish:sha256-b",
                target=target,
                maturity=CapabilityMaturity.VERIFIED,
                actor_id="admin-a",
                actor_role="admin",
                reason="发布：六步验证与签名全部通过",
                source_digest="sha256:" + "a" * 64,
                platform_digest=target.digest,
                audience="admin_gray",
                platform_validation_run_id="pfval_" + "a" * 20,
                signing_signature_digest="sha256:" + "c" * 64,
                signing_public_key_sha256="d" * 64,
            )
        )
        repository.save_platform_event(
            CapabilityGovernanceEvent(
                event_type="audience_changed",
                idempotency_key="audience:sha256-b",
                target=target,
                maturity=CapabilityMaturity.VERIFIED,
                actor_id="admin-a",
                actor_role="admin",
                reason="受众变更：向普通用户开放",
                audience="users",
            )
        )
        pack = governance._catalog.resolve_pack(
            CatalogActor(owner_id="owner-a", role="user"),
            target.pack_id,
            target.version,
        )
        assert pack is not None
        projection = governance.runtime_projection_for_pack(pack)
        assert projection.audience == "users"
