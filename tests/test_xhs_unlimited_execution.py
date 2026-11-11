"""不限执行时长仍保留冻结兼容、数量额度、来源失败和所属进程取消。"""
import asyncio
import hashlib
import json

import pytest

from src.collectors.base import CollectedItem, CollectResult
from src.collectors.social_media_collector import SocialMediaCollector
from src.config.settings import settings
from src.config.user_ctx import user_overrides_context
from src.conductor import evidence_collection as flow
from src.conductor.nodes import collect as collect_node_module, planner
from src.conductor.task_spec import TaskSpec
from tests import test_xhs_browser_session_unittest as session_fixture


def unlimited_spec(**scope):
    return TaskSpec(intent="采集小红书会员权益", platforms=["小红书"],
        xhs_unlimited_execution=True, evidence_collection={
            "source_evidence": "小红书", "queries": [{"name": "会员", "keyword": "会员权益"}],
            "fields": ["name"], "target_count": 1, "include_images": False,
            "time_budget_seconds": None, **scope})


def test_historical_frozen_json_and_digest_do_not_acquire_unlimited_mode():
    historical = TaskSpec(intent="旧小红书任务", platforms=["小红书"], evidence_collection={
        "source_evidence": "小红书", "queries": [{"name": "旧范围", "keyword": "旧范围"}],
        "fields": ["name"], "frozen_at": "2026-11-01T00:00:00+08:00"}).model_dump(mode="json")
    assert "xhs_unlimited_execution" not in historical
    assert historical["evidence_collection"]["time_budget_seconds"] == 1800
    original = json.dumps(historical, sort_keys=True, ensure_ascii=False).encode("utf-8")
    loaded = TaskSpec.model_validate(historical)
    restored = json.dumps(loaded.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode("utf-8")
    assert not loaded.xhs_unlimited_execution
    assert hashlib.sha256(restored).digest() == hashlib.sha256(original).digest()


@pytest.mark.parametrize("changes", [
    {"platforms": ["微博"]},
    {"platforms": ["小红书", "微博"]},
    {"time_budget_seconds": 120},
    {"xhs_unlimited_execution": "true"},
    {"xhs_unlimited_execution": False},
])
def test_conflicting_or_untyped_unlimited_contract_is_rejected(changes):
    payload = unlimited_spec().model_dump(mode="json")
    with pytest.raises(ValueError):
        TaskSpec.model_validate({**payload, **changes})


def test_unlimited_contract_cannot_keep_a_finite_collection_budget():
    payload = unlimited_spec().model_dump(mode="json")
    payload["evidence_collection"]["time_budget_seconds"] = 1800
    with pytest.raises(ValueError):
        TaskSpec.model_validate(payload)


@pytest.mark.parametrize("profile", [None, "evidence_collection", "bank_benefits"])
def test_new_xhs_plans_freeze_unlimited_mode_instead_of_model_default(monkeypatch, profile):
    draft = {"intent": "采集小红书会员权益5条", "platforms": ["小红书"], "max_items": 5,
             "time_budget_seconds": 1800}
    if profile:
        draft[profile] = {"source_evidence": "小红书", "time_budget_seconds": 1800,
            "banks" if profile == "bank_benefits" else "queries": [{"name": "会员", "keyword": "会员权益"}]}
        if profile == "evidence_collection":
            draft[profile]["fields"] = ["name"]

    async def model(*args):
        return draft, None

    monkeypatch.setattr(planner, "_plan", model)
    monkeypatch.setattr(planner, "lesson_for_planner", lambda *args, **kwargs: "")
    result = asyncio.run(planner.planner_node({"user_input": "采集小红书会员权益5条"}))
    spec = result["task_spec"]
    frozen = spec.model_dump(mode="json")
    assert frozen["xhs_unlimited_execution"] is True
    assert spec.time_budget_seconds is None
    assert TaskSpec.model_validate(frozen).model_dump(mode="json") == frozen
    if profile:
        assert frozen[profile]["time_budget_seconds"] is None
        assert frozen[profile]["candidate_limit"] == 100
        assert frozen[profile]["ocr_limit"] == 20


def test_unlimited_evidence_survives_old_stage_deadlines_but_stops_at_candidate_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    monkeypatch.setattr(flow, "execution_owner", lambda: "synthetic-owner")
    monkeypatch.setattr(flow, "STEP_TIMEOUT_SECONDS", {key: .001 for key in flow.STEP_TIMEOUT_SECONDS})
    calls = []

    async def collect(self, request):
        assert request.xhs_unlimited_execution
        assert request.bank_benefits is request.evidence_collection is None
        calls.append(request.max_items)
        await asyncio.sleep(.03)
        return CollectResult(True, "synthetic", items=[CollectedItem(content="不相关资料", metadata={
            "note_id": f"{len(calls)}-{i}", "note_type": "normal"}) for i in range(request.max_items)],
            coverage={"pages": [{"has_more": True}]})

    async def assess(*args):
        await asyncio.sleep(.03)
        return {"relevant": False}

    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    spec = unlimited_spec(initial_candidates=2, candidate_limit=4)
    result = asyncio.run(flow.collect_evidence({"task_id": "unlimited-candidates", "task_spec": spec},
        scope=spec.evidence_collection, queries=spec.evidence_collection.queries,
        assess=assess, exclude=flow.exclude_topic, read_images=None))
    group = result["evidence_collection"]["groups"][0]
    assert calls == [2, 2]
    assert group["candidate_attempted"] == 4 and group["stop_reason"] == "candidate_limit"
    assert len(result["evidence_collection"]["candidates"]) == 4


def test_unlimited_evidence_still_stops_on_confirmed_source_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    calls = []

    async def collect(self, request):
        calls.append(request.xhs_search_page)
        return CollectResult(False, "synthetic", message="来源拒绝", coverage={
            "failure": {"reason": "platform_challenge", "rejection_confirmed": True}})

    async def assess(*args):
        pytest.fail("来源失败后不得调用模型")

    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    spec = unlimited_spec()
    result = asyncio.run(flow.collect_evidence({"task_id": "unlimited-refused", "task_spec": spec},
        scope=spec.evidence_collection, queries=spec.evidence_collection.queries,
        assess=assess, exclude=flow.exclude_topic, read_images=None))
    assert calls == [1]
    assert result["evidence_collection"]["groups"][0]["stop_reason"] == "collection_failed"
    assert not result["collection_outcome"]["ready_for_review"]


def test_unlimited_execution_keeps_twenty_image_budget(tmp_path, monkeypatch):
    from tests.test_collection_image_budget import image_flow
    from src.conductor import bank_benefits

    state, calls = image_flow(monkeypatch, tmp_path)
    payload = state["task_spec"].model_dump(mode="json")
    payload.update(platforms=["小红书"], xhs_unlimited_execution=True)
    payload["bank_benefits"]["time_budget_seconds"] = None
    state["task_spec"] = TaskSpec.model_validate(payload)
    result = asyncio.run(bank_benefits.collect_bank_benefits(state))
    assert len(calls["download"]) == len(calls["ocr"]) == 20
    assert result["bank_collection"]["banks"][0]["stop_reason"] == "ocr_limit"


def test_service_timeout_keeps_selected_data_without_budget_error_or_further_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    calls = []

    async def collect(self, request):
        calls.append((request.keywords, request.xhs_search_page))
        if len(calls) == 2:
            raise TimeoutError("合成服务请求超时")
        return CollectResult(True, "synthetic", items=[CollectedItem(content="已取得的会员权益", metadata={
            "note_id": "retained", "note_type": "normal"})], coverage={"pages": [{"has_more": True}]})

    async def assess(*args):
        return {"relevant": True, "records": []}

    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    spec = unlimited_spec(target_count=2, initial_candidates=1, candidate_limit=3,
        queries=[{"name": "会员", "keyword": "会员权益"}, {"name": "后续组", "keyword": "不得请求"}])
    result = asyncio.run(flow.collect_evidence({"task_id": "unlimited-service-timeout", "task_spec": spec},
        scope=spec.evidence_collection, queries=spec.evidence_collection.queries,
        assess=assess, exclude=flow.exclude_topic, read_images=None))
    snapshot = result["evidence_collection"]
    assert calls == [(["会员权益"], 1), (["会员权益"], 2)]
    assert snapshot["groups"][0]["stop_reason"] == "operation_timeout"
    assert snapshot["candidates"][0]["status"] == "selected"
    assert result["collection_outcome"]["status"] == "incomplete"
    assert not result["collection_outcome"]["ready_for_review"]
    assert "服务响应超时" in result["collector_notes"][0]
    assert "时间预算" not in result["collector_notes"][0]


def test_generic_xhs_entry_does_not_reintroduce_collector_deadline(monkeypatch):
    class Collector:
        async def collect(self, spec):
            await asyncio.sleep(.03)
            return CollectResult(True, "mediacrawler", items=[CollectedItem(content="合成正文")])

    monkeypatch.setattr(collect_node_module, "get_registry", lambda: {"mediacrawler": Collector()})
    monkeypatch.setattr(collect_node_module, "metrics_record", lambda *args: None)
    monkeypatch.setattr(collect_node_module, "domain_health_record", lambda *args: None)
    monkeypatch.setattr(settings, "collect_timeout_mediacrawler_seconds", .001)
    spec = TaskSpec(intent="小红书", platforms=["小红书"], max_items=1, xhs_unlimited_execution=True)
    result = asyncio.run(collect_node_module.collect_node({"task_spec": spec, "collector_candidates": ["mediacrawler"]}))
    assert result["raw_dataset"][0]["content"] == "合成正文"


@pytest.mark.parametrize("platform,enabled,expected", [("小红书", True, True), ("小红书", False, False), ("微博", True, False)])
def test_web_adapter_only_uses_explicit_frozen_mode_for_xhs(platform, enabled, expected):
    from src.connectors.web_adapter import _v2_to_v1_spec
    from src.data_prep.models import SourceSpec

    source = SourceSpec(source_id="synthetic", source_type="web", locator="关键词")
    spec = _v2_to_v1_spec(source, {"platforms": [platform], "keywords": ["会员权益"], "xhs_unlimited_execution": enabled})
    assert spec.xhs_unlimited_execution is expected
    assert ("xhs_unlimited_execution" in spec.model_dump(mode="json")) is expected


@pytest.fixture
def collector_fixture():
    fixture = session_fixture.BrowserSessionTests(methodName="runTest")
    fixture.setUp()
    try:
        yield fixture
    finally:
        fixture.doCleanups()


@pytest.mark.parametrize("cancel", [False, True])
def test_real_offline_child_exceeds_old_limit_and_remains_cancellable(collector_fixture, monkeypatch, cancel):
    root = collector_fixture.root
    marker = root / "ready"
    release = root / "release"
    main = root / "main.py"
    original = main.read_text(encoding="utf-8")
    main.write_text("import os,time\nfrom pathlib import Path\n"
        "assert os.environ.get('MANGROVE_XHS_UNLIMITED_EXECUTION') == '1'\n"
        f"Path({str(marker)!r}).write_text('ready',encoding='utf-8')\n"
        f"while not Path({str(release)!r}).exists(): time.sleep(.01)\n" + original, encoding="utf-8")
    monkeypatch.setattr(settings, "collect_timeout_mediacrawler_seconds", .1)
    monkeypatch.setenv("EXPECT_BROWSER", "no")
    created = []
    launch = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        process = await launch(*args, **kwargs)
        created.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)

    async def run():
        with user_overrides_context({"mc_cookie_xhs": "synthetic-cookie", "mc_xhs_login_mode": "cookie"}):
            spec = TaskSpec(intent="离线不限时", platforms=["小红书"], keywords=["agent"], max_items=1,
                xhs_unlimited_execution=True)
            task = asyncio.create_task(SocialMediaCollector().collect(spec))
            try:
                async with asyncio.timeout(10):
                    while not marker.exists():
                        if task.done():
                            pytest.fail(f"子进程尚未就绪即结束：{await task}")
                        await asyncio.sleep(.02)
                    await asyncio.sleep(.2)
                    assert not task.done(), "仍受旧采集进程时限约束"
                    if cancel:
                        task.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await task
                    else:
                        release.write_text("done", encoding="utf-8")
                        result = await task
                        assert result.success, result.message
                        assert len(result.items) == 1
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        assert len(created) == 1 and created[0].returncode is not None

    asyncio.run(run())


