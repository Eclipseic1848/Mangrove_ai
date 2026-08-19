# -*- coding: utf-8 -*-
"""AC07-10 S2：手动重扫命令（证据追加 + 自动隔离触发矩阵）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.capability_catalog import (
    CapabilityCatalog,
    CapabilityPackRef,
    InMemoryCapabilityCatalogRepository,
)
from src.capability_catalog.models import CatalogActor
from src.capability_governance import (
    CapabilityEligibility,
    CapabilityGovernance,
    CapabilityGovernanceEvent,
    CapabilityGovernanceTarget,
    CapabilityLifecycle,
    CapabilityMaturity,
    CapabilitySupplyChainEvidence,
    InMemoryCapabilityGovernanceRepository,
    SupplyChainEvidenceStatus,
    TrivyDatabaseMetadata,
)
from src.conversation_steering import (
    CapabilityMaturity as LegacyCapabilityMaturity,
    CapabilityPack,
    ProcedureScope,
)


def _target(digest_char: str = "a") -> CapabilityGovernanceTarget:
    return CapabilityGovernanceTarget(
        owner_id=None,
        scope=ProcedureScope.PLATFORM,
        pack_id="gray-python-table",
        version="1.0.0",
        digest="sha256:" + digest_char * 64,
    )


def _platform_pack(digest_char: str = "a") -> CapabilityPack:
    return CapabilityPack(
        pack_id="gray-python-table",
        version="1.0.0",
        digest="sha256:" + digest_char * 64,
        scope=ProcedureScope.PLATFORM,
        maturity=LegacyCapabilityMaturity.VERIFIED,
        owner_id=None,
    )


def _publication() -> CapabilityGovernanceEvent:
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
        signing_signature_digest="sha256:" + "c" * 64,
        signing_public_key_sha256="d" * 64,
    )


def _evidence(
    target: CapabilityGovernanceTarget,
    *,
    passed: bool = True,
    blockers: tuple[str, ...] = (),
    evidence_char: str = "b",
    occurred_at: datetime | None = None,
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
        critical_count=1 if "critical_vulnerability" in blockers else 0,
        fixable_high_count=1 if "fixable_high_vulnerability" in blockers else 0,
        misconfiguration_failure_count=(
            1 if "misconfiguration_failure" in blockers else 0
        ),
        trivy_version="0.70.0",
        trivy_config_sha256="c" * 64,
        trivy_result_sha256="d" * 64,
        trivy_database=TrivyDatabaseMetadata(
            version=2, updated_at=datetime.now(timezone.utc)
        ),
        syft_version="1.50.0",
        syft_json_sha256="e" * 64,
        cyclonedx_json_sha256="f" * 64,
        cyclonedx_spec_version="1.6",
        occurred_at=occurred_at or datetime.now(timezone.utc),
    )


def _governance(
    repository: InMemoryCapabilityGovernanceRepository,
    *,
    published: bool = True,
    collector=None,
    materialize=None,
) -> CapabilityGovernance:
    catalog_repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(catalog_repository)
    catalog_repository.save_pack(_platform_pack())
    governance = CapabilityGovernance(
        catalog,
        repository,
        platform_materialize=materialize or (lambda target: Path(".")),
        supply_chain_collector=collector,
    )
    if published:
        repository.save_platform_event(_publication())
    return governance


def _ref() -> CapabilityPackRef:
    return CapabilityPackRef(
        pack_id="gray-python-table",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
    )


def _admin() -> CatalogActor:
    return CatalogActor(owner_id="admin-a", role="admin")


def _quarantine_events(
    repository: InMemoryCapabilityGovernanceRepository,
) -> list[CapabilityGovernanceEvent]:
    return [
        event
        for event in repository.list_events(_target())
        if event.event_type == "eligibility_changed"
        and event.eligibility is CapabilityEligibility.QUARANTINED
    ]


class TestS2RescanMatrix:
    def test_blocked_rescan_auto_quarantines(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        collected: list[tuple] = []

        def collector(target, subject_root):
            collected.append((target, subject_root))
            return _evidence(
                target,
                passed=False,
                blockers=("critical_vulnerability",),
                evidence_char="c",
            )

        governance = _governance(repository, collector=collector)
        outcome = governance.rescan_supply_chain(
            _admin(), pack_ref=_ref(), reason="定期重扫", idempotency_key="rescan:run-1"
        )
        assert outcome.status == "applied"
        quarantine = _quarantine_events(repository)
        assert len(quarantine) == 1
        event = quarantine[0]
        assert event.eligibility is CapabilityEligibility.QUARANTINED
        assert event.lifecycle is CapabilityLifecycle.ACTIVE
        assert event.actor_id == "system"
        # 物化目录确实传给采集器（真实 Seam 接通）。
        assert len(collected) == 1
        assert isinstance(collected[0][1], Path)

    def test_passed_rescan_no_quarantine(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance = _governance(
            repository,
            collector=lambda target, subject_root: _evidence(
                target, evidence_char="c"
            ),
        )
        outcome = governance.rescan_supply_chain(
            _admin(), pack_ref=_ref(), reason="定期重扫", idempotency_key="rescan:run-1"
        )
        assert outcome.status == "applied"
        assert _quarantine_events(repository) == []
        # 新证据已保存（追加，不覆盖）。
        assert repository.get_latest_supply_chain_evidence(
            _target()
        ) is not None

    def test_already_quarantined_only_updates_evidence(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        # 每次采集生成独立 evidence_id（真实采集器行为）。
        chars = iter("cd")

        def collector(target, subject_root):
            char = next(chars)
            # 第一次返回 blocker（触发隔离），第二次返回 passed（修复后重扫）。
            if char == "c":
                return _evidence(
                    target,
                    passed=False,
                    blockers=("critical_vulnerability",),
                    evidence_char=char,
                )
            return _evidence(target, evidence_char=char)

        governance = _governance(repository, collector=collector)
        # 先制造一次隔离（首次重扫发现 blocker）。
        first = governance.rescan_supply_chain(
            _admin(), pack_ref=_ref(), reason="定期重扫", idempotency_key="rescan:run-1"
        )
        assert first.status == "applied"
        # 修复后重扫通过：证据更新，隔离状态不变（解除隔离是独立命令）。
        outcome = governance.rescan_supply_chain(
            _admin(), pack_ref=_ref(), reason="修复后重扫", idempotency_key="rescan:run-2"
        )
        assert outcome.status == "applied"
        assert len(_quarantine_events(repository)) == 1

    def test_idempotent_replay_returns_existing(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        calls: list[tuple] = []

        def collector(target, subject_root):
            calls.append((target, subject_root))
            return _evidence(target, evidence_char="c")

        governance = _governance(repository, collector=collector)
        first = governance.rescan_supply_chain(
            _admin(), pack_ref=_ref(), reason="定期重扫", idempotency_key="rescan:run-1"
        )
        second = governance.rescan_supply_chain(
            _admin(), pack_ref=_ref(), reason="定期重扫", idempotency_key="rescan:run-1"
        )
        assert first.status == "applied"
        assert second.status == "already_applied"
        # 幂等命中先于一切检查：不重复采集。
        assert len(calls) == 1

    def test_non_admin_rejected(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance = _governance(repository)
        with pytest.raises(PermissionError, match="管理员"):
            governance.rescan_supply_chain(
                CatalogActor(owner_id="user-a", role="user"),
                pack_ref=_ref(),
                reason="定期重扫", idempotency_key="rescan:run-1",
            )

    def test_unpublished_platform_pack_rejected(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance = _governance(repository, published=False)
        outcome = governance.rescan_supply_chain(
            _admin(), pack_ref=_ref(), reason="定期重扫", idempotency_key="rescan:run-1"
        )
        assert outcome.status == "rejected"
        assert "not_platform_published" in outcome.gaps

    def test_personal_pack_rejected(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        catalog_repository = InMemoryCapabilityCatalogRepository()
        catalog = CapabilityCatalog(catalog_repository)
        catalog_repository.save_pack(
            CapabilityPack(
                pack_id="private-a",
                version="1.0.0",
                digest="sha256:" + "b" * 64,
                scope=ProcedureScope.PERSONAL,
                maturity=LegacyCapabilityMaturity.DRAFT,
                owner_id="owner-a",
            )
        )
        governance = CapabilityGovernance(catalog, repository)
        outcome = governance.rescan_supply_chain(
            _admin(),
            pack_ref=CapabilityPackRef(
                pack_id="private-a",
                version="1.0.0",
                digest="sha256:" + "b" * 64,
            ),
            reason="定期重扫", idempotency_key="rescan:run-1",
        )
        assert outcome.status == "rejected"
        assert "not_platform_pack" in outcome.gaps

    def test_not_configured_rejected(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance = _governance(repository, collector=None)
        outcome = governance.rescan_supply_chain(
            _admin(), pack_ref=_ref(), reason="定期重扫", idempotency_key="rescan:run-1"
        )
        assert outcome.status == "rejected"
        assert "rescan_not_configured" in outcome.gaps

    def test_evidence_append_keeps_old_row(self) -> None:
        """重扫追加新证据行，旧证据不覆盖（不覆盖旧证据原则）。"""
        repository = InMemoryCapabilityGovernanceRepository()
        target = _target()
        old = _evidence(target, evidence_char="b")
        repository.save_supply_chain_evidence(old)
        governance = _governance(
            repository,
            collector=lambda t, root: _evidence(
                t, evidence_char="c", occurred_at=old.occurred_at + timedelta(minutes=1)
            ),
        )
        outcome = governance.rescan_supply_chain(
            _admin(), pack_ref=_ref(), reason="定期重扫", idempotency_key="rescan:run-1"
        )
        assert outcome.status == "applied"
        latest = repository.get_latest_supply_chain_evidence(target)
        assert latest is not None
        assert latest.evidence_id == "supply_" + "c" * 20
        # 旧证据行按独立 evidence_id 保留：再次保存同对象必须幂等返回
        # 原对象（若实现走了覆盖路径会抛「不可覆盖」异常）。
        assert repository.save_supply_chain_evidence(old) is old
        assert len(repository._supply_chain_evidence) == 2


class TestS4SnapshotConsistency:
    def test_rescan_completed_snapshot_matches_quarantined_projection(
        self,
    ) -> None:
        """触发隔离时，重扫事件的快照必须等于写入时刻投影（quarantined）。"""
        repository = InMemoryCapabilityGovernanceRepository()
        governance = _governance(
            repository,
            collector=lambda t, root: _evidence(
                t,
                passed=False,
                blockers=("critical_vulnerability",),
                evidence_char="c",
            ),
        )
        outcome = governance.rescan_supply_chain(
            _admin(), pack_ref=_ref(), reason="定期重扫", idempotency_key="rescan:run-1"
        )
        assert outcome.status == "applied"
        rescan_events = [
            event
            for event in repository.list_events(_target())
            if event.event_type == "rescan_completed"
        ]
        assert len(rescan_events) == 1
        snapshot = rescan_events[0]
        assert snapshot.eligibility is CapabilityEligibility.QUARANTINED
        assert snapshot.lifecycle is CapabilityLifecycle.ACTIVE
        assert snapshot.maturity is CapabilityMaturity.VERIFIED
        # 投影折叠对重扫事件零污染（投影权威 = 隔离事件）。
        pack = governance._catalog.resolve_pack(
            _admin(), _ref().pack_id, _ref().version
        )
        assert pack is not None
        projection = governance.runtime_projection_for_pack(pack)
        assert projection.eligibility is CapabilityEligibility.QUARANTINED

    def test_quarantine_event_precedes_rescan_event(self) -> None:
        """隔离事件先于重扫事件写入（#14 多事件非原子教训的写序保证）。"""
        repository = InMemoryCapabilityGovernanceRepository()
        governance = _governance(
            repository,
            collector=lambda t, root: _evidence(
                t,
                passed=False,
                blockers=("critical_vulnerability",),
                evidence_char="c",
            ),
        )
        governance.rescan_supply_chain(
            _admin(), pack_ref=_ref(), reason="定期重扫", idempotency_key="rescan:run-1"
        )
        events = repository.list_events(_target())
        quarantine = [
            e
            for e in events
            if e.event_type == "eligibility_changed"
            and e.eligibility is CapabilityEligibility.QUARANTINED
        ][0]
        rescan = [
            e for e in events if e.event_type == "rescan_completed"
        ][0]
        assert quarantine.occurred_at <= rescan.occurred_at

    def test_passed_rescan_snapshot_carries_deprecated_projection(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance = _governance(
            repository,
            collector=lambda t, root: _evidence(t, evidence_char="c"),
        )
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
        outcome = governance.rescan_supply_chain(
            _admin(), pack_ref=_ref(), reason="弃用中重扫", idempotency_key="rescan:run-1"
        )
        assert outcome.status == "applied"
        rescan_events = [
            event
            for event in repository.list_events(_target())
            if event.event_type == "rescan_completed"
        ]
        assert len(rescan_events) == 1
        assert rescan_events[0].lifecycle is CapabilityLifecycle.DEPRECATED
        assert rescan_events[0].eligibility is CapabilityEligibility.ELIGIBLE

    def test_collector_contract_accepts_bound_collect_method(self) -> None:
        """真实装配件传采集服务的绑定 collect 方法（可调用契约）。"""

        class _Service:
            def collect(self, target, subject_root):
                return _evidence(target, evidence_char="e")

        service = _Service()
        repository = InMemoryCapabilityGovernanceRepository()
        governance = _governance(repository, collector=service.collect)
        outcome = governance.rescan_supply_chain(
            _admin(), pack_ref=_ref(), reason="定期重扫", idempotency_key="rescan:run-1"
        )
        assert outcome.status == "applied"

    def test_crash_window_replay_backfills_rescan_event(self) -> None:
        """崩溃窗口：隔离事件已写而重扫事件缺失 → 重放按投影补写留痕。"""
        repository = InMemoryCapabilityGovernanceRepository()
        calls: list[tuple] = []

        def collector(target, subject_root):
            calls.append((target, subject_root))
            return _evidence(target, evidence_char="d")

        governance = _governance(repository, collector=collector)
        # 模拟上次重扫在写隔离后崩溃：证据行 + 隔离事件（同幂等键）在库。
        evidence = _evidence(
            _target(),
            passed=False,
            blockers=("critical_vulnerability",),
            evidence_char="c",
        )
        repository.save_supply_chain_evidence(evidence)
        repository.save_governance_event(
            CapabilityGovernanceEvent(
                event_type="eligibility_changed",
                idempotency_key="rescan:run-1",
                target=_target(),
                maturity=CapabilityMaturity.VERIFIED,
                lifecycle=CapabilityLifecycle.ACTIVE,
                eligibility=CapabilityEligibility.QUARANTINED,
                actor_id="system",
                actor_role="admin",
                reason="自动隔离（供应链重扫发现硬门）：critical_vulnerability",
            )
        )
        outcome = governance.rescan_supply_chain(
            _admin(), pack_ref=_ref(), reason="定期重扫", idempotency_key="rescan:run-1"
        )
        assert outcome.status == "already_applied"
        # 不重复采集（幂等优先）。
        assert calls == []
        rescan_events = [
            event
            for event in repository.list_events(_target())
            if event.event_type == "rescan_completed"
        ]
        assert len(rescan_events) == 1
        assert (
            rescan_events[0].source_supply_chain_evidence_id
            == evidence.evidence_id
        )
        # 补写事件的快照 = 补写时刻投影（quarantined）。
        assert rescan_events[0].eligibility is CapabilityEligibility.QUARANTINED

