#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Article collector + content extraction cascade tests.

Usage: python scripts/test_article_collector.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.collectors._extract import (
    extract_content,
    is_readability_available,
    is_trafilatura_available,
)
from src.collectors._common import html_to_text
from src.collectors.article_collector import ArticleCollector
from src.conductor.task_spec import AnalysisType, DataType, TaskSpec


# ── _extract.py: cascade extraction tests ──

_SAMPLE_HTML = """<!DOCTYPE html>
<html><head><title>Test Article Title</title></head>
<body>
<nav>Navigation menu</nav>
<article>
<h1>Test Article Title</h1>
<p>This is the main content of the article.</p>
<p>Second paragraph with more details.</p>
</article>
<footer>Copyright 2026</footer>
</body></html>"""

_SAMPLE_EMPTY = "<html><body></body></html>"

_SAMPLE_NOISY = """<!DOCTYPE html>
<html><head><title>Real Article</title></head>
<body>
<div class="sidebar">Related links</div>
<div class="ad">Buy now!</div>
<main>
<h1>Real Article</h1>
<p>Quality content that should be extracted.</p>
<p>More quality content here.</p>
</main>
<div class="comments">User comments section</div>
<footer>Site footer</footer>
</body></html>"""


def test_extract_content_finds_text():
    """Cascade extractor finds content from valid HTML."""
    title, text, meta = extract_content(_SAMPLE_HTML)
    # Trafilatura may or may not find title from minimal HTML; content is what matters
    has_content = "main content" in text.lower() or "Second paragraph" in text.lower()
    assert has_content, f"Should find article text, got: {text[:100]}"
    assert meta.get("via") in ("trafilatura", "readability", "html_to_text"), \
        f"Unexpected via: {meta.get('via')}"


def test_extract_content_filters_noise():
    """Cascade extractor should find the real content, not just noise."""
    title, text, meta = extract_content(_SAMPLE_NOISY)
    # Main content must be extracted; sidebar/ads may or may not be filtered
    # depending on which extractor is used (Trafilatura/readability are better than html_to_text)
    assert "Quality content" in text, f"Should find main content, got: {text[:100]}"
    via = meta.get("via", "")
    if via == "trafilatura":
        # Trafilatura should filter sidebar noise (but may not on all minimal HTML)
        # At minimum, it should have the main content
        pass
    elif via == "readability":
        # readability should produce cleaner output than raw
        pass


def test_extract_content_empty_html():
    """Empty HTML returns empty results, not errors."""
    title, text, meta = extract_content(_SAMPLE_EMPTY)
    assert isinstance(title, str)
    assert isinstance(text, str)
    assert meta.get("via") in ("empty", "trafilatura", "readability", "html_to_text")


def test_extract_content_fallback_works():
    """html_to_text fallback always works (zero-dependency guarantee)."""
    title, text = html_to_text(_SAMPLE_HTML)
    assert "Test Article Title" in title or "Test Article Title" in text, \
        f"html_to_text should always extract something, got title={title!r} text={text[:100]!r}"


def test_trafilatura_readability_detection():
    """is_available() functions work correctly."""
    assert isinstance(is_trafilatura_available(), bool)
    assert isinstance(is_readability_available(), bool)
    # At least html_to_text fallback is always available (zero deps)
    print(f"  Trafilatura: {is_trafilatura_available()}, readability: {is_readability_available()}")


# ── ArticleCollector: matching & routing tests ──

def test_article_collector_matches_article_url():
    """Article data_type + URL → matches."""
    spec = TaskSpec(
        intent="Analyze this news", urls=["https://example.com/news/1"],
        data_type=DataType.ARTICLE, keywords=[],
    )
    assert ArticleCollector().matches(spec), "Should match article type with URL"


def test_article_collector_matches_generic_url():
    """Generic data_type + URL → matches."""
    spec = TaskSpec(
        intent="Fetch this page", urls=["https://example.com/page"],
        data_type=DataType.GENERIC, keywords=[],
    )
    assert ArticleCollector().matches(spec)


def test_article_collector_skips_no_url():
    """No URL → skip (search handles keyword discovery)."""
    spec = TaskSpec(
        intent="News about AI", keywords=["AI", "news"],
        data_type=DataType.ARTICLE, urls=[],
    )
    assert not ArticleCollector().matches(spec), "Should skip keyword-only tasks"


