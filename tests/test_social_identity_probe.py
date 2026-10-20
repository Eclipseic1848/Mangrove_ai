"""账号接口探针与公开采集分离；合成响应不冒充在线平台验收。"""
import asyncio
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from scripts.mediacrawler_identity import probe_account
from src.collectors.base import CollectResult
from tests.test_kuaishou_identity_current import FakeKuaishouPage


@pytest.mark.parametrize("platform,response,status", [
    ("xhs", {"success": True, "data": {"result": {"success": True}, "basic_info": {"red_id": "synthetic-account"}}}, "valid"),
    ("xhs", {"success": True, "data": {"result": {"success": True}}}, "unknown"),
    ("xhs", None, "unknown"),
    ("xhs", {"success": True, "data": {"result": {"success": True}, "basic_info": {"red_id": True}}}, "unknown"),
    ("bili", {"isLogin": True, "mid": 123}, "valid"),
    ("bili", {"isLogin": False}, "invalid"),
    ("bili", {"isLogin": "true"}, "unknown"),
    ("bili", {"items": [{"title": "公开结果"}]}, "unknown"),
    ("wb", {"login": True, "uid": "synthetic-account"}, "valid"),
    ("wb", {"login": False}, "invalid"),
    ("wb", {}, "unknown"),
    ("zhihu", {"uid": "synthetic-account", "name": "测试"}, "valid"),
    ("zhihu", {"uid": "synthetic-account"}, "unknown"),
    ("zhihu", {"uid": True, "name": "测试"}, "unknown"),
    ("zhihu", {"uid": "synthetic-account", "name": " "}, "unknown"),
    ("dy", {"logged_in": True}, "unknown"),
    ("dy", {"status_code": 0, "user": {"uid": "synthetic-account"}}, "valid"),
    ("dy", {"status_code": 0, "user": {"uid": True}}, "unknown"),
    ("dy", {"status_code": 0, "user": {"uid": "0"}}, "unknown"),
    ("dy", {"status_code": 8, "status_msg": "用户未登录"}, "invalid"),
    ("dy", {"status_code": 8, "status_msg": "其它错误"}, "unknown"),
    ("dy", {"status_code": 2483}, "invalid"),
    ("tieba", {"BDUSS": "present"}, "unknown"),
    ("tieba", {"is_login": 1, "tbs": "private-token"}, "valid"),
    ("tieba", {"is_login": "1", "tbs": "private-token"}, "valid"),
    ("tieba", {"is_login": 1}, "unknown"),
    ("tieba", {"is_login": True, "tbs": "private-token"}, "unknown"),
    ("tieba", {"is_login": 0, "tbs": "anonymous-token"}, "invalid"),
    ("tieba", {"is_login": 1, "tbs": "private-token", "error_code": 0}, "valid"),
    ("tieba", {"is_login": 1, "tbs": "private-token", "no": "0"}, "valid"),
    ("tieba", {"is_login": 1, "tbs": "private-token", "no": 403}, "unknown"),
    ("tieba", {"is_login": 0, "tbs": "private-token", "error_code": True}, "unknown"),
])
def test_account_endpoint_evidence_is_required(platform, response, status):
    calls = []

    async def fetch(*args, **kwargs):
        if platform == "dy":
            assert args == ("/aweme/v1/web/user/profile/self/",) and kwargs == {"params": {"aid": "6383"}}
        calls.append(True)
        return response

    async def browser_get(url, **kwargs):
        assert url == "https://tieba.baidu.com/dc/common/tbs"
        assert kwargs == {"headers": {"Referer": "https://tieba.baidu.com/"}, "timeout": 15000, "max_redirects": 0}
        return SimpleNamespace(status=200, json=fetch)

    client = SimpleNamespace(query_self=fetch, get=fetch, request=fetch,
                             get_current_user_info=fetch, _fetch_json_by_browser=fetch,
                             _host="https://synthetic.invalid", headers={},
                             playwright_page=SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(get=browser_get))))
    if platform == "tieba":
        async def forbidden(*args, **kwargs):
            raise AssertionError("身份探针不得调用可能被页面重渲染阻断的 JS fetch")
        client._fetch_json_by_browser = forbidden
    result = asyncio.run(probe_account(platform, client))
    assert result["status"] == status
    assert "synthetic-account" not in json.dumps(result)
    assert "private-token" not in json.dumps(result)


