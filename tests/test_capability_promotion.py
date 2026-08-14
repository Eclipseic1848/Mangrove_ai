# -*- coding: utf-8 -*-
"""AC-07-05：从 CapabilityGovernance 公共 Interface 验证能力晋级与缺口投影。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

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
    CapabilityValidationManager,
    CapabilityValidationRun,
    InMemoryCapabilityGovernanceRepository,
    PromotionGap,
    PromotionOutcome,
    SqliteCapabilityGovernanceRepository,
    SupplyChainEvidenceStatus,
    TrivyDatabaseMetadata,
    ValidationEvidence,
    ValidationRunStatus,
    ValidationStep,
    ValidationStepStatus,
    ValidationTaskRef,
    migrate_capability_governance,
)
from src.conversation_steering import ProcedureScope


def _target(
    digest_char: str = "a",
    *,
    pack_id: str = "python-table-summary",
    version: str = "1.0.0",
) -> CapabilityGovernanceTarget:
    return CapabilityGovernanceTarget(
        owner_id="owner-a",
        scope=ProcedureScope.PERSONAL,
        pack_id=pack_id,
        version=version,
        digest="sha256:" + digest_char * 64,
    )


def _promoted_event(
    target: CapabilityGovernanceTarget,
    **overrides: object,
) -> CapabilityGovernanceEvent:
    fields: dict[str, object] = {
        "event_type": "promoted_to_verified",
        "maturity": CapabilityMaturity.VERIFIED,
        "idempotency_key": "promotion:run-a",
        "actor_id": "owner-a",
        "actor_role": "user",
        "source_validation_run_id": "capval_a1b2c3d4e5f6a1b2c3d4",
        "source_supply_chain_evidence_id": "supply_" + "a" * 20,
    }
    fields.update(overrides)
    return CapabilityGovernanceEvent(target=target, **fields)


def _task_ref(target: CapabilityGovernanceTarget) -> ValidationTaskRef:
    return ValidationTaskRef(
        task_id="workspace-validated-source",
        revision=2,
        source_snapshot_sha256="b" * 64,
        input_sha256="c" * 64,
        output_sha256="d" * 64,
        capability_digest=target.digest,
        authorization_id="grant-owner-a-task-replay",
    )


def _run(
    target: CapabilityGovernanceTarget,
    *,
    run_id: str,
    status: ValidationRunStatus,
) -> CapabilityValidationRun:
    return CapabilityValidationRun(
        run_id=run_id,
        owner_id="owner-a",
        target=target,
        actor_id="owner-a",
        actor_role="user",
        idempotency_key=f"validate-{run_id}",
        task_ref=_task_ref(target),
        status=status,
    )


def _passed_evidence(step: ValidationStep, run_id: str) -> ValidationEvidence:
    return ValidationEvidence(
        step=step,
        status=ValidationStepStatus.PASSED,
        evidence_ref=f"evidence://validation/{run_id}/{step.value}",
        evidence_sha256="a" * 64,
        summary="验证步骤已通过",
    )


def _succeeded_run(
    target: CapabilityGovernanceTarget,
    *,
    run_id: str,
    complete: bool = True,
) -> CapabilityValidationRun:
    run = _run(target, run_id=run_id, status=ValidationRunStatus.SUCCEEDED)
    if complete:
        steps = tuple(
            _passed_evidence(step, run_id) for step in ValidationStep
        )
        return run.model_copy(update={"evidence": steps})
    # 缺一步证据：模拟库中记录与成功状态不一致。
    steps = tuple(
        _passed_evidence(step, run_id)
        for step in ValidationStep
        if step.value != "cleanup"
    )
    return run.model_copy(update={"evidence": steps})


def _supply_evidence(
    target: CapabilityGovernanceTarget,
    *,
    passed: bool = True,
    blockers: tuple[str, ...] = (),
    updated_at_days_ago: int = 0,
    evidence_char: str = "b",
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


def _governance(
    repository: InMemoryCapabilityGovernanceRepository,
) -> CapabilityGovernance:
    return CapabilityGovernance(
        CapabilityCatalog(InMemoryCapabilityCatalogRepository()),
        repository,
    )


# ---------- S1：事件模型与迁移门 ----------


def test_promoted_event_accepts_only_verified_active_eligible() -> None:
    target = _target()
    event = _promoted_event(target)
    assert event.maturity is CapabilityMaturity.VERIFIED
    assert event.lifecycle is CapabilityLifecycle.ACTIVE
    assert event.eligibility is CapabilityEligibility.ELIGIBLE

    illegal = (
        {"maturity": CapabilityMaturity.DRAFT},
        {"lifecycle": CapabilityLifecycle.DEPRECATED},
        {"eligibility": CapabilityEligibility.QUARANTINED},
        {"source_validation_run_id": None},
        {"source_supply_chain_evidence_id": None},
    )
    for update in illegal:
        with pytest.raises(ValueError):
            _promoted_event(target, **update)


def test_registered_event_cannot_carry_promotion_sources() -> None:
    with pytest.raises(ValueError):
        CapabilityGovernanceEvent(
            target=_target(),
            idempotency_key="register-with-source",
            actor_id="owner-a",
            actor_role="user",
            source_validation_run_id="capval_x",
        )


def test_promotion_outcome_shapes() -> None:
    target = _target()
    event = _promoted_event(target)
    # held 必须携带缺口且不携带事件。
    held = PromotionOutcome(status="held", gaps=(PromotionGap.VALIDATION_INCOMPLETE,))
    assert held.event is None
    # promoted / already_verified 必须携带事件且无缺口。
    assert PromotionOutcome(status="promoted", gaps=(), event=event).event == event
    assert (
        PromotionOutcome(status="already_verified", gaps=(), event=event).event
        == event
    )
    with pytest.raises(ValueError):
        PromotionOutcome(status="held", gaps=())
    with pytest.raises(ValueError):
        PromotionOutcome(status="promoted", gaps=(), event=None)
    with pytest.raises(ValueError):
        PromotionOutcome(
            status="promoted",
            gaps=(PromotionGap.VALIDATION_INCOMPLETE,),
            event=event,
        )


def test_generic_save_event_rejects_promotion_events(tmp_path) -> None:
    db_path = tmp_path / "webui.db"
    migrate_capability_governance(db_path, tmp_path / "backup.db")
    sqlite_repository = SqliteCapabilityGovernanceRepository(str(db_path))
    with pytest.raises(ValueError):
        sqlite_repository.save_event(_promoted_event(_target()))
    memory_repository = InMemoryCapabilityGovernanceRepository()
    with pytest.raises(ValueError):
        memory_repository.save_event(_promoted_event(_target()))


def test_sqlite_migration_installs_promotion_gate_and_replays(tmp_path) -> None:
    db_path = tmp_path / "webui.db"
    migrate_capability_governance(db_path, tmp_path / "backup-a.db")
    # 升级场景：新备份路径重放全部迁移不得失败。
    migrate_capability_governance(db_path, tmp_path / "backup-b.db")

    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(capability_governance_events)"
            )
        }
        indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(capability_governance_events)"
            )
        }
    assert "event_type" in columns
    assert "idx_capability_governance_single_promotion" in indexes


def test_sqlite_allows_only_one_promotion_event_per_digest(tmp_path) -> None:
    db_path = tmp_path / "webui.db"
    migrate_capability_governance(db_path, tmp_path / "backup.db")
    repository = SqliteCapabilityGovernanceRepository(str(db_path))
    target = _target()
    first = repository.save_promotion_event(
        _promoted_event(target, idempotency_key="promotion:run-a")
    )
    second = repository.save_promotion_event(
        _promoted_event(
            target,
            idempotency_key="promotion:run-b",
            event_id="capgov_second_promotion",
        )
    )
    assert second == first
    assert repository.get_latest_promotion_event(target) == first
    # 数据库层硬门：绕过服务层直接插入第二个晋级事件必须被部分唯一索引拒绝。
    with sqlite3.connect(db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO capability_governance_events "
                "(event_id, owner_key, scope, pack_id, version, digest, "
                "idempotency_key, event_type, payload_json, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "capgov_direct_insert",
                    "owner-a",
                    "personal",
                    target.pack_id,
                    target.version,
                    target.digest,
                    "promotion:direct",
                    "promoted_to_verified",
                    _promoted_event(
                        target, idempotency_key="promotion:direct"
                    ).model_dump_json(),
                    "2026-08-14T00:00:00+00:00",
                ),
            )


def test_inmemory_allows_only_one_promotion_event_per_digest() -> None:
    repository = InMemoryCapabilityGovernanceRepository()
    target = _target()
    first = repository.save_promotion_event(
        _promoted_event(target, idempotency_key="promotion:run-a")
    )
    second = repository.save_promotion_event(
        _promoted_event(
            target,
            idempotency_key="promotion:run-b",
            event_id="capgov_second_promotion",
        )
    )
    assert second == first
    assert repository.get_latest_promotion_event(target) == first


def test_latest_succeeded_validation_run_queries_by_target() -> None:
    repository = InMemoryCapabilityGovernanceRepository()
    target = _target()
    repository.create_validation_run(
        _run(target, run_id="capval_failed", status=ValidationRunStatus.FAILED)
    )
    repository.create_validation_run(
        _run(target, run_id="capval_succeeded", status=ValidationRunStatus.SUCCEEDED)
    )
    assert (
        repository.get_latest_succeeded_validation_run(target).run_id
        == "capval_succeeded"
    )
    other = _target("b")
    assert repository.get_latest_succeeded_validation_run(other) is None


def test_sqlite_latest_succeeded_validation_run_queries_by_target(
    tmp_path,
) -> None:
    db_path = tmp_path / "webui.db"
    migrate_capability_governance(db_path, tmp_path / "backup.db")
    repository = SqliteCapabilityGovernanceRepository(str(db_path))
    target = _target()
    repository.create_validation_run(
        _run(target, run_id="capval_failed", status=ValidationRunStatus.FAILED)
    )
    repository.create_validation_run(
        _run(target, run_id="capval_succeeded", status=ValidationRunStatus.SUCCEEDED)
    )
    assert (
        repository.get_latest_succeeded_validation_run(target).run_id
        == "capval_succeeded"
    )
    assert repository.get_latest_succeeded_validation_run(_target("b")) is None


# ---------- S2：晋级判定门 ----------


def test_evaluate_requires_complete_validation() -> None:
    repository = InMemoryCapabilityGovernanceRepository()
    governance = _governance(repository)
    target = _target()
    assert PromotionGap.VALIDATION_INCOMPLETE in governance.evaluate_promotion(
        target
    )
    # 只有失败运行不能晋级，也不能被当成成功证据。
    repository.create_validation_run(
        _run(target, run_id="capval_failed", status=ValidationRunStatus.FAILED)
    )
    assert PromotionGap.VALIDATION_INCOMPLETE in governance.evaluate_promotion(
        target
    )
    # 成功运行缺一步证据属于引用不一致。
    repository.create_validation_run(
        _succeeded_run(target, run_id="capval_incomplete", complete=False)
    )
    gaps = governance.evaluate_promotion(target)
    assert PromotionGap.VALIDATION_INCOMPLETE not in gaps
    assert PromotionGap.EVIDENCE_REFERENCE_MISMATCH in gaps
    # 五步各不相同且全 passed、但存在重复步骤（记录被改写）同样必须拒绝。
    duplicated = _succeeded_run(target, run_id="capval_duplicated")
    duplicated_evidence = (
        *_succeeded_run(target, run_id="capval_x").evidence,
        _passed_evidence(ValidationStep.SYNTHETIC_SMOKE, "capval_x"),
    )
    repository.create_validation_run(
        duplicated.model_copy(update={"evidence": duplicated_evidence})
    )
    assert (
        PromotionGap.EVIDENCE_REFERENCE_MISMATCH
        in governance.evaluate_promotion(target)
    )


def test_evaluate_requires_passed_supply_chain_evidence() -> None:
    repository = InMemoryCapabilityGovernanceRepository()
    governance = _governance(repository)
    target = _target()
    repository.create_validation_run(
        _succeeded_run(target, run_id="capval_complete")
    )
    # 无供应链证据不能晋级。
    assert (
        PromotionGap.SUPPLY_CHAIN_EVIDENCE_MISSING
        in governance.evaluate_promotion(target)
    )
    # 阻断证据按 blocker 逐项映射为缺口。
    repository.save_supply_chain_evidence(
        _supply_evidence(
            target,
            passed=False,
            blockers=("secret_detected",),
        )
    )
    gaps = governance.evaluate_promotion(target)
    assert PromotionGap.SECRET_DETECTED in gaps
    assert PromotionGap.SUPPLY_CHAIN_EVIDENCE_MISSING not in gaps


def test_evaluate_checks_database_freshness_at_judgement_time() -> None:
    repository = InMemoryCapabilityGovernanceRepository()
    governance = _governance(repository)
    target = _target()
    repository.create_validation_run(
        _succeeded_run(target, run_id="capval_complete")
    )
    repository.save_supply_chain_evidence(
        _supply_evidence(target, passed=True, updated_at_days_ago=8)
    )
    assert (
        PromotionGap.TRIVY_DATABASE_STALE
        in governance.evaluate_promotion(target)
    )
    # 刷新证据后缺口消失。
    repository.save_supply_chain_evidence(
        _supply_evidence(
            target, passed=True, updated_at_days_ago=1, evidence_char="c"
        )
    )
    assert governance.evaluate_promotion(target) == ()


def test_evaluate_passes_with_complete_evidence() -> None:
    repository = InMemoryCapabilityGovernanceRepository()
    governance = _governance(repository)
    target = _target()
    repository.create_validation_run(
        _succeeded_run(target, run_id="capval_complete")
    )
    repository.save_supply_chain_evidence(_supply_evidence(target))
    assert governance.evaluate_promotion(target) == ()


# ---------- S3：晋级命令 ----------


def _personal_pack(
    version: str,
    digest_char: str,
    *,
    pack_id: str = "python-table-summary",
):
    from src.conversation_steering import (
        CapabilityMaturity as LegacyCapabilityMaturity,
        CapabilityPack,
    )

    return CapabilityPack(
        pack_id=pack_id,
        version=version,
        digest="sha256:" + digest_char * 64,
        scope=ProcedureScope.PERSONAL,
        maturity=LegacyCapabilityMaturity.DRAFT,
        owner_id="owner-a",
    )


def _registered_governance(
    version: str = "1.0.0",
    digest_char: str = "a",
    *,
    pack_id: str = "python-table-summary",
):
    catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
    repository = InMemoryCapabilityGovernanceRepository()
    governance = CapabilityGovernance(catalog, repository)
    owner = CatalogActor(owner_id="owner-a", role="user")
    pack = _personal_pack(version, digest_char, pack_id=pack_id)
    catalog.register_pack(owner, pack)
    governance.register_pack(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
        idempotency_key=f"register-{version}",
    )
    return catalog, repository, governance, owner


def test_maybe_promote_promotes_and_projects_verified() -> None:
    catalog, repository, governance, owner = _registered_governance()
    target = _target()
    run = _succeeded_run(target, run_id="capval_complete")
    repository.create_validation_run(run)
    evidence = _supply_evidence(target)
    repository.save_supply_chain_evidence(evidence)

    outcome = governance.maybe_promote(target, actor=owner)

    assert outcome.status == "promoted"
    assert outcome.event.event_type == "promoted_to_verified"
    assert outcome.event.source_validation_run_id == run.run_id
    assert outcome.event.source_supply_chain_evidence_id == evidence.evidence_id
    assert outcome.event.actor_id == "owner-a"
    projection = governance.list_visible_projections(owner)[0]
    assert projection.maturity is CapabilityMaturity.VERIFIED
    assert projection.promotion_gaps == ()


def test_maybe_promote_holds_with_gaps_without_writing() -> None:
    catalog, repository, governance, owner = _registered_governance()
    target = _target()
    # 单次任务成功（只有 succeeded 运行、没有供应链证据）不能晋级。
    repository.create_validation_run(
        _succeeded_run(target, run_id="capval_complete")
    )
    outcome = governance.maybe_promote(target, actor=owner)
    assert outcome.status == "held"
    assert PromotionGap.SUPPLY_CHAIN_EVIDENCE_MISSING in outcome.gaps
    assert repository.get_latest_promotion_event(target) is None
    assert governance.list_visible_projections(owner)[0].maturity is (
        CapabilityMaturity.DRAFT
    )
    # 阻断证据同样保持草稿，且缺口逐项可解释。
    repository.save_supply_chain_evidence(
        _supply_evidence(target, passed=False, blockers=("secret_detected",))
    )
    blocked = governance.maybe_promote(target, actor=owner)
    assert blocked.status == "held"
    assert PromotionGap.SECRET_DETECTED in blocked.gaps
    assert repository.get_latest_promotion_event(target) is None


def test_maybe_promote_repeat_returns_already_verified() -> None:
    _, repository, governance, owner = _registered_governance()
    target = _target()
    repository.create_validation_run(
        _succeeded_run(target, run_id="capval_complete")
    )
    repository.save_supply_chain_evidence(_supply_evidence(target))

    first = governance.maybe_promote(target, actor=owner)
    repeated = governance.maybe_promote(target, actor=owner)

    assert first.status == "promoted"
    assert repeated.status == "already_verified"
    assert repeated.event == first.event
    promoted = [
        event
        for event in repository.list_events(target)
        if event.event_type == "promoted_to_verified"
    ]
    assert len(promoted) == 1


def test_new_digest_does_not_inherit_verified() -> None:
    catalog, repository, governance, owner = _registered_governance()
    verified_target = _target("a")
    repository.create_validation_run(
        _succeeded_run(verified_target, run_id="capval_a")
    )
    repository.save_supply_chain_evidence(_supply_evidence(verified_target))
    assert (
        governance.maybe_promote(verified_target, actor=owner).status
        == "promoted"
    )

    new_pack = _personal_pack("2.0.0", "b")
    catalog.register_pack(owner, new_pack)
    governance.register_pack(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=new_pack.pack_id,
            version=new_pack.version,
            digest=new_pack.digest,
        ),
        idempotency_key="register-2.0.0",
    )
    new_target = _target("b")
    outcome = governance.maybe_promote(new_target, actor=owner)
    assert outcome.status == "held"
    assert PromotionGap.VALIDATION_INCOMPLETE in outcome.gaps
    assert repository.get_latest_promotion_event(new_target) is None


def test_failed_new_version_keeps_old_verified() -> None:
    catalog, repository, governance, owner = _registered_governance()
    verified_target = _target("a")
    repository.create_validation_run(
        _succeeded_run(verified_target, run_id="capval_a")
    )
    repository.save_supply_chain_evidence(_supply_evidence(verified_target))
    assert (
        governance.maybe_promote(verified_target, actor=owner).status
        == "promoted"
    )

    # 新版本验证失败：只留下不可变失败记录，旧 verified 投影不受影响。
    new_pack = _personal_pack("2.0.0", "b")
    catalog.register_pack(owner, new_pack)
    governance.register_pack(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=new_pack.pack_id,
            version=new_pack.version,
            digest=new_pack.digest,
        ),
        idempotency_key="register-2.0.0",
    )
    new_target = _target("b")
    repository.create_validation_run(
        _run(new_target, run_id="capval_b_failed", status=ValidationRunStatus.FAILED)
    )
    assert (
        governance.maybe_promote(new_target, actor=owner).status == "held"
    )
    projections = {
        item.version: item
        for item in governance.list_visible_projections(owner)
    }
    assert projections["1.0.0"].maturity is CapabilityMaturity.VERIFIED
    assert projections["2.0.0"].maturity is CapabilityMaturity.DRAFT


def test_concurrent_maybe_promote_writes_single_event_inmemory() -> None:
    from concurrent.futures import ThreadPoolExecutor

    _, repository, governance, owner = _registered_governance()
    target = _target()
    repository.create_validation_run(
        _succeeded_run(target, run_id="capval_complete")
    )
    repository.save_supply_chain_evidence(_supply_evidence(target))

    def promote() -> str:
        return governance.maybe_promote(target, actor=owner).status

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _: promote(), range(8)))

    assert statuses.count("promoted") == 1
    assert statuses.count("already_verified") == 7
    promoted = [
        event
        for event in repository.list_events(target)
        if event.event_type == "promoted_to_verified"
    ]
    assert len(promoted) == 1


# ---------- S4：缺口投影 ----------


def test_projection_exposes_sanitized_gaps_for_draft_pack() -> None:
    _, repository, governance, owner = _registered_governance()
    target = _target()
    repository.create_validation_run(
        _succeeded_run(target, run_id="capval_complete")
    )
    # 缺供应链证据：缺口必须可解释，且只使用脱敏字面量。
    view = governance.list_visible_projections(owner)[0]
    assert view.maturity is CapabilityMaturity.DRAFT
    assert PromotionGap.SUPPLY_CHAIN_EVIDENCE_MISSING in view.promotion_gaps
    assert set(view.promotion_gaps) <= set(PromotionGap)


def test_projection_gaps_are_empty_for_verified_pack() -> None:
    _, repository, governance, owner = _registered_governance()
    target = _target()
    repository.create_validation_run(
        _succeeded_run(target, run_id="capval_complete")
    )
    repository.save_supply_chain_evidence(_supply_evidence(target))
    governance.maybe_promote(target, actor=owner)
    view = governance.list_visible_projections(owner)[0]
    assert view.maturity is CapabilityMaturity.VERIFIED
    assert view.promotion_gaps == ()


def test_other_owner_sees_nothing_including_gaps() -> None:
    _, _, governance, _ = _registered_governance()
    other = CatalogActor(owner_id="owner-b", role="user")
    assert governance.list_visible_projections(other) == ()


# ---------- S5：worker 触发点 ----------


def _queued_run(governance, owner, target) -> CapabilityValidationRun:
    return governance.request_validation(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=target.pack_id,
            version=target.version,
            digest=target.digest,
        ),
        task_ref=_task_ref(target),
        idempotency_key=f"worker-{target.digest[-4:]}",
    )


class _PassingExecutor:
    def execute(self, run, step):
        return _passed_evidence(step, run.run_id)


def test_worker_promotes_after_succeeded_run() -> None:
    import asyncio

    _, repository, governance, owner = _registered_governance()
    target = _target()
    run = _queued_run(governance, owner, target)
    # 供应链证据先于验证完成落库。
    repository.save_supply_chain_evidence(_supply_evidence(target))
    manager = CapabilityValidationManager(
        governance,
        lambda _run: _PassingExecutor(),
    )

    assert asyncio.run(manager.run_once()) == 1
    completed = governance.get_validation(owner, run.run_id)
    assert completed.status is ValidationRunStatus.SUCCEEDED
    promoted = repository.get_latest_promotion_event(target)
    assert promoted is not None
    assert promoted.source_validation_run_id == run.run_id
    assert governance.list_visible_projections(owner)[0].maturity is (
        CapabilityMaturity.VERIFIED
    )


def test_worker_holds_promotion_without_supply_chain_evidence() -> None:
    import asyncio

    _, repository, governance, owner = _registered_governance()
    target = _target()
    run = _queued_run(governance, owner, target)
    manager = CapabilityValidationManager(
        governance,
        lambda _run: _PassingExecutor(),
    )

    assert asyncio.run(manager.run_once()) == 1
    # 五步成功只形成验证事实，不能在没有供应链证据时晋级。
    completed = governance.get_validation(owner, run.run_id)
    assert completed.status is ValidationRunStatus.SUCCEEDED
    assert repository.get_latest_promotion_event(target) is None
    view = governance.list_visible_projections(owner)[0]
    assert view.maturity is CapabilityMaturity.DRAFT
    assert PromotionGap.SUPPLY_CHAIN_EVIDENCE_MISSING in view.promotion_gaps


def test_worker_does_not_promote_failed_run() -> None:
    import asyncio

    class FailingExecutor:
        def execute(self, run, step):
            if step is ValidationStep.SYNTHETIC_SMOKE:
                raise RuntimeError("合成 Smoke 失败")
            return _passed_evidence(step, run.run_id)

    _, repository, governance, owner = _registered_governance()
    target = _target()
    run = _queued_run(governance, owner, target)
    repository.save_supply_chain_evidence(_supply_evidence(target))
    manager = CapabilityValidationManager(
        governance,
        lambda _run: FailingExecutor(),
    )

    assert asyncio.run(manager.run_once()) == 1
    completed = governance.get_validation(owner, run.run_id)
    assert completed.status is ValidationRunStatus.FAILED
    assert repository.get_latest_promotion_event(target) is None
    assert governance.list_visible_projections(owner)[0].maturity is (
        CapabilityMaturity.DRAFT
    )


def test_worker_promotes_when_evidence_lands_in_preflight(tmp_path) -> None:
    import asyncio

    class LandingCollector:
        def __init__(self, repository, target) -> None:
            self._repository = repository
            self._target = target
            self.calls = 0

        def requires_collection(self, target) -> bool:
            return True

        def collect(self, target, subject_root):
            self.calls += 1
            self._repository.save_supply_chain_evidence(
                _supply_evidence(self._target, evidence_char="d")
            )

    _, repository, governance, owner = _registered_governance()
    target = _target()
    run = _queued_run(governance, owner, target)
    mount = tmp_path / "mount"
    mount.mkdir()
    (mount / ".mangrove-capability-digest").write_text(
        target.digest,
        encoding="utf-8",
    )
    collector = LandingCollector(repository, target)
    manager = CapabilityValidationManager(
        governance,
        lambda _run: _PassingExecutor(),
        supply_chain_evidence=collector,
        capability_mounts=lambda _owner, _task, _revision: (str(mount),),
    )

    assert asyncio.run(manager.run_once()) == 1
    completed = governance.get_validation(owner, run.run_id)
    assert completed.status is ValidationRunStatus.SUCCEEDED
    assert collector.calls == 1
    promoted = [
        event
        for event in repository.list_events(target)
        if event.event_type == "promoted_to_verified"
    ]
    assert len(promoted) == 1
    assert governance.list_visible_projections(owner)[0].maturity is (
        CapabilityMaturity.VERIFIED
    )


# ---------- S7：冻结夹具双向验证 ----------
# （S6 前端卡片缺口展示为前端切片，见 frontend/e2e/settings-role-access.spec.ts
#   "草稿能力卡片展示脱敏缺口并提示自动晋级"与 CapabilityGovernancePanel.tsx。）


def test_python_tool_frozen_fixture_promotion_and_failure_paths() -> None:
    """Python 表格 Tool 夹具：晋级断言引用冻结证据，失败路径精确可解释。"""

    _, repository, governance, owner = _registered_governance()
    target = _target()
    run = _succeeded_run(target, run_id="capval_py_success")
    repository.create_validation_run(run)
    evidence = _supply_evidence(target, evidence_char="e")
    repository.save_supply_chain_evidence(evidence)

    outcome = governance.maybe_promote(target, actor=owner)

    # 成功路径：晋级事件必须引用夹具显式构造的验证运行与供应链证据，
    # 不能用实现自己生成的摘要证明自己。
    assert outcome.status == "promoted"
    assert outcome.event.source_validation_run_id == run.run_id
    assert outcome.event.source_supply_chain_evidence_id == evidence.evidence_id

    # 失败路径：新版本 digest 遇到可修复 High 漏洞，保持草稿且缺口精确。
    new_target = _target("f", version="2.0.0")
    catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
    new_pack = _personal_pack("2.0.0", "f")
    catalog.register_pack(owner, new_pack)
    governance_with_new = CapabilityGovernance(catalog, repository)
    governance_with_new.register_pack(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=new_pack.pack_id,
            version=new_pack.version,
            digest=new_pack.digest,
        ),
        idempotency_key="register-2.0.0",
    )
    repository.create_validation_run(
        _succeeded_run(new_target, run_id="capval_py_v2")
    )
    repository.save_supply_chain_evidence(
        _supply_evidence(
            new_target,
            passed=False,
            blockers=("fixable_high_vulnerability",),
            evidence_char="f",
        )
    )
    blocked = governance_with_new.maybe_promote(new_target, actor=owner)
    assert blocked.status == "held"
    assert blocked.gaps == (PromotionGap.FIXABLE_HIGH_VULNERABILITY,)
    assert repository.get_latest_promotion_event(new_target) is None


def test_mcp_frozen_fixture_promotion_and_failure_paths() -> None:
    """Everything MCP 夹具：协议型能力同样走晋级门，过期库缺口可解释。"""

    _, repository, governance, owner = _registered_governance(
        version="2026.7.4",
        digest_char="c",
        pack_id="everything-mcp",
    )
    target = _target("c", pack_id="everything-mcp", version="2026.7.4")
    run = _succeeded_run(target, run_id="capval_mcp_success")
    repository.create_validation_run(run)
    evidence = _supply_evidence(target, evidence_char="c")
    repository.save_supply_chain_evidence(evidence)

    assert governance.maybe_promote(target, actor=owner).status == "promoted"

    # 失败路径：同夹具新 digest 的证据在判定时刻已过期。
    stale_target = _target("d", pack_id="everything-mcp", version="2026.7.4")
    catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
    stale_pack = _personal_pack(
        "2026.7.4", "d", pack_id="everything-mcp"
    )
    catalog.register_pack(owner, stale_pack)
    governance_with_stale = CapabilityGovernance(catalog, repository)
    governance_with_stale.register_pack(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=stale_pack.pack_id,
            version=stale_pack.version,
            digest=stale_pack.digest,
        ),
        idempotency_key="register-stale",
    )
    repository.create_validation_run(
        _succeeded_run(stale_target, run_id="capval_mcp_stale")
    )
    repository.save_supply_chain_evidence(
        _supply_evidence(
            stale_target, passed=True, updated_at_days_ago=8, evidence_char="d"
        )
    )
    held = governance_with_stale.maybe_promote(stale_target, actor=owner)
    assert held.status == "held"
    assert held.gaps == (PromotionGap.TRIVY_DATABASE_STALE,)
    assert repository.get_latest_promotion_event(stale_target) is None


def test_python_tool_fixture_promotion_persists_in_sqlite(tmp_path) -> None:
    from src.capability_catalog import SqliteCapabilityCatalogRepository

    db_path = tmp_path / "webui.db"
    migrate_capability_governance(db_path, tmp_path / "backup.db")
    catalog = CapabilityCatalog(SqliteCapabilityCatalogRepository(str(db_path)))
    repository = SqliteCapabilityGovernanceRepository(str(db_path))
    governance = CapabilityGovernance(catalog, repository)
    owner = CatalogActor(owner_id="owner-a", role="user")
    pack = _personal_pack("1.0.0", "a")
    catalog.register_pack(owner, pack)
    governance.register_pack(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
        idempotency_key="register-sqlite",
    )
    target = _target()
    run = _succeeded_run(target, run_id="capval_sqlite")
    repository.create_validation_run(run)
    repository.save_supply_chain_evidence(
        _supply_evidence(target, evidence_char="e")
    )
    assert governance.maybe_promote(target, actor=owner).status == "promoted"

    # 重开全部 Repository：晋级事实必须持久化，且不依赖内存状态。
    reopened = CapabilityGovernance(
        CapabilityCatalog(SqliteCapabilityCatalogRepository(str(db_path))),
        SqliteCapabilityGovernanceRepository(str(db_path)),
    )
    projection = reopened.list_visible_projections(owner)[0]
    assert projection.maturity is CapabilityMaturity.VERIFIED
    assert projection.promotion_gaps == ()
    event = SqliteCapabilityGovernanceRepository(
        str(db_path)
    ).get_latest_promotion_event(target)
    assert event is not None
    assert event.source_validation_run_id == run.run_id


def test_concurrent_maybe_promote_writes_single_event_sqlite(tmp_path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from src.capability_catalog import SqliteCapabilityCatalogRepository

    db_path = tmp_path / "webui.db"
    migrate_capability_governance(db_path, tmp_path / "backup.db")
    catalog = CapabilityCatalog(SqliteCapabilityCatalogRepository(str(db_path)))
    repository = SqliteCapabilityGovernanceRepository(str(db_path))
    governance = CapabilityGovernance(catalog, repository)
    owner = CatalogActor(owner_id="owner-a", role="user")
    pack = _personal_pack("1.0.0", "a")
    catalog.register_pack(owner, pack)
    governance.register_pack(
        owner,
        pack_ref=CapabilityPackRef(
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
        idempotency_key="register-v1",
    )
    target = _target()
    repository.create_validation_run(
        _succeeded_run(target, run_id="capval_complete")
    )
    repository.save_supply_chain_evidence(_supply_evidence(target))

    def promote() -> str:
        return governance.maybe_promote(target, actor=owner).status

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _: promote(), range(8)))

    assert statuses.count("promoted") == 1
    assert statuses.count("already_verified") == 7
    promoted = [
        event
        for event in repository.list_events(target)
        if event.event_type == "promoted_to_verified"
    ]
    assert len(promoted) == 1
