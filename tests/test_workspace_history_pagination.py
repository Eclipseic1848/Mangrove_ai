"""工作台历史分页：验证完整发现、状态筛选和 Owner/回收站隔离。"""
import pytest

from src.agentic_runtime.models import PermissionProfile, RuntimeTaskConfig, RuntimeVersion
from src.agentic_runtime.repository import AgenticRuntimeRepository
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


def test_history_checks_runtime_schema_once_per_request(tmp_path, monkeypatch):
    client, _user = _client(tmp_path, monkeypatch)
    store = get_store()
    with execution_context(ExecutionAuthorization("user-a", 0)):
        for task_id in ("expense", "legacy", "pi-task"):
            store.create_semantic_workspace_task(
                "user-a", task_id=task_id, title=task_id, objective_text="历史加载合成记录",
                upload_ids=[], output_formats=["json"], provider="local",
                model="synthetic", external_api_confirmed=False,
            )
    repository = AgenticRuntimeRepository(store.db_path)
    with execution_context(ExecutionAuthorization("user-a", 0)):
        repository.register(RuntimeTaskConfig(
            user_id="user-a", task_id="pi-task", revision=1,
            runtime_version=RuntimeVersion.PI, permission_profile=PermissionProfile.EXTENDED,
        ))
    from src.agentic_runtime import repository as runtime_repository
    inspect = runtime_repository.inspect_database
    checks = []

    def counted_inspect(target):
        checks.append(target)
        return inspect(target)

    monkeypatch.setattr(runtime_repository, "inspect_database", counted_inspect)
    response = client.get("/api/semantic-workspace/tasks")
    assert response.status_code == 200, response.text
    items = {item["task_id"]: item for item in response.json()}
    assert set(items) == {"expense", "legacy", "pi-task"}
    assert items["expense"]["runtime_version"] == "legacy"
    assert items["pi-task"]["runtime_version"] == "pi"
    assert items["pi-task"]["permission_profile"] == "extended"
    assert len(checks) == 1, "同一页历史不能按记录数重复核对整库结构"
    checks.clear()
    assert client.get("/api/semantic-workspace/tasks?offset=3").json() == []
    assert not checks
    assert client.get("/api/semantic-workspace/tasks").status_code == 200
    assert len(checks) == 1, "下一次请求仍须重新核对结构，不使用跨请求缓存"

    def reject_schema(_target):
        raise RuntimeError("合成数据库结构不兼容")

    monkeypatch.setattr(runtime_repository, "inspect_database", reject_schema)
    with pytest.raises(RuntimeError, match="合成数据库结构不兼容"):
        client.get("/api/semantic-workspace/tasks")


def test_storage_reuses_delivery_schema_check_and_preserves_owner(tmp_path, monkeypatch):
    client, user = _client(tmp_path, monkeypatch)
    store = get_store()
    for owner, task_id, deleted in (("user-a", "current", False), ("user-a", "recycled", True), ("user-b", "foreign", False)):
        with execution_context(ExecutionAuthorization(owner, 0)):
            store.create_semantic_workspace_task(
                owner, task_id=task_id, title=task_id, objective_text="占用统计合成记录",
                upload_ids=[], output_formats=["json"], provider="local",
                model="synthetic", external_api_confirmed=False,
            )
            store.update_semantic_workspace_task(owner, task_id, status="completed", run_id=task_id)
            store.update_semantic_workspace_revision(owner, task_id, 1, status="completed", run_id=task_id)
            if deleted:
                store.soft_delete_semantic_workspace_task(owner, task_id)
    from src.delivery_publishing import repository as delivery_repository
    inspect = delivery_repository.inspect_database
    checks = []
    reads = []

    def counted_inspect(target):
        checks.append(target)
        return inspect(target)

    def delivery(_repository, owner, run_id):
        reads.append((owner, run_id))
        return {"outputs": [{"output_id": "shared-output" if owner == "user-a" else "foreign-output", "size_bytes": 42 if owner == "user-a" else 99}]}

    monkeypatch.setattr(delivery_repository, "inspect_database", counted_inspect)
    monkeypatch.setattr(delivery_repository.DeliveryPublishingRepository, "latest_delivery", delivery)
    response = client.get("/api/semantic-workspace/storage")
    assert response.status_code == 200, response.text
    assert response.json()["task_count"] == 2
    assert response.json()["recycle_bin_count"] == 1
    assert response.json()["delivery_bytes"] == 42
    assert set(reads) == {("user-a", "current"), ("user-a", "recycled")}
    assert len(checks) == 1, "一次占用统计不能按历史修订重复核对整库结构"
    checks.clear()
    user["value"] = "user-b"
    response = client.get("/api/semantic-workspace/storage")
    assert response.status_code == 200
    assert response.json()["task_count"] == 1
    assert response.json()["delivery_bytes"] == 99
    assert len(checks) == 1, "新请求和其他Owner必须重新检查结构"

    def reject_schema(_target):
        raise RuntimeError("合成数据库结构不兼容")

    monkeypatch.setattr(delivery_repository, "inspect_database", reject_schema)
    with pytest.raises(RuntimeError, match="合成数据库结构不兼容"):
        client.get("/api/semantic-workspace/storage")