@pytest.mark.parametrize("http_status,response,status", [
    (200, {"result": 1, "userId": "private-account"}, "valid"),
    (200, {"result": 1, "userId": 123}, "valid"),
    (200, {"data": {"ownerInfo": {"id": "private-account"}}}, "unknown"),
    (200, {"data": {"ownerInfo": {"id": 123}, "kshellBalance": {"result": 1}}}, "unknown"),
    (200, {"data": {"ownerInfo": {}, "kshellBalance": {"result": 6001, "error_msg": "私有正文"}}}, "unknown"),
    (200, {"data": {"ownerInfo": {"id": "private-account"}, "kshellBalance": {"result": 6001}}}, "unknown"),
    (200, {"data": {"ownerInfo": {"id": True}}}, "unknown"),
    (200, {"data": {"ownerInfo": {"id": 0}}}, "unknown"),
    (200, {"data": {"ownerInfo": {"id": "0"}}}, "unknown"),
    (200, {"data": {"ownerInfo": {"id": " "}}}, "unknown"),
    (200, {"data": {"ownerInfo": {"id": "private-account"}}, "errors": ["私有正文"]}, "unknown"),
    (200, {"data": {"ownerInfo": {"id": "private-account"}}, "result": 6001}, "unknown"),
    (200, {"data": {"ownerInfo": {"id": "private-account"}}, "error_code": 1}, "unknown"),
    (200, {"data": {"ownerInfo": {"id": "private-account"}, "error_msg": "私有正文"}}, "unknown"),
    (200, {"data": {"ownerInfo": {"id": "private-account"}, "kshellBalance": {"result": 403}}}, "unknown"),
    (200, {"data": {"ownerInfo": {"id": "private-account"}, "kshellBalance": []}}, "unknown"),
    (200, {"result": 1, "data": {"visionProfileUserList": {"result": 1}}}, "unknown"),
    (200, {"data": []}, "unknown"),
    (302, {"data": {"ownerInfo": {"id": "private-account"}}}, "unknown"),
    (403, {"data": {"ownerInfo": {}, "kshellBalance": {"result": 6001}}}, "unknown"),
    (429, {"data": {"ownerInfo": {}, "kshellBalance": {"result": 6001}}}, "unknown"),
])
def test_kuaishou_uses_current_account_evidence_without_public_collection(http_status, response, status):
    page = FakeKuaishouPage(response, status=http_status)
    client = SimpleNamespace(playwright_page=page)
    result = asyncio.run(probe_account("ks", client))
    assert result["status"] == status and page.events == ["navigation", "account", "page_closed"]
    assert page.closed
    assert all(value not in json.dumps(result, ensure_ascii=False) for value in ("private-account", "私有正文", "ownerInfo", "kshellBalance"))
    if status == "valid":
        account = response["userId"]
        assert result["account_ref"] == hashlib.sha256(f"ks\0{account}".encode("utf-8")).hexdigest()


@pytest.mark.parametrize("http_status", [302, 403, 429])
def test_tieba_challenge_is_not_evidence_of_expired_cookie(http_status):
    async def parse():
        raise AssertionError("HTTP 挑战页不得被当作身份响应")

    async def get(*args, **kwargs):
        return SimpleNamespace(status=http_status, json=parse)

    client = SimpleNamespace(playwright_page=SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(get=get))))
    result = asyncio.run(probe_account("tieba", client))
    assert result["status"] == "unknown"


@pytest.mark.parametrize("platform", ["xhs", "bili", "wb", "dy", "zhihu", "tieba", "ks"])
def test_probe_exception_is_unknown_and_does_not_leak_response(platform):
    async def fail(*args, **kwargs):
        raise RuntimeError("private-cookie-value")

    async def close():
        pass

    client = SimpleNamespace(query_self=fail, get=fail, request=fail,
                             get_current_user_info=fail, _fetch_json_by_browser=fail,
                             _host="https://synthetic.invalid", headers={},
                             playwright_page=SimpleNamespace(route=fail, close=close, context=SimpleNamespace(route=fail, request=SimpleNamespace(get=fail, post=fail))))
    result = asyncio.run(probe_account(platform, client))
    assert result == {"status": "unknown", "reason": "identity_probe_failed"}


def test_probe_cancellation_is_not_swallowed():
    async def cancel():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(probe_account("xhs", SimpleNamespace(query_self=cancel)))


def test_health_route_uses_identity_probe_without_search(monkeypatch):
    from src.api.routes.config_routes import _verify_mc_cookie
    from src.collectors import registry, social_media_collector

    async def verify(spec):
        return CollectResult(True, "mediacrawler", authentication={"status": "valid"})

    async def forbidden(spec):
        raise AssertionError("身份探测不应执行公开搜索")

    collector = SimpleNamespace(is_available=lambda: True, verify_cookie=verify, collect=forbidden)
    monkeypatch.setattr(registry, "get_registry", lambda: SimpleNamespace(get=lambda _: collector))
    monkeypatch.setattr(social_media_collector, "_platform_cookie", lambda _: "synthetic")
    assert "尚未验证搜索" in asyncio.run(_verify_mc_cookie("mc_cookie_xhs"))


