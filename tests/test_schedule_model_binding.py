"""通过公开计划接口验证冻结模型连接；不调用真实供应商。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.account_execution import execution_context
from src.api import auth
from src.api.routes import tasks
from src.api.session_store import pending_store
from tests.test_account_execution_scheduler import fixture
from tests.test_workspace_draft_chat import draft_api, payload


def test_confirmed_schedule_preserves_connection_and_cannot_repeat(fixture, monkeypatch):
    web, store, owner, authorization = fixture
    monkeypatch.setattr(tasks, "get_schedule_store", lambda: store)
    app = FastAPI()
    app.include_router(tasks.router)
    user = {"user_id": owner, "role": "user"}
    app.dependency_overrides[auth.get_current_user] = lambda: user
    app.dependency_overrides[auth.get_execution_user] = lambda: user
    with execution_context(authorization), TestClient(app) as client:
        pending_store.put(owner, "isolated-schedule", {"schedule": {
            "schedule": "cron@30 9 * * 1,3,5", "user_input": "每周一三五搜集标讯",
            "provider": "deepseek", "model": "synthetic-model",
            "model_connection_id": "synthetic-connection", "model_connection_version": "1" * 64,
        }})
        result = client.post("/api/tasks", json={"task_id": "isolated-schedule"})
        assert result.status_code == 200, result.text
        plans = client.get("/api/tasks").json()
        assert len(plans) == 1
        assert plans[0]["model_connection_id"] == "synthetic-connection"
        assert plans[0]["model_connection_version"] == "1" * 64
        assert client.post("/api/tasks", json={"task_id": "isolated-schedule"}).status_code == 404
        assert len(client.get("/api/tasks").json()) == 1


@pytest.mark.parametrize("followup", ["每周一三五9:30", "取消定时，先不要执行", "请解释每周一三五9:30这句话，不要创建定时任务"])
def test_natural_schedule_creates_once_after_time_clarification(draft_api, tmp_path, monkeypatch, followup):
    import asyncio
    import json
    import httpx
    from src.api.routes import chat, semantic_workspace, conversations
    from src.config.settings import settings
    from src.conversation_steering import rewriter
    from src.model_connections import ConnectionBroker, conductor
    import src.model_connections as connections
    from src.model_connections.storage import ModelConnectionRepository
    from src.model_connections.vault import FernetCredentialVault
    from src.scheduler.store import ScheduleStore
    from tests.database_migration_helpers import migrated_profile_database

    client, user, _ = draft_api
    calls = []

    def provider(request):
        body = json.loads(request.content)
        system = body.get("messages", [{}])[0].get("content", "")
        calls.append(body)
        if "工作台任务前对话" in system:
            raw = json.dumps(body, ensure_ascii=False)
            no_execution = "取消定时" in raw or "不要创建定时任务" in raw
            has_time = "9:30" in raw and not no_execution
            value = {"intent": "new_task", "confidence": "high", "normalized_text": "周期标讯",
                     "direct_answer": "未创建定时任务" if no_execution else "开始安排" if has_time else "每周几、几点执行？",
                     "open_questions": [] if has_time or no_execution else ["每周几、几点执行？"],
                     "selection_delta": {"workflow": "collection"} if has_time else {}}
        elif "意图理解模块" in system:
            value = {"need_clarification": False, "understanding": {"intent": "定期搜集医疗标讯", "where": "全网", "what": "标讯"}}
        elif "任务规划模块" in system:
            value = {"intent": "定期搜集医疗标讯", "platforms": [], "keywords": ["医疗标讯"],
                     "data_type": "article", "analysis_type": "summary", "max_items": 3,
                     "schedule": "cron@30 9 * * 1,3,5"}
        else:
            value = "OK"
        return httpx.Response(200, json={"choices": [{"message": {"content": value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}}]})

    broker = ConnectionBroker(repository=ModelConnectionRepository(settings.webui_db_path),
        vault=FernetCredentialVault.generate(), transport=httpx.MockTransport(provider), resolver=lambda _: ["8.8.8.8"])
    connection = asyncio.run(broker.create_personal(owner_user_id=user["user_id"], display_name="合成测试", preset_id="deepseek", api_key="synthetic-only", verify_all=True))
    for module in (connections, conductor, semantic_workspace, rewriter):
        monkeypatch.setattr(module, "get_default_broker", lambda: broker)
    monkeypatch.setattr(semantic_workspace, "build_context_rewriter", rewriter.build_context_rewriter)
    monkeypatch.setattr(chat, "platform_session_valid", lambda _: True)
    monkeypatch.setattr(settings, "checkpoint_enabled", False)
    store = ScheduleStore(str(migrated_profile_database(tmp_path / "schedule.db", profile="scheduler")))
    monkeypatch.setattr(tasks, "get_schedule_store", lambda: store)
    from src.scheduler.service import SchedulerService
    isolated_service = SchedulerService(store)
    monkeypatch.setattr(tasks, "get_scheduler_service", lambda: isolated_service)
    client.app.include_router(tasks.router)
    client.app.include_router(conversations.router)
    body = payload(text="定期搜集3条医疗标讯", model="deepseek-v4-pro", model_connection_id=connection["connection_id"], external_api_confirmed=True)
    first = client.post("/api/semantic-workspace/draft/turns", json=body)
    assert first.status_code == 200, first.text
    assert "几点" in first.json()["reply"]
    assert client.get("/api/tasks").json() == []
    follow = {**body, "request_id": "schedule-followup-0002", "conv_id": first.json()["conv_id"], "text": followup}
    result = client.post("/api/semantic-workspace/draft/turns", json=follow)
    assert result.status_code == 200, result.text
    if followup != "每周一三五9:30":
        assert "未创建定时任务" in result.text
        assert client.get("/api/tasks").json() == []
        return
    assert "已创建定时任务" in result.text, result.text
    plans = client.get("/api/tasks").json()
    assert len(plans) == 1
    assert "医疗标讯" in plans[0]["user_input"] and "9:30" in plans[0]["user_input"]
    assert plans[0]["model_connection_id"] == connection["connection_id"]
    assert plans[0]["model_connection_version"] == broker.freeze_connection(user["user_id"], connection["connection_id"]).connection_version
    assert client.post("/api/semantic-workspace/draft/turns", json=follow).status_code == 409
    assert len(client.get("/api/tasks").json()) == 1
    restored = client.get(f'/api/conversations/{first.json()["conv_id"]}/messages').json()
    assert restored[-1]["meta"]["scheduled_task_id"] == plans[0]["task_id"]
    from src.scheduler.service import SchedulerService
    from src.llm.provider import achat
    async def runner(*args, **kwargs):
        return {"reply": await achat([{"role": "user", "content": "合成执行"}], provider=kwargs["provider"], model=kwargs["model"])}
    service = SchedulerService(store, runner=runner)
    with execution_context(auth.get_store().capture_account_execution(user["user_id"])):
        asyncio.run(service.run_task_now(plans[0]["task_id"]))
    assert store.get(plans[0]["task_id"])["last_success"] == 1
    assert calls[-1]["model"] == "deepseek-v4-pro"
    broker.delete_connection(connection["connection_id"], user["user_id"], can_manage=False)
    call_count = len(calls)
    with execution_context(auth.get_store().capture_account_execution(user["user_id"])):
        asyncio.run(service.run_task_now(plans[0]["task_id"]))
    assert store.get(plans[0]["task_id"])["last_success"] == 0
    assert len(calls) == call_count


@pytest.mark.parametrize("quality", [{"passed": True}, {}, {"passed": False}])
def test_scheduled_report_emails_only_requested_business_files(fixture, tmp_path, monkeypatch, quality):
    import asyncio
    import json
    from datetime import datetime
    from src.scheduler.service import SchedulerService
    from src.conductor import email_sender
    import importlib
    settings_module = importlib.import_module("src.config.settings")
    from src.llm import provider
    web, store, owner, authorization = fixture
    monkeypatch.setattr(settings_module, "PROJECT_ROOT", tmp_path)
    output = tmp_path / "downloads" / "synthetic-output"
    output.mkdir(parents=True)
    (output / "report.md").write_text("# 合成报告", encoding="utf-8")
    (output / "trace.json").write_text("内部轨迹，不外发", encoding="utf-8")
    sent = []
    class SMTP:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def login(self, *args): pass
        def send_message(self, message, **kwargs): sent.append(message); return {}
    monkeypatch.setattr(email_sender.smtplib, "SMTP_SSL", SMTP)
    for key, value in dict(smtp_enabled=True, smtp_host="smtp.example.invalid", smtp_port=465,
                           smtp_use_ssl=True, smtp_user="sender@example.invalid", smtp_from="", smtp_password="synthetic").items():
        monkeypatch.setattr(email_sender.settings, key, value)
    requirement = "每天9点生成报告，完成后发给 reader@example.invalid"
    async def model(*args, **kwargs):
        return json.dumps({"channel": "email", "recipients": ["reader@example.invalid"], "body": True,
                           "attachments": True, "evidence": "完成后发给 reader@example.invalid"})
    monkeypatch.setattr(provider, "achat", model)
    async def runner(*args, **kwargs):
        return {"task_id": "synthetic-output", "reply": "报告已生成", "quality": quality,
                "outputs": {"report_md": str(output / "report.md"), "trace_file": str(output / "trace.json")}}
    with execution_context(authorization):
        task_id = store.add(user_input=requirement, provider=None, model=None, trigger_type="cron",
            cron_expr="0 9 * * *", run_at=None, next_run_at=datetime(2099, 1, 1), owner_user_id=owner)
        asyncio.run(SchedulerService(store, runner=runner).run_task_now(task_id))
    if quality.get("passed") is True:
        assert len(sent) == 1
        assert sent[0]["To"] == "reader@example.invalid"
        assert [item.get_filename() for item in sent[0].iter_attachments()] == ["report.md"]
    else:
        assert sent == []
        assert "未自动发送" in store.list_runs(task_id)[0]["summary"]
    assert store.list_runs(task_id)[0]["summary"].startswith("发送结果：")
