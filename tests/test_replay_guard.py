# -*- coding: utf-8 -*-
"""AC-07-08 B6：验证重放前投影检查（隔离/撤销拒绝；draft 验证目标允许）。"""
from __future__ import annotations

import pytest

from src.api.capability_governance_runtime import _replay_guard
from src.capability_catalog import (
    CapabilityCatalog,
    CatalogActor,
    InMemoryCapabilityCatalogRepository,
)
from src.capability_governance import (
    CapabilityGovernance,
    CapabilityEligibility,
    CapabilityGovernanceProjection,
    CapabilityGovernanceTarget,
    CapabilityLifecycle,
    CapabilityMaturity,
    CapabilityValidationRun,
    InMemoryCapabilityGovernanceRepository,
    ValidationTaskRef,
)
from src.capability_governance.validation_runtime import PiTaskReplayRunner
from src.conversation_steering import (
    CapabilityMaturity as LegacyCapabilityMaturity,
    CapabilityPack,
    ProcedureScope,
)


def _target(digest_char: str = "a") -> CapabilityGovernanceTarget:
    return CapabilityGovernanceTarget(
        owner_id="owner-a",
        scope=ProcedureScope.PERSONAL,
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + digest_char * 64,
    )


def _run(target: CapabilityGovernanceTarget) -> CapabilityValidationRun:
    return CapabilityValidationRun(
        owner_id="owner-a",
        target=target,
        actor_id="owner-a",
        actor_role="user",
        idempotency_key="replay:guard",
        task_ref=ValidationTaskRef(
            task_id="workspace-guard",
            revision=1,
            source_snapshot_sha256="a" * 64,
            input_sha256="b" * 64,
            output_sha256="c" * 64,
            capability_digest=target.digest,
            authorization_id="auth_" + "a" * 20,
        ),
    )


def _catalog_with_pack():
    catalog_repository = InMemoryCapabilityCatalogRepository()
    catalog = CapabilityCatalog(catalog_repository)
    pack = CapabilityPack(
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
        scope=ProcedureScope.PERSONAL,
        maturity=LegacyCapabilityMaturity.DRAFT,
        owner_id="owner-a",
    )
    catalog.register_pack(CatalogActor(owner_id="owner-a", role="user"), pack)
    return catalog


class TestReplayGuardFactory:
    def test_draft_target_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """验证目标 draft（未晋升）是验证的正常输入，允许重放。"""
        catalog = _catalog_with_pack()
        governance = CapabilityGovernance(
            catalog, InMemoryCapabilityGovernanceRepository()
        )
        guard = _replay_guard(catalog, governance)
        # 无事件 → legacy draft 投影 → 放行（不抛异常）。
        guard(_run(_target()))

    def test_revoked_target_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        catalog = _catalog_with_pack()
        governance = CapabilityGovernance(
            catalog, InMemoryCapabilityGovernanceRepository()
        )
        target = _target()
        projection = CapabilityGovernanceProjection(
            target=target,
            maturity=CapabilityMaturity.VERIFIED,
            lifecycle=CapabilityLifecycle.REVOKED,
            eligibility=CapabilityEligibility.ELIGIBLE,
            source="governance_event",
            audience=None,
        )
        # revoked 事件形态属于 #14；B6 只验证判断逻辑，替换投影输出。
        monkeypatch.setattr(
            governance,
            "runtime_projection_for_pack",
            lambda pack: projection,
        )
        guard = _replay_guard(catalog, governance)
        with pytest.raises(RuntimeError, match="撤销"):
            guard(_run(target))

    def test_quarantined_target_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        catalog = _catalog_with_pack()
        governance = CapabilityGovernance(
            catalog, InMemoryCapabilityGovernanceRepository()
        )
        target = _target()
        projection = CapabilityGovernanceProjection(
            target=target,
            maturity=CapabilityMaturity.VERIFIED,
            lifecycle=CapabilityLifecycle.ACTIVE,
            eligibility=CapabilityEligibility.QUARANTINED,
            source="governance_event",
            audience=None,
        )
        monkeypatch.setattr(
            governance,
            "runtime_projection_for_pack",
            lambda pack: projection,
        )
        guard = _replay_guard(catalog, governance)
        with pytest.raises(RuntimeError, match="隔离"):
            guard(_run(target))


class _FakeTaskResolver:
    def load_replay_request(self, actor, target, task_ref):
        raise AssertionError("guard 拒绝后不得进入重放请求加载")


class TestReplayRunnerGuardSeam:
    def test_guard_rejection_blocks_replay_before_request_load(
        self, tmp_path
    ) -> None:
        def raising_guard(run):
            raise RuntimeError("验证目标能力已被撤销")

        runner = PiTaskReplayRunner(
            task_resolver=_FakeTaskResolver(),
            capability_mounts=lambda *args: (),
            execution_root=tmp_path,
            cancel_requested=lambda: False,
            replay_guard=raising_guard,
        )
        with pytest.raises(RuntimeError, match="撤销"):
            runner(_run(_target()))

    def test_guard_absent_keeps_existing_behavior(self, tmp_path) -> None:
        """未装配 guard 的调用方（测试替身/独立进程）保持原路径。"""
        runner = PiTaskReplayRunner(
            task_resolver=_FakeTaskResolver(),
            capability_mounts=lambda *args: (),
            execution_root=tmp_path,
            cancel_requested=lambda: False,
        )
        # 无 guard 时直接进入请求加载（Fake 以 AssertionError 表达被调用）。
        with pytest.raises(AssertionError):
            runner(_run(_target()))
