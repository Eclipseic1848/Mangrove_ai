# -*- coding: utf-8 -*-
"""需要显式 Pi 的 HTTP 测试所共用的真实 Runtime 路由前提。"""
from pathlib import Path

from src.runtime_routing import (
    GateCheck,
    GateSnapshot,
    RolloutActor,
    RolloutApproval,
    RolloutMode,
    RuntimeRouting,
    SqliteRuntimeRoutingRepository,
    migrate_runtime_routing,
)


def enable_admin_gray_routing(database: Path, backup: Path) -> None:
    """迁移测试库并通过真实门禁把显式 Pi 开放给管理员。"""

    migrate_runtime_routing(database, backup)
    routing = RuntimeRouting(SqliteRuntimeRoutingRepository(database))
    snapshot = GateSnapshot.build(
        gate_version="test-runtime-routing-v1",
        code_commit="a" * 40,
        environment_digest="b" * 64,
        checks=(
            GateCheck(
                gate_id="delivery-integrity",
                passed=True,
                evidence_hash="c" * 64,
            ),
        ),
    )
    admin = RolloutActor(actor_id="admin-test", role="admin")
    routing.record_gate(snapshot, admin)
    approval = RolloutApproval(
        approval_id="approval-test-admin-gray",
        target_mode=RolloutMode.ADMIN_GRAY,
        gate_snapshot_id=snapshot.snapshot_id,
        approved_by="maintainer-test",
    )
    routing.record_approval(
        approval,
        RolloutActor(actor_id="maintainer-test", role="user"),
    )
    routing.change_mode(RolloutMode.ADMIN_GRAY, approval, admin)
