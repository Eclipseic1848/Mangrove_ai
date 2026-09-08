"""确认新修订前的冻结身份、取消竞争与事务回滚。"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.account_execution import ExecutionAuthorization, execution_context
from src.api import auth
from src.api.routes import semantic_workspace as route
from src.api.semantic_workspace_runtime import SemanticWorkspaceManager
from src.agentic_runtime.kernel import RuntimeBinding
from src.agentic_runtime.models import RuntimeTaskConfig, RuntimeVersion
from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.config.settings import settings
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database
from tests.test_conversation_steering_api import _ApiManager, _ApiMaterialRewriter


@pytest.fixture
def proposed(tmp_path, monkeypatch):
    database = migrated_webui_database(tmp_path / "revision.db")
    seed_execution_owner(database, "user-a")
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    monkeypatch.setattr(auth, "_store", None)
    monkeypatch.setattr(route, "build_context_rewriter", lambda _: _ApiMaterialRewriter())
    # 隔离灰度策略；本组验证已选路线内的冻结身份和提交边界。
    monkeypatch.setattr(route, "_preview_revision_runtime", lambda user, **kwargs: route._RevisionRoutingPlan(selected_runtime=kwargs["requested_runtime"]))
    manager = _ApiManager()
    monkeypatch.setattr(route, "get_semantic_workspace_manager", lambda: manager)
    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[auth.get_current_user] = lambda: {
        "user_id": "user-a", "role": "user", "execution_generation": 0,
    }
    with execution_context(ExecutionAuthorization("user-a", 0)):
        store = auth.get_store()
        store.create_semantic_workspace_task(
            "user-a", task_id="safe-revision", title="合成订单", objective_text="统计订单",
            upload_ids=[], output_formats=["json"], provider="local", model="fixture",
            external_api_confirmed=False,
        )
        store.update_semantic_workspace_task("user-a", "safe-revision", status="running", run_id="old-run")
        with TestClient(app) as client:
            result = client.post("/api/semantic-workspace/tasks/safe-revision/turns", json={"text": "改成 CSV"})
            assert result.status_code == 200, result.text
            path = f"/api/semantic-workspace/tasks/safe-revision/revision-proposals/{result.json()['proposal_id']}/decision"
            yield client, store, manager, path


def _no_new_revision(store, manager):
    assert store.get_semantic_workspace_task("user-a", "safe-revision")["active_revision"] == 1
    assert store.get_semantic_workspace_revision("user-a", "safe-revision", 2) is None
    assert AgenticRuntimeRepository(store.db_path).get("user-a", "safe-revision", 2) is None
    assert manager.enqueued == []


def test_connection_version_drift_rejected_before_cancel(proposed, monkeypatch):
    client, store, manager, path = proposed
    AgenticRuntimeRepository(store.db_path).register(RuntimeTaskConfig(
        user_id="user-a", task_id="safe-revision", revision=1, runtime_version=RuntimeVersion.PI,
        model_connection_id="synthetic-connection", model_connection_version="old-version",
        model_connection_model="fixture", external_api_confirmed=True,
    ))
    monkeypatch.setattr(route, "get_default_broker", lambda: SimpleNamespace(
        freeze_connection=lambda *args: SimpleNamespace(connection_version="changed-version"),
    ))
    response = client.post(path, json={"mode": "cancel_now", "external_api_confirmed": True})
    assert response.status_code == 409, response.text
    assert "连接版本" in response.json()["detail"]
    assert manager.cancelled == []
    _no_new_revision(store, manager)


@pytest.mark.parametrize("changed", [{"adapter_id": "changed-adapter"}, {"model": "changed-model"}])
def test_binding_identity_drift_rejected_before_cancel(proposed, monkeypatch, changed):
    client, store, original_manager, path = proposed
    binding = RuntimeBinding(
        adapter_id="fixture-adapter", adapter_version="1", runtime_artifact="fixture-artifact",
        protocol_version="1", event_schema_version="1", capability_digest="a" * 64,
        external_run_id="old-run", model="fixture",
    )
    repository = AgenticRuntimeRepository(store.db_path)
    repository.register(RuntimeTaskConfig(user_id="user-a", task_id="safe-revision", revision=1, runtime_version=RuntimeVersion.PI))
    repository.append_event("user-a", "safe-revision", 1, event_type="kernel.binding.frozen",
                            summary="合成冻结绑定", details={"binding": binding.model_dump(mode="json")})
    calls = []

    class PreparedKernel:
        async def prepare_binding(self, **kwargs):
            calls.append(kwargs)
            return binding.model_copy(update={"external_run_id": "new-run", **changed}), None

    class BindingManager(_ApiManager):
        prepare_runtime_binding = SemanticWorkspaceManager.prepare_runtime_binding

        def _kernel(self, adapter_id):
            assert adapter_id == "fixture-adapter"
            return PreparedKernel()

    manager = BindingManager()
    monkeypatch.setattr(route, "get_semantic_workspace_manager", lambda: manager)
    response = client.post(path, json={"mode": "cancel_now"})
    assert response.status_code == 409, response.text
    assert len(calls) == 1 and calls[0]["model"] == "fixture"
    assert manager.cancelled == [] and original_manager.cancelled == []
    _no_new_revision(store, manager)


def test_second_cancel_before_revision_transaction_rejects_late_commit(proposed, monkeypatch):
    client, store, manager, path = proposed
    create = store.create_semantic_workspace_revision

    def second_cancel(*args, **kwargs):
        assert store.get_semantic_workspace_task("user-a", "safe-revision")["status"] == "cancelled"
        store.request_semantic_workspace_cancellation("user-a", "safe-revision")
        return create(*args, **kwargs)

    monkeypatch.setattr(store, "create_semantic_workspace_revision", second_cancel)
    response = client.post(path, json={"mode": "cancel_now"})
    assert response.status_code == 409, response.text
    assert manager.cancelled == ["safe-revision"]
    _no_new_revision(store, manager)


def test_decision_and_revision_rollback_together_then_replay_once(proposed, monkeypatch):
    client, store, manager, path = proposed
    create = store.create_semantic_workspace_revision

    def interrupt_commit(*args, **kwargs):
        hook = kwargs["transaction_hook"]

        def after_decision(connection):
            hook(connection)
            raise RuntimeError("合成决策提交中断")

        kwargs["transaction_hook"] = after_decision
        return create(*args, **kwargs)

    monkeypatch.setattr(store, "create_semantic_workspace_revision", interrupt_commit)
    interrupted = client.post(path, json={"mode": "cancel_now"})
    assert interrupted.status_code == 409, interrupted.text
    _no_new_revision(store, manager)
    with store._conn() as conn:
        decisions = conn.execute("SELECT status FROM conversation_revision_decisions").fetchall()
        assert len(decisions) == 1 and decisions[0][0] == "ready_to_apply"
    monkeypatch.setattr(store, "create_semantic_workspace_revision", create)
    resumed = client.post(path, json={"mode": "cancel_now"})
    assert resumed.status_code == 202, resumed.text
    replay = client.post(path, json={"mode": "cancel_now"})
    assert replay.status_code == 202, replay.text
    assert resumed.json()["decision"] == replay.json()["decision"]
    assert resumed.json()["revision"]["revision"] == replay.json()["revision"]["revision"] == 2
    assert manager.enqueued == ["safe-revision"]
    with store._conn() as conn:
        assert conn.execute("SELECT count(*) FROM semantic_workspace_revisions").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM conversation_revision_decisions WHERE status='applied'").fetchone()[0] == 1


@pytest.mark.parametrize("mode", ["cancel_now", "new_task"])
def test_waiting_decision_can_be_explicitly_changed_after_input_pause(proposed, mode):
    client, store, manager, path = proposed
    waiting = client.post(path, json={"mode": "after_safe_point"})
    assert waiting.status_code == 202, waiting.text
    waiting_decision = waiting.json()["decision"]
    assert waiting_decision["status"] == "waiting_safe_point"
    assert manager.cancelled == [] and manager.enqueued == []
    # 模拟升级前的真实持久格式：旧决策没有本票新增的目标关联默认字段。
    with store._conn() as conn:
        import json
        row = conn.execute("SELECT payload_json FROM conversation_revision_decisions WHERE decision_id=?", (waiting_decision["decision_id"],)).fetchone()
        legacy = json.loads(row[0])
        legacy.pop("applied_revision")
        legacy.pop("applied_task_id")
        conn.execute("UPDATE conversation_revision_decisions SET payload_json=? WHERE decision_id=?", (json.dumps(legacy, ensure_ascii=False), waiting_decision["decision_id"]))
    # 确定性注入真实可能的等待输入状态，不假设旧 Run 必经安全点。
    store.update_semantic_workspace_task("user-a", "safe-revision", status="needs_input")
    chosen = client.post(path, json={"mode": mode})
    assert chosen.status_code == 202, chosen.text
    assert chosen.json()["decision"]["decision_id"] == waiting_decision["decision_id"]
    assert chosen.json()["decision"]["status"] == "applied"
    repeated = client.post(path, json={"mode": mode})
    assert repeated.status_code == 202, repeated.text
    assert repeated.json()["decision"] == chosen.json()["decision"]
    if mode == "cancel_now":
        assert chosen.json()["revision"]["revision"] == repeated.json()["revision"]["revision"] == 2
        assert manager.cancelled == ["safe-revision"]
        assert manager.enqueued == ["safe-revision"]
    else:
        target = chosen.json()["new_task"]["task_id"]
        assert repeated.json()["new_task"]["task_id"] == target != "safe-revision"
        assert manager.cancelled == [] and manager.enqueued == [target]
        original = store.get_semantic_workspace_task("user-a", "safe-revision")
        assert original["active_revision"] == 1 and original["status"] == "needs_input"
    with store._conn() as conn:
        import json
        assert conn.execute("SELECT count(*) FROM semantic_workspace_tasks").fetchone()[0] == (1 if mode == "cancel_now" else 2)
        assert conn.execute("SELECT count(*) FROM semantic_workspace_revisions").fetchone()[0] == 2
        changes = conn.execute("SELECT details_json FROM semantic_workspace_events WHERE event_type='revision.choice_changed'").fetchall()
        assert len(changes) == 1
        audit = json.loads(changes[0][0])
        assert audit["decision_id"] == waiting_decision["decision_id"]
        assert audit["previous_mode"] == "after_safe_point" and audit["mode"] == mode
        assert audit["previous_external_api_confirmed"] is False and audit["external_api_confirmed"] is False
