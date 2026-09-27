"""十个平台公开验证结果保留身份分类，但不声称已验证操作权限。"""
import asyncio
from types import SimpleNamespace

import pytest

from src.api.routes import config_routes as routes


@pytest.mark.parametrize("target", routes._COOKIE_HEALTH_KEYS)
@pytest.mark.parametrize("role", ["user", "admin"])
@pytest.mark.parametrize("failure,status,reason", [
    (None, "valid", "authenticated"),
    (routes.CookieProbeError("已过期", status="invalid", reason="login_required"), "invalid", "login_required"),
    (routes.CookieProbeError("暂时受限", reason="rate_limited"), "unknown", "rate_limited"),
    (TimeoutError("网络结果未知"), "unknown", "probe_failed"),
])
def test_cookie_response_keeps_evidence_scope(monkeypatch, target, role, failure, status, reason):
    async def probe(key, *args):
        assert key == target
        if failure:
            raise failure
        return "合成身份验证通过"
    monkeypatch.setattr(routes, "get_store", lambda: SimpleNamespace(config_all=lambda _: {}))
    monkeypatch.setattr(routes, "set_user_overrides", lambda _: None)
    monkeypatch.setattr(routes, "_verify_target", probe)
    monkeypatch.setattr(routes, "_verify_global_cookie", probe)
    result = asyncio.run(routes.verify_config(routes.VerifyIn(target=target), {"user_id": "synthetic", "role": role}))
    assert result["ok"] is (failure is None)
    assert result["verification"] == {"scope": "identity", "status": status, "reason": reason, "operations": "not_checked"}


def test_unknown_reason_and_failure_cannot_claim_valid_identity():
    evidence = routes._cookie_verification_evidence(routes.CookieProbeError("失败", status="valid", reason="synthetic-secret"))
    assert evidence["reason"] == "probe_failed"
    assert evidence["status"] == "unknown"
    assert "synthetic-secret" not in str(evidence)


@pytest.mark.parametrize("http_status,reason", [(429, "rate_limited"), (403, "access_denied"), (503, "http_error")])
def test_ecommerce_http_failure_does_not_invent_expiration(http_status, reason):
    with pytest.raises(routes.CookieProbeError) as failure:
        routes._classify_ecommerce_probe("https://example.com/account", http_status, "合成平台", (), True)
    assert failure.value.status == "unknown"
    assert failure.value.reason == reason
