# -*- coding: utf-8 -*-
"""AC-07：从 CapabilityGovernance 公共 Interface 验证三轴治理投影。"""
from __future__ import annotations

import json
from pathlib import Path

from src.capability_catalog import (
    CapabilityCatalog,
    CapabilityPackRef,
    CatalogActor,
    InMemoryCapabilityCatalogRepository,
    SqliteCapabilityCatalogRepository,
)
from src.capability_governance import (
    CapabilityValidationManager,
    CapabilityEligibility,
    CapabilityGovernance,
    CapabilityLifecycle,
    CapabilityMaturity,
    InMemoryCapabilityGovernanceRepository,
    SqliteCapabilityGovernanceRepository,
    SqliteValidationTaskResolver,
    TaskEvidenceValidationExecutor,
    migrate_capability_governance,
    ValidationTaskRef,
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


def _personal_pack(version: str, digest_char: str) -> CapabilityPack:
    return CapabilityPack(
        pack_id="python-table-summary",
        version=version,
        digest="sha256:" + digest_char * 64,
        scope=ProcedureScope.PERSONAL,
        maturity=LegacyCapabilityMaturity.DRAFT,
        owner_id="owner-a",
    )


def test_registration_projects_each_exact_digest_and_is_idempotent() -> None:
    catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
    repository = InMemoryCapabilityGovernanceRepository()
    governance = CapabilityGovernance(catalog, repository)
    owner = CatalogActor(owner_id="owner-a", role="user")
    first = _personal_pack("1.0.0", "a")
    second = _personal_pack("2.0.0", "b")
    catalog.register_pack(owner, first)
    catalog.register_pack(owner, second)

    first_event = governance.register_pack(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=first.pack_id,
            version=first.version,
            digest=first.digest,
        ),
        idempotency_key="register-python-table-v1",
    )
    repeated = governance.register_pack(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=first.pack_id,
            version=first.version,
            digest=first.digest,
        ),
        idempotency_key="register-python-table-v1",
    )
    governance.register_pack(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=second.pack_id,
            version=second.version,
            digest=second.digest,
        ),
        idempotency_key="register-python-table-v2",
    )

    assert repeated == first_event
    projections = governance.list_visible_projections(owner)
    assert [item.version for item in projections] == ["1.0.0", "2.0.0"]
    assert {item.maturity for item in projections} == {
        CapabilityMaturity.DRAFT
    }
    assert {item.lifecycle for item in projections} == {
        CapabilityLifecycle.ACTIVE
    }
    assert {item.eligibility for item in projections} == {
        CapabilityEligibility.ELIGIBLE
    }
    assert {item.source for item in projections} == {"governance_event"}
    assert [item.digest for item in projections] == [first.digest, second.digest]
    assert all(item.owner_id == "owner-a" for item in projections)


def test_registration_uses_exact_pack_ref_when_platform_identity_collides() -> None:
    catalog_repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(catalog_repository)
    governance = CapabilityGovernance(
        catalog,
        InMemoryCapabilityGovernanceRepository(),
    )
    owner = CatalogActor(owner_id="owner-a", role="user")
    personal = _personal_pack("1.0.0", "a")
    platform = personal.model_copy(
        update={
            "digest": "sha256:" + "b" * 64,
            "scope": ProcedureScope.PLATFORM,
            "owner_id": None,
            "maturity": LegacyCapabilityMaturity.VERIFIED,
        }
    )
    catalog.register_pack(owner, personal)
    catalog_repository.save_pack(platform)

    event = governance.register_pack(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=personal.pack_id,
            version=personal.version,
            digest=personal.digest,
        ),
        idempotency_key="register-personal-collision",
    )

    assert event.target.owner_id == "owner-a"
    assert event.target.scope is ProcedureScope.PERSONAL
    assert event.target.digest == personal.digest


def test_ac06_admin_gray_platform_pack_exposes_owner_validation_bridge() -> None:
    class Resolver:
        def __init__(self, task_ref: ValidationTaskRef) -> None:
            self.task_ref = task_ref

        def list_options(self, actor, target):
            assert actor.owner_id == "admin-a"
            assert target.scope is ProcedureScope.PLATFORM
            return ("owner-task",)

        def resolve(self, actor, target, *, task_id, revision):
            assert actor.owner_id == "admin-a"
            assert target.scope is ProcedureScope.PLATFORM
            assert (task_id, revision) == ("workspace-gray", 1)
            return self.task_ref

        def verify(self, actor, target, task_ref):
            assert actor.owner_id == "admin-a"
            assert target.scope is ProcedureScope.PLATFORM
            assert task_ref == self.task_ref
            return task_ref

    catalog_repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(catalog_repository)
    gray_pack = CapabilityPack(
        pack_id="gray-python-table",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
        scope=ProcedureScope.PLATFORM,
        maturity=LegacyCapabilityMaturity.VERIFIED,
        permission_requirements=("admin_gray_only", "network:none"),
        created_by="ac06-gray-preparation",
    )
    unrelated_platform = gray_pack.model_copy(
        update={
            "pack_id": "published-platform-tool",
            "digest": "sha256:" + "b" * 64,
            "permission_requirements": (),
            "created_by": "platform-publisher",
        }
    )
    catalog_repository.save_pack(gray_pack)
    catalog_repository.save_pack(unrelated_platform)
    task_ref = ValidationTaskRef(
        task_id="workspace-gray",
        revision=1,
        source_snapshot_sha256="c" * 64,
        input_sha256="d" * 64,
        output_sha256="e" * 64,
        capability_digest=gray_pack.digest,
        authorization_id="selection-gray",
    )
    governance = CapabilityGovernance(
        catalog,
        InMemoryCapabilityGovernanceRepository(),
        task_resolver=Resolver(task_ref),
    )
    admin = CatalogActor(owner_id="admin-a", role="admin")
    ordinary = CatalogActor(owner_id="user-a", role="user")

    admin_views = {item.pack_id: item for item in governance.list_visible_projections(admin)}
    ordinary_views = {item.pack_id: item for item in governance.list_visible_projections(ordinary)}
    assert admin_views[gray_pack.pack_id].can_validate is True
    assert admin_views[unrelated_platform.pack_id].can_validate is False
    assert ordinary_views[gray_pack.pack_id].can_validate is False
    assert governance.list_validation_task_options(
        admin,
        pack_ref=CapabilityPackRef(
            pack_id=gray_pack.pack_id,
            version=gray_pack.version,
            digest=gray_pack.digest,
        ),
    ) == ("owner-task",)

    run = governance.request_validation_for_task(
        admin,
        pack_ref=CapabilityPackRef(
            pack_id=gray_pack.pack_id,
            version=gray_pack.version,
            digest=gray_pack.digest,
        ),
        task_id="workspace-gray",
        revision=1,
        idempotency_key="validate-ac06-gray",
    )
    assert run.owner_id == admin.owner_id
    assert run.target.scope is ProcedureScope.PLATFORM


