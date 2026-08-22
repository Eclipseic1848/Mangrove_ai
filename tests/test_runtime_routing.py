# -*- coding: utf-8 -*-
"""G3：只通过 RuntimeRouting 公共接口验证生产门与路由。"""
from __future__ import annotations

from src.runtime_routing import (
    GateCheck,
    GateSnapshot,
    InMemoryRuntimeRoutingRepository,
    RolloutActor,
    RolloutApproval,
    RolloutMode,
    SqliteRuntimeRoutingRepository,
    RuntimeTaskRevisionRef,
    RuntimeRouting,
    migrate_runtime_routing,
    open_runtime_routing_repository,
)
from src.agentic_runtime import RuntimeVersion
from pydantic import ValidationError
import pytest
import sqlite3
import threading


def _actor() -> RolloutActor:
    return RolloutActor(actor_id="admin-a", role="admin")


def _snapshot(*, passed: bool) -> GateSnapshot:
    return GateSnapshot.build(
        gate_version="phase4-g3-v1",
        code_commit="a" * 40,
        environment_digest="b" * 64,
        checks=(
            GateCheck(
                gate_id="delivery-integrity",
                passed=passed,
                evidence_hash="c" * 64,
            ),
        ),
    )


def _approval(
    routing: RuntimeRouting,
    *,
    approval_id: str,
    target_mode: RolloutMode,
    gate_snapshot_id: str,
    approved_by: str = "maintainer-a",
) -> RolloutApproval:
    approval = RolloutApproval(
        approval_id=approval_id,
        target_mode=target_mode,
        gate_snapshot_id=gate_snapshot_id,
        approved_by=approved_by,
    )
    routing.record_approval(
        approval,
        RolloutActor(actor_id=approved_by, role="user"),
    )
    return approval


def test_failed_p0_snapshot_automatically_rolls_back_new_routing() -> None:
    routing = RuntimeRouting(InMemoryRuntimeRoutingRepository())

    rollout = routing.record_gate(_snapshot(passed=False), _actor())

    assert rollout.mode is RolloutMode.LEGACY_ROLLBACK
    assert rollout.p0_blocked is True
    assert rollout.active_gate_snapshot_id == _snapshot(passed=False).snapshot_id


def test_gate_comparison_reports_p0_regression_with_audit_identity() -> None:
    routing = RuntimeRouting(InMemoryRuntimeRoutingRepository())
    baseline = _snapshot(passed=True)
    candidate = _snapshot(passed=False)
    routing.record_gate(baseline, _actor())
    routing.record_gate(candidate, _actor())

    comparison = routing.compare_gates(
        baseline.snapshot_id,
        candidate.snapshot_id,
        _actor(),
    )

    assert comparison.regressed_gate_ids == ("delivery-integrity",)
    assert comparison.baseline_snapshot_id == baseline.snapshot_id
    assert comparison.candidate_snapshot_id == candidate.snapshot_id
    assert comparison.candidate_recorded_by == "admin-a"


def test_qualified_snapshot_does_not_automatically_leave_legacy_rollback() -> None:
    routing = RuntimeRouting(InMemoryRuntimeRoutingRepository())
    routing.record_gate(_snapshot(passed=False), _actor())

    rollout = routing.record_gate(_snapshot(passed=True), _actor())

    assert rollout.mode is RolloutMode.LEGACY_ROLLBACK
    assert rollout.p0_blocked is True
    assert rollout.active_gate_snapshot_id == _snapshot(passed=True).snapshot_id


def test_gate_snapshot_rejects_blank_and_duplicate_gate_ids() -> None:
    with pytest.raises(ValidationError):
        GateCheck(gate_id="   ", passed=True, evidence_hash="c" * 64)

    duplicate = GateCheck(
        gate_id="delivery-integrity",
        passed=True,
        evidence_hash="c" * 64,
    )
    with pytest.raises(ValidationError):
        GateSnapshot.build(
            gate_version="phase4-g3-v1",
            code_commit="a" * 40,
            environment_digest="b" * 64,
            checks=(duplicate, duplicate),
        )


