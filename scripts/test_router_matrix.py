#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""路由决策测试矩阵：给定代表性 TaskSpec，断言 select_collectors() 选中的相对顺序。

article(15)/site_crawler(18)/crawl4ai(30) 等多个采集器的 matches() 对"有 URL"任务
存在隐式重叠（都可能命中同一个任务），现在全靠 tier 数字的精确排列维持秩序——
本测试把这些重叠点的期望顺序显式断言下来，未来新增/调整采集器 tier 时能第一时间
发现是否无意打乱了这套隐式契约。

只断言"相对顺序"（谁该排在谁前面），不断言完整列表/绝对下标，避免因本地环境
里某些采集器 is_available() 结果不同（如 scrapling 是否装了 Camoufox 内核、
mediacrawler 路径是否配置）而产生环境相关的脆弱失败。

运行：python scripts/test_router_matrix.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.collectors.registry import select_collectors
from src.conductor.task_spec import DataType, TaskSpec


def _order(spec: TaskSpec) -> list:
    return [c.name for c in select_collectors(spec)]


def _assert_before(order: list, first: str, second: str, ctx: str) -> None:
    """断言 first 排在 second 前面（若两者都在候选列表里；不在则跳过——环境相关的
    is_available() 差异不应让测试失败，只校验"两者都在场时的相对顺序"）。"""
    if first in order and second in order:
        assert order.index(first) < order.index(second), (
            f"{ctx}: 期望 {first} 排在 {second} 前面，实际顺序={order}"
        )


def test_ecommerce_beats_article_on_jd_url():
    """京东商品 URL：ecommerce(10) 应排在 article(15)/crawl4ai(30) 前面。"""
    spec = TaskSpec(
        intent="分析京东商品评论", platforms=["京东"],
        urls=["https://item.jd.com/100012043978.html"], data_type=DataType.COMMENT,
    )
    order = _order(spec)
    _assert_before(order, "ecommerce", "article", "京东URL")
    _assert_before(order, "ecommerce", "crawl4ai", "京东URL")


def test_article_beats_site_crawler_and_crawl4ai_on_article_url():
    """新闻文章 URL（data_type=ARTICLE）：article(15) 应先于 site_crawler(18)/crawl4ai(30)。"""
    spec = TaskSpec(
        intent="分析这篇新闻", urls=["https://example.com/news/1"], data_type=DataType.ARTICLE,
    )
    order = _order(spec)
    _assert_before(order, "article", "site_crawler", "文章URL")
    _assert_before(order, "article", "crawl4ai", "文章URL")


def test_bid_task_skips_article_uses_site_crawler():
    """招投标 URL（data_type=BID）：article 的 data_type 集合不含 BID，不应出现在候选里；
    site_crawler(18) 应先于 crawl4ai(30)。"""
    spec = TaskSpec(
        intent="梳理这批招标公告", urls=["https://example.com/bid/1"], data_type=DataType.BID,
    )
    order = _order(spec)
    assert "article" not in order, f"BID 任务不应命中 article，实际={order}"
    _assert_before(order, "site_crawler", "crawl4ai", "招投标URL")


def test_rsshub_beats_search_on_known_platform_keyword_task():
    """无 URL + 关键词 + 平台在 rsshub 路由表内（微博）：rsshub(5) 应先于 search(25)。"""
    spec = TaskSpec(intent="搜索微博热门话题", platforms=["微博"], keywords=["AI"])
    order = _order(spec)
    _assert_before(order, "rsshub", "search", "微博关键词任务")


def test_search_is_fallback_for_unknown_platform_keyword_task():
    """无 URL + 关键词 + 平台不在 rsshub 路由表内：应命中 search，不命中 rsshub。"""
    spec = TaskSpec(intent="搜索某小众话题", keywords=["某小众话题"])
    order = _order(spec)
    assert "search" in order, f"通用关键词任务应命中 search，实际={order}"
    assert "rsshub" not in order, f"无匹配平台不应命中 rsshub，实际={order}"


def test_mediacrawler_always_first_for_social_platform():
    """社媒平台任务：mediacrawler(0) 应排在所有其他候选最前面。"""
    spec = TaskSpec(
        intent="分析抖音评论", platforms=["抖音"], keywords=["测试"], data_type=DataType.COMMENT,
    )
    order = _order(spec)
    assert order, f"抖音任务候选列表不应为空"
    assert order[0] == "mediacrawler", f"mediacrawler 应排第一，实际顺序={order}"


def main():
    tests = [
        test_ecommerce_beats_article_on_jd_url,
        test_article_beats_site_crawler_and_crawl4ai_on_article_url,
        test_bid_task_skips_article_uses_site_crawler,
        test_rsshub_beats_search_on_known_platform_keyword_task,
        test_search_is_fallback_for_unknown_platform_keyword_task,
        test_mediacrawler_always_first_for_social_platform,
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
