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


class _AuditStubResolver:
    """#11 API 测试替身：只提供审计查看需要的两个读取方法。"""

    def __init__(self) -> None:
        self.content_calls: list[tuple[str, int, str]] = []

    def read_task_metadata(self, actor, task_id, revision, *, task_owner_id):
        from src.capability_governance import CapabilityTaskMetadata

        return CapabilityTaskMetadata(
            task_id=task_id,
            revision=revision,
            owner_id=task_owner_id,
            task_status="completed",
            created_at="2026-08-14T00:00:00+00:00",
            updated_at="2026-08-14T01:00:00+00:00",
        )

    def read_business_content(
        self,
        actor,
        task_id,
        revision,
        subject_type,
        *,
        task_owner_id,
    ):
        from src.capability_governance import BusinessContent

        import hashlib

        self.content_calls.append((task_id, revision, subject_type))
        text = f"审计正文：任务 {task_id} 的 {subject_type}"
        return BusinessContent(
            status="succeeded",
            subject_type=subject_type,
            content=text,
            content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            size_bytes=len(text.encode("utf-8")),
        )


def _audit_governance_fixture(monkeypatch) -> None:
    from src.capability_governance import (
        CapabilityValidationRun,
        ValidationEvidence,
        ValidationRunStatus,
        ValidationStep,
        ValidationStepStatus,
    )

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
    target = CapabilityGovernanceTarget(
        owner_id="owner-a",
        scope=ProcedureScope.PERSONAL,
        pack_id=pack.pack_id,
        version=pack.version,
        digest=pack.digest,
    )
    task_ref = ValidationTaskRef(
        task_id="workspace-owner-a",
        revision=2,
        source_snapshot_sha256="b" * 64,
        input_sha256="c" * 64,
        output_sha256="d" * 64,
        capability_digest=target.digest,
        authorization_id="selection-owner-a",
    )
    run = CapabilityValidationRun(
        run_id="capval_a1b2c3d4e5f6a1b2c3d4",
        owner_id="owner-a",
        target=target,
        actor_id="owner-a",
        actor_role="user",
        idempotency_key="validate-owner-task",
        task_ref=task_ref,
        status=ValidationRunStatus.SUCCEEDED,
        evidence=tuple(
            ValidationEvidence(
                step=step,
                status=ValidationStepStatus.PASSED,
                evidence_ref=f"evidence://validation/run/{step.value}",
                evidence_sha256="e" * 64,
                summary="验证步骤已通过",
            )
            for step in ValidationStep
        ),
    )
    repository.create_validation_run(run)
    governance = CapabilityGovernance(
        catalog,
        repository,
        task_resolver=_AuditStubResolver(),
    )
    monkeypatch.setattr(governance_routes, "_governance", lambda: governance)


def test_admin_review_endpoints_enforce_admin_and_serialize_sanitized(
    monkeypatch,
) -> None:
    _audit_governance_fixture(monkeypatch)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "owner-a",
        "role": "user",
    }
    client = TestClient(app)

    forbidden = client.get("/api/capability-governance/admin/review")
    assert forbidden.status_code == 403
    forbidden_log = client.get("/api/capability-governance/admin/audit-log")
    assert forbidden_log.status_code == 403

    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "admin-a",
        "role": "admin",
    }
    response = client.get("/api/capability-governance/admin/review")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["owner_id"] == "owner-a"
    assert item["digest"].startswith("sha256:")
    assert item["validation"]["run_id"].startswith("capval_")
    assert item["task_metadata"]["task_id"] == "workspace-owner-a"
    assert item["task_metadata"]["task_status"] == "completed"
    # 脱敏白名单：审核聚合响应不含任何业务正文或敏感字段。
    serialized = str(response.json()).lower()
    assert "objective_text" not in serialized
    assert "prompt" not in serialized
    assert "secret" not in serialized
    assert "file_path" not in serialized
    assert "token" not in serialized

    detail = client.get(
        "/api/capability-governance/admin/review/python-table-summary/1.0.0"
        f"?digest={item['digest']}"
    )
    assert detail.status_code == 200
    assert detail.json()["pack_id"] == "python-table-summary"
    assert detail.json()["audit_history"] == []

    missing = client.get(
        "/api/capability-governance/admin/review/no-such-pack/1.0.0"
        "?digest=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    assert missing.status_code == 404

    # 超级管理员与管理员同一治理类型（ADR-0029 决策 7）。
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "super-a",
        "role": "super_admin",
    }
    super_response = client.get("/api/capability-governance/admin/review")
    assert super_response.status_code == 200
    assert len(super_response.json()["items"]) == 1
    app.dependency_overrides.clear()


