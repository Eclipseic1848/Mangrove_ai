# -*- coding: utf-8 -*-
"""AC-07-08 S6：运行期治理监督（隔离/撤销 → 停 Sidecar + 取消 + 不发布）。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.agentic_runtime.models import PermissionProfile
from src.api import semantic_workspace_runtime as runtime_mod
from src.api.semantic_workspace_runtime import SemanticWorkspaceManager
from src.capability_catalog import (
    CapabilityCatalog,
    CapabilityPackRef,
    CatalogActor,
    SqliteCapabilityCatalogRepository,
)
from src.capability_governance import (
    CapabilityGovernanceEvent,
    CapabilityGovernanceTarget,
    CapabilityMaturity,
    SqliteCapabilityGovernanceRepository,
)
from src.config.settings import settings
from src.conversation_steering import (
    CapabilityMaturity as LegacyCapabilityMaturity,
    CapabilityPack,
    ProcedureScope,
)
from tests.database_migration_helpers import migrated_webui_database


class _GateFakeRuntime:
    """替身 Pi Runtime：只记录 cancel。"""

    def __init__(self):
        self.cancel_calls: list[tuple] = []
        self.candidate_verification = None

    def bind_candidate_verification(self, service):
        """测试替身遵守 Pi Runtime 的候选验证绑定接口。"""
        self.candidate_verification = service

    async def cancel(self, user_id, task_id, revision):
        self.cancel_calls.append((user_id, task_id, revision))


def _db_with_selection(
    tmp_path: Path,
    *,
    scope: ProcedureScope = ProcedureScope.PERSONAL,
) -> str:
    db_path = str(migrated_webui_database(tmp_path / "workspace.db"))
    catalog = CapabilityCatalog(SqliteCapabilityCatalogRepository(db_path))
    actor = CatalogActor(owner_id="user-a", role="user")
    digest = "sha256:" + "a" * 64
    if scope is ProcedureScope.PERSONAL:
        catalog.register_pack(
            actor,
            CapabilityPack(
                pack_id="private-a",
                version="1.0.0",
                digest=digest,
                scope=scope,
                maturity=LegacyCapabilityMaturity.DRAFT,
                owner_id="user-a",
            ),
        )
    else:
        SqliteCapabilityCatalogRepository(db_path).save_pack(
            CapabilityPack(
                pack_id="platform-a",
                version="1.0.0",
                digest=digest,
                scope=scope,
                maturity=LegacyCapabilityMaturity.VERIFIED,
                owner_id=None,
            )
        )
    catalog.freeze_selection(
        actor,
        task_id="workspace-s6",
        revision=1,
        pack_refs=(
            CapabilityPackRef(
                pack_id="private-a" if scope is ProcedureScope.PERSONAL
                else "platform-a",
                version="1.0.0",
                digest=digest,
            ),
        ),
    )
    return db_path


def _event(
    *,
    scope: ProcedureScope,
    event_type: str,
    **overrides,
) -> CapabilityGovernanceEvent:
    fields: dict = {
        "event_type": event_type,
        "idempotency_key": f"govern:{event_type}",
        "target": CapabilityGovernanceTarget(
            owner_id="user-a" if scope is ProcedureScope.PERSONAL else None,
            scope=scope,
            pack_id="private-a" if scope is ProcedureScope.PERSONAL
            else "platform-a",
            version="1.0.0",
            digest="sha256:" + "a" * 64,
        ),
        "maturity": CapabilityMaturity.VERIFIED,
        "actor_id": "admin-a",
        "actor_role": "admin",
        "source_validation_run_id": "capval_a1b2c3d4e5f6a1b2c3d4",
        "source_supply_chain_evidence_id": "supply_" + "a" * 20,
    }
    fields.update(overrides)
    return CapabilityGovernanceEvent(**fields)


def _projection_patch(monkeypatch: pytest.MonkeyPatch, **overrides) -> None:
    """替换投影读取为固定投影；撤销/隔离事件类型属于 #14，其 DB 形态到
    #14 才有合法 validator，因此 #13 测试直接替换投影输出验证判断逻辑。"""
    from src.capability_governance import (
        CapabilityEligibility,
        CapabilityGovernanceProjection,
        CapabilityLifecycle,
    )

    fields = dict(
        maturity=CapabilityMaturity.VERIFIED,
        lifecycle=CapabilityLifecycle.ACTIVE,
        eligibility=CapabilityEligibility.ELIGIBLE,
        source="governance_event",
        audience=None,
    )
    fields.update(overrides)
    target = CapabilityGovernanceTarget(
        owner_id="user-a",
        scope=ProcedureScope.PERSONAL,
        pack_id="private-a",
        version="1.0.0",
        digest="sha256:" + "a" * 64,
    )
    projection = CapabilityGovernanceProjection(target=target, **fields)
    monkeypatch.setattr(
        "src.capability_governance.CapabilityGovernance"
        ".runtime_projection_for_pack",
        lambda self, pack: projection,
    )