def test_legacy_projection_is_actor_scoped_without_rewriting_pack() -> None:
    catalog_repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(catalog_repository)
    governance = CapabilityGovernance(
        catalog,
        InMemoryCapabilityGovernanceRepository(),
    )
    owner_a = CatalogActor(owner_id="owner-a", role="user")
    owner_b = CatalogActor(owner_id="owner-b", role="user")
    admin = CatalogActor(owner_id="admin-a", role="admin")
    first = _personal_pack("1.0.0", "a")
    second = _personal_pack("2.0.0", "b").model_copy(
        update={"owner_id": "owner-b"}
    )
    deprecated_platform = CapabilityPack(
        pack_id="legacy-everything-mcp",
        version="2026.7.4",
        digest="sha256:" + "c" * 64,
        scope=ProcedureScope.PLATFORM,
        maturity=LegacyCapabilityMaturity.DEPRECATED,
    )
    catalog.register_pack(owner_a, first)
    catalog.register_pack(owner_b, second)
    catalog_repository.save_pack(deprecated_platform)

    owner_view = governance.list_visible_projections(owner_a)
    admin_view = governance.list_visible_projections(admin)

    assert [(item.pack_id, item.version) for item in owner_view] == [
        ("legacy-everything-mcp", "2026.7.4"),
        ("python-table-summary", "1.0.0"),
    ]
    assert owner_view[0].owner_id is None
    assert owner_view[1].owner_id == "owner-a"
    assert owner_view[0].digest is None
    assert owner_view[1].digest == first.digest
    platform_view = owner_view[0]
    assert platform_view.maturity is CapabilityMaturity.VERIFIED
    assert platform_view.lifecycle is CapabilityLifecycle.DEPRECATED
    assert platform_view.eligibility is CapabilityEligibility.ELIGIBLE
    assert platform_view.source == "legacy_compat"
    assert [(item.owner_id, item.digest) for item in admin_view] == [
        (None, deprecated_platform.digest),
        ("owner-a", first.digest),
        ("owner-b", second.digest),
    ]
    assert catalog.resolve_pack(owner_a, first.pack_id, first.version) == first
    assert catalog.resolve_pack(owner_a, second.pack_id, second.version) is None


def test_sqlite_migration_backs_up_and_reopens_without_rewriting_catalog(
    tmp_path,
) -> None:
    db_path = tmp_path / "webui.db"
    first_backup = tmp_path / "backup-before-ac07.db"
    catalog = CapabilityCatalog(
        SqliteCapabilityCatalogRepository(str(db_path))
    )
    owner = CatalogActor(owner_id="owner-a", role="user")
    admin = CatalogActor(owner_id="admin-a", role="admin")
    pack = _personal_pack("1.0.0", "a")
    catalog.register_pack(owner, pack)
    selection = catalog.freeze_selection(
        owner,
        task_id="workspace-ac07",
        revision=1,
        pack_refs=(
            CapabilityPackRef(
                pack_id=pack.pack_id,
                version=pack.version,
                digest=pack.digest,
            ),
        ),
    )

    migrate_capability_governance(db_path, first_backup)
    repeated_backup = migrate_capability_governance(db_path, first_backup)
    governance = CapabilityGovernance(
        CapabilityCatalog(SqliteCapabilityCatalogRepository(str(db_path))),
        SqliteCapabilityGovernanceRepository(str(db_path)),
    )
    governance.register_pack(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
        idempotency_key="register-after-migration",
    )

    reopened_catalog = CapabilityCatalog(
        SqliteCapabilityCatalogRepository(str(db_path))
    )
    reopened = CapabilityGovernance(
        reopened_catalog,
        SqliteCapabilityGovernanceRepository(str(db_path)),
    )
    projection = reopened.list_visible_projections(admin)[0]
    assert projection.source == "governance_event"
    assert projection.digest == pack.digest
    assert reopened_catalog.resolve_pack(owner, pack.pack_id, pack.version) == pack
    assert reopened_catalog.resolve_selection(
        owner,
        task_id="workspace-ac07",
        revision=1,
    ) == selection
    assert first_backup.is_file()
    assert repeated_backup == first_backup.resolve()
    backup_governance = CapabilityGovernance(
        CapabilityCatalog(
            SqliteCapabilityCatalogRepository(str(first_backup))
        ),
        SqliteCapabilityGovernanceRepository(str(first_backup)),
    )
    assert backup_governance.list_visible_projections(admin)[0].source == (
        "legacy_compat"
    )


def test_owner_requests_exact_digest_validation_idempotently() -> None:
    catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
    repository = InMemoryCapabilityGovernanceRepository()
    governance = CapabilityGovernance(
        catalog,
        repository,
    )
    owner = CatalogActor(owner_id="owner-a", role="user")
    other = CatalogActor(owner_id="owner-b", role="user")
    pack = _personal_pack("1.0.0", "a")
    catalog.register_pack(owner, pack)
    task_ref = ValidationTaskRef(
        task_id="workspace-validated-source",
        revision=2,
        source_snapshot_sha256="b" * 64,
        input_sha256="c" * 64,
        output_sha256="d" * 64,
        capability_digest=pack.digest,
        authorization_id="grant-owner-a-task-replay",
    )

    first = governance.request_validation(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
        task_ref=task_ref,
        idempotency_key="validate-python-table-v1",
    )
    repeated = governance.request_validation(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
        task_ref=task_ref,
        idempotency_key="validate-python-table-v1",
    )
    concurrent = governance.request_validation(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
        task_ref=task_ref.model_copy(
            update={"task_id": "workspace-other-valid-source"}
        ),
        idempotency_key="validate-python-table-concurrent",
    )

    assert repeated == first
    assert concurrent == first
    assert first.owner_id == "owner-a"
    assert first.target.digest == pack.digest
    assert first.actor_id == "owner-a"
    assert first.task_ref == task_ref
    repository.save_validation_run(
        first.model_copy(update={"status": ValidationRunStatus.SUCCEEDED})
    )
    retried_concurrent = governance.request_validation(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
        task_ref=task_ref.model_copy(
            update={"task_id": "workspace-other-valid-source"}
        ),
        idempotency_key="validate-python-table-concurrent",
    )
    assert retried_concurrent.run_id == first.run_id
    assert (
        governance.get_validation(owner, first.run_id).status
        is ValidationRunStatus.SUCCEEDED
    )
    try:
        governance.get_validation(other, first.run_id)
    except PermissionError as exc:
        assert "其他用户" in str(exc)
    else:
        raise AssertionError("跨 Owner 不得读取验证运行")


