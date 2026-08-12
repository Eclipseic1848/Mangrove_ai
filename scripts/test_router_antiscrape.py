#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""强反爬站短路路由单测：命中强反爬域名的 URL 任务，scrapling(Camoufox) 提前。
运行：python scripts/test_router_antiscrape.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.collectors import _metrics
from src.collectors.platforms import url_is_anti_scrape
from src.collectors.registry import _is_anti_scrape_task, _effective_tier, _SCRAPLING_ANTISCRAPE_TIER
from src.conductor.task_spec import DataType, TaskSpec


class _FakeCollector:
    def __init__(self, name, tier):
        self.name = name
        self.tier = tier


def test_url_is_anti_scrape():
    assert url_is_anti_scrape("https://www.zhihu.com/question/1")
    assert url_is_anti_scrape("https://www.autohome.com.cn/news/")
    assert url_is_anti_scrape("https://mp.toutiao.com/a/1")  # 子域名也命中
    assert not url_is_anti_scrape("https://www.gov.cn/news")
    assert not url_is_anti_scrape("https://example.com/a")


def test_anti_scrape_task_detection():
    assert _is_anti_scrape_task(TaskSpec(intent="x", urls=["https://www.zhihu.com/q/1"]))
    # 混合：任一命中即短路
    assert _is_anti_scrape_task(TaskSpec(intent="x", urls=["https://example.com", "https://autohome.com.cn/a"]))
    # 全不命中
    assert not _is_anti_scrape_task(TaskSpec(intent="x", urls=["https://example.com/a"]))
    # 无 URL（关键词任务）不短路
    assert not _is_anti_scrape_task(TaskSpec(intent="x", keywords=["k"]))


def test_effective_tier_promotes_scrapling():
    """强反爬时 scrapling 有效 tier=16（提前），其余采集器不变。"""
    scrapling = _FakeCollector("scrapling", 50)
    crawl4ai = _FakeCollector("crawl4ai", 30)
    firecrawl = _FakeCollector("firecrawl", 20)
    # 强反爬：scrapling 提前到 16，排在 firecrawl(20)/crawl4ai(30) 前
    assert _effective_tier(scrapling, True) == _SCRAPLING_ANTISCRAPE_TIER == 16
    assert _effective_tier(scrapling, True) < _effective_tier(firecrawl, True)
    assert _effective_tier(scrapling, True) < _effective_tier(crawl4ai, True)
    # 非强反爬：scrapling 保持 50（在后）
    assert _effective_tier(scrapling, False) == 50
    assert _effective_tier(scrapling, False) > _effective_tier(crawl4ai, False)
    # 其它采集器有效 tier 恒等于自身 tier
    assert _effective_tier(firecrawl, True) == 20 and _effective_tier(firecrawl, False) == 20


def test_unique_source_collectors_skip_health_penalty():
    """mediacrawler/ecommerce 是专属数据源，没有真替代品；即使近期不健康也不该被降权
    排到 search(25)/crawl4ai(30) 这类"能匹配同一任务但数据源完全不同"的采集器之后
    ——不然会出现"社媒 Cookie 过期，系统却悄悄换成泛化网页搜索"这种文不对题的结果。
    """
    _metrics.clear()
    mediacrawler = _FakeCollector("mediacrawler", 0)
    ecommerce = _FakeCollector("ecommerce", 10)
    search = _FakeCollector("search", 25)
    for name in ("mediacrawler", "ecommerce"):
        for _ in range(10):
            _metrics.record(name, False, 50)
    assert _metrics.is_unhealthy("mediacrawler") and _metrics.is_unhealthy("ecommerce")
    # 即使判定为"不健康"，有效 tier 也不该变化（不参与降权）
    assert _effective_tier(mediacrawler, False) == 0
    assert _effective_tier(ecommerce, False) == 10
    assert _effective_tier(mediacrawler, False) < _effective_tier(search, False)
    _metrics.clear()


def main():
    tests = [
        test_url_is_anti_scrape, test_anti_scrape_task_detection, test_effective_tier_promotes_scrapling,
        test_unique_source_collectors_skip_health_penalty,
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
