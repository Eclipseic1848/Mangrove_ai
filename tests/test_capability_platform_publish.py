# -*- coding: utf-8 -*-
"""AC-07-07：从 CapabilityGovernance 公共 Interface 验证平台快照、签名与 admin_gray 发布。

分区编号对齐 task-breakdown：S1 模型、S2 Repository、S5 服务层命令。
S3（快照生成器）与 S4（平台验证执行器）在各自测试文件中。
"""
from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest
from pydantic import ValidationError

from src.capability_catalog import (
    CapabilityCatalog,
    CapabilityPackRef,
    CatalogActor,
    InMemoryCapabilityCatalogRepository,
)
from src.capability_governance import (
    AudienceOutcome,
    CapabilityEligibility,
    CapabilityGovernance,
    CapabilityGovernanceEvent,
    CapabilityGovernanceProjection,
    CapabilityGovernanceTarget,
    CapabilityLifecycle,
    CapabilityMaturity,
    CapabilitySupplyChainEvidence,
    InMemoryCapabilityGovernanceRepository,
    PlatformCandidateOutcome,
    PlatformSnapshot,
    PlatformValidationEvidence,
    PlatformValidationRun,
    PlatformValidationStep,
    PublishOutcome,
    SupplyChainEvidenceStatus,
    TrivyDatabaseMetadata,
    ValidationRunStatus,
    ValidationStepStatus,
)
from src.conversation_steering import ProcedureScope


def _platform_target(
    digest_char: str = "b",
    *,
    pack_id: str = "python-table-summary",
    version: str = "1.0.0",
) -> CapabilityGovernanceTarget:
    return CapabilityGovernanceTarget(
        owner_id=None,
        scope=ProcedureScope.PLATFORM,
        pack_id=pack_id,
        version=version,
        digest="sha256:" + digest_char * 64,
    )


def _personal_target(
    digest_char: str = "a",
    *,
    owner_id: str = "owner-a",
) -> CapabilityGovernanceTarget:
    return CapabilityGovernanceTarget(
        owner_id=owner_id,
        scope=ProcedureScope.PERSONAL,
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + digest_char * 64,
    )


def _candidate_event(
    target: CapabilityGovernanceTarget,
    **overrides: object,
) -> CapabilityGovernanceEvent:
    fields: dict[str, object] = {
        "event_type": "platform_candidate",
        "idempotency_key": "candidate:owner-a:sha256-a",
        "actor_id": "admin-a",
        "actor_role": "admin",
        "maturity": CapabilityMaturity.VERIFIED,
        "reason": "平台候选：该能力已完成个人验证",
        "source_digest": "sha256:" + "a" * 64,
        "platform_digest": "sha256:" + "b" * 64,
    }
    fields.update(overrides)
    return CapabilityGovernanceEvent(target=target, **fields)


def _published_event(
    target: CapabilityGovernanceTarget,
    **overrides: object,
) -> CapabilityGovernanceEvent:
    fields: dict[str, object] = {
        "event_type": "platform_published",
        "idempotency_key": "publish:sha256-b",
        "actor_id": "admin-a",
        "actor_role": "admin",
        "maturity": CapabilityMaturity.VERIFIED,
        "reason": "发布：六步验证与签名全部通过",
        "source_digest": "sha256:" + "a" * 64,
        "platform_digest": "sha256:" + "b" * 64,
        "audience": "admin_gray",
        "platform_validation_run_id": "pfval_" + "a" * 20,
        "signing_signature_digest": "sha256:" + "c" * 64,
        "signing_public_key_sha256": "d" * 64,
    }
    fields.update(overrides)
    return CapabilityGovernanceEvent(target=target, **fields)


def _snapshot(
    source_digest: str = "sha256:" + "a" * 64,
    platform_digest: str = "sha256:" + "b" * 64,
) -> PlatformSnapshot:
    return PlatformSnapshot(
        pack_id="python-table-summary",
        version="1.0.0",
        source_digest=source_digest,
        platform_digest=platform_digest,
        manifest_summary=(
            "schema_version",
            "name",
            "version",
            "kind",
            "entrypoint",
            "healthcheck",
            "permissions",
        ),
    )


