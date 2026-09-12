# -*- coding: utf-8 -*-
"""AC-01/03：通过工作台 HTTP Interface 验证追问与进度。"""
from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.auth as auth_mod
from src.api.auth import get_current_user, get_store
from src.api.routes import semantic_workspace
from src.api.semantic_workspace_runtime import SemanticWorkspaceManager
from src.agentic_runtime.models import (
    PermissionProfile,
    RuntimeTaskConfig,
    RuntimeVersion,
)
from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.config.settings import settings
from tests.database_migration_helpers import migrated_webui_database
from src.conversation_steering import (
    ContextDelta,
    DeltaConfidence,
    RawUserTurn,
    SteeringRequest,
    TurnIntent,
)


import pytest
from src.account_execution import ExecutionAuthorization, execution_context
from tests.account_execution_helpers import seed_execution_owner


@pytest.fixture(autouse=True)
def _authenticated_execution():
    with execution_context(ExecutionAuthorization("user-a", 0)):
        yield


class _ApiStatusRewriter:
    async def rewrite(
        self,
        turn: RawUserTurn,
        request: SteeringRequest,
    ) -> ContextDelta:
        return ContextDelta(
            delta_id="delta-api-status",
            owner_id=turn.owner_id,
            task_id=turn.task_id,
            inherited_revision=request.revision,
            source_turn_ids=tuple(item.turn_id for item in request.relevant_turns)+(turn.turn_id,),
            intent=TurnIntent.STATUS_QUESTION,
            confidence=DeltaConfidence.HIGH,
            normalized_text="询问当前进度",
            direct_answer=f"当前状态是 {request.current_status}，任务不会重启。",
        )


class _CountingStatusRewriter:
    def __init__(self) -> None:
        self.calls = 0
        self.contexts: list[tuple[str, ...]] = []

    async def rewrite(self, turn: RawUserTurn, request: SteeringRequest) -> ContextDelta:
        self.calls += 1
        self.contexts.append(tuple(item.text for item in request.relevant_turns))
        normalize = "口径" in turn.text
        return ContextDelta(
            delta_id=f"delta-regenerate-{self.calls}",
            owner_id=turn.owner_id,
            task_id=turn.task_id,
            inherited_revision=request.revision,
            source_turn_ids=tuple(item.turn_id for item in request.relevant_turns) + (turn.turn_id,),
            intent=TurnIntent.NORMALIZATION if normalize else TurnIntent.STATUS_QUESTION,
            confidence=DeltaConfidence.HIGH,
            normalized_text="重新回答原消息",
            direct_answer=None if normalize else f"第 {self.calls} 次回答",
        )


class _FailingStatusRewriter:
    def __init__(self) -> None:
        self.calls = 0

    async def rewrite(self, _turn: RawUserTurn, _request: SteeringRequest) -> ContextDelta:
        self.calls += 1
        raise RuntimeError("模拟模型调用后结果未知")


class _ApiMaterialRewriter:
    async def rewrite(
        self,
        turn: RawUserTurn,
        request: SteeringRequest,
    ) -> ContextDelta:
        return ContextDelta(
            delta_id="delta-api-material",
            owner_id=turn.owner_id,
            task_id=turn.task_id,
            inherited_revision=request.revision,
            source_turn_ids=tuple(item.turn_id for item in request.relevant_turns)+(turn.turn_id,),
            intent=TurnIntent.TASK_REFINEMENT,
            confidence=DeltaConfidence.HIGH,
            normalized_text="输出格式改为 CSV，其余已确认语义保持不变",
            output_delta=("csv",),
        )


class _ApiManager:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.enqueued: list[str] = []

    async def cancel(self, user_id: str, task_id: str):
        self.cancelled.append(task_id)
        return get_store().update_semantic_workspace_task(
            user_id,
            task_id,
            status="cancelled",
            cancel_requested=True,
        )

    def enqueue(self, _user_id: str, task_id: str) -> None:
        self.enqueued.append(task_id)


