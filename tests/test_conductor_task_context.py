# -*- coding: utf-8 -*-
"""真实编排图冻结本人上下文；只替换采集与模型等外部接缝。"""
import asyncio
import hashlib
import json

import pytest

import src.api.auth as auth
from src.account_execution import execution_context
from src.api.store import WebUIStore
from src.config.settings import settings
from src.data_prep import graph as prep
from src.data_prep.models import DataPrepTaskSpec, SourceSpec, SourceType
from src.conductor import graph as host
from src.conductor.nodes import analyze, intent, planner
from src.memory import loader, templates
from src.task_context import MemorySelection, TaskContextRepository, TaskContextService, TaskContextSelection, TaskTemplateDraft, TaskTemplateRef
from tests.database_migration_helpers import migrated_webui_database


@pytest.fixture
def context_store(tmp_path, monkeypatch):
    database = migrated_webui_database(tmp_path / "webui.db")
    web = WebUIStore(str(database))
    monkeypatch.setattr(settings, "webui_db_path", str(database))
    monkeypatch.setattr(settings, "checkpoint_enabled", False)
    monkeypatch.setattr(auth, "get_store", lambda: web)
    monkeypatch.setattr(loader, "MEMORY_DIR", tmp_path / "memory")
    loader.MEMORY_DIR.mkdir()
    (loader.MEMORY_DIR / "user-preferences.md").write_text(
        "## 用户偏好\n所有报告使用合成平台格式\n", encoding="utf-8")
    owners = [web.create_user(name, "synthetic-hash")["user_id"] for name in ("alice", "bob")]
    return web, owners


@pytest.mark.parametrize("streaming", [False, True])
def test_conductor_report_uses_frozen_owner_memory_and_template(context_store, monkeypatch, streaming):
    web, (owner, other) = context_store
    saved_memory = web.memory_add(owner, "所有报告携带合成个人标记")
    web.memory_add(other, "所有报告携带其他账号标记")
    web.memory_add(owner, "所有报告携带发票专属标记", purpose="invoice_processing")
    templates.TEMPLATES_DIR.mkdir()
    template_file = templates.TEMPLATES_DIR / "synthetic-method.md"
    template_text = ("---\ntitle: 合成方法\nkeywords: [合成]\ndata_type: generic\n"
                     f"scope: owner\nowner_id: {owner}\nstatus: active\n---\n"
                     "合成冻结方法：报告必须分为两段。\n")
    template_file.write_text(template_text, encoding="utf-8")
    prompts = []

    async def intent_model(messages, **kwargs):
        prompts.append(("intent", messages[0]["content"]))
        return json.dumps({"understanding": {"intent": "合成市场摘要"}})

    async def planner_model(messages, **kwargs):
        prompts.append(("planner", messages[0]["content"]))
        return json.dumps({"intent": "合成市场摘要", "data_type": "generic", "analysis_type": "summary", "max_items": 1})

    async def analyze_model(messages, **kwargs):
        if "模板路由器" in messages[0]["content"]:
            return "generic"
        prompts.append(("analyze", messages[0]["content"]))
        return "合成报告"

    async def collect(state):
        # 运行中目录变化只影响后续新任务，不能改变已冻结的分析上下文。
        web.memory_delete(owner, saved_memory["id"])
        template_file.write_text(template_text.replace("合成冻结方法", "后来的方法"), encoding="utf-8")
        (loader.MEMORY_DIR / "user-preferences.md").write_text("## 用户偏好\n所有报告使用后来的格式\n", encoding="utf-8")
        return {"raw_dataset": [{"title": "合成资料", "content": "合成来源正文"}]}

    async def clean(state):
        return {"cleaned_dataset": state["raw_dataset"]}

    async def noop(state):
        return {}

    async def output(state):
        return {"reply": state["analysis"]}

    monkeypatch.setattr(intent, "achat", intent_model)
    monkeypatch.setattr(planner, "achat", planner_model)
    monkeypatch.setattr(analyze, "achat", analyze_model)
    for name, node in {"target_resolve": noop, "router": noop, "collect": collect, "clean": clean,
                       "checker": noop, "output": output}.items():
        monkeypatch.setattr(host, name + "_node", node)
    monkeypatch.setattr(host, "_GRAPH", None)

    async def run():
        with execution_context(web.capture_account_execution(owner)):
            if streaming:
                events = [event async for event in host.astream_conductor("合成市场摘要", task_id="synthetic-task")]
                return next(value for kind, value in events if kind == "final")
            return await host.run_conductor("合成市场摘要", task_id="synthetic-task")

    result = asyncio.run(run())
    assert result["reply"] == "合成报告"
    assert [name for name, _ in prompts] == ["intent", "planner", "analyze"]
    for _, prompt in prompts:
        assert "合成个人标记" in prompt
        assert "合成平台格式" in prompt
        assert "其他账号标记" not in prompt
        assert "发票专属标记" not in prompt
        assert "后来的格式" not in prompt
    assert prompts[-1][1].count("合成冻结方法") == 1
    assert "后来的方法" not in prompts[-1][1]
    assert result["task_context_preview"]["owner_id"] == owner


