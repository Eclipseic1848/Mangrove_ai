# -*- coding: utf-8 -*-
"""平台快照六步验证执行器：不含个人任务重放，证据绑定平台新 digest。"""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Callable, Protocol
import uuid
from filelock import Timeout as FileLockTimeout

from src.account_execution import ExecutionDenied
from src.api.execution import execution_to_thread
from .sqlite_repository import validation_checkpoint

from .models import (
    CapabilityGovernanceTarget,
    CapabilitySupplyChainEvidence,
    PlatformValidationEvidence,
    PlatformValidationRun,
    PlatformValidationStep,
    SupplyChainEvidenceStatus,
    ValidationRunStatus,
    ValidationStepStatus,
)

_STEP_LABEL = {
    PlatformValidationStep.SYNTHETIC_SMOKE: "合成 Smoke",
    PlatformValidationStep.FAIL_CLOSED: "失败关闭",
    PlatformValidationStep.TRIVY: "Trivy 扫描",
    PlatformValidationStep.SYFT: "Syft SBOM",
    PlatformValidationStep.MOUNT_PROBE: "装载探针",
    PlatformValidationStep.INDEPENDENT_VERIFIER: "独立验证",
}


class PlatformStepRunner(Protocol):
    """单个平台验证步骤的执行边界；异常必须失败关闭为 failed 证据。"""

    def run(self, subject: Path) -> PlatformValidationEvidence: ...


def _failed_evidence(
    run: PlatformValidationRun,
    step: PlatformValidationStep,
    error: Exception,
) -> PlatformValidationEvidence:
    # 异常正文可能含宿主路径或业务文件名；只保留异常类型证明。
    failure_hash = hashlib.sha256(
        f"{step.value}:{type(error).__name__}".encode("utf-8")
    ).hexdigest()
    return PlatformValidationEvidence(
        step=step,
        status=ValidationStepStatus.FAILED,
        evidence_ref=(
            f"evidence://platform/{run.run_id}/{step.value}-failure"
        ),
        evidence_sha256=failure_hash,
        summary=f"{_STEP_LABEL[step]}未通过：{type(error).__name__}",
    )


class SupplyChainCollectContract(Protocol):
    def collect(
        self,
        target: CapabilityGovernanceTarget,
        subject_root: str | Path,
    ) -> CapabilitySupplyChainEvidence: ...


class PlatformValidationExecutorContract(Protocol):
    def execute(
        self,
        run: PlatformValidationRun,
        step: PlatformValidationStep,
    ) -> PlatformValidationEvidence: ...


class PlatformSigningContract(Protocol):
    def execute(
        self,
        request: object,
        *,
        cancel_requested: Callable[[], bool] = lambda: False,
    ) -> object: ...


