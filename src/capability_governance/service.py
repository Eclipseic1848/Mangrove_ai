# -*- coding: utf-8 -*-
"""隐藏三轴投影、Legacy 兼容与 Actor 过滤的治理 Interface。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import threading
from typing import Callable, Literal, Protocol

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
    AdminReviewItem,
    AudienceOutcome,
    AuditSubjectType,
    AuditViewOutcome,
    CapabilityEligibility,
    CapabilityGovernanceEvent,
    CapabilityGovernanceProjection,
    CapabilityGovernanceTarget,
    CapabilityGovernanceView,
    CapabilitySupplyChainEvidence,
    CapabilityValidationRun,
    CapabilityLifecycle,
    CapabilityMaturity,
    GovernanceCommandOutcome,
    PlatformCandidateOutcome,
    PlatformCandidateSummary,
    PlatformSnapshot,
    PlatformValidationRun,
    PlatformValidationStep,
    PromotionGap,
    PromotionOutcome,
    PublishOutcome,
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


class PlatformSnapshotGeneratorContract(Protocol):
    def generate(self, pack: CapabilityPack) -> PlatformSnapshot: ...


class PlatformPublisherContract(Protocol):
    def save_pack(self, pack: CapabilityPack) -> CapabilityPack: ...


class CapabilityGovernance:
    def __init__(
        self,
        catalog: CapabilityCatalog,
        repository: CapabilityGovernanceRepository,
        *,
        task_resolver: ValidationTaskResolver | None = None,
        platform_snapshot_generator: (
            PlatformSnapshotGeneratorContract | None
        ) = None,
        platform_publisher: PlatformPublisherContract | None = None,
    ) -> None:
        self._catalog = catalog
        self._repository = repository
        self._task_resolver = task_resolver
        self._platform_snapshot_generator = platform_snapshot_generator
        self._platform_publisher = platform_publisher

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

    def runtime_projection_for_pack(
        self,
        pack: CapabilityPack,
    ) -> CapabilityGovernanceProjection:
        """按 pack 折叠治理事件的公开只读投影；运行时门与新任务选择共用的单一来源。

        三轴逐轴折叠（各轴取最后改变该轴的事件值）；风险接受按到期时间惰性
        判定（Q5A：到期即按 quarantined 投影，不写事件）；推荐指针取最后一条
        recommendation_changed 事件。
        """
        target = self._target(pack)
        events = [
            event
            for event in self._repository.list_events(target)
            if event.event_type != "audit_viewed"
        ]
        if events:
            latest = events[-1]
            lifecycle_events = [
                event
                for event in events
                if event.event_type == "lifecycle_changed"
            ]
            eligibility_events = [
                event
                for event in events
                if event.event_type
                in {"eligibility_changed", "risk_accepted"}
            ]
            lifecycle = (
                lifecycle_events[-1].lifecycle
                if lifecycle_events
                else latest.lifecycle
            )
            eligibility = (
                eligibility_events[-1].eligibility
                if eligibility_events
                else latest.eligibility
            )
            if eligibility_events and eligibility_events[-1].event_type == "risk_accepted":
                accepted = eligibility_events[-1]
                if (
                    accepted.expires_at is not None
                    and datetime.now(timezone.utc) >= accepted.expires_at
                ):
                    # 限期接受已到期：投影回到隔离，等待管理员决定（Q5A 惰性判定）。
                    eligibility = CapabilityEligibility.QUARANTINED
            recommendation_events = [
                event
                for event in events
                if event.event_type == "recommendation_changed"
            ]
            audience = None
            if target.scope is ProcedureScope.PLATFORM:
                # 受众只由发布/受众变更事件决定；候选事件不携带受众。
                for event in reversed(events):
                    if event.event_type in {
                        "platform_published",
                        "audience_changed",
                    }:
                        audience = event.audience
                        break
            return CapabilityGovernanceProjection(
                target=target,
                maturity=latest.maturity,
                lifecycle=lifecycle,
                eligibility=eligibility,
                source="governance_event",
                audience=audience,
                recommended_version=(
                    recommendation_events[-1].recommended_version
                    if recommendation_events
                    else None
                ),
            )
        legacy_deprecated = (
            pack.maturity is LegacyCapabilityMaturity.DEPRECATED
        )
        # Legacy 只有单轴状态：已验证/已弃用必须保留原含义，安全资格没有历史反证时仅作
        # 兼容 eligible 投影；该映射只读且不写回 Pack，后续真实治理事件会覆盖它。
        # AC-06 历史灰度包语义上只对管理员开放，受众兼容投影固定 admin_gray。
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
            audience=(
                "admin_gray"
                if target.scope is ProcedureScope.PLATFORM
                else None
            ),
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
            projection = self.runtime_projection_for_pack(pack)
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

    def list_admin_review(
        self,
        actor: CatalogActor,
    ) -> tuple[AdminReviewItem, ...]:
        """管理员跨 Owner 审核聚合；正文永不进入此投影，逐项尽力呈现。"""
        if not actor.is_admin:
            raise PermissionError("只有管理员可以读取能力审核视图")
        packs = self._catalog.list_governable_packs(actor)
        items: list[AdminReviewItem] = []
        for pack in sorted(packs, key=lambda item: (item.pack_id, item.version)):
            target = self._target(pack)
            projection = self.runtime_projection_for_pack(pack)
            gaps: tuple[PromotionGap, ...] = ()
            if projection.maturity is not CapabilityMaturity.VERIFIED:
                gaps = self.evaluate_promotion(target)
            run = self._repository.get_latest_succeeded_validation_run(target)
            task_metadata = None
            if run is not None and self._task_resolver is not None:
                try:
                    task_metadata = self._task_resolver.read_task_metadata(
                        actor,
                        run.task_ref.task_id,
                        run.task_ref.revision,
                        task_owner_id=run.owner_id,
                    )
                except (KeyError, PermissionError, ValueError):
                    # 任务已清理、JSON 元数据损坏或当前不可读时，管理元数据留空，
                    # 不阻断审核列表（JSONDecodeError 是 ValueError 子类）。
                    task_metadata = None
            items.append(
                AdminReviewItem(
                    pack_id=target.pack_id,
                    version=target.version,
                    scope=target.scope,
                    maturity=projection.maturity,
                    lifecycle=projection.lifecycle,
                    eligibility=projection.eligibility,
                    source=projection.source,
                    owner_id=target.owner_id,
                    digest=target.digest,
                    promotion_gaps=gaps,
                    validation=run,
                    supply_chain=(
                        self._repository.get_latest_supply_chain_evidence(target)
                    ),
                    task_metadata=task_metadata,
                    audit_history=self._repository.list_audit_view_events(target),
                )
            )
        return tuple(items)

    def audit_view_business_content(
        self,
        actor: CatalogActor,
        *,
        pack_ref: CapabilityPackRef,
        task_id: str,
        revision: int,
        subject_type: AuditSubjectType,
        reason: str,
        idempotency_key: str,
    ) -> AuditViewOutcome:
        """有原因、可审计的业务正文读取；失败也留痕，正文不落任何副本。"""
        if not actor.is_admin:
            raise PermissionError("只有管理员可以发起审计查看")
        reason = reason.strip()
        if len(reason) < 5:
            raise ValueError("审计查看原因过短，至少 5 个字符")
        if self._task_resolver is None:
            raise RuntimeError("能力治理未配置真实任务解析器")
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
        if pack is None:
            raise KeyError("能力包不存在或当前 Actor 不可见")
        target = self._target(pack)
        run = self._repository.get_latest_succeeded_validation_run(target)
        if run is None:
            raise ValueError("该能力尚无成功验证运行，无可审计查看的关联任务")
        if (
            task_id != run.task_ref.task_id
            or revision != run.task_ref.revision
        ):
            # 审计查看只针对验证证据实际关联的冻结任务，防止借能力包读取任意任务正文。
            raise ValueError("审计查看任务与验证证据不一致")
        content = self._task_resolver.read_business_content(
            actor,
            task_id,
            revision,
            subject_type,
            task_owner_id=run.owner_id,
        )
        event = CapabilityGovernanceEvent(
            event_type="audit_viewed",
            idempotency_key=idempotency_key,
            target=target,
            actor_id=actor.owner_id,
            actor_role=actor.role,
            reason=reason,
            subject_type=subject_type,
            subject_sha256=(
                content.content_sha256
                if content.status == "succeeded"
                else None
            ),
            result=content.status,
            task_id=task_id,
            revision=revision,
            failure_reason=(
                content.failure_reason
                if content.status == "failed"
                else None
            ),
        )
        saved = self._repository.save_audit_view_event(event)
        return AuditViewOutcome(
            status=content.status,
            content=content.content if content.status == "succeeded" else None,
            truncated=content.truncated,
            failure_reason=(
                content.failure_reason
                if content.status == "failed"
                else None
            ),
            event=saved,
        )

    def list_audit_records(
        self,
        actor: CatalogActor,
    ) -> tuple[CapabilityGovernanceEvent, ...]:
        """管理员读取全量审计查看记录（升序）；记录不含正文，只含 hash 与原因。"""
        if not actor.is_admin:
            raise PermissionError("只有管理员可以读取审计查看记录")
        return self._repository.list_audit_view_events(None)

    def submit_platform_candidate(
        self,
        actor: CatalogActor,
        *,
        pack_ref: CapabilityPackRef,
        reason: str,
        idempotency_key: str,
    ) -> PlatformCandidateOutcome:
        """把已验证个人能力复制为脱敏平台快照并登记平台候选。"""
        if not actor.is_admin:
            raise PermissionError("只有管理员可以提交平台候选")
        # 管理员跨 Owner 审核个人能力，必须走治理投影而非 Owner 可见目录。
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
        if pack is None:
            raise KeyError("能力包不存在或当前 Actor 不可见")
        if pack.scope is not ProcedureScope.PERSONAL:
            # 平台能力不能再次候选；个人 Owner 身份是候选的前提。
            return PlatformCandidateOutcome(
                status="rejected",
                gaps=("platform_scope",),
            )
        projection = self.runtime_projection_for_pack(pack)
        gaps: list[str] = []
        if projection.maturity is not CapabilityMaturity.VERIFIED:
            gaps.append("not_verified")
        if projection.lifecycle is not CapabilityLifecycle.ACTIVE:
            gaps.append("not_active")
        if projection.eligibility is not CapabilityEligibility.ELIGIBLE:
            gaps.append("not_eligible")
        if gaps:
            return PlatformCandidateOutcome(status="rejected", gaps=tuple(gaps))
        if self._platform_snapshot_generator is None:
            raise RuntimeError("能力治理未配置平台快照生成器")
        snapshot = self._platform_snapshot_generator.generate(pack)
        platform_target = CapabilityGovernanceTarget(
            owner_id=None,
            scope=ProcedureScope.PLATFORM,
            pack_id=pack.pack_id,
            version=pack.version,
            digest=snapshot.platform_digest,
        )
        # 确定性重打包保证同源快照 digest 相同：既有候选按平台目标命中幂等。
        existing = self._repository.get_latest_platform_event(
            platform_target, "platform_candidate"
        )
        if existing is not None:
            existing_runs = [
                run
                for run in self._repository.list_platform_validation_runs()
                if run.target == platform_target
            ]
            if existing_runs and all(
                run.status is ValidationRunStatus.FAILED
                for run in existing_runs
            ):
                # 候选事件幂等保留；验证失败后允许新运行重试（幂等键带序号），
                # 失败记录不覆盖（#34 同一纪律）。
                self._repository.create_platform_validation_run(
                    PlatformValidationRun(
                        actor_id=actor.owner_id,
                        actor_role=actor.role,
                        idempotency_key=(
                            f"candidate:{snapshot.platform_digest}"
                            f":retry:{len(existing_runs) + 1}"
                        ),
                        target=platform_target,
                        status=ValidationRunStatus.QUEUED,
                    )
                )
            return PlatformCandidateOutcome(
                status="already_submitted",
                snapshot=snapshot,
                event=existing,
            )
        event = CapabilityGovernanceEvent(
            event_type="platform_candidate",
            idempotency_key=idempotency_key,
            target=platform_target,
            maturity=CapabilityMaturity.VERIFIED,
            actor_id=actor.owner_id,
            actor_role=actor.role,
            reason=reason,
            source_digest=pack.digest,
            platform_digest=snapshot.platform_digest,
        )
        saved = self._repository.save_platform_event(event)
        # 候选登记后由平台 worker 推进六步验证与签名；运行幂等键绑定平台 digest。
        self._repository.create_platform_validation_run(
            PlatformValidationRun(
                actor_id=actor.owner_id,
                actor_role=actor.role,
                idempotency_key=f"candidate:{snapshot.platform_digest}",
                target=platform_target,
                status=ValidationRunStatus.QUEUED,
            )
        )
        return PlatformCandidateOutcome(
            status="created",
            snapshot=snapshot,
            event=saved,
        )

    def list_platform_candidates(
        self,
        actor: CatalogActor,
    ) -> tuple[PlatformCandidateSummary, ...]:
        """管理员读取平台候选脱敏摘要（含验证六步与签名状态）。"""
        if not actor.is_admin:
            raise PermissionError("只有管理员可以读取平台候选列表")
        items: list[PlatformCandidateSummary] = []
        for run in self._repository.list_platform_validation_runs():
            candidate = self._repository.get_latest_platform_event(
                run.target, "platform_candidate"
            )
            if candidate is None or candidate.source_digest is None:
                continue
            passed_steps = [
                item.step
                for item in run.evidence
                if item.status is ValidationStepStatus.PASSED
            ]
            items.append(
                PlatformCandidateSummary(
                    pack_id=run.target.pack_id,
                    version=run.target.version,
                    source_digest=candidate.source_digest,
                    platform_digest=run.target.digest,
                    validation_status=run.status.value,
                    steps_passed=len(passed_steps),
                    steps_total=len(PlatformValidationStep),
                    signed=(
                        run.signing_signature_digest is not None
                        and run.signing_public_key_sha256 is not None
                    ),
                    submitted_at=candidate.occurred_at,
                    reason=candidate.reason or "",
                )
            )
        return tuple(items)

    def publish_platform(
        self,
        actor: CatalogActor,
        *,
        pack_ref: CapabilityPackRef,
        reason: str,
        idempotency_key: str,
    ) -> PublishOutcome:
        """发布平台快照：候选存在 + 六步验证全绿 + 签名证据齐备才生效。"""
        if not actor.is_admin:
            raise PermissionError("只有管理员可以发布平台能力")
        platform_target = CapabilityGovernanceTarget(
            owner_id=None,
            scope=ProcedureScope.PLATFORM,
            pack_id=pack_ref.pack_id,
            version=pack_ref.version,
            digest=pack_ref.digest,
        )
        published = self._repository.get_latest_platform_event(
            platform_target, "platform_published"
        )
        if published is not None:
            return PublishOutcome(status="already_published", event=published)
        candidate = self._repository.get_latest_platform_event(
            platform_target, "platform_candidate"
        )
        if candidate is None:
            return PublishOutcome(status="not_ready", gaps=("no_candidate",))
        green_runs = [
            run
            for run in self._repository.list_platform_validation_runs()
            if run.target == platform_target
            and run.status is ValidationRunStatus.SUCCEEDED
            and len(run.evidence) == len(PlatformValidationStep)
            and {item.step for item in run.evidence}
            == set(PlatformValidationStep)
            and all(
                item.status is ValidationStepStatus.PASSED
                for item in run.evidence
            )
        ]
        if not green_runs:
            return PublishOutcome(
                status="not_ready",
                gaps=("validation_not_green",),
            )
        evidence = self._repository.get_latest_supply_chain_evidence(
            platform_target
        )
        if evidence is None:
            # 平台六步验证的 Trivy/Syft 共享供应链采集；证据缺失说明
            # 运行记录残缺，不能凭步骤摘要发布。
            return PublishOutcome(
                status="not_ready",
                gaps=("supply_chain_evidence_missing",),
            )
        if not self._trivy_database_current(evidence):
            # Issue AC6：漏洞库过期阻止新发布（按内容 UpdatedAt 判定）。
            return PublishOutcome(
                status="not_ready",
                gaps=("trivy_database_stale",),
            )
        run = green_runs[0]
        if (
            run.signing_signature_digest is None
            or run.signing_public_key_sha256 is None
        ):
            # 签名由平台 worker 在验证全绿后执行并写回运行记录，不复用个人签名。
            return PublishOutcome(
                status="not_ready",
                gaps=("signing_missing",),
            )
        if self._platform_publisher is None:
            # 发布目录写入是生效动作；缺失必须失败关闭，不能留下"投影已发布但
            # 目录无 pack"的永久孤儿（事件不可改写，重试会被 already_published 吞掉）。
            raise RuntimeError("能力治理未配置平台发布 Adapter")
        # 先写目录（INSERT OR IGNORE 幂等）再写不可变事件：目录失败时事件不落库，
        # 重试可完整重走；事件失败时目录多一行 pack，由下一次发布幂等覆盖。
        self._platform_publisher.save_pack(
            CapabilityPack(
                pack_id=platform_target.pack_id,
                version=platform_target.version,
                digest=platform_target.digest,
                scope=ProcedureScope.PLATFORM,
                maturity=LegacyCapabilityMaturity.VERIFIED,
            )
        )
        event = CapabilityGovernanceEvent(
            event_type="platform_published",
            # 幂等键由平台 digest 派生，调用方任意键不得产生第二条发布事实。
            idempotency_key=f"publish:{platform_target.digest}",
            target=platform_target,
            maturity=CapabilityMaturity.VERIFIED,
            actor_id=actor.owner_id,
            actor_role=actor.role,
            reason=reason,
            source_digest=candidate.source_digest,
            platform_digest=platform_target.digest,
            audience="admin_gray",
            platform_validation_run_id=run.run_id,
            signing_signature_digest=run.signing_signature_digest,
            signing_public_key_sha256=run.signing_public_key_sha256,
        )
        saved = self._repository.save_platform_event(event)
        return PublishOutcome(status="published", event=saved)

    def change_audience(
        self,
        actor: CatalogActor,
        *,
        pack_ref: CapabilityPackRef,
        audience: Literal["admin_gray", "users"],
        reason: str,
        idempotency_key: str,
    ) -> AudienceOutcome:
        """改变已发布平台能力的受众；#12 只实现命令，产品入口留待后续授权。"""
        if not actor.is_admin:
            raise PermissionError("只有管理员可以改变平台能力受众")
        platform_target = CapabilityGovernanceTarget(
            owner_id=None,
            scope=ProcedureScope.PLATFORM,
            pack_id=pack_ref.pack_id,
            version=pack_ref.version,
            digest=pack_ref.digest,
        )
        published = self._repository.get_latest_platform_event(
            platform_target, "platform_published"
        )
        if published is None:
            raise ValueError("平台能力尚未发布，不能改变受众")
        # AC7：受众变更必须重查当前事实——验证六步全绿（含 Trivy/Syft 扫描）
        # 且签名证据仍齐备，不能只凭历史发布事件放行。
        green_runs = [
            run
            for run in self._repository.list_platform_validation_runs()
            if run.target == platform_target
            and run.status is ValidationRunStatus.SUCCEEDED
            and len(run.evidence) == len(PlatformValidationStep)
            and {item.step for item in run.evidence}
            == set(PlatformValidationStep)
            and all(
                item.status is ValidationStepStatus.PASSED
                for item in run.evidence
            )
            and run.signing_signature_digest is not None
            and run.signing_public_key_sha256 is not None
        ]
        if not green_runs:
            raise ValueError("平台能力验证或签名证据不再有效，不能改变受众")
        evidence = self._repository.get_latest_supply_chain_evidence(
            platform_target
        )
        if evidence is None:
            raise ValueError("平台能力供应链证据缺失，不能改变受众")
        if not self._trivy_database_current(evidence):
            # Issue AC6：受众变更必须重查当前事实，漏洞库过期不能扩大受众。
            raise ValueError("漏洞库已过期，不能改变受众")
        current = self._repository.get_latest_platform_event(
            platform_target, "audience_changed"
        )
        if current is not None and current.audience == audience:
            return AudienceOutcome(status="already", event=current)
        event = CapabilityGovernanceEvent(
            event_type="audience_changed",
            idempotency_key=idempotency_key,
            target=platform_target,
            maturity=CapabilityMaturity.VERIFIED,
            actor_id=actor.owner_id,
            actor_role=actor.role,
            reason=reason,
            audience=audience,
        )
        saved = self._repository.save_platform_event(event)
        return AudienceOutcome(status="changed", event=saved)

    # ---- #14 治理状态命令：弃用/撤销/隔离/风险接受/恢复/回滚 ----

    @staticmethod
    def _trivy_database_current(
        evidence: CapabilitySupplyChainEvidence,
    ) -> bool:
        """漏洞库 7 天有效期判定（按内容 UpdatedAt，Issue AC6）。"""
        return (
            datetime.now(timezone.utc) - evidence.trivy_database.updated_at
            <= TRIVY_DATABASE_MAX_AGE
        )

    def _governable_pack(
        self,
        actor: CatalogActor,
        pack_ref: CapabilityPackRef,
    ) -> CapabilityPack:
        if not actor.is_admin:
            raise PermissionError("只有管理员可以执行能力治理命令")
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
        if pack is None:
            raise KeyError("能力包不存在或当前 Actor 不可见")
        return pack

    def _governance_idempotent_hit(
        self,
        target: CapabilityGovernanceTarget,
        event_type: str,
        idempotency_key: str,
    ) -> GovernanceCommandOutcome | None:
        """同幂等键重试返回既有事件；必须先于预期状态检查（幂等优先）。"""
        existing = self._repository.get_governance_event_by_idempotency(
            target, event_type, idempotency_key
        )
        if existing is not None:
            return GovernanceCommandOutcome(
                status="already_applied", event=existing
            )
        return None

    def _save_governance_event(
        self,
        target: CapabilityGovernanceTarget,
        event: CapabilityGovernanceEvent,
    ) -> GovernanceCommandOutcome:
        saved = self._repository.save_governance_event(event)
        status = (
            "applied"
            if saved.event_id == event.event_id
            else "already_applied"
        )
        return GovernanceCommandOutcome(status=status, event=saved)

    def deprecate_pack(
        self,
        actor: CatalogActor,
        *,
        pack_ref: CapabilityPackRef,
        reason: str,
        idempotency_key: str,
    ) -> GovernanceCommandOutcome:
        """弃用：新任务不再推荐，历史冻结任务与恢复任务仍可继续使用。"""
        pack = self._governable_pack(actor, pack_ref)
        target = self._target(pack)
        hit = self._governance_idempotent_hit(
            target, "lifecycle_changed", idempotency_key
        )
        if hit is not None:
            return hit
        projection = self.runtime_projection_for_pack(pack)
        if projection.lifecycle is not CapabilityLifecycle.ACTIVE:
            return GovernanceCommandOutcome(
                status="rejected", gaps=("not_active",)
            )
        return self._save_governance_event(
            target,
            CapabilityGovernanceEvent(
                event_type="lifecycle_changed",
                idempotency_key=idempotency_key,
                target=target,
                maturity=CapabilityMaturity.VERIFIED,
                lifecycle=CapabilityLifecycle.DEPRECATED,
                # 事件快照携带当前运行资格（隔离中的弃用不得冒充 eligible）。
                eligibility=projection.eligibility,
                actor_id=actor.owner_id,
                actor_role=actor.role,
                reason=reason,
            ),
        )

    def revoke_pack(
        self,
        actor: CatalogActor,
        *,
        pack_ref: CapabilityPackRef,
        reason: str,
        idempotency_key: str,
    ) -> GovernanceCommandOutcome:
        """撤销：禁止新任务、重试和恢复；历史事实不可改写。"""
        pack = self._governable_pack(actor, pack_ref)
        target = self._target(pack)
        hit = self._governance_idempotent_hit(
            target, "lifecycle_changed", idempotency_key
        )
        if hit is not None:
            return hit
        projection = self.runtime_projection_for_pack(pack)
        if projection.lifecycle is CapabilityLifecycle.REVOKED:
            return GovernanceCommandOutcome(
                status="rejected", gaps=("already_revoked",)
            )
        return self._save_governance_event(
            target,
            CapabilityGovernanceEvent(
                event_type="lifecycle_changed",
                idempotency_key=idempotency_key,
                target=target,
                maturity=CapabilityMaturity.VERIFIED,
                lifecycle=CapabilityLifecycle.REVOKED,
                # 事件快照携带当前运行资格（隔离中的撤销不得冒充 eligible）。
                eligibility=projection.eligibility,
                actor_id=actor.owner_id,
                actor_role=actor.role,
                reason=reason,
            ),
        )

    def quarantine_pack(
        self,
        actor: CatalogActor,
        *,
        pack_ref: CapabilityPackRef,
        reason: str,
        idempotency_key: str,
    ) -> GovernanceCommandOutcome:
        """隔离：自动或人工安全刹车；最终撤销或恢复由管理员决定。"""
        pack = self._governable_pack(actor, pack_ref)
        target = self._target(pack)
        hit = self._governance_idempotent_hit(
            target, "eligibility_changed", idempotency_key
        )
        if hit is not None:
            return hit
        projection = self.runtime_projection_for_pack(pack)
        if projection.eligibility is CapabilityEligibility.QUARANTINED:
            return GovernanceCommandOutcome(
                status="rejected", gaps=("already_quarantined",)
            )
        if projection.lifecycle is CapabilityLifecycle.REVOKED:
            # 已撤销的能力不需要隔离语义；恢复走 restore_pack 完整复查。
            return GovernanceCommandOutcome(
                status="rejected", gaps=("revoked",)
            )
        return self._save_governance_event(
            target,
            CapabilityGovernanceEvent(
                event_type="eligibility_changed",
                idempotency_key=idempotency_key,
                target=target,
                maturity=CapabilityMaturity.VERIFIED,
                # 事件快照携带当前生命周期（弃用中的隔离不得冒充 active）。
                lifecycle=projection.lifecycle,
                eligibility=CapabilityEligibility.QUARANTINED,
                actor_id=actor.owner_id,
                actor_role=actor.role,
                reason=reason,
            ),
        )

    def accept_pack_risk(
        self,
        actor: CatalogActor,
        *,
        pack_ref: CapabilityPackRef,
        reason: str,
        finding_ref: str,
        days: int = 30,
        idempotency_key: str,
    ) -> GovernanceCommandOutcome:
        """限期风险接受：仅隔离中的平台 admin_gray 能力可接受；默认 30 天、最长 90 天。"""
        if days < 1 or days > 90:
            return GovernanceCommandOutcome(
                status="rejected", gaps=("days_out_of_range",)
            )
        pack = self._governable_pack(actor, pack_ref)
        target = self._target(pack)
        hit = self._governance_idempotent_hit(
            target, "risk_accepted", idempotency_key
        )
        if hit is not None:
            return hit
        projection = self.runtime_projection_for_pack(pack)
        gaps: list[str] = []
        if projection.eligibility is not CapabilityEligibility.QUARANTINED:
            gaps.append("not_quarantined")
        if (
            target.scope is not ProcedureScope.PLATFORM
            or projection.audience != "admin_gray"
        ):
            # ADR-0029：风险接受只存在于 admin_gray 范围；个人能力不适用。
            gaps.append("not_admin_gray")
        evidence = self._repository.get_latest_supply_chain_evidence(target)
        if evidence is None:
            gaps.append("supply_chain_evidence_missing")
        elif evidence.status is SupplyChainEvidenceStatus.BLOCKED:
            # 任何 blocker（Secret/Critical/可修复 High/误配置/库过期）
            # 都是不可例外硬门（ADR-0029）；无修复 High 不产生 blocker。
            gaps.append("non_waivable_findings")
        finding_run = self._repository.get_validation_run(finding_ref)
        if finding_run is None or finding_run.target.digest != target.digest:
            # finding_ref 必须实引本包验证运行证据（Q2A 只豁免路径不可达
            # 的人工判定，不豁免引用存档的真实性；不得跨包引用）。
            gaps.append("finding_ref_unknown")
        if gaps:
            return GovernanceCommandOutcome(
                status="rejected", gaps=tuple(gaps)
            )
        return self._save_governance_event(
            target,
            CapabilityGovernanceEvent(
                event_type="risk_accepted",
                idempotency_key=idempotency_key,
                target=target,
                maturity=CapabilityMaturity.VERIFIED,
                # 事件快照携带当前生命周期（弃用中的接受不得冒充 active）。
                lifecycle=projection.lifecycle,
                eligibility=CapabilityEligibility.ELIGIBLE,
                actor_id=actor.owner_id,
                actor_role=actor.role,
                reason=reason,
                expires_at=datetime.now(timezone.utc) + timedelta(days=days),
                finding_ref=finding_ref,
            ),
        )

    def restore_pack(
        self,
        actor: CatalogActor,
        *,
        pack_ref: CapabilityPackRef,
        reason: str,
        idempotency_key: str,
    ) -> GovernanceCommandOutcome:
        """恢复：完整复查链（Q6A）通过后恢复生命周期并解除隔离。

        幂等优先于预期状态（双键：生命周期键与隔离解除派生键）；写序视
        状态选择——REVOKED 先写生命周期（携带 quarantined 快照）再解除
        隔离，其余先解除隔离（携带当前生命周期快照）再恢复生命周期，
        保证每个事件快照与写入时刻投影一致。
        """
        pack = self._governable_pack(actor, pack_ref)
        target = self._target(pack)
        lifecycle_hit = self._governance_idempotent_hit(
            target, "lifecycle_changed", idempotency_key
        )
        eligibility_hit = self._governance_idempotent_hit(
            target, "eligibility_changed", f"{idempotency_key}:eligibility"
        )
        # 两事件非原子：任一键命中都不能无条件返回——上次可能只写入了
        # 其中一条而另一条失败，重试必须按投影补写（部分应用不能被幂等吞掉）。
        projection = self.runtime_projection_for_pack(pack)
        needs_lifecycle_restore = projection.lifecycle in {
            CapabilityLifecycle.DEPRECATED,
            CapabilityLifecycle.REVOKED,
        }
        needs_eligibility_restore = (
            projection.eligibility is CapabilityEligibility.QUARANTINED
        )
        if not needs_lifecycle_restore and not needs_eligibility_restore:
            if lifecycle_hit is not None:
                return lifecycle_hit
            if eligibility_hit is not None:
                return eligibility_hit
            return GovernanceCommandOutcome(
                status="rejected", gaps=("nothing_to_restore",)
            )
        gaps = self._restore_recheck_gaps(target)
        if gaps:
            return GovernanceCommandOutcome(
                status="rejected", gaps=tuple(gaps)
            )
        last_event: CapabilityGovernanceEvent | None = None
        if lifecycle_hit is not None:
            last_event = lifecycle_hit.event
        if eligibility_hit is not None:
            last_event = eligibility_hit.event
        # 写序视状态选择：revoked 时先恢复生命周期（事件携带 quarantined
        # 快照合法），再解除隔离；否则先解除隔离（携带当前生命周期快照），
        # 保证每个事件快照与写入时刻投影一致。
        lifecycle_first = (
            projection.lifecycle is CapabilityLifecycle.REVOKED
        )
        if needs_lifecycle_restore and lifecycle_hit is None and lifecycle_first:
            outcome = self._save_governance_event(
                target,
                CapabilityGovernanceEvent(
                    event_type="lifecycle_changed",
                    idempotency_key=idempotency_key,
                    target=target,
                    maturity=CapabilityMaturity.VERIFIED,
                    lifecycle=CapabilityLifecycle.ACTIVE,
                    # 事件快照携带当前资格（可能 quarantined）。
                    eligibility=projection.eligibility,
                    actor_id=actor.owner_id,
                    actor_role=actor.role,
                    reason=reason,
                ),
            )
            last_event = outcome.event
        if needs_eligibility_restore and eligibility_hit is None:
            outcome = self._save_governance_event(
                target,
                CapabilityGovernanceEvent(
                    event_type="eligibility_changed",
                    idempotency_key=f"{idempotency_key}:eligibility",
                    target=target,
                    maturity=CapabilityMaturity.VERIFIED,
                    lifecycle=(
                        CapabilityLifecycle.ACTIVE
                        if lifecycle_first and needs_lifecycle_restore
                        else projection.lifecycle
                    ),
                    eligibility=CapabilityEligibility.ELIGIBLE,
                    actor_id=actor.owner_id,
                    actor_role=actor.role,
                    reason=reason,
                ),
            )
            last_event = outcome.event
        if needs_lifecycle_restore and lifecycle_hit is None and not lifecycle_first:
            outcome = self._save_governance_event(
                target,
                CapabilityGovernanceEvent(
                    event_type="lifecycle_changed",
                    idempotency_key=idempotency_key,
                    target=target,
                    maturity=CapabilityMaturity.VERIFIED,
                    lifecycle=CapabilityLifecycle.ACTIVE,
                    eligibility=CapabilityEligibility.ELIGIBLE,
                    actor_id=actor.owner_id,
                    actor_role=actor.role,
                    reason=reason,
                ),
            )
            last_event = outcome.event
        assert last_event is not None  # needs_* 判定已保证至少写一条
        return GovernanceCommandOutcome(status="applied", event=last_event)

    def _restore_recheck_gaps(
        self,
        target: CapabilityGovernanceTarget,
    ) -> list[str]:
        """恢复前完整复查：发布/签名证据、供应链证据与漏洞库时效、验证运行。"""
        gaps: list[str] = []
        if target.scope is ProcedureScope.PLATFORM:
            publication = self._repository.get_latest_platform_event(
                target, "platform_published"
            )
            if publication is None:
                gaps.append("publication_missing")
            elif (
                publication.signing_signature_digest is None
                or publication.signing_public_key_sha256 is None
            ):
                gaps.append("signature_evidence_invalid")
        evidence = self._repository.get_latest_supply_chain_evidence(target)
        if evidence is None:
            gaps.append("supply_chain_evidence_missing")
        elif evidence.status is SupplyChainEvidenceStatus.BLOCKED:
            gaps.extend(
                blocker for blocker in evidence.blockers
            )
        elif not self._trivy_database_current(evidence):
            # 恢复必须重查漏洞库时效（Q6A）；过期不能解除治理状态。
            gaps.append("trivy_database_stale")
        run = self._repository.get_latest_succeeded_validation_run(target)
        if run is None:
            gaps.append("validation_incomplete")
        return gaps

    def rollback_recommendation(
        self,
        actor: CatalogActor,
        *,
        pack_ref: CapabilityPackRef,
        reason: str,
        idempotency_key: str,
    ) -> GovernanceCommandOutcome:
        """回滚：原子改变新任务推荐指针；目标必须全绿且签名证据齐备。"""
        pack = self._governable_pack(actor, pack_ref)
        target = self._target(pack)
        hit = self._governance_idempotent_hit(
            target, "recommendation_changed", idempotency_key
        )
        if hit is not None:
            return hit
        projection = self.runtime_projection_for_pack(pack)
        gaps: list[str] = []
        if projection.maturity is not CapabilityMaturity.VERIFIED:
            gaps.append("not_verified")
        if projection.lifecycle is not CapabilityLifecycle.ACTIVE:
            gaps.append("not_active")
        if projection.eligibility is not CapabilityEligibility.ELIGIBLE:
            gaps.append("not_eligible")
        if target.scope is ProcedureScope.PLATFORM:
            publication = self._repository.get_latest_platform_event(
                target, "platform_published"
            )
            if publication is None:
                # 无发布事件即无签名证据；回滚目标必须签名有效（Issue AC2）。
                # 真实密码学验证由装载门（#13 唯一 Seam）在装载时执行。
                gaps.append("publication_missing")
            elif (
                publication.signing_signature_digest is None
                or publication.signing_public_key_sha256 is None
            ):
                gaps.append("signature_evidence_invalid")
            if projection.audience is None:
                # 受众未确定的目标不能成为推荐指针（Issue AC2 受众匹配）。
                gaps.append("audience_unknown")
        if gaps:
            return GovernanceCommandOutcome(
                status="rejected", gaps=tuple(gaps)
            )
        if projection.recommended_version == pack_ref.version:
            return GovernanceCommandOutcome(
                status="rejected", gaps=("already_recommended",)
            )
        return self._save_governance_event(
            target,
            CapabilityGovernanceEvent(
                event_type="recommendation_changed",
                idempotency_key=idempotency_key,
                target=target,
                maturity=CapabilityMaturity.VERIFIED,
                lifecycle=CapabilityLifecycle.ACTIVE,
                eligibility=CapabilityEligibility.ELIGIBLE,
                actor_id=actor.owner_id,
                actor_role=actor.role,
                reason=reason,
                recommended_version=pack_ref.version,
            ),
        )

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