def test_gate_comparison_distinguishes_added_removed_and_evidence_changes() -> None:
    routing = RuntimeRouting(InMemoryRuntimeRoutingRepository())
    baseline = GateSnapshot.build(
        gate_version="phase4-g3-v1",
        code_commit="a" * 40,
        environment_digest="b" * 64,
        checks=(
            GateCheck(gate_id="stable", passed=False, evidence_hash="1" * 64),
            GateCheck(gate_id="removed", passed=True, evidence_hash="2" * 64),
        ),
    )
    candidate = GateSnapshot.build(
        gate_version="phase4-g3-v1",
        code_commit="a" * 40,
        environment_digest="b" * 64,
        checks=(
            GateCheck(gate_id="stable", passed=True, evidence_hash="3" * 64),
            GateCheck(gate_id="added", passed=True, evidence_hash="4" * 64),
        ),
    )
    routing.record_gate(baseline, _actor())
    routing.record_gate(candidate, _actor())

    comparison = routing.compare_gates(
        baseline.snapshot_id,
        candidate.snapshot_id,
        _actor(),
    )

    assert comparison.recovered_gate_ids == ("stable",)
    assert comparison.added_gate_ids == ("added",)
    assert comparison.removed_gate_ids == ("removed",)
    assert comparison.evidence_changed_gate_ids == ("stable",)


def test_removing_a_previously_frozen_gate_forces_p0_rollback() -> None:
    routing = RuntimeRouting(InMemoryRuntimeRoutingRepository())
    baseline = GateSnapshot.build(
        gate_version="phase4-g3-v1",
        code_commit="a" * 40,
        environment_digest="b" * 64,
        checks=(
            GateCheck(gate_id="safety", passed=True, evidence_hash="1" * 64),
            GateCheck(gate_id="delivery", passed=True, evidence_hash="2" * 64),
        ),
    )
    removed = GateSnapshot.build(
        gate_version="phase4-g3-v1",
        code_commit="a" * 40,
        environment_digest="b" * 64,
        checks=(
            GateCheck(gate_id="safety", passed=True, evidence_hash="3" * 64),
        ),
    )
    routing.record_gate(baseline, _actor())

    rollout = routing.record_gate(removed, _actor())

    assert removed.qualified is True
    assert rollout.mode is RolloutMode.LEGACY_ROLLBACK
    assert rollout.p0_blocked is True


def test_approval_cannot_recover_snapshot_missing_historical_gate() -> None:
    routing = RuntimeRouting(InMemoryRuntimeRoutingRepository())
    baseline = GateSnapshot.build(
        gate_version="phase4-g3-v1",
        code_commit="a" * 40,
        environment_digest="b" * 64,
        checks=(
            GateCheck(gate_id="delivery", passed=True, evidence_hash="1" * 64),
            GateCheck(gate_id="safety", passed=True, evidence_hash="2" * 64),
        ),
    )
    missing = GateSnapshot.build(
        gate_version="phase4-g3-v2",
        code_commit="a" * 40,
        environment_digest="b" * 64,
        checks=(
            GateCheck(gate_id="delivery", passed=True, evidence_hash="3" * 64),
        ),
    )
    routing.record_gate(baseline, _actor())
    routing.record_gate(missing, _actor())
    approval = RolloutApproval(
        approval_id="approval-missing-gate",
        target_mode=RolloutMode.ADMIN_GRAY,
        gate_snapshot_id=missing.snapshot_id,
        approved_by="maintainer-a",
    )
    routing.record_approval(
        approval,
        RolloutActor(actor_id="maintainer-a", role="user"),
    )

    with pytest.raises(ValueError, match="累计门禁有效合格"):
        routing.change_mode(RolloutMode.ADMIN_GRAY, approval, _actor())


def test_historical_gate_snapshot_cannot_be_replayed_as_active() -> None:
    routing = RuntimeRouting(InMemoryRuntimeRoutingRepository())
    baseline = _snapshot(passed=True)
    candidate = _snapshot(passed=False)
    routing.record_gate(baseline, _actor())
    current = routing.record_gate(candidate, _actor())

    with pytest.raises(ValueError, match="历史 GateSnapshot"):
        routing.record_gate(baseline, _actor())

    assert routing.record_gate(candidate, _actor()) == current


def test_gate_snapshot_rejects_caller_supplied_mismatched_identity() -> None:
    snapshot = _snapshot(passed=True)

    with pytest.raises(ValidationError, match="内容不一致"):
        GateSnapshot(**{
            **snapshot.model_dump(mode="python"),
            "snapshot_id": "f" * 64,
        })


