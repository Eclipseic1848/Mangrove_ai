#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""京东/淘宝/拼多多 Cookie 真实校验单测。运行：python scripts/test_ecommerce_cookie_verify.py

_classify_ecommerce_probe 是纯函数（不联网），覆盖分类逻辑；
_verify_ecommerce_cookie 只测"未配置 Cookie 直接报错"这条不联网的分支，
真实联网探测另见人工/集成验证（见任务报告），不在此文件跑。
"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.routes.config_routes import _classify_ecommerce_probe, _verify_ecommerce_cookie


def test_classify_valid():
    # 落地 URL 就是预期页面本身、状态码 200 → 有效
    msg = _classify_ecommerce_probe(
        "https://order.jd.com/center/list.action", 200, "京东",
        ("passport.jd.com",), False,
    )
    assert "京东 Cookie 有效" in msg, msg


def test_classify_invalid_login_redirect():
    # 落地 URL 命中登录页特征 → 失效
    try:
        _classify_ecommerce_probe(
            "https://passport.jd.com/uc/login?ReturnUrl=xxx", 200, "京东",
            ("passport.jd.com",), False,
        )
        assert False, "应该抛出 RuntimeError"
    except RuntimeError as e:
        assert "失效" in str(e), str(e)


def test_classify_ambiguous_non_best_effort():
    # 非 best-effort（京东/淘宝）遇到非 200 且未命中登录特征 → 无法判断，不误报失效
    try:
        _classify_ecommerce_probe(
            "https://order.jd.com/center/list.action", 403, "京东",
            ("passport.jd.com",), False,
        )
        assert False, "应该抛出 RuntimeError"
    except RuntimeError as e:
        assert "无法判断" in str(e), str(e)
        assert "失效" not in str(e), "非明确登录跳转时不应该说'失效'"


def test_classify_ambiguous_best_effort_hints_anti_scrape():
    # best-effort（拼多多）遇到非 200 → 无法判断，且提示可能是反爬拦截
    try:
        _classify_ecommerce_probe(
            "https://mobile.yangkeduo.com/user_setting.html", 461, "拼多多",
            ("login.yangkeduo.com", "/login.html"), True,
        )
        assert False, "应该抛出 RuntimeError"
    except RuntimeError as e:
        assert "无法判断" in str(e), str(e)
        assert "反爬" in str(e), str(e)


def test_verify_ecommerce_cookie_empty_cookie():
    # 未配置 Cookie：不联网，直接报错
    from src.config.settings import settings
    old = settings.jd_cookie
    try:
        settings.jd_cookie = ""

        async def run():
            await _verify_ecommerce_cookie("jd_cookie")

        try:
            asyncio.run(run())
            assert False, "应该抛出 RuntimeError"
        except RuntimeError as e:
            assert "未配置" in str(e), str(e)
    finally:
        settings.jd_cookie = old


def main():
    tests = [
        test_classify_valid,
        test_classify_invalid_login_redirect,
        test_classify_ambiguous_non_best_effort,
        test_classify_ambiguous_best_effort_hints_anti_scrape,
        test_verify_ecommerce_cookie_empty_cookie,
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
