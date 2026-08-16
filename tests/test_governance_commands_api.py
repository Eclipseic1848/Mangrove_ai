# -*- coding: utf-8 -*-
"""AC07-09 S5：治理命令 HTTP 端点（幂等头/权限/拒绝映射）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from src.api.auth import get_current_user
from src.api.main import app
from src.api.routes import capability_governance as governance_routes
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
    CapabilityValidationRun,
    InMemoryCapabilityGovernanceRepository,
    SupplyChainEvidenceStatus,
    TrivyDatabaseMetadata,
    ValidationEvidence,
    ValidationRunStatus,
    ValidationStep,
    ValidationStepStatus,
)
from src.conversation_steering import (
    CapabilityMaturity as LegacyCapabilityMaturity,
    CapabilityPack,
    ProcedureScope,
)


def _platform_target() -> CapabilityGovernanceTarget:
    return CapabilityGovernanceTarget(
        owner_id=None,
        scope=ProcedureScope.PLATFORM,
        pack_id="gray-python-table",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
    )


def _governance_instance():
    repository = InMemoryCapabilityGovernanceRepository()
    catalog_repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(catalog_repository)
    target = _platform_target()
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
    return CapabilityGovernance(catalog, repository), repository, target


def _client(
    monkeypatch,
    *,
    role: str = "admin",
) -> tuple[TestClient, InMemoryCapabilityGovernanceRepository]:
    governance, repository, _ = _governance_instance()
    monkeypatch.setattr(
        governance_routes, "_governance", lambda: governance
    )
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "admin-a",
        "role": role,
    }
    client = TestClient(app)
    return client, repository


def _body(**overrides) -> dict:
    fields: dict = {
        "pack_id": "gray-python-table",
        "version": "1.0.0",
        "digest": "sha256:" + "a" * 64,
        "reason": "测试治理命令",
    }
    fields.update(overrides)
    return fields


def _post(client: TestClient, path: str, body: dict, key: str = "k1"):
    return client.post(
        path,
        json=body,
        headers={"Idempotency-Key": key},
    )


class TestGovernanceCommandEndpoints:
    def test_deprecate_applies(self, monkeypatch) -> None:
        client, _ = _client(monkeypatch)
        response = _post(client, "/api/capability-governance/admin/deprecate", _body())
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "applied"

    def test_deprecate_twice_rejected_with_409(self, monkeypatch) -> None:
        client, _ = _client(monkeypatch)
        assert _post(client, "/api/capability-governance/admin/deprecate", _body()).status_code == 200
        response = _post(
            client,
            "/api/capability-governance/admin/deprecate",
            _body(),
            key="k2",
        )
        assert response.status_code == 409, response.text

    def test_same_idempotency_key_returns_existing(self, monkeypatch) -> None:
        client, _ = _client(monkeypatch)
        first = _post(client, "/api/capability-governance/admin/deprecate", _body())
        second = _post(client, "/api/capability-governance/admin/deprecate", _body())
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["status"] == "already_applied"

    def test_revoke_applies(self, monkeypatch) -> None:
        client, _ = _client(monkeypatch)
        response = _post(client, "/api/capability-governance/admin/revoke", _body())
        assert response.status_code == 200
        assert response.json()["status"] == "applied"

    def test_quarantine_applies(self, monkeypatch) -> None:
        client, _ = _client(monkeypatch)
        response = _post(client, "/api/capability-governance/admin/quarantine", _body())
        assert response.status_code == 200
        assert response.json()["status"] == "applied"

    def test_risk_accept_requires_quarantine(self, monkeypatch) -> None:
        client, _ = _client(monkeypatch)
        response = _post(
            client,
            "/api/capability-governance/admin/risk-accept",
            _body(finding_ref="capval_a1b2c3d4e5f6a1b2c3d4"),
        )
        assert response.status_code == 409, response.text
        assert "not_quarantined" in response.json()["detail"]

    def test_risk_accept_days_out_of_range(self, monkeypatch) -> None:
        client, repository = _client(monkeypatch)
        _quarantine(repository)
        response = _post(
            client,
            "/api/capability-governance/admin/risk-accept",
            _body(
                finding_ref="capval_a1b2c3d4e5f6a1b2c3d4",
                days=91,
            ),
        )
        # 输入校验层拒绝（422）；命令层的天数边界由服务层测试覆盖。
        assert response.status_code == 422, response.text

    def test_rollback_requires_publication(self, monkeypatch) -> None:
        client, _ = _client(monkeypatch)
        response = _post(client, "/api/capability-governance/admin/rollback", _body())
        assert response.status_code == 409, response.text
        assert "publication_missing" in response.json()["detail"]

    def test_restore_rejects_without_evidence(self, monkeypatch) -> None:
        client, repository = _client(monkeypatch)
        _revoke(repository)
        response = _post(client, "/api/capability-governance/admin/restore", _body())
        assert response.status_code == 409, response.text

    def test_change_audience_endpoint(self, monkeypatch) -> None:
        client, repository = _client(monkeypatch)
        _publish_with_green_run(repository)
        response = _post(
            client,
            "/api/capability-governance/admin/change-audience",
            _body(audience="users"),
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "changed"

    def test_non_admin_forbidden(self, monkeypatch) -> None:
        client, _ = _client(monkeypatch, role="user")
        response = _post(client, "/api/capability-governance/admin/deprecate", _body())
        assert response.status_code == 403

    def test_unknown_pack_404(self, monkeypatch) -> None:
        client, _ = _client(monkeypatch)
        response = _post(
            client,
            "/api/capability-governance/admin/deprecate",
            _body(pack_id="no-such-pack", digest="sha256:" + "f" * 64),
        )
        assert response.status_code == 404


def _quarantine(repository: InMemoryCapabilityGovernanceRepository) -> None:
    repository.save_governance_event(
        CapabilityGovernanceEvent(
            event_type="eligibility_changed",
            idempotency_key="gov:eligibility:q",
            target=_platform_target(),
            maturity=CapabilityMaturity.VERIFIED,
            lifecycle=CapabilityLifecycle.ACTIVE,
            eligibility=CapabilityEligibility.QUARANTINED,
            actor_id="admin-a",
            actor_role="admin",
            reason="隔离",
        )
    )


def _revoke(repository: InMemoryCapabilityGovernanceRepository) -> None:
    repository.save_governance_event(
        CapabilityGovernanceEvent(
            event_type="lifecycle_changed",
            idempotency_key="gov:lifecycle:r",
            target=_platform_target(),
            maturity=CapabilityMaturity.VERIFIED,
            lifecycle=CapabilityLifecycle.REVOKED,
            eligibility=CapabilityEligibility.ELIGIBLE,
            actor_id="admin-a",
            actor_role="admin",
            reason="撤销",
        )
    )


def _publish(repository: InMemoryCapabilityGovernanceRepository) -> None:
    target = _platform_target()
    repository.save_platform_event(
        CapabilityGovernanceEvent(
            event_type="platform_published",
            idempotency_key="publish:test",
            target=target,
            maturity=CapabilityMaturity.VERIFIED,
            actor_id="admin-a",
            actor_role="admin",
            reason="发布",
            source_digest="sha256:" + "b" * 64,
            platform_digest=target.digest,
            audience="admin_gray",
            platform_validation_run_id="pfval_" + "a" * 20,
            signing_signature_digest="sha256:" + "c" * 64,
            signing_public_key_sha256="d" * 64,
        )
    )


def _publish_with_green_run(
    repository: InMemoryCapabilityGovernanceRepository,
) -> None:
    """发布事件 + 六步全绿签名运行 + 新鲜供应链证据（受众变更门前置）。"""
    from src.capability_governance import (
        PlatformValidationEvidence,
        PlatformValidationRun,
        PlatformValidationStep,
    )

    target = _platform_target()
    _publish(repository)
    repository.create_platform_validation_run(
        PlatformValidationRun(
            run_id="pfval_" + "a" * 20,
            actor_id="admin-a",
            actor_role="admin",
            idempotency_key="pfval-green",
            target=target,
            status=ValidationRunStatus.QUEUED,
        )
    )
    repository.save_platform_validation_run(
        PlatformValidationRun(
            run_id="pfval_" + "a" * 20,
            actor_id="admin-a",
            actor_role="admin",
            idempotency_key="pfval-green",
            target=target,
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
    repository.save_supply_chain_evidence(
        CapabilitySupplyChainEvidence(
            evidence_id="supply_" + "b" * 20,
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
                updated_at=datetime.now(timezone.utc),
            ),
            syft_version="1.50.0",
            syft_json_sha256="e" * 64,
            cyclonedx_json_sha256="f" * 64,
            cyclonedx_spec_version="1.6",
        )
    )