def test_data_prep_stream_uses_confirmed_template_and_freezes_owner_memory(context_store, monkeypatch):
    web, (owner, other) = context_store
    memory = web.memory_add(owner, "所有任务使用合成采集偏好")
    web.memory_add(other, "所有任务使用其他账号偏好")
    repository = TaskContextRepository(settings.webui_db_path)
    repository.save_template(owner, TaskTemplateDraft(template_id="synthetic-prep", version=1,
        title="合成预处理", source="owner_created", purpose="web_research", goal_contract_draft="读取当前网址",
        method_draft="合成预处理方法：保留用户字段。"))
    service = TaskContextService(repository)
    snapshot = service.conversation_preview(owner_id=owner, objective_text="合成采集",
        purpose="web_research", selection=TaskContextSelection(template=TaskTemplateRef(template_id="synthetic-prep", version=1),
            memories=(MemorySelection(memory_id=memory["id"]),)))
    observed = []

    async def model(messages, **kwargs):
        observed.append(messages[0]["content"])
        return json.dumps({"understanding": {"intent": "合成采集", "urls": ["https://synthetic.invalid/"]}})

    async def acquire(state):
        assert state["task_context_preview"]["template"]["template_id"] == "synthetic-prep"
        assert state["spec"].sources[0].locator == "https://synthetic.invalid/"
        return {"error": "合成停止", "status": "FAILED"}

    monkeypatch.setattr(intent, "achat", model)
    monkeypatch.setattr(prep, "acquire_node", acquire)
    monkeypatch.setattr(prep, "_GRAPH", None)

    async def run():
        with execution_context(web.capture_account_execution(owner)):
            events = [event async for event in prep.astream_data_prep("合成采集", task_id="synthetic-prep",
                task_context_preview=snapshot.model_dump(mode="json"))]
            return next(value for kind, value in events if kind == "final")

    result = asyncio.run(run())
    assert "合成预处理方法" in observed[0]
    assert "合成采集偏好" in observed[0]
    assert "其他账号偏好" not in observed[0]
    assert result["task_context_preview"]["preview_sha256"] == snapshot.preview_sha256


def test_typed_data_prep_preserves_recipe_and_freezes_relevant_context(context_store, monkeypatch):
    web, (owner, other) = context_store
    web.memory_add(owner, "所有任务使用合成处理偏好", purpose="data_processing")
    web.memory_add(other, "所有任务使用其他账号偏好")
    web.memory_add(owner, "所有任务使用发票专属偏好", purpose="invoice_processing")
    spec = DataPrepTaskSpec(intent="合成处理", sources=[SourceSpec(source_id="synthetic",
        source_type=SourceType.WEB, locator="https://synthetic.invalid/")])
    expected = spec.model_dump(mode="json", exclude={"task_id"})

    async def acquire(state):
        assert state["spec"].model_dump(mode="json", exclude={"task_id"}) == expected
        context = state["task_context_preview"]["compiled_context"]["content"]
        assert "合成处理偏好" in context
        assert "其他账号偏好" not in context and "发票专属偏好" not in context
        return {"error": "合成停止", "status": "FAILED"}

    monkeypatch.setattr(prep, "acquire_node", acquire)
    monkeypatch.setattr(prep, "_GRAPH", None)
    with execution_context(web.capture_account_execution(owner)):
        result = asyncio.run(prep.run_data_prep(spec, "synthetic-typed"))
    assert result["task_context_preview"]["owner_id"] == owner


