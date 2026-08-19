# -*- coding: utf-8 -*-
"""AC07-10 S1：装载门自动隔离钩子（验签失败→隔离）与签名失败自动隔离服务方法。"""
from __future__ import annotations

from src.capability_catalog import (
    CapabilityCatalog,
    CapabilityMountGateRejected,
    CapabilityPackRef,
    InMemoryCapabilityCatalogRepository,
)
from src.capability_catalog.models import CatalogActor
from src.capability_governance import (
    CapabilityEligibility,
    CapabilityGovernance,
    CapabilityGovernanceEvent,
    CapabilityGovernanceProjection,
    CapabilityGovernanceTarget,
    CapabilityLifecycle,
    CapabilityMaturity,
    InMemoryCapabilityGovernanceRepository,
)
from src.capability_governance.models import (
    CapabilitySupplyChainEvidence,
    PlatformValidationRun,
    SupplyChainEvidenceStatus,
    TrivyDatabaseMetadata,
    ValidationRunStatus,
)
from src.capability_governance.oci_signing import SigningStepResult
from src.conversation_steering import (
    CapabilityMaturity as LegacyCapabilityMaturity,
    CapabilityPack,
    ProcedureScope,
)
from src.capability_governance.runtime_gate import CapabilityGovernanceRuntimeGate


def _platform_pack(digest_char: str = "a") -> CapabilityPack:
    return CapabilityPack(
        pack_id="gray-python-table",
        version="1.0.0",
        digest="sha256:" + digest_char * 64,
        scope=ProcedureScope.PLATFORM,
        maturity=LegacyCapabilityMaturity.VERIFIED,
        owner_id=None,
    )


def _target(digest_char: str = "a") -> CapabilityGovernanceTarget:
    return CapabilityGovernanceTarget(
        owner_id=None,
        scope=ProcedureScope.PLATFORM,
        pack_id="gray-python-table",
        version="1.0.0",
        digest="sha256:" + digest_char * 64,
    )


def _projection(
    *,
    lifecycle: CapabilityLifecycle = CapabilityLifecycle.ACTIVE,
    eligibility: CapabilityEligibility = CapabilityEligibility.ELIGIBLE,
) -> CapabilityGovernanceProjection:
    return CapabilityGovernanceProjection(
        target=_target(),
        maturity=CapabilityMaturity.VERIFIED,
        lifecycle=lifecycle,
        eligibility=eligibility,
        source="governance_event",  # type: ignore[arg-type]
        audience="admin_gray",  # type: ignore[arg-type]
    )


def _publication(
    signature_digest: str = "sha256:" + "c" * 64,
) -> CapabilityGovernanceEvent:
    return CapabilityGovernanceEvent(
        event_type="platform_published",
        idempotency_key="publish:sha256-a",
        target=_target(),
        maturity=CapabilityMaturity.VERIFIED,
        actor_id="admin-a",
        actor_role="admin",
        reason="发布：六步验证与签名全部通过",
        source_digest="sha256:" + "b" * 64,
        platform_digest="sha256:" + "a" * 64,
        audience="admin_gray",
        platform_validation_run_id="pfval_" + "a" * 20,
        signing_signature_digest=signature_digest,
        signing_public_key_sha256="d" * 64,
    )


