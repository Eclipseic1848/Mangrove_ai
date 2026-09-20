"""新本地任务仅沿冻结端点学习，验证全部使用模拟网络。"""
import json
import time
import sqlite3

import httpx
import pytest

from src.config.settings import settings
from src.memory import templates
from tests.test_pi_runtime_workspace_api import FakePiRuntime, _client, _uploads, _wait_for_delivery


@pytest.mark.parametrize("outcome", ["ok", "changed_settings", "historical", "failed", "unknown_usage", "usage_write_interrupted"])
def test_new_local_task_learns_with_frozen_model_and_persists_usage(tmp_path, monkeypatch, outcome):
    class LocalRuntime(FakePiRuntime):
        async def start(self, request, **kwargs):
            if outcome == "changed_settings":
                monkeypatch.setattr(settings, "llm_base_url", "http://127.0.0.1:18888/v1")
                monkeypatch.setattr(settings, "llm_model_name", "wrong-current-model")
            if outcome == "historical":
                # 隔离历史夹具去掉新版标记，不修改真实历史任务。
                with sqlite3.connect(settings.webui_db_path) as database:
                    database.execute("UPDATE semantic_workspace_events SET details_json='{}' WHERE user_id=? AND task_id=? AND event_type='task_created'",
                        (request.user_id, request.task_id))
            return await super().start(request, **kwargs)

    client = _client(tmp_path, monkeypatch, role="admin", pi_runtime=LocalRuntime())
    if outcome == "usage_write_interrupted":
        from src.api.store import WebUIStore
        append = WebUIStore.append_semantic_workspace_event
        def fail_finished(self, *args, **kwargs):
            if kwargs.get("details", {}).get("learning_usage_state") == "finished":
                raise OSError("模拟请求完成后计量写入中断")
            return append(self, *args, **kwargs)
        monkeypatch.setattr(WebUIStore, "append_semantic_workspace_event", fail_finished)
    monkeypatch.setattr(settings, "llm_base_url", "http://127.0.0.1:19999/v1")
    monkeypatch.setattr(settings, "llm_model_name", "frozen-local")
    calls = []

    def provider(request):
        calls.append((str(request.url), json.loads(request.content)))
        if outcome == "failed":
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "title": "费用核对", "keywords": ["费用"], "body": "逐项核对来源金额，不猜测缺失值。"}, ensure_ascii=False)}}],
            "usage": {} if outcome == "unknown_usage" else {"prompt_tokens": 17, "completion_tokens": 9, "total_tokens": 26}})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        **{**kwargs, "transport": httpx.MockTransport(provider)}))
    document, _ = _uploads(tmp_path)
    with client:
        response = client.post("/api/semantic-workspace/tasks", json={"objective_text": "汇总费用",
            "upload_ids": [document], "output_formats": ["json"], "provider": "local", "runtime_version": "pi"})
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        _wait_for_delivery(client, task_id)
        deadline = time.monotonic() + 5
        while not templates.load_templates(owner_id="user-a") and time.monotonic() < deadline:
            time.sleep(0.05)
        if outcome == "historical":
            assert calls == [] and templates.load_templates(owner_id="user-a") == []
            return
        assert len(calls) == 1
        assert calls[0][0] == "http://127.0.0.1:19999/v1/chat/completions"
        assert calls[0][1]["model"] == "frozen-local"
        detail = client.get(f"/api/semantic-workspace/tasks/{task_id}").json()
        if outcome in {"failed", "usage_write_interrupted"}:
            assert templates.load_templates(owner_id="user-a") == []
            assert detail["status"] == "completed"
        else:
            assert templates.load_templates(owner_id="user-a")[0]["status"] == "draft"
        usage = [event["details"] for event in detail["events"] if event.get("details", {}).get("purpose") == "方法学习"]
        assert usage
        assert usage[-1]["total_tokens"] == (None if outcome in {"failed", "unknown_usage", "usage_write_interrupted"} else 26)
        assert "方法学习" in json.dumps(detail["work_session"], ensure_ascii=False)
        assert detail["work_session"]["usage"]["call_count"] == 2
        assert detail["work_session"]["usage"]["unknown_call_count"] == (
            2 if outcome in {"failed", "unknown_usage", "usage_write_interrupted"} else 1)
        for _ in range(3):
            assert client.get(f"/api/semantic-workspace/tasks/{task_id}").status_code == 200
        assert len(calls) == 1
        assert templates.load_templates(owner_id="user-b") == []


@pytest.mark.parametrize("outcome", ["business", "inconclusive", "infrastructure", "historical"])
def test_local_failed_task_only_learns_verified_business_failure(tmp_path, monkeypatch, outcome):
    from src.agentic_runtime.models import VerificationReport, VerificationStatus, VerificationCheck
    from src.memory import lessons
    from tests.test_pi_runtime_workspace_api import _wait_for_status

    class FailedRuntime(FakePiRuntime):
        async def start(self, request, **kwargs):
            if outcome == "historical":
                with sqlite3.connect(settings.webui_db_path) as database:
                    database.execute("UPDATE semantic_workspace_events SET details_json='{}' WHERE user_id=? AND task_id=? AND event_type='task_created'",
                        (request.user_id, request.task_id))
            return await super().start(request, **kwargs)

        def _verification_report(self):
            return VerificationReport(status=VerificationStatus.INCONCLUSIVE if outcome == "inconclusive" else VerificationStatus.FAILED,
                summary="候选未满足目标", evidence_count=1, checks=(VerificationCheck(
                    code="source_grounding" if outcome == "infrastructure" else "semantic_goal",
                    passed=False, summary="费用汇总遗漏字段"),))

    client = _client(tmp_path, monkeypatch, role="admin", pi_runtime=FailedRuntime())
    calls = []
    def provider(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "title": "费用字段遗漏", "keywords": ["费用"], "body": "核对用户要求字段，避免遗漏。"}, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        **{**kwargs, "transport": httpx.MockTransport(provider)}))
    document, _ = _uploads(tmp_path)
    with client:
        response = client.post("/api/semantic-workspace/tasks", json={"objective_text": "汇总费用",
            "upload_ids": [document], "output_formats": ["json"], "provider": "local", "runtime_version": "pi"})
        assert response.status_code == 202, response.text
        task_id = response.json()["task_id"]
        _wait_for_status(client, task_id, "candidate_ready")
        deadline = time.monotonic() + 5
        while not lessons.load_lessons(owner_id="user-a") and time.monotonic() < deadline:
            time.sleep(0.05)
        learned = lessons.load_lessons(owner_id="user-a")
        if outcome != "business":
            assert calls == [] and learned == []
            return
        assert len(calls) == 1 and len(learned) == 1
        assert learned[0]["status"] == "draft" and learned[0]["helped_avoid"] == 0
        for _ in range(3):
            detail = client.get(f"/api/semantic-workspace/tasks/{task_id}").json()
            assert detail["work_session"]["usage"]["total_tokens"] == 15
        assert len(calls) == 1 and lessons.load_lessons(owner_id="user-a")[0]["occurrences"] == 1
        assert lessons.load_lessons(owner_id="user-b") == []
