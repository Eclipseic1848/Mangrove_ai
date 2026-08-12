#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""static 代理连通性自检（不爬数据，只验证代理本身可用）。

用法：先在 .env 配好 MC_ENABLE_IP_PROXY=true / MC_IP_PROXY_PROVIDER=static / MC_STATIC_PROXY_URL=...，
再运行：python scripts/check_proxy.py
通过代理请求一个 IP 回显端点，打印出口 IP——若与本机公网 IP 不同，说明代理已生效。
仅适用于 static 隧道/固定代理；kuaidaili/wandouhttp 为动态提取，请直接用 mediacrawler 真跑验证。
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import httpx

from src.config.settings import settings

# 国内可达的 IP 回显端点（任一成功即可）
_ECHO_ENDPOINTS = ["https://myip.ipip.net", "https://httpbin.org/ip"]


def _fetch_ip(client: httpx.Client) -> str:
    last = ""
    for url in _ECHO_ENDPOINTS:
        try:
            r = client.get(url, timeout=15.0)
            if r.status_code == 200:
                return f"{url} -> {r.text.strip()[:200]}"
        except Exception as e:  # 单个端点失败则试下一个
            last = f"{url} 失败: {e}"
    return last or "全部回显端点不可达"


def main() -> None:
    if not settings.mc_enable_ip_proxy:
        print("MC_ENABLE_IP_PROXY 未启用：请在 .env 设 MC_ENABLE_IP_PROXY=true 后再自检。")
        sys.exit(2)
    if settings.mc_ip_proxy_provider != "static":
        print(f"当前 provider={settings.mc_ip_proxy_provider}，本脚本仅自检 static；"
              "动态代理(kuaidaili/wandouhttp)请直接用 mediacrawler 真跑验证。")
        sys.exit(2)
    proxy_url = settings.mc_static_proxy_url.strip()
    if not proxy_url:
        print("MC_STATIC_PROXY_URL 为空：请在 .env 填入 http://user:pwd@host:port 后再自检。")
        sys.exit(2)

    masked = proxy_url
    if "@" in proxy_url:  # 掩码凭证，避免日志泄露
        masked = proxy_url.split("@", 1)[0].split("//", 1)[0] + "//***@" + proxy_url.split("@", 1)[1]
    print(f"使用代理：{masked}\n")

    # 1) 直连出口 IP（基线）
    with httpx.Client(trust_env=False) as c:
        print("直连出口：", _fetch_ip(c))

    # 2) 经代理出口 IP（httpx 新版用 proxy=，旧版回退 proxies=）
    try:
        proxied = httpx.Client(proxy=proxy_url, trust_env=False)
    except TypeError:
        proxied = httpx.Client(proxies=proxy_url, trust_env=False)
    try:
        result = _fetch_ip(proxied)
    finally:
        proxied.close()
    print("经代理出口：", result)

    ok = "->" in result
    print("\n结论：", "代理连通 ✓（出口 IP 见上方，应与直连不同）" if ok else "代理不可用 ✗（检查 URL/凭证/白名单）")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
