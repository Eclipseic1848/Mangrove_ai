# -*- coding: utf-8 -*-
"""能力治理事件 Repository 的公共契约与内存 Adapter。"""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import threading
from typing import Protocol

from .models import (
    CapabilityGovernanceEvent,
    CapabilityGovernanceTarget,
    CapabilityValidationRun,
    CapabilitySupplyChainEvidence,
    PlatformValidationRun,
    ValidationRunStatus,
)


def _validation_request_hash(run: CapabilityValidationRun) -> str:
    payload = json.dumps(
        {
            "target": run.target.model_dump(mode="json"),
            "task_ref": run.task_ref.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class CapabilityGovernanceRepository(Protocol):
    def save_event(
        self,
        event: CapabilityGovernanceEvent,
    ) -> CapabilityGovernanceEvent: ...

    def get_by_idempotency(
        self,
        target: CapabilityGovernanceTarget,
        idempotency_key: str,
    ) -> CapabilityGovernanceEvent | None: ...

    def list_events(
        self,
        target: CapabilityGovernanceTarget | None = None,
    ) -> tuple[CapabilityGovernanceEvent, ...]: ...

    def create_validation_run(
        self,
        run: CapabilityValidationRun,
    ) -> CapabilityValidationRun: ...

    def get_validation_run(self, run_id: str) -> CapabilityValidationRun | None: ...

    def list_validation_runs(self) -> tuple[CapabilityValidationRun, ...]: ...

    def save_validation_run(
        self,
        run: CapabilityValidationRun,
        *,
        lease_worker_id: str | None = None,
        lease_now: datetime | None = None,
    ) -> CapabilityValidationRun: ...

    def acquire_validation_lease(
        self,
        *,
        run_id: str,
        digest: str,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool: ...

    def release_validation_lease(self, run_id: str, worker_id: str) -> None: ...

    def renew_validation_lease(
        self,
        *,
        run_id: str,
        digest: str,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool: ...

    def save_supply_chain_evidence(
        self,
        evidence: CapabilitySupplyChainEvidence,
    ) -> CapabilitySupplyChainEvidence: ...

    def get_latest_supply_chain_evidence(
        self,
        target: CapabilityGovernanceTarget,
    ) -> CapabilitySupplyChainEvidence | None: ...

    def save_promotion_event(
        self,
        event: CapabilityGovernanceEvent,
    ) -> CapabilityGovernanceEvent: ...

    def get_latest_promotion_event(
        self,
        target: CapabilityGovernanceTarget,
    ) -> CapabilityGovernanceEvent | None: ...

    def get_latest_succeeded_validation_run(
        self,
        target: CapabilityGovernanceTarget,
    ) -> CapabilityValidationRun | None: ...

    def save_audit_view_event(
        self,
        event: CapabilityGovernanceEvent,
    ) -> CapabilityGovernanceEvent: ...

    def list_audit_view_events(
        self,
        target: CapabilityGovernanceTarget | None = None,
    ) -> tuple[CapabilityGovernanceEvent, ...]: ...

    def save_platform_event(
        self,
        event: CapabilityGovernanceEvent,
    ) -> CapabilityGovernanceEvent: ...

    def save_governance_event(
        self,
        event: CapabilityGovernanceEvent,
    ) -> CapabilityGovernanceEvent: ...

    def get_governance_event_by_idempotency(
        self,
        target: CapabilityGovernanceTarget,
        event_type: str,
        idempotency_key: str,
    ) -> CapabilityGovernanceEvent | None: ...

    def get_latest_platform_event(
        self,
        target: CapabilityGovernanceTarget,
        event_type: str,
    ) -> CapabilityGovernanceEvent | None: ...

    def list_platform_events(
        self,
        target: CapabilityGovernanceTarget,
    ) -> tuple[CapabilityGovernanceEvent, ...]: ...

    def create_platform_validation_run(
        self,
        run: PlatformValidationRun,
    ) -> PlatformValidationRun: ...

    def get_platform_validation_run(
        self,
        run_id: str,
    ) -> PlatformValidationRun | None: ...

    def list_platform_validation_runs(self) -> tuple[PlatformValidationRun, ...]: ...

    def save_platform_validation_run(
        self,
        run: PlatformValidationRun,
    ) -> PlatformValidationRun: ...

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

    def renew_platform_validation_lease(
        self,
        *,
        run_id: str,
        digest: str,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool: ...


def _target_key(target: CapabilityGovernanceTarget) -> tuple[str | None, str, str, str]:
    return (target.owner_id, target.pack_id, target.version, target.digest)


class InMemoryCapabilityGovernanceRepository:
    def __init__(self) -> None:
        self._events: dict[str, CapabilityGovernanceEvent] = {}
        self._idempotency: dict[
            tuple[str | None, str, str, str, str], str
        ] = {}
        self._events_lock = threading.Lock()
        self._validation_runs: dict[str, CapabilityValidationRun] = {}
        self._validation_idempotency: dict[
            tuple[str, str, str], tuple[str, str]
        ] = {}
        self._validation_leases: dict[str, tuple[str, str, datetime]] = {}
        self._validation_lock = threading.Lock()
        self._supply_chain_evidence: dict[str, CapabilitySupplyChainEvidence] = {}
        self._platform_validation_runs: dict[
            tuple[str, str, str], PlatformValidationRun
        ] = {}
        self._platform_validation_leases: dict[str, tuple[str, str, datetime]] = {}

    def _insert_event(
        self,
        event: CapabilityGovernanceEvent,
    ) -> CapabilityGovernanceEvent:
        # 幂等键按事件类型隔离；跨类型复用同一键不得互相命中（与 SQLite 语义一致）。
        idempotency_key = (
            *_target_key(event.target),
            event.event_type,
            event.idempotency_key,
        )
        existing_id = self._idempotency.get(idempotency_key)
        if existing_id is not None:
            return self._events[existing_id]
        existing = self._events.get(event.event_id)
        if existing is not None:
            if existing == event:
                return existing
            raise ValueError("治理事件 ID 不可覆盖")
        self._events[event.event_id] = event
        self._idempotency[idempotency_key] = event.event_id
        return event

    def save_event(
        self,
        event: CapabilityGovernanceEvent,
    ) -> CapabilityGovernanceEvent:
        if event.event_type != "registered":
            # 晋级事件只能走专用入口，防止把 promoted 事实落成默认 registered 行。
            raise ValueError("通用事件入口只接受能力登记事件")
        return self._insert_event(event)

    def get_by_idempotency(
        self,
        target: CapabilityGovernanceTarget,
        idempotency_key: str,
    ) -> CapabilityGovernanceEvent | None:
        # 登记命令只用 registered 键；晋级与审计事件有各自专用入口。
        event_id = self._idempotency.get(
            (*_target_key(target), "registered", idempotency_key)
        )
        return self._events.get(event_id) if event_id is not None else None

    def list_events(
        self,
        target: CapabilityGovernanceTarget | None = None,
    ) -> tuple[CapabilityGovernanceEvent, ...]:
        events = tuple(self._events.values())
        if target is not None:
            events = tuple(item for item in events if item.target == target)
        return tuple(sorted(events, key=lambda item: (item.occurred_at, item.event_id)))

    def create_validation_run(
        self,
        run: CapabilityValidationRun,
    ) -> CapabilityValidationRun:
        with self._validation_lock:
            key = (run.owner_id, run.target.digest, run.idempotency_key)
            alias = self._validation_idempotency.get(key)
            request_hash = _validation_request_hash(run)
            if alias is not None:
                existing_id, saved_request_hash = alias
                existing = self._validation_runs[existing_id]
                if saved_request_hash != request_hash:
                    raise ValueError("同一验证幂等键不得改写请求")
                return existing
            for existing in self._validation_runs.values():
                if (
                    existing.owner_id == run.owner_id
                    and existing.target.digest == run.target.digest
                    and existing.status
                    in {
                        ValidationRunStatus.QUEUED,
                        ValidationRunStatus.RUNNING,
                        ValidationRunStatus.CANCELLING,
                    }
                ):
                    # 同一 digest 同时只允许一条活动运行；后来的任务证据不会另起并发执行。
                    self._validation_idempotency[key] = (
                        existing.run_id,
                        request_hash,
                    )
                    return existing
            self._validation_runs[run.run_id] = run
            self._validation_idempotency[key] = (run.run_id, request_hash)
            return run

    def get_validation_run(self, run_id: str) -> CapabilityValidationRun | None:
        return self._validation_runs.get(run_id)

    def list_validation_runs(self) -> tuple[CapabilityValidationRun, ...]:
        return tuple(
            sorted(
                self._validation_runs.values(),
                key=lambda item: (item.created_at, item.run_id),
                reverse=True,
            )
        )

    def save_validation_run(
        self,
        run: CapabilityValidationRun,
        *,
        lease_worker_id: str | None = None,
        lease_now: datetime | None = None,
    ) -> CapabilityValidationRun:
        with self._validation_lock:
            if lease_worker_id is not None:
                lease = self._validation_leases.get(run.target.digest)
                current = lease_now or datetime.now(run.updated_at.tzinfo)
                if (
                    lease is None
                    or lease[:2] != (run.run_id, lease_worker_id)
                    or lease[2] <= current
                ):
                    raise RuntimeError("能力验证 Lease 已失效或不属于当前 worker")
            existing = self._validation_runs.get(run.run_id)
            if existing is None or existing.target != run.target:
                raise ValueError("能力验证运行不存在或目标身份不一致")
            if existing.status in {
                ValidationRunStatus.SUCCEEDED,
                ValidationRunStatus.FAILED,
                ValidationRunStatus.CANCELLED,
            } and existing != run:
                return existing
            if existing.cancel_requested and not run.cancel_requested:
                run = run.model_copy(update={"cancel_requested": True})
            self._validation_runs[run.run_id] = run
            return run

    def acquire_validation_lease(
        self,
        *,
        run_id: str,
        digest: str,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        with self._validation_lock:
            lease = self._validation_leases.get(digest)
            if lease is not None and lease[2] > now:
                return lease[0] == run_id and lease[1] == worker_id
            self._validation_leases[digest] = (
                run_id,
                worker_id,
                now + timedelta(seconds=lease_seconds),
            )
            return True

    def release_validation_lease(self, run_id: str, worker_id: str) -> None:
        with self._validation_lock:
            for digest, lease in tuple(self._validation_leases.items()):
                if lease[:2] == (run_id, worker_id):
                    del self._validation_leases[digest]

    def renew_validation_lease(
        self,
        *,
        run_id: str,
        digest: str,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        with self._validation_lock:
            lease = self._validation_leases.get(digest)
            if lease is None or lease[:2] != (run_id, worker_id):
                return False
            self._validation_leases[digest] = (
                run_id,
                worker_id,
                now + timedelta(seconds=lease_seconds),
            )
            return True

    def save_supply_chain_evidence(
        self,
        evidence: CapabilitySupplyChainEvidence,
    ) -> CapabilitySupplyChainEvidence:
        existing = self._supply_chain_evidence.get(evidence.evidence_id)
        if existing is not None:
            if existing != evidence:
                raise ValueError("供应链证据 ID 不可覆盖")
            return existing
        self._supply_chain_evidence[evidence.evidence_id] = evidence
        return evidence

    def get_latest_supply_chain_evidence(
        self,
        target: CapabilityGovernanceTarget,
    ) -> CapabilitySupplyChainEvidence | None:
        items = [
            item
            for item in self._supply_chain_evidence.values()
            if item.target == target
        ]
        return max(items, key=lambda item: (item.occurred_at, item.evidence_id)) if items else None

    def save_promotion_event(
        self,
        event: CapabilityGovernanceEvent,
    ) -> CapabilityGovernanceEvent:
        if event.event_type != "promoted_to_verified":
            raise ValueError("晋级事件专用入口只接受 promoted_to_verified 事件")
        # 检查与插入必须原子，否则并发晋级会同时写入两个不同事件 ID。
        with self._events_lock:
            promoted = [
                item
                for item in self._events.values()
                if item.target == event.target
                and item.event_type == "promoted_to_verified"
            ]
            if promoted:
                # 同一 digest 至多一个晋级结果；并发后写者拿到已有事件，不覆盖。
                return max(
                    promoted, key=lambda item: (item.occurred_at, item.event_id)
                )
            return self._insert_event(event)

    def get_latest_promotion_event(
        self,
        target: CapabilityGovernanceTarget,
    ) -> CapabilityGovernanceEvent | None:
        promoted = [
            item
            for item in self._events.values()
            if item.target == target
            and item.event_type == "promoted_to_verified"
        ]
        return (
            max(promoted, key=lambda item: (item.occurred_at, item.event_id))
            if promoted
            else None
        )

    def get_latest_succeeded_validation_run(
        self,
        target: CapabilityGovernanceTarget,
    ) -> CapabilityValidationRun | None:
        succeeded = [
            item
            for item in self._validation_runs.values()
            if item.target == target
            and item.status is ValidationRunStatus.SUCCEEDED
        ]
        return (
            max(succeeded, key=lambda item: (item.updated_at, item.run_id))
            if succeeded
            else None
        )

    def save_audit_view_event(
        self,
        event: CapabilityGovernanceEvent,
    ) -> CapabilityGovernanceEvent:
        if event.event_type != "audit_viewed":
            raise ValueError("审计查看事件专用入口只接受 audit_viewed 事件")
        # 幂等键查重复用 _insert_event 的原子语义：同 target+键 返回既有事件。
        return self._insert_event(event)

    def list_audit_view_events(
        self,
        target: CapabilityGovernanceTarget | None = None,
    ) -> tuple[CapabilityGovernanceEvent, ...]:
        events = [
            item
            for item in self._events.values()
            if item.event_type == "audit_viewed"
            and (target is None or item.target == target)
        ]
        return tuple(
            sorted(events, key=lambda item: (item.occurred_at, item.event_id))
        )

    def save_platform_event(
        self,
        event: CapabilityGovernanceEvent,
    ) -> CapabilityGovernanceEvent:
        if event.event_type not in {
            "platform_candidate",
            "platform_published",
            "audience_changed",
        }:
            raise ValueError("平台事件专用入口只接受发布类事件")
        return self._insert_event(event)

    def save_governance_event(
        self,
        event: CapabilityGovernanceEvent,
    ) -> CapabilityGovernanceEvent:
        if event.event_type not in {
            "lifecycle_changed",
            "eligibility_changed",
            "risk_accepted",
            "recommendation_changed",
            "rescan_completed",
        }:
            raise ValueError("治理事件专用入口只接受生命周期/资格/风险接受/推荐指针/重扫事件")
        return self._insert_event(event)

    def get_governance_event_by_idempotency(
        self,
        target: CapabilityGovernanceTarget,
        event_type: str,
        idempotency_key: str,
    ) -> CapabilityGovernanceEvent | None:
        event_id = self._idempotency.get(
            (*_target_key(target), event_type, idempotency_key)
        )
        return self._events.get(event_id) if event_id is not None else None

    def get_latest_platform_event(
        self,
        target: CapabilityGovernanceTarget,
        event_type: str,
    ) -> CapabilityGovernanceEvent | None:
        matching = [
            item
            for item in self._events.values()
            if item.target == target and item.event_type == event_type
        ]
        return (
            max(matching, key=lambda item: (item.occurred_at, item.event_id))
            if matching
            else None
        )

    def list_platform_events(
        self,
        target: CapabilityGovernanceTarget,
    ) -> tuple[CapabilityGovernanceEvent, ...]:
        events = [
            item
            for item in self._events.values()
            if item.target == target
            and item.event_type
            in {
                "platform_candidate",
                "platform_published",
                "audience_changed",
            }
        ]
        return tuple(
            sorted(events, key=lambda item: (item.occurred_at, item.event_id))
        )

    def create_platform_validation_run(
        self,
        run: PlatformValidationRun,
    ) -> PlatformValidationRun:
        with self._validation_lock:
            # 幂等键按能力身份（pack/version）+ 键查重；同键换 digest 是请求改写。
            key = (run.target.pack_id, run.target.version, run.idempotency_key)
            existing = self._platform_validation_runs.get(key)
            if existing is not None:
                if existing.target != run.target:
                    raise ValueError("同一平台验证幂等键不得改写请求")
                return existing
            self._platform_validation_runs[key] = run
            return run

    def acquire_platform_validation_lease(
        self,
        *,
        run_id: str,
        digest: str,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        with self._validation_lock:
            lease = self._platform_validation_leases.get(digest)
            if lease is not None and lease[2] > now:
                return lease[0] == run_id and lease[1] == worker_id
            self._platform_validation_leases[digest] = (
                run_id,
                worker_id,
                now + timedelta(seconds=lease_seconds),
            )
            return True

    def release_platform_validation_lease(
        self,
        run_id: str,
        worker_id: str,
    ) -> None:
        with self._validation_lock:
            for digest, lease in tuple(self._platform_validation_leases.items()):
                if lease[:2] == (run_id, worker_id):
                    del self._platform_validation_leases[digest]

    def renew_platform_validation_lease(
        self,
        *,
        run_id: str,
        digest: str,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        with self._validation_lock:
            lease = self._platform_validation_leases.get(digest)
            if lease is None or lease[:2] != (run_id, worker_id):
                return False
            self._platform_validation_leases[digest] = (
                run_id,
                worker_id,
                now + timedelta(seconds=lease_seconds),
            )
            return True

    def get_platform_validation_run(
        self,
        run_id: str,
    ) -> PlatformValidationRun | None:
        return next(
            (
                run
                for run in self._platform_validation_runs.values()
                if run.run_id == run_id
            ),
            None,
        )

    def list_platform_validation_runs(self) -> tuple[PlatformValidationRun, ...]:
        return tuple(
            sorted(
                self._platform_validation_runs.values(),
                key=lambda item: (item.created_at, item.run_id),
                reverse=True,
            )
        )

    def save_platform_validation_run(
        self,
        run: PlatformValidationRun,
    ) -> PlatformValidationRun:
        with self._validation_lock:
            key = (run.target.pack_id, run.target.version, run.idempotency_key)
            existing = self._platform_validation_runs.get(key)
            if existing is None or existing.run_id != run.run_id:
                raise ValueError("平台验证运行不存在或目标身份不一致")
            if existing.status in {
                ValidationRunStatus.SUCCEEDED,
                ValidationRunStatus.FAILED,
                ValidationRunStatus.CANCELLED,
            } and existing != run:
                # 终态运行只允许补充签名证据（从无到有），其他变化一律拒绝；
                # 只有 SUCCEEDED 运行有资格获得签名证据。
                signing_only = (
                    existing.status is ValidationRunStatus.SUCCEEDED
                    and existing.signing_signature_digest is None
                    and run.signing_signature_digest is not None
                    and existing.model_copy(
                        update={
                            "signing_signature_digest": (
                                run.signing_signature_digest
                            ),
                            "signing_public_key_sha256": (
                                run.signing_public_key_sha256
                            ),
                            "updated_at": run.updated_at,
                        }
                    )
                    == run
                )
                if not signing_only:
                    return existing
            self._platform_validation_runs[key] = run
            return run