def test_resolve_freezes_each_revision_and_does_not_rewrite_old_assignment() -> None:
    routing = RuntimeRouting(InMemoryRuntimeRoutingRepository())
    first = routing.resolve(
        RuntimeTaskRevisionRef(owner_id="user-a", task_id="task-a", revision=1),
        RolloutActor(actor_id="user-a", role="user"),
    )
    routing.record_gate(_snapshot(passed=True), _actor())
    routing.change_mode(
        RolloutMode.EXPLICIT_OPT_IN,
        _approval(
            routing,
            approval_id="approval-gray",
            target_mode=RolloutMode.EXPLICIT_OPT_IN,
            gate_snapshot_id=_snapshot(passed=True).snapshot_id,
        ),
        _actor(),
    )
    routing.change_mode(
        RolloutMode.VNEXT_DEFAULT,
        _approval(
            routing,
            approval_id="approval-default",
            target_mode=RolloutMode.VNEXT_DEFAULT,
            gate_snapshot_id=_snapshot(passed=True).snapshot_id,
        ),
        _actor(),
    )
    second = routing.resolve(
        RuntimeTaskRevisionRef(owner_id="user-b", task_id="task-b", revision=1),
        RolloutActor(actor_id="user-b", role="user"),
    )

    assert first.runtime_version is RuntimeVersion.LEGACY
    assert routing.resolve(first.task_revision, _actor()) == first
    assert second.runtime_version is RuntimeVersion.PI


def test_legacy_task_new_revision_stays_legacy_after_default_cutover() -> None:
    routing = RuntimeRouting(InMemoryRuntimeRoutingRepository())
    actor = RolloutActor(actor_id="user-a", role="user")
    routing.resolve(
        RuntimeTaskRevisionRef(owner_id="user-a", task_id="task-a", revision=1),
        actor,
    )
    routing.record_gate(_snapshot(passed=True), _actor())
    routing.change_mode(
        RolloutMode.EXPLICIT_OPT_IN,
        _approval(
            routing,
            approval_id="approval-gray",
            target_mode=RolloutMode.EXPLICIT_OPT_IN,
            gate_snapshot_id=_snapshot(passed=True).snapshot_id,
        ),
        _actor(),
    )
    routing.change_mode(
        RolloutMode.VNEXT_DEFAULT,
        _approval(
            routing,
            approval_id="approval-default",
            target_mode=RolloutMode.VNEXT_DEFAULT,
            gate_snapshot_id=_snapshot(passed=True).snapshot_id,
        ),
        _actor(),
    )

    assignment = routing.resolve(
        RuntimeTaskRevisionRef(owner_id="user-a", task_id="task-a", revision=2),
        actor,
    )

    assert assignment.runtime_version is RuntimeVersion.LEGACY


def test_mode_change_rejects_permission_mismatch_and_unqualified_gate() -> None:
    routing = RuntimeRouting(InMemoryRuntimeRoutingRepository())
    failed = _snapshot(passed=False)
    routing.record_gate(failed, _actor())
    approval = RolloutApproval(
        approval_id="approval-invalid",
        target_mode=RolloutMode.ADMIN_GRAY,
        gate_snapshot_id=failed.snapshot_id,
        approved_by="maintainer-a",
    )
    routing.record_approval(
        approval,
        RolloutActor(actor_id="maintainer-a", role="user"),
    )

    with pytest.raises(PermissionError):
        routing.change_mode(
            RolloutMode.ADMIN_GRAY,
            approval,
            RolloutActor(actor_id="user-a", role="user"),
        )
    with pytest.raises(ValueError, match="合格"):
        routing.change_mode(RolloutMode.ADMIN_GRAY, approval, _actor())


def test_p0_rollback_blocks_new_vnext_assignment_until_explicit_recovery() -> None:
    routing = RuntimeRouting(InMemoryRuntimeRoutingRepository())
    routing.record_gate(_snapshot(passed=False), _actor())

    assignment = routing.resolve(
        RuntimeTaskRevisionRef(
            owner_id="admin-a",
            task_id="task-p0",
            revision=1,
            requested_runtime=RuntimeVersion.PI,
        ),
        _actor(),
    )

    assert assignment.runtime_version is RuntimeVersion.LEGACY


def test_same_revision_rejects_different_requested_runtime() -> None:
    routing = RuntimeRouting(InMemoryRuntimeRoutingRepository())
    routing.resolve(
        RuntimeTaskRevisionRef(owner_id="admin-a", task_id="task-a", revision=1),
        _actor(),
    )

    with pytest.raises(ValueError, match="requested_runtime"):
        routing.resolve(
            RuntimeTaskRevisionRef(
                owner_id="admin-a",
                task_id="task-a",
                revision=1,
                requested_runtime=RuntimeVersion.PI,
            ),
            _actor(),
        )


def test_preview_cas_rejects_rollout_change_without_freezing_assignment() -> None:
    repository = InMemoryRuntimeRoutingRepository()
    routing = RuntimeRouting(repository)
    task_revision = RuntimeTaskRevisionRef(
        owner_id="admin-a",
        task_id="task-preview",
        revision=1,
        requested_runtime=RuntimeVersion.PI,
    )
    _runtime, preview = routing.preview(task_revision, _actor())
    routing.record_gate(_snapshot(passed=False), _actor())

    with pytest.raises(RuntimeError, match="请重试"):
        routing.resolve(
            task_revision,
            _actor(),
            expected_rollout=preview,
        )

    assert repository.get_assignment(task_revision) is None


