"""自动化Excel通知只选择本轮产物，使用真实工作簿和SMTP替身。"""
from datetime import datetime
import importlib
import json
from pathlib import Path

import pytest

from src.account_execution import execution_context
from src.conductor import email_sender
from src.conductor.nodes import output
from src.conductor.task_spec import TaskSpec
from src.llm import provider
from src.scheduler.service import SchedulerService
from tests.test_account_execution_scheduler import fixture


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["full", "xlsx_only", "missing_xlsx", "xlsx_without_notification"])
async def test_scheduled_excel_uses_only_current_declared_output(fixture, tmp_path, monkeypatch, mode):
    web, store, owner, authorization = fixture
    monkeypatch.setattr(importlib.import_module("src.config.settings"), "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(output, "PROJECT_ROOT", tmp_path)
    sent = []

    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def login(self, *args):
            pass

        def send_message(self, message, **kwargs):
            sent.append((message, kwargs))
            return {}

    monkeypatch.setattr(email_sender.smtplib, "SMTP_SSL", SMTP)
    for key, value in dict(smtp_enabled=True, smtp_host="smtp.example.invalid", smtp_port=465,
                           smtp_use_ssl=True, smtp_user="sender@example.invalid", smtp_from="",
                           smtp_password="synthetic").items():
        monkeypatch.setattr(email_sender.settings, key, value)
    requirement = ("生成Excel" if mode == "xlsx_without_notification"
                   else "生成后只把Excel发给 reader@example.invalid")

    async def model(messages, **kwargs):
        assert messages[1]["content"] == requirement
        return json.dumps({"channel": "email", "recipients": ["reader@example.invalid"],
                           "body": False, "attachments": True, "files": ["Excel"], "evidence": requirement})

    monkeypatch.setattr(provider, "achat", model)
    old_state = {"task_id": "previous-round", "task_spec": TaskSpec(intent="旧轮资料", outputs=["xlsx"]),
                 "cleaned_dataset": [{"title": "旧轮内容", "content": "不得发送"}]}
    old = await output.output_node(old_state)
    old_bytes = Path(old["outputs"]["xlsx"]).read_bytes()
    current_dir = tmp_path / "downloads" / "current-round"
    if mode == "missing_xlsx":
        # 同名残留不能因本轮没有声明Excel而被目录扫描补进附件。
        current_dir.mkdir(parents=True)
        (current_dir / "data.xlsx").write_bytes(old_bytes)

    async def runner(*args, **kwargs):
        state = {"task_id": "current-round", "quality": {"passed": True},
                 "task_spec": TaskSpec(intent="本轮资料", outputs=["json"] if mode == "missing_xlsx" else ["xlsx", "json"]),
                 "cleaned_dataset": [{"title": "本轮内容", "content": "仅此轮结果"}]}
        result = {**state, **await output.output_node(state)}
        if mode in ("xlsx_only", "xlsx_without_notification"):
            result["outputs"] = {"xlsx": result["outputs"]["xlsx"]}
            result["reply"] = ""
        return result

    with execution_context(authorization):
        task_id = store.add(user_input=requirement, provider=None, model=None, trigger_type="cron",
                            cron_expr="0 9 * * *", run_at=None, next_run_at=datetime(2099, 1, 1),
                            owner_user_id=owner)
        assert await SchedulerService(store, runner=runner).run_task_now(task_id) == "started"
    run = store.list_runs(task_id)[0]
    if mode == "missing_xlsx":
        assert sent == []
        assert json.loads(run["notification_json"])["status"] != "sent"
        assert (current_dir / "data.xlsx").read_bytes() == old_bytes
    elif mode == "xlsx_without_notification":
        assert sent == [] and run["notification_json"] is None
        assert run["success"] == 1 and run["state"] == "succeeded"
        assert "xlsx=已生成" in run["summary"]
        assert str(current_dir / "data.xlsx") not in run["summary"]
    else:
        assert len(sent) == 1
        message, envelope = sent[0]
        attachments = list(message.iter_attachments())
        assert len(attachments) == 1 and attachments[0].get_filename() == "data.xlsx"
        assert attachments[0].get_content_type() == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert attachments[0].get_payload(decode=True) == (current_dir / "data.xlsx").read_bytes()
        assert attachments[0].get_payload(decode=True) != old_bytes
        assert message["To"] == "reader@example.invalid" and envelope["to_addrs"] == ["reader@example.invalid"]
        assert json.loads(run["notification_json"])["status"] == "sent"
        assert run["success"] == 1 and run["state"] == "succeeded"
