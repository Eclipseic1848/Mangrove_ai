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
