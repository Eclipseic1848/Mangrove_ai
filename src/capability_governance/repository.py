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


def _target_key(target: CapabilityGovernanceTarget) -> tuple[str | None, str, str, str]:
    return (target.owner_id, target.pack_id, target.version, target.digest)


class InMemoryCapabilityGovernanceRepository:
    def __init__(self) -> None:
        self._events: dict[str, CapabilityGovernanceEvent] = {}
        self._idempotency: dict[
            tuple[str | None, str, str, str, str], str
        ] = {}
        self._validation_runs: dict[str, CapabilityValidationRun] = {}
        self._validation_idempotency: dict[
            tuple[str, str, str], tuple[str, str]
        ] = {}
        self._validation_leases: dict[str, tuple[str, str, datetime]] = {}
        self._validation_lock = threading.Lock()
        self._supply_chain_evidence: dict[str, CapabilitySupplyChainEvidence] = {}

    def save_event(
        self,
        event: CapabilityGovernanceEvent,
    ) -> CapabilityGovernanceEvent:
        idempotency_key = (*_target_key(event.target), event.idempotency_key)
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

    def get_by_idempotency(
        self,
        target: CapabilityGovernanceTarget,
        idempotency_key: str,
    ) -> CapabilityGovernanceEvent | None:
        event_id = self._idempotency.get((*_target_key(target), idempotency_key))
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
