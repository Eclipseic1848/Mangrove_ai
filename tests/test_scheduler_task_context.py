"""自动化计划持久选中引用；每次运行重新冻结，不调用外部模型。"""
import asyncio
import copy
import json
from datetime import datetime

import pytest

import src.api.auth as auth
from src.account_execution import execution_context
from src.api.store import WebUIStore
from src.memory import loader
from src.scheduler.service import SchedulerService
from src.scheduler.store import ScheduleStore
from src.task_context import MemorySelection, TaskContextRepository, TaskContextSelection, TaskContextService, TaskTemplateDraft, TaskTemplateRef
from tests.database_migration_helpers import migrated_profile_database, migrated_webui_database


@pytest.fixture
def selected_context(tmp_path, monkeypatch):
    web = WebUIStore(str(migrated_webui_database(tmp_path / "webui.db")))
    schedules = ScheduleStore(str(migrated_profile_database(tmp_path / "scheduler.db", profile="scheduler")))
    monkeypatch.setattr(auth, "get_store", lambda: web)
    monkeypatch.setattr(loader, "MEMORY_DIR", tmp_path / "memory")
    loader.MEMORY_DIR.mkdir()
    preferences = loader.MEMORY_DIR / "user-preferences.md"
    preferences.write_text("## 用户偏好\n所有报告使用合成平台第一版\n", encoding="utf-8")
    owner, other = [web.create_user(name, "synthetic-hash")["user_id"] for name in ("alice", "bob")]
    memory = web.memory_add(owner, "所有报告携带合成记忆第一版", purpose="general")
    web.memory_add(other, "其他账号的合成记忆", purpose="web_research")
    repository = TaskContextRepository(web.db_path)
    draft = TaskTemplateDraft(template_id="synthetic-method", version=1, title="合成模板",
        source="owner_created", purpose="general", goal_contract_draft="完成合成分析",
        delivery_spec_draft={}, method_draft="合成方法第一版")
    repository.put_template(owner, draft, expected_version=0)
    preview = TaskContextService(repository).preview(owner_id=owner, purpose="web_research",
        objective_text="合成分析", output_formats=("markdown",),
        selection=TaskContextSelection(template=TaskTemplateRef(template_id=draft.template_id, version=1),
            memories=(MemorySelection(memory_id=memory["id"]),)))
    return web, schedules, owner, other, repository, memory, draft, preview, preferences


def _add(schedules, owner, preview=None):
    with execution_context(auth.get_store().capture_account_execution(owner)):
        return schedules.add(user_input="合成分析", provider="synthetic", model="saved-model",
            trigger_type="once", cron_expr=None, run_at=datetime(2026, 1, 1),
            next_run_at=datetime(2026, 1, 1), owner_user_id=owner,
            task_context_preview=preview)


def test_schedule_reopens_selected_snapshot_and_old_null_plan(selected_context):
    _, schedules, owner, _, _, _, _, preview, _ = selected_context
    task_id = _add(schedules, owner, preview.model_dump(mode="json"))
    old_id = _add(schedules, owner)
    reopened = ScheduleStore(schedules.db_path)
    assert json.loads(reopened.get(task_id)["task_context_json"]) == preview.model_dump(mode="json")
    assert reopened.get(old_id)["task_context_json"] is None


def test_each_schedule_run_refreshes_memory_and_global_but_keeps_template_version(selected_context):
    web, schedules, owner, _, repository, memory, draft, preview, preferences = selected_context
    task_id = _add(schedules, owner, preview.model_dump(mode="json"))
    repository.put_template(owner, draft.model_copy(update={"version": 2, "method_draft": "后来模板版本"}), expected_version=1)
    observed = []

    async def runner(user_input, **kwargs):
        snapshot = copy.deepcopy(kwargs["task_context_preview"])
        assert kwargs["provider"] == "synthetic" and kwargs["model"] == "saved-model"
        assert kwargs["approved_db_write"] is False and kwargs["ignore_schedule"] is True
        observed.append(snapshot)
        # 启动后读库变化不能改写同次运行已经给执行器的快照。
        if len(observed) == 1:
            assert web.memory_update(owner, memory["id"], "所有报告携带合成记忆第二版", memory["text"])[0] == "updated"
            preferences.write_text("## 用户偏好\n所有报告使用合成平台第二版\n", encoding="utf-8")
            await asyncio.sleep(0)
            assert kwargs["task_context_preview"] == snapshot
        return {"needs_clarification": True, "clarification_question": "合成追问"}

    service = SchedulerService(schedules, runner=runner)
    with execution_context(web.capture_account_execution(owner)):
        assert asyncio.run(service.run_task_now(task_id)) == "started"
        assert asyncio.run(service.run_task_now(task_id)) == "started"
    assert len(observed) == 2
    first, second = [item["compiled_context"]["content"] for item in observed]
    assert "合成记忆第一版" in first and "合成平台第一版" in first
    assert "合成记忆第二版" not in first and "合成平台第二版" not in first
    assert "合成记忆第二版" in second and "合成平台第二版" in second
    assert "合成记忆第一版" not in second and "合成平台第一版" not in second
    assert all(item["owner_id"] == owner and item["purpose"] == "web_research"
        and item["template"]["version"] == 1 and "合成方法第一版" in item["compiled_context"]["content"]
        and "后来模板版本" not in item["compiled_context"]["content"]
        and "其他账号" not in item["compiled_context"]["content"] for item in observed)


