"""真实小红书客户端的合成会话回归；签名仅验证输入一致，不实测算法。"""
import asyncio
import importlib
import importlib.util
import os
import sys
import time
import types
from pathlib import Path

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[1] / "external/MediaCrawler/repo"
API = "https://edith.xiaohongshu.com"
WWW = "https://www.xiaohongshu.com"


def cookie(value="before==", *, name="web_session", domain=".xiaohongshu.com", path="/", expires=-1):
    return dict(name=name, value=value, domain=domain, path=path, expires=expires,
                httpOnly=True, secure=True, sameSite="Lax")


class Browser:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else [cookie()]
        self.writes = 0
        self.fail_write = False

    async def cookies(self, urls=None):
        return [dict(row) for row in self.rows if row.get("expires", -1) == -1 or row["expires"] > time.time()]

    async def add_cookies(self, rows):
        if self.fail_write:
            raise RuntimeError("合成写入失败")
        self.writes += 1
        for row in rows:
            key = row["name"], row["domain"], row["path"]
            self.rows = [old for old in self.rows if (old["name"], old["domain"], old["path"]) != key]
            if row.get("expires", -1) == -1 or row["expires"] > time.time():
                self.rows.append(dict(row))


@pytest.fixture(params=["local"] + (["server"] if os.environ.get("MANGROVE_XHS_SERVER_CLIENT") else []))
def xhs(monkeypatch, request):
    if not (ROOT / "media_platform/xhs/client.py").is_file():
        pytest.skip("需先准备可选 MediaCrawler 工作副本")
    prefixes = ("media_platform", "tools", "proxy", "base", "config")
    old = {key: value for key, value in sys.modules.items() if key.split(".")[0] in prefixes}
    for key in old:
        sys.modules.pop(key)
    monkeypatch.syspath_prepend(str(ROOT))
    config = types.ModuleType("config")
    config.XHS_INTERNATIONAL = False
    config.DISABLE_SSL_VERIFY = False
    sys.modules["config"] = config
    for name, relative in (("media_platform", "media_platform"), ("media_platform.xhs", "media_platform/xhs")):
        package = types.ModuleType(name)
        package.__path__ = [str(ROOT / relative)]
        sys.modules[name] = package
    signs = []
    signing = types.ModuleType("media_platform.xhs.playwright_sign")

    def sign(**kwargs):
        signs.append(dict(kwargs))
        return {"x-s": kwargs["cookie_str"], "x-t": "synthetic", "x-s-common": "synthetic", "x-b3-traceid": "synthetic"}

    signing.sign_with_xhshow = sign
    sys.modules[signing.__name__] = signing
    # 本轮只测请求会话合同；无关 HTML 抽取器的 humps 依赖不参与。
    extractor = types.ModuleType("media_platform.xhs.extractor")
    extractor.XiaoHongShuExtractor = type("SyntheticExtractor", (), {
        "extract_note_detail_from_html": lambda self, *args: {},
        "extract_creator_info_from_html": lambda self, *args: {},
    })
    sys.modules[extractor.__name__] = extractor
    if request.param == "server":
        client_path = Path(os.environ["MANGROVE_XHS_SERVER_CLIENT"])
        exception_spec = importlib.util.spec_from_file_location(
            "media_platform.xhs.exception", client_path.with_name("server-exception.original.py"))
        exceptions = importlib.util.module_from_spec(exception_spec)
        sys.modules[exception_spec.name] = exceptions
        exception_spec.loader.exec_module(exceptions)
        client_spec = importlib.util.spec_from_file_location("media_platform.xhs.client", client_path)
        module = importlib.util.module_from_spec(client_spec)
        sys.modules[client_spec.name] = module
        client_spec.loader.exec_module(module)
    else:
        if os.environ.get("MANGROVE_XHS_LOCAL_CLIENT"):
            client_spec = importlib.util.spec_from_file_location("media_platform.xhs.client", os.environ["MANGROVE_XHS_LOCAL_CLIENT"])
            module = importlib.util.module_from_spec(client_spec)
            sys.modules[client_spec.name] = module
            client_spec.loader.exec_module(module)
        else:
            module = importlib.import_module("media_platform.xhs.client")
    made = []

    def build(handler, browser=None):
        browser = browser or Browser()

        def factory(**kwargs):
            client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False, **kwargs)
            made.append(client)
            return client

        monkeypatch.setattr(module, "make_async_client", factory)
        client = module.XiaoHongShuClient(headers={"Cookie": "web_session=before=="},
                                         playwright_page=types.SimpleNamespace(context=browser),
                                         cookie_dict={"web_session": "before=="})
        return client, browser

    yield module, build, signs, made
    for key in list(sys.modules):
        if key.split(".")[0] in prefixes:
            sys.modules.pop(key)
    sys.modules.update(old)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get", "post", "query_self", "html"])
