"""外部业务写在共享执行接缝拒绝；全部使用虚构配置与临时文件。"""

import pytest
import sqlite3
from types import SimpleNamespace

from src.conductor import db_writer, email_sender, slack_sender
from tests.database_migration_helpers import migrated_profile_database
from src import account_execution as execution
from src.api import auth
from src.api.routes import confirm
from src.api.schemas import ConfirmIn
from src.api.session_store import PendingStore
from src.api.store import WebUIStore
from tests.account_execution_helpers import seed_execution_owner
from tests.database_migration_helpers import migrated_webui_database
from fastapi import HTTPException
from src.conductor.nodes import output
from src.conductor.task_spec import TaskSpec
from src.api.routes import chat
from src.api.routes import config_routes, settings_routes


class UnreadableSettings:
    def __getattr__(self, name):
        raise AssertionError("拒绝外部写之前不得读取配置或凭据")


@pytest.mark.asyncio
async def test_delivery_senders_reject_before_credentials_attachments_or_network(monkeypatch):
    monkeypatch.setattr(email_sender, "settings", UnreadableSettings())
    monkeypatch.setattr(slack_sender, "settings", UnreadableSettings())
    with pytest.raises(PermissionError, match="外部只读"):
        email_sender.send_report(["fixture@example.invalid"], "虚构", "正文", ["不存在的附件"])
    with pytest.raises(PermissionError, match="外部只读"):
        await slack_sender.send_report("虚构", "正文")


def test_mysql_write_is_denied_while_internal_sqlite_result_is_preserved(monkeypatch, tmp_path):
    monkeypatch.setattr(db_writer, "settings", UnreadableSettings())
    with pytest.raises(PermissionError, match="外部只读"):
        db_writer._write_mysql([("虚构",)])
    monkeypatch.setattr(db_writer, "settings", SimpleNamespace(db_backend="mysql"))
    with pytest.raises(PermissionError, match="外部只读"):
        db_writer.write_items("task", [{"title": "虚构", "content": "本机结果"}])
    path = migrated_profile_database(tmp_path / "local.db", profile="legacy_app")
    monkeypatch.setattr(db_writer, "_DB_PATH", path)
    monkeypatch.setattr(db_writer, "settings", SimpleNamespace(db_backend="sqlite"))
    assert db_writer.write_items("task", [{"title": "虚构", "content": "本机结果"}]) == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT title, content FROM collected_items").fetchall() == [("虚构", "本机结果")]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["email", "slack", "db"])
async def test_historical_confirmation_rejects_without_consuming_pending(monkeypatch, tmp_path, action):
    path = migrated_webui_database(tmp_path / "confirm.db")
    owner = seed_execution_owner(path)
    store = WebUIStore(str(path))
    pending = PendingStore()
    monkeypatch.setattr(auth, "get_store", lambda: store)
    monkeypatch.setattr(confirm, "pending_store", pending)
    monkeypatch.setattr(db_writer.settings, "db_backend", "mysql")
    payload = {action: {"to": ["fixture@example.invalid"], "subject": "虚构", "title": "虚构",
                        "task_id": "task", "items": [{"content": "虚构"}]}}
    with execution.execution_context(owner):
        pending.put(owner.owner_user_id, "task", payload)
        for _ in range(2):
            with pytest.raises(HTTPException) as caught:
                await getattr(confirm, "confirm_" + action)(ConfirmIn(task_id="task"), user={"user_id": owner.owner_user_id})
            assert caught.value.status_code == 403
            assert "外部只读" in caught.value.detail
            assert pending.get(owner.owner_user_id, "task") == payload
        store.update_user(owner.owner_user_id, disabled=True)
        with pytest.raises(execution.ExecutionDenied):
            await getattr(confirm, "confirm_" + action)(ConfirmIn(task_id="task"), user={"user_id": owner.owner_user_id})


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [False, True])
async def test_output_keeps_local_report_but_never_offers_external_confirmation(monkeypatch, tmp_path, approved):
    monkeypatch.setattr(output, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(email_sender.settings, "smtp_enabled", False)
    monkeypatch.setattr(slack_sender.settings, "slack_enabled", False)
    spec = TaskSpec(intent="整理虚构资料", outputs=["report_md", "email", "slack"])
    state = {"task_spec": spec, "task_id": "task", "cleaned_dataset": [],
             "approved_email": approved, "approved_slack": approved,
             "outputs": {"email_pending": True, "slack_pending": True}}
    result = await output.output_node(state)
    assert (tmp_path / "downloads/task/report.md").is_file()
    assert "外部只读" in result["reply"]
    assert not result["outputs"].get("email_pending")
    assert not result["outputs"].get("slack_pending")
    assert "确认发送" not in result["reply"]
    restored = chat._build_result("owner", "conv", state, "旧报告", None, None, "虚构")
    assert restored["actions"] == []


@pytest.mark.asyncio
async def test_slack_selfchecks_reject_without_reading_saved_webhook(monkeypatch):
    monkeypatch.setattr(slack_sender, "settings", UnreadableSettings())
    with pytest.raises(PermissionError, match="外部只读"):
        await config_routes._verify_target("slack")
    result = await settings_routes.selfcheck(settings_routes.SelfCheckIn(target="slack"), _admin={"role": "admin"})
    assert result["ok"] is False
    assert "外部只读" in result["detail"]