def test_running_followup_uses_turn_api_without_creating_revision(
    tmp_path,
    monkeypatch,
) -> None:
    database = migrated_webui_database(tmp_path / "workspace.db")
    seed_execution_owner(database, "user-a")
    seed_execution_owner(database, "user-b")
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    auth_mod._store = None
    user = {"value": "user-a"}
    app = FastAPI()
    app.include_router(semantic_workspace.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": user["value"],
        "role": "user",
        "execution_generation": 0,
    }
    monkeypatch.setattr(
        semantic_workspace,
        "build_context_rewriter",
        lambda _request: _ApiStatusRewriter(),
    )
    store = get_store()
    store.create_semantic_workspace_task(
        "user-a",
        task_id="workspace-1",
        title="报销审批",
        objective_text="提取王总的全部报销记录",
        upload_ids=[],
        output_formats=["json"],
        provider="local",
        model="qwen-local",
        external_api_confirmed=False,
    )
    store.update_semantic_workspace_task(
        "user-a",
        "workspace-1",
        status="running",
        run_id="run-existing",
        summary="正在检查来源",
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/semantic-workspace/tasks/workspace-1/turns",
            headers={"Idempotency-Key": "followup-1"},
            json={"text": "现在做到哪了？"},
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["action"] == "answer_only"
        assert result["run_id"] == "run-existing"
        assert result["revision"] == 1

        feedback = client.post(
            "/api/semantic-workspace/tasks/workspace-1/feedback",
            headers={"Idempotency-Key": "message-feedback-1"},
            json={
                "revision": 1,
                "result_id": result["result_id"],
                "rating": "up",
                "reasons": [],
                "comment": "回答清楚",
                "expected_version": 0,
            },
        )
        assert feedback.status_code == 200, feedback.text
        assert feedback.json()["result_id"] == result["result_id"]
        assert feedback.json()["turn_id"] == result["turn_id"]
        assert feedback.json()["run_id"] == "run-existing"
        restored = client.get(
            "/api/semantic-workspace/tasks/workspace-1/feedback",
            params={"revision": 1, "result_id": result["result_id"]},
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["feedback"]["comment"] == "回答清楚"
        with store._conn() as connection:
            saved = connection.execute(
                "SELECT id,target_kind,result_id,turn_id,run_id FROM workspace_feedback"
            ).fetchone()
            assert tuple(saved)[1:] == (
                "message", result["result_id"], result["turn_id"], "run-existing",
            )
            from src.api.feedback_audit import feedback_content
            _, audited = feedback_content(connection, -saved["id"])
        assert audited["answer_kind"] == "message"
        assert audited["content"]["question"] == "现在做到哪了？"
        assert audited["content"]["answer"] == "当前状态是 running，任务不会重启。"
        assert audited["source"]["result_id"] == result["result_id"]

        detail = client.get(
            "/api/semantic-workspace/tasks/workspace-1"
        ).json()
        assert detail["active_revision"] == 1
        assert any(
            event["event_type"] == "followup.answered_without_change"
            for event in detail["events"]
        )
        assert detail["progress"]["active_stage"] is not None

        user["value"] = "user-b"
        assert client.post(
            "/api/semantic-workspace/tasks/workspace-1/turns",
            json={"text": "进度？"},
        ).status_code == 404
        assert client.get(
            "/api/semantic-workspace/tasks/workspace-1/feedback",
            params={"revision": 1, "result_id": result["result_id"]},
        ).status_code == 404


def test_regenerate_binds_source_message_and_replays_without_another_model_call(
    tmp_path,
    monkeypatch,
) -> None:
    database = migrated_webui_database(tmp_path / "regenerate.db")
    seed_execution_owner(database, "user-a")
    seed_execution_owner(database, "user-b")
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    auth_mod._store = None
    user = {"value": "user-a"}
    app = FastAPI()
    app.include_router(semantic_workspace.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": user["value"], "role": "user", "execution_generation": 0,
    }
    rewriter = _CountingStatusRewriter()
    monkeypatch.setattr(semantic_workspace, "build_context_rewriter", lambda _request, **_kwargs: rewriter)
    store = get_store()
    store.create_semantic_workspace_task(
        "user-a", task_id="regenerate-task", title="重生成", objective_text="检查状态",
        upload_ids=[], output_formats=["json"], provider="local", model="qwen-local",
        external_api_confirmed=False,
    )
    store.update_semantic_workspace_task("user-a", "regenerate-task", status="running")

    with TestClient(app) as client:
        source = client.post(
            "/api/semantic-workspace/tasks/regenerate-task/turns",
            headers={"Idempotency-Key": "source"}, json={"text": "现在进度？"},
        ).json()
        path = f"/api/semantic-workspace/tasks/regenerate-task/turns/{source['result_id']}/regenerate"
        regenerated = client.post(
            path, headers={"Idempotency-Key": "regenerate-once"},
            json={"expected_revision": 1},
        )
        assert regenerated.status_code == 200, regenerated.text
        assert regenerated.json()["answer"] == "第 2 次回答"
        assert regenerated.json()["result_id"] != source["result_id"]
        assert client.post(
            path, headers={"Idempotency-Key": "regenerate-once"},
            json={"expected_revision": 1},
        ).json() == regenerated.json()
        assert rewriter.calls == 2
        public_events = client.get("/api/semantic-workspace/tasks/regenerate-task").json()["events"]
        assert all(event["event_type"] != "message.regeneration_claimed" for event in public_events)

        user["value"] = "user-b"
        assert client.post(
            path, headers={"Idempotency-Key": "other-owner"},
            json={"expected_revision": 1},
        ).status_code == 404

        user["value"] = "user-a"
        store.create_semantic_workspace_task(
            "user-a", task_id="external-task", title="外部重生成", objective_text="检查状态",
            upload_ids=[], output_formats=["json"], provider="deepseek", model="deepseek-chat",
            external_api_confirmed=True,
        )
        store.update_semantic_workspace_task("user-a", "external-task", status="running")
        external_source = client.post(
            "/api/semantic-workspace/tasks/external-task/turns",
            headers={"Idempotency-Key": "external-source"}, json={"text": "外部模型进度？"},
        ).json()
        external_path = f"/api/semantic-workspace/tasks/external-task/turns/{external_source['result_id']}/regenerate"
        rejected = client.post(
            external_path, headers={"Idempotency-Key": "external-regenerate"},
            json={"expected_revision": 1},
        )
        assert rejected.status_code == 422
        confirmed = client.post(
            external_path, headers={"Idempotency-Key": "external-regenerate"},
            json={"expected_revision": 1, "external_api_confirmed": True},
        )
        assert confirmed.status_code == 200, confirmed.text

        store.create_semantic_workspace_task(
            "user-a", task_id="context-task", title="上下文重生成", objective_text="检查状态",
            upload_ids=[], output_formats=["json"], provider="local", model="qwen-local",
            external_api_confirmed=False,
        )
        store.update_semantic_workspace_task("user-a", "context-task", status="running")
        client.post("/api/semantic-workspace/tasks/context-task/turns", json={"text": "采用旧口径"})
        target = client.post("/api/semantic-workspace/tasks/context-task/turns", json={"text": "目标问题"}).json()
        client.post("/api/semantic-workspace/tasks/context-task/turns", json={"text": "采用新口径"})
        exact = client.post(
            f"/api/semantic-workspace/tasks/context-task/turns/{target['result_id']}/regenerate",
            headers={"Idempotency-Key": "exact-context"}, json={"expected_revision": 1},
        )
        assert exact.status_code == 200, exact.text
        assert rewriter.contexts[-1] == ("采用旧口径",)

        provider_calls: list[str] = []
        def race_builder(_request, *, before_call=None):
            store.update_semantic_workspace_task("user-a", "context-task", cancel_requested=True)
            class RaceRewriter:
                async def rewrite(self, turn, request):
                    if before_call:
                        before_call()
                    provider_calls.append(turn.turn_id)
                    return await rewriter.rewrite(turn, request)
            return RaceRewriter()
        monkeypatch.setattr(semantic_workspace, "build_context_rewriter", race_builder)
        raced = client.post(
            f"/api/semantic-workspace/tasks/context-task/turns/{target['result_id']}/regenerate",
            headers={"Idempotency-Key": "revision-race"}, json={"expected_revision": 1},
        )
        assert raced.status_code == 409
        assert provider_calls == []


def test_regenerate_unknown_result_never_repeats_model_call(tmp_path, monkeypatch) -> None:
    database = migrated_webui_database(tmp_path / "regenerate-unknown.db")
    seed_execution_owner(database, "user-a")
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    auth_mod._store = None
    app = FastAPI()
    app.include_router(semantic_workspace.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "user-a", "role": "user", "execution_generation": 0,
    }
    source_rewriter = _CountingStatusRewriter()
    monkeypatch.setattr(semantic_workspace, "build_context_rewriter", lambda _request, **_kwargs: source_rewriter)
    store = get_store()
    store.create_semantic_workspace_task(
        "user-a", task_id="unknown-task", title="重生成", objective_text="检查状态",
        upload_ids=[], output_formats=["json"], provider="local", model="qwen-local",
        external_api_confirmed=False,
    )
    store.update_semantic_workspace_task("user-a", "unknown-task", status="running")

    with TestClient(app, raise_server_exceptions=False) as client:
        source = client.post(
            "/api/semantic-workspace/tasks/unknown-task/turns",
            headers={"Idempotency-Key": "source"}, json={"text": "现在进度？"},
        ).json()
        failing = _FailingStatusRewriter()
        monkeypatch.setattr(semantic_workspace, "build_context_rewriter", lambda _request, **_kwargs: failing)
        path = f"/api/semantic-workspace/tasks/unknown-task/turns/{source['result_id']}/regenerate"
        assert client.post(path, headers={"Idempotency-Key": "unknown"}, json={"expected_revision": 1}).status_code == 500
        replay = client.post(path, headers={"Idempotency-Key": "unknown"}, json={"expected_revision": 1})
        assert replay.status_code == 409
        assert "结果未知" in replay.json()["detail"]
        assert failing.calls == 1


def test_material_followup_only_creates_confirmation_proposal(
    tmp_path,
    monkeypatch,
) -> None:
    database = migrated_webui_database(tmp_path / "workspace.db")
    seed_execution_owner(database, "user-a")
    seed_execution_owner(database, "user-b")
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    auth_mod._store = None
    app = FastAPI()
    app.include_router(semantic_workspace.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "user-a",
        "role": "user",
        "execution_generation": 0,
    }
    monkeypatch.setattr(
        semantic_workspace,
        "build_context_rewriter",
        lambda _request: _ApiMaterialRewriter(),
    )
    store = get_store()
    store.create_semantic_workspace_task(
        "user-a",
        task_id="workspace-2",
        title="报销审批",
        objective_text="提取王总的全部报销记录",
        upload_ids=[],
        output_formats=["json"],
        provider="local",
        model="qwen-local",
        external_api_confirmed=False,
    )
    store.update_semantic_workspace_task(
        "user-a",
        "workspace-2",
        status="running",
        run_id="run-existing",
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/semantic-workspace/tasks/workspace-2/turns",
            headers={"Idempotency-Key": "material-1"},
            json={"text": "改成 CSV"},
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["action"] == "revision_proposal"
        assert result["proposal_id"]
        assert result["revision"] == 1

        repeated = client.post(
            "/api/semantic-workspace/tasks/workspace-2/turns",
            headers={"Idempotency-Key": "material-1"},
            json={"text": "改成 CSV"},
        )
        assert repeated.json()["proposal_id"] == result["proposal_id"]
        detail = client.get(
            "/api/semantic-workspace/tasks/workspace-2"
        ).json()
        assert detail["active_revision"] == 1
        assert len(detail["revisions"]) == 1
        proposal_events = [
            event
            for event in detail["events"]
            if event["event_type"] == "context.revision_proposed"
        ]
        assert len(proposal_events) == 1

        thread = client.get(
            "/api/semantic-workspace/tasks/workspace-2/turns"
        )
        assert thread.status_code == 200, thread.text
        assert thread.json()["turns"][0]["text"] == "改成 CSV"
        assert thread.json()["deltas"][0]["normalized_text"].startswith("输出格式")
        assert thread.json()["proposals"][0]["status"] == "pending"

        rejected = client.post(
            "/api/semantic-workspace/tasks/workspace-2/"
            f"revision-proposals/{result['proposal_id']}/reject"
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["status"] == "rejected"
        unchanged = client.get(
            "/api/semantic-workspace/tasks/workspace-2"
        ).json()
        assert unchanged["status"] == "running"
        assert unchanged["run_id"] == "run-existing"
        assert unchanged["active_revision"] == 1


def test_confirmed_cancel_now_applies_semantic_delta_as_v2(
    tmp_path,
    monkeypatch,
) -> None:
    database = migrated_webui_database(tmp_path / "workspace.db")
    seed_execution_owner(database, "user-a")
    seed_execution_owner(database, "user-b")
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    auth_mod._store = None
    manager = _ApiManager()
    app = FastAPI()
    app.include_router(semantic_workspace.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "user-a",
        "role": "user",
        "execution_generation": 0,
    }
    monkeypatch.setattr(
        semantic_workspace,
        "build_context_rewriter",
        lambda _request: _ApiMaterialRewriter(),
    )
    monkeypatch.setattr(
        semantic_workspace,
        "get_semantic_workspace_manager",
        lambda: manager,
    )
    store = get_store()
    store.create_semantic_workspace_task(
        "user-a",
        task_id="workspace-3",
        title="报销审批",
        objective_text="提取王总的全部报销记录",
        upload_ids=[],
        output_formats=["json"],
        provider="local",
        model="qwen-local",
        external_api_confirmed=False,
    )
    store.update_semantic_workspace_task(
        "user-a", "workspace-3", status="running", run_id="run-v1"
    )

    with TestClient(app) as client:
        proposal_id = client.post(
            "/api/semantic-workspace/tasks/workspace-3/turns",
            json={"text": "改成 CSV"},
        ).json()["proposal_id"]
        response = client.post(
            f"/api/semantic-workspace/tasks/workspace-3/"
            f"revision-proposals/{proposal_id}/decision",
            json={"mode": "cancel_now"},
        )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["decision"]["status"] == "applied"
        assert body["revision"]["revision"] == 2
        assert body["revision"]["output_formats"] == ["csv"]
        assert manager.cancelled == ["workspace-3"]
        assert manager.enqueued == ["workspace-3"]

        detail = client.get(
            "/api/semantic-workspace/tasks/workspace-3"
        ).json()
        assert detail["active_revision"] == 2
        assert detail["run_id"] is None
        assert "已确认的上下文变更" in detail["objective_text"]


def test_cancel_now_cannot_create_revision_until_old_run_is_stopped(
    tmp_path,
    monkeypatch,
) -> None:
    database = migrated_webui_database(tmp_path / "workspace.db")
    seed_execution_owner(database, "user-a")
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    auth_mod._store = None

    class CleanupPendingManager(_ApiManager):
        async def cancel(self, user_id, task_id):
            self.cancelled.append(task_id)
            return get_store().update_semantic_workspace_task(
                user_id, task_id, status="cancelling", cancel_requested=True,
            )

    manager = CleanupPendingManager()
    app = FastAPI()
    app.include_router(semantic_workspace.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "user-a", "role": "user", "execution_generation": 0,
    }
    monkeypatch.setattr(
        semantic_workspace, "build_context_rewriter", lambda _request: _ApiMaterialRewriter(),
    )
    monkeypatch.setattr(
        semantic_workspace, "get_semantic_workspace_manager", lambda: manager,
    )
    store = get_store()
    store.create_semantic_workspace_task(
        "user-a", task_id="workspace-cleanup-pending", title="合成报销任务",
        objective_text="提取全部报销记录", upload_ids=[], output_formats=["json"],
        provider="local", model="fixture-local", external_api_confirmed=False,
    )
    store.update_semantic_workspace_task(
        "user-a", "workspace-cleanup-pending", status="needs_input", run_id="run-v1",
    )
    path = "/api/semantic-workspace/tasks/workspace-cleanup-pending"
    with TestClient(app) as client:
        proposed = client.post(f"{path}/turns", json={"text": "改成 CSV"})
        assert proposed.status_code == 200, proposed.text
        response = client.post(
            f"{path}/revision-proposals/{proposed.json()['proposal_id']}/decision",
            json={"mode": "cancel_now"},
        )
        detail = client.get(path).json()

    assert response.status_code == 409, response.text
    assert detail["active_revision"] == 1
    assert detail["status"] == "cancelling"
    assert detail["run_id"] == "run-v1"
    assert manager.enqueued == []


def test_after_safe_point_is_persisted_then_applied_by_worker(
    tmp_path,
    monkeypatch,
) -> None:
    database = migrated_webui_database(tmp_path / "workspace.db")
    seed_execution_owner(database, "user-a")
    seed_execution_owner(database, "user-b")
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    auth_mod._store = None
    app = FastAPI()
    app.include_router(semantic_workspace.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "user-a",
        "role": "user",
        "execution_generation": 0,
    }
    monkeypatch.setattr(
        semantic_workspace,
        "build_context_rewriter",
        lambda _request: _ApiMaterialRewriter(),
    )
    store = get_store()
    store.create_semantic_workspace_task(
        "user-a",
        task_id="workspace-4",
        title="报销审批",
        objective_text="提取王总的全部报销记录",
        upload_ids=[],
        output_formats=["json"],
        provider="local",
        model="qwen-local",
        external_api_confirmed=False,
    )
    store.update_semantic_workspace_task(
        "user-a", "workspace-4", status="running", run_id="run-v1"
    )

    with TestClient(app) as client:
        proposal_id = client.post(
            "/api/semantic-workspace/tasks/workspace-4/turns",
            json={"text": "改成 CSV"},
        ).json()["proposal_id"]
        response = client.post(
            f"/api/semantic-workspace/tasks/workspace-4/"
            f"revision-proposals/{proposal_id}/decision",
            json={"mode": "after_safe_point"},
        )
        assert response.status_code == 202, response.text
        assert response.json()["decision"]["status"] == "waiting_safe_point"
        assert store.get_semantic_workspace_task("user-a", "workspace-4")[
            "active_revision"
        ] == 1

        manager = SemanticWorkspaceManager()
        assert asyncio.run(
            manager._apply_waiting_revision_at_safe_point(
                "user-a", "workspace-4", 1, "sources_bound"
            )
        )
        switched = store.get_semantic_workspace_task("user-a", "workspace-4")
        assert switched["active_revision"] == 2
        assert switched["run_id"] is None
        assert "workspace-4" in manager._deferred_requeue
        store.update_semantic_workspace_task("user-a", "workspace-4", status="completed")
        replay = client.post(
            f"/api/semantic-workspace/tasks/workspace-4/revision-proposals/{proposal_id}/decision",
            json={"mode": "after_safe_point"},
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["revision"]["revision"] == 2
        assert manager._deferred_requeue == {"workspace-4"}


def test_new_task_choice_keeps_current_run_and_creates_isolated_task(
    tmp_path,
    monkeypatch,
) -> None:
    database = migrated_webui_database(tmp_path / "workspace.db")
    seed_execution_owner(database, "user-a")
    seed_execution_owner(database, "user-b")
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    auth_mod._store = None
    manager = _ApiManager()
    app = FastAPI()
    app.include_router(semantic_workspace.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "user-a",
        "role": "user",
        "execution_generation": 0,
    }
    monkeypatch.setattr(
        semantic_workspace,
        "build_context_rewriter",
        lambda _request: _ApiMaterialRewriter(),
    )
    monkeypatch.setattr(
        semantic_workspace,
        "get_semantic_workspace_manager",
        lambda: manager,
    )
    store = get_store()
    store.create_semantic_workspace_task(
        "user-a",
        task_id="workspace-5",
        title="报销审批",
        objective_text="提取王总的全部报销记录",
        upload_ids=[],
        output_formats=["json"],
        provider="local",
        model="qwen-local",
        external_api_confirmed=False,
    )
    store.update_semantic_workspace_task(
        "user-a", "workspace-5", status="running", run_id="run-v1"
    )

    with TestClient(app) as client:
        proposal_id = client.post(
            "/api/semantic-workspace/tasks/workspace-5/turns",
            json={"text": "改成 CSV"},
        ).json()["proposal_id"]
        response = client.post(
            f"/api/semantic-workspace/tasks/workspace-5/"
            f"revision-proposals/{proposal_id}/decision",
            json={"mode": "new_task"},
        )
        assert response.status_code == 202, response.text
        new_task = response.json()["new_task"]
        assert new_task["task_id"] != "workspace-5"
        assert new_task["output_formats"] == ["csv"]
        assert store.get_semantic_workspace_task("user-a", "workspace-5")[
            "run_id"
        ] == "run-v1"
        assert manager.cancelled == []
        assert manager.enqueued == [new_task["task_id"]]
        replay = client.post(
            f"/api/semantic-workspace/tasks/workspace-5/revision-proposals/{proposal_id}/decision",
            json={"mode": "new_task"},
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["new_task"]["task_id"] == new_task["task_id"]
        assert manager.enqueued == [new_task["task_id"]]


def test_external_revision_confirmation_is_required_before_cancelling_run(
    tmp_path,
    monkeypatch,
) -> None:
    database = migrated_webui_database(tmp_path / "workspace.db")
    seed_execution_owner(database, "user-a")
    seed_execution_owner(database, "user-b")
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    auth_mod._store = None
    manager = _ApiManager()
    app = FastAPI()
    app.include_router(semantic_workspace.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "user-a",
        "role": "user",
        "execution_generation": 0,
    }
    monkeypatch.setattr(
        semantic_workspace,
        "build_context_rewriter",
        lambda _request: _ApiMaterialRewriter(),
    )
    monkeypatch.setattr(
        semantic_workspace,
        "get_semantic_workspace_manager",
        lambda: manager,
    )
    store = get_store()
    store.create_semantic_workspace_task(
        "user-a",
        task_id="workspace-external",
        title="外部模型任务",
        objective_text="提取王总的全部报销记录",
        upload_ids=[],
        output_formats=["json"],
        provider="deepseek",
        model="deepseek-v4-flash",
        external_api_confirmed=True,
    )
    store.update_semantic_workspace_task(
        "user-a", "workspace-external", status="running", run_id="run-v1"
    )
    AgenticRuntimeRepository(settings.webui_db_path).register(
        RuntimeTaskConfig(
            user_id="user-a",
            task_id="workspace-external",
            revision=1,
            runtime_version=RuntimeVersion.PI,
            permission_profile=PermissionProfile.STANDARD,
            model_connection_id="connection-external",
                model_connection_version="1",
            model_connection_model="deepseek-v4-flash",
            external_api_confirmed=True,
        )
    )

    with TestClient(app) as client:
        proposal_id = client.post(
            "/api/semantic-workspace/tasks/workspace-external/turns",
            json={"text": "改成 CSV"},
        ).json()["proposal_id"]
        response = client.post(
            f"/api/semantic-workspace/tasks/workspace-external/"
            f"revision-proposals/{proposal_id}/decision",
            json={"mode": "cancel_now"},
        )
        assert response.status_code == 422
        assert "外发" in response.json()["detail"]
        assert manager.cancelled == []
        assert store.get_semantic_workspace_task(
            "user-a", "workspace-external"
        )["run_id"] == "run-v1"