def test_audit_view_endpoint_requires_reason_idempotency_and_admin(
    monkeypatch,
) -> None:
    _audit_governance_fixture(monkeypatch)
    digest = "sha256:" + "a" * 64
    payload = {
        "pack_id": "python-table-summary",
        "version": "1.0.0",
        "digest": digest,
        "task_id": "workspace-owner-a",
        "revision": 2,
        "subject_type": "task_prompt",
        "reason": "排障：核对验证任务原始正文",
    }
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "owner-a",
        "role": "user",
    }
    client = TestClient(app)
    forbidden = client.post(
        "/api/capability-governance/admin/audit-view",
        headers={"Idempotency-Key": "audit:one"},
        json=payload,
    )
    assert forbidden.status_code == 403

    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "admin-a",
        "role": "admin",
    }
    missing_key = client.post(
        "/api/capability-governance/admin/audit-view",
        json=payload,
    )
    assert missing_key.status_code == 422

    short_reason = client.post(
        "/api/capability-governance/admin/audit-view",
        headers={"Idempotency-Key": "audit:short"},
        json={**payload, "reason": "短"},
    )
    assert short_reason.status_code == 422

    ok = client.post(
        "/api/capability-governance/admin/audit-view",
        headers={"Idempotency-Key": "audit:one"},
        json=payload,
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["status"] == "succeeded"
    assert "审计正文" in body["content"]
    assert body["event"]["subject_sha256"]
    assert body["event"]["result"] == "succeeded"

    repeated = client.post(
        "/api/capability-governance/admin/audit-view",
        headers={"Idempotency-Key": "audit:one"},
        json=payload,
    )
    assert repeated.status_code == 200
    assert repeated.json()["event"]["event_id"] == body["event"]["event_id"]

    log = client.get("/api/capability-governance/admin/audit-log")
    assert log.status_code == 200
    records = log.json()["items"]
    assert len(records) == 1
    assert records[0]["reason"].startswith("排障")
    assert records[0]["actor_id"] == "admin-a"
    assert records[0]["task_id"] == "workspace-owner-a"
    assert records[0]["revision"] == 2
    # 审计记录不含正文，只含 hash。
    assert "content" not in str(records[0]).lower()

    # 不存在的 pack 返回 404 而非 403。
    unknown = client.post(
        "/api/capability-governance/admin/audit-view",
        headers={"Idempotency-Key": "audit:unknown"},
        json={**payload, "pack_id": "no-such-pack"},
    )
    assert unknown.status_code == 404

    # 任务身份与验证证据不一致被拒绝（422）。
    unbound = client.post(
        "/api/capability-governance/admin/audit-view",
        headers={"Idempotency-Key": "audit:unbound"},
        json={**payload, "task_id": "unrelated-task", "revision": 9},
    )
    assert unbound.status_code == 422
    app.dependency_overrides.clear()


def _platform_candidate_governance(monkeypatch) -> None:
    """替身治理：快照生成器返回固定平台 digest，发布记录调用。"""
    from src.capability_governance import (
        CapabilityGovernanceEvent,
        CapabilityMaturity,
        PlatformSnapshot,
        PlatformValidationRun,
        ValidationRunStatus,
    )

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
    target = CapabilityGovernanceTarget(
        owner_id="owner-a",
        scope=ProcedureScope.PERSONAL,
        pack_id=pack.pack_id,
        version=pack.version,
        digest=pack.digest,
    )
    repository.save_event(
        CapabilityGovernanceEvent(
            idempotency_key="register-a",
            target=target,
            actor_id="owner-a",
            actor_role="user",
        )
    )
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
    platform_snapshot = PlatformSnapshot(
        pack_id=pack.pack_id,
        version=pack.version,
        source_digest=pack.digest,
        platform_digest="sha256:" + "b" * 64,
        manifest_summary=("entrypoint",),
    )

    class StubGenerator:
        def generate(self, source_pack):
            return platform_snapshot

    published_packs: list = []

    class StubPublisher:
        def save_pack(self, platform_pack):
            published_packs.append(platform_pack)
            return platform_pack

    governance = CapabilityGovernance(
        catalog,
        repository,
        platform_snapshot_generator=StubGenerator(),
        platform_publisher=StubPublisher(),
    )
    monkeypatch.setattr(governance_routes, "_governance", lambda: governance)


def test_platform_candidate_endpoints_enforce_admin_and_serialize(
    monkeypatch,
) -> None:
    _platform_candidate_governance(monkeypatch)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "owner-a",
        "role": "user",
    }
    client = TestClient(app)
    forbidden = client.post(
        "/api/capability-governance/admin/platform-candidates",
        headers={"Idempotency-Key": "candidate:one"},
        json={
            "pack_id": "python-table-summary",
            "version": "1.0.0",
            "digest": "sha256:" + "a" * 64,
            "reason": "平台候选",
        },
    )
    assert forbidden.status_code == 403

    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "admin-a",
        "role": "admin",
    }
    missing_key = client.post(
        "/api/capability-governance/admin/platform-candidates",
        json={
            "pack_id": "python-table-summary",
            "version": "1.0.0",
            "digest": "sha256:" + "a" * 64,
            "reason": "平台候选",
        },
    )
    assert missing_key.status_code == 422

    created = client.post(
        "/api/capability-governance/admin/platform-candidates",
        headers={"Idempotency-Key": "candidate:one"},
        json={
            "pack_id": "python-table-summary",
            "version": "1.0.0",
            "digest": "sha256:" + "a" * 64,
            "reason": "平台候选：个人验证已完成",
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["status"] == "created"
    assert body["snapshot"]["platform_digest"] == "sha256:" + "b" * 64
    assert body["event"]["event_type"] == "platform_candidate"

    listed = client.get("/api/capability-governance/admin/platform-candidates")
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1
    item = listed.json()["items"][0]
    assert item["platform_digest"] == "sha256:" + "b" * 64
    assert item["steps_total"] == 6
    assert item["validation_status"] in {"queued", "running"}
    assert item["signed"] is False
    serialized = str(listed.json()).lower()
    assert "purpose" not in serialized
    assert "secret" not in serialized

    # 发布前未就绪：候选验证未完成返回 409。
    not_ready = client.post(
        "/api/capability-governance/admin/platform-publish",
        headers={"Idempotency-Key": "publish:one"},
        json={
            "pack_id": "python-table-summary",
            "version": "1.0.0",
            "platform_digest": "sha256:" + "b" * 64,
            "reason": "发布",
        },
    )
    assert not_ready.status_code == 409

    # 受众变更无端点（#12 只实现命令，产品不暴露）；SPA fallback 可能令
    # 未知 POST 路径返回 405（路径被 GET fallback 占用），404/405 都视为无端点。
    audience = client.post(
        "/api/capability-governance/admin/platform-audience",
        headers={"Idempotency-Key": "audience:one"},
        json={
            "pack_id": "python-table-summary",
            "version": "1.0.0",
            "platform_digest": "sha256:" + "b" * 64,
            "audience": "users",
            "reason": "开放普通用户",
        },
    )
    assert audience.status_code in (404, 405)
    app.dependency_overrides.clear()
