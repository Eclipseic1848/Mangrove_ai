"""正式交付后的经验沉淀只使用隔离库和模拟供应商。"""
import asyncio
import json
import threading
import time

import httpx
import pytest

from src.config.settings import settings
from src.memory import templates
from src.model_connections import ConnectionBroker
from src.model_connections.storage import ModelConnectionRepository
from src.model_connections.vault import FernetCredentialVault
from tests.test_web_source_delivery_api import _client, CoverageAwareWebPiRuntime
from tests.test_pi_runtime_workspace_api import _uploads, _wait_for_delivery


@pytest.mark.parametrize("outcome", ["ok", "invalid", "failure", "unverified", "concurrent", "delayed"])
def test_verified_delivery_learns_private_draft_and_next_task_reuses_it(tmp_path, monkeypatch, outcome):
    import src.model_connections.broker as broker_module
    from tests.test_pi_runtime_workspace_api import InconclusivePiRuntime, _wait_for_status
    runtime = InconclusivePiRuntime() if outcome == "unverified" else CoverageAwareWebPiRuntime()
    client = _client(tmp_path, monkeypatch, role="admin", pi_runtime=runtime)
    monkeypatch.setattr(templates, "TEMPLATES_DIR", tmp_path / "methods")
    calls = []
    release_learning = threading.Event()

    async def provider(request):
        body = json.loads(request.content)
        if "工作台方法草稿" in json.dumps(body, ensure_ascii=False):
            calls.append(body)
            if outcome == "delayed":
                while not release_learning.is_set():
                    await asyncio.sleep(0.01)
            if outcome == "failure":
                return httpx.Response(503, json={"error": "synthetic failure"})
            if outcome == "concurrent":
                await asyncio.sleep(0.02)
        result = {"title": "费用字段核对", "keywords": ["费用汇总"],
                  "body": "逐项核对费用字段，保留来源定位；缺失内容不得猜测。"}
        if outcome == "invalid":
            result = {}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})

    broker = ConnectionBroker(repository=ModelConnectionRepository(settings.webui_db_path),
        vault=FernetCredentialVault.generate(), transport=httpx.MockTransport(provider), resolver=lambda _: ["8.8.8.8"])
    connection = asyncio.run(broker.configure_personal(owner_user_id="user-a", preset_id="deepseek", api_key="synthetic-only"))
    monkeypatch.setattr(broker_module, "_default_broker", broker)
    document, _ = _uploads(tmp_path)
    payload = {"objective_text": "费用汇总，不猜测缺失项", "upload_ids": [document], "output_formats": ["json"],
        "runtime_version": "pi", "model_connection_id": connection["connection_id"],
        "model_connection_model": connection["default_model"], "external_api_confirmed": True}
    with client:
        first = client.post("/api/semantic-workspace/tasks", json=payload)
        assert first.status_code == 202, first.text
        first_id = first.json()["task_id"]
        if outcome == "delayed":
            try:
                deadline = time.monotonic() + 15
                while not calls and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert calls, "学习请求未启动"
                assert client.get(f"/api/semantic-workspace/tasks/{first_id}").json()["status"] == "completed"
            finally:
                release_learning.set()
        if outcome == "concurrent":
            parallel = client.post("/api/semantic-workspace/tasks", json=payload)
            assert parallel.status_code == 202, parallel.text
            _wait_for_delivery(client, parallel.json()["task_id"])
        if outcome == "unverified":
            _wait_for_status(client, first_id, "candidate_ready")
            assert templates.load_templates(owner_id="user-a") == []
            assert calls == []
            return
        first_detail = _wait_for_delivery(client, first_id)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            first_detail = client.get(f"/api/semantic-workspace/tasks/{first_id}").json()
            if any(event.get("event_type") == "learning_feedback" for event in first_detail.get("events", [])):
                break
            time.sleep(0.05)
        assert any(event.get("event_type") == "learning_feedback" for event in first_detail.get("events", []))
        if outcome in {"invalid", "failure"}:
            assert first_detail["status"] == "completed"
            assert templates.load_templates(owner_id="user-a") == []
            for _ in range(2):
                assert client.get(f"/api/semantic-workspace/tasks/{first_id}").json()["status"] == "completed"
            assert len(calls) == 1
            return
        learned = templates.load_templates(owner_id="user-a")
        assert len(learned) == 1
        assert learned[0]["status"] == "draft" and learned[0]["scope"] == "owner"
        assert learned[0]["data_type"] == "workspace_document"
        if outcome == "concurrent":
            assert 1 <= len(calls) <= 2
            assert templates.load_templates(owner_id="user-b") == []
            return
        assert learned[0]["uses"] == 0 and learned[0]["quality_avg"] == 0
        assert templates.load_templates(owner_id="user-b") == []
        assert len(calls) == 1 and calls[0]["model"] == connection["default_model"]
        usage = first_detail["agentic_runtime"]["provider_usage"]
        assert any(row["input_tokens"] == 20 and row["output_tokens"] == 10 for row in usage)
        for _ in range(2):
            assert client.get(f"/api/semantic-workspace/tasks/{first_id}").status_code == 200
        second = client.post("/api/semantic-workspace/tasks", json=payload)
        assert second.status_code == 202, second.text
        detail = _wait_for_delivery(client, second.json()["task_id"])
        assert detail["task_context"]["automatically_selected"] is True
        assert "核对费用字段" in detail["task_context"]["template"]["method_draft"]
        assert len(calls) == 1