class TestS1PlatformEventModel:
    """发布类事件字段与分支校验；旧 payload 兼容；投影受众。"""

    def test_candidate_event_requires_source_and_platform_digest(self) -> None:
        with pytest.raises(ValidationError):
            _candidate_event(_platform_target(), source_digest=None)
        with pytest.raises(ValidationError):
            _candidate_event(_platform_target(), platform_digest=None)

    def test_candidate_event_rejects_audience_and_validation_refs(self) -> None:
        with pytest.raises(ValidationError):
            _candidate_event(_platform_target(), audience="admin_gray")
        with pytest.raises(ValidationError):
            _candidate_event(
                _platform_target(),
                platform_validation_run_id="pfval_" + "a" * 20,
            )
        with pytest.raises(ValidationError):
            _candidate_event(
                _platform_target(),
                signing_signature_digest="sha256:" + "c" * 64,
            )

    def test_candidate_event_requires_verified_active_eligible(self) -> None:
        with pytest.raises(ValidationError):
            _candidate_event(_platform_target(), maturity=CapabilityMaturity.DRAFT)
        with pytest.raises(ValidationError):
            _candidate_event(
                _platform_target(), lifecycle=CapabilityLifecycle.DEPRECATED
            )
        with pytest.raises(ValidationError):
            _candidate_event(
                _platform_target(), eligibility=CapabilityEligibility.QUARANTINED
            )

    def test_candidate_event_requires_reason(self) -> None:
        with pytest.raises(ValidationError):
            _candidate_event(_platform_target(), reason=None)

    def test_candidate_event_requires_platform_target(self) -> None:
        with pytest.raises(ValidationError):
            _candidate_event(_personal_target())

    def test_published_event_requires_admin_gray_audience_fixed(self) -> None:
        event = _published_event(_platform_target())
        assert event.audience == "admin_gray"
        with pytest.raises(ValidationError):
            # #12 阶段发布只允许 admin_gray；users 由受众变更命令产生。
            _published_event(_platform_target(), audience="users")
        with pytest.raises(ValidationError):
            _published_event(_platform_target(), audience=None)

    def test_published_event_requires_validation_and_signing_refs(self) -> None:
        with pytest.raises(ValidationError):
            _published_event(_platform_target(), platform_validation_run_id=None)
        with pytest.raises(ValidationError):
            _published_event(_platform_target(), signing_signature_digest=None)
        with pytest.raises(ValidationError):
            _published_event(_platform_target(), signing_public_key_sha256=None)

    def test_audience_changed_event_requires_new_audience(self) -> None:
        event = CapabilityGovernanceEvent(
            event_type="audience_changed",
            idempotency_key="audience:users",
            target=_platform_target(),
            actor_id="admin-a",
            actor_role="admin",
            maturity=CapabilityMaturity.VERIFIED,
            reason="开放普通用户：重新检查签名与扫描后授权",
            audience="users",
        )
        assert event.audience == "users"
        with pytest.raises(ValidationError):
            CapabilityGovernanceEvent(
                event_type="audience_changed",
                idempotency_key="audience:no-target",
                target=_platform_target(),
                actor_id="admin-a",
                actor_role="admin",
                maturity=CapabilityMaturity.VERIFIED,
                reason="缺少受众",
            )
        with pytest.raises(ValidationError):
            # 受众变更只能针对平台目标。
            CapabilityGovernanceEvent(
                event_type="audience_changed",
                idempotency_key="audience:personal",
                target=_personal_target(),
                actor_id="admin-a",
                actor_role="admin",
                maturity=CapabilityMaturity.VERIFIED,
                reason="个人能力没有平台受众",
                audience="users",
            )

    def test_other_event_types_reject_platform_fields(self) -> None:
        platform_fields = (
            {"source_digest": "sha256:" + "a" * 64},
            {"platform_digest": "sha256:" + "b" * 64},
            {"audience": "admin_gray"},
            {"platform_validation_run_id": "pfval_" + "a" * 20},
            {"signing_signature_digest": "sha256:" + "c" * 64},
            {"signing_public_key_sha256": "d" * 64},
        )
        for extra in platform_fields:
            with pytest.raises(ValidationError):
                CapabilityGovernanceEvent(
                    idempotency_key="register-x",
                    target=_personal_target(),
                    actor_id="owner-a",
                    actor_role="user",
                    **extra,
                )
            with pytest.raises(ValidationError):
                CapabilityGovernanceEvent(
                    event_type="promoted_to_verified",
                    idempotency_key="promotion:run-a",
                    target=_personal_target(),
                    maturity=CapabilityMaturity.VERIFIED,
                    actor_id="owner-a",
                    actor_role="user",
                    source_validation_run_id="capval_a1b2c3d4e5f6a1b2c3d4",
                    source_supply_chain_evidence_id="supply_" + "a" * 20,
                    **extra,
                )

    def test_legacy_payload_without_platform_fields_round_trips(self) -> None:
        payload = json.dumps(
            {
                "event_id": "capgov_legacy02",
                "idempotency_key": "register-legacy",
                "target": _personal_target().model_dump(mode="json"),
                "event_type": "registered",
                "maturity": "draft",
                "lifecycle": "active",
                "eligibility": "eligible",
                "actor_id": "owner-a",
                "actor_role": "user",
                "source_validation_run_id": None,
                "source_supply_chain_evidence_id": None,
                "reason": None,
                "subject_type": None,
                "subject_sha256": None,
                "result": None,
                "task_id": None,
                "revision": None,
                "failure_reason": None,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        revived = CapabilityGovernanceEvent.model_validate_json(payload)
        assert revived.event_type == "registered"
        assert revived.source_digest is None
        assert revived.platform_digest is None
        assert revived.audience is None

    def test_projection_carries_audience(self) -> None:
        projection = CapabilityGovernanceProjection(
            target=_platform_target(),
            maturity=CapabilityMaturity.VERIFIED,
            lifecycle=CapabilityLifecycle.ACTIVE,
            eligibility=CapabilityEligibility.ELIGIBLE,
            source="governance_event",
            audience="admin_gray",
        )
        assert projection.audience == "admin_gray"


class TestS1PlatformModels:
    """PlatformValidationRun / PlatformSnapshot / Outcome 模型。"""

    def test_platform_validation_run_holds_six_step_evidence(self) -> None:
        run = PlatformValidationRun(
            run_id="pfval_" + "a" * 20,
            actor_id="admin-a",
            actor_role="admin",
            idempotency_key="pfval-one",
            target=_platform_target(),
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
        )
        assert len(run.evidence) == 6
        assert {item.step for item in run.evidence} == set(PlatformValidationStep)

    def test_platform_validation_run_requires_platform_target(self) -> None:
        with pytest.raises(ValidationError):
            PlatformValidationRun(
                run_id="pfval_" + "a" * 20,
                actor_id="admin-a",
                actor_role="admin",
                idempotency_key="pfval-personal",
                target=_personal_target(),
                status=ValidationRunStatus.QUEUED,
            )

    def test_platform_validation_run_rejects_personal_step_evidence(self) -> None:
        with pytest.raises(ValidationError):
            PlatformValidationRun(
                run_id="pfval_" + "a" * 20,
                actor_id="admin-a",
                actor_role="admin",
                idempotency_key="pfval-bad-step",
                target=_platform_target(),
                status=ValidationRunStatus.QUEUED,
                evidence=(
                    PlatformValidationEvidence(
                        step="owner_task_replay",  # 不存在于平台六步
                        status=ValidationStepStatus.PASSED,
                        evidence_ref="evidence://platform/run/bad",
                        evidence_sha256="e" * 64,
                        summary="个人重放不得进入平台验证",
                    ),
                ),
            )

    def test_snapshot_fields(self) -> None:
        snapshot = _snapshot()
        assert snapshot.pack_id == "python-table-summary"
        assert snapshot.platform_digest.startswith("sha256:")
        assert "purpose" not in snapshot.manifest_summary
        assert "secret_ref" not in snapshot.manifest_summary
        assert "connection_ref" not in snapshot.manifest_summary

    def test_snapshot_rejects_mismatched_digests(self) -> None:
        with pytest.raises(ValidationError):
            _snapshot(
                source_digest="not-a-digest",
            )

    def test_candidate_outcome_shapes(self) -> None:
        target = _platform_target()
        snapshot = _snapshot()
        created = PlatformCandidateOutcome(
            status="created",
            snapshot=snapshot,
            event=_candidate_event(target),
        )
        assert created.snapshot == snapshot
        with pytest.raises(ValidationError):
            PlatformCandidateOutcome(status="created", snapshot=None, event=None)
        with pytest.raises(ValidationError):
            PlatformCandidateOutcome(
                status="rejected",
                snapshot=None,
                event=None,
                gaps=(),
            )
        rejected = PlatformCandidateOutcome(
            status="rejected",
            snapshot=None,
            event=None,
            gaps=("not_verified", "not_personal"),
        )
        assert rejected.gaps == ("not_verified", "not_personal")

    def test_publish_outcome_shapes(self) -> None:
        target = _platform_target()
        event = _published_event(target)
        published = PublishOutcome(status="published", event=event)
        assert published.event == event
        with pytest.raises(ValidationError):
            PublishOutcome(status="published", event=None)
        with pytest.raises(ValidationError):
            PublishOutcome(status="not_ready", event=None, gaps=())

    def test_audience_outcome_shapes(self) -> None:
        target = _platform_target()
        event = CapabilityGovernanceEvent(
            event_type="audience_changed",
            idempotency_key="audience:users",
            target=target,
            actor_id="admin-a",
            actor_role="admin",
            maturity=CapabilityMaturity.VERIFIED,
            reason="开放普通用户",
            audience="users",
        )
        changed = AudienceOutcome(status="changed", event=event)
        assert changed.event == event
        with pytest.raises(ValidationError):
            AudienceOutcome(status="changed", event=None)


class TestS2PlatformRepository:
    """发布事件专用入口与平台验证运行 CRUD；InMemory + SQLite 双实现。"""

    def test_memory_save_platform_event_is_idempotent(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        target = _platform_target()
        first = repository.save_platform_event(_candidate_event(target))
        second = repository.save_platform_event(
            _candidate_event(target, event_id="capgov_other_candidate")
        )
        assert second == first

    def test_memory_save_platform_event_rejects_other_types(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        with pytest.raises(ValueError):
            repository.save_platform_event(
                CapabilityGovernanceEvent(
                    idempotency_key="register-x",
                    target=_personal_target(),
                    actor_id="owner-a",
                    actor_role="user",
                )
            )

    def test_memory_list_platform_events_filters_by_type_and_sorts(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        target = _platform_target()
        first = repository.save_platform_event(
            _candidate_event(
                target,
                idempotency_key="candidate:one",
                occurred_at=datetime(2026, 8, 14, 1, 0, tzinfo=timezone.utc),
            )
        )
        second = repository.save_platform_event(
            _candidate_event(
                target,
                idempotency_key="candidate:two",
                occurred_at=datetime(2026, 8, 14, 2, 0, tzinfo=timezone.utc),
            )
        )
        assert repository.list_platform_events(target) == (first, second)
        assert (
            repository.get_latest_platform_event(
                target, "platform_candidate"
            )
            == second
        )
        assert (
            repository.get_latest_platform_event(
                target, "platform_published"
            )
            is None
        )

    def test_memory_platform_validation_run_crud(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        target = _platform_target()
        run = PlatformValidationRun(
            run_id="pfval_" + "a" * 20,
            actor_id="admin-a",
            actor_role="admin",
            idempotency_key="pfval-one",
            target=target,
            status=ValidationRunStatus.SUCCEEDED,
        )
        created = repository.create_platform_validation_run(run)
        assert created == run
        # 同幂等键重试返回同一运行。
        repeated = repository.create_platform_validation_run(
            PlatformValidationRun(
                run_id="pfval_" + "b" * 20,
                actor_id="admin-a",
                actor_role="admin",
                idempotency_key="pfval-one",
                target=target,
                status=ValidationRunStatus.QUEUED,
            )
        )
        assert repeated == run
        assert repository.get_platform_validation_run(run.run_id) == run
        assert repository.list_platform_validation_runs() == (run,)
        with pytest.raises(ValueError):
            # 同幂等键不得改写请求。
            repository.create_platform_validation_run(
                PlatformValidationRun(
                    run_id="pfval_" + "c" * 20,
                    actor_id="admin-a",
                    actor_role="admin",
                    idempotency_key="pfval-one",
                    target=_platform_target(digest_char="c"),
                    status=ValidationRunStatus.QUEUED,
                )
            )

    def test_sqlite_platform_event_idempotent_and_migration_replays(
        self, tmp_path
    ) -> None:
        db_path = tmp_path / "webui.db"
        from src.capability_governance import (
            SqliteCapabilityGovernanceRepository,
            migrate_capability_governance,
        )

        migrate_capability_governance(db_path, tmp_path / "backup-a.db")
        # 升级场景：新备份路径重放全部迁移不得失败。
        migrate_capability_governance(db_path, tmp_path / "backup-b.db")
        repository = SqliteCapabilityGovernanceRepository(str(db_path))
        target = _platform_target()
        first = repository.save_platform_event(_candidate_event(target))
        second = repository.save_platform_event(
            _candidate_event(target, event_id="capgov_sqlite_other")
        )
        assert second == first
        assert repository.list_platform_events(target) == (first,)

    def test_sqlite_platform_validation_run_crud(self, tmp_path) -> None:
        import sqlite3 as sqlite3_module

        from src.capability_governance import (
            SqliteCapabilityGovernanceRepository,
            migrate_capability_governance,
        )

        db_path = tmp_path / "webui.db"
        migrate_capability_governance(db_path, tmp_path / "backup.db")
        with sqlite3_module.connect(db_path) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert "capability_platform_validation_runs" in tables
        repository = SqliteCapabilityGovernanceRepository(str(db_path))
        target = _platform_target()
        run = PlatformValidationRun(
            run_id="pfval_" + "a" * 20,
            actor_id="admin-a",
            actor_role="admin",
            idempotency_key="pfval-sqlite",
            target=target,
            status=ValidationRunStatus.QUEUED,
        )
        created = repository.create_platform_validation_run(run)
        assert created == run
        repeated = repository.create_platform_validation_run(
            PlatformValidationRun(
                run_id="pfval_" + "b" * 20,
                actor_id="admin-a",
                actor_role="admin",
                idempotency_key="pfval-sqlite",
                target=target,
                status=ValidationRunStatus.RUNNING,
            )
        )
        assert repeated == run
        assert repository.get_platform_validation_run(run.run_id) == run
        assert repository.list_platform_validation_runs() == (run,)

    def test_sqlite_platform_events_coexist_with_governance_events(
        self, tmp_path
    ) -> None:
        from src.capability_governance import (
            SqliteCapabilityGovernanceRepository,
            migrate_capability_governance,
        )

        db_path = tmp_path / "webui.db"
        migrate_capability_governance(db_path, tmp_path / "backup.db")
        repository = SqliteCapabilityGovernanceRepository(str(db_path))
        personal = _personal_target()
        repository.save_event(
            CapabilityGovernanceEvent(
                idempotency_key="register-a",
                target=personal,
                actor_id="owner-a",
                actor_role="user",
            )
        )
        platform = _platform_target()
        candidate = repository.save_platform_event(
            _candidate_event(platform)
        )
        all_events = repository.list_events(platform)
        assert all_events == (candidate,)
        assert len(repository.list_events(personal)) == 1


class _StubSnapshotGenerator:
    def __init__(self, snapshot: PlatformSnapshot) -> None:
        self._snapshot = snapshot
        self.calls: list = []

    def generate(self, pack):
        self.calls.append(pack)
        return self._snapshot


class _StubPublisher:
    def __init__(self, catalog_repository=None) -> None:
        self.calls: list = []
        self._catalog_repository = catalog_repository

    def save_pack(self, pack):
        self.calls.append(pack)
        if self._catalog_repository is not None:
            self._catalog_repository.save_pack(pack)
        return pack


def _save_platform_evidence(
    repository: InMemoryCapabilityGovernanceRepository,
    target: CapabilityGovernanceTarget,
) -> None:
    """平台六步验证的 Trivy/Syft 共享供应链采集证据（#14 发布门复查时效）。"""
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


def _verified_personal_governance(
    repository: InMemoryCapabilityGovernanceRepository,
    snapshot_generator: _StubSnapshotGenerator,
    publisher: _StubPublisher,
    catalog_repository: InMemoryCapabilityCatalogRepository,
) -> tuple[CapabilityGovernance, CapabilityGovernanceTarget]:
    """注册个人 pack + 写晋级事件，使个人投影为 verified/active/eligible。"""
    from src.capability_governance import CapabilityGovernanceEvent
    from src.conversation_steering import CapabilityPack

    catalog = CapabilityCatalog(catalog_repository)
    governance = CapabilityGovernance(
        catalog,
        repository,
        platform_snapshot_generator=snapshot_generator,
        platform_publisher=publisher,
    )
    owner = CatalogActor(owner_id="owner-a", role="user")
    target = _personal_target()
    catalog.register_pack(
        owner,
        CapabilityPack(
            pack_id=target.pack_id,
            version=target.version,
            digest=target.digest,
            scope=ProcedureScope.PERSONAL,
            maturity=CapabilityMaturity.DRAFT,
            owner_id="owner-a",
        ),
    )
    governance.register_pack(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=target.pack_id,
            version=target.version,
            digest=target.digest,
        ),
        idempotency_key="register-a",
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
    return governance, target


class TestS5ServiceCommands:
    """服务层：提交候选、发布、受众变更与投影。"""

    def _fixture(self):
        repository = InMemoryCapabilityGovernanceRepository()
        catalog_repository = InMemoryCapabilityCatalogRepository()
        snapshot = _snapshot()
        generator = _StubSnapshotGenerator(snapshot)
        publisher = _StubPublisher(catalog_repository)
        governance, personal = _verified_personal_governance(
            repository, generator, publisher, catalog_repository
        )
        return repository, governance, personal, generator, publisher

    def _admin(self) -> CatalogActor:
        return CatalogActor(owner_id="admin-a", role="admin")

    def test_submit_candidate_requires_admin(self) -> None:
        repository, governance, personal, _, _ = self._fixture()
        with pytest.raises(PermissionError):
            governance.submit_platform_candidate(
                CatalogActor(owner_id="owner-a", role="user"),
                pack_ref=CapabilityPackRef(
                    pack_id=personal.pack_id,
                    version=personal.version,
                    digest=personal.digest,
                ),
                reason="候选提交",
                idempotency_key="candidate:one",
            )

    def test_submit_candidate_rejects_draft_personal_pack(self) -> None:
        from src.capability_governance import CapabilityGovernanceEvent
        from src.conversation_steering import CapabilityPack

        repository = InMemoryCapabilityGovernanceRepository()
        generator = _StubSnapshotGenerator(_snapshot())
        publisher = _StubPublisher()
        catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
        governance = CapabilityGovernance(
            catalog,
            repository,
            platform_snapshot_generator=generator,
            platform_publisher=publisher,
        )
        owner = CatalogActor(owner_id="owner-a", role="user")
        target = _personal_target()
        catalog.register_pack(
            owner,
            CapabilityPack(
                pack_id=target.pack_id,
                version=target.version,
                digest=target.digest,
                scope=ProcedureScope.PERSONAL,
                maturity=CapabilityMaturity.DRAFT,
                owner_id="owner-a",
            ),
        )
        governance.register_pack(
            owner,
            pack_ref=CapabilityPackRef(
                pack_id=target.pack_id,
                version=target.version,
                digest=target.digest,
            ),
            idempotency_key="register-a",
        )
        outcome = governance.submit_platform_candidate(
            self._admin(),
            pack_ref=CapabilityPackRef(
                pack_id=target.pack_id,
                version=target.version,
                digest=target.digest,
            ),
            reason="候选提交",
            idempotency_key="candidate:draft",
        )
        assert outcome.status == "rejected"
        assert "not_verified" in outcome.gaps
        assert generator.calls == []

    def test_submit_candidate_rejects_platform_pack(self) -> None:
        from src.conversation_steering import CapabilityPack

        repository = InMemoryCapabilityGovernanceRepository()
        generator = _StubSnapshotGenerator(_snapshot())
        publisher = _StubPublisher()
        catalog_repository = InMemoryCapabilityCatalogRepository()
        catalog = CapabilityCatalog(catalog_repository)
        governance = CapabilityGovernance(
            catalog,
            repository,
            platform_snapshot_generator=generator,
            platform_publisher=publisher,
        )
        # 平台 pack 只能由发布流程写入目录（register_pack 拒绝）；测试直接落库模拟。
        catalog_repository.save_pack(
            CapabilityPack(
                pack_id="gray-python-table",
                version="1.0.0",
                digest="sha256:" + "c" * 64,
                scope=ProcedureScope.PLATFORM,
                maturity=CapabilityMaturity.VERIFIED,
            ),
        )
        outcome = governance.submit_platform_candidate(
            self._admin(),
            pack_ref=CapabilityPackRef(
                pack_id="gray-python-table",
                version="1.0.0",
                digest="sha256:" + "c" * 64,
            ),
            reason="重复候选",
            idempotency_key="candidate:platform",
        )
        assert outcome.status == "rejected"
        assert "platform_scope" in outcome.gaps

    def test_submit_candidate_creates_snapshot_and_event(self) -> None:
        repository, governance, personal, generator, publisher = self._fixture()
        outcome = governance.submit_platform_candidate(
            self._admin(),
            pack_ref=CapabilityPackRef(
                pack_id=personal.pack_id,
                version=personal.version,
                digest=personal.digest,
            ),
            reason="平台候选：个人验证已完成",
            idempotency_key="candidate:one",
        )
        assert outcome.status == "created"
        assert len(generator.calls) == 1
        assert outcome.snapshot.platform_digest == _snapshot().platform_digest
        platform_target = CapabilityGovernanceTarget(
            owner_id=None,
            scope=ProcedureScope.PLATFORM,
            pack_id=personal.pack_id,
            version=personal.version,
            digest=outcome.snapshot.platform_digest,
        )
        events = repository.list_platform_events(platform_target)
        assert len(events) == 1
        assert events[0].event_type == "platform_candidate"
        assert events[0].source_digest == personal.digest
        # 候选不改变个人投影与平台目录。
        assert publisher.calls == []

    def test_resubmit_after_failed_validation_creates_retry_run(self) -> None:
        """候选事件幂等保留；验证失败后重提候选创建新运行重试，失败记录不覆盖。"""
        repository, governance, personal, generator, publisher = self._fixture()
        outcome = governance.submit_platform_candidate(
            self._admin(),
            pack_ref=CapabilityPackRef(
                pack_id=personal.pack_id,
                version=personal.version,
                digest=personal.digest,
            ),
            reason="平台候选：个人验证已完成",
            idempotency_key="candidate:one",
        )
        platform_target = CapabilityGovernanceTarget(
            owner_id=None,
            scope=ProcedureScope.PLATFORM,
            pack_id=personal.pack_id,
            version=personal.version,
            digest=outcome.snapshot.platform_digest,
        )
        failed_run = repository.list_platform_validation_runs()[0]
        repository.save_platform_validation_run(
            failed_run.model_copy(update={"status": ValidationRunStatus.FAILED})
        )
        resubmit = governance.submit_platform_candidate(
            self._admin(),
            pack_ref=CapabilityPackRef(
                pack_id=personal.pack_id,
                version=personal.version,
                digest=personal.digest,
            ),
            reason="平台候选：个人验证已完成",
            idempotency_key="candidate:one",
        )
        assert resubmit.status == "already_submitted"
        runs = repository.list_platform_validation_runs()
        assert len(runs) == 2
        assert {run.status for run in runs} == {
            ValidationRunStatus.FAILED,
            ValidationRunStatus.QUEUED,
        }
        # 候选事件仍只有一条。
        assert len(repository.list_platform_events(platform_target)) == 1

    def test_submit_candidate_idempotent(self) -> None:
        repository, governance, personal, _, _ = self._fixture()
        common = dict(
            actor=self._admin(),
            pack_ref=CapabilityPackRef(
                pack_id=personal.pack_id,
                version=personal.version,
                digest=personal.digest,
            ),
            reason="平台候选：个人验证已完成",
            idempotency_key="candidate:one",
        )
        first = governance.submit_platform_candidate(**common)
        second = governance.submit_platform_candidate(**common)
        assert second.status == "already_submitted"
        assert second.event.event_id == first.event.event_id

    def _green_candidate_fixture(self):
        repository, governance, personal, generator, publisher = self._fixture()
        outcome = governance.submit_platform_candidate(
            self._admin(),
            pack_ref=CapabilityPackRef(
                pack_id=personal.pack_id,
                version=personal.version,
                digest=personal.digest,
            ),
            reason="平台候选：个人验证已完成",
            idempotency_key="candidate:one",
        )
        platform_target = CapabilityGovernanceTarget(
            owner_id=None,
            scope=ProcedureScope.PLATFORM,
            pack_id=personal.pack_id,
            version=personal.version,
            digest=outcome.snapshot.platform_digest,
        )
        # 六步全绿 + 签名证据齐备的平台验证运行。
        repository.create_platform_validation_run(
            PlatformValidationRun(
                run_id="pfval_" + "a" * 20,
                actor_id="admin-a",
                actor_role="admin",
                idempotency_key="pfval-green",
                target=platform_target,
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
        # 平台验证的 Trivy/Syft 共享供应链采集证据（#14 发布门复查其时效）。
        _save_platform_evidence(repository, platform_target)
        return repository, governance, personal, publisher, platform_target

    def test_publish_requires_candidate_and_green_validation(self) -> None:
        repository, governance, personal, _, _ = self._fixture()
        outcome = governance.publish_platform(
            self._admin(),
            pack_ref=CapabilityPackRef(
                pack_id=personal.pack_id,
                version=personal.version,
                digest=_snapshot().platform_digest,
            ),
            reason="发布",
            idempotency_key="publish:one",
        )
        assert outcome.status == "not_ready"
        assert "no_candidate" in outcome.gaps

    def test_publish_rejects_candidate_without_green_validation(self) -> None:
        repository, governance, personal, _, publisher = self._fixture()
        outcome = governance.submit_platform_candidate(
            self._admin(),
            pack_ref=CapabilityPackRef(
                pack_id=personal.pack_id,
                version=personal.version,
                digest=personal.digest,
            ),
            reason="平台候选：个人验证已完成",
            idempotency_key="candidate:one",
        )
        result = governance.publish_platform(
            self._admin(),
            pack_ref=CapabilityPackRef(
                pack_id=personal.pack_id,
                version=personal.version,
                digest=outcome.snapshot.platform_digest,
            ),
            reason="发布",
            idempotency_key="publish:one",
        )
        assert result.status == "not_ready"
        assert "validation_not_green" in result.gaps

    def test_publish_rejects_green_run_without_signing(self) -> None:
        repository, governance, personal, _, publisher = self._fixture()
        outcome = governance.submit_platform_candidate(
            self._admin(),
            pack_ref=CapabilityPackRef(
                pack_id=personal.pack_id,
                version=personal.version,
                digest=personal.digest,
            ),
            reason="平台候选：个人验证已完成",
            idempotency_key="candidate:one",
        )
        platform_target = CapabilityGovernanceTarget(
            owner_id=None,
            scope=ProcedureScope.PLATFORM,
            pack_id=personal.pack_id,
            version=personal.version,
            digest=outcome.snapshot.platform_digest,
        )
        repository.create_platform_validation_run(
            PlatformValidationRun(
                run_id="pfval_" + "a" * 20,
                actor_id="admin-a",
                actor_role="admin",
                idempotency_key="pfval-green-no-sign",
                target=platform_target,
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
            )
        )
        _save_platform_evidence(repository, platform_target)
        result = governance.publish_platform(
            self._admin(),
            pack_ref=CapabilityPackRef(
                pack_id=personal.pack_id,
                version=personal.version,
                digest=platform_target.digest,
            ),
            reason="发布",
            idempotency_key="publish:one",
        )
        assert result.status == "not_ready"
        assert "signing_missing" in result.gaps
        assert publisher.calls == []

    def test_publish_success_fixes_admin_gray_audience(self) -> None:
        (
            repository,
            governance,
            personal,
            publisher,
            platform_target,
        ) = self._green_candidate_fixture()
        outcome = governance.publish_platform(
            self._admin(),
            pack_ref=CapabilityPackRef(
                pack_id=personal.pack_id,
                version=personal.version,
                digest=platform_target.digest,
            ),
            reason="发布：六步验证与签名全部通过",
            idempotency_key="publish:one",
        )
        assert outcome.status == "published"
        assert outcome.event.audience == "admin_gray"
        assert outcome.event.platform_validation_run_id.startswith("pfval_")
        # 发布 Adapter 写入平台 pack（唯一写入口）。
        assert len(publisher.calls) == 1
        published_pack = publisher.calls[0]
        assert published_pack.scope is ProcedureScope.PLATFORM
        assert published_pack.digest == platform_target.digest
        assert published_pack.maturity.value == "verified"

    def test_publish_idempotent(self) -> None:
        (
            repository,
            governance,
            personal,
            publisher,
            platform_target,
        ) = self._green_candidate_fixture()
        common = dict(
            actor=self._admin(),
            pack_ref=CapabilityPackRef(
                pack_id=personal.pack_id,
                version=personal.version,
                digest=platform_target.digest,
            ),
            reason="发布：六步验证与签名全部通过",
            idempotency_key="publish:one",
        )
        first = governance.publish_platform(**common)
        second = governance.publish_platform(**common)
        assert second.status == "already_published"
        assert second.event.event_id == first.event.event_id
        assert len(publisher.calls) == 1

    def test_publish_without_publisher_fails_closed(self) -> None:
        """发布目录写入缺失必须失败关闭，不能留下投影已发布但目录无 pack 的孤儿。"""
        repository = InMemoryCapabilityGovernanceRepository()
        catalog_repository = InMemoryCapabilityCatalogRepository()
        generator = _StubSnapshotGenerator(_snapshot())
        catalog = CapabilityCatalog(catalog_repository)
        governance = CapabilityGovernance(
            catalog,
            repository,
            platform_snapshot_generator=generator,
            platform_publisher=None,
        )
        owner = CatalogActor(owner_id="owner-a", role="user")
        target = _personal_target()
        from src.conversation_steering import CapabilityPack

        catalog.register_pack(
            owner,
            CapabilityPack(
                pack_id=target.pack_id,
                version=target.version,
                digest=target.digest,
                scope=ProcedureScope.PERSONAL,
                maturity=CapabilityMaturity.DRAFT,
                owner_id="owner-a",
            ),
        )
        governance.register_pack(
            owner,
            pack_ref=CapabilityPackRef(
                pack_id=target.pack_id,
                version=target.version,
                digest=target.digest,
            ),
            idempotency_key="register-a",
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
        outcome = governance.submit_platform_candidate(
            self._admin(),
            pack_ref=CapabilityPackRef(
                pack_id=target.pack_id,
                version=target.version,
                digest=target.digest,
            ),
            reason="平台候选：个人验证已完成",
            idempotency_key="candidate:one",
        )
        platform_target = CapabilityGovernanceTarget(
            owner_id=None,
            scope=ProcedureScope.PLATFORM,
            pack_id=target.pack_id,
            version=target.version,
            digest=outcome.snapshot.platform_digest,
        )
        repository.create_platform_validation_run(
            PlatformValidationRun(
                run_id="pfval_" + "a" * 20,
                actor_id="admin-a",
                actor_role="admin",
                idempotency_key="pfval-green",
                target=platform_target,
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
        _save_platform_evidence(repository, platform_target)
        with pytest.raises(RuntimeError):
            governance.publish_platform(
                self._admin(),
                pack_ref=CapabilityPackRef(
                    pack_id=target.pack_id,
                    version=target.version,
                    digest=platform_target.digest,
                ),
                reason="发布",
                idempotency_key="publish:one",
            )
        # 事件不落库：重试可完整重走。
        assert (
            repository.get_latest_platform_event(
                platform_target, "platform_published"
            )
            is None
        )

    def test_change_audience_requires_green_and_signed_run(self) -> None:
        """受众变更必须重查验证六步与签名证据；只有历史发布事件不放行。"""
        (
            repository,
            governance,
            personal,
            publisher,
            platform_target,
        ) = self._green_candidate_fixture()
        governance.publish_platform(
            self._admin(),
            pack_ref=CapabilityPackRef(
                pack_id=personal.pack_id,
                version=personal.version,
                digest=platform_target.digest,
            ),
            reason="发布：六步验证与签名全部通过",
            idempotency_key="publish:one",
        )
        # 用无签名的绿色运行替换：约束检查必须拒绝。
        unsigned = PlatformValidationRun(
            run_id="pfval_" + "b" * 20,
            actor_id="admin-a",
            actor_role="admin",
            idempotency_key="pfval-unsigned",
            target=platform_target,
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
        )
        repository.create_platform_validation_run(unsigned)
        # 发布历史事件仍在，但最新绿色运行无签名 → 拒绝。
        # 注意：fixture 的签名运行仍在列表里，约束按"存在绿色+签名运行"判定。
        outcome = governance.change_audience(
            self._admin(),
            pack_ref=CapabilityPackRef(
                pack_id=personal.pack_id,
                version=personal.version,
                digest=platform_target.digest,
            ),
            audience="users",
            reason="开放普通用户",
            idempotency_key="audience:one",
        )
        assert outcome.status == "changed"
        # 全部绿色+签名运行删除后，约束拒绝。
        for run_id in ("pfval_" + "a" * 20, "pfval_" + "b" * 20):
            pass  # InMemory 无删除接口；改用新 repository 场景测拒绝分支。
        repository2 = InMemoryCapabilityGovernanceRepository()
        catalog2 = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
        governance2 = CapabilityGovernance(catalog2, repository2)
        platform_target2 = _platform_target()
        repository2.save_platform_event(_published_event(platform_target2))
        with pytest.raises(ValueError):
            governance2.change_audience(
                self._admin(),
                pack_ref=CapabilityPackRef(
                    pack_id=platform_target2.pack_id,
                    version=platform_target2.version,
                    digest=platform_target2.digest,
                ),
                audience="users",
                reason="开放普通用户",
                idempotency_key="audience:two",
            )

    def test_publish_requires_admin(self) -> None:
        repository, governance, personal, _, _ = self._fixture()
        with pytest.raises(PermissionError):
            governance.publish_platform(
                CatalogActor(owner_id="owner-a", role="user"),
                pack_ref=CapabilityPackRef(
                    pack_id=personal.pack_id,
                    version=personal.version,
                    digest=_snapshot().platform_digest,
                ),
                reason="发布",
                idempotency_key="publish:one",
            )

    def test_change_audience_requires_published(self) -> None:
        repository, governance, personal, _, _ = self._fixture()
        with pytest.raises(ValueError):
            governance.change_audience(
                self._admin(),
                pack_ref=CapabilityPackRef(
                    pack_id=personal.pack_id,
                    version=personal.version,
                    digest=_snapshot().platform_digest,
                ),
                audience="users",
                reason="开放普通用户",
                idempotency_key="audience:one",
            )

    def test_change_audience_writes_event_and_updates_projection(self) -> None:
        (
            repository,
            governance,
            personal,
            publisher,
            platform_target,
        ) = self._green_candidate_fixture()
        governance.publish_platform(
            self._admin(),
            pack_ref=CapabilityPackRef(
                pack_id=personal.pack_id,
                version=personal.version,
                digest=platform_target.digest,
            ),
            reason="发布：六步验证与签名全部通过",
            idempotency_key="publish:one",
        )
        outcome = governance.change_audience(
            self._admin(),
            pack_ref=CapabilityPackRef(
                pack_id=personal.pack_id,
                version=personal.version,
                digest=platform_target.digest,
            ),
            audience="users",
            reason="开放普通用户：重新检查签名与扫描后授权",
            idempotency_key="audience:one",
        )
        assert outcome.status == "changed"
        assert outcome.event.audience == "users"
        # 投影受众随事件流变化。
        views = governance.list_visible_projections(
            CatalogActor(owner_id="admin-x", role="admin")
        )
        platform_view = next(
            item for item in views if item.pack_id == personal.pack_id
        )
        assert platform_view.audience == "users"


class _StubPlatformExecutor:
    """替身平台执行器：按配置返回全过证据或某步失败。"""

    def __init__(self, fail_step=None) -> None:
        self._fail_step = fail_step
        self.calls: list[tuple[str, PlatformValidationStep]] = []

    def execute(self, run, step):
        self.calls.append((run.run_id, step))
        if step is self._fail_step:
            return PlatformValidationEvidence(
                step=step,
                status=ValidationStepStatus.FAILED,
                evidence_ref=f"evidence://platform/{run.run_id}/{step.value}-failure",
                evidence_sha256="f" * 64,
                summary=f"{step.value} 未通过",
            )
        return PlatformValidationEvidence(
            step=step,
            status=ValidationStepStatus.PASSED,
            evidence_ref=f"evidence://platform/{run.run_id}/{step.value}",
            evidence_sha256="e" * 64,
            summary="平台验证步骤已通过",
        )


class _StubSigning:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list = []

    def execute(self, request, *, cancel_requested=None):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        from src.capability_governance.oci_signing import OciSigningEvidence

        return OciSigningEvidence(
            transaction_id=request.transaction_id,
            subject_digest=request.subject_digest,
            signature_digest="sha256:" + "c" * 64,
            public_key_sha256="d" * 64,
            referrer_digests=(),
        )


class TestS6PlatformWorker:
    """平台验证 worker：六步编排、失败关闭、签名触发与写回。"""

    def _manager(self, repository, *, executor, signing, layout: str = "data/platform-oci"):
        from src.capability_governance.platform_validation import (
            PlatformValidationManager,
        )

        return PlatformValidationManager(
            repository,
            executor=executor,
            signing=signing,
            layout_path=layout,
            private_key_path="keys/cosign.key",
            public_key_path="keys/cosign.pub",
        )

    def test_queued_run_advances_all_six_steps_to_succeeded(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        target = _platform_target()
        repository.create_platform_validation_run(
            PlatformValidationRun(
                run_id="pfval_" + "a" * 20,
                actor_id="admin-a",
                actor_role="admin",
                idempotency_key="pfval-one",
                target=target,
                status=ValidationRunStatus.QUEUED,
            )
        )
        repository.save_platform_event(_candidate_event(target))
        executor = _StubPlatformExecutor()
        signing = _StubSigning()
        manager = self._manager(repository, executor=executor, signing=signing)
        manager.run_once()
        run = repository.get_platform_validation_run("pfval_" + "a" * 20)
        assert run.status is ValidationRunStatus.SUCCEEDED
        assert len(run.evidence) == 6
        assert len(executor.calls) == 6
        # 六步按固定顺序执行。
        assert [step for _, step in executor.calls] == [
            PlatformValidationStep.SYNTHETIC_SMOKE,
            PlatformValidationStep.FAIL_CLOSED,
            PlatformValidationStep.TRIVY,
            PlatformValidationStep.SYFT,
            PlatformValidationStep.MOUNT_PROBE,
            PlatformValidationStep.INDEPENDENT_VERIFIER,
        ]

    def test_succeeded_run_with_candidate_triggers_signing(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        target = _platform_target()
        repository.save_platform_event(_candidate_event(target))
        repository.create_platform_validation_run(
            PlatformValidationRun(
                run_id="pfval_" + "a" * 20,
                actor_id="admin-a",
                actor_role="admin",
                idempotency_key="pfval-one",
                target=target,
                status=ValidationRunStatus.QUEUED,
            )
        )
        signing = _StubSigning()
        manager = self._manager(
            repository,
            executor=_StubPlatformExecutor(),
            signing=signing,
        )
        manager.run_once()
        run = repository.get_platform_validation_run("pfval_" + "a" * 20)
        assert run.signing_signature_digest == "sha256:" + "c" * 64
        assert run.signing_public_key_sha256 == "d" * 64
        assert len(signing.calls) == 1
        # 签名请求绑定平台 digest。
        request = signing.calls[0]
        assert request.subject_digest == target.digest
        assert request.transaction_id == "platform-sign-pfval_" + "a" * 20

    def test_signing_failure_keeps_run_unsigned_for_retry(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        target = _platform_target()
        repository.save_platform_event(_candidate_event(target))
        repository.create_platform_validation_run(
            PlatformValidationRun(
                run_id="pfval_" + "a" * 20,
                actor_id="admin-a",
                actor_role="admin",
                idempotency_key="pfval-one",
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
            )
        )
        signing = _StubSigning(error=RuntimeError("Zot 注册表不可用"))
        manager = self._manager(
            repository,
            executor=_StubPlatformExecutor(),
            signing=signing,
        )
        manager.run_once()
        run = repository.get_platform_validation_run("pfval_" + "a" * 20)
        assert run.status is ValidationRunStatus.SUCCEEDED
        assert run.signing_signature_digest is None

    def test_already_signed_run_skips_signing(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        target = _platform_target()
        repository.save_platform_event(_candidate_event(target))
        repository.create_platform_validation_run(
            PlatformValidationRun(
                run_id="pfval_" + "a" * 20,
                actor_id="admin-a",
                actor_role="admin",
                idempotency_key="pfval-one",
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
        signing = _StubSigning()
        manager = self._manager(
            repository,
            executor=_StubPlatformExecutor(),
            signing=signing,
        )
        manager.run_once()
        assert signing.calls == []

    def test_run_without_candidate_skips_signing(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        target = _platform_target()
        repository.create_platform_validation_run(
            PlatformValidationRun(
                run_id="pfval_" + "a" * 20,
                actor_id="admin-a",
                actor_role="admin",
                idempotency_key="pfval-one",
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
            )
        )
        signing = _StubSigning()
        manager = self._manager(
            repository,
            executor=_StubPlatformExecutor(),
            signing=signing,
        )
        manager.run_once()
        assert signing.calls == []

    def test_lease_prevents_concurrent_advance(self) -> None:
        """digest Lease：并发 worker 不得重复推进同一运行。"""
        from datetime import timedelta as _timedelta

        repository = InMemoryCapabilityGovernanceRepository()
        target = _platform_target()
        repository.create_platform_validation_run(
            PlatformValidationRun(
                run_id="pfval_" + "a" * 20,
                actor_id="admin-a",
                actor_role="admin",
                idempotency_key="pfval-one",
                target=target,
                status=ValidationRunStatus.QUEUED,
            )
        )
        now = datetime(2026, 8, 15, 0, 0, tzinfo=timezone.utc)
        assert repository.acquire_platform_validation_lease(
            run_id="pfval_" + "a" * 20,
            digest=target.digest,
            worker_id="worker-a",
            now=now,
            lease_seconds=60,
        )
        # 其他 worker 在租约有效期内被拒绝。
        assert not repository.acquire_platform_validation_lease(
            run_id="pfval_" + "a" * 20,
            digest=target.digest,
            worker_id="worker-b",
            now=now + _timedelta(seconds=10),
            lease_seconds=60,
        )
        # 同一 worker 续租成功。
        assert repository.renew_platform_validation_lease(
            run_id="pfval_" + "a" * 20,
            digest=target.digest,
            worker_id="worker-a",
            now=now + _timedelta(seconds=30),
            lease_seconds=60,
        )
        repository.release_platform_validation_lease(
            "pfval_" + "a" * 20, "worker-a"
        )
        # 释放后其他 worker 可获取。
        assert repository.acquire_platform_validation_lease(
            run_id="pfval_" + "a" * 20,
            digest=target.digest,
            worker_id="worker-b",
            now=now + _timedelta(seconds=40),
            lease_seconds=60,
        )

    def test_lease_in_sqlite(self, tmp_path) -> None:
        from src.capability_governance import (
            SqliteCapabilityGovernanceRepository,
            migrate_capability_governance,
        )

        db_path = tmp_path / "webui.db"
        migrate_capability_governance(db_path, tmp_path / "backup.db")
        repository = SqliteCapabilityGovernanceRepository(str(db_path))
        target = _platform_target()
        repository.create_platform_validation_run(
            PlatformValidationRun(
                run_id="pfval_" + "a" * 20,
                actor_id="admin-a",
                actor_role="admin",
                idempotency_key="pfval-one",
                target=target,
                status=ValidationRunStatus.QUEUED,
            )
        )
        now = datetime(2026, 8, 15, 0, 0, tzinfo=timezone.utc)
        assert repository.acquire_platform_validation_lease(
            run_id="pfval_" + "a" * 20,
            digest=target.digest,
            worker_id="worker-a",
            now=now,
            lease_seconds=60,
        )
        assert not repository.acquire_platform_validation_lease(
            run_id="pfval_" + "a" * 20,
            digest=target.digest,
            worker_id="worker-b",
            now=now,
            lease_seconds=60,
        )
        repository.release_platform_validation_lease(
            "pfval_" + "a" * 20, "worker-a"
        )

    def test_failed_step_stops_remaining_steps_and_signing(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        target = _platform_target()
        repository.save_platform_event(_candidate_event(target))
        repository.create_platform_validation_run(
            PlatformValidationRun(
                run_id="pfval_" + "a" * 20,
                actor_id="admin-a",
                actor_role="admin",
                idempotency_key="pfval-one",
                target=target,
                status=ValidationRunStatus.QUEUED,
            )
        )
        executor = _StubPlatformExecutor(
            fail_step=PlatformValidationStep.TRIVY
        )
        signing = _StubSigning()
        manager = self._manager(repository, executor=executor, signing=signing)
        manager.run_once()
        run = repository.get_platform_validation_run("pfval_" + "a" * 20)
        assert run.status is ValidationRunStatus.FAILED
        executed = [step for _, step in executor.calls]
        assert PlatformValidationStep.SYNTHETIC_SMOKE in executed
        assert PlatformValidationStep.FAIL_CLOSED in executed
        assert PlatformValidationStep.TRIVY in executed
        # 失败关闭：trivy 之后的步骤不再执行。
        assert PlatformValidationStep.SYFT not in executed
        assert signing.calls == []