def test_validation_run_is_persisted_and_idempotent_after_reopen(tmp_path) -> None:
    db_path = tmp_path / "webui.db"
    backup = tmp_path / "before-validation-run.db"
    catalog = CapabilityCatalog(SqliteCapabilityCatalogRepository(str(db_path)))
    owner = CatalogActor(owner_id="owner-a", role="user")
    pack = _personal_pack("1.0.0", "a")
    catalog.register_pack(owner, pack)
    migrate_capability_governance(db_path, backup)
    task_ref = ValidationTaskRef(
        task_id="workspace-persisted",
        revision=1,
        source_snapshot_sha256="b" * 64,
        input_sha256="c" * 64,
        output_sha256="d" * 64,
        capability_digest=pack.digest,
        authorization_id="grant-persisted",
    )
    repository = SqliteCapabilityGovernanceRepository(str(db_path))
    first_governance = CapabilityGovernance(
        catalog,
        repository,
    )
    first = first_governance.request_validation(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
        task_ref=task_ref,
        idempotency_key="persist-validation",
    )
    merged_ref = task_ref.model_copy(update={"task_id": "workspace-merged"})
    merged = first_governance.request_validation(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
        task_ref=merged_ref,
        idempotency_key="persist-validation-merged",
    )
    assert merged.run_id == first.run_id
    repository.save_validation_run(
        first.model_copy(update={"status": ValidationRunStatus.SUCCEEDED})
    )

    reopened = CapabilityGovernance(
        CapabilityCatalog(SqliteCapabilityCatalogRepository(str(db_path))),
        SqliteCapabilityGovernanceRepository(str(db_path)),
    )
    repeated = reopened.request_validation(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
        task_ref=task_ref,
        idempotency_key="persist-validation",
    )
    repeated_merged = reopened.request_validation(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
        task_ref=merged_ref,
        idempotency_key="persist-validation-merged",
    )

    assert repeated.run_id == first.run_id
    assert repeated_merged.run_id == first.run_id
    assert (
        reopened.get_validation(owner, first.run_id).status
        is ValidationRunStatus.SUCCEEDED
    )


def test_validation_resumes_after_lease_expiry_without_repeating_steps() -> None:
    from datetime import datetime, timedelta, timezone

    class ProcessInterrupted(BaseException):
        pass

    class RecordingExecutor:
        def __init__(self) -> None:
            self.calls: list[ValidationStep] = []
            self.interrupt_once = True

        def execute(self, run, step: ValidationStep) -> ValidationEvidence:
            self.calls.append(step)
            if step is ValidationStep.OWNER_TASK_REPLAY and self.interrupt_once:
                self.interrupt_once = False
                raise ProcessInterrupted()
            return ValidationEvidence(
                step=step,
                status=ValidationStepStatus.PASSED,
                evidence_ref=f"evidence://validation/{run.run_id}/{step.value}",
                evidence_sha256="e" * 64,
                summary=f"{step.value} 已通过",
            )

    catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
    governance = CapabilityGovernance(
        catalog,
        InMemoryCapabilityGovernanceRepository(),
    )
    owner = CatalogActor(owner_id="owner-a", role="user")
    pack = _personal_pack("1.0.0", "a")
    catalog.register_pack(owner, pack)
    run = governance.request_validation(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
        task_ref=ValidationTaskRef(
            task_id="workspace-resume",
            revision=1,
            source_snapshot_sha256="b" * 64,
            input_sha256="c" * 64,
            output_sha256="d" * 64,
            capability_digest=pack.digest,
            authorization_id="grant-resume",
        ),
        idempotency_key="resume-validation",
    )
    executor = RecordingExecutor()
    started_at = datetime(2026, 8, 7, tzinfo=timezone.utc)

    try:
        governance.execute_validation(
            owner,
            run.run_id,
            worker_id="worker-a",
            executor=executor,
            now=started_at,
            lease_seconds=30,
        )
    except ProcessInterrupted:
        pass
    else:
        raise AssertionError("测试必须模拟进程中断")

    during_lease = governance.execute_validation(
        owner,
        run.run_id,
        worker_id="worker-b",
        executor=executor,
        now=started_at + timedelta(seconds=10),
        lease_seconds=30,
    )
    assert during_lease.status.value == "running"
    assert executor.calls.count(ValidationStep.SYNTHETIC_SMOKE) == 1

    recovered = governance.execute_validation(
        owner,
        run.run_id,
        worker_id="worker-b",
        executor=executor,
        now=started_at + timedelta(seconds=31),
        lease_seconds=30,
    )

    assert recovered.status.value == "succeeded"
    assert executor.calls.count(ValidationStep.SYNTHETIC_SMOKE) == 1
    assert [item.step for item in recovered.evidence] == list(ValidationStep)


