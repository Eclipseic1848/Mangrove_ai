"""
Crawl4AI 通用采集器（默认引擎，Tier 1）。

Crawl4AI（Apache-2.0）输出干净的 LLM-ready Markdown，适合新闻/资讯/招投标/通用友好站。
未安装 crawl4ai 时本采集器自动判定不可用，路由会跳过它、降级到下一层。
安装：pip install crawl4ai && playwright install chromium
"""
from __future__ import annotations

import logging

from src.config.settings import settings
from src.conductor.task_spec import TaskSpec

from ._common import build_target_urls
from ._net import get_rate_limiter, retry_async
from .base import BaseCollector, CollectedItem, CollectResult
from .registry import register

logger = logging.getLogger(__name__)

try:
    from crawl4ai import AsyncWebCrawler  # type: ignore

    _CRAWL4AI_OK = True
except Exception:  # 未安装或导入失败
    AsyncWebCrawler = None  # type: ignore
    _CRAWL4AI_OK = False


class Crawl4AICollector(BaseCollector):
    name = "crawl4ai"
    tier = 30  # 通用默认引擎

    def is_available(self) -> bool:
        return _CRAWL4AI_OK

    def matches(self, spec: TaskSpec) -> bool:
        # 无 URL 的关键词发现由 search 负责，不浪费浏览器资源做无效调用
        return bool(spec.urls)

    async def collect(self, spec: TaskSpec) -> CollectResult:
        if not _CRAWL4AI_OK:
            return CollectResult(False, self.name, message="crawl4ai 未安装")

        urls = build_target_urls(spec)
        if not urls:
            return CollectResult(False, self.name, message="无可采集的 URL 或关键词")
        urls = urls[: max(1, min(spec.max_items, settings.collector_max_items))]

        items: list[CollectedItem] = []
        try:
            async with AsyncWebCrawler() as crawler:
                for url in urls:
                    try:
                        # 按域名限速 + 失败指数退避
                        await get_rate_limiter().acquire(url)
                        result = await retry_async(lambda u=url: crawler.arun(url=u))
                    except Exception as e:
                        logger.warning("crawl4ai 抓取失败 %s: %s", url, e)
                        continue
                    if not getattr(result, "success", True):
                        continue
                    markdown = getattr(result, "markdown", "") or ""
                    # 不同版本 markdown 可能是对象，统一转字符串
                    content = str(markdown)
                    md_meta = getattr(result, "metadata", {}) or {}
                    items.append(
                        CollectedItem(
                            url=url,
                            title=md_meta.get("title", ""),
                            content=content,
                            metadata={"engine": self.name, **md_meta},
                        )
                    )
        except Exception as e:
            return CollectResult(False, self.name, message=f"crawl4ai 运行异常: {e}")

        if not items:
            return CollectResult(False, self.name, message="crawl4ai 未取得内容")
        return CollectResult(True, self.name, items=items, message=f"采集 {len(items)} 个页面")


register(Crawl4AICollector())
