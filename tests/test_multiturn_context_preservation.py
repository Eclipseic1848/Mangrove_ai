"""追问不得静默丢掉后续修正或摘要前的约束。"""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from src.conversation_steering import RawUserTurn, SteeringRequest
from src.conversation_steering import rewriter
from src.conductor import context
from tests.test_workspace_conversation_stream import conversation


def history_goal():
    return json.dumps([{"role": "user", "content": "早前资料" * 6000},
                       {"role": "user", "content": "更正：只使用私银资料，不要重新采集"}], ensure_ascii=False)


def test_broker_preserves_full_history_and_latest_correction(conversation):
    _, _, _, calls, _, _, request = conversation
    goal = history_goal()
    request = request.model_copy(update={"current_goal": goal})
    turn = RawUserTurn(turn_id="history-turn", owner_id=request.owner_id,
                      task_id=request.task_id, revision=request.revision, text=request.text)
    asyncio.run(rewriter.BrokerContextRewriter().rewrite(turn, request))
    assert json.loads(calls[-1]["messages"][-1]["content"])["current_goal"] == goal


def test_instructor_preserves_same_history(monkeypatch):
    payloads = []

    def respond(request):
        payloads.append(json.loads(request.content))
        draft = {"open_questions": [], "intent": "status_question", "confidence": "high",
                 "normalized_text": "查询进度", "direct_answer": "保留原范围"}
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 0,
            "model": "synthetic", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps(draft, ensure_ascii=False)}}]})

    original = httpx.AsyncClient

    class IsolatedClient(original):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(rewriter.httpx, "AsyncClient", IsolatedClient)
    connection = SimpleNamespace(provider="local", model="synthetic", base_url="http://127.0.0.1:9/v1",
                                 api_key="synthetic", timeout=2, trust_env=False, extra_body=None)
    monkeypatch.setattr(rewriter, "get_provider", lambda: SimpleNamespace(resolve_model=lambda *a, **kw: connection))
    turn = RawUserTurn(turn_id="turn", owner_id="owner", task_id="task", revision=1, text="继续解释")
    request = SteeringRequest(owner_id="owner", task_id="task", revision=1, text=turn.text,
                              current_status="completed", current_goal=history_goal())
    asyncio.run(rewriter.InstructorContextRewriter(provider="local", model=None).rewrite(turn, request))
    assert json.loads(payloads[-1]["messages"][-1]["content"])["current_goal"] == request.current_goal


def test_oversized_history_is_rejected_without_truncation():
    with pytest.raises(ValidationError):
        SteeringRequest(owner_id="owner", task_id="task", revision=1, text="继续",
                        current_status="completed", current_goal="字" * 120001)


@pytest.mark.parametrize("outcome", ["success", "empty", "error", "cancel"])
def test_history_summary_never_drops_unseen_constraints(monkeypatch, outcome):
    monkeypatch.setattr(context.settings, "context_max_messages", 3)
    monkeypatch.setattr(context.settings, "context_keep_recent", 2)
    messages = [{"role": "user", "content": "资料" * 5000},
                {"role": "user", "content": "不得重新采集，保留来源"},
                {"role": "assistant", "content": "已记录"},
                {"role": "user", "content": "解释上一条"}]
    seen = []

    async def summarize(payload, **kwargs):
        seen.append(payload[-1]["content"])
        if outcome == "error":
            raise RuntimeError("合成失败")
        if outcome == "cancel":
            raise asyncio.CancelledError()
        return "不得重新采集，保留来源" if outcome == "success" else ""

    monkeypatch.setattr(context, "achat", summarize)
    if outcome == "cancel":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(context.compress_history(messages))
    else:
        result = asyncio.run(context.compress_history(messages))
        if outcome == "success":
            assert "不得重新采集，保留来源" in seen[0]
            assert result[-2:] == messages[-2:]
        else:
            assert result == messages