async def test_refresh_reaches_next_request_browser_and_signatures(xhs, method):
    module, build, signs, made = xhs
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"success": True, "data": {"result": {"success": True}}}, headers=[
            ("Set-Cookie", "web_session=after===; Domain=.xiaohongshu.com; Path=/; Secure; HttpOnly; SameSite=Lax"),
            ("Set-Cookie", "other=second==; Domain=.xiaohongshu.com; Path=/; Secure; Expires=Wed, 09 Jun 2038 10:18:14 GMT"),
        ])

    client, browser = build(respond)
    async def call():
        if method == "query_self":
            result = await client.query_self()
            assert result["success"] is True
        elif method == "html":
            await client.request("GET", WWW + "/user/profile/synthetic", headers=client.headers, return_response=True)
        elif method == "post":
            await client.post("/api/synthetic", {"field": "合成"})
        else:
            await client.get("/api/synthetic", {})
    await call()
    await call()
    assert "web_session=after===" in seen[-1].headers["Cookie"]
    assert "other=second==" in seen[-1].headers["Cookie"]
    assert client.cookie_dict["web_session"] == "after==="
    assert any(row["value"] == "after===" and row["httpOnly"] for row in browser.rows)
    if signs:
        assert [item["cookie_str"] for item in signs] == [request.headers["Cookie"] for request in seen]
    assert all(item.is_closed for item in made)


@pytest.mark.asyncio
@pytest.mark.parametrize("deletion", ["Max-Age=0", "Expires=Thu, 01 Jan 1970 00:00:00 GMT"])
async def test_scoped_deletion_does_not_resurrect_or_remove_sibling(xhs, deletion):
    _, build, _, _ = xhs
    seen = []
    browser = Browser([cookie(), cookie("sibling", path="/private")])

    def respond(request):
        seen.append(request.headers.get("Cookie", ""))
        return httpx.Response(200, json={"success": True, "data": {}}, headers={
            "Set-Cookie": f"web_session=; Domain=.xiaohongshu.com; Path=/; Secure; {deletion}"})

    client, _ = build(respond, browser)
    await client.get("/api/synthetic", {})
    await client.get("/api/synthetic", {})
    assert seen[-1] == ""
    assert [(row["value"], row["path"]) for row in browser.rows] == [("sibling", "/private")]


@pytest.mark.asyncio
async def test_actual_url_scope_browser_refresh_and_foreign_domain_rejection(xhs):
    _, build, signs, _ = xhs
    seen = []
    browser = Browser([cookie("domain"), cookie("www-only", name="www_cookie", domain="www.xiaohongshu.com"),
                       cookie("api-only", name="path_cookie", path="/api"),
                       cookie("foreign", name="foreign", domain="other.invalid")])

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"success": True, "data": {}}, headers={
            "Set-Cookie": "foreign=bad; Domain=other.invalid; Path=/"})

    client, _ = build(respond, browser)
    await client.get("/api/synthetic", {})
    assert "www_cookie" not in seen[-1].headers["Cookie"]
    assert "path_cookie=api-only" in seen[-1].headers["Cookie"]
    assert "foreign" not in seen[-1].headers["Cookie"]
    browser.rows[0]["value"] = "browser-new=="
    await client.request("GET", WWW + "/html", headers=client.headers, return_response=True)
    assert "www_cookie=www-only" in seen[-1].headers["Cookie"]
    assert "path_cookie" not in seen[-1].headers["Cookie"]
    assert "web_session=browser-new==" in seen[-1].headers["Cookie"]
    assert not any(row["value"] == "bad" for row in browser.rows)
    assert signs[0]["cookie_str"] == seen[0].headers["Cookie"]


@pytest.mark.asyncio
async def test_anonymous_html_and_media_keep_credentials_out(xhs):
    _, build, _, _ = xhs
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, text="synthetic", headers={"Set-Cookie": "web_session=anonymous; Domain=.xiaohongshu.com; Path=/"})

    client, browser = build(respond)
    await client.request("GET", WWW + "/html", headers={}, return_response=True)
    if hasattr(client, "get_note_media"):
        await client.get_note_media("https://media.invalid/synthetic")
    assert all("Cookie" not in request.headers for request in seen)
    assert browser.rows[0]["value"] == "before=="
    assert browser.writes == 0


