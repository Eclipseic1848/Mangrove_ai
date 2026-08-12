"""
Scrapling 反爬/自愈采集器（Tier 2，反爬升级层）。

Scrapling（BSD-3）通过浏览器指纹伪装的 HTTP 抓取（curl_cffi）应对一般反爬，
在 Crawl4AI 被封/取空后作为升级层；改版后元素自动重定位，"先抽取再喂 AI"省 token。

- 默认用 Fetcher（HTTP，带指纹伪装，无需浏览器内核）。
- 若安装了隐身浏览器内核（`scrapling install` 下载 camoufox），可进一步用 StealthyFetcher 过 Cloudflare；
  本采集器在该内核不可用时自动退回 Fetcher，不影响运行。
- 未安装 scrapling 或其依赖时本采集器判定不可用，路由自动跳过。

安装：pip install "scrapling[fetchers]"（过 Cloudflare 还需 `scrapling install`）
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from src.config.settings import settings
from src.conductor.task_spec import TaskSpec

from ._common import build_target_urls
from ._net import get_rate_limiter, retry_async
from .base import BaseCollector, CollectedItem, CollectResult
from .registry import register

logger = logging.getLogger(__name__)

try:
    from scrapling.fetchers import Fetcher  # type: ignore

    _SCRAPLING_OK = True
except Exception:  # 未安装或依赖缺失
    Fetcher = None  # type: ignore
    _SCRAPLING_OK = False

try:
    from scrapling.fetchers import StealthyFetcher  # type: ignore  # 底层 Camoufox 隐身浏览器

    _STEALTH_OK = True
except Exception:
    StealthyFetcher = None  # type: ignore
    _STEALTH_OK = False

# Camoufox 内核首次调用失败（多为未 `scrapling install`）后置真，后续不再尝试隐身兜底，避免反复拖慢
_stealth_broken = False


def _extract_title(page) -> str:
    """尽力取 <title>，不同版本 API 容错。"""
    try:
        el = page.find("title")
        text = getattr(el, "text", None)
        if text:
            return str(text).strip()
    except Exception:
        pass
    try:
        nodes = page.css("title::text")
        if nodes:
            return str(nodes[0]).strip()
    except Exception:
        pass
    return ""


def _page_text(page) -> str:
    """从 scrapling page 取正文，多版本 API 容错。"""
    try:
        return page.get_all_text()
    except Exception:
        return str(getattr(page, "html_content", "") or "")


def _fetch_one(url: str) -> CollectedItem:
    """同步抓取单页（在线程池中调用，避免阻塞事件循环）。用 Fetcher（HTTP 指纹，快）。"""
    page = Fetcher.get(url, timeout=30)
    return CollectedItem(
        url=url,
        title=_extract_title(page),
        content=_page_text(page),
        metadata={"engine": "scrapling", "via": "fetcher", "status": getattr(page, "status", None)},
    )


async def _fetch_stealth(url: str) -> Optional[CollectedItem]:
    """用 StealthyFetcher（Camoufox 隐身浏览器）抓取，过知乎/汽车之家/Cloudflare 等强反爬站。

    内核不可用（多为未 `scrapling install`）时置全局 _stealth_broken 并返回 None，后续不再尝试。
    """
    global _stealth_broken
    if not _STEALTH_OK or _stealth_broken:
        return None
    try:
        # async_fetch 启动 Camoufox 无头浏览器；network_idle 等页面稳定，headless 省资源
        page = await StealthyFetcher.async_fetch(url, headless=True, network_idle=True)
    except Exception as e:
        _stealth_broken = True
        logger.warning(
            "StealthyFetcher(Camoufox) 不可用，本次起停用隐身兜底（请先执行 `scrapling install` 下载内核）：%s", e
        )
        return None
    text = _page_text(page)
    if not (text or "").strip():
        return None
    return CollectedItem(
        url=url,
        title=_extract_title(page),
        content=text,
        metadata={"engine": "scrapling", "via": "camoufox", "status": getattr(page, "status", None)},
    )


class ScraplingCollector(BaseCollector):
    name = "scrapling"
    tier = 50  # 反爬升级层：crawl4ai(30) 之后、http 兜底(90) 之前

    def is_available(self) -> bool:
        return _SCRAPLING_OK

    def matches(self, spec: TaskSpec) -> bool:
        # 无 URL 的关键词发现由 search 负责，不浪费反爬资源做无效调用
        return bool(spec.urls)

    async def collect(self, spec: TaskSpec) -> CollectResult:
        if not _SCRAPLING_OK:
            return CollectResult(False, self.name, message="scrapling 未安装")

        urls = build_target_urls(spec)
        if not urls:
            return CollectResult(False, self.name, message="无可采集的 URL 或关键词")
        urls = urls[: max(1, min(spec.max_items, settings.collector_max_items))]

        items: list[CollectedItem] = []
        stealth_used = False
        for url in urls:
            # 按域名限速 + 失败指数退避（Fetcher 抓取在线程池中执行）
            await get_rate_limiter().acquire(url)
            item: Optional[CollectedItem] = None
            try:
                item = await retry_async(lambda u=url: asyncio.to_thread(_fetch_one, u))
            except Exception as e:
                logger.warning("scrapling Fetcher 抓取失败 %s: %s", url, e)
            # Fetcher 取空/失败 且启用隐身兜底 → StealthyFetcher(Camoufox 隐身浏览器)重抓强反爬站
            if (item is None or not (item.content or "").strip()) and settings.scrapling_stealth_enabled:
                stealth_item = await _fetch_stealth(url)
                if stealth_item is not None:
                    item = stealth_item
                    stealth_used = True
            if item is not None and (item.content or "").strip():
                items.append(item)

        if not items:
            return CollectResult(False, self.name, message="scrapling 未取得内容")
        via = "Fetcher+Camoufox 隐身兜底" if stealth_used else "Fetcher"
        return CollectResult(True, self.name, items=items, message=f"采集 {len(items)} 个页面（{via}）")


register(ScraplingCollector())