def test_cancelled_validation_only_runs_cleanup_and_remains_draft() -> None:
    class RecordingExecutor:
        def __init__(self) -> None:
            self.calls: list[ValidationStep] = []

        def execute(self, run, step: ValidationStep) -> ValidationEvidence:
            self.calls.append(step)
            return ValidationEvidence(
                step=step,
                status=ValidationStepStatus.PASSED,
                evidence_ref=f"evidence://validation/{run.run_id}/{step.value}",
                evidence_sha256="e" * 64,
                summary="资源清理已通过",
            )

    catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
    repository = InMemoryCapabilityGovernanceRepository()
    governance = CapabilityGovernance(catalog, repository)
    owner = CatalogActor(owner_id="owner-a", role="user")
    other = CatalogActor(owner_id="owner-b", role="user")
    pack = _personal_pack("1.0.0", "a")
    catalog.register_pack(owner, pack)
    run = governance.request_validation(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
        task_ref=ValidationTaskRef(
            task_id="workspace-cancel",
            revision=1,
            source_snapshot_sha256="b" * 64,
            input_sha256="c" * 64,
            output_sha256="d" * 64,
            capability_digest=pack.digest,
            authorization_id="grant-cancel",
        ),
        idempotency_key="cancel-validation",
    )

    try:
        governance.cancel_validation(other, run.run_id)
    except PermissionError:
        pass
    else:
        raise AssertionError("跨 Owner 不得取消验证运行")
    governance.cancel_validation(owner, run.run_id)
    executor = RecordingExecutor()
    cancelled = governance.execute_validation(
        owner,
        run.run_id,
        worker_id="worker-cancel",
        executor=executor,
    )

    assert cancelled.status.value == "cancelled"
    assert executor.calls == [ValidationStep.CLEANUP]
    assert governance.list_visible_projections(owner)[0].maturity.value == "draft"


def test_sqlite_lease_merges_workers_and_recovers_completed_steps(tmp_path) -> None:
    from datetime import datetime, timedelta, timezone

    class ProcessInterrupted(BaseException):
        pass

    class Executor:
        def __init__(self) -> None:
            self.calls: list[ValidationStep] = []
            self.interrupt_once = True

        def execute(self, run, step: ValidationStep) -> ValidationEvidence:
            self.calls.append(step)
            if step is ValidationStep.OWNER_TASK_REPLAY and self.interrupt_once:
                self.interrupt_once = False
                raise ProcessInterrupted()
            return ValidationEvidence(
                step=step,
                status=ValidationStepStatus.PASSED,
                evidence_ref=f"evidence://validation/{run.run_id}/{step.value}",
                evidence_sha256="e" * 64,
                summary=f"{step.value} 已通过",
            )

    db_path = tmp_path / "webui.db"
    catalog = CapabilityCatalog(SqliteCapabilityCatalogRepository(str(db_path)))
    owner = CatalogActor(owner_id="owner-a", role="user")
    pack = _personal_pack("1.0.0", "a")
    catalog.register_pack(owner, pack)
    migrate_capability_governance(db_path, tmp_path / "before-run.db")
    first = CapabilityGovernance(
        catalog,
        SqliteCapabilityGovernanceRepository(str(db_path)),
    )
    run = first.request_validation(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
        task_ref=ValidationTaskRef(
            task_id="workspace-sqlite-resume",
            revision=1,
            source_snapshot_sha256="b" * 64,
            input_sha256="c" * 64,
            output_sha256="d" * 64,
            capability_digest=pack.digest,
            authorization_id="grant-sqlite-resume",
        ),
        idempotency_key="sqlite-resume",
    )
    executor = Executor()
    started = datetime(2026, 8, 7, tzinfo=timezone.utc)
    try:
        first.execute_validation(
            owner,
            run.run_id,
            worker_id="worker-a",
            executor=executor,
            now=started,
            lease_seconds=30,
        )
    except ProcessInterrupted:
        pass

    reopened = CapabilityGovernance(
        CapabilityCatalog(SqliteCapabilityCatalogRepository(str(db_path))),
        SqliteCapabilityGovernanceRepository(str(db_path)),
    )
    blocked = reopened.execute_validation(
        owner,
        run.run_id,
        worker_id="worker-b",
        executor=executor,
        now=started + timedelta(seconds=10),
        lease_seconds=30,
    )
    recovered = reopened.execute_validation(
        owner,
        run.run_id,
        worker_id="worker-b",
        executor=executor,
        now=started + timedelta(seconds=31),
        lease_seconds=30,
    )

    assert blocked.status.value == "running"
    assert recovered.status.value == "succeeded"
    assert executor.calls.count(ValidationStep.SYNTHETIC_SMOKE) == 1


def test_cleanup_failure_keeps_run_recoverable_until_resources_are_clean() -> None:
    from datetime import datetime, timedelta, timezone

    class CleanupRecovers:
        def __init__(self) -> None:
            self.cleanup_calls = 0

        def execute(self, run, step: ValidationStep) -> ValidationEvidence:
            if step is ValidationStep.CLEANUP:
                self.cleanup_calls += 1
                if self.cleanup_calls == 1:
                    raise RuntimeError("Docker 暂时拒绝删除资源")
            return ValidationEvidence(
                step=step,
                status=ValidationStepStatus.PASSED,
                evidence_ref=f"evidence://validation/{run.run_id}/{step.value}",
                evidence_sha256="e" * 64,
                summary=f"{step.value} 已通过",
            )

    catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
    governance = CapabilityGovernance(
        catalog,
        InMemoryCapabilityGovernanceRepository(),
    )
    owner = CatalogActor(owner_id="owner-a", role="user")
    pack = _personal_pack("1.0.0", "a")
    catalog.register_pack(owner, pack)
    run = governance.request_validation(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
        task_ref=ValidationTaskRef(
            task_id="workspace-cleanup-recovery",
            revision=1,
            source_snapshot_sha256="b" * 64,
            input_sha256="c" * 64,
            output_sha256="d" * 64,
            capability_digest=pack.digest,
            authorization_id="grant-cleanup-recovery",
        ),
        idempotency_key="cleanup-recovery",
    )
    executor = CleanupRecovers()
    started = datetime(2026, 8, 7, tzinfo=timezone.utc)

    try:
        governance.execute_validation(
            owner,
            run.run_id,
            worker_id="worker-a",
            executor=executor,
            now=started,
            lease_seconds=30,
        )
    except RuntimeError as exc:
        assert "等待恢复重试" in str(exc)
    else:
        raise AssertionError("清理失败时不得写成终态")

    interrupted = governance.get_validation(owner, run.run_id)
    assert interrupted.status is ValidationRunStatus.RUNNING
    assert ValidationStep.CLEANUP not in {item.step for item in interrupted.evidence}
    blocked = governance.execute_validation(
        owner,
        run.run_id,
        worker_id="worker-b",
        executor=executor,
        now=started + timedelta(seconds=10),
        lease_seconds=30,
    )
    recovered = governance.execute_validation(
        owner,
        run.run_id,
        worker_id="worker-b",
        executor=executor,
        now=started + timedelta(seconds=31),
        lease_seconds=30,
    )

    assert blocked.status is ValidationRunStatus.RUNNING
    assert recovered.status is ValidationRunStatus.SUCCEEDED
    assert executor.cleanup_calls == 2


