"""
整站递归爬取采集器（Tier 18）。

从种子 URL 出发，广度优先遍历同域名下的链接，逐页抓取正文。
适合"全量扫标讯网""博客所有文章"等需要递归遍历的场景。

基于 httpx + asyncio（避免 Scrapy Twisted 事件循环与 asyncio 冲突），
限速/UA轮换/重试均复用 _net 层。

策略：
- 仅递归同域名链接（防止爬出目标站）
- 最大深度限制（默认 3 层）
- 最大页数限制（默认 50 页）
- 跳过已知的 JS 重度站（交给 crawl4ai/firecrawl）
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import List, Set
from urllib.parse import urljoin, urlsplit

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

# JS 重度站——BFS 静态抓取拿不到内容，交给 crawl4ai
_JS_HEAVY_DOMAINS = {
    "zhihu.com", "xiaohongshu.com", "douyin.com", "bilibili.com",
    "kuaishou.com", "taobao.com", "tmall.com", "weibo.com",
}

# 链接提取：从 HTML 中提取 href（>30 字节的 http 链接）
_HREF_RE = re.compile(r'href\s*=\s*["\'](https?://[^"\'\s]{10,300})["\']', re.IGNORECASE)

_MAX_DEPTH = 3       # 最大递归深度
_MAX_PAGES = 50       # 总页数上限
_CONCURRENCY = 3       # 并发抓取数


class SiteCrawlerCollector(BaseCollector):
    """整站 BFS 递归爬取——从种子 URL 出发，遍历同域名链接。"""

    name = "site_crawler"
    tier = 18  # article(15) 之后、firecrawl(20) 之前

    def matches(self, spec: TaskSpec) -> bool:
        # 有 URL 且非 JS 重度站（article 已过滤一层）
        if not spec.urls:
            return False
        # 整站爬取适合 bid（招投标扫标）/ article（博客）/ generic 类型
        if spec.data_type not in {DataType.BID, DataType.ARTICLE, DataType.GENERIC}:
            return False
        # JS 重度站跳过
        for u in spec.urls:
            host = (urlsplit(u).netloc or "").lower().replace("www.", "")
            if host in _JS_HEAVY_DOMAINS:
                return False
        return True

    async def collect(self, spec: TaskSpec) -> CollectResult:
        seeds = build_target_urls(spec)[:3]  # 最多 3 个种子 URL
        if not seeds:
            return CollectResult(False, self.name, message="无种子 URL")

        max_items = max(1, min(spec.max_items, settings.collector_max_items, _MAX_PAGES))

        visited: Set[str] = set()
        items: List[CollectedItem] = []
        # BFS 队列: (url, depth)
        queue: List[tuple[str, int]] = [(u, 0) for u in seeds]

        # BFS 仅爬同域名，按种子域名分流：国内站强制直连（绕 Clash 系统代理劫持），境外站走代理
        async with smart_client(seeds[0], timeout=30.0) as client:
            while queue and len(items) < max_items:
                # 取当前深度的一批 URL 并发抓取
                batch = []
                current_depth = queue[0][1] if queue else 0
                while queue and queue[0][1] == current_depth and len(batch) < _CONCURRENCY * 3:
                    url, depth = queue.pop(0)
                    if url in visited or dedup_seen(url):
                        continue
                    visited.add(url)
                    batch.append((url, depth))
                    if len(items) + len(batch) >= max_items:
                        break

                if not batch:
                    continue

                # 并发抓取当前批次
                sem = asyncio.Semaphore(_CONCURRENCY)
                tasks = [self._crawl_one(client, sem, url, depth, seeds[0], max_items - len(items))
                         for url, depth in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for r in results:
                    if isinstance(r, CollectedItem):
                        items.append(r)
                    elif isinstance(r, list):
                        # 返回的是新发现的同域名链接（仅在未达深度上限时）
                        if current_depth < _MAX_DEPTH:
                            for new_url in r:
                                if new_url not in visited and not dedup_seen(new_url):
                                    queue.append((new_url, current_depth + 1))
                    # Exception → 跳过

        if not items:
            return CollectResult(False, self.name, message=f"BFS 遍历 {len(visited)} 个页面，均未提取到有效正文")
        return CollectResult(True, self.name, items=items[:max_items],
                             message=f"BFS 爬取 {len(items)} 页（遍历 {len(visited)} 个页面，深度 ≤{_MAX_DEPTH}）")

    async def _crawl_one(self, client: httpx.AsyncClient, sem: asyncio.Semaphore,
                          url: str, depth: int, seed_url: str,
                          remaining: int) -> object:
        """抓取单页，返回 CollectedItem（正文）或 List[str]（发现的新链接）。"""
        async with sem:
            await get_rate_limiter().acquire(url)
            try:
                resp = await retry_async(
                    lambda u=url: client.get(u, headers=pick_headers()),
                    retries=1,
                )
                resp.raise_for_status()
            except Exception:
                return None

        dedup_mark(url, self.name)
        html = decode_html(resp)
        title, text, meta = extract_content(html, url)

        # 从当前页提取同域名链接（仅 BFS 继续使用，深度未达上限时）
        seed_host = (urlsplit(seed_url).netloc or "").lower().replace("www.", "")
        links: List[str] = []
        if depth < _MAX_DEPTH and remaining > 0:
            for m in _HREF_RE.finditer(html):
                link = m.group(1)
                host = (urlsplit(link).netloc or "").lower().replace("www.", "")
                if host == seed_host or host.endswith("." + seed_host):
                    # 跳过明显的静态资源和非 HTML 路径
                    path = (urlsplit(link).path or "").lower()
                    if any(path.endswith(ext) for ext in (".jpg", ".png", ".pdf", ".css", ".js", ".zip")):
                        continue
                    links.append(link)

        if text and text.strip():
            return CollectedItem(
                url=url, title=title, content=text,
                metadata={"engine": self.name, "via": meta.get("via", ""), "depth": depth},
            )
        return links if links else None


register(SiteCrawlerCollector())
