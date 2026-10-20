"""官网自发的当前账号响应才可证明快手身份；全部请求为本地合成对象。"""
import asyncio
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from scripts.mediacrawler_identity import probe_account


HOME = "https://www.kuaishou.com/new-reco"
SELF = "https://www.kuaishou.com/rest/v/profile/get"


class _ResponseWaiter:
    def __init__(self, page, predicate):
        self.page, self.predicate, self.response = page, predicate, None

    async def __aenter__(self):
        self.page.waiter = self
        return self

    async def __aexit__(self, error_type, *_args):
        self.page.waiter = None
        if error_type is None and self.response is None:
            raise TimeoutError("private-response-timeout")

    @property
    def value(self):
        async def result():
            return self.response
        return result()


class FakeKuaishouPage:
    def __init__(self, payload, *, status=200, requests=None, events=None, goto_error=None):
        self.context = self
        self.payload, self.status = payload, status
        self.requests = [{"url": SELF}] if requests is None else requests
        self.events = [] if events is None else events
        self.goto_error = goto_error
        self.guard = self.waiter = None
        self.closed = False
        self.routes = []
        self.json_calls = 0

    async def route(self, pattern, guard):
        assert pattern == "**/*" and self.guard is None
        self.guard = guard

    async def unroute(self, *_args):
        raise AssertionError("关闭页面前不能撤掉请求保护")

    def expect_response(self, predicate, *, timeout):
        assert timeout > 0 and self.guard is not None
        return _ResponseWaiter(self, predicate)

    async def _emit(self, specification):
        request = SimpleNamespace(url=specification["url"], method=specification.get("method", "GET"),
                                  resource_type=specification.get("kind", "fetch"),
                                  post_data=specification.get("post_data"),
                                  frame=SimpleNamespace(page=self if specification.get("same_page", True) else object(),
                                                        url=specification.get("frame_url", HOME)))
        route = SimpleNamespace(request=request, decision=None)

        async def proceed():
            assert route.decision is None
            route.decision = "continued"

        async def abort():
            assert route.decision is None
            route.decision = "aborted"

        route.continue_, route.abort = proceed, abort
        await self.guard(route)
        assert route.decision in {"continued", "aborted"}
        self.routes.append(route)
        if route.decision == "continued":
            async def parse():
                self.json_calls += 1
                if isinstance(self.payload, BaseException):
                    raise self.payload
                return self.payload

            response = SimpleNamespace(request=request, status=self.status, json=parse)
            if request.resource_type in {"xhr", "fetch"}:
                self.events.append("account")
            if self.waiter is not None and self.waiter.response is None and self.waiter.predicate(response):
                self.waiter.response = response
        return route

    async def goto(self, url, *, wait_until, timeout):
        assert url == HOME and wait_until == "domcontentloaded" and timeout > 0
        assert self.waiter is not None
        self.events.append("navigation")
        if self.goto_error is not None:
            raise self.goto_error
        await self._emit({"url": HOME, "kind": "document"})
        for specification in self.requests:
            await self._emit(specification)

    async def close(self):
        assert self.guard is not None and self.waiter is None
        background = await self._emit({"url": "https://www.kuaishou.com/rest/v/feed/hot"})
        assert background.decision == "aborted"
        self.closed = True
        self.events.append("page_closed")


def _probe(page):
    result = asyncio.run(probe_account("ks", SimpleNamespace(playwright_page=page)))
    assert page.closed and page.waiter is None
    exposed = json.dumps(result, ensure_ascii=False)
    assert all(secret not in exposed for secret in ("private-account", "private-body", "private-cookie", "private-response"))
    return result


@pytest.mark.parametrize("account", ["private-account", 123])
def test_current_account_requires_root_user_id_and_explicit_success(account):
    page = FakeKuaishouPage({"result": 1, "userId": account, "nickname": "private-body"})
    result = _probe(page)
    assert result == {"status": "valid", "reason": "authenticated", "account_ref_kind": "account_id",
                      "account_ref": hashlib.sha256(f"ks\0{account}".encode("utf-8")).hexdigest()}
    assert page.events == ["navigation", "account", "page_closed"]