@pytest.mark.parametrize("guest,account,status,kind", [
    (False, "immutable-id", "valid", "account_id"),
    (True, "guest-id", "invalid", None),
    ("false", "immutable-id", "valid", "handle"),
    (False, True, "valid", "handle"),
    (False, "", "valid", "handle"),
])
def test_xhs_stable_account_identity_requires_non_guest_and_valid_id(guest, account, status, kind):
    calls = []
    async def current(uri, *, params):
        assert uri == "/api/sns/web/v2/user/me" and params == {}
        calls.append("current")
        return {"guest": guest, "user_id": account, "red_id": "changeable-handle"}
    async def legacy():
        calls.append("legacy")
        return {"success": True, "data": {"result": {"success": True}, "basic_info": {"red_id": "changeable-handle"}}}
    result = asyncio.run(probe_account("xhs", SimpleNamespace(get=current, query_self=legacy)))
    assert result["status"] == status
    assert result.get("account_ref_kind") == kind
    if kind == "account_id":
        assert result["account_ref"] == hashlib.sha256(b"xhs\0immutable-id").hexdigest()
    assert calls == (["current"] if kind == "account_id" or guest is True else ["current", "legacy"])
    assert "immutable-id" not in json.dumps(result) and "changeable-handle" not in json.dumps(result)


def test_xhs_current_identity_cancellation_does_not_fall_back():
    async def current(*args, **kwargs):
        raise asyncio.CancelledError()
    async def forbidden():
        raise AssertionError("取消后不能再调用旧接口")
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(probe_account("xhs", SimpleNamespace(get=current, query_self=forbidden)))


def test_xhs_new_identity_error_preserves_legacy_validation():
    async def current(*args, **kwargs):
        raise RuntimeError("private-upstream-error")
    async def legacy():
        return {"success": True, "data": {"result": {"success": True}, "basic_info": {"red_id": "legacy-handle"}}}
    result = asyncio.run(probe_account("xhs", SimpleNamespace(get=current, query_self=legacy)))
    assert result["status"] == "valid" and result["account_ref_kind"] == "handle"
    assert "private-upstream-error" not in json.dumps(result)


