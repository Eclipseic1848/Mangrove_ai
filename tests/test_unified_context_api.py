"""同一模板与记忆经过任务前对话的公开接口使用，模型为隔离替身。"""
from src.api import auth
from src.api.routes import conversations, memory_routes
from src.memory import loader
from src.task_context import TaskContextRepository, TaskTemplateDraft
from tests.test_workspace_draft_chat import draft_api, payload


def test_chat_uses_reviewed_template_and_persists_frozen_owner_context(draft_api, tmp_path, monkeypatch):
    client, user, calls = draft_api
    monkeypatch.setattr(loader, "MEMORY_DIR", tmp_path / "preferences")
    client.app.include_router(memory_routes.router)
    client.app.include_router(conversations.router)
    repository = TaskContextRepository(auth.get_store().db_path)
    repository.save_template("owner-a", TaskTemplateDraft(
        template_id="weekly-report", version=1, title="周报", source="owner_created",
        purpose="general", goal_contract_draft="保留当前要求",
        delivery_spec_draft={"formats": ["markdown"]}, method_draft="先写本周成果，再列下周安排",
    ))
    memory = client.post("/api/memory/self", json={"text": "周报中金额保留两位小数"}).json()["item"]
    selection = {"template": {"template_id": "weekly-report", "version": 1}, "memories": [{"memory_id": memory["id"]}]}
    preview = client.post("/api/semantic-workspace/context-preview", json={
        "purpose": "general", "objective_text": "帮我讨论周报结构", "output_formats": ["markdown"], "selection": selection,
    }).json()
    request = payload(text="帮我讨论周报结构", context_purpose="general", context_selection=selection,
        context_preview_sha256=preview["preview_sha256"], output_formats=["markdown"])
    response = client.post("/api/semantic-workspace/draft/turns", json=request)
    assert response.status_code == 200, response.text
    sent = calls[-1][1].memory_context
    assert "先写本周成果，再列下周安排" in sent
    assert "金额保留两位小数" in sent
    messages = client.get(f"/api/conversations/{response.json()['conv_id']}/messages").json()
    frozen = messages[-1]["meta"]["task_context"]
    assert frozen["owner_id"] == "owner-a"
    assert frozen["template"]["version"] == 1
    assert client.delete(f"/api/memory/self/{memory['id']}").status_code == 200
    assert client.get(f"/api/conversations/{response.json()['conv_id']}/messages").json()[-1]["meta"]["task_context"] == frozen
    # 无权限或内容漂移在占位之前拒绝，同一标识可以纠正后重试。
    user["user_id"] = "owner-b"
    denied = client.post("/api/semantic-workspace/draft/turns", json={**request, "request_id": "context-denied-0002"})
    assert denied.status_code == 404, denied.text
    assert len(calls) == 1


def test_legacy_workbench_compiler_receives_the_same_frozen_context(tmp_path, monkeypatch):
    from tests.test_semantic_workspace_api import _client, _upload, _wait
    from tests.test_semantic_plan_api import ApiFakeGenerator

    requests = []

    class Generator(ApiFakeGenerator):
        async def generate(self, request, **kwargs):
            requests.append(request)
            return await super().generate(request, **kwargs)

    monkeypatch.setattr(loader, "MEMORY_DIR", tmp_path / "preferences")
    client, _ = _client(tmp_path, monkeypatch, generator=Generator())
    client.app.include_router(memory_routes.router)
    upload = _upload(tmp_path)
    with client:
        client.post("/api/memory/self", json={"text": "费用明细保留原值，不补猜金额"})
        response = client.post("/api/semantic-workspace/tasks", json={
            "objective_text": "只提取谢超群的费用明细，只保留核销工作量天数和工作量费用",
            "upload_ids": [upload.upload_id], "output_formats": ["xlsx"], "runtime_version": "legacy",
        })
        assert response.status_code == 202, response.text
        task = _wait(client, response.json()["task_id"], {"completed", "failed"})
        assert requests
        context = getattr(requests[0], "compiled_context", None)
        assert context is not None
        assert "不补猜金额" in context.content
        assert context.owner_id == "user-a"
        assert context.task_id == task["task_id"]
        assert task["task_context"]["compiled_context"] == context.model_dump(mode="json")


def test_explicit_context_does_not_silently_omit_selected_memories(draft_api, tmp_path, monkeypatch):
    client, _, _ = draft_api
    monkeypatch.setattr(loader, "MEMORY_DIR", tmp_path / "preferences")
    client.app.include_router(memory_routes.router)
    memories = [client.post("/api/memory/self", json={"text": "费用说明" * 990 + str(index)}).json()["item"]["id"] for index in range(3)]
    response = client.post("/api/semantic-workspace/context-preview", json={
        "purpose": "general", "objective_text": "汇总费用", "output_formats": ["markdown"],
        "selection": {"memories": [{"memory_id": identity} for identity in memories]},
    })
    assert response.status_code == 422, response.text
    assert "精简" in response.json()["detail"]


def test_chat_preserves_an_accepted_long_current_request(draft_api, tmp_path, monkeypatch):
    client, _, calls = draft_api
    monkeypatch.setattr(loader, "MEMORY_DIR", tmp_path / "preferences")
    text = "请解释以下网页资料：" + "合成资料" * 1800
    response = client.post("/api/semantic-workspace/draft/turns", json=payload(text=text))
    assert response.status_code == 200, response.text
    assert calls[-1][1].text == text


def test_expanded_context_rejection_does_not_claim_creation_key(tmp_path, monkeypatch):
    from tests.test_semantic_workspace_api import _client, _upload
    from src.task_context import TaskContextService

    monkeypatch.setattr(loader, "MEMORY_DIR", tmp_path / "preferences")
    client, _ = _client(tmp_path, monkeypatch)
    upload = _upload(tmp_path)
    body = {
        "objective_text": "只提取谢超群的费用明细，只保留核销工作量天数和工作量费用",
        "upload_ids": [upload.upload_id], "output_formats": ["xlsx"], "runtime_version": "legacy",
    }
    original = TaskContextService.automatic_preview
    def too_long(*args, **kwargs):
        raise ValueError("上下文过长，请精简目标或记忆")
    with client:
        monkeypatch.setattr(TaskContextService, "automatic_preview", too_long)
        rejected = client.post("/api/semantic-workspace/tasks", json=body, headers={"Idempotency-Key": "context-correctable"})
        assert rejected.status_code == 422, rejected.text
        assert "精简" in rejected.json()["detail"]
        monkeypatch.setattr(TaskContextService, "automatic_preview", original)
        accepted = client.post("/api/semantic-workspace/tasks", json=body, headers={"Idempotency-Key": "context-correctable"})
        assert accepted.status_code == 202, accepted.text