@pytest.mark.parametrize("payload", [
    {"result": 1, "userId": True}, {"result": 1, "userId": 0},
    {"result": 1, "userId": "0"}, {"result": 1, "userId": ""}, {"result": 1, "userId": " "},
    {"result": True, "userId": "private-account"}, {"result": "1", "userId": "private-account"},
    {"result": 109}, {"result": 109, "userId": "private-account"},
    {"result": 1, "data": {"userId": "private-account"}},
    {"data": {"ownerInfo": {"id": "private-account"}}},
    {"result": 1, "data": {"visionProfileUserList": {"userId": "private-account"}}},
    {"result": 1, "userId": "private-account", "error_code": 1},
    {"result": 1, "userId": "private-account", "errors": ["private-body"]},
    {"result": 1, "userId": "private-account", "error_msg": "private-body"},
    {"result": 1, "userId": "private-account", "errorMsg": "private-body"},
    {"result": 1, "userId": "private-account", "message": "未登录"},
    None, [],
])
def test_missing_contradictory_or_undocumented_identity_is_unknown(payload):
    assert _probe(FakeKuaishouPage(payload))["status"] == "unknown"


@pytest.mark.parametrize("message", ["用户未登录", "请先登录", "登录已过期", "登录已失效"])
def test_explicit_same_domain_unauthenticated_response_is_invalid(message):
    assert _probe(FakeKuaishouPage({"result": 109, "error_msg": message})) == {
        "status": "invalid", "reason": "login_required"}


@pytest.mark.parametrize("payload", [
    {"result": 109, "error_msg": "验证码 private-body"},
    {"result": 109, "error_msg": "请先登录，完成安全验证"},
    {"result": 109, "message": "访问频繁"},
    {"result": 1, "userId": "private-account", "needCaptcha": True},
    {"result": 1, "userId": "private-account", "needVerify": True},
])
def test_challenge_never_becomes_expired_or_valid(payload):
    assert _probe(FakeKuaishouPage(payload)) == {"status": "unknown", "reason": "challenge_required"}


@pytest.mark.parametrize("status", [302, 403, 429])
def test_http_failure_is_unknown_without_parsing_account_body(status):
    page = FakeKuaishouPage({"result": 1, "userId": "private-account"}, status=status)
    assert _probe(page)["status"] == "unknown" and page.json_calls == 0


@pytest.mark.parametrize("specification", [
    {"url": SELF + "?userId=public-account"}, {"url": SELF + "?target=public-account"},
    {"url": SELF + "?caver=x&target=public-account"},
    {"url": "https://www.kuaishou.com.example.test/rest/v/profile/get"},
    {"url": "https://live.kuaishou.com/rest/v/profile/get"},
    {"url": "http://www.kuaishou.com/rest/v/profile/get"},
    {"url": SELF, "method": "POST"}, {"url": SELF, "post_data": "private-cookie"},
    {"url": SELF, "same_page": False}, {"url": SELF, "frame_url": "https://www.kuaishou.com/profile/public-account"},
    {"url": SELF, "kind": "script"},
])
def test_target_profiles_or_wrong_request_scope_cannot_prove_self(specification):
    page = FakeKuaishouPage({"result": 1, "userId": "private-account"}, requests=[specification])
    assert _probe(page)["status"] == "unknown"
    assert page.routes[1].decision == "aborted" and page.json_calls == 0


def test_only_document_static_assets_and_one_signed_self_request_are_forwarded():
    requests = [{"url": "https://static.yximgs.com/main.js", "kind": "script"},
                {"url": "https://www.kuaishou.com/main.css", "kind": "stylesheet"},
                {"url": "https://www.kuaishou.com/rest/v/feed/hot"},
                {"url": "https://www.kuaishou.com/graphql", "method": "POST"},
                {"url": SELF + "?caver=2&__NS_hxfalcon=synthetic-signature"}]
    page = FakeKuaishouPage({"result": 1, "userId": "private-account"}, requests=requests)
    assert _probe(page)["status"] == "valid"
    assert [route.decision for route in page.routes] == [
        "continued", "continued", "continued", "aborted", "aborted", "continued", "aborted"]


def test_duplicate_self_request_is_aborted_and_does_not_approve_first_response():
    page = FakeKuaishouPage({"result": 1, "userId": "private-account"}, requests=[{"url": SELF}, {"url": SELF}])
    assert _probe(page)["status"] == "unknown"
    assert [route.decision for route in page.routes[1:3]] == ["continued", "aborted"]