@pytest.mark.parametrize("platform,origin,response", [
    ("wb", "https://m.weibo.cn", {"login": True, "uid": "private-account"}),
    ("dy", "https://www.douyin.com", {"status_code": 0, "user": {"uid": "private-account"}}),
    ("zhihu", "https://www.zhihu.com", {"uid": "private-account", "name": "测试"}),
    ("tieba", "https://tieba.baidu.com", {"is_login": 1, "tbs": "private-token"}),
    ("ks", "https://www.kuaishou.com", {"result": 1, "userId": "private-account"}),
    ("zhihu", "https://www.zhihu.com", asyncio.CancelledError()),
    ("tieba", "https://tieba.baidu.com", asyncio.CancelledError()),
    ("ks", "https://www.kuaishou.com", asyncio.CancelledError()),
])
def test_identity_start_injects_only_supplied_cookie_before_account_navigation(tmp_path, monkeypatch, platform, origin, response):
    from scripts import mediacrawler_identity as module

    calls = []
    startup_calls = (["sockets_blocked", "cookies"] if platform == "ks" else ["cookies", "navigation"]) + (["settled"] if platform == "dy" else [])
    configuration = SimpleNamespace(COOKIES="session=synthetic", SAVE_LOGIN_STATE=True, ENABLE_CDP_MODE=True,
                                    ENABLE_IP_PROXY=False, HEADLESS=True)
    monkeypatch.setitem(sys.modules, "config", configuration)

    async def fetch(*args, **kwargs):
        calls.append("account")
        if isinstance(response, BaseException):
            raise response
        return response

    client = SimpleNamespace(get=fetch, request=fetch, get_current_user_info=fetch, _fetch_json_by_browser=fetch,
                             _host=origin, headers={})

    class Context:
        @property
        def context(self):
            return self

        @property
        def request(self):
            return SimpleNamespace(post=self.post, get=self.get)

        async def get(self, url, **kwargs):
            assert platform == "tieba" and url == "https://tieba.baidu.com/dc/common/tbs"
            body = await fetch()

            async def parse():
                return body

            return SimpleNamespace(status=200, json=parse)

        async def post(self, url, **kwargs):
            raise AssertionError("快手身份验证必须捕获官网自发请求，不能手工调用直播接口")

        async def add_cookies(self, cookies):
            assert cookies == [{"name": "session", "value": "synthetic", "url": origin}]
            calls.append("cookies")

        async def add_init_script(self, **kwargs):
            assert kwargs == {"path": "libs/stealth.min.js"}

        async def new_page(self):
            if platform == "ks":
                self.page = FakeKuaishouPage(response, events=calls)
                self.page.context = self
                return self.page
            return self

        async def route(self, pattern, guard):
            assert platform == "ks"
            await self.page.route(pattern, guard)

        async def route_web_socket(self, pattern, handler):
            assert platform == "ks" and pattern == "**/*"
            closed = []

            async def close():
                closed.append(True)

            await handler(SimpleNamespace(close=close))
            assert closed == [True]
            calls.append("sockets_blocked")

        async def goto(self, url, **kwargs):
            assert calls == ["cookies"] and url == origin
            calls.append("navigation")

        async def wait_for_load_state(self, state, *, timeout):
            assert platform == "dy" and state == "networkidle" and timeout == 8000
            calls.append("settled")

        async def close(self):
            calls.append("closed")

    class Playwright:
        async def __aenter__(self):
            return SimpleNamespace(chromium=BrowserType() if platform == "ks" else "isolated")

        async def __aexit__(self, *args):
            pass

    class BrowserType:
        async def launch(self, **kwargs):
            assert kwargs == {"headless": True, "proxy": None}
            assert not configuration.SAVE_LOGIN_STATE and not configuration.ENABLE_CDP_MODE
            return Browser()

    class Browser:
        async def new_context(self, **kwargs):
            assert kwargs == {"viewport": {"width": 1920, "height": 1080},
                              "user_agent": "synthetic-browser", "service_workers": "block"}
            return Context()

        async def close(self):
            calls.append("browser_closed")

    monkeypatch.setitem(sys.modules, "playwright.async_api", SimpleNamespace(async_playwright=Playwright, TimeoutError=TimeoutError))

    class Crawler:
        index_url = origin
        cookie_urls = [origin]
        mobile_index_url = origin
        user_agent = "synthetic-browser"
        mobile_user_agent = "synthetic-browser"

        async def launch_browser(self, chromium, proxy, agent, *, headless):
            assert platform != "ks"
            assert not configuration.SAVE_LOGIN_STATE and not configuration.ENABLE_CDP_MODE
            assert chromium == "isolated" and proxy is None and headless
            assert agent == (None if platform == "dy" else "synthetic-browser")
            return Context()

        async def start(self):
            raise AssertionError("身份验证不可调用原启动、登录或搜索流程")

        async def search(self):
            raise AssertionError("身份验证不可搜索")

    async def factory(self, proxy):
        assert calls == startup_calls and proxy is None
        if platform == "ks":
            assert self.index_url == origin and self.cookie_urls == [origin]
        if platform in {"ks", "tieba"}:
            client.playwright_page = self.context_page
        return client

    if platform == "dy":
        # 实际 DouYinCrawler 没有 user_agent 字段，不能靠合成类补上不存在的属性。
        delattr(Crawler, "user_agent")
    factory_name = module._IDENTITY_STARTS[platform][0]
    setattr(Crawler, factory_name, factory)
    core = SimpleNamespace(**{module._CRAWLERS[platform][1]: Crawler},
                           utils=SimpleNamespace(convert_str_cookie_to_dict=lambda _: {"session": "synthetic"}))
    monkeypatch.setattr(module.importlib, "import_module", lambda _: core)
    output = tmp_path / "identity.json"
    assert module.install_probe(platform, output)
    if isinstance(response, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(Crawler().start())
        assert not output.exists() and calls[-1] == ("browser_closed" if platform == "ks" else "closed")
        return
    asyncio.run(Crawler().start())
    record = output.read_text(encoding="utf-8")
    assert json.loads(record)["status"] == "valid"
    assert calls == startup_calls + (["navigation", "account", "page_closed", "closed", "browser_closed"]
                                     if platform == "ks" else ["account", "closed"])
    assert "private-account" not in record and "private-token" not in record and "synthetic" not in record


@pytest.mark.parametrize("platform", ["xhs", "bili"])
def test_existing_identity_entry_preserves_verified_browser_start(monkeypatch, tmp_path, platform):
    from scripts import mediacrawler_identity as module

    class Crawler:
        async def start(self):
            pass

    original = Crawler.start
    core = SimpleNamespace(**{module._CRAWLERS[platform][1]: Crawler})
    monkeypatch.setattr(module.importlib, "import_module", lambda _: core)
    assert module.install_probe(platform, tmp_path / "identity.json")
    assert Crawler.start is original


@pytest.mark.parametrize("platform", ["wb", "dy", "zhihu", "tieba", "ks"])
def test_supported_account_probe_propagates_cancellation(platform):
    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    async def close():
        pass

    client = SimpleNamespace(get=cancelled, request=cancelled, get_current_user_info=cancelled,
                             _fetch_json_by_browser=cancelled, _host="https://synthetic.invalid", headers={},
                             playwright_page=SimpleNamespace(route=cancelled, close=close, context=SimpleNamespace(route=cancelled, request=SimpleNamespace(get=cancelled, post=cancelled))))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(probe_account(platform, client))
