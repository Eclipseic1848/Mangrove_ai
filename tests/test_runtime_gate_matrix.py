# -*- coding: utf-8 -*-
"""AC-07-08 S2：运行时门实现的三轴/受众/签名判定矩阵。"""
from __future__ import annotations

import pytest

from src.capability_catalog import (
    CapabilityMountGateRejected,
)
from src.capability_catalog.models import CatalogActor
from src.capability_governance import (
    CapabilityEligibility,
    CapabilityGovernanceEvent,
    CapabilityGovernanceProjection,
    CapabilityGovernanceTarget,
    CapabilityLifecycle,
    CapabilityMaturity,
)
from src.capability_governance.oci_signing import SigningStepResult
from src.conversation_steering import (
    CapabilityMaturity as LegacyCapabilityMaturity,
    CapabilityPack,
    ProcedureScope,
)


def _pack(
    scope: ProcedureScope = ProcedureScope.PERSONAL,
    owner_id: str = "owner-a",
) -> CapabilityPack:
    is_platform = scope is ProcedureScope.PLATFORM
    return CapabilityPack(
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
        scope=scope,
        # 平台 Pack 模型要求 legacy VERIFIED；治理三轴以投影为权威。
        maturity=(
            LegacyCapabilityMaturity.VERIFIED
            if is_platform
            else LegacyCapabilityMaturity.DRAFT
        ),
        owner_id=owner_id if not is_platform else None,
    )


