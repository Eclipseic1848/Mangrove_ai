"""恢复资格只说明原任务的真实条件，不把来源验证写成平台登录。"""
import asyncio

import pytest
from fastapi import HTTPException

from src.api.routes import chat


def frozen_recovery(source, reason=None):
    group = {"done": False, "batches": []}
    if reason:
        group.update(done=True, stop_reason="collection_failed", discovery_inflight=False, batches=[{
            "phase": "discovery", "returned_count": 0, "attempted": 1,
            "coverage": {"failure": {"reason": reason, "rejection_confirmed": True}},
        }])
    return {"state": {"session_id": "conversation"},
            "saved": {"credential_source": source, "groups": [group]}}


@pytest.mark.parametrize("source,reason,account,action", [
    ("platform", "challenge_required", "平台统一的小红书采集账号", "complete_platform_challenge"),
    ("personal", "login_required", "你配置的小红书采集账号", "reauthenticate_selected_account"),
])
def test_recovery_status_identifies_frozen_source_account(monkeypatch, source, reason, account, action):
    calls = []

    def load(user, task_id):
        calls.append((user, task_id))
        return frozen_recovery(source, reason)

    monkeypatch.setattr(chat, "_load_owned_recovery", load)
    result = asyncio.run(chat.collection_recovery_status("task", {"user_id": "owner"}))
    assert calls == [({"user_id": "owner"}, "task")]
    assert result["credential_source"] == source and result["reason"] == reason
    assert result["next_action"] == action and account in result["message"]
    if source == "platform":
        assert "管理员" in result["message"]
    else:
        assert "管理员" not in result["message"]
    assert "当前账号" not in result["message"]


def test_unfinished_processing_does_not_require_source_login(monkeypatch):
    monkeypatch.setattr(chat, "_load_owned_recovery", lambda *_: frozen_recovery("platform"))
    result = asyncio.run(chat.collection_recovery_status("task", {"user_id": "owner"}))
    assert result["reason"] == "unfinished_steps"
    assert result["next_action"] == "resume_unfinished_steps"
    assert "验证" not in result["message"] and "登录" not in result["message"]


@pytest.mark.parametrize("status", [404, 409])
def test_status_preserves_owned_recovery_rejection(monkeypatch, status):
    rejection = HTTPException(status, "受控的恢复拒绝")

    def reject(*_):
        raise rejection

    monkeypatch.setattr(chat, "_load_owned_recovery", reject)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(chat.collection_recovery_status("task", {"user_id": "owner"}))
    assert caught.value is rejection
