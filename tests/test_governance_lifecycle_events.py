# -*- coding: utf-8 -*-
"""AC07-09 S1：生命周期/隔离/风险接受/推荐指针事件模型与 validator 矩阵。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.capability_governance import (
    CapabilityEligibility,
    CapabilityGovernanceEvent,
    CapabilityGovernanceTarget,
    CapabilityLifecycle,
    CapabilityMaturity,
)
from src.conversation_steering import ProcedureScope


def _target(
    scope: ProcedureScope = ProcedureScope.PLATFORM,
    digest_char: str = "a",
) -> CapabilityGovernanceTarget:
    return CapabilityGovernanceTarget(
        owner_id=None if scope is ProcedureScope.PLATFORM else "owner-a",
        scope=scope,
        pack_id="gray-python-table",
        version="1.0.0",
        digest="sha256:" + digest_char * 64,
    )


def _base(**overrides) -> dict:
    fields: dict = {
        "event_type": "lifecycle_changed",
        "idempotency_key": "gov:lifecycle",
        "target": _target(),
        "maturity": CapabilityMaturity.VERIFIED,
        "lifecycle": CapabilityLifecycle.DEPRECATED,
        "eligibility": CapabilityEligibility.ELIGIBLE,
        "actor_id": "admin-a",
        "actor_role": "admin",
        "reason": "弃用：存在更安全的替代版本",
    }
    fields.update(overrides)
    return fields


class TestLifecycleChangedValidator:
    def test_deprecated_is_valid(self) -> None:
        event = CapabilityGovernanceEvent(**_base())
        assert event.event_type == "lifecycle_changed"
        assert event.lifecycle is CapabilityLifecycle.DEPRECATED

    def test_revoked_is_valid(self) -> None:
        CapabilityGovernanceEvent(
            **_base(lifecycle=CapabilityLifecycle.REVOKED)
        )

    def test_restore_to_active_is_valid(self) -> None:
        CapabilityGovernanceEvent(
            **_base(lifecycle=CapabilityLifecycle.ACTIVE)
        )

    def test_draft_maturity_rejected(self) -> None:
        # 弃用/撤销只作用于已验证能力；draft 不需要生命周期命令。
        with pytest.raises(ValueError, match="verified"):
            CapabilityGovernanceEvent(
                **_base(maturity=CapabilityMaturity.DRAFT)
            )

    def test_quarantined_eligibility_snapshot_allowed(self) -> None:
        # 隔离中的包被弃用/撤销时，事件快照必须与当时投影一致，
        # 不得冒充 eligible（AC7 预期状态真实性）。
        event = CapabilityGovernanceEvent(
            **_base(eligibility=CapabilityEligibility.QUARANTINED)
        )
        assert event.eligibility is CapabilityEligibility.QUARANTINED

    def test_reason_required(self) -> None:
        with pytest.raises(ValueError, match="原因"):
            CapabilityGovernanceEvent(**_base(reason=None))

    def test_audit_fields_rejected(self) -> None:
        with pytest.raises(ValueError, match="审计"):
            CapabilityGovernanceEvent(
                **_base(task_id="workspace-a", revision=1)
            )

    def test_platform_fields_rejected(self) -> None:
        with pytest.raises(ValueError, match="平台"):
            CapabilityGovernanceEvent(
                **_base(
                    platform_digest="sha256:" + "b" * 64,
                    signing_signature_digest="sha256:" + "c" * 64,
                )
            )


class TestEligibilityChangedValidator:
    def _event(self, **overrides) -> CapabilityGovernanceEvent:
        fields: dict = {
            "event_type": "eligibility_changed",
            "idempotency_key": "gov:eligibility",
            "target": _target(),
            "maturity": CapabilityMaturity.VERIFIED,
            "lifecycle": CapabilityLifecycle.ACTIVE,
            "eligibility": CapabilityEligibility.QUARANTINED,
            "actor_id": "admin-a",
            "actor_role": "admin",
            "reason": "隔离：安全扫描发现 Critical 漏洞",
        }
        fields.update(overrides)
        return CapabilityGovernanceEvent(**fields)

    def test_quarantine_is_valid(self) -> None:
        event = self._event()
        assert event.eligibility is CapabilityEligibility.QUARANTINED

    def test_unquarantine_is_valid(self) -> None:
        self._event(eligibility=CapabilityEligibility.ELIGIBLE)

    def test_revoked_lifecycle_rejected(self) -> None:
        # 已撤销的能力不需要隔离语义；恢复走 lifecycle_changed。
        with pytest.raises(ValueError, match="active 或 deprecated"):
            self._event(lifecycle=CapabilityLifecycle.REVOKED)

    def test_draft_maturity_rejected(self) -> None:
        with pytest.raises(ValueError, match="verified"):
            self._event(maturity=CapabilityMaturity.DRAFT)

    def test_reason_required(self) -> None:
        with pytest.raises(ValueError, match="原因"):
            self._event(reason=None)


class TestRiskAcceptedValidator:
    def _event(self, **overrides) -> CapabilityGovernanceEvent:
        fields: dict = {
            "event_type": "risk_accepted",
            "idempotency_key": "gov:risk",
            "target": _target(),
            "maturity": CapabilityMaturity.VERIFIED,
            "lifecycle": CapabilityLifecycle.ACTIVE,
            "eligibility": CapabilityEligibility.ELIGIBLE,
            "actor_id": "admin-a",
            "actor_role": "admin",
            "reason": "风险接受：无修复且路径不可达的 High",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=30),
            "finding_ref": "capval_a1b2c3d4e5f6a1b2c3d4",
        }
        fields.update(overrides)
        return CapabilityGovernanceEvent(**fields)

    def test_valid(self) -> None:
        event = self._event()
        assert event.expires_at is not None
        assert event.finding_ref == "capval_a1b2c3d4e5f6a1b2c3d4"

    def test_expires_at_required(self) -> None:
        with pytest.raises(ValueError, match="到期"):
            self._event(expires_at=None)

    def test_finding_ref_required(self) -> None:
        with pytest.raises(ValueError, match="证据"):
            self._event(finding_ref=None)

    def test_non_eligible_rejected(self) -> None:
        # 接受发生在隔离之后；事件本身表达"限期恢复 eligible"。
        with pytest.raises(ValueError, match="eligible"):
            self._event(eligibility=CapabilityEligibility.QUARANTINED)


class TestRecommendationChangedValidator:
    def _event(self, **overrides) -> CapabilityGovernanceEvent:
        fields: dict = {
            "event_type": "recommendation_changed",
            "idempotency_key": "gov:recommend",
            "target": _target(digest_char="b"),
            "maturity": CapabilityMaturity.VERIFIED,
            "lifecycle": CapabilityLifecycle.ACTIVE,
            "eligibility": CapabilityEligibility.ELIGIBLE,
            "actor_id": "admin-a",
            "actor_role": "admin",
            "reason": "回滚：新版本存在阻断问题，推荐切回 1.0.0",
            "recommended_version": "1.0.0",
        }
        fields.update(overrides)
        return CapabilityGovernanceEvent(**fields)

    def test_valid(self) -> None:
        event = self._event()
        assert event.recommended_version == "1.0.0"

    def test_recommended_version_required(self) -> None:
        with pytest.raises(ValueError, match="推荐版本"):
            self._event(recommended_version=None)

    def test_personal_scope_rejected(self) -> None:
        # 推荐指针仅平台 Pack（Q4A）。
        with pytest.raises(ValueError, match="平台"):
            self._event(
                target=_target(scope=ProcedureScope.PERSONAL)
            )

    def test_reason_required(self) -> None:
        with pytest.raises(ValueError, match="原因"):
            self._event(reason=None)
