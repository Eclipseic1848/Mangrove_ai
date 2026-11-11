"""执行已配置采集器的真实启动方法及客户端，浏览器和 HTTP 使用离线替身。"""
import ast
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
import json
import sys
from types import ModuleType, SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest

from scripts import mediacrawler_identity, mediacrawler_xhs
from tests.test_xhs_cookie_refresh import Browser, ROOT, cookie, xhs


def actual_class(path, name, methods, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)
    cls.bases, cls.decorator_list = [], []
    cls.body = [node for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in methods]
    assert {node.name for node in cls.body} == set(methods)
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), cls], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[name]


@pytest.mark.asyncio
@pytest.mark.parametrize("entry,outcome", [("collection", "ok"), ("identity", "ok"),
    ("collection", 401), ("collection", 461), ("collection", "unknown"), ("collection", "cancel"),
    ("identity", 401), ("identity", 461), ("identity", "unknown"), ("identity", "cancel")])
async def test_actual_startup_uses_selected_fields_and_never_replays_on_failure(xhs, monkeypatch, tmp_path, entry, outcome):
    module, build, signs, made = xhs
    seen, navigations = [], []

    class Context(Browser):
        closed = False

        async def add_cookies(self, rows):
            converted = []
            for row in rows:
                converted.append(cookie(row["value"], name=row["name"], domain=urlsplit(row["url"]).hostname)
                                 if "url" in row else row)
            await super().add_cookies(converted)

        async def add_init_script(self, **kwargs):
            pass

        async def new_page(self):
            async def goto(*args, **kwargs):
                navigations.append({row["name"]: row["value"] for row in self.rows})
            return SimpleNamespace(context=self, goto=goto)

        async def close(self):
            self.closed = True

    context = Context([])

    def respond(request):
        seen.append(request)
        if outcome == "cancel":
            raise asyncio.CancelledError()
        if isinstance(outcome, int):
            return httpx.Response(outcome)
        if outcome == "unknown":
            return httpx.Response(200, json={"unexpected": True})
        return httpx.Response(200, json={"success": True, "data": {
            "result": {"success": True}, "guest": False, "user_id": "synthetic-account"}}, headers=[
                ("Set-Cookie", "web_session=rotated==; Domain=.xiaohongshu.com; Path=/; Secure"),
                ("Set-Cookie", "webId=; Domain=.xiaohongshu.com; Path=/; Max-Age=0"),
        ])

    # 复用现有 HTTP 替身工厂；受测客户端由真实 create_xhs_client 创建。
    build(respond, context)
    config = sys.modules["config"]
    for key, value in dict(LOGIN_TYPE="cookie", COOKIES="web_session=synthetic-selected; a1=synthetic-device; webId=synthetic-web; token=value==",
                           ENABLE_IP_PROXY=False, ENABLE_CDP_MODE=False, SAVE_LOGIN_STATE=False,
                           HEADLESS=True, CRAWLER_TYPE="search").items():
        monkeypatch.setattr(config, key, value, raising=False)

    async def new_context(**kwargs):
        return context

    async def launch(**kwargs):
        return SimpleNamespace(new_context=new_context)

    @asynccontextmanager
    async def fake_playwright():
        yield SimpleNamespace(chromium=SimpleNamespace(launch=launch))

    core = ModuleType("media_platform.xhs.core")
    core.__dict__.update(config=config, utils=module.utils, async_playwright=fake_playwright,
                         crawler_type_var=ContextVar("synthetic_crawler_type"), XiaoHongShuClient=module.XiaoHongShuClient)
    actual_class(ROOT / "media_platform/xhs/login.py", "XiaoHongShuLogin",
                 {"__init__", "begin", "login_by_cookies"}, core.__dict__)
    crawler = actual_class(ROOT / "media_platform/xhs/core.py", "XiaoHongShuCrawler",
                           {"__init__", "start", "launch_browser", "create_xhs_client"}, core.__dict__)

    async def search(self):
        await self.xhs_client.get_note_by_keyword(keyword="合成", page=1)
    crawler.search = search
    if entry == "identity":
        monkeypatch.setitem(sys.modules, core.__name__, core)
        import playwright.async_api
        monkeypatch.setattr(playwright.async_api, "async_playwright", fake_playwright)
        mediacrawler_identity.install_probe("xhs", tmp_path / "identity.json")
    else:
        mediacrawler_xhs._install_runtime_diagnostics(core, tmp_path)

    if outcome == "ok" or (entry == "identity" and outcome != "cancel"):
        await crawler().start()
    else:
        with pytest.raises(asyncio.CancelledError if outcome == "cancel" else RuntimeError):
            await crawler().start()

    assert navigations == [{"web_session": "synthetic-selected", "a1": "synthetic-device", "webId": "synthetic-web", "token": "value=="}]
    assert all(field in seen[0].headers["Cookie"] for field in (
        "web_session=synthetic-selected", "a1=synthetic-device", "webId=synthetic-web", "token=value=="))
    assert len(seen) == (2 if entry == "collection" and outcome == "ok" else 1)
    if entry == "collection" and outcome == "ok":
        assert "web_session=rotated==" in seen[1].headers["Cookie"]
        assert "webId=" not in seen[1].headers["Cookie"]
    if entry == "identity":
        if outcome == "cancel":
            assert not (tmp_path / "identity.json").exists()
        else:
            assert json.loads((tmp_path / "identity.json").read_text(encoding="utf-8"))["status"] == ("valid" if outcome == "ok" else "unknown")
        assert context.closed
    assert [call["cookie_str"] for call in signs] == [request.headers["Cookie"] for request in seen]
    assert all(client.is_closed for client in made)

