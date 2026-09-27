"""模板入口只投影当前 Owner 的待确认动作，不复活已处理或过期动作。"""
from contextlib import contextmanager
import asyncio
import pytest
from src.account_execution import ExecutionAuthorization, execution_context
from src.api.execution import execution_validation
from src.api import auth, session_store
from src.api.routes import chat, confirm, conversations
from src.conductor.task_spec import TaskSpec
from tests.test_workspace_draft_chat import draft_api


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_template_action_is_visible_consumed_and_owner_scoped(draft_api, monkeypatch, bound, failure):
    client, user, _ = draft_api
    user["role"] = "user"
    client.app.include_router(conversations.router)
    client.app.include_router(confirm.router)
    pending = session_store.PendingStore()
    for module in (session_store, chat, confirm):
        monkeypatch.setattr(module, "pending_store", pending)
    store = auth.get_store()
    conv_id = store.create_conversation(user["user_id"], "模板测试")["conv_id"]
    state = {"task_id": "template-task", "task_spec": TaskSpec(intent="报告模板"),
             "analysis": "报告正文", "outputs": {"template_suggest": True}}
    binding = {"model_connection_id": "connection-a", "model_connection_version": 3} if bound else {}
    with execution_context(ExecutionAuthorization(user["user_id"], 0)), execution_validation(lambda _: None):
        chat._build_result(user["user_id"], conv_id, state, "沉淀为模板", "bound" if bound else "local", "test", "报告", **binding)
    store.add_message(conv_id, "assistant", "沉淀为模板", task_id="template-task", meta={"token_usage": {"calls": 1}})
    active = []

    @contextmanager
    def model_context(**kwargs):
        assert kwargs["owner_id"] == "owner-a"
        assert kwargs["connection_id"] == "connection-a"
        assert kwargs["connection_version"] == 3
        active.append(True)
        try:
            yield
        finally:
            active.clear()

    monkeypatch.setattr("src.model_connections.conductor.conductor_connection", model_context)

    async def distill(*args, **kwargs):
        assert bool(active) == bound
        if failure:
            raise RuntimeError("合成模型失败")
        return {"title": "报告结构", "keywords": [], "body": "结构"}

    async def save(**kwargs):
        assert bool(active) == bound
        assert kwargs["owner_id"] == "owner-a"
        assert kwargs["local_dedup"] is True
        return "report-template"

    monkeypatch.setattr(confirm, "distill_template", distill)
    monkeypatch.setattr(confirm, "save_template", save)
    url = f"/api/conversations/{conv_id}/messages"
    assert client.get(url).json()[0]["meta"]["template_available"] is True
    user["user_id"] = "owner-b"
    assert client.get(url).status_code == 404
    assert client.post("/api/confirm/template", json={"task_id": "template-task"}).status_code == 404
    user["user_id"] = "owner-a"
    assert client.post("/api/confirm/template", json={"task_id": "template-task"}).status_code == (500 if failure else 200)
    assert client.get(url).json()[0]["meta"]["template_available"] is False
    assert client.post("/api/confirm/template", json={"task_id": "template-task"}).status_code == 404


def test_old_bound_action_without_connection_cannot_fall_back(draft_api, monkeypatch):
    client, user, _ = draft_api
    client.app.include_router(confirm.router)
    pending = session_store.PendingStore()
    monkeypatch.setattr(confirm, "pending_store", pending)
    with execution_context(ExecutionAuthorization(user["user_id"], 0)), execution_validation(lambda _: None):
        pending.put(user["user_id"], "old-bound", {"template": {"provider": "bound", "analysis": "旧报告"}})

    async def forbidden(*args, **kwargs):
        raise AssertionError("身份缺失时不得调用默认模型")

    monkeypatch.setattr(confirm, "distill_template", forbidden)
    result = client.post("/api/confirm/template", json={"task_id": "old-bound"})
    assert result.status_code == 409
    assert "连接身份缺失" in result.json()["detail"]
    assert client.post("/api/confirm/template", json={"task_id": "old-bound"}).status_code == 404


@pytest.mark.parametrize("failed", [False, True])
def test_template_save_uses_local_dedup_and_checks_failure_before_writing(tmp_path, monkeypatch, failed):
    from src.memory import templates
    from src.llm.provider import _bound_chat_failure
    monkeypatch.setattr(templates, "TEMPLATES_DIR", tmp_path)
    candidate = {"slug": "existing", "title": "旧结构", "keywords": ["旧"], "body": "旧正文",
                 "uses": 0, "quality_avg": 0, "status": "draft"}
    monkeypatch.setattr(templates, "_semantic_candidates", lambda *a, **kw: [candidate])
    monkeypatch.setattr(templates, "_fallback_decision", lambda *a, **kw: {"decision": "new"})
    failures = []

    async def model(messages, **kwargs):
        if not failed:
            raise AssertionError("本地去重不能调用辅助模型")
        else:
            failures.append("合成绑定模型失败")
            raise RuntimeError(failures[0])

    monkeypatch.setattr(templates, "achat", model)
    if not failed:
        def forbidden(*args, **kwargs):
            raise AssertionError("本地去重不能进入向量或重排链路")
        monkeypatch.setattr(templates, "_semantic_candidates", forbidden)
        monkeypatch.setattr(templates, "find_duplicate_semantic", forbidden)
    token = _bound_chat_failure.set(failures)
    try:
        args = {} if failed else {"local_dedup": True}
        call = templates.save_template("新结构", "generic", ["新"], "新正文", owner_id="owner-a", **args)
        if failed:
            with pytest.raises(ValueError, match="合成绑定模型失败"):
                asyncio.run(call)
            assert not list(tmp_path.glob("*.md"))
        else:
            slug = asyncio.run(call)
            assert slug
            assert asyncio.run(templates.save_template("新结构", "generic", ["新"], "新正文", owner_id="owner-a", local_dedup=True)) == slug
            assert len(list(tmp_path.glob("*.md"))) == 1
    finally:
        _bound_chat_failure.reset(token)