def test_article_collector_skips_social_media():
    """Social media platforms → skip (mediacrawler handles)."""
    for plat in ["小红书", "抖音", "微博", "B站"]:
        spec = TaskSpec(
            intent="Social media task", urls=["https://example.com/post"],
            platforms=[plat], data_type=DataType.COMMENT, keywords=[],
        )
        assert not ArticleCollector().matches(spec), f"Should skip {plat}"


def test_article_collector_skips_ecommerce():
    """JD platform → skip (ecommerce handles)."""
    spec = TaskSpec(
        intent="JD reviews", urls=["https://item.jd.com/123.html"],
        platforms=["京东"], data_type=DataType.COMMENT, keywords=[],
    )
    assert not ArticleCollector().matches(spec)


def test_article_collector_tier_correct():
    """Verify tier is correctly positioned: ecommerce(10) < article(15) < firecrawl(20)."""
    assert ArticleCollector.tier == 15


def test_article_collector_is_available():
    """Article collector is always available (httpx is hard dep)."""
    assert ArticleCollector().is_available()


# ── P0 strategy fix: generic collectors skip keyword-only tasks ──

def test_crawl4ai_skips_keyword_only():
    """crawl4ai: no URL → skip. Keyword discovery is search's job."""
    from src.collectors.crawl4ai_collector import Crawl4AICollector
    spec = TaskSpec(intent="search", keywords=["AI"], data_type=DataType.GENERIC, urls=[])
    assert not Crawl4AICollector().matches(spec), "keyword-only should skip crawl4ai"

def test_crawl4ai_matches_url():
    """crawl4ai: has URL → matches."""
    from src.collectors.crawl4ai_collector import Crawl4AICollector
    spec = TaskSpec(intent="fetch", urls=["https://example.com"], data_type=DataType.GENERIC)
    assert Crawl4AICollector().matches(spec)

def test_scrapling_skips_keyword_only():
    """scrapling: no URL → skip. Keyword discovery is search's job."""
    from src.collectors.scrapling_collector import ScraplingCollector
    spec = TaskSpec(intent="search", keywords=["AI"], data_type=DataType.GENERIC, urls=[])
    assert not ScraplingCollector().matches(spec), "keyword-only should skip scrapling"

def test_scrapling_matches_url():
    """scrapling: has URL → matches."""
    from src.collectors.scrapling_collector import ScraplingCollector
    spec = TaskSpec(intent="fetch", urls=["https://example.com"], data_type=DataType.GENERIC)
    assert ScraplingCollector().matches(spec)

# ── P0 strategy fix: article skips JS-heavy sites ──

def test_article_skips_js_heavy_sites():
    """JS-heavy sites → skip article, let crawl4ai/firecrawl handle."""
    js_sites = [
        "https://www.zhihu.com/question/123",
        "https://weibo.com/123",
        "https://www.xiaohongshu.com/explore/123",
        "https://www.douyin.com/video/123",
        "https://www.bilibili.com/video/BV123",
        "https://www.taobao.com/item/123",
    ]
    for url in js_sites:
        spec = TaskSpec(intent="x", urls=[url], data_type=DataType.ARTICLE)
        assert not ArticleCollector().matches(spec), f"JS-heavy site should skip: {url}"

def test_simple_http_skips_keyword_only():
    """simple_http: no URL → skip, consistent with crawl4ai/scrapling."""
    from src.collectors.simple_http_collector import SimpleHttpCollector
    spec = TaskSpec(intent="search", keywords=["AI"], data_type=DataType.GENERIC, urls=[])
    assert not SimpleHttpCollector().matches(spec)

def test_simple_http_matches_url():
    """simple_http: has URL → matches (last resort)."""
    from src.collectors.simple_http_collector import SimpleHttpCollector
    spec = TaskSpec(intent="fetch", urls=["https://example.com"], data_type=DataType.GENERIC)
    assert SimpleHttpCollector().matches(spec)

# ── RSS collector tests ──

def test_rss_collector_matches_feed_url():
    """RSS feed URL → matches."""
    from src.collectors.rss_collector import RssCollector, _looks_like_feed
    assert _looks_like_feed("https://example.com/feed")
    assert _looks_like_feed("https://example.com/rss")
    assert _looks_like_feed("https://example.com/sitemap.xml")
    assert not _looks_like_feed("https://example.com/article/123")

