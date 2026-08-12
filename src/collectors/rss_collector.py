"""
RSS / 站点地图订阅采集器（Tier 12）。

从 RSS feed 或 sitemap.xml 中发现链接，再抓取正文。适合新闻/博客类站点
的周期性增量采集——比整站递归爬取轻量得多，且对目标站零负担。

支持的格式：
- RSS 2.0 / Atom feed（优先 feedparser 库，回退正则提取 <link>）
- sitemap.xml / sitemap index（正则提取 <loc>）

安装（可选）：pip install feedparser
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import List
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from src.config.settings import settings
from src.conductor.task_spec import DataType, TaskSpec

from ._common import build_target_urls
from ._dedup import mark as dedup_mark, seen as dedup_seen
from ._extract import extract_content
from ._net import decode_html, get_rate_limiter, pick_headers, retry_async, smart_client
from .base import BaseCollector, CollectedItem, CollectResult
from .registry import register

logger = logging.getLogger(__name__)

# URL 模式：识别 RSS/sitemap 端点
_RSS_PATTERNS = [
    r"/feed/?$", r"/rss/?$", r"/atom\.xml$", r"/feed\.xml$", r"/rss\.xml$",
    r"/sitemap\.xml$", r"/sitemap_index\.xml$", r"/wp-sitemap\.xml$",
    r"/sitemap-\d+\.xml$", r"/post-sitemap\d*\.xml$",
]
_RSS_URL_RE = re.compile("|".join(_RSS_PATTERNS), re.IGNORECASE)

# sitemap 中 <loc> 标签提取
_LOC_RE = re.compile(r"<loc>\s*([^<]+?)\s*</loc>", re.IGNORECASE)

# RSS/Atom 中 <link> 提取（纯文本，非 HTML编码）
_LINK_RE = re.compile(r"<link[^>]*?>(https?://[^<]+?)</link>", re.IGNORECASE)

try:
    import feedparser  # type: ignore

    _FEEDPARSER_OK = True
except Exception:
    feedparser = None  # type: ignore
    _FEEDPARSER_OK = False


def _looks_like_feed(url: str) -> bool:
    """URL 是否看起来像 RSS feed 或 sitemap。"""
    return bool(_RSS_URL_RE.search(url))


def _extract_urls_from_feed(xml_text: str) -> List[str]:
    """从 RSS/Atom feed XML 中提取文章链接。优先 feedparser，回退正则。"""
    urls: List[str] = []
    if _FEEDPARSER_OK:
        try:
            feed = feedparser.parse(xml_text)
            for entry in (feed.entries or [])[:200]:
                link = entry.get("link") or ""
                if link and link.startswith("http"):
                    urls.append(link)
            if urls:
                return _dedup(urls)
        except Exception:
            logger.debug("feedparser 解析失败，回退正则", exc_info=True)
    # 正则回退：<link> 标签
    for m in _LINK_RE.finditer(xml_text):
        urls.append(m.group(1))
    return _dedup(urls)


def _extract_urls_from_sitemap(xml_text: str) -> List[str]:
    """从 sitemap.xml 中提取 <loc> URL。支持 sitemap index 嵌套。"""
    urls: List[str] = []
    for m in _LOC_RE.finditer(xml_text):
        urls.append(m.group(1).strip())
    return _dedup(urls)


def _dedup(urls: List[str]) -> List[str]:
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


class RssCollector(BaseCollector):
    """RSS/站点地图订阅采集器——发现+抓取。"""

    name = "rss"
    tier = 12  # ecommerce(10) 之后、article(15) 之前

    def matches(self, spec: TaskSpec) -> bool:
        # 有 URL + 看起来像 feed/sitemap → 匹配
        if not spec.urls:
            return False
        return any(_looks_like_feed(u) for u in spec.urls)

    async def collect(self, spec: TaskSpec) -> CollectResult:
        feed_urls = [u for u in build_target_urls(spec) if _looks_like_feed(u)]
        if not feed_urls:
            return CollectResult(False, self.name, message="URL 不包含 RSS/sitemap 端点")

        limit = max(1, min(spec.max_items, settings.collector_max_items))

        # 1) 从 feed/sitemap 发现链接（feed 可能国内外，逐个用 smart_client 按域名分流）
        discovered: List[str] = []
        for feed_url in feed_urls[:5]:  # 最多发现 5 个 feed
            await get_rate_limiter().acquire(feed_url)
            try:
                async def _get(u=feed_url) -> "httpx.Response":
                    async with smart_client(u, timeout=30.0) as client:
                        return await client.get(u, headers=pick_headers())
                resp = await retry_async(_get, retries=1)
                resp.raise_for_status()
            except Exception as e:
                logger.warning("rss: feed 拉取失败 %s: %s", feed_url, e)
                continue

            text = resp.text
            if "sitemap" in feed_url.lower():
                discovered.extend(_extract_urls_from_sitemap(text))
            else:
                discovered.extend(_extract_urls_from_feed(text))

        if not discovered:
            return CollectResult(False, self.name, message="feed/sitemap 中未发现有效链接")

        discovered = discovered[:limit]

        # 2) 逐个抓取正文（复用 article 的提取链路），smart_client 按域名分流
        items: List[CollectedItem] = []
        for url in discovered:
            await get_rate_limiter().acquire(url)
            try:
                async def _get(u=url) -> "httpx.Response":
                    async with smart_client(u, timeout=30.0) as client:
                        return await client.get(u, headers=pick_headers())
                resp = await retry_async(_get, retries=1)
                resp.raise_for_status()
            except Exception as e:
                logger.debug("rss: 正文抓取失败 %s: %s", url, e)
                continue
            if dedup_seen(url):
                continue
            title, text, meta = extract_content(decode_html(resp), url)
            if text and text.strip():
                dedup_mark(url, self.name)
                items.append(CollectedItem(
                    url=url, title=title, content=text,
                    metadata={"engine": self.name, "via": meta.get("via", ""),
                              "feed_url": feed_urls[0]},
                ))

        if not items:
            return CollectResult(False, self.name, message="feed 链接正文均提取失败")
        return CollectResult(True, self.name, items=items,
                             message=f"RSS/sitemap 发现 {len(discovered)} 个链接，抓取 {len(items)} 篇正文")


register(RssCollector())