def test_identity_probe_clears_inherited_unlimited_mode_and_keeps_its_deadline(collector_fixture, monkeypatch):
    root = collector_fixture.root
    (root / "media_platform/xhs/core.py").write_text("class XiaoHongShuCrawler: pass\n", encoding="utf-8")
    (root / "main.py").write_text("import time\nwhile True: time.sleep(.01)\n", encoding="utf-8")
    monkeypatch.setenv("MANGROVE_XHS_UNLIMITED_EXECUTION", "1")
    monkeypatch.setattr(settings, "collect_timeout_mediacrawler_seconds", .2)
    created = []
    launch = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        assert "MANGROVE_XHS_UNLIMITED_EXECUTION" not in kwargs["env"]
        process = await launch(*args, **kwargs)
        created.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)

    async def run():
        async with asyncio.timeout(10):
            result = await SocialMediaCollector().collect(TaskSpec(intent="身份验证", platforms=["小红书"],
                keywords=["agent"], xhs_unlimited_execution=True), identity_probe=True)
        assert not result.success
        assert result.coverage["failure"]["reason"] == "collector_timeout"
        assert len(created) == 1 and created[0].returncode is not None

    asyncio.run(run())


def test_unlimited_profile_shutdown_reaps_driver_before_parent_exits(collector_fixture, monkeypatch):
    import os
    from pathlib import Path
    import subprocess
    import time
    from tests.test_xhs_login_diagnostics import LoginDiagnosticTests

    LoginDiagnosticTests.install_stuck_driver(collector_fixture)
    core = collector_fixture.root / "media_platform/xhs/core.py"
    # 红灯时也让合成后代自然退出，不能留下测试进程。
    core.write_text(core.read_text(encoding="utf-8").replace("time.sleep(20)", "time.sleep(12)"), encoding="utf-8")
    monkeypatch.setattr(settings, "collect_timeout_mediacrawler_seconds", .1)
    created = []
    launch = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        process = await launch(*args, **kwargs)
        created.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)

    def alive(pid):
        if os.name == "nt":
            result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                    capture_output=True, timeout=5, check=True)
            return f',"{pid}",'.encode("utf-8") in result.stdout
        try:
            return Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0] != "Z"
        except FileNotFoundError:
            return False

    async def run():
        with user_overrides_context({"mc_xhs_login_mode": "browser"}):
            async with asyncio.timeout(15):
                return await SocialMediaCollector().collect(TaskSpec(intent="合成浏览器退出回归",
                    platforms=["小红书"], keywords=["synthetic"], xhs_unlimited_execution=True))

    result = asyncio.run(run())
    pid = int((collector_fixture.root / "driver-pid").read_text(encoding="utf-8"))
    try:
        assert len(created) == 1 and created[0].returncode is not None
        assert not alive(pid), "采集组长已退出，所属浏览器后代仍存活"
        assert not result.success
        assert result.coverage["runtime"]["termination"] == "startup_cleanup_failed"
        assert result.coverage["runtime"]["startup_steps"]["driver_shutdown"]["status"] == "timeout"
    finally:
        deadline = time.monotonic() + 15
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(.05)
        assert not alive(pid), "合成后代未按自身限时退出"