@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["init_script", "new_page", "homepage_navigation", "create_client", "shutdown_after_timeout"])
async def test_startup_timeout_records_step_before_bounded_driver_exit(monkeypatch, tmp_path, step):
    monkeypatch.delenv("MANGROVE_XHS_UNLIMITED_EXECUTION", raising=False)
    config = SimpleNamespace(LOGIN_TYPE="cookie", COOKIES="web_session=synthetic",
                             ENABLE_IP_PROXY=False, ENABLE_CDP_MODE=False, HEADLESS=True)
    monkeypatch.setitem(sys.modules, "config", config)
    monkeypatch.setattr(mediacrawler_xhs, "STARTUP_TIMEOUT_SECONDS", 0.15, raising=False)
    monkeypatch.setattr(mediacrawler_xhs, "DRIVER_SHUTDOWN_TIMEOUT_SECONDS", 0.02, raising=False)
    diagnostic = tmp_path / "runtime_diagnostic.json"

    async def stall_at(name):
        if name == step:
            await asyncio.Event().wait()

    class Context:
        async def add_cookies(self, rows):
            pass

        async def add_init_script(self, **kwargs):
            await stall_at("init_script")

        async def new_page(self):
            await stall_at("new_page")

            async def goto(url, *, wait_until, timeout):
                assert wait_until == "domcontentloaded" and timeout == 60000
                if step == "shutdown_after_timeout":
                    raise TimeoutError("合成首页导航超时")
                await stall_at("homepage_navigation")

            return SimpleNamespace(goto=goto)

    @asynccontextmanager
    async def fake_playwright():
        try:
            yield SimpleNamespace(chromium=object())
        finally:
            if step == "shutdown_after_timeout":
                saved = json.loads(diagnostic.read_text(encoding="utf-8"))
                assert saved["startup_steps"]["homepage_navigation"]["status"] == "timeout"
                await asyncio.Event().wait()

    class Client:
        async def pong(self):
            pytest.fail("前置步骤失败后不得继续登录校验")

    core = ModuleType("media_platform.xhs.core")
    core.__dict__.update(config=config, async_playwright=fake_playwright, XiaoHongShuClient=Client,
                         utils=SimpleNamespace(logger=SimpleNamespace(info=lambda *_: None)))
    crawler = actual_class(ROOT / "media_platform/xhs/core.py", "XiaoHongShuCrawler", {"start"}, core.__dict__)

    async def launch(self, *args, **kwargs):
        return Context()

    async def create_client(self, *args):
        await stall_at("create_client")
        return Client()

    crawler.index_url = "https://www.xiaohongshu.com"
    crawler.user_agent = "synthetic"
    crawler.launch_browser = launch
    crawler.create_xhs_client = create_client
    mediacrawler_xhs._install_runtime_diagnostics(core, tmp_path)

    with pytest.raises(TimeoutError) as raised:
        # 外层仅保护离线测试；正常应由脚本的较短阶段预算先退出。
        async with asyncio.timeout(2):
            await crawler().start()
    saved = json.loads(diagnostic.read_text(encoding="utf-8"))
    assert saved["search_started"] is False
    failed_step = "homepage_navigation" if step == "shutdown_after_timeout" else step
    assert saved["startup_steps"][failed_step]["status"] == "timeout"
    assert saved["startup_steps"]["driver_shutdown"]["status"] == (
        "timeout" if step == "shutdown_after_timeout" else "completed")
    if step == "shutdown_after_timeout":
        assert str(raised.value) == "合成首页导航超时"
    assert "synthetic" not in diagnostic.read_text(encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "cancel", "navigation_error", "search_error", "parent_reap"])