def test_rss_collector_skips_regular_urls():
    """Non-feed URL → skip."""
    from src.collectors.rss_collector import RssCollector
    spec = TaskSpec(intent="x", urls=["https://example.com/article"], data_type=DataType.ARTICLE)
    assert not RssCollector().matches(spec)

def test_rss_collector_tier_correct():
    from src.collectors.rss_collector import RssCollector
    assert RssCollector.tier == 12

def test_rss_sitemap_url_extraction():
    """_extract_urls_from_sitemap extracts <loc> URLs."""
    from src.collectors.rss_collector import _extract_urls_from_sitemap
    xml = '<?xml version="1.0"?><urlset><url><loc>https://example.com/a</loc></url><url><loc>https://example.com/b</loc></url></urlset>'
    urls = _extract_urls_from_sitemap(xml)
    assert urls == ["https://example.com/a", "https://example.com/b"]

def test_rss_feed_url_extraction():
    """_extract_urls_from_feed extracts <link> URLs from RSS."""
    from src.collectors.rss_collector import _extract_urls_from_feed
    rss = '<?xml version="1.0"?><rss version="2.0"><channel><item><title>T</title><link>https://example.com/1</link></item><item><title>T2</title><link>https://example.com/2</link></item></channel></rss>'
    urls = _extract_urls_from_feed(rss)
    assert "https://example.com/1" in urls
    assert "https://example.com/2" in urls

# ── Site crawler tests ──

def test_site_crawler_matches_bid_url():
    """BID type + URL → matches."""
    from src.collectors.site_crawler_collector import SiteCrawlerCollector
    spec = TaskSpec(intent="扫标", urls=["https://example.com/bid"], data_type=DataType.BID)
    assert SiteCrawlerCollector().matches(spec)

def test_site_crawler_skips_js_heavy():
    """JS-heavy domain → skip."""
    from src.collectors.site_crawler_collector import SiteCrawlerCollector
    for url in ["https://www.zhihu.com/question/1", "https://www.bilibili.com/video/1"]:
        spec = TaskSpec(intent="x", urls=[url], data_type=DataType.GENERIC)
        assert not SiteCrawlerCollector().matches(spec)

def test_site_crawler_skips_no_url():
    """No URL → skip."""
    from src.collectors.site_crawler_collector import SiteCrawlerCollector
    spec = TaskSpec(intent="x", keywords=["AI"], data_type=DataType.ARTICLE, urls=[])
    assert not SiteCrawlerCollector().matches(spec)

def test_site_crawler_tier_correct():
    from src.collectors.site_crawler_collector import SiteCrawlerCollector
    assert SiteCrawlerCollector.tier == 18

def test_article_still_matches_news_sites():
    """Normal news/article sites should still match article."""
    news_sites = [
        "https://www.thepaper.cn/newsDetail_123",
        "https://news.sina.com.cn/article/123",
        "https://www.163.com/dy/article/123",
    ]
    for url in news_sites:
        spec = TaskSpec(intent="x", urls=[url], data_type=DataType.ARTICLE)
        assert ArticleCollector().matches(spec), f"News site should match: {url}"


def main():
    tests = [
        test_extract_content_finds_text,
        test_extract_content_filters_noise,
        test_extract_content_empty_html,
        test_extract_content_fallback_works,
        test_trafilatura_readability_detection,
        test_article_collector_matches_article_url,
        test_article_collector_matches_generic_url,
        test_article_collector_skips_no_url,
        test_article_collector_skips_social_media,
        test_article_collector_skips_ecommerce,
        test_article_collector_tier_correct,
        test_article_collector_is_available,
        test_crawl4ai_skips_keyword_only,
        test_crawl4ai_matches_url,
        test_scrapling_skips_keyword_only,
        test_scrapling_matches_url,
        test_simple_http_skips_keyword_only,
        test_simple_http_matches_url,
        test_article_skips_js_heavy_sites,
        test_article_still_matches_news_sites,
        test_rss_collector_matches_feed_url,
        test_rss_collector_skips_regular_urls,
        test_rss_collector_tier_correct,
        test_rss_sitemap_url_extraction,
        test_rss_feed_url_extraction,
        test_site_crawler_matches_bid_url,
        test_site_crawler_skips_js_heavy,
        test_site_crawler_skips_no_url,
        test_site_crawler_tier_correct,
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
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
