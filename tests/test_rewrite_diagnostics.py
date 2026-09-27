"""模型失败只公开分类，保留单次调用和未知结果边界。"""
import asyncio
import json
from types import SimpleNamespace

import pytest
import httpx

from src.conversation_steering import RawUserTurn, rewriter
from src.api.routes import semantic_workspace as routes
from tests.test_workspace_conversation_stream import conversation
from tests.test_workspace_draft_chat import draft_api, payload


@pytest.mark.parametrize("content, category", [('{"intent":"private-secret"}', "response_contract_invalid"), ('not-json-private-secret', "response_contract_invalid")])
def test_broker_classifies_without_response_leak(conversation, monkeypatch, content, category):
    _, _, _, calls, _, _, request = conversation
    monkeypatch.setattr(rewriter, "response_text", lambda *args: content)
    turn = RawUserTurn(turn_id="diagnostic", owner_id=request.owner_id, task_id=request.task_id,
                       revision=request.revision, text=request.text)
    with pytest.raises(rewriter.ContextRewriteError) as caught:
        asyncio.run(rewriter.BrokerContextRewriter().rewrite(turn, request))
    assert caught.value.error_code == category
    assert "private-secret" not in str(caught.value)
    assert caught.value.validation_types
    assert len(calls) == 1


def test_user_output_format_is_scoped_to_direct_answer(conversation):
    _, _, _, calls, _, _, request = conversation
    turn = RawUserTurn(turn_id="format-boundary", owner_id=request.owner_id, task_id=request.task_id,
                       revision=request.revision, text="只输出业务JSON对象")
    asyncio.run(rewriter.BrokerContextRewriter().rewrite(turn, request))
    system = calls[0]["messages"][0]["content"]
    assert rewriter._STRUCTURED_BOUNDARY in system
    assert json.loads(calls[0]["messages"][-1]["content"])["user_turn"] == turn.text


def test_public_diagnostic_preserves_detail_and_never_retries(draft_api, monkeypatch):
    client, _, calls = draft_api

    async def fail(*args):
        calls.append("called")
        raise rewriter.ContextRewriteError("response_contract_invalid", validation_types=("enum",))

    def build(request, *, before_call, system_prompt):
        before_call()
        return SimpleNamespace(rewrite=fail)

    monkeypatch.setattr(routes, "build_context_rewriter", build)
    response = client.post("/api/semantic-workspace/draft/turns", json=payload())
    assert response.status_code == 502
    assert response.json()["error_code"] == "response_contract_invalid"
    assert response.json()["validation_types"] == ["enum"]
    assert isinstance(response.json()["detail"], str)
    assert client.post("/api/semantic-workspace/draft/turns", json=payload()).status_code == 409
    assert calls == ["called"]


def test_response_body_disconnect_is_unknown_without_retry(conversation, monkeypatch):
    from src.model_connections.contracts import RelayResponse
    _, _, _, calls, _, _, request = conversation

    async def interrupted(self):
        yield b'{"partial":'
        raise httpx.ReadError("private-provider-detail")

    monkeypatch.setattr(RelayResponse, "iter_bytes", interrupted)
    turn = RawUserTurn(turn_id="stream-error", owner_id=request.owner_id, task_id=request.task_id,
                       revision=request.revision, text=request.text)
    with pytest.raises(rewriter.ContextRewriteError) as caught:
        asyncio.run(rewriter.BrokerContextRewriter().rewrite(turn, request))
    assert caught.value.error_code == "provider_outcome_unknown"
    assert "private-provider-detail" not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.parametrize("wrapper, accepted", [
    ("```json\n%s\n```", True), ("```\n%s\n```", True),
    ("```json\n%s```", True), ("```json %s```", True),
    ("前置指令\n```json\n%s\n```", False),
    ("```json\n%s\n```\n```json\n{}\n```", False),
    ("```json\n%s", False),
])
def test_only_complete_outer_json_fence_is_accepted(conversation, monkeypatch, wrapper, accepted):
    _, _, _, calls, _, _, request = conversation
    draft = {"open_questions": [], "intent": "normalization", "confidence": "high",
             "normalized_text": "讨论", "direct_answer": "```json\n{\"甲\":3}\n```"}
    monkeypatch.setattr(rewriter, "response_text", lambda *args: wrapper % json.dumps(draft))
    turn = RawUserTurn(turn_id="fenced", owner_id=request.owner_id, task_id=request.task_id,
                       revision=request.revision, text=request.text)
    if accepted:
        result = asyncio.run(rewriter.BrokerContextRewriter().rewrite(turn, request))
        assert result.direct_answer == draft["direct_answer"]
        assert result.selection_delta == {}
    else:
        with pytest.raises(rewriter.ContextRewriteError):
            asyncio.run(rewriter.BrokerContextRewriter().rewrite(turn, request))
    assert len(calls) == 1