def _migrated_database(tmp_path):
    database = tmp_path / "runtime-routing.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE existing_delivery "
            "(delivery_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO existing_delivery VALUES ('delivery-1', 'kept')"
        )
        connection.execute(
            "CREATE TABLE runtime_config (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO runtime_config VALUES ('mode', 'existing')")
    backup = tmp_path / "runtime-routing-before-g3.db"
    migrate_runtime_routing(database, backup)
    return database, backup


def test_explicit_sqlite_migration_preserves_recovery_point_and_existing_data(
    tmp_path,
) -> None:
    database, backup = _migrated_database(tmp_path)

    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute(
            "SELECT payload FROM existing_delivery"
        ).fetchone() == ("kept",)
        assert connection.execute(
            "SELECT value FROM runtime_config WHERE key='mode'"
        ).fetchone() == ("existing",)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='runtime_gate_snapshots'"
        ).fetchone() is None
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute(
            "SELECT payload FROM existing_delivery"
        ).fetchone() == ("kept",)


def test_sqlite_repository_refuses_implicit_schema_creation(tmp_path) -> None:
    database = tmp_path / "not-migrated.db"
    with sqlite3.connect(database):
        pass

    with pytest.raises(RuntimeError, match="尚未执行带备份迁移"):
        SqliteRuntimeRoutingRepository(database)
    assert open_runtime_routing_repository(database) is None


def test_sqlite_repository_persists_rollback_without_rewriting_history(
    tmp_path,
) -> None:
    database, _backup = _migrated_database(tmp_path)
    routing = RuntimeRouting(SqliteRuntimeRoutingRepository(database))
    with sqlite3.connect(database) as connection:
        before = connection.execute("SELECT * FROM existing_delivery").fetchall()

    routing.record_gate(_snapshot(passed=False), _actor())
    assignment = routing.resolve(
        RuntimeTaskRevisionRef(
            owner_id="admin-a",
            task_id="task-after-p0",
            revision=1,
            requested_runtime=RuntimeVersion.PI,
        ),
        _actor(),
    )

    reopened = RuntimeRouting(SqliteRuntimeRoutingRepository(database))
    assert reopened.resolve(assignment.task_revision, _actor()) == assignment
    assert assignment.runtime_version is RuntimeVersion.LEGACY
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT * FROM existing_delivery"
        ).fetchall() == before


def test_concurrent_sqlite_resolve_freezes_one_assignment(tmp_path) -> None:
    database, _backup = _migrated_database(tmp_path)
    task_revision = RuntimeTaskRevisionRef(
        owner_id="admin-a",
        task_id="task-concurrent",
        revision=1,
        requested_runtime=RuntimeVersion.PI,
    )
    results = []
    failures = []
    barrier = threading.Barrier(4)

    def worker() -> None:
        try:
            routing = RuntimeRouting(SqliteRuntimeRoutingRepository(database))
            barrier.wait()
            results.append(routing.resolve(task_revision, _actor()))
        except Exception as error:  # pragma: no cover - 失败内容由断言展示
            failures.append(error)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    assert len(results) == 4
    assert all(result == results[0] for result in results)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM runtime_assignments"
        ).fetchone() == (1,)


def test_sqlite_migration_replay_keeps_first_recovery_point(tmp_path) -> None:
    database, backup = _migrated_database(tmp_path)
    original = backup.read_bytes()
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO existing_delivery VALUES ('delivery-2', 'new')")

    assert migrate_runtime_routing(database, backup) == backup.resolve()
    assert backup.read_bytes() == original


def test_concurrent_sqlite_migrations_share_one_recovery_point(tmp_path) -> None:
    database = tmp_path / "concurrent-migration.db"
    backup = tmp_path / "concurrent-before-g3.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE existing_data (value TEXT NOT NULL)")
        connection.execute("INSERT INTO existing_data VALUES ('kept')")
    barrier = threading.Barrier(2)
    results = []
    failures = []

    def worker() -> None:
        try:
            barrier.wait()
            results.append(migrate_runtime_routing(database, backup))
        except Exception as error:  # pragma: no cover - 失败内容由断言展示
            failures.append(error)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    assert results == [backup.resolve(), backup.resolve()]
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM runtime_routing_migrations"
        ).fetchone() == (1,)
    with sqlite3.connect(backup) as connection:
        assert connection.execute(
            "SELECT value FROM existing_data"
        ).fetchone() == ("kept",)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='runtime_rollout_state'"
        ).fetchone() is None


