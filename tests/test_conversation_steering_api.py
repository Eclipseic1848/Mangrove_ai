# -*- coding: utf-8 -*-
"""AC-01/03：通过工作台 HTTP Interface 验证追问与进度。"""
from __future__ import annotations

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
            source_turn_ids=(turn.turn_id,),
            intent=TurnIntent.STATUS_QUESTION,
            confidence=DeltaConfidence.HIGH,
            normalized_text="询问当前进度",
            direct_answer=f"当前状态是 {request.current_status}，任务不会重启。",
        )


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
            source_turn_ids=(turn.turn_id,),
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
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    auth_mod._store = None
    user = {"value": "user-a"}
    app = FastAPI()
    app.include_router(semantic_workspace.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": user["value"],
        "role": "user",
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


def test_material_followup_only_creates_confirmation_proposal(
    tmp_path,
    monkeypatch,
) -> None:
    database = migrated_webui_database(tmp_path / "workspace.db")
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    auth_mod._store = None
    app = FastAPI()
    app.include_router(semantic_workspace.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "user-a",
        "role": "user",
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
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    auth_mod._store = None
    manager = _ApiManager()
    app = FastAPI()
    app.include_router(semantic_workspace.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "user-a",
        "role": "user",
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


def test_after_safe_point_is_persisted_then_applied_by_worker(
    tmp_path,
    monkeypatch,
) -> None:
    database = migrated_webui_database(tmp_path / "workspace.db")
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    auth_mod._store = None
    app = FastAPI()
    app.include_router(semantic_workspace.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "user-a",
        "role": "user",
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
        assert manager._apply_waiting_revision_at_safe_point(
            "user-a", "workspace-4", 1, "sources_bound"
        )
        switched = store.get_semantic_workspace_task("user-a", "workspace-4")
        assert switched["active_revision"] == 2
        assert switched["run_id"] is None
        assert "workspace-4" in manager._deferred_requeue


def test_new_task_choice_keeps_current_run_and_creates_isolated_task(
    tmp_path,
    monkeypatch,
) -> None:
    database = migrated_webui_database(tmp_path / "workspace.db")
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    auth_mod._store = None
    manager = _ApiManager()
    app = FastAPI()
    app.include_router(semantic_workspace.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "user-a",
        "role": "user",
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


def test_external_revision_confirmation_is_required_before_cancelling_run(
    tmp_path,
    monkeypatch,
) -> None:
    database = migrated_webui_database(tmp_path / "workspace.db")
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    auth_mod._store = None
    manager = _ApiManager()
    app = FastAPI()
    app.include_router(semantic_workspace.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "user-a",
        "role": "user",
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