class TestS6ViolationProbe:
    def test_no_selection_returns_false(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db_path = _db_with_selection(tmp_path)
        monkeypatch.setattr(settings, "webui_db_path", db_path)
        manager = SemanticWorkspaceManager(pi_runtime=_GateFakeRuntime())

        async def run():
            # 无选择任务：零负担，不视为违规。
            return await manager._runtime_gate_violation(
                "user-a", "absent-task", 1
            )

        assert asyncio.run(run()) is False

    def test_revoked_projection_violates(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.capability_governance import CapabilityLifecycle

        db_path = _db_with_selection(tmp_path)
        monkeypatch.setattr(settings, "webui_db_path", db_path)
        _projection_patch(
            monkeypatch, lifecycle=CapabilityLifecycle.REVOKED
        )
        manager = SemanticWorkspaceManager(pi_runtime=_GateFakeRuntime())

        async def run():
            return await manager._runtime_gate_violation(
                "user-a", "workspace-s6", 1
            )

        assert asyncio.run(run()) is True

    def test_active_eligible_projection_does_not_violate(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db_path = _db_with_selection(tmp_path)
        monkeypatch.setattr(settings, "webui_db_path", db_path)
        _projection_patch(monkeypatch)
        manager = SemanticWorkspaceManager(pi_runtime=_GateFakeRuntime())

        async def run():
            return await manager._runtime_gate_violation(
                "user-a", "workspace-s6", 1
            )

        assert asyncio.run(run()) is False


class TestS6Supervision:
    def test_no_capability_task_awaits_execution_without_supervision(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "webui_db_path", str(tmp_path / "w.db"))
        manager = SemanticWorkspaceManager(pi_runtime=_GateFakeRuntime())
        violation_calls: list = []
        monkeypatch.setattr(
            manager,
            "_runtime_gate_violation",
            _async_counter(violation_calls, result=False),
        )

        async def run():
            execution: asyncio.Future = asyncio.get_running_loop().create_future()
            execution.set_result("done")
            result = await manager._await_with_gate_supervision(
                "user-a", "task-a", 1, execution
            )
            assert result == "done"
            # 无能力任务不启动监督（零负担）。
            assert violation_calls == []

        asyncio.run(run())

    def test_violation_stops_runtime_and_cancels_execution(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            runtime_mod, "RUNTIME_GATE_POLL_SECONDS", 0.02
        )
        monkeypatch.setattr(settings, "webui_db_path", str(tmp_path / "w.db"))
        runtime = _GateFakeRuntime()
        manager = SemanticWorkspaceManager(pi_runtime=runtime)
        cancelled_marks: list = []
        monkeypatch.setattr(
            manager,
            "_selection_has_capabilities",
            _async_counter([], result=True),
        )
        monkeypatch.setattr(
            manager,
            "_runtime_gate_violation",
            _async_counter([], result=True),
        )
        monkeypatch.setattr(
            manager,
            "_mark_cancelled",
            lambda user_id, task_id, revision: cancelled_marks.append(
                (user_id, task_id, revision)
            ),
        )

        async def run():
            execution: asyncio.Future = asyncio.get_running_loop().create_future()
            # 命中时抛出专用异常（B1）：调用方可精确区分，不误走 failed 标记。
            with pytest.raises(runtime_mod._GateViolationAbort):
                await manager._await_with_gate_supervision(
                    "user-a", "task-a", 1, execution
                )
            # 硬门命中：停容器与 Sidecar + 标记取消 + 取消执行。
            assert len(runtime.cancel_calls) == 1
            assert cancelled_marks == [("user-a", "task-a", 1)]
            assert execution.cancelled()

        asyncio.run(run())

    def test_violation_during_cancel_does_not_return_result(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """竞态（A2）：监督命中后的容器清理期间执行恰好正常完成，
        结果也不得返回——监督优先失败关闭。"""

        monkeypatch.setattr(
            runtime_mod, "RUNTIME_GATE_POLL_SECONDS", 0.02
        )
        monkeypatch.setattr(settings, "webui_db_path", str(tmp_path / "w.db"))
        runtime = _GateFakeRuntime()
        manager = SemanticWorkspaceManager(pi_runtime=runtime)
        cancelled_marks: list = []
        monkeypatch.setattr(
            manager,
            "_selection_has_capabilities",
            _async_counter([], result=True),
        )
        monkeypatch.setattr(
            manager,
            "_runtime_gate_violation",
            _async_counter([], result=True),
        )
        monkeypatch.setattr(
            manager,
            "_mark_cancelled",
            lambda user_id, task_id, revision: cancelled_marks.append(
                (user_id, task_id, revision)
            ),
        )

        async def run():
            execution: asyncio.Future = (
                asyncio.get_running_loop().create_future()
            )

            # 容器清理期间执行完成：execution.cancel() 对已完成 Future 无效。
            async def cancel_then_complete(user_id, task_id, revision):
                runtime.cancel_calls.append((user_id, task_id, revision))
                if not execution.done():
                    execution.set_result("completed-during-cancel")

            monkeypatch.setattr(runtime, "cancel", cancel_then_complete)
            with pytest.raises(runtime_mod._GateViolationAbort):
                await manager._await_with_gate_supervision(
                    "user-a", "task-a", 1, execution
                )
            assert len(runtime.cancel_calls) == 1
            assert cancelled_marks == [("user-a", "task-a", 1)]

        asyncio.run(run())

    def test_violation_read_error_keeps_supervising(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """B2：投影读取异常不是确定性违反——跳过本轮继续监督，
        任务正常完成时不被误杀。"""

        monkeypatch.setattr(
            runtime_mod, "RUNTIME_GATE_POLL_SECONDS", 0.02
        )
        monkeypatch.setattr(settings, "webui_db_path", str(tmp_path / "w.db"))
        runtime = _GateFakeRuntime()
        manager = SemanticWorkspaceManager(pi_runtime=runtime)
        monkeypatch.setattr(
            manager,
            "_selection_has_capabilities",
            _async_counter([], result=True),
        )

        async def flaky_violation(self, *args):
            if not calls:
                calls.append(args)
                raise RuntimeError("数据库瞬时故障")
            calls.append(args)
            return False

        calls: list = []
        monkeypatch.setattr(manager, "_runtime_gate_violation", flaky_violation)

        async def run():
            execution: asyncio.Future = (
                asyncio.get_running_loop().create_future()
            )

            # 执行在监督经历「读取异常 → 继续轮询」多轮之后才完成。
            async def complete_later():
                await asyncio.sleep(0.3)
                execution.set_result("clean-result")

            completer = asyncio.ensure_future(complete_later())
            result = await manager._await_with_gate_supervision(
                "user-a", "task-a", 1, execution
            )
            await completer
            assert result == "clean-result"
            assert runtime.cancel_calls == []
            # 异常后继续轮询（而非退出监督或误杀）。
            assert len(calls) >= 2

        asyncio.run(run())

    def test_cancel_cleanup_failure_is_recorded(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """B5：容器清理失败不掩盖治理取消事实，但必须留痕工作台事件。"""

        monkeypatch.setattr(
            runtime_mod, "RUNTIME_GATE_POLL_SECONDS", 0.02
        )
        monkeypatch.setattr(settings, "webui_db_path", str(tmp_path / "w.db"))
        runtime = _GateFakeRuntime()

        async def failing_cancel(user_id, task_id, revision):
            runtime.cancel_calls.append((user_id, task_id, revision))
            raise RuntimeError("容器已被外部移除")

        monkeypatch.setattr(runtime, "cancel", failing_cancel)
        events: list = []

        class _EventStore:
            def append_semantic_workspace_event(
                self, user_id, task_id, **kwargs
            ):
                events.append((user_id, task_id, kwargs))

        monkeypatch.setattr(runtime_mod, "get_store", lambda: _EventStore())
        manager = SemanticWorkspaceManager(pi_runtime=runtime)
        monkeypatch.setattr(
            manager,
            "_selection_has_capabilities",
            _async_counter([], result=True),
        )
        monkeypatch.setattr(
            manager,
            "_runtime_gate_violation",
            _async_counter([], result=True),
        )
        monkeypatch.setattr(
            manager,
            "_mark_cancelled",
            lambda user_id, task_id, revision: None,
        )

        async def run():
            execution: asyncio.Future = asyncio.get_running_loop().create_future()
            with pytest.raises(runtime_mod._GateViolationAbort):
                await manager._await_with_gate_supervision(
                    "user-a", "task-a", 1, execution
                )
            assert len(runtime.cancel_calls) == 1
            assert events
            assert events[0][2]["event_type"] == "gate_cancel_cleanup_failed"
            assert "容器" in events[0][2]["summary"]
            assert execution.cancelled()

        asyncio.run(run())

    def test_gate_violation_abort_does_not_mark_failed(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """B1：_run_pi_task 捕获专用异常静默退出，状态保持 cancelled，
        不落 failed 标记（事件流不再矛盾）。"""

        class _FakeRuntimeRepository:
            def __init__(self, runtime_row):
                self._row = runtime_row
                self.updates: list = []

            def get(self, user_id, task_id, revision):
                return dict(self._row)

            def update(self, user_id, task_id, revision, **fields):
                self.updates.append(fields)

        repository = _FakeRuntimeRepository(
            {
                "status": runtime_mod.RuntimeStatus.PREPARING,
                "run_id": None,
                "workspace_root": None,
                "container_name": None,
                "session_file": None,
                "runtime_version": runtime_mod.RuntimeVersion.PI,
                "permission_profile": PermissionProfile.STANDARD,
                "model_connection_id": None,
            }
        )
        monkeypatch.setattr(
            runtime_mod, "AgenticRuntimeRepository", lambda db_path: repository
        )

        class _Store:
            def get_semantic_workspace_revision(
                self, user_id, task_id, revision
            ):
                return {
                    "objective_text": "汇总目标",
                    "output_formats": ("json",),
                    "table_output_contracts": [],
                }

            def get_web_task_contract(self, user_id, task_id, revision):
                return None

            def append_semantic_workspace_event(
                self, user_id, task_id, **kwargs
            ): ...

            def update_semantic_workspace_task(
                self, user_id, task_id, **kwargs
            ): ...

            def update_semantic_workspace_revision(
                self, user_id, task_id, revision, **kwargs
            ): ...

        class _Upload:
            upload_id = "u1"
            original_name = "f.csv"
            storage_path = str(tmp_path / "f.csv")
            sha256 = "a" * 64
            media_type = "text/csv"

        class _UploadStore:
            def resolve(self, user_id, upload_id):
                return _Upload()

        class _StartFakeRuntime(_GateFakeRuntime):
            """start 挂起不完成：_await_with_gate_supervision 已被替换，
            执行包装体本身不需要结果。"""

            async def start(self, request, on_event=None):
                await asyncio.get_running_loop().create_future()

        monkeypatch.setattr(runtime_mod, "get_store", lambda: _Store())
        monkeypatch.setattr(
            runtime_mod, "_upload_store", lambda: _UploadStore()
        )
        # 本用例只验证运行期 Gate 命中后的退出语义；显式数据库迁移门由独立迁移测试覆盖。
        # 注入占位服务可避免测试误连维护者数据库，同时若执行越过 Gate 仍会因缺少方法而失败。
        manager = SemanticWorkspaceManager(
            pi_runtime=_StartFakeRuntime(),
            candidate_verification=object(),
        )

        async def gate_hit(self, *args, **kwargs):
            raise runtime_mod._GateViolationAbort("测试命中")

        monkeypatch.setattr(manager, "_await_with_gate_supervision", gate_hit)

        async def run():
            await manager._run_pi_task(
                "user-a",
                "task-a",
                1,
                {
                    "objective_text": "汇总目标",
                    "output_formats": ("json",),
                    "upload_ids": ["u1"],
                    "model": None,
                },
            )

        # 静默退出：不抛异常，也绝不落 failed 状态标记。
        asyncio.run(run())
        statuses = [
            fields.get("status") for fields in repository.updates
        ]
        assert statuses
        assert "failed" not in statuses

    def test_clean_execution_passes_through_without_cancel(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            runtime_mod, "RUNTIME_GATE_POLL_SECONDS", 0.02
        )
        monkeypatch.setattr(settings, "webui_db_path", str(tmp_path / "w.db"))
        runtime = _GateFakeRuntime()
        manager = SemanticWorkspaceManager(pi_runtime=runtime)
        monkeypatch.setattr(
            manager,
            "_selection_has_capabilities",
            _async_counter([], result=True),
        )
        monkeypatch.setattr(
            manager,
            "_runtime_gate_violation",
            _async_counter([], result=False),
        )

        async def run():
            execution: asyncio.Future = asyncio.get_running_loop().create_future()
            execution.set_result("clean-result")
            result = await manager._await_with_gate_supervision(
                "user-a", "task-a", 1, execution
            )
            assert result == "clean-result"
            assert runtime.cancel_calls == []

        asyncio.run(run())


def _async_counter(calls: list, *, result):
    async def fake(self, *args):
        calls.append(args)
        return result

    return fake