def test_real_task_ref_is_recomputed_from_owner_revision_and_formal_output(
    tmp_path,
) -> None:
    import sqlite3

    from src.agentic_runtime.repository import AgenticRuntimeRepository
    from src.api.store import WebUIStore
    from src.services.upload_store import UploadStore

    db_path = tmp_path / "webui.db"
    store = WebUIStore(str(db_path))
    AgenticRuntimeRepository(db_path)
    upload_root = tmp_path / "uploads"
    upload = UploadStore(str(upload_root), max_bytes=1024).save_bytes(
        "owner-a",
        "source.csv",
        b"name,amount\nA,1\n",
    )
    output_path = tmp_path / "result.csv"
    output_path.write_bytes(b"name,total\nA,1\n")
    output_hash = __import__("hashlib").sha256(output_path.read_bytes()).hexdigest()
    owner = CatalogActor(owner_id="owner-a", role="user")
    catalog = CapabilityCatalog(SqliteCapabilityCatalogRepository(str(db_path)))
    pack = _personal_pack("1.0.0", "a")
    catalog.register_pack(owner, pack)
    capability_mount = tmp_path / "capability-mount"
    capability_mount.mkdir()
    (capability_mount / ".mangrove-capability-digest").write_text(
        pack.digest,
        encoding="utf-8",
    )
    (capability_mount / "mangrove-capability.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "python-table-summary",
                "version": "1.0.0",
                "kind": "python",
                "purpose": "汇总表格",
                "entrypoint": {
                    "program": "python",
                    "arguments": ["table_summary.py"],
                    "working_directory": ".",
                    "environment": [],
                    "timeout_seconds": 30,
                },
                "permissions": ["process:child", "network:none"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store.create_semantic_workspace_task(
        "owner-a",
        task_id="workspace-real-ref",
        title="真实表格任务",
        objective_text="汇总表格",
        upload_ids=[upload.upload_id],
        source_refs=[{"kind": "upload", "ref": upload.upload_id}],
        output_formats=["csv"],
        provider="local",
        model=None,
        external_api_confirmed=False,
    )
    catalog.freeze_selection(
        owner,
        task_id="workspace-real-ref",
        revision=1,
        pack_refs=(
            CapabilityPackRef(
                pack_id=pack.pack_id,
                version=pack.version,
                digest=pack.digest,
            ),
        ),
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE semantic_workspace_tasks SET status='completed', run_id='run-real' "
            "WHERE task_id='workspace-real-ref'"
        )
        connection.execute(
            "UPDATE semantic_workspace_revisions SET status='completed', run_id='run-real' "
            "WHERE task_id='workspace-real-ref' AND revision=1"
        )
        connection.execute(
            "INSERT INTO semantic_harness_attempts "
            "(attempt_id, run_id, user_id, node, attempt_number, idempotency_key, "
            "input_hash, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "attempt-real",
                "run-real",
                "owner-a",
                "execute",
                1,
                "attempt-real-key",
                "b" * 64,
                "succeeded",
                "2026-08-07T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO semantic_delivery_outputs "
            "(output_id, delivery_id, run_id, user_id, format, filename, "
            "media_type, sha256, size_bytes, file_path, qa_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "output-real",
                "delivery-real",
                "run-real",
                "owner-a",
                "csv",
                "result.csv",
                "text/csv",
                output_hash,
                output_path.stat().st_size,
                str(output_path),
                "{}",
                "2026-08-07T00:00:00+00:00",
            ),
        )
    migrate_capability_governance(db_path, tmp_path / "before-real-ref.db")
    task_resolver = SqliteValidationTaskResolver(
        str(db_path),
        upload_root=upload_root,
        execution_root=tmp_path,
        capability_mounts=lambda *_args: (capability_mount,),
    )
    governance = CapabilityGovernance(
        catalog,
        SqliteCapabilityGovernanceRepository(str(db_path)),
        task_resolver=task_resolver,
    )

    # 仅冻结但从未成功调用目标能力的任务不能进入真实重放，避免无效运行消耗 Token。
    assert governance.list_validation_task_options(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
    ) == ()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO agentic_runtime_events "
            "(event_id, user_id, task_id, revision, event_type, summary, "
            "details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "event-run-real",
                "owner-a",
                "workspace-real-ref",
                1,
                "runtime.preparing",
                "开始运行",
                '{"run_id":"run-real"}',
                "2026-08-07T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO agentic_runtime_events "
            "(event_id, user_id, task_id, revision, event_type, summary, "
            "details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "event-tool-real",
                "owner-a",
                "workspace-real-ref",
                1,
                "tool.completed",
                "能力调用完成",
                '{"tool":"capability_python_table_summary","failed":false}',
                "2026-08-07T00:00:01+00:00",
            ),
        )

    run = governance.request_validation_for_task(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
        task_id="workspace-real-ref",
        revision=1,
        idempotency_key="real-ref",
    )

    assert run.task_ref.input_sha256 == "b" * 64
    assert run.task_ref.authorization_id.startswith("selection_")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO agentic_runtime_runs "
            "(user_id, task_id, revision, runtime_version, permission_profile, "
            "model_connection_id, external_api_confirmed, status, run_id, request_json, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "owner-a",
                "workspace-real-ref",
                1,
                "pi",
                "standard",
                "connection-a",
                0,
                "completed",
                "run-real",
                '{"model_connection_id":"connection-a"}',
                "2026-08-07T00:00:00+00:00",
                "2026-08-07T00:00:00+00:00",
            ),
        )
    assert governance.list_validation_task_options(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
    ) == ()
    try:
        governance.request_validation_for_task(
            owner,
            pack_ref=CapabilityPackRef(
                pack_id=pack.pack_id,
                version=pack.version,
                digest=pack.digest,
            ),
            task_id="workspace-real-ref",
            revision=1,
            idempotency_key="real-ref-unconfirmed-external",
        )
    except PermissionError as exc:
        assert "未确认" in str(exc)
    else:
        raise AssertionError("存在 attempt hash 时也必须拒绝未经确认的外部模型重放")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "DELETE FROM agentic_runtime_runs "
            "WHERE user_id=? AND task_id=? AND revision=?",
            ("owner-a", "workspace-real-ref", 1),
        )
        connection.execute(
            "UPDATE semantic_delivery_outputs SET sha256=? WHERE output_id=?",
            ("d" * 64, "output-real"),
        )
    try:
        governance.request_validation(
            owner,
            pack_ref=CapabilityPackRef(
                pack_id=pack.pack_id,
                version=pack.version,
                digest=pack.digest,
            ),
            task_ref=run.task_ref,
            idempotency_key="real-ref-recheck",
        )
    except ValueError as exc:
        assert "已变化" in str(exc)
    else:
        raise AssertionError("输出 hash 改变后必须失败关闭")


