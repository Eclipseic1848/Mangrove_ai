# -*- coding: utf-8 -*-
"""AC07-09 S3：弃用/撤销/隔离/风险接受/恢复/回滚治理命令。"""
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


_FINDING_RUN_ID = "capval_f1f2f3f4e5e6a1b2c3d4"
_PLATFORM_FINDING_RUN_ID = "pfval_f1f2f3f4e5e6a1b2c3d4"


def _admin() -> CatalogActor:
    return CatalogActor(owner_id="admin-a", role="admin")


def _user() -> CatalogActor:
    return CatalogActor(owner_id="owner-a", role="user")


def _platform_target(digest_char: str = "a") -> CapabilityGovernanceTarget:
    return CapabilityGovernanceTarget(
        owner_id=None,
        scope=ProcedureScope.PLATFORM,
        pack_id="gray-python-table",
        version="1.0.0",
        digest="sha256:" + digest_char * 64,
    )


def _pack(target: CapabilityGovernanceTarget) -> CapabilityPack:
    return CapabilityPack(
        pack_id=target.pack_id,
        version=target.version,
        digest=target.digest,
        scope=ProcedureScope.PLATFORM,
        maturity=LegacyCapabilityMaturity.VERIFIED,
        owner_id=None,
    )


def _ref(target: CapabilityGovernanceTarget) -> CapabilityPackRef:
    return CapabilityPackRef(
        pack_id=target.pack_id,
        version=target.version,
        digest=target.digest,
    )


def _governance(
    repository: InMemoryCapabilityGovernanceRepository,
) -> tuple[CapabilityGovernance, CapabilityGovernanceTarget]:
    catalog_repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(catalog_repository)
    target = _platform_target()
    catalog_repository.save_pack(_pack(target))
    return CapabilityGovernance(catalog, repository), target


def _lifecycle(
    repository: InMemoryCapabilityGovernanceRepository,
    target: CapabilityGovernanceTarget,
    lifecycle: CapabilityLifecycle,
    *,
    occurred_at: datetime | None = None,
) -> None:
    fields: dict = {
        "event_type": "lifecycle_changed",
        "idempotency_key": f"gov:lifecycle:{lifecycle.value}",
        "target": target,
        "maturity": CapabilityMaturity.VERIFIED,
        "lifecycle": lifecycle,
        "eligibility": CapabilityEligibility.ELIGIBLE,
        "actor_id": "admin-a",
        "actor_role": "admin",
        "reason": "测试生命周期事件",
    }
    if occurred_at is not None:
        fields["occurred_at"] = occurred_at
    repository.save_governance_event(CapabilityGovernanceEvent(**fields))


def _quarantine(
    repository: InMemoryCapabilityGovernanceRepository,
    target: CapabilityGovernanceTarget,
    *,
    occurred_at: datetime | None = None,
) -> None:
    fields: dict = {
        "event_type": "eligibility_changed",
        "idempotency_key": "gov:eligibility:quarantine",
        "target": target,
        "maturity": CapabilityMaturity.VERIFIED,
        "lifecycle": CapabilityLifecycle.ACTIVE,
        "eligibility": CapabilityEligibility.QUARANTINED,
        "actor_id": "admin-a",
        "actor_role": "admin",
        "reason": "测试隔离事件",
    }
    if occurred_at is not None:
        fields["occurred_at"] = occurred_at
    repository.save_governance_event(CapabilityGovernanceEvent(**fields))


def _publish_event(
    repository: InMemoryCapabilityGovernanceRepository,
    target: CapabilityGovernanceTarget,
) -> None:
    repository.save_platform_event(
        CapabilityGovernanceEvent(
            event_type="platform_published",
            idempotency_key="publish:test",
            target=target,
            maturity=CapabilityMaturity.VERIFIED,
            actor_id="admin-a",
            actor_role="admin",
            reason="发布：测试",
            source_digest="sha256:" + "b" * 64,
            platform_digest=target.digest,
            audience="admin_gray",
            platform_validation_run_id="pfval_" + "a" * 20,
            signing_signature_digest="sha256:" + "c" * 64,
            signing_public_key_sha256="d" * 64,
        )
    )