def _projection(
    pack: CapabilityPack,
    *,
    maturity: CapabilityMaturity = CapabilityMaturity.VERIFIED,
    lifecycle: CapabilityLifecycle = CapabilityLifecycle.ACTIVE,
    eligibility: CapabilityEligibility = CapabilityEligibility.ELIGIBLE,
    source: str = "governance_event",
    audience: str | None = None,
) -> CapabilityGovernanceProjection:
    return CapabilityGovernanceProjection(
        target=CapabilityGovernanceTarget(
            owner_id=pack.owner_id,
            scope=pack.scope,
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
        maturity=maturity,
        lifecycle=lifecycle,
        eligibility=eligibility,
        source=source,  # type: ignore[arg-type]
        audience=audience,  # type: ignore[arg-type]
    )


def _publication(
    pack: CapabilityPack,
    *,
    signature_digest: str = "sha256:" + "c" * 64,
    public_key_sha256: str = "d" * 64,
) -> CapabilityGovernanceEvent:
    # 发布事件受众固定 admin_gray（#12 模型约束）。
    return CapabilityGovernanceEvent(
        event_type="platform_published",
        idempotency_key="publish:sha256-a",
        target=CapabilityGovernanceTarget(
            owner_id=None,
            scope=ProcedureScope.PLATFORM,
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        ),
        maturity=CapabilityMaturity.VERIFIED,
        actor_id="admin-a",
        actor_role="admin",
        reason="发布：六步验证与签名全部通过",
        source_digest="sha256:" + "b" * 64,
        platform_digest=pack.digest,
        audience="admin_gray",
        platform_validation_run_id="pfval_" + "a" * 20,
        signing_signature_digest=signature_digest,
        signing_public_key_sha256=public_key_sha256,
    )


class _StubSignatureVerifier:
    """替身验证器：记录调用并返回可控结果。"""

    def __init__(self, result: SigningStepResult | None = None):
        self.calls: list[tuple] = []
        self.result = result or SigningStepResult(
            subject_digest="sha256:" + "a" * 64,
            signature_digest="sha256:" + "c" * 64,
            public_key_sha256="d" * 64,
            referrer_digests=("sha256:" + "e" * 64,),
        )
        self.error: Exception | None = None

    def verify(self, pack, publication):
        self.calls.append((pack, publication))
        if self.error is not None:
            raise self.error
        return self.result


def _gate(
    projection,
    publication=None,
    verifier: _StubSignatureVerifier | None = None,
):
    from src.capability_governance.runtime_gate import (
        CapabilityGovernanceRuntimeGate,
    )

    return CapabilityGovernanceRuntimeGate(
        projection_for=lambda pack: projection,
        platform_publication_for=lambda pack: publication,
        signature_verifier=verifier or _StubSignatureVerifier(),
    )


def _user(owner_id: str = "owner-a") -> CatalogActor:
    return CatalogActor(owner_id=owner_id, role="user")


def _admin() -> CatalogActor:
    return CatalogActor(owner_id="admin-a", role="admin")


class TestS2PersonalGate:
    def test_cross_owner_personal_pack_rejected(self) -> None:
        pack = _pack()
        gate = _gate(_projection(pack))
        with pytest.raises(CapabilityMountGateRejected):
            gate.check_mount(_user(owner_id="other-owner"), pack)

    def test_draft_personal_pack_rejected(self) -> None:
        pack = _pack()
        gate = _gate(
            _projection(pack, maturity=CapabilityMaturity.DRAFT)
        )
        with pytest.raises(CapabilityMountGateRejected):
            gate.check_mount(_user(), pack)

    def test_quarantined_personal_pack_rejected(self) -> None:
        pack = _pack()
        gate = _gate(
            _projection(
                pack, eligibility=CapabilityEligibility.QUARANTINED
            )
        )
        with pytest.raises(CapabilityMountGateRejected):
            gate.check_mount(_user(), pack)

    def test_revoked_personal_pack_rejected(self) -> None:
        pack = _pack()
        gate = _gate(
            _projection(pack, lifecycle=CapabilityLifecycle.REVOKED)
        )
        with pytest.raises(CapabilityMountGateRejected):
            gate.check_mount(_user(), pack)

    def test_deprecated_personal_pack_allowed(self) -> None:
        """AC3/Q4：deprecated 历史冻结任务允许恢复装载；新任务由
        列表过滤与冻结拦截挡住，不由装载门承担。"""
        pack = _pack()
        gate = _gate(
            _projection(
                pack, lifecycle=CapabilityLifecycle.DEPRECATED
            )
        )
        # 不抛异常即放行。
        gate.check_mount(_user(), pack)

    def test_verified_active_eligible_owned_pack_allowed(self) -> None:
        pack = _pack()
        gate = _gate(_projection(pack))
        # 不抛异常即放行。
        gate.check_mount(_user(), pack)


class TestS2PlatformGate:
    def test_legacy_platform_pack_without_publication_allowed(self) -> None:
        """Q2：无发布事件的平台 Pack 继续旧路径放行，直至 #17 切换。"""
        pack = _pack(scope=ProcedureScope.PLATFORM)
        gate = _gate(
            _projection(
                pack,
                source="legacy_compat",
                audience="admin_gray",
            ),
            publication=None,
        )
        # 旧路径维持 AC-06 现状，不因 #13 中断历史灰度任务。
        gate.check_mount(_user(), pack)

    def test_platform_pack_without_audience_rejected(self) -> None:
        """有发布事件但投影无受众是异常形态，防御性拒绝。"""
        pack = _pack(scope=ProcedureScope.PLATFORM)
        gate = _gate(
            _projection(pack, audience=None),
            publication=_publication(pack),
        )
        with pytest.raises(CapabilityMountGateRejected):
            gate.check_mount(_user(), pack)

    def test_draft_platform_pack_rejected(self) -> None:
        pack = _pack(scope=ProcedureScope.PLATFORM)
        gate = _gate(
            _projection(pack, maturity=CapabilityMaturity.DRAFT),
            publication=_publication(pack),
        )
        with pytest.raises(CapabilityMountGateRejected):
            gate.check_mount(_user(), pack)

    def test_quarantined_platform_pack_rejected(self) -> None:
        pack = _pack(scope=ProcedureScope.PLATFORM)
        gate = _gate(
            _projection(
                pack, eligibility=CapabilityEligibility.QUARANTINED
            ),
            publication=_publication(pack),
        )
        with pytest.raises(CapabilityMountGateRejected):
            gate.check_mount(_user(), pack)

    def test_revoked_platform_pack_rejected(self) -> None:
        pack = _pack(scope=ProcedureScope.PLATFORM)
        gate = _gate(
            _projection(pack, lifecycle=CapabilityLifecycle.REVOKED),
            publication=_publication(pack),
        )
        with pytest.raises(CapabilityMountGateRejected):
            gate.check_mount(_user(), pack)

    def test_deprecated_platform_pack_allowed(self) -> None:
        """与个人分支一致的 DEPRECATED 例外：历史恢复装载放行。"""
        pack = _pack(scope=ProcedureScope.PLATFORM)
        gate = _gate(
            _projection(
                pack,
                lifecycle=CapabilityLifecycle.DEPRECATED,
                audience="users",
            ),
            publication=_publication(pack),
        )
        # 不抛异常即放行。
        gate.check_mount(_user(), pack)

    def test_admin_gray_audience_rejects_regular_user(self) -> None:
        pack = _pack(scope=ProcedureScope.PLATFORM)
        gate = _gate(
            _projection(pack, audience="admin_gray"),
            publication=_publication(pack),
        )
        with pytest.raises(CapabilityMountGateRejected):
            gate.check_mount(_user(), pack)

    def test_admin_gray_audience_allows_admin_with_valid_signature(
        self,
    ) -> None:
        pack = _pack(scope=ProcedureScope.PLATFORM)
        verifier = _StubSignatureVerifier()
        gate = _gate(
            _projection(pack, audience="admin_gray"),
            publication=_publication(pack),
            verifier=verifier,
        )
        gate.check_mount(_admin(), pack)
        assert len(verifier.calls) == 1

    def test_users_audience_allows_regular_user_with_valid_signature(
        self,
    ) -> None:
        # users 受众只能由 audience_changed 事件产生；签名证据仍来自发布事件。
        pack = _pack(scope=ProcedureScope.PLATFORM)
        gate = _gate(
            _projection(pack, audience="users"),
            publication=_publication(pack),
        )
        gate.check_mount(_user(), pack)

    def test_signature_subject_mismatch_rejected(self) -> None:
        pack = _pack(scope=ProcedureScope.PLATFORM)
        verifier = _StubSignatureVerifier(
            result=SigningStepResult(
                subject_digest="sha256:" + "f" * 64,
                signature_digest="sha256:" + "c" * 64,
                public_key_sha256="d" * 64,
                referrer_digests=(),
            )
        )
        gate = _gate(
            _projection(pack, audience="admin_gray"),
            publication=_publication(pack),
            verifier=verifier,
        )
        with pytest.raises(CapabilityMountGateRejected):
            gate.check_mount(_admin(), pack)

    def test_signature_digest_mismatch_rejected(self) -> None:
        pack = _pack(scope=ProcedureScope.PLATFORM)
        verifier = _StubSignatureVerifier(
            result=SigningStepResult(
                subject_digest="sha256:" + "a" * 64,
                signature_digest="sha256:" + "9" * 64,
                public_key_sha256="d" * 64,
                referrer_digests=(),
            )
        )
        gate = _gate(
            _projection(pack, audience="admin_gray"),
            publication=_publication(pack),
            verifier=verifier,
        )
        with pytest.raises(CapabilityMountGateRejected):
            gate.check_mount(_admin(), pack)

    def test_public_key_mismatch_rejected(self) -> None:
        pack = _pack(scope=ProcedureScope.PLATFORM)
        verifier = _StubSignatureVerifier(
            result=SigningStepResult(
                subject_digest="sha256:" + "a" * 64,
                signature_digest="sha256:" + "c" * 64,
                public_key_sha256="9" * 64,
                referrer_digests=(),
            )
        )
        gate = _gate(
            _projection(pack, audience="admin_gray"),
            publication=_publication(pack),
            verifier=verifier,
        )
        with pytest.raises(CapabilityMountGateRejected):
            gate.check_mount(_admin(), pack)

    def test_verifier_failure_rejected(self) -> None:
        pack = _pack(scope=ProcedureScope.PLATFORM)
        verifier = _StubSignatureVerifier()
        verifier.error = RuntimeError("cosign verify 失败")
        gate = _gate(
            _projection(pack, audience="admin_gray"),
            publication=_publication(pack),
            verifier=verifier,
        )
        with pytest.raises(CapabilityMountGateRejected):
            gate.check_mount(_admin(), pack)
