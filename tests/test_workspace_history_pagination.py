"""工作台历史分页：验证完整发现、状态筛选和 Owner/回收站隔离。"""
from src.account_execution import ExecutionAuthorization, execution_context
from src.api.auth import get_store
from tests.test_semantic_workspace_api import _client


def test_history_pages_filter_before_limit_and_preserve_owner(tmp_path, monkeypatch):
    client, user = _client(tmp_path, monkeypatch)
    store = get_store()
    # 相同时刻的记录也必须有稳定顺序；只冻结时间，不模拟 Repository。
    monkeypatch.setattr("src.api.store._now", lambda: "2026-09-26T00:00:00Z")

    def seed(owner, task_id, status="completed", deleted=False):
        with execution_context(ExecutionAuthorization(owner, 0)):
            store.create_semantic_workspace_task(
                owner, task_id=task_id, title=task_id, objective_text="分页合成记录",
                upload_ids=[], output_formats=["json"], provider="local",
                model="synthetic", external_api_confirmed=False,
            )
            store.update_semantic_workspace_task(owner, task_id, status=status)
            if deleted:
                store.soft_delete_semantic_workspace_task(owner, task_id)

    states = ["needs_input", "candidate_ready", "queued", "running", "cancelling"]
    for i in range(105):
        seed("user-a", f"task-{i:03}", states[i] if i < len(states) else "completed")
    for i in range(3):
        seed("user-a", f"recycle-{i}", deleted=True)
    seed("user-b", "foreign-task")

    def ids(**params):
        response = client.get("/api/semantic-workspace/tasks", params=params)
        assert response.status_code == 200, response.text
        return [item["task_id"] for item in response.json()]

    assert ids() == [f"task-{i:03}" for i in range(104, 4, -1)]
    assert ids(offset=100) == ["task-004", "task-003", "task-002", "task-001", "task-000"]
    assert ids(filter="needs_input", limit=1) == ["task-001"]
    assert ids(filter="needs_input", limit=1, offset=1) == ["task-000"]
    assert ids(filter="active") == ["task-004", "task-003", "task-002"]
    assert len(ids(filter="completed")) == 100
    assert ids(status="running") == ["task-003"]
    assert ids(deleted=True, limit=2) == ["recycle-2", "recycle-1"]
    assert ids(deleted=True, offset=2) == ["recycle-0"]
    assert ids(offset=105) == []
    for invalid in [{"offset": -1}, {"offset": 2**63}, {"filter": "unknown"}, {"limit": 501}]:
        assert client.get("/api/semantic-workspace/tasks", params=invalid).status_code == 422
    user["value"] = "user-b"
    assert ids() == ["foreign-task"]
    assert ids(offset=100) == []
