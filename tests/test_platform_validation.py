# -*- coding: utf-8 -*-
"""AC-07-07 S4：平台验证六步执行器（步骤证据、失败关闭、供应链绑定平台 digest）。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.capability_governance import (
    CapabilityGovernanceTarget,
    PlatformValidationEvidence,
    PlatformValidationRun,
    PlatformValidationStep,
    ValidationRunStatus,
    ValidationStepStatus,
)
from src.capability_governance.platform_validation import (
    LockedPlatformValidationExecutor,
    PlatformStepRunner,
)
from src.conversation_steering import ProcedureScope


def _platform_target(
    digest_char: str = "b",
) -> CapabilityGovernanceTarget:
    return CapabilityGovernanceTarget(
        owner_id=None,
        scope=ProcedureScope.PLATFORM,
        pack_id="python-table-summary",
        version="1.0.0",
        digest="sha256:" + digest_char * 64,
    )


def _run(target: CapabilityGovernanceTarget) -> PlatformValidationRun:
    return PlatformValidationRun(
        run_id="pfval_" + "a" * 20,
        actor_id="admin-a",
        actor_role="admin",
        idempotency_key="pfval-one",
        target=target,
        status=ValidationRunStatus.RUNNING,
    )


def _passed(step: PlatformValidationStep, run_id: str = "pfval_a") -> PlatformValidationEvidence:
    return PlatformValidationEvidence(
        step=step,
        status=ValidationStepStatus.PASSED,
        evidence_ref=f"evidence://platform/{run_id}/{step.value}",
        evidence_sha256="a" * 64,
        summary=f"{step.value} 已通过",
    )


class _Recorder:
    """替身步骤执行器：记录调用并把返回值透传为平台验证证据。"""

    def __init__(self, name: str):
        self.name = name
        self.calls: list[Path] = []
        self.raise_on: Exception | None = None

    def run(self, subject: Path) -> PlatformValidationEvidence:
        self.calls.append(subject)
        if self.raise_on is not None:
            raise self.raise_on
        return _passed(PlatformValidationStep(self.name))


class _FakeSupplyChain:
    """替身供应链服务：只暴露 collect，记录目标与主体。"""

    def __init__(self):
        self.calls: list[tuple[CapabilityGovernanceTarget, Path]] = []
        self.error: Exception | None = None

    def collect(
        self, target: CapabilityGovernanceTarget, subject_root: str | Path
    ):
        self.calls.append((target, Path(subject_root)))
        if self.error is not None:
            raise self.error
        from src.capability_governance import (
            CapabilitySupplyChainEvidence,
            SupplyChainEvidenceStatus,
            TrivyDatabaseMetadata,
        )

        return CapabilitySupplyChainEvidence(
            evidence_id="supply_" + "1" * 20,
            target=target,
            subject_digest=target.digest,
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
                updated_at=datetime.now(timezone.utc),
            ),
            syft_version="1.50.0",
            syft_json_sha256="4" * 64,
            cyclonedx_json_sha256="5" * 64,
            cyclonedx_spec_version="1.6",
            occurred_at=datetime.now(timezone.utc),
        )


def _executor(
    subject_root: Path,
    *,
    smoke: PlatformStepRunner,
    fail_closed: PlatformStepRunner,
    supply_chain: _FakeSupplyChain,
    mount_probe: PlatformStepRunner,
    verifier: PlatformStepRunner,
) -> LockedPlatformValidationExecutor:
    return LockedPlatformValidationExecutor(
        materialize=lambda digest: subject_root,
        smoke=smoke,
        fail_closed=fail_closed,
        supply_chain=supply_chain,
        mount_probe=mount_probe,
        verifier=verifier,
    )


class TestS4PlatformValidationExecutor:
    def test_smoke_and_fail_closed_forward_to_runners(self, tmp_path) -> None:
        smoke = _Recorder("synthetic_smoke")
        fail_closed = _Recorder("fail_closed")
        mount = _Recorder("mount_probe")
        verifier = _Recorder("independent_verifier")
        executor = _executor(
            tmp_path,
            smoke=smoke,
            fail_closed=fail_closed,
            supply_chain=_FakeSupplyChain(),
            mount_probe=mount,
            verifier=verifier,
        )
        run = _run(_platform_target())
        smoke_evidence = executor.execute(
            run, PlatformValidationStep.SYNTHETIC_SMOKE
        )
        assert smoke_evidence.step is PlatformValidationStep.SYNTHETIC_SMOKE
        assert smoke_evidence.status is ValidationStepStatus.PASSED
        assert smoke.calls == [tmp_path]
        fail_evidence = executor.execute(
            run, PlatformValidationStep.FAIL_CLOSED
        )
        assert fail_evidence.step is PlatformValidationStep.FAIL_CLOSED
        assert fail_closed.calls == [tmp_path]

    def test_trivy_binds_platform_target_and_subject(self, tmp_path) -> None:
        supply = _FakeSupplyChain()
        executor = _executor(
            tmp_path,
            smoke=_Recorder("synthetic_smoke"),
            fail_closed=_Recorder("fail_closed"),
            supply_chain=supply,
            mount_probe=_Recorder("mount_probe"),
            verifier=_Recorder("independent_verifier"),
        )
        target = _platform_target()
        evidence = executor.execute(
            _run(target), PlatformValidationStep.TRIVY
        )
        assert evidence.status is ValidationStepStatus.PASSED
        assert len(supply.calls) == 1
        called_target, called_subject = supply.calls[0]
        assert called_target == target
        assert called_target.digest == target.digest
        assert called_subject == tmp_path

    def test_syft_derives_from_cached_collection(self, tmp_path) -> None:
        supply = _FakeSupplyChain()
        executor = _executor(
            tmp_path,
            smoke=_Recorder("synthetic_smoke"),
            fail_closed=_Recorder("fail_closed"),
            supply_chain=supply,
            mount_probe=_Recorder("mount_probe"),
            verifier=_Recorder("independent_verifier"),
        )
        run = _run(_platform_target())
        executor.execute(run, PlatformValidationStep.TRIVY)
        syft_evidence = executor.execute(
            run, PlatformValidationStep.SYFT
        )
        assert syft_evidence.step is PlatformValidationStep.SYFT
        assert syft_evidence.status is ValidationStepStatus.PASSED
        # 供应链扫描只执行一次；syft 步从同一证据派生。
        assert len(supply.calls) == 1

    def test_syft_requires_prior_trivy_collection(self, tmp_path) -> None:
        executor = _executor(
            tmp_path,
            smoke=_Recorder("synthetic_smoke"),
            fail_closed=_Recorder("fail_closed"),
            supply_chain=_FakeSupplyChain(),
            mount_probe=_Recorder("mount_probe"),
            verifier=_Recorder("independent_verifier"),
        )
        with pytest.raises(RuntimeError):
            executor.execute(
                _run(_platform_target()), PlatformValidationStep.SYFT
            )

    def test_supply_chain_blocked_becomes_failed_evidence(self, tmp_path) -> None:
        supply = _FakeSupplyChain()
        supply.error = ValueError("Trivy 数据库不可判定")
        executor = _executor(
            tmp_path,
            smoke=_Recorder("synthetic_smoke"),
            fail_closed=_Recorder("fail_closed"),
            supply_chain=supply,
            mount_probe=_Recorder("mount_probe"),
            verifier=_Recorder("independent_verifier"),
        )
        evidence = executor.execute(
            _run(_platform_target()), PlatformValidationStep.TRIVY
        )
        assert evidence.status is ValidationStepStatus.FAILED
        assert "Trivy" in evidence.summary

    def test_mount_probe_and_verifier_forward(self, tmp_path) -> None:
        mount = _Recorder("mount_probe")
        verifier = _Recorder("independent_verifier")
        executor = _executor(
            tmp_path,
            smoke=_Recorder("synthetic_smoke"),
            fail_closed=_Recorder("fail_closed"),
            supply_chain=_FakeSupplyChain(),
            mount_probe=mount,
            verifier=verifier,
        )
        run = _run(_platform_target())
        mount_evidence = executor.execute(
            run, PlatformValidationStep.MOUNT_PROBE
        )
        assert mount_evidence.step is PlatformValidationStep.MOUNT_PROBE
        assert mount.calls == [tmp_path]
        verifier_evidence = executor.execute(
            run, PlatformValidationStep.INDEPENDENT_VERIFIER
        )
        assert verifier_evidence.step is PlatformValidationStep.INDEPENDENT_VERIFIER
        assert verifier.calls == [tmp_path]

    def test_runner_failure_becomes_failed_evidence(self, tmp_path) -> None:
        smoke = _Recorder("synthetic_smoke")
        smoke.raise_on = RuntimeError("Smoke 执行器崩溃")
        executor = _executor(
            tmp_path,
            smoke=smoke,
            fail_closed=_Recorder("fail_closed"),
            supply_chain=_FakeSupplyChain(),
            mount_probe=_Recorder("mount_probe"),
            verifier=_Recorder("independent_verifier"),
        )
        evidence = executor.execute(
            _run(_platform_target()), PlatformValidationStep.SYNTHETIC_SMOKE
        )
        assert evidence.status is ValidationStepStatus.FAILED
        assert "RuntimeError" in evidence.summary