@pytest.mark.asyncio
async def test_browser_concurrent_change_refuses_without_overwrite_or_retry(xhs):
    module, build, _, _ = xhs
    count = 0
    browser = Browser()

    def respond(request):
        nonlocal count
        count += 1
        browser.rows[0]["value"] = "browser-current"
        return httpx.Response(200, json={"success": True, "data": {}}, headers={
            "Set-Cookie": "web_session=stale-response; Domain=.xiaohongshu.com; Path=/; Secure"})

    client, _ = build(respond, browser)
    with pytest.raises(Exception) as caught:
        await client.get("/api/synthetic", {})
    assert type(caught.value).__name__ == "CookieSyncError"
    assert count == 1 and browser.rows[0]["value"] == "browser-current"


@pytest.mark.asyncio
async def test_serial_requests_sign_only_after_previous_refresh_and_cancel_releases_lock(xhs):
    _, build, signs, _ = xhs
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def respond(request):
        seen.append(request)
        if len(seen) == 1:
            entered.set()
            await release.wait()
        return httpx.Response(200, json={"success": True, "data": {}}, headers={
            "Set-Cookie": "web_session=rotated; Domain=.xiaohongshu.com; Path=/; Secure"})

    client, _ = build(respond)
    first = asyncio.create_task(client.get("/api/first", {}))
    await asyncio.wait_for(entered.wait(), 1)
    cancelled = asyncio.create_task(client.get("/api/cancelled", {}))
    await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    second = asyncio.create_task(client.post("/api/second", {}))
    await asyncio.sleep(0)
    assert len(seen) == 1 and len(signs) == 1
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), 2)
    assert len(seen) == 2 and "web_session=rotated" in seen[1].headers["Cookie"]


@pytest.mark.asyncio
async def test_cookie_write_failure_is_not_retried(xhs):
    _, build, _, _ = xhs
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"success": True, "data": {}}, headers={
            "Set-Cookie": "web_session=rotated; Domain=.xiaohongshu.com; Path=/; Secure"})
    client, browser = build(respond)
    browser.fail_write = True
    with pytest.raises(Exception) as caught:
        await client.get("/api/synthetic", {})
    assert type(caught.value).__name__ == "CookieSyncError"
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("response_cookie", ["web_session=parent-new==; Domain=.xiaohongshu.com; Path=/; Secure",
                                             "web_session=; Domain=.xiaohongshu.com; Path=/; Max-Age=0"])
async def test_core_host_only_bootstrap_parent_update_or_delete_never_resurrects(xhs, response_cookie):
    _, build, _, _ = xhs
    browser = Browser([cookie(domain="www.xiaohongshu.com")])
    seen = []

    def respond(request):
        seen.append(request.headers.get("Cookie", ""))
        headers = {"Set-Cookie": response_cookie} if len(seen) == 1 else {}
        return httpx.Response(200, json={"success": True, "data": {}}, headers=headers)

    client, _ = build(respond, browser)
    await client.get("/api/synthetic", {})
    await client.get("/api/synthetic", {})
    await client.get("/api/synthetic", {})
    assert seen[0] == "web_session=before=="
    expected = "" if "Max-Age=0" in response_cookie else "web_session=parent-new=="
    assert seen[1:] == [expected, expected]
    assert all(row["domain"] == ".xiaohongshu.com" for row in browser.rows)


@pytest.mark.asyncio
async def test_www_only_refresh_does_not_expand_into_api_bootstrap(xhs):
    _, build, _, _ = xhs
    browser = Browser([cookie(domain="www.xiaohongshu.com")])
    seen = []

    def respond(request):
        seen.append(request)
        headers = {"Set-Cookie": "web_session=www-new; Path=/; Secure"} if request.url.host == "www.xiaohongshu.com" else {}
        return httpx.Response(200, json={"success": True, "data": {}}, headers=headers)

    client, _ = build(respond, browser)
    await client.get("/api/synthetic", {})
    await client.request("GET", WWW + "/html", headers=client.headers, return_response=True)
    await client.get("/api/synthetic", {})
    assert seen[0].headers["Cookie"] == "web_session=before=="
    assert seen[2].headers["Cookie"] == "web_session=before=="
    assert any(row["domain"] == "www.xiaohongshu.com" and row["value"] == "www-new" for row in browser.rows)