def test_step_failure_is_redacted_failed_closed_and_cleanup_still_runs() -> None:
    class FailingExecutor:
        def __init__(self) -> None:
            self.calls: list[ValidationStep] = []

        def execute(self, run, step: ValidationStep) -> ValidationEvidence:
            self.calls.append(step)
            if step is ValidationStep.OWNER_TASK_REPLAY:
                raise RuntimeError("C:\\private\\客户名单.xlsx token=secret")
            return ValidationEvidence(
                step=step,
                status=ValidationStepStatus.PASSED,
                evidence_ref=f"evidence://validation/{run.run_id}/{step.value}",
                evidence_sha256="e" * 64,
                summary="调用方提供的任意摘要",
            )

    catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
    governance = CapabilityGovernance(
        catalog,
        InMemoryCapabilityGovernanceRepository(),
    )
    owner = CatalogActor(owner_id="owner-a", role="user")
    pack = _personal_pack("1.0.0", "a")
    catalog.register_pack(owner, pack)
    run = governance.request_validation(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
        task_ref=ValidationTaskRef(
            task_id="workspace-fail",
            revision=1,
            source_snapshot_sha256="b" * 64,
            input_sha256="c" * 64,
            output_sha256="d" * 64,
            capability_digest=pack.digest,
            authorization_id="grant-fail",
        ),
        idempotency_key="failed-validation",
    )
    executor = FailingExecutor()

    failed = governance.execute_validation(
        owner,
        run.run_id,
        worker_id="worker-fail",
        executor=executor,
    )

    assert failed.status.value == "failed"
    assert executor.calls == [
        ValidationStep.SYNTHETIC_SMOKE,
        ValidationStep.OWNER_TASK_REPLAY,
        ValidationStep.CLEANUP,
    ]
    payload = failed.model_dump_json()
    assert "客户名单" not in payload
    assert "token=secret" not in payload
    assert all(item.summary != "调用方提供的任意摘要" for item in failed.evidence)


def test_old_migration_backup_cannot_mask_missing_validation_schema(tmp_path) -> None:
    import sqlite3

    db_path = tmp_path / "old-governance.db"
    backup = tmp_path / "old-governance-backup.db"
    ddl = (
        Path("src/capability_governance/migrations/0001_capability_governance.sql")
        .read_text(encoding="utf-8")
    )
    with sqlite3.connect(db_path) as connection:
        connection.executescript(ddl)
    with sqlite3.connect(backup):
        pass

    try:
        migrate_capability_governance(db_path, backup)
    except FileExistsError:
        pass
    else:
        raise AssertionError("旧备份不能让缺少 #34 表的迁移被误判为完成")


def test_executor_reference_is_replaced_by_current_run_namespace() -> None:
    class Executor:
        def execute(self, run, step):
            return ValidationEvidence(
                step=step,
                status=ValidationStepStatus.PASSED,
                evidence_ref="evidence://private/customer-file-name",
                evidence_sha256="e" * 64,
                summary="客户文件和 token",
            )

    catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
    governance = CapabilityGovernance(
        catalog,
        InMemoryCapabilityGovernanceRepository(),
    )
    owner = CatalogActor(owner_id="owner-a", role="user")
    pack = _personal_pack("1.0.0", "a")
    catalog.register_pack(owner, pack)
    run = governance.request_validation(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
        task_ref=ValidationTaskRef(
            task_id="workspace-controlled-ref",
            revision=1,
            source_snapshot_sha256="b" * 64,
            input_sha256="c" * 64,
            output_sha256="d" * 64,
            capability_digest=pack.digest,
            authorization_id="selection-controlled-ref",
        ),
        idempotency_key="controlled-ref",
    )

    completed = governance.execute_validation(
        owner,
        run.run_id,
        worker_id="worker-controlled-ref",
        executor=Executor(),
    )

    assert completed.status is ValidationRunStatus.SUCCEEDED
    assert all(
        item.evidence_ref
        == f"evidence://validation/{run.run_id}/{item.step.value}"
        for item in completed.evidence
    )
    assert "customer-file-name" not in completed.model_dump_json()


def test_cancelled_executor_result_cannot_finish_as_succeeded() -> None:
    class Executor:
        def execute(self, run, step):
            return ValidationEvidence(
                step=step,
                status=(
                    ValidationStepStatus.CANCELLED
                    if step is ValidationStep.OWNER_TASK_REPLAY
                    else ValidationStepStatus.PASSED
                ),
                evidence_ref=f"evidence://validation/{run.run_id}/{step.value}",
                evidence_sha256="e" * 64,
                summary="受控结果",
            )

    catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
    governance = CapabilityGovernance(catalog, InMemoryCapabilityGovernanceRepository())
    owner = CatalogActor(owner_id="owner-a", role="user")
    pack = _personal_pack("1.0.0", "a")
    catalog.register_pack(owner, pack)
    run = governance.request_validation(
        owner,
        pack_ref=CapabilityPackRef(pack_id=pack.pack_id, version=pack.version, digest=pack.digest),
        task_ref=ValidationTaskRef(
            task_id="workspace-cancelled-result",
            revision=1,
            source_snapshot_sha256="b" * 64,
            input_sha256="c" * 64,
            output_sha256="d" * 64,
            capability_digest=pack.digest,
            authorization_id="selection-cancelled-result",
        ),
        idempotency_key="cancelled-result",
    )

    completed = governance.execute_validation(
        owner,
        run.run_id,
        worker_id="worker-cancelled-result",
        executor=Executor(),
    )

    assert completed.status is ValidationRunStatus.CANCELLED
    assert completed.evidence[-1].step is ValidationStep.CLEANUP


