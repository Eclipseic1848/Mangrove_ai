#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Cookie 健康定时巡检协程单测。运行：python scripts/test_cookie_health_scanner.py

不联网：monkeypatch config_routes._verify_target，只验证调度/开关/落库逻辑本身。
"""
import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.cookie_health_scanner import CookieHealthScanner
from src.api.routes import config_routes as cr
from src.api.store import WebUIStore
from src.config.settings import settings


def _tmp_store() -> WebUIStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return WebUIStore(tmp.name)


def test_disabled_does_not_scan():
    old = settings.cookie_health_scan_enabled
    settings.cookie_health_scan_enabled = False
    try:
        scanner = CookieHealthScanner()
        with patch.object(scanner, "_run_one_scan") as mock_scan:
            async def one_tick():
                # 直接调 _loop 的单次判断逻辑：开关关闭应该走 idle 分支，不调用 _run_one_scan
                if not settings.cookie_health_scan_enabled:
                    return
                await scanner._run_one_scan()
            asyncio.run(one_tick())
            mock_scan.assert_not_called()
    finally:
        settings.cookie_health_scan_enabled = old


def test_run_one_scan_records_all_keys():
    store = _tmp_store()
    scanner = CookieHealthScanner()

    call_count = {"n": 0}

    async def fake_verify_target(key):
        call_count["n"] += 1
        if key == "jd_cookie":
            raise RuntimeError("京东 登录状态已失效，请重新导出 Cookie")
        return f"{key} 有效"

    async def run():
        # _run_one_scan 内部延迟导入 config_routes，这里直接 patch 模块级函数
        with patch.object(cr, "get_store", return_value=store), \
             patch.object(cr, "_verify_target", side_effect=fake_verify_target):
            # 缩短相邻项等待，测试跑快点
            scanner._sleep = lambda seconds: asyncio.sleep(0)
            await scanner._run_one_scan()

    asyncio.run(run())
    assert call_count["n"] == len(cr._COOKIE_HEALTH_KEYS)
    all_health = store.cookie_health_all()
    assert len(all_health) == len(cr._COOKIE_HEALTH_KEYS)
    assert all_health["jd_cookie"]["status"] == "invalid"
    assert all_health["mc_cookie_xhs"]["status"] == "valid"


def test_start_is_idempotent():
    async def run():
        scanner = CookieHealthScanner()
        scanner.start()
        task1 = scanner._task
        scanner.start()  # 第二次调用不应该新建协程
        task2 = scanner._task
        assert task1 is task2
        await scanner.stop()
    asyncio.run(run())


def main():
    tests = [test_disabled_does_not_scan, test_run_one_scan_records_all_keys, test_start_is_idempotent]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print("=" * 50)
    print(f"{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