@pytest.mark.asyncio
async def test_html_outer_retry_also_stops_on_cookie_conflict(xhs):
    _, build, _, _ = xhs
    browser = Browser()
    calls = []

    def respond(request):
        calls.append(request)
        browser.rows[0]["value"] = "newer-browser"
        return httpx.Response(200, text="synthetic", headers={
            "Set-Cookie": "web_session=stale; Domain=.xiaohongshu.com; Path=/; Secure"})

    client, _ = build(respond, browser)
    with pytest.raises(Exception) as caught:
        await client.get_note_by_id_from_html("synthetic", "synthetic", "synthetic", enable_cookie=True)
    assert type(caught.value).__name__ == "CookieSyncError"
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(401, None), (403, None), (429, None), (200, 300011), (200, 300012)])
async def test_server_existing_security_failures_stay_single_attempt(xhs, status, code):
    module, build, _, _ = xhs
    if not hasattr(module, "PlatformAccessError"):
        pytest.skip("本机基线没有该生产安全分类，不在此补丁扩大行为")
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, json={"success": False, "code": code})

    client, _ = build(respond)
    with pytest.raises((module.PlatformAccessError, module.IPBlockError)):
        await client.get_note_by_id_from_html("synthetic", "synthetic", "synthetic", enable_cookie=True)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_cancellation_during_transport_propagates_and_closes_client(xhs):
    _, build, _, made = xhs
    entered = asyncio.Event()
    calls = []

    async def respond(request):
        calls.append(request)
        entered.set()
        await asyncio.Event().wait()

    client, _ = build(respond)
    task = asyncio.create_task(client.get("/api/synthetic", {}))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert len(calls) == 1 and made[-1].is_closed


@pytest.mark.asyncio
async def test_two_contexts_keep_same_named_cookies_isolated(xhs):
    _, build, _, _ = xhs
    seen = []
    def respond(request):
        seen.append(request.headers.get("Cookie", ""))
        return httpx.Response(200, json={"success": True, "data": {}})
    first, _ = build(respond, Browser([cookie("owner-one")]))
    second, _ = build(respond, Browser([cookie("owner-two")]))
    await asyncio.gather(first.get("/api/synthetic", {}), second.get("/api/synthetic", {}))
    assert sorted(seen) == ["web_session=owner-one", "web_session=owner-two"]


@pytest.mark.asyncio
@pytest.mark.parametrize("same_site", ["lax", "none", "sTrIcT"])
async def test_legal_same_site_case_is_normalized_for_browser(xhs, same_site):
    _, build, _, _ = xhs
    def respond(request):
        return httpx.Response(200, json={"success": True, "data": {}}, headers={
            "Set-Cookie": f"web_session=new; Domain=.xiaohongshu.com; Path=/; Secure; SameSite={same_site}"})
    client, browser = build(respond)
    await client.get("/api/synthetic", {})
    assert browser.rows[0]["sameSite"] == {"lax": "Lax", "none": "None", "strict": "Strict"}[same_site.lower()]


@pytest.mark.asyncio
async def test_no_set_cookie_preserves_fractional_expiry_and_does_not_write(xhs):
    _, build, _, _ = xhs
    original = cookie(expires=time.time() + 3600.12345)
    browser = Browser([dict(original)])
    def respond(request):
        return httpx.Response(200, json={"success": True, "data": {}})
    client, _ = build(respond, browser)
    await client.get("/api/synthetic", {})
    await client.get("/api/synthetic", {})
    assert browser.writes == 0 and browser.rows == [original]


@pytest.mark.asyncio
async def test_invalid_set_cookie_does_not_retire_initial_bootstrap(xhs):
    _, build, _, _ = xhs
    seen = []
    browser = Browser([cookie(domain="www.xiaohongshu.com")])
    def respond(request):
        seen.append(request.headers.get("Cookie", ""))
        return httpx.Response(200, json={"success": True, "data": {}}, headers={
            "Set-Cookie": "web_session=invalid; Domain=.xiaohongshu.com; Path=/; Max-Age=invalid"})
    client, _ = build(respond, browser)
    await client.get("/api/synthetic", {})
    await client.get("/api/synthetic", {})
    assert seen == ["web_session=before==", "web_session=before=="]
