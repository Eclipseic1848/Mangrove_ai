# -*- coding: utf-8 -*-
"""AC-07：通过认证 HTTP Interface 验证三轴治理读取权限。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from src.api.auth import get_current_user
from src.api.main import app
from src.api.routes import capability_governance as governance_routes
from src.capability_catalog import (
    CapabilityCatalog,
    CatalogActor,
    InMemoryCapabilityCatalogRepository,
    SqliteCapabilityCatalogRepository,
)
from src.capability_governance import (
    CapabilityGovernance,
    CapabilityGovernanceTarget,
    CapabilitySupplyChainEvidence,
    InMemoryCapabilityGovernanceRepository,
    SqliteCapabilityGovernanceRepository,
    ValidationTaskRef,
    SupplyChainEvidenceStatus,
    TrivyDatabaseMetadata,
    migrate_capability_governance,
)
from src.config.settings import settings
from src.conversation_steering import (
    CapabilityMaturity,
    CapabilityPack,
    ProcedureScope,
)


def _seed_catalog(db_path: str) -> None:
    repository = SqliteCapabilityCatalogRepository(db_path)
    catalog = CapabilityCatalog(repository)
    for owner_id, version, digest_char in (
        ("owner-a", "1.0.0", "a"),
        ("owner-b", "2.0.0", "b"),
    ):
        catalog.register_pack(
            CatalogActor(owner_id=owner_id, role="user"),
            CapabilityPack(
                pack_id="python-table-summary",
                version=version,
                digest="sha256:" + digest_char * 64,
                scope=ProcedureScope.PERSONAL,
                maturity=CapabilityMaturity.DRAFT,
                owner_id=owner_id,
            ),
        )
    repository.save_pack(
        CapabilityPack(
            pack_id="everything-mcp",
            version="2026.7.4",
            digest="sha256:" + "c" * 64,
            scope=ProcedureScope.PLATFORM,
            maturity=CapabilityMaturity.VERIFIED,
        )
    )


def test_governance_api_projects_user_and_admin_fields(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "webui.db"
    _seed_catalog(str(db_path))
    monkeypatch.setattr(settings, "webui_db_path", str(db_path))
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "owner-a",
        "role": "user",
    }
    client = TestClient(app)

    user_response = client.get("/api/capability-governance/packs")

    assert user_response.status_code == 200
    assert [item["version"] for item in user_response.json()["items"]] == [
        "2026.7.4",
        "1.0.0",
    ]
    assert user_response.json()["items"][0]["owner_id"] is None
    assert user_response.json()["items"][1]["owner_id"] == "owner-a"
    assert user_response.json()["items"][0]["digest"] is None
    assert user_response.json()["items"][1]["digest"].startswith("sha256:")

    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "admin-a",
        "role": "admin",
    }
    admin_response = client.get("/api/capability-governance/packs")

    assert admin_response.status_code == 200
    assert [item["owner_id"] for item in admin_response.json()["items"]] == [
        None,
        "owner-a",
        "owner-b",
    ]
    assert all(item["digest"].startswith("sha256:") for item in admin_response.json()["items"])
    app.dependency_overrides.clear()


def test_owner_starts_and_reads_validation_without_supplying_hashes(
    monkeypatch,
) -> None:
    class FrozenTaskResolver:
        def resolve(self, actor, target, *, task_id: str, revision: int):
            if task_id != "workspace-owner-a":
                raise PermissionError("任务不存在或无权访问")
            return ValidationTaskRef(
                task_id=task_id,
                revision=revision,
                source_snapshot_sha256="d" * 64,
                input_sha256="e" * 64,
                output_sha256="f" * 64,
                capability_digest=target.digest,
                authorization_id="selection-owner-a",
            )

        def verify(self, actor, target, task_ref):
            current = self.resolve(
                actor,
                target,
                task_id=task_ref.task_id,
                revision=task_ref.revision,
            )
            if current != task_ref:
                raise ValueError("冻结任务证据已变化")
            return current

    catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
    owner = CatalogActor(owner_id="owner-a", role="user")
    pack = CapabilityPack(
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
        scope=ProcedureScope.PERSONAL,
        maturity=CapabilityMaturity.DRAFT,
        owner_id="owner-a",
    )
    catalog.register_pack(owner, pack)
    governance = CapabilityGovernance(
        catalog,
        InMemoryCapabilityGovernanceRepository(),
        task_resolver=FrozenTaskResolver(),
    )
    monkeypatch.setattr(governance_routes, "_governance", lambda: governance)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "owner-a",
        "role": "user",
    }
    client = TestClient(app)
    payload = {
        "pack_id": pack.pack_id,
        "version": pack.version,
        "digest": pack.digest,
        "task_id": "workspace-owner-a",
        "revision": 1,
    }

    response = client.post(
        "/api/capability-governance/validations",
        headers={"Idempotency-Key": "validate-owner-task"},
        json=payload,
    )
    repeated = client.post(
        "/api/capability-governance/validations",
        headers={"Idempotency-Key": "validate-owner-task"},
        json=payload,
    )

    assert response.status_code == 202
    assert repeated.json()["run_id"] == response.json()["run_id"]
    assert "source_snapshot_sha256" in response.json()["task_ref"]
    listed = client.get("/api/capability-governance/validations")
    assert listed.status_code == 200
    assert [item["run_id"] for item in listed.json()["items"]] == [
        response.json()["run_id"]
    ]
    app.dependency_overrides.clear()


def test_packs_endpoint_serializes_sanitized_promotion_gaps(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "webui.db"
    _seed_catalog(str(db_path))
    migrate_capability_governance(db_path, tmp_path / "backup.db")
    monkeypatch.setattr(settings, "webui_db_path", str(db_path))
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "owner-a",
        "role": "user",
    }
    client = TestClient(app)

    response = client.get("/api/capability-governance/packs")

    assert response.status_code == 200
    items = {item["version"]: item for item in response.json()["items"]}
    gaps = items["1.0.0"]["promotion_gaps"]
    assert "validation_incomplete" in gaps
    assert "supply_chain_evidence_missing" in gaps
    # 平台 verified 历史包没有缺口。
    assert items["2026.7.4"]["promotion_gaps"] == []
    app.dependency_overrides.clear()


def test_owner_and_admin_read_sanitized_supply_chain_summary(monkeypatch) -> None:
    catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
    owner = CatalogActor(owner_id="owner-a", role="user")
    pack = CapabilityPack(
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
        scope=ProcedureScope.PERSONAL,
        maturity=CapabilityMaturity.DRAFT,
        owner_id="owner-a",
    )
    catalog.register_pack(owner, pack)
    repository = InMemoryCapabilityGovernanceRepository()
    repository.save_supply_chain_evidence(
        CapabilitySupplyChainEvidence(
            evidence_id="supply_" + "1" * 20,
            target=CapabilityGovernanceTarget(
                owner_id="owner-a",
                scope=ProcedureScope.PERSONAL,
                pack_id=pack.pack_id,
                version=pack.version,
                digest=pack.digest,
            ),
            subject_digest=pack.digest,
            status=SupplyChainEvidenceStatus.PASSED,
            secret_count=0,
            critical_count=0,
            fixable_high_count=0,
            misconfiguration_failure_count=0,
            trivy_version="0.70.0",
            trivy_config_sha256="2" * 64,
            trivy_result_sha256="3" * 64,
            trivy_database=TrivyDatabaseMetadata(
                version=2,
                updated_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
            ),
            syft_version="1.50.0",
            syft_json_sha256="4" * 64,
            cyclonedx_json_sha256="5" * 64,
            cyclonedx_spec_version="1.6",
            occurred_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
        )
    )
    governance = CapabilityGovernance(catalog, repository)
    monkeypatch.setattr(governance_routes, "_governance", lambda: governance)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "owner-a",
        "role": "user",
    }
    client = TestClient(app)
    url = (
        f"/api/capability-governance/packs/{pack.pack_id}/{pack.version}"
        f"/supply-chain-evidence?digest={pack.digest}"
    )

    owner_response = client.get(url)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "admin-a",
        "role": "admin",
    }
    admin_response = client.get(url)

    assert owner_response.status_code == 200
    assert admin_response.status_code == 200
    payload = owner_response.json()["evidence"]
    assert payload["status"] == "passed"
    assert payload["trivy_database"]["version"] == 2
    assert "host" not in str(payload).lower()
    assert "token" not in str(payload).lower()
    app.dependency_overrides.clear()
