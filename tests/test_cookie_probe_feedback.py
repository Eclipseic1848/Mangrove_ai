"""未知探测保留配置并说明原因，不将任意上游正文或新凭证公开。"""
import asyncio

import pytest

from src.api.routes import config_routes as routes
from tests.test_cookie_health_binding import store


@pytest.mark.parametrize("reason,hint", [
    ("identity_probe_unsupported", "暂未接通"),
    ("network_error", "网络"),
    ("challenge_required", "验证挑战"),
    ("rate_limited", "频繁"),
    ("identity_unverified", "无法确认"),
    ("credential_scope_incomplete", "业务域"),
])
def test_reauthentication_explains_unknown_without_adopting_cookie(store, monkeypatch, reason, hint):
    key = "mc_cookie_dy"
    store.config_set("global", key, "old-cookie")

    async def unknown(_key):
        raise routes.CookieProbeError("private-new-cookie", reason=reason)

    monkeypatch.setattr(routes, "_verify_target", unknown)
    result = asyncio.run(routes.reauthenticate_cookie(
        routes.CookieReauthenticationIn(key=key, value="private-new-cookie", scope="platform"),
        user={"user_id": "admin", "role": "admin"},
    ))
    assert result["ok"] is result["saved"] is False
    assert result["verification"]["status"] == "unknown"
    assert hint in result["detail"]
    assert "原配置未修改" in result["detail"]
    assert "private-new-cookie" not in str(result)
    assert store.config_all("global")[key] == "old-cookie"


@pytest.mark.parametrize("response,status,reason", [
    ({"retcode": "0", "data": {"userInfo": {"baseInfo": {"curPin": "synthetic-account"}}}}, "valid", None),
    ({"retcode": "1001"}, "invalid", "login_required"),
    ({"retcode": "0", "data": {"userInfo": {"baseInfo": {"curPin": ""}}}}, "unknown", "identity_unverified"),
    ({"retcode": "0", "data": {"userInfo": {"baseInfo": {"curPin": True}}}}, "unknown", "identity_unverified"),
    ({"items": [{"name": "公开商品"}]}, "unknown", "identity_unverified"),
])
def test_jd_requires_current_account_response(monkeypatch, response, status, reason):
    import httpx
    from src.config.user_ctx import user_overrides_context

    original = httpx.AsyncClient

    def reply(request):
        assert request.url.host == "me-api.jd.com"
        assert request.url.path == "/user_new/info/GetJDUserInfoUnion"
        return httpx.Response(200, json=response, request=request)

    monkeypatch.setattr(routes.httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(reply)))
    with user_overrides_context({"jd_cookie": "synthetic-cookie"}):
        if status == "valid":
            assert "身份验证通过" in asyncio.run(routes._verify_ecommerce_cookie("jd_cookie"))
        else:
            with pytest.raises(routes.CookieProbeError) as error:
                asyncio.run(routes._verify_ecommerce_cookie("jd_cookie"))
            assert error.value.status == status and error.value.reason == reason


@pytest.mark.parametrize("key", ["jd_cookie", "tb_cookie"])
def test_account_endpoint_login_redirect_does_not_prove_session_expired(monkeypatch, key):
    import httpx
    from src.config.user_ctx import user_overrides_context

    original = httpx.AsyncClient

    def reply(request):
        if request.url.host in {"me-api.jd.com", "h5api.m.taobao.com"}:
            return httpx.Response(302, headers={"Location": "https://login.taobao.com/"}, request=request)
        return httpx.Response(200, text="访问验证", request=request)

    monkeypatch.setattr(routes.httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(reply)))
    with user_overrides_context({key: "_m_h5_tk=synthetic_123"}):
        with pytest.raises(routes.CookieProbeError) as error:
            asyncio.run(routes._verify_ecommerce_cookie(key))
        assert error.value.status == "unknown"


def test_taobao_incomplete_business_cookie_does_not_start_http(monkeypatch):
    from src.config.user_ctx import user_overrides_context

    def unexpected(**kwargs):
        raise AssertionError("缺少业务令牌时不发起身份请求")

    monkeypatch.setattr(routes.httpx, "AsyncClient", unexpected)
    with user_overrides_context({"tb_cookie": "cookie2=synthetic"}):
        with pytest.raises(routes.CookieProbeError) as error:
            asyncio.run(routes._verify_ecommerce_cookie("tb_cookie"))
        assert error.value.status == "unknown"
        assert error.value.reason == "credential_scope_incomplete"


@pytest.mark.parametrize("status,reason", [(403, "access_denied"), (429, "rate_limited")])
def test_login_host_denial_does_not_prove_expired_cookie(status, reason):
    with pytest.raises(routes.CookieProbeError) as error:
        routes._classify_ecommerce_probe("https://login.taobao.com/", status, "淘宝", ("login.taobao.com",), True)
    assert error.value.status == "unknown" and error.value.reason == reason


@pytest.mark.parametrize("response,status,reason", [
    ({"ret": ["SUCCESS::调用成功"], "data": {"userNumId": "123"}}, "valid", None),
    ({"ret": ["FAIL_SYS_SESSION_EXPIRED::失效"]}, "invalid", "login_required"),
    ({"ret": ["RGV587_ERROR::SM"], "data": {"url": "https://login.m.taobao.com/"}}, "unknown", "challenge_required"),
    ({"ret": ["FAIL_SYS_USER_VALIDATE::验证"]}, "unknown", "challenge_required"),
    ({"ret": ["FAIL_SYS_TOKEN_EXPIRED::令牌"]}, "unknown", "identity_unverified"),
    ({"ret": ["SUCCESS::调用成功"], "data": {"userNumId": "0"}}, "unknown", "identity_unverified"),
    ({"ret": ["SUCCESS::调用成功"], "data": {"userNumId": True}}, "unknown", "identity_unverified"),
    ({"ret": ["SUCCESS::调用成功", "UNKNOWN"], "data": {"userNumId": "123"}}, "unknown", "identity_unverified"),
    ({"ret": ["SUCCESS::调用成功", 7], "data": {"userNumId": "123"}}, "unknown", "identity_unverified"),
    ({"ret": {"SUCCESS": "调用成功"}, "data": {"userNumId": "123"}}, "unknown", "identity_unverified"),
])
def test_taobao_requires_authenticated_current_account(monkeypatch, response, status, reason):
    import hashlib
    import httpx
    from src.config.user_ctx import user_overrides_context

    original = httpx.AsyncClient

    def reply(request):
        assert request.url.host == "h5api.m.taobao.com"
        assert request.url.path == "/h5/mtop.user.getusersimple/1.0/"
        params = request.url.params
        assert params["sign"] == hashlib.md5(f"synthetic&{params['t']}&12574478&{{}}".encode("utf-8"), usedforsecurity=False).hexdigest()
        assert params["sessionOption"] == "AutoLoginOnly" and params["data"] == "{}"
        return httpx.Response(200, json=response, request=request)

    monkeypatch.setattr(routes.httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(reply)))
    with user_overrides_context({"tb_cookie": "_m_h5_tk=synthetic_123; cookie2=synthetic"}):
        if status == "valid":
            assert "身份验证通过" in asyncio.run(routes._verify_ecommerce_cookie("tb_cookie"))
        else:
            with pytest.raises(routes.CookieProbeError) as error:
                asyncio.run(routes._verify_ecommerce_cookie("tb_cookie"))
            assert error.value.status == status and error.value.reason == reason
