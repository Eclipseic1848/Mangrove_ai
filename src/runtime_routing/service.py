# -*- coding: utf-8 -*-
"""生产门快照与 Runtime 路由的唯一业务入口。"""
from __future__ import annotations

from .models import (
    GateComparison,
    GateSnapshot,
    RolloutActor,
    RolloutApproval,
    RolloutMode,
    RolloutSnapshot,
    RuntimeAssignment,
    RuntimeTaskRevisionRef,
)
from datetime import datetime, timezone
import uuid
from src.agentic_runtime import RuntimeVersion
from .repository import RuntimeRoutingRepository


class RuntimeRouting:
    def __init__(self, repository: RuntimeRoutingRepository) -> None:
        self._repository = repository

    def record_gate(
        self,
        snapshot: GateSnapshot,
        actor: RolloutActor,
    ) -> RolloutSnapshot:
        if actor.role not in {"admin", "super_admin"}:
            raise PermissionError("只有管理员可以记录生产门快照")
        # Adapter 必须把追加快照和 P0 回退放进同一个事务，不能留下崩溃窗口。
        _record, rollout = self._repository.apply_gate(
            snapshot,
            actor_id=actor.actor_id,
        )
        return rollout

    def compare_gates(
        self,
        baseline_snapshot_id: str,
        candidate_snapshot_id: str,
        actor: RolloutActor,
    ) -> GateComparison:
        if actor.role not in {"admin", "super_admin"}:
            raise PermissionError("只有管理员可以比较生产门快照")
        baseline = self._repository.get_gate(baseline_snapshot_id)
        candidate = self._repository.get_gate(candidate_snapshot_id)
        if baseline is None or candidate is None:
            raise KeyError("GateSnapshot 不存在")
        baseline_items = {item.gate_id: item for item in baseline.snapshot.checks}
        candidate_items = {item.gate_id: item for item in candidate.snapshot.checks}
        baseline_checks = {key: item.passed for key, item in baseline_items.items()}
        candidate_checks = {key: item.passed for key, item in candidate_items.items()}
        all_gate_ids = sorted(set(baseline_checks) | set(candidate_checks))
        regressed = tuple(
            gate_id
            for gate_id in all_gate_ids
            if baseline_checks.get(gate_id) is True
            and candidate_checks.get(gate_id) is not True
        )
        recovered = tuple(
            gate_id
            for gate_id in sorted(set(baseline_checks) & set(candidate_checks))
            if baseline_checks.get(gate_id) is False
            and candidate_checks.get(gate_id) is True
        )
        added = tuple(sorted(set(candidate_checks) - set(baseline_checks)))
        removed = tuple(sorted(set(baseline_checks) - set(candidate_checks)))
        evidence_changed = tuple(
            gate_id
            for gate_id in sorted(set(baseline_checks) & set(candidate_checks))
            if baseline_items[gate_id].evidence_hash
            != candidate_items[gate_id].evidence_hash
        )
        comparison = GateComparison(
            comparison_id=f"comparison_{uuid.uuid4().hex}",
            baseline_snapshot_id=baseline_snapshot_id,
            candidate_snapshot_id=candidate_snapshot_id,
            baseline_recorded_by=baseline.recorded_by,
            candidate_recorded_by=candidate.recorded_by,
            regressed_gate_ids=regressed,
            recovered_gate_ids=recovered,
            added_gate_ids=added,
            removed_gate_ids=removed,
            evidence_changed_gate_ids=evidence_changed,
            compared_by=actor.actor_id,
            compared_at=datetime.now(timezone.utc),
        )
        return self._repository.record_comparison(comparison)

    def record_approval(
        self,
        approval: RolloutApproval,
        actor: RolloutActor,
    ) -> RolloutApproval:
        if actor.actor_id != approval.approved_by:
            raise PermissionError("Rollout 授权只能由确认人本人记录")
        return self._repository.record_approval(
            approval,
            actor_id=actor.actor_id,
        )

    def resolve(
        self,
        task_revision: RuntimeTaskRevisionRef,
        actor: RolloutActor,
        *,
        expected_rollout: RolloutSnapshot | None = None,
    ) -> RuntimeAssignment:
        if actor.role == "user" and actor.actor_id != task_revision.owner_id:
            raise PermissionError("普通用户只能解析自己的任务修订")
        if expected_rollout is not None:
            current = self._repository.get_rollout()
            if current != expected_rollout:
                raise RuntimeError("Rollout 已并发变化，请重试任务创建")
        existing = self._repository.get_assignment(task_revision)
        if existing is not None:
            if existing.task_revision != task_revision:
                raise ValueError("同一任务修订不得改写 requested_runtime")
            return existing
        for _attempt in range(3):
            rollout = self._repository.get_rollout()
            if expected_rollout is not None and rollout != expected_rollout:
                raise RuntimeError("Rollout 已并发变化，请重试任务创建")
            runtime_version = self._select_runtime(task_revision, actor, rollout)
            proposed = RuntimeAssignment(
                task_revision=task_revision,
                runtime_version=runtime_version,
                rollout_mode=rollout.mode,
                gate_snapshot_id=rollout.active_gate_snapshot_id,
                assigned_by=actor.actor_id,
                assigned_at=datetime.now(timezone.utc),
            )
            try:
                return self._repository.create_assignment(proposed)
            except RuntimeError as error:
                if str(error) != "Rollout 已并发变化":
                    raise
        raise RuntimeError("Rollout 持续并发变化，未能冻结任务路由")

    def preview(
        self,
        task_revision: RuntimeTaskRevisionRef,
        actor: RolloutActor,
    ) -> tuple[RuntimeVersion, RolloutSnapshot]:
        if actor.role == "user" and actor.actor_id != task_revision.owner_id:
            raise PermissionError("普通用户只能解析自己的任务修订")
        rollout = self._repository.get_rollout()
        return self._select_runtime(task_revision, actor, rollout), rollout

    def _select_runtime(
        self,
        task_revision: RuntimeTaskRevisionRef,
        actor: RolloutActor,
        rollout: RolloutSnapshot,
    ) -> RuntimeVersion:
        requested = task_revision.requested_runtime
        if rollout.mode in {RolloutMode.LEGACY_DEFAULT, RolloutMode.LEGACY_ROLLBACK}:
            return RuntimeVersion.LEGACY
        if rollout.mode is RolloutMode.ADMIN_GRAY:
            if (
                requested is RuntimeVersion.PI
                and actor.role in {"admin", "super_admin"}
            ):
                return RuntimeVersion.PI
            return RuntimeVersion.LEGACY
        if rollout.mode is RolloutMode.EXPLICIT_OPT_IN:
            return (
                RuntimeVersion.PI
                if requested is RuntimeVersion.PI
                else RuntimeVersion.LEGACY
            )
        if requested is RuntimeVersion.LEGACY:
            return RuntimeVersion.LEGACY
        if self._repository.has_legacy_assignment(
            owner_id=task_revision.owner_id,
            task_id=task_revision.task_id,
        ):
            return RuntimeVersion.LEGACY
        return RuntimeVersion.PI

    def change_mode(
        self,
        target_mode: RolloutMode,
        approval: RolloutApproval,
        actor: RolloutActor,
    ) -> RolloutSnapshot:
        if actor.role not in {"admin", "super_admin"}:
            raise PermissionError("只有管理员可以变更 Rollout 模式")
        current = self._repository.get_rollout()
        if current.mode is target_mode:
            existing_approval = self._repository.get_approval(approval.approval_id)
            if existing_approval == approval:
                return current
            raise ValueError("目标模式已生效，但授权身份不一致")
        allowed = {
            RolloutMode.ADMIN_GRAY: {RolloutMode.EXPLICIT_OPT_IN},
            RolloutMode.EXPLICIT_OPT_IN: {RolloutMode.VNEXT_DEFAULT},
            RolloutMode.LEGACY_ROLLBACK: {RolloutMode.ADMIN_GRAY},
        }
        if target_mode not in allowed.get(current.mode, set()):
            raise ValueError("Rollout 模式转换不允许")
        if approval.target_mode is not target_mode:
            raise ValueError("授权目标与 Rollout 目标不一致")
        if self._repository.get_approval(approval.approval_id) != approval:
            raise PermissionError("Rollout 授权尚未由确认人独立记录")
        if approval.gate_snapshot_id != current.active_gate_snapshot_id:
            raise ValueError("授权未绑定当前 GateSnapshot")
        if not self._repository.is_gate_effectively_qualified(
            current.active_gate_snapshot_id
        ):
            # 历史上出现过的硬门不得靠删除检查项和人工审批绕过。
            raise ValueError("Rollout 模式变更需要累计门禁有效合格的 GateSnapshot")
        return self._repository.change_rollout(
            expected_mode=current.mode,
            target_mode=target_mode,
            snapshot_id=current.active_gate_snapshot_id,
            approval=approval,
            actor_id=actor.actor_id,
        )
