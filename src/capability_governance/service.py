# -*- coding: utf-8 -*-
"""隐藏三轴投影、Legacy 兼容与 Actor 过滤的治理 Interface。"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import threading
from typing import Callable, Protocol

from src.capability_catalog import (
    CapabilityCatalog,
    CapabilityPackRef,
    CatalogActor,
)
from src.conversation_steering import (
    CapabilityMaturity as LegacyCapabilityMaturity,
    CapabilityPack,
    ProcedureScope,
)

from .models import (
    CapabilityEligibility,
    CapabilityGovernanceEvent,
    CapabilityGovernanceProjection,
    CapabilityGovernanceTarget,
    CapabilityGovernanceView,
    CapabilitySupplyChainEvidence,
    CapabilityValidationRun,
    CapabilityLifecycle,
    CapabilityMaturity,
    PromotionGap,
    PromotionOutcome,
    SupplyChainEvidenceStatus,
    TRIVY_DATABASE_MAX_AGE,
    ValidationTaskRef,
    ValidationEvidence,
    ValidationRunStatus,
    ValidationStep,
    ValidationStepStatus,
    is_ac06_admin_gray_validation_target,
)
from .repository import CapabilityGovernanceRepository
from .task_replay import ValidationTaskResolver


class CapabilityValidationExecutor(Protocol):
    def execute(
        self,
        run: CapabilityValidationRun,
        step: ValidationStep,
    ) -> ValidationEvidence: ...


_VALIDATION_STEP_LABELS = {
    ValidationStep.SYNTHETIC_SMOKE: "合成 Smoke",
    ValidationStep.OWNER_TASK_REPLAY: "授权真实任务重放",
    ValidationStep.FAIL_CLOSED: "失败关闭与权限",
    ValidationStep.VERIFIER: "独立 Verifier",
    ValidationStep.CLEANUP: "资源清理",
}


def _controlled_evidence(
    run: CapabilityValidationRun,
    evidence: ValidationEvidence,
) -> ValidationEvidence:
    label = _VALIDATION_STEP_LABELS[evidence.step]
    status = {
        ValidationStepStatus.PASSED: "已通过",
        ValidationStepStatus.FAILED: "未通过",
        ValidationStepStatus.CANCELLED: "已取消",
    }[evidence.status]
    # Executor 可能来自能力 Adapter；对外只保留当前 Run/Step 的受控引用，避免业务文件名、
    # 宿主路径或连接标识借 evidence_ref 进入治理台账。
    return evidence.model_copy(
        update={
            "evidence_ref": (
                f"evidence://validation/{run.run_id}/{evidence.step.value}"
            ),
            "summary": f"{label}{status}",
        }
    )


def _executor_failure_evidence(
    run: CapabilityValidationRun,
    step: ValidationStep,
    error: Exception,
) -> ValidationEvidence:
    # 异常正文可能包含业务文件名、宿主路径或 Secret；持久化时只保留异常类型证明。
    failure_hash = hashlib.sha256(
        f"{step.value}:{type(error).__name__}".encode("utf-8")
    ).hexdigest()
    return ValidationEvidence(
        step=step,
        status=ValidationStepStatus.FAILED,
        evidence_ref=f"evidence://validation/{run.run_id}/{step.value}-failure",
        evidence_sha256=failure_hash,
        summary=f"{_VALIDATION_STEP_LABELS[step]}未通过",
    )


class CapabilityGovernance:
    def __init__(
        self,
        catalog: CapabilityCatalog,
        repository: CapabilityGovernanceRepository,
        *,
        task_resolver: ValidationTaskResolver | None = None,
    ) -> None:
        self._catalog = catalog
        self._repository = repository
        self._task_resolver = task_resolver

    @staticmethod
    def _target(pack: CapabilityPack) -> CapabilityGovernanceTarget:
        return CapabilityGovernanceTarget(
            owner_id=pack.owner_id,
            scope=pack.scope,
            pack_id=pack.pack_id,
            version=pack.version,
            digest=pack.digest,
        )

    @classmethod
    def _can_validate_pack(
        cls,
        actor: CatalogActor,
        pack: CapabilityPack,
    ) -> bool:
        if (
            pack.scope is ProcedureScope.PERSONAL
            and pack.owner_id == actor.owner_id
        ):
            return True
        target = cls._target(pack)
        return (
            actor.is_admin
            and is_ac06_admin_gray_validation_target(target)
            and pack.created_by == "ac06-gray-preparation"
            and "admin_gray_only" in pack.permission_requirements
        )

    def register_pack(
        self,
        actor: CatalogActor,
        *,
        pack_ref: CapabilityPackRef,
        idempotency_key: str,
    ) -> CapabilityGovernanceEvent:
        pack = self._catalog.resolve_pack(
            actor,
            pack_ref.pack_id,
            pack_ref.version,
            pack_ref.digest,
        )
        if pack is None:
            raise PermissionError("能力包不存在或当前 Actor 不可见")
        if pack.scope is ProcedureScope.PLATFORM:
            # 平台治理事实必须由后续 Publisher 生成，不能借登记接口写入。
            raise PermissionError("平台能力包只能由发布治理流程登记")
        target = self._target(pack)
        existing = self._repository.get_by_idempotency(
            target,
            idempotency_key,
        )
        if existing is not None:
            return existing
        return self._repository.save_event(
            CapabilityGovernanceEvent(
                idempotency_key=idempotency_key,
                target=target,
                actor_id=actor.owner_id,
                actor_role=actor.role,
            )
        )

    def evaluate_promotion(
        self,
        target: CapabilityGovernanceTarget,
        *,
        now: datetime | None = None,
    ) -> tuple[PromotionGap, ...]:
        """晋级判定门：只读既有证据并返回脱敏缺口；空元组表示全部硬门通过。"""

        gaps: list[PromotionGap] = []
        run = self._repository.get_latest_succeeded_validation_run(target)
        if run is None:
            gaps.append(PromotionGap.VALIDATION_INCOMPLETE)
        else:
            by_step = {item.step for item in run.evidence}
            if (
                # 成功状态必须与五步 evidence 一一对应；缺步、重复步骤或任一未
                # 通过都可能说明记录残缺或被改写，不能只看步骤集合相等。
                set(by_step) != set(ValidationStep)
                or len(run.evidence) != len(ValidationStep)
                or any(
                    item.status is not ValidationStepStatus.PASSED
                    for item in run.evidence
                )
            ):
                gaps.append(PromotionGap.EVIDENCE_REFERENCE_MISMATCH)
        evidence = self._repository.get_latest_supply_chain_evidence(target)
        if evidence is None:
            gaps.append(PromotionGap.SUPPLY_CHAIN_EVIDENCE_MISSING)
        elif evidence.status is SupplyChainEvidenceStatus.BLOCKED:
            # blocker 字面量与 PromotionGap 值一一对应，按证据逐项映射。
            gaps.extend(PromotionGap(blocker) for blocker in evidence.blockers)
        else:
            current = now or datetime.now(timezone.utc)
            if current - evidence.trivy_database.updated_at > TRIVY_DATABASE_MAX_AGE:
                # 漏洞库时效按判定时刻复查；采集时未过期不等于晋级时仍有效。
                gaps.append(PromotionGap.TRIVY_DATABASE_STALE)
        return tuple(gaps)

    def maybe_promote(
        self,
        target: CapabilityGovernanceTarget,
        *,
        actor: CatalogActor | None = None,
        now: datetime | None = None,
    ) -> PromotionOutcome:
        """确定性晋级命令：证据全过写晋级事件；任何缺口保持草稿且不写事件。"""

        existing = self._repository.get_latest_promotion_event(target)
        if existing is not None:
            return PromotionOutcome(status="already_verified", event=existing)
        gaps = self.evaluate_promotion(target, now=now)
        if gaps:
            return PromotionOutcome(status="held", gaps=gaps)
        run = self._repository.get_latest_succeeded_validation_run(target)
        evidence = self._repository.get_latest_supply_chain_evidence(target)
        assert run is not None and evidence is not None  # evaluate 已保证
        if actor is None:
            # 审计归因于验证运行的所有者：Owner 发起验证即授权自动晋级。
            actor = CatalogActor(owner_id=run.owner_id, role=run.actor_role)
        new_event = CapabilityGovernanceEvent(
            event_type="promoted_to_verified",
            idempotency_key=(
                f"promotion:{target.digest}:validation:{run.run_id}"
            ),
            target=target,
            maturity=CapabilityMaturity.VERIFIED,
            actor_id=actor.owner_id,
            actor_role=actor.role,
            source_validation_run_id=run.run_id,
            source_supply_chain_evidence_id=evidence.evidence_id,
        )
        saved = self._repository.save_promotion_event(new_event)
        # 并发后写者拿到既有事件：本次调用没有产生新事实，按幂等命中返回。
        status = "promoted" if saved.event_id == new_event.event_id else "already_verified"
        return PromotionOutcome(status=status, event=saved)

    def _projection_for_pack(
        self,
        pack: CapabilityPack,
    ) -> CapabilityGovernanceProjection:
        target = self._target(pack)
        events = self._repository.list_events(target)
        if events:
            latest = events[-1]
            return CapabilityGovernanceProjection(
                target=target,
                maturity=latest.maturity,
                lifecycle=latest.lifecycle,
                eligibility=latest.eligibility,
                source="governance_event",
            )
        legacy_deprecated = (
            pack.maturity is LegacyCapabilityMaturity.DEPRECATED
        )
        # Legacy 只有单轴状态：已验证/已弃用必须保留原含义，安全资格没有历史反证时仅作
        # 兼容 eligible 投影；该映射只读且不写回 Pack，后续真实治理事件会覆盖它。
        return CapabilityGovernanceProjection(
            target=target,
            maturity=(
                CapabilityMaturity.VERIFIED
                if pack.maturity
                in {
                    LegacyCapabilityMaturity.VERIFIED,
                    LegacyCapabilityMaturity.DEPRECATED,
                }
                else CapabilityMaturity.DRAFT
            ),
            lifecycle=(
                CapabilityLifecycle.DEPRECATED
                if legacy_deprecated
                else CapabilityLifecycle.ACTIVE
            ),
            eligibility=CapabilityEligibility.ELIGIBLE,
            source="legacy_compat",
        )

    def list_visible_projections(
        self,
        actor: CatalogActor,
    ) -> tuple[CapabilityGovernanceView, ...]:
        packs = (
            self._catalog.list_governable_packs(actor)
            if actor.is_admin
            else self._catalog.list_visible_packs(actor)
        )
        views: list[CapabilityGovernanceView] = []
        for pack in sorted(
            packs,
            key=lambda item: (item.pack_id, item.version),
        ):
            projection = self._projection_for_pack(pack)
            view = CapabilityGovernanceView.from_projection(projection, actor)
            gaps: tuple[PromotionGap, ...] = ()
            if projection.maturity is not CapabilityMaturity.VERIFIED:
                # 草稿能力给出脱敏缺口，让 Owner 知道还缺哪些证据。
                gaps = self.evaluate_promotion(self._target(pack))
            views.append(
                view.model_copy(
                    update={
                        "can_validate": self._can_validate_pack(actor, pack),
                        "promotion_gaps": gaps,
                    }
                )
            )
        return tuple(views)

    def get_supply_chain_evidence(
        self,
        actor: CatalogActor,
        *,
        pack_ref: CapabilityPackRef,
    ) -> CapabilitySupplyChainEvidence | None:
        if actor.is_admin:
            pack = next(
                (
                    item
                    for item in self._catalog.list_governable_packs(actor)
                    if item.pack_id == pack_ref.pack_id
                    and item.version == pack_ref.version
                    and item.digest == pack_ref.digest
                ),
                None,
            )
        else:
            pack = self._catalog.resolve_pack(
                actor,
                pack_ref.pack_id,
                pack_ref.version,
                pack_ref.digest,
            )
        if pack is None:
            raise PermissionError("能力包不存在或当前 Actor 不可见")
        if pack.scope is ProcedureScope.PLATFORM and not actor.is_admin:
            raise PermissionError("平台能力供应链证据仅管理员可见")
        return self._repository.get_latest_supply_chain_evidence(
            self._target(pack)
        )

    def request_validation(
        self,
        actor: CatalogActor,
        *,
        pack_ref: CapabilityPackRef,
        task_ref: ValidationTaskRef,
        idempotency_key: str,
    ) -> CapabilityValidationRun:
        pack = self._catalog.resolve_pack(
            actor,
            pack_ref.pack_id,
            pack_ref.version,
            pack_ref.digest,
        )
        if pack is None:
            raise PermissionError("能力包不存在或当前 Actor 不可见")
        if not self._can_validate_pack(actor, pack):
            # 一般平台能力仍只读；这里只兼容 AC-06 两项由管理员亲自跑过真实任务的过渡灰度包。
            raise PermissionError("只能验证自己的个人能力或 AC-06 管理员灰度包")
        if task_ref.capability_digest != pack.digest:
            raise ValueError("真实任务引用的能力 digest 与目标不一致")
        if self._task_resolver is not None:
            task_ref = self._task_resolver.verify(actor, self._target(pack), task_ref)
        return self._repository.create_validation_run(
            CapabilityValidationRun(
                owner_id=pack.owner_id or actor.owner_id,
                target=self._target(pack),
                actor_id=actor.owner_id,
                actor_role=actor.role,
                idempotency_key=idempotency_key,
                task_ref=task_ref,
            )
        )

    def request_validation_for_task(
        self,
        actor: CatalogActor,
        *,
        pack_ref: CapabilityPackRef,
        task_id: str,
        revision: int,
        idempotency_key: str,
    ) -> CapabilityValidationRun:
        if self._task_resolver is None:
            raise RuntimeError("能力验证未配置真实任务解析器")
        pack = self._catalog.resolve_pack(
            actor,
            pack_ref.pack_id,
            pack_ref.version,
            pack_ref.digest,
        )
        if pack is None:
            raise PermissionError("能力包不存在或当前 Actor 不可见")
        task_ref = self._task_resolver.resolve(
            actor,
            self._target(pack),
            task_id=task_id,
            revision=revision,
        )
        return self.request_validation(
            actor,
            pack_ref=pack_ref,
            task_ref=task_ref,
            idempotency_key=idempotency_key,
        )

    def list_validation_task_options(
        self,
        actor: CatalogActor,
        *,
        pack_ref: CapabilityPackRef,
    ):
        if self._task_resolver is None:
            return ()
        pack = self._catalog.resolve_pack(
            actor,
            pack_ref.pack_id,
            pack_ref.version,
            pack_ref.digest,
        )
        if pack is None or not self._can_validate_pack(actor, pack):
            return ()
        return self._task_resolver.list_options(actor, self._target(pack))

    def get_validation(
        self,
        actor: CatalogActor,
        run_id: str,
    ) -> CapabilityValidationRun:
        run = self._repository.get_validation_run(run_id)
        if run is None:
            raise KeyError("能力验证运行不存在")
        if not actor.is_admin and run.owner_id != actor.owner_id:
            raise PermissionError("不能读取其他用户的能力验证运行")
        return run

    def list_validations(
        self,
        actor: CatalogActor,
    ) -> tuple[CapabilityValidationRun, ...]:
        return tuple(
            run
            for run in self._repository.list_validation_runs()
            if actor.is_admin or run.owner_id == actor.owner_id
        )

    def list_all_validations_for_worker(self) -> tuple[CapabilityValidationRun, ...]:
        """后台 worker 只获取运行身份；HTTP 调用方不能借此绕过 Actor 投影。"""

        return self._repository.list_validation_runs()

    def cancel_validation(
        self,
        actor: CatalogActor,
        run_id: str,
    ) -> CapabilityValidationRun:
        run = self.get_validation(actor, run_id)
        if run.owner_id != actor.owner_id:
            raise PermissionError("不能取消其他用户的能力验证运行")
        if run.status in {
            ValidationRunStatus.SUCCEEDED,
            ValidationRunStatus.FAILED,
            ValidationRunStatus.CANCELLED,
        }:
            return run
        return self._repository.save_validation_run(
            run.model_copy(
                update={
                    "status": ValidationRunStatus.CANCELLING,
                    "cancel_requested": True,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
        )

    def execute_validation(
        self,
        actor: CatalogActor,
        run_id: str,
        *,
        worker_id: str,
        executor: CapabilityValidationExecutor,
        lease_guarded_preflight: (
            Callable[[CapabilityValidationRun], None] | None
        ) = None,
        now: datetime | None = None,
        lease_seconds: int = 60,
    ) -> CapabilityValidationRun:
        run = self.get_validation(actor, run_id)
        if run.owner_id != actor.owner_id:
            raise PermissionError("只有能力 Owner 可以执行验证运行")
        if run.status in {
            ValidationRunStatus.SUCCEEDED,
            ValidationRunStatus.FAILED,
            ValidationRunStatus.CANCELLED,
        }:
            return run
        lease_now = now or datetime.now(timezone.utc)
        if not self._repository.acquire_validation_lease(
            run_id=run.run_id,
            digest=run.target.digest,
            worker_id=worker_id,
            now=lease_now,
            lease_seconds=lease_seconds,
        ):
            return self.get_validation(actor, run_id)

        run = self._repository.save_validation_run(
            run.model_copy(
                update={
                    "status": ValidationRunStatus.RUNNING,
                    "updated_at": datetime.now(timezone.utc),
                }
            ),
            lease_worker_id=worker_id,
            lease_now=lease_now,
        )
        finished_normally = False
        heartbeat_stop = threading.Event()
        heartbeat_lost = threading.Event()
        heartbeat: threading.Thread | None = None
        if now is None:
            interval = max(1.0, lease_seconds / 3)

            def renew_lease() -> None:
                while not heartbeat_stop.wait(interval):
                    try:
                        renewed = self._repository.renew_validation_lease(
                            run_id=run.run_id,
                            digest=run.target.digest,
                            worker_id=worker_id,
                            now=datetime.now(timezone.utc),
                            lease_seconds=lease_seconds,
                        )
                    except Exception:
                        heartbeat_lost.set()
                        return
                    if not renewed:
                        heartbeat_lost.set()
                        return

            heartbeat = threading.Thread(
                target=renew_lease,
                name=f"capability-validation-lease-{run.run_id}",
                daemon=True,
            )
            heartbeat.start()
        try:
            if lease_guarded_preflight is not None and not run.cancel_requested:
                lease_guarded_preflight(run)
            completed = {item.step for item in run.evidence}
            failed = any(
                item.status is ValidationStepStatus.FAILED
                for item in run.evidence
            )
            for step in ValidationStep:
                current = self._repository.get_validation_run(run.run_id)
                if current is None:
                    raise RuntimeError("能力验证运行在执行期间消失")
                run = current
                if step in completed:
                    continue
                if run.cancel_requested and step is not ValidationStep.CLEANUP:
                    continue
                if failed and step is not ValidationStep.CLEANUP:
                    continue
                try:
                    if (
                        self._task_resolver is not None
                        and step is not ValidationStep.CLEANUP
                    ):
                        # 每一步执行前都重新打开冻结事实；授权撤销或来源/输出变化必须立即失败关闭。
                        self._task_resolver.verify(actor, run.target, run.task_ref)
                    evidence = executor.execute(run, step)
                except Exception as error:
                    evidence = _executor_failure_evidence(run, step, error)
                if evidence.step is not step:
                    raise ValueError("验证执行器返回了错误的步骤身份")
                if heartbeat_lost.is_set():
                    raise RuntimeError("能力验证 Lease 在步骤执行期间失效")
                if (
                    step is ValidationStep.CLEANUP
                    and evidence.status is ValidationStepStatus.FAILED
                ):
                    # 清理失败不能写成终态，否则 Docker/Grant 残留会永久失去恢复入口。
                    # 保留 RUNNING 与当前 Lease，待 Lease 到期后由 worker 再次幂等清理。
                    raise RuntimeError("能力验证资源尚未清理，等待恢复重试")
                evidence = _controlled_evidence(run, evidence)
                run = self._repository.save_validation_run(
                    run.model_copy(
                        update={
                            "evidence": (*run.evidence, evidence),
                            "cancel_requested": (
                                run.cancel_requested
                                or evidence.status is ValidationStepStatus.CANCELLED
                            ),
                            "updated_at": datetime.now(timezone.utc),
                        }
                    ),
                    lease_worker_id=worker_id,
                    lease_now=lease_now if now is not None else datetime.now(timezone.utc),
                )
                if evidence.status is ValidationStepStatus.FAILED:
                    failed = True
            status = (
                ValidationRunStatus.CANCELLED
                if run.cancel_requested
                else (
                    ValidationRunStatus.FAILED
                    if failed
                    else ValidationRunStatus.SUCCEEDED
                )
            )
            run = self._repository.save_validation_run(
                run.model_copy(
                    update={
                        "status": status,
                        "updated_at": datetime.now(timezone.utc),
                    }
                ),
                lease_worker_id=worker_id,
                lease_now=lease_now if now is not None else datetime.now(timezone.utc),
            )
            finished_normally = True
            return run
        finally:
            heartbeat_stop.set()
            if heartbeat is not None:
                heartbeat.join(timeout=max(1.0, lease_seconds / 3 + 0.5))
            # 进程级中断时保留 Lease，避免另一 worker 在旧进程尚未真正退出时并发接管。
            if finished_normally:
                self._repository.release_validation_lease(run_id, worker_id)
