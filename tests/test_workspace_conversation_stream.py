# -*- coding: utf-8 -*-
"""完整公开消息快照和原生用量必须保持持久身份。"""
import asyncio
import json
import sqlite3

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from src.account_execution import ExecutionAuthorization, execution_context
from src.api import auth
from src.api.routes import semantic_workspace as routes
from src.config.settings import settings
from src.agentic_runtime.models import RuntimeTaskConfig, RuntimeVersion
from src.agentic_runtime.repository import AgenticRuntimeRepository
from src.conversation_steering import ConversationSteering, SqliteSteeringRepository, SteeringRequest
from src.conversation_steering import rewriter
from src.model_connections import ConnectionBroker
from src.model_connections.storage import ModelConnectionRepository
from src.model_connections.vault import FernetCredentialVault
from tests.database_migration_helpers import migrated_webui_database
from tests.account_execution_helpers import seed_execution_owner
from src.conversation_steering.models import SteeringAction, SteeringResult
from src.api.routes.semantic_workspace import _public_steering_message
from src.work_trace import WorkTraceProjection


def test_message_keeps_markdown_identity_and_filters_internal_content():
    result = SteeringResult(
        result_id="result-1", owner_id="owner-1", task_id="task-1",
        turn_id="turn-1", delta_id="delta-1", action=SteeringAction.ANSWER_ONLY,
        acknowledgement="已回答", answer="中文\n\n```python\nprint('好')",
        run_id="run-1", revision=1,
    )
    message = _public_steering_message(result)
    assert message["content"] == result.answer
    assert message["message_id"] == result.result_id
    assert message["version"] == 1
    assert message["revision"] == 1
    assert message["run_id"] == "run-1"
    assert _public_steering_message(result.model_copy(update={"answer": None})) is None
    private = result.model_copy(update={"answer": "<think>内部判断</think> cookie=fixture-secret C:\\private\\data.csv"})
    assert "fixture-secret" not in str(_public_steering_message(private))
    assert "内部判断" not in str(_public_steering_message(private))
    assert "private" not in str(_public_steering_message(private))
    assert "fixture-secret" not in str(_public_steering_message(result.model_copy(update={"answer": '{"api_key": "fixture-secret"}'})))


def test_native_dimensions_are_unknown_independently_of_total():
    def usage(rows):
        return WorkTraceProjection().project(
            task_id="task-1", revision=1, run_id="run-1", status="running",
            events=(), provider_usage=[{"run_id": "run-1", "request_count": 1, **row} for row in rows],
        ).usage
    partial = usage([{"input_tokens": 7, "output_tokens": None, "total_tokens": 9}])
    assert partial.input_tokens == 7
    assert partial.output_tokens is None
    assert partial.total_tokens == 9
    native = usage([{"input_tokens": 7, "output_tokens": 2, "total_tokens": None}])
    assert native.input_tokens == 7
    assert native.output_tokens == 2
    assert native.total_tokens == 0
    assert native.unknown_call_count == 1
    mixed = usage([{"input_tokens": 7, "total_tokens": 9}, {"total_tokens": None}])
    assert mixed.input_tokens is None


@pytest.mark.parametrize("text", [
    "https://example.com/report?view=full#result",
    "http://example.com/report",
    "[参考资料](https://example.com/reports/2026)",
    "[文件名称](https://example.com/C:/report)",
])
def test_public_answer_preserves_http_links_in_all_message_projections(text):
    result = SteeringResult(
        result_id="result-link", owner_id="owner-1", task_id="task-1",
        turn_id="turn-link", delta_id="delta-link", action=SteeringAction.ANSWER_ONLY,
        acknowledgement="已回答", answer=text, revision=1,
    )
    assert routes._public_steering_payload(result.model_dump(mode="json"))["answer"] == text
    assert routes._public_steering_message(result)["content"] == text


@pytest.mark.parametrize("path", [r"C:\private\report.csv", "C:/private/report.csv", r"\\server\private\report.csv", "/private/report.csv", "file:///C:/private/report.csv"])
def test_public_answer_still_hides_host_paths(path):
    assert routes._public_answer_text(f"位置：{path}") == "位置：[路径已隐藏]"


