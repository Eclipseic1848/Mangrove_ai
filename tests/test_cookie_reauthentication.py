"""重新认证先验证后替换；不覆盖并发更新，不恢复旧任务。"""
import asyncio

import pytest
from fastapi import HTTPException

from src.api.routes import config_routes as routes
from src.config import runtime_config as rc, user_ctx
from tests.test_cookie_health_binding import store


@pytest.mark.parametrize("key", sorted(routes._COOKIE_HEALTH_KEYS))
@pytest.mark.parametrize("scope", ["personal", "platform"])
@pytest.mark.parametrize("outcome", ["valid", "invalid", "unknown", "rotated", "cancelled"])
def test_reauthentication_keeps_existing_credentials_until_verified(store, monkeypatch, key, scope, outcome):
    owner = "global" if scope == "platform" else "owner"
    store.config_set(owner, key, "old-cookie")
    monkeypatch.setattr(routes.settings, key, "old-cookie")
    async def verify(target):
        assert target == key
        assert user_ctx.effective(key) == "new-cookie"
        assert store.config_all(owner)[key] == "old-cookie"
        if outcome == "rotated":
            store.config_set(owner, key, "concurrent-cookie")
        if outcome == "cancelled":
            raise asyncio.CancelledError()
        if outcome in {"invalid", "unknown"}:
            raise routes.CookieProbeError("new-cookie", status=outcome, reason="login_required" if outcome == "invalid" else "probe_failed")
        return "身份通过"
    monkeypatch.setattr(routes, "_verify_target", verify)
    async def run():
        return await routes.reauthenticate_cookie(routes.CookieReauthenticationIn(key=key, value="new-cookie", scope=scope),
            user={"user_id":"owner", "role":"admin" if scope == "platform" else "user"})
    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run())
    elif outcome == "rotated":
        with pytest.raises(HTTPException) as error:
            asyncio.run(run())
        assert error.value.status_code == 409
    else:
        result = asyncio.run(run())
        assert result["ok"] is (outcome == "valid")
        assert result["saved"] is (outcome == "valid")
        assert "new-cookie" not in str(result)
        if outcome == "valid":
            assert result["task_resume"] == "requires_explicit_recovery"
    expected = "concurrent-cookie" if outcome == "rotated" else "new-cookie" if outcome == "valid" else "old-cookie"
    assert store.config_all(owner)[key] == expected
    assert store.config_all("other") == {}


def test_personal_user_cannot_replace_platform_cookie(store, monkeypatch):
    async def forbidden(*args):
        raise AssertionError("无权请求不得发起验证")
    monkeypatch.setattr(routes, "_verify_target", forbidden)
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.reauthenticate_cookie(routes.CookieReauthenticationIn(key="mc_cookie_xhs", value="new", scope="platform"), user={"user_id":"owner","role":"user"}))
    assert error.value.status_code == 403


def test_secret_compare_and_swap_checks_version_even_after_same_value_write(store):
    key = "mc_cookie_xhs"
    store.config_set("owner", key, "old")
    version, _ = store.config_secret_snapshot("owner", key)
    store.config_set("owner", key, "old")
    assert not store.config_replace_secret("owner", key, "new", expected_version=version, updated_by="owner")
    assert store.config_all("owner")[key] == "old"


@pytest.mark.parametrize("key", sorted(routes._COOKIE_HEALTH_KEYS))
@pytest.mark.parametrize("scope", ["personal", "platform"])
def test_reauthentication_rejects_changed_node_without_saving(store, monkeypatch, key, scope):
    from src.config import cookie_probe_binding

    owner = "global" if scope == "platform" else "owner"
    store.config_set(owner, key, "old-cookie")
    original = store.config_secret_snapshot(owner, key)
    environment = cookie_probe_binding.capture_probe_environment()
    monkeypatch.setattr(cookie_probe_binding, "capture_probe_environment", lambda: dict(environment))

    async def verify(target):
        assert target == key and user_ctx.effective(key) == "new-cookie"
        environment["node"] = "another-node"
        return "身份通过"

    monkeypatch.setattr(routes, "_verify_target", verify)
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes.reauthenticate_cookie(
            routes.CookieReauthenticationIn(key=key, value="new-cookie", scope=scope),
            user={"user_id": "owner", "role": "admin" if scope == "platform" else "user"},
        ))
    assert error.value.status_code == 409
    assert store.config_secret_snapshot(owner, key) == original