def _supply_evidence(
    repository: InMemoryCapabilityGovernanceRepository,
    target: CapabilityGovernanceTarget,
    *,
    updated_at_days_ago: int = 0,
    secret_count: int = 0,
    critical_count: int = 0,
) -> None:
    blockers = tuple(
        blocker
        for blocker, present in (
            ("secret_detected", secret_count > 0),
            ("critical_vulnerability", critical_count > 0),
        )
        if present
    )
    repository.save_supply_chain_evidence(
        CapabilitySupplyChainEvidence(
            evidence_id="supply_" + "b" * 20,
            target=target,
            subject_digest=target.digest,
            status=(
                SupplyChainEvidenceStatus.BLOCKED
                if blockers
                else SupplyChainEvidenceStatus.PASSED
            ),
            blockers=blockers,
            secret_count=secret_count,
            critical_count=critical_count,
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


def _succeeded_run(
    repository: InMemoryCapabilityGovernanceRepository,
    target: CapabilityGovernanceTarget,
    *,
    run_id: str = "capval_" + "a" * 16,
) -> None:
    run = CapabilityValidationRun(
        run_id=run_id,
        owner_id="owner-a",
        target=target,
        actor_id="owner-a",
        actor_role="user",
        idempotency_key="validation:test",
        task_ref=__import__(
            "src.capability_governance", fromlist=["ValidationTaskRef"]
        ).ValidationTaskRef(
            task_id="workspace-a",
            revision=1,
            source_snapshot_sha256="a" * 64,
            input_sha256="a" * 64,
            output_sha256="a" * 64,
            capability_digest=target.digest,
            authorization_id="auth_" + "a" * 20,
        ),
        status=ValidationRunStatus.SUCCEEDED,
        evidence=tuple(
            ValidationEvidence(
                step=step,
                status=ValidationStepStatus.PASSED,
                evidence_ref=f"evidence://validation/run/{step.value}",
                evidence_sha256="a" * 64,
                summary="通过",
            )
            for step in ValidationStep
        ),
    )
    created = repository.create_validation_run(
        run.model_copy(update={"status": ValidationRunStatus.QUEUED})
    )
    repository.save_validation_run(
        created.model_copy(
            update={"status": ValidationRunStatus.SUCCEEDED}
        )
    )


def _succeeded_platform_run(
    repository: InMemoryCapabilityGovernanceRepository,
    target: CapabilityGovernanceTarget,
    *,
    run_id: str = _PLATFORM_FINDING_RUN_ID,
) -> None:
    """六步全绿平台验证运行（#15 阶段 6：平台包 finding_ref 取平台验证运行表）。"""
    from src.capability_governance.models import (
        PlatformValidationEvidence,
        PlatformValidationRun,
        PlatformValidationStep,
    )

    run = PlatformValidationRun(
        run_id=run_id,
        actor_id="admin-a",
        actor_role="admin",
        idempotency_key=f"candidate:{target.digest}",
        target=target,
        status=ValidationRunStatus.SUCCEEDED,
        evidence=tuple(
            PlatformValidationEvidence(
                step=step,
                status=ValidationStepStatus.PASSED,
                evidence_ref=f"evidence://platform/{step.value}",
                evidence_sha256="a" * 64,
                summary="通过",
            )
            for step in PlatformValidationStep
        ),
        signing_signature_digest="sha256:" + "c" * 64,
        signing_public_key_sha256="d" * 64,
    )
    repository.create_platform_validation_run(run)


class TestDeprecateCommand:
    def test_active_pack_deprecates(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _governance(repository)
        outcome = governance.deprecate_pack(
            _admin(),
            pack_ref=_ref(target),
            reason="弃用：替代版本已发布",
            idempotency_key="deprecate:test",
        )
        assert outcome.status == "applied"
        assert outcome.event is not None
        assert outcome.event.lifecycle is CapabilityLifecycle.DEPRECATED

    def test_same_key_is_idempotent(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _governance(repository)
        first = governance.deprecate_pack(
            _admin(),
            pack_ref=_ref(target),
            reason="弃用",
            idempotency_key="deprecate:test",
        )
        second = governance.deprecate_pack(
            _admin(),
            pack_ref=_ref(target),
            reason="弃用",
            idempotency_key="deprecate:test",
        )
        assert second.status == "already_applied"
        assert first.event.event_id == second.event.event_id

    def test_deprecated_pack_rejected(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _governance(repository)
        _lifecycle(repository, target, CapabilityLifecycle.DEPRECATED)
        outcome = governance.deprecate_pack(
            _admin(),
            pack_ref=_ref(target),
            reason="重复弃用",
            idempotency_key="deprecate:again",
        )
        assert outcome.status == "rejected"
        assert outcome.gaps == ("not_active",)

    def test_non_admin_rejected(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _governance(repository)
        with pytest.raises(PermissionError):
            governance.deprecate_pack(
                _user(),
                pack_ref=_ref(target),
                reason="越权",
                idempotency_key="deprecate:user",
            )

    def test_deprecate_quarantined_keeps_snapshot(self) -> None:
        """隔离中的弃用：事件快照携带 quarantined，不冒充 eligible。"""
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _governance(repository)
        _quarantine(repository, target)
        outcome = governance.deprecate_pack(
            _admin(),
            pack_ref=_ref(target),
            reason="弃用",
            idempotency_key="deprecate:quarantined",
        )
        assert outcome.status == "applied"
        assert outcome.event is not None
        assert outcome.event.eligibility is CapabilityEligibility.QUARANTINED
        # 投影：两轴独立——deprecated + quarantined。
        projection = governance.runtime_projection_for_pack(_pack(target))
        assert projection.lifecycle is CapabilityLifecycle.DEPRECATED
        assert projection.eligibility is CapabilityEligibility.QUARANTINED


class TestRevokeCommand:
    def test_active_pack_revokes(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _governance(repository)
        outcome = governance.revoke_pack(
            _admin(),
            pack_ref=_ref(target),
            reason="撤销：存在 Critical 漏洞",
            idempotency_key="revoke:test",
        )
        assert outcome.status == "applied"
        assert outcome.event.lifecycle is CapabilityLifecycle.REVOKED

    def test_deprecated_pack_revokes(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _governance(repository)
        _lifecycle(repository, target, CapabilityLifecycle.DEPRECATED)
        outcome = governance.revoke_pack(
            _admin(),
            pack_ref=_ref(target),
            reason="撤销",
            idempotency_key="revoke:test",
        )
        assert outcome.status == "applied"

    def test_revoked_pack_rejected(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _governance(repository)
        _lifecycle(repository, target, CapabilityLifecycle.REVOKED)
        outcome = governance.revoke_pack(
            _admin(),
            pack_ref=_ref(target),
            reason="重复撤销",
            idempotency_key="revoke:again",
        )
        assert outcome.status == "rejected"
        assert outcome.gaps == ("already_revoked",)


class TestQuarantineCommand:
    def test_eligible_pack_quarantines(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _governance(repository)
        outcome = governance.quarantine_pack(
            _admin(),
            pack_ref=_ref(target),
            reason="隔离：扫描发现 Secret",
            idempotency_key="quarantine:test",
        )
        assert outcome.status == "applied"
        assert outcome.event.eligibility is CapabilityEligibility.QUARANTINED

    def test_quarantined_pack_rejected(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _governance(repository)
        _quarantine(repository, target)
        outcome = governance.quarantine_pack(
            _admin(),
            pack_ref=_ref(target),
            reason="重复隔离",
            idempotency_key="quarantine:again",
        )
        assert outcome.status == "rejected"
        assert outcome.gaps == ("already_quarantined",)

    def test_revoked_pack_rejected(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _governance(repository)
        _lifecycle(repository, target, CapabilityLifecycle.REVOKED)
        outcome = governance.quarantine_pack(
            _admin(),
            pack_ref=_ref(target),
            reason="隔离已撤销能力",
            idempotency_key="quarantine:revoked",
        )
        assert outcome.status == "rejected"


class TestRiskAcceptCommand:
    def _quarantined_governance(
        self,
        *,
        secret_count: int = 0,
        critical_count: int = 0,
    ) -> tuple[CapabilityGovernance, InMemoryCapabilityGovernanceRepository, CapabilityGovernanceTarget]:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _governance(repository)
        # 风险接受前置：admin_gray 受众 + 供应链证据 + finding_ref 实引。
        # 平台包 finding_ref 必须实引平台验证运行表（#15 阶段 6 修复：
        # 个人表只有个人 digest，平台 digest 永不匹配）。
        _publish_event(repository, target)
        _supply_evidence(
            repository,
            target,
            secret_count=secret_count,
            critical_count=critical_count,
        )
        _succeeded_platform_run(repository, target, run_id=_PLATFORM_FINDING_RUN_ID)
        _quarantine(repository, target)
        return governance, repository, target

    def test_default_30_days(self) -> None:
        governance, _, target = self._quarantined_governance()
        outcome = governance.accept_pack_risk(
            _admin(),
            pack_ref=_ref(target),
            reason="风险接受：无修复且路径不可达的 High",
            finding_ref=_PLATFORM_FINDING_RUN_ID,
            idempotency_key="risk:test",
        )
        assert outcome.status == "applied"
        assert outcome.event is not None
        remaining = outcome.event.expires_at - outcome.event.occurred_at
        assert timedelta(days=29) < remaining <= timedelta(days=30)

    def test_max_90_days(self) -> None:
        governance, _, target = self._quarantined_governance()
        outcome = governance.accept_pack_risk(
            _admin(),
            pack_ref=_ref(target),
            reason="风险接受",
            finding_ref=_PLATFORM_FINDING_RUN_ID,
            days=90,
            idempotency_key="risk:90",
        )
        assert outcome.status == "applied"

    def test_91_days_rejected(self) -> None:
        governance, _, target = self._quarantined_governance()
        outcome = governance.accept_pack_risk(
            _admin(),
            pack_ref=_ref(target),
            reason="风险接受",
            finding_ref=_FINDING_RUN_ID,
            days=91,
            idempotency_key="risk:91",
        )
        assert outcome.status == "rejected"
        assert outcome.gaps == ("days_out_of_range",)

    def test_non_quarantined_rejected(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _governance(repository)
        outcome = governance.accept_pack_risk(
            _admin(),
            pack_ref=_ref(target),
            reason="风险接受",
            finding_ref=_FINDING_RUN_ID,
            idempotency_key="risk:not-quarantined",
        )
        assert outcome.status == "rejected"
        assert "not_quarantined" in outcome.gaps

    def test_secret_finding_rejected(self) -> None:
        """Secret/Critical 是不可例外硬门（ADR-0029），不能风险接受。"""
        governance, _, target = self._quarantined_governance(
            secret_count=1
        )
        outcome = governance.accept_pack_risk(
            _admin(),
            pack_ref=_ref(target),
            reason="风险接受",
            finding_ref=_PLATFORM_FINDING_RUN_ID,
            idempotency_key="risk:secret",
        )
        assert outcome.status == "rejected"
        assert "non_waivable_findings" in outcome.gaps

    def test_unknown_finding_ref_rejected(self) -> None:
        """finding_ref 必须实引验证运行证据（Q2A 只豁免人工判定）。"""
        governance, _, target = self._quarantined_governance()
        outcome = governance.accept_pack_risk(
            _admin(),
            pack_ref=_ref(target),
            reason="风险接受",
            finding_ref="capval_" + "0" * 20,
            idempotency_key="risk:unknown-ref",
        )
        assert outcome.status == "rejected"
        assert "finding_ref_unknown" in outcome.gaps

    def test_platform_pack_requires_platform_run_ref(self) -> None:
        """#15 阶段 6 回归：平台包 finding_ref 必须实引平台验证运行。

        个人验证运行表只有个人 digest，平台 digest 永不匹配；引用个人表
        运行必须被拒（Q2A 引用存档真实性，不得跨表引用）。
        """
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _governance(repository)
        _publish_event(repository, target)
        _supply_evidence(repository, target)
        _succeeded_run(repository, target, run_id=_FINDING_RUN_ID)
        _quarantine(repository, target)
        outcome = governance.accept_pack_risk(
            _admin(),
            pack_ref=_ref(target),
            reason="风险接受",
            finding_ref=_FINDING_RUN_ID,
            idempotency_key="risk:platform-ref",
        )
        assert outcome.status == "rejected"
        assert "finding_ref_unknown" in outcome.gaps

    def test_personal_pack_rejected(self) -> None:
        """风险接受只存在于平台 admin_gray 范围（ADR-0029）。"""
        repository = InMemoryCapabilityGovernanceRepository()
        catalog_repository = InMemoryCapabilityCatalogRepository()
        catalog = CapabilityCatalog(catalog_repository)
        target = CapabilityGovernanceTarget(
            owner_id="owner-a",
            scope=ProcedureScope.PERSONAL,
            pack_id="private-a",
            version="1.0.0",
            digest="sha256:" + "e" * 64,
        )
        catalog_repository.save_pack(
            CapabilityPack(
                pack_id="private-a",
                version="1.0.0",
                digest="sha256:" + "e" * 64,
                scope=ProcedureScope.PERSONAL,
                maturity=LegacyCapabilityMaturity.VERIFIED,
                owner_id="owner-a",
            )
        )
        governance = CapabilityGovernance(catalog, repository)
        _quarantine(repository, target)
        outcome = governance.accept_pack_risk(
            _admin(),
            pack_ref=_ref(target),
            reason="风险接受",
            finding_ref=_FINDING_RUN_ID,
            idempotency_key="risk:personal",
        )
        assert outcome.status == "rejected"
        assert "not_admin_gray" in outcome.gaps


class TestRestoreCommand:
    def _revoked_with_evidence(
        self,
        *,
        updated_at_days_ago: int = 0,
        with_publish: bool = True,
    ) -> tuple[CapabilityGovernance, InMemoryCapabilityGovernanceRepository, CapabilityGovernanceTarget]:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _governance(repository)
        if with_publish:
            _publish_event(repository, target)
        _supply_evidence(
            repository, target, updated_at_days_ago=updated_at_days_ago
        )
        _succeeded_platform_run(repository, target)
        _lifecycle(repository, target, CapabilityLifecycle.REVOKED)
        return governance, repository, target

    def test_revoked_restores_with_full_recheck(self) -> None:
        governance, _, target = self._revoked_with_evidence()
        outcome = governance.restore_pack(
            _admin(),
            pack_ref=_ref(target),
            reason="恢复：修复版本已验证",
            idempotency_key="restore:test",
        )
        assert outcome.status == "applied"
        assert outcome.event.lifecycle is CapabilityLifecycle.ACTIVE

    def test_stale_trivy_db_rejected(self) -> None:
        governance, _, target = self._revoked_with_evidence(
            updated_at_days_ago=8
        )
        outcome = governance.restore_pack(
            _admin(),
            pack_ref=_ref(target),
            reason="恢复",
            idempotency_key="restore:stale",
        )
        assert outcome.status == "rejected"
        assert "trivy_database_stale" in outcome.gaps

    def test_missing_publish_event_rejected(self) -> None:
        governance, _, target = self._revoked_with_evidence(
            with_publish=False
        )
        outcome = governance.restore_pack(
            _admin(),
            pack_ref=_ref(target),
            reason="恢复",
            idempotency_key="restore:no-publish",
        )
        assert outcome.status == "rejected"
        assert "publication_missing" in outcome.gaps

    def test_expired_risk_acceptance_restores_both_axes(self) -> None:
        """隔离中的能力恢复：先解除隔离，再恢复生命周期（快照自洽）。"""
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _governance(repository)
        _publish_event(repository, target)
        _supply_evidence(repository, target)
        _succeeded_platform_run(repository, target)
        now = datetime.now(timezone.utc)
        _quarantine(repository, target, occurred_at=now - timedelta(days=40))
        repository.save_governance_event(
            CapabilityGovernanceEvent(
                event_type="risk_accepted",
                idempotency_key="gov:risk:expired",
                target=target,
                maturity=CapabilityMaturity.VERIFIED,
                lifecycle=CapabilityLifecycle.ACTIVE,
                eligibility=CapabilityEligibility.ELIGIBLE,
                actor_id="admin-a",
                actor_role="admin",
                reason="风险接受",
                expires_at=now - timedelta(days=10),
                finding_ref="capval_a1b2c3d4e5f6a1b2c3d4",
                occurred_at=now - timedelta(days=40),
            )
        )
        outcome = governance.restore_pack(
            _admin(),
            pack_ref=_ref(target),
            reason="恢复",
            idempotency_key="restore:expired-risk",
        )
        assert outcome.status == "applied"
        projection = governance.runtime_projection_for_pack(
            _pack(target)
        )
        assert projection.lifecycle is CapabilityLifecycle.ACTIVE
        assert projection.eligibility is CapabilityEligibility.ELIGIBLE

    def test_eligibility_only_restore_is_idempotent(self) -> None:
        """仅隔离（无生命周期异常）恢复：同键重试返回 already_applied，
        不得退化为 nothing_to_restore 的 409 语义。"""
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _governance(repository)
        _publish_event(repository, target)
        _supply_evidence(repository, target)
        _succeeded_platform_run(repository, target)
        now = datetime.now(timezone.utc)
        _quarantine(repository, target, occurred_at=now - timedelta(days=40))
        repository.save_governance_event(
            CapabilityGovernanceEvent(
                event_type="risk_accepted",
                idempotency_key="gov:risk:expired2",
                target=target,
                maturity=CapabilityMaturity.VERIFIED,
                lifecycle=CapabilityLifecycle.ACTIVE,
                eligibility=CapabilityEligibility.ELIGIBLE,
                actor_id="admin-a",
                actor_role="admin",
                reason="风险接受",
                expires_at=now - timedelta(days=10),
                finding_ref="capval_a1b2c3d4e5f6a1b2c3d4",
                occurred_at=now - timedelta(days=40),
            )
        )
        first = governance.restore_pack(
            _admin(),
            pack_ref=_ref(target),
            reason="恢复",
            idempotency_key="restore:elig-only",
        )
        second = governance.restore_pack(
            _admin(),
            pack_ref=_ref(target),
            reason="恢复",
            idempotency_key="restore:elig-only",
        )
        assert first.status == "applied"
        assert second.status == "already_applied"
        assert second.event is not None
        assert first.event.event_id == second.event.event_id

    def test_revoked_quarantined_restores_both_axes(self) -> None:
        """先隔离再撤销的组合：恢复先写生命周期（携带 quarantined 快照），
        再解除隔离；每个事件快照与写入时刻投影一致。"""
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _governance(repository)
        _publish_event(repository, target)
        _supply_evidence(repository, target)
        _succeeded_platform_run(repository, target)
        _quarantine(repository, target)
        _lifecycle(repository, target, CapabilityLifecycle.REVOKED)
        outcome = governance.restore_pack(
            _admin(),
            pack_ref=_ref(target),
            reason="恢复",
            idempotency_key="restore:revoked-quarantined",
        )
        assert outcome.status == "applied"
        projection = governance.runtime_projection_for_pack(_pack(target))
        assert projection.lifecycle is CapabilityLifecycle.ACTIVE
        assert projection.eligibility is CapabilityEligibility.ELIGIBLE


class TestRollbackCommand:
    def test_rollback_sets_recommendation(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _governance(repository)
        _publish_event(repository, target)
        outcome = governance.rollback_recommendation(
            _admin(),
            pack_ref=_ref(target),
            reason="回滚：新版本有阻断问题",
            idempotency_key="rollback:test",
        )
        assert outcome.status == "applied"
        assert outcome.event is not None
        assert outcome.event.recommended_version == target.version

    def test_legacy_pack_without_publication_rejected(self) -> None:
        """无发布事件即无签名证据：不满足回滚目标前置。"""
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _governance(repository)
        outcome = governance.rollback_recommendation(
            _admin(),
            pack_ref=_ref(target),
            reason="回滚",
            idempotency_key="rollback:legacy",
        )
        assert outcome.status == "rejected"
        assert "publication_missing" in outcome.gaps

    def test_same_recommendation_rejected(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _governance(repository)
        _publish_event(repository, target)
        governance.rollback_recommendation(
            _admin(),
            pack_ref=_ref(target),
            reason="回滚",
            idempotency_key="rollback:first",
        )
        outcome = governance.rollback_recommendation(
            _admin(),
            pack_ref=_ref(target),
            reason="重复回滚",
            idempotency_key="rollback:second",
        )
        assert outcome.status == "rejected"
        assert outcome.gaps == ("already_recommended",)
