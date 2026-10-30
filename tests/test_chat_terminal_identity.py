"""聊天流、持久回复和只读运行状态必须绑定同一轮，防止旧回复收口新任务。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from src.api.routes import chat, conversations
from tests.test_workspace_draft_chat import draft_api


@pytest.fixture
def terminal_api(draft_api, monkeypatch):
    client, user, calls = draft_api
    client.app.include_router(chat.router)
    client.app.include_router(conversations.router)
    monkeypatch.setattr(chat, "platform_session_valid", lambda _request: True)

    async def unchanged_history(history, **_kwargs):
        return history

    monkeypatch.setattr(chat, "compress_history", unchanged_history)
    return client, user, calls


def stream_events(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = {}
    for block in response.text.replace("\r\n", "\n").split("\n\n"):
        lines = block.splitlines()
        event = next((line[7:] for line in lines if line.startswith("event: ")), None)
        data = next((line[6:] for line in lines if line.startswith("data: ")), None)
        if event and data is not None:
            events.setdefault(event, []).append(json.loads(data))
    return events


@pytest.mark.parametrize("outcome", ["normal", "error", "cancelled"])
def test_stream_and_saved_terminal_share_current_run_identity(terminal_api, monkeypatch, outcome):
    client, _user, model_calls = terminal_api
    entered = []

    async def synthetic_stream(**kwargs):
        entered.append(kwargs["user_input"])
        if outcome == "error":
            raise RuntimeError("纯合成流水线失败")
        if outcome == "cancelled":
            raise asyncio.CancelledError()
        yield "final", {"needs_clarification": True, "clarification_question": "请补充合成要求"}

    monkeypatch.setattr(chat, "astream_conductor", synthetic_stream)
    conv_id = None
    identities = []
    rounds = 2 if outcome == "normal" else 1
    for turn in range(rounds):
        events = stream_events(client.post("/api/chat/stream", json={
            "content": f"纯合成要求{turn}", "conv_id": conv_id,
            "mode": "legacy_analysis", "provider": "synthetic", "model": "synthetic",
        }))
        meta = events["meta"][0]
        conv_id = meta["conv_id"]
        messages = client.get(f"/api/conversations/{conv_id}/messages")
        assert messages.status_code == 200, messages.text
        terminal = messages.json()[-1]
        assert terminal["role"] == "assistant"
        identity = meta.get("chat_run_id")
        assert isinstance(identity, str) and identity, meta
        assert terminal["meta"]["chat_run_id"] == identity
        assert terminal["meta"]["kind"] == {"normal": "clarification", "error": "error", "cancelled": "cancelled"}[outcome]
        assert events["done"] == [{}]
        status = client.get(f"/api/chat/running/{conv_id}")
        assert status.status_code == 200
        assert status.json()["running"] is False
        assert "chat_run_id" in status.json() and status.json()["chat_run_id"] is None
        identities.append(identity)
    assert len(set(identities)) == rounds
    assert len(entered) == rounds and not model_calls


def test_running_identity_matches_live_stream_and_remains_owner_scoped(terminal_api, monkeypatch):
    client, user, model_calls = terminal_api
    entered, release = threading.Event(), threading.Event()

    async def synthetic_stream(**_kwargs):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.005)
        yield "final", {"needs_clarification": True, "clarification_question": "合成执行已结束"}

    monkeypatch.setattr(chat, "astream_conductor", synthetic_stream)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, "/api/chat/stream", json={
            "content": "只用于隔离运行身份验证", "mode": "legacy_analysis",
            "provider": "synthetic", "model": "synthetic",
        })
        try:
            assert entered.wait(3), "合成流水线未进入"
            listed = client.get("/api/conversations")
            assert listed.status_code == 200 and len(listed.json()) == 1
            conv_id = listed.json()[0]["conv_id"]
            running = client.get(f"/api/chat/running/{conv_id}")
            assert running.status_code == 200
            assert running.json()["running"] is True
            identity = running.json().get("chat_run_id")
            assert isinstance(identity, str) and identity, running.json()
            user["user_id"] = "owner-b"
            assert client.get(f"/api/chat/running/{conv_id}").status_code == 404
            assert client.get(f"/api/conversations/{conv_id}/messages").status_code == 404
        finally:
            user["user_id"] = "owner-a"
            release.set()
        events = stream_events(pending.result(timeout=5))
    assert events["meta"][0]["chat_run_id"] == identity
    messages = client.get(f"/api/conversations/{conv_id}/messages").json()
    assert messages[-1]["meta"]["chat_run_id"] == identity
    completed = client.get(f"/api/chat/running/{conv_id}").json()
    assert completed["running"] is False and completed["chat_run_id"] is None
    assert not model_calls