def test_validation_manager_executes_queued_run_and_rechecks_task(tmp_path) -> None:
    import asyncio

    class Resolver:
        def __init__(self, task_ref):
            self.task_ref = task_ref
            self.verify_calls = 0

        def verify(self, actor, target, task_ref):
            self.verify_calls += 1
            if actor.owner_id != target.owner_id or task_ref != self.task_ref:
                raise PermissionError("冻结任务证据无效")
            return task_ref

    class Executor:
        def execute(self, run, step):
            return ValidationEvidence(
                step=step,
                status=ValidationStepStatus.PASSED,
                evidence_ref=f"evidence://validation/{run.run_id}/{step.value}",
                evidence_sha256="e" * 64,
                summary="受控结果",
            )
    catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
    repository = InMemoryCapabilityGovernanceRepository()
    owner = CatalogActor(owner_id="owner-a", role="user")
    pack = _personal_pack("1.0.0", "a")
    catalog.register_pack(owner, pack)
    task_ref = ValidationTaskRef(
        task_id="workspace-manager",
        revision=1,
        source_snapshot_sha256="b" * 64,
        input_sha256="c" * 64,
        output_sha256="d" * 64,
        capability_digest=pack.digest,
        authorization_id="selection-manager",
    )
    resolver = Resolver(task_ref)
    governance = CapabilityGovernance(catalog, repository, task_resolver=resolver)
    run = governance.request_validation(
        owner,
        pack_ref=CapabilityPackRef(pack_id=pack.pack_id, version=pack.version, digest=pack.digest),
        task_ref=task_ref,
        idempotency_key="manager-run",
    )
    manager = CapabilityValidationManager(
        governance,
        lambda _run: Executor(),
    )

    assert asyncio.run(manager.run_once()) == 1
    completed = governance.get_validation(owner, run.run_id)
    assert completed.status is ValidationRunStatus.SUCCEEDED
    assert [item.step for item in completed.evidence] == list(ValidationStep)
    assert resolver.verify_calls >= 5


