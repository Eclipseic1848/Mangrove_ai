"""账号接口探针与公开采集分离；合成响应不冒充在线平台验收。"""
import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from scripts.mediacrawler_identity import probe_account
from src.collectors.base import CollectResult


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
    ("zhihu", {"uid": "synthetic-account", "name": "测试"}, "unknown"),
    ("zhihu", {"uid": "synthetic-account"}, "unknown"),
    ("zhihu", {"uid": True, "name": "测试"}, "unknown"),
    ("zhihu", {"uid": "synthetic-account", "name": " "}, "unknown"),
    ("dy", {"logged_in": True}, "unknown"),
    ("ks", {"result": 1}, "unknown"),
    ("tieba", {"BDUSS": "present"}, "unknown"),
])
def test_account_endpoint_evidence_is_required(platform, response, status):
    calls = []

    async def fetch(*args, **kwargs):
        calls.append(True)
        return response

    client = SimpleNamespace(query_self=fetch, get=fetch, request=fetch,
                             get_current_user_info=fetch, _host="https://synthetic.invalid", headers={})
    result = asyncio.run(probe_account(platform, client))
    assert result["status"] == status
    assert "synthetic-account" not in json.dumps(result)
    if platform in {"dy", "ks", "tieba", "zhihu"}:
        assert calls == []


@pytest.mark.parametrize("platform", ["xhs", "bili", "wb"])
def test_probe_exception_is_unknown_and_does_not_leak_response(platform):
    async def fail(*args, **kwargs):
        raise RuntimeError("private-cookie-value")

    client = SimpleNamespace(query_self=fail, get=fail, request=fail,
                             get_current_user_info=fail, _host="https://synthetic.invalid", headers={})
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