async def test_unlimited_startup_keeps_diagnostics_cancellation_and_bounded_exit(monkeypatch, tmp_path, outcome):
    monkeypatch.setenv("MANGROVE_XHS_UNLIMITED_EXECUTION", "1")
    monkeypatch.delenv("MANGROVE_XHS_PARENT_REAPS_STARTUP", raising=False)
    if outcome == "parent_reap":
        monkeypatch.setenv("MANGROVE_XHS_PARENT_REAPS_STARTUP", "1")
    monkeypatch.setattr(mediacrawler_xhs, "STARTUP_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(mediacrawler_xhs, "HOMEPAGE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(mediacrawler_xhs, "DRIVER_SHUTDOWN_TIMEOUT_SECONDS", 0.01)
    config = SimpleNamespace(LOGIN_TYPE="cookie", COOKIES="web_session=synthetic",
                             ENABLE_IP_PROXY=False, ENABLE_CDP_MODE=False, HEADLESS=True, CRAWLER_TYPE="search")
    monkeypatch.setitem(sys.modules, "config", config)
    diagnostic = tmp_path / "runtime_diagnostic.json"
    navigation_started = asyncio.Event()
    searches = []

    class Context:
        async def add_cookies(self, rows):
            pass

        async def add_init_script(self, **kwargs):
            await asyncio.sleep(0.03)

        async def new_page(self):
            await asyncio.sleep(0.03)

            async def goto(url, *, wait_until, timeout):
                assert wait_until == "domcontentloaded" and timeout == 0
                navigation_started.set()
                if outcome == "cancel":
                    await asyncio.Event().wait()
                await asyncio.sleep(0.03)
                if outcome == "navigation_error":
                    raise RuntimeError("合成首页故障")

            return SimpleNamespace(goto=goto)

    @asynccontextmanager
    async def fake_playwright():
        try:
            yield SimpleNamespace(chromium=object())
        finally:
            if outcome in {"navigation_error", "search_error", "parent_reap"}:
                await asyncio.Event().wait()

    class Client:
        async def pong(self):
            return True

    core = ModuleType("media_platform.xhs.core")
    core.__dict__.update(config=config, async_playwright=fake_playwright, XiaoHongShuClient=Client,
                         crawler_type_var=ContextVar("synthetic_crawler_type"),
                         utils=SimpleNamespace(logger=SimpleNamespace(info=lambda *_: None)))
    crawler = actual_class(ROOT / "media_platform/xhs/core.py", "XiaoHongShuCrawler", {"start"}, core.__dict__)

    async def launch(self, *args, **kwargs):
        return Context()

    async def create_client(self, *args):
        await asyncio.sleep(0.03)
        return Client()

    async def search(self):
        searches.append(True)
        if outcome in {"search_error", "parent_reap"}:
            raise RuntimeError("合成搜索故障")

    crawler.index_url = "https://www.xiaohongshu.com"
    crawler.user_agent = "synthetic"
    crawler.launch_browser = launch
    crawler.create_xhs_client = create_client
    crawler.search = search
    mediacrawler_xhs._install_runtime_diagnostics(core, tmp_path)
    task = asyncio.create_task(crawler().start())
    try:
        async with asyncio.timeout(2):
            if outcome == "cancel":
                await navigation_started.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif outcome == "parent_reap":
                # 父进程必须在前置完成后仍能收到退出回收请求。
                while not (tmp_path / "startup-reap-requested").exists():
                    await asyncio.sleep(0.005)
                assert (tmp_path / "startup-finished").exists()
                assert not task.done()
            elif outcome in {"navigation_error", "search_error"}:
                with pytest.raises(RuntimeError, match="合成首页故障" if outcome == "navigation_error" else "合成搜索故障"):
                    await task
            else:
                await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    saved = json.loads(diagnostic.read_text(encoding="utf-8"))
    assert saved["startup_steps"]["init_script"]["status"] == "completed"
    assert saved["startup_steps"]["new_page"]["status"] == "completed"
    assert saved["startup_steps"]["homepage_navigation"]["status"] == (
        "cancelled" if outcome == "cancel" else "failed" if outcome == "navigation_error" else "completed")
    assert saved["startup_steps"]["driver_shutdown"]["status"] == (
        "timeout" if outcome in {"navigation_error", "search_error", "parent_reap"} else "completed")
    assert searches == ([] if outcome in {"cancel", "navigation_error"} else [True])
    assert "synthetic" not in diagnostic.read_text(encoding="utf-8")


@pytest.mark.parametrize("unlimited", [False, True])
def test_comment_entry_only_removes_time_budget_for_unlimited_mode(monkeypatch, tmp_path, unlimited):
    if unlimited:
        monkeypatch.setenv("MANGROVE_XHS_UNLIMITED_EXECUTION", "1")
    else:
        monkeypatch.delenv("MANGROVE_XHS_UNLIMITED_EXECUTION", raising=False)
    monkeypatch.setenv("MANGROVE_XHS_TIMEOUT_SECONDS", "None" if unlimited else "100")
    monkeypatch.setattr(mediacrawler_xhs, "time", SimpleNamespace(monotonic=lambda: 100.0))
    monkeypatch.setattr(sys, "argv", ["wrapper", "--save_data_path", str(tmp_path), "--crawler_max_notes_count", "3"])
    monkeypatch.setattr(sys, "path", list(sys.path))
    config = SimpleNamespace(CRAWLER_MAX_NOTES_COUNT=3)
    monkeypatch.setitem(sys.modules, "config", config)
    platform = ModuleType("media_platform")
    platform.__path__ = []
    xhs_package = ModuleType("media_platform.xhs")
    xhs_package.__path__ = []
    core = ModuleType("media_platform.xhs.core")

    class Client:
        async def get_note_by_keyword(self, **kwargs):
            pytest.fail("评论入口测试不得触发搜索")

    core.XiaoHongShuClient = Client
    xhs_package.core = core
    platform.xhs = xhs_package
    writer = ModuleType("tools.async_file_writer")
    writer.AsyncFileWriter = type("AsyncFileWriter", (), {})
    for name, module in (("media_platform", platform), ("media_platform.xhs", xhs_package),
                         ("media_platform.xhs.core", core), ("tools.async_file_writer", writer)):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(mediacrawler_xhs, "_install_runtime_diagnostics", lambda *_: (lambda **_: None, {}))
    calls = []

    async def comments(client, note_id, token, interval, callback, max_count, *, deadline):
        calls.append((note_id, interval, max_count, deadline))
        return [{"id": "synthetic-comment"}], {"top_level_count": 1}

    def run_path(*args, **kwargs):
        rows = asyncio.run(Client().get_note_all_comments("synthetic-note", "synthetic-token", 0.4, max_count=2))
        assert rows == [{"id": "synthetic-comment"}]

    monkeypatch.setattr(mediacrawler_xhs, "bounded_comments", comments)
    monkeypatch.setattr(mediacrawler_xhs.runpy, "run_path", run_path)
    mediacrawler_xhs.main()
    assert len(calls) == 1 and calls[0][:3] == ("synthetic-note", 0.4, 2)
    assert calls[0][3] == (None if unlimited else pytest.approx(129.6))
    assert json.loads((tmp_path / "comment_coverage.json").read_text(encoding="utf-8")) == {
        "synthetic-note": {"top_level_count": 1}}


@pytest.mark.asyncio
async def test_unlimited_comments_propagate_source_timeout():
    class Client:
        async def get_note_comments(self, **kwargs):
            raise asyncio.TimeoutError("合成来源超时")

    with pytest.raises(asyncio.TimeoutError, match="合成来源超时"):
        await mediacrawler_xhs.bounded_comments(Client(), "note", "token", 0, None, 2, deadline=None)