def test_production_validation_executor_runs_host_denial_verifier_and_cleanup(
    tmp_path,
    monkeypatch,
) -> None:
    import asyncio
    import json
    import subprocess

    from src.capability_governance import CapabilityGovernanceTarget, CapabilityValidationRun
    from src.capability_host import CapabilityHostLease

    mount = tmp_path / "mount"
    mount.mkdir()
    (mount / "mangrove-capability.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "python-table-summary",
                "version": "1.0.0",
                "kind": "python",
                "purpose": "表格汇总",
                "entrypoint": {"program": "python", "arguments": ["tool.py"]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class Resolver:
        def verify(self, actor, target, task_ref):
            if actor.owner_id != target.owner_id:
                raise PermissionError("跨 Owner")
            if task_ref.output_sha256 == "0" * 64:
                raise ValueError("输出 hash 被篡改")
            return task_ref

        def verify_independent_verifier(self, actor, target, task_ref):
            self.verify(actor, target, task_ref)
            return "f" * 64

    class Host:
        def __init__(self):
            self.started = 0
            self.stopped = 0

        async def start(self, request):
            self.started += 1
            runtime_dir = tmp_path / "runtime" / "active"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            return CapabilityHostLease(
                container_name="capval-host",
                relay_url="http://capval-host:8765",
                relay_token="controlled-token",
                capability_names=("python-table-summary",),
                capability_kinds=(("python-table-summary", "python"),),
                runtime_dir=runtime_dir,
            )

        async def stop(self, lease):
            self.stopped += 1
            import shutil
            shutil.rmtree(lease.runtime_dir, ignore_errors=True)

    target = CapabilityGovernanceTarget(
        owner_id="owner-a",
        scope=ProcedureScope.PERSONAL,
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
    )
    (mount / ".mangrove-capability-digest").write_text(
        target.digest,
        encoding="utf-8",
    )
    run = CapabilityValidationRun(
        owner_id="owner-a",
        target=target,
        actor_id="owner-a",
        actor_role="user",
        idempotency_key="runtime-executor",
        task_ref=ValidationTaskRef(
            task_id="workspace-runtime",
            revision=1,
            source_snapshot_sha256="b" * 64,
            input_sha256="c" * 64,
            output_sha256="d" * 64,
            capability_digest=target.digest,
            authorization_id="selection-runtime",
        ),
    )
    host = Host()
    executor = TaskEvidenceValidationExecutor(
        task_resolver=Resolver(),
        capability_mounts=lambda *_args: (mount,),
        capability_host=host,
        execution_root=tmp_path / "runtime",
        task_replay=lambda _run: {
            "cancelled": False,
            "candidate_hashes": ["e" * 64],
            "verification": {"status": "passed"},
        },
    )

    def docker(*arguments, check=False):
        returncode = 1 if arguments[:2] in {
            ("container", "inspect"),
            ("network", "inspect"),
        } else 0
        stderr = (
            "No such object"
            if arguments[:2] == ("container", "inspect")
            else "No such network"
            if arguments[:2] == ("network", "inspect")
            else ""
        )
        return subprocess.CompletedProcess(arguments, returncode, "", stderr)

    monkeypatch.setattr(executor, "_docker", docker)
    evidence = []
    current = run
    for step in ValidationStep:
        item = executor.execute(current, step)
        evidence.append(item)
        current = current.model_copy(update={"evidence": (*current.evidence, item)})

    assert all(item.status is ValidationStepStatus.PASSED for item in evidence)
    assert host.started == 1
    assert host.stopped >= 2
    assert not (tmp_path / "runtime" / "active").exists()


def test_production_cleanup_does_not_treat_docker_daemon_failure_as_absent(
    tmp_path,
    monkeypatch,
) -> None:
    import subprocess

    from src.capability_governance import CapabilityGovernanceTarget, CapabilityValidationRun

    class Host:
        async def stop(self, _lease):
            return None

    target = CapabilityGovernanceTarget(
        owner_id="owner-a",
        scope=ProcedureScope.PERSONAL,
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
    )
    run = CapabilityValidationRun(
        owner_id="owner-a",
        target=target,
        actor_id="owner-a",
        actor_role="user",
        idempotency_key="runtime-cleanup-daemon-failure",
        task_ref=ValidationTaskRef(
            task_id="workspace-runtime-cleanup",
            revision=1,
            source_snapshot_sha256="b" * 64,
            input_sha256="c" * 64,
            output_sha256="d" * 64,
            capability_digest=target.digest,
            authorization_id="selection-runtime-cleanup",
        ),
    )
    executor = TaskEvidenceValidationExecutor(
        task_resolver=object(),
        capability_mounts=lambda *_args: (),
        capability_host=Host(),
        execution_root=tmp_path / "runtime",
        task_replay=lambda _run: {},
    )

    monkeypatch.setattr(
        executor,
        "_docker",
        lambda *arguments, **_kwargs: subprocess.CompletedProcess(
            arguments,
            1,
            "",
            "Cannot connect to the Docker daemon",
        ),
    )

    try:
        executor.execute(run, ValidationStep.CLEANUP)
    except RuntimeError as exc:
        assert "清理不完整" in str(exc)
    else:
        raise AssertionError("Docker daemon 失败不得被误判为资源不存在")


def test_pi_task_replay_reruns_frozen_request_and_removes_all_temporary_state(
    tmp_path,
    monkeypatch,
) -> None:
    from src.agentic_runtime.models import (
        CandidateArtifact,
        PiRuntimeRequest,
        PiRuntimeResult,
        RuntimeStatus,
        RuntimeEvent,
        SourceInput,
        VerificationCheck,
        VerificationReport,
        VerificationStatus,
    )
    from src.capability_governance import (
        CapabilityGovernanceTarget,
        CapabilityValidationRun,
        PiTaskReplayRunner,
    )
    import src.capability_governance.validation_runtime as runtime_module

    source = tmp_path / "source.csv"
    source.write_text("name,total\nA,1\n", encoding="utf-8")
    candidate = tmp_path / "candidate.csv"
    candidate.write_text("name,total\nA,1\n", encoding="utf-8")
    request = PiRuntimeRequest(
        user_id="owner-a",
        task_id="workspace-replay",
        revision=2,
        objective_text="汇总表格",
        requested_output_formats=("csv",),
        sources=(
            SourceInput(
                upload_id="upload-a",
                original_name="source.csv",
                host_path=source,
                sha256=__import__("hashlib").sha256(source.read_bytes()).hexdigest(),
                media_type="text/csv",
            ),
        ),
        model="local-model",
        base_url="http://127.0.0.1:8000/v1",
        api_key="local-runtime",
    )
    verification = VerificationReport(
        status=VerificationStatus.PASSED,
        summary="通过",
        checks=(VerificationCheck(code="semantic", passed=True, summary="通过"),),
        evidence_count=1,
    )

    class FakeRuntime:
        started_request = None

        def __init__(self, **_kwargs):
            pass

        async def start(self, replay_request, *, on_event, run_id=None):
            FakeRuntime.started_request = replay_request
            assert run_id and run_id.startswith("pi_validation_")
            await on_event(
                RuntimeEvent(
                    event_type="tool.completed",
                    summary="能力已完成",
                    details={"tool": "capability_python_table_summary"},
                )
            )
            return PiRuntimeResult(
                status=RuntimeStatus.CANDIDATE_READY,
                run_id="pi-replay",
                workspace_root=tmp_path,
                candidates=(
                    CandidateArtifact(
                        artifact_id="candidate-a",
                        filename="candidate.csv",
                        format="csv",
                        host_path=candidate,
                        sha256=__import__("hashlib").sha256(candidate.read_bytes()).hexdigest(),
                        size_bytes=candidate.stat().st_size,
                        openable=True,
                    ),
                ),
                verification=verification,
            )

        async def cancel(self, *_args):
            raise AssertionError("本用例不应取消")

    class Resolver:
        def load_replay_request(self, actor, target, task_ref):
            assert actor.owner_id == target.owner_id == "owner-a"
            assert task_ref.task_id == request.task_id
            return request

    monkeypatch.setattr(runtime_module, "PiRuntime", FakeRuntime)
    mount = tmp_path / "mount"
    mount.mkdir()
    (mount / "mangrove-capability.json").write_text(
        __import__("json").dumps(
            {
                "schema_version": 1,
                "name": "python-table-summary",
                "version": "1.0.0",
                "kind": "python",
                "purpose": "表格汇总",
                "entrypoint": {"program": "python", "arguments": ["tool.py"]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    target = CapabilityGovernanceTarget(
        owner_id="owner-a",
        scope=ProcedureScope.PERSONAL,
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
    )
    (mount / ".mangrove-capability-digest").write_text(
        target.digest,
        encoding="utf-8",
    )
    run = CapabilityValidationRun(
        owner_id="owner-a",
        target=target,
        actor_id="owner-a",
        actor_role="user",
        idempotency_key="pi-replay",
        task_ref=ValidationTaskRef(
            task_id=request.task_id,
            revision=request.revision,
            source_snapshot_sha256="b" * 64,
            input_sha256="c" * 64,
            output_sha256="d" * 64,
            capability_digest=target.digest,
            authorization_id="selection-replay",
        ),
    )
    root = tmp_path / "validation-root"
    outcome = PiTaskReplayRunner(
        task_resolver=Resolver(),
        capability_mounts=lambda *_args: (mount,),
        execution_root=root,
        cancel_requested=lambda: False,
        cleanup_runner=lambda *args: __import__("subprocess").CompletedProcess(
            args,
            1,
            "",
            "No such container" if args[0] == "container" else "not found",
        ),
        grant_revoker=lambda *_args: 0,
    )(run)

    assert outcome["cancelled"] is False
    assert outcome["candidate_hashes"]
    assert FakeRuntime.started_request.objective_text == request.objective_text
    assert FakeRuntime.started_request.task_id == request.task_id
    assert not any((root / "task-replays").iterdir())