class _StubSignatureVerifier:
    """替身验证器：按需抛错或返回不一致结果，并记录调用。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.error: Exception | None = None
        self.subject_digest = "sha256:" + "a" * 64
        self.signature_digest = "sha256:" + "c" * 64
        self.public_key_sha256 = "d" * 64

    def verify(self, pack, publication):
        self.calls.append((pack, publication))
        if self.error is not None:
            raise self.error
        return SigningStepResult(
            subject_digest=self.subject_digest,
            signature_digest=self.signature_digest,
            public_key_sha256=self.public_key_sha256,
            referrer_digests=("sha256:" + "e" * 64,),
        )


def _gate(verifier, calls: list[tuple[str, str]]):
    def hook(pack, reason):
        calls.append((pack.pack_id, reason))

    return CapabilityGovernanceRuntimeGate(
        projection_for=lambda pack: _projection(),
        platform_publication_for=lambda pack: _publication(),
        signature_verifier=verifier,
        auto_quarantine=hook,
    )


def _admin() -> CatalogActor:
    return CatalogActor(owner_id="admin-a", role="admin")


class TestS1GateHook:
    def test_verify_exception_triggers_hook(self) -> None:
        verifier = _StubSignatureVerifier()
        verifier.error = RuntimeError("cosign verify failed")
        calls: list[tuple[str, str]] = []
        gate = _gate(verifier, calls)
        try:
            gate.check_mount(_admin(), _platform_pack())
        except CapabilityMountGateRejected:
            pass
        assert calls == [("gray-python-table", "平台签名重验失败：RuntimeError")]

    def test_subject_digest_mismatch_triggers_hook(self) -> None:
        verifier = _StubSignatureVerifier()
        verifier.subject_digest = "sha256:" + "f" * 64
        calls: list[tuple[str, str]] = []
        gate = _gate(verifier, calls)
        try:
            gate.check_mount(_admin(), _platform_pack())
        except CapabilityMountGateRejected:
            pass
        assert len(calls) == 1
        assert "主体" in calls[0][1]

    def test_signature_digest_mismatch_triggers_hook(self) -> None:
        verifier = _StubSignatureVerifier()
        verifier.signature_digest = "sha256:" + "9" * 64
        calls: list[tuple[str, str]] = []
        gate = _gate(verifier, calls)
        try:
            gate.check_mount(_admin(), _platform_pack())
        except CapabilityMountGateRejected:
            pass
        assert len(calls) == 1
        assert "签名" in calls[0][1]

    def test_public_key_mismatch_triggers_hook(self) -> None:
        verifier = _StubSignatureVerifier()
        verifier.public_key_sha256 = "e" * 64
        calls: list[tuple[str, str]] = []
        gate = _gate(verifier, calls)
        try:
            gate.check_mount(_admin(), _platform_pack())
        except CapabilityMountGateRejected:
            pass
        assert len(calls) == 1
        assert "公钥" in calls[0][1]

    def test_verifier_missing_does_not_trigger_hook(self) -> None:
        """验证器未配置是配置问题而非篡改证据，不得触发自动隔离。"""
        calls: list[tuple[str, str]] = []
        gate = CapabilityGovernanceRuntimeGate(
            projection_for=lambda pack: _projection(),
            platform_publication_for=lambda pack: _publication(),
            signature_verifier=None,
            auto_quarantine=lambda pack, reason: calls.append((pack.pack_id, reason)),
        )
        try:
            gate.check_mount(_admin(), _platform_pack())
        except CapabilityMountGateRejected:
            pass
        assert calls == []

    def test_valid_signature_does_not_trigger_hook(self) -> None:
        calls: list[tuple[str, str]] = []
        gate = _gate(_StubSignatureVerifier(), calls)
        gate.check_mount(_admin(), _platform_pack())
        assert calls == []

    def test_hook_exception_preserves_reject_contract(self) -> None:
        """自动隔离失败不得改变门的拒绝契约：仍抛 CapabilityMountGateRejected。"""
        verifier = _StubSignatureVerifier()
        verifier.error = RuntimeError("cosign verify failed")

        def broken_hook(pack, reason):
            raise RuntimeError("数据库不可用")

        gate = CapabilityGovernanceRuntimeGate(
            projection_for=lambda pack: _projection(),
            platform_publication_for=lambda pack: _publication(),
            signature_verifier=verifier,
            auto_quarantine=broken_hook,
        )
        try:
            gate.check_mount(_admin(), _platform_pack())
        except CapabilityMountGateRejected as error:
            assert "签名" in error.reason
        else:
            raise AssertionError("签名失败必须拒绝装载")

    def test_default_none_hook_preserves_behaviour(self) -> None:
        """默认无钩子时装载门行为与 #13 完全一致：拒绝抛出、无副作用。"""
        verifier = _StubSignatureVerifier()
        verifier.error = RuntimeError("cosign verify failed")
        gate = CapabilityGovernanceRuntimeGate(
            projection_for=lambda pack: _projection(),
            platform_publication_for=lambda pack: _publication(),
            signature_verifier=verifier,
        )
        try:
            gate.check_mount(_admin(), _platform_pack())
        except CapabilityMountGateRejected as error:
            assert "签名" in error.reason
        else:
            raise AssertionError("签名失败必须拒绝装载")


def _governance_with_platform_pack(
    repository: InMemoryCapabilityGovernanceRepository,
) -> CapabilityGovernance:
    catalog_repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(catalog_repository)
    catalog_repository.save_pack(_platform_pack())
    governance = CapabilityGovernance(catalog, repository)
    repository.save_platform_event(_publication())
    return governance


def _save_platform_evidence(
    repository: InMemoryCapabilityGovernanceRepository,
    target: CapabilityGovernanceTarget,
) -> None:
    """恢复复查链要求的供应链证据（PASSED，漏洞库未过期）。"""
    from datetime import datetime, timezone

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