@pytest.fixture
def conversation(tmp_path, monkeypatch):
    database = migrated_webui_database(tmp_path / "conversation.db")
    for owner in ("user-a", "user-b"):
        seed_execution_owner(database, owner)
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    monkeypatch.setattr(auth, "_store", None)
    calls = []
    draft = {"intent": "status_question", "confidence": "high", "normalized_text": "查询进度", "direct_answer": "中文答复\n\n```python\nprint('好')"}

    async def provider(request):
        body = json.loads(request.content)
        is_rewrite = "JSON Schema" in str(body)
        if is_rewrite:
            calls.append(body)
            await asyncio.sleep(0.02)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(draft, ensure_ascii=False) if is_rewrite else "OK"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9,
                      "prompt_tokens_details": {"cached_tokens": 3}},
        })

    repository = ModelConnectionRepository(str(database))
    broker = ConnectionBroker(repository=repository, vault=FernetCredentialVault.generate(),
                              transport=httpx.MockTransport(provider), resolver=lambda _host: ["8.8.8.8"])
    monkeypatch.setattr(rewriter, "get_default_broker", lambda: broker)
    monkeypatch.setattr(routes, "get_default_broker", lambda: broker)
    monkeypatch.setattr(rewriter, "get_provider", lambda: pytest.fail("禁止回退全局模型"))
    monkeypatch.setattr(routes, "platform_session_valid", lambda _request: True)
    with execution_context(ExecutionAuthorization("user-a", 0)):
        connection = asyncio.run(broker.configure_personal(
            owner_user_id="user-a", preset_id="deepseek", model="deepseek-v4-pro", api_key="fixture-key-123",
        ))
        binding = broker.freeze_connection("user-a", connection["connection_id"])
        store = auth.get_store()
        store.create_semantic_workspace_task("user-a", task_id="task-1", title="合成任务", objective_text="检查资料",
            upload_ids=[], output_formats=["json"], provider="local", model="wrong-global-model", external_api_confirmed=True)
        store.update_semantic_workspace_task("user-a", "task-1", status="running", run_id="harness-run")
        AgenticRuntimeRepository(str(database)).register(RuntimeTaskConfig(
            user_id="user-a", task_id="task-1", revision=1, run_id="runtime-run", runtime_version=RuntimeVersion.PI,
            model_connection_id=binding.connection_id, model_connection_version=binding.connection_version,
            model_connection_model=binding.model, external_api_confirmed=True,
        ))
        app = FastAPI()
        app.include_router(routes.router)
        user = {"user_id": "user-a", "role": "user", "execution_generation": 0}
        app.dependency_overrides[auth.get_current_user] = lambda: user
        request = SteeringRequest(owner_id="user-a", task_id="task-1", revision=1, run_id="runtime-run",
            text="当前进度？", idempotency_key="turn-key", current_status="running", provider="local",
            model=binding.model, model_connection_id=binding.connection_id,
            model_connection_version=binding.connection_version, external_api_confirmed=True)
        yield database, store, broker, calls, app, user, request


def test_byok_http_answer_stream_replays_same_revision_without_reexecution(conversation, monkeypatch):
    database, store, broker, calls, app, user, _ = conversation
    with TestClient(app) as client:
        url = "/api/semantic-workspace/tasks/task-1"
        posted = client.post(url + "/turns", headers={"Idempotency-Key": "http-turn"}, json={"text": "当前进度？"})
        assert posted.status_code == 200, posted.text
        assert posted.json()["run_id"] == "runtime-run"
        assert client.post(url + "/turns", headers={"Idempotency-Key": "http-turn"}, json={"text": "当前进度？"}).json() == posted.json()
        assert len(calls) == 1
        assert calls[0]["model"] == "deepseek-v4-pro"
        store.update_semantic_workspace_task("user-a", "task-1", status="cancelled")
        detail = client.get(url).json()
        message = detail["messages"][0]
        assert message["content"] == posted.json()["answer"]
        assert detail["work_session"]["usage"]["total_tokens"] == 9
        assert detail["work_session"]["usage"]["cache_tokens"] == 3
        assert detail["work_session"]["provider_usage"][0]["purpose"] == "context_rewrite"
        first = client.get(url + "/stream?revision=1").text
        # 新 Store 模拟服务重新读取持久事实，不能再请求 Provider。
        monkeypatch.setattr(auth, "_store", None)
        replay = client.get(url + "/stream?revision=1", headers={"Last-Event-ID": message["message_id"]}).text
        assert first == replay
        assert first.count("event: message") == 1
        assert "中文答复" in first
        assert '"run_id": "runtime-run"' in first
        assert len(calls) == 1
        user["user_id"] = "user-b"
        assert client.get(url + "/stream?revision=1").status_code == 404
        assert client.get(url).status_code == 404


