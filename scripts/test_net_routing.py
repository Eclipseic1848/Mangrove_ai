#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""网络智能分流单测（P-A：让采集免疫 VPN 开关）。
运行：python scripts/test_net_routing.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.collectors._net import is_overseas, smart_client, _host_of


def test_host_of():
    assert _host_of("https://www.baidu.com/s?wd=x") == "baidu.com"
    assert _host_of("http://News.Sina.com.cn/a") == "news.sina.com.cn"
    assert _host_of("https://www.google.com:443/search") == "google.com"
    assert _host_of("zhihu.com") == "zhihu.com"


def test_overseas_true():
    """境外服务应判为需代理。"""
    for u in [
        "https://www.google.com/search?q=x",
        "https://api.tavily.com/search",
        "https://html.duckduckgo.com/html/?q=x",
        "https://hooks.slack.com/services/xxx",
        "https://x.com/user",
        "https://en.wikipedia.org/wiki/x",
    ]:
        assert is_overseas(u), f"应判为境外：{u}"


def test_domestic_false():
    """国内/本地/未知站应判为直连（非境外）。"""
    for u in [
        "https://www.baidu.com/s?wd=x",
        "https://www.autohome.com.cn/news/",
        "https://www.zhihu.com/question/1",
        "https://www.dongchedi.com/article/1",
        "http://localhost:8080/search",
        "http://127.0.0.1:6015/v1",
        "https://some-unknown-domestic-site.cn/a",
    ]:
        assert not is_overseas(u), f"应判为直连：{u}"


def test_smart_client_domestic_no_trust_env():
    """国内目标：trust_env=False（强制直连，绕系统代理劫持）。"""
    c = smart_client("https://www.baidu.com")
    assert c.trust_env is False, "国内目标应 trust_env=False 直连"


def test_smart_client_overseas_trust_env():
    """境外目标（无 static 代理时）：trust_env=True（读系统代理/VPN）。"""
    c = smart_client("https://www.google.com")
    # 未配 static 境外代理时应读系统代理
    assert c.trust_env is True, "境外目标应 trust_env=True 读系统代理"


def main():
    tests = [test_host_of, test_overseas_true, test_domestic_false,
             test_smart_client_domestic_no_trust_env, test_smart_client_overseas_trust_env]
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
