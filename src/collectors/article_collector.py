"""
文章/新闻专用采集器（Tier 15）。

用 httpx 抓取页面后经 Trafilatura → readability → html_to_text 级联提取正文，
产出干净的 Markdown 文本，比 simple_http 的正则提取质量显著更高、比 crawl4ai 更轻量。

适合：新闻/资讯/文章/博客/招投标公告等以文字为主的页面。
不适合：社媒（由 mediacrawler 承接）、电商（由 ecommerce 承接）、JS 重渲染 SPA（由 crawl4ai 承接）。

安装（可选但强烈建议）：pip install trafilatura readability-lxml
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit

import httpx

from src.config.settings import settings
from src.conductor.task_spec import DataType, TaskSpec

from ._common import build_target_urls
from ._dedup import mark as dedup_mark, seen as dedup_seen
from ._extract import extract_content, is_readability_available, is_trafilatura_available
from ._net import decode_html, get_rate_limiter, pick_headers, retry_async, smart_client
from .base import BaseCollector, CollectedItem, CollectResult
from .platforms import normalize_platform
from .registry import register

logger = logging.getLogger(__name__)

# 社媒/电商平台名——已有专用采集器，article 不抢
_RESERVED_PLATFORMS = {
    "抖音", "小红书", "微博", "B站", "快手", "知乎", "贴吧",  # mediacrawler
    "京东",                                                    # ecommerce
}

# JS 重度渲染站——httpx 静态抓取拿不到内容，应交给 crawl4ai/firecrawl
_JS_HEAVY_DOMAINS = {
    "zhihu.com", "xiaohongshu.com", "douyin.com", "bilibili.com",
    "kuaishou.com", "taobao.com", "tmall.com", "weibo.com",
}
# article 采集器适用的数据类型
_ARTICLE_TYPES = {DataType.ARTICLE, DataType.GENERIC, DataType.COMMENT}


def _is_js_heavy(url: str) -> bool:
    """检查 URL 是否指向已知的 JS 重度渲染站。"""
    host = (urlsplit(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return any(host == d or host.endswith("." + d) for d in _JS_HEAVY_DOMAINS)


class ArticleCollector(BaseCollector):
    """文章/新闻正文采集器——httpx 抓取 + 多级正文提取。"""

    name = "article"
    tier = 15  # ecommerce(10) 之后、firecrawl(20) 之前

    def is_available(self) -> bool:
        # httpx 为项目硬依赖，恒可用；Trafilatura/readability 为可选增强
        return True

    def matches(self, spec: TaskSpec) -> bool:
        # 有 URL 才匹配（无 URL 的关键词发现由 search 采集器负责）
        if not spec.urls:
            return False
        # data_type 适合文章/新闻/通用内容
        if spec.data_type not in _ARTICLE_TYPES:
            return False
        # 社媒/电商平台不抢——已有专用采集器
        for p in spec.platforms:
            if normalize_platform(p) in _RESERVED_PLATFORMS:
                return False
        # JS 重度渲染站不抢——httpx 拿不到内容，交给 crawl4ai/firecrawl
        if all(_is_js_heavy(u) for u in spec.urls):
            return False
        return True

    async def collect(self, spec: TaskSpec) -> CollectResult:
        urls = build_target_urls(spec)
        if not urls:
            return CollectResult(False, self.name, message="无可采集的 URL")
        urls = urls[: max(1, min(spec.max_items, settings.collector_max_items))]

        items: list[CollectedItem] = []
        results = await asyncio.gather(
            *(self._fetch_one(u) for u in urls), return_exceptions=True
        )
        for r in results:
            if isinstance(r, CollectedItem):
                items.append(r)
            elif isinstance(r, Exception):
                logger.warning("article 抓取失败: %s", r)

        if not items:
            return CollectResult(False, self.name, message="所有 URL 正文提取失败，交由后续引擎兜底")
        # 标记使用的提取器，便于 trace 中观察正文质量来源
        extractor = "trafilatura" if is_trafilatura_available() else (
            "readability" if is_readability_available() else "html_to_text"
        )
        return CollectResult(
            True, self.name, items=items,
            message=f"采集 {len(items)} 个页面（提取器: {extractor}）",
        )

    async def _fetch_one(self, url: str) -> CollectedItem:
        await get_rate_limiter().acquire(url)

        async def _do() -> httpx.Response:
            # smart_client 按域名智能分流：国内站强制直连（绕 Clash 系统代理劫持），境外走代理
            async with smart_client(url) as client:
                resp = await client.get(url, headers=pick_headers())
                resp.raise_for_status()
                return resp

        if dedup_seen(url):
            raise ValueError(f"URL 已在缓存窗口内抓取过，跳过: {url}")
        resp = await retry_async(_do)
        title, text, meta = extract_content(decode_html(resp), url)
        if not text or not text.strip():
            raise ValueError(f"正文为空（可能为 JS 渲染页或反爬）: {url}")
        dedup_mark(url, self.name)
        return CollectedItem(
            url=url, title=title, content=text,
            metadata={"engine": self.name, "status_code": resp.status_code, **meta},
        )


register(ArticleCollector())