def test_sqlite_repository_rejects_tampered_schema(tmp_path) -> None:
    database, _backup = _migrated_database(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER runtime_assignments_no_update")

    with pytest.raises(RuntimeError, match="尚未执行带备份迁移"):
        SqliteRuntimeRoutingRepository(database)
    with pytest.raises(RuntimeError, match="尚未执行带备份迁移"):
        open_runtime_routing_repository(database)


def test_sqlite_append_only_records_reject_update_and_delete(tmp_path) -> None:
    database, _backup = _migrated_database(tmp_path)
    routing = RuntimeRouting(SqliteRuntimeRoutingRepository(database))
    routing.record_gate(_snapshot(passed=True), _actor())
    routing.resolve(
        RuntimeTaskRevisionRef(owner_id="admin-a", task_id="task-a", revision=1),
        _actor(),
    )

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="不可改写"):
            connection.execute(
                "UPDATE runtime_gate_snapshots SET recorded_by='attacker'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="不可删除"):
            connection.execute("DELETE FROM runtime_assignments")
        with pytest.raises(sqlite3.IntegrityError, match="不可改写"):
            connection.execute(
                "UPDATE runtime_routing_migrations SET backup_sha256=?",
                ("0" * 64,),
            )


def test_duplicate_mode_change_is_idempotent_but_conflict_is_rejected() -> None:
    routing = RuntimeRouting(InMemoryRuntimeRoutingRepository())
    passed = _snapshot(passed=True)
    routing.record_gate(passed, _actor())
    approval = RolloutApproval(
        approval_id="approval-one",
        target_mode=RolloutMode.EXPLICIT_OPT_IN,
        gate_snapshot_id=passed.snapshot_id,
        approved_by="maintainer-a",
    )
    with pytest.raises(PermissionError, match="独立记录"):
        routing.change_mode(RolloutMode.EXPLICIT_OPT_IN, approval, _actor())
    with pytest.raises(PermissionError, match="本人"):
        routing.record_approval(approval, _actor())
    routing.record_approval(
        approval,
        RolloutActor(actor_id="maintainer-a", role="user"),
    )

    first = routing.change_mode(RolloutMode.EXPLICIT_OPT_IN, approval, _actor())
    second = routing.change_mode(RolloutMode.EXPLICIT_OPT_IN, approval, _actor())

    assert first == second
    with pytest.raises(ValueError, match="授权身份不一致"):
        routing.change_mode(
            RolloutMode.EXPLICIT_OPT_IN,
            approval.model_copy(update={"approved_by": "maintainer-b"}),
            _actor(),
        )


def test_mode_change_detects_concurrent_gate_snapshot_change() -> None:
    class RacingRepository(InMemoryRuntimeRoutingRepository):
        def change_rollout(self, **kwargs):
            self.apply_gate(
                GateSnapshot.build(
                    gate_version="phase4-g3-v2",
                    code_commit="d" * 40,
                    environment_digest="e" * 64,
                    checks=(
                        GateCheck(
                            gate_id="delivery-integrity",
                            passed=True,
                            evidence_hash="f" * 64,
                        ),
                    ),
                ),
                actor_id="admin-racer",
            )
            return super().change_rollout(**kwargs)

    routing = RuntimeRouting(RacingRepository())
    passed = _snapshot(passed=True)
    routing.record_gate(passed, _actor())
    approval = RolloutApproval(
        approval_id="approval-race",
        target_mode=RolloutMode.EXPLICIT_OPT_IN,
        gate_snapshot_id=passed.snapshot_id,
        approved_by="maintainer-a",
    )
    routing.record_approval(
        approval,
        RolloutActor(actor_id="maintainer-a", role="user"),
    )

    with pytest.raises(RuntimeError, match="GateSnapshot 已并发变化"):
        routing.change_mode(RolloutMode.EXPLICIT_OPT_IN, approval, _actor())


@pytest.mark.parametrize(
    "factory",
    [
        lambda: RolloutActor(actor_id=" ", role="admin"),
        lambda: RolloutApproval(
            approval_id=" ",
            target_mode=RolloutMode.ADMIN_GRAY,
            gate_snapshot_id="a" * 64,
            approved_by="maintainer-a",
        ),
        lambda: RuntimeTaskRevisionRef(
            owner_id="user-a",
            task_id=" ",
            revision=1,
        ),
    ],
)
def test_runtime_routing_rejects_blank_identities(factory) -> None:
    with pytest.raises(ValidationError):
        factory()
