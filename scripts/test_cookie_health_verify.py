#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""手动验证结果落库 + describe() 暴露 health 字段，单测。

运行：python scripts/test_cookie_health_verify.py
不联网：monkeypatch _verify_target 让它直接返回/抛错，只测落库与角色隔离逻辑。
"""
import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.store import WebUIStore
from src.api.routes import config_routes as cr
from src.config import runtime_config as rc


def _tmp_store() -> WebUIStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return WebUIStore(tmp.name)


def test_record_cookie_health_writes():
    store = _tmp_store()
    with patch.object(cr, "get_store", return_value=store):
        cr._record_cookie_health("jd_cookie", "valid", "京东 Cookie 有效", "manual")
    row = store.cookie_health_all()["jd_cookie"]
    assert row["status"] == "valid"
    assert row["checked_by"] == "manual"


def test_record_cookie_health_swallow_store_errors():
    # 落库失败（比如坏路径）不应该抛出，是旁路副作用
    class BrokenStore:
        def cookie_health_set(self, *a, **k):
            raise RuntimeError("磁盘满了")
    with patch.object(cr, "get_store", return_value=BrokenStore()):
        cr._record_cookie_health("jd_cookie", "valid", "x", "manual")  # 不应该抛异常


def test_verify_config_admin_success_writes_health():
    store = _tmp_store()
    admin_user = {"user_id": "u_admin", "role": "admin"}

    async def fake_verify_target(target):
        return "小红书 Cookie 有效（真实登录并采到 1 条数据）"

    async def run():
        with patch.object(cr, "get_store", return_value=store), \
             patch.object(cr, "_verify_target", side_effect=fake_verify_target):
            body = cr.VerifyIn(target="mc_cookie_xhs")
            result = await cr.verify_config.__wrapped__(body, admin_user) \
                if hasattr(cr.verify_config, "__wrapped__") else await cr.verify_config(body, admin_user)
            return result

    result = asyncio.run(run())
    assert result["ok"] is True
    row = store.cookie_health_all()["mc_cookie_xhs"]
    assert row["status"] == "valid"
    assert row["checked_by"] == "manual"


def test_verify_config_admin_failure_writes_invalid():
    store = _tmp_store()
    admin_user = {"user_id": "u_admin", "role": "admin"}

    async def fake_verify_target(target):
        raise RuntimeError("京东 登录状态已失效，请重新导出 Cookie")

    async def run():
        with patch.object(cr, "get_store", return_value=store), \
             patch.object(cr, "_verify_target", side_effect=fake_verify_target):
            body = cr.VerifyIn(target="jd_cookie")
            return await cr.verify_config(body, admin_user)

    result = asyncio.run(run())
    assert result["ok"] is False
    row = store.cookie_health_all()["jd_cookie"]
    assert row["status"] == "invalid"


def test_verify_config_ambiguous_failure_writes_unknown():
    store = _tmp_store()
    admin_user = {"user_id": "u_admin", "role": "admin"}

    async def fake_verify_target(target):
        raise RuntimeError("拼多多 无法判断 Cookie 状态（HTTP 461），可能是反爬拦截而非 Cookie 失效")

    async def run():
        with patch.object(cr, "get_store", return_value=store), \
             patch.object(cr, "_verify_target", side_effect=fake_verify_target):
            body = cr.VerifyIn(target="pdd_cookie")
            return await cr.verify_config(body, admin_user)

    asyncio.run(run())
    row = store.cookie_health_all()["pdd_cookie"]
    assert row["status"] == "unknown", row


def test_verify_config_self_user_does_not_write_health():
    # 普通用户验证自己的个人 Cookie 覆盖，不应该污染全局 cookie_health 表
    store = _tmp_store()
    self_user = {"user_id": "u_self", "role": "user"}

    async def fake_verify_target(target):
        return "小红书 Cookie 有效（真实登录并采到 1 条数据）"

    async def run():
        with patch.object(cr, "get_store", return_value=store), \
             patch.object(cr, "set_user_overrides", return_value=None), \
             patch.object(cr, "_verify_target", side_effect=fake_verify_target):
            body = cr.VerifyIn(target="mc_cookie_xhs")
            return await cr.verify_config(body, self_user)

    result = asyncio.run(run())
    assert result["ok"] is True
    assert store.cookie_health_all() == {}, "普通用户验证不应写全局 cookie_health"


def test_describe_includes_health_field():
    store = _tmp_store()
    store.cookie_health_set("mc_cookie_xhs", "valid", "有效", "manual")
    groups = rc.describe(store)
    cookies_group = next(g for g in groups if g["key"] == "cookies")
    xhs_item = next(i for i in cookies_group["items"] if i["key"] == "mc_cookie_xhs")
    assert xhs_item["health"]["status"] == "valid"
    dy_item = next(i for i in cookies_group["items"] if i["key"] == "mc_cookie_dy")
    assert dy_item["health"] is None, "没验证过的 Cookie health 应为 None"


def main():
    tests = [
        test_record_cookie_health_writes,
        test_record_cookie_health_swallow_store_errors,
        test_verify_config_admin_success_writes_health,
        test_verify_config_admin_failure_writes_invalid,
        test_verify_config_ambiguous_failure_writes_unknown,
        test_verify_config_self_user_does_not_write_health,
        test_describe_includes_health_field,
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