def test_collection_recovery_carries_original_context_and_rejects_snapshot_change(context_store, tmp_path, monkeypatch):
    from src.collectors.base import CollectResult
    from src.config import cookie_identity
    from src.config.cookie_probe_binding import capture_probe_environment
    from src.conductor import evidence_collection as flow
    from src.conductor.collection_recovery import load_recovery, progress_location
    from tests.test_evidence_collection_flow import spec_for

    web, (owner, other) = context_store
    saved_memory = web.memory_add(owner, "所有任务使用合成恢复偏好")
    monkeypatch.setattr(settings, "semantic_execution_root", str(tmp_path / "execution"))
    monkeypatch.setattr(settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(settings, "mc_cookie_xhs", "synthetic-cookie")
    service = TaskContextService(TaskContextRepository(settings.webui_db_path))
    snapshot = service.conversation_preview(owner_id=owner, objective_text="合成恢复", purpose="web_research")
    spec = spec_for("合成恢复", initial_candidates=1, candidate_limit=1)
    state = {"task_id": "synthetic-recovery", "task_spec": spec, "user_input": spec.intent,
        "session_id": "synthetic-chat", "task_context_preview": snapshot.model_dump(mode="json")}

    async def collect(self, request):
        return CollectResult(False, "synthetic", coverage={"failure": {"reason": "login_required"}})

    async def forbidden(*args):
        raise AssertionError("零来源不得抽取或读图")

    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    with execution_context(web.capture_account_execution(owner)):
        cookie_identity.record_identity("mc_cookie_xhs", "synthetic-cookie", capture_probe_environment(),
            {"status": "valid", "account_ref_kind": "account_id", "account_ref": hashlib.sha256(b"synthetic-account").hexdigest()})
        asyncio.run(flow.collect_evidence(state, scope=spec.evidence_collection,
            queries=spec.evidence_collection.queries, assess=forbidden, exclude=flow.exclude_topic, read_images=forbidden))
        web.memory_delete(owner, saved_memory["id"])
        recovered = load_recovery(owner, state["task_id"])
    assert recovered["state"]["task_context_preview"] == state["task_context_preview"]
    store, key = progress_location(owner, state["task_id"])
    private_path = store.task_dir(key) / "progress.json"
    saved = json.loads(private_path.read_text(encoding="utf-8"))
    saved["request"]["task_context_preview"]["owner_id"] = other
    store.write_json(key, "progress.json", saved)
    before = private_path.read_bytes()
    with pytest.raises(ValueError, match="上下文"):
        load_recovery(owner, state["task_id"])
    assert private_path.read_bytes() == before


@pytest.mark.parametrize("damage", ["owner", "compiled_owner", "content", "read_failure"])
def test_conductor_rejects_invalid_context_before_model_or_collection(context_store, monkeypatch, damage):
    web, (owner, other) = context_store
    service = TaskContextService(TaskContextRepository(settings.webui_db_path))
    snapshot = service.conversation_preview(owner_id=owner, objective_text="合成检查").model_dump(mode="json")
    if damage == "owner":
        snapshot["owner_id"] = other
    elif damage == "compiled_owner":
        snapshot["compiled_context"]["owner_id"] = other
    elif damage == "content":
        snapshot["compiled_context"]["content"] += "未经冻结的文字"
    else:
        snapshot = None
        def fail(**kwargs):
            raise OSError("synthetic-private-path")
        monkeypatch.setattr(loader, "load_preferences", fail)
    called = []

    async def forbidden(*args, **kwargs):
        called.append("external")
        raise AssertionError("校验失败不能请求模型或采集")

    monkeypatch.setattr(intent, "achat", forbidden)
    monkeypatch.setattr(host, "collect_node", forbidden)
    monkeypatch.setattr(host, "_GRAPH", None)
    with execution_context(web.capture_account_execution(owner)):
        result = asyncio.run(host.run_conductor("合成检查", task_context_preview=snapshot))
    assert called == []
    assert "记忆或冻结上下文" in result["error"]
    assert "synthetic-private-path" not in result["error"]


def test_scheduler_triggers_new_frozen_context_without_changing_model_or_owner(context_store, tmp_path, monkeypatch):
    from datetime import datetime
    from src.scheduler.service import SchedulerService
    from src.scheduler.store import ScheduleStore
    from tests.database_migration_helpers import migrated_profile_database

    web, (owner, other) = context_store
    saved_memory = web.memory_add(owner, "所有任务使用合成调度第一版")
    web.memory_add(other, "所有任务使用其他账号调度偏好")
    schedules = ScheduleStore(str(migrated_profile_database(tmp_path / "scheduler.db", profile="scheduler")))
    with execution_context(web.capture_account_execution(owner)):
        task_id = schedules.add(user_input="合成调度", provider="synthetic", model="frozen-model",
            trigger_type="once", cron_expr=None, run_at=datetime(2026, 1, 1), next_run_at=datetime(2026, 1, 1), owner_user_id=owner)
    observed = []

    async def model(messages, **kwargs):
        assert kwargs["provider"] == "synthetic" and kwargs["model"] == "frozen-model"
        return json.dumps({"need_clarification": True, "question": "合成追问"})

    async def runner(user_input, **kwargs):
        assert kwargs["approved_db_write"] is False and kwargs["ignore_schedule"] is True
        result = await host.run_conductor(user_input, **kwargs)
        observed.append(result)
        return result

    monkeypatch.setattr(intent, "achat", model)
    monkeypatch.setattr(host, "_GRAPH", None)
    service = SchedulerService(schedules, runner=runner)
    with execution_context(web.capture_account_execution(owner)):
        assert asyncio.run(service.run_task_now(task_id)) == "started"
        web.memory_delete(owner, saved_memory["id"])
        web.memory_add(owner, "所有任务使用合成调度第二版")
        assert asyncio.run(service.run_task_now(task_id)) == "started"
    first, second = [result["task_context_preview"] for result in observed]
    assert "合成调度第一版" in first["compiled_context"]["content"]
    assert "合成调度第二版" not in first["compiled_context"]["content"]
    assert "合成调度第二版" in second["compiled_context"]["content"]
    assert "合成调度第一版" not in second["compiled_context"]["content"]
    assert all(snapshot["owner_id"] == owner and "其他账号调度偏好" not in snapshot["compiled_context"]["content"] for snapshot in (first, second))
    assert observed[0]["task_id"] != observed[1]["task_id"]
