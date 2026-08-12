#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""概览页 cookie_status 应反映"最近一次验证是否通过"，而非"是否配置了 Cookie"。

运行：python scripts/test_overview_cookie_status.py
用临时 store 隔离，不碰真实 data/webui.db。
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.store import WebUIStore
from src.api.routes import overview as ov
from src.scheduler.store import ScheduleStore


def _tmp_store() -> WebUIStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return WebUIStore(tmp.name)


def _call_overview(store: WebUIStore) -> dict:
    user = {"user_id": "u_test", "role": "admin"}
    with tempfile.TemporaryDirectory(prefix="mg_overview_schedule_") as d:
        schedule_store = ScheduleStore(str(Path(d) / "schedule.db"))
        with patch.object(ov, "get_store", return_value=store), \
             patch.object(ov, "get_schedule_store", return_value=schedule_store):
            return ov.overview(user=user)


def test_never_verified_is_grey():
    # 配置中心里可能已经填了 Cookie，但只要没验证过，概览就不应该显示"亮"
    store = _tmp_store()
    result = _call_overview(store)
    assert result["cookie_status"]["douyin"] is False
    assert result["cookie_status"]["bilibili"] is False


def test_verified_valid_is_bright():
    store = _tmp_store()
    store.cookie_health_set("mc_cookie_dy", "valid", "抖音 Cookie 有效", "manual")
    result = _call_overview(store)
    assert result["cookie_status"]["douyin"] is True
    # 没验证过的平台不受影响，仍然是灰
    assert result["cookie_status"]["bilibili"] is False


def test_verified_invalid_is_grey():
    store = _tmp_store()
    store.cookie_health_set("mc_cookie_bili", "invalid", "B站登录已失效", "manual")
    result = _call_overview(store)
    assert result["cookie_status"]["bilibili"] is False


def test_ecommerce_platform_key_mapping():
    # jd/taobao/pdd 三个电商平台落库用的 key 是 *_cookie 而非 mc_cookie_*，映射容易写错
    store = _tmp_store()
    store.cookie_health_set("jd_cookie", "valid", "京东 Cookie 有效", "manual")
    store.cookie_health_set("tb_cookie", "invalid", "淘宝登录已失效", "manual")
    result = _call_overview(store)
    assert result["cookie_status"]["jd"] is True
    assert result["cookie_status"]["taobao"] is False
    assert result["cookie_status"]["pdd"] is False


def main():
    tests = [
        test_never_verified_is_grey,
        test_verified_valid_is_bright,
        test_verified_invalid_is_grey,
        test_ecommerce_platform_key_mapping,
    ]
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