def test_parallel_same_turn_uses_one_persisted_grant_and_one_provider_request(conversation):
    database, _, broker, calls, _, _, request = conversation

    async def scenario():
        service = ConversationSteering(SqliteSteeringRepository(database), rewriter.BrokerContextRewriter())
        outcomes = await asyncio.gather(service.handle_turn(request), service.handle_turn(request), return_exceptions=True)
        assert sum(isinstance(value, SteeringResult) for value in outcomes) == 1
        assert sum(isinstance(value, ValueError) for value in outcomes) == 1
        saved = await ConversationSteering(SqliteSteeringRepository(database), rewriter.BrokerContextRewriter()).handle_turn(request)
        assert saved.answer
        assert len(calls) == 1
    asyncio.run(scenario())
    assert len(broker.list_usage("user-a", task_id="task-1", revision=1)) == 1


def test_different_turns_do_not_collide_and_connection_mismatch_never_falls_back(conversation):
    database, _, broker, calls, _, _, request = conversation
    async def scenario():
        service = ConversationSteering(SqliteSteeringRepository(database), rewriter.BrokerContextRewriter())
        first = await service.handle_turn(request)
        second = await service.handle_turn(request.model_copy(update={"idempotency_key": "another-turn"}))
        assert first.turn_id != second.turn_id
        assert len(calls) == 2
        with pytest.raises(Exception, match="连接版本已变化"):
            await service.handle_turn(request.model_copy(update={"idempotency_key": "stale", "model_connection_version": "stale"}))
        with execution_context(ExecutionAuthorization("user-b", 0)):
            with pytest.raises(Exception, match="无权访问"):
                await service.handle_turn(request.model_copy(update={"owner_id": "user-b"}))
        assert len(calls) == 2
    asyncio.run(scenario())
    assert len(broker.list_usage("user-a", task_id="task-1", revision=1)) == 2


def test_unrelated_integrity_failure_is_not_mislabeled_as_duplicate(conversation, monkeypatch):
    database, _, broker, _, _, _, request = conversation
    def corrupt(**_kwargs):
        raise sqlite3.IntegrityError("NOT NULL constraint failed: another_table.value")
    monkeypatch.setattr(broker, "issue_grant", corrupt)
    with pytest.raises(sqlite3.IntegrityError, match="another_table"):
        asyncio.run(ConversationSteering(SqliteSteeringRepository(database), rewriter.BrokerContextRewriter()).handle_turn(request))


def test_grant_survives_crash_before_response_and_projects_unknown_without_resend(conversation, monkeypatch):
    database, _, broker, calls, _, _, request = conversation
    async def crash(**_kwargs):
        raise asyncio.CancelledError()
    monkeypatch.setattr(broker, "relay", crash)
    service = ConversationSteering(SqliteSteeringRepository(database), rewriter.BrokerContextRewriter())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.handle_turn(request))
    # 即使没有 response/finalize，网络前持久占位仍禁止跨实例重发。
    with pytest.raises(ValueError, match="禁止自动重复"):
        asyncio.run(ConversationSteering(SqliteSteeringRepository(database), rewriter.BrokerContextRewriter()).handle_turn(request))
    usage = broker.list_usage("user-a", task_id="task-1", revision=1, include_identity=True)
    assert len(usage) == 1
    assert usage[0]["status"] == "unknown"
    assert usage[0]["total_tokens"] is None
    assert usage[0]["run_id"] == "runtime-run"
    assert calls == []
    assert broker.list_usage("user-b", task_id="task-1", revision=1) == []


def test_old_revision_subscription_cannot_receive_late_new_revision_state(conversation):
    database, store, _, _, _, user, request = conversation
    async def scenario():
        response = routes.stream_task("task-1", Request({"type": "http", "headers": []}), revision=1, user=user)
        iterator = response.body_iterator
        first = await anext(iterator)
        assert json.loads(first["data"])["revision"] == 1
        store.create_semantic_workspace_revision("user-a", "task-1", objective_text="新修订", output_formats=["json"], change_summary="明确修改")
        # 旧模型回复在新修订创建后返回，仍保存为 revision 1。
        await ConversationSteering(SqliteSteeringRepository(database), rewriter.BrokerContextRewriter()).handle_turn(request)
        rest = [event async for event in iterator]
        messages = [json.loads(event["data"]) for event in rest if event["event"] == "message"]
        assert len(messages) == 1 and messages[0]["revision"] == 1
        assert all(json.loads(event["data"]).get("revision") == 1 for event in rest)
        assert json.loads(rest[-1]["data"])["revision"] == 1
        assert routes._steering_messages("user-a", "task-1", 2) == []
    asyncio.run(scenario())