@pytest.mark.parametrize("stage", ["response_json", "close"])
def test_duplicate_self_during_json_or_close_cannot_escape_the_final_scope_check(stage):
    class LateDuplicatePage(FakeKuaishouPage):
        async def goto(self, *args, **kwargs):
            await super().goto(*args, **kwargs)
            if stage == "response_json":
                original = self.waiter.response.json

                async def parse():
                    await self._emit({"url": SELF})
                    return await original()

                self.waiter.response.json = parse

        async def close(self):
            if stage == "close":
                await self._emit({"url": SELF})
            await super().close()

    page = LateDuplicatePage({"result": 1, "userId": "private-account"})
    assert _probe(page) == {"status": "unknown", "reason": "identity_response_unconfirmed"}
    profiles = [route for route in page.routes if route.request.url == SELF]
    assert [route.decision for route in profiles] == ["continued", "aborted"]


@pytest.mark.parametrize("stage", ["navigation", "response_json"])
def test_cancellation_propagates_and_closes_page_under_request_guard(stage):
    page = FakeKuaishouPage(asyncio.CancelledError("private-cookie") if stage == "response_json" else {},
                            goto_error=asyncio.CancelledError("private-cookie") if stage == "navigation" else None)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(probe_account("ks", SimpleNamespace(playwright_page=page)))
    assert page.closed and page.waiter is None and page.events[-1] == "page_closed"


def test_response_timeout_is_unknown_and_closes_page_without_unblocking_background_requests():
    page = FakeKuaishouPage({}, requests=[])
    assert _probe(page) == {"status": "unknown", "reason": "identity_probe_failed"}


def test_invalid_json_is_unknown_without_exposing_the_response_or_losing_cleanup():
    page = FakeKuaishouPage(ValueError("private-body private-cookie"))
    assert _probe(page) == {"status": "unknown", "reason": "identity_probe_failed"}


@pytest.mark.parametrize("cancelled", [False, True])
def test_close_failure_preserves_cancellation_and_cannot_finish_with_valid_identity(cancelled):
    class BrokenClosePage(FakeKuaishouPage):
        async def close(self):
            await super().close()
            raise RuntimeError("private-body private-cookie")

    page = BrokenClosePage(asyncio.CancelledError() if cancelled else {"result": 1, "userId": "private-account"})
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(probe_account("ks", SimpleNamespace(playwright_page=page)))
        assert page.closed
    else:
        assert _probe(page) == {"status": "unknown", "reason": "identity_probe_failed"}


def test_old_browser_without_websocket_guard_stops_before_any_source_request(tmp_path, monkeypatch):
    from scripts import mediacrawler_identity as module

    events = []
    monkeypatch.setitem(sys.modules, "config", SimpleNamespace(
        ENABLE_IP_PROXY=False, SAVE_LOGIN_STATE=True, ENABLE_CDP_MODE=True,
        COOKIES="private-cookie", HEADLESS=True,
    ))

    async def close_context():
        events.append("context_closed")

    async def close_browser():
        events.append("browser_closed")

    async def new_context(**kwargs):
        assert kwargs == {"viewport": {"width": 1920, "height": 1080},
                          "user_agent": "synthetic-browser", "service_workers": "block"}
        events.append("new_context")
        return SimpleNamespace(close=close_context)

    async def launch(**kwargs):
        assert kwargs == {"headless": True, "proxy": None}
        events.append("launch")
        return SimpleNamespace(new_context=new_context, close=close_browser)

    class Playwright:
        async def __aenter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        async def __aexit__(self, *_args):
            pass

    monkeypatch.setitem(sys.modules, "playwright.async_api", SimpleNamespace(
        async_playwright=Playwright, TimeoutError=TimeoutError,
    ))
    output = tmp_path / "identity.json"
    asyncio.run(module._start_identity(SimpleNamespace(index_url="https://www.kuaishou.com",
                        user_agent="synthetic-browser"), "ks", SimpleNamespace(), output))
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "status": "unknown", "reason": "browser_version_unsupported"}
    assert events == ["launch", "new_context", "context_closed", "browser_closed"]
