#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""MediaCrawler 按平台 Cookie 登录单测。运行：python scripts/test_mc_cookie.py"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import src.collectors.social_media_collector as smc
from src.collectors.social_media_collector import _build_cmd, _platform_cookie, _proxy_env


def test_build_cmd_with_cookie():
    cmd = _build_cmd("py", "dy", "小米SU7", True, "ck=1; uid=2", 30)
    assert cmd[cmd.index("--lt") + 1] == "cookie"
    assert cmd[cmd.index("--cookies") + 1] == "ck=1; uid=2"
    assert cmd[cmd.index("--get_comment") + 1] == "yes"  # 需要评论时显式开启
    assert cmd[cmd.index("--platform") + 1] == "dy"
    assert cmd[cmd.index("--crawler_max_notes_count") + 1] == "30"  # 显式下发帖子数，避免默认10


def test_build_cmd_without_cookie():
    cmd = _build_cmd("py", "xhs", "k", False, "", 15)
    assert "--lt" not in cmd and "--cookies" not in cmd  # 无 cookie 不传登录类型，回退扫码
    assert cmd[cmd.index("--get_comment") + 1] == "no"  # 不需要评论时显式关闭，避免默认爬评论超时
    assert cmd[cmd.index("--crawler_max_notes_count") + 1] == "15"


def test_platform_cookie_lookup():
    old_dy = smc.settings.mc_cookie_dy
    old_ks = smc.settings.mc_cookie_ks
    try:
        smc.settings.mc_cookie_dy = "  DYCOOKIE  "  # 含空白，应被 strip
        smc.settings.mc_cookie_ks = ""
        assert _platform_cookie("dy") == "DYCOOKIE"
        # 明确清空本测试覆盖的平台，不能依赖开发机 .env 中是否配置真实 Cookie。
        assert _platform_cookie("ks") == ""
    finally:
        smc.settings.mc_cookie_dy = old_dy
        smc.settings.mc_cookie_ks = old_ks
    # 未识别的平台返回空串。
    assert _platform_cookie("unknown") == ""


def test_proxy_env_disabled():
    old = smc.settings.mc_enable_ip_proxy
    try:
        smc.settings.mc_enable_ip_proxy = False
        assert _proxy_env() == {}  # 未启用代理 → 直连，不注入任何变量
    finally:
        smc.settings.mc_enable_ip_proxy = old


def test_proxy_env_static():
    s = smc.settings
    olds = (s.mc_enable_ip_proxy, s.mc_ip_proxy_provider, s.mc_static_proxy_url)
    try:
        s.mc_enable_ip_proxy = True
        s.mc_ip_proxy_provider = "static"
        s.mc_static_proxy_url = "http://u:p@1.2.3.4:8000"
        env = _proxy_env()
        assert env["MC_ENABLE_IP_PROXY"] == "true"
        assert env["MC_IP_PROXY_PROVIDER"] == "static"
        assert env["MC_STATIC_PROXY_URL"] == "http://u:p@1.2.3.4:8000"
        assert "KDL_SECERT_ID" not in env and "WANDOU_APP_KEY" not in env  # 未配凭证不注入
    finally:
        s.mc_enable_ip_proxy, s.mc_ip_proxy_provider, s.mc_static_proxy_url = olds


def test_proxy_env_kuaidaili():
    s = smc.settings
    olds = (s.mc_enable_ip_proxy, s.mc_ip_proxy_provider, s.mc_kdl_secret_id,
            s.mc_kdl_signature, s.mc_kdl_user_name, s.mc_kdl_user_pwd)
    try:
        s.mc_enable_ip_proxy = True
        s.mc_ip_proxy_provider = "kuaidaili"
        s.mc_kdl_secret_id = "sid"
        s.mc_kdl_signature = "sig"
        s.mc_kdl_user_name = "usr"
        s.mc_kdl_user_pwd = "pwd"
        env = _proxy_env()
        # 注入 MediaCrawler 约定的环境变量名（注意 SECERT 原拼写）
        assert env["KDL_SECERT_ID"] == "sid"
        assert env["KDL_SIGNATURE"] == "sig"
        assert env["KDL_USER_NAME"] == "usr"
        assert env["KDL_USER_PWD"] == "pwd"
    finally:
        (s.mc_enable_ip_proxy, s.mc_ip_proxy_provider, s.mc_kdl_secret_id,
         s.mc_kdl_signature, s.mc_kdl_user_name, s.mc_kdl_user_pwd) = olds


def main():
    tests = [test_build_cmd_with_cookie, test_build_cmd_without_cookie, test_platform_cookie_lookup,
             test_proxy_env_disabled, test_proxy_env_static, test_proxy_env_kuaidaili]
    failed = 0
    for t in tests:
        try:
            t(); print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1; print(f"FAIL  {t.__name__}: {e}")
    print("=" * 50); print(f"{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