class PlatformValidationRepositoryContract(Protocol):
    def list_platform_validation_runs(self) -> tuple[PlatformValidationRun, ...]: ...

    def save_platform_validation_run(
        self,
        run: PlatformValidationRun,
    ) -> PlatformValidationRun: ...

    def get_latest_platform_event(
        self,
        target: CapabilityGovernanceTarget,
        event_type: str,
    ): ...

    def acquire_platform_validation_lease(
        self,
        *,
        run_id: str,
        digest: str,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool: ...

    def release_platform_validation_lease(
        self,
        run_id: str,
        worker_id: str,
    ) -> None: ...


class PlatformValidationManager:
    """平台验证与签名的后台推进器：Lease 防并发，幂等续跑，签名证据写回运行记录。"""

    def __init__(
        self,
        repository: PlatformValidationRepositoryContract,
        *,
        executor: PlatformValidationExecutorContract,
        signing: PlatformSigningContract,
        layout_path: str | Path,
        private_key_path: str | Path,
        public_key_path: str | Path,
        poll_seconds: float = 5.0,
        lease_seconds: int = 300,
    ) -> None:
        self._repository = repository
        self._executor = executor
        self._signing = signing
        self._layout_path = Path(layout_path)
        self._private_key_path = Path(private_key_path)
        self._public_key_path = Path(public_key_path)
        self._worker_id = f"pfval-worker-{uuid.uuid4().hex[:12]}"
        self._poll_seconds = poll_seconds
        self._lease_seconds = lease_seconds
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._run(),
                name="platform-validation-worker",
            )

    def notify(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            await execution_to_thread(self.run_once)
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self._poll_seconds
                )
            except TimeoutError:
                pass
            self._wake.clear()

    def run_once(self) -> None:
        for run in self._repository.list_platform_validation_runs():
            context = getattr(self._repository, 'validation_execution', None)
            try:
                with context(run) if context else nullcontext():
                    self._step_active = False
                    self._known_quiet = self._repository.validation_never_started(run, self._worker_id) if context else True
                    try:
                        validation_checkpoint()
                        self._run_one(run)
                    except ExecutionDenied:
                        if context:
                            if self._repository.validation_cleanup_available(run, self._worker_id):
                                self._repository.confirm_validation_stopped(run, cleanup_failed=not self._known_quiet)
            except (ExecutionDenied, FileLockTimeout):
                continue

    def _run_one(self, run: PlatformValidationRun) -> None:
        # 外部步骤（Trivy/Syft/探针/签名）必须独占；Lease 到期由下一轮接管。
        if not self._repository.acquire_platform_validation_lease(
            run_id=run.run_id,
            digest=run.target.digest,
            worker_id=self._worker_id,
            now=datetime.now(timezone.utc),
            lease_seconds=self._lease_seconds,
        ):
            return
        try:
            run = self._advance_validation(run)
            if (
                run is not None
                and run.status is ValidationRunStatus.SUCCEEDED
            ):
                self._advance_signing(run)
            if self._known_quiet and run is not None and run.status in {ValidationRunStatus.SUCCEEDED, ValidationRunStatus.FAILED, ValidationRunStatus.CANCELLED}:
                finish = getattr(self._repository, 'finish_validation_execution', None)
                if finish:
                    finish(run)
        finally:
            self._repository.release_platform_validation_lease(
                run.run_id,
                self._worker_id,
            )

    def reconcile_account_execution(self, owner_id: str, resource_id: str, expected_generation: int) -> bool:
        if not resource_id.startswith('platform:'):
            return False
        run = self._repository.get_platform_validation_run(resource_id.removeprefix('platform:'))
        if run is None or run.actor_id != owner_id:
            return False
        binding = self._repository.get_validation_binding(run)
        if binding is None or binding['generation'] != expected_generation:
            return False
        if binding['state'] == 'paused':
            return True
        # 无持久资源清理协议的历史外部步骤必须保留未知，不能仅靠拿到锁确认。
        if not self._repository.validation_never_started(run, self._worker_id):
            return False
        try:
            with self._repository.validation_execution(run):
                current = self._repository.get_validation_binding(run)
                if current is None or current['generation'] != expected_generation:
                    return False
                self._repository.confirm_validation_stopped(run)
                return True
        except (ExecutionDenied, FileLockTimeout):
            return False

    def _advance_validation(
        self,
        run: PlatformValidationRun,
    ) -> PlatformValidationRun | None:
        if run.status not in {
            ValidationRunStatus.QUEUED,
            ValidationRunStatus.RUNNING,
        }:
            return run
        if run.status is ValidationRunStatus.QUEUED:
            run = self._repository.save_platform_validation_run(run.model_copy(update={'status': ValidationRunStatus.RUNNING, 'updated_at': datetime.now(timezone.utc)}))
        completed = {item.step for item in run.evidence}
        failed = any(
            item.status is ValidationStepStatus.FAILED
            for item in run.evidence
        )
        for step in PlatformValidationStep:
            if step in completed or failed:
                continue
            validation_checkpoint()
            self._step_active = True
            self._known_quiet = False
            evidence = self._executor.execute(run, step)
            self._step_active = False
            self._known_quiet = True
            validation_checkpoint()
            if evidence.step is not step:
                raise ValueError("平台验证执行器返回了错误的步骤身份")
            run = self._repository.save_platform_validation_run(
                run.model_copy(
                    update={
                        "evidence": (*run.evidence, evidence),
                        "updated_at": datetime.now(timezone.utc),
                    }
                )
            )
            # 外部步骤（Trivy/Syft）可能超过默认租约时长；每步完成后续租，
            # 避免长时间运行的扫描被另一 worker 接管重复执行。
            self._repository.renew_platform_validation_lease(
                run_id=run.run_id,
                digest=run.target.digest,
                worker_id=self._worker_id,
                now=datetime.now(timezone.utc),
                lease_seconds=self._lease_seconds,
            )
            if evidence.status is ValidationStepStatus.FAILED:
                failed = True
        finished = {item.step for item in run.evidence}
        if failed and run.status is not ValidationRunStatus.FAILED:
            run = self._repository.save_platform_validation_run(
                run.model_copy(
                    update={
                        "status": ValidationRunStatus.FAILED,
                        "updated_at": datetime.now(timezone.utc),
                    }
                )
            )
        elif not failed and finished == set(PlatformValidationStep) and (
            run.status is not ValidationRunStatus.SUCCEEDED
        ):
            run = self._repository.save_platform_validation_run(
                run.model_copy(
                    update={
                        "status": ValidationRunStatus.SUCCEEDED,
                        "updated_at": datetime.now(timezone.utc),
                    }
                )
            )
        return run

    def _advance_signing(self, run: PlatformValidationRun) -> None:
        if (
            run.signing_signature_digest is not None
            and run.signing_public_key_sha256 is not None
        ):
            return
        candidate = self._repository.get_latest_platform_event(
            run.target, "platform_candidate"
        )
        if candidate is None:
            return
        binding_reader = getattr(self._repository, 'get_validation_binding', None)
        if binding_reader and not self._known_quiet:
            binding = binding_reader(run)
            if binding is not None and binding['state'] == 'active':
                # 重启时只剩在途标记，不能猜测签名尚未发生并重复外发。
                self._repository.confirm_validation_stopped(run, cleanup_failed=True)
                return
        from .oci_signing import OciSigningRequest

        request = OciSigningRequest(
            transaction_id=f"platform-sign-{run.run_id}",
            source_layout=self._layout_path,
            source_reference=run.target.digest,
            output_layout=self._layout_path / "signed" / run.run_id,
            output_reference=run.target.digest,
            registry_repository="mangrove/platform-snapshots",
            subject_digest=run.target.digest,
            private_key_path=self._private_key_path,
            public_key_path=self._public_key_path,
        )
        try:
            validation_checkpoint()
            begin = getattr(self._repository, 'begin_validation_execution', None)
            if begin:
                begin(run)
            self._step_active = True
            self._known_quiet = False
            evidence = self._signing.execute(request)
            self._step_active = False
            self._known_quiet = True
            validation_checkpoint()
        except ExecutionDenied:
            raise
        except Exception:
            # 普通异常也可能留下签名进程或未知外部结果；不得标为空闲或自动重签。
            self._step_active = False
            confirm = getattr(self._repository, 'confirm_validation_stopped', None)
            if confirm:
                confirm(run, cleanup_failed=True)
            return
        if evidence.subject_digest != run.target.digest:
            raise RuntimeError("平台签名主体 digest 与运行目标不一致")
        self._repository.save_platform_validation_run(
            run.model_copy(
                update={
                    "signing_signature_digest": evidence.signature_digest,
                    "signing_public_key_sha256": evidence.public_key_sha256,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
        )


class LockedPlatformValidationExecutor:
    """六步执行的唯一入口；供应链证据只对平台 digest 与快照目录产生。"""

    def __init__(
        self,
        *,
        materialize: Callable[[CapabilityGovernanceTarget], Path],
        smoke: PlatformStepRunner,
        fail_closed: PlatformStepRunner,
        supply_chain: SupplyChainCollectContract,
        mount_probe: PlatformStepRunner,
        verifier: PlatformStepRunner,
    ) -> None:
        self._materialize = materialize
        self._smoke = smoke
        self._fail_closed = fail_closed
        self._supply_chain = supply_chain
        self._mount_probe = mount_probe
        self._verifier = verifier
        # 同一运行内 trivy/syft 共享一次供应链采集，不重复扫描。
        self._supply_cache: dict[str, CapabilitySupplyChainEvidence] = {}

    def execute(
        self,
        run: PlatformValidationRun,
        step: PlatformValidationStep,
    ) -> PlatformValidationEvidence:
        subject = self._materialize(run.target)
        if step is PlatformValidationStep.SYNTHETIC_SMOKE:
            return self._run_step(run, step, self._smoke, subject)
        if step is PlatformValidationStep.FAIL_CLOSED:
            return self._run_step(run, step, self._fail_closed, subject)
        if step is PlatformValidationStep.TRIVY:
            try:
                evidence = self._supply_chain.collect(run.target, subject)
            except ExecutionDenied:
                raise
            except Exception as error:
                return _failed_evidence(run, step, error)
            self._supply_cache[run.run_id] = evidence
            passed = evidence.status is SupplyChainEvidenceStatus.PASSED
            return PlatformValidationEvidence(
                step=step,
                status=(
                    ValidationStepStatus.PASSED
                    if passed
                    else ValidationStepStatus.FAILED
                ),
                evidence_ref=f"evidence://platform/{run.run_id}/trivy",
                evidence_sha256=evidence.trivy_result_sha256,
                summary="Trivy 扫描已通过" if passed else "Trivy 扫描存在硬门",
            )
        if step is PlatformValidationStep.SYFT:
            cached = self._supply_cache.get(run.run_id)
            if cached is None:
                raise RuntimeError("Syft SBOM 步骤必须先执行供应链采集")
            passed = cached.status is SupplyChainEvidenceStatus.PASSED
            return PlatformValidationEvidence(
                step=step,
                status=(
                    ValidationStepStatus.PASSED
                    if passed
                    else ValidationStepStatus.FAILED
                ),
                evidence_ref=f"evidence://platform/{run.run_id}/syft",
                evidence_sha256=cached.cyclonedx_json_sha256,
                summary="Syft SBOM 已生成" if passed else "Syft 证据不可用",
            )
        if step is PlatformValidationStep.MOUNT_PROBE:
            return self._run_step(run, step, self._mount_probe, subject)
        if step is PlatformValidationStep.INDEPENDENT_VERIFIER:
            return self._run_step(run, step, self._verifier, subject)
        raise ValueError("未知平台验证步骤")

    @staticmethod
    def _run_step(
        run: PlatformValidationRun,
        step: PlatformValidationStep,
        runner: PlatformStepRunner,
        subject: Path,
    ) -> PlatformValidationEvidence:
        try:
            evidence = runner.run(subject)
        except ExecutionDenied:
            raise
        except Exception as error:
            return _failed_evidence(run, step, error)
        if evidence.step is not step:
            raise ValueError("平台验证执行器返回了错误的步骤身份")
        return evidence