@pytest.mark.parametrize("damage", ["template_retired", "template_deleted", "memory_deleted", "owner", "compiled_owner", "content", "json", "memory_id", "template_version", "purpose", "output_formats"])
def test_invalid_scheduled_context_stops_before_runner(selected_context, damage, caplog):
    web, schedules, owner, other, repository, memory, draft, preview, _ = selected_context
    task_id = _add(schedules, owner, preview.model_dump(mode="json"))
    snapshot = preview.model_dump(mode="json")
    if damage == "template_retired":
        repository.retire_template(owner, draft.template_id, 1)
    elif damage == "template_deleted":
        with web._conn() as connection:
            connection.execute("DELETE FROM task_templates WHERE owner_id=? AND template_id=?", (owner, draft.template_id))
    elif damage == "memory_deleted":
        web.memory_delete(owner, memory["id"])
    else:
        if damage == "owner":
            snapshot["owner_id"] = other
        elif damage == "compiled_owner":
            snapshot["compiled_context"]["owner_id"] = other
        elif damage == "content":
            snapshot["compiled_context"]["content"] += "synthetic-sensitive-marker"
        elif damage == "memory_id":
            replacement = web.memory_add(owner, "替代的本人合法记忆", purpose="general")
            snapshot["memories"][0]["memory_id"] = replacement["id"]
        elif damage == "template_version":
            repository.put_template(owner, draft.model_copy(update={"version": 2, "method_draft": "替代的合法模板"}), expected_version=1)
            snapshot["template"]["version"] = 2
        elif damage == "purpose":
            snapshot["purpose"] = "invoice_processing"
        elif damage == "output_formats":
            snapshot["output_formats"] = ["json"]
        value = "synthetic-sensitive-marker" if damage == "json" else json.dumps(snapshot, ensure_ascii=False)
        with schedules._conn() as connection:
            connection.execute("UPDATE scheduled_tasks SET task_context_json=? WHERE task_id=?", (value, task_id))
    called = []

    async def runner(*args, **kwargs):
        called.append(kwargs)
        return {"reply": "不应执行"}

    with execution_context(web.capture_account_execution(owner)):
        asyncio.run(SchedulerService(schedules, runner=runner).run_task_now(task_id))
    assert called == []
    assert schedules.list_runs(task_id)[0]["success"] == 0
    assert "synthetic-sensitive-marker" not in str(schedules.list_runs(task_id)) + caplog.text
    assert web.account_execution_binding(owner, "schedule", task_id)["state"] == "idle"


@pytest.mark.parametrize("damage", ["owner", "compiled_owner", "content"])
def test_add_rejects_invalid_context_without_execution_binding(selected_context, damage):
    web, schedules, owner, other, _, _, _, preview, _ = selected_context
    snapshot = preview.model_dump(mode="json")
    if damage == "owner":
        snapshot["owner_id"] = other
    elif damage == "compiled_owner":
        snapshot["compiled_context"]["owner_id"] = other
    else:
        snapshot["compiled_context"]["content"] += "合成改写"
    with pytest.raises((PermissionError, ValueError)):
        _add(schedules, owner, snapshot)
    assert schedules.list_active(owner) == []
    with web._conn() as connection:
        assert connection.execute("SELECT COUNT(*) FROM account_execution_bindings WHERE owner_user_id=? AND resource_kind='schedule'", (owner,)).fetchone()[0] == 0
