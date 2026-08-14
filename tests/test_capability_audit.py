# -*- coding: utf-8 -*-
"""AC-07-06：从 CapabilityGovernance 公共 Interface 验证管理员审核与业务内容审计查看。

分区编号对齐 task-breakdown：S1 模型、S2 Repository、S3 投影、S5 服务层命令。
S4（任务解析器正文/元数据读取）与 S6（HTTP 路由组）在各自测试文件中。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3

import pytest
from pydantic import ValidationError

from src.capability_catalog import (
    CapabilityCatalog,
    CapabilityPackRef,
    CatalogActor,
    InMemoryCapabilityCatalogRepository,
)
from src.capability_governance import (
    AdminReviewItem,
    AuditViewOutcome,
    BusinessContent,
    CapabilityEligibility,
    CapabilityGovernance,
    CapabilityGovernanceEvent,
    CapabilityGovernanceTarget,
    CapabilityLifecycle,
    CapabilityMaturity,
    CapabilitySupplyChainEvidence,
    CapabilityTaskMetadata,
    CapabilityValidationRun,
    InMemoryCapabilityGovernanceRepository,
    PromotionGap,
    SqliteCapabilityGovernanceRepository,
    SupplyChainEvidenceStatus,
    TrivyDatabaseMetadata,
    ValidationEvidence,
    ValidationRunStatus,
    ValidationStep,
    ValidationStepStatus,
    ValidationTaskRef,
    migrate_capability_governance,
)
from src.conversation_steering import CapabilityPack, ProcedureScope


def _target(
    digest_char: str = "a",
    *,
    pack_id: str = "python-table-summary",
    version: str = "1.0.0",
    owner_id: str = "owner-a",
) -> CapabilityGovernanceTarget:
    return CapabilityGovernanceTarget(
        owner_id=owner_id,
        scope=ProcedureScope.PERSONAL,
        pack_id=pack_id,
        version=version,
        digest="sha256:" + digest_char * 64,
    )


def _audit_event(
    target: CapabilityGovernanceTarget,
    **overrides: object,
) -> CapabilityGovernanceEvent:
    fields: dict[str, object] = {
        "event_type": "audit_viewed",
        "idempotency_key": "audit:task-1:revision:2:prompt",
        "actor_id": "admin-a",
        "actor_role": "admin",
        "reason": "排障：验证步骤与任务输出不一致，需要核对原始正文",
        "subject_type": "task_prompt",
        "subject_sha256": "e" * 64,
        "result": "succeeded",
        "task_id": "workspace-validated-source",
        "revision": 2,
        "failure_reason": None,
    }
    fields.update(overrides)
    return CapabilityGovernanceEvent(target=target, **fields)


def _task_ref(target: CapabilityGovernanceTarget) -> ValidationTaskRef:
    return ValidationTaskRef(
        task_id="workspace-validated-source",
        revision=2,
        source_snapshot_sha256="b" * 64,
        input_sha256="c" * 64,
        output_sha256="d" * 64,
        capability_digest=target.digest,
        authorization_id="grant-owner-a-task-replay",
    )


def _run(
    target: CapabilityGovernanceTarget,
    *,
    run_id: str,
    status: ValidationRunStatus,
    owner_id: str = "owner-a",
) -> CapabilityValidationRun:
    return CapabilityValidationRun(
        run_id=run_id,
        owner_id=owner_id,
        target=target,
        actor_id=owner_id,
        actor_role="user",
        idempotency_key=f"validate-{run_id}",
        task_ref=_task_ref(target),
        status=status,
    )


def _passed_evidence(step: ValidationStep, run_id: str) -> ValidationEvidence:
    return ValidationEvidence(
        step=step,
        status=ValidationStepStatus.PASSED,
        evidence_ref=f"evidence://validation/{run_id}/{step.value}",
        evidence_sha256="a" * 64,
        summary="验证步骤已通过",
    )


def _succeeded_run(
    target: CapabilityGovernanceTarget,
    *,
    run_id: str,
) -> CapabilityValidationRun:
    run = _run(target, run_id=run_id, status=ValidationRunStatus.SUCCEEDED)
    return run.model_copy(
        update={
            "evidence": tuple(_passed_evidence(step, run_id) for step in ValidationStep),
        }
    )


def _supply_evidence(
    target: CapabilityGovernanceTarget,
    *,
    status: SupplyChainEvidenceStatus = SupplyChainEvidenceStatus.PASSED,
    updated_at: datetime | None = None,
) -> CapabilitySupplyChainEvidence:
    return CapabilitySupplyChainEvidence(
        evidence_id="supply_" + "c" * 20,
        target=target,
        subject_digest=target.digest,
        status=status,
        trivy_version="0.70.0",
        trivy_config_sha256="0" * 64,
        trivy_result_sha256="1" * 64,
        trivy_database=TrivyDatabaseMetadata(
            version=1,
            updated_at=updated_at or datetime.now(timezone.utc),
        ),
        secret_count=0,
        critical_count=0,
        fixable_high_count=0,
        misconfiguration_failure_count=0,
        syft_version="1.50.0",
        syft_json_sha256="2" * 64,
        cyclonedx_json_sha256="3" * 64,
        cyclonedx_spec_version="1.6",
        blockers=(),
        occurred_at=datetime.now(timezone.utc),
    )


def _register(
    repository: InMemoryCapabilityGovernanceRepository,
    target: CapabilityGovernanceTarget,
    *,
    idempotency_key: str = "register-a",
) -> None:
    repository.save_event(
        CapabilityGovernanceEvent(
            idempotency_key=idempotency_key,
            target=target,
            actor_id=target.owner_id or "owner-a",
            actor_role="user",
        )
    )


def _governance(
    repository: InMemoryCapabilityGovernanceRepository,
) -> CapabilityGovernance:
    return CapabilityGovernance(
        CapabilityCatalog(InMemoryCapabilityCatalogRepository()),
        repository,
    )


def _registered_pack(
    repository: InMemoryCapabilityGovernanceRepository,
    *,
    owner_id: str = "owner-a",
    task_resolver: object | None = None,
) -> tuple[CapabilityGovernance, CapabilityGovernanceTarget]:
    """catalog 注册 pack + 治理登记事件，返回可用的治理服务与目标。"""
    catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
    governance = CapabilityGovernance(
        catalog,
        repository,
        task_resolver=task_resolver,
    )
    actor = CatalogActor(owner_id=owner_id, role="user")
    target = _target(owner_id=owner_id)
    pack = CapabilityPack(
        pack_id=target.pack_id,
        version=target.version,
        digest=target.digest,
        scope=ProcedureScope.PERSONAL,
        maturity=CapabilityMaturity.DRAFT,
        owner_id=owner_id,
    )
    catalog.register_pack(actor, pack)
    governance.register_pack(
        actor,
        pack_ref=CapabilityPackRef(
            pack_id=target.pack_id,
            version=target.version,
            digest=target.digest,
        ),
        idempotency_key="register-a",
    )
    return governance, target


class TestS1AuditEventModel:
    """audit_viewed 事件字段与分支校验；旧 payload 兼容。"""

    def test_audit_event_requires_reason(self) -> None:
        with pytest.raises(ValidationError):
            _audit_event(_target(), reason=None)

    def test_audit_event_requires_subject_and_result(self) -> None:
        with pytest.raises(ValidationError):
            _audit_event(_target(), subject_type=None)
        with pytest.raises(ValidationError):
            _audit_event(_target(), result=None)

    def test_audit_event_requires_task_and_revision(self) -> None:
        with pytest.raises(ValidationError):
            _audit_event(_target(), task_id=None)
        with pytest.raises(ValidationError):
            _audit_event(_target(), revision=None)

    def test_audit_event_rejects_nondefault_three_axes(self) -> None:
        with pytest.raises(ValidationError):
            _audit_event(_target(), maturity=CapabilityMaturity.VERIFIED)
        with pytest.raises(ValidationError):
            _audit_event(_target(), lifecycle=CapabilityLifecycle.DEPRECATED)
        with pytest.raises(ValidationError):
            _audit_event(_target(), eligibility=CapabilityEligibility.QUARANTINED)

    def test_audit_event_rejects_promotion_source_references(self) -> None:
        with pytest.raises(ValidationError):
            _audit_event(
                _target(),
                source_validation_run_id="capval_a1b2c3d4e5f6a1b2c3d4",
            )
        with pytest.raises(ValidationError):
            _audit_event(
                _target(),
                source_supply_chain_evidence_id="supply_" + "a" * 20,
            )

    def test_audit_event_succeeded_requires_content_hash(self) -> None:
        with pytest.raises(ValidationError):
            _audit_event(_target(), result="succeeded", subject_sha256=None)

    def test_audit_event_succeeded_rejects_failure_reason(self) -> None:
        with pytest.raises(ValidationError):
            _audit_event(
                _target(),
                result="succeeded",
                failure_reason="task_not_found",
            )

    def test_audit_event_failed_requires_typed_failure_reason(self) -> None:
        with pytest.raises(ValidationError):
            _audit_event(
                _target(),
                result="failed",
                subject_sha256=None,
                failure_reason=None,
            )
        event = _audit_event(
            _target(),
            result="failed",
            subject_sha256=None,
            failure_reason="task_not_found",
        )
        assert event.result == "failed"
        assert event.subject_sha256 is None
        assert event.failure_reason == "task_not_found"

    def test_registered_and_promoted_reject_all_audit_fields(self) -> None:
        audit_fields = (
            {"reason": "x"},
            {"subject_type": "task_prompt"},
            {"subject_sha256": "e" * 64},
            {"result": "failed"},
            {"task_id": "t"},
            {"revision": 1},
            {"failure_reason": "task_not_found"},
        )
        for extra in audit_fields:
            with pytest.raises(ValidationError):
                CapabilityGovernanceEvent(
                    idempotency_key="register-x",
                    target=_target(),
                    actor_id="owner-a",
                    actor_role="user",
                    **extra,
                )
        for extra in audit_fields:
            with pytest.raises(ValidationError):
                CapabilityGovernanceEvent(
                    event_type="promoted_to_verified",
                    idempotency_key="promotion:run-a",
                    target=_target(),
                    maturity=CapabilityMaturity.VERIFIED,
                    actor_id="owner-a",
                    actor_role="user",
                    source_validation_run_id="capval_a1b2c3d4e5f6a1b2c3d4",
                    source_supply_chain_evidence_id="supply_" + "a" * 20,
                    **extra,
                )

    def test_legacy_payload_without_audit_fields_round_trips(self) -> None:
        payload = json.dumps(
            {
                "event_id": "capgov_legacy01",
                "idempotency_key": "register-legacy",
                "target": _target().model_dump(mode="json"),
                "event_type": "registered",
                "maturity": "draft",
                "lifecycle": "active",
                "eligibility": "eligible",
                "actor_id": "owner-a",
                "actor_role": "user",
                "source_validation_run_id": None,
                "source_supply_chain_evidence_id": None,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        revived = CapabilityGovernanceEvent.model_validate_json(payload)
        assert revived.event_type == "registered"
        assert revived.reason is None
        assert revived.subject_type is None
        assert revived.subject_sha256 is None
        assert revived.result is None


class TestS1OutcomeModels:
    """AuditViewOutcome / BusinessContent / CapabilityTaskMetadata / AdminReviewItem。"""

    def _failed_event(self) -> CapabilityGovernanceEvent:
        return _audit_event(
            _target(),
            result="failed",
            subject_sha256=None,
            failure_reason="task_not_found",
        )

    def test_outcome_succeeded_requires_content(self) -> None:
        with pytest.raises(ValidationError):
            AuditViewOutcome(
                status="succeeded",
                content=None,
                truncated=False,
                event=_audit_event(_target()),
            )

    def test_outcome_failed_rejects_content(self) -> None:
        with pytest.raises(ValidationError):
            AuditViewOutcome(
                status="failed",
                content="正文",
                truncated=False,
                failure_reason="task_not_found",
                event=self._failed_event(),
            )

    def test_outcome_failed_requires_failure_reason(self) -> None:
        with pytest.raises(ValidationError):
            AuditViewOutcome(
                status="failed",
                content=None,
                truncated=False,
                event=self._failed_event(),
            )

    def test_outcome_failure_reason_must_match_event(self) -> None:
        with pytest.raises(ValidationError):
            AuditViewOutcome(
                status="failed",
                content=None,
                truncated=False,
                failure_reason="source_invalid",
                event=self._failed_event(),
            )
        outcome = AuditViewOutcome(
            status="failed",
            content=None,
            truncated=False,
            failure_reason="task_not_found",
            event=self._failed_event(),
        )
        assert outcome.failure_reason == "task_not_found"

    def test_outcome_status_matches_event_result(self) -> None:
        with pytest.raises(ValidationError):
            AuditViewOutcome(
                status="succeeded",
                content="正文",
                truncated=False,
                event=self._failed_event(),
            )

    def test_outcome_succeeded_with_truncated_content_is_valid(self) -> None:
        outcome = AuditViewOutcome(
            status="succeeded",
            content="截断正文",
            truncated=True,
            event=_audit_event(_target()),
        )
        assert outcome.truncated is True

    def test_business_content_succeeded_requires_hash(self) -> None:
        with pytest.raises(ValidationError):
            BusinessContent(
                status="succeeded",
                subject_type="task_prompt",
                content="正文",
                content_sha256=None,
                size_bytes=6,
            )

    def test_business_content_failed_requires_reason_without_content(self) -> None:
        with pytest.raises(ValidationError):
            BusinessContent(
                status="failed",
                subject_type="task_prompt",
                content="正文",
                content_sha256=None,
                size_bytes=0,
                failure_reason="missing_file",
            )
        with pytest.raises(ValidationError):
            BusinessContent(
                status="failed",
                subject_type="task_prompt",
                content="",
                content_sha256=None,
                size_bytes=0,
                failure_reason=None,
            )

    def test_task_metadata_holds_management_fields_only(self) -> None:
        metadata = CapabilityTaskMetadata(
            task_id="workspace-validated-source",
            revision=2,
            owner_id="owner-a",
            task_status="completed",
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
            input_count=1,
            input_types=("csv",),
            output_count=1,
            output_formats=("csv",),
        )
        assert metadata.input_count == 1
        assert metadata.input_types == ("csv",)

    def test_task_metadata_forbids_business_fields(self) -> None:
        with pytest.raises(ValidationError):
            CapabilityTaskMetadata(
                task_id="t",
                revision=1,
                owner_id="owner-a",
                task_status="completed",
                created_at="2026-08-14T00:00:00+00:00",
                updated_at="2026-08-14T00:00:00+00:00",
                objective_text="不应出现在管理元数据里的业务正文",
            )

    def test_admin_review_item_holds_sanitized_fields_only(self) -> None:
        item = AdminReviewItem(
            pack_id="python-table-summary",
            version="1.0.0",
            scope=ProcedureScope.PERSONAL,
            maturity=CapabilityMaturity.DRAFT,
            lifecycle=CapabilityLifecycle.ACTIVE,
            eligibility=CapabilityEligibility.ELIGIBLE,
            source="governance_event",
            owner_id="owner-a",
            digest="sha256:" + "a" * 64,
        )
        assert item.pack_id == "python-table-summary"
        assert item.promotion_gaps == ()

    def test_admin_review_item_forbids_prompt_fields(self) -> None:
        with pytest.raises(ValidationError):
            AdminReviewItem(
                pack_id="python-table-summary",
                version="1.0.0",
                scope=ProcedureScope.PERSONAL,
                maturity=CapabilityMaturity.DRAFT,
                lifecycle=CapabilityLifecycle.ACTIVE,
                eligibility=CapabilityEligibility.ELIGIBLE,
                source="governance_event",
                owner_id="owner-a",
                digest="sha256:" + "a" * 64,
                objective_text="业务正文不允许进入审核聚合项",
            )


class TestS2AuditEventRepository:
    """审计事件专用入口：幂等、串类型拒绝、按 target 列表。"""

    def test_memory_save_audit_event_is_idempotent(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        target = _target()
        event = _audit_event(target)
        first = repository.save_audit_view_event(event)
        second = repository.save_audit_view_event(
            _audit_event(target, event_id="capgov_other_id")
        )
        assert second == first

    def test_memory_save_audit_event_rejects_other_types(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        with pytest.raises(ValueError):
            repository.save_audit_view_event(
                CapabilityGovernanceEvent(
                    idempotency_key="register-x",
                    target=_target(),
                    actor_id="owner-a",
                    actor_role="user",
                )
            )
        with pytest.raises(ValueError):
            repository.save_audit_view_event(
                CapabilityGovernanceEvent(
                    event_type="promoted_to_verified",
                    idempotency_key="promotion:run-a",
                    target=_target(),
                    maturity=CapabilityMaturity.VERIFIED,
                    actor_id="owner-a",
                    actor_role="user",
                    source_validation_run_id="capval_a1b2c3d4e5f6a1b2c3d4",
                    source_supply_chain_evidence_id="supply_" + "a" * 20,
                )
            )

    def test_memory_generic_save_event_rejects_audit_events(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        with pytest.raises(ValueError):
            repository.save_event(_audit_event(_target()))

    def test_memory_list_audit_events_filters_by_target_and_sorts(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        target_a = _target(digest_char="a")
        target_b = _target(digest_char="b")
        first = repository.save_audit_view_event(
            _audit_event(
                target_a,
                idempotency_key="audit:one",
                occurred_at=datetime(2026, 8, 14, 1, 0, tzinfo=timezone.utc),
            )
        )
        second = repository.save_audit_view_event(
            _audit_event(
                target_b,
                idempotency_key="audit:two",
                occurred_at=datetime(2026, 8, 14, 2, 0, tzinfo=timezone.utc),
            )
        )
        third = repository.save_audit_view_event(
            _audit_event(
                target_a,
                idempotency_key="audit:three",
                occurred_at=datetime(2026, 8, 14, 3, 0, tzinfo=timezone.utc),
            )
        )
        assert repository.list_audit_view_events(target_a) == (first, third)
        # 全量按 occurred_at 升序：1 点、2 点、3 点。
        assert repository.list_audit_view_events() == (first, second, third)

    def test_sqlite_save_audit_event_is_idempotent_and_scoped(self, tmp_path) -> None:
        db_path = tmp_path / "webui.db"
        migrate_capability_governance(db_path, tmp_path / "backup.db")
        repository = SqliteCapabilityGovernanceRepository(str(db_path))
        target = _target()
        first = repository.save_audit_view_event(_audit_event(target))
        second = repository.save_audit_view_event(
            _audit_event(target, event_id="capgov_other_sqlite_id")
        )
        assert second == first
        assert repository.list_audit_view_events(target) == (first,)
        assert repository.list_audit_view_events() == (first,)

    def test_sqlite_save_audit_event_rejects_other_types(self, tmp_path) -> None:
        db_path = tmp_path / "webui.db"
        migrate_capability_governance(db_path, tmp_path / "backup.db")
        repository = SqliteCapabilityGovernanceRepository(str(db_path))
        with pytest.raises(ValueError):
            repository.save_audit_view_event(
                CapabilityGovernanceEvent(
                    idempotency_key="register-x",
                    target=_target(),
                    actor_id="owner-a",
                    actor_role="user",
                )
            )

    def test_sqlite_latest_succeeded_run_for_platform_target(self, tmp_path) -> None:
        """平台能力无个人 Owner；成功运行按能力身份查询，不按 owner 列过滤。"""
        db_path = tmp_path / "webui.db"
        migrate_capability_governance(db_path, tmp_path / "backup.db")
        repository = SqliteCapabilityGovernanceRepository(str(db_path))
        platform_target = CapabilityGovernanceTarget(
            owner_id=None,
            scope=ProcedureScope.PLATFORM,
            pack_id="gray-python-table",
            version="1.0.0",
            digest="sha256:" + "a" * 64,
        )
        # 真实数据形态：run.owner_id 是发起验证的管理员，run.target.owner_id 为 None。
        repository.create_validation_run(
            _succeeded_run(platform_target, run_id="capval_platform_run")
        )
        found = repository.get_latest_succeeded_validation_run(platform_target)
        assert found is not None
        assert found.run_id == "capval_platform_run"

    def test_sqlite_latest_succeeded_run_keeps_owner_isolation(self, tmp_path) -> None:
        """个人能力的成功运行仍按 owner 隔离；跨 owner 查不到。"""
        db_path = tmp_path / "webui.db"
        migrate_capability_governance(db_path, tmp_path / "backup.db")
        repository = SqliteCapabilityGovernanceRepository(str(db_path))
        target_a = _target(owner_id="owner-a")
        repository.create_validation_run(
            _succeeded_run(target_a, run_id="capval_owner_a_run")
        )
        other = _target(owner_id="owner-b", pack_id="python-table-summary")
        assert (
            repository.get_latest_succeeded_validation_run(other) is None
        )

    def test_sqlite_audit_event_coexists_with_governance_events(self, tmp_path) -> None:
        """audit 事件与 registered/promoted 同表共存；list_events 全量返回（投影过滤在 S3）。"""
        db_path = tmp_path / "webui.db"
        migrate_capability_governance(db_path, tmp_path / "backup.db")
        repository = SqliteCapabilityGovernanceRepository(str(db_path))
        target = _target()
        repository.save_event(
            CapabilityGovernanceEvent(
                idempotency_key="register-a",
                target=target,
                actor_id="owner-a",
                actor_role="user",
            )
        )
        repository.save_promotion_event(
            CapabilityGovernanceEvent(
                event_type="promoted_to_verified",
                idempotency_key="promotion:run-a",
                target=target,
                maturity=CapabilityMaturity.VERIFIED,
                actor_id="owner-a",
                actor_role="user",
                source_validation_run_id="capval_a1b2c3d4e5f6a1b2c3d4",
                source_supply_chain_evidence_id="supply_" + "a" * 20,
            )
        )
        audit = repository.save_audit_view_event(_audit_event(target))
        all_events = repository.list_events(target)
        assert len(all_events) == 3
        assert all_events[-1] == audit
        assert repository.list_audit_view_events(target) == (audit,)


def _register_pack_in(
    catalog: CapabilityCatalog,
    governance: CapabilityGovernance,
    *,
    owner_id: str,
    digest_char: str,
    pack_id: str = "python-table-summary",
) -> CapabilityGovernanceTarget:
    actor = CatalogActor(owner_id=owner_id, role="user")
    target = _target(owner_id=owner_id, digest_char=digest_char, pack_id=pack_id)
    catalog.register_pack(
        actor,
        CapabilityPack(
            pack_id=target.pack_id,
            version=target.version,
            digest=target.digest,
            scope=ProcedureScope.PERSONAL,
            maturity=CapabilityMaturity.DRAFT,
            owner_id=owner_id,
        ),
    )
    governance.register_pack(
        actor,
        pack_ref=CapabilityPackRef(
            pack_id=target.pack_id,
            version=target.version,
            digest=target.digest,
        ),
        idempotency_key=f"register-{owner_id}",
    )
    return target


def _metadata() -> CapabilityTaskMetadata:
    return CapabilityTaskMetadata(
        task_id="workspace-validated-source",
        revision=2,
        owner_id="owner-a",
        task_status="completed",
        created_at="2026-08-14T00:00:00+00:00",
        updated_at="2026-08-14T01:00:00+00:00",
        input_count=1,
        input_types=("csv",),
        output_count=1,
        output_formats=("csv",),
    )


def _succeeded_content(
    subject_type: str = "task_prompt",
    text: str = "季度销售汇总正文",
) -> BusinessContent:
    return BusinessContent(
        status="succeeded",
        subject_type=subject_type,
        content=text,
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        size_bytes=len(text.encode("utf-8")),
    )


class _StubTaskResolver:
    """S5 服务层测试替身；断言只落在服务层可观察结果，不测私有查询函数。"""

    def __init__(
        self,
        metadata: CapabilityTaskMetadata | Exception | None = None,
        content: BusinessContent | Exception | None = None,
    ) -> None:
        self._metadata = metadata
        self._content = content
        self.metadata_calls: list[tuple[str, int, str]] = []
        self.content_calls: list[tuple[str, int, str, str]] = []

    def read_task_metadata(
        self,
        actor: CatalogActor,
        task_id: str,
        revision: int,
        *,
        task_owner_id: str,
    ) -> CapabilityTaskMetadata:
        self.metadata_calls.append((task_id, revision, task_owner_id))
        if isinstance(self._metadata, Exception):
            raise self._metadata
        if self._metadata is None:
            raise AssertionError("未配置元数据替身")
        return self._metadata

    def read_business_content(
        self,
        actor: CatalogActor,
        task_id: str,
        revision: int,
        subject_type: str,
        *,
        task_owner_id: str,
    ) -> BusinessContent:
        self.content_calls.append(
            (task_id, revision, subject_type, task_owner_id)
        )
        if isinstance(self._content, Exception):
            raise self._content
        if self._content is None:
            raise AssertionError("未配置正文替身")
        return self._content

    def list_options(self, actor: CatalogActor, target) -> tuple:
        return ()

    def resolve(self, actor: CatalogActor, target, *, task_id: str, revision: int):
        raise NotImplementedError

    def verify(self, actor: CatalogActor, target, task_ref):
        raise NotImplementedError

    def verify_independent_verifier(
        self, actor: CatalogActor, target, task_ref
    ) -> str:
        raise NotImplementedError

    def load_replay_request(self, actor: CatalogActor, target, task_ref):
        raise NotImplementedError


class TestS3ProjectionIsolation:
    """审计查看事件不参与三轴投影；缺口评估不受审计记录影响。"""

    def _view(self, governance: CapabilityGovernance):
        views = governance.list_visible_projections(
            CatalogActor(owner_id="admin-x", role="admin")
        )
        assert len(views) == 1
        return views[0]

    def test_audit_event_does_not_change_draft_projection(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _registered_pack(repository)
        repository.save_audit_view_event(_audit_event(target))
        view = self._view(governance)
        assert view.maturity is CapabilityMaturity.DRAFT
        assert view.lifecycle is CapabilityLifecycle.ACTIVE
        assert view.eligibility is CapabilityEligibility.ELIGIBLE

    def test_audit_event_does_not_override_verified_projection(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _registered_pack(repository)
        repository.save_promotion_event(
            CapabilityGovernanceEvent(
                event_type="promoted_to_verified",
                idempotency_key="promotion:run-a",
                target=target,
                maturity=CapabilityMaturity.VERIFIED,
                actor_id="owner-a",
                actor_role="user",
                source_validation_run_id="capval_a1b2c3d4e5f6a1b2c3d4",
                source_supply_chain_evidence_id="supply_" + "a" * 20,
            )
        )
        repository.save_audit_view_event(_audit_event(target))
        view = self._view(governance)
        assert view.maturity is CapabilityMaturity.VERIFIED

    def test_audit_event_does_not_affect_promotion_gaps(self) -> None:
        repository = InMemoryCapabilityGovernanceRepository()
        governance, target = _registered_pack(repository)
        repository.save_audit_view_event(_audit_event(target))
        view = self._view(governance)
        assert PromotionGap.VALIDATION_INCOMPLETE in view.promotion_gaps
        assert (
            PromotionGap.SUPPLY_CHAIN_EVIDENCE_MISSING in view.promotion_gaps
        )


class TestS5ServiceCommands:
    """服务层：审核聚合与审计查看命令。"""

    def _admin_governance(
        self,
        resolver: _StubTaskResolver,
        *,
        with_run: bool = False,
    ) -> tuple[
        CapabilityGovernance,
        InMemoryCapabilityGovernanceRepository,
        CapabilityGovernanceTarget,
    ]:
        repository = InMemoryCapabilityGovernanceRepository()
        catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
        governance = CapabilityGovernance(
            catalog, repository, task_resolver=resolver
        )
        target = _register_pack_in(
            catalog, governance, owner_id="owner-a", digest_char="a"
        )
        if with_run:
            # 审计查看只针对验证证据绑定的任务；_task_ref 的
            # task_id="workspace-validated-source" revision=2 即匹配键。
            repository.create_validation_run(
                _succeeded_run(target, run_id="capval_a1b2c3d4e5f6a1b2c3d4")
            )
        return governance, repository, target

    def test_admin_review_lists_cross_owner_items(self) -> None:
        resolver = _StubTaskResolver(metadata=_metadata())
        repository = InMemoryCapabilityGovernanceRepository()
        catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
        governance = CapabilityGovernance(
            catalog, repository, task_resolver=resolver
        )
        target_a = _register_pack_in(
            catalog, governance, owner_id="owner-a", digest_char="a"
        )
        target_b = _register_pack_in(
            catalog,
            governance,
            owner_id="owner-b",
            digest_char="b",
            pack_id="python-table-summary-b",
        )
        repository.create_validation_run(
            _succeeded_run(target_a, run_id="capval_a1b2c3d4e5f6a1b2c3d4")
        )
        repository.create_validation_run(
            _succeeded_run(target_b, run_id="capval_a1b2c3d4e5f6a1b2c3d5")
        )
        repository.save_supply_chain_evidence(_supply_evidence(target_a))
        items = governance.list_admin_review(
            CatalogActor(owner_id="admin-x", role="admin")
        )
        assert len(items) == 2
        item_a = next(item for item in items if item.pack_id == target_a.pack_id)
        assert item_a.owner_id == "owner-a"
        assert item_a.validation is not None
        assert item_a.supply_chain is not None
        assert item_a.task_metadata is not None
        assert item_a.task_metadata.task_id == "workspace-validated-source"
        item_b = next(item for item in items if item.pack_id == target_b.pack_id)
        assert item_b.owner_id == "owner-b"
        assert item_b.supply_chain is None

    def test_admin_review_rejects_non_admin(self) -> None:
        resolver = _StubTaskResolver()
        governance, _, _ = self._admin_governance(resolver)
        with pytest.raises(PermissionError):
            governance.list_admin_review(
                CatalogActor(owner_id="owner-a", role="user")
            )

    def test_admin_review_metadata_failure_is_none(self) -> None:
        resolver = _StubTaskResolver(metadata=KeyError("任务管理信息不存在"))
        repository = InMemoryCapabilityGovernanceRepository()
        catalog = CapabilityCatalog(InMemoryCapabilityCatalogRepository())
        governance = CapabilityGovernance(
            catalog, repository, task_resolver=resolver
        )
        target = _register_pack_in(
            catalog, governance, owner_id="owner-a", digest_char="a"
        )
        repository.create_validation_run(
            _succeeded_run(target, run_id="capval_a1b2c3d4e5f6a1b2c3d4")
        )
        items = governance.list_admin_review(
            CatalogActor(owner_id="admin-x", role="admin")
        )
        assert len(items) == 1
        assert items[0].task_metadata is None

    def test_admin_review_without_validation_run_has_none_fields(self) -> None:
        resolver = _StubTaskResolver(metadata=_metadata())
        governance, _, _ = self._admin_governance(resolver)
        items = governance.list_admin_review(
            CatalogActor(owner_id="admin-x", role="admin")
        )
        assert len(items) == 1
        assert items[0].validation is None
        assert items[0].task_metadata is None
        assert items[0].promotion_gaps != ()

    def test_audit_view_success_writes_event_and_returns_content(self) -> None:
        content = _succeeded_content()
        resolver = _StubTaskResolver(content=content)
        governance, repository, target = self._admin_governance(
            resolver, with_run=True
        )
        outcome = governance.audit_view_business_content(
            CatalogActor(owner_id="admin-x", role="admin"),
            pack_ref=CapabilityPackRef(
                pack_id=target.pack_id,
                version=target.version,
                digest=target.digest,
            ),
            task_id="workspace-validated-source",
            revision=2,
            subject_type="task_prompt",
            reason="排障：验证步骤与任务输出不一致，需要核对原始正文",
            idempotency_key="audit:one",
        )
        assert outcome.status == "succeeded"
        assert outcome.content == content.content
        assert outcome.event.subject_sha256 == content.content_sha256
        assert outcome.event.reason.startswith("排障")
        assert outcome.event.task_id == "workspace-validated-source"
        assert outcome.event.revision == 2
        records = repository.list_audit_view_events(target)
        assert records == (outcome.event,)
        # 审计查看不改变三轴投影。
        views = governance.list_visible_projections(
            CatalogActor(owner_id="admin-x", role="admin")
        )
        assert views[0].maturity is CapabilityMaturity.DRAFT

    def test_audit_view_requires_bound_validation_task(self) -> None:
        resolver = _StubTaskResolver(content=_succeeded_content())
        governance, repository, target = self._admin_governance(
            resolver, with_run=True
        )
        with pytest.raises(ValueError):
            # 任务身份必须与验证证据绑定的冻结任务一致，防止借能力包读任意任务。
            governance.audit_view_business_content(
                CatalogActor(owner_id="admin-x", role="admin"),
                pack_ref=CapabilityPackRef(
                    pack_id=target.pack_id,
                    version=target.version,
                    digest=target.digest,
                ),
                task_id="unrelated-task",
                revision=9,
                subject_type="task_prompt",
                reason="排障：核对验证证据关联任务正文",
                idempotency_key="audit:unbound",
            )
        assert repository.list_audit_view_events(target) == ()

    def test_audit_view_without_succeeded_run_rejected(self) -> None:
        resolver = _StubTaskResolver(content=_succeeded_content())
        governance, _, target = self._admin_governance(resolver)
        with pytest.raises(ValueError):
            governance.audit_view_business_content(
                CatalogActor(owner_id="admin-x", role="admin"),
                pack_ref=CapabilityPackRef(
                    pack_id=target.pack_id,
                    version=target.version,
                    digest=target.digest,
                ),
                task_id="workspace-validated-source",
                revision=2,
                subject_type="task_prompt",
                reason="排障：核对验证证据关联任务正文",
                idempotency_key="audit:no-run",
            )

    def test_audit_view_rejects_short_reason(self) -> None:
        resolver = _StubTaskResolver(content=_succeeded_content())
        governance, _, target = self._admin_governance(resolver)
        with pytest.raises(ValueError):
            governance.audit_view_business_content(
                CatalogActor(owner_id="admin-x", role="admin"),
                pack_ref=CapabilityPackRef(
                    pack_id=target.pack_id,
                    version=target.version,
                    digest=target.digest,
                ),
                task_id="workspace-validated-source",
                revision=2,
                subject_type="task_prompt",
                reason="短",
                idempotency_key="audit:short",
            )

    def test_audit_view_rejects_non_admin(self) -> None:
        resolver = _StubTaskResolver(content=_succeeded_content())
        governance, _, target = self._admin_governance(resolver)
        with pytest.raises(PermissionError):
            governance.audit_view_business_content(
                CatalogActor(owner_id="owner-a", role="user"),
                pack_ref=CapabilityPackRef(
                    pack_id=target.pack_id,
                    version=target.version,
                    digest=target.digest,
                ),
                task_id="workspace-validated-source",
                revision=2,
                subject_type="task_prompt",
                reason="排障：需要核对原始正文内容",
                idempotency_key="audit:forbidden",
            )

    def test_audit_view_unknown_pack_raises_keyerror(self) -> None:
        resolver = _StubTaskResolver(content=_succeeded_content())
        governance, _, target = self._admin_governance(
            resolver, with_run=True
        )
        with pytest.raises(KeyError):
            governance.audit_view_business_content(
                CatalogActor(owner_id="admin-x", role="admin"),
                pack_ref=CapabilityPackRef(
                    pack_id="no-such-pack",
                    version=target.version,
                    digest=target.digest,
                ),
                task_id="workspace-validated-source",
                revision=2,
                subject_type="task_prompt",
                reason="排障：核对验证证据关联任务正文",
                idempotency_key="audit:unknown",
            )

    def test_audit_view_failure_still_records(self) -> None:
        failed = BusinessContent(
            status="failed",
            subject_type="task_prompt",
            failure_reason="task_not_found",
        )
        resolver = _StubTaskResolver(content=failed)
        governance, repository, target = self._admin_governance(
            resolver, with_run=True
        )
        outcome = governance.audit_view_business_content(
            CatalogActor(owner_id="admin-x", role="admin"),
            pack_ref=CapabilityPackRef(
                pack_id=target.pack_id,
                version=target.version,
                digest=target.digest,
            ),
            task_id="workspace-validated-source",
            revision=2,
            subject_type="task_prompt",
            reason="排障：任务可能已被清理，尝试核对",
            idempotency_key="audit:fail",
        )
        assert outcome.status == "failed"
        assert outcome.content is None
        assert outcome.failure_reason == "task_not_found"
        records = repository.list_audit_view_events(target)
        assert len(records) == 1
        assert records[0].result == "failed"
        assert records[0].subject_sha256 is None
        assert records[0].failure_reason == "task_not_found"
        assert records[0].task_id == "workspace-validated-source"
        assert records[0].reason.startswith("排障")

    def test_audit_view_idempotent_retry_returns_same_event(self) -> None:
        resolver = _StubTaskResolver(content=_succeeded_content())
        governance, repository, target = self._admin_governance(
            resolver, with_run=True
        )
        common = dict(
            actor=CatalogActor(owner_id="admin-x", role="admin"),
            pack_ref=CapabilityPackRef(
                pack_id=target.pack_id,
                version=target.version,
                digest=target.digest,
            ),
            task_id="workspace-validated-source",
            revision=2,
            subject_type="task_prompt",
            reason="排障：核对原始正文内容",
            idempotency_key="audit:same",
        )
        first = governance.audit_view_business_content(**common)
        second = governance.audit_view_business_content(**common)
        assert second.event.event_id == first.event.event_id
        assert len(repository.list_audit_view_events(target)) == 1
