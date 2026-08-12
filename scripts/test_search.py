#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SearchDiscoveryCollector 纯逻辑单元测试（无网络）。

运行：python scripts/test_search.py
"""
import sys
from pathlib import Path
from urllib.parse import unquote

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.collectors.search_collector import (
    SearchDiscoveryCollector,
    _resolve_domain,
    _UDDG_RE,
)
from src.conductor.task_spec import TaskSpec


def test_resolve_domain():
    assert _resolve_domain(TaskSpec(intent="x", platforms=["懂车帝"])) == "dongchedi.com"
    assert _resolve_domain(TaskSpec(intent="x", platforms=["DongChedi"])) == "dongchedi.com"
    assert _resolve_domain(TaskSpec(intent="x", platforms=["汽车之家"])) == "autohome.com.cn"
    assert _resolve_domain(TaskSpec(intent="x", platforms=["某小众网"])) == ""


def test_build_query():
    c = SearchDiscoveryCollector()
    q, d = c._build_query(TaskSpec(intent="x", platforms=["懂车帝"], keywords=["领克 关闭大灯"]))
    assert q == "site:dongchedi.com 领克 关闭大灯" and d == "dongchedi.com", (q, d)
    q2, d2 = c._build_query(TaskSpec(intent="x", platforms=["小众论坛"], keywords=["A"]))
    assert q2 == "小众论坛 A" and d2 == ""
    q3, d3 = c._build_query(TaskSpec(intent="x", keywords=["A B"]))
    assert q3 == "A B" and d3 == ""


def test_matches():
    c = SearchDiscoveryCollector()
    assert c.matches(TaskSpec(intent="x", keywords=["a"]))
    assert not c.matches(TaskSpec(intent="x", keywords=["a"], urls=["http://x"]))
    assert not c.matches(TaskSpec(intent="x"))


def test_firecrawl_url_only():
    # firecrawl 退出关键词发现：只接显式 URL；关键词任务交给 search
    from src.collectors.firecrawl_collector import FirecrawlCollector
    f = FirecrawlCollector()
    assert f.matches(TaskSpec(intent="x", urls=["http://x"]))
    assert not f.matches(TaskSpec(intent="x", keywords=["a"]))  # 关键词任务不再由 firecrawl 接


def test_uddg_parse():
    sample = (
        '<a href="//duckduckgo.com/l/?uddg='
        'https%3A%2F%2Fwww.dongchedi.com%2Farticle%2F123&rut=abc">t</a>'
    )
    found = [unquote(x) for x in _UDDG_RE.findall(sample)]
    assert found == ["https://www.dongchedi.com/article/123"], found


def test_use_tavily_toggle():
    from src.config import settings
    c = SearchDiscoveryCollector()
    old_key, old_prov = settings.tavily_api_key, settings.search_provider
    try:
        settings.tavily_api_key = ""
        assert c._use_tavily() is False  # 无 key 不用
        settings.tavily_api_key = "tvly-xxx"
        settings.search_provider = "auto"
        assert c._use_tavily() is True
        settings.search_provider = "duckduckgo"
        assert c._use_tavily() is False  # 显式指定 DDG 则不用 Tavily
    finally:
        settings.tavily_api_key, settings.search_provider = old_key, old_prov


def test_discovery_backends():
    from src.config import settings
    c = SearchDiscoveryCollector()
    old = (settings.search_provider, settings.searxng_base_url)
    try:
        # auto + 配了 SearXNG → searxng 优先，再 ddg
        settings.search_provider = "auto"
        settings.searxng_base_url = "http://localhost:8080"
        assert c._discovery_backends() == ["searxng", "ddg"]
        # auto + 没配 SearXNG → 只 ddg
        settings.searxng_base_url = ""
        assert c._discovery_backends() == ["ddg"]
        # 显式 searxng 但没配地址 → 空
        settings.search_provider = "searxng"
        assert c._discovery_backends() == []
        # 显式 tavily → 不走链接发现
        settings.search_provider = "tavily"
        assert c._discovery_backends() == []
        # 显式 ddgs → ddg
        settings.search_provider = "ddgs"
        assert c._discovery_backends() == ["ddg"]
    finally:
        settings.search_provider, settings.searxng_base_url = old


def test_dedup_filter():
    c = SearchDiscoveryCollector()
    urls = [
        "https://www.dongchedi.com/a", "https://www.dongchedi.com/a",  # 重复
        "https://other.com/x",                                          # 异域
        "not-a-url", "https://m.dongchedi.com/b",
    ]
    out = c._dedup_filter(urls, "dongchedi.com", 10)
    assert out == ["https://www.dongchedi.com/a", "https://m.dongchedi.com/b"], out
    # 无域名限制时保留全部有效 http 链接（去重），并受 limit 截断
    out2 = c._dedup_filter(urls, "", 2)
    assert out2 == ["https://www.dongchedi.com/a", "https://other.com/x"], out2


def main():
    tests = [
        test_resolve_domain, test_build_query, test_matches, test_firecrawl_url_only,
        test_uddg_parse, test_use_tavily_toggle,
        test_discovery_backends, test_dedup_filter,
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
