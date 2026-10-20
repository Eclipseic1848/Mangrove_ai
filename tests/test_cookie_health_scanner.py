"""关闭巡检后不能继续对其他平台发起认证探测。"""
import asyncio
from pathlib import Path

import pytest
from src.api import cookie_health_scanner as scanner_module
from src.api.cookie_health_scanner import CookieHealthScanner
from src.api.routes import config_routes
from src.config.settings import settings


@pytest.fixture(autouse=True)
def isolate_maintenance_file(monkeypatch):
    monkeypatch.setitem(settings.__dict__, "cookie_health_maintenance_file", "")


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


@pytest.mark.parametrize("state,expected", [
    ("empty", False), ("missing", False), ("not_directory", False),
    ("present", True), ("permission", True), ("other_oserror", True),
])
def test_maintenance_status_fails_closed(tmp_path, monkeypatch, state, expected):
    marker = tmp_path / "maintenance"
    if state == "present":
        marker.touch()
    elif state == "not_directory":
        marker.touch()
        marker = marker / "child"
    monkeypatch.setitem(settings.__dict__, "cookie_health_maintenance_file", "" if state == "empty" else str(marker))
    original = Path.stat

    def stat(path, *args, **kwargs):
        if path == marker and state in {"permission", "other_oserror"}:
            raise PermissionError() if state == "permission" else OSError()
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    assert scanner_module._maintenance_active() is expected


@pytest.mark.asyncio
async def test_maintenance_waits_without_probes_and_removal_resumes(tmp_path, monkeypatch):
    scanner = CookieHealthScanner()
    marker = tmp_path / "maintenance"
    marker.touch()
    monkeypatch.setitem(settings.__dict__, "cookie_health_maintenance_file", str(marker))
    monkeypatch.setattr(settings, "cookie_health_scan_enabled", True)
    monkeypatch.setattr(config_routes, "_COOKIE_HEALTH_KEYS", ["mc_cookie_xhs", "mc_cookie_bili"])
    calls = []

    async def verify(key, source):
        calls.append(key)

    async def sleep(seconds):
        if seconds == scanner_module._IDLE_CHECK_SECONDS:
            if marker.exists():
                assert not calls and scanner._last_scan_at == 0
                marker.unlink()
            else:
                assert calls == ["mc_cookie_xhs", "mc_cookie_bili"]
                assert scanner._last_scan_at > 0
                scanner._stop.set()
        await asyncio.sleep(0)

    monkeypatch.setattr(config_routes, "_verify_global_cookie", verify)
    monkeypatch.setattr(scanner, "_sleep", sleep)
    await asyncio.wait_for(scanner._loop(), timeout=2)


@pytest.mark.asyncio
async def test_maintenance_mid_request_resumes_next_item_without_duplicate(tmp_path, monkeypatch):
    scanner = CookieHealthScanner()
    marker = tmp_path / "maintenance"
    monkeypatch.setitem(settings.__dict__, "cookie_health_maintenance_file", str(marker))
    monkeypatch.setattr(settings, "cookie_health_scan_enabled", True)
    monkeypatch.setattr(config_routes, "_COOKIE_HEALTH_KEYS", ["mc_cookie_xhs", "mc_cookie_bili"])
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def verify(key, source):
        calls.append(key)
        started.set()
        await release.wait()

    async def sleep(seconds):
        if seconds == scanner_module._IDLE_CHECK_SECONDS:
            if marker.exists():
                assert calls == ["mc_cookie_xhs"]
                assert scanner._last_scan_at == 0
                marker.unlink()
            else:
                assert calls == ["mc_cookie_xhs", "mc_cookie_bili"]
                assert scanner._last_scan_at > 0
                scanner._stop.set()
        await asyncio.sleep(0)

    monkeypatch.setattr(config_routes, "_verify_global_cookie", verify)
    monkeypatch.setattr(scanner, "_sleep", sleep)
    task = asyncio.create_task(scanner._loop())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        marker.touch()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        await asyncio.wait_for(task, timeout=2)
        assert calls == ["mc_cookie_xhs", "mc_cookie_bili"]
        assert scanner._last_scan_at > 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_maintenance_stat_error_blocks_all_loop_probes(tmp_path, monkeypatch):
    scanner = CookieHealthScanner()
    marker = tmp_path / "maintenance"
    monkeypatch.setitem(settings.__dict__, "cookie_health_maintenance_file", str(marker))
    monkeypatch.setattr(settings, "cookie_health_scan_enabled", True)
    original = Path.stat
    calls = []

    def stat(path, *args, **kwargs):
        if path == marker:
            raise PermissionError()
        return original(path, *args, **kwargs)

    async def forbidden(*args):
        calls.append(args)
        raise AssertionError("维护状态无法确认时禁止身份请求")

    async def sleep(seconds):
        assert seconds == scanner_module._IDLE_CHECK_SECONDS
        assert scanner._last_scan_at == 0
        scanner._stop.set()
        await asyncio.sleep(0)

    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(config_routes, "_verify_global_cookie", forbidden)
    monkeypatch.setattr(scanner, "_sleep", sleep)
    await asyncio.wait_for(scanner._loop(), timeout=2)
    assert not calls


@pytest.mark.asyncio
async def test_admin_disable_keeps_previous_completion_timestamp(monkeypatch):
    scanner = CookieHealthScanner()
    monkeypatch.setattr(settings, "cookie_health_scan_enabled", True)
    monkeypatch.setattr(config_routes, "_COOKIE_HEALTH_KEYS", ["mc_cookie_xhs", "mc_cookie_bili"])
    calls = []

    async def verify(key, source):
        calls.append(key)
        monkeypatch.setattr(settings, "cookie_health_scan_enabled", False)

    async def sleep(seconds):
        if seconds == scanner_module._IDLE_CHECK_SECONDS:
            assert scanner._last_scan_at > 0
            scanner._stop.set()
        await asyncio.sleep(0)

    monkeypatch.setattr(config_routes, "_verify_global_cookie", verify)
    monkeypatch.setattr(scanner, "_sleep", sleep)
    await asyncio.wait_for(scanner._loop(), timeout=2)
    assert calls == ["mc_cookie_xhs"]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["stop", "disable"])
async def test_maintenance_item_wait_can_stop_without_probe(tmp_path, monkeypatch, action):
    scanner = CookieHealthScanner()
    marker = tmp_path / "maintenance"
    marker.touch()
    monkeypatch.setitem(settings.__dict__, "cookie_health_maintenance_file", str(marker))
    monkeypatch.setattr(settings, "cookie_health_scan_enabled", True)
    monkeypatch.setattr(scanner_module, "_IDLE_CHECK_SECONDS", 0.01)
    monkeypatch.setattr(config_routes, "_COOKIE_HEALTH_KEYS", ["mc_cookie_xhs"])
    waiting = asyncio.Event()
    calls = []
    original_sleep = scanner._sleep

    async def sleep(seconds):
        waiting.set()
        await original_sleep(seconds)

    async def verify(*args):
        calls.append(args)

    monkeypatch.setattr(config_routes, "_verify_global_cookie", verify)
    monkeypatch.setattr(scanner, "_sleep", sleep)
    task = asyncio.create_task(scanner._run_one_scan())
    try:
        await asyncio.wait_for(waiting.wait(), timeout=1)
        if action == "stop":
            scanner._stop.set()
        else:
            monkeypatch.setattr(settings, "cookie_health_scan_enabled", False)
        assert await asyncio.wait_for(task, timeout=1) is None
        assert not calls
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
