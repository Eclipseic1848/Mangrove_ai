"""
Scrapling 反爬/自愈采集器（Tier 2，反爬升级层）。

Scrapling（BSD-3）通过浏览器指纹伪装的 HTTP 抓取（curl_cffi）应对一般反爬，
在 Crawl4AI 被封/取空后作为升级层；改版后元素自动重定位，"先抽取再喂 AI"省 token。

- 默认用 Fetcher（HTTP，带指纹伪装，无需浏览器内核）。
- 使用 StealthyFetcher 的隐身浏览器进一步读取动态页面；Linux 优先复用已安装的 Chrome。
  本采集器在该内核不可用时自动退回 Fetcher，不影响运行。
- 未安装 scrapling 或其依赖时本采集器判定不可用，路由自动跳过。

安装：pip install "scrapling[fetchers]"；无 Chrome 的环境需另行准备浏览器内核。
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import sys
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
    from scrapling.fetchers import StealthyFetcher  # type: ignore  # 当前冻结版本使用隐身 Chrome

    _STEALTH_OK = True
except Exception:
    StealthyFetcher = None  # type: ignore
    _STEALTH_OK = False

# 只有明确缺浏览器内核才停用；单个来源超时不能永久影响其他正常来源。
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


def _usable_item(item: Optional[CollectedItem]) -> bool:
    """错误响应和短验证页需升级或报失败，不能当作已获取的业务正文。"""
    if item is None or not (item.content or "").strip():
        return False
    return not item.is_error_page


async def _fetch_stealth(url: str) -> Optional[CollectedItem]:
    """读取隐身浏览器正文；缺内核停用，来源临时失败只影响本次读取。"""
    global _stealth_broken
    if not _STEALTH_OK or _stealth_broken:
        return None
    try:
        # 复用服务器现有 Chrome，避免默认查找未下载的浏览器内核。
        page = await StealthyFetcher.async_fetch(url, headless=True, network_idle=True,
            real_chrome=sys.platform.startswith("linux") and bool(shutil.which("google-chrome")))
    except Exception as e:
        if "Executable doesn't exist" in str(e):
            _stealth_broken = True
        logger.warning(
            "隐身浏览器本次读取失败（缺内核=%s）：%s", _stealth_broken, e
        )
        return None
    text = _page_text(page)
    if not (text or "").strip():
        return None
    item = CollectedItem(
        url=url,
        title=_extract_title(page),
        content=text,
        metadata={"engine": "scrapling", "via": "stealth", "status": getattr(page, "status", None)},
    )
    return item if _usable_item(item) else None


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
            # 验证页也是未取得正文；启用时才尝试隐身读取，失败仍按失败返回。
            if not _usable_item(item) and settings.scrapling_stealth_enabled:
                stealth_item = await _fetch_stealth(url)
                if stealth_item is not None:
                    item = stealth_item
                    stealth_used = True
            if _usable_item(item):
                items.append(item)

        if not items:
            return CollectResult(False, self.name, message="scrapling 未取得内容")
        via = "Fetcher+隐身浏览器兜底" if stealth_used else "Fetcher"
        return CollectResult(True, self.name, items=items, message=f"采集 {len(items)} 个页面（{via}）")


register(ScraplingCollector())