class TestS1AutoQuarantineService:
    def test_writes_quarantine_event_with_projection_snapshot(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance = _governance_with_platform_pack(repository)
        governance.auto_quarantine_for_signature_failure(
            _platform_pack(), "平台签名重验失败：RuntimeError"
        )
        events = repository.list_events(_target())
        quarantine = [e for e in events if e.event_type == "eligibility_changed"]
        assert len(quarantine) == 1
        event = quarantine[0]
        assert event.eligibility is CapabilityEligibility.QUARANTINED
        # 事件快照 = 写入时刻投影（lifecycle/maturity 不变）。
        assert event.lifecycle is CapabilityLifecycle.ACTIVE
        assert event.maturity is CapabilityMaturity.VERIFIED
        assert event.actor_id == "system"
        assert event.actor_role == "admin"

    def test_skips_when_already_quarantined(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance = _governance_with_platform_pack(repository)
        governance.auto_quarantine_for_signature_failure(
            _platform_pack(), "第一次失败"
        )
        governance.auto_quarantine_for_signature_failure(
            _platform_pack(), "第二次失败"
        )
        events = repository.list_events(_target())
        quarantine = [e for e in events if e.event_type == "eligibility_changed"]
        assert len(quarantine) == 1

    def test_snapshot_carries_deprecated_lifecycle(self) -> None:
        """弃用中验签失败：隔离事件快照必须携带 deprecated，不得冒充 active。"""
        repository = InMemoryCapabilityGovernanceRepository()
        governance = _governance_with_platform_pack(repository)
        repository.save_governance_event(
            CapabilityGovernanceEvent(
                event_type="lifecycle_changed",
                idempotency_key="gov:deprecate",
                target=_target(),
                maturity=CapabilityMaturity.VERIFIED,
                lifecycle=CapabilityLifecycle.DEPRECATED,
                eligibility=CapabilityEligibility.ELIGIBLE,
                actor_id="admin-a",
                actor_role="admin",
                reason="弃用：存在更安全的替代版本",
            )
        )
        governance.auto_quarantine_for_signature_failure(
            _platform_pack(), "弃用中签名失败"
        )
        events = repository.list_events(_target())
        quarantine = [e for e in events if e.event_type == "eligibility_changed"]
        assert len(quarantine) == 1
        assert quarantine[0].lifecycle is CapabilityLifecycle.DEPRECATED
        assert quarantine[0].eligibility is CapabilityEligibility.QUARANTINED

    def test_repeat_after_restore_uses_new_generation_key(self) -> None:
        """restore 解除隔离后再次失败必须能写新事件（代次幂等键）。"""
        repository = InMemoryCapabilityGovernanceRepository()
        governance = _governance_with_platform_pack(repository)
        governance.auto_quarantine_for_signature_failure(
            _platform_pack(), "第一次失败"
        )
        # 模拟 restore 解除隔离（#14 restore 命令已单独验证）。
        repository.save_governance_event(
            CapabilityGovernanceEvent(
                event_type="eligibility_changed",
                idempotency_key="gov:restore-1",
                target=_target(),
                maturity=CapabilityMaturity.VERIFIED,
                lifecycle=CapabilityLifecycle.ACTIVE,
                eligibility=CapabilityEligibility.ELIGIBLE,
                actor_id="admin-a",
                actor_role="admin",
                reason="恢复：签名验证已通过",
            )
        )
        governance.auto_quarantine_for_signature_failure(
            _platform_pack(), "恢复后再次失败"
        )
        events = repository.list_events(_target())
        quarantine = [
            e
            for e in events
            if e.event_type == "eligibility_changed"
            and e.eligibility is CapabilityEligibility.QUARANTINED
        ]
        assert len(quarantine) == 2
        # 两条隔离事件幂等键代次不同（同键会被唯一约束拒绝）。
        assert quarantine[0].idempotency_key != quarantine[1].idempotency_key


class TestPlatformRestoreRecheck:
    """#15 阶段 5 缺陷回归：平台能力 restore 复查链必须从平台验证运行表取证。"""

    def _green_platform_run(self) -> PlatformValidationRun:
        from src.capability_governance.models import (
            PlatformValidationEvidence,
            PlatformValidationRun,
            PlatformValidationStep,
            ValidationRunStatus,
            ValidationStepStatus,
        )

        evidence = tuple(
            PlatformValidationEvidence(
                step=step,
                status=ValidationStepStatus.PASSED,
                evidence_ref=f"evidence://platform/{step.value}",
                evidence_sha256="e" * 64,
                summary="步骤通过",
            )
            for step in PlatformValidationStep
        )
        return PlatformValidationRun(
            actor_id="admin-a",
            actor_role="admin",
            idempotency_key=f"candidate:{_target().digest}",
            target=_target(),
            status=ValidationRunStatus.SUCCEEDED,
            evidence=evidence,
            signing_signature_digest="sha256:" + "c" * 64,
            signing_public_key_sha256="d" * 64,
        )

    def test_restore_recheck_uses_platform_validation_runs(self) -> None:
        """平台能力隔离后 restore：复查链从平台验证运行表取到成功运行，不再误报。"""
        from src.capability_governance import (
            CapabilityGovernance,
        )

        repository = InMemoryCapabilityGovernanceRepository()
        governance = _governance_with_platform_pack(repository)
        # 注入平台验证运行（六步全绿 + 签名）——替代个人表记录。
        repository.create_platform_validation_run(self._green_platform_run())
        # 供应链证据（恢复复查链要求）
        _save_platform_evidence(repository, _target())
        # 隔离
        governance.auto_quarantine_for_signature_failure(
            _platform_pack(), "篡改演示"
        )
        # restore：复查链应通过（不再 validation_incomplete）
        outcome = governance.restore_pack(
            _admin(),
            pack_ref=CapabilityPackRef(
                pack_id="gray-python-table",
                version="1.0.0",
                digest="sha256:" + "a" * 64,
            ),
            reason="篡改演示后恢复",
            idempotency_key="gov:restore-test",
        )
        assert outcome.status != "rejected", outcome.gaps
