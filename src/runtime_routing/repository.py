# -*- coding: utf-8 -*-
"""RuntimeRouting 的仓库 Interface 与内存 Adapter。"""
from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Protocol

from .models import (
    GateComparison,
    GateRecord,
    GateSnapshot,
    RolloutApproval,
    RolloutMode,
    RolloutSnapshot,
    RuntimeAssignment,
    RuntimeTaskRevisionRef,
)


class RuntimeRoutingRepository(Protocol):
    def apply_gate(
        self,
        snapshot: GateSnapshot,
        *,
        actor_id: str,
    ) -> tuple[GateRecord, RolloutSnapshot]: ...

    def get_gate(self, snapshot_id: str) -> GateRecord | None: ...

    def is_gate_effectively_qualified(self, snapshot_id: str) -> bool: ...

    def record_comparison(
        self,
        comparison: GateComparison,
    ) -> GateComparison: ...

    def get_rollout(self) -> RolloutSnapshot: ...

    def get_approval(self, approval_id: str) -> RolloutApproval | None: ...

    def record_approval(
        self,
        approval: RolloutApproval,
        *,
        actor_id: str,
    ) -> RolloutApproval: ...

    def get_assignment(
        self,
        task_revision: RuntimeTaskRevisionRef,
    ) -> RuntimeAssignment | None: ...

    def has_legacy_assignment(self, *, owner_id: str, task_id: str) -> bool: ...

    def create_assignment(
        self,
        assignment: RuntimeAssignment,
    ) -> RuntimeAssignment: ...

    def change_rollout(
        self,
        *,
        expected_mode: RolloutMode,
        target_mode: RolloutMode,
        snapshot_id: str,
        approval: RolloutApproval,
        actor_id: str,
    ) -> RolloutSnapshot: ...

class InMemoryRuntimeRoutingRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._gates: dict[str, GateRecord] = {}
        self._assignments: dict[tuple[str, str, int], RuntimeAssignment] = {}
        self._approvals: dict[str, RolloutApproval] = {}
        self._comparisons: dict[str, GateComparison] = {}
        self._rollout = RolloutSnapshot(
            mode=RolloutMode.ADMIN_GRAY,
            p0_blocked=False,
            active_gate_snapshot_id="0" * 64,
        )

    def apply_gate(
        self,
        snapshot: GateSnapshot,
        *,
        actor_id: str,
    ) -> tuple[GateRecord, RolloutSnapshot]:
        with self._lock:
            existing = self._gates.get(snapshot.snapshot_id)
            if existing is not None and existing.snapshot != snapshot:
                raise ValueError("GateSnapshot 身份已绑定不同内容")
            if existing is not None:
                if self._rollout.active_gate_snapshot_id != snapshot.snapshot_id:
                    raise ValueError("历史 GateSnapshot 不得重新激活")
                return existing, self._rollout
            required_gate_ids = {
                check.gate_id
                for record in self._gates.values()
                for check in record.snapshot.checks
            }
            passed_gate_ids = {
                check.gate_id for check in snapshot.checks if check.passed
            }
            effective_qualified = (
                snapshot.qualified
                and required_gate_ids.issubset(passed_gate_ids)
            )
            existing = GateRecord(
                snapshot=snapshot,
                recorded_by=actor_id,
                recorded_at=datetime.now(timezone.utc),
            )
            self._gates[snapshot.snapshot_id] = existing
            mode = (
                self._rollout.mode
                if effective_qualified
                else RolloutMode.LEGACY_ROLLBACK
            )
            self._rollout = RolloutSnapshot(
                mode=mode,
                p0_blocked=mode is RolloutMode.LEGACY_ROLLBACK,
                active_gate_snapshot_id=snapshot.snapshot_id,
            )
            return existing, self._rollout

    def get_gate(self, snapshot_id: str) -> GateRecord | None:
        with self._lock:
            return self._gates.get(snapshot_id)

    def is_gate_effectively_qualified(self, snapshot_id: str) -> bool:
        with self._lock:
            record = self._gates.get(snapshot_id)
            if record is None:
                return False
            required_gate_ids = {
                check.gate_id
                for gate_record in self._gates.values()
                for check in gate_record.snapshot.checks
            }
            passed_gate_ids = {
                check.gate_id
                for check in record.snapshot.checks
                if check.passed
            }
            return (
                record.snapshot.qualified
                and required_gate_ids.issubset(passed_gate_ids)
            )

    def record_comparison(
        self,
        comparison: GateComparison,
    ) -> GateComparison:
        with self._lock:
            self._comparisons[comparison.comparison_id] = comparison
            return comparison

    def get_rollout(self) -> RolloutSnapshot:
        with self._lock:
            return self._rollout

    def get_approval(self, approval_id: str) -> RolloutApproval | None:
        with self._lock:
            return self._approvals.get(approval_id)

    def record_approval(
        self,
        approval: RolloutApproval,
        *,
        actor_id: str,
    ) -> RolloutApproval:
        del actor_id
        with self._lock:
            existing = self._approvals.get(approval.approval_id)
            if existing is not None and existing != approval:
                raise ValueError("同一 approval_id 不得绑定不同授权")
            if existing is not None:
                return existing
            self._approvals[approval.approval_id] = approval
            return approval

    @staticmethod
    def _assignment_key(
        task_revision: RuntimeTaskRevisionRef,
    ) -> tuple[str, str, int]:
        return (
            task_revision.owner_id,
            task_revision.task_id,
            task_revision.revision,
        )

    def get_assignment(
        self,
        task_revision: RuntimeTaskRevisionRef,
    ) -> RuntimeAssignment | None:
        with self._lock:
            return self._assignments.get(self._assignment_key(task_revision))

    def has_legacy_assignment(self, *, owner_id: str, task_id: str) -> bool:
        from src.agentic_runtime import RuntimeVersion

        with self._lock:
            return any(
                assignment.runtime_version is RuntimeVersion.LEGACY
                for key, assignment in self._assignments.items()
                if key[:2] == (owner_id, task_id)
            )

    def create_assignment(
        self,
        assignment: RuntimeAssignment,
    ) -> RuntimeAssignment:
        with self._lock:
            key = self._assignment_key(assignment.task_revision)
            existing = self._assignments.get(key)
            if existing is not None:
                if existing.task_revision != assignment.task_revision:
                    raise ValueError("同一任务修订不得改写 requested_runtime")
                return existing
            if (
                assignment.rollout_mode is not self._rollout.mode
                or assignment.gate_snapshot_id
                != self._rollout.active_gate_snapshot_id
            ):
                raise RuntimeError("Rollout 已并发变化")
            self._assignments[key] = assignment
            return assignment

    def change_rollout(
        self,
        *,
        expected_mode: RolloutMode,
        target_mode: RolloutMode,
        snapshot_id: str,
        approval: RolloutApproval,
        actor_id: str,
    ) -> RolloutSnapshot:
        del actor_id
        with self._lock:
            if self._approvals.get(approval.approval_id) != approval:
                raise PermissionError("Rollout 授权尚未由确认人独立记录")
            if self._rollout.mode is not expected_mode:
                raise RuntimeError("Rollout 已并发变化")
            if self._rollout.active_gate_snapshot_id != snapshot_id:
                raise RuntimeError("GateSnapshot 已并发变化")
            self._rollout = RolloutSnapshot(
                mode=target_mode,
                p0_blocked=target_mode is RolloutMode.LEGACY_ROLLBACK,
                active_gate_snapshot_id=snapshot_id,
            )
            return self._rollout
