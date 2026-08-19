# -*- coding: utf-8 -*-
"""AC07-10 S3：手动重扫 HTTP 端点（幂等头/权限/拒绝映射）。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from src.api.auth import get_current_user
from src.api.main import app
from src.api.routes import capability_governance as governance_routes
from src.capability_catalog import (
    CapabilityCatalog,
    InMemoryCapabilityCatalogRepository,
)
from src.capability_governance import (
    CapabilityGovernance,
    CapabilityGovernanceEvent,
    CapabilityGovernanceTarget,
    CapabilityMaturity,
    CapabilitySupplyChainEvidence,
    InMemoryCapabilityGovernanceRepository,
    SupplyChainEvidenceStatus,
    TrivyDatabaseMetadata,
)
from src.conversation_steering import (
    CapabilityMaturity as LegacyCapabilityMaturity,
    CapabilityPack,
    ProcedureScope,
)


def _target() -> CapabilityGovernanceTarget:
    return CapabilityGovernanceTarget(
        owner_id=None,
        scope=ProcedureScope.PLATFORM,
        pack_id="gray-python-table",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
    )


def _evidence(
    target: CapabilityGovernanceTarget,
    *,
    passed: bool = True,
    blockers: tuple[str, ...] = (),
    evidence_char: str = "c",
) -> CapabilitySupplyChainEvidence:
    return CapabilitySupplyChainEvidence(
        evidence_id="supply_" + evidence_char * 20,
        target=target,
        subject_digest=target.digest,
        status=(
            SupplyChainEvidenceStatus.PASSED
            if passed
            else SupplyChainEvidenceStatus.BLOCKED
        ),
        blockers=blockers,
        secret_count=1 if "secret_detected" in blockers else 0,
        critical_count=1 if "critical_vulnerability" in blockers else 0,
        fixable_high_count=0,
        misconfiguration_failure_count=0,
        trivy_version="0.70.0",
        trivy_config_sha256="c" * 64,
        trivy_result_sha256="d" * 64,
        trivy_database=TrivyDatabaseMetadata(
            version=2, updated_at=datetime.now(timezone.utc)
        ),
        syft_version="1.50.0",
        syft_json_sha256="e" * 64,
        cyclonedx_json_sha256="f" * 64,
        cyclonedx_spec_version="1.6",
    )


def _governance_instance(published: bool = True):
    repository = InMemoryCapabilityGovernanceRepository()
    catalog_repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(catalog_repository)
    target = _target()
    catalog_repository.save_pack(
        CapabilityPack(
            pack_id=target.pack_id,
            version=target.version,
            digest=target.digest,
            scope=ProcedureScope.PLATFORM,
            maturity=LegacyCapabilityMaturity.VERIFIED,
            owner_id=None,
        )
    )
    if published:
        repository.save_platform_event(
            CapabilityGovernanceEvent(
                event_type="platform_published",
                idempotency_key="publish:sha256-a",
                target=target,
                maturity=CapabilityMaturity.VERIFIED,
                actor_id="admin-a",
                actor_role="admin",
                reason="发布：六步验证与签名全部通过",
                source_digest="sha256:" + "b" * 64,
                platform_digest=target.digest,
                audience="admin_gray",
                platform_validation_run_id="pfval_" + "a" * 20,
                signing_signature_digest="sha256:" + "c" * 64,
                signing_public_key_sha256="d" * 64,
            )
        )
    governance = CapabilityGovernance(
        catalog,
        repository,
        platform_materialize=lambda t: Path("."),
        supply_chain_collector=lambda t, root: _evidence(t),
    )
    return governance, repository


def _client(monkeypatch, *, role: str = "admin", published: bool = True):
    governance, repository = _governance_instance(published=published)
    monkeypatch.setattr(governance_routes, "_governance", lambda: governance)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "admin-a",
        "role": role,
    }
    return TestClient(app), repository


def _body() -> dict:
    return {
        "pack_id": "gray-python-table",
        "version": "1.0.0",
        "digest": "sha256:" + "a" * 64,
        "reason": "定期重扫",
    }


class TestS3RescanEndpoint:
    def test_rescan_applies(self, monkeypatch) -> None:
        client, _ = _client(monkeypatch)
        response = client.post(
            "/api/capability-governance/admin/supply-chain-rescan",
            json=_body(),
            headers={"Idempotency-Key": "k1"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "applied"

    def test_rescan_idempotent_replay(self, monkeypatch) -> None:
        client, _ = _client(monkeypatch)
        first = client.post(
            "/api/capability-governance/admin/supply-chain-rescan",
            json=_body(),
            headers={"Idempotency-Key": "k1"},
        )
        assert first.status_code == 200
        second = client.post(
            "/api/capability-governance/admin/supply-chain-rescan",
            json=_body(),
            headers={"Idempotency-Key": "k1"},
        )
        assert second.status_code == 200
        assert second.json()["status"] == "already_applied"

    def test_unpublished_pack_rejected_409(self, monkeypatch) -> None:
        client, _ = _client(monkeypatch, published=False)
        response = client.post(
            "/api/capability-governance/admin/supply-chain-rescan",
            json=_body(),
            headers={"Idempotency-Key": "k1"},
        )
        assert response.status_code == 409, response.text
        assert "not_platform_published" in response.json()["detail"]

    def test_non_admin_forbidden(self, monkeypatch) -> None:
        client, _ = _client(monkeypatch, role="user")
        response = client.post(
            "/api/capability-governance/admin/supply-chain-rescan",
            json=_body(),
            headers={"Idempotency-Key": "k1"},
        )
        assert response.status_code == 403, response.text

    def test_missing_idempotency_key_422(self, monkeypatch) -> None:
        client, _ = _client(monkeypatch)
        response = client.post(
            "/api/capability-governance/admin/supply-chain-rescan",
            json=_body(),
        )
        assert response.status_code == 422, response.text
