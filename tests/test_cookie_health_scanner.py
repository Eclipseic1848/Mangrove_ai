"""关闭巡检后不能继续对其他平台发起认证探测。"""
import pytest
from src.api.cookie_health_scanner import CookieHealthScanner
from src.api.routes import config_routes
from src.config.settings import settings


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_disabling_scan_stops_new_platform_probes(monkeypatch, enabled):
    scanner = CookieHealthScanner()
    calls = []
    monkeypatch.setattr(settings, "cookie_health_scan_enabled", enabled)
    monkeypatch.setattr(config_routes, "_COOKIE_HEALTH_KEYS", ["mc_cookie_xhs", "mc_cookie_bili"])

    async def verify(key, source):
        calls.append((key, source))
        monkeypatch.setattr(settings, "cookie_health_scan_enabled", False)

    async def no_wait(seconds):
        pass

    monkeypatch.setattr(config_routes, "_verify_global_cookie", verify)
    monkeypatch.setattr(scanner, "_sleep", no_wait)
    await scanner._run_one_scan()
    assert calls == ([("mc_cookie_xhs", "scheduled")] if enabled else [])
