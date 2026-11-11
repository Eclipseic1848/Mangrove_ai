"""运行中前置状态只使用固定标记，不读取活 JSON 或等待采集结束。"""
import asyncio
import sys
from types import SimpleNamespace

import pytest

from src.collectors import social_media_collector as collector
from src.conductor import bank_benefits, collection_results
from tests.test_collection_login_feedback import isolated_collection
from scripts import mediacrawler_xhs


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [None, 3])
async def test_wait_reports_changed_stage_before_exit_without_reading_live_json(tmp_path, monkeypatch, timeout):
    released = asyncio.Event()
    seen = []

    async def communicate(**kwargs):
        await released.wait()

    def no_json(*args):
        pytest.fail("等待阶段不能读取正在原子替换的诊断 JSON")

    monkeypatch.setattr(collector, "_runtime_diagnostic", no_json)
    (tmp_path / "startup-stage-new_page").touch()
    (tmp_path / "startup-stage-unknown-secret").touch()
    with collector.collection_diagnostics(seen.append):
        waiting = asyncio.create_task(collector._wait_for_xhs_startup(
            SimpleNamespace(communicate=communicate), b"synthetic", tmp_path, timeout))
        try:
            async with asyncio.timeout(3):
                while not seen:
                    await asyncio.sleep(.02)
                assert not waiting.done()
                assert seen[0]["startup_steps"]["new_page"]["status"] == "started"
                assert seen[0]["search_started"] is False
                await asyncio.sleep(.55)
                assert len(seen) == 1, "相同前置步骤不能反复刷进度"
                (tmp_path / "startup-finished").touch()
                await asyncio.sleep(.3)
                (tmp_path / "startup-stage-search").touch()
                while len(seen) < 2:
                    await asyncio.sleep(.02)
                assert seen[-1]["search_started"] is True
                assert seen[-1]["runtime_phase"] == "search"
                assert "unknown-secret" not in str(seen)
                released.set()
                assert await waiting is False
        finally:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)


@pytest.mark.asyncio
async def test_real_startup_wrapper_marks_pending_page_and_cancellation(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "config", SimpleNamespace(LOGIN_TYPE="cookie", COOKIES="synthetic"))
    monkeypatch.setenv("MANGROVE_XHS_PARENT_REAPS_STARTUP", "1")
    monkeypatch.setenv("MANGROVE_XHS_UNLIMITED_EXECUTION", "1")

    async def seed(*args):
        pass

    class Context:
        async def add_init_script(self):
            pass

        async def new_page(self):
            await asyncio.Event().wait()

    class Crawler:
        index_url = "https://synthetic.invalid"

        async def launch_browser(self):
            return Context()

        async def create_xhs_client(self):
            return object()

    monkeypatch.setattr(mediacrawler_xhs, "seed_xhs_cookies", seed)
    core = SimpleNamespace(XiaoHongShuCrawler=Crawler, XiaoHongShuClient=type("Client", (), {}), async_playwright=object())
    report, state = mediacrawler_xhs._install_runtime_diagnostics(core, tmp_path)
    context = await Crawler().launch_browser()
    await context.add_init_script()
    waiting = asyncio.create_task(context.new_page())
    try:
        async with asyncio.timeout(2):
            while not (tmp_path / "startup-stage-new_page").exists():
                await asyncio.sleep(.01)
        assert (tmp_path / "startup-stage-init_script").exists()
        assert not (tmp_path / "startup-stage-search").exists()
        assert state["search_started"] is False
    finally:
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
    assert state["startup_steps"]["new_page"]["status"] == "cancelled"
    report(search_started=True, runtime_phase="search")
    assert (tmp_path / "startup-stage-search").exists()


@pytest.mark.parametrize("stage,expected", [
    ("init_script", "初始化采集浏览器"),
    ("new_page", "创建采集页面"),
    ("homepage_navigation", "打开小红书首页"),
    ("create_client", "初始化小红书采集客户端"),
])
def test_running_summary_uses_fixed_stage_without_raw_details(stage, expected):
    diagnostic = {"search_started": False, "startup_steps": {
        stage: {"status": "started", "raw_error": "synthetic-secret"},
        "synthetic-secret": {"status": "started"}}}
    summary = collection_results.collection_running_explanation(diagnostic)
    assert expected in summary and "搜索尚未开始" in summary
    assert "synthetic-secret" not in summary
    assert collection_results.collection_running_explanation({"runtime_phase": "synthetic-secret"}) is None


def test_workbench_reports_presearch_step_before_collector_returns(monkeypatch, isolated_collection):
    state, progress = isolated_collection

    async def collect(self, spec):
        sink = collector._DIAGNOSTIC_SINK.get()
        sink({"runtime_phase": "browser_ready", "search_started": False,
              "startup_steps": {"new_page": {"status": "started"}}})
        assert "创建采集页面" in progress[-1]
        assert "搜索尚未开始" in progress[-1]
        assert not any("正在检索第" in item for item in progress)
        sink({"runtime_phase": "search", "search_started": True})
        assert "正在检索第 1 批候选" in progress[-1]
        raise asyncio.CancelledError()

    monkeypatch.setattr(collector.SocialMediaCollector, "collect", collect)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(bank_benefits.collect_bank_benefits(state))
